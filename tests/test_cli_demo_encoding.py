"""Regression for GitHub Windows runner output with a legacy code page."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_offline_demo_runs_with_cp1252_stdout():
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    # PYTHONIOENCODING takes precedence over PYTHONUTF8 (also used in Actions).
    env["PYTHONIOENCODING"] = "cp1252:strict"
    process = subprocess.run(
        [sys.executable, "-m", "itmo_local", "demo"],
        cwd=root, env=env, capture_output=True, timeout=30,
    )
    assert process.returncode == 0, process.stderr.decode("ascii", errors="backslashreplace")
    assert b"DEMO OK" in process.stdout
    assert process.stdout.isascii()
