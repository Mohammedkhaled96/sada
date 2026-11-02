"""Starting with Windows, as a shortcut the user can see and delete.

A dictation shortcut is only useful if the program is already running when the
user wants to speak, and asking somebody to launch it first every morning
defeats the point of a global hotkey.

This is done with a shortcut in the Startup folder rather than a registry Run
entry, for one reason: the user can find it. `Win+R`, `shell:startup`, and
there it is, deletable like any other file, with no idea that a registry exists
needed. A program that can only be stopped from starting by the program itself
is a program that has taken something from its user.

Nothing here raises. Autostart is a convenience, and the failure to arrange it
must not stop the program it was arranging.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from engine import applog
from engine.paths import PROJECT_ROOT

SHORTCUT_NAME = "Sada.lnk"


def _startup_dir() -> Path | None:
    """The per-user Startup folder, or None if Windows does not name one."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return (Path(appdata) / "Microsoft" / "Windows" / "Start Menu"
            / "Programs" / "Startup")


def shortcut_path() -> Path | None:
    folder = _startup_dir()
    return (folder / SHORTCUT_NAME) if folder else None


def _target() -> tuple[str, str, str]:
    """(program, arguments, working directory) for however Sada is installed.

    A release is its own executable. A source checkout goes through Sada.vbs,
    which starts pythonw with no console window - the same thing the user
    double-clicks, so autostart and a manual start behave identically.
    """
    if getattr(sys, "frozen", False):
        return sys.executable, "", str(Path(sys.executable).parent)
    launcher = PROJECT_ROOT / "Sada.vbs"
    return "wscript.exe", f'"{launcher}"', str(PROJECT_ROOT)


def available() -> bool:
    """Whether a shortcut can be written at all on this installation."""
    if shortcut_path() is None:
        return False
    try:
        import win32com.client            # noqa: F401
    except Exception:
        return False
    return True


def enabled() -> bool:
    path = shortcut_path()
    try:
        return bool(path and path.is_file())
    except OSError:
        return False


def enable() -> bool:
    """Create the shortcut. Returns whether it is there afterwards."""
    path = shortcut_path()
    if path is None:
        return False
    try:
        import pythoncom
        import win32com.client

        # This can be called from a worker thread; COM needs initialising on
        # whichever thread is asking, and may already be initialised on it.
        try:
            pythoncom.CoInitialize()
        except Exception:
            pass
        path.parent.mkdir(parents=True, exist_ok=True)
        program, arguments, workdir = _target()
        shell = win32com.client.Dispatch("WScript.Shell")
        link = shell.CreateShortcut(str(path))
        link.TargetPath = program
        link.Arguments = arguments
        link.WorkingDirectory = workdir
        link.Description = "Sada - transcription and dictation into Word"
        link.Save()
        applog.write(f"autostart on: {path}", where="autostart")
    except Exception as exc:
        applog.failed("creating the autostart shortcut", exc, where="autostart")
        return False
    return enabled()


def disable() -> bool:
    """Remove the shortcut. Returns whether it is gone afterwards."""
    path = shortcut_path()
    if path is None:
        return True
    try:
        path.unlink(missing_ok=True)
        applog.write("autostart off", where="autostart")
    except OSError as exc:
        applog.failed("removing the autostart shortcut", exc, where="autostart")
        return False
    return not enabled()


def apply(wanted: bool) -> bool:
    """Make the shortcut match the setting. Returns the state it ended in."""
    if not available():
        return False
    if wanted == enabled():
        return wanted
    return enable() if wanted else not disable()
