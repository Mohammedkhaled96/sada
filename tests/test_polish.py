"""The guard on the language model, and the shape of what is sent.

Almost every check here is about refusing output rather than producing it. The
model writes into a document the user cannot see, so the question that matters
is not "does it repair well" - it is "what happens when it does something
else". The cost of wrongly keeping the original is that the user fixes a comma
themselves. The cost of wrongly accepting a rewrite is that a document now says
something they never said.

No network and no key: the transport is replaced.

Run:  PYTHONUTF8=1 .venv/Scripts/python.exe tests/test_polish.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config
from engine import polish

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))
    if not ok:
        failures.append(label)


def replying(text: str):
    """A transport that always answers with this text."""
    return lambda body: {"candidates": [{"content": {"parts": [{"text": text}]}}]}


SPOKEN = ("السلام عليكم ورحمة الله وبركاته انا هقابلك الساعة ستة ونص "
          "المغرب لا لا خليها ساعة تمنية")
REPAIRED = "السلام عليكم ورحمة الله وبركاته، أنا هقابلك الساعة تمانية المغرب."


# --------------------------------------------------------------- what it keeps
print("--- a repair is accepted ---")

p = polish.GeminiPolisher("key", transport=replying(REPAIRED))
result = p.polish(SPOKEN, "ar")
check("the correction the speaker spoke is applied", result.text == REPAIRED,
      result.text)
check("and it is reported as changed", result.changed)
check("with no complaint", result.reason == "", result.reason)

# The thing the user asked for, in their own words: say "emoji" and get one.
p = polish.GeminiPolisher("key", transport=replying("تمام خلاص 👍"))
result = p.polish("تمام خلاص ايموجي ايد بتعمل تمام", "ar")
check("an emoji asked for out loud is accepted", "👍" in result.text,
      result.text + f"  [{result.reason}]")
# An emoji is one character standing in for several spoken words, so a real
# repair shrinks the text. That must not look like text going missing - nor
# may emoji be a way to smuggle one past the guard.
ok, why = polish.acceptable("one two three four five six seven eight", "😀😀😀")
check("but emoji cannot excuse the rest of the text vanishing", not ok, why)


# ------------------------------------------------------------ what it refuses
print("\n--- and what it refuses ---")

cases = [
    ("an answer instead of a repair",
     "Sorry, I cannot help with that request.", SPOKEN),
    ("a translation",
     "Peace be upon you, I will meet you at eight in the evening.", SPOKEN),
    ("an essay built out of the dictation",
     REPAIRED + " " + ("وهذا يعني أن الاجتماع سيكون في وقت متأخر من المساء "
                       "وهو ما يناسب الجميع ويسمح بالتحضير الكافي للموضوعات "
                       "المطروحة على جدول الأعمال وكذلك مراجعة التقارير "),
     SPOKEN),
    ("most of the text going missing", "الساعة تمانية", SPOKEN),
    ("an empty reply", "   ", SPOKEN),
]
for label, reply, original in cases:
    p = polish.GeminiPolisher("key", transport=replying(reply))
    result = p.polish(original, "ar")
    check(f"refuses {label}", result.text == original and not result.changed,
          f"reason: {result.reason or 'ACCEPTED IT'}")

# Transliteration is the specific failure this program has hit before, with
# Whisper rather than with an LLM: the words are right and the script is wrong.
# Long enough that the length check cannot be what catches it: the script
# check has to be the thing that does.
p = polish.GeminiPolisher("key", transport=replying(
    "assalamu alaykum wa rahmatullahi wa barakatuh ana haqabilak "
    "elsaaa tamanya belmaghreb enshallah ya basha"))
result = p.polish(SPOKEN, "ar")
check("refuses a transliteration into Latin letters",
      result.text == SPOKEN, result.reason or "ACCEPTED IT")
check("and names the script, not the length",
      "came back as latin" in result.reason, result.reason)

p = polish.GeminiPolisher("key", transport=replying("مرحبا كيف حالك اليوم"))
english = ("hello this is a test of the dictation feature and it should stay "
           "in english when it comes back from the model")
result = p.polish(english, "en")
check("and the same the other way round", result.text == english,
      result.reason or "ACCEPTED IT")


# --------------------------------------------------------------- when it is off
print("\n--- off is the default, and off means off ---")

check("the default provider does nothing",
      polish.make("off").polish(SPOKEN).text == SPOKEN)
check("and reports itself unavailable", not polish.make("off").available)
check("a provider with no key is unavailable",
      not polish.GeminiPolisher("").available)
check("and returns the text untouched",
      polish.GeminiPolisher("").polish(SPOKEN).text == SPOKEN)
check("a fresh install has it off", config.Settings().dictation_ai == "off")
check("because this is the only thing here that leaves the machine",
      "generativelanguage.googleapis.com" in polish.ENDPOINT)


# ------------------------------------------------------------- what is sent
print("\n--- what is sent ---")

sent = {}
p = polish.GeminiPolisher("key", transport=lambda body: sent.update(body) or
                          {"candidates": [{"content": {"parts": [{"text": REPAIRED}]}}]})
p.polish(SPOKEN, "ar")
instruction = sent["systemInstruction"]["parts"][0]["text"].lower()
check("the dictation is what is sent, and nothing else",
      sent["contents"][0]["parts"][0]["text"] == SPOKEN)
check("it is told not to translate", "never translate" in instruction)
check("it is told to keep the dialect", "dialect" in instruction)
check("it is told to apply spoken corrections", "no, eight" in instruction)
check("it is told about emoji", "emoji" in instruction)
check("it is told to add nothing", "add nothing" in instruction)
check("the temperature is low, because this is repair not writing",
      sent["generationConfig"]["temperature"] <= 0.2,
      str(sent["generationConfig"]["temperature"]))


# ----------------------------------------------------------------- the key
print("\n--- the key ---")

stored = config.protect("AIzaSyExample-not-a-real-key")
check("is not written down in plain text", "AIzaSyExample" not in stored, stored[:20])
check("and comes back out", config.unprotect(stored) == "AIzaSyExample-not-a-real-key")
check("an empty key stays empty", config.protect("") == "")
check("and rubbish in the file does not crash the program",
      config.unprotect("dpapi:not-base64!!") == "")


print("\n" + "=" * 60)
if failures:
    print(f"FAILED: {len(failures)}")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("all polish checks passed")
print("=" * 60)
