from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterator

from .common import SafeError, canonical, ensure_private_dir, file_lock


class Store:
    def __init__(self, directory: Path):
        self.directory = directory
        ensure_private_dir(directory)
        self.path = directory / "calendar.sqlite3"
        with file_lock(directory / "initialize.lock"):
            with self.connect() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.executescript("""
                    CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS events (
                        uid TEXT PRIMARY KEY, match_id TEXT NOT NULL, day TEXT NOT NULL,
                        payload TEXT NOT NULL, fingerprint TEXT NOT NULL,
                        active INTEGER NOT NULL, sequence INTEGER NOT NULL,
                        created REAL NOT NULL, modified REAL NOT NULL,
                        missing_count INTEGER NOT NULL DEFAULT 0,
                        missing_since REAL, last_missing_at REAL
                    );
                    CREATE UNIQUE INDEX IF NOT EXISTS event_identity ON events(match_id, day);
                    CREATE TABLE IF NOT EXISTS reused_ids (match_id TEXT PRIMARY KEY);
                """)
                old_version = self.get_meta(connection, "schema_version", 1)
                if old_version != 1:
                    raise SafeError("unsupported_database_version")
                self.set_meta(connection, "schema_version", 1)
                connection.commit()
            if os.name != "nt":
                self.path.chmod(0o600)

    @contextlib.contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        # Caller controls commit; an exception always rolls back.
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("PRAGMA synchronous=FULL")
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def get_meta(connection: sqlite3.Connection, key: str, default: Any = None) -> Any:
        row = connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def set_meta(connection: sqlite3.Connection, key: str, value: Any) -> None:
        connection.execute("INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           (key, canonical(value)))

    def metadata(self, key: str, default: Any = None) -> Any:
        with self.connect() as connection:
            return self.get_meta(connection, key, default)

    def update_meta(self, **values: Any) -> None:
        with self.connect() as connection:
            for key, value in values.items():
                self.set_meta(connection, key, value)
            connection.commit()

    @staticmethod
    def rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
        result = []
        for record in connection.execute("SELECT * FROM events"):
            row = dict(record)
            row["payload"] = json.loads(row["payload"])
            result.append(row)
        return result

    def status(self) -> dict[str, Any]:
        with self.connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM events WHERE active=1").fetchone()[0]
            keys = ("last_attempt", "last_success", "last_error", "next_sync", "last_counts",
                    "pending_digest", "pending_reason", "auth_error", "auth_next_attempt")
            result = {key: self.get_meta(connection, key) for key in keys}
            result["active_event_count"] = count
            result["icloud_bound"] = bool(self.get_meta(connection, "icloud_binding"))
            result["last_push"] = self.get_meta(connection, "last_push")
            result["last_push_error"] = self.get_meta(connection, "last_push_error")
            return result

    def backup(self, target: Path) -> None:
        """Consistent SQLite backup; never copy a live .sqlite3 without its WAL."""
        if target.exists():
            raise SafeError("backup_target_already_exists")
        ensure_private_dir(target.parent)
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with self.connect() as source:
            destination = sqlite3.connect(target)
            try:
                source.backup(destination)
            finally:
                destination.close()
        if os.name != "nt":
            target.chmod(0o600)
