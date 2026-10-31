"""The dictation check: proving the microphone works, not assuming it.

Written because the program was once deaf and said nothing. Reading the Windows
settings would not have caught it: on the machine this was developed on all
three microphone-consent keys say Allow and every capture endpoint reports OK,
while the built-in array records 0.00003 peak - silence - and the USB headset
records 0.03. The only check that tells them apart is recording and measuring.

So this runs the whole path the way dictation runs it: open the device, record,
measure the levels, find the voice, transcribe it, and show the words back. It
also times that last step and reports it, because how long dictation takes on
*this* computer is a fact the user should be told rather than left to discover.

Every step yields a result as it finishes, so the dialog can read them out as
they happen instead of freezing for ten seconds and then printing a report.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from app.i18n import t
from engine import mic

RECORD_SECONDS = 3.0


@dataclass
class Step:
    """One finished check, already in the user's language."""

    title: str
    ok: bool | None               # None: information, nothing passed or failed
    detail: str = ""
    advice: str = ""

    @property
    def mark(self) -> str:
        return "..." if self.ok is None else ("OK" if self.ok else "X")

    def line(self) -> str:
        parts = [f"[{self.mark}] {self.title}"]
        if self.detail:
            parts.append(f"    {self.detail}")
        if self.advice and not self.ok:
            parts.append(f"    -> {self.advice}")
        return "\n".join(parts)


# ----------------------------------------------------------------- the checks

def consent() -> dict[str, str]:
    """What Windows thinks about microphone access, at all three levels.

    The per-user key governs packaged apps, NonPackaged governs desktop
    programs like this one, and the machine key can override both. A user who
    has turned off "let desktop apps access your microphone" has a working
    microphone and a silent program.
    """
    import winreg

    base = (r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
            r"\ConsentStore\microphone")
    places = {
        "user": (winreg.HKEY_CURRENT_USER, base),
        "desktop apps": (winreg.HKEY_CURRENT_USER, base + r"\NonPackaged"),
        "machine": (winreg.HKEY_LOCAL_MACHINE, base),
    }
    found = {}
    for name, (root, path) in places.items():
        try:
            with winreg.OpenKey(root, path) as key:
                found[name] = str(winreg.QueryValueEx(key, "Value")[0])
        except OSError:
            found[name] = "unset"
    return found


def run(frame, device_index: int | None = None, ai=None):
    """Yield a Step at a time. Runs off the GUI thread - it records audio."""
    # -- 1. is there anything to record with -------------------------------
    try:
        devices = mic.input_devices()
    except mic.MicError as exc:
        yield Step(t("chk_devices"), False, str(exc), t("chk_devices_fix"))
        return

    if not devices:
        yield Step(t("chk_devices"), False, "", t("chk_devices_fix"))
        return

    chosen = next((d for d in devices if d.index == device_index), None)
    if chosen is None:
        chosen = mic.default_input() or devices[0]
    yield Step(t("chk_devices"), True,
               t("chk_devices_found", count=len(devices), name=str(chosen)))

    # -- 2. is this program allowed to use it ------------------------------
    states = consent()
    denied = [name for name, value in states.items() if value == "Deny"]
    yield Step(
        t("chk_permission"), not denied,
        ", ".join(f"{k}: {v}" for k, v in states.items()),
        t("chk_permission_fix") if denied else "")
    if denied:
        return

    # -- 3. does the device open at all ------------------------------------
    started = time.time()
    try:
        audio = mic.record(RECORD_SECONDS, device=chosen.index)
    except mic.MicError as exc:
        yield Step(t("chk_open"), False, str(exc), t("chk_open_fix"))
        return
    yield Step(t("chk_open"), True,
               t("chk_open_ok", seconds=round(time.time() - started, 1)))

    # -- 4. was there anything in it ---------------------------------------
    levels = mic.measure(audio)
    detail = t("chk_level_detail", peak=f"{levels.peak:.4f}",
               rms=f"{levels.rms:.4f}")
    if levels.silent:
        yield Step(t("chk_level"), False, detail, t("chk_level_silent"))
        yield from _other_devices(devices, chosen)
        return
    if levels.clipping:
        yield Step(t("chk_level"), False, detail, t("chk_level_loud"))
    elif levels.quiet:
        yield Step(t("chk_level"), False, detail, t("chk_level_quiet"))
    else:
        yield Step(t("chk_level"), True, detail)

    # -- 5. was it a voice, or just noise ----------------------------------
    try:
        from engine.chunking import speech_regions

        regions = speech_regions(audio)
    except Exception:
        regions = []
    speech_s = sum((r["end"] - r["start"]) for r in regions) / mic.SAMPLE_RATE
    yield Step(t("chk_voice"), bool(regions),
               t("chk_voice_detail", seconds=f"{speech_s:.1f}"),
               t("chk_voice_fix"))

    # -- 6 and 7. can it be transcribed, and how long does that take -------
    if not frame.model_ready.is_set() or frame.backend is None:
        yield Step(t("chk_model"), False, "", t("chk_model_fix"))
        return

    from engine.window import short_window

    seconds = len(audio) / mic.SAMPLE_RATE
    started = time.time()
    try:
        with short_window(frame.backend.model, seconds):
            rows = list(frame.backend.stream_segments(
                audio.astype(np.float32), None,
                {"condition_on_previous_text": False}))
        text = " ".join((r.get("text") or "").strip() for r in rows).strip()
    except Exception as exc:
        yield Step(t("chk_recognised"), False, str(exc), t("chk_model_fix"))
        return
    took = time.time() - started

    yield Step(t("chk_recognised"), bool(text),
               text or "", t("chk_recognised_fix"))
    yield Step(t("chk_speed"), None,
               t("chk_speed_detail", seconds=f"{took:.1f}",
                 audio=f"{seconds:.0f}"))

    # -- 8. can the shortcut be taken --------------------------------------
    failed = getattr(frame, "hotkey_failures", [])
    wanted = frame.settings.hotkey_dictate
    taken = wanted not in failed
    yield Step(t("chk_hotkey"), taken, wanted,
               t("chk_hotkey_fix", key=wanted))

    # -- 9. is the language model reachable, if one is turned on ----------
    # From what is on screen, not from what was last saved: somebody who has
    # just pasted a key wants to know about that key, and making them save and
    # reopen first would be the opposite of a diagnostic.
    provider, key, model = ai or (frame.settings.dictation_ai,
                                  frame.settings.dictation_ai_key,
                                  frame.settings.dictation_ai_model)
    if provider == "off":
        yield Step(t("chk_ai"), None, t("chk_ai_off"))
    else:
        from engine import polish

        polisher = polish.make(provider, key, model)
        try:
            model = polisher.check()
            yield Step(t("chk_ai"), True, t("chk_ai_ok", model=model))
        except Exception as exc:
            yield Step(t("chk_ai"), False, "", t("chk_ai_fix", error=exc))

    # -- 10. is there somewhere to write -----------------------------------
    from app import wordio

    try:
        wordio.init_thread()
        wordio.get_word()
        yield Step(t("chk_word"), True)
    except Exception:
        yield Step(t("chk_word"), False, "", t("chk_word_fix"))


def _other_devices(_devices, skip):
    """Nothing was heard - so say which device would have heard something.

    A user whose default input is a dead built-in array cannot be expected to
    guess that the headset two entries down works.
    """
    for device in mic.all_input_devices():
        if device.index == skip.index:
            continue
        try:
            audio = mic.record(1.0, device=device.index)
        except mic.MicError:
            continue
        if not mic.measure(audio).silent:
            yield Step(t("chk_other_device"), None,
                       t("chk_other_device_found", name=str(device)))
            return
    yield Step(t("chk_other_device"), None, t("chk_other_device_none"))
