from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .common import SafeError

ISSUER = "https://id.itmo.ru/auth/realms/itmo"
API_URL = "https://my.itmo.ru/api/schedule/schedule/personal"


def default_data_dir() -> Path:
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA")
        if not root:
            raise SafeError("windows_localappdata_not_found")
        return Path(root) / "PersonalItmoCalendar"
    return Path(os.getenv("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))) / "personal-itmo-calendar"


def load_env_file(path: Path = Path(".env")) -> None:
    """Parse plain settings only. Shell expansion and arbitrary code disabled."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise SafeError("invalid_env_file")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key in {"PASSWORD", "ACCESS_TOKEN", "REFRESH_TOKEN", "APPLE_PASSWORD", "CLIENT_SECRET"}:
            raise SafeError("secrets_must_not_be_in_env_file")
        os.environ.setdefault(key, value)


def integer(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(os.getenv(name, str(default)))
    except ValueError:
        raise SafeError("invalid_setting_" + name) from None
    if not minimum <= number <= maximum:
        raise SafeError("invalid_setting_" + name)
    return number


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    mode: str = "demo"
    client_id: str = ""
    itmo_auth_method: str = "browser"
    browser_channel: str = "msedge"
    browser_timeout_seconds: int = 35
    browser_range_days: int = 35
    scopes: str = "openid"
    redirect_uri: str = "http://127.0.0.1:8765/callback"
    apple_id: str = ""
    calendar_name: str = "ИТМО"
    source_timezone: str = "Europe/Moscow"
    sync_seconds: int = 10800
    retry_seconds: int = 300
    past_days: int = 14
    future_days: int = 90
    chunk_days: int = 7
    missing_confirmations: int = 2
    missing_min_seconds: int = 1800
    max_drop_fraction: float = 0.5
    min_mass_drop: int = 3
    trust_complete_range: bool = False
    allow_empty_initial: bool = False
    max_response_bytes: int = 4_000_000
    max_events: int = 10000
    alarm_minutes: int = 0

    @classmethod
    def from_env(cls) -> "Settings":
        try:
            fraction = float(os.getenv("MAX_DROP_FRACTION", "0.5"))
        except ValueError:
            raise SafeError("invalid_setting_MAX_DROP_FRACTION") from None
        data_env = os.getenv("APP_DATA_DIR", "").strip()
        settings = cls(
            data_dir=Path(data_env).expanduser() if data_env else default_data_dir(),
            mode=os.getenv("MODE", "demo").strip(),
            client_id=os.getenv("ITMO_CLIENT_ID", "").strip(),
            itmo_auth_method=os.getenv("ITMO_AUTH_METHOD", "browser").strip(),
            browser_channel=os.getenv("BROWSER_CHANNEL", "msedge").strip(),
            browser_timeout_seconds=integer("BROWSER_TIMEOUT_SECONDS", 35, 15, 120),
            browser_range_days=integer("BROWSER_RANGE_DAYS", 35, 7, 35),
            scopes=os.getenv("ITMO_SCOPES", "openid").strip(),
            redirect_uri=os.getenv("ITMO_REDIRECT_URI", "http://127.0.0.1:8765/callback").strip(),
            apple_id=os.getenv("ICLOUD_APPLE_ID", "").strip(),
            calendar_name=os.getenv("ICLOUD_CALENDAR_NAME", "ИТМО").strip(),
            source_timezone=os.getenv("SOURCE_TIMEZONE", "Europe/Moscow").strip(),
            past_days=integer("PAST_DAYS", 14, 1, 365),
            future_days=integer("FUTURE_DAYS", 90, 1, 365),
            chunk_days=integer("CHUNK_DAYS", 7, 1, 31),
            sync_seconds=integer("SYNC_SECONDS", 10800, 300, 86400),
            retry_seconds=integer("RETRY_SECONDS", 300, 30, 3600),
            missing_confirmations=integer("MISSING_CONFIRMATIONS", 2, 2, 10),
            missing_min_seconds=integer("MISSING_MIN_SECONDS", 1800, 60, 86400),
            max_drop_fraction=fraction,
            min_mass_drop=integer("MIN_MASS_DROP", 3, 1, 1000),
            trust_complete_range=os.getenv("TRUST_COMPLETE_RANGE", "false").lower() == "true",
            allow_empty_initial=os.getenv("ALLOW_EMPTY_INITIAL", "false").lower() == "true",
            alarm_minutes=integer("ALARM_MINUTES", 0, 0, 1440),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.mode not in {"demo", "myitmo"}:
            raise SafeError("invalid_source_mode")
        if self.itmo_auth_method not in {"browser", "oauth"}:
            raise SafeError("invalid_itmo_auth_method")
        if self.browser_channel not in {"msedge", "chrome", "chromium"}:
            raise SafeError("invalid_browser_channel")
        if self.browser_range_days % 7:
            raise SafeError("browser_range_days_must_be_full_weeks")
        if not self.calendar_name or len(self.calendar_name) > 100:
            raise SafeError("invalid_calendar_name")
        if not 0 < self.max_drop_fraction <= 1:
            raise SafeError("invalid_drop_fraction")
        if self.client_id and (len(self.client_id) > 256 or any(ord(c) < 33 for c in self.client_id)):
            raise SafeError("invalid_client_id")
        if not self.scopes or any(ord(c) < 32 for c in self.scopes):
            raise SafeError("invalid_scope")
        parsed = urlsplit(self.redirect_uri)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port is None or parsed.path != "/callback" or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise SafeError("oauth_redirect_must_be_loopback_callback")
        if not 1024 <= parsed.port <= 65535:
            raise SafeError("oauth_redirect_port_invalid")
        if self.apple_id and (len(self.apple_id) > 320 or any(c in self.apple_id for c in "\r\n\0")):
            raise SafeError("invalid_apple_id")
        try:
            ZoneInfo(self.source_timezone)
        except (ValueError, KeyError):
            raise SafeError("invalid_source_timezone") from None
