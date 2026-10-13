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
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _dir(env_var: str, default_name: str) -> Path:
    value = os.environ.get(env_var)
    return Path(value) if value else PROJECT_ROOT / default_name


# Resolved at import time. A process that needs to change these after startup
# should pass an explicit path instead of mutating module state - engine.daemon
# takes --out for exactly that reason.
MODELS_DIR = _dir("SADA_MODELS_DIR", "models")
OUT_DIR = _dir("SADA_OUT_DIR", "out")
