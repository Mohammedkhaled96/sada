"""Remove hesitation sounds from dictated text.

Dictation is spoken, not read, so it arrives with the noises speech actually
contains: "um", "uh", "ممم", a vowel dragged out while the speaker thinks.
Whisper transcribes them faithfully, which is correct of it and useless in a
document.

The governing rule here is that **a filter which eats a real word is far worse
than one which leaves an "um" behind**. A stray filler is a cosmetic flaw the
reader skims past. A missing word changes the meaning of a sentence, and the
person dictating cannot see that it happened. So every pattern below has to be
something that is not a word in any context, and the tests assert as much about
what survives as about what is dropped.

Three things are deliberately NOT removed:

* **Interjections that carry meaning** - "ah", "oh", "eh", "huh", and Arabic
  "اه", which means yes in Egyptian Arabic. Dropping that one would turn an
  answer into its opposite.
* **Discourse markers** - "well", "like", "you know", "يعني". These are real
  words, and in Arabic "يعني" is often doing real work in the sentence.
  Removing them is editing someone's prose, not filtering noise.
* **Short single letters** - "a", "I", "و", "م". A single letter is a word, an
  initial or a list marker far more often than it is a hesitation.

`clean` returns what it removed as well as the cleaned text, so dictation can
log it. If this filter ever does eat something real, the evidence of what it
took is in the log rather than lost.
"""
from __future__ import annotations

import re
import unicodedata

from engine.textout import collapse_repeats, is_junk

# Arabic letters, without the diacritics that may sit on them.
AR = r"ء-ي"

# Tatweel: a typographic stretch, never part of a word's spelling.
TATWEEL = "ـ"

# Diacritics and the Quranic marks, stripped before matching so that a
# hesitation wearing a fatha is still recognised as one.
_MARKS = re.compile(r"[ً-ٰٟۖ-ۭ]")

# Everything a token might be wrapped in, so "um," and "«ممم»" still match.
_EDGES = re.compile(r"^[\s\"'“”«»(){}\[\].,،;؛:!?؟…\-–—]+|"
                    r"[\s\"'“”«»(){}\[\].,،;؛:!?؟…\-–—]+$")

# A token is a hesitation if it matches one of these in full. Each is a sound
# with no meaning in any context; anything arguable is in the docstring above
# as something we leave alone.
HESITATIONS = [
    # -------------------------------------------------- English
    re.compile(r"^um+$", re.I),          # um, umm, ummm  (NOT "am")
    re.compile(r"^uh+$", re.I),          # uh, uhh
    re.compile(r"^uh+m+$", re.I),        # uhm, uhmm
    re.compile(r"^erm+$", re.I),         # erm, ermm
    # "er" and a dragged "errrr", but not "err", which is a verb.
    re.compile(r"^(er|er{3,})$", re.I),
    re.compile(r"^h+m+$", re.I),         # hm, hmm, hhmm
    re.compile(r"^m+h+m+$", re.I),       # mhm, mhmm
    # Three m's at least: "mm" is how millimetres are written, and "5 mm"
    # must not lose its unit.
    re.compile(r"^m{3,}$", re.I),

    # -------------------------------------------------- Arabic
    # امم / أمم: a hum with a leading alif. Needs the m doubled, because
    # "أم" is a word (mother, or "or").
    re.compile(rf"^[اأإآ]م{{2,}}$"),
    # Alif repeated: never a word at any length.
    re.compile(r"^ا{2,}$"),
    # اههه: a sigh. "اه" alone means yes and is left alone, so the h must
    # be tripled before this fires.
    re.compile(r"^[اأإآ]ه{3,}$"),

    # ------------------------------------- any script: a dragged-out sound
    # One letter held: مممم, وووو, aaaa, ييي. Three or more of the same
    # letter and nothing else is not a word in Arabic or English.
    re.compile(rf"^([{AR}a-zA-Z])\1{{2,}}$"),
    # A letter followed by one held vowel: واااا, بااااا, soooo. The vowel is
    # back-referenced, so it is the SAME letter three times or more. Written
    # as a character class instead, "[اوي]{3,}" matches any mix of those
    # letters and would delete راوي, which is a word.
    re.compile(rf"^[{AR}a-zA-Z]([اويoe])\1{{2,}}$", re.I),
]

# Collapse a letter repeated this many times or more, for tokens that survive
# as words. Latin keeps two, because "ll" and "ee" are real spellings; Arabic
# doubles a letter with shadda rather than by writing it twice.
_LONG_RUN = 3
_AR_RUN = re.compile(rf"([{AR}])\1{{{_LONG_RUN - 1},}}")
_LATIN_RUN = re.compile(r"([a-zA-Z])\1{%d,}" % (_LONG_RUN - 1))


def _bare(token: str) -> str:
    """A token reduced to what the patterns are written against."""
    token = unicodedata.normalize("NFC", token)
    token = _EDGES.sub("", token)
    token = token.replace(TATWEEL, "")
    return _MARKS.sub("", token)


def is_hesitation(token: str) -> bool:
    """Whether a single token is a hesitation sound and nothing else."""
    bare = _bare(token)
    if not bare:
        return False
    return any(p.match(bare) for p in HESITATIONS)


def shorten_elongation(text: str) -> str:
    """Undo a held letter in a token that is otherwise a real word.

    "طيـــب" and "طيبببب" both become "طيب". This runs on words we are
    keeping, so it must not change spelling: only runs of three or more are
    touched, and Latin runs collapse to two rather than one.
    """
    text = text.replace(TATWEEL, "")
    text = _AR_RUN.sub(r"\1", text)
    return _LATIN_RUN.sub(r"\1\1", text)


def _tidy(text: str) -> str:
    """Close the gaps a removed token leaves behind."""
    text = re.sub(r"\s{2,}", " ", text)
    # " ," and " ." left where a filler used to be
    text = re.sub(r"\s+([.,،;؛:!?؟…])", r"\1", text)
    # ",," or ",." from two fillers in a row
    text = re.sub(r"([.,،;؛:!?؟…])\s*(?=[.,،;؛:!?؟…])", "", text)
    # punctuation now starting the line
    text = re.sub(r"^[\s.,،;؛:!?؟…]+", "", text)
    return text.strip()


def clean(text: str, *, remove_hesitations: bool = True) -> tuple[str, list[str]]:
    """Clean one dictated utterance.

    Returns (text, removed). `removed` lists the tokens that were dropped, in
    the order they appeared, so the caller can log what this did.

    With `remove_hesitations=False` the hallucination and stutter filters still
    run and elongation is still shortened - those are fixing Whisper's output,
    not editing the speaker's words.
    """
    if not text or not text.strip():
        return "", []

    # Whisper's closing-credits hallucinations in silence, and a stuttered
    # word or phrase. Both already handled for file transcription; a dictated
    # utterance is just as prone to them, more so because it is short.
    if is_junk(text):
        return "", [text.strip()]
    text = collapse_repeats(text)

    removed: list[str] = []
    kept: list[str] = []
    for token in text.split():
        if remove_hesitations and is_hesitation(token):
            removed.append(token)
            continue
        kept.append(shorten_elongation(token))

    cleaned = _tidy(" ".join(kept))
    # Everything was hesitation: an utterance of pure thinking noise should
    # insert nothing at all rather than a stray comma.
    if not re.search(rf"[{AR}a-zA-Z0-9]", cleaned):
        return "", removed + ([cleaned] if cleaned else [])
    return cleaned, removed
