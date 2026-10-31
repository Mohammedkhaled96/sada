"""Saying "smile face emoji" and getting 🙂.

Done with a table rather than a model, and only where the speaker said the word
"emoji" (or "ايموجي"). That restriction is the whole safety of it: "my heart"
stays "my heart", and only "heart emoji" becomes a heart. Nothing is guessed
from sentiment and nothing is inserted that was not asked for by name.

It works with no network and no language model, so it behaves the same on all
three dictation engines - and on the two that reach Google it runs afterwards
as well, harmlessly, because by then the words are already gone.
"""
from __future__ import annotations

import re

# The marker word, in each language. Without one of these present in the
# phrase, nothing here does anything at all.
MARKERS = ("emoji", "emojis", "ايموجي", "إيموجي", "ايموجى", "امواجي")

# name -> emoji. Several spellings per emoji, because a speech model writes
# what it hears and "thumbs up" arrives as "thumbs up", "thumb up" or "thumbsup".
NAMES: dict[str, tuple[str, ...]] = {
    "🙂": ("smile face", "smiley face", "smiling face", "smile", "smiley",
           "ابتسامة", "وش مبتسم", "ابتسام"),
    "😀": ("grinning face", "grin", "happy face", "وش فرحان", "فرح"),
    "😂": ("laughing", "laugh", "tears of joy", "crying laughing", "ضحك",
           "ضحك بالدموع", "بيضحك"),
    "😉": ("winking face", "wink", "غمزة"),
    "😍": ("heart eyes", "love eyes", "قلوب في العين", "عيون قلوب"),
    "😢": ("crying face", "crying", "sad face", "sad", "حزين", "بكاء", "زعلان"),
    "😡": ("angry face", "angry", "غاضب", "زعل", "عصبية"),
    "😮": ("surprised face", "surprised", "wow face", "اندهاش", "مندهش"),
    "😴": ("sleeping face", "sleepy", "نوم", "نعسان"),
    "🤔": ("thinking face", "thinking", "تفكير", "بيفكر"),
    "👍": ("thumbs up", "thumb up", "thumbsup", "like", "ايد بتعمل تمام",
           "لايك", "إعجاب", "تمام"),
    "👎": ("thumbs down", "thumb down", "dislike", "مش عاجبني"),
    "👏": ("clapping", "clap", "applause", "تصفيق"),
    # "please" is not on this list: "add an emoji, please" is a request for
    # any emoji, not for praying hands.
    "🙏": ("praying hands", "pray", "thank you hands", "دعاء",
           "ايد بتدعي", "شكرا"),
    "🤝": ("handshake", "shaking hands", "مصافحة", "سلام بالايد"),
    "❤️": ("heart", "red heart", "love", "قلب", "قلب احمر", "حب"),
    "💔": ("broken heart", "قلب مكسور"),
    "🔥": ("fire", "نار", "نار حمرا"),
    "⭐": ("star", "نجمة"),
    "✅": ("check mark", "tick", "correct", "علامة صح", "صح"),
    "❌": ("cross mark", "wrong", "علامة غلط", "غلط", "اكس"),
    "⚠️": ("warning", "warning sign", "تحذير", "علامة تحذير"),
    "🎉": ("party popper", "party", "celebration", "احتفال", "مبروك"),
    "🎂": ("birthday cake", "cake", "تورتة", "كيكة"),
    "🌹": ("rose", "وردة"),
    "☀️": ("sun", "شمس"),
    "🌙": ("moon", "قمر", "هلال"),
    "💡": ("light bulb", "idea", "فكرة", "لمبة"),
    "📌": ("pin", "pushpin", "دبوس"),
    "📞": ("telephone", "phone", "call", "تليفون", "مكالمة"),
    "✉️": ("envelope", "email", "mail", "ايميل", "جواب"),
    "⏰": ("alarm clock", "clock", "ساعة منبه", "منبه"),
    "💻": ("laptop", "computer", "كمبيوتر", "لابتوب"),
    "🚗": ("car", "عربية", "سيارة"),
    "✈️": ("airplane", "plane", "طيارة", "طائرة"),
    "💰": ("money bag", "money", "فلوس", "كيس فلوس"),
    "📈": ("chart up", "chart increasing", "رسم بياني صاعد", "زيادة"),
    "🤷": ("shrug", "مش عارف", "هز كتاف"),
}

# Longest first, so "crying laughing" wins over "crying" and "heart eyes" over
# "heart". Without this the longer name never matches at all.
_LOOKUP: list[tuple[str, str]] = sorted(
    ((name, glyph) for glyph, names in NAMES.items() for name in names),
    key=lambda pair: -len(pair[0]))

_MARKER_RE = "|".join(re.escape(m) for m in MARKERS)
_MARKS = re.compile(r"[ً-ْـ]")


def _normalise(text: str) -> str:
    text = _MARKS.sub("", text)
    return (text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
            .replace("ة", "ه").replace("ى", "ي"))


def _patterns():
    """Both word orders, in both languages.

    English puts the marker last - "smile face emoji" - and Arabic puts it
    first - "ايموجي ابتسامة" - and dictated speech does both in either
    language, so both are accepted everywhere.
    """
    for name, glyph in _LOOKUP:
        spelled = re.escape(_normalise(name)).replace(r"\ ", r"[\s\-]+")
        yield re.compile(rf"(?<!\w)(?:{_MARKER_RE})[\s\-]*(?:of[\s\-]+)?"
                         rf"(?:a[\s\-]+|the[\s\-]+)?{spelled}(?!\w)",
                         re.IGNORECASE | re.UNICODE), glyph
        yield re.compile(rf"(?<!\w){spelled}[\s\-]*(?:{_MARKER_RE})(?!\w)",
                         re.IGNORECASE | re.UNICODE), glyph


_COMPILED = list(_patterns())
# Said with no name at all - "put an emoji here" - there is nothing to insert,
# so the stray word goes, and the article in front of it goes with it. Leaving
# "put an here" behind would be a worse sentence than the one dictated.
_BARE = re.compile(rf"(?<!\w)(?:(?:an?|the)[\s\-]+)?(?:{_MARKER_RE})(?!\w)",
                   re.IGNORECASE | re.UNICODE)
_SPACING = re.compile(r"\s+([،,.؟?!:;])")


def apply(text: str) -> str:
    """Replace spoken emoji requests with the emoji themselves."""
    if not text:
        return text
    lowered = _normalise(text)
    if not any(_MARKS.sub("", m) in lowered.lower() for m in MARKERS):
        return text                      # nothing asked for one; do nothing

    # Matching happens on a normalised copy and the edits are applied to the
    # original by offset, so diacritics the speaker's words carried are not
    # quietly stripped from the rest of the sentence.
    edits: list[tuple[int, int, str]] = []
    taken = [False] * len(lowered)
    for pattern, glyph in _COMPILED:
        for found in pattern.finditer(lowered):
            start, end = found.span()
            if any(taken[start:end]):
                continue
            for i in range(start, end):
                taken[i] = True
            edits.append((start, end, glyph))

    for found in _BARE.finditer(lowered):
        start, end = found.span()
        if not any(taken[start:end]):
            edits.append((start, end, ""))

    if not edits:
        return text
    out = text
    for start, end, glyph in sorted(edits, reverse=True):
        out = out[:start] + glyph + out[end:]
    out = _SPACING.sub(r"\1", out)
    return re.sub(r"\s{2,}", " ", out).strip()
