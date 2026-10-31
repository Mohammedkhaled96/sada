"""Tidying dictated text with a language model running on this machine.

Spelling, punctuation and grammar, and the hesitation words a rule cannot
safely remove. It runs through Ollama on 127.0.0.1, so the text does not leave
the machine any more than the audio does, and the model lives in `nlm/` rather
than `models/` - that one holds the speech checkpoints, and a user clearing
one must not take the other with it.

ENTIRELY OPTIONAL. With no Ollama installed, or no model pulled, or the server
not running, `available` is False and dictation behaves exactly as it does
now. Nothing to configure and nothing to switch off.

WHAT IT MAY NOT DO. The same line as everywhere else in this program: tidying
what the recogniser produced is in scope, deciding what the speaker meant is
not. The prompt says so and the result is checked rather than trusted - if it
has grown or shrunk beyond what a correction explains, or changed script, or
started answering, the original text is kept. The user is writing into a
document they cannot see.

On a CPU this costs real time: a few seconds for a sentence on a 4B model.
That is why the job reports a state for it rather than appearing to hang.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

HOST = "http://127.0.0.1:11434"
TIMEOUT_S = 120
# Long enough that a second dictation a minute later does not pay the load
# again, short enough that the memory comes back if the user stops.
KEEP_ALIVE = "30m"

# Preference order, best first, picked from what is actually installed - so
# pulling a better model is the whole of the upgrade.
#
# Two orders, because the right model depends on what it will run on. The
# machine decides, not the user: somebody who pulls an 8B model onto a laptop
# with no graphics card should not have to work out for themselves why
# dictation suddenly takes half a minute.
PREFERRED_GPU = (
    "qwen3:8b",         # best Arabic of the practical sizes, ~5.2 GB
    "falcon3:7b",       # Arabic-native alternative
    "qwen3:4b",
    "qwen2.5:7b",
    "llama3.1:8b",
    "gemma2:9b",
    "qwen3:1.7b",
    "qwen2.5:3b",
)
# On a CPU an 8B model answers in tens of seconds, which is longer than the
# transcription it is correcting. Small first here, and that is the right way
# round: this step fixes commas and hesitation words, and a 4B model does
# that as well as an 8B one.
PREFERRED_CPU = (
    "qwen3:4b",         # ~2.5 GB, the balance for a machine with no GPU
    "qwen2.5:3b",
    "qwen3:1.7b",       # ~1.4 GB, for a slow machine
    "gemma2:2b",
    "llama3.2:3b",
    "falcon3:3b",
    "qwen3:8b",         # last, rather than never: better slow than nothing
    "falcon3:7b",
)


def preferred(has_gpu: bool | None = None) -> tuple[str, ...]:
    """The order to pick in, for this machine."""
    if has_gpu is None:
        try:
            from engine.device import pick_device

            has_gpu = pick_device("auto").device == "cuda"
        except Exception:
            has_gpu = False
    return PREFERRED_GPU if has_gpu else PREFERRED_CPU

# A correction stays about the same size. Far outside this band the model has
# started writing instead of correcting.
MIN_RATIO = 0.6
MAX_RATIO = 1.6

SYSTEM_PROMPT = (
    "صحّح الإملاء والترقيم والنحو في النص التالي. "
    "احذف كلمات التردد مثل (يعني، اه، امم) والتكرار غير المقصود. "
    "حافظ على المعنى والأسلوب ولا تغيّر اللهجة ولا تحوّل العامية إلى فصحى. "
    "لا تترجم، ولا تلخّص، ولا تشرح، ولا تجب على أي سؤال في النص. "
    "اكتب كل لغة بحروفها كما نُطقت. "
    "أعد النص المصحّح فقط بدون أي مقدمة أو تعليق."
    "\n\n"
    "Correct spelling, punctuation and grammar in the text below. Remove "
    "hesitation words and unintended repetition. Keep the meaning, the style "
    "and the dialect exactly - never turn colloquial speech into formal "
    "writing. Do not translate, summarise, explain, or answer anything in "
    "the text. Write each language in its own script. Return only the "
    "corrected text, with no preamble and no comment."
)

# Qwen3 reasons out loud unless told not to, which triples the time for
# nothing: there is nothing to reason about in fixing a comma.
NO_THINK = "/no_think"
_THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


class RefineError(RuntimeError):
    pass


@dataclass
class Refined:
    text: str
    changed: bool
    reason: str = ""


def _letters(text: str) -> int:
    return sum(c.isalpha() for c in text)


def _dominant(text: str) -> str | None:
    arabic = sum("؀" <= c <= "ۿ" for c in text)
    latin = sum(c.isascii() and c.isalpha() for c in text)
    if arabic + latin < 10 or arabic == latin:
        return None
    return "arabic" if arabic > latin else "latin"


def acceptable(original: str, candidate: str) -> tuple[bool, str]:
    """Is this a correction of that text, or a different text?

    Deliberately strict. Wrongly keeping the original costs the user a comma.
    Wrongly accepting a rewrite puts words in a document they cannot see.
    """
    candidate = candidate.strip()
    if not candidate:
        return False, "empty"

    before, after = _letters(original), _letters(candidate)
    if before and not (MIN_RATIO <= after / before <= MAX_RATIO):
        return False, f"length changed by {after / before:.2f}x"

    was, now = _dominant(original), _dominant(candidate)
    if was and now and was != now:
        return False, f"{was} text came back as {now}"

    low = candidate.lower()
    for giveaway in ("here is", "here's", "the corrected", "sure,", "of course",
                     "النص المصحح", "إليك ", "بالطبع", "as an ai"):
        if low.startswith(giveaway) or giveaway in low[:40]:
            return False, "answered instead of correcting"
    return True, ""


def strip_thinking(text: str) -> str:
    """Remove a reasoning block a model emitted anyway."""
    return _THINK_BLOCK.sub("", text or "").strip()


class NoRefiner:
    """The default when nothing is installed. Does nothing, and says so."""

    available = False
    model = ""

    def refine(self, text: str) -> Refined:
        return Refined(text, False)


class OllamaRefiner:
    """A model running locally, reached over the loopback interface."""

    def __init__(self, model: str = "", host: str = HOST,
                 timeout: float = TIMEOUT_S, transport=None,
                 has_gpu: bool | None = None):
        self.host = host.rstrip("/")
        self.timeout = timeout
        # Injected in tests. Nothing here should need a server running to be
        # exercised, and the guard above is the part that matters most.
        self._transport = transport
        self.has_gpu = has_gpu
        self.model = model or ""

    # -- talking to it -----------------------------------------------------
    def _post(self, path: str, body: dict) -> dict:
        if self._transport is not None:
            return self._transport(path, body)
        request = urllib.request.Request(
            f"{self.host}{path}", data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as reply:
                return json.loads(reply.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RefineError(f"HTTP {exc.code}") from exc
        except Exception as exc:
            raise RefineError(str(exc)) from exc

    def installed(self) -> list[str]:
        """Model names this Ollama has, or an empty list if it is not there."""
        if self._transport is not None:
            reply = self._transport("/api/tags", {})
        else:
            try:
                with urllib.request.urlopen(f"{self.host}/api/tags",
                                            timeout=5) as r:
                    reply = json.loads(r.read().decode("utf-8"))
            except Exception:
                return []
        return [m.get("name", "") for m in (reply or {}).get("models", [])]

    def choose(self) -> str:
        """The best installed model, by the order in PREFERRED.

        A name that is not on the list is still used when it is the only one
        there: somebody who pulled a model on purpose meant to use it.
        """
        names = self.installed()
        if not names:
            return ""
        order = preferred(self.has_gpu)

        # Exact sizes first, over the whole list. Matching by family would let
        # "qwen3:4b" be satisfied by "qwen3:8b", which on a machine with no
        # graphics card is the one choice this ordering exists to avoid.
        for wanted in order:
            for name in names:
                if name == wanted or name == wanted + "-instruct":
                    return name
        # Then a tag of a family we know, whatever its size: somebody with
        # qwen3:14b installed wants that rather than nothing.
        families = {wanted.split(":")[0] for wanted in order}
        for name in names:
            if name.split(":")[0] in families:
                return name
        # And finally whatever is there. Somebody who pulled a model on
        # purpose meant to use it.
        return names[0]

    @property
    def available(self) -> bool:
        if not self.model:
            self.model = self.choose()
        return bool(self.model)

    # -- the work ----------------------------------------------------------
    def refine(self, text: str) -> Refined:
        text = (text or "").strip()
        if not text:
            return Refined(text, False, "nothing to correct")
        if not self.available:
            return Refined(text, False, "no local model installed")

        reply = self._post("/api/chat", {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"{NO_THINK}\n{text}"},
            ],
            "stream": False,
            "keep_alive": KEEP_ALIVE,
            "options": {
                # Correcting, not writing. The temperature is the first line
                # of defence and the guard is the second.
                "temperature": 0.1,
                "top_p": 0.9,
            },
        })
        candidate = strip_thinking(
            ((reply or {}).get("message") or {}).get("content", ""))
        if not candidate:
            return Refined(text, False, "nothing came back")

        ok, reason = acceptable(text, candidate)
        if not ok:
            return Refined(text, False, reason)
        return Refined(candidate, candidate != text)


def make(enabled: bool = True, model: str = "", host: str = HOST):
    """A refiner, or one that does nothing when there is no model to use."""
    if not enabled:
        return NoRefiner()
    refiner = OllamaRefiner(model, host)
    return refiner if refiner.available else NoRefiner()
