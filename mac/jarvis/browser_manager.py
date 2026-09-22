"""Navigateur contrôlé live (Playwright) — aperçu réel dans le Command Center.

Une seule session partagée entre l'agent (outils browser.*) et l'aperçu
(Browser Preview). Toutes les actions passent par un thread unique, car la
librairie sync de Playwright n'est pas thread-safe.

Confidentialité :
- les valeurs de type secret ne sont JAMAIS émises dans les événements ;
- le champ password est affiché comme « champ protégé » ;
- cookies/tokens/headers ne transitent jamais par le flux.

Playwright reste une dépendance optionnelle : import à la demande, échec
propre si absent.
"""
from __future__ import annotations

import base64
import queue
import threading
import time
from pathlib import Path
from typing import Any

from .config import DATA_DIR

SECRET_KINDS = {"password", "token", "secret", "key", "cookie"}


def _browser_error(msg: str) -> dict[str, Any]:
    return {"ok": False, "error": msg}


class BrowserManager:
    """File d'attente de commandes + thread Playwright unique."""

    def __init__(self, core: Any) -> None:
        self.core = core
        self._queue: queue.Queue = queue.Queue(maxsize=32)
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._running = False
        self._playwright = None
        self._browser = None
        self._page = None
        self._active = False          # une page est ouverte
        self._url = ""
        self._title = ""
        self._state = "idle"          # idle | navigating | running | gate | error
        self._last_frame: bytes | None = None
        self._last_frame_ts: float = 0.0
        self._frame_seq: int = 0
        self._frame_size: tuple[int, int] = (1280, 800)
        self._halo: dict[str, Any] | None = None
        self._halo_until = 0.0
        self._visible_until = 0.0     # rafraîchi par /api/browser/touch
        self._gate = threading.Event()  # défini = automation en attente utilisateur
        self._gate_message = ""
        self._downloads: list[str] = []
        self._pending_result: dict[int, queue.Queue] = {}
        self._cmd_id = 0
        self._download_dir = Path(DATA_DIR) / "downloads"
        self._download_dir.mkdir(parents=True, exist_ok=True)
        # Profil du navigateur DE VELKO. Il lui appartient : ce n'est ni le
        # Chrome de l'utilisateur, ni une fenêtre qu'il faudrait sélectionner.
        self._profile_dir = Path(DATA_DIR) / "browser-profile"
        self._headless = True

    # -- utilitaires événements -----------------------------------------
    def _emit(self, kind: str, payload: dict[str, Any], cache: bool = True) -> None:
        try:
            self.core.events.emit(kind, payload, cache=cache)
        except Exception:
            pass

    def available(self) -> bool:
        try:
            import playwright  # noqa: F401  (vérifie l'installation)
            return True
        except Exception:
            return False

    # -- API depuis l'agent et l'UI --------------------------------------
    def action(self, op: str, args: dict[str, Any] | None = None, timeout: float = 40) -> dict[str, Any]:
        """Exécute une commande de façon synchrone (bloquant jusqu'au résultat)."""
        args = dict(args or {})
        if self._gate.is_set() and op not in ("status", "pause", "resume", "close"):
            return {"ok": False, "error": "en_attente_utilisateur",
                    "gate": self._gate_message}
        with self._lock:
            self._cmd_id += 1
            cmd_id = self._cmd_id
            self._pending_result[cmd_id] = queue.Queue(maxsize=1)
        try:
            self._queue.put_nowait({"id": cmd_id, "op": op, "args": args})
        except queue.Full:
            return _browser_error("file navigateur pleine")
        try:
            return self._pending_result[cmd_id].get(timeout=timeout)
        except queue.Empty:
            return _browser_error(f"timeout ({op})")
        finally:
            with self._lock:
                self._pending_result.pop(cmd_id, None)

    def touch(self) -> None:
        self._visible_until = time.time() + 2.5

    def request_user(self, message: str) -> dict[str, Any]:
        self._gate_message = str(message or "Action manuelle requise")[:200]
        self._gate.set()
        self._state = "gate"
        self._emit("browser.gate", {"message": self._gate_message})
        return {"ok": True, "gate": True, "message": self._gate_message}

    def resume(self) -> dict[str, Any]:
        self._gate.clear()
        self._state = "running" if self._active else "idle"
        self._emit("browser.session.resumed", {"url": self._url})
        return {"ok": True, "state": self._state}

    def status(self) -> dict[str, Any]:
        return {
            "ok": True,
            "available": self.available(),
            "active": self._active,
            "url": self._url,
            "title": self._title,
            "state": self._state,
            "gate": self._gate.is_set(),
            "gate_message": self._gate_message if self._gate.is_set() else "",
            "downloads": list(self._downloads[-5:]),
            "frame_size": list(self._frame_size),
            "frame_bytes": len(self._last_frame or b""),
            "frame_ts": self._last_frame_ts,
            "frame_seq": self._frame_seq,
        }

    def last_frame_data_url(self) -> str:
        if not self._last_frame:
            return ""
        return "data:image/jpeg;base64," + base64.b64encode(self._last_frame).decode()

    def frame_snapshot(self) -> dict[str, Any]:
        """Frame courante pour le fallback HTTP de l'aperçu.

        Le polling vaut aussi « je suis visible » : on rafraîchit la fenêtre
        de streaming et, si aucune frame n'est encore disponible, on en
        demande une immédiatement au thread Playwright.
        """
        self.touch()
        if not self._last_frame and self._active:
            self.action("screenshot", {}, timeout=15)
        if not self._last_frame:
            return {}
        w, h = self._frame_size
        return {
            "data_url": self.last_frame_data_url(),
            "bytes": len(self._last_frame),
            "w": w, "h": h,
            "ts": self._last_frame_ts,
            "seq": self._frame_seq,
            "url": self._url,
        }

    # -- thread Playwright ----------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive() and self._running:
                return
            self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="browser-live")
        self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._running = False
        try:
            self._queue.put_nowait({"id": -1, "op": "__stop__", "args": {}})
        except queue.Full:
            pass

    def _loop(self) -> None:
        last_tick = 0.0
        while self._running:
            cmd = None
            try:
                cmd = self._queue.get(timeout=0.12)
            except queue.Empty:
                pass
            if cmd:
                if cmd.get("op") == "__stop__":
                    break
                self._dispatch(cmd)
            else:
                now = time.time()
                # ~4-5 FPS max : assez pour suivre la page, pas de quoi
                # saturer le flux SSE ni le CPU.
                if self._active and self._page is not None and now - last_tick >= 0.22:
                    last_tick = now
                    self._maybe_frame()
        self._teardown()

    def _dispatch(self, cmd: dict[str, Any]) -> None:
        cid = cmd["id"]
        op = cmd["op"]
        args = cmd.get("args") or {}
        out = self._run_op(op, args)
        q = self._pending_result.get(cid)
        if q:
            try:
                q.put_nowait(out)
            except queue.Full:
                pass

    def _run_op(self, op: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            import playwright.sync_api as pw
        except Exception as exc:
            return _browser_error(f"playwright indisponible : {exc}")

        def result(ok: bool, **extra):
            return {"ok": ok, **extra}

        # -- gestion de session -------------------------------------------
        if op in ("start", "navigate"):
            url = str(args.get("url") or "").strip()
            if not url:
                return _browser_error("url requise")
            if not (url.startswith("http://") or url.startswith("https://")):
                url = "https://" + url
            try:
                if self._playwright is None:
                    self._playwright = pw.sync_playwright().start()
                    # Contexte PERSISTANT : cookies et session restent sur disque.
                    # Sans cela, une authentification faite une fois (Discord) était
                    # perdue au redémarrage et VELKO redemandait la connexion.
                    self._profile_dir.mkdir(parents=True, exist_ok=True)
                    self._browser = self._playwright.chromium.launch_persistent_context(
                        user_data_dir=str(self._profile_dir), headless=self._headless,
                        viewport={"width": 1280, "height": 800},
                        accept_downloads=True)
                if self._page is None:
                    pages = list(getattr(self._browser, "pages", []) or [])
                    self._page = pages[0] if pages else self._browser.new_page()
                    self._bind_page(self._page)
                self._state = "navigating"
                self._emit("browser.session.started", {"url": url})
                self._emit("browser.started", {"url": url, "profile": str(self._profile_dir)})
                self._active = True
                self._page.goto(url, wait_until="commit", timeout=self.timeout_for(args, 30000))
                try:
                    self._page.wait_for_load_state("domcontentloaded", timeout=10000)
                except Exception:
                    pass
                self._url = self._page.url
                self._title = (self._page.title() or "")[:160]
                self._state = "running"
                self._emit("browser.navigate", {"url": self._url, "title": self._title})
                self._emit("browser.loaded", {"url": self._url, "title": self._title})
                self._emit("browser.action", {"kind": "OPEN", "target": self._title or self._url})
                return result(True, url=self._url, title=self._title)
            except Exception as exc:
                self._state = "error"
                self._emit("browser.error", {"message": str(exc)[:300], "stage": op})
                return result(False, error=str(exc)[:300])

        if op == "click":
            sel = str(args.get("selector") or args.get("target") or "").strip()
            if not sel:
                return _browser_error("selector requise (click)")
            try:
                locator = self._resolve(sel)
                locator.scroll_into_view_if_needed(timeout=6000)
                box = locator.bounding_box() or {}
                if box:
                    self._halo = {"x": box.get("x", 0), "y": box.get("y", 0),
                                  "w": box.get("width", 40), "h": box.get("height", 20)}
                    self._halo_until = time.time() + 0.9
                locator.click(timeout=12000)
                label = self._friendly_target(locator, sel)
                self._emit("browser.click", {"target": label, "url": self._url})
                self._emit("browser.action", {"kind": "CLICK", "target": label})
                return result(True, target=label)
            except Exception as exc:
                self._emit("browser.error", {"message": str(exc)[:200], "stage": "click"})
                return result(False, error=str(exc)[:200])

        if op == "type":
            sel = str(args.get("selector") or args.get("target") or "").strip()
            value = str(args.get("value") or "")
            private = bool(args.get("private")) or self._is_secret(sel)
            if not sel:
                return _browser_error("selector requise (type)")
            try:
                locator = self._resolve(sel)
                locator.scroll_into_view_if_needed(timeout=6000)
                locator.fill(value, timeout=8000)
                label = self._friendly_target(locator, sel)
                self._emit("browser.action", {
                    "kind": "TYPE",
                    "target": "champ protégé" if private else label,
                    "private": private,
                })
                return result(True, target=label, private=private)
            except Exception as exc:
                self._emit("browser.error", {"message": str(exc)[:200], "stage": "type"})
                return result(False, error=str(exc)[:200])

        if op == "scroll":
            y = int(args.get("y") or 0)
            try:
                self._page.evaluate(f"window.scrollTo(window.scrollX, window.scrollY + {y})")
                self._emit("browser.scroll", {"delta": y, "url": self._url})
                self._emit("browser.action", {"kind": "SCROLL", "target": f"{y}px"})
                return result(True)
            except Exception as exc:
                return result(False, error=str(exc)[:200])

        if op in ("back", "forward"):
            try:
                getattr(self._page, "go_back" if op == "back" else "go_forward")(wait_until="commit")
                self._url = self._page.url
                self._title = (self._page.title() or "")[:160]
                self._emit("browser.navigate", {"url": self._url, "title": self._title})
                self._emit("browser.action", {"kind": "BACK" if op == "back" else "FORWARD",
                                              "target": self._url})
                return result(True, url=self._url)
            except Exception as exc:
                return result(False, error=str(exc)[:200])

        if op == "reload":
            try:
                self._page.reload(wait_until="commit", timeout=self.timeout_for(args, 30000))
                self._url = self._page.url
                self._title = (self._page.title() or "")[:160]
                self._emit("browser.navigate", {"url": self._url, "title": self._title})
                self._emit("browser.loaded", {"url": self._url, "title": self._title})
                return result(True, url=self._url, title=self._title)
            except Exception as exc:
                self._emit("browser.error", {"message": str(exc)[:200], "stage": "reload"})
                return result(False, error=str(exc)[:200])

        if op == "read_page":
            # Texte RÉEL de la page réellement ouverte. Aucun contenu reconstitué.
            try:
                text = self._page.inner_text("body", timeout=8000)
            except Exception as exc:
                return result(False, error=str(exc)[:200])
            limit = int(args.get("max_chars") or 6000)
            body = self.core.vault.scrub(text)[:limit] if hasattr(self.core, "vault") else text[:limit]
            self._emit("browser.read", {"url": self._url, "title": self._title,
                                        "chars": len(text)})
            return result(True, url=self._url, title=self._title, text=body,
                          truncated=len(text) > limit)

        if op == "wait":
            # Le paramètre est en MILLISECONDES. Il était passé tel quel à
            # time.sleep(), qui attend des secondes : « wait 2500 ms » bloquait
            # le thread Playwright 41 minutes et gelait toute la session.
            ms = max(0, min(int(args.get("ms") or 800), 15000))
            time.sleep(ms / 1000.0)
            return result(True, waited=ms)

        if op == "pause":
            return self.request_user(str(args.get("message") or "Action manuelle requise"))

        if op == "resume":
            return self.resume()

        if op == "screenshot":
            shot = self._capture()
            if shot:
                return result(True, data_url=shot)
            return result(False, error="aucune frame disponible")

        if op == "close":
            self._close_page()
            self._emit("browser.closed", {})
            return result(True)

        if op == "status":
            return {"ok": True, **self.status()}

        return _browser_error(f"commande inconnue : {op}")

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def timeout_for(args: dict[str, Any], default: int) -> int:
        try:
            return int(args.get("timeout") or default)
        except (TypeError, ValueError):
            return default

    def _bind_page(self, page) -> None:
        try:
            page.on("download", self._on_download)
        except Exception:
            pass

    def _on_download(self, download) -> None:
        try:
            fname = download.suggested_filename or "download"
            dest = self._download_dir / fname
            download.save_as(dest)
            self._downloads.append(fname)
            self._emit("browser.download", {"filename": fname})
        except Exception as exc:
            self._emit("browser.error", {"message": f"download échoué : {exc}", "stage": "download"})

    def _resolve(self, sel: str):
        page = self._page
        if not page:
            raise RuntimeError("aucune page ouverte")
        text = sel.strip().strip('"')
        try:
            return page.get_by_text(text, exact=False).first
        except Exception:
            return page.locator(sel).first

    @staticmethod
    def _friendly_target(locator, fallback: str) -> str:
        try:
            for attr in ("aria-label", "placeholder", "title", "data-testid"):
                v = locator.get_attribute(attr)
                if v:
                    return str(v)[:60]
        except Exception:
            pass
        try:
            t = locator.inner_text()
            if t and len(t) <= 60:
                return t.strip()
        except Exception:
            pass
        return fallback[:60]

    @staticmethod
    def _is_secret(sel: str) -> bool:
        lowered = str(sel).lower()
        return any(k in lowered for k in SECRET_KINDS)

    def _capture(self) -> str:
        if self._page is None:
            return ""
        try:
            b = self._page.screenshot(type="jpeg", quality=72)
            self._last_frame = b
            self._last_frame_ts = time.time()
            self._frame_seq += 1
            try:
                vp = self._page.viewport_size or {}
                if vp.get("width") and vp.get("height"):
                    self._frame_size = (int(vp["width"]), int(vp["height"]))
            except Exception:
                pass
            return "data:image/jpeg;base64," + base64.b64encode(b).decode()
        except Exception:
            return ""

    def _maybe_frame(self) -> None:
        now = time.time()
        # Pas de stream si l'aperçu n'est pas visible récemment.
        if now > self._visible_until + 2.0:
            return
        data_url = self._capture()
        if not data_url:
            return
        w, h = (self._page.viewport_size["width"], self._page.viewport_size["height"]) \
            if self._page else self._frame_size
        halo = self._halo if now < self._halo_until else None
        jpeg = data_url.split(",", 1)[1]
        self._emit("browser.frame", {
            "url": self._url[:200], "w": w, "h": h, "halo": halo,
            "seq": self._frame_seq, "bytes": len(self._last_frame or b""),
            "jpeg": jpeg,
        }, cache=False)

    def _close_page(self) -> None:
        if self._page is not None:
            try:
                self._page.close()
            except Exception:
                pass
        self._page = None
        was_active = self._active
        self._active = False
        self._url = ""
        self._title = ""
        self._state = "idle"
        self._halo = None
        self._last_frame = None
        self._last_frame_ts = 0.0
        self._downloads = []
        self._gate.clear()
        self._gate_message = ""
        if was_active:
            self._emit("browser.session.finished", {})

    def _teardown(self) -> None:
        self._close_page()
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass
        try:
            self._queue.task_done()
        except Exception:
            pass


_MANAGER: BrowserManager | None = None


def set_manager(m: BrowserManager | None) -> None:
    global _MANAGER
    _MANAGER = m


def get_manager() -> BrowserManager | None:
    return _MANAGER