"""Download a model checkpoint from the command line.

The desktop application has a Settings page for this; a container has no UI, so
it needs a way to populate its models volume before the daemon starts.

    python -m engine.fetch turbo
    python -m engine.fetch --list
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import models
from engine.backends import MODEL_NAMES


def show_list() -> int:
    print(f"{'model':<18} {'size':>9}  state")
    for m in models.all_info():
        state = ("installed" if m.installed
                 else "incomplete download" if m.partial else "not installed")
        print(f"{m.checkpoint:<18} {m.size_text:>9}  {state}")
    print(f"\nmodels directory: {models.MODELS_DIR}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="fetch", description=__doc__)
    p.add_argument("model", nargs="?", default=None,
                   help=" | ".join(k for k in MODEL_NAMES if k != "tiny"))
    p.add_argument("--list", action="store_true", help="show what is installed")
    args = p.parse_args()

    if args.list or not args.model:
        return show_list()

    if args.model not in MODEL_NAMES:
        print(f"unknown model {args.model!r}; one of "
              f"{', '.join(k for k in MODEL_NAMES if k != 'tiny')}")
        return 2

    info = models.info(args.model)
    if info.installed:
        print(f"{info.checkpoint} is already installed ({info.size_text})")
        return 0

    print(f"downloading {info.checkpoint} ({info.size_text}) to {models.MODELS_DIR}")
    last = [0.0]

    def progress(done: int, total: int) -> None:
        now = time.time()
        if now - last[0] < 1.0 and done < total:
            return
        last[0] = now
        pct = (done / total * 100) if total else 0
        print(f"  {models.human(done)} of {models.human(total)}  ({pct:.0f}%)",
              flush=True)

    try:
        path = models.download(args.model, on_progress=progress)
    except KeyboardInterrupt:
        print("\ncancelled; no partial file was left behind")
        return 130
    except Exception as exc:
        print(f"failed: {exc}")
        return 1

    print(f"done: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
