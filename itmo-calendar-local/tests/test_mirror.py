"""No internet, iCloud or credential access. Fake CalDAV objects."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace

import pytest

from conftest import lesson, rows, snapshot
from itmo_local.common import SafeError
from itmo_local.mirror import ICloudMirror
from itmo_local.sync import Syncer


def fake_signature(data):
    """A deterministic test-only ICS parser; production uses icalendar."""
    lines = str(data).replace('\r\n ', '').split('\r\n')
    props = {}
    for line in lines:
        if ':' in line:
            key, _, value = line.partition(':')
            props[key.split(';')[0]] = value
    return (props['UID'], tuple(props.get(field, '') for field in
                                 ['DTSTART', 'DTEND', 'SUMMARY', 'DESCRIPTION', 'LOCATION']))


class FakeEvent:
    def __init__(self, ics, calendar):
        self.data = ics
        self.calendar = calendar
        self.save_count = 0
        self.delete_count = 0

    def save(self, **kwargs):
        uid, _ = fake_signature(self.data)
        self.save_count += 1
        self.calendar.events[uid] = self

    def delete(self):
        uid, _ = fake_signature(self.data)
        self.delete_count += 1
        del self.calendar.events[uid]


class FakeCalendar:
    url = 'https://p11-caldav.icloud.com/testuser/calendars/itmo/'

    def __init__(self):
        self.events = {}
        self.created = []
        self.fail_after = None

    def get_events(self):
        return list(self.events.values())

    def add_event(self, ical, no_overwrite=False, increase_seqno=None):
        if no_overwrite:
            # iCloud may reject the extra UID lookup with ReportError.
            raise RuntimeError('simulated_uid_report_error')
        uid, _ = fake_signature(ical)
        if uid in self.events:
            raise ValueError('duplicate UID')
        if self.fail_after is not None and len(self.created) >= self.fail_after:
            raise ConnectionError('simulated drop')
        self.created.append(uid)
        self.events[uid] = FakeEvent(ical, self)
        return self.events[uid]


class FakeSecrets:
    def get(self, key):
        return 'FAKE' if key == 'icloud_password' else None


@pytest.fixture
def env(settings, store, monkeypatch):
    new_settings = replace(settings, mode='myitmo', apple_id='unit-test@icloud.com', calendar_name='ИТМО')
    syncer = Syncer(new_settings, store, None)
    server = FakeCalendar()
    bridge = ICloudMirror(new_settings, store, FakeSecrets())
    @contextmanager
    def connection():
        yield server
    monkeypatch.setattr(bridge, 'connection', connection)
    monkeypatch.setattr('itmo_local.mirror.extract_signature', fake_signature)
    store.update_meta(icloud_binding={'url':server.url, 'account_tag': bridge._account_tag(), 'name': 'ИТМО'})
    return new_settings, store, syncer, server, bridge


def test_first_push_repeat_is_idempotent(env, now):
    _, store, syncer, cloud, mirror = env
    syncer.apply(snapshot(lesson(), lesson(201, day='2026-10-17')), now=now)
    assert mirror.push() == {'create':2,'update':0,'delete':0,'unchanged':0}
    assert len(cloud.events) == 2
    assert mirror.push() == {'create':0,'update':0,'delete':0,'unchanged':2}
    assert len(cloud.created) == 2


def test_reschedule_preserves_uid_and_updates_existing(env, now):
    _, store, syncer, cloud, mirror = env
    syncer.apply(snapshot(lesson()), now=now)
    mirror.push()
    uid = rows(store)[0]['uid']
    syncer.apply(snapshot(lesson(room='55', day='2026-10-16')), now=now + 10)
    result = mirror.push()
    assert result['update'] == 1
    assert result['create'] == 0
    assert list(cloud.events) == [uid]
    assert 'LOCATION:55' in cloud.events[uid].data


def test_confirmed_cancellation_deletes_only_owned_event(env, now):
    _, store, syncer, cloud, mirror = env
    a, b = lesson(), lesson(222, day='2026-10-16')
    syncer.apply(snapshot(a,b), now=now)
    mirror.push()
    missing = snapshot(b, reported={a.day,b.day})
    syncer.apply(missing, now=now + 65)
    assert mirror.push()['delete'] == 0
    syncer.apply(missing, now=now + 2000)
    assert mirror.push()['delete'] == 1
    assert len(cloud.events) == 1


def test_partial_cloud_failure_is_retried_without_duplicates(env, now):
    _, store, syncer, cloud, mirror = env
    syncer.apply(snapshot(lesson(),lesson(222, day='2026-10-16')), now=now)
    cloud.fail_after = 1
    with pytest.raises(SafeError, match='icloud_sync_failed'):
        mirror.push()
    assert len(cloud.events) == 1
    cloud.fail_after = None
    assert mirror.push()['create'] == 1
    assert len(cloud.events) == 2
    assert len(set(cloud.created)) == 2


def test_orphan_remote_event_blocks_modification(env, now):
    _, store, syncer, cloud, mirror = env
    syncer.apply(snapshot(lesson()), now=now)
    cloud.events['orphan@personal-itmo-calendar'] = FakeEvent(
        'BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:orphan@personal-itmo-calendar\r\nEND:VEVENT\r\nEND:VCALENDAR', cloud)
    with pytest.raises(SafeError, match='orphan_managed'):
        mirror.push()
    assert not cloud.created


def test_unmanaged_event_blocks_modification(env, now):
    _, store, syncer, cloud, mirror = env
    syncer.apply(snapshot(lesson()), now=now)
    cloud.events['personal@example.com'] = FakeEvent(
        'BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:personal@example.com\r\nEND:VEVENT\r\nEND:VCALENDAR', cloud)
    with pytest.raises(SafeError, match='non_itmo_events'):
        mirror.push()
    assert not cloud.created


def test_dry_run_no_writes(env, now):
    _, store, syncer, cloud, mirror = env
    syncer.apply(snapshot(lesson()), now=now)
    assert mirror.push(dry_run=True)['create'] == 1
    assert not cloud.events


def test_binding_change_blocks_writes(env, now):
    _, store, syncer, cloud, mirror = env
    syncer.apply(snapshot(lesson()), now=now)
    store.update_meta(icloud_binding={'url': 'https://other.example/', 'account_tag': mirror._account_tag()})
    with pytest.raises(SafeError, match='binding_changed'):
        mirror.push()
    assert not cloud.events


def test_mass_delete_requires_explicit_confirmation(env, now):
    _, store, syncer, cloud, mirror = env
    lessons = [lesson(i+1, day='2026-10-15', time_start=f'{i+7:02}:00', time_end=f'{i+8:02}:00') for i in range(12)]
    syncer.apply(snapshot(*lessons), now=now)
    mirror.push()
    many = snapshot(*lessons[:1], reported={'2026-10-15'})
    syncer.apply(many, approve_digest=many.checksum, now=now+100)
    with pytest.raises(SafeError, match='many_deletions'):
        mirror.push()
    assert len(cloud.events) == 12
    assert mirror.push(allow_large_deletion=True)['delete'] == 11


def test_no_real_snapshot_refuses_cloud_publish(env):
    _, store, syncer, cloud, mirror = env
    with pytest.raises(SafeError, match='real_timetable_not_yet_synchronized'):
        mirror.push()


def test_demo_mode_cannot_publish(settings, store):
    mirror = ICloudMirror(settings, store, FakeSecrets())
    with pytest.raises(SafeError, match='refuse_to_publish_demo'):
        mirror.push()
