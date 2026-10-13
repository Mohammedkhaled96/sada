"""Test the application layer: settings, hotkey parsing, the GUI windows, and
real insertion into Word over COM.

The Word part launches Word, writes into a *new* document, checks the result and
closes it **without saving**. It never touches a document that was already open.

Run:  PYTHONUTF8=1 .venv/Scripts/python.exe tests/test_app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, i18n
from app.config import Settings, format_hotkey, parse_hotkey
from app.i18n import t

# A sample of Arabic used to prove non-ASCII text survives a round trip. Kept as
# a named constant so the assertions below read in English.
ARABIC_SAMPLE = "مُكَفَّفين تفريغ"

PASS, FAIL = "  ok  ", " FAIL "
failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""),
          flush=True)
    if not cond:
        failures.append(name)


# ----------------------------------------------------------------- hotkeys
print("\n--- hotkey parsing ---")
MOD_ALT, MOD_CTRL, MOD_SHIFT, MOD_WIN = 1, 2, 4, 8

check("ctrl+shift+letter", parse_hotkey("Ctrl+Shift+T") == (MOD_CTRL | MOD_SHIFT, ord("T")))
check("case and spacing are forgiving",
      parse_hotkey("  ctrl + SHIFT + t ") == parse_hotkey("Ctrl+Shift+T"))
check("dash separator works", parse_hotkey("Ctrl-Alt-K") == (MOD_CTRL | MOD_ALT, ord("K")))
check("function keys", parse_hotkey("Ctrl+F9") == (MOD_CTRL, 0x78), str(parse_hotkey("Ctrl+F9")))
check("named keys", parse_hotkey("Alt+Space") == (MOD_ALT, 0x20))
check("win modifier", parse_hotkey("Win+J") == (MOD_WIN, ord("J")))
check("digits", parse_hotkey("Ctrl+Shift+5") == (MOD_CTRL | MOD_SHIFT, ord("5")))

check("rejects a bare letter", parse_hotkey("T") is None,
      "would swallow the key from every app on the machine")
check("rejects modifier with no key", parse_hotkey("Ctrl+Shift") is None)
check("rejects empty", parse_hotkey("") is None and parse_hotkey("   ") is None)
check("rejects nonsense", parse_hotkey("Ctrl+Banana") is None)

check("formats canonically", format_hotkey("shift+ctrl+t") == "Ctrl+Shift+T",
      format_hotkey("shift+ctrl+t"))
check("format round-trips", parse_hotkey(format_hotkey("alt+f4")) == parse_hotkey("alt+f4"))

# ---------------------------------------------------------------- settings
print("\n--- settings ---")
s = Settings()
check("defaults are valid", all(parse_hotkey(getattr(s, f)) for f in
      ("hotkey_transcribe", "hotkey_settings", "hotkey_status", "hotkey_cancel",
       "hotkey_window")))
check("default shortcuts are all distinct",
      len({parse_hotkey(getattr(s, f)) for f in
           ("hotkey_transcribe", "hotkey_settings", "hotkey_status",
            "hotkey_cancel", "hotkey_window")}) == 5,
      "five commands, five different shortcuts")
check("interface defaults to english", Settings().ui_language == "en")
check("auto language maps to None", Settings(language="auto").language_or_none is None)
check("explicit language passes through", Settings(language="ar").language_or_none == "ar")

bad = Settings(model="nope", device="gpu?", language="klingon", insert_mode="sideways",
               speak_progress_every=99999, hotkey_transcribe="Banana",
               ui_language="klingon")
bad.validate()
check("invalid interface language falls back to english",
      bad.ui_language == "en", bad.ui_language)
check("invalid model falls back", bad.model == "turbo", bad.model)
check("invalid device falls back", bad.device == "auto", bad.device)
check("invalid language falls back", bad.language == "auto", bad.language)
check("invalid insert mode falls back", bad.insert_mode == "cursor", bad.insert_mode)
check("out-of-range interval is clamped", bad.speak_progress_every == 600,
      str(bad.speak_progress_every))
check("invalid hotkey reverts to its default",
      bad.hotkey_transcribe == "Ctrl+Shift+T", bad.hotkey_transcribe)

import json
import tempfile

with tempfile.TemporaryDirectory() as td:
    orig_file, orig_dir = config.SETTINGS_FILE, config.STATE_DIR
    config.STATE_DIR = Path(td)
    config.SETTINGS_FILE = Path(td) / "settings.json"
    try:
        s2 = Settings(model="large-v3", hotwords=ARABIC_SAMPLE, timestamps=True)
        config.save(s2)
        loaded = config.load()
        check("settings survive a save/load round trip",
              loaded.model == "large-v3" and loaded.timestamps is True)
        check("arabic hotwords survive", loaded.hotwords == "مُكَفَّفين تفريغ",
              loaded.hotwords)

        config.SETTINGS_FILE.write_text("{ this is not json", encoding="utf-8")
        check("a corrupt file falls back to defaults", config.load().model == "turbo")

        config.SETTINGS_FILE.write_text(
            json.dumps({"model": "small", "bogus_key_from_a_future_version": 1}),
            encoding="utf-8")
        check("unknown keys are ignored, known ones kept",
              config.load().model == "small")

        config.SETTINGS_FILE.unlink()
        check("a missing file falls back to defaults", config.load().model == "turbo")
    finally:
        config.SETTINGS_FILE, config.STATE_DIR = orig_file, orig_dir

# -------------------------------------------------------------------- speech
print("\n--- screen reader channel ---")
from app.speech import Speaker

sp = Speaker(enabled=True, allow_sapi=False)
check("picks a channel without crashing", sp.channel in ("jaws", "nvda", "sapi", "none"),
      f"{sp.channel} — {sp.describe()}")
check("say() never raises", sp.say("اختبار") in (True, False))
i18n.set_language("en")
check("duration reads as words not digits (en)",
      i18n.duration(3725) == "1 hour and 2 minutes", i18n.duration(3725))
check("short duration (en)", i18n.duration(45) == "45 seconds", i18n.duration(45))
check("zero duration (en)", i18n.duration(0) == "less than a second", i18n.duration(0))
i18n.set_language("ar")
check("duration in arabic", i18n.duration(3725) == "1 ساعة و2 دقيقة", i18n.duration(3725))
check("short duration (ar)", i18n.duration(45) == "45 ثانية", i18n.duration(45))
i18n.set_language("en")

# ----------------------------------------------------------------------- gui
print("\n--- gui construction ---")
import wx

wxapp = wx.App(False)
from app.gui import HelpDialog, SettingsDialog

dlg = SettingsDialog(None, Settings(), "NVDA")
labelled = []
for child in dlg.GetChildren():
    for c in child.GetChildren():
        labelled.extend(c.GetChildren() if hasattr(c, "GetChildren") else [])
interactive = [w for w in dlg.GetChildren()[0].GetChildren()
               if isinstance(w, (wx.Choice, wx.TextCtrl, wx.CheckBox, wx.SpinCtrl))]
all_ctrls = []


def walk(w):
    for c in w.GetChildren():
        all_ctrls.append(c)
        walk(c)


walk(dlg)
inputs = [c for c in all_ctrls
          if isinstance(c, (wx.Choice, wx.TextCtrl, wx.CheckBox, wx.SpinCtrl))]
check("settings dialog builds", dlg is not None)
check("has every input group", len(inputs) >= 16, f"{len(inputs)} inputs")
unnamed = [type(c).__name__ for c in inputs
           if not (c.GetName() and c.GetName() not in ("choice", "text", "check",
                                                       "spinctrl", "panel", ""))
           and not isinstance(c, wx.CheckBox)]
check("every non-checkbox input has an accessible name", not unnamed, str(unnamed[:4]))
check("checkboxes carry their own label",
      all(c.GetLabel() for c in inputs if isinstance(c, wx.CheckBox)))
check("escape closes the dialog", dlg.GetEscapeId() == wx.ID_CANCEL)
check("has a default button", dlg.GetDefaultItem() is not None)
dlg.Destroy()

h = HelpDialog(None)
htxt = [c for c in h.GetChildren()[0].GetChildren() if isinstance(c, wx.TextCtrl)]
check("help dialog builds with a readable text field", len(htxt) == 1)
check("help text is read-only", bool(htxt[0].GetWindowStyle() & wx.TE_READONLY))
check("help text is multiline so it can be navigated by line",
      bool(htxt[0].GetWindowStyle() & wx.TE_MULTILINE))
check("help covers the shortcuts and the formats",
      all(w in htxt[0].GetValue()
          for w in ("shortcut", "screen reader", "mp3", "Ctrl+Z")),
      "english help, since english is the default")
h.Destroy()

i18n.set_language("ar")
_ha = HelpDialog(None)
_hat = [c for c in _ha.GetChildren()[0].GetChildren()
        if isinstance(c, wx.TextCtrl)][0]
check("arabic help covers the same ground",
      all(w in _hat.GetValue()
          for w in ("الاختصارات", "قارئ الشاشة", "mp3", "Ctrl+Z")))
_ha.Destroy()
i18n.set_language("en")

# ------------------------------------------------------------------- i18n
print("\n--- interface language ---")
check("english is the default", Settings().ui_language == "en")
check("arabic is selectable", i18n.set_language("ar") == "ar")
check("an unknown code falls back to english", i18n.set_language("zz") == "en")
i18n.set_language("ar")
check("rtl flag follows the language", i18n.is_rtl_ui())
i18n.set_language("en")
check("ltr for english", not i18n.is_rtl_ui())

missing = [k for k, v in i18n.S.items() if not v[0]]
check("every string has an english text", not missing, str(missing[:5]))
untranslated = [k for k, v in i18n.S.items() if len(v) < 2 or not v[1]]
check("every string has an arabic text", not untranslated, str(untranslated[:5]))

import re as _re

bad_ph = []
for _k, (_en, _ar) in i18n.S.items():
    if set(_re.findall(r"\{(\w+)\}", _en)) != set(_re.findall(r"\{(\w+)\}", _ar)):
        bad_ph.append(_k)
check("placeholders match between the two languages", not bad_ph, str(bad_ph[:5]))

check("a missing id returns itself rather than crashing",
      t("no_such_string_id") == "no_such_string_id")
check("placeholders are filled", "42" in t("line_progress", pct=42),
      t("line_progress", pct=42))
check("a missing placeholder does not crash", isinstance(t("line_progress"), str))
check("help text exists in both languages",
      len(i18n.HELP_TEXT["en"]) > 500 and len(i18n.HELP_TEXT["ar"]) > 500)
check("about text exists in both languages",
      len(i18n.ABOUT_TEXT["en"]) > 100 and len(i18n.ABOUT_TEXT["ar"]) > 100)
check("the shortcut placeholder does not collide with the lookup id",
      "Ctrl+Shift+T" in t("btn_transcribe", key="Ctrl+Shift+T"),
      t("btn_transcribe", key="Ctrl+Shift+T"))

# ------------------------------------------------- accessible names on controls
print("\n--- controls announce what they are FOR, not just their value ---")
_sd = SettingsDialog(None, Settings(), "JAWS")
_named = 0
_unnamed = []
for _attr in ("ui_language", "model", "device", "language", "hotwords",
              "insert_mode", "k_transcribe", "k_window", "k_settings",
              "k_status", "k_cancel", "progress_every", "model_list"):
    _c = getattr(_sd, _attr, None)
    if _c is None:
        continue
    if getattr(_c, "_sada_accessible", None) is None:
        _unnamed.append(_attr)
    else:
        _named += 1
check("every input has an explicit MSAA accessible name", not _unnamed,
      str(_unnamed) if _unnamed else f"{_named} controls")

# The reported bug: a shortcut field announced only "Ctrl Shift T", with no clue
# which command it belonged to. SetName alone does not set the MSAA name on
# Windows, so the name has to be supplied through wx.Accessible.
_kname = _sd.k_transcribe._sada_accessible.GetName(0)[1]
check("a shortcut field names its command, not just the keys",
      "transcrib" in _kname.lower() and "ctrl" not in _kname.lower(), _kname)
check("and the keys are still its value", "Ctrl" in _sd.k_transcribe.GetValue(),
      _sd.k_transcribe.GetValue())
check("each shortcut field has a distinct name",
      len({getattr(_sd, a)._sada_accessible.GetName(0)[1]
           for a in ("k_transcribe", "k_window", "k_settings", "k_status",
                     "k_cancel")}) == 5)
_sd.Destroy()

i18n.set_language("ar")
_sa = SettingsDialog(None, Settings(), "JAWS")
check("the accessible names are translated too",
      "التفريغ" in _sa.k_transcribe._sada_accessible.GetName(0)[1],
      _sa.k_transcribe._sada_accessible.GetName(0)[1])
_sa.Destroy()
i18n.set_language("en")

# ------------------------------------------- model list: selection and progress
print("\n--- model list keeps your selection ---")
from engine import models as _models

_md = SettingsDialog(None, Settings(), "JAWS")
_keys = [m.key for m in _md._model_rows]
# Pick something that is NOT the configured model, which is what the list used
# to jump back to on every refresh.
_other = next(k for k in _keys if k != Settings().model)
_md.model_list.Select(_keys.index(_other))
check("a model can be selected", _md._selected_model().key == _other, _other)

_md.refresh_models()
check("the selection survives a refresh", _md._selected_model().key == _other,
      f"{_md._selected_model().key} (was {_other})")
check("it did NOT jump to the configured model",
      _md._selected_model().key != Settings().model,
      "this is what made Delete remove the wrong model")

_md._model_rows = []        # as if the list had never been populated
_md.refresh_models()
check("with nothing selected it falls back to the configured model",
      _md._selected_model().key == Settings().model,
      _md._selected_model().key)

print("\n--- download progress reports measured values ---")
check("the status field is read-only so it can be read on demand",
      bool(_md.dl_status.GetWindowStyle() & wx.TE_READONLY))
check("the status field is a text control, not static text",
      isinstance(_md.dl_status, wx.TextCtrl),
      "static text is not announced and cannot be navigated into")
check("the status field has an accessible name",
      getattr(_md.dl_status, "_sada_accessible", None) is not None)
check("the gauge has 1000 steps, so tenths of a percent move it",
      _md.dl_gauge.GetRange() == 1000, str(_md.dl_gauge.GetRange()))

# The sentences must stand on their own. "about {eta} remaining" with
# "still measuring" substituted in reads as gibberish, which it once did.
_calc = t("say_dl_progress_calc", pct=10)
check("the unmeasured case is its own sentence",
      "remaining" not in _calc.lower() and "10" in _calc, _calc)
_eta = t("say_dl_progress", pct=50, eta="3 seconds")
check("the measured case names a real duration", "3 seconds" in _eta, _eta)
check("there is a sentence for nearly finished", bool(t("say_dl_almost", pct=90)),
      t("say_dl_almost", pct=90))
check("completion reports size and elapsed time",
      all(x in t("say_dl_done", name="small", total="461 MB", elapsed="6 seconds")
          for x in ("small", "461 MB", "6 seconds")))
check("deleting announces the size being freed",
      "461 MB" in t("delete_done_detail", name="small", size="461 MB"))
_md.Destroy()

# ---------------------------------------------------------------- main window
print("\n--- main window ---")
from app.gui import MainWindow


class FakeApp:
    settings = Settings()

    def start_transcription(self): pass
    def speak_status(self): pass
    def cancel_job(self): pass
    def open_settings(self): pass
    def open_help(self): pass
    def quit_app(self): pass


mw = MainWindow(FakeApp())
check("main window builds", mw is not None, mw.GetTitle())
check("offers every command",
      set(mw.buttons) == {"transcribe", "window_status", "cancel", "settings",
                          "help", "about", "quit"},
      ", ".join(sorted(mw.buttons)))
check("a blind user can quit without the tray icon", "quit" in mw.buttons)
check("buttons show their shortcut",
      "Ctrl+Shift+T" in mw.buttons["transcribe"].GetLabel(),
      mw.buttons["transcribe"].GetLabel())
check("buttons have access keys for keyboard navigation",
      all("&" in b.GetLabel() for b in mw.buttons.values()))
check("has a default button", mw.GetDefaultItem() is not None)
check("status field is read-only", bool(mw.status.GetWindowStyle() & wx.TE_READONLY))
mw.set_status("Transcribing\nProgress: 10 percent")
check("status shows text", "10 percent" in mw.status.GetValue())

FakeApp.settings.hotkey_transcribe = "Ctrl+Alt+Z"
mw.refresh_labels()
check("labels follow a shortcut change",
      "Ctrl+Alt+Z" in mw.buttons["transcribe"].GetLabel(),
      mw.buttons["transcribe"].GetLabel())
FakeApp.settings.hotkey_transcribe = "Ctrl+Shift+T"
mw.Destroy()

print("\n--- every window builds in both languages ---")
for _lang in ("en", "ar"):
    i18n.set_language(_lang)
    _rtl_expected = _lang == "ar"
    _d = SettingsDialog(None, Settings(), "JAWS")
    check(f"settings dialog builds in {_lang}", bool(_d.GetTitle()), _d.GetTitle())
    check(f"layout direction is correct for {_lang}",
          (_d.GetLayoutDirection() == wx.Layout_RightToLeft) == _rtl_expected)
    _d.Destroy()
    _h2 = HelpDialog(None)
    check(f"help dialog builds in {_lang}", bool(_h2.GetTitle()), _h2.GetTitle())
    _h2.Destroy()
    _m2 = MainWindow(FakeApp())
    check(f"main window builds in {_lang}", bool(_m2.GetTitle()), _m2.GetTitle())
    _m2.Destroy()
i18n.set_language("en")

# ---------------------------------------------------------------------- word
print("\n--- word integration (launches Word, writes to a NEW document) ---")
from app import wordio

wordio.init_thread()
word = None
doc = None
try:
    word = wordio.get_word(start_if_needed=True)
    word.Visible = False
    doc = word.Documents.Add()
    check("attaches to Word", word is not None, str(word.Version))

    sample = "السلام عليكم ورحمة الله وبركاته\n\nهذا اختبار للإدراج."
    where = wordio.insert_transcript(word, sample, rtl=True, mode="cursor")
    body = doc.Content.Text
    check("inserts the text", "السلام عليكم" in body, body.strip()[:40])
    check("keeps the paragraph break", "هذا اختبار للإدراج" in body)
    check("reports where it landed", where["end"] > where["start"],
          f"{where['start']}..{where['end']}")
    check("cursor is left at the start of the new text",
          word.Selection.Range.Start == where["start"],
          f"{word.Selection.Range.Start} vs {where['start']}")

    check("reports that it set the reading order", where["reading_order_set"] is True)
    rng = doc.Range(where["start"], where["start"] + 5)
    check("sets right-to-left reading order",
          int(rng.ParagraphFormat.ReadingOrder) == wordio.WD_READING_ORDER_RTL,
          f"ReadingOrder={rng.ParagraphFormat.ReadingOrder} "
          f"Alignment={rng.ParagraphFormat.Alignment} (Word sets alignment itself)")

    before = doc.Content.Text
    doc.Undo()
    check("the whole insertion is a single undo",
          "السلام عليكم" not in doc.Content.Text,
          f"{len(before)} -> {len(doc.Content.Text)} chars")

    ltr = wordio.insert_transcript(word, "Hello world.", rtl=False, mode="end")
    check("left-to-right path works too", "Hello world" in doc.Content.Text)
    check("left-to-right reading order also verified", ltr["reading_order_set"] is True)

    check("detects whether Word is in the foreground",
          wordio.word_is_foreground() in (True, False))
except Exception as exc:
    check("word integration", False, f"{type(exc).__name__}: {exc}")
finally:
    try:
        if doc is not None:
            doc.Close(SaveChanges=0)        # 0 = wdDoNotSaveChanges
    except Exception:
        pass
    try:
        if word is not None and word.Documents.Count == 0:
            word.Quit(SaveChanges=0)
    except Exception:
        pass
    # Drop the COM references before uninitialising, or Word complains on the
    # way out with "Win32 exception occurred releasing IUnknown".
    doc = None
    word = None
    import gc
    gc.collect()
    wordio.uninit_thread()

print("\n--- no-Word error path ---")
i18n.set_language("en")
check("the no-Word message is a sentence, not a code",
      "Word is not open" in t("word_not_open"), t("word_not_open"))
i18n.set_language("ar")
check("and is a sentence in arabic too", "وورد" in t("word_not_open"))
i18n.set_language("en")

print("\n" + "=" * 60)
if failures:
    print(f"FAILED: {len(failures)}")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("all app checks passed")
print("=" * 60)
