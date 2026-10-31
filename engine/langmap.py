"""Finding where one language stops and the next begins, inside one segment.

Whisper decides a language once per call and then writes the whole call in it.
Hand it a sentence that starts in Arabic and ends in English and it does not
switch - it transliterates, so "the quarterly report" comes back spelled in
Arabic letters. That is not a bug to tune around; it is what the parameter
means.

So the segment is split before it is transcribed: Silero marks the pauses, a
language-identification model says what each phrase is in, consecutive phrases
that agree are joined into runs, and each run is transcribed separately with
its own language. Boundaries land in the pauses, never inside a phrase, since
cutting mid-word to change language loses the word in both halves.

NOTHING CALLS THIS WITH A MODEL ANY MORE. The one that was cheap enough to ask
per phrase was `tiny`, and it was removed from the program at the user's
request. `split()` still takes the model it needs, returns a single run when
given None, and is kept whole because the Google engine does this job properly
and because a local identification model may come back.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 16000

# A phrase shorter than this is not asked about: a small model guesses badly
# on a syllable, and a wrong answer here costs a whole run in the wrong script.
MIN_PHRASE_S = 0.6
# Below this the answer is treated as "no idea" and the phrase joins its
# neighbour rather than starting a run of its own.
CONFIDENCE_MIN = 0.55
# A run shorter than this is not worth a call: every call pays for a whole
# window, so a one-second run costs what an eight-second one does.
MIN_RUN_S = 1.2
# However mixed the speech, this many calls per segment is the ceiling.
MAX_RUNS = 4


@dataclass
class Run:
    start: int                  # sample offsets into the segment
    end: int
    language: str | None

    @property
    def seconds(self) -> float:
        return (self.end - self.start) / SAMPLE_RATE


def phrases(audio: np.ndarray) -> list[tuple[int, int]]:
    """Speech spans with the pauses between them, from Silero."""
    try:
        from engine.chunking import speech_regions

        found = speech_regions(audio)
    except Exception:
        found = []
    return [(int(r["start"]), int(r["end"])) for r in found]


def _detect(lid, clip: np.ndarray) -> tuple[str | None, float]:
    try:
        language, confidence, _ = lid.detect_language(audio=clip)
        return language, float(confidence)
    except Exception:
        return None, 0.0


def _merge_short(runs: list[Run]) -> list[Run]:
    """Fold runs too short to be worth their own call into a neighbour.

    Into the longer side, so the language that dominates the sentence is the
    one that absorbs the fragment.
    """
    while len(runs) > 1:
        shortest = min(range(len(runs)), key=lambda i: runs[i].seconds)
        if runs[shortest].seconds >= MIN_RUN_S and len(runs) <= MAX_RUNS:
            break
        before = runs[shortest - 1] if shortest > 0 else None
        after = runs[shortest + 1] if shortest + 1 < len(runs) else None
        if before is None:
            target, index = after, shortest + 1
        elif after is None:
            target, index = before, shortest - 1
        else:
            target, index = ((before, shortest - 1)
                             if before.seconds >= after.seconds
                             else (after, shortest + 1))
        target.start = min(target.start, runs[shortest].start)
        target.end = max(target.end, runs[shortest].end)
        runs.pop(shortest)
    return runs


def split(audio: np.ndarray, lid, fallback: str | None = None) -> list[Run]:
    """The segment, divided into runs of one language each.

    `lid` is the language-detection backend. Returns a single run covering
    everything when the speech is all one language, which is the usual case
    and costs exactly what it did before.
    """
    whole = Run(0, len(audio), fallback)
    if lid is None or len(audio) < int(MIN_PHRASE_S * SAMPLE_RATE):
        return [whole]

    spans = phrases(audio)
    if len(spans) < 2:
        language, confidence = _detect(lid, audio)
        return [Run(0, len(audio),
                    language if confidence >= CONFIDENCE_MIN else fallback)]

    answers: list[tuple[int, int, str | None]] = []
    for start, end in spans:
        if (end - start) < int(MIN_PHRASE_S * SAMPLE_RATE):
            answers.append((start, end, None))
            continue
        language, confidence = _detect(lid, audio[start:end])
        answers.append((start, end,
                        language if confidence >= CONFIDENCE_MIN else None))

    # A phrase nobody could identify takes the language around it rather than
    # breaking a run in half.
    known = [a[2] for a in answers if a[2]]
    if not known:
        return [whole]
    filled = []
    last = known[0]
    for start, end, language in answers:
        last = language or last
        filled.append((start, end, last))

    runs: list[Run] = []
    for start, end, language in filled:
        if runs and runs[-1].language == language:
            runs[-1].end = end
        else:
            runs.append(Run(start, end, language))

    # Stretch to cover the gaps as well as the speech: a boundary is placed at
    # the start of the next phrase, so no audio is dropped between runs.
    runs[0].start = 0
    runs[-1].end = len(audio)
    for before, after in zip(runs, runs[1:]):
        before.end = after.start

    return _merge_short(runs)


def majority(runs: list[Run]) -> str:
    """The language most of the audio was in."""
    totals: dict[str, int] = {}
    for run in runs:
        if run.language:
            totals[run.language] = totals.get(run.language, 0) + (run.end - run.start)
    return max(totals, key=lambda k: totals[k]) if totals else ""
