# ITMO Calendar Local

**Unofficial local Windows synchronization of your personal my.itmo class timetable to a dedicated iCloud calendar, visible in Apple Calendar on iPhone.** No VPS or paid subscription required.

**[Русская инструкция](README_RU.md)** · [Windows setup](docs/SETUP_WINDOWS_RU.md) (Russian) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md)

> **Public beta v0.5.1**, Windows 10/11, 64-bit Python 3.12 or 3.13, Microsoft Edge or Chrome, iCloud account. Not endorsed by ITMO University or Apple. Check ITMO's terms and authorization requirements before automating your account. Browser automation and undocumented endpoints may stop working at any time.

## Features

- Uses a dedicated persistent Edge/Chrome profile for ordinary interactive ITMO.ID login (including MFA); the Python application does **not** collect or export browser cookies or credentials.
- Fetches **14 days back plus 90 days ahead** (105 inclusive days) through four authorized, same-origin, date-range GET requests. Completeness and consistency checks protect against false cancellations.
- Synchronizes stable-UID iCalendar events to an **explicitly selected, initially empty** iCloud Calendar; previews are read-only, writes require opt-in during setup.
- Stores the Apple app-specific password using Windows DPAPI, timetable data in local SQLite, and browser session data only within the per-user browser profile.
- Optional Windows Task Scheduler: sign-in and 07:00 / 12:00 / 18:00 / 23:00 while logged in.

## Installation for non-developers

1. Install [64-bit Python 3.12/3.13](https://www.python.org/downloads/windows/), then ensure Edge or Chrome is installed.
2. At [iCloud Calendar](https://www.icloud.com/calendar/), create a **new empty dedicated calendar**, e.g. `ITMO`, and generate an Apple **app-specific password** via [Apple Account](https://account.apple.com/).
3. Download the Windows ZIP from **GitHub Releases** and extract it to a folder such as `C:\Apps\itmo-calendar-local` (avoid cloud-synced folders).
4. Run **`SETUP.cmd`**: it creates a local `.venv`, installs dependencies and asks for your non-secret account/calendar settings. No admin access needed.
5. Run **`FIRST_RUN.cmd`**: it guides you through entering the app-specific password, binding the empty calendar, signing in at the official site, reviewing the import preview and explicitly typing **`PUBLISH`** before any iCloud changes are applied.
6. Confirm your lessons in Apple Calendar and optionally enable scheduled sync.

The installer does not download or bundle Python, Chrome, Edge, Apple credentials or the ITMO login. An internet connection and manual ITMO authentication are required.

## Command-line usage

```powershell
.\.venv\Scripts\python.exe -m itmo_local browser login   # interactive university login
.\.venv\Scripts\python.exe -m itmo_local browser check   # read-only 105-day check
.\.venv\Scripts\python.exe -m itmo_local sync-itmo       # SQLite only
.\.venv\Scripts\python.exe -m itmo_local icloud preview  # read-only cloud preview
.\.venv\Scripts\python.exe -m itmo_local icloud push     # explicit cloud write
.\.venv\Scripts\python.exe -m itmo_local run             # unattended full sync
```

The Windows schedule works only with the computer on and the relevant user signed in; stale ITMO sessions require a fresh interactive login. iCloud propagation to the iPhone can also take time. The program cannot guarantee data correctness after the website changes.

## Development

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
python -m itmo_local demo
python tools/build_release.py --output dist/itmo-calendar-local-v0.5.1-windows.zip
```

Non-Windows runs support the offline synthetic demo and unit tests; real operations require Windows DPAPI. See [project architecture](docs/ARCHITECTURE_RU.md), [security policy](SECURITY.md), [contribution guide](CONTRIBUTING.md) and [MIT license](LICENSE).

**Privacy:** Never upload `.env`, SQLite files, `.dpapi` files, private app data, `itmo-browser-profile`, browser exports, network HAR files, session cookies or real class data to public issues or repositories. Use error codes and synthetic reproduction cases.
