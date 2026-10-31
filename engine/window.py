"""Encoding a shorter window than thirty seconds.

Whisper pads every clip to a 30-second window, so a two-second phrase costs
almost exactly what a twenty-second one does. Measured on the machine this was
written on, `large-v3-turbo` on CPU:

    30 s window, language auto-detected   18.5 s
    30 s window, language passed in        9.3 s
    15 s window, language passed in        4.2 s
     8 s window, language passed in        2.7 s   <- same text, byte for byte
     6 s window, language passed in        3.1 s   <- runs on into repetition

Dictation is only possible on a CPU because of that third line. The encoder's
positional embedding is `sinusoids(n_ctx, n_state)` - fixed, not learned - so
taking the first n frames of it is well defined, and attention then runs over
the frames that exist instead of 1500 of which 1400 are padding.

The last line is why HEADROOM exists. With a window barely longer than the
speech the decoder has nowhere to put its end-of-window prediction and starts
repeating itself. Three seconds of room was enough at five seconds of speech
and not at eleven, so the room grows with the content.

This is process-wide state inside someone else's package, so it is applied
under a lock and restored afterwards, and file transcription - which wants the
full 30 seconds - cannot run while it is in effect.
"""
from __future__ import annotations

import contextlib
import sys
import threading
import time

HOP_LENGTH = 160                 # samples per mel frame, from whisper.audio
FULL_WINDOW_S = 30
HEADROOM_S = 3
MIN_HEADROOM_FRACTION = 0.4

# Two sizes, not one per segment. Every new window length is a new tensor shape
# for the maths library underneath, and creating one costs several seconds the
# first time - the very first short call measured 12.4 s against 9.1 s for the
# full window, then 6.3 s for the next. Two shapes can be warmed up once at
# startup; a different shape per utterance could not be.
#
# Both sizes are ones that were checked against the full window on real speech.
# 28 s was not: it hallucinated a subtitle credit line into trailing silence
# and cost 65 s instead of 16 s, which is why nothing here goes near 30.
WINDOWS = (8, 14)
MAX_WINDOW_S = WINDOWS[-1]

# The most speech that may be put in a window, measured rather than derived.
# 10 s of speech in a 14 s window gives the same words as the full 30 s one -
# better, in fact, catching a final word the full window dropped. 11 s in the
# same window repeats the opening phrase at the end. So the ceiling is 10,
# and a longer segment is split by the caller rather than squeezed in here.
MAX_SPEECH_S = 10.0

# Held for the whole of any transcription, so the window one caller sets cannot
# be observed by another. Dictation and file transcription therefore take turns
# rather than corrupting each other's state.
BACKEND_LOCK = threading.RLock()


def headroom_for(speech_seconds: float) -> float:
    """How much empty window this much speech needs after it.

    Not decoration, and not a constant. Given a window barely longer than the
    speech the decoder has nowhere to put the end of it and runs on: 5 s of
    speech in a 6 s window produced "بسم الله بسم الله بس...", and 11 s in a
    14 s window repeated the opening phrase at the end. Three seconds was
    enough at 5 s of speech and not at 11, so the room grows with the content.
    """
    return max(HEADROOM_S, MIN_HEADROOM_FRACTION * speech_seconds)


def window_for(speech_seconds: float) -> int:
    """The smallest offered window this much speech fits in, with headroom."""
    needed = speech_seconds + headroom_for(speech_seconds)
    for window in WINDOWS:
        if needed <= window:
            return window
    return FULL_WINDOW_S


def warm_up(model) -> dict[int, float]:
    """Encode silence once per window size, so the first dictation is not slow.

    The maths library builds its kernels per tensor shape. Paid here, in the
    background after the model loads, rather than in the middle of the first
    thing the user says.
    """
    import numpy as np

    timings = {}
    for window in WINDOWS:
        started = time.time()
        try:
            with short_window(model, 0, exact=window):
                model.transcribe(np.zeros(16000, dtype=np.float32),
                                 language="en", task="transcribe", fp16=False,
                                 condition_on_previous_text=False)
        except Exception:
            continue                 # a failed warm-up costs speed, not function
        timings[window] = round(time.time() - started, 2)
    return timings


def _patch_encoder() -> None:
    """Let the encoder accept fewer frames than it was built for.

    Applied once, to the class, and harmless at the full width: slicing the
    positional embedding to the number of frames present returns the whole of
    it when there are 1500.
    """
    from whisper.model import AudioEncoder

    if getattr(AudioEncoder, "_sada_short_window", False):
        return

    import torch.nn.functional as F

    def forward(self, x):
        x = F.gelu(self.conv1(x))
        x = F.gelu(self.conv2(x))
        x = x.permute(0, 2, 1)
        x = (x + self.positional_embedding[: x.shape[1]]).to(x.dtype)
        for block in self.blocks:
            x = block(x)
        return self.ln_post(x)

    AudioEncoder.forward = forward
    AudioEncoder._sada_short_window = True


def _modules():
    """The three namespaces holding a copy of the window constants.

    `whisper.transcribe` is a function, not the module - whisper's __init__
    rebinds the name - so setting attributes on it silently does nothing. That
    mistake made an earlier measurement of this idea show no speed-up at all.
    """
    import whisper.audio
    import whisper.decoding

    return [whisper.audio, sys.modules["whisper.transcribe"], whisper.decoding]


@contextlib.contextmanager
def short_window(model, seconds: float, exact: int | None = None):
    """Encode in a window just long enough for `seconds` of speech.

    Yields the window length in seconds. Does nothing, safely, for a backend
    that is not openai-whisper. `exact` overrides the choice, and exists so
    the warm-up can reach each window directly: deriving one from a speech
    length silently warmed the wrong shapes, because the headroom rule maps
    11 seconds of speech to the full window rather than to the 14-second one.
    """
    with BACKEND_LOCK:
        try:
            _patch_encoder()
            mods = _modules()
        except Exception:
            yield MAX_WINDOW_S                  # not openai-whisper; carry on
            return

        window = exact if exact else window_for(seconds)
        if window >= FULL_WINDOW_S:
            yield FULL_WINDOW_S
            return

        n_samples = window * 16000
        n_frames = n_samples // HOP_LENGTH
        wanted = {"CHUNK_LENGTH": window, "N_SAMPLES": n_samples,
                  "N_FRAMES": n_frames}

        saved = [(m, {k: getattr(m, k) for k in wanted if hasattr(m, k)})
                 for m in mods]
        dims = getattr(model, "dims", None)
        saved_ctx = getattr(dims, "n_audio_ctx", None)
        try:
            for m in mods:
                for k, v in wanted.items():
                    if hasattr(m, k):
                        setattr(m, k, v)
            if saved_ctx is not None:
                # decoding.py asserts the encoder output against this, and
                # derives timestamp precision from it, so it has to agree.
                dims.n_audio_ctx = n_frames // 2
            yield window
        finally:
            for m, values in saved:
                for k, v in values.items():
                    setattr(m, k, v)
            if saved_ctx is not None:
                dims.n_audio_ctx = saved_ctx
