# Changelog

## 0.5.0 — public beta (2026-10)

- First GitHub-ready public repository; MIT license, privacy/security guides,
  English/Russian README, issue templates, and CI.
- `SETUP.cmd` installs dependencies in an isolated Windows virtual environment,
  checks Python version and initializes `.env` without overwriting user data.
- `FIRST_RUN.cmd` provides a guided secure first run. It requires `BIND` before
  binding an empty iCloud calendar and `PUBLISH` before posting events.
- Reproducible allowlist-only Windows release ZIP builder and GitHub Release
  automation.
- Core synchronization logic retained from the successfully used v0.4.0.

## 0.4.0 — multi-month source

- Read a 105-day rolling timetable window using full-week date ranges.
- Strict completeness, overlap and error checking before SQLite/cloud writes.

## 0.3.0 — browser sign-in

- Added a dedicated Playwright browser profile with manual ITMO.ID sign-in and
  unattended background checks.

## 0.2.x — local iCloud CalDAV

- Local Windows secrets via DPAPI, separate dedicated calendar binding,
  preview/write reconciliation, and duplicate/mass-delete protections.
