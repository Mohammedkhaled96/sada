"""Where models and transcripts live.

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


def _dir(env_var: str, name: str) -> Path:
    value = os.environ.get(env_var)
    if value:
        return Path(value)
    # A release keeps data under the user profile; running from a source
    # checkout keeps it beside the project, where a developer expects it.
    return (_user_data_dir() if _frozen() else PROJECT_ROOT) / name


# Resolved at import time. A process that needs to change these after startup
# should pass an explicit path instead of mutating module state - engine.daemon
# takes --out for exactly that reason.
MODELS_DIR = _dir("SADA_MODELS_DIR", "models")
OUT_DIR = _dir("SADA_OUT_DIR", "out")
