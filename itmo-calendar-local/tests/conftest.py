from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from itmo_local.config import Settings
from itmo_local.models import Lesson, Snapshot, parse_response
from itmo_local.store import Store
from itmo_local.sync import Syncer


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "state", mode="demo",
                    missing_min_seconds=60)


@pytest.fixture
def store(settings):
    return Store(settings.data_dir)


@pytest.fixture
def syncer(settings, store):
    return Syncer(settings, store, None)


@pytest.fixture
def now():
    return datetime(2026, 10, 15, 12, tzinfo=timezone.utc).timestamp()


def lesson(pair_id=101, day="2026-10-15", **changes):
    row = {"pair_id": pair_id, "subject": "Computer architecture", "subject_id": 12,
           "time_start": "10:00", "time_end": "11:30", "room": "101", "building": "Campus",
           "teacher_name": "Teacher", "work_type": "Lecture", "group": "P0000", "note": None}
    row.update(changes)
    response = {"code": 0, "data": [{"date": day, "lessons": [row]}]}
    parsed, _ = parse_response(response, date.fromisoformat(day), date.fromisoformat(day), "Europe/Moscow")
    return parsed[0]


def snapshot(*lessons, reported=None, empty_chunks=()):
    if reported is None:
        reported = {x.day for x in lessons}
    return Snapshot("2026-10-01", "2026-11-01", tuple(lessons), frozenset(reported), tuple(empty_chunks))


def rows(store):
    with store.connect() as connection:
        return store.rows(connection)
