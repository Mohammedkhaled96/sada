"""Hesitation filtering for dictated text.

Half of this file is about what must NOT be removed, and that is the point. A
stray "um" in a document is cosmetic; a deleted word changes the meaning of a
sentence and the person dictating cannot see that it happened. Every real word
listed below is one a careless pattern would have eaten - راوي was eaten by the
first draft of this module.

Run:  PYTHONUTF8=1 .venv/Scripts/python.exe tests/test_fillers.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.fillers import clean, is_hesitation, shorten_elongation

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))
    if not ok:
        failures.append(label)


def dropped(token: str) -> bool:
    return is_hesitation(token)


# ------------------------------------------------------- hesitations removed
print("--- recognised as hesitation ---")

for token in ["um", "umm", "ummm", "uh", "uhh", "uhm", "uhmm",
              "erm", "ermm", "er", "errrr", "hm", "hmm", "mhm", "mmm",
              "UM", "Uh", "Hmm"]:
    check(f"english: {token}", dropped(token))

for token in ["ممم", "مممم", "امم", "أمم", "اا", "ااا", "اههه",
              "وووو", "ييي", "واااا", "بااااا"]:
    check(f"arabic: {token}", dropped(token))

check("wrapped in punctuation: um,", dropped("um,"))
check("wrapped in quotes: “ممم”", dropped("“ممم”"))
check("with a diacritic: مُمم", dropped("مُمم"))
check("with tatweel: مـممم", dropped("مـممم"))
check("latin held letter: aaaa", dropped("aaaa"))
check("latin held vowel: soooo", dropped("soooo"))


# ------------------------------------------------------------- words survive
print("\n--- must survive ---")

# Each of these is a word a sloppy pattern would have taken.
survivors = {
    "am": "the verb, one letter from um",
    "err": "to err is human",
    "mm": "millimetres - 5 mm must keep its unit",
    "ah": "an interjection that carries meaning",
    "oh": "likewise",
    "eh": "likewise",
    "huh": "likewise",
    "well": "a real word, and a discourse marker we do not edit",
    "like": "likewise",
    "see": "two e's, not a held vowel",
    "too": "two o's",
    "so": "one o",
    "راوي": "narrator - the first draft of this module deleted it",
    "داوي": "same shape",
    "اه": "yes, in Egyptian Arabic - deleting it inverts an answer",
    "أم": "mother, or 'or' - one meem",
    "مم": "rare, but left alone rather than risked",
    "يعني": "a real word doing real work in the sentence",
    "و": "and",
    "م": "a single letter is an initial or a list marker",
    "ا": "likewise",
    "I": "likewise",
    "a": "likewise",
}
for token, why in survivors.items():
    check(f"keeps {token!r}", not dropped(token), why)


# ------------------------------------------------------------- elongation
print("\n--- elongation shortened, spelling kept ---")

check("tatweel removed: طيـــب", shorten_elongation("طيـــب") == "طيب",
      shorten_elongation("طيـــب"))
check("held arabic letter: طيبببب", shorten_elongation("طيبببب") == "طيب",
      shorten_elongation("طيبببب"))
check("latin run collapses to two: greeeat",
      shorten_elongation("greeeat") == "greeat", shorten_elongation("greeeat"))
check("a real double is untouched: letter",
      shorten_elongation("letter") == "letter")
check("a real double is untouched: coffee",
      shorten_elongation("coffee") == "coffee")
check("arabic spelling untouched: السلام",
      shorten_elongation("السلام") == "السلام")


# ------------------------------------------------------------- whole phrases
print("\n--- phrases ---")

text, removed = clean("um I think, uh, this is fine")
check("english fillers dropped from a sentence",
      text == "I think, this is fine", text)
check("removed tokens reported", removed == ["um", "uh,"], str(removed))

text, _ = clean("ممم يعني الكلام ده اااا صحيح")
check("arabic fillers dropped, يعني kept",
      text == "يعني الكلام ده صحيح", text)

text, _ = clean("اه صح")
check("اه is kept as an answer", text == "اه صح", text)

text, removed = clean("um uh hmm")
check("an utterance of pure hesitation inserts nothing", text == "", repr(text))
check("and still reports what it dropped", len(removed) >= 3, str(removed))

text, _ = clean("   ")
check("blank input gives blank output", text == "", repr(text))

text, _ = clean("um, this.")
check("no comma left stranded where a filler was", text == "this.", text)

text, _ = clean("thanks for watching")
check("a known hallucination is dropped whole", text == "", repr(text))

text, _ = clean("the the the point")
check("a stutter collapses", text == "the point", text)

text, _ = clean("5 mm wide")
check("a unit survives", text == "5 mm wide", text)

text, _ = clean("um طيـــب um")
check("filtering and elongation together", text == "طيب", text)

# With filtering off, Whisper's own faults are still corrected: that is fixing
# the transcript, not editing what the speaker said.
text, removed = clean("um طيبببب", remove_hesitations=False)
check("filtering off keeps the um", "um" in text, text)
check("filtering off still shortens elongation", "طيب" in text, text)
check("filtering off removes nothing", removed == [], str(removed))


# ----------------------------------------------------------------------- done
print("\n" + "=" * 60)
if failures:
    print(f"FAILED: {len(failures)}")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("all checks passed")
print("=" * 60)
