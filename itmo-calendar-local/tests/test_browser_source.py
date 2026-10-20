"""Pure local tests: no ITMO login, browser, network, or iCloud access."""
from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace
import json

import pytest

from itmo_local.browser_source import (BrowserCollector, BrowserRangeExpansion, browser_summary,
                                        is_personal_response_url, requested_bounds,
                                        window_ranges, week_aligned_windows, window_url)
from itmo_local.common import SafeError
from itmo_local.models import Snapshot
from itmo_local.sync import Syncer
from conftest import lesson, rows

TODAY = date(2026, 10, 20)
BASE = 'https://my.itmo.ru/api/schedule/schedule/personal'
URL = BASE + '?date_start=2026-10-20&date_end=2026-10-28'


def sample(day='2026-10-21', *, room='101', pair_id=77):
    return {'code': 0, 'data': [
        {'date': day, 'lessons': [{'pair_id': pair_id, 'subject': 'Math',
                                  'time_start': '09:30', 'time_end': '11:00',
                                  'room': room}]}
    ]}


class FakeResponse:
    def __init__(self, body=None, *, url=URL, status=200, method='GET'):
        self.url = url
        self.status = status
        self.request = SimpleNamespace(method=method)
        self._body = json.dumps(sample() if body is None else body).encode()

    def body(self):
        return self._body


def collect(settings, *responses):
    result = BrowserCollector(settings, today=TODAY)
    for response in responses:
        result.observe(response)
    return result.finish()


@pytest.mark.parametrize('url,valid', [
    (URL, True),
    (BASE, True),
    ('http://my.itmo.ru/api/schedule/schedule/personal', False),
    ('https://evil.example/api/schedule/schedule/personal', False),
    ('https://my.itmo.ru.evil.example/api/schedule/schedule/personal', False),
    ('https://my.itmo.ru/api/schedule/schedule/personal2', False),
    ('https://my.itmo.ru:444/api/schedule/schedule/personal', False),
    ('https://user:pass@my.itmo.ru/api/schedule/schedule/personal', False),
])
def test_exact_api_filter(url, valid):
    assert is_personal_response_url(url) is valid


def test_one_observed_site_response_is_normalized(settings):
    snap = collect(settings, FakeResponse())
    assert len(snap.lessons) == 1
    assert snap.reported_days == {'2026-10-21'}
    assert snap.start == snap.end == '2026-10-21'
    assert snap.empty_chunks == ()
    assert browser_summary(snap)['lessons_received'] == 1
    assert 'subject' not in browser_summary(snap)


def test_does_not_accept_post_or_other_hosts(settings):
    collector = BrowserCollector(settings, today=TODAY)
    collector.observe(FakeResponse(method='POST'))
    collector.observe(FakeResponse(url='https://evil.example/api/schedule/schedule/personal'))
    with pytest.raises(SafeError, match='browser_no_schedule_response'):
        collector.finish()


@pytest.mark.parametrize('status,reason', [(401, 'session_rejected'), (403, 'session_rejected'),
                                          (429, 'rate_limited'), (500, 'http_error')])
def test_auth_errors_stop_sync_even_if_other_response_succeeded(settings, status, reason):
    collector = BrowserCollector(settings, today=TODAY)
    collector.observe(FakeResponse())
    collector.observe(FakeResponse(status=status))
    with pytest.raises(SafeError, match=reason):
        collector.finish()


def test_identical_overlap_is_safe(settings):
    snap = collect(settings, FakeResponse(), FakeResponse())
    assert len(snap.lessons) == 1
    assert len(snap.reported_days) == 1


def test_conflicting_overlap_stops_sync(settings):
    with pytest.raises(SafeError, match='browser_overlapping_responses_disagree'):
        collect(settings, FakeResponse(), FakeResponse(sample(room='212')))


@pytest.mark.parametrize('url', [
    BASE + '?date_start=oops&date_end=2026-10-21',
    BASE + '?date_start=2026-10-21',
    BASE + '?date_start=2026-10-22&date_end=2026-10-21',
    BASE + '?date_start=2026-01-01&date_end=2027-10-21',
    BASE + '?date_start=2026-10-21&date_start=2026-10-20&date_end=2026-10-22',
])
def test_invalid_request_date_params_rejected(url):
    with pytest.raises(SafeError, match='browser_request_date_query_changed'):
        requested_bounds(url)


def test_day_outside_query_fails_closed(settings):
    url = BASE + '?date_start=2026-10-20&date_end=2026-10-20'
    with pytest.raises(SafeError, match='browser_schedule_day_outside_query'):
        collect(settings, FakeResponse(url=url))


def test_empty_api_response_is_not_an_authoritative_cancellation(settings):
    with pytest.raises(SafeError, match='browser_no_schedule_days'):
        collect(settings, FakeResponse({'code':0,'data':[]}))


def test_api_response_with_wrong_schema_is_rejected(settings):
    with pytest.raises(SafeError, match='browser_schedule_schema_changed'):
        collect(settings, FakeResponse({'ok': True}))


def test_broken_content_is_rejected(settings):
    r = FakeResponse()
    r._body = b'<html>Login required</html>'
    with pytest.raises(SafeError, match='browser_schedule_not_json'):
        collect(settings, r)


def test_large_response_is_rejected(settings):
    r = FakeResponse()
    r._body = b'x' * (settings.max_response_bytes + 1)
    with pytest.raises(SafeError, match='browser_schedule_response_too_large'):
        collect(settings, r)


def test_unobserved_days_are_never_considered_missing(settings, store):
    real_settings = replace(settings, mode='myitmo', missing_min_seconds=60,
                            trust_complete_range=False)
    syncer = Syncer(real_settings, store, None)
    old = Snapshot('2026-10-21', '2026-10-22',
                   (lesson(pair_id=11, day='2026-10-21'),
                    lesson(pair_id=22, day='2026-10-22')),
                   frozenset({'2026-10-21', '2026-10-22'}), ())
    syncer.apply(old, now=12345678)
    current = collect(real_settings, FakeResponse(sample(day='2026-10-21', pair_id=11)))
    changed = syncer.apply(current, now=12348000)
    assert changed['cancelled'] == 0
    assert len([r for r in rows(store) if r['active']]) == 2


def test_only_recent_observed_days_pass_through(settings):
    data = {'code': 0, 'data': [
        {'date': '2026-07-01', 'lessons': []},
        {'date': '2026-10-21', 'lessons': []},
    ]}
    result = collect(settings, FakeResponse(data, url=BASE))
    assert result.reported_days == {'2026-10-21'}
    assert len(result.lessons) == 0


def test_successful_login_after_initial_401_clears_stale_auth_error(settings):
    collector = BrowserCollector(settings, today=TODAY)
    collector.observe(FakeResponse(status=401))
    collector.observe(FakeResponse(status=200))
    assert len(collector.finish().lessons) == 1


def test_105_day_window_has_three_inclusive_ranges():
    today = date(2026, 10, 20)
    first, last = today - timedelta(days=14), today + timedelta(days=90)
    windows = window_ranges(first, last, 35)
    assert windows == [(date(2026, 10, 6), date(2026, 11, 9)),
                       (date(2026, 11, 10), date(2026, 12, 14)),
                       (date(2026, 12, 15), date(2027, 1, 18))]
    days = [d for a, b in windows for d in
            (a + timedelta(days=i) for i in range((b-a).days+1))]
    assert len(days) == len(set(days)) == 105
    assert days[0] == first and days[-1] == last
    assert days[34] + timedelta(days=1) == days[35]
    assert days[69] + timedelta(days=1) == days[70]


def test_week_aligned_requests_cover_105_days_with_four_chunk_windows():
    first, last = date(2026, 10, 6), date(2027, 1, 18)
    result = week_aligned_windows(first, last, 35)
    assert result == [(date(2026, 10, 5), date(2026, 11, 8)),
                      (date(2026, 11, 9), date(2026, 12, 13)),
                      (date(2026, 12, 14), date(2027, 1, 17)),
                      (date(2027, 1, 18), date(2027, 1, 24))]
    assert all(a.weekday() == 0 and b.weekday() == 6 for a, b in result)
    days = {a + timedelta(days=i) for a, b in result
            for i in range((b - a).days + 1)}
    assert len(days) == 112
    assert all(first + timedelta(days=i) in days for i in range(105))
    with pytest.raises(SafeError, match='browser_range_days_must_be_full_weeks'):
        week_aligned_windows(first, last, 30)


def test_window_builder_exactly_35_and_36_days():
    base = date(2026, 9, 28)
    assert window_ranges(base, base + timedelta(days=34), 35) == [(base, base + timedelta(days=34))]
    assert window_ranges(base, base + timedelta(days=35), 35) == [
        (base, base + timedelta(days=34)),
        (base + timedelta(days=35), base + timedelta(days=35))]


def test_window_url_rewrites_only_dates_and_preserves_other_query():
    base = BASE + '?view=month&date_start=2026-09-28&date_end=2026-11-01'
    result = window_url(base, date(2026, 10, 6), date(2026, 11, 9))
    assert result == (BASE + '?view=month&date_start=2026-10-06&date_end=2026-11-09')
    assert requested_bounds(result) == (date(2026, 10, 6), date(2026, 11, 9))
    with pytest.raises(SafeError, match='browser_request_not_personal_schedule'):
        window_url('https://attacker.example/schedule', date(2026, 10, 6), date(2026, 11, 9))


def test_full_range_incomplete_coverage_fails_even_on_http_200(settings):
    collector = BrowserCollector(settings, today=TODAY, full_coverage=True)
    collector.observe(FakeResponse())
    with pytest.raises(SafeError, match='browser_expanded_date_coverage_incomplete'):
        collector.finish()


def test_full_range_does_not_clear_429_on_later_200(settings):
    collector = BrowserCollector(settings, today=TODAY, full_coverage=True)
    collector.observe(FakeResponse(status=429))
    collector.observe(FakeResponse())
    with pytest.raises(SafeError, match='browser_schedule_rate_limited'):
        collector.finish()


class FakeAuthRoute:
    def __init__(self, callback, *, fail_at=None, omit=None, modify=None):
        self.request = SimpleNamespace(method='GET', url=URL)
        self.callback = callback
        self.calls = []
        self.fulfilled = False
        self.fail_at = fail_at
        self.omit = omit
        self.modify = modify

    def fetch(self, **kwargs):
        assert kwargs['max_redirects'] == 0
        assert kwargs['timeout'] == 25000
        url = kwargs.get('url', self.request.url)
        self.calls.append(url)
        if self.fail_at and len(self.calls) == self.fail_at:
            raise ConnectionError('fake network failure')
        if len(self.calls) == 1:
            return FakeResponse(url=url)
        assert url.startswith(BASE + '?')
        a, b = requested_bounds(url)
        items = []
        for i in range((b-a).days + 1):
            day = a + timedelta(days=i)
            if self.omit == day:
                continue
            lessons = sample(day=day.isoformat())['data'][0]['lessons'] if day == date(2026, 10, 21) else []
            if self.modify == day:
                lessons = sample(day=day.isoformat(),room='changed')['data'][0]['lessons']
            items.append({'date':day.isoformat(), 'lessons':lessons})
        return FakeResponse({'code':0,'data':items},url=url)

    def fulfill(self, *, response):
        self.fulfilled = True
        self.callback(response)

    def continue_(self):
        raise AssertionError('Expected to fulfill original response')


def test_browser_expansion_three_ranges_through_site_headers(settings):
    collector = BrowserCollector(settings, today=TODAY, full_coverage=True)
    route = FakeAuthRoute(collector.observe)
    expander = BrowserRangeExpansion(collector)
    expander.handle(route)
    assert expander.done and expander.fetched == 4 and expander.error is None
    assert route.fulfilled
    assert len(route.calls) == 5  # site-generated GET plus four week-aligned windows
    assert all(url.startswith(BASE) for url in route.calls)
    assert len(collector.finish().reported_days) == 105
    assert len(collector.finish().lessons) == 1
    assert browser_summary(collector.finish())['last_observed_day'] == '2027-01-18'


def test_browser_expansion_rejects_omitted_final_day(settings):
    collector = BrowserCollector(settings, today=TODAY, full_coverage=True)
    route = FakeAuthRoute(collector.observe, omit=date(2027, 1, 18))
    expander = BrowserRangeExpansion(collector)
    expander.handle(route)
    assert expander.error is None and expander.fetched == 4
    with pytest.raises(SafeError, match='browser_expanded_date_coverage_incomplete'):
        collector.finish()


def test_browser_expansion_rejects_failed_second_window(settings):
    collector = BrowserCollector(settings, today=TODAY, full_coverage=True)
    route = FakeAuthRoute(collector.observe, fail_at=3)
    expander = BrowserRangeExpansion(collector)
    expander.handle(route)
    assert expander.error == 'browser_expanded_request_failed'
    assert expander.fetched == 1
    assert len(route.calls) == 3
    assert route.fulfilled


def test_browser_expansion_refuses_conflicting_overlap(settings):
    collector = BrowserCollector(settings, today=TODAY, full_coverage=True)
    route = FakeAuthRoute(collector.observe, modify=date(2026, 10, 21))
    expander = BrowserRangeExpansion(collector)
    expander.handle(route)
    with pytest.raises(SafeError, match='browser_overlapping_responses_disagree'):
        collector.finish()


def test_browser_source_launches_separate_headless_profile_and_observes_response(settings, monkeypatch):
    """End-to-end using fake Playwright with actual route callbacks, no network."""
    from contextlib import contextmanager
    import sys
    from itmo_local.browser_source import BrowserSource

    class FakePage:
        def __init__(self, context):
            self.context = context
        def goto(self, url, **kwargs):
            assert url == 'https://my.itmo.ru/schedule'
            self.context.auth_route = FakeAuthRoute(self.context.callback)
            self.context.route_callback(self.context.auth_route)
        def wait_for_timeout(self, _ms):
            pass

    class FakeContext:
        def __init__(self):
            self.pages = []
            self.closed = False
            self.callback = None
            self.route_callback = None
        def on(self, name, callback):
            assert name == 'response'
            self.callback = callback
        def route(self, pattern, callback):
            assert pattern == '**/api/schedule/schedule/personal*'
            self.route_callback = callback
        def new_page(self):
            return FakePage(self)
        def close(self):
            self.closed = True

    class FakeChromium:
        def __init__(self):
            self.last_args = None
            self.context = None
        def launch_persistent_context(self, **kwargs):
            self.last_args = kwargs
            self.context = FakeContext()
            return self.context

    chromium = FakeChromium()
    @contextmanager
    def fake_playwright():
        yield SimpleNamespace(chromium=chromium)
    monkeypatch.setitem(sys.modules, 'playwright.sync_api', SimpleNamespace(sync_playwright=fake_playwright))
    current = [1000.0]
    def fake_monotonic():
        current[0] += 1.0
        return current[0]
    monkeypatch.setattr('itmo_local.browser_source.time.monotonic', fake_monotonic)
    settings = replace(settings, browser_timeout_seconds=15)
    snap = BrowserSource(settings).fetch(today=TODAY)
    assert len(snap.lessons) == 1
    assert len(snap.reported_days) == 105
    assert snap.start == '2026-10-06'
    assert snap.end == '2027-01-18'
    assert chromium.last_args['headless'] is True
    assert chromium.last_args['channel'] == 'msedge'
    assert chromium.last_args['service_workers'] == 'block'
    assert chromium.last_args['user_data_dir'] == str(settings.data_dir / 'itmo-browser-profile')
    assert chromium.context.closed
    assert len(chromium.context.auth_route.calls) == 5


def test_browser_profile_symlink_is_refused(settings, tmp_path):
    from itmo_local.browser_source import BrowserSource
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    fake_target = tmp_path / 'target'
    fake_target.mkdir()
    link = settings.data_dir / 'itmo-browser-profile'
    try:
        link.symlink_to(fake_target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip('Symlinks unavailable on this filesystem')
    with pytest.raises(SafeError, match='browser_profile_symlink_refused'):
        BrowserSource(settings)._profile_dir()


def test_range_expansion_rejects_http_429_and_never_resets_error(settings):
    class ThrottledRoute(FakeAuthRoute):
        def fetch(self, **kwargs):
            if len(self.calls) == 2:
                url = kwargs['url']
                self.calls.append(url)
                return FakeResponse({'code': 0, 'data': []},url=url,status=429)
            return super().fetch(**kwargs)

    collector = BrowserCollector(settings, today=TODAY, full_coverage=True)
    route = ThrottledRoute(collector.observe)
    expander = BrowserRangeExpansion(collector)
    expander.handle(route)
    assert expander.error is None
    assert expander.fetched == 2
    assert route.fulfilled
    assert len(route.calls) == 3
    with pytest.raises(SafeError, match='browser_schedule_rate_limited'):
        collector.finish()


def test_range_expansion_never_follows_redirects(settings):
    class RedirectingRoute(FakeAuthRoute):
        def fetch(self, **kwargs):
            if 'url' in kwargs:
                self.calls.append(kwargs['url'])
                return FakeResponse({'code': 0, 'data': []},url=kwargs['url'],status=302)
            return super().fetch(**kwargs)

    collector = BrowserCollector(settings, today=TODAY, full_coverage=True)
    route = RedirectingRoute(collector.observe)
    expander = BrowserRangeExpansion(collector)
    expander.handle(route)
    assert expander.fetched == 1
    assert route.fulfilled
    with pytest.raises(SafeError, match='browser_schedule_http_error'):
        collector.finish()


def test_full_range_failure_does_not_replace_last_good_sqlite(settings, store):
    from itmo_local.sync import Syncer
    previous = Snapshot('2026-10-21', '2026-10-21',
                        (lesson(pair_id=1234, day='2026-10-21'),),
                        frozenset({'2026-10-21'}))
    good_syncer = Syncer(settings, store, None)
    good_syncer.apply(previous, now=1010)
    before = rows(store)
    class FailSource:
        def fetch(self):
            collector = BrowserCollector(settings, today=TODAY, full_coverage=True)
            collector.observe(FakeResponse())
            return collector.finish()
    with pytest.raises(SafeError, match='browser_expanded_date_coverage_incomplete'):
        Syncer(settings, store, FailSource()).run()
    assert rows(store) == before
