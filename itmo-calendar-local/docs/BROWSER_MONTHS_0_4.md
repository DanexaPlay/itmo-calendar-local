# Version 0.4.0: full timetable date coverage (inclusive endpoints)

**Read this document instead of the old v0.3.0 partial-week caveat.**

## What changed

`browser check` and `sync-itmo` open the already authorized, dedicated Edge
browser profile in headless mode. The site makes its normal GET request to
`https://my.itmo.ru/api/schedule/schedule/personal`. After intercepting that
first-party request, Playwright's `route.fetch` also sends four additional
read-only GET requests to the **same** HTTPS API endpoint. Only the
`date_start` / `date_end` query parameters are changed, preserving any other
query parameters the site supplied. The original page gets its original
unmodified response. No Authorization values, cookies, or tokens are read,
printed, extracted to Python, or sent to another provider.

This is **not** identical to passively observing the website. It depends on
ITMO continuing to accept range queries by an authorized user. It is not an
officially documented integration, and it does not bypass or defeat ITMO's
login, MFA, rate limiting, redirects, or security checks. On failure, the
operation stops with a safe error and does not change existing iCloud events.

## Date arithmetic

For `PAST_DAYS=14`, `FUTURE_DAYS=90`, `BROWSER_RANGE_DAYS=35`,
the retained window is inclusive: `(14 + 1 + 90) = 105` dates.
Requests use full Monday-Sunday weeks, as observed in the site month view.
Four GETs retrieve 112 dates; extra boundary dates are discarded before
local processing. An October 20, 2026 test date corresponds to:

- 2026-10-05 to 2026-11-08 (35 days)
- 2026-11-09 to 2026-12-13 (35 days)
- 2026-12-14 to 2027-01-17 (35 days)
- 2027-01-18 to 2027-01-24 (7 days)

These are rolling ranges based on Moscow time. The published window is
2026-10-06 through 2027-01-18 inclusive (105 days). No day is duplicated
or lost at the boundaries. Different days or week grids in the original page are
accepted only when they overlap consistently with the requested ranges.

## Safety gates

- The expanded GETs use the exact HTTPS ITMO personal schedule endpoint,
  never a third-party host or a redirected location (`max_redirects=0`).
- Each request/response must be successful and preserve the expected JSON
  schema; 401, 403, 429, other errors, or malformed data abort everything.
- Every day in the 105-day window must actually be returned in the JSON
  (including days with an empty `lessons` list).
- Contradictory entries in overlapping page and expanded responses abort.
- No schedule data are published until all checks have passed.
- iCloud is not modified by `browser check` or `sync-itmo`. `icloud preview`
  is read-only. The explicit `icloud push` remains a separate operation.
- Daily background operation still depends on the laptop being powered on,
  the separate browser session remaining valid, and ITMO permitting this use.

## Install and verify

Extract the **patch ZIP** into the existing Windows project with file
replacement, leaving `.env`, `.venv`, and `%LOCALAPPDATA%` untouched.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m itmo_local browser check
.\.venv\Scripts\python.exe -m itmo_local sync-itmo
.\.venv\Scripts\python.exe -m itmo_local icloud preview
```

The first browser check on actual ITMO may show an error even when all unit
tests pass: we cannot validate your real signed-in session from here.
Check `first_observed_day`, `last_observed_day`, and `days_reported_by_site`.

Troubleshooting codes:

- `browser_range_bootstrap_request_not_seen`: the site did not issue a routable
  GET before the timeout. Verify login and avoid retry loops.
- `browser_expanded_request_failed`: ITMO/network/Playwright request failed;
  no cloud write happened. Inspect connectivity and retry later.
- `browser_expanded_date_coverage_incomplete`: at least one requested date was
  omitted, even though HTTP returned 200. Existing calendar stays unchanged.
- `browser_schedule_rate_limited`: stop, respect the server's rate limit.
- `browser_overlapping_responses_disagree`: the site returned inconsistent
  information for an overlapping date. Do not force deletion approvals.

The `browser login` command remains the **manual interactive login**, and is
allowed to observe only a week; use `browser check` for the full range.
