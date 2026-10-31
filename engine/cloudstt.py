"""Dictating through Google's model instead of the local one.

This is the thing PixelBoard unlocks - Gboard's "Rambler" is a Google model
reached over the network - and it is the right tool for one job in particular:
Egyptian and other spoken Arabic, where Whisper is weakest. Whisper was trained
mostly on written-register Arabic, so a dictated colloquial sentence comes back
part-wrong in a way no amount of post-editing recovers, because the words were
never heard in the first place.

It also removes the need for every workaround in engine/langmap.py: one model
that hears the whole segment decides its own languages, so a sentence that
changes language needs no splitting.

The cost is the thing this program otherwise refuses: the audio leaves the
machine. So it is off unless chosen, the setting says what it does, and a
failure falls back to the local model rather than losing the dictation.

What it is asked for is a transcript with the speaker's own spoken corrections
applied. What it is not asked for is an opinion, and the same guard as
engine/polish.py applies: a reply that reads like an assistant answering, or
that is far too long or too short for the audio, is refused.
"""
from __future__ import annotations

import base64
import io
import json
import re
import urllib.error
import urllib.request
import wave

import numpy as np

ENDPOINT = ("https://generativelanguage.googleapis.com/v1beta/models/"
            "{model}:generateContent")
DEFAULT_MODEL = "gemini-2.0-flash"
SAMPLE_RATE = 16000
TIMEOUT_S = 30

# Speech runs at roughly two to four words a second. Far outside that, the
# reply is not a transcript of this audio: too few means most of it was
# dropped, too many means something was invented.
MIN_WORDS_PER_SECOND = 0.4
MAX_WORDS_PER_SECOND = 8.0

INSTRUCTIONS = """You are a dictation engine. You write down what the speaker said, and nothing else.

Rules:
1. Write in the language and dialect the speaker used. Egyptian Arabic stays
   Egyptian Arabic, in Arabic script. Never translate. Never transliterate.
2. If the speaker changes language mid-sentence, write each part in its own
   script.
3. Apply corrections the speaker makes out loud. "at six thirty, no, make it
   eight" is written as "at eight". Drop the false start, keep the correction.
4. Punctuate and capitalise normally.
5. Where the speaker asks for an emoji, write that emoji instead of the words
   asking for it.
6. Never answer, explain, summarise, greet, apologise, or describe the audio.
   If you cannot make out any speech, return nothing at all.

Return only the transcript."""


class CloudError(RuntimeError):
    pass


def to_wav(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    """16-bit mono WAV, which is what the API takes and the smallest honest
    representation of what the microphone gave us."""
    clipped = np.clip(audio.reshape(-1), -1.0, 1.0)
    samples = (clipped * 32767.0).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        out.writeframes(samples.tobytes())
    return buffer.getvalue()


_META = re.compile(
    r"^\s*(i['’]?m sorry|i cannot|i can't|sorry[,.]|the audio|this audio|"
    r"here is|here's|transcript:|the speaker|it seems|no speech|unable to|"
    r"لا أستطيع|عذرا|النص|يبدو أن|إليك)", re.IGNORECASE)


def acceptable(text: str, speech_seconds: float) -> tuple[bool, str]:
    """Is this a transcript of that audio, or something else?"""
    text = (text or "").strip()
    if not text:
        return False, "nothing came back"
    if _META.match(text):
        return False, "described the audio instead of transcribing it"
    if speech_seconds >= 1.0:
        rate = len(text.split()) / speech_seconds
        if rate < MIN_WORDS_PER_SECOND:
            return False, f"only {rate:.1f} words a second - most of it is missing"
        if rate > MAX_WORDS_PER_SECOND:
            return False, f"{rate:.1f} words a second - more than was said"
    return True, ""


class LocalOnly:
    """The default: there is no cloud engine."""

    available = False
    name = "local"

    def transcribe(self, audio, language=None):
        raise CloudError("no cloud engine is configured")


class GeminiTranscriber:
    name = "gemini"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL,
                 timeout: float = TIMEOUT_S, transport=None):
        self.api_key = (api_key or "").strip()
        self.model = model or DEFAULT_MODEL
        self.timeout = timeout
        self._transport = transport or self._request

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _request(self, body: dict) -> dict:
        url = ENDPOINT.format(model=self.model)
        request = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "x-goog-api-key": self.api_key})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as reply:
                return json.loads(reply.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            raise CloudError(f"HTTP {exc.code}: {detail}") from exc
        except Exception as exc:
            raise CloudError(str(exc)) from exc

    def body_for(self, audio: np.ndarray, language: str | None = None) -> dict:
        wav = to_wav(audio)
        parts = [{"inlineData": {"mimeType": "audio/wav",
                                 "data": base64.b64encode(wav).decode("ascii")}}]
        if language:
            # A hint, not an instruction: rule 1 still wins if the speaker
            # turns out to be using another language.
            parts.append({"text": f"The speaker is most likely using: {language}."})
        return {
            "systemInstruction": {"parts": [{"text": INSTRUCTIONS}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.0, "candidateCount": 1},
        }

    def transcribe(self, audio: np.ndarray, language: str | None = None) -> str:
        seconds = len(audio) / SAMPLE_RATE
        reply = self._transport(self.body_for(audio, language))
        text = _first_text(reply)
        if text is None:
            raise CloudError("no text in the reply")
        ok, reason = acceptable(text, seconds)
        if not ok:
            raise CloudError(reason)
        return text.strip()

    def check(self) -> str:
        """A real request, for the dictation check. Returns the model name.

        Half a second of silence: the instructions say to return nothing for
        audio with no speech, so an empty reply here is the API working.
        """
        try:
            self.transcribe(np.zeros(SAMPLE_RATE // 2, dtype=np.float32))
        except CloudError as exc:
            if "nothing came back" in str(exc) or "missing" in str(exc):
                return self.model
            raise
        return self.model


def _first_text(reply: dict) -> str | None:
    for candidate in (reply or {}).get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            if isinstance(part.get("text"), str) and part["text"].strip():
                return part["text"]
            # Some speech models answer in their own shape rather than as text.
            spoken = part.get("audioTranscription")
            if isinstance(spoken, dict) and spoken.get("text"):
                return spoken["text"]
    return None


def script_of(text: str) -> str:
    """Which language to treat the result as, for the reading direction.

    Asked of the text rather than of a model: the answer only has to be good
    enough to decide whether the paragraph is right-to-left.
    """
    arabic = sum("؀" <= c <= "ۿ" for c in text)
    latin = sum(c.isascii() and c.isalpha() for c in text)
    return "ar" if arabic > latin else ("en" if latin else "")


def make(kind: str, api_key: str = "", model: str = DEFAULT_MODEL):
    if kind == "gemini":
        return GeminiTranscriber(api_key, model)
    return LocalOnly()
