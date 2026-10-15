"""Live dictation: phrase cutting and the session loop.

Neither a microphone nor the Whisper weights are needed. The segmenter is pure
logic with one injectable speech detector, and the session takes a fake
microphone and a fake backend - so what is tested is the cutting rules and the
threading, which is where the behaviour a speaker feels actually lives.

Note on fixtures: a sine tone cannot stand in for speech, because Silero
correctly reports a tone as not speech. That is desirable - a fan should not
start a phrase - so the tests inject a detector that treats loud audio as
speech, and the real Silero path is checked separately for that property.

Run:  PYTHONUTF8=1 .venv/Scripts/python.exe tests/test_dictation.py
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from engine.audio import SAMPLE_RATE
from engine.dictation import (
    DICTATION_OPTS,
    Dictation,
    DictationConfig,
    UtteranceSegmenter,
    _energy_speech,
    _silero_speech,
)

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))
    if not ok:
        failures.append(label)


# ------------------------------------------------------------------- fixtures
def loud(seconds: float, amplitude: float = 0.4) -> np.ndarray:
    """Stands in for speech: anything the injected detector calls loud."""
    n = int(seconds * SAMPLE_RATE)
    rng = np.random.default_rng(0)
    return (rng.standard_normal(n) * amplitude).astype(np.float32)


def quiet(seconds: float) -> np.ndarray:
    n = int(seconds * SAMPLE_RATE)
    return np.zeros(n, dtype=np.float32)


def amplitude_speech(audio: np.ndarray, sampling_rate: int = SAMPLE_RATE) -> list[dict]:
    """Deterministic stand-in for VAD: loud 10 ms frames are speech."""
    frame = sampling_rate // 100
    usable = len(audio) // frame * frame
    if not usable:
        return []
    frames = np.abs(audio[:usable]).reshape(-1, frame).max(axis=1)
    spans, start = [], None
    for i, level in enumerate(frames):
        if level > 0.05 and start is None:
            start = i
        elif level <= 0.05 and start is not None:
            spans.append({"start": start * frame, "end": i * frame})
            start = None
    if start is not None:
        spans.append({"start": start * frame, "end": usable})
    return spans


def feed_all(seg: UtteranceSegmenter, audio: np.ndarray,
             block_ms: int = 100) -> list[np.ndarray]:
    """Push audio through in realistic blocks and collect the phrases."""
    step = int(block_ms * SAMPLE_RATE / 1000)
    out = []
    for i in range(0, len(audio), step):
        out.extend(seg.feed(audio[i:i + step]))
    return out


def new_segmenter(**kw) -> UtteranceSegmenter:
    return UtteranceSegmenter(DictationConfig(**kw), speech_fn=amplitude_speech)


# ----------------------------------------------------------------- the options
print("--- decoding options ---")

check("word timestamps are off for dictation",
      DICTATION_OPTS["word_timestamps"] is False)
check("the inert hallucination option is not carried over",
      "hallucination_silence_threshold" not in DICTATION_OPTS)
check("the anti-hallucination temperature fallback is kept",
      len(DICTATION_OPTS["temperature"]) > 1, str(DICTATION_OPTS["temperature"]))
check("conditioning on previous text stays off",
      DICTATION_OPTS["condition_on_previous_text"] is False)


# -------------------------------------------------------------- phrase cutting
print("\n--- phrase cutting ---")

seg = new_segmenter(pause_ms=600)
phrases = feed_all(seg, np.concatenate([loud(1.0), quiet(1.0)]))
check("a phrase followed by a pause is emitted", len(phrases) == 1,
      f"{len(phrases)} phrases")
if phrases:
    seconds = len(phrases[0]) / SAMPLE_RATE
    check("the phrase is trimmed to its speech plus padding",
          1.0 <= seconds <= 1.6, f"{seconds:.2f}s")

seg = new_segmenter(pause_ms=600)
phrases = feed_all(seg, np.concatenate([
    loud(0.8), quiet(1.0), loud(0.8), quiet(1.0)]))
check("two phrases separated by a pause are two phrases", len(phrases) == 2,
      f"{len(phrases)} phrases")

# A breath mid-sentence is shorter than the threshold and must not split it.
seg = new_segmenter(pause_ms=600)
phrases = feed_all(seg, np.concatenate([
    loud(0.8), quiet(0.3), loud(0.8), quiet(1.0)]))
check("a short breath does not split a sentence", len(phrases) == 1,
      f"{len(phrases)} phrases")

seg = new_segmenter(pause_ms=600)
phrases = feed_all(seg, quiet(3.0))
check("silence alone produces nothing", phrases == [], f"{len(phrases)} phrases")

seg = new_segmenter(pause_ms=600, min_speech_ms=250)
phrases = feed_all(seg, np.concatenate([loud(0.08), quiet(1.0)]))
check("a click too short to be speech is discarded", phrases == [],
      f"{len(phrases)} phrases")

# Waiting at an open microphone must not grow the buffer without end.
seg = new_segmenter(pause_ms=600, tail_window_s=1.5)
feed_all(seg, quiet(20.0))
held = len(seg._buf) / SAMPLE_RATE
check("waiting on silence does not grow memory without end", held <= 1.6,
      f"{held:.2f}s held")

# Someone who does not pause must still see words appear.
seg = new_segmenter(pause_ms=600, max_phrase_s=2.0)
phrases = feed_all(seg, loud(5.0))
check("an unbroken talker is cut anyway", len(phrases) >= 2,
      f"{len(phrases)} phrases")

seg = new_segmenter(pause_ms=600)
feed_all(seg, loud(0.5))                    # still talking when stop is pressed
tail = seg.flush()
check("the last words are kept when recording stops", tail is not None,
      "nothing" if tail is None else f"{len(tail) / SAMPLE_RATE:.2f}s")
check("flush leaves the segmenter empty", len(seg._buf) == 0)

seg = new_segmenter()
check("flushing an empty segmenter gives nothing", seg.flush() is None)


# ------------------------------------------------------------ speech detection
print("\n--- speech detection ---")

tone = (0.3 * np.sin(2 * np.pi * 200 * np.arange(SAMPLE_RATE) / SAMPLE_RATE)
        ).astype(np.float32)
silero = _silero_speech(np.concatenate([quiet(0.5), tone, quiet(0.8)]))
if silero is None:
    print("  skip  silero is not installed")
else:
    check("silero does not mistake a steady tone for speech", silero == [],
          str(silero))

spans = _energy_speech(np.concatenate([quiet(0.5), loud(1.0), quiet(0.5)]))
check("the energy fallback finds a loud span", len(spans) >= 1, str(len(spans)))
check("the energy fallback finds nothing in silence",
      _energy_speech(quiet(2.0)) == [])

# If Silero cannot be loaded, dictation must degrade rather than go deaf.
seg = UtteranceSegmenter(DictationConfig())
seg._silero_failed = True
spans = seg.speech_regions(np.concatenate([quiet(0.3), loud(1.0), quiet(0.3)]))
check("a segmenter without silero still detects speech", len(spans) >= 1)
check("and reports that it fell back", seg.vad_unavailable)


# ---------------------------------------------------------------- the session
print("\n--- the session ---")


class FakeMic:
    """Serves a fixed script of blocks, then ends the stream."""

    def __init__(self, audio: np.ndarray, block_ms: int = 100):
        self.step = int(block_ms * SAMPLE_RATE / 1000)
        self.audio = audio
        self.pos = 0
        self.stopped = False

    def start(self) -> None:
        pass

    def read(self, timeout: float = 0.5):
        if self.stopped:
            return None
        if self.pos >= len(self.audio):
            time.sleep(0.01)
            return np.empty(0, dtype=np.float32)
        block = self.audio[self.pos:self.pos + self.step]
        self.pos += self.step
        return block

    def stop(self) -> None:
        self.stopped = True


class FakeBackend:
    """Returns a canned phrase, and records what it was asked."""

    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = []
        self.detections = []
        self.lock = threading.Lock()

    def detect_language(self, audio=None, **kw):
        with self.lock:
            self.detections.append(len(audio) if audio is not None else 0)
        return "ar", 0.97, [("ar", 0.97)]

    def stream_segments(self, audio, language, opts):
        with self.lock:
            self.calls.append({"samples": len(audio), "language": language,
                               "opts": opts})
            text = self.texts.pop(0) if self.texts else ""
        yield {"text": text}


def run_session(audio, texts, **cfg):
    backend = FakeBackend(texts)
    got: list[str] = []
    errors: list[str] = []
    d = Dictation(
        backend,
        config=DictationConfig(**cfg),
        on_text=got.append,
        on_error=errors.append,
        mic_factory=lambda: FakeMic(audio),
        segmenter=UtteranceSegmenter(DictationConfig(**cfg),
                                     speech_fn=amplitude_speech),
    )
    d.start()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and len(got) < len(texts):
        time.sleep(0.02)
    d.stop()
    return d, backend, got, errors


script = np.concatenate([loud(0.8), quiet(1.0), loud(0.8), quiet(1.0)])
d, backend, got, errors = run_session(script, ["السلام عليكم", "ممم طيب"],
                                      pause_ms=600)
check("each phrase is delivered once", got == ["السلام عليكم", "طيب"], str(got))
check("hesitation is filtered on the way out",
      "ممم" not in " ".join(got), str(got))
check("what the filter removed is recorded", "ممم" in d.state.removed,
      str(d.state.removed))
check("no errors were raised", errors == [], str(errors))
check("the session is stopped afterwards", d.running is False)
check("phrases are counted", d.state.phrases == 2, str(d.state.phrases))
check("words are counted", d.state.words == 3, str(d.state.words))

check("the language is settled for the session", d.state.language == "ar",
      d.state.language)
check("it is asked once, not once per phrase", len(backend.detections) == 1,
      f"{len(backend.detections)} detections")
check("it is asked about more audio than one short phrase",
      bool(backend.detections) and backend.detections[0] > 1.5 * SAMPLE_RATE,
      f"{backend.detections[0] / SAMPLE_RATE:.2f}s" if backend.detections else "never")
check("once settled, it is passed to the following calls",
      backend.calls[-1]["language"] == "ar",
      str([c["language"] for c in backend.calls]))
check("each call got only its own phrase, not the whole session",
      all(c["samples"] < 1.8 * SAMPLE_RATE for c in backend.calls),
      str([round(c["samples"] / SAMPLE_RATE, 2) for c in backend.calls]))

# An empty transcript must not reach the document.
_d, _b, got, _e = run_session(np.concatenate([loud(0.8), quiet(1.0)]), [""],
                              pause_ms=600)
check("a phrase that transcribes to nothing inserts nothing", got == [],
      str(got))


class ExplodingMic(FakeMic):
    def start(self):
        from engine.mic import MicrophoneError

        raise MicrophoneError("No microphone was found.")


d = Dictation(FakeBackend([]), mic_factory=lambda: ExplodingMic(quiet(1.0)))
raised = ""
try:
    d.start()
except Exception as exc:
    raised = str(exc)
check("a microphone that cannot start raises, and does not leave a session running",
      "No microphone" in raised and d.running is False, raised)
check("the failure is recorded on the state", "No microphone" in d.state.error)


class BrokenBackend(FakeBackend):
    def stream_segments(self, audio, language, opts):
        raise RuntimeError("model is wedged")
        yield  # pragma: no cover


errors: list[str] = []
d = Dictation(
    BrokenBackend([]),
    config=DictationConfig(pause_ms=600),
    on_text=lambda t: None,
    on_error=errors.append,
    mic_factory=lambda: FakeMic(np.concatenate([loud(0.8), quiet(1.0)])),
    segmenter=UtteranceSegmenter(DictationConfig(pause_ms=600),
                                 speech_fn=amplitude_speech),
)
d.start()
deadline = time.monotonic() + 10
while time.monotonic() < deadline and not errors:
    time.sleep(0.02)
d.stop()
check("a backend failure is reported, not swallowed", bool(errors), str(errors))

# An insert that throws (Word closed mid-sentence) must not end the session.
survived: list[str] = []


def throw_once(text: str) -> None:
    survived.append(text)
    if len(survived) == 1:
        raise RuntimeError("Word went away")


d = Dictation(
    FakeBackend(["one", "two"]),
    config=DictationConfig(pause_ms=600),
    on_text=throw_once,
    mic_factory=lambda: FakeMic(np.concatenate([
        loud(0.8), quiet(1.0), loud(0.8), quiet(1.0)])),
    segmenter=UtteranceSegmenter(DictationConfig(pause_ms=600),
                                 speech_fn=amplitude_speech),
)
d.start()
deadline = time.monotonic() + 15
while time.monotonic() < deadline and len(survived) < 2:
    time.sleep(0.02)
d.stop()
check("a failed insert does not end the session", survived == ["one", "two"],
      str(survived))

d = Dictation(FakeBackend([]), mic_factory=lambda: FakeMic(quiet(0.5)))
d.stop()                                    # never started
check("stopping a session that never started is harmless", d.running is False)


# ----------------------------------------------------------------------- done
print("\n" + "=" * 60)
if failures:
    print(f"FAILED: {len(failures)}")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("all checks passed")
print("=" * 60)
