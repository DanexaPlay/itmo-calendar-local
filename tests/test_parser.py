from datetime import date
import pytest
from itmo_local.common import SafeError
from itmo_local.models import parse_response
from conftest import lesson

START = date(2026, 10, 15)


def payload(item=None):
    item = item or {"pair_id": 1, "subject": "Test", "time_start": "10:00", "time_end": "11:30"}
    return {"code": 0, "data": [{"date": "2026-10-15", "lessons": [item]}]}


def parse(value):
    return parse_response(value, START, START, "Europe/Moscow")


def test_moscow_to_utc():
    result, days = parse(payload())
    assert result[0].start == "2026-10-15T07:00:00+00:00"
    assert result[0].end == "2026-10-15T08:30:00+00:00"
    assert days == {"2026-10-15"}


@pytest.mark.parametrize("value", [None, [], {}, {"code": 1, "data": []}, {"code": True, "data": []},
                                    {"code": "0", "data": []}, {"code": 0, "data": None},
                                    {"code": 0, "data": [{"date": "2026-10-15"}]},
                                    {"code": 0, "data": [{"date": "2026-10-15", "lessons": None}]}])
def test_invalid_envelope_fails_closed(value):
    with pytest.raises(SafeError):
        parse(value)


@pytest.mark.parametrize("changes", [{"time_start": "bad"}, {"time_end": "09:00"}, {"time_start": None},
                                      {"time_start": "10:00+03:00"}, {"time_end": "10:00"},
                                      {"teacher_name": {"name": "Changed schema"}}, {"pair_id": []},
                                      {"cancelled": True}, {"status": "CANCELLED"}])
def test_invalid_lesson_is_not_silently_skipped(changes):
    value = payload()
    value["data"][0]["lessons"][0].update(changes)
    with pytest.raises(SafeError):
        parse(value)


def test_duplicate_day_rejected():
    value = payload()
    value["data"].append(dict(value["data"][0]))
    with pytest.raises(SafeError, match="duplicate_day"):
        parse(value)


def test_exact_duplicate_lesson_deduplicated():
    value = payload()
    value["data"][0]["lessons"] *= 2
    assert len(parse(value)[0]) == 1


def test_conflicting_id_rejected():
    value = payload()
    value["data"][0]["lessons"].append({"pair_id": 1, "subject": "Other", "time_start": "12:00", "time_end": "13:30"})
    with pytest.raises(SafeError, match="conflicting_lesson_identity"):
        parse(value)


def test_day_outside_window_rejected():
    value = payload()
    value["data"][0]["date"] = "2026-10-16"
    with pytest.raises(SafeError, match="outside_requested"):
        parse(value)


def test_missing_optional_fields_and_null_subject():
    row = lesson(pair_id=None, subject=None, teacher_name=None, room=None)
    assert row.subject == "ITMO class"
    assert row.match_id.startswith("fallback:")


def test_no_id_fallback_excludes_room_and_teacher():
    a = lesson(pair_id=None)
    b = lesson(pair_id=None, room="204", teacher_name="Another teacher")
    assert a.key == b.key


def test_no_id_time_change_is_not_guessed():
    assert lesson(pair_id=None).key != lesson(pair_id=None, time_start="10:15").key
