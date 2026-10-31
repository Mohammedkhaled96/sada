"""Tidying what the model wrote, without deciding what the speaker meant.

There is a line here and it is deliberate. Collapsing a word the model emitted
four times in a row is tidying: nobody said it four times, and the repetition
is an artefact of the decoder. Turning "let's meet at two, no, three" into
"let's meet at 3:00" is deciding what the speaker meant, which needs a language
model, and PixelBoard's own source is the argument against it - Google's
Rambler does exactly that and sometimes answers the user instead of
transcribing them, so the patch set has a file whose whole job is forcing the
verbatim transcript back.

So: repetition that is clearly the decoder stuttering, spacing and punctuation,
and a list of replacements the user wrote themselves. Nothing that guesses.
"""
from __future__ import annotations

import re

from engine import emoji

# A word repeated this many times in a row was not said that many times. Twice
# is left alone: "very very", "لا لا", "no no" are things people say.
REPEAT_LIMIT = 3
# A phrase of this many words repeated back to back is the decoder looping.
# One word is not enough to be sure; two is the shortest that is.
PHRASE_MIN_WORDS = 2

_SPACE_BEFORE_PUNCT = re.compile(r"\s+([،,.؟?!:;%])")
_SPACE_AFTER_OPEN = re.compile(r"([(\[«])\s+")
_SPACE_BEFORE_CLOSE = re.compile(r"\s+([)\]»])")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")
_MULTI_PUNCT = re.compile(r"([،,.؟?!])\1{1,}")


def collapse_repeats(text: str) -> str:
    """Remove runs the decoder produced rather than the speaker.

    Two passes: a single word three or more times over, and then a phrase of
    two or more words repeated immediately after itself. Both are signatures
    of a decoder that has lost its place - the kind that produced
    "بسم الله بسم الله بس..." when a window was given no headroom.
    """
    words = text.split()
    if len(words) < 2:
        return text

    out: list[str] = []
    index = 0
    while index < len(words):
        run = 1
        while index + run < len(words) and words[index + run] == words[index]:
            run += 1
        # Three or more in a row leaves one. Two stays two: "very very",
        # "لا لا" and "no no" are things people say on purpose.
        out.extend([words[index]] * (1 if run >= REPEAT_LIMIT else run))
        index += run

    # Phrase repeats, longest first so "a b a b a b" collapses once rather
    # than leaving a shorter repeat behind.
    changed = True
    while changed:
        changed = False
        for size in range(len(out) // 2, PHRASE_MIN_WORDS - 1, -1):
            for i in range(len(out) - 2 * size + 1):
                if out[i:i + size] == out[i + size:i + 2 * size]:
                    del out[i + size:i + 2 * size]
                    changed = True
                    break
            if changed:
                break

    return " ".join(out)


def tidy(text: str, language: str | None = None) -> str:
    """Spacing, punctuation, repetition, and emoji the speaker asked for by
    name. Never wording."""
    if not text:
        return text
    text = collapse_repeats(text)
    text = emoji.apply(text)
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _SPACE_AFTER_OPEN.sub(r"\1", text)
    text = _SPACE_BEFORE_CLOSE.sub(r"\1", text)
    text = _MULTI_PUNCT.sub(r"\1", text)
    return _MULTI_SPACE.sub(" ", text).strip()


# ------------------------------------------------------------- user's own list

def parse_corrections(text: str) -> list[tuple[str, str]]:
    """Read the replacements the user wrote, one per line, `wrong = right`.

    Deliberately literal. A speech model will keep hearing a particular name as
    a particular wrong word, and the person who knows which is the person
    dictating - so they say it once here instead of fixing it by hand every
    time. Nothing is inferred and nothing is applied that they did not write.
    """
    pairs = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        wrong, _, right = line.partition("=")
        wrong, right = wrong.strip(), right.strip()
        if wrong:
            pairs.append((wrong, right))
    return pairs


def apply_corrections(text: str, pairs: list[tuple[str, str]]) -> str:
    """Whole words only, so a replacement cannot eat the inside of a word.

    Longest first: with both "عبد الله" and "عبد" on the list, the longer one
    has to win, or it never matches at all.
    """
    for wrong, right in sorted(pairs, key=lambda p: -len(p[0])):
        pattern = re.compile(rf"(?<!\w){re.escape(wrong)}(?!\w)", re.UNICODE)
        text = pattern.sub(right.replace("\\", "\\\\"), text)
    return _MULTI_SPACE.sub(" ", text).strip()
