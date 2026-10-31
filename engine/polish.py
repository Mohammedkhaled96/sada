"""Letting a language model repair a dictation - under guard.

Everything else in this program runs on the machine it is installed on. This
does not. It sends what you dictated to Google, so it is off unless you turn it
on, it needs a key you supply, and the interface says so in plain words.

WHY GOOGLE AND NOT A LOCAL MODEL. A local model was the first choice and is
still the better one. It is not possible on this network: huggingface.co,
ollama.com, registry.ollama.ai, api.openai.com and openrouter.ai are all
TLS-blocked, and llama-cpp-python publishes no Windows wheel, so neither the
weights nor the runtime can be fetched. generativelanguage.googleapis.com is
the one host that answers. If a model file is ever brought here by hand, the
only thing that has to change is a second class in this file.

WHAT IT IS ALLOWED TO DO. Apply a correction the speaker made out loud - "six
thirty, no, eight" is eight. Punctuate. Write an emoji where the speaker asked
for one. Repair words the recogniser clearly misheard, which matters most in
dialect, where Whisper is weakest.

WHAT IT MAY NOT DO. Answer, summarise, translate, add a fact, change the
language, or make the speaker sound different. PixelBoard exists partly
because Google's own dictation crossed that line and started replying to
people instead of transcribing them, so the result is checked rather than
trusted: if it has grown or shrunk beyond what repair can explain, or if it
has changed script, the original is kept and the user is told nothing was
changed. A quiet fallback to the words that were actually said is always
safe; a confident rewrite of something the user cannot see is not.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

ENDPOINT = ("https://generativelanguage.googleapis.com/v1beta/models/"
            "{model}:generateContent")
DEFAULT_MODEL = "gemini-2.0-flash"
TIMEOUT_S = 20

# A repair stays about the same size. Far outside this band the model has
# started writing rather than correcting, so the original is kept.
MIN_RATIO = 0.55
MAX_RATIO = 1.8
# An emoji the speaker asked for replaces the words asking for it - "emoji of
# a thumbs up" becomes one character - so a legitimate repair can be much
# shorter than what was said. Each new emoji is credited roughly the length of
# a spoken request for one, and the credit is capped so that emoji alone can
# never excuse the rest of the text going missing.
EMOJI_CREDIT = 12
MAX_EMOJI_CREDIT_RATIO = 0.4
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF←-⇿⬀-⯿]")

INSTRUCTIONS = """You repair dictated text. You are not an assistant and you never reply to it.

Rules:
1. Keep the speaker's own words, language and dialect. If they dictated
   Egyptian Arabic, the result is Egyptian Arabic. Never translate. Never
   change the script a word is written in.
2. Apply corrections the speaker made out loud. "at six thirty, no, eight"
   becomes "at eight". "الساعة ستة ونص، لا لا، تمانية" becomes "الساعة تمانية".
   Remove the false start, keep the correction.
3. Repair words the speech recogniser clearly misheard, using the surrounding
   sentence. Only where the intended word is obvious. If it is not obvious,
   leave the text exactly as it is.
4. Punctuate and paragraph normally.
5. Where the speaker asks for an emoji, write that emoji instead of the words
   asking for it.
6. Add nothing. No greeting, no summary, no explanation, no answer to a
   question in the text, no heading, no quotation marks around the result.

Return only the repaired text."""


class PolishError(RuntimeError):
    pass


@dataclass
class Polished:
    text: str
    changed: bool
    reason: str = ""          # why it was refused, when it was


def _letters(text: str) -> int:
    return sum(c.isalpha() for c in _EMOJI.sub("", text))


def _dominant(text: str) -> str | None:
    """Which script this text is mostly in, or None when it is too short to say."""
    stripped = _EMOJI.sub("", text)
    arabic = sum("؀" <= c <= "ۿ" for c in stripped)
    latin = sum(c.isascii() and c.isalpha() for c in stripped)
    if arabic + latin < 10 or arabic == latin:
        return None
    return "arabic" if arabic > latin else "latin"


def acceptable(original: str, candidate: str) -> tuple[bool, str]:
    """Is this a repair of that text, or a different text?

    Deliberately crude and deliberately strict. This runs on words that are
    about to be written into a document by someone who cannot see them, and
    the cost of wrongly keeping the original is that the user edits a comma
    themselves.
    """
    candidate = candidate.strip()
    if not candidate:
        return False, "empty"

    before, after = _letters(original), _letters(candidate)
    added_emoji = max(0, len(_EMOJI.findall(candidate))
                      - len(_EMOJI.findall(original)))
    credit = min(added_emoji * EMOJI_CREDIT,
                 int(before * MAX_EMOJI_CREDIT_RATIO))
    if before and not (MIN_RATIO <= (after + credit) / before <= MAX_RATIO):
        return False, f"length changed by {(after + credit) / before:.2f}x"

    # A dictation that was mostly Arabic may not come back mostly Latin: that
    # is transliteration or translation, which rule 1 forbids. Compared as
    # which script dominates, not as how many letters survived - an emoji
    # standing in for four spoken words removes letters legitimately, and a
    # repair is allowed to be shorter than what was said.
    was, now = _dominant(original), _dominant(candidate)
    if was and now and was != now:
        return False, f"{was} text came back as {now}"

    lowered = candidate.lower()
    for giveaway in ("as an ai", "i cannot", "sorry,", "here is the",
                     "النص المصحح", "إليك ", "لا أستطيع"):
        if lowered.startswith(giveaway) or giveaway in lowered[:40]:
            return False, "answered instead of repairing"
    return True, ""


class NullPolisher:
    """The default. Does nothing, and says so."""

    available = False
    name = "off"

    def polish(self, text: str, language: str = "") -> Polished:
        return Polished(text, False)


class GeminiPolisher:
    """Google's API, with the user's own key."""

    name = "gemini"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL,
                 timeout: float = TIMEOUT_S, transport=None):
        self.api_key = (api_key or "").strip()
        self.model = model or DEFAULT_MODEL
        self.timeout = timeout
        # Swapped out in tests. Nothing here should need the network to be
        # exercised - the guard above is the part that matters most, and it
        # must be testable without a key or a connection.
        self._transport = transport or self._request

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    # -- the call ----------------------------------------------------------
    def _request(self, body: dict) -> dict:
        url = ENDPOINT.format(model=self.model)
        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json",
                     "x-goog-api-key": self.api_key})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as reply:
                return json.loads(reply.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            raise PolishError(f"HTTP {exc.code}: {detail}") from exc
        except Exception as exc:
            raise PolishError(str(exc)) from exc

    def check(self) -> str:
        """Prove the key works, for the dictation check. Returns the model."""
        result = self.polish("hello world", "en")
        if result.reason:
            raise PolishError(result.reason)
        return self.model

    def polish(self, text: str, language: str = "") -> Polished:
        text = (text or "").strip()
        if not text or not self.available:
            return Polished(text, False, "" if text else "nothing to repair")

        body = {
            "systemInstruction": {"parts": [{"text": INSTRUCTIONS}]},
            "contents": [{"role": "user", "parts": [{"text": text}]}],
            "generationConfig": {
                # Repair, not invention. The temperature is the first line of
                # defence and the guard below is the second.
                "temperature": 0.1,
                "candidateCount": 1,
                "maxOutputTokens": max(256, len(text)),
            },
        }
        reply = self._transport(body)
        candidate = _first_text(reply)
        if candidate is None:
            return Polished(text, False, "no text in the reply")

        ok, reason = acceptable(text, candidate)
        if not ok:
            return Polished(text, False, reason)
        return Polished(candidate.strip(), candidate.strip() != text)


def _first_text(reply: dict) -> str | None:
    """Pull the text out, whichever shape the reply arrives in."""
    for candidate in (reply or {}).get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            if isinstance(part.get("text"), str) and part["text"].strip():
                return part["text"]
    return None


def make(kind: str, api_key: str = "", model: str = DEFAULT_MODEL):
    if kind == "gemini":
        return GeminiPolisher(api_key, model)
    return NullPolisher()
