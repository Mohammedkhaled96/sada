"""Where the program keeps its data, and how it finds a model that moved.

Two things are being guarded, and both of them cost the user gigabytes when
they go wrong.

The cleanup deletes. It runs at every startup, on every machine, with no
prompt, so the tests that matter most here are the ones about what it refuses
to touch.

The search reads. A checkpoint that is on the disk but not in the folder this
version writes to used to be handed to whisper.load_model() as a path that
does not exist, which makes it a model name - and the 1.5 GB arrives again
with no progress and nothing asked. So the tests assert that a file an older
release left behind is found, and found where it lies.

Every case builds its own temporary profile and project, so nothing here can
reach the real folders.

Run:  PYTHONUTF8=1 .venv/Scripts/python.exe tests/test_paths.py
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))
    if not ok:
        failures.append(label)


def fresh_paths(profile: Path, state: Path, models: Path | None = None):
    """engine.paths, re-resolved against a temporary profile and project."""
    os.environ["LOCALAPPDATA"] = str(profile)
    os.environ["SADA_STATE_DIR"] = str(state)
    if models is not None:
        os.environ["SADA_MODELS_DIR"] = str(models)
    else:
        os.environ.pop("SADA_MODELS_DIR", None)
    sys.modules.pop("engine.paths", None)
    import engine.paths as paths

    return importlib.reload(paths)


# ------------------------------------------------------------ where things go
print("--- where things go ---")

# Asked of the module as it actually resolves, with nothing overridden: the
# point of the change was that settings land beside models rather than in a
# separate folder under the user profile, and overriding one of the two would
# hide exactly that.
for name in ("SADA_STATE_DIR", "SADA_MODELS_DIR", "SADA_OUT_DIR"):
    os.environ.pop(name, None)
sys.modules.pop("engine.paths", None)
import engine.paths as real_paths

importlib.reload(real_paths)

check("settings sit beside models, not in a separate place",
      real_paths.SETTINGS_FILE.parent == real_paths.MODELS_DIR.parent,
      f"{real_paths.SETTINGS_FILE.parent} vs {real_paths.MODELS_DIR.parent}")
check("the settings file is named settings.json",
      real_paths.SETTINGS_FILE.name == "settings.json")
check("running from a checkout, that is the project itself",
      real_paths.SETTINGS_FILE.parent == real_paths.PROJECT_ROOT,
      str(real_paths.SETTINGS_FILE))
check("and every one of them can still be pointed elsewhere for a container",
      all(f'"{v}"' in (Path(__file__).resolve().parent.parent / "engine"
                       / "paths.py").read_text(encoding="utf-8")
          for v in ("SADA_MODELS_DIR", "SADA_OUT_DIR", "SADA_STATE_DIR")))


# --------------------------------------------- what it refuses to delete
print("\n--- what it refuses to delete ---")

# An installed release keeps its data under the user profile, so the folder
# the cleanup is aimed at IS the live one. Without this check it would delete
# the settings and the multi-gigabyte models of every installed copy, the
# first time it ever started.
with tempfile.TemporaryDirectory() as td:
    live = Path(td) / "profile" / "Sada"
    (live / "models").mkdir(parents=True)
    (live / "settings.json").write_text("{}", encoding="utf-8")
    (live / "models" / "large-v3-turbo.pt").write_bytes(b"x" * 1024)

    p = fresh_paths(Path(td) / "profile", live)
    check("the folder in use is not a duplicate of itself",
          p.duplicate_state_dirs() == [], str(p.duplicate_state_dirs()))
    report = p.cleanup_duplicate_state()
    check("so an installed copy removes nothing", report["removed"] == [],
          str(report["removed"]))
    check("its settings survive", (live / "settings.json").exists())
    check("its model survives",
          (live / "models" / "large-v3-turbo.pt").exists())

# A folder called Sada that somebody else made is not ours to delete. Our
# settings.json inside it is what makes it ours.
with tempfile.TemporaryDirectory() as td:
    other = Path(td) / "profile" / "Sada"
    other.mkdir(parents=True)
    (other / "someone-elses-data.db").write_bytes(b"important")

    p = fresh_paths(Path(td) / "profile", Path(td) / "project")
    report = p.cleanup_duplicate_state()
    check("a folder with no settings.json is left alone",
          report["removed"] == [], str(report["removed"]))
    check("and its contents are untouched",
          (other / "someone-elses-data.db").exists())

with tempfile.TemporaryDirectory() as td:
    p = fresh_paths(Path(td), Path(td) / "project")
    report = p.cleanup_duplicate_state()
    check("nothing there at all is not an error",
          report == {"removed": [], "kept": [], "failed": []}, str(report))


# ------------------------------------------------- what it does delete
print("\n--- what it does delete ---")

with tempfile.TemporaryDirectory() as td:
    stale = Path(td) / "profile" / "Sada"
    (stale / "models").mkdir(parents=True)
    (stale / "settings.json").write_text('{"model": "turbo"}', encoding="utf-8")
    (stale / "models" / "medium.pt").write_bytes(b"m" * 2048)
    (stale / "models" / "small.pt").write_bytes(b"the old copy")
    (stale / "out").mkdir()
    (stale / "endpoint.json").write_text("{}", encoding="utf-8")

    project = Path(td) / "project"
    (project / "models").mkdir(parents=True)
    (project / "models" / "small.pt").write_bytes(b"the good copy")

    p = fresh_paths(Path(td) / "profile", project, project / "models")
    check("a stale folder with our settings.json is a duplicate",
          [x.name for x in p.duplicate_state_dirs()] == ["Sada"])

    report = p.cleanup_duplicate_state()
    check("the stale settings file is what gets removed",
          not (stale / "settings.json").exists())
    check("and it is reported, rather than removed silently",
          [x.name for x in report["removed"]] == ["settings.json"],
          str(report["removed"]))

    # This is the line that matters most in the file. An earlier version
    # deleted the whole folder, having first moved the checkpoints out. One
    # unreadable file or one path it had not thought of, and somebody's
    # three-gigabyte download was gone. Nothing is moved and nothing else is
    # deleted now: find_checkpoint() searches that folder, so a model left
    # there goes on being used from where it is.
    check("the checkpoints in it are left exactly where they were",
          (stale / "models" / "medium.pt").exists()
          and (stale / "models" / "small.pt").read_bytes() == b"the old copy")
    check("so is everything else in it",
          (stale / "out").is_dir() and (stale / "endpoint.json").exists())
    check("the folder stays, and is reported as kept",
          stale.is_dir() and [x.name for x in report["kept"]] == ["Sada"],
          str(report["kept"]))
    check("the copy we already had is untouched",
          (project / "models" / "small.pt").read_bytes() == b"the good copy")

# A folder with nothing but our stale settings in it has nothing left to keep,
# so it goes. Leaving an empty folder behind would be the duplicate surviving
# in name, which is the thing this is for.
with tempfile.TemporaryDirectory() as td:
    empty = Path(td) / "profile" / "Sada"
    empty.mkdir(parents=True)
    (empty / "settings.json").write_text("{}", encoding="utf-8")

    p = fresh_paths(Path(td) / "profile", Path(td) / "project")
    report = p.cleanup_duplicate_state()
    check("a folder holding nothing else is removed as well",
          not empty.exists(), str(report))


# --------------------------------------------- finding a model anywhere
print("\n--- finding a model anywhere ---")

# The failure this prevents is not a warning. engine.backends used to build
# MODELS_DIR / name and hand it to whisper.load_model() without looking: a
# path that does not exist is taken as a model name, and the checkpoint is
# downloaded again - 1.5 GB, no progress, nothing asked. So a file that is on
# the disk in a folder an older release wrote to has to be found.
with tempfile.TemporaryDirectory() as td:
    profile = Path(td) / "profile"
    (profile / "Sada" / "models").mkdir(parents=True)
    (profile / "Sada" / "models" / "large-v3-turbo.pt").write_bytes(b"old release")

    project = Path(td) / "project"
    (project / "models").mkdir(parents=True)

    p = fresh_paths(profile, project, project / "models")
    check("the folder we write to is searched first",
          p.candidate_models_dirs()[0] == project / "models",
          str(p.candidate_models_dirs()[0]))
    found = p.find_checkpoint("large-v3-turbo.pt")
    check("a checkpoint an older release left behind is found",
          found == profile / "Sada" / "models" / "large-v3-turbo.pt", str(found))
    check("and it is read from where it lies, not copied",
          (profile / "Sada" / "models" / "large-v3-turbo.pt").exists()
          and not (project / "models" / "large-v3-turbo.pt").exists())
    check("one that is nowhere is reported as nowhere",
          p.find_checkpoint("medium.pt") is None)
    # Membership, not equality: PROJECT_ROOT is a real candidate folder and
    # this checkout has a real checkpoint in it, which no temporary directory
    # can hide. That it shows up here is the function working.
    listed = p.found_checkpoints()
    check("every checkpoint on every path is listed for the user",
          profile / "Sada" / "models" / "large-v3-turbo.pt" in listed,
          str(listed))
    check("the same folder twice is searched once",
          len(p.candidate_models_dirs()) == len(
              {str(x).lower() for x in p.candidate_models_dirs()}))

# The chosen folder is read out of settings.json by engine/paths.py itself,
# because app.config imports it and the import cannot go the other way. If
# that ever stops working the symptom is silent: the setting is accepted,
# saved, and ignored.
with tempfile.TemporaryDirectory() as td:
    state = Path(td) / "state"
    state.mkdir(parents=True)
    elsewhere = Path(td) / "another drive" / "whisper"
    elsewhere.mkdir(parents=True)
    (state / "settings.json").write_text(
        '{"models_dir": "' + str(elsewhere).replace("\\", "\\\\") + '"}',
        encoding="utf-8")

    p = fresh_paths(Path(td) / "profile", state)
    check("a folder chosen in Settings is where checkpoints are written",
          p.MODELS_DIR == elsewhere, str(p.MODELS_DIR))
    check("and the default is still reported separately, so Settings can "
          "store nothing when they match",
          p.default_models_dir() != elsewhere, str(p.default_models_dir()))

with tempfile.TemporaryDirectory() as td:
    state = Path(td) / "state"
    state.mkdir(parents=True)
    (state / "settings.json").write_text("{ this is not json", encoding="utf-8")

    p = fresh_paths(Path(td) / "profile", state)
    check("an unreadable settings file falls back to the default rather "
          "than failing to start",
          p.MODELS_DIR == p.default_models_dir(), str(p.MODELS_DIR))

# The environment variable has to win, or a container with a mounted volume
# would follow whatever happened to be in a settings file inside the image.
with tempfile.TemporaryDirectory() as td:
    state = Path(td) / "state"
    state.mkdir(parents=True)
    (state / "settings.json").write_text('{"models_dir": "C:\\\\chosen"}',
                                         encoding="utf-8")
    p = fresh_paths(Path(td) / "profile", state, Path(td) / "mounted")
    check("SADA_MODELS_DIR beats the setting", p.MODELS_DIR == Path(td) / "mounted",
          str(p.MODELS_DIR))


# ------------------------------------- the one folder an upgrade deletes
print("\n--- the one folder an upgrade deletes ---")

# A checkpoint beside the executable is the only one that has to be moved: the
# installer replaces that folder wholesale, so the file would be gone after
# the next update. Everything found anywhere else is left alone on purpose.
with tempfile.TemporaryDirectory() as td:
    p = fresh_paths(Path(td) / "profile", Path(td) / "state",
                    Path(td) / "state" / "models")
    check("a source checkout is not replaced by an upgrade, so nothing moves",
          p.adopt_stray_checkpoints() == [], str(p.adopt_stray_checkpoints()))

check("only the installation folder is adopted from",
      "_program_dir() / \"models\"" in Path(
          Path(__file__).resolve().parent.parent / "engine" / "paths.py"
      ).read_text(encoding="utf-8"))
check("and only in a frozen build",
      "if not _frozen():\n        return []" in Path(
          Path(__file__).resolve().parent.parent / "engine" / "paths.py"
      ).read_text(encoding="utf-8"))


# ----------------------------------------------------- it runs at startup
print("\n--- it runs at startup ---")

source = (Path(__file__).resolve().parent.parent / "app" / "main.py").read_text(
    encoding="utf-8")
check("startup calls it", "paths.cleanup_duplicate_state()" in source)

# Order matters: config.load() copies the old settings to the new location,
# and the cleanup then removes the folder they came from. Reversed, the
# settings would be deleted before being read.
load_at = source.index("config.load()")
cleanup_at = source.index("cleanup_duplicate_state()")
check("after the settings have been read and copied across",
      load_at < cleanup_at, f"load at {load_at}, cleanup at {cleanup_at}")

config_source = (Path(__file__).resolve().parent.parent / "app"
                 / "config.py").read_text(encoding="utf-8")
check("and loading from the old place writes them to the new one",
      "if source != SETTINGS_FILE:" in config_source)

check("a failure there cannot stop the program starting",
      "pass            # housekeeping must never stop the app from starting"
      in source)


# ----------------------------------------------------------------------- done
print("\n" + "=" * 60)
if failures:
    print(f"FAILED: {len(failures)}")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("all checks passed")
print("=" * 60)
