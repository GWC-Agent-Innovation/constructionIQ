"""Mirror every console line the backend prints into a persistent log file.

The server logs generation/validation activity with plain print(flush=True)
calls throughout agent/ and backend/. Those are fine for a live terminal, but
uvicorn's --reload restarts a fresh worker process on every file change (and a
closed terminal loses everything), so nothing survives past the console
scrollback. This wraps stdout/stderr so each already-printed line is also
appended, timestamped, to data/logs/backend.log - no call sites change.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "logs"
LOG_FILE = LOG_DIR / "backend.log"

_configured = False


class _TimestampedTee:
    """Passes writes through to the console untouched; buffers by line to
    write a timestamped copy of each completed line to the log file."""

    def __init__(self, console, log_file):
        self._console = console
        self._log_file = log_file
        self._buffer = ""

    def write(self, data: str) -> int:
        self._console.write(data)
        self._buffer += data
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._log_file.write(f"[{timestamp}] {line}\n" if line else "\n")
        return len(data)

    def flush(self) -> None:
        self._console.flush()
        self._log_file.flush()

    def isatty(self) -> bool:
        return False


def configure_server_logging() -> Path:
    """Idempotent: safe to call from every module that might import first."""
    global _configured
    if _configured:
        return LOG_FILE
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_stream = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
    log_stream.write(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] === server process starting ===\n")
    sys.stdout = _TimestampedTee(sys.stdout, log_stream)
    sys.stderr = _TimestampedTee(sys.stderr, log_stream)
    _configured = True
    return LOG_FILE
