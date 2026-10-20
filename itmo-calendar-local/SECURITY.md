# Security policy / Политика безопасности

**Unofficial software. Do not send real account credentials, app-specific passwords,
ITMO cookies, browser profiles, exported HAR files or personal class data in bug
reports.**

## Reporting security problems

Before a public release, the repository maintainer should enable **GitHub
Settings → Security → Private vulnerability reporting**. Please submit potential
vulnerabilities privately through the GitHub repository's **Security** tab
instead of opening a public Issue. If that feature is unavailable, contact the
maintainer through a non-public channel listed in their GitHub profile. Never
include live credentials even in an initial report: first explain the impact
using fake/synthetic data.

## Support and threat model

- Supported for security fixes: latest `0.5.x` beta; earlier versions should be
  upgraded once a later release is tested.
- **Windows DPAPI** protects the saved Apple app-specific password against
  straightforward file copying to other accounts. It does not protect against
  malware executing as the same Windows user.
- The **Playwright browser profile** under
  `%LOCALAPPDATA%\PersonalItmoCalendar\itmo-browser-profile` contains a
  persistent ITMO session, which can include sensitive cookies and local
  storage. Treat the entire directory as secret; disk encryption is advised.
- The **SQLite database is not application-encrypted** and contains personal
  schedule details. Do not upload it or put it into shared cloud folders.
- The Apple app-specific password can grant broader CalDAV access than the
  calendar selected by this software. The program is designed to modify only
  the explicitly bound empty calendar, but this is a software safety control,
  not an Apple credential scope restriction.
- Browser automation is not an officially approved university integration.
  It uses ITMO's regular sign-in flow and first-party endpoints; it does not
  bypass MFA, CAPTCHA, rate limits or access checks. If access is denied, stop.
- If a machine or browser profile may have been compromised: revoke the
  Apple app-specific password in Apple Account and terminate ITMO sessions
  through the university's supported account interface. Removing local files
  alone does not revoke remote credentials.

## GitHub repository hygiene

Do not commit `.env`, `.venv`, `*.dpapi`, `*.sqlite3`, personal backups,
`itmo-browser-profile/`, `*.har`, tokens or cookie exports. The release builder
uses an **explicit allowlist**, but maintainers must still review changed
source and documents for manually pasted secrets before publishing. CI never
contacts real ITMO or iCloud accounts and should never contain those secrets.

Read [docs/SETUP_WINDOWS_RU.md](docs/SETUP_WINDOWS_RU.md) for safe removal.
