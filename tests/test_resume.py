"""Regression tests for the duplicated-transcript bug.

Reported symptom: transcribing a file inserted the whole transcript twice.

Cause: the app passed resume=True on every run; run_transcription kept the old
checkpoint and opened it in append mode, then set clip_timestamps to skip the
finished part — but OpenAIWhisperBackend lists clip_timestamps in DROP because
the reference implementation does not support it, so the option was silently
discarded and the entire file was transcribed again on top of the previous pass.

These tests use a fake backend so they run in a second and need no weights.

Run:  PYTHONUTF8=1 .venv/Scripts/python.exe tests/test_resume.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np

from engine.backends import OpenAIWhisperBackend
from engine.job import load_checkpoint, run_transcription

PASS, FAIL = "  ok  ", " FAIL "
failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""),
          flush=True)
    if not cond:
        failures.append(name)


SENTENCES = ["السلام عليكم", "هذا اختبار", "للتفريغ النصي", "والحمد لله"]


class FakeBackend:
    """Returns four fixed segments covering 0-20s, and records the options it
    was handed so a test can assert what the pipeline asked for."""

    name = "fake"
    label = "fake/test"

    def __init__(self, supports_resume: bool):
        self.supports_resume = supports_resume
        self.calls: list[dict] = []

    def detect_language(self, audio=None, vad_filter=False, **kw):
        return "ar", 0.99, []

    def stream_segments(self, audio, language, opts):
        self.calls.append(dict(opts))
        clip = opts.get("clip_timestamps")
        start_at = clip[0] if clip else 0.0
        for i, text in enumerate(SENTENCES):
            s, e = i * 5.0, i * 5.0 + 4.5
            if s < start_at:
                continue                 # a backend that honours the request
            yield {"id": i, "start": s, "end": e, "text": text,
                   "avg_logprob": -0.3, "no_speech_prob": 0.01,
                   "compression_ratio": 1.4}


def write_wav(path: Path, seconds: float = 20.0) -> None:
    """A real silent wav, so decode_audio works through ffmpeg as usual."""
    import struct
    import wave

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))


# ---------------------------------------------------------- the reported bug
print("\n--- the reported bug: transcript inserted twice ---")
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    audio = td / "clip.wav"
    write_wav(audio)
    out = td / "out"

    # First run, as the app does it.
    be = FakeBackend(supports_resume=False)
    m1 = run_transcription(audio, be, out, resume=True)
    check("first run transcribes once", m1["segments"] == 4, f"{m1['segments']} segments")
    check("first run text is correct", m1["text"].count("السلام عليكم") == 1,
          f"{m1['text'].count('السلام عليكم')} occurrences")

    ckpt = out / "clip" / "partial.jsonl"
    check("a finished run leaves no checkpoint behind", not ckpt.exists(),
          "a checkpoint now means 'interrupted', nothing else")

    # Second run on the same file — this is what produced the duplicate.
    m2 = run_transcription(audio, be, out, resume=True)
    check("running again does not double the transcript",
          m2["text"].count("السلام عليكم") == 1,
          f"{m2['text'].count('السلام عليكم')} occurrences of the first sentence")
    check("segment count stays right", m2["segments"] == 4, f"{m2['segments']} segments")
    check("no sentence appears twice",
          all(m2["text"].count(s) == 1 for s in SENTENCES),
          {s: m2["text"].count(s) for s in SENTENCES}.__str__())

    # Third and fourth, because the original bug compounded each run.
    m3 = run_transcription(audio, be, out, resume=True)
    m4 = run_transcription(audio, be, out, resume=True)
    check("it does not compound over repeated runs",
          m4["segments"] == 4 and m4["text"] == m1["text"],
          f"run1 {m1['segments']} -> run4 {m4['segments']} segments")

# ------------------------------------------------ resume refused when unsupported
print("\n--- resume is refused on a backend that cannot honour it ---")
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    audio = td / "clip.wav"
    write_wav(audio)
    out = td / "out"
    ckdir = out / "clip"
    ckdir.mkdir(parents=True)
    # Pretend a previous run stopped half way.
    (ckdir / "partial.jsonl").write_text(
        "\n".join(json.dumps({"id": i, "start": i * 5.0, "end": i * 5.0 + 4.5,
                              "text": SENTENCES[i]}, ensure_ascii=False)
                  for i in range(2)) + "\n", encoding="utf-8")

    be_no = FakeBackend(supports_resume=False)
    m = run_transcription(audio, be_no, out, resume=True)
    check("a backend without resume support starts clean",
          be_no.calls[-1].get("clip_timestamps") is None,
          "clip_timestamps was not sent")
    check("and still produces each sentence once",
          all(m["text"].count(s) == 1 for s in SENTENCES), f"{m['segments']} segments")

    # Now the same half-done checkpoint against a backend that does support it.
    (ckdir / "partial.jsonl").write_text(
        "\n".join(json.dumps({"id": i, "start": i * 5.0, "end": i * 5.0 + 4.5,
                              "text": SENTENCES[i]}, ensure_ascii=False)
                  for i in range(2)) + "\n", encoding="utf-8")
    be_yes = FakeBackend(supports_resume=True)
    m = run_transcription(audio, be_yes, out, resume=True)
    check("a backend with resume support is asked to skip ahead",
          be_yes.calls[-1].get("clip_timestamps") is not None,
          str(be_yes.calls[-1].get("clip_timestamps")))
    check("resuming keeps the earlier work and adds the rest",
          m["segments"] == 4 and all(m["text"].count(s) == 1 for s in SENTENCES),
          f"{m['segments']} segments")

# ------------------------------------------- a complete checkpoint is not resumed
print("\n--- a checkpoint that reaches the end is finished work ---")
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    audio = td / "clip.wav"
    write_wav(audio)
    out = td / "out"
    ckdir = out / "clip"
    ckdir.mkdir(parents=True)
    (ckdir / "partial.jsonl").write_text(
        "\n".join(json.dumps({"id": i, "start": i * 5.0, "end": i * 5.0 + 4.5,
                              "text": SENTENCES[i]}, ensure_ascii=False)
                  for i in range(4)) + "\n", encoding="utf-8")
    be = FakeBackend(supports_resume=True)
    m = run_transcription(audio, be, out, resume=True)
    check("does not try to resume from the end",
          be.calls[-1].get("clip_timestamps") is None,
          "would have been a zero-length window")
    check("transcribes cleanly instead",
          all(m["text"].count(s) == 1 for s in SENTENCES), f"{m['segments']} segments")

# --------------------------------------------------- the loader's safety net
print("\n--- load_checkpoint de-duplicates as a safety net ---")
with tempfile.TemporaryDirectory() as td:
    ck = Path(td) / "partial.jsonl"
    rows = [{"id": i, "start": i * 5.0, "end": i * 5.0 + 4.5, "text": SENTENCES[i]}
            for i in range(4)]
    ck.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows * 2) + "\n",
                  encoding="utf-8")
    segs, resume_from = load_checkpoint(ck)
    check("a doubled checkpoint loads once", len(segs) == 4, f"{len(segs)} segments")
    check("resume point is the true end", resume_from == 19.5, str(resume_from))

    ck.write_text("\n".join(json.dumps(r, ensure_ascii=False)
                            for r in [rows[2], rows[0], rows[3], rows[1]]) + "\n",
                  encoding="utf-8")
    segs, _ = load_checkpoint(ck)
    check("out-of-order segments are sorted",
          [s["start"] for s in segs] == [0.0, 5.0, 10.0, 15.0],
          str([s["start"] for s in segs]))

# ------------------------------------------- the dropped-option trap in general
print("\n--- how openai-whisper handles the option that caused this ---")
check("it still keeps clip_timestamps away from transcribe()",
      "clip_timestamps" in OpenAIWhisperBackend.DROP,
      "the reference implementation has no such argument")
check("but now declares that it can resume",
      OpenAIWhisperBackend.supports_resume is True,
      "stream_segments honours the request itself by skipping whole chunks")


print("\n--- and the chunk skipping actually happens ---")


class FakeWhisper:
    """Stands in for the loaded torch model, recording which slices it was asked
    to transcribe so the test can prove earlier chunks were skipped."""

    def __init__(self):
        self.slices: list[int] = []

    def transcribe(self, audio, **kw):
        self.slices.append(len(audio))
        return {"segments": [{"id": 0, "start": 0.0, "end": 1.0, "text": "x",
                              "avg_logprob": -0.2, "no_speech_prob": 0.01,
                              "compression_ratio": 1.2}]}


be = OpenAIWhisperBackend.__new__(OpenAIWhisperBackend)   # skip loading weights
be.model = FakeWhisper()
be._fp16 = False

tone = np.arange(16000 * 300) / 16000.0
speechy = (0.3 * np.sin(2 * np.pi * 180 * tone) * ((tone % 10) < 8)).astype("float32")

all_chunks = len(list(be.stream_segments(speechy, "ar", {})))
without_skip = len(be.model.slices)
be.model.slices.clear()

list(be.stream_segments(speechy, "ar", {"clip_timestamps": [200.0, 300.0]}))
with_skip = len(be.model.slices)
check("resuming transcribes fewer chunks than starting over",
      with_skip < without_skip and with_skip > 0,
      f"{with_skip} chunks instead of {without_skip}")
check("and it did not simply do nothing", with_skip >= 1, f"{with_skip} chunks")

print("\n" + "=" * 60)
if failures:
    print(f"FAILED: {len(failures)}")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("all resume checks passed")
print("=" * 60)
