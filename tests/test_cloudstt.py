"""Dictating through Google: what is sent, and what is refused.

Same balance as tests/test_polish.py and for the same reason - this writes
into a document the user cannot see. The transport is injected, so none of
this needs a key or a connection.

Run:  PYTHONUTF8=1 .venv/Scripts/python.exe tests/test_cloudstt.py
"""
from __future__ import annotations

import base64
import io
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config
from engine import cloudstt
from engine.cloudstt import SAMPLE_RATE

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))
    if not ok:
        failures.append(label)


def replying(text: str):
    return lambda body: {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def speech(seconds: float) -> np.ndarray:
    rng = np.random.default_rng(1)
    return (rng.normal(0, 0.1, int(seconds * SAMPLE_RATE))).astype(np.float32)


SPOKEN = ("السلام عليكم، أنا هقابلك الساعة تمانية المغرب إن شاء الله "
          "ونراجع التقرير مع بعض")


# ------------------------------------------------------------- what is sent
print("--- what is sent ---")

sent = {}
engine = cloudstt.GeminiTranscriber(
    "key", transport=lambda body: sent.update(body) or
    {"candidates": [{"content": {"parts": [{"text": SPOKEN}]}}]})
engine.transcribe(speech(6), "ar")

parts = sent["contents"][0]["parts"]
instruction = sent["systemInstruction"]["parts"][0]["text"].lower()
check("the audio itself goes, as wav",
      parts[0]["inlineData"]["mimeType"] == "audio/wav")
check("and it really is a readable wav",
      wave.open(io.BytesIO(base64.b64decode(parts[0]["inlineData"]["data"]))
                ).getframerate() == SAMPLE_RATE)
check("mono and 16-bit, which is all a microphone gave us",
      (lambda w: w.getnchannels() == 1 and w.getsampwidth() == 2)(
          wave.open(io.BytesIO(base64.b64decode(parts[0]["inlineData"]["data"])))))
check("a known language goes as a hint, not as an order",
      "most likely" in parts[1]["text"], parts[1]["text"])
check("it is told to keep the dialect and the script",
      "dialect" in instruction and "transliterate" in instruction)
check("it is told to apply corrections said out loud", "no, make it" in instruction)
check("it is told about emoji", "emoji" in instruction)
check("it is told never to answer or describe", "never answer" in instruction)
check("it is told to return nothing when there is no speech",
      "return nothing" in instruction)
check("the temperature is zero: this is listening, not writing",
      sent["generationConfig"]["temperature"] == 0.0)

engine.transcribe(speech(6), None)
check("and no hint is sent when the language is unknown",
      len(sent["contents"][0]["parts"]) == 1)


# --------------------------------------------------------- what is accepted
print("\n--- what comes back ---")

engine = cloudstt.GeminiTranscriber("key", transport=replying(SPOKEN))
check("a plausible transcript is taken", engine.transcribe(speech(6), "ar") == SPOKEN)

# Some speech models answer in their own shape rather than as plain text. One
# of them cost an afternoon the first time.
odd = {"candidates": [{"content": {"parts": [
    {"audioTranscription": {"text": SPOKEN}}]}}]}
engine = cloudstt.GeminiTranscriber("key", transport=lambda body: odd)
check("and so is one in the other reply shape",
      engine.transcribe(speech(6), "ar") == SPOKEN)


print("\n--- and what is refused, so the local model writes it instead ---")

for label, reply, seconds in [
    ("a refusal", "I'm sorry, I cannot transcribe this audio.", 6),
    ("a description of the audio", "The audio contains a person speaking.", 6),
    ("an answer in Arabic", "لا أستطيع تحويل هذا المقطع.", 6),
    ("nothing at all", "   ", 6),
    ("most of the speech missing", "السلام", 30),
]:
    engine = cloudstt.GeminiTranscriber("key", transport=replying(reply))
    try:
        got = engine.transcribe(speech(seconds), "ar")
        check(f"refuses {label}", False, f"ACCEPTED {got!r}")
    except cloudstt.CloudError as exc:
        check(f"refuses {label}", True, str(exc))

engine = cloudstt.GeminiTranscriber("key", transport=replying(" ".join(["كلمة"] * 400)))
try:
    engine.transcribe(speech(5), "ar")
    check("refuses far more words than were said", False, "ACCEPTED IT")
except cloudstt.CloudError as exc:
    check("refuses far more words than were said", True, str(exc))


# ------------------------------------------------------------ what it is not
print("\n--- local stays the default ---")

check("a fresh install writes on this computer",
      config.Settings().dictation_ai == "local")
check("and the default engine sends nothing",
      not cloudstt.make("local").available)
check("no key means not available", not cloudstt.GeminiTranscriber("").available)
check("the three choices are local, repair and gemini",
      config.AI_PROVIDERS == ["local", "repair", "gemini"],
      str(config.AI_PROVIDERS))

# Whichever one heard it, the paragraph has to face the right way.
check("Arabic text is marked right-to-left", cloudstt.script_of(SPOKEN) == "ar")
check("English text is not", cloudstt.script_of("the quarterly report") == "en")
check("and an emoji on its own decides nothing",
      cloudstt.script_of("👍") == "")


print("\n" + "=" * 60)
if failures:
    print(f"FAILED: {len(failures)}")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("all cloud dictation checks passed")
print("=" * 60)
