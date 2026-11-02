"""Putting dictated text into whatever window has focus.

Word is written to over COM - `app/wordio.py` - and that is still the better
path when Word is in front: one undo record for the whole insertion, the
paragraph's reading direction set, and no synthetic keystrokes at all.

But dictation is asked for in Notepad, in Teams, in WhatsApp, in a browser's
message box, in every other text field on the machine, and none of those has
an automation interface worth having. For those there are two ways in, and
both are here:

* **the clipboard and Ctrl+V**, which is what every dictation tool on Windows
  does. It is instant whatever the length of the text, it carries Arabic and
  every other script without a per-character encoding question, and the
  previous clipboard contents are put back afterwards.
* **typing the characters**, as a fallback for a field that refuses a paste -
  some secured inputs do. Slower, and it is the second choice for that reason.

Two things make the difference between this working and silently doing nothing:

**The modifiers have to be released first.** The user is holding Ctrl+Shift at
the moment the shortcut fires. Sending Ctrl+V while Shift is still down
arrives as Ctrl+Shift+V - "paste without formatting" in some applications,
nothing at all in others. So this waits for the user's fingers to come off the
keys, then releases them itself, and only then pastes. The project this idea
came from had the same routine for the same reason, having found it the hard
way with Alt.

**Nothing is typed into a password field.** A dictation is text the user spoke
out loud; a password box is the one place where putting it is a security
problem rather than a feature. The class of the focused control is checked and
a password field is refused, out loud, rather than filled.
"""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes

from engine import applog

# How long to wait for the user to let go of the shortcut before giving up and
# forcing the keys up. Measured against how long a person actually holds a
# chord: a tenth of a second is usual, half a second is a slow release.
MODIFIER_WAIT_S = 0.7
# After putting text on the clipboard, before pasting. The clipboard is owned
# by whichever process set it last and the target reads it asynchronously.
CLIPBOARD_SETTLE_S = 0.06
# Before putting the user's own clipboard contents back. Long enough that the
# paste has been served, short enough that nobody notices.
CLIPBOARD_RESTORE_S = 0.35

VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_MENU = 0x12                     # Alt
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_V = 0x56

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_KEYBOARD = 1


class _KeyboardInput(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                # ULONG_PTR, so eight bytes on a 64-bit build and four on a
                # 32-bit one. Getting this wrong changes sizeof(INPUT), and
                # SendInput rejects the whole call when the size it is given
                # does not match the one it expects - silently, by returning
                # zero. That is exactly what happened here first.
                ("dwExtraInfo", wintypes.WPARAM)]


class _MouseInput(ctypes.Structure):
    """Never sent. Present only because it is the largest member of the union,
    and therefore the member that decides sizeof(INPUT)."""
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", wintypes.WPARAM)]


class _HardwareInput(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _InputUnion(ctypes.Union):
    _fields_ = [("ki", _KeyboardInput), ("mi", _MouseInput),
                ("hi", _HardwareInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _InputUnion)]


def _send(*events: _Input) -> int:
    """Send key events. Returns how many Windows accepted.

    The count matters. SendInput refuses silently when there is nothing to
    send to - a locked screen, a window running elevated while this is not -
    and a caller that ignores the return value reports a dictation as written
    when not one character of it arrived anywhere.
    """
    array = (_Input * len(events))(*events)
    return int(ctypes.windll.user32.SendInput(
        len(events), array, ctypes.sizeof(_Input)))


def _key(vk: int, up: bool = False) -> _Input:
    return _Input(type=INPUT_KEYBOARD,
                  union=_InputUnion(ki=_KeyboardInput(
                      wVk=vk, wScan=0,
                      dwFlags=KEYEVENTF_KEYUP if up else 0,
                      time=0, dwExtraInfo=0)))


def _char(code: int, up: bool = False) -> _Input:
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    return _Input(type=INPUT_KEYBOARD,
                  union=_InputUnion(ki=_KeyboardInput(
                      wVk=0, wScan=code, dwFlags=flags,
                      time=0, dwExtraInfo=0)))


def _held(vk: int) -> bool:
    # The high bit of GetAsyncKeyState is "down right now".
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


def release_modifiers(timeout: float = MODIFIER_WAIT_S) -> bool:
    """Wait for the shortcut's keys to come up, then force them up.

    Returns whether the user had let go on their own. Without this the paste
    carries whatever was still held: Ctrl+Shift+V instead of Ctrl+V, which in
    some applications pastes unformatted and in others does nothing at all.
    """
    keys = (VK_CONTROL, VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN)
    deadline = time.time() + timeout
    released = False
    while time.time() < deadline:
        if not any(_held(k) for k in keys):
            released = True
            break
        time.sleep(0.01)
    # Up events for anything still down. Sending an up for a key that is
    # already up is harmless, and leaving one down is not.
    for key in keys:
        if _held(key):
            _send(_key(key, up=True))
    time.sleep(0.02)
    return released


# --------------------------------------------------------------- the target

def focused_window() -> tuple[int, str, str]:
    """(handle, window class, process name) of whatever has focus right now."""
    try:
        u32 = ctypes.windll.user32
        hwnd = u32.GetForegroundWindow()
        if not hwnd:
            return 0, "", ""
        buf = ctypes.create_unicode_buffer(256)
        u32.GetClassNameW(hwnd, buf, 256)
        pid = ctypes.c_ulong()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        from app.wordio import _process_name

        return int(hwnd), buf.value or "", _process_name(pid.value)
    except Exception:
        return 0, "", ""


def window_process(hwnd: int) -> str:
    """The process name behind a window handle, or "" if it has gone."""
    try:
        u32 = ctypes.windll.user32
        if not hwnd or not u32.IsWindow(hwnd):
            return ""
        pid = ctypes.c_ulong()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        from app.wordio import _process_name

        return _process_name(pid.value)
    except Exception:
        return ""


def focus_window(hwnd: int) -> bool:
    """Bring a window back to the front. Returns whether it is there now.

    This exists because of where a dictation ends up. The text is written when
    the transcription finishes, which is seconds after the user stopped
    speaking, and by then the window in front may not be the one they were
    typing in - the progress window has appeared, or a notification took it.
    Pasting into whatever happens to be in front at that moment is how a
    sentence meant for Teams ends up somewhere else entirely.

    So the window is remembered when the recording starts and restored here.

    SetForegroundWindow is deliberately restricted by Windows: a process can
    only use it when it already owns the foreground, among other conditions.
    Attaching to the target's input thread is the long-standing way to be
    allowed, and if it still refuses, the caller finds out from the return
    value rather than pasting somewhere unintended.
    """
    try:
        u32 = ctypes.windll.user32
        if not hwnd or not u32.IsWindow(hwnd):
            return False
        if u32.GetForegroundWindow() == hwnd:
            return True

        if u32.IsIconic(hwnd):
            u32.ShowWindow(hwnd, 9)        # SW_RESTORE
        k32 = ctypes.windll.kernel32
        ours = k32.GetCurrentThreadId()
        theirs = u32.GetWindowThreadProcessId(hwnd, None)
        attached = bool(u32.AttachThreadInput(ours, theirs, True))
        try:
            u32.BringWindowToTop(hwnd)
            u32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                u32.AttachThreadInput(ours, theirs, False)

        for _ in range(25):               # up to a quarter of a second
            if u32.GetForegroundWindow() == hwnd:
                return True
            time.sleep(0.01)
        return False
    except Exception as exc:
        applog.failed("restoring the focused window", exc, where="insert")
        return False


def _focused_control_class() -> str:
    """The class of the control with the caret, not of the window around it.

    GUI threads are separate, so this asks the foreground window's own thread
    what it considers focused rather than reading this process's focus.
    """
    try:
        u32 = ctypes.windll.user32
        hwnd = u32.GetForegroundWindow()
        if not hwnd:
            return ""
        thread = u32.GetWindowThreadProcessId(hwnd, None)

        class GuiThreadInfo(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("hwndActive", wintypes.HWND),
                        ("hwndFocus", wintypes.HWND),
                        ("hwndCapture", wintypes.HWND),
                        ("hwndMenuOwner", wintypes.HWND),
                        ("hwndMoveSize", wintypes.HWND),
                        ("hwndCaret", wintypes.HWND),
                        ("rcCaret", wintypes.RECT)]

        info = GuiThreadInfo()
        info.cbSize = ctypes.sizeof(GuiThreadInfo)
        if not u32.GetGUIThreadInfo(thread, ctypes.byref(info)):
            return ""
        target = info.hwndFocus or info.hwndCaret or hwnd
        buf = ctypes.create_unicode_buffer(256)
        u32.GetClassNameW(target, buf, 256)
        return buf.value or ""
    except Exception:
        return ""


def looks_like_password_field() -> bool:
    """Whether the caret is in a password box, as far as Windows will say.

    Only the standard control answers this honestly - a web page's password
    input is a browser-drawn thing with no class to read. So this is a floor,
    not a guarantee, and it is here because the cost of being wrong in the
    other direction is somebody's spoken password in a log or a chat window.
    """
    try:
        cls = _focused_control_class()
        if cls.lower() not in ("edit", "richedit", "richedit20w",
                               "richedit20a", "richedit50w"):
            return False
        u32 = ctypes.windll.user32
        hwnd = u32.GetForegroundWindow()
        thread = u32.GetWindowThreadProcessId(hwnd, None)

        class GuiThreadInfo(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("hwndActive", wintypes.HWND),
                        ("hwndFocus", wintypes.HWND),
                        ("hwndCapture", wintypes.HWND),
                        ("hwndMenuOwner", wintypes.HWND),
                        ("hwndMoveSize", wintypes.HWND),
                        ("hwndCaret", wintypes.HWND),
                        ("rcCaret", wintypes.RECT)]

        info = GuiThreadInfo()
        info.cbSize = ctypes.sizeof(GuiThreadInfo)
        if not u32.GetGUIThreadInfo(thread, ctypes.byref(info)):
            return False
        focus = info.hwndFocus
        if not focus:
            return False
        # ES_PASSWORD is a style bit on the edit control itself.
        ES_PASSWORD = 0x0020
        GWL_STYLE = -16
        style = u32.GetWindowLongW(focus, GWL_STYLE)
        return bool(style & ES_PASSWORD)
    except Exception:
        return False


# ------------------------------------------------------------- the clipboard

def _clipboard_text() -> str | None:
    """Whatever text is on the clipboard, or None. Never raises."""
    try:
        import win32clipboard

        for _ in range(5):
            try:
                win32clipboard.OpenClipboard()
            except Exception:
                time.sleep(0.02)       # another process has it open
                continue
            try:
                import win32con

                if win32clipboard.IsClipboardFormatAvailable(
                        win32con.CF_UNICODETEXT):
                    return win32clipboard.GetClipboardData(
                        win32con.CF_UNICODETEXT)
                return None
            finally:
                win32clipboard.CloseClipboard()
    except Exception:
        pass
    return None


def _set_clipboard(text: str) -> bool:
    """Put text on the clipboard. Returns whether it went."""
    try:
        import win32clipboard
        import win32con

        for _ in range(5):
            try:
                win32clipboard.OpenClipboard()
            except Exception:
                time.sleep(0.02)
                continue
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
                return True
            finally:
                win32clipboard.CloseClipboard()
    except Exception:
        pass
    return False


def _restore_clipboard_later(previous: str | None) -> None:
    """Put the user's own clipboard back, off the calling thread.

    Their clipboard is theirs. Dictation borrowing it for a third of a second
    is acceptable; keeping it is not - somebody who copied a paragraph, then
    dictated a sentence, would find the paragraph gone.
    """
    def restore():
        time.sleep(CLIPBOARD_RESTORE_S)
        if previous is None:
            return                  # nothing text-shaped was there to restore
        _set_clipboard(previous)

    threading.Thread(target=restore, daemon=True,
                     name="clipboard-restore").start()


# --------------------------------------------------------------- the insert

def type_text(text: str) -> bool:
    """Send the text as characters, one at a time. Returns whether it went.

    The fallback. Unicode above the basic plane is sent as its two surrogates,
    which is what SendInput expects.
    """
    if not text:
        return True
    try:
        events: list[_Input] = []
        for char in text:
            for unit in char.encode("utf-16-le", "replace").decode(
                    "utf-16-le", "replace"):
                code = ord(unit)
                if code > 0xFFFF:
                    high = 0xD800 + ((code - 0x10000) >> 10)
                    low = 0xDC00 + ((code - 0x10000) & 0x3FF)
                    for part in (high, low):
                        events.append(_char(part))
                        events.append(_char(part, up=True))
                else:
                    events.append(_char(code))
                    events.append(_char(code, up=True))
        # In batches, so a long dictation does not hand the driver one
        # enormous array.
        for start in range(0, len(events), 200):
            batch = events[start:start + 200]
            if _send(*batch) != len(batch):
                applog.write("Windows accepted no key events - a locked "
                             "screen, or a window this cannot reach",
                             where="insert")
                return False
            time.sleep(0.001)
    except Exception as exc:
        applog.failed("typing the text", exc, where="insert")
        return False
    return True


def paste_text(text: str, restore_clipboard: bool = True) -> bool:
    """Put the text on the clipboard and send Ctrl+V. Returns whether it went."""
    if not text:
        return True
    previous = _clipboard_text() if restore_clipboard else None
    if not _set_clipboard(text):
        return False
    time.sleep(CLIPBOARD_SETTLE_S)
    try:
        sent = _send(_key(VK_CONTROL), _key(VK_V),
                     _key(VK_V, up=True), _key(VK_CONTROL, up=True))
    except Exception as exc:
        applog.failed("sending Ctrl+V", exc, where="insert")
        return False
    if sent != 4:
        applog.write("Windows accepted no key events for the paste",
                     where="insert")
        if restore_clipboard:
            _restore_clipboard_later(previous)
        return False
    if restore_clipboard:
        _restore_clipboard_later(previous)
    return True


def send(text: str, *, prefer_typing: bool = False,
         restore_clipboard: bool = True) -> dict:
    """Put `text` into the focused field. Returns what happened and where.

    {"ok": bool, "method": "paste"|"type"|"", "window": str, "refused": str}

    `refused` is a reason for the user rather than a log line: the only one is
    a password field, which is refused on purpose.
    """
    _hwnd, window_class, process = focused_window()
    where = process or window_class or "?"

    if looks_like_password_field():
        applog.write(f"refused to write into a password field in {where}",
                     where="insert")
        return {"ok": False, "method": "", "window": where,
                "refused": "password"}

    # Before anything is sent: the user is still holding the shortcut.
    released = release_modifiers()

    method = "type" if prefer_typing else "paste"
    ok = (type_text(text) if prefer_typing
          else paste_text(text, restore_clipboard))
    if not ok and not prefer_typing:
        # A field that refuses a paste still accepts keystrokes.
        applog.write("the paste did not go; typing instead", where="insert")
        method = "type"
        ok = type_text(text)

    applog.write(f"{method} {len(text)} characters into {where}"
                 f"{'' if released else ' (forced the modifier keys up)'}"
                 f"{'' if ok else ' - FAILED'}", where="insert")
    return {"ok": ok, "method": method, "window": where, "refused": ""}
