from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .auth import Auth
from .common import SafeError
from .config import API_URL, Settings
from .models import Lesson, Snapshot, parse_response
from .network import HTTP


class Source:
    def __init__(self, settings: Settings, auth: Auth, http: HTTP):
        self.settings, self.auth, self.http = settings, auth, http

    def fetch(self, *, today: date | None = None) -> Snapshot:
        today = today or datetime.now(ZoneInfo(self.settings.source_timezone)).date()
        start = today - timedelta(days=self.settings.past_days)
        end = today + timedelta(days=self.settings.future_days)
        lessons: dict[tuple[str, str], Lesson] = {}
        reported: set[str] = set()
        empty: list[tuple[str, str]] = []
        current = start
        while current <= end:
            chunk_end = min(end, current + timedelta(days=self.settings.chunk_days - 1))
            if self.settings.mode == "demo":
                payload = self._demo_payload(current, chunk_end, today)
            else:
                payload = self._get(current, chunk_end)
            chunk, days = parse_response(payload, current, chunk_end, self.settings.source_timezone)
            for lesson in chunk:
                if lesson.key in lessons and lessons[lesson.key] != lesson:
                    raise SafeError("conflicting_lesson_identity")
                lessons[lesson.key] = lesson
            if len(lessons) > self.settings.max_events:
                raise SafeError("too_many_events")
            if not chunk:
                empty.append((current.isoformat(), chunk_end.isoformat()))
            reported.update(days)
            current = chunk_end + timedelta(days=1)
        return Snapshot(start.isoformat(), end.isoformat(), tuple(lessons.values()), frozenset(reported), tuple(empty))

    def _get(self, start: date, end: date) -> dict:
        for attempt in range(2):
            token = self.auth.access_token(force_refresh=attempt == 1)
            try:
                return self.http.json("GET", API_URL, allowed_host="my.itmo.ru",
                                      params={"date_start": start.isoformat(), "date_end": end.isoformat()},
                                      headers={"Authorization": "Bearer " + token, "Accept-Language": "ru"})
            except SafeError as error:
                if error.code != "access_token_rejected" or attempt == 1:
                    raise
        raise SafeError("access_token_rejected")

    @staticmethod
    def _demo_payload(start: date, end: date, today: date) -> dict:
        days = []
        current = start
        while current <= end:
            lessons = []
            delta = (current - today).days
            if delta in (1, 2):
                lessons = [{"pair_id": int(current.strftime("%Y%m%d")),
                            "subject": "DEMO - " + ("Web programming" if delta == 1 else "Computer architecture"),
                            "subject_id": delta, "time_start": "10:00", "time_end": "11:30",
                            "room": "101", "building": "Demo campus", "teacher_name": "Demo teacher",
                            "work_type": "Lecture", "group": "DEMO", "format": "In person",
                            "note": "Synthetic data. Not your real university timetable."}]
            days.append({"date": current.isoformat(), "lessons": lessons, "note": None})
            current += timedelta(days=1)
        return {"code": 0, "data": days}
