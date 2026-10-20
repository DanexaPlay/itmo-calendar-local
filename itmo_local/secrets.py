"""Per-user Windows DPAPI credential storage, never plaintext on disk."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .common import SafeError, atomic_write, canonical


class WindowsSecrets:
    def __init__(self, directory: Path):
        if os.name != "nt":
            raise SafeError("windows_dpapi_required")
        try:
            import win32crypt
        except ImportError:
            raise SafeError("pywin32_not_installed") from None
        self.crypto = win32crypt
        self.directory = directory

    def _path(self, key: str) -> Path:
        if key not in {"itmo_tokens", "icloud_password", "oauth_client_secret"}:
            raise SafeError("invalid_secret_name")
        return self.directory / (key + ".dpapi")

    def set(self, key: str, value: str) -> None:
        if not value or len(value) > 40000:
            raise SafeError("invalid_secret")
        try:
            encrypted = self.crypto.CryptProtectData(value.encode("utf-8"), "Personal ITMO Calendar", None, None, None, 0)
            atomic_write(self._path(key), encrypted)
        except SafeError:
            raise
        except Exception:
            raise SafeError("dpapi_save_failed") from None

    def get(self, key: str) -> str | None:
        path = self._path(key)
        if path.is_symlink():
            raise SafeError("unsafe_secret_file_symlink")
        try:
            protected = path.read_bytes()
        except FileNotFoundError:
            return None
        try:
            _description, plaintext = self.crypto.CryptUnprotectData(protected, None, None, None, 0)
            return plaintext.decode("utf-8")
        except Exception:
            raise SafeError("dpapi_decryption_failed_wrong_windows_user_or_corrupt_data") from None

    def forget(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)
        # Delete is not secure erasure and does not revoke remote access.

    def get_tokens(self) -> dict[str, Any] | None:
        raw = self.get("itmo_tokens")
        if raw is None:
            return None
        try:
            result = json.loads(raw)
        except ValueError:
            raise SafeError("invalid_token_store") from None
        if not isinstance(result, dict):
            raise SafeError("invalid_token_store")
        return result

    def save_tokens(self, tokens: dict[str, Any]) -> None:
        self.set("itmo_tokens", canonical(tokens))
