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

from app import config, wordio
from app.i18n import t
from engine import cloudstt, mic, polish
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
        self.last_result: Result | None = None

    def _forget_model_if_gone(self, note: str) -> None:
        """Drop the cached model name when Google says it is not there.

        Withdrawn names are the one failure that never recovers on its own:
        every later dictation would 404 against the same stale entry. A
        network blip, a rate limit or a refused reply is not this, and must
        not throw away a model that works.
        """
        if not any(sign in note for sign in ("404", "no longer available",
                                             "not found", "NOT_FOUND")):
            return
        settings = self.frame.settings
        if not settings.dictation_ai_model:
            return
        settings.dictation_ai_model = ""
        try:
            config.save(settings)
        except Exception:
            pass

    def _settle_model(self, settings) -> str:
        """The Gemini model to use, found once and remembered.

        Google withdraws model names, and its own list still advertises ones
        that answer "no longer available" or never answer at all - which is
        exactly how this came to report that Google could not be reached while
        the key was perfectly good. So the name is probed and then written back
        to settings, and nothing probes again until that one stops working.
        """
        if settings.dictation_ai != "auto" or not settings.dictation_ai_key:
            return settings.dictation_ai_model
        if settings.dictation_ai_model:
            return settings.dictation_ai_model
        try:
            found = cloudstt.discover(settings.dictation_ai_key)
        except Exception:
            return ""           # the check will say why; dictation falls back
        settings.dictation_ai_model = found
        try:
            config.save(settings)
        except Exception:
            pass                # remembering it is an optimisation, not a need
        return found

    def preload(self) -> None:
        """Load what dictation needs before it is asked for.

        Called from the startup warm-up thread. Without it the first press of
        the shortcut spends about four seconds loading the language model
        before it can say "start typing" - on the GUI thread, so the program
        looks frozen at the exact moment the user has started talking.
        """
        self._language_backend()
        self._settle_model(self.frame.settings)

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
            language=None if settings.language == "auto" else settings.language,
            hotwords=settings.hotwords,
            corrections=settings.dictation_corrections,
            polisher=polish.make(settings.dictation_ai,
                                 settings.dictation_ai_key,
                                 settings.dictation_ai_model),
            cloud=cloudstt.make(settings.dictation_ai,
                                settings.dictation_ai_key,
                                self._settle_model(settings)),
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
        # Said differently when the model changed something, so the user knows
        # the words in their document are not only the ones they spoke.
        session = self.session
        if session is not None and getattr(session, "fell_back", False):
            self._forget_model_if_gone(getattr(session, "cloud_note", ""))
            # Not silently: the user chose Google, and what they got was the
            # local model. They should know which one wrote their document.
            said = "say_dictation_fellback"
        elif session is not None and getattr(session, "polished", False):
            said = "say_dictation_repaired"
        else:
            said = "say_dictation_done"
        self.frame.say(t(said, words=len(result.text.split())), interrupt=True)
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
        """Nothing. There is no separate language-detection model any more.

        There was one - `tiny`, asked only "which language is this phrase",
        never asked for a word of text - and it is what let the language be
        decided per phrase so a sentence could change language halfway. It was
        removed at the user's instruction. The local engine now detects once
        per dictation with the model that is already loaded, and a dictation
        that mixes languages belongs on the Google engine, which hears the
        whole segment and decides for itself.
        """
        return None
