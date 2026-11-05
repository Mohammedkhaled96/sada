"""Where models, transcripts and settings live.

One definition, because there were five. Three of them resolved to a path beside
the source tree and ignored the environment, which in a container meant looking
in `/app/models` instead of the mounted `/models`. The visible symptom was a
container downloading a 1.5 GB checkpoint that was already mounted and sitting
right there - and it looked like a slow start rather than a bug.

Both paths are environment-overridable so a container can point them at its
volumes, and both default to a folder beside the project for a normal install.

A checkpoint is looked for in more than one place on purpose. The folder this
program writes to has moved between releases, people move their own files, and
the cost of guessing wrong is not a warning - whisper.load_model() quietly
downloads 1.5 GB again when the file is not where it expected. So reading
searches every folder a checkpoint could plausibly be in, and writing always
goes to one: see candidate_models_dirs() and find_checkpoint().
"""
from __future__ import annotations

import json
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


def _roaming_data_dir() -> Path:
    """The roaming profile's Sada folder. Never written to, only searched.

    It is the folder people assume holds application data, so a checkpoint
    moved by hand lands here often enough to be worth looking in.
    """
    base = os.environ.get("APPDATA")
    return (Path(base) if base else Path.home() / ".config") / "Sada"


def _program_dir() -> Path:
    """The folder the running program sits in.

    For a frozen build that is the installation directory, which an upgrade
    replaces wholesale - so a checkpoint found under it is searched for but
    also moved out: see adopt_stray_checkpoints().
    """
    if _frozen():
        return Path(sys.executable).resolve().parent
    return PROJECT_ROOT


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


def _state_dir() -> Path:
    value = os.environ.get("SADA_STATE_DIR")
    return Path(value) if value else _base()


def _settings_path() -> Path:
    """The settings file, resolved without reading the settings.

    _configured_models_dir() needs it before STATE_DIR exists, and an
    installed build that has not written its settings yet may still have the
    file in the location releases up to 1.0.1 used.
    """
    current = _state_dir() / "settings.json"
    if current.is_file():
        return current
    legacy = _user_data_dir() / "settings.json"
    return legacy if legacy.is_file() else current


def _configured_models_dir() -> Path | None:
    """A models folder the user chose in Settings, read straight from the file.

    engine/ cannot import app.config, because app.config imports this module,
    so the one field needed is read as plain JSON. Anything unreadable, absent
    or blank means nothing was chosen, which is the normal case.
    """
    try:
        raw = json.loads(_settings_path().read_text(encoding="utf-8"))
    except Exception:
        return None
    value = raw.get("models_dir") or ""
    return Path(value) if isinstance(value, str) and value.strip() else None


def default_models_dir() -> Path:
    """Where checkpoints go when nothing has been configured.

    Public and separate from MODELS_DIR so Settings can tell a chosen folder
    from the default and store nothing when they are the same. A stored path
    that merely happens to match today's default would quietly stop following
    it the next time the default moves - which is the whole problem this
    module exists to end.
    """
    return _base() / "models"


def _models_dir() -> Path:
    """The one folder this program writes checkpoints to.

    In order: an explicit environment variable, so a container can point it at
    a volume; the folder chosen in Settings, so a user who keeps 3 GB on
    another drive is not overruled; otherwise the default beside the rest of
    this installation's data.
    """
    value = os.environ.get("SADA_MODELS_DIR")
    if value:
        return Path(value)
    return _configured_models_dir() or default_models_dir()


# Resolved at import time. A process that needs to change these after startup
# should pass an explicit path instead of mutating module state - engine.daemon
# takes --out for exactly that reason.
MODELS_DIR = _models_dir()
OUT_DIR = _dir("SADA_OUT_DIR", "out")

# Anything small the program writes about itself: the settings file, and
# the daemon's endpoint file. Beside the project when run from source, so
# settings sit next to models rather than in a hidden folder under the
# user profile - which is what a developer looking for them expects.
#
# A release still keeps them under the user profile, for the same reason
# models do: an upgrade replaces the program folder, and resetting
# everyone's settings on every update would be worse than hiding them.
STATE_DIR = _state_dir()
SETTINGS_FILE = STATE_DIR / "settings.json"

# Where releases up to 1.0.1 kept everything. Still the live location for an
# installed build, which is exactly why the cleanup below refuses to touch it
# when it is the folder in use.
LEGACY_STATE_DIR = _user_data_dir()


# --------------------------------------------------------------------------
# finding a checkpoint that is not where this version writes them
# --------------------------------------------------------------------------

def candidate_models_dirs() -> list[Path]:
    """Every folder a checkpoint could be sitting in, best first.

    MODELS_DIR leads because it is where this version writes, so the normal
    case finds the file on the first look and nothing else is touched. The
    rest exist because a checkpoint is a 1.5 GB download that must never be
    paid twice: the folder an older release wrote to, the installation folder
    for anyone who put it beside the program, the roaming folder people
    assume is right, and the source checkout.

    Duplicates are dropped, so on an ordinary installed build this returns
    two or three folders rather than five.
    """
    ordered = [
        MODELS_DIR,
        _user_data_dir() / "models",
        _program_dir() / "models",
        _roaming_data_dir() / "models",
        PROJECT_ROOT / "models",
    ]
    seen: set[str] = set()
    out: list[Path] = []
    for folder in ordered:
        try:
            key = str(folder.resolve()).lower()
        except OSError:
            key = str(folder).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(folder)
    return out


def find_checkpoint(filename: str) -> Path | None:
    """Where `filename` actually is, or None if it is on none of the paths.

    Callers use this instead of building MODELS_DIR / filename themselves.
    The difference matters most in engine.backends: a path that does not
    exist is handed to whisper.load_model(), which treats it as a model name
    and downloads the checkpoint again without asking.
    """
    for folder in candidate_models_dirs():
        try:
            path = folder / filename
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def find_model_folder(name: str) -> Path | None:
    """find_checkpoint, for a model that is a folder rather than a file.

    faster-whisper loads a CTranslate2 conversion, which is a directory. It
    falls back to the same behaviour as whisper does with a name it cannot
    resolve, so the same search applies.
    """
    for folder in candidate_models_dirs():
        try:
            path = folder / name
            if path.is_dir():
                return path
        except OSError:
            continue
    return None


def found_checkpoints() -> list[Path]:
    """Every checkpoint on every candidate path, for reporting to the user.

    A folder with no model in it is the single hardest thing to diagnose from
    the outside, so Settings and the log both show this rather than leaving
    the user to guess where the program looked.
    """
    out: list[Path] = []
    for folder in candidate_models_dirs():
        try:
            out.extend(sorted(folder.glob("*.pt")))
        except OSError:
            continue
    return out


def adopt_stray_checkpoints() -> list[tuple[Path, Path]]:
    """Move checkpoints out of the one folder an upgrade will delete.

    Returns the (from, to) pairs that were moved.

    Only the installation folder qualifies. A checkpoint anywhere else is
    left exactly where it is and simply used from there - find_checkpoint()
    already looks - because moving gigabytes the user did not ask to have
    moved is worse than reading them in place. The installation folder is
    different: the next upgrade replaces it, and the file would be gone.

    Within one drive this is a rename and costs nothing. Across drives it is
    a copy, and shutil.move removes its own partial file if that fails, so a
    full disk loses the move rather than the checkpoint.
    """
    import shutil

    if not _frozen():
        return []               # a source checkout is not replaced by an upgrade

    source = _program_dir() / "models"
    try:
        if not source.is_dir() or source.resolve() == MODELS_DIR.resolve():
            return []
        checkpoints = sorted(source.glob("*.pt"))
    except OSError:
        return []

    moved: list[tuple[Path, Path]] = []
    for checkpoint in checkpoints:
        destination = MODELS_DIR / checkpoint.name
        try:
            if destination.exists():
                continue        # already have it; the stray copy can stay put
            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            shutil.move(str(checkpoint), str(destination))
            moved.append((checkpoint, destination))
        except OSError:
            continue            # unreadable or no room; it is still findable
    return moved


# --------------------------------------------------------------------------
# one location for the program's own data
# --------------------------------------------------------------------------

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


def cleanup_duplicate_state() -> dict:
    """Keep one location for this program's settings, and remove the others.

    Returns {"removed": [...], "kept": [...], "failed": [(path, reason)]},
    so the caller can say what happened instead of doing it silently.

    What is removed is the stale settings.json, and the folder itself only if
    nothing else is left in it. An earlier version of this deleted the whole
    folder, having first moved any checkpoint out of it, and that was too
    sharp an instrument for a folder that can hold three gigabytes somebody
    waited an hour for: one unreadable file, one path this code had not
    thought of, and the download is gone. A stale settings file is a few
    hundred bytes and is the only thing that makes the folder a duplicate, so
    that is what goes.

    Checkpoints left behind are not a problem to solve here. find_checkpoint()
    searches that folder, so they keep working from where they are.
    """
    report: dict = {"removed": [], "kept": [], "failed": []}

    for folder in duplicate_state_dirs():
        try:
            (folder / "settings.json").unlink()
            report["removed"].append(folder / "settings.json")
            if sorted(folder.iterdir()):
                report["kept"].append(folder)
            else:
                folder.rmdir()
        except OSError as exc:
            report["failed"].append((folder, str(exc)))
    return report
