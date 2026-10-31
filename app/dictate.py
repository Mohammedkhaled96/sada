"""The dictation shortcut: press once to speak, press again to have it written.

This is the part that touches the microphone, the speaker and Word. The actual
pipeline is engine/dictation.py, which knows nothing about any of them and can
be tested from recorded files.

Two rules shape what happens here, and both come from docs/accessibility.md:
the text is inserted once, at the end, because moving the insertion point while
someone is reading drags their screen reader cursor away; and every state
change is both spoken and given a tone, because speech arrives late when the
screen reader is busy and a tone does not.
"""
from __future__ import annotations

import threading
import time

import numpy as np
import wx

from app import wordio
from app.i18n import t
from engine import mic
from engine.dictation import SAMPLE_RATE, Dictation, Result
from engine.textout import is_rtl

# Short, distinct, and not alarming. Rising to start, falling to stop.
TONE_START = (880, 90)
TONE_STOP = (523, 90)
TONE_DONE = (1047, 70)
TONE_ERROR = (220, 180)


def tone(pitch_ms: tuple[int, int]) -> None:
    """A beep, off the calling thread so nothing waits for the sound card."""
    def play():
        try:
            import winsound

            winsound.Beep(pitch_ms[0], pitch_ms[1])
        except Exception:
            pass                       # a missing beep must never break anything

    threading.Thread(target=play, daemon=True).start()


class DictationController:
    """Owns one dictation at a time, and the microphone while it runs."""

    def __init__(self, frame):
        self.frame = frame
        self.session: Dictation | None = None
        self.recorder: mic.Recorder | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._busy = False
        self._abandoned = False
        self._lid = None
        self._lid_failed = False
        self.last_result: Result | None = None

    def preload(self) -> None:
        """Load what dictation needs before it is asked for.

        Called from the startup warm-up thread. Without it the first press of
        the shortcut spends about four seconds loading the language model
        before it can say "start typing" - on the GUI thread, so the program
        looks frozen at the exact moment the user has started talking.
        """
        self._language_backend()

    @property
    def recording(self) -> bool:
        """The microphone is open and the user is talking."""
        return self._busy and not self._stop.is_set()

    @property
    def active(self) -> bool:
        """Anything at all is in flight - recording, or the last phrase still
        being written. Kept separate from `recording` because the thread dies
        the moment the microphone closes, while the text it produced is still
        on its way to Word; treating that gap as idle let a second press open
        the microphone again underneath the first dictation."""
        return self._busy

    # ------------------------------------------------------------ the toggle
    def toggle(self) -> None:
        if self.recording:
            self.stop()
        elif self._busy:
            tone(TONE_ERROR)            # still writing the last one
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
        # The two share one model and one set of window constants. Letting them
        # overlap would mean a file chunk holding the engine for half a minute
        # in the middle of a sentence.
        if self.frame.jobs.current_id is not None:
            self._refuse(t("say_dictation_busy"))
            return

        try:
            self.recorder = mic.Recorder(device=self._device_index(settings))
            self.recorder.start()
        except mic.MicError as exc:
            self.recorder = None
            self._refuse(t("say_dictation_mic_failed", error=exc))
            return

        self.session = Dictation(
            self.frame.backend,
            lid_backend=self._language_backend(),
            on_state=self._on_state,
            remove_fillers=settings.dictation_remove_fillers,
            language=None if settings.language == "auto" else settings.language,
            hotwords=settings.hotwords,
            corrections=settings.dictation_corrections,
        )
        self._stop.clear()
        self._abandoned = False
        self._busy = True
        self.session.start()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="dictation-mic")
        self._thread.start()

        tone(TONE_START)
        self.frame.say(t("say_dictation_start"), interrupt=True)
        self.frame.tray.set_state("tray_dictating")

    def stop(self) -> None:
        if not self.recording:
            return
        self._stop.set()
        tone(TONE_STOP)
        self.frame.say(t("say_dictation_stop"), interrupt=True)

    def cancel(self) -> None:
        if not self._busy:
            return
        if self.session is not None:
            self.session.cancel()
        self._abandoned = True
        self._stop.set()
        tone(TONE_ERROR)

    # ----------------------------------------------------------- the session
    def _run(self) -> None:
        """Read the microphone until stop, then wait for the text."""
        session, recorder = self.session, self.recorder
        started = time.time()
        warned_deaf = False
        try:
            while not self._stop.is_set():
                block = recorder.read(timeout=0.1)
                if block.size:
                    session.feed(block)
                elapsed = time.time() - started
                if (not warned_deaf and elapsed > 5.0
                        and not session.segmenter.heard_anything):
                    warned_deaf = True
                    wx.CallAfter(self._deaf)
                if elapsed > session.max_total_s:
                    break
            # Whatever arrived between the last read and the key press.
            block = recorder.read(timeout=0.2)
            if block.size:
                session.feed(block)
        except Exception as exc:
            wx.CallAfter(self.frame.say, t("say_error", error=exc), True)
        finally:
            recorder.stop()

        result = session.finish()
        self.last_result = result
        wx.CallAfter(self._deliver, result)

    def _on_state(self, name: str, **info) -> None:
        if name == "error":
            wx.CallAfter(self.frame.say, t("say_error", error=info.get("message", "")),
                         True)

    def _deaf(self) -> None:
        tone(TONE_ERROR)
        self.frame.say(t("say_dictation_not_hearing"), interrupt=True)

    # ------------------------------------------------------------- delivery
    def _deliver(self, result: Result) -> None:
        self._thread = None
        self._busy = False
        if self._abandoned:
            # Cancelled, or the program is closing. Announcing an empty result
            # on the way out would be noise, and the frame may already be gone.
            return
        self.frame.tray.set_state("tray_ready")

        if result.error:
            tone(TONE_ERROR)
            self.frame.say(t("say_error", error=result.error), interrupt=True)
            return
        if not result.text:
            tone(TONE_ERROR)
            self.frame.say(
                t("say_dictation_not_hearing") if not result.heard_anything
                else t("say_dictation_nothing"), interrupt=True)
            return

        settings = self.frame.settings
        rtl = is_rtl(result.language) if settings.rtl_auto else False
        try:
            wordio.init_thread()
            app = wordio.get_word()
            wordio.insert_transcript(app, result.text, rtl=rtl,
                                     mode=settings.insert_mode)
        except wordio.WordNotRunning as exc:
            tone(TONE_ERROR)
            self.frame.say(t("word_saved_instead", message=str(exc)), interrupt=True)
            self._offer_clipboard(result.text)
            return
        except Exception as exc:
            tone(TONE_ERROR)
            self.frame.say(t("word_insert_failed", error=exc), interrupt=True)
            self._offer_clipboard(result.text)
            return

        tone(TONE_DONE)
        self.frame.say(t("say_dictation_done", words=len(result.text.split())),
                       interrupt=True)
        try:
            wordio.get_word().Activate()
        except Exception:
            pass

    def _offer_clipboard(self, text: str) -> None:
        """Word was not there. Losing the words the user just said is not an
        acceptable outcome, so they go to the clipboard instead."""
        try:
            if wx.TheClipboard.Open():
                wx.TheClipboard.SetData(wx.TextDataObject(text))
                wx.TheClipboard.Close()
                self.frame.say(t("say_dictation_clipboard"), interrupt=True)
        except Exception:
            pass

    # -------------------------------------------------------------- helpers
    def _refuse(self, message: str) -> None:
        tone(TONE_ERROR)
        self.frame.say(message, interrupt=True)

    def _device_index(self, settings) -> int | None:
        name = (settings.dictation_device or "").strip()
        if not name:
            return None
        for device in mic.input_devices():
            if str(device) == name or device.name == name:
                return device.index
        return None                 # the saved device is gone; use the default

    def _language_backend(self):
        """`tiny` decides the language, when it is installed.

        Measured, it answers in 0.65 s where the large model takes about nine,
        and it answers as well. None means it is not here: the pipeline then
        detects once per dictation with the large model instead of once per
        segment, which costs mixing languages mid-sentence and keeps the
        feature fast enough to be worth having.
        """
        if self._lid is not None or self._lid_failed:
            return self._lid
        try:
            from engine.backends import load_backend
            from engine.models import LANGUAGE_DETECT, info

            if not info(LANGUAGE_DETECT).installed:
                raise FileNotFoundError(f"{LANGUAGE_DETECT} is not installed")
            self._lid = load_backend("openai", LANGUAGE_DETECT, self.frame.device)
        except Exception:
            self._lid_failed = True
            return None
        return self._lid
