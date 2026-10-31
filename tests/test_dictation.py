"""Dictation: the segmenter, the filler list, and one real end-to-end run.

The pipeline is fed audio and reports state, so none of this needs a
microphone or a window on screen. The parts that need no model run first and
fast; the end-to-end run at the bottom is skipped when large-v3-turbo is not
installed.

The filler checks are written the other way round from most tests: more than
half of them assert what is *kept*. The previous attempt at this feature
removed the word راوي from somebody's dictation, because `[اوي]{3,}` matched
it, and nothing failed.

Run:  PYTHONUTF8=1 .venv/Scripts/python.exe tests/test_dictation.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import cleanup, emoji, fillers
from engine.dictation import (MAX_SEGMENT_S, MIN_SEGMENT_S, PAUSE_S, Dictation,
                              Segmenter, transcribe_stream)

SR = 16000
failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))
    if not ok:
        failures.append(label)


# ------------------------------------------------------------------ fillers
print("--- words that must survive ---")

# Each of these contains something a pattern-based filter would have eaten.
for text, why in [
    ("راوي القصة حضر", "اوي repeated three times inside a real word"),
    ("the box is 5 mm wide", "mm is a unit here, not a hesitation"),
    ("err on the side of caution", "err is a verb"),
    ("هو أمّن على كلامه", "أمن is a word, أمم is a sound"),
    ("I like it", "like is a filler in speech and a verb here"),
    ("يعني إيه الكلام ده", "يعني carries meaning and is left alone"),
    ("ummah", "a word that merely starts like one"),
]:
    check(f"kept: {text}", fillers.strip_fillers(text) == text, why)

print("\n--- sounds that must go ---")
for text, expected in [
    ("امم تمام", "تمام"),
    ("أمم، تمام يا باشا", "تمام يا باشا"),
    ("um, I think so", "I think so"),
    ("uh the meeting is at four", "the meeting is at four"),
    ("هممم ممم خلاص", "خلاص"),
]:
    got = fillers.strip_fillers(text)
    check(f"removed from: {text}", got == expected, f"got {got!r}")

print("\n--- repetition the decoder produced, not the speaker ---")
for text, expected, why in [
    ("the the the meeting is at four", "the meeting is at four",
     "three in a row is the decoder"),
    ("بسم الله بسم الله بسم الله خلاص", "بسم الله خلاص",
     "a repeated phrase is the decoder losing its place"),
    ("very very good", "very very good", "twice is something people say"),
    ("لا لا مش كده", "لا لا مش كده", "and in Arabic too"),
    ("نعم ، تمام .  خلاص", "نعم، تمام. خلاص", "spacing around punctuation"),
]:
    got = cleanup.tidy(text)
    check(f"{why}: {text}", got == expected, f"got {got!r}")

print("\n--- replacements the user wrote ---")
pairs = cleanup.parse_corrections("سدى = صدى\n# a comment\nwisper = Whisper\n"
                                  "عبد = Abd\nعبد الله = Abdullah")
check("comments and blank lines are ignored", len(pairs) == 4, str(len(pairs)))
check("the longest match wins, not the first",
      cleanup.apply_corrections("مع عبد الله", pairs) == "مع Abdullah",
      cleanup.apply_corrections("مع عبد الله", pairs))
check("a replacement cannot eat the inside of a word",
      cleanup.apply_corrections("سداسي", pairs) == "سداسي",
      cleanup.apply_corrections("سداسي", pairs))
check("and nothing happens without a list",
      cleanup.apply_corrections("سدى", []) == "سدى")

check("a segment of nothing but hesitation is recognised",
      fillers.is_all_filler("أمم ممم") and fillers.is_all_filler("um uh"))
check("and a segment with one real word is not",
      not fillers.is_all_filler("أمم تمام"))
check("an empty string is not all filler", not fillers.is_all_filler(""))


# ---------------------------------------------------------------- segmenter
print("\n--- cutting at pauses ---")


class FakeVAD(Segmenter):
    """The segmenter with the voice decision replaced.

    Silero is not being tested here - the cutting rules are. Feeding it a tone
    would test whether Silero thinks a sine wave is a person, which is not a
    question this module is responsible for.
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.voiced = True

    def has_voice(self, block):
        if self.voiced:
            self.heard_anything = True
        return self.voiced


def block(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SR), dtype=np.float32)


seg = FakeVAD()
cuts = []
for _ in range(int(MIN_SEGMENT_S * 10) - 5):        # just under the minimum
    cuts += seg.feed(block(0.1))
check("a short burst of speech is not cut on its own", cuts == [],
      f"{seg.pending_speech_s:.1f}s pending")

seg.voiced = False
for _ in range(int(PAUSE_S * 10) + 2):
    cuts += seg.feed(block(0.1))
check("and a pause after it does not cut either", cuts == [],
      "ten short phrases cost nine times what one call costs")

seg = FakeVAD()
spoken_blocks = int(MIN_SEGMENT_S * 10) + 5
for _ in range(spoken_blocks):
    cuts += seg.feed(block(0.1))
spoken_s = spoken_blocks * 0.1
seg.voiced = False
paused_blocks = int(PAUSE_S * 10) + 2
closed = []
for _ in range(paused_blocks):
    closed += seg.feed(block(0.1))
check("enough speech, then a pause, is cut", len(closed) == 1,
      f"{len(closed)} segments")
if closed:
    length = len(closed[0]) / SR
    fed = spoken_s + paused_blocks * 0.1
    # All of the speech, and a little of the pause - not all of it. Every
    # second of silence inside a segment is a second of window spent on
    # nothing, and the window is the whole cost.
    check("the speech is all there",
          length >= spoken_s - 0.05, f"{length:.1f}s of {spoken_s:.1f}s spoken")
    check("and the silence after it is trimmed off",
          length < fed - 0.2, f"{length:.1f}s kept of {fed:.1f}s fed")

seg = FakeVAD()
closed = []
for _ in range(int(MAX_SEGMENT_S * 10) + 2):        # never pauses at all
    closed += seg.feed(block(0.1))
check("someone who never pauses is still cut", len(closed) == 1,
      f"{len(closed)} segments")

# Every segment has to fit a short window. One second over and it falls back
# to the full 30-second window, which costs about eighteen seconds instead of
# seven - and the pipeline stops keeping up with the person talking.
from engine import window as win                                    # noqa: E402

seg = FakeVAD()
closed = []
for phase, secs in (("v", 4), ("s", 0.5), ("v", 4), ("s", 0.5), ("v", 4)):
    seg.voiced = phase == "v"
    for _ in range(int(secs * 10)):
        closed += seg.feed(block(0.1))
closed += [c for c in [seg.flush()] if c is not None]
check("pauses too short to cut on still count towards the length",
      all(len(c) / SR <= win.MAX_SPEECH_S + 0.2 for c in closed),
      ", ".join(f"{len(c) / SR:.1f}s" for c in closed))
check("so every segment fits a short window",
      all(win.window_for(len(c) / SR) < win.FULL_WINDOW_S for c in closed),
      ", ".join(f"{win.window_for(len(c) / SR)}s" for c in closed))

seg = FakeVAD()
seg.voiced = False
closed = []
for _ in range(100):                                 # ten seconds of nothing
    closed += seg.feed(block(0.1))
check("silence alone never becomes a segment", closed == [])
check("and is not kept in memory",
      seg._buf.size <= int(0.4 * SR), f"{seg._buf.size / SR:.2f}s buffered")
check("nothing was heard, and it knows that", not seg.heard_anything)
check("flushing silence produces nothing", seg.flush() is None)


# -------------------------------------------------------------- end to end
print("\n--- a real dictation, fed from a file ---")

audio_file = ROOT / "audio" / "test.mp3"
try:
    from engine.models import info

    have_model = info("turbo").installed and audio_file.exists()
except Exception:
    have_model = False

if not have_model:
    print("  (large-v3-turbo or audio/test.mp3 missing - skipped)")
else:
    from engine.audio import decode_audio
    from engine.backends import load_backend
    from engine.device import pick_device
    from engine.window import warm_up

    audio = decode_audio(audio_file)[: 20 * SR].astype(np.float32)
    backend = load_backend("openai", "turbo", pick_device("cpu"))
    warm_up(backend.model)

    # No separate language-identification model: it was removed from the
    # program, so the large model answers that question once per dictation,
    # in a short window.
    states: list[str] = []
    dictation = Dictation(backend,
                          on_state=lambda name, **kw: states.append(name))

    started = time.time()
    result = transcribe_stream(dictation, audio)
    wall = time.time() - started

    check("it produced text", bool(result.text), result.text[:60])
    check("it knows it heard something", result.heard_anything)
    check("it found the language", result.language == "ar", result.language)
    check("it split the audio rather than sending one long call",
          result.segments >= 2, f"{result.segments} segments")
    check("it reported listening, then working, then done",
          states[0] == "listening" and "working" in states and states[-1] == "done",
          ", ".join(dict.fromkeys(states)))

    # The whole point: compute has to run faster than speech, or the queue
    # grows for as long as the user keeps talking and stop is never the end.
    check("it keeps up with speech",
          result.compute_seconds < result.audio_seconds,
          f"{result.compute_seconds:.1f}s of work for "
          f"{result.audio_seconds:.1f}s of audio")
    print(f"        fed in {wall:.1f}s wall, text: {result.text[:70]}")


print("\n--- a dictation that changes language ---")

if not have_model:
    print("  (large-v3-turbo missing - skipped)")
else:
    sys.path.insert(0, str(ROOT / "tests"))
    import speechgen

    from engine import langmap

    english = speechgen.pick("david", "zira", "english")
    arabic = speechgen.pick("arabic", "leila")
    if english is None or arabic is None:
        print(f"  (needs an English and an Arabic voice; this machine has "
              f"{len(speechgen.voices())} - skipped)")
    else:
        # No language-identification model any more: it was removed from the
        # program, so the local engine writes a whole dictation in one
        # language. The splitting below is still exercised, with the large
        # model answering, because the Google engine is the supported way to
        # mix languages and a local one may come back.
        lid = backend
        mixed = speechgen.conversation([
            ("السلام عليكم، التقرير جاهز.", arabic),
            ("The quarterly report is ready for review.", english),
            ("شكرا جزيلا لك.", arabic),
        ])
        text = transcribe_stream(Dictation(backend, lid_backend=lid), mixed).text

        def in_arabic(s):
            return sum("؀" <= c <= "ۿ" for c in s)

        def in_latin(s):
            return sum(c.isascii() and c.isalpha() for c in s)

        check("both scripts come back", in_arabic(text) > 10 and in_latin(text) > 10,
              f"{in_arabic(text)} Arabic letters, {in_latin(text)} Latin")
        # The failure this replaces: `language` is not a hint. Whisper decides
        # once per call and writes the whole call in that script, so the
        # English used to come back spelled in Arabic letters.
        check("the English was not transliterated",
              "report" in text.lower() or "quarterly" in text.lower(), text)
        check("the Arabic was not transliterated either",
              "عليكم" in text or "شكرا" in text, text)
        check("and they are in the order they were said",
              "eport" in text and "عليكم" in text
              and text.index("eport") > text.index("عليكم"), text)
        print(f"        {text}")

        if lid is not None:
            runs = langmap.split(mixed, lid)
            check("the language boundaries were found", len(runs) >= 2,
                  ", ".join(f"{r.language}:{r.seconds:.1f}s" for r in runs))
            check("and they land in the pauses, not inside a phrase",
                  all(r.seconds >= langmap.MIN_RUN_S for r in runs),
                  ", ".join(f"{r.seconds:.1f}s" for r in runs))


print("\n" + "=" * 60)
if failures:
    print(f"FAILED: {len(failures)}")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("all dictation checks passed")
print("=" * 60)
