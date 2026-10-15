"""Talking to a running Word through COM.

No macros, no .dotm, no VBA project trust setting: we attach to whatever Word
the user already has open and write into it. That keeps installation to "run the
app" and leaves Word's macro security untouched.
"""
from __future__ import annotations

from app.i18n import t

# Word enumerations we rely on. Hard-coded because win32com's generated
# constants are not available when Word has not been early-bound.
#
# ReadingOrder is documented as wdReadingOrderRtl=2 / wdReadingOrderLtr=1, but
# Word 16.0 rejects 2 outright ("The number must be between 0 and 1") and
# measured behaviour on it is 0 = right-to-left, 1 = left-to-right. Builds
# apparently disagree, so we try the candidates in order and verify by reading
# the value back rather than trusting either set of constants.
WD_READING_ORDER_RTL_CANDIDATES = (0, 2)
WD_READING_ORDER_LTR_CANDIDATES = (1,)
WD_READING_ORDER_RTL = 0         # what actually works on Word 16.0
WD_READING_ORDER_LTR = 1
WD_STORY_END = 6                 # wdEndOfStory


class WordNotRunning(RuntimeError):
    """Raised when there is no Word to write into. The message is a full
    sentence because it gets spoken to the user as-is."""


def _pythoncom():
    import pythoncom

    return pythoncom


def init_thread() -> None:
    """COM must be initialised per thread. The worker thread that finishes a
    transcription is not the one that started it."""
    try:
        _pythoncom().CoInitialize()
    except Exception:
        pass


def uninit_thread() -> None:
    try:
        _pythoncom().CoUninitialize()
    except Exception:
        pass


def get_word(start_if_needed: bool = False):
    """Attach to a running Word. Starting one is opt-in: silently launching
    Word because a hotkey was pressed would be a surprise."""
    import win32com.client

    try:
        return win32com.client.GetActiveObject("Word.Application")
    except Exception:
        pass

    if not start_if_needed:
        raise WordNotRunning(t("word_not_open"))

    try:
        app = win32com.client.Dispatch("Word.Application")
        app.Visible = True
        return app
    except Exception as exc:
        raise WordNotRunning(t("word_cannot_start")) from exc


def active_document(app, create_if_none: bool = True):
    try:
        if app.Documents.Count == 0:
            if not create_if_none:
                raise WordNotRunning(t("word_no_doc"))
            return app.Documents.Add()
        return app.ActiveDocument
    except WordNotRunning:
        raise
    except Exception as exc:
        raise WordNotRunning(t("word_no_access")) from exc


def word_is_foreground() -> bool:
    """Whether Word owns the foreground window, so a global hotkey can stay out
    of the way of every other application."""
    try:
        import ctypes

        u32 = ctypes.windll.user32
        hwnd = u32.GetForegroundWindow()
        if not hwnd:
            return False
        length = u32.GetClassNameW(hwnd, None, 0)
        buf = ctypes.create_unicode_buffer(256)
        u32.GetClassNameW(hwnd, buf, 256)
        cls = buf.value or ""
        # Word's frame is OpusApp; its document panes sit inside it.
        if cls == "OpusApp":
            return True
        pid = ctypes.c_ulong()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return _process_name(pid.value).lower() in ("winword.exe",)
    except Exception:
        return False


def _process_name(pid: int) -> str:
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(512)
            size = wintypes.DWORD(512)
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return buf.value.rsplit("\\", 1)[-1]
        finally:
            k32.CloseHandle(h)
    except Exception:
        pass
    return ""


def set_reading_order(rng, rtl: bool) -> bool:
    """Set the paragraph reading order, verifying it actually took.

    Reading order is not cosmetic for a screen reader user: navigating by word
    follows the paragraph's reading order, so getting this wrong makes Arabic
    read in the wrong direction. We therefore check the value came back instead
    of assuming the assignment worked, and report failure to the caller rather
    than swallowing it - silently ignoring the error is what hid the wrong
    constant in the first place.

    Word sets Alignment itself when the reading order changes, so we leave
    alignment alone rather than fighting it.
    """
    candidates = (WD_READING_ORDER_RTL_CANDIDATES if rtl
                  else WD_READING_ORDER_LTR_CANDIDATES)
    pf = rng.ParagraphFormat
    for value in candidates:
        try:
            pf.ReadingOrder = value
            if int(rng.ParagraphFormat.ReadingOrder) == value:
                return True
        except Exception:
            continue
    return False


def insert_dictation(app, text: str, *, rtl: bool = False) -> dict:
    """Append one dictated phrase at the insertion point.

    Deliberately not insert_transcript. That one writes a finished transcript
    as a block and collapses the selection back to the *start*, so a screen
    reader user can read the result from its beginning. Dictation needs the
    opposite of both: each phrase continues the last one, so it must be
    separated by a space rather than a paragraph break, and the cursor has to
    end up *after* the new text or the next phrase would land in front of it
    and the sentence would assemble backwards.

    One undo record per phrase, so Ctrl+Z takes back the last thing said
    rather than the whole session.

    Returns {"start": int, "end": int, "text": str} - `text` being what was
    actually written, which includes the leading space when one was added.
    """
    if not text or not text.strip():
        return {"start": 0, "end": 0, "text": ""}

    undo = None
    try:
        undo = app.UndoRecord
        undo.StartCustomRecord(t("undo_dictation"))
    except Exception:
        undo = None

    try:
        doc = active_document(app)
        try:
            doc.Activate()
        except Exception:
            pass

        sel = app.Selection
        start = int(sel.Range.Start)

        # Space before the phrase, unless we are at the very start of the
        # document or the character in front is already a space or a newline.
        body = text.strip()
        if start > 0:
            try:
                before = doc.Range(max(0, start - 1), start).Text or ""
            except Exception:
                before = ""
            if before and before not in (" ", "\r", "\n", "\t", "\u000b"):
                body = " " + body

        sel.Range.InsertAfter(body)
        rng = doc.Range(start, start + len(body))
        reading_order = set_reading_order(rng, rtl)

        end = int(rng.End)
        # After the phrase, ready for the next one.
        sel.SetRange(end, end)
        return {"start": start, "end": end, "text": body,
                "reading_order_set": reading_order}
    finally:
        if undo is not None:
            try:
                undo.EndCustomRecord()
            except Exception:
                pass


def insert_transcript(app, text: str, *, rtl: bool = False,
                      mode: str = "cursor", heading: str | None = None) -> dict:
    """Write the transcript into Word.

    mode: "cursor" at the insertion point, "end" at the end of the document,
    "new" into a fresh document.

    Returns {"start": int, "end": int, "document": str}.

    The whole insertion is one undo record, so a single Ctrl+Z removes all of
    it rather than making the user hold it down for a three-hour transcript.
    The selection is left collapsed at the *start* of the new text so a screen
    reader user can begin reading immediately with their normal reading keys.
    """
    if not text:
        return {"start": 0, "end": 0, "document": ""}

    body = (f"{heading}\n{text}\n" if heading else f"{text}\n")

    undo = None
    try:                                    # UndoRecord needs Word 2010+
        undo = app.UndoRecord
        undo.StartCustomRecord(t("undo_record"))
    except Exception:
        undo = None

    try:
        if mode == "new":
            doc = app.Documents.Add()
        else:
            doc = active_document(app)

        try:
            doc.Activate()
        except Exception:
            pass

        sel = app.Selection
        if mode == "end":
            sel.EndKey(Unit=WD_STORY_END)
        elif mode == "new":
            sel.HomeKey(Unit=WD_STORY_END)

        start = int(sel.Range.Start)
        sel.Range.InsertAfter(body)         # faster than TypeText on long text

        rng = doc.Range(start, start + len(body))
        reading_order = set_reading_order(rng, rtl)

        end = int(rng.End)
        # Collapse to the beginning of what we inserted, so a screen reader user
        # can start reading straight away with their normal reading keys.
        sel.SetRange(start, start)
        return {"start": start, "end": end, "document": str(doc.Name),
                "reading_order_set": reading_order}
    finally:
        if undo is not None:
            try:
                undo.EndCustomRecord()
            except Exception:
                pass
