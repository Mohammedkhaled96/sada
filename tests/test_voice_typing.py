"""Voice typing on this machine: the microphone, the model, and real speech.

The text pipeline moved to tests/test_text.py, which needs nothing but numpy
and therefore runs in CI. What is left here is everything that cannot: the
recording guard, a real transcription through the real model, and the checks
that read the source to be sure dictation still goes through the job queue
rather than growing a second pipeline of its own.

Speech is generated through SAPI, so an Arabic and an English voice have to be
installed; the sections that need them say so and skip if they are missing.

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

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))
    if not ok:
        failures.append(label)


# ------------------------------------------------------ the recording guard
print("--- what is worth queueing ---")

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


# ------------------------------------------------- one pipeline, not two
# A dictation is submitted the same way a picked file is. If these ever stop
# matching, dictation has grown its own pipeline again - which is the thing
# this design exists to prevent.
print("\n--- a dictation is a job like any other ---")

source = (ROOT / "app" / "dictate.py").read_text(encoding="utf-8")
check("a dictation is queued as a job", "jobs.submit(" in source)
check("and the progress window is the one transcribing already uses",
      "window.show_without_focus()" in source and "refresh_jobs()" in source)
check("dictation does not transcribe anything itself",
      "stream_segments" not in source and "transcribe(" not in source)
check("the corrections list is handed to the job",
      "corrections=s.dictation_corrections" in source)
check("whatever was playing is paused for the recording",
      "mediapause.pause_playing()" in source
      and "self._resume_media()" in source)

job_source = (ROOT / "engine" / "job.py").read_text(encoding="utf-8")
check("one language, voted over the whole recording, dictation included",
      "lang, conf, _votes = detect_language(" in job_source
      and "elif dictation:" not in job_source)
check("only a dictation gets the polish",
      "if dictation:" in job_source and "segments = polish(" in job_source)
check("and only a dictation asks for sentence chunks",
      'opts["chunk_target_s"] = SHORT_TARGET_S' in job_source)


# ------------------------------------------------------- real speech
print("\n--- a real recording, through the real model ---")

sys.path.insert(0, str(ROOT / "tests"))
import speechgen                                                    # noqa: E402

from engine.chunking import SAMPLE_RATE, SHORT_TARGET_S, plan_chunks  # noqa: E402

english = speechgen.pick("david", "zira", "english")
arabic = speechgen.pick("arabic", "leila")

if english is None or arabic is None:
    print(f"  (needs an English and an Arabic voice; this machine has "
          f"{len(speechgen.voices())} - skipped)")
else:
    spoken_ar = speechgen.conversation([
        ("السلام عليكم، التقرير جاهز للمراجعة.", arabic),
        ("وسوف نراجعه غدا صباحا إن شاء الله.", arabic),
    ], gap=1.0)
    spoken_en = speechgen.conversation([
        ("The quarterly report is ready for review.", english),
        ("We will go through it tomorrow morning.", english),
    ], gap=1.0)

    chunks = plan_chunks(spoken_ar, target_s=SHORT_TARGET_S)
    check("a dictation is chunked by sentence, for the short window",
          len(chunks) >= 2,
          ", ".join(f"{a / SAMPLE_RATE:.1f}-{b / SAMPLE_RATE:.1f}s"
                    for a, b in chunks))
    # And a file is never chunked that way, however short it is. Letting every
    # chunk of a file pick its own language ended an hour of Arabic with
    # "Wassalamualaikum warahmatullahi wabarakatuh" in Latin letters.
    check("a file is not chunked by sentence, whatever its length",
          len(plan_chunks(spoken_ar)) == 1,
          f"{len(plan_chunks(spoken_ar))} chunks for "
          f"{len(spoken_ar) / SAMPLE_RATE:.0f}s")
    long_audio = np.tile(spoken_ar, 20)[: 20 * 60 * SAMPLE_RATE]
    per_minute = len(plan_chunks(long_audio)) / (len(long_audio) / SAMPLE_RATE / 60)
    check("and a long one is chunked as it always was", per_minute < 10,
          f"{per_minute:.1f} chunks a minute")

    from engine.models import info                                  # noqa: E402

    if not info("turbo").installed:
        print("  (large-v3-turbo is not installed - transcription skipped)")
    else:
        from engine.backends import load_backend                   # noqa: E402
        from engine.device import pick_device                      # noqa: E402
        from engine.job import run_transcription                   # noqa: E402

        backend = load_backend("openai", "turbo", pick_device("cpu"))

        def transcribe(audio, name):
            with tempfile.TemporaryDirectory() as td:
                wav = dictate.write_wav(audio, Path(td) / f"{name}.wav")
                return run_transcription(wav, backend, Path(td) / "out",
                                         dictation=True)

        def arabic_letters(s):
            return sum("؀" <= c <= "ۿ" for c in s)

        meta = transcribe(spoken_ar, "ar")
        print(f"        [{meta['language']}] {meta['text'][:70]}")
        check("the Arabic comes back in Arabic letters",
              arabic_letters(meta["text"]) > 10,
              f"{arabic_letters(meta['text'])} of them")
        check("detected as one language for the whole recording",
              meta["language"] == "ar", meta["language"])
        check("and faces right to left", meta["rtl"] is True, str(meta["rtl"]))

        meta = transcribe(spoken_en, "en")
        print(f"        [{meta['language']}] {meta['text'][:70]}")
        check("the English comes back in Latin letters",
              "report" in meta["text"].lower(), meta["text"][:60])
        check("detected as English", meta["language"] == "en", meta["language"])
        check("and faces left to right", meta["rtl"] is False, str(meta["rtl"]))


# ------------------------------------------------------------- local only
print("\n--- nothing leaves the machine ---")

reaching_out = []
for folder in ("engine", "app"):
    for module in sorted((ROOT / folder).glob("*.py")):
        text = module.read_text(encoding="utf-8")
        found = [w for w in ("generativelanguage", "api_key", "gemini",
                             "openai.com", "api.groq.com") if w in text]
        if found:
            reaching_out.append(f"{module.name}: {', '.join(found)}")
check("no module reaches an outside service", not reaching_out,
      "; ".join(reaching_out))
check("and no setting could turn one on",
      not any(f.startswith("dictation_ai") or f.endswith("_key")
              for f in vars(config.Settings())),
      ", ".join(f for f in vars(config.Settings()) if "dictation" in f))


print("\n" + "=" * 60)
if failures:
    print(f"FAILED: {len(failures)}")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("all voice typing checks passed")
print("=" * 60)
