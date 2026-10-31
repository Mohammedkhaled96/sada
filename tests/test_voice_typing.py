"""Voice typing: the emoji generator, the cleanup, and the recording guard.

The transcription itself is a job, and jobs are covered by tests/test_jobs.py.
What is left here is what dictation adds on top: turning spoken emoji names
into emoji, tidying what the model wrote without changing what it says, and
refusing to queue a recording with nothing in it.

The emoji and filler checks are written the other way round from most tests:
more than half assert what is *kept*. The previous attempt at this removed the
word راوي from somebody's dictation and nothing failed.

Run:  PYTHONUTF8=1 .venv/Scripts/python.exe tests/test_voice_typing.py
"""
from __future__ import annotations

import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, dictate
from engine import cleanup, emoji, fillers

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))
    if not ok:
        failures.append(label)


# ------------------------------------------------------------------- emoji
print("--- emoji asked for by name ---")

for text, expected, why in [
    ("Hello, how are you? Smile face emoji.", "Hello, how are you? 🙂.",
     "the emoji, not the words naming it"),
    ("thanks a lot thumbs up emoji", "thanks a lot 👍", "English, marker last"),
    ("شكرا جزيلا ايموجي قلب", "شكرا جزيلا ❤️", "Arabic, marker first"),
    ("علامة صح ايموجي", "✅", "Arabic, marker last"),
    ("emoji of a heart", "❤️", "with the little words between"),
    ("crying laughing emoji", "😂", "the longer name beats 'crying'"),
]:
    check(f"{why}: {text}", emoji.apply(text) == expected,
          f"got {emoji.apply(text)!r}")

print("\n--- and how many of them ---")

for text, expected in [
    ("put 3 smile face emoji", "put 🙂🙂🙂"),
    ("three smile face emoji", "🙂🙂🙂"),
    ("خمس ايموجي ابتسامة", "🙂🙂🙂🙂🙂"),
    ("حط ٣ ايموجي قلب", "حط ❤️❤️❤️"),
    ("اتنين ايموجي نار", "🔥🔥"),
    ("a heart emoji", "❤️"),
]:
    check(f"counted: {text}", emoji.apply(text) == expected,
          f"got {emoji.apply(text)!r}")

# A misheard number must cost a wrong count, not a document full of emoji.
many = emoji.apply("put 10 fire emoji")
check("the count is capped", many.count("🔥") <= emoji.MAX_COUNT,
      f"{many.count('🔥')} of them")

print("\n--- and what is left alone ---")

for text, why in [
    ("my heart is fine", "no marker word, so no replacement"),
    ("قلبي تعبان", "and none in Arabic either"),
    ("I have 3 meetings today", "a number with no emoji named"),
    ("راوي القصة حضر", "اوي three times inside a real word"),
    ("the box is 5 mm wide", "mm is a unit, not a hesitation"),
    ("err on the side of caution", "err is a verb"),
    ("يعني إيه الكلام ده", "يعني carries meaning"),
]:
    check(f"kept: {text}",
          fillers.strip_fillers(emoji.apply(text)) == text, why)

# A name no phrasing can reach is a dead entry nothing else would notice.
unreachable = [name for glyph, names in emoji.NAMES.items() for name in names
               if emoji.apply(f"{name} emoji") != glyph]
check("every name in the table can be said", not unreachable,
      ", ".join(unreachable[:5]) or
      f"{sum(len(v) for v in emoji.NAMES.values())} names, {len(emoji.NAMES)} emoji")


# ----------------------------------------------------------------- cleanup
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
    check(f"{why}: {text}", cleanup.tidy(text) == expected,
          f"got {cleanup.tidy(text)!r}")

check("emoji run as part of the pipeline, not separately",
      cleanup.tidy("ok thumbs up emoji") == "ok 👍",
      cleanup.tidy("ok thumbs up emoji"))

print("\n--- replacements the user wrote ---")

pairs = cleanup.parse_corrections(
    "سدى = صدى\n# a comment\nعبد = Abd\nعبد الله = Abdullah")
check("comments and blank lines are ignored", len(pairs) == 3, str(len(pairs)))
check("the longest match wins, not the first",
      cleanup.apply_corrections("مع عبد الله", pairs) == "مع Abdullah",
      cleanup.apply_corrections("مع عبد الله", pairs))
check("a replacement cannot eat the inside of a word",
      cleanup.apply_corrections("سداسي", pairs) == "سداسي")


# ------------------------------------------------------ the recording guard
print("\n--- what is worth queueing ---")

check("silence is recognised as silence",
      float(np.abs(np.zeros(16000, dtype=np.float32)).max()) < dictate.SILENT_PEAK)
check("and room tone is not",
      float(np.abs(np.full(16000, 0.01, dtype=np.float32)).max())
      >= dictate.SILENT_PEAK)
check("a key press is shorter than a sentence",
      dictate.MIN_LENGTH_S <= 1.0, f"{dictate.MIN_LENGTH_S}s")

with tempfile.TemporaryDirectory() as td:
    tone = (0.2 * np.sin(np.linspace(0, 400, 16000))).astype(np.float32)
    path = dictate.write_wav(tone, Path(td) / "x.wav")
    with wave.open(str(path), "rb") as w:
        check("the recording is written as 16 kHz mono 16-bit",
              (w.getframerate(), w.getnchannels(), w.getsampwidth())
              == (16000, 1, 2),
              f"{w.getframerate()} Hz, {w.getnchannels()} ch, "
              f"{w.getsampwidth() * 8} bit")
        check("and holds every sample", w.getnframes() == len(tone))

# A dictation is submitted the same way a picked file is. If these ever stop
# matching, dictation has grown its own pipeline again - which is the thing
# this design exists to prevent.
source = (ROOT / "app" / "dictate.py").read_text(encoding="utf-8")
check("a dictation is queued as a job", "jobs.submit(" in source)
check("and the progress window is the one transcribing already uses",
      "window.show_without_focus()" in source and "refresh_jobs()" in source)
check("dictation does not transcribe anything itself",
      "stream_segments" not in source and "transcribe(" not in source)


# ------------------------------------------------- more than one language
print("\n--- a dictation that changes language ---")

from engine.chunking import SAMPLE_RATE, plan_chunks, speech_regions  # noqa: E402

sys.path.insert(0, str(ROOT / "tests"))
import speechgen                                                   # noqa: E402

english = speechgen.pick("david", "zira", "english")
arabic = speechgen.pick("arabic", "leila")

if english is None or arabic is None:
    print(f"  (needs an English and an Arabic voice; this machine has "
          f"{len(speechgen.voices())} - skipped)")
else:
    mixed = speechgen.conversation([
        ("السلام عليكم، التقرير جاهز للمراجعة.", arabic),
        ("The quarterly report is ready for review.", english),
        ("Buenos dias, el informe esta listo para la revision.", english),
    ], gap=1.0)

    # Whisper decides one language per call and writes the whole call in that
    # script, so the chunk is the unit a speaker can change language in. Given
    # all three sentences in one call it detected Arabic, wrote the Arabic and
    # stopped - ten seconds of speech lost. Measured, on this recording.
    chunks = plan_chunks(mixed)
    regions = speech_regions(mixed)
    check("a short recording is chunked by sentence, not in one piece",
          len(chunks) >= 3,
          ", ".join(f"{a / SAMPLE_RATE:.1f}-{b / SAMPLE_RATE:.1f}s"
                    for a, b in chunks))
    check("there is a chunk boundary for every pause",
          len(chunks) >= len(regions) - 1,
          f"{len(regions)} regions, {len(chunks)} chunks")
    check("and the chunks cover the whole recording",
          chunks[0][0] <= regions[0]["start"] and chunks[-1][1] >= regions[-1]["end"])

    # A long recording keeps long chunks: an hour of audio must not become
    # 1,400 calls because dictation wanted sentence-sized ones.
    long_audio = np.tile(mixed, 20)[: 20 * 60 * SAMPLE_RATE]
    per_minute = len(plan_chunks(long_audio)) / (len(long_audio) / SAMPLE_RATE / 60)
    check("but a long recording does not", per_minute < 10,
          f"{per_minute:.1f} chunks a minute")

    from engine.models import info                                 # noqa: E402

    if not info("turbo").installed:
        print("  (large-v3-turbo is not installed - transcription skipped)")
    else:
        from engine.backends import load_backend                   # noqa: E402
        from engine.device import pick_device                      # noqa: E402
        from engine.job import DECODE_OPTS                         # noqa: E402

        backend = load_backend("openai", "turbo", pick_device("cpu"))
        rows = list(backend.stream_segments(mixed, None, dict(DECODE_OPTS)))
        text = " ".join(r["text"] for r in rows)

        def arabic_letters(s):
            return sum("؀" <= c <= "ۿ" for c in s)

        check("every sentence survives, not just the first",
              len(rows) >= 3, f"{len(rows)} segments")
        check("the Arabic is in Arabic letters", arabic_letters(text) > 10,
              f"{arabic_letters(text)} of them")
        check("the English is not transliterated into them",
              "report" in text.lower(), text[:60])
        check("and neither is the Spanish",
              "informe" in text.lower(), text[:60])
        for row in rows:
            print(f"        [{row['language']}] {row['text'].strip()[:58]}")


# ------------------------------------------------------------- local only
print("\n--- nothing leaves the machine ---")

reaching_out = []
for folder in ("engine", "app"):
    for module in sorted((ROOT / folder).glob("*.py")):
        text = module.read_text(encoding="utf-8")
        found = [w for w in ("generativelanguage", "api_key", "gemini",
                             "openai.com") if w in text]
        if found:
            reaching_out.append(f"{module.name}: {', '.join(found)}")
check("no module reaches an outside service", not reaching_out,
      "; ".join(reaching_out))
check("and no setting could turn one on",
      not any(f.startswith("dictation_ai") for f in vars(config.Settings())),
      ", ".join(f for f in vars(config.Settings()) if "dictation" in f))


print("\n" + "=" * 60)
if failures:
    print(f"FAILED: {len(failures)}")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("all voice typing checks passed")
print("=" * 60)
