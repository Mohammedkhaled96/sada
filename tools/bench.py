"""Measure the speed/accuracy trade-off of the decoding settings.

beam_size=5 with best_of=5 is roughly five times the compute of greedy decoding.
On a CPU-only machine that is the difference between "usable for hours of audio"
and "leave it running overnight", so it needs measuring rather than assuming.

Run:  PYTHONUTF8=1 .venv/Scripts/python.exe tools/bench.py [audio] [--model turbo]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.audio import SAMPLE_RATE, decode_audio
from engine.backends import load_backend
from engine.device import pick_device
from engine.job import DECODE_OPTS
from engine.textout import build_paragraphs

CONFIGS = {
    "beam5 (current default)": dict(beam_size=5, best_of=5),
    "beam2": dict(beam_size=2, best_of=2),
    "greedy + fallback": dict(beam_size=1, best_of=1),
    "greedy, no fallback": dict(beam_size=1, best_of=1, temperature=[0.0]),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio", nargs="?", default="E:/test.mp3")
    ap.add_argument("--model", default="turbo")
    ap.add_argument("--language", default=None)
    args = ap.parse_args()

    audio = decode_audio(args.audio, sampling_rate=SAMPLE_RATE)
    duration = len(audio) / SAMPLE_RATE
    print(f"file     {Path(args.audio).name}  ({duration:.1f}s of audio)")

    dev = pick_device("auto")
    print(f"device   {dev.label}")
    backend = load_backend("auto", args.model, dev)
    print(f"backend  {backend.name}  {backend.label}\n")

    language = args.language
    if not language:
        t0 = time.time()
        from engine.langdetect import detect_language

        language, conf, _ = detect_language(backend, audio, log=lambda *a: None)
        print(f"language {language} ({conf:.0%}) detected in {time.time() - t0:.1f}s\n")

    rows = []
    for name, override in CONFIGS.items():
        opts = {**DECODE_OPTS, **override}
        t0 = time.time()
        segs = list(backend.stream_segments(audio, language, opts))
        el = time.time() - t0
        text = " ".join(p["text"] for p in build_paragraphs(segs))
        rows.append((name, el, duration / el if el else 0, len(text.split()), text))
        print(f"{name:26s} {el:6.1f}s  {duration / el:5.2f}x realtime  "
              f"{len(text.split()):4d} words")

    base = rows[0]
    print(f"\n{'-' * 70}\nspeed relative to {base[0]}:")
    for name, el, rtf, words, _ in rows:
        print(f"  {name:26s} {base[1] / el:4.2f}x faster")

    print(f"\n{'-' * 70}\ntranscripts (compare them yourself):")
    for name, _, _, _, text in rows:
        print(f"\n### {name}\n{text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
