"""Removing hesitation sounds, and nothing else.

The previous attempt at this used patterns for repeated letters, and they ate
real words: `[اوي]{3,}` deleted **راوي**, and `^m{2,}$` deleted the "mm" in
"5 mm". A dictation tool that silently removes a word the user said is worse
than one that leaves an "أمم" in.

So there are no patterns here. There is a list of sounds that are not words in
either language, matched whole, and anything not on the list survives. Words
that are fillers in speech but also carry meaning - "يعني", "like", "you know",
"آه" - are deliberately absent: deciding whether this particular "يعني" meant
something takes a language model, and docs/voice-typing.md is about what
happens when a language model is allowed to decide what you meant.

This runs on a dictation and never on a file, from engine/job.py's `polish`.
tests/test_text.py asserts the survivors, not just the removals.
"""
from __future__ import annotations

import re

# Sounds, not words. Each is matched whole, case-insensitively, with its own
# diacritics stripped first.
FILLERS = {
    "ar": {
        "امم", "أمم", "إمم", "اممم", "أممم", "امممم",
        "ممم", "مممم", "همم", "هممم",
        "ااا", "اااا", "اووو", "اييي",
    },
    "en": {
        "um", "umm", "ummm", "uhm", "uhmm",
        "uh", "uhh", "uhhh",
        "er", "erm", "ermm",
        "hmm", "hmmm", "mmm", "mmmm", "mhm",
    },
}

# Arabic diacritics and the tatweel, which change nothing about which word it is.
_MARKS = re.compile(r"[ً-ْـ]")
# Anything that is not a letter or a digit in any script.
_EDGE = re.compile(r"^\W+|\W+$", re.UNICODE)


def _key(word: str) -> str:
    stripped = _EDGE.sub("", _MARKS.sub("", word)).lower()
    # Alef written three ways is the same letter to a listener.
    return stripped.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")


def is_filler(word: str, language: str | None = None) -> bool:
    key = _key(word)
    if not key:
        return False
    if language and language in FILLERS:
        return key in FILLERS[language]
    return any(key in words for words in FILLERS.values())


def strip_fillers(text: str, language: str | None = None) -> str:
    """Drop filler words, keeping the spacing and punctuation sane.

    A word is only dropped if the whole of it is a filler. Punctuation that was
    attached to it is kept, so "أمم، تمام" does not become "، تمام".
    """
    if not text:
        return text

    out: list[str] = []
    for word in text.split(" "):
        if not word:
            continue
        if is_filler(word, language):
            tail = _EDGE.search(word)
            trailing = re.sub(r"^\w+", "", _MARKS.sub("", word), flags=re.UNICODE)
            if trailing.strip() and out:
                out[-1] = out[-1] + trailing.strip()
            continue
        out.append(word)

    cleaned = " ".join(out)
    cleaned = re.sub(r"\s+([،,.؟?!:;])", r"\1", cleaned)
    cleaned = re.sub(r"^[\s،,.]+", "", cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def is_all_filler(text: str, language: str | None = None) -> bool:
    """True when a whole segment is nothing but hesitation.

    Used to throw a segment away rather than insert an empty line for it.
    """
    words = [w for w in text.split() if _key(w)]
    return bool(words) and all(is_filler(w, language) for w in words)
