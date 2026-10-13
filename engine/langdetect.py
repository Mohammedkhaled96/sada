"""Language detection by voting across samples spread over the whole file.

Whisper's own detection looks at the opening 30 seconds, which misreads files
that start with music, silence or a short greeting in another language. We take
several windows spread across the audio, detect each one, and keep the language
with the highest summed confidence. The result is then locked for the whole
file so the language cannot flap mid-transcript.
"""
from __future__ import annotations

from collections import defaultdict

from engine.audio import SAMPLE_RATE

WINDOW_S = 30


def detect_language(model, audio, n_samples: int = 5, log=print):
    """audio: float32 mono numpy array at 16 kHz.
    Returns (language, confidence, votes) where votes maps language -> score."""
    duration = len(audio) / SAMPLE_RATE
    window = WINDOW_S * SAMPLE_RATE

    if duration <= WINDOW_S * 1.5:
        lang, prob, _ = model.detect_language(audio=audio, vad_filter=True)
        log(f"  language: {lang} ({prob:.0%}) [short file, single window]")
        return lang, prob, {lang: prob}

    # Skip the first and last 5% of the file: intros and outros are the worst
    # places to sample from.
    usable_start = int(len(audio) * 0.05)
    usable_end = max(usable_start + window, int(len(audio) * 0.95) - window)
    step = max(1, (usable_end - usable_start) // max(1, n_samples - 1))

    votes: dict[str, float] = defaultdict(float)
    taken = 0
    for i in range(n_samples):
        start = usable_start + i * step
        clip = audio[start:start + window]
        if len(clip) < SAMPLE_RATE * 5:        # under 5s of audio is not worth a vote
            continue
        try:
            lang, prob, _ = model.detect_language(audio=clip, vad_filter=True)
        except Exception as exc:                # a window that is pure silence
            log(f"  language sample @{start / SAMPLE_RATE / 60:.1f}min skipped: {exc}")
            continue
        votes[lang] += prob
        taken += 1
        log(f"  language sample @{start / SAMPLE_RATE / 60:5.1f}min -> {lang} ({prob:.0%})")

    if not votes:
        log("  language: detection failed on every window, falling back to the opening")
        lang, prob, _ = model.detect_language(audio=audio, vad_filter=True)
        return lang, prob, {lang: prob}

    best = max(votes, key=votes.get)
    confidence = votes[best] / max(1, taken)
    agreement = sum(1 for lg in votes if lg == best)
    log(f"  language LOCKED: {best} (confidence {confidence:.0%}, "
        f"{agreement}/{len(votes)} distinct languages seen across {taken} windows)")
    return best, confidence, dict(votes)
