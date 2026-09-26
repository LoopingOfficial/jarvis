"""Thread-safe in-memory event log for the distributed cluster."""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional


class EventLog:
    def __init__(self, max_events: int = 5000):
        self.max_events = max(100, int(max_events))
        self._lock = threading.RLock()
        self._events: List[Dict[str, Any]] = []

    def append(
        self,
        event: str,
        worker_id: Optional[str] = None,
        task_id: Optional[str] = None,
        attempt: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        item: Dict[str, Any] = {
            "timestamp": time.time(),
            "event": event,
        }
        if worker_id is not None:
            item["worker_id"] = worker_id
        if task_id is not None:
            item["task_id"] = task_id
        if attempt is not None:
            item["attempt"] = attempt
        if details:
            item["details"] = dict(details)
        with self._lock:
            self._events.append(item)
            if len(self._events) > self.max_events:
                del self._events[:-self.max_events]
        return dict(item)

    def recent(self, since: Optional[float] = None, limit: int = 500) -> List[Dict[str, Any]]:
        with self._lock:
            events = self._events if since is None else [
                event for event in self._events
                if event["timestamp"] >= since
            ]
            return [dict(event) for event in events[-max(1, min(limit, self.max_events)):]]

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
