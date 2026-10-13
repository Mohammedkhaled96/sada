"""Sada engine - command line interface.

Takes any audio or video file and produces an accurate transcript, with the
language detected automatically. Writes a checkpoint after every segment so a
multi-hour file survives a crash, and can resume where it stopped.

Usage:
    python -m engine.transcribe "audio.m4a"
    python -m engine.transcribe "audio.m4a" --model large-v3 --timestamps
    python -m engine.transcribe "audio.m4a" --resume
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

if __package__ in (None, ""):                      # allow `python engine/transcribe.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.backends import MODEL_NAMES, load_backend
from engine.device import pick_device
from engine.job import Cancelled, run_transcription

from engine.paths import OUT_DIR


def log(msg: str = "") -> None:
    print(msg, flush=True)


def hms(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


class ConsoleProgress:
    """Prints a line every 30 audio-seconds, and announces each state change."""

    def __init__(self) -> None:
        self.last_pos = 0.0
        self.last_state = ""

    def __call__(self, p) -> None:
        if p.state != self.last_state:
            self.last_state = p.state
            if p.state == "detecting" and p.language:
                log(f"  language: {p.language} ({p.language_confidence:.0%})")
            elif p.state == "transcribing":
                log(f"\n[3/4] transcribing {hms(p.duration)} of audio")
            elif p.state == "writing":
                log("\n[4/4] writing output")
            elif p.state in ("cancelled", "error"):
                log(f"\n  {p.state}: {p.message}")

        if p.state == "transcribing" and p.position - self.last_pos >= 30:
            self.last_pos = p.position
            log(f"  {hms(p.position)} / {hms(p.duration)}  "
                f"({p.percent:4.0f}%)  {p.realtime_factor:4.1f}x realtime  "
                f"ETA {hms(p.eta_seconds)}")


def transcribe(args) -> int:
    src = Path(args.audio).expanduser()
    if not src.exists():
        log(f"ERROR: file not found: {src}")
        return 2

    log("=" * 68)
    log(f"FILE   {src.name}  ({src.stat().st_size / 1e6:.1f} MB)")

    dev = pick_device(args.device)
    log(f"DEVICE {dev.label}")
    log("=" * 68)

    log("\n[1/4] loading model")
    t0 = time.time()
    try:
        backend = load_backend(args.backend, args.model, dev)
    except Exception as exc:
        if dev.device == "cuda":
            log(f"  GPU init failed ({exc}); falling back to CPU")
            dev = pick_device("cpu")
            backend = load_backend(args.backend, args.model, dev)
        else:
            raise
    log(f"  {backend.name}: {backend.label}  (ready in {time.time() - t0:.1f}s)")

    log("\n[2/4] decoding audio and detecting the language")
    try:
        meta = run_transcription(
            src, backend, args.out or OUT_DIR,
            language=args.language,
            timestamps=args.timestamps,
            hotwords=args.hotwords,
            resume=args.resume,
            on_progress=ConsoleProgress(),
        )
    except Cancelled:
        log("  progress is saved, rerun with --resume")
        return 130
    except KeyboardInterrupt:
        log("\n  interrupted - progress is saved, rerun with --resume")
        return 130

    log("")
    log("=" * 68)
    log(f"  language    {meta['language']}  "
        f"({meta['language_confidence']:.0%} confidence)"
        f"{'  [rtl]' if meta['rtl'] else ''}")
    log(f"  words       {meta['words']:,}   paragraphs {meta['paragraphs']}")
    log(f"  speed       {meta['realtime_factor']}x realtime on {dev.label}")
    log(f"  backend     {meta['backend']}  ({meta['model']})")
    for f in meta["files"]:
        log(f"  -> {f}")
    log("=" * 68)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="transcribe", description=__doc__)
    p.add_argument("audio", help="audio or video file")
    p.add_argument("--model", default="turbo",
                   help=f"{' | '.join(MODEL_NAMES)} (default: turbo)")
    p.add_argument("--backend", default="auto",
                   choices=["auto", "faster-whisper", "openai-whisper"],
                   help="auto prefers faster-whisper, falls back to openai-whisper")
    p.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    p.add_argument("--language", default=None,
                   help="force a language code instead of detecting it")
    p.add_argument("--timestamps", action="store_true",
                   help="prefix every paragraph with its time")
    p.add_argument("--hotwords", default=None,
                   help="names/terms to bias the model towards")
    p.add_argument("--resume", action="store_true",
                   help="continue from the last checkpoint")
    p.add_argument("--out", default=None, help="output directory")
    args = p.parse_args()
    try:
        return transcribe(args)
    except Exception:
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
