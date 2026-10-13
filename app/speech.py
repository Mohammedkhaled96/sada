"""Speaking to the user's screen reader.

Order of preference, because the right answer depends on what is running:

1. **JAWS** exposes a COM object, so `JawsApi.SayString` needs no extra files.
2. **NVDA** exposes `nvdaControllerClient64.dll`. NVDA does not install that DLL
   system-wide, so we only use it when we find one - shipped in `bin/` or sitting
   next to an NVDA installation.
3. **SAPI** as a last resort, and off by default: if a screen reader is already
   talking, a second voice on top of it is worse than silence.

If none of these work the app is still fully usable, because the UI is built
from native controls that the screen reader reads on its own. Explicit speech is
an improvement, never the only channel - see docs/accessibility.md.
"""
from __future__ import annotations

import ctypes
import os
import threading
from pathlib import Path

from app.i18n import t

_lock = threading.Lock()


def _candidate_nvda_dlls() -> list[Path]:
    names = ["nvdaControllerClient64.dll", "nvdaControllerClient.dll"]
    roots = [
        Path(__file__).resolve().parent.parent / "bin",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "NVDA",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "NVDA",
        Path(os.environ.get("APPDATA", "")) / "nvda",
    ]
    return [r / n for r in roots for n in names]


class Speaker:
    """Finds a channel once and reuses it. Thread safe: progress announcements
    come from the worker thread while the UI may announce from the main one."""

    def __init__(self, enabled: bool = True, allow_sapi: bool = False):
        self.enabled = enabled
        self.allow_sapi = allow_sapi
        self.channel = "none"
        self._jaws = None
        self._nvda = None
        self._sapi = None
        self._detect()

    # ------------------------------------------------------------------ setup
    def _detect(self) -> None:
        # JAWS: COM, present only while JAWS is running
        try:
            import win32com.client

            self._jaws = win32com.client.Dispatch("FreedomSci.JawsApi")
            self.channel = "jaws"
            return
        except Exception:
            self._jaws = None

        for dll in _candidate_nvda_dlls():
            if not dll.exists():
                continue
            try:
                lib = ctypes.windll.LoadLibrary(str(dll))
                if lib.nvdaController_testIfRunning() == 0:   # 0 == running
                    self._nvda = lib
                    self.channel = "nvda"
                    return
            except Exception:
                continue

        if self.allow_sapi:
            try:
                import win32com.client

                self._sapi = win32com.client.Dispatch("SAPI.SpVoice")
                self.channel = "sapi"
            except Exception:
                self._sapi = None

    def describe(self) -> str:
        return t(f"reader_{self.channel}")

    # ------------------------------------------------------------------ speak
    def say(self, text: str, interrupt: bool = False) -> bool:
        """Returns whether the text actually went anywhere."""
        if not self.enabled or not text:
            return False
        with _lock:
            try:
                if self._jaws is not None:
                    # second argument: flush the queue first
                    return bool(self._jaws.SayString(text, interrupt))
                if self._nvda is not None:
                    if interrupt:
                        self._nvda.nvdaController_cancelSpeech()
                    return self._nvda.nvdaController_speakText(text) == 0
                if self._sapi is not None:
                    self._sapi.Speak(text, 1 | (2 if interrupt else 0))  # async
                    return True
            except Exception:
                # A screen reader that quit mid-session should not crash a
                # three-hour transcription; try to find a channel again.
                self._jaws = self._nvda = self._sapi = None
                self.channel = "none"
                self._detect()
            return False

    def stop(self) -> None:
        with _lock:
            try:
                if self._jaws is not None:
                    self._jaws.StopSpeech()
                elif self._nvda is not None:
                    self._nvda.nvdaController_cancelSpeech()
                elif self._sapi is not None:
                    self._sapi.Speak("", 3)         # purge
            except Exception:
                pass


# Durations are produced by app.i18n.duration(), which renders them as words in
# the active interface language rather than as a run of digits.
