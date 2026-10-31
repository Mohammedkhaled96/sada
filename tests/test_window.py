"""Encoding a shorter window: does it cost less, and does it say the same?

This is the gate the whole dictation feature stands on. If a short window does
not produce the same words as the full one, dictation has to fall back to a
smaller model, so the comparison is made against the full window on real speech
rather than asserted.

Needs large-v3-turbo installed and takes a few minutes on a CPU.

Run:  PYTHONUTF8=1 .venv/Scripts/python.exe tests/test_window.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import window
from engine.audio import decode_audio
from engine.dictation import PAST_END_S

SR = 16000
failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))
    if not ok:
        failures.append(label)


# ------------------------------------------------------------- the arithmetic
print("--- choosing a window ---")

check("a short phrase gets the small window", window.window_for(3) == 8,
      str(window.window_for(3)))
check("headroom is kept, not spent", window.window_for(5.5) == 14,
      f"5.5s + {window.HEADROOM_S}s headroom -> {window.window_for(5.5)}s")
check("the longest segment still fits an offered window",
      window.window_for(window.MAX_SPEECH_S) == window.MAX_WINDOW_S)
check("anything longer falls back to the full window",
      window.window_for(25) == window.FULL_WINDOW_S)
# 11 s in a 14 s window repeated the opening phrase; 10 s did not.
check("the ceiling is where the measurement put it",
      window.window_for(window.MAX_SPEECH_S + 1) == window.FULL_WINDOW_S,
      f"{window.MAX_SPEECH_S + 1}s -> {window.window_for(window.MAX_SPEECH_S + 1)}s")
check("headroom grows with the speech, it is not a constant",
      window.headroom_for(10) > window.headroom_for(4))
check("there are few window sizes, because each one is a tensor shape",
      len(window.WINDOWS) <= 3, str(window.WINDOWS))

# 6 seconds for 5 seconds of speech is what produced "بسم الله بسم الله بس...".
check("no window is offered without real headroom",
      all(w - window.HEADROOM_S >= 4 for w in window.WINDOWS), str(window.WINDOWS))


# ------------------------------------------------------- against the full one
audio_file = ROOT / "audio" / "test.mp3"
if not audio_file.exists():
    print("\n(no audio/test.mp3 - skipping the comparison)")
else:
    from engine.backends import load_backend
    from engine.device import pick_device
    from engine.models import info

    if not info("turbo").installed:
        print("\n(large-v3-turbo is not installed - skipping the comparison)")
    else:
        print("\n--- the same words as the full window ---")
        audio = decode_audio(audio_file)
        backend = load_backend("openai", "turbo", pick_device("cpu"))
        model = backend.model
        opts = dict(language="ar", task="transcribe", fp16=False,
                    condition_on_previous_text=False)

        window.warm_up(model)           # as the app does, so timings are real

        for clip_s in (4, 8, 10):
            clip = audio[: int(clip_s * SR)].astype(np.float32)

            started = time.time()
            full = model.transcribe(clip, **opts)
            full_s = time.time() - started

            started = time.time()
            with window.short_window(model, clip_s) as w:
                short = model.transcribe(clip, **opts)
            short_s = time.time() - started

            def words(result):
                """Only what was inside the audio: a segment that starts after
                the clip ended is a hallucination, and the dictation pipeline
                drops it before the text is ever assembled."""
                return " ".join(
                    s["text"].strip() for s in result["segments"]
                    if s["start"] < clip_s - PAST_END_S).split()

            a, b = words(full), words(short)

            # Not equality. The full window is not ground truth: at 10 s it
            # stopped at its own boundary and dropped the next word, which the
            # short window caught. What must hold is that nothing is lost and
            # nothing is invented - so everything the full window heard is
            # here, in order, and anything extra is new speech rather than a
            # repeat of what was already said, which is how the decoder runs
            # on when the window is too tight.
            kept = a == b[:len(a)]
            extra = b[len(a):]
            repeated = bool(extra) and " ".join(extra) in " ".join(a)

            check(f"{clip_s}s in a {w}s window loses nothing", kept,
                  f"{len(a)} words vs {len(b)}")
            check(f"{clip_s}s in a {w}s window invents nothing", not repeated,
                  " ".join(extra) or "nothing extra")
            if not kept or repeated:
                print(f"        full : {' '.join(a)}")
                print(f"        short: {' '.join(b)}")
            check(f"{clip_s}s of speech is faster in a {w}s window",
                  short_s < full_s,
                  f"{short_s:.1f}s vs {full_s:.1f}s = {full_s / short_s:.1f}x")

        print("\n--- and leaves nothing behind ---")
        check("the window constants are restored",
              sys.modules["whisper.transcribe"].N_FRAMES == 3000,
              str(sys.modules["whisper.transcribe"].N_FRAMES))
        check("and so is the model's own idea of its width",
              model.dims.n_audio_ctx == 1500, str(model.dims.n_audio_ctx))


print("\n" + "=" * 60)
if failures:
    print(f"FAILED: {len(failures)}")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("all window checks passed")
print("=" * 60)
