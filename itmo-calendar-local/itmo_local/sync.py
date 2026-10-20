from __future__ import annotations

import logging
import time
import uuid
from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Any

from .common import SafeError, canonical, digest, file_lock, write_json
from .config import Settings
from .models import Lesson, Snapshot
from .source import Source
from .store import Store

LOG = logging.getLogger("itmo_calendar")


class Syncer:
    def __init__(self, settings: Settings, store: Store, source: Source):
        self.settings, self.store, self.source = settings, store, source

    def run(self, *, approve_digest: str | None = None) -> dict[str, int]:
        with file_lock(self.settings.data_dir / "sync.lock", blocking=False):
            self.store.update_meta(last_attempt=time.time())
            try:
                snapshot = self.source.fetch()
                result = self.apply(snapshot, approve_digest=approve_digest)
                self.store.update_meta(next_sync=time.time() + self.settings.sync_seconds)
                LOG.info("sync_ok added=%d changed=%d cancelled=%d held=%d", result["added"], result["changed"],
                         result["cancelled"], result["held"])
                return result
            except SafeError as error:
                attempts = int(self.store.metadata("failure_count", 0)) + 1
                backoff = min(self.settings.retry_seconds * 2 ** min(attempts - 1, 5), self.settings.sync_seconds)
                delay = max(backoff, error.retry_after)
                self.store.update_meta(last_error=error.code, failure_count=attempts, next_sync=time.time() + delay)
                LOG.warning("sync_failed code=%s", error.code)
                raise
            except Exception:
                self.store.update_meta(last_error="unexpected_internal_error", next_sync=time.time() + self.settings.retry_seconds)
                LOG.error("sync_failed code=unexpected_internal_error")
                raise SafeError("unexpected_internal_error") from None

    def apply(self, snapshot: Snapshot, *, approve_digest: str | None = None, now: float | None = None) -> dict[str, int]:
        """Update only local SQLite; cloud mirror is a separate recoverable step.

        Caller must hold sync.lock outside of unit tests. An upstream HTTP 200
        cannot prove completeness: missing-day guards fail closed.
        """
        now = time.time() if now is None else now
        checksum = snapshot.checksum
        approved = approve_digest is not None and approve_digest == checksum
        if approve_digest is not None and not approved:
            raise SafeError("approval_digest_no_longer_matches")
        if len({lesson.key for lesson in snapshot.lessons}) != len(snapshot.lessons):
            raise SafeError("duplicate_snapshot_identity")
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            origin_mode = self.store.get_meta(connection, "source_mode")
            if origin_mode is not None and origin_mode != self.settings.mode:
                raise SafeError("state_mode_mismatch_use_separate_demo_state")
            previous = self.store.rows(connection)
            by_key = {(row["match_id"], row["day"]): row for row in previous}
            by_id: dict[str, list[dict]] = defaultdict(list)
            for row in previous:
                by_id[row["match_id"]].append(row)
            new_counts = Counter(lesson.match_id for lesson in snapshot.lessons)
            reused = {row[0] for row in connection.execute("SELECT match_id FROM reused_ids")}
            reused.update(key for key, count in new_counts.items() if count > 1)
            reused.update(key for key, rows in by_id.items() if len(rows) > 1)
            matched_uids: set[str] = set()
            matches: list[tuple[Lesson, dict | None]] = []
            # Exact occurrence identity takes priority over cross-day moves.
            unmatched_lessons = []
            for lesson in snapshot.lessons:
                match = by_key.get(lesson.key)
                if match is not None:
                    matched_uids.add(match["uid"])
                    matches.append((lesson, match))
                else:
                    unmatched_lessons.append(lesson)
            for lesson in unmatched_lessons:
                candidates = [row for row in by_id.get(lesson.match_id, [])
                              if row["uid"] not in matched_uids and snapshot.start <= row["day"] <= snapshot.end]
                match = None
                if (lesson.source_id and lesson.match_id not in reused and new_counts[lesson.match_id] == 1
                        and len(candidates) == 1):
                    match = candidates[0]
                    matched_uids.add(match["uid"])
                matches.append((lesson, match))
            old_in_range = [row for row in previous if row["active"] and snapshot.start <= row["day"] <= snapshot.end]
            missing = [row for row in old_in_range if row["uid"] not in matched_uids]
            authoritative_missing = [row for row in missing if approved or self.settings.trust_complete_range or
                                     row["day"] in snapshot.reported_days]
            held_unknown = len(missing) - len(authoritative_missing)
            reason = None
            if not snapshot.lessons and not previous and not self.settings.allow_empty_initial:
                reason = "empty_initial_timetable"
            elif not snapshot.lessons and old_in_range:
                reason = "unexpected_empty_timetable"
            elif any(any(begin <= row["day"] <= end for row in missing) for begin, end in snapshot.empty_chunks):
                reason = "previously_populated_chunk_is_empty"
            elif (len(authoritative_missing) >= self.settings.min_mass_drop and old_in_range
                  and len(authoritative_missing) / len(old_in_range) >= self.settings.max_drop_fraction):
                reason = "mass_disappearance"
            if reason is not None and not approved:
                # Keep the last good feed. Store only normalized fields, not the
                # original response (which can contain unnecessary private data).
                connection.rollback()
                # Quarantine only metadata; do not create an extra plaintext copy of the timetable.
                self.store.update_meta(pending_digest=checksum, pending_reason=reason)
                raise SafeError("snapshot_quarantined_" + reason)
            counts = {"added": 0, "changed": 0, "cancelled": 0, "held": held_unknown,
                      "without_source_id": sum(not lesson.source_id for lesson in snapshot.lessons)}
            records = {row["uid"]: row for row in previous}
            for lesson, row in matches:
                payload = lesson.payload()
                fingerprint = digest(payload)
                if row is None:
                    row = {"uid": str(uuid.uuid4()) + "@personal-itmo-calendar", "match_id": lesson.match_id,
                           "day": lesson.day, "payload": payload, "fingerprint": fingerprint,
                           "active": 1, "sequence": 0, "created": now, "modified": now,
                           "missing_count": 0, "missing_since": None, "last_missing_at": None}
                    counts["added"] += 1
                else:
                    changed = fingerprint != row["fingerprint"] or not row["active"]
                    if changed:
                        row["sequence"] += 1
                        row["modified"] = now
                        counts["changed"] += 1
                    row.update(match_id=lesson.match_id, day=lesson.day, payload=payload,
                               fingerprint=fingerprint, active=1, missing_count=0, missing_since=None, last_missing_at=None)
                records[row["uid"]] = row
            missing_ids = {row["uid"] for row in authoritative_missing}
            for row in missing:
                if row["uid"] not in missing_ids:
                    # An unreported day is not a confirmation; break the streak.
                    row.update(missing_count=0, missing_since=None, last_missing_at=None)
                    continue
                last_confirmation = row["last_missing_at"]
                if last_confirmation is None or now - last_confirmation >= self.settings.missing_min_seconds:
                    row["missing_count"] += 1
                    row["last_missing_at"] = now
                    if row["missing_since"] is None:
                        row["missing_since"] = now
                if approved or row["missing_count"] >= self.settings.missing_confirmations:
                    row.update(active=0, modified=now, sequence=row["sequence"] + 1)
                    counts["cancelled"] += 1
                else:
                    counts["held"] += 1
            # Keep UID history indefinitely. Dropping a record here would make
            # an iCloud event orphaned and could create duplicates after a move.
            # The SQLite database grows slowly (one row per lesson occurrence).
            # Rebuild inside one transaction: this avoids transient UNIQUE-key
            # collisions during a source's re-identification of an occurrence.
            connection.execute("DELETE FROM events")
            columns = ("uid", "match_id", "day", "payload", "fingerprint", "active", "sequence", "created", "modified",
                       "missing_count", "missing_since", "last_missing_at")
            for row in records.values():
                values = [canonical(row[key]) if key == "payload" else row[key] for key in columns]
                connection.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", values)
            for match_id in reused:
                connection.execute("INSERT OR IGNORE INTO reused_ids VALUES(?)", (match_id,))
            for key, value in {"source_mode": self.settings.mode, "last_success": now, "last_error": None, "failure_count": 0, "last_counts": counts,
                               "pending_digest": None, "pending_reason": None, "last_snapshot_digest": checksum}.items():
                self.store.set_meta(connection, key, value)
            connection.commit()
        return counts
