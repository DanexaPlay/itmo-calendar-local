from dataclasses import replace
import pytest
from itmo_local.common import SafeError
from itmo_local.store import Store
from itmo_local.sync import Syncer
from conftest import lesson, rows, snapshot


def test_idempotent_database(syncer, store, now):
    snap = snapshot(lesson())
    syncer.apply(snap, now=now)
    original = rows(store)
    identity = rows(store)[0]["uid"]
    result = syncer.apply(snap, now=now + 4000)
    assert result["added"] == result["changed"] == 0
    assert len(rows(store)) == 1
    assert rows(store)[0]["uid"] == identity
    assert rows(store)[0]["sequence"] == 0
    assert rows(store) == original
    assert store.metadata("last_success") == now + 4000


@pytest.mark.parametrize("changes", [{"room": "202"}, {"teacher_name": "Changed"},
                                      {"time_start": "10:15"}, {"subject": "New name"}, {"note": "Updated note"}])
def test_changes_keep_uid_and_increment_sequence(syncer, store, now, changes):
    syncer.apply(snapshot(lesson()), now=now)
    old = rows(store)[0]
    syncer.apply(snapshot(lesson(**changes)), now=now + 100)
    updated = rows(store)[0]
    assert len(rows(store)) == 1 and updated["uid"] == old["uid"]
    assert updated["sequence"] == 1 and updated["modified"] == now + 100


def test_cross_day_move_with_unique_id(syncer, store, now):
    syncer.apply(snapshot(lesson()), now=now)
    uid = rows(store)[0]["uid"]
    syncer.apply(snapshot(lesson(day="2026-10-16")), now=now + 100)
    assert len(rows(store)) == 1
    assert rows(store)[0]["uid"] == uid
    assert rows(store)[0]["day"] == "2026-10-16"


def test_repeated_id_has_separate_occurrences(syncer, store, now):
    first, second = lesson(), lesson(day="2026-10-22")
    syncer.apply(snapshot(first, second), now=now)
    original = {row["day"]: row["uid"] for row in rows(store)}
    assert len(set(original.values())) == 2
    syncer.apply(snapshot(lesson(room="2"), second), now=now + 100)
    assert {row["day"]: row["uid"] for row in rows(store)} == original


def test_two_spaced_confirmations_for_cancellation(syncer, store, now):
    a, b = lesson(), lesson(202, day="2026-10-16")
    syncer.apply(snapshot(a, b), now=now)
    uid = next(row["uid"] for row in rows(store) if row["match_id"] == a.match_id)
    remaining = snapshot(b, reported={a.day, b.day})
    assert syncer.apply(remaining, now=now + 1)["cancelled"] == 0
    assert syncer.apply(remaining, now=now + 30)["cancelled"] == 0
    assert syncer.apply(remaining, now=now + 62)["cancelled"] == 1
    assert next(row for row in rows(store) if row["uid"] == uid)["active"] == 0
    syncer.apply(snapshot(a, b), now=now + 200)
    restored = next(row for row in rows(store) if row["uid"] == uid)
    assert restored["active"] == 1 and restored["sequence"] == 2


def test_unreported_day_does_not_cancel(syncer, store, now):
    a, b = lesson(), lesson(202, day="2026-10-16")
    syncer.apply(snapshot(a, b), now=now)
    for delta in [100, 200, 400]:
        result = syncer.apply(snapshot(b), now=now + delta)
        assert result["held"] == 1
    assert all(row["active"] for row in rows(store))


def test_missing_streak_resets_when_class_reappears(syncer, store, now):
    a, b = lesson(), lesson(202, day="2026-10-16")
    syncer.apply(snapshot(a, b), now=now)
    missing = snapshot(b, reported={a.day, b.day})
    syncer.apply(missing, now=now + 100)
    syncer.apply(snapshot(a, b), now=now + 200)
    assert syncer.apply(missing, now=now + 300)["cancelled"] == 0


def test_empty_response_preserves_last_good(syncer, store, now):
    syncer.apply(snapshot(lesson()), now=now)
    previous = rows(store)
    with pytest.raises(SafeError, match="unexpected_empty"):
        syncer.apply(snapshot(reported={"2026-10-15"}), now=now + 100)
    assert rows(store) == previous
    assert store.metadata("pending_digest")


def test_initial_empty_rejected(syncer):
    with pytest.raises(SafeError, match="empty_initial"):
        syncer.apply(snapshot())


def test_mass_loss_requires_exact_explicit_approval(syncer, store, now):
    all_lessons = [lesson(i, day="2026-10-15", time_start=f"{8+i:02d}:00", time_end=f"{9+i:02d}:00") for i in range(1, 6)]
    syncer.apply(snapshot(*all_lessons), now=now)
    remaining = snapshot(all_lessons[0])
    with pytest.raises(SafeError, match="mass_disappearance"):
        syncer.apply(remaining, now=now + 100)
    with pytest.raises(SafeError, match="approval_digest"):
        syncer.apply(remaining, approve_digest="wrong", now=now + 200)
    result = syncer.apply(remaining, approve_digest=remaining.checksum, now=now + 300)
    assert result["cancelled"] == 4
    assert store.metadata("pending_digest") is None


def test_empty_chunk_is_quarantined(syncer, store, now):
    a, b = lesson(), lesson(202, day="2026-10-25")
    syncer.apply(snapshot(a, b), now=now)
    suspicious = snapshot(b, reported={a.day, b.day}, empty_chunks=(("2026-10-12", "2026-10-18"),))
    with pytest.raises(SafeError, match="populated_chunk_is_empty"):
        syncer.apply(suspicious, now=now + 100)


def test_history_not_deleted_when_outside_fetch_window(syncer, store, now):
    a, b = lesson(day="2026-10-02"), lesson(202, day="2026-10-25")
    syncer.apply(snapshot(a, b), now=now)
    later = replace(snapshot(b), start="2026-10-10", end="2026-11-10")
    syncer.apply(later, now=now + 86400)
    assert len(rows(store)) == 2


def test_sqlite_failure_rolls_back_everything(syncer, store, now, monkeypatch):
    syncer.apply(snapshot(lesson()), now=now)
    before = rows(store)
    original = __import__("itmo_local.sync", fromlist=["canonical"]).canonical
    def failed(value):
        if isinstance(value, dict) and value.get("room") == "999":
            raise ValueError("Synthetic serialization error")
        return original(value)
    monkeypatch.setattr("itmo_local.sync.canonical", failed)
    with pytest.raises(ValueError):
        syncer.apply(snapshot(lesson(room="999")), now=now + 100)
    assert rows(store) == before


def test_fetch_error_keeps_database_and_schedules_retry(settings, store, now):
    class FailingSource:
        def fetch(self):
            raise SafeError("upstream_rate_limited", retry_after=8000)
    syncer = Syncer(settings, store, FailingSource())
    syncer.apply(snapshot(lesson()), now=now)
    original = rows(store)
    with pytest.raises(SafeError):
        syncer.run()
    assert rows(store) == original
    assert store.metadata("last_error") == "upstream_rate_limited"
    assert store.metadata("next_sync") > store.metadata("last_attempt") + 7999


def test_cancellation_retained_in_local_sqlite(store, syncer, now):
    a, b = lesson(), lesson(202, day="2026-10-16")
    syncer.apply(snapshot(a, b), now=now)
    missing = snapshot(b, reported={a.day, b.day})
    syncer.apply(missing, now=now + 100)
    syncer.apply(missing, now=now + 200)
    assert any(row["active"] == 0 for row in rows(store))


def test_restart_retains_uid_history(settings, store, syncer, now):
    syncer.apply(snapshot(lesson()), now=now)
    before = rows(store)
    restarted = Store(settings.data_dir)
    with restarted.connect() as conn:
        assert restarted.rows(conn) == before


def test_approved_empty_snapshot_cancels_unreported_days(syncer, store, now):
    syncer.apply(snapshot(lesson()), now=now)
    empty = snapshot()
    with pytest.raises(SafeError):
        syncer.apply(empty, now=now + 100)
    assert syncer.apply(empty, approve_digest=empty.checksum, now=now + 200)["cancelled"] == 1
    assert all(not row["active"] for row in rows(store))


def test_demo_state_not_reused_for_real_timetable(settings, store, syncer, now):
    syncer.apply(snapshot(lesson()), now=now)
    real = Syncer(replace(settings, mode="myitmo"), store, None)
    with pytest.raises(SafeError, match="state_mode_mismatch"):
        real.apply(snapshot(lesson()), now=now + 100)


def test_sqlite_backup_has_local_events(store, syncer, now, tmp_path):
    import sqlite3
    syncer.apply(snapshot(lesson()), now=now)
    target = tmp_path / "backup" / "calendar.sqlite3"
    store.backup(target)
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    # POSIX permission bits are not meaningful on Windows, which uses ACLs.
    # The backup-content assertion above must run on every OS.
    import os
    if os.name == "posix":
        assert target.stat().st_mode & 0o777 == 0o600
