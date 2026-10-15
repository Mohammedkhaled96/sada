"""Live dictation: speak, and the words arrive as you finish each phrase.

Whisper is not a streaming model. It takes a clip and returns text for that
clip; there is no way to feed it a continuous microphone and read words out as
they are spoken. Pretending otherwise would mean transcribing the same growing
buffer over and over, which is slow, and which rewrites words the listener has
already been told - the worst possible behaviour for someone who cannot see the
screen and is being read the result.

So dictation is **phrase at a time**. Audio accumulates, Silero VAD watches for
the pause that ends a phrase, and that phrase alone is transcribed and handed
over. The delay a speaker feels is the pause threshold plus inference on a few
seconds of audio, not on everything said so far - and crucially, text once
delivered is never revised.

Two threads, because they must not wait for each other: one reads the
microphone and cuts phrases, one transcribes. If transcription ran on the
capture thread, every word spoken during inference would be dropped.
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from engine.audio import SAMPLE_RATE
from engine.fillers import clean
from engine.job import DECODE_OPTS
from engine.mic import Microphone, MicrophoneError

# Decoding options for a dictated phrase, starting from the file-transcription
# ones so there is one place where accuracy is tuned - see
# docs/accuracy-notes.md - and differing only where dictation differs:
#
#   word_timestamps        off. Dictation discards timestamps, and producing
#                          them runs a DTW alignment pass per phrase, which is
#                          latency spent on an output nobody reads.
#   hallucination_silence  dropped. openai-whisper only consults it inside its
#                          word-timestamp path, so with those off it is inert,
#                          and leaving it set would suggest a protection that
#                          is not actually running. Phrases are VAD-trimmed to
#                          speech before they get here, which is what that
#                          option was defending against on long files.
DICTATION_OPTS = {
    **{k: v for k, v in DECODE_OPTS.items()
       if k not in ("word_timestamps", "hallucination_silence_threshold")},
    "word_timestamps": False,
}

# How much speech to gather before asking which language it is. Below this a
# detection is a guess, and a wrong guess would hold for the whole session.
MIN_DETECT_S = 1.5

# VAD settings for finding the end of a phrase. Deliberately not the ones in
# engine/chunking.py: those pad each speech region by 200 ms to protect a word
# from being clipped, which here would inflate every measurement of how long
# the speaker has been quiet. Padding is applied once, later, when the phrase
# is cut.
SEG_VAD = dict(
    threshold=0.5,
    min_speech_duration_ms=120,      # a short word is still a word
    max_speech_duration_s=30,
    min_silence_duration_ms=100,     # measure pauses, do not smooth them away
    speech_pad_ms=0,
)


@dataclass
class DictationConfig:
    """How speech is cut into phrases, and why the numbers are what they are.

    The first version of this cut at every 600 ms pause, which turned out to
    be the worst possible strategy, for a reason that is not obvious: Whisper
    pads every clip to a 30-second window before encoding it, so **a two
    second phrase costs about as much as a twenty second one**. Measured on
    this project's CPU reference machine with large-v3-turbo:

        2 s of audio   12.6 s   0.16x real time
        5 s             9.9 s   0.50x
       10 s            11.6 s   0.86x
       20 s            21.9 s   0.91x

    Cutting at every short pause therefore maximised the number of expensive
    calls, the queue fell further behind the speaker with every phrase, and
    pressing stop meant waiting for the whole backlog. Longer phrases are both
    faster overall and fewer.

    So a short pause only ends a phrase once `target_phrase_s` of audio has
    been gathered. Before that, only a deliberate `long_pause_ms` silence ends
    it - which is what happens when someone says one sentence and waits, so a
    short utterance still comes back quickly.

    `max_phrase_s` stays under 30 s on purpose: beyond that Whisper needs a
    second window, and the cost steps up.
    """
    pause_ms: int = 600             # ends a phrase once the target is reached
    long_pause_ms: int = 1400       # ends a phrase at any length
    target_phrase_s: float = 12.0   # gather at least this before a short pause counts
    min_speech_ms: int = 250        # below this it is a cough, not a phrase
    max_phrase_s: float = 25.0      # cut anyway, and stay inside one 30 s window
    tail_window_s: float = 2.0      # how much audio a pause is measured over
    pad_ms: int = 200               # kept around the speech when cutting
    language: str | None = None     # None identifies the language per phrase
    language_confidence: float = 0.5    # below this, let Whisper decide instead
    remove_hesitations: bool = True


def _silero_speech(audio: np.ndarray,
                   sampling_rate: int = SAMPLE_RATE) -> list[dict] | None:
    """Speech spans as [{'start': samples, 'end': samples}], or None if Silero
    is unavailable, so the caller can fall back rather than hear silence."""
    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        return get_speech_timestamps(audio, VadOptions(**SEG_VAD),
                                     sampling_rate=sampling_rate)
    except Exception:
        return None


def _energy_speech(audio: np.ndarray,
                   sampling_rate: int = SAMPLE_RATE) -> list[dict]:
    """Fallback speech detection from signal energy alone.

    Only used when Silero cannot be loaded. It is genuinely worse - a fan or a
    hum reads as speech - but a dictation feature that silently never produces
    a word because a VAD import failed is worse still.
    """
    frame = max(1, sampling_rate // 100)              # 10 ms
    frames = audio[: len(audio) // frame * frame].reshape(-1, frame)
    if not len(frames):
        return []
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    # Noise floor from the quietest tenth, so a noisy room raises the bar
    # instead of reading as continuous speech.
    floor = float(np.quantile(rms, 0.1))
    threshold = max(floor * 3.0, 0.01)
    loud = rms > threshold

    spans: list[dict] = []
    start = None
    for i, is_loud in enumerate(loud):
        if is_loud and start is None:
            start = i
        elif not is_loud and start is not None:
            spans.append({"start": start * frame, "end": i * frame})
            start = None
    if start is not None:
        spans.append({"start": start * frame, "end": len(loud) * frame})
    return spans


class UtteranceSegmenter:
    """Accumulates audio blocks and hands back complete phrases.

    Pure logic, no threads and no microphone, so the cutting rules can be
    tested exactly. `speech_fn` is the only thing a test has to replace.
    """

    def __init__(self, config: DictationConfig | None = None,
                 speech_fn=None, sampling_rate: int = SAMPLE_RATE):
        self.cfg = config or DictationConfig()
        self.sampling_rate = sampling_rate
        self._speech_fn = speech_fn
        self._silero_failed = False
        self._buf = np.empty(0, dtype=np.float32)

    # ------------------------------------------------------------------ speech
    def speech_regions(self, audio: np.ndarray) -> list[dict]:
        if self._speech_fn is not None:
            return self._speech_fn(audio, self.sampling_rate)
        if not self._silero_failed:
            regions = _silero_speech(audio, self.sampling_rate)
            if regions is not None:
                return regions
            self._silero_failed = True      # fall back from here on
        return _energy_speech(audio, self.sampling_rate)

    @property
    def vad_unavailable(self) -> bool:
        """Whether this segmenter has fallen back to energy detection."""
        return self._silero_failed

    # ------------------------------------------------------------------ timing
    def _samples(self, ms: float) -> int:
        return int(ms * self.sampling_rate / 1000)

    def _trailing_silence(self, window: np.ndarray) -> int:
        regions = self.speech_regions(window)
        if not regions:
            return len(window)
        return max(0, len(window) - int(regions[-1]["end"]))

    # -------------------------------------------------------------------- feed
    def feed(self, block: np.ndarray) -> list[np.ndarray]:
        """Add a block of audio. Returns any phrases that just completed."""
        if block is None or not len(block):
            return []
        self._buf = np.concatenate([self._buf, block])

        short = self._samples(self.cfg.pause_ms)
        long = self._samples(self.cfg.long_pause_ms)
        target = int(self.cfg.target_phrase_s * self.sampling_rate)
        window_len = self._samples(self.cfg.tail_window_s * 1000)
        window = self._buf[-window_len:] if len(self._buf) > window_len else self._buf

        # Force a cut on a speaker who has not paused. Without this, dictating
        # a long sentence in one breath would hold every word back until the
        # end of it - and would eventually cross the 30 second window.
        if len(self._buf) >= self.cfg.max_phrase_s * self.sampling_rate:
            phrase = self._cut()
            return [phrase] if phrase is not None else []

        if len(self._buf) < short:
            return []

        silence = self._trailing_silence(window)
        # A short pause only ends a phrase once there is enough audio to be
        # worth a call; before that it takes a deliberate silence. See the
        # measurements in DictationConfig for why.
        needed = short if len(self._buf) >= target else long
        if silence < needed:
            return []                       # still talking, or only pausing

        phrase = self._cut()
        if phrase is not None:
            return [phrase]

        # Nothing but silence so far. Keep only enough to measure the next
        # pause over, so waiting at an open microphone cannot grow without end.
        self._buf = self._buf[-window_len:] if len(self._buf) > window_len else self._buf
        return []

    def flush(self) -> np.ndarray | None:
        """The phrase still in hand when recording stops.

        Called on stop, so the last thing said is not thrown away for the
        want of a closing pause.
        """
        return self._cut()

    # --------------------------------------------------------------------- cut
    def _cut(self) -> np.ndarray | None:
        """Trim the buffer to its speech and return it, or None if there is
        nothing worth transcribing. Always leaves the buffer empty."""
        buf, self._buf = self._buf, np.empty(0, dtype=np.float32)
        if not len(buf):
            return None

        regions = self.speech_regions(buf)
        if not regions:
            return None
        speech = sum(int(r["end"]) - int(r["start"]) for r in regions)
        if speech < self._samples(self.cfg.min_speech_ms):
            return None                     # a cough, a click, a door

        pad = self._samples(self.cfg.pad_ms)
        start = max(0, int(regions[0]["start"]) - pad)
        end = min(len(buf), int(regions[-1]["end"]) + pad)
        clip = buf[start:end]
        return clip if len(clip) else None


@dataclass
class DictationState:
    """What the session is doing, for the interface to show and speak."""
    running: bool = False
    language: str = ""
    phrases: int = 0
    words: int = 0
    removed: list[str] = field(default_factory=list)
    error: str = ""


class Dictation:
    """One dictation session.

    `on_text(text)` is called once per phrase, in order, from the worker
    thread. Text handed over is final - a phrase is never corrected later, so
    a caller can insert it into a document or speak it immediately.

    `on_error(message)` is called if the session has to stop on its own, which
    in practice means the microphone failed.
    """

    def __init__(self, backend, *, config: DictationConfig | None = None,
                 on_text=None, on_error=None, on_state=None,
                 mic_factory=None, segmenter=None, lid=None):
        self.backend = backend
        # A small model used only to name the language of each phrase. See
        # _resolve_language for the measurements behind this.
        self.lid = lid
        self.cfg = config or DictationConfig()
        self._on_text = on_text
        self._on_error = on_error
        self._on_state = on_state
        self._mic_factory = mic_factory or (lambda: Microphone())
        self.segmenter = segmenter or UtteranceSegmenter(self.cfg)

        self.state = DictationState()
        self._phrases: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._mic = None
        self._capture: threading.Thread | None = None
        self._worker: threading.Thread | None = None
        self._language = self.cfg.language
        self._sample: np.ndarray | None = None      # audio gathered to detect on

    # ------------------------------------------------------------------ public
    @property
    def running(self) -> bool:
        return self.state.running

    @property
    def pending(self) -> int:
        """Phrases captured but not yet written. Pressing stop waits for
        these, so the interface can say how many there are instead of going
        quiet for an unexplained stretch."""
        return self._phrases.qsize()

    def start(self) -> None:
        if self.state.running:
            return
        self._stop.clear()
        self.state = DictationState(running=True, language=self._language or "")
        self._mic = self._mic_factory()
        try:
            self._mic.start()
        except MicrophoneError as exc:
            self.state.running = False
            self.state.error = str(exc)
            self._notify()
            raise

        self._capture = threading.Thread(target=self._capture_loop, daemon=True,
                                         name="dictation-capture")
        self._worker = threading.Thread(target=self._worker_loop, daemon=True,
                                        name="dictation-worker")
        self._capture.start()
        self._worker.start()
        self._notify()

    def stop(self, timeout: float = 20.0) -> None:
        """Stop recording and wait for the phrases already captured.

        It waits on purpose: the speaker's last sentence is still in the queue
        when they press the key, and dropping it to return a few hundred
        milliseconds sooner would lose words they said.
        """
        if not self.state.running and self._capture is None:
            return
        self._stop.set()
        if self._mic is not None:
            self._mic.stop()
        deadline = time.monotonic() + timeout
        for thread in (self._capture, self._worker):
            if thread is not None:
                thread.join(timeout=max(0.1, deadline - time.monotonic()))
        self._capture = self._worker = None
        self._mic = None
        self.state.running = False
        self._notify()

    # ------------------------------------------------------------------ inside
    def _notify(self) -> None:
        if self._on_state is not None:
            try:
                self._on_state(self.state)
            except Exception:
                pass

    def _fail(self, message: str) -> None:
        self.state.error = message
        self.state.running = False
        self._notify()
        if self._on_error is not None:
            try:
                self._on_error(message)
            except Exception:
                pass

    def _capture_loop(self) -> None:
        try:
            while not self._stop.is_set():
                block = self._mic.read(timeout=0.25)
                if block is None:
                    break                   # microphone closed
                for phrase in self.segmenter.feed(block):
                    self._phrases.put(phrase)
        except MicrophoneError as exc:
            self._fail(str(exc))
        except Exception as exc:
            self._fail(f"Dictation stopped: {exc}")
        finally:
            # Whatever was being said when stop was pressed.
            try:
                tail = self.segmenter.flush()
                if tail is not None:
                    self._phrases.put(tail)
            except Exception:
                pass
            self._phrases.put(None)         # no more phrases

    def _worker_loop(self) -> None:
        while True:
            phrase = self._phrases.get()
            if phrase is None:
                return
            try:
                text = self._transcribe(phrase)
            except Exception as exc:
                self._fail(f"Dictation could not transcribe: {exc}")
                continue
            if not text:
                continue
            self.state.phrases += 1
            self.state.words += len(text.split())
            self._notify()
            if self._on_text is not None:
                try:
                    self._on_text(text)
                except Exception:
                    pass                    # a failed insert must not kill the session

    def _resolve_language(self, clip: np.ndarray) -> str:
        """Identify the language of *this* phrase.

        The previous version settled on one language for the whole session and
        forced it on every phrase. For a speaker who mixes Arabic and English -
        which is most people dictating technical work - that is actively
        destructive: forced to Arabic, Whisper writes English words in Arabic
        letters, and the result was unusable.

        Letting Whisper detect on its own instead is correct but expensive,
        because it costs a second encoder pass over the padded 30-second
        window. Measured on the reference machine, for a 10 second phrase:

            large-v3-turbo   with a language 9.9 s    detecting 23.4 s
            small            with a language 7.0 s    detecting 11.4 s

        So detection is done by a separate, much smaller model whose only job
        is to name the language. That is a task a small model is good at, and
        it is cheap - measured on the same clip:

            tiny    detect_language 0.20 s   -> ar 0.96
            small   detect_language 2.58 s   -> ar 0.90

        tiny is both faster and, here, more confident than small. So with a
        `lid` model supplied, every phrase gets its own language for 0.2 s,
        and mixed speech works.

        Without one, we fall back to asking the transcription model once and
        reusing the answer, which is cheap but cannot follow a speaker who
        switches language.
        """
        if self.cfg.language:
            return self.cfg.language

        if self.lid is not None:
            try:
                language, confidence, _ranked = self.lid.detect_language(audio=clip)
            except Exception:
                return ""
            if confidence < self.cfg.language_confidence:
                # An unclear phrase: let Whisper decide for this one rather
                # than stamping a guess on it.
                return ""
            if language != self.state.language:
                self.state.language = language
                self._notify()
            return language

        # No identifier model: settle once and reuse it.
        if self._language:
            return self._language
        self._sample = (clip if self._sample is None
                        else np.concatenate([self._sample, clip]))
        if len(self._sample) < MIN_DETECT_S * SAMPLE_RATE:
            return ""
        try:
            language, _confidence, _ranked = self.backend.detect_language(
                audio=self._sample)
        except Exception:
            return ""
        finally:
            self._sample = None
        self._language = language
        self.state.language = language
        self._notify()
        return language

    def _transcribe(self, clip: np.ndarray) -> str:
        language = self._resolve_language(clip)
        parts = [
            (segment.get("text") or "").strip()
            for segment in self.backend.stream_segments(
                clip, language or None, dict(DICTATION_OPTS))
        ]
        raw = " ".join(p for p in parts if p)
        text, removed = clean(raw, remove_hesitations=self.cfg.remove_hesitations)
        if removed:
            self.state.removed.extend(removed)
            del self.state.removed[:-50]
        return text
