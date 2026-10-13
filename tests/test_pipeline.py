"""Verify everything in the pipeline that does not need the Whisper weights:
audio decoding, device selection, language-sample placement, checkpoint
resume, paragraph building and UTF-8 output.

Run:  PYTHONUTF8=1 .venv/Scripts/python.exe tests/test_pipeline.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from engine.audio import SAMPLE_RATE, decode_audio
from engine.device import pick_device
from engine.langdetect import detect_language
from engine.textout import build_paragraphs, is_rtl, write_outputs
from engine.job import load_checkpoint

PASS, FAIL = "  ok  ", " FAIL "
failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- device
print("\n--- device selection ---")
d = pick_device("auto")
check("auto picks a device", d.device in ("cuda", "cpu"), d.label)
check("cpu forced -> int8", pick_device("cpu").compute_type == "int8")
check("leaves cores for the OS", d.cpu_threads <= max(4, (__import__("os").cpu_count() or 4)))

# ------------------------------------------------------- audio decoding
print("\n--- audio decoding (real files) ---")
CANDIDATES = [
    (Path("E:/test.mp3"), 34.24),
    (Path("E:/ef core audio overview.m4a"), 510.58),
    (Path("E:/agent md file audio.m4a"), 1076.41),
]
decoded = {}
for path, expected in CANDIDATES:
    if not path.exists():
        print(f"[ skip ] {path.name} not present")
        continue
    audio = decode_audio(str(path), sampling_rate=SAMPLE_RATE)
    dur = len(audio) / SAMPLE_RATE
    decoded[path.name] = audio
    check(f"decode {path.name}",
          abs(dur - expected) < 1.0 and audio.dtype == np.float32,
          f"{dur:.1f}s, {audio.dtype}, peak {np.abs(audio).max():.3f}")

# --------------------------------------------- language sample placement
print("\n--- language sampling (stub model) ---")


class StubModel:
    """Returns 'ar' everywhere except one window, to prove voting works and
    that samples are spread across the file rather than taken from the start."""

    def __init__(self) -> None:
        self.calls: list[int] = []

    def detect_language(self, audio=None, vad_filter=False, **kw):
        self.calls.append(len(audio))
        return ("en", 0.55, []) if len(self.calls) == 2 else ("ar", 0.93, [])


stub = StubModel()
one_hour = np.zeros(SAMPLE_RATE * 3600, dtype=np.float32)
lang, conf, votes = detect_language(stub, one_hour, n_samples=5, log=lambda *a: None)
check("votes across 5 windows", len(stub.calls) == 5, f"{len(stub.calls)} windows")
check("majority language wins", lang == "ar", f"{lang} {conf:.0%} votes={votes}")
check("minority language still recorded", "en" in votes)
check("each window is 30s", all(c == SAMPLE_RATE * 30 for c in stub.calls))

short = np.zeros(SAMPLE_RATE * 20, dtype=np.float32)
stub2 = StubModel()
lang2, _, _ = detect_language(stub2, short, log=lambda *a: None)
check("short file uses a single window", len(stub2.calls) == 1, f"{lang2}")

# ----------------------------------------------------------- checkpoint
print("\n--- checkpoint / resume ---")
with tempfile.TemporaryDirectory() as td:
    ck = Path(td) / "partial.jsonl"
    rows = [{"id": i, "start": i * 10.0, "end": i * 10.0 + 9.5, "text": f"مقطع {i}"}
            for i in range(5)]
    ck.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                  encoding="utf-8")
    segs, resume = load_checkpoint(ck)
    check("reads all segments", len(segs) == 5, f"{len(segs)} segments")
    check("resumes from last end", abs(resume - 49.5) < 1e-6, f"{resume}")
    check("arabic survives the round trip", segs[2]["text"] == "مقطع 2", segs[2]["text"])

    # simulate a hard power cut mid-write
    with ck.open("a", encoding="utf-8") as f:
        f.write('{"id": 5, "start": 50.0, "end": 59.5, "te')
    segs2, resume2 = load_checkpoint(ck)
    check("tolerates a truncated final line", len(segs2) == 5 and abs(resume2 - 49.5) < 1e-6,
          f"{len(segs2)} segments, resume {resume2}")

    segs3, resume3 = load_checkpoint(Path(td) / "nope.jsonl")
    check("missing checkpoint starts at zero", segs3 == [] and resume3 == 0.0)

# ----------------------------------------------------------- paragraphs
print("\n--- paragraph building ---")
segs = [
    {"start": 0.0, "end": 2.0, "text": "السلام عليكم ورحمة الله"},
    {"start": 2.1, "end": 5.0, "text": "معانا النهاردة موضوع مهم جدًا."},
    {"start": 9.0, "end": 12.0, "text": "نبدأ بالجزء الأول"},      # 4s gap -> break
    {"start": 12.1, "end": 14.0, "text": "وهو الأساس."},
    {"start": 14.2, "end": 16.0, "text": "Thanks for watching"},    # hallucination
    {"start": 30.0, "end": 33.0, "text": "   "},                    # empty
    {"start": 40.0, "end": 44.0, "text": "تمام تمام تمام تمام خلاص"},  # stutter
]
ps = build_paragraphs(segs)
check("splits on a long pause", len(ps) == 3, f"{len(ps)} paragraphs")
check("merges within a pause", ps[0]["text"].startswith("السلام عليكم ورحمة الله معانا"))
check("drops known hallucinations",
      not any("watching" in p["text"].lower() for p in ps))
check("drops empty segments", all(p["text"].strip() for p in ps))
check("collapses stutters", ps[-1]["text"] == "تمام خلاص", ps[-1]["text"])
check("keeps paragraph timings", ps[1]["start"] == 9.0 and ps[1]["end"] == 14.0)
check("rtl for arabic, not for english", is_rtl("ar") and not is_rtl("en"))

# -------------------------------------------------------------- outputs
print("\n--- output files ---")
with tempfile.TemporaryDirectory() as td:
    meta = {"language": "ar", "duration_seconds": 44.0, "model": "large-v3-turbo"}
    files = write_outputs(Path(td), "sample", segs, ps, meta, with_timestamps=True)
    names = {f.suffix for f in files}
    check("writes txt, srt and json", names == {".txt", ".srt", ".json"}, str(sorted(names)))

    txt = (Path(td) / "sample.txt").read_text(encoding="utf-8")
    check("txt is utf-8 with arabic", "السلام عليكم" in txt)
    check("timestamps prefixed when asked", txt.startswith("[00:00:00]"), txt[:14])
    check("paragraphs separated by a blank line", "\n\n" in txt)

    srt = (Path(td) / "sample.srt").read_text(encoding="utf-8")
    check("srt has the arrow and comma-millis", "-->" in srt and ",000" in srt)
    check("srt excludes hallucinations", "watching" not in srt.lower())

    js = json.loads((Path(td) / "sample.json").read_text(encoding="utf-8"))
    check("json records rtl flag", js["meta"]["rtl"] is True)
    check("json keeps segments and paragraphs",
          len(js["paragraphs"]) == 3 and len(js["segments"]) == len(segs))

# ----------------------------------------------------------------- done
print("\n" + "=" * 60)
if failures:
    print(f"FAILED: {len(failures)}")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("all checks passed")
print("=" * 60)
