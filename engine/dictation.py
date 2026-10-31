"""Dictation: transcribe while the user is still speaking.

The cost of one Whisper call is almost all fixed - see engine/window.py - so the
arithmetic that matters is how much speech each call carries. Sending every
phrase the moment it ends is the worst possible choice: ten two-second phrases
sent separately cost about 27 seconds of compute for 20 seconds of speech, and
the program falls behind the speaker and never catches up. Sending one call at
the end is cheapest in total but leaves the whole bill to be paid after the
user presses stop, which is exactly what made the previous attempt unusable.

So segments close at a pause once they are worth sending, and are transcribed
while the user carries on talking. Measured, that runs at about 0.28x realtime
against speech, so it cannot fall behind; and when stop is pressed only the
last phrase is outstanding, which is a second or three rather than eighteen.

Nothing here touches the GUI, Word, or the microphone API directly: it is fed
audio and reports state, so it can be tested from recorded files with no
microphone and no window on screen.
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from engine import cleanup, fillers, langmap, window
from engine.cloudstt import LocalOnly, script_of
from engine.polish import NullPolisher
from engine.window import short_window

SAMPLE_RATE = 16000

# A pause this long, after enough speech, ends a segment.
PAUSE_S = 0.7
# Below this much speech a pause is ignored: a two-second segment costs a full
# call, so several of them in a row is how the engine falls behind.
MIN_SEGMENT_S = 4.0
# And above this it is cut regardless, pause or not, to keep the window short
# and to stop one long sentence from being left to the end. The ceiling is the
# largest window engine/window.py will encode in, less its headroom - longer
# segments would fall back to the full 30 seconds and cost three times as much.
MAX_SEGMENT_S = float(window.MAX_SPEECH_S)
# How long to leave around the speech when a segment is cut.
KEEP_SILENCE_S = 0.3
# Nothing heard for this long, after starting, is worth telling the user about.
DEAF_AFTER_S = 5.0
# A dictation nobody stopped.
MAX_TOTAL_S = 300.0

# Whisper's own markers that a segment is not speech. Both are what it reports
# when it has decoded silence or run on into repetition.
NO_SPEECH_MAX = 0.6
COMPRESSION_MAX = 2.4
# A segment starting this close to the end of the audio, or past it, was not in
# the audio at all.
PAST_END_S = 0.2

# Whisper retries a segment it is unhappy with at a higher temperature, up to
# six times. On a file that is a cheap insurance against hallucination; here a
# segment cut mid-sentence - which is what a hard cut on someone who never
# pauses is - measured 56.6 s instead of 7.0 s, because no amount of retrying
# puts back a word that was cut in half. Two attempts, then take what is there
# and let the checks above throw it out if it is nonsense.
TEMPERATURES = (0.0, 0.2)

# When speech runs on past the limit, cut at the quietest moment near it
# rather than exactly on it, so the cut falls between words more often.
QUIET_SEARCH_S = 1.5
QUIET_STEP_S = 0.05
# Below this the language guess is not worth forcing on the model.
LANGUAGE_CONFIDENCE_MIN = 0.6
# A run of audio shorter than this holds no word worth a whole window.
MIN_RUN_AUDIO_S = 0.35
# How much of what has already been written is shown to the model as context.
# Enough to carry a name or a term across a pause, short enough that it cannot
# become something the decoder starts copying instead of listening.
PROMPT_TAIL_CHARS = 160


@dataclass
class Result:
    text: str = ""
    language: str = ""
    audio_seconds: float = 0.0
    compute_seconds: float = 0.0
    segments: int = 0
    dropped: int = 0
    heard_anything: bool = False
    error: str | None = None


class Segmenter:
    """Audio in, closed segments out.

    Voice activity comes from Silero, the same model engine/chunking.py splits
    files with, called on a short rolling window so the decision is about now
    rather than about the whole recording.
    """

    def __init__(self, pause_s: float = PAUSE_S,
                 min_segment_s: float = MIN_SEGMENT_S,
                 max_segment_s: float = MAX_SEGMENT_S):
        self.pause_s = pause_s
        self.min_segment_s = min_segment_s
        self.max_segment_s = max_segment_s

        self._buf = np.empty(0, dtype=np.float32)
        self._speech_s = 0.0
        self._silence_s = 0.0
        self._vad_tail = np.empty(0, dtype=np.float32)
        self._model = None
        self.heard_anything = False

    # -- voice activity ----------------------------------------------------
    def _vad(self):
        if self._model is None:
            from faster_whisper.vad import get_vad_model

            self._model = get_vad_model()
        return self._model

    def has_voice(self, block: np.ndarray) -> bool:
        """Is someone speaking in the last moment of audio?

        Silero resets its state on every call, so it is given a second of
        context rather than only the new block - on 100 ms alone it misses the
        quiet start of a word and the segmenter cuts in the middle of it.
        """
        self._vad_tail = np.concatenate([self._vad_tail, block])[-SAMPLE_RATE:]
        usable = (len(self._vad_tail) // 512) * 512
        if usable < 512:
            return False
        try:
            probs = self._vad()(self._vad_tail[-usable:])
        except Exception:
            # Without VAD, fall back to level: better a crude cut than none.
            return float(np.abs(block).max()) > 0.02
        recent = np.asarray(probs).reshape(-1)[-max(1, len(block) // 512):]
        return bool(recent.max() > 0.5)

    # -- feeding -----------------------------------------------------------
    def feed(self, block: np.ndarray) -> list[np.ndarray]:
        """Add audio; return whatever segments that completed."""
        if block.size == 0:
            return []
        block = block.astype(np.float32).reshape(-1)
        seconds = len(block) / SAMPLE_RATE
        self._buf = np.concatenate([self._buf, block])

        if self.has_voice(block):
            self._speech_s += seconds
            self._silence_s = 0.0
            self.heard_anything = True
        else:
            self._silence_s += seconds
            if self._speech_s == 0.0:
                # Still nothing but silence: keep a little for the start of the
                # first word and throw the rest away, so a long wait before
                # speaking does not become a long segment of nothing.
                keep = int(KEEP_SILENCE_S * SAMPLE_RATE)
                if len(self._buf) > keep:
                    self._buf = self._buf[-keep:]
                return []

        closed = []
        # On the length of the audio, not of the speech in it. The window has
        # to hold the whole segment including the pauses inside it, and a
        # segment one second over the limit falls back to the full 30-second
        # window - which costs about eighteen seconds instead of seven, and is
        # how this first failed to keep up with the speaker.
        if len(self._buf) / SAMPLE_RATE >= self.max_segment_s:
            closed.append(self._cut())
        elif (self._silence_s >= self.pause_s
                and self._speech_s >= self.min_segment_s):
            closed.append(self._cut())
        return [c for c in closed if c is not None]

    def flush(self) -> np.ndarray | None:
        """The tail, when dictation stops."""
        return self._cut() if self._speech_s > 0 else None

    def _quiet_point(self, end: int) -> int:
        """The quietest instant in the last moment before `end`.

        A cut that lands inside a word gives the decoder half a word to make
        sense of, and it answers by retrying at ever higher temperatures - the
        single most expensive thing that can happen to a segment. Cutting at
        the dip between two words costs one pass over a second of samples.
        """
        window_n = int(QUIET_SEARCH_S * SAMPLE_RATE)
        step = int(QUIET_STEP_S * SAMPLE_RATE)
        start = max(step, end - window_n)
        if end - start < step * 2:
            return end
        tail = np.abs(self._buf[start:end])
        usable = (len(tail) // step) * step
        if usable < step * 2:
            return end
        energy = tail[:usable].reshape(-1, step).mean(axis=1)
        return start + int(np.argmin(energy)) * step + step

    def _cut(self) -> np.ndarray | None:
        if self._speech_s <= 0 or self._buf.size == 0:
            return None
        keep = int(KEEP_SILENCE_S * SAMPLE_RATE)
        trailing = int(self._silence_s * SAMPLE_RATE)
        end = len(self._buf) - max(0, trailing - keep)
        if self._silence_s < self.pause_s:
            # Still mid-speech, so this is the length limit firing rather than
            # a pause. Look for somewhere quieter to break.
            end = self._quiet_point(end)
        segment = self._buf[:max(end, keep)].copy()
        # Never hand out more than a short window can hold. Reaching here means
        # the cut happened late - a long trailing pause, say - and silently
        # paying for the full window would be worse than losing the silence.
        ceiling = int(window.MAX_SPEECH_S * SAMPLE_RATE)
        if len(segment) > ceiling:
            segment = segment[:ceiling]

        self._buf = np.empty(0, dtype=np.float32)
        self._speech_s = 0.0
        self._silence_s = 0.0
        return segment

    @property
    def pending_speech_s(self) -> float:
        return self._speech_s


class Dictation:
    """One dictation session.

    `on_state(name, **info)` is called from worker threads with: listening,
    not_hearing, working, done, error.
    """

    def __init__(self, backend, lid_backend=None, on_state=None,
                 remove_fillers: bool = True,
                 language: str | None = None,
                 hotwords: str | None = None,
                 corrections: str | None = None,
                 polisher=None,
                 cloud=None,
                 max_total_s: float = MAX_TOTAL_S):
        self.backend = backend
        # Whether language detection is cheap enough to do per segment.
        self.cheap_detection = lid_backend is not None
        self.lid_backend = lid_backend or backend
        self.on_state = on_state or (lambda name, **kw: None)
        self.remove_fillers = remove_fillers
        self.forced_language = language or None
        self.hotwords = (hotwords or "").strip() or None
        self.corrections = cleanup.parse_corrections(corrections or "")
        self.polisher = polisher or NullPolisher()
        self.cloud = cloud or LocalOnly()
        self.fell_back = False
        self._written = ""
        self.polished = False
        self.polish_note = ""
        self.cloud_note = ""
        self.max_total_s = max_total_s

        self.segmenter = Segmenter()
        self._work: "queue.Queue[tuple[int, np.ndarray] | None]" = queue.Queue()
        self._texts: dict[int, str] = {}
        self._languages: list[str] = []
        self._next = 0
        self._dropped = 0
        self._compute_s = 0.0
        self._audio_s = 0.0
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._cancelled = threading.Event()
        self._error: str | None = None
        self.started_at = 0.0

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self.started_at = time.time()
        self._worker = threading.Thread(target=self._run, daemon=True,
                                        name="dictation-transcriber")
        self._worker.start()
        self.on_state("listening")

    def feed(self, block: np.ndarray) -> None:
        """Hand over audio as it arrives from the microphone, or from a file."""
        if self._cancelled.is_set():
            return
        self._audio_s += len(block) / SAMPLE_RATE
        for segment in self.segmenter.feed(block):
            self._submit(segment)

    def cancel(self) -> None:
        self._cancelled.set()
        self._work.put(None)

    def finish(self) -> Result:
        """Flush the tail, wait for the queue, and assemble the text."""
        tail = self.segmenter.flush()
        if tail is not None and not self._cancelled.is_set():
            self._submit(tail)
        self._work.put(None)
        if self._worker is not None:
            self._worker.join(timeout=120)

        with self._lock:
            parts = [self._texts[i] for i in sorted(self._texts) if self._texts[i]]
            language = _majority(self._languages)
            text = " ".join(parts).strip()

        # Outside the lock: this one can reach the network, and holding the
        # lock across it would block every worker thread behind it.
        polished = self._polish(text, language)

        with self._lock:
            # The user's own replacements go on last, over the whole text, so
            # one that spans a segment boundary still matches - and so that a
            # replacement they wrote always beats whatever the model decided.
            text = cleanup.apply_corrections(polished, self.corrections)
            result = Result(
                text=text,
                language=language,
                audio_seconds=round(self._audio_s, 2),
                compute_seconds=round(self._compute_s, 2),
                segments=len(parts),
                dropped=self._dropped,
                heard_anything=self.segmenter.heard_anything,
                error=self._error,
            )
        self.on_state("done", result=result)
        return result

    def _polish(self, text: str, language: str) -> str:
        """Hand the finished text to the language model, if one is turned on.

        Never fatal. A dictation that could not be repaired is a dictation
        written exactly as it was heard, which is the behaviour with this
        turned off - the state it ships in.
        """
        if not text or not getattr(self.polisher, "available", False):
            return text
        try:
            result = self.polisher.polish(text, language)
        except Exception as exc:
            self.polish_note = str(exc)
            return text
        self.polish_note = result.reason
        self.polished = result.changed
        return result.text

    # -- the work ----------------------------------------------------------
    def _submit(self, segment: np.ndarray) -> None:
        with self._lock:
            index = self._next
            self._next += 1
        self.on_state("working", pending=self._work.qsize() + 1)
        self._work.put((index, segment))

    def _run(self) -> None:
        while True:
            item = self._work.get()
            if item is None:
                break
            index, segment = item
            if self._cancelled.is_set():
                continue
            try:
                text, language = self._transcribe(segment)
            except Exception as exc:
                self._error = str(exc)
                self.on_state("error", message=str(exc))
                continue
            with self._lock:
                self._texts[index] = text
                if language:
                    self._languages.append(language)

    def _transcribe(self, segment: np.ndarray) -> tuple[str, str]:
        """One segment, which may be in more than one language."""
        started = time.time()

        if getattr(self.cloud, "available", False):
            text = self._transcribe_cloud(segment)
            if text is not None:
                self._compute_s += time.time() - started
                return text, script_of(text)
            # Fell through: the local model writes it instead, below, rather
            # than the user losing what they just said to a network error.

        if self.forced_language:
            runs = [langmap.Run(0, len(segment), self.forced_language)]
        else:
            # Whisper writes a whole call in one language, transliterating
            # anything else into its script. A sentence that changes language
            # has to be split before it is handed over, not after.
            runs = langmap.split(segment, self.lid_backend if self.cheap_detection
                                 else None, fallback=self._last_language())
            if not self.cheap_detection and runs[0].language is None:
                runs[0].language = self._detect(segment)

        texts = []
        for run in runs:
            text = self._transcribe_run(segment[run.start:run.end], run.language)
            if text:
                texts.append(text)

        self._compute_s += time.time() - started
        return " ".join(texts).strip(), langmap.majority(runs)

    def _transcribe_cloud(self, segment: np.ndarray) -> str | None:
        """Send the audio itself. None means fall back to the local model.

        No language splitting and no detection pass: one model hearing the
        whole segment decides its own languages, which is the arrangement
        engine/langmap.py exists to imitate when it cannot.
        """
        try:
            text = self.cloud.transcribe(segment, self.forced_language)
        except Exception as exc:
            self.fell_back = True
            self.cloud_note = str(exc)
            self.on_state("fallback", message=str(exc))
            return None
        if self.remove_fillers and text:
            if fillers.is_all_filler(text):
                self._dropped += 1
                return ""
            text = fillers.strip_fillers(text)
        return cleanup.tidy(text)

    def _transcribe_run(self, audio: np.ndarray, language: str | None) -> str:
        """One stretch of audio known to be in a single language."""
        seconds = len(audio) / SAMPLE_RATE
        if seconds < MIN_RUN_AUDIO_S:
            return ""

        opts = {"condition_on_previous_text": False,
                "temperature": TEMPERATURES}
        prompt = self._prompt()
        if prompt:
            opts["initial_prompt"] = prompt

        pieces: list[str] = []
        with short_window(self.backend.model if hasattr(self.backend, "model")
                          else None, seconds):
            for row in self.backend.stream_segments(audio, language, opts):
                text = (row.get("text") or "").strip()
                if not text:
                    continue
                if (row.get("no_speech_prob", 0.0) > NO_SPEECH_MAX
                        or row.get("compression_ratio", 0.0) > COMPRESSION_MAX):
                    # Whisper's own signal that it decoded silence, or ran on
                    # into repetition. Keeping it would paste nonsense into a
                    # document the user cannot see.
                    self._dropped += 1
                    continue
                if row.get("start", 0.0) >= seconds - PAST_END_S:
                    # A segment that begins after the audio ended is invented.
                    # This is how Whisper's trailing-silence hallucinations
                    # arrive - a 25-second clip produced "ترجمة نانسي قنقر",
                    # a subtitle credit, timed at 25 to 55 seconds - and
                    # neither of the checks above sees them: that one scored a
                    # perfectly ordinary 0.77 compression and 0.00 no-speech.
                    self._dropped += 1
                    continue
                pieces.append(text)

        joined = " ".join(pieces).strip()
        if not joined:
            return ""
        with self._lock:
            self._written = (self._written + " " + joined)[-PROMPT_TAIL_CHARS:]
        if self.remove_fillers:
            if fillers.is_all_filler(joined, language):
                self._dropped += 1
                return ""
            joined = fillers.strip_fillers(joined, language)
        return cleanup.tidy(joined, language)

    def _prompt(self) -> str:
        """Terms the model should already have in mind.

        The user's names and jargon, then the end of what has been written so
        far. Segments are decoded independently - they have to be, or an error
        in one would be carried into every one after it - and that costs the
        continuity a human transcriber has: a name spelled one way before the
        pause and another way after it. This gives that back without giving up
        the independence, because it is only ever a hint.
        """
        with self._lock:
            tail = self._written.strip()
        parts = [p for p in (self.hotwords, tail) if p]
        return " ".join(parts)[-PROMPT_TAIL_CHARS * 2:]

    def _detect(self, segment: np.ndarray) -> str | None:
        """Which language this segment is in, decided as cheaply as possible.

        Per segment, not per session: forcing one language on a whole dictation
        is what previously wrote English sentences in Arabic letters. That is
        only affordable on a small model - measured, `tiny` answers this in
        0.65 s where the large model takes about nine, which is the difference
        between keeping up with speech and falling behind it.

        Without the small model, detection happens once and is then reused.
        Mixing languages mid-dictation stops working, and that is the right
        way round: a user who has not installed it gets a slower, simpler
        feature rather than one that falls further behind the longer they talk.
        """
        last = self._last_language()
        if not self.cheap_detection and last:
            return last
        try:
            language, confidence, _ = self.lid_backend.detect_language(audio=segment)
        except Exception:
            return last
        if confidence < LANGUAGE_CONFIDENCE_MIN:
            return last
        return language

    def _last_language(self) -> str | None:
        with self._lock:
            return self._languages[-1] if self._languages else None


def _majority(languages: list[str]) -> str:
    if not languages:
        return ""
    counts: dict[str, int] = {}
    for language in languages:
        counts[language] = counts.get(language, 0) + 1
    return max(counts, key=lambda k: (counts[k], k))


def transcribe_stream(dictation: Dictation, audio: np.ndarray,
                      block_s: float = 0.1) -> Result:
    """Feed a recording through as though it were arriving live.

    This is how the pipeline is tested without a microphone.
    """
    dictation.start()
    step = int(block_s * SAMPLE_RATE)
    for start in range(0, len(audio), step):
        dictation.feed(audio[start:start + step])
    return dictation.finish()
