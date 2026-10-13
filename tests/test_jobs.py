"""Tests for the job queue, the honest progress values, and the model manager.

These cover three reported problems:

1. The progress window showed "Progress: 0 percent, Time remaining: less than a
   second, Speed: 0.0 times real time" on an 18-minute file, because
   openai-whisper returned every segment only at the very end, so there was
   exactly one progress report and nothing to measure from.
2. Cancelling a transcription blocked starting another one until the cancel
   finished unwinding.
3. Settings offered models that were not on the machine and selecting one
   started a silent multi-gigabyte download that appeared to hang.

Run:  PYTHONUTF8=1 .venv/Scripts/python.exe tests/test_jobs.py
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np

from engine import models
from engine.backends import MODEL_NAMES
from app.jobs import JobManager
from engine.chunking import SAMPLE_RATE, plan_chunks
from engine.job import Progress

PASS, FAIL = "  ok  ", " FAIL "
failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""),
          flush=True)
    if not cond:
        failures.append(name)


def write_wav(path: Path, seconds: float, tone: bool = True) -> None:
    """A wav with alternating tone and silence, so VAD has something to find."""
    n = int(SAMPLE_RATE * seconds)
    audio = np.zeros(n, dtype=np.float32)
    if tone:
        t = np.arange(n) / SAMPLE_RATE
        speechish = (0.3 * np.sin(2 * np.pi * 180 * t)
                     * (1 + 0.5 * np.sin(2 * np.pi * 3 * t)))
        # 8 seconds of sound, 2 of silence, repeating
        mask = ((t % 10) < 8).astype(np.float32)
        audio = (speechish * mask).astype(np.float32)
    pcm = (audio * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())


# --------------------------------------------------------------- chunking
print("\n--- chunking gives progress something to report ---")
audio = np.zeros(SAMPLE_RATE * 600, dtype=np.float32)
t = np.arange(len(audio)) / SAMPLE_RATE
audio = (0.3 * np.sin(2 * np.pi * 180 * t) * ((t % 10) < 8)).astype(np.float32)

chunks = plan_chunks(audio)
check("a 10-minute file is split into several chunks", len(chunks) > 2,
      f"{len(chunks)} chunks")
check("chunks are in order and do not overlap",
      all(chunks[i][1] <= chunks[i + 1][0] for i in range(len(chunks) - 1)))
check("chunks stay inside the audio",
      chunks[0][0] >= 0 and chunks[-1][1] <= len(audio))
lens = [(e - s) / SAMPLE_RATE for s, e in chunks]
check("no chunk is absurdly long", max(lens) <= 160, f"longest {max(lens):.0f}s")
covered = sum(e - s for s, e in chunks) / SAMPLE_RATE
check("covers most of the speech", covered > 400,
      f"{covered:.0f}s of {len(audio) / SAMPLE_RATE:.0f}s")

silence = np.zeros(SAMPLE_RATE * 200, dtype=np.float32)
sc = plan_chunks(silence)
check("silence still returns full coverage rather than nothing",
      len(sc) > 0 and sc[-1][1] == len(silence), f"{len(sc)} chunks")
check("an empty array returns no chunks",
      plan_chunks(np.zeros(0, dtype=np.float32)) == [])

# -------------------------------------------------------- honest progress
print("\n--- progress does not invent an eta or a speed ---")
p = Progress()
check("a fresh snapshot is not claiming a measurement", p.measured is False)
check("and reports no speed", p.realtime_factor == 0.0)
check("and no time remaining", p.eta_seconds == 0.0)
p = Progress(state="transcribing", position=0.0, duration=1076.0)
check("percent is zero at the start, which is true", p.percent == 0.0)


class SlowBackend:
    """Yields segments with a real delay, so progress can be observed arriving
    rather than appearing all at once at the end."""

    name = "slow-fake"
    label = "slow/test"
    supports_resume = True

    def __init__(self, segments: int = 6, delay: float = 0.12):
        self.segments = segments
        self.delay = delay

    def detect_language(self, audio=None, vad_filter=False, **kw):
        return "ar", 0.97, []

    def stream_segments(self, audio, language, opts):
        total = len(audio) / SAMPLE_RATE
        step = total / self.segments
        for i in range(self.segments):
            time.sleep(self.delay)
            yield {"id": i, "start": round(i * step, 3),
                   "end": round((i + 1) * step, 3),
                   "text": f"part {i}", "avg_logprob": -0.3,
                   "no_speech_prob": 0.01, "compression_ratio": 1.4}


from engine.job import run_transcription  # noqa: E402  (after the fake above)

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    wav = td / "clip.wav"
    write_wav(wav, 60)
    seen: list[Progress] = []
    # The delay has to carry the run past the two-second gate before a rate is
    # claimed. A real chunk takes a minute or more, so that gate never bites in
    # practice; it only matters here.
    run_transcription(wav, SlowBackend(segments=8, delay=0.4), td / "out",
                      on_progress=lambda pr: seen.append(
                          Progress(**{k: getattr(pr, k) for k in
                                      pr.__dataclass_fields__})))
    transcribing = [s for s in seen if s.state == "transcribing"]
    check("progress is reported many times, not once", len(transcribing) >= 8,
          f"{len(transcribing)} updates")
    check("position climbs", [s.position for s in transcribing]
          == sorted(s.position for s in transcribing))
    early = transcribing[0]
    check("the first update admits it has not measured a rate yet",
          early.measured is False and early.realtime_factor == 0.0,
          f"measured={early.measured} speed={early.realtime_factor}")
    check("no fake eta on the first update", early.eta_seconds == 0.0)
    later = [s for s in transcribing if s.measured]
    check("a rate is reported once there is something to measure",
          bool(later) and later[-1].realtime_factor > 0,
          f"{later[-1].realtime_factor}x" if later else "never measured")
    check("the eta is positive while work remains",
          any(s.eta_seconds > 0 for s in later[:-1]) or len(later) < 2,
          str([s.eta_seconds for s in later[:3]]))

# ------------------------------------------------------------ job queue
print("\n--- the queue never blocks a submission ---")
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    for i in range(3):
        write_wav(td / f"f{i}.wav", 30)

    backend = SlowBackend(segments=10, delay=0.25)
    finished: list = []
    mgr = JobManager(lambda: backend, td / "out",
                     on_finished=lambda j: finished.append(j))

    j1 = mgr.submit(str(td / "f0.wav"))
    check("a job is accepted", j1.id == 1 and j1.state == "queued")

    for _ in range(80):
        if mgr.get(j1.id).state == "running":
            break
        time.sleep(0.05)
    check("it starts running", mgr.get(j1.id).state == "running",
          mgr.get(j1.id).state)

    j2 = mgr.submit(str(td / "f1.wav"))
    check("a second job can be submitted while the first runs",
          j2.state == "queued", j2.state)
    check("and it is listed as waiting its turn", mgr.queued_count == 1,
          f"{mgr.queued_count} queued")

    # The reported bug: cancel, then try to submit.
    check("cancelling the running job is accepted", mgr.cancel(j1.id) is True)
    check("it goes to cancelling, not straight to cancelled",
          mgr.get(j1.id).state in ("cancelling", "cancelled"),
          mgr.get(j1.id).state)
    j3 = mgr.submit(str(td / "f2.wav"))
    check("a new job can be submitted DURING the cancel",
          j3.state == "queued", "this was the reported bug")
    check("three jobs are tracked", len(mgr.all()) == 3, f"{len(mgr.all())}")

    for _ in range(300):
        if not mgr.active():
            break
        time.sleep(0.1)
    states = {j.id: j.state for j in mgr.all()}
    check("the cancelled job ends cancelled", states[j1.id] == "cancelled",
          states[j1.id])
    check("the queued jobs still completed", states[j2.id] == "done"
          and states[j3.id] == "done", str(states))
    check("every job reported finishing", len(finished) == 3, f"{len(finished)}")

    check("cancelling a finished job does nothing", mgr.cancel(j1.id) is False)
    check("clear_finished removes them", mgr.clear_finished() == 3)
    check("and leaves an empty list", mgr.all() == [])
    mgr.shutdown()

print("\n--- cancelling a job that has not started yet ---")
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    for i in range(2):
        write_wav(td / f"q{i}.wav", 20)
    backend = SlowBackend(segments=8, delay=0.3)
    mgr = JobManager(lambda: backend, td / "out")
    a = mgr.submit(str(td / "q0.wav"))
    b = mgr.submit(str(td / "q1.wav"))
    for _ in range(80):
        if mgr.get(a.id).state == "running":
            break
        time.sleep(0.05)
    check("a queued job cancels immediately", mgr.cancel(b.id) is True)
    check("and is cancelled outright, having never started",
          mgr.get(b.id).state == "cancelled", mgr.get(b.id).state)
    mgr.cancel(a.id)
    for _ in range(200):
        if not mgr.active():
            break
        time.sleep(0.1)
    mgr.shutdown()
    check("nothing is left active", not mgr.active())

print("\n--- a job with no model loaded fails cleanly ---")
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    write_wav(td / "x.wav", 10)
    mgr = JobManager(lambda: None, td / "out")
    j = mgr.submit(str(td / "x.wav"))
    for _ in range(100):
        if not j.active:
            break
        time.sleep(0.05)
    check("it ends in error, not silently", j.state == "error", j.state)
    check("with a readable reason", "model" in j.error.lower(), j.error)
    mgr.shutdown()

# -------------------------------------------------------------- models
print("\n--- model manager ---")
info = models.info("turbo")
check("knows the checkpoint for a settings key",
      info.checkpoint == "large-v3-turbo", info.checkpoint)
check("reports the download size", info.expected_size > 1_000_000_000,
      info.size_text)
# "Installed" is detected by size, so prove that with a stand-in rather than
# requiring a real 1.6 GB checkpoint on disk. These tests used to assume the
# recommended model happened to be installed, and started failing the moment it
# was deleted - a test must not depend on what a developer has lying around.
_orig_sizes = dict(models.SIZES)
_stand_in = models.MODELS_DIR / "small.pt"
try:
    models.SIZES["small"] = 2048
    models.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    _stand_in.write_bytes(b"\0" * 2048)
    m = models.info("small")
    check("a file of the expected size counts as installed",
          m.installed is True, f"{m.actual_size} of {m.expected_size} bytes")
    check("installed_keys names it", "small" in models.installed_keys(),
          str(models.installed_keys()))

    _stand_in.write_bytes(b"\0" * 100)
    m = models.info("small")
    check("a file of the wrong size is an incomplete download",
          m.partial is True and m.installed is False,
          f"{m.actual_size} of {m.expected_size} bytes")

    _stand_in.unlink()
    m = models.info("small")
    check("no file at all means not installed",
          m.installed is False and m.partial is False)
    check("installed_keys no longer names it",
          "small" not in models.installed_keys(), str(models.installed_keys()))
finally:
    models.SIZES.clear()
    models.SIZES.update(_orig_sizes)
    _stand_in.unlink(missing_ok=True)

missing = models.info("medium")
check("reports a model that is not here", missing.installed is False,
      f"{missing.checkpoint} {missing.size_text}")
check("and does not call it a partial download", missing.partial is False)

check("lists every offered model", len(models.all_info()) >= 4,
      ", ".join(m.checkpoint for m in models.all_info()))
check("installed_keys never names a model that is absent",
      all((models.MODELS_DIR / f"{MODEL_NAMES[k][1]}.pt").exists()
          for k in models.installed_keys()),
      str(models.installed_keys()) or "nothing installed, which is also valid")
check("human sizes read sensibly",
      models.human(1_617_941_637) == "1.5 GB" and models.human(483_617_219) == "461 MB",
      f"{models.human(1_617_941_637)} / {models.human(483_617_219)}")

# A zero-byte file is exactly what the old blocking download left behind.
zero = models.MODELS_DIR / "medium.pt"
try:
    zero.write_bytes(b"")
    check("a zero-byte file is not reported as installed",
          models.info("medium").installed is False)
    removed = models.cleanup_partials()
    check("cleanup removes the zero-byte leftover",
          "medium.pt" in removed and not zero.exists(), str(removed))
finally:
    zero.unlink(missing_ok=True)

half = models.MODELS_DIR / "small.pt"
try:
    half.write_bytes(b"x" * 1024)
    m = models.info("small")
    check("a truncated file is flagged as an incomplete download",
          m.partial is True and m.installed is False,
          f"{m.actual_size} of {m.expected_size} bytes")
finally:
    half.unlink(missing_ok=True)

cancel = threading.Event()
cancel.set()
try:
    models.download("small", cancel=cancel)
    check("a pre-cancelled download raises", False, "it did not raise")
except models.DownloadCancelled:
    check("a pre-cancelled download raises DownloadCancelled", True)
except Exception as exc:
    check("a pre-cancelled download raises DownloadCancelled", False,
          f"{type(exc).__name__}: {exc}")
check("and leaves no partial file behind",
      not (models.MODELS_DIR / "small.pt.part").exists()
      and not (models.MODELS_DIR / "small.pt").exists())

print("\n" + "=" * 60)
if failures:
    print(f"FAILED: {len(failures)}")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("all job, progress and model checks passed")
print("=" * 60)
