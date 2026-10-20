from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .common import SafeError, digest


def text(value: Any, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise SafeError("schema_text_type_changed")
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    value = "".join(c for c in value if ord(c) >= 32 or c in "\n\t")
    if len(value) > 10000 or (required and not value):
        raise SafeError("schema_text_invalid")
    return value


def identifier(value: Any) -> str:
    if value is None or value == "" or value == 0:
        return ""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise SafeError("schema_identifier_invalid")
    result = str(value)
    if len(result) > 200 or any(ord(c) < 32 for c in result):
        raise SafeError("schema_identifier_invalid")
    return result


@dataclass(frozen=True)
class Lesson:
    source_id: str
    day: str
    start: str
    end: str
    subject: str
    subject_id: str = ""
    room: str = ""
    building: str = ""
    teacher: str = ""
    work_type: str = ""
    group: str = ""
    note: str = ""
    format: str = ""

    def payload(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def match_id(self) -> str:
        if self.source_id:
            return "pair:" + self.source_id
        # Deliberately not a fuzzy match: a changed time without an ID cannot
        # safely be distinguished from an additional class.
        return "fallback:" + digest([self.subject_id or self.subject, self.work_type, self.group, self.start])

    @property
    def key(self) -> tuple[str, str]:
        return self.match_id, self.day


@dataclass(frozen=True)
class Snapshot:
    start: str
    end: str
    lessons: tuple[Lesson, ...]
    reported_days: frozenset[str]
    empty_chunks: tuple[tuple[str, str], ...] = ()

    def payload(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end,
                "lessons": [lesson.payload() for lesson in sorted(self.lessons, key=lambda x: x.key)],
                "reported_days": sorted(self.reported_days), "empty_chunks": list(self.empty_chunks)}

    @property
    def checksum(self) -> str:
        return digest(self.payload())


def parse_day(value: Any) -> date:
    if not isinstance(value, str):
        raise SafeError("schema_date_invalid")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise SafeError("schema_date_invalid") from None
    if parsed.isoformat() != value:
        raise SafeError("schema_date_invalid")
    return parsed


def parse_clock(value: Any) -> time:
    if not isinstance(value, str):
        raise SafeError("schema_time_invalid")
    try:
        result = time.fromisoformat(value)
    except ValueError:
        raise SafeError("schema_time_invalid") from None
    if result.tzinfo is not None or result.microsecond:
        raise SafeError("schema_time_invalid")
    return result


def parse_response(payload: Any, start: date, end: date, zone: str) -> tuple[list[Lesson], set[str]]:
    """Strict adapter for the observed {code: 0, data: [{date, lessons}]} API.

    No generic recursive JSON search: schema drift must fail closed, not erase
    calendars. Sports/other services are not queried behind the user's back.
    """
    if not isinstance(payload, dict) or type(payload.get("code")) is not int or payload["code"] != 0:
        raise SafeError("schedule_api_error_or_schema_changed")
    days = payload.get("data")
    if not isinstance(days, list):
        raise SafeError("schema_days_missing")
    result: dict[tuple[str, str], Lesson] = {}
    reported: set[str] = set()
    tz = ZoneInfo(zone)
    for day_data in days:
        if not isinstance(day_data, dict):
            raise SafeError("schema_day_invalid")
        day = parse_day(day_data.get("date"))
        if not start <= day <= end:
            raise SafeError("response_outside_requested_dates")
        if day.isoformat() in reported:
            raise SafeError("duplicate_day_in_response")
        reported.add(day.isoformat())
        lessons = day_data.get("lessons")
        if not isinstance(lessons, list):
            raise SafeError("schema_lessons_missing")
        for item in lessons:
            if not isinstance(item, dict):
                raise SafeError("schema_lesson_invalid")
            # Unknown cancellation encodings need adapter review, not a guess.
            for flag in ("cancelled", "canceled", "is_cancelled", "is_canceled"):
                if item.get(flag) not in (None, False, 0):
                    raise SafeError("cancellation_schema_needs_review")
            if "status" in item and item["status"] is not None:
                raise SafeError("status_schema_needs_review")
            local_start = datetime.combine(day, parse_clock(item.get("time_start")), tzinfo=tz)
            local_end = datetime.combine(day, parse_clock(item.get("time_end")), tzinfo=tz)
            # Do not silently turn bad end times into overnight events.
            if not local_start < local_end or (local_end - local_start).total_seconds() > 16 * 3600:
                raise SafeError("schema_invalid_duration")
            note = "\n".join(x for x in (text(day_data.get("note")), text(item.get("note"))) if x)
            lesson = Lesson(
                source_id=identifier(item.get("pair_id")), day=day.isoformat(),
                start=local_start.astimezone(timezone.utc).isoformat(),
                end=local_end.astimezone(timezone.utc).isoformat(),
                subject=text(item.get("subject")) or "ITMO class",
                subject_id=identifier(item.get("subject_id")), room=text(item.get("room")),
                building=text(item.get("building")), teacher=text(item.get("teacher_name")),
                work_type=text(item.get("work_type")), group=text(item.get("group")),
                note=note, format=text(item.get("format")),
            )
            existing = result.get(lesson.key)
            if existing is not None and existing != lesson:
                raise SafeError("conflicting_lesson_identity")
            result[lesson.key] = lesson
    return list(result.values()), reported
