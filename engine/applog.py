"""A log file, because a blind user cannot read a console that is not there.

Sada had no log at all. When dictation came back with nothing, or a shortcut
did nothing, or Word refused the text, the only record was a traceback printed
to a window nobody was looking at - and in an installed build there is no
console to print to. Every problem had to be reproduced live to be seen.

So: one file, appended to, flushed on every line, rotated at a megabyte with a
single `.1` backup kept. The handle is opened once and kept, because opening a
file per line is what makes logging slow enough to be worth switching off.

Nothing here raises. A logger that can break the program it is reporting on is
worse than no logger, so every call is wrapped and a failure to write is
swallowed - the caller has real work to do.

Rules for what goes in: enough to explain a failure without the user having to
reproduce it, and nothing that would be a betrayal to write down. The text the
user dictated is their document, not ours, so only its length is recorded.
"""
from __future__ import annotations

import threading
import time
import traceback
from pathlib import Path

from engine.paths import STATE_DIR

LOG_FILE = STATE_DIR / "sada.log"
# A megabyte is about fifteen thousand lines: weeks of ordinary use, and still
# small enough to attach to a message.
MAX_BYTES = 1_000_000

_lock = threading.Lock()
_handle = None
_opened = False


def _open():
    """Open the log once, rotating a full one first. Returns a handle or None."""
    global _handle, _opened
    if _opened:
        return _handle
    _opened = True
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > MAX_BYTES:
            backup = LOG_FILE.with_suffix(".log.1")
            try:
                backup.unlink(missing_ok=True)
                LOG_FILE.rename(backup)
            except OSError:
                pass                # keep appending rather than lose the log
        _handle = open(LOG_FILE, "a", encoding="utf-8")
    except Exception:
        _handle = None              # read-only folder, locked file, no disk
    return _handle


def write(message: str, *, where: str = "") -> None:
    """One line, timestamped. Never raises."""
    try:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{stamp}] {f'{where}: ' if where else ''}{message}"
        with _lock:
            handle = _open()
            if handle is not None:
                handle.write(line + "\n")
                handle.flush()
    except Exception:
        pass


def failed(message: str, exc: BaseException, *, where: str = "") -> None:
    """A caught exception, with its traceback, so it need not be reproduced."""
    try:
        detail = "".join(traceback.format_exception(
            type(exc), exc, exc.__traceback__)).rstrip()
        write(f"{message}: {type(exc).__name__}: {exc}\n{detail}", where=where)
    except Exception:
        pass


def path() -> Path:
    """Where the log is, for the interface to tell the user."""
    return LOG_FILE


def read_tail(lines: int = 200) -> str:
    """The end of the log, for the dictation check to show without a file
    manager. Returns an empty string if there is nothing to read."""
    try:
        if not LOG_FILE.exists():
            return ""
        text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
        return "\n".join(text.splitlines()[-lines:])
    except Exception:
        return ""
