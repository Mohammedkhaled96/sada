"""The application side of dictation: settings, strings, and insertion.

Word insertion is tested against a stand-in COM object rather than Word
itself, because the behaviour that matters here is the joining rule and where
the cursor ends up - and those are decided by our code, not by Word. The real
COM path is already exercised by tests/test_app.py.

Run:  PYTHONUTF8=1 .venv/Scripts/python.exe tests/test_dictation_app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, i18n, wordio
from app.config import Settings, format_hotkey, parse_hotkey

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))
    if not ok:
        failures.append(label)


# ------------------------------------------------------------------- settings
print("--- settings ---")

s = Settings()
check("dictation has a shortcut by default", bool(s.hotkey_dictate),
      s.hotkey_dictate)
check("it parses as a real shortcut", parse_hotkey(s.hotkey_dictate) is not None,
      str(parse_hotkey(s.hotkey_dictate)))

hotkeys = [f for f in vars(Settings()) if f.startswith("hotkey_")]
parsed = [parse_hotkey(getattr(s, f)) for f in hotkeys]
check("every default shortcut is distinct, including the new one",
      len(set(parsed)) == len(parsed), str(hotkeys))

s = Settings(dictation_pause_ms=10).validate()
check("an impossibly short pause is clamped up", s.dictation_pause_ms == 200,
      str(s.dictation_pause_ms))
s = Settings(dictation_pause_ms=100000).validate()
check("an absurdly long pause is clamped down", s.dictation_pause_ms == 3000,
      str(s.dictation_pause_ms))
s = Settings(hotkey_dictate="Ctrl+Banana").validate()
check("an unparseable shortcut falls back to the default",
      s.hotkey_dictate == Settings().hotkey_dictate, s.hotkey_dictate)

check("filtering is on by default", Settings().dictation_remove_fillers is True)
check("reading each phrase back is on by default",
      Settings().dictation_speak_phrases is True)
check("no microphone is pinned by default", Settings().mic_device == "")


# -------------------------------------------------------------------- strings
print("\n--- strings ---")

# The exact two announcements that were asked for.
i18n.set_language("en")
check("starting says 'Start dictation'",
      i18n.t("say_dictation_start") == "Start dictation",
      i18n.t("say_dictation_start"))
check("stopping says 'Stop'", i18n.t("say_dictation_stop") == "Stop",
      i18n.t("say_dictation_stop"))

needed = ["say_dictation_start", "say_dictation_stop", "say_dictation_busy",
          "say_dictation_failed", "say_dictation_status", "say_transcribe_busy_dictating",
          "field_k_dictate", "help_k_dictate", "group_dictation", "field_mic",
          "mic_auto", "mic_none", "help_mic", "field_pause", "help_pause",
          "check_fillers", "help_fillers", "check_speak_phrases",
          "help_speak_phrases", "undo_dictation", "tray_dictating",
          "tray_dictate", "status_dictating"]
for code in ("en", "ar"):
    i18n.set_language(code)
    missing = [i for i in needed if i18n.t(i) == i]
    check(f"every dictation string exists in {code}", not missing, str(missing))

# A missing Arabic half silently falls back to English, which would ship a
# half-translated dialog without failing anything.
untranslated = [i for i in needed
                if len(i18n.S[i]) < 2 or not i18n.S[i][1].strip()]
check("none of them fall back to english in the arabic interface",
      not untranslated, str(untranslated))

i18n.set_language("en")
check("the welcome message names the dictation shortcut",
      "{k_dictate}" in i18n.S["say_welcome"][0])
check("and in arabic too", "{k_dictate}" in i18n.S["say_welcome"][1])

for code in ("en", "ar"):
    i18n.set_language(code)
    check(f"the help document explains dictation in {code}",
          "Ctrl+Z" in i18n.help_text())


# ---------------------------------------------------- insertion into the document
print("\n--- insertion ---")


class FakeRange:
    def __init__(self, doc, start, end):
        self.doc, self.Start, self.End = doc, start, end

    @property
    def Text(self):
        return self.doc.text[self.Start:self.End]

    def InsertAfter(self, body):
        self.doc.text = self.doc.text[:self.End] + body + self.doc.text[self.End:]

    @property
    def ParagraphFormat(self):
        return self.doc


class FakeSelection:
    def __init__(self, doc):
        self.doc = doc

    @property
    def Range(self):
        return FakeRange(self.doc, self.doc.cursor, self.doc.cursor)

    def SetRange(self, start, end):
        self.doc.cursor = start


class FakeDoc:
    Name = "Document1"

    def __init__(self, text="", cursor=0):
        self.text, self.cursor = text, cursor
        self.reading_order = 1

    def Range(self, start, end):
        return FakeRange(self, start, end)

    def Activate(self):
        pass

    @property
    def ParagraphFormat(self):
        return self

    # set_reading_order assigns this and then reads it back to confirm the
    # value took, so the fake has to behave the same way.
    @property
    def ReadingOrder(self):
        return self.reading_order

    @ReadingOrder.setter
    def ReadingOrder(self, value):
        self.reading_order = value


class FakeWord:
    def __init__(self, doc):
        self.ActiveDocument = doc
        self.Selection = FakeSelection(doc)

        class Docs:
            Count = 1

        self.Documents = Docs()

    @property
    def UndoRecord(self):
        raise AttributeError("older Word")      # the no-undo-record path


def dictate(doc, *phrases, rtl=False):
    word = FakeWord(doc)
    results = [wordio.insert_dictation(word, p, rtl=rtl) for p in phrases]
    return doc, results


doc, _ = dictate(FakeDoc(), "hello")
check("the first phrase goes in with no leading space", doc.text == "hello",
      repr(doc.text))

doc, _ = dictate(FakeDoc(), "hello", "there")
check("a following phrase is joined by a space, not a new paragraph",
      doc.text == "hello there", repr(doc.text))

doc, results = dictate(FakeDoc(), "hello", "there")
check("the cursor ends after the text, so the next phrase follows it",
      doc.cursor == len(doc.text), f"cursor {doc.cursor} of {len(doc.text)}")
check("the range reported matches what was written",
      results[-1]["end"] - results[-1]["start"] == len(results[-1]["text"]),
      str(results[-1]))

doc, _ = dictate(FakeDoc(text="draft ", cursor=6), "continues")
check("no double space when the cursor already follows one",
      doc.text == "draft continues", repr(doc.text))

doc, _ = dictate(FakeDoc(text="line\r", cursor=5), "next")
check("no space inserted at the start of a new paragraph",
      doc.text == "line\rnext", repr(doc.text))

doc, results = dictate(FakeDoc(), "", "   ")
check("an empty phrase writes nothing", doc.text == "", repr(doc.text))
check("and reports that it wrote nothing",
      all(r["text"] == "" for r in results), str(results))

doc = FakeDoc()
dictate(doc, "مرحبا", rtl=True)
check("right-to-left reading order is applied for arabic",
      doc.reading_order == wordio.WD_READING_ORDER_RTL,
      str(doc.reading_order))

doc = FakeDoc()
dictate(doc, "hello", rtl=False)
check("and left-to-right otherwise",
      doc.reading_order == wordio.WD_READING_ORDER_LTR, str(doc.reading_order))

# Insertion must not depend on UndoRecord: it needs Word 2010+, and FakeWord
# above refuses it on purpose.
doc, _ = dictate(FakeDoc(), "works", "anyway")
check("insertion still works where UndoRecord is unavailable",
      doc.text == "works anyway", repr(doc.text))

check("the two insert functions are separate",
      wordio.insert_dictation is not wordio.insert_transcript)


# ----------------------------------------------------------------------- done
print("\n" + "=" * 60)
if failures:
    print(f"FAILED: {len(failures)}")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("all checks passed")
print("=" * 60)
