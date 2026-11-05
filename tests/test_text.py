"""The text pipeline, with nothing but numpy - so this one runs in CI.

tests/test_voice_typing.py is the thorough suite: it records, loads the model,
speaks through SAPI and transcribes. None of that can run on a Linux runner
with no sound card, no Word and no 1.6 GB checkpoint, which is why CI used to
run no tests at all and only checked that the files parse.

What CI can run is the part that is pure text, and that turned out to be
exactly the part that broke. engine/cleanup.py and engine/fillers.py were
complete and tested and imported by nothing: dictations reached Word with the
user's corrections list never applied. The last sections here are the guard
against that happening again - they run the real job function and assert the
text that comes out of it.

Run:  PYTHONUTF8=1 .venv/Scripts/python.exe tests/test_text.py
"""
from __future__ import annotations

import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import cleanup, fillers
from engine.job import polish, run_transcription

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))
    if not ok:
        failures.append(label)


# ----------------------------------------------------------------- cleanup
print("--- repetition the decoder produced, not the speaker ---")

for text, expected, why in [
    ("the the the meeting is at four", "the meeting is at four",
     "three in a row is the decoder"),
    ("بسم الله بسم الله بسم الله خلاص", "بسم الله خلاص",
     "a repeated phrase is the decoder losing its place"),
    ("very very good", "very very good", "twice is something people say"),
    ("لا لا مش كده", "لا لا مش كده", "and in Arabic too"),
    ("نعم ، تمام .  خلاص", "نعم، تمام. خلاص", "spacing around punctuation"),
]:
    check(f"{why}: {text}", cleanup.tidy(text) == expected,
          f"got {cleanup.tidy(text)!r}")

print("\n--- hesitation sounds, and only those ---")

for text, expected, why in [
    ("امم السلام عليكم", "السلام عليكم", "a sound that is not a word"),
    ("um I think so", "I think so", "and in English"),
    ("راوي القصة حضر", "راوي القصة حضر",
     "اوي three times inside a real word survives"),
    ("the box is 5 mm wide", "the box is 5 mm wide",
     "mm is a unit, not a hesitation"),
    ("err on the side of caution", "err on the side of caution",
     "err is a verb"),
    ("يعني إيه الكلام ده", "يعني إيه الكلام ده", "يعني carries meaning"),
]:
    check(f"{why}: {text}", fillers.strip_fillers(text) == expected,
          f"got {fillers.strip_fillers(text)!r}")

print("\n--- replacements the user wrote ---")

pairs = cleanup.parse_corrections(
    "سدى = صدى\n# a comment\nعبد = Abd\nعبد الله = Abdullah")
check("comments and blank lines are ignored", len(pairs) == 3, str(len(pairs)))
check("the longest match wins, not the first",
      cleanup.apply_corrections("مع عبد الله", pairs) == "مع Abdullah",
      cleanup.apply_corrections("مع عبد الله", pairs))
check("a replacement cannot eat the inside of a word",
      cleanup.apply_corrections("سداسي", pairs) == "سداسي")


# ----------------------------------------------- the polish, through the job
# These run the real job function over a stand-in backend, which is the only
# way to assert that the pipeline *calls* any of the above. For a while it did
# not, and every test in the sections before this one still passed.
print("\n--- the polish, as the job actually runs it ---")


class FakeBackend:
    name = label = "fake"
    supports_resume = False

    def __init__(self, rows):
        self.rows = rows
        self.asked_language = "unset"
        self.opts: dict | None = None
        self.detections = 0

    def stream_segments(self, audio, language, opts):
        self.asked_language = language
        self.opts = opts
        yield from self.rows

    def detect_language(self, audio=None, vad_filter=False, **kw):
        self.detections += 1
        return "ar", 0.99, [("ar", 0.99)]


def write_wav(audio: np.ndarray, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
    return path


def run_job(rows, **kw):
    with tempfile.TemporaryDirectory() as td:
        wav = write_wav((0.2 * np.sin(np.linspace(0, 400, 16000 * 4))
                         ).astype(np.float32), Path(td) / "d.wav")
        backend = FakeBackend(rows)
        return run_transcription(wav, backend, Path(td) / "out", **kw), backend


def seg(text, start=0.0, end=2.0, language="ar"):
    return {"id": 0, "language": language, "start": start, "end": end,
            "text": text, "avg_logprob": -0.2, "no_speech_prob": 0.01,
            "compression_ratio": 1.1}


for rows, expected, why in [
    ([seg("امم السلام عليكم")], "السلام عليكم",
     "a dictation gets its hesitations removed"),
    ([seg("the the the meeting is at four", language="en")],
     "the meeting is at four", "and the decoder's stutter collapsed"),
    ([seg("امم", 0.0, 0.5), seg("تمام كده", 0.5, 2.0)], "تمام كده",
     "a segment of pure hesitation is dropped"),
]:
    meta, _ = run_job(rows, dictation=True)
    check(why, meta["text"] == expected, meta["text"])

meta, _ = run_job([seg("مع سدى للتفريغ")], dictation=True,
                  corrections="سدى = صدى")
check("and the replacements the user wrote", meta["text"] == "مع صدى للتفريغ",
      meta["text"])

# The guard on the thing that was asked for in so many words: transcribing a
# file stays exactly as it was, polish and all.
meta, _ = run_job([seg("امم السلام عليكم")])
check("a file is left exactly as the model wrote it",
      meta["text"] == "امم السلام عليكم", meta["text"])

check("polish is a no-op on an empty transcript", polish([]) == [])


# --------------------------------------------------- one language, voted for
# The decision, in the user's words: one language per recording, detected by
# the model, with the reading direction following it - the same as a file.
#
# Mixed language used to be the default: every chunk was handed over with no
# language and chose its own, so one breath could move between Arabic, English
# and Spanish. Measured on this machine before it was removed:
#
#     5.5 s Arabic, per-chunk detection   16.3 s
#     5.5 s Arabic, one language given     5.6 s   byte-identical text
#
# It also left the reading direction with no single answer, and let one chunk
# heard as the wrong language come back in the wrong script.
print("\n--- one language for the whole recording ---")

_, backend = run_job([seg("x")], dictation=True)
check("a dictation votes on the language like a file does",
      backend.detections > 0, f"{backend.detections} detections")
check("and hands every chunk that one language",
      backend.asked_language == "ar", repr(backend.asked_language))
check("asked in sentence-sized chunks, for the short encoder window",
      backend.opts.get("chunk_target_s") is not None,
      str(backend.opts.get("chunk_target_s")))

_, backend = run_job([seg("x")], dictation=True, language="en")
check("a language the user pinned skips the vote",
      backend.asked_language == "en" and backend.detections == 0,
      f"{backend.asked_language!r}, {backend.detections} detections")

meta, _ = run_job([seg("السلام عليكم")], dictation=True)
check("the direction follows the language, right to left for Arabic",
      meta["rtl"] is True, str(meta["rtl"]))
meta, _ = run_job([seg("hello there", language="en")], dictation=True,
                  language="en")
check("and left to right for English", meta["rtl"] is False, str(meta["rtl"]))


# ------------------------------------------------- how the text reaches Word
# Source checks, because the real thing needs Word running. Both of these were
# bugs: the text of two jobs ran together as one sentence, and before that two
# dictations came out in the opposite order to the one they were spoken in.
print("\n--- how it lands in the document ---")

word_source = (ROOT / "app" / "wordio.py").read_text(encoding="utf-8")
check("each job has a blank line put in front of it",
      'lead = "\\n" * (2 - breaks_before(doc, start))' in word_source)
check("made up to, not added blindly, so it is never a stack of them",
      "def breaks_before" in word_source)
check("the caret is left after the text, so the next job follows it",
      "sel.SetRange(end, end)" in word_source)

main_source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
check("the dictation shortcut is not gated on Word being in front",
      "if self.dictation.active or self._hotkey_allowed():\n            self.dictate_now()"
      not in main_source)
check("a dictation goes back to the window it started in",
      "target = job.target_hwnd" in main_source
      and "sendtext.focus_window(target)" in main_source)
check("and only Word takes the COM path",
      'if process.lower() != "winword.exe":' in main_source)
check("a window that has closed falls back to the one in front",
      "the window it started in has " in main_source)
check("a second dictation into the same window gets a blank line",
      'lead = "\\n\\n" if target and target == self._last_insert_hwnd else ""'
      in main_source)

dictate_source = (ROOT / "app" / "dictate.py").read_text(encoding="utf-8")
check("the window is remembered when the recording starts, not at the end",
      "self._target_hwnd, window_class, process = sendtext.focused_window()"
      in dictate_source and "target_hwnd=self._target_hwnd," in dictate_source)

gui_source = (ROOT / "app" / "gui.py").read_text(encoding="utf-8")
check("the progress window is shown without taking the foreground",
      "SW_SHOWNA = 8" in gui_source)

send_source = (ROOT / "app" / "sendtext.py").read_text(encoding="utf-8")
check("the modifier keys are released before anything is pasted",
      "def release_modifiers" in send_source
      and "released = release_modifiers()" in send_source)
check("the user's clipboard is put back afterwards",
      "_restore_clipboard_later" in send_source)
check("a password field is refused rather than filled",
      "def looks_like_password_field" in send_source
      and '"refused": "password"' in send_source)
check("and a refused keystroke is reported rather than assumed",
      "!= len(batch)" in send_source and "if sent != 4:" in send_source)
check("the focus restore reports whether it worked",
      "def focus_window" in send_source
      and "if u32.GetForegroundWindow() == hwnd:" in send_source)


# ------------------------------------------------------ the window and tray
# Source checks; tests/test_app.py drives the real window with real keys.
print("\n--- the one window, and the tray ---")

gui_source = (ROOT / "app" / "gui.py").read_text(encoding="utf-8")
check("Escape, Ctrl+F4 and Ctrl+W all close the window",
      "def on_char_hook" in gui_source
      and "ctrl and key in (wx.WXK_F4, ord(\"W\"))" in gui_source)
check("through CHAR_HOOK, because the focused control ate the accelerator",
      "EVT_CHAR_HOOK" in gui_source)
check("closing hides and never quits",
      "event.Veto()" in gui_source and "def dismiss" in gui_source)
check("and a closed window is not dragged back open by the next job",
      "self.app_frame.window_dismissed" in gui_source)

# One window at a time, not one per job. It is built once at startup; the only
# other construction is the language change, which destroys the old one first
# because a right-to-left interface needs a mirrored layout that an existing
# window cannot adopt cleanly.
check("there is one window at a time",
      main_source.count("MainWindow(self)") == 2
      and "self.window.Destroy()\n            self.window = MainWindow(self)"
      in main_source)
check("the tray icon shows the state as a light",
      "TRAY_LIGHTS" in main_source and "def lit_icon" in main_source)
check("the tray menu has a jobs submenu",
      "def _jobs_menu" in main_source
      and 'm.AppendSubMenu(self._jobs_menu(), t("tray_jobs"))' in main_source)
check("choosing a job opens the window at it, rather than acting on it",
      "self.frame.open_window(select=i)" in main_source)
check("and double-clicking the tray opens the window",
      "EVT_TASKBAR_LEFT_DCLICK" in main_source)

check("memory is handed back on a timer",
      "HOUSEKEEPING_MS" in main_source
      and "def on_housekeeping" in main_source)
check("never in the middle of a job",
      "if self.jobs.busy or self.jobs.queued_count or self.dictation.active:"
      in main_source)
# The trim does not choose what it evicts, and the model's weights are 1.6 GB
# of pages that are meant to be resident. Trimming them buys a smaller number
# in Task Manager and charges the next dictation for it.
check("and the full trim only when no model is loaded",
      "memory.release(trim=self.backend is None)" in main_source)

# Imported on any platform, which is the point: this file runs on a Linux
# runner, and the module is all Windows below the collect. It used to import
# ctypes.wintypes at the top, which does not exist there.
from app import memory                                              # noqa: E402

check("releasing never raises, on any platform",
      isinstance(memory.release(trim=False), float))
if memory.WINDOWS:
    check("and the memory query returns a real number",
          memory.working_set_mb() > 1.0, f"{memory.working_set_mb():.0f} MB")
else:
    check("and reports 'unknown' rather than crashing off Windows",
          memory.working_set_mb() == 0.0)


print("\n--- the log ---")

from engine import applog                                            # noqa: E402

check("writing to the log never raises",
      applog.write("a line from the tests", where="test") is None)
check("and neither does logging a failure",
      applog.failed("a deliberate failure", ValueError("x"),
                    where="test") is None)
check("it rotates rather than growing without end",
      applog.MAX_BYTES <= 2_000_000, f"{applog.MAX_BYTES} bytes")


print("\n--- one install location, and a model that is never lost twice ---")

# The installer settings are checked as text because compiling an Inno script
# needs Windows and Inno Setup, and this file runs on a Linux runner. They are
# worth checking anywhere: these three lines are the difference between the
# program and its data agreeing on a folder and not.
iss = (ROOT / "installer" / "Sada.iss").read_text(encoding="utf-8")
check("the install folder is under the user's own profile",
      "DefaultDirName={localappdata}\\Programs\\{#AppName}" in iss)
check("the wizard offers no folder to change it to",
      "DisableDirPage=yes" in iss)
check("an upgrade lands on top of the existing installation",
      "UsePreviousAppDir=yes" in iss)
# This is the one that actually moved the install location. "Install for all
# users" restarts Setup elevated, and {localappdata} in an elevated process is
# the administrator's profile - so the program installed under one account and
# ran as another, looking for its models in a folder nothing had written to.
directives = [line for line in iss.splitlines()
              if line.strip() and not line.lstrip().startswith(";")]
check("and there is no way to install it elevated into another profile",
      "PrivilegesRequired=lowest" in directives
      and not any(line.startswith("PrivilegesRequiredOverridesAllowed")
                  for line in directives))
check("the AppId has not changed, so every update replaces the same folder",
      "{8E2C1F4A-9D3B-4A77-B5E1-2C9F6A0D1B83}" in iss)

from engine import paths                                             # noqa: E402

check("checkpoints are looked for in more than one folder",
      len(paths.candidate_models_dirs()) > 1,
      str([str(p) for p in paths.candidate_models_dirs()]))
check("starting with the one this version writes to",
      paths.candidate_models_dirs()[0] == paths.MODELS_DIR)
check("no folder is searched twice",
      len(paths.candidate_models_dirs())
      == len({str(p).lower() for p in paths.candidate_models_dirs()}))
check("a checkpoint that is nowhere is reported as nowhere, not guessed at",
      paths.find_checkpoint("no-such-model-420.pt") is None)

# The symptom of getting this wrong is not an error message. load_model() is
# handed a path; if the path does not exist it treats the string as a model
# name and downloads the checkpoint itself, 1.5 GB, with nothing asked.
backends_source = (ROOT / "engine" / "backends.py").read_text(encoding="utf-8")
check("the model is loaded from where the file is, not where it would be put",
      'local = find_checkpoint(f"{ckpt}.pt")' in backends_source
      and "str(local) if local else ckpt" in backends_source)
models_source = (ROOT / "engine" / "models.py").read_text(encoding="utf-8")
check("and Settings calls a model installed wherever it actually is",
      "path = find_checkpoint(filename) or (MODELS_DIR / filename)"
      in models_source)
check("while a download still goes to the one folder we write to",
      'target = MODELS_DIR / f"{m.checkpoint}.pt"' in models_source)

# The folder and what is in it have to be readable by someone who cannot see
# the screen: this was the hardest thing anybody had to report, because the
# program said "not installed" and nothing named the folder it had looked in.
gui_source = (ROOT / "app" / "gui.py").read_text(encoding="utf-8")
check("Settings says which folder, in full, and what was found in it",
      "def refresh_models_where" in gui_source
      and "models_found_at" in gui_source)
check("with a button that names every folder searched",
      "def on_find_model" in gui_source and "models_searched" in gui_source)
check("and one to put the models somewhere else entirely",
      "def on_models_folder" in gui_source and "wx.DirDialog" in gui_source)
check("the resolved folder is in the log, so a report can name it",
      "models={paths.MODELS_DIR}" in main_source)

from app import i18n                                                 # noqa: E402

for key in ("models_folder_is", "models_found_none", "models_searched",
            "btn_find_model", "btn_models_folder", "say_models_none"):
    pair = i18n.S.get(key)
    check(f"{key} is translated, not just English",
          bool(pair) and len(pair) > 1 and bool(pair[1]), str(pair))


print("\n" + "=" * 60)
if failures:
    print(f"FAILED: {len(failures)}")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("all text checks passed")
print("=" * 60)
