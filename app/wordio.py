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


BREAKS = ("\r", "\n", "\x07")           # paragraph mark, line break, cell mark


def breaks_before(doc, position: int, most: int = 2) -> int:
    """How many line breaks are immediately before `position`, up to `most`.

    Used to put exactly one blank line in front of each insertion: two breaks
    in a row are what a blank line is made of, so what is missing is added and
    nothing more. Counting rather than assuming matters because the caret is
    sometimes already at the start of a paragraph and sometimes mid-sentence,
    and adding a fixed number of breaks gives one of those two cases a stack
    of empty paragraphs.
    """
    if position <= 0:
        return most                     # the start of the document needs none
    found = 0
    try:
        while found < most and position - found > 0:
            char = doc.Range(position - found - 1, position - found).Text
            if char not in BREAKS:
                break
            found += 1
    except Exception:
        return 0                        # cannot tell, so add the full break
    return found


def insert_transcript(app, text: str, *, rtl: bool = False,
                      mode: str = "cursor", heading: str | None = None) -> dict:
    """Write the transcript into Word.

    mode: "cursor" at the insertion point, "end" at the end of the document,
    "new" into a fresh document.

    Returns {"start": int, "end": int, "document": str}.

    The whole insertion is one undo record, so a single Ctrl+Z removes all of
    it rather than making the user hold it down for a three-hour transcript.

    **Every insertion has a blank line before it and the caret is left after
    it.** The blank line is made up to rather than added blindly:
    `breaks_before` counts what is already there, so a caret sitting
    mid-sentence gets two breaks and one already on an empty line gets none.

    Neither half of that is cosmetic. Without the separation a second job lands
    against the end of the first and the two run together as one sentence.
    Without the caret moving to the end, the second insertion happens at the
    *start* of the first - so two jobs come out in the opposite order to the
    one they were spoken in. This used to leave the caret at the start so a
    screen reader user could read from the top at once; correct order is worth
    more than that, and the spoken confirmation already says what landed where.
    """
    if not text:
        return {"start": 0, "end": 0, "document": ""}

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
        lead = "\n" * (2 - breaks_before(doc, start))
        body = (f"{lead}{heading}\n{text}\n" if heading
                else f"{lead}{text}\n")

        sel.Range.InsertAfter(body)         # faster than TypeText on long text

        rng = doc.Range(start, start + len(body))
        reading_order = set_reading_order(rng, rtl)

        end = int(rng.End)
        sel.SetRange(end, end)
        return {"start": start, "end": end, "document": str(doc.Name),
                "reading_order_set": reading_order}
    finally:
        if undo is not None:
            try:
                undo.EndCustomRecord()
            except Exception:
                pass
