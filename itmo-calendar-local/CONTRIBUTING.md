# Contributing / Участие в разработке

Thanks for helping improve this unofficial project! Please keep issues and PRs
free of personal schedule data, session details and credentials.

## Start locally

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m itmo_local demo
```

The synthetic demo and tests may also be run on Linux without connecting to
Apple or ITMO. Actual syncing is currently Windows-only.

## Before a PR

1. Explain the expected behavior and add tests for changes to parsing,
   calendar writes, date coverage or deletion policy.
2. Keep remote-calendar changes fail-closed. Never silently widen the set of
   calendars that can be modified, disable date coverage checks, or weaken
   authorization/anti-automation controls.
3. Do not include request headers, HAR captures, real JSON timetable responses,
   browser profile exports or accounts. Use synthetic fixtures only.
4. Run `python -m pytest -q` and `python -m itmo_local demo`.
5. Update README and CHANGELOG for user-facing changes.
6. Do not add analytics, telemetry, hidden network targets, or secret export.

## Code scope

- `itmo_local`: synchronization logic and CLI.
- `scripts`: Windows install, initial setup and scheduled tasks.
- `tools`: reproducible release packaging and checksums.
- `.github/workflows`: tests and GitHub releases.

The MIT license applies to your contribution. This project has **no formal
endorsement or support from ITMO University or Apple**. Follow the applicable
terms of the services you connect to.
