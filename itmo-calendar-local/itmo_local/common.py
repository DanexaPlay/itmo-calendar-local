"""Portable state operations. Never put credentials in JSON or logs."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterator


class SafeError(Exception):
    """Machine-readable non-sensitive error codes; do not leak remote exceptions."""
    def __init__(self, code: str, *, retry_after: int = 0):
        self.code = code
        self.retry_after = max(0, min(int(retry_after), 86400))
        super().__init__(code)


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def ensure_private_dir(path: Path) -> None:
    if path.is_symlink():
        raise SafeError("unsafe_state_directory_symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise SafeError("unsafe_state_directory_symlink")
    if os.name != "nt":
        path.chmod(0o700)
    # On Windows default location is beneath the user's LOCALAPPDATA and
    # inherits its account ACL; use DPAPI for actual credentials.


def atomic_write(path: Path, data: bytes) -> None:
    ensure_private_dir(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            if os.name != "nt":
                os.fchmod(handle.fileno(), 0o600)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        with contextlib.suppress(FileNotFoundError):
            Path(temporary).unlink()


def write_json(path: Path, value: Any) -> None:
    atomic_write(path, canonical(value).encode("utf-8"))


def read_json(path: Path) -> Any:
    if path.is_symlink():
        raise SafeError("unsafe_state_file_symlink")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (ValueError, UnicodeError):
        raise SafeError("invalid_state_file") from None


@contextlib.contextmanager
def file_lock(path: Path, *, blocking: bool = True) -> Iterator[None]:
    """Native advisory cross-process lock, compatible with Windows and Linux.

    Persistent files are fine: operating system releases lock after a crash.
    No secrets are held in lock files.
    """
    ensure_private_dir(path.parent)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.name == "nt":
            import msvcrt
            # msvcrt.locking() requires a byte in the file and starts at the
            # current position. Exclusive locking is per Windows user process.
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            except OSError:
                raise SafeError("operation_already_running") from None
            try:
                yield
            finally:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError:
                raise SafeError("operation_already_running") from None
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
