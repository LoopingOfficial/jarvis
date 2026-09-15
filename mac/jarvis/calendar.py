"""Agenda local + import des événements Google (lecture seule)."""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Any

from .db import Database, dumps, loads, new_id

WEEKDAYS = {
    "lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3, "vendredi": 4, "samedi": 5, "dimanche": 6,
}


class CalendarManager:
    def __init__(self, db: Database, events) -> None:
        self._db = db
        self._events = events

    def add(self, *, title: str, start_at: float, end_at: float | None = None, duration_min: int = 60,
            description: str = "", source: str = "local", all_day: bool = False,
            meta: dict[str, Any] | None = None, external_id: str = "") -> dict[str, Any]:
        eid = external_id or new_id("evt")
        end = end_at if end_at else start_at + duration_min * 60
        self._db.execute(
            "INSERT INTO calendar_events(id, title, start_at, end_at, all_day, source, description, meta, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title, "
            "start_at=excluded.start_at, end_at=excluded.end_at, description=excluded.description",
            (eid, title[:300], float(start_at), float(end), 1 if all_day else 0, source,
             description[:2000], dumps(meta or {}), time.time()),
        )
        self._events.emit("calendar.updated", {"id": eid, "title": title})
        return self.get(eid)  # type: ignore[return-value]

    def get(self, event_id: str) -> dict[str, Any] | None:
        row = self._db.one("SELECT * FROM calendar_events WHERE id=?", (event_id,))
        return self._row(row) if row else None

    def delete(self, event_id: str) -> bool:
        ok = bool(self._db.execute("DELETE FROM calendar_events WHERE id=?", (event_id,)).rowcount)
        if ok:
            self._events.emit("calendar.updated", {"id": event_id, "deleted": True})
        return ok

    def upcoming(self, days: int = 7, limit: int = 40) -> list[dict[str, Any]]:
        now = time.time()
        rows = self._db.query(
            "SELECT * FROM calendar_events WHERE end_at >= ? AND start_at <= ? ORDER BY start_at LIMIT ?",
            (now - 3600, now + days * 86400, limit))
        return [self._row(r) for r in rows]

    def today(self) -> list[dict[str, Any]]:
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        rows = self._db.query(
            "SELECT * FROM calendar_events WHERE start_at >= ? AND start_at < ? ORDER BY start_at",
            (start, start + 86400))
        return [self._row(r) for r in rows]

    def range(self, start_ts: float, end_ts: float) -> list[dict[str, Any]]:
        rows = self._db.query(
            "SELECT * FROM calendar_events WHERE start_at < ? AND end_at > ? ORDER BY start_at",
            (end_ts, start_ts))
        return [self._row(r) for r in rows]

    def sync_google(self, items: list[dict[str, Any]]) -> int:
        count = 0
        for ev in items or []:
            start = (ev.get("start") or {})
            raw = start.get("dateTime") or start.get("date")
            if not raw:
                continue
            ts = self._parse_iso(raw)
            if ts is None:
                continue
            end_raw = (ev.get("end") or {}).get("dateTime") or (ev.get("end") or {}).get("date")
            end_ts = self._parse_iso(end_raw) if end_raw else None
            self.add(title=ev.get("summary") or "(sans titre)", start_at=ts, end_at=end_ts,
                     description=(ev.get("description") or "")[:1000], source="google",
                     all_day="date" in start, external_id=f"gcal_{ev.get('id')}",
                     meta={"html_link": ev.get("htmlLink", "")})
            count += 1
        return count

    @staticmethod
    def _parse_iso(raw: str) -> float | None:
        if not raw:
            return None
        text = raw.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text).timestamp()
        except ValueError:
            try:
                return datetime.strptime(raw[:10], "%Y-%m-%d").timestamp()
            except ValueError:
                return None

    def parse_when(self, text: str) -> float | None:
        """Comprend « demain 14h », « lundi 9h30 », « 2026-09-12 08:00 », « dans 2 heures »."""
        text = (text or "").strip().casefold()
        if not text:
            return None
        iso = self._parse_iso(text)
        if iso:
            return iso
        now = datetime.now()

        m = re.search(r"dans\s+(\d+)\s*(minute|min|heure|h|jour|j)", text)
        if m:
            n = int(m.group(1))
            unit = m.group(2)
            delta = timedelta(minutes=n) if unit.startswith("min") else \
                timedelta(hours=n) if unit in {"heure", "h"} else timedelta(days=n)
            return (now + delta).timestamp()

        hour, minute = 9, 0
        hm = re.search(r"(\d{1,2})\s*(?:h|:)\s*(\d{2})?", text)
        if hm:
            hour = int(hm.group(1))
            minute = int(hm.group(2) or 0)

        base = now
        if "après-demain" in text or "apres-demain" in text:
            base = now + timedelta(days=2)
        elif "demain" in text:
            base = now + timedelta(days=1)
        else:
            for name, idx in WEEKDAYS.items():
                if name in text:
                    ahead = (idx - now.weekday()) % 7
                    ahead = ahead or 7
                    base = now + timedelta(days=ahead)
                    break
            else:
                dm = re.search(r"(\d{1,2})[/-](\d{1,2})(?:[/-](\d{4}))?", text)
                if dm:
                    day, month = int(dm.group(1)), int(dm.group(2))
                    year = int(dm.group(3) or now.year)
                    try:
                        base = now.replace(year=year, month=month, day=day)
                    except ValueError:
                        return None
        candidate = base.replace(hour=min(23, hour), minute=min(59, minute), second=0, microsecond=0)
        if candidate < now and "demain" not in text and not hm:
            candidate += timedelta(days=1)
        return candidate.timestamp()

    @staticmethod
    def _row(row) -> dict[str, Any]:
        d = {k: row[k] for k in row.keys()}
        d["meta"] = loads(d.get("meta"), {})
        d["all_day"] = bool(d.get("all_day"))
        return d
