"""Where the program keeps its data, and removing a second copy of it.

The cleanup in engine.paths deletes a folder, so the tests that matter most
here are the ones about what it refuses to delete. It runs at every startup,
on every machine, with no prompt - the cost of getting it wrong is somebody
else's gigabytes.

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
          report == {"removed": [], "adopted": [], "failed": []}, str(report))


# ------------------------------------------------- what it does delete
print("\n--- what it does delete ---")

with tempfile.TemporaryDirectory() as td:
    stale = Path(td) / "profile" / "Sada"
    (stale / "models").mkdir(parents=True)
    (stale / "settings.json").write_text('{"model": "turbo"}', encoding="utf-8")
    (stale / "models" / "medium.pt").write_bytes(b"m" * 2048)
    (stale / "models" / "tiny.pt").write_bytes(b"the old copy")
    (stale / "out").mkdir()
    (stale / "endpoint.json").write_text("{}", encoding="utf-8")

    project = Path(td) / "project"
    (project / "models").mkdir(parents=True)
    (project / "models" / "tiny.pt").write_bytes(b"the good copy")

    p = fresh_paths(Path(td) / "profile", project, project / "models")
    check("a stale folder with our settings.json is a duplicate",
          [x.name for x in p.duplicate_state_dirs()] == ["Sada"])

    report = p.cleanup_duplicate_state()
    check("it is removed", not stale.exists())
    check("and reported, rather than removed silently",
          len(report["removed"]) == 1, str(report["removed"]))

    # Deleting a checkpoint would be the letter of "remove the folder" and the
    # opposite of its purpose: the user waits out the same download again.
    check("a checkpoint we do not have is moved across, not destroyed",
          (project / "models" / "medium.pt").exists())
    check("and reported as adopted",
          [x.name for x in report["adopted"]] == ["medium.pt"],
          str(report["adopted"]))
    check("a checkpoint we already have is not overwritten by the old one",
          (project / "models" / "tiny.pt").read_bytes() == b"the good copy")


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
