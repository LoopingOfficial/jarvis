"""Scheduler AUTO_EDITORIAL: une recherche éditoriale toutes les 12 heures."""
from __future__ import annotations

import threading
import time
from typing import Any


class EditorialScheduler:
    INTERVAL_SECONDS = 12 * 60 * 60

    def __init__(self, core: Any) -> None:
        self._core = core
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_run_at = 0.0
        self.last_result: dict[str, Any] = {}

    def start(self) -> threading.Thread:
        if self._thread and self._thread.is_alive():
            return self._thread
        self._stop.clear()

        def loop() -> None:
            while not self._stop.wait(self.INTERVAL_SECONDS):
                self.run_now(reason="planifié")

        self._thread = threading.Thread(target=loop, daemon=True, name="jarvis-editorial-scheduler")
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._stop.set()

    def run_now(self, *, reason: str = "manuel") -> dict[str, Any]:
        try:
            from .blog_editorial import EditorialAgent
            agent = getattr(self._core, "blog_editorial", None) or EditorialAgent(self._core)
            self._core.blog_editorial = agent
            result = agent.auto_cycle(limit=1)
        except Exception as exc:
            result = {"status": "FAILED", "error": str(exc)[:300], "created": [], "needs_review": []}
        self.last_run_at = time.time()
        self.last_result = {**result, "reason": reason, "run_at": self.last_run_at}
        try:
            self._core.events.emit("blog.auto_cycle", self.last_result)
        except Exception:
            pass
        return self.last_result

    def status(self) -> dict[str, Any]:
        return {"enabled": True, "interval_hours": 12, "last_run_at": self.last_run_at,
                "last_result": self.last_result}
