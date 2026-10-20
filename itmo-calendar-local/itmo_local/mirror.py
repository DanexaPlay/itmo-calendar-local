"""Reconcile an explicitly bound, dedicated iCloud CalDAV calendar.

No unrelated events or calendars are modified, ever. If DB state is lost,
known remote events become orphans and *stop* the sync instead of duplicating.
"""
from __future__ import annotations

import hashlib
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterator

from .common import SafeError
from .config import Settings
from .ical import render_event
from .store import Store

MANAGED_SUFFIX = '@personal-itmo-calendar'


def _utc(value) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise SafeError('icloud_event_missing_timezone')
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        raise SafeError('icloud_unexpected_all_day_event')
    raise SafeError('icloud_event_invalid_date')


def extract_signature(data: str | bytes) -> tuple[str, tuple]:
    """Parse semantics instead of comparing iCloud's reformatted ICS text.

    Server-generated TZID, DTSTAMP, sequence, scheduling flags and reminders
    may differ. We do compare changes important for the class timetable.
    """
    try:
        from icalendar import Calendar
        calendar = Calendar.from_ical(data)
        events = calendar.walk('VEVENT')
        if len(events) != 1:
            raise SafeError('icloud_multi_component_event_unexpected')
        comp = events[0]
        uid = str(comp.get('UID', ''))
        if not uid or len(uid) > 400:
            raise SafeError('icloud_event_missing_uid')
        fields = (_utc(comp.decoded('DTSTART')), _utc(comp.decoded('DTEND')),
                  str(comp.get('SUMMARY', '')), str(comp.get('DESCRIPTION', '')),
                  str(comp.get('LOCATION', '')))
        return uid, fields
    except SafeError:
        raise
    except Exception:
        raise SafeError('icloud_invalid_icalendar') from None


@dataclass
class Plan:
    create: list[tuple[str, str]]
    update: list[tuple[Any, str]]
    delete: list[Any]
    unchanged: int

    def counts(self) -> dict[str, int]:
        return {'create': len(self.create), 'update': len(self.update),
                'delete': len(self.delete), 'unchanged': self.unchanged}


class ICloudMirror:
    def __init__(self, settings: Settings, store: Store, protected):
        self.settings = settings
        self.store = store
        self.protected = protected

    @contextmanager
    def connection(self) -> Iterator[Any]:
        if not self.settings.apple_id:
            raise SafeError('icloud_apple_id_missing_from_settings')
        password = self.protected.get('icloud_password')
        if not password:
            raise SafeError('icloud_app_password_not_configured')
        try:
            import caldav
        except ImportError:
            raise SafeError('caldav_package_not_installed') from None
        try:
            # No app-specific password is sent to any host but Apple's CalDAV.
            with caldav.DAVClient(url='https://caldav.icloud.com/',
                                  username=self.settings.apple_id,
                                  password=password, timeout=30) as client:
                principal = client.principal()
                calendars = principal.calendars()
                matches = [item for item in calendars
                           if item.get_display_name() == self.settings.calendar_name]
                if len(matches) != 1:
                    raise SafeError('icloud_calendar_missing_or_duplicate_name')
                yield matches[0]
        except SafeError:
            raise
        except Exception:
            # Do not print CalDAV exceptions; they can include account URLs,
            # credentials, event contents or Authorization headers.
            raise SafeError('icloud_caldav_connection_or_request_failed') from None

    def binding(self) -> dict | None:
        return self.store.metadata('icloud_binding')

    def _account_tag(self) -> str:
        return hashlib.sha256(self.settings.apple_id.casefold().encode('utf-8')).hexdigest()

    def _verify_binding(self, calendar: Any) -> None:
        binding = self.binding()
        if binding is None:
            raise SafeError('icloud_calendar_not_bound_run_icloud_bind')
        if binding.get('url') != str(calendar.url) or binding.get('account_tag') != self._account_tag():
            raise SafeError('icloud_binding_changed_refusing_to_write')

    def check(self) -> dict[str, int]:
        """Read-only connectivity check. No calendar is created or modified."""
        with self.connection() as calendar:
            count = len(calendar.get_events())
            return {'events_in_chosen_calendar': count, 'bound': bool(self.binding())}

    def bind_empty(self) -> None:
        """Explicit one-time consent to modify this exact *empty* calendar."""
        if self.binding():
            raise SafeError('icloud_calendar_already_bound')
        with self.connection() as calendar:
            if calendar.get_events():
                raise SafeError('icloud_bind_requires_empty_dedicated_calendar')
            self.store.update_meta(icloud_binding={'url': str(calendar.url),
                                                   'account_tag': self._account_tag(),
                                                   'name': self.settings.calendar_name})

    def prepare(self, local_rows: list[dict], remote_events: list[Any]) -> Plan:
        known = {row['uid']: row for row in local_rows}
        remote: dict[str, tuple[Any, tuple]] = {}
        # Fail closed *before* any mutation if the selected calendar includes
        # unrelated items, unknown UIDs or ambiguous duplicate UIDs.
        for obj in remote_events:
            uid, signature = extract_signature(obj.data)
            if not uid.endswith(MANAGED_SUFFIX):
                raise SafeError('icloud_calendar_contains_non_itmo_events')
            if uid not in known:
                raise SafeError('icloud_orphan_managed_event_missing_local_db')
            if uid in remote:
                raise SafeError('icloud_duplicate_event_uid')
            remote[uid] = (obj, signature)
        plan = Plan([], [], [], 0)
        for uid, row in known.items():
            existing = remote.get(uid)
            if not row['active']:
                if existing is not None:
                    plan.delete.append(existing[0])
                continue
            expected = render_event(row, self.settings)
            if existing is None:
                plan.create.append((uid, expected))
            elif existing[1] != extract_signature(expected)[1]:
                plan.update.append((existing[0], expected))
            else:
                plan.unchanged += 1
        return plan

    def push(self, *, dry_run: bool = False, allow_large_deletion: bool = False) -> dict[str, int]:
        if self.settings.mode == 'demo':
            raise SafeError('refuse_to_publish_demo_lessons_to_real_icloud')
        if self.store.metadata('source_mode') != 'myitmo' or not self.store.metadata('last_success'):
            raise SafeError('real_timetable_not_yet_synchronized')
        with self.store.connect() as conn:
            local_rows = self.store.rows(conn)
        if not local_rows:
            raise SafeError('no_local_events_refusing_empty_mirror')
        if not self.binding():
            raise SafeError('icloud_calendar_not_bound_run_icloud_bind')
        try:
            with self.connection() as calendar:
                self._verify_binding(calendar)
                remote = calendar.get_events()
                plan = self.prepare(local_rows, remote)
                counts = plan.counts()
                # Unexpected mass deletions need a separate explicit approval.
                if len(plan.delete) > 10 and not allow_large_deletion:
                    raise SafeError('icloud_many_deletions_need_explicit_confirmation')
                if len(plan.create) + len(plan.update) + len(plan.delete) > 500:
                    raise SafeError('icloud_too_many_operations_need_review')
                if dry_run:
                    return counts
                # Resilient to partial network failures. The next run lists
                # remote events and reconciles by stable UID, not by a local
                # flag that might have been committed before the remote PUT.
                for _uid, ics in plan.create:
                    # Avoid caldav's no_overwrite=True UID lookup (REPORT),
                    # which is not reliably supported by Apple iCloud.
                    # prepare() has already read all remote events, rejects
                    # unknown UIDs and creates only missing stable UIDs.
                    calendar.add_event(ical=ics)
                for event, ics in plan.update:
                    event.data = ics
                    event.save(increase_seqno=False)
                for event in plan.delete:
                    event.delete()
            self.store.update_meta(last_push=time.time(), last_push_error=None, last_push_counts=counts)
            return counts
        except SafeError as error:
            if not dry_run:
                self.store.update_meta(last_push_error=error.code)
            raise
        except Exception:
            if not dry_run:
                self.store.update_meta(last_push_error='icloud_sync_failed')
            raise SafeError('icloud_sync_failed') from None
