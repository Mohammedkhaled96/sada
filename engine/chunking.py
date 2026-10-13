"""Split audio into chunks on natural pauses.

Needed for two reasons:

* **Real progress.** openai-whisper's `transcribe()` blocks until the whole file
  is done and only then returns every segment, so a three-hour file produced one
  progress report saying "0 percent, 0.0x real time, less than a second
  remaining" and then nothing. Transcribing chunk by chunk makes progress, speed
  and the estimate genuinely measurable instead of guessed.
* **Resume.** Having our own chunks means we can skip the ones already done,
  which is what `clip_timestamps` would have given us on a backend that
  supported it.

Boundaries land in silence found by Silero VAD, never at a fixed interval, so a
word is not cut in half. If VAD finds no speech at all — a music-only file, or
something it misreads — we fall back to fixed windows rather than returning
nothing.
"""
from __future__ import annotations

import numpy as np

SAMPLE_RATE = 16000


def speech_regions(audio: np.ndarray, sampling_rate: int = SAMPLE_RATE) -> list[dict]:
    """Silero VAD speech spans as [{'start': samples, 'end': samples}, ...]."""
    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        return get_speech_timestamps(
            audio,
            VadOptions(
                threshold=0.5,
                min_speech_duration_ms=250,
                max_speech_duration_s=30,
                min_silence_duration_ms=500,
                speech_pad_ms=200,
            ),
            sampling_rate=sampling_rate,
        )
    except Exception:
        return []


def plan_chunks(audio: np.ndarray, target_s: float = 90.0, max_s: float = 150.0,
                sampling_rate: int = SAMPLE_RATE) -> list[tuple[int, int]]:
    """Return (start_sample, end_sample) pairs covering the speech in `audio`.

    Chunks grow until `target_s`, then close at the next pause. A single
    uninterrupted stretch longer than `max_s` is split anyway, because one
    enormous chunk would stall progress reporting for minutes at a time.
    """
    total = len(audio)
    if total == 0:
        return []

    target = int(target_s * sampling_rate)
    hard_max = int(max_s * sampling_rate)
    regions = speech_regions(audio, sampling_rate)

    if not regions:
        # No detectable speech: cover the file in fixed windows so the caller
        # still gets a complete transcript attempt and usable progress.
        return [(s, min(s + target, total)) for s in range(0, total, target)]

    chunks: list[tuple[int, int]] = []
    start = regions[0]["start"]
    end = regions[0]["end"]

    for region in regions[1:]:
        candidate = region["end"] - start
        if candidate > hard_max or (end - start) >= target:
            chunks.append((start, end))
            start, end = region["start"], region["end"]
        else:
            end = region["end"]
    chunks.append((start, end))

    # Widen each chunk slightly into the surrounding silence: Whisper needs a
    # little lead-in to settle, and a hard cut on the first phoneme costs words.
    pad = int(0.2 * sampling_rate)
    padded = []
    for i, (s, e) in enumerate(chunks):
        s2 = max(0, s - pad) if i == 0 else max(chunks[i - 1][1], s - pad)
        e2 = min(total, e + pad)
        if e2 > s2:
            padded.append((s2, e2))
    return padded
