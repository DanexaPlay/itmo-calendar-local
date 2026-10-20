"""Read *site-initiated* personal timetable responses in an authenticated browser.

The user signs in at ITMO.ID in a dedicated, persistent Playwright profile.
This module does not read browser cookies, OAuth tokens, localStorage or request
Authorization headers, and never saves raw HTTP traffic, screenshots or HAR.
Only HTTPS GET responses issued by the website to the exact personal schedule
API endpoint are inspected and normalized into the existing Snapshot model.

For unattended runs, Playwright can also expand the initial, site-initiated,
authenticated GET into a small set of additional, read-only GET requests, using
route.fetch with only date_start/date_end changed. No credential is exported,
read into Python or persisted. All requests stay on the exact ITMO endpoint.
The site may reject this behavior; then no local or iCloud update occurs.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from .common import SafeError, canonical, ensure_private_dir
from .config import API_URL, Settings
from .models import Lesson, Snapshot, parse_day, parse_response

SCHEDULE_PAGE = 'https://my.itmo.ru/schedule'
PERSONAL_PATH = urlsplit(API_URL).path


def window_ranges(first: date, last: date, length: int) -> list[tuple[date, date]]:
    """Split inclusive [first,last] into contiguous inclusive intervals."""
    if first > last or not 7 <= length <= 35:
        raise SafeError('browser_invalid_requested_window')
    windows = []
    current = first
    while current <= last:
        end = min(last, current + timedelta(days=length - 1))
        windows.append((current, end))
        current = end + timedelta(days=1)
    if len(windows) > 12:
        raise SafeError('browser_too_many_date_ranges')
    return windows


def week_aligned_windows(first: date, last: date, length: int) -> list[tuple[date, date]]:
    """Request full Mon-Sun weeks, like ITMO's observed 35-day month grid.

    We deliberately include a few extra boundary dates; BrowserCollector
    retains ONLY the configured rolling window. Range limits are inclusive.
    """
    if length % 7 != 0:
        raise SafeError('browser_range_days_must_be_full_weeks')
    monday = first - timedelta(days=first.weekday())
    sunday = last + timedelta(days=6 - last.weekday())
    return window_ranges(monday, sunday, length)


def window_url(site_request_url: str, first: date, last: date) -> str:
    """Update only date query parameters; retain other site-owned parameters."""
    if not is_personal_response_url(site_request_url):
        raise SafeError('browser_request_not_personal_schedule')
    requested_bounds(site_request_url)  # reject ambiguous/malformed dates
    parts = urlsplit(site_request_url)
    params = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
              if k not in ('date_start', 'date_end')]
    params.extend([('date_start', first.isoformat()), ('date_end', last.isoformat())])
    return urlunsplit(('https', 'my.itmo.ru', PERSONAL_PATH, urlencode(params), ''))


class BrowserRangeExpansion:
    """One interception of the site's own authorized GET, no token extraction.

    The page receives its unmodified original response. At most twelve extra
    requests go to the same first-party endpoint, with NO redirects followed.
    If anything fails, the caller rejects the whole run before updating SQLite.
    """
    def __init__(self, collector: 'BrowserCollector'):
        self.collector = collector
        self.windows = week_aligned_windows(collector.min_day, collector.max_day,
                                          collector.settings.browser_range_days)
        self.started = False
        self.done = False
        self.error: str | None = None
        self.fetched = 0

    def handle(self, route: Any) -> None:
        request = route.request
        try:
            if not is_personal_response_url(request.url) or request.method != 'GET':
                route.continue_()
                return
            if self.started:
                route.continue_()
                return
            self.started = True
            original = None
            try:
                # Same-origin request, original site-generated headers untouched.
                original = route.fetch(max_redirects=0, timeout=25000)
                if original.status == 200:
                    for first, last in self.windows:
                        url = window_url(request.url, first, last)
                        result = route.fetch(url=url, max_redirects=0, timeout=25000)
                        # The explicit same-origin URL plus zero redirects keeps
                        # browser credentials from being forwarded elsewhere.
                        if not is_personal_response_url(result.url):
                            raise SafeError('browser_expanded_response_redirected')
                        body = result.body() if result.status == 200 else b''
                        if len(body) > self.collector.settings.max_response_bytes:
                            raise SafeError('browser_schedule_response_too_large')
                        self.collector.observe_bytes(url, result.status, body)
                        self.fetched += 1
                        if self.collector.errors:
                            break
            except SafeError as error:
                self.error = error.code
            except Exception:
                # Never print Playwright errors: they can contain sensitive URLs.
                self.error = 'browser_expanded_request_failed'
            finally:
                try:
                    if original is not None:
                        route.fulfill(response=original)
                    else:
                        route.continue_()
                except Exception:
                    self.error = 'browser_original_request_restore_failed'
                self.done = True
        except Exception:
            self.error = 'browser_expanded_request_failed'
            self.done = True



def is_personal_response_url(url: str) -> bool:
    """Only exact secure ITMO API paths, never loosely matched substrings."""
    try:
        parts = urlsplit(url)
        return (parts.scheme == 'https' and parts.hostname == 'my.itmo.ru'
                and parts.port in (None, 443) and not parts.username and not parts.password
                and parts.path == PERSONAL_PATH and not parts.fragment)
    except (ValueError, TypeError):
        return False


def requested_bounds(url: str) -> tuple[date, date] | None:
    """Check request-supplied dates if present; do not trust arbitrary URL parameters."""
    query = parse_qs(urlsplit(url).query, keep_blank_values=True)
    a, b = query.get('date_start'), query.get('date_end')
    if a is None and b is None:
        return None
    if a is None or b is None or len(a) != 1 or len(b) != 1:
        raise SafeError('browser_request_date_query_changed')
    try:
        start, end = parse_day(a[0]), parse_day(b[0])
    except SafeError:
        raise SafeError('browser_request_date_query_changed') from None
    if not start <= end or (end - start).days > 366:
        raise SafeError('browser_request_date_query_changed')
    return start, end


class BrowserCollector:
    """Validate & merge response bodies; never treat missing days as empty."""
    def __init__(self, settings: Settings, *, today: date, full_coverage: bool = False):
        self.settings = settings
        self.min_day = today - timedelta(days=settings.past_days)
        self.max_day = today + timedelta(days=settings.future_days)
        self.full_coverage = full_coverage
        self.responses: list[Any] = []
        self.errors: list[str] = []
        self.last_seen_at = 0.0

    def observe(self, response: Any) -> None:
        try:
            if not is_personal_response_url(response.url) or response.request.method != 'GET':
                return
            self.last_seen_at = time.monotonic()
            if response.status in (401, 403):
                self.errors.append('browser_session_rejected_by_itmo')
            elif response.status == 429:
                self.errors.append('browser_schedule_rate_limited')
            elif response.status != 200:
                self.errors.append('browser_schedule_http_error')
            else:
                # An interactive login may begin with a 401 and then succeed.
                # During unattended full-range collection, NEVER clear errors
                # from another range or a failed/rate-limited API request.
                if not self.full_coverage:
                    self.errors = [e for e in self.errors
                                   if e != 'browser_session_rejected_by_itmo']
                self.responses.append(response)
        except (ValueError, TypeError, AttributeError):
            # Malformed/unexpected requests cannot turn into a successful import.
            self.errors.append('browser_response_metadata_invalid')

    def observe_bytes(self, url: str, status: int, body: bytes) -> None:
        """Memory-only response from Playwright route.fetch (no headers read)."""
        class _BytesResponse:
            request = SimpleNamespace(method='GET')
            def __init__(self, response_url: str, code: int, data: bytes):
                self.url, self.status, self._data = response_url, code, data
            def body(self):
                return self._data
        self.observe(_BytesResponse(url, status, body))

    def finish(self) -> Snapshot:
        if self.errors:
            raise SafeError(self.errors[0])
        if not self.responses:
            raise SafeError('browser_no_schedule_response_login_or_site_navigation_required')
        all_lessons: dict[tuple[str, str], Lesson] = {}
        by_day: dict[str, tuple[str, ...]] = {}
        reported: set[str] = set()
        for response in self.responses:
            raw = response.body()
            if not isinstance(raw, bytes) or len(raw) > self.settings.max_response_bytes:
                raise SafeError('browser_schedule_response_too_large')
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                raise SafeError('browser_schedule_not_json') from None
            if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
                raise SafeError('browser_schedule_schema_changed')
            bounds = requested_bounds(response.url)
            dates = []
            for item in payload['data']:
                if not isinstance(item, dict):
                    raise SafeError('browser_schedule_schema_changed')
                day = parse_day(item.get('date'))
                if bounds is not None and not bounds[0] <= day <= bounds[1]:
                    raise SafeError('browser_schedule_day_outside_query')
                dates.append(day)
            # No dates, no proof of absence; never treat as an empty timetable.
            if not dates:
                continue
            if (max(dates) - min(dates)).days > 366:
                raise SafeError('browser_schedule_date_range_excessive')
            # If API returns a wide window, only retain the configured local period.
            selected = [item for item in payload['data']
                        if self.min_day <= date.fromisoformat(item['date']) <= self.max_day]
            if not selected:
                continue
            selected_payload = dict(payload, data=selected)
            start = min(date.fromisoformat(item['date']) for item in selected)
            end = max(date.fromisoformat(item['date']) for item in selected)
            lessons, days = parse_response(selected_payload, start, end, self.settings.source_timezone)
            # Comparing normalized event data prevents conflicting overlapping
            # responses from silently replacing one another.
            for day in days:
                fingerprints = tuple(sorted(canonical(row.payload()) for row in lessons if row.day == day))
                if day in by_day and by_day[day] != fingerprints:
                    raise SafeError('browser_overlapping_responses_disagree')
                by_day[day] = fingerprints
                reported.add(day)
            for lesson in lessons:
                existing = all_lessons.get(lesson.key)
                if existing is not None and existing != lesson:
                    raise SafeError('browser_conflicting_lesson_identity')
                all_lessons[lesson.key] = lesson
            if len(all_lessons) > self.settings.max_events:
                raise SafeError('browser_too_many_events')
        if not reported:
            raise SafeError('browser_no_schedule_days_in_configured_window')
        if self.full_coverage:
            expected = { (self.min_day + timedelta(days=i)).isoformat()
                         for i in range((self.max_day - self.min_day).days + 1) }
            if reported != expected:
                raise SafeError('browser_expanded_date_coverage_incomplete')
        return Snapshot(min(reported), max(reported), tuple(all_lessons.values()),
                        frozenset(reported), ())


def browser_summary(snapshot: Snapshot) -> dict[str, Any]:
    """Non-sensitive diagnostic; no subjects, teachers, URLs, tokens or cookies."""
    return {'first_observed_day': snapshot.start, 'last_observed_day': snapshot.end,
            'days_reported_by_site': len(snapshot.reported_days),
            'lessons_received': len(snapshot.lessons),
            'days_without_events': sum(1 for day in snapshot.reported_days
                                       if not any(lesson.day == day for lesson in snapshot.lessons)),
            'only_observed_days_can_confirm_cancellations': True}


class BrowserSource:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _profile_dir(self) -> Path:
        directory = self.settings.data_dir
        ensure_private_dir(directory)
        profile = directory / 'itmo-browser-profile'
        if profile.is_symlink():
            raise SafeError('browser_profile_symlink_refused')
        profile.mkdir(parents=True, exist_ok=True)
        return profile

    def fetch(self, *, today: date | None = None, interactive: bool = False) -> Snapshot:
        """Interactive first login or headless site-initiated fetch.

        Browsing may be blocked by ITMO's security policy. We never bypass
        CAPTCHA, MFA, login checks, or manipulate the site's auth material.
        """
        today = today or datetime.now(ZoneInfo(self.settings.source_timezone)).date()
        collector = BrowserCollector(self.settings, today=today,
                                    full_coverage=not interactive)
        expansion = BrowserRangeExpansion(collector) if not interactive else None
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise SafeError('playwright_package_missing_run_pip_install') from None
        profile = self._profile_dir()
        try:
            with sync_playwright() as p:
                try:
                    opts = dict(user_data_dir=str(profile),
                                headless=not interactive,
                                accept_downloads=False,
                                service_workers='allow' if interactive else 'block',
                                timeout=45000)
                    if self.settings.browser_channel != 'chromium':
                        opts['channel'] = self.settings.browser_channel
                    context = p.chromium.launch_persistent_context(**opts)
                except Exception:
                    raise SafeError('browser_launch_failed_check_edge_or_profile_lock') from None
                try:
                    context.on('response', collector.observe)
                    if expansion is not None:
                        # Route only this first-party API, not ITMO.ID or any SSO.
                        context.route('**/api/schedule/schedule/personal*', expansion.handle)
                    page = context.pages[0] if context.pages else context.new_page()
                    if interactive:
                        print('В окне браузера войди в ITMO.ID и открой раздел расписания.')
                        print('Пароль, токены и cookies программа не запрашивает и не выводит.')
                        print('Окно закроется после получения ответа расписания.')
                    try:
                        page.goto(SCHEDULE_PAGE, wait_until='domcontentloaded', timeout=45000)
                    except Exception:
                        # A slow SSO redirect may time out after already opening
                        # the login page. Give the user time in interactive mode.
                        if not interactive:
                            raise SafeError('browser_schedule_page_unavailable') from None
                    deadline = time.monotonic() + (600 if interactive else self.settings.browser_timeout_seconds)
                    extended = False
                    refreshed = False
                    while time.monotonic() < deadline:
                        page.wait_for_timeout(600)
                        if expansion and expansion.started and not extended:
                            # A few sequential read-only range requests may be
                            # slower than normal page startup. Extend only
                            # after an actual site API request was intercepted.
                            deadline = max(deadline, time.monotonic() + 30 * len(expansion.windows) + 10)
                            extended = True
                        if expansion and expansion.error:
                            raise SafeError(expansion.error)
                        if collector.errors and not interactive:
                            break
                        if interactive and collector.responses and time.monotonic() - collector.last_seen_at > 3.0:
                            break
                        if (expansion and expansion.done and collector.responses and
                                time.monotonic() - collector.last_seen_at > 0.8):
                            break
                        if (not interactive and not refreshed and not expansion.started and
                                time.monotonic() + 10 > deadline):
                            refreshed = True
                            try:
                                page.reload(wait_until='domcontentloaded', timeout=15000)
                            except Exception:
                                pass
                    if expansion:
                        if expansion.error:
                            raise SafeError(expansion.error)
                        if not expansion.started:
                            raise SafeError('browser_range_bootstrap_request_not_seen')
                        if not expansion.done:
                            raise SafeError('browser_expanded_request_timeout')
                        if expansion.fetched != len(expansion.windows) and not collector.errors:
                            raise SafeError('browser_expanded_request_incomplete')
                    if not collector.responses and not collector.errors:
                        raise SafeError('browser_no_schedule_response_login_or_site_navigation_required')
                    return collector.finish()
                finally:
                    context.close()
        except SafeError:
            raise
        except Exception:
            # No raw Playwright exception: it can contain URLs/HTTP headers.
            raise SafeError('browser_unexpected_failure') from None
