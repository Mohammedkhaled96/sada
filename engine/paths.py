"""Where models, transcripts and settings live.

One definition, because there were five. Three of them resolved to a path beside
the source tree and ignored the environment, which in a container meant looking
in `/app/models` instead of the mounted `/models`. The visible symptom was a
container downloading a 1.5 GB checkpoint that was already mounted and sitting
right there - and it looked like a slow start rather than a bug.

Both paths are environment-overridable so a container can point them at its
volumes, and both default to a folder beside the project for a normal install.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _frozen() -> bool:
    """True in a PyInstaller build, i.e. an installed or portable release."""
    return getattr(sys, "frozen", False)


def _user_data_dir() -> Path:
    """Per-user data, outside the installation.

    An installed build must not keep the model inside its own program folder.
    Uninstalling removes that folder, and so would an upgrade - which would
    throw away a 1.5 GB download the user had to wait for, every single time
    they updated. It also lets the uninstaller offer to keep it.
    """
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) if base else Path.home() / ".local" / "share") / "Sada"


def _base() -> Path:
    """Where this installation keeps everything it writes.

    A release keeps it under the user profile; running from a source
    checkout keeps it beside the project, where a developer expects it.
    """
    return _user_data_dir() if _frozen() else PROJECT_ROOT


def _dir(env_var: str, name: str) -> Path:
    value = os.environ.get(env_var)
    if value:
        return Path(value)
    return _base() / name


# Resolved at import time. A process that needs to change these after startup
# should pass an explicit path instead of mutating module state - engine.daemon
# takes --out for exactly that reason.
MODELS_DIR = _dir("SADA_MODELS_DIR", "models")
OUT_DIR = _dir("SADA_OUT_DIR", "out")

# Anything small the program writes about itself: the settings file, and
# the daemon's endpoint file. Beside the project when run from source, so
# settings sit next to models rather than in a hidden folder under the
# user profile - which is what a developer looking for them expects.
#
# A release still keeps them under the user profile, for the same reason
# models do: an upgrade replaces the program folder, and resetting
# everyone's settings on every update would be worse than hiding them.
STATE_DIR = Path(os.environ["SADA_STATE_DIR"]) if os.environ.get(
    "SADA_STATE_DIR") else _base()
SETTINGS_FILE = STATE_DIR / "settings.json"

# Where releases up to 1.0.1 kept everything. Still the live location for an
# installed build, which is exactly why the cleanup below refuses to touch it
# when it is the folder in use.
LEGACY_STATE_DIR = _user_data_dir()


def duplicate_state_dirs() -> list[Path]:
    """Folders holding a second copy of this program's data.

    A folder qualifies on two conditions. It must hold a settings.json - that
    file is what makes it ours, rather than somebody else's folder that
    happens to be named Sada. And it must not be the folder currently in use.

    The second condition is the one that matters. In an installed release this
    program's data lives under the user profile, so STATE_DIR and
    LEGACY_STATE_DIR are the same folder; a cleanup without this check would
    delete the settings and the multi-gigabyte models of every installed copy
    the first time it started.
    """
    try:
        active = STATE_DIR.resolve()
    except OSError:
        return []

    found: list[Path] = []
    for candidate in (LEGACY_STATE_DIR,):
        try:
            if not (candidate / "settings.json").is_file():
                continue
            if candidate.resolve() == active:
                continue                # the folder we are using right now
        except OSError:
            continue
        found.append(candidate)
    return found


def cleanup_duplicate_state(models_dir: Path | None = None) -> dict:
    """Keep one location for this program's data, and remove the others.

    Returns {"removed": [...], "adopted": [...], "failed": [(path, reason)]},
    so the caller can say what happened instead of doing it silently.

    Checkpoints found in the old folder are moved into the active models
    folder first, unless a copy is already there. Deleting them would be the
    letter of "remove the duplicate folder" and the opposite of its purpose:
    the user would sit through a download of up to 2.9 GB again, into the new
    location, to arrive exactly where moving them puts them at once.
    """
    import shutil

    target = Path(models_dir) if models_dir is not None else MODELS_DIR
    report: dict = {"removed": [], "adopted": [], "failed": []}

    for folder in duplicate_state_dirs():
        try:
            for checkpoint in sorted((folder / "models").glob("*.pt")):
                destination = target / checkpoint.name
                if destination.exists():
                    continue            # already have it, so the copy can go
                target.mkdir(parents=True, exist_ok=True)
                shutil.move(str(checkpoint), str(destination))
                report["adopted"].append(destination)
            shutil.rmtree(folder)
            report["removed"].append(folder)
        except OSError as exc:
            report["failed"].append((folder, str(exc)))
    return report
