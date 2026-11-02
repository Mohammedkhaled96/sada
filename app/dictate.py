"""Voice typing: press once to speak, press again to have it written.

The recording is handed to the same job queue that transcribes files, and that
is most of this file's value. The queue, the progress, the estimated time, the
speed, the model name, the window that reports all of it, the cancel and the
insertion into Word already exist and are tested. A dictation that went down
its own path would be a second implementation of every one of them, and the
two would drift apart at the first change to either.

So this module does three things: it records, it refuses to hand over a
recording with nothing in it, and it says out loud what is happening. The
transcription itself is a job like any other.
"""
from __future__ import annotations

import threading
import time
import wave
from pathlib import Path

import numpy as np
import wx

from app.i18n import t
from app import mediapause, sendtext
from engine import applog, mic
from engine.paths import OUT_DIR

# Short, distinct, and not alarming. Rising to start, falling to stop.
TONE_START = (880, 90)
TONE_STOP = (523, 90)
TONE_ERROR = (220, 180)

# Nothing heard for this long, after starting, is worth saying so rather than
# letting the user talk into a dead microphone for a minute.
DEAF_AFTER_S = 5.0
# A dictation nobody stopped.
MAX_TOTAL_S = 600.0
# Below this peak over the whole recording, nothing was heard at all. Room
# tone on a working microphone measures about 0.003; a muted one, 0.00003.
SILENT_PEAK = 0.001
# Shorter than this is a key press, not a sentence.
MIN_LENGTH_S = 0.5


def tone(pitch_ms: tuple[int, int]) -> None:
    """A beep, off the calling thread so nothing waits for the sound card."""
    def play():
        try:
            import winsound

            winsound.Beep(pitch_ms[0], pitch_ms[1])
        except Exception:
            pass                       # a missing beep must never break anything

    threading.Thread(target=play, daemon=True).start()


def write_wav(audio: np.ndarray, path: Path,
              sample_rate: int = mic.SAMPLE_RATE) -> Path:
    """16-bit mono WAV - what the microphone gave us, losing nothing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = (np.clip(audio.reshape(-1), -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        out.writeframes(samples.tobytes())
    return path


class DictationController:
    """Owns the microphone while a dictation runs, and nothing else."""

    def __init__(self, frame):
        self.frame = frame
        self.recorder: mic.Recorder | None = None
        # Players paused for the length of this recording, so exactly those can
        # be put back and nothing else is started.
        self._paused_media: list[str] = []
        # The window the user was typing in when they pressed the shortcut.
        # The text goes back to that window, not to whatever is in front when
        # the transcription finishes several seconds later.
        self._target_hwnd = 0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._busy = False
        self._abandoned = False
        self._blocks: list[np.ndarray] = []

    # -- state -------------------------------------------------------------
    def preload(self) -> None:
        """Pay PortAudio's start-up cost now rather than in the first dictation.

        Importing sounddevice loads its own PortAudio build and enumerating the
        devices is what initialises the host APIs - on this machine eighteen
        entries for two microphones. Doing it here, on the warm-up thread,
        keeps it out of the moment the user presses the shortcut. No stream is
        opened: the device is not touched until a dictation actually starts.
        """
        mic.input_devices()

    @property
    def recording(self) -> bool:
        return self._busy and not self._stop.is_set()

    @property
    def active(self) -> bool:
        """Recording, or still closing the microphone and queueing the job.

        Kept apart from `recording` because the thread dies the moment the
        microphone closes while the job is still being handed over; treating
        that gap as idle let a second press open the microphone underneath the
        first dictation.
        """
        return self._busy

    # -- the toggle --------------------------------------------------------
    def toggle(self) -> None:
        if self.recording:
            self.stop()
        elif self._busy:
            tone(TONE_ERROR)            # still handing the last one over
        else:
            self.start()

    def start(self) -> None:
        settings = self.frame.settings
        if not self.frame.model_ready.is_set():
            self._refuse(t("say_loading"))
            return
        if self.frame.backend_error or self.frame.backend is None:
            self._refuse(t("say_load_failed", error=self.frame.backend_error or ""))
            return

        self._target_hwnd, window_class, process = sendtext.focused_window()

        if settings.dictation_pause_media:
            self._paused_media = mediapause.pause_playing()
            if self._paused_media:
                applog.write(f"paused {len(self._paused_media)} player(s): "
                             + ", ".join(self._paused_media), where="dictation")

        try:
            self.recorder = mic.Recorder(device=self._device_index(settings))
            self.recorder.start()
        except mic.MicError as exc:
            self.recorder = None
            # Opening the microphone failed: never leave the user's music
            # paused because of a dictation that did not happen.
            self._resume_media()
            applog.failed(f"opening the microphone "
                          f"{settings.dictation_device or '(system default)'}",
                          exc, where="dictation")
            self._refuse(t("say_dictation_mic_failed", error=exc))
            return

        self._blocks = []
        self._stop.clear()
        self._abandoned = False
        self._busy = True
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="dictation-mic")
        self._thread.start()

        tone(TONE_START)
        self.frame.say(t("say_dictation_start"), interrupt=True)
        self.frame.paint_tray()
        applog.write(
            f"recording from {settings.dictation_device or '(system default)'}"
            f" at {self.recorder.rate} Hz, for "
            f"{process or window_class or '?'}", where="dictation")

    def stop(self) -> None:
        if not self.recording:
            return
        self._stop.set()
        tone(TONE_STOP)
        self.frame.say(t("say_dictation_stop"), interrupt=True)

    def cancel(self) -> None:
        if not self._busy:
            return
        self._abandoned = True
        self._stop.set()
        tone(TONE_ERROR)

    # -- recording ---------------------------------------------------------
    def _run(self) -> None:
        recorder = self.recorder
        started = time.time()
        warned = False
        try:
            while not self._stop.is_set():
                block = recorder.read(timeout=0.1)
                if block.size:
                    self._blocks.append(block)
                elapsed = time.time() - started
                if not warned and elapsed > DEAF_AFTER_S and self._is_silent():
                    warned = True
                    wx.CallAfter(self._deaf)
                if elapsed > MAX_TOTAL_S:
                    break
            block = recorder.read(timeout=0.2)      # whatever arrived last
            if block.size:
                self._blocks.append(block)
        except Exception as exc:
            applog.failed("reading from the microphone", exc, where="dictation")
            wx.CallAfter(self.frame.say, t("say_error", error=exc), True)
        finally:
            recorder.stop()
        wx.CallAfter(self._hand_over)

    def _resume_media(self) -> None:
        """Put back exactly the players this dictation paused."""
        if self._paused_media:
            players, self._paused_media = self._paused_media, []
            mediapause.resume(players)

    def _is_silent(self) -> bool:
        if not self._blocks:
            return True
        return float(np.abs(np.concatenate(self._blocks)).max()) < SILENT_PEAK

    def _deaf(self) -> None:
        tone(TONE_ERROR)
        applog.write(f"nothing heard in the first {DEAF_AFTER_S:.0f}s - the "
                     f"microphone may be muted", where="dictation")
        self.frame.say(t("say_dictation_not_hearing"), interrupt=True)

    # -- handing it to the queue -------------------------------------------
    def _hand_over(self) -> None:
        """Queue the recording as a job, the same as a file the user picked."""
        self.frame.paint_tray()
        self._resume_media()
        self._thread = None
        self._busy = False
        blocks, self._blocks = self._blocks, []
        # Dropped blocks mean the machine could not keep up with the sound
        # card, which is audible as a gap and reads as a missing word.
        overflows = self.recorder.overflows if self.recorder else 0

        if self._abandoned:
            applog.write("cancelled by the user", where="dictation")
            return

        audio = np.concatenate(blocks) if blocks else np.empty(0, dtype=np.float32)
        peak = float(np.abs(audio).max()) if audio.size else 0.0
        rms = float(np.sqrt(np.mean(audio ** 2))) if audio.size else 0.0
        applog.write(f"stopped: {audio.size / mic.SAMPLE_RATE:.1f}s recorded, "
                     f"peak {peak:.4f}, rms {rms:.4f}, "
                     f"{overflows} dropped block(s)", where="dictation")
        if audio.size and float(np.abs(audio).max()) >= SILENT_PEAK:
            pass
        else:
            # Nothing was heard. Transcribing silence spends the user's time
            # and then tells them nothing was recognised, which is a worse way
            # of saying what can be said now.
            self._refuse(t("say_dictation_not_hearing"))
            return
        if len(audio) < MIN_LENGTH_S * mic.SAMPLE_RATE:
            self._refuse(t("say_dictation_too_short"))
            return

        try:
            path = write_wav(audio, OUT_DIR / time.strftime("dictation-%H-%M-%S.wav"))
        except Exception as exc:
            self._refuse(t("say_error", error=exc))
            return

        s = self.frame.settings
        busy_before = self.frame.jobs.busy
        job = self.frame.jobs.submit(
            path,
            language=s.language_or_none,
            # Timestamps belong on a recording somebody will scrub through.
            # Dictation is a sentence going into a document.
            timestamps=False,
            hotwords=s.hotwords or None,
            insert_mode=s.insert_mode,
            rtl_auto=s.rtl_auto,
            # Sentence-sized chunks, each free to be in its own language.
            # A file picked from disk is not treated this way - see
            # engine/job.py for why that distinction has to exist.
            dictation=True,
            # The names this user keeps having misheard, written once in
            # Settings instead of fixed by hand after every dictation.
            corrections=s.dictation_corrections,
            # Where it came from, and therefore where it goes back to.
            target_hwnd=self._target_hwnd,
        )
        # The window the user already knows from transcribing a file: name,
        # length, language, progress, time left, speed, model. It does not
        # take focus, so Word keeps the caret where the text will land.
        applog.write(f"queued job {job.id} as {path.name}", where="dictation")
        self.frame._last_spoken = 0.0
        self.frame.window.show_without_focus()
        self.frame.refresh_jobs()
        self.frame.say(
            t("say_queued", name=job.filename, n=self.frame.jobs.queued_count)
            if busy_before else
            t("say_dictation_writing",
              seconds=int(len(audio) / mic.SAMPLE_RATE)),
            interrupt=True)

    # -- helpers -----------------------------------------------------------
    def _refuse(self, message: str) -> None:
        tone(TONE_ERROR)
        applog.write(f"refused: {message}", where="dictation")
        self.frame.say(message, interrupt=True)

    def _device_index(self, settings) -> int | None:
        name = (settings.dictation_device or "").strip()
        if not name:
            return None
        for device in mic.input_devices():
            if str(device) == name or device.name == name:
                return device.index
        return None                 # the saved device is gone; use the default
