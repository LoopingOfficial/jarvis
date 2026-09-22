"""Audit d'un projet réel — séquence déterministe, jamais racontée.

Pourquoi déterministe : un modèle local s'arrête volontiers après le premier
outil réussi et « conclut » sur une seule observation. Un audit qui liste huit
constats doit donc exécuter réellement ces huit étapes, comme le fait déjà le
pipeline de sécurité. Le modèle n'intervient qu'après, pour formuler — jamais
pour décider de ce qui a été vérifié.

Chaque constat de la sortie provient d'une opération effectuée ici :
lecture de fichier, `git`, inspection de l'arborescence, scan des processus,
état réel du connecteur Discord. Ce qui n'a pas pu être vérifié est écrit tel
quel, jamais comblé.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

MAX_TREE = 40
# Exécutables qui font tourner une application, par opposition aux outils.
RUNTIMES = {"node", "bun", "deno", "python", "python3", "php", "php-fpm",
            "pm2", "nodemon", "gunicorn", "uvicorn"}
ENTRY_HEAD_LINES = 60


class ProjectAuditPipeline:
    def __init__(self, core: Any) -> None:
        self.core = core

    # -- utilitaires ------------------------------------------------------
    def _emit(self, kind: str, payload: dict[str, Any], task_id: str = "") -> None:
        try:
            self.core.events.emit(kind, {"task_id": task_id, "ts": time.time(), **payload},
                                  cache=False)
        except Exception:
            pass

    def _opened(self, path: Path, task_id: str) -> None:
        """Annonce une lecture réelle : l'écran gauche ouvre ce vrai fichier."""
        self._emit("file.opened", {"path": str(path)}, task_id)
        self._emit("code.file.active", {"path": str(path), "source": "audit"}, task_id)

    def _git(self, root: Path, args: list[str], task_id: str) -> tuple[int, str]:
        command = "git " + " ".join(args)
        self._emit("terminal.command", {"command": command, "cwd": str(root)}, task_id)
        try:
            proc = subprocess.run(["git", "-C", str(root), *args],
                                  capture_output=True, text=True, timeout=25)
        except Exception as exc:
            self._emit("terminal.failed", {"command": command, "error": str(exc)}, task_id)
            return 1, str(exc)
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
        if out:
            self._emit("terminal.output", {"command": command, "cwd": str(root),
                                           "stream": "stdout", "text": out[:4000] + "\n"}, task_id)
        self._emit("terminal.completed", {"command": command, "cwd": str(root),
                                          "exit_code": proc.returncode, "duration_ms": 0}, task_id)
        return proc.returncode, out

    # -- audit ------------------------------------------------------------
    def run(self, project, task_id: str = "") -> dict[str, Any]:
        root = Path(project.path)
        report: dict[str, Any] = {"project": project.summary(), "steps": [], "path": str(root)}
        lines: list[str] = [f"Projet détecté : {project.display_name}", f"Chemin : {root}"]

        def step(name: str, ok: bool, detail: str, data: Any = None) -> None:
            report["steps"].append({"step": name, "ok": ok, "detail": detail, "data": data})
            self._emit("velko.audit.step", {"step": name, "ok": ok, "detail": detail[:200]}, task_id)

        # 1. Dépôt git réel
        if (root / ".git").exists():
            _, branch = self._git(root, ["rev-parse", "--abbrev-ref", "HEAD"], task_id)
            code, status = self._git(root, ["status", "--short"], task_id)
            dirty = [l for l in status.splitlines() if l.strip()]
            lines.append(f"Git : branche {branch or '?'} — "
                         + (f"{len(dirty)} fichier(s) modifié(s)" if dirty else "arbre propre"))
            step("git", code == 0, f"branche={branch} modifiés={len(dirty)}",
                 {"branch": branch, "status": status[:4000]})
        else:
            lines.append("Git : ce dossier n'est pas un dépôt git.")
            step("git", True, "pas de dépôt git")

        # 2. Manifeste réel
        manifest_path = next((root / m for m in ("package.json", "pyproject.toml",
                                                 "requirements.txt") if (root / m).is_file()), None)
        scripts: dict[str, str] = {}
        deps: list[str] = []
        if manifest_path:
            self._opened(manifest_path, task_id)
            raw = manifest_path.read_text(encoding="utf-8", errors="replace")
            if manifest_path.name == "package.json":
                try:
                    data = json.loads(raw)
                except Exception:
                    data = {}
                scripts = {k: str(v) for k, v in (data.get("scripts") or {}).items()}
                deps = sorted((data.get("dependencies") or {}).keys())
                lines.append(f"Manifeste : {manifest_path.name} — "
                             f"{data.get('name') or '?'} v{data.get('version') or '?'}, "
                             f"{len(deps)} dépendance(s)")
            else:
                lines.append(f"Manifeste : {manifest_path.name} ({len(raw.splitlines())} lignes)")
            step("manifeste", True, manifest_path.name,
                 {"scripts": scripts, "deps": deps[:40]})
        else:
            lines.append("Manifeste : aucun (package.json / pyproject.toml / requirements.txt absents)")
            step("manifeste", False, "aucun manifeste")

        # 3. Arborescence réelle
        try:
            entries = sorted(p.name + ("/" if p.is_dir() else "")
                             for p in root.iterdir() if not p.name.startswith("."))
        except OSError as exc:
            entries = []
            step("arborescence", False, str(exc))
        if entries:
            lines.append(f"Racine : {len(entries)} entrée(s) — " + ", ".join(entries[:MAX_TREE]))
            step("arborescence", True, f"{len(entries)} entrées", entries[:200])

        # 4. Commandes et événements réellement présents
        for sub in ("commands", "events", "interactions", "cogs", "addons"):
            folder = root / sub
            if not folder.is_dir():
                continue
            files = [p for p in folder.rglob("*") if p.is_file()
                     and p.suffix in {".js", ".ts", ".py", ".mjs", ".cjs"}]
            lines.append(f"{sub}/ : {len(files)} fichier(s)"
                         + (" — ex. " + ", ".join(p.name for p in files[:6]) if files else ""))
            step(sub, True, f"{len(files)} fichiers", [str(p) for p in files[:60]])

        # 5. Point d'entrée réel
        entry = getattr(project, "entrypoint", "") or ""
        if not entry:
            for candidate in ("index.js", "main.js", "bot.js", "src/index.js", "bot.py",
                              "main.py", "index.php", "bootstrap.php", "accueil.php",
                              "app.php", "public/index.php"):
                if (root / candidate).is_file():
                    entry = candidate
                    break
        if entry and (root / entry).is_file():
            path = root / entry
            self._opened(path, task_id)
            head = path.read_text(encoding="utf-8", errors="replace").splitlines()[:ENTRY_HEAD_LINES]
            intents = list(dict.fromkeys(re.findall(r"GatewayIntentBits\.(\w+)", "\n".join(head))))
            lines.append(f"Point d'entrée : {entry} ({len(head)} premières lignes lues)"
                         + (f" — intents {', '.join(intents[:6])}" if intents else ""))
            step("point_d_entree", True, entry, {"intents": intents})
        else:
            lines.append("Point d'entrée : non identifié.")
            step("point_d_entree", False, "non identifié")

        # 6. Scripts de test / lint réellement déclarés
        testish = {k: v for k, v in scripts.items()
                   if re.search(r"test|lint|check|build", k, re.I)}
        if testish:
            lines.append("Scripts de vérification déclarés : "
                         + " ; ".join(f"{k} → {v[:60]}" for k, v in testish.items()))
        else:
            lines.append("Scripts de vérification : aucun script test/lint déclaré dans le manifeste.")
        step("scripts", bool(testish), ", ".join(testish) or "aucun", testish)

        # 7. Le bot tourne-t-il déjà ? (processus système réels)
        running = self._running(root)
        # Un shell ou un éditeur ouvert dans le dossier n'est pas l'application.
        # Sans ce tri, l'audit listait mes propres terminaux comme « le projet
        # tourne », ce qui est trompeur.
        app = [p for p in running if p["name"].lower() in RUNTIMES]
        others = [p for p in running if p not in app]
        if app:
            lines.append("Processus applicatifs en cours : " + " ; ".join(
                f"pid {p['pid']} ({p['name']}) depuis {int((time.time()-p['started_at'])/60)} min"
                for p in app))
        else:
            lines.append("Processus applicatifs : aucune instance locale de ce projet ne tourne.")
        if others:
            lines.append(f"({len(others)} autres processus ont ce dossier comme répertoire "
                         "courant — shells, éditeurs : ce n'est pas l'application.)")
        step("processus", True, f"{len(app)} applicatifs / {len(others)} autres",
             {"app": app, "others": others})

        # 8. Discord réellement connecté
        discord = self._discord()
        lines.append("Discord : " + discord["label"])
        step("discord", discord["connected"], discord["label"], discord)

        report["summary"] = "\n".join(lines)
        return report

    def _running(self, root: Path) -> list[dict[str, Any]]:
        try:
            import psutil
        except Exception:
            return []
        needle = str(root)
        out: list[dict[str, Any]] = []
        for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
            try:
                info = proc.info
                try:
                    cwd = proc.cwd()
                except Exception:
                    cwd = ""
                cmdline = " ".join(info.get("cmdline") or [])
                in_cwd = bool(cwd) and (cwd == needle or cwd.startswith(needle + "/"))
                runtime = (info.get("name") or "").lower() in {
                    "node", "bun", "deno", "python", "python3", "pm2", "nodemon"}
                if not in_cwd and not (runtime and needle in cmdline):
                    continue
                out.append({"pid": info["pid"], "name": info.get("name") or "",
                            "cmdline": cmdline[:200], "cwd": cwd,
                            "started_at": info.get("create_time") or 0})
            except Exception:
                continue
        return out

    def _discord(self) -> dict[str, Any]:
        engine = getattr(self.core, "discord", None)
        if engine is None:
            try:
                from .discord_engine import DiscordEngine
                engine = DiscordEngine(self.core)
                self.core.discord = engine
            except Exception as exc:
                return {"connected": False, "label": f"intégration indisponible ({exc})"}
        try:
            status = engine.status()
        except Exception as exc:
            return {"connected": False, "label": f"état indisponible ({exc})"}
        if not status.get("connected"):
            return {"connected": False,
                    "label": "bot hors ligne (aucune session Discord authentifiée)",
                    **status}
        guilds = ", ".join(g.get("name", "") for g in (status.get("guilds") or []))
        return {"connected": True,
                "label": f"{status.get('user') or 'bot'} connecté — serveur : {guilds or 'aucun'}",
                **status}
