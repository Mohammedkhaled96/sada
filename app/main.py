"""Sada - the application the user actually runs.

Sits in the tray, holds the Whisper model in RAM, and listens for global
hotkeys. Press one and it asks for a file, transcribes it, and writes the text
into the Word document you already have open.

The model lives in this process rather than behind the HTTP daemon, so there is
no socket, no token and no second process in the common path. engine/daemon.py
remains available for scripting and automation.

Run:  PYTHONUTF8=1 .venv/Scripts/pythonw.exe -m app.main
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import wx
import wx.adv

from app import config, i18n, memory, sendtext, wordio
from app.dictate import DictationController
from app.gui import HelpDialog, MainWindow, SettingsDialog
from app.i18n import duration as dur
from app.i18n import t
from app.jobs import JobManager
from app.speech import Speaker
from engine.backends import load_backend
from engine import applog, models, paths
from engine.device import pick_device

from engine.paths import OUT_DIR
SINGLE_INSTANCE = "Sada-single-instance"

AUDIO_EXT = "*.mp3;*.m4a;*.wav;*.ogg;*.opus;*.wma;*.flac;*.aac"
VIDEO_EXT = "*.mp4;*.mkv;*.avi;*.mov;*.webm"

HK_TRANSCRIBE, HK_SETTINGS, HK_STATUS, HK_CANCEL, HK_WINDOW = 1, 2, 3, 4, 5
HK_DICTATE = 6

# How often to hand unused memory back to Windows. Three minutes: often enough
# that the memory a finished job was holding does not sit there for the rest of
# the day, and rare enough that it costs nothing measurable. It only runs when
# no job and no dictation is going - see AppFrame.on_housekeeping.
HOUSEKEEPING_MS = 3 * 60 * 1000

_icon_cache = None


def icon_path() -> Path | None:
    """The .ico, both when running from source and inside a frozen build.

    PyInstaller unpacks bundled data under sys._MEIPASS, so the file is not
    where __file__ suggests.
    """
    roots = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots += [Path(meipass) / "assets", Path(meipass)]
    roots.append(Path(__file__).resolve().parent.parent / "assets")
    for root in roots:
        candidate = root / "sada.ico"
        if candidate.exists():
            return candidate
    return None


def app_icon() -> "wx.Icon":
    """The window and tray icon, loaded once.

    Falls back to a plain coloured square if the file is missing, because a
    missing icon must not stop the program from starting - the icon is the one
    part of this application its users cannot see anyway.
    """
    global _icon_cache
    if _icon_cache is not None:
        return _icon_cache
    path = icon_path()
    if path is not None:
        try:
            _icon_cache = wx.Icon(str(path), wx.BITMAP_TYPE_ICO)
            if _icon_cache.IsOk():
                return _icon_cache
        except Exception:
            pass
    bmp = wx.Bitmap(16, 16)
    dc = wx.MemoryDC(bmp)
    dc.SetBackground(wx.Brush(wx.Colour(30, 58, 138)))
    dc.Clear()
    del dc
    _icon_cache = wx.Icon()
    _icon_cache.CopyFromBitmap(bmp)
    return _icon_cache


# What the tray icon says at a glance, as a dot in the corner of the program's
# own icon. The colours are the ordinary ones - green working, amber busy, red
# recording or failed, grey asleep - and the tooltip says the same in words,
# because the dot is for whoever can see it and the tooltip is for everyone.
TRAY_LIGHTS = {
    "tray_ready":       (46, 160, 67),      # green: ready for a shortcut
    "tray_working":     (219, 152, 11),     # amber: transcribing
    "tray_dictating":   (218, 54, 51),      # red: the microphone is open
    "tray_loading":     (219, 152, 11),     # amber: loading the model
    "tray_load_failed": (218, 54, 51),      # red: it did not load
    "tray_unloaded":    (130, 130, 130),    # grey: freed from memory
}
_light_cache: dict[str, "wx.Icon"] = {}


def lit_icon(state: str) -> "wx.Icon":
    """The program's icon with a state light on it, built once per state.

    A tray icon that looks identical whatever the program is doing tells a
    sighted user nothing, and "is it even running?" was the question being
    asked. Drawn over the real icon rather than replacing it, so it is still
    recognisably this program.
    """
    if state in _light_cache:
        return _light_cache[state]
    colour = TRAY_LIGHTS.get(state)
    base = app_icon()
    if colour is None or not base.IsOk():
        return base
    try:
        size = 32
        bmp = wx.Bitmap(size, size)
        dc = wx.MemoryDC(bmp)
        dc.SetBackground(wx.Brush(wx.Colour(0, 0, 0)))
        dc.Clear()
        dc.DrawIcon(base, 0, 0)
        radius = size // 4
        dc.SetBrush(wx.Brush(wx.Colour(*colour)))
        dc.SetPen(wx.Pen(wx.Colour(255, 255, 255), 2))
        dc.DrawCircle(size - radius - 1, size - radius - 1, radius)
        del dc
        bmp.SetMaskColour(wx.Colour(0, 0, 0))
        icon = wx.Icon()
        icon.CopyFromBitmap(bmp)
        _light_cache[state] = icon
        return icon
    except Exception:
        return base                     # a plain icon beats no icon


def wildcard() -> str:
    return (f"{t('wildcard_all_media')}|{AUDIO_EXT};{VIDEO_EXT}"
            f"|{t('wildcard_audio')}|{AUDIO_EXT}"
            f"|{t('wildcard_video')}|{VIDEO_EXT}"
            f"|{t('wildcard_any')}|*.*")


class TrayIcon(wx.adv.TaskBarIcon):
    """A convenience, never the only way to reach anything - every item here
    also has a hotkey, because a tray icon is unreachable without a mouse."""

    # The jobs submenu lists this many at most. A tray menu is read one arrow
    # press at a time, and a hundred finished jobs is a corridor, not a menu.
    MENU_JOBS = 12

    def __init__(self, frame: "AppFrame"):
        super().__init__()
        self.frame = frame
        self.state = "tray_loading"
        self.set_state("tray_loading")
        # Double-clicking a tray icon opens the thing it belongs to, in every
        # other Windows program.
        self.Bind(wx.adv.EVT_TASKBAR_LEFT_DCLICK,
                  lambda e: self.frame.open_window())

    def set_state(self, key: str) -> None:
        self.state = key
        self.SetIcon(lit_icon(key), f"{t('app_name')} - {t(key)}")

    def _jobs_menu(self) -> wx.Menu:
        """Every job and what became of it, newest first.

        Running ones first and then the rest, because the one the user is
        waiting on is the one they opened this for. Selecting a job opens the
        window with it in view rather than acting on it - a menu that cancels
        work on a single click is a menu that cancels work by accident.
        """
        menu = wx.Menu()
        jobs = self.frame.jobs.all()
        if not jobs:
            item = menu.Append(wx.ID_ANY, t("tray_no_jobs"))
            item.Enable(False)
            return menu

        active = [j for j in jobs if j.active]
        done = [j for j in jobs if not j.active]
        shown = active + list(reversed(done))[:max(0, self.MENU_JOBS - len(active))]

        for job in shown:
            if job.state == "running":
                state = t(f"state_{job.progress.state}")
                detail = f"{int(job.percent)}%"
                if job.progress.measured and job.progress.eta_seconds:
                    detail += f", {dur(job.progress.eta_seconds)}"
            elif job.state == "done":
                state = t("jstate_done")
                detail = t("line_words", n=(job.meta or {}).get("words", 0))
            else:
                state = t(f"jstate_{job.state}")
                detail = job.error[:40] if job.error else ""
            label = f"{job.filename} - {state}" + (f" ({detail})" if detail else "")
            # A tab in a menu label is a shortcut column to Windows, and a
            # file name is allowed to contain one.
            item = menu.Append(wx.ID_ANY, label.replace("\t", " "))
            self.Bind(wx.EVT_MENU,
                      lambda e, i=job.id: self.frame.open_window(select=i), item)

        hidden = len(jobs) - len(shown)
        if hidden > 0:
            more = menu.Append(wx.ID_ANY, t("tray_more_jobs", n=hidden))
            self.Bind(wx.EVT_MENU, lambda e: self.frame.open_window(), more)
        menu.AppendSeparator()
        clear = menu.Append(wx.ID_ANY, t("btn_clear_finished"))
        self.Bind(wx.EVT_MENU, lambda e: self.frame.clear_finished_jobs(), clear)
        return menu

    def CreatePopupMenu(self):
        m = wx.Menu()
        s = self.frame.settings

        # What it is doing, first and unclickable - the same sentence the
        # tooltip carries, where a screen reader will read it on the way past.
        heading = m.Append(wx.ID_ANY, f"{t('app_name')} - {t(self.state)}")
        heading.Enable(False)
        m.AppendSeparator()

        for label, handler in (
            (f"{t('btn_transcribe_plain')}\t{s.hotkey_transcribe}",
             self.frame.start_transcription),
            (f"{t('tray_dictate')}\t{s.hotkey_dictate}", self.frame.dictate_now),
            (f"{t('btn_cancel')}\t{s.hotkey_cancel}", self.frame.cancel_job),
        ):
            item = m.Append(wx.ID_ANY, label)
            self.Bind(wx.EVT_MENU, lambda e, h=handler: h(), item)

        m.AppendSubMenu(self._jobs_menu(), t("tray_jobs"))
        m.AppendSeparator()

        for label, handler in (
            (f"{t('field_k_status').rstrip(':')}\t{s.hotkey_status}",
             self.frame.speak_status),
            (f"{t('tray_window')}\t{s.hotkey_window}", self.frame.open_window),
            (f"{t('tray_settings')}\t{s.hotkey_settings}", self.frame.open_settings),
            (t("tray_help"), self.frame.open_help),
            (t("tray_free_memory") if self.frame.backend is not None
             else t("tray_load_model"), self.frame.toggle_model_loaded),
        ):
            item = m.Append(wx.ID_ANY, label)
            self.Bind(wx.EVT_MENU, lambda e, h=handler: h(), item)

        m.AppendSeparator()
        quit_item = m.Append(wx.ID_ANY, t("tray_quit"))
        self.Bind(wx.EVT_MENU, lambda e: self.frame.quit_app(), quit_item)
        return m


class AppFrame(wx.Frame):
    """Hidden frame that owns the hotkeys. wx routes EVT_HOTKEY to a window, so
    there has to be one, but the user never sees it."""

    def __init__(self):
        super().__init__(None, title=t("app_name"), size=(1, 1),
                         style=wx.FRAME_NO_TASKBAR)
        self.settings = config.load()          # also applies the saved language
        self.speaker = Speaker(self.settings.speak, self.settings.speak_via_sapi)
        # The resolved folders go in the log because the hardest thing to
        # diagnose from the outside was a model the program could not see:
        # the user had the file, the program said it was not installed, and
        # nothing anywhere said which folder had been looked in.
        found = paths.found_checkpoints()
        applog.write(
            f"starting: model={self.settings.model} device={self.settings.device} "
            f"language={self.settings.language} insert={self.settings.insert_mode} "
            f"state={paths.STATE_DIR} models={paths.MODELS_DIR} "
            f"checkpoints={[str(p) for p in found] or 'none found'}", where="app")

        self.backend = None
        self.backend_error: str | None = None
        self.device = pick_device(self.settings.device)
        self.model_ready = threading.Event()
        self._loading = True
        # Set when the dictation shortcut was pressed with no model loaded, so
        # the recording can start by itself once there is one.
        self._start_dictation_when_loaded = False
        # The window the last dictation outside Word was written into. A
        # second dictation into that same window gets a blank line in front of
        # it; a first one into a fresh window does not.
        self._last_insert_hwnd = 0
        # Set when the user closes the program window. Work carries on and the
        # window keeps itself up to date, but it does not reappear by itself.
        self.window_dismissed = False

        # Clear leftovers from a download that was cancelled or interrupted:
        # a zero-byte or truncated checkpoint otherwise sits in the models
        # folder forever, taking space and looking like a half-installed model.
        try:
            models.cleanup_partials()
        except Exception:
            pass            # housekeeping must never stop the app from starting

        # One settings file, not two. Settings moved out of the user profile
        # and into the program's own folder, so an older copy could be left
        # behind there - a second settings.json that nothing reads.
        #
        # This runs after config.load(), which has already copied those
        # settings across, and never touches the folder currently in use. Any
        # checkpoint in that folder is left alone and goes on being used from
        # there; engine/paths.py searches it.
        self.state_cleanup = {"removed": [], "kept": [], "failed": []}
        try:
            self.state_cleanup = paths.cleanup_duplicate_state()
        except Exception:
            pass            # housekeeping must never stop the app from starting

        # A checkpoint inside the installation folder is the one that an
        # upgrade would delete, so it is moved out before that can happen.
        # Everything found anywhere else is used where it lies.
        try:
            for source, destination in paths.adopt_stray_checkpoints():
                applog.write(f"moved {source} to {destination} - the "
                             f"installation folder is replaced by an upgrade",
                             where="models")
        except Exception:
            pass

        self._last_spoken = 0.0
        self.jobs = JobManager(lambda: self.backend, OUT_DIR,
                               on_change=self._job_changed,
                               on_finished=self._job_finished)

        self.dictation = DictationController(self)

        self.window = MainWindow(self)
        self.window.SetIcon(app_icon())
        self.SetIcon(app_icon())
        self.tray = TrayIcon(self)
        self._registered: list[int] = []
        # Kept, because the dictation check reports which shortcut Windows
        # refused - a dictation key another program owns is otherwise a
        # feature that silently does nothing.
        self.hotkey_failures = self.register_hotkeys()
        if self.hotkey_failures:
            # The commonest "the program does nothing" report: another
            # application already owns the key and Windows said no.
            applog.write("Windows refused these shortcuts: "
                         + ", ".join(self.hotkey_failures), where="hotkeys")
        self.Bind(wx.EVT_HOTKEY, self.on_transcribe, id=HK_TRANSCRIBE)
        self.Bind(wx.EVT_HOTKEY, self.on_settings, id=HK_SETTINGS)
        self.Bind(wx.EVT_HOTKEY, self.on_status, id=HK_STATUS)
        self.Bind(wx.EVT_HOTKEY, self.on_cancel, id=HK_CANCEL)
        self.Bind(wx.EVT_HOTKEY, self.on_window, id=HK_WINDOW)
        self.Bind(wx.EVT_HOTKEY, self.on_dictate, id=HK_DICTATE)

        threading.Thread(target=self._load_model, daemon=True,
                         name="model-loader").start()

        # Hand memory back between jobs. A transcription allocates a great
        # deal and then stops needing it, and a program left running all day
        # otherwise sits on it - on a machine that is also running Word, a
        # browser and a screen reader. Only ever while nothing is running:
        # see app/memory.py for why the timing matters.
        self.housekeeping = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_housekeeping, self.housekeeping)
        self.housekeeping.Start(HOUSEKEEPING_MS)

    def on_housekeeping(self, _evt) -> None:
        if self.jobs.busy or self.jobs.queued_count or self.dictation.active:
            return                      # it would be releasing what the job
                                        # is in the middle of using
        # The full trim only when no model is loaded. With one in memory it
        # would evict 1.6 GB of weights that are meant to be resident, and
        # charge the next dictation for faulting them back - see app/memory.py.
        memory.release(trim=self.backend is None)

    # ------------------------------------------------------------- hotkeys
    def register_hotkeys(self) -> list[str]:
        """Returns the list of shortcuts Windows refused, usually because
        another application already owns them."""
        for hid in self._registered:
            try:
                self.UnregisterHotKey(hid)
            except Exception:
                pass
        self._registered.clear()

        failed = []
        for hid, text in (
            (HK_TRANSCRIBE, self.settings.hotkey_transcribe),
            (HK_SETTINGS, self.settings.hotkey_settings),
            (HK_STATUS, self.settings.hotkey_status),
            (HK_CANCEL, self.settings.hotkey_cancel),
            (HK_WINDOW, self.settings.hotkey_window),
            (HK_DICTATE, self.settings.hotkey_dictate),
        ):
            parsed = config.parse_hotkey(text)
            if not parsed:
                failed.append(text)
                continue
            mods, key = parsed
            try:
                if self.RegisterHotKey(hid, mods, key):
                    self._registered.append(hid)
                else:
                    failed.append(config.format_hotkey(text))
            except Exception:
                failed.append(config.format_hotkey(text))
        return failed

    # --------------------------------------------------------------- model
    def _load_model(self) -> None:
        started = time.time()
        try:
            self.backend = load_backend("auto", self.settings.model, self.device)
        except Exception as exc:
            applog.failed(f"loading {self.settings.model} on "
                          f"{self.device.device}", exc, where="model")
            if self.device.device == "cuda":
                try:
                    self.device = pick_device("cpu")
                    self.backend = load_backend("auto", self.settings.model,
                                                self.device)
                except Exception as exc2:
                    self.backend_error = str(exc2)
                    applog.failed("falling back to the CPU", exc2, where="model")
            else:
                self.backend_error = str(exc)
        finally:
            self.model_ready.set()
        if self.backend is not None:
            applog.write(f"loaded {getattr(self.backend, 'label', '?')} on "
                         f"{self.device.device} in {time.time() - started:.1f}s",
                         where="model")
        wx.CallAfter(self._model_loaded)

    # ------------------------------------------------------------- the tray
    def tray_state(self) -> str:
        """What the icon should be showing, in order of what matters most.

        Worked out in one place because it used to be set from eight, and they
        disagreed: a job finishing while a dictation was recording painted the
        icon "ready" with the microphone still open, and freeing the model
        from memory left it green.
        """
        if self.dictation.active:
            return "tray_dictating"
        if self.jobs.active():
            return "tray_working"
        if self.backend_error:
            return "tray_load_failed"
        if self.backend is None:
            return "tray_loading" if self._loading else "tray_unloaded"
        return "tray_ready"

    def paint_tray(self) -> None:
        self.tray.set_state(self.tray_state())

    # --------------------------------------------------- freeing the memory
    def unload_model(self) -> str | None:
        """Drop the model from memory, returning about three gigabytes.

        Refused while anything is using it: a job holds the model for the
        length of its run, and a dictation is about to. Returns a message when
        it refused, so the caller can say why.

        What makes this safe rather than clever is that loading is already
        asynchronous and already has a state everything checks - `model_ready`.
        Unloading puts the program back into exactly that state, so every
        "still loading" path in the program covers this too.
        """
        if self.dictation.active:
            return t("say_unload_busy")
        if self.jobs.busy or self.jobs.queued_count:
            return t("say_unload_busy")
        if self.backend is None:
            return None

        self.model_ready.clear()
        self.backend = None
        self.backend_error = None
        # The one moment the full trim is right: the weights have just been
        # dropped and there is nothing resident left worth keeping.
        freed = memory.release("freeing the model", trim=True)
        applog.write(f"model freed from memory, {freed:.0f} MB returned",
                     where="model")
        self.paint_tray()
        if self.window and self.window.IsShown():
            self.window.set_status(self.status_text())
        return None

    def load_model_again(self, then_dictate: bool = False) -> None:
        """Load the model back, optionally starting a dictation when it lands.

        `then_dictate` is what makes the memory setting usable without sight:
        pressing the dictation shortcut with no model loaded begins the load,
        says so, and starts recording by itself when it is ready - rather than
        refusing and leaving the user to guess when to press again.
        """
        if self.backend is not None:
            if then_dictate:
                self.dictation.toggle()
            return
        if self._loading:
            # Already on its way; just remember what to do when it lands.
            self._start_dictation_when_loaded = (
                self._start_dictation_when_loaded or then_dictate)
            return
        self._start_dictation_when_loaded = then_dictate
        self._loading = True
        self.model_ready.clear()
        self.paint_tray()
        self.say(t("say_loading"), interrupt=True)
        threading.Thread(target=self._load_model, daemon=True,
                         name="model-loader").start()

    def toggle_model_loaded(self) -> None:
        """The tray item: free the memory, or load the model back."""
        if self.backend is None:
            self.load_model_again()
            return
        refusal = self.unload_model()
        self.say(refusal if refusal else t("say_model_freed"), interrupt=True)

    def _model_loaded(self) -> None:
        self._loading = False
        self.paint_tray()
        if self.backend_error:
            self.say(t("say_load_failed", error=self.backend_error))
        else:
            # Dictation encodes shorter windows than file transcription, and
            # each window length is a tensor shape the maths library builds
            # kernels for on first use - several seconds, once. Paid here, in
            # the background, rather than inside the first thing the user says.
            threading.Thread(target=self._warm_dictation, daemon=True,
                             name="dictation-warmup").start()
        if self.window and self.window.IsShown():
            self.window.set_status(self.status_text())
        if self._start_dictation_when_loaded:
            self._start_dictation_when_loaded = False
            if self.backend is not None:
                self.dictation.toggle()

    def _warm_dictation(self) -> None:
        try:
            from engine import window as window_sizes

            if getattr(self.backend, "model", None) is not None:
                window_sizes.warm_up(self.backend.model)
            self.dictation.preload()
            applog.write("dictation warmed up", where="model")
        except Exception as exc:
            # A cold first dictation is slower, not broken - but this used to
            # hide a call to a method that did not exist, so it is logged now.
            applog.failed("warming up dictation", exc, where="model")

    # --------------------------------------------------------------- speech
    def say(self, text: str, interrupt: bool = False) -> None:
        if self.settings.speak:
            self.speaker.say(text, interrupt)

    # -------------------------------------------------------- hotkey handlers
    def _hotkey_allowed(self) -> bool:
        """Honour "only when Word is in front" so these shortcuts do not eat a
        key combination system-wide while the user works elsewhere.

        **Dictation is not gated on this and does not call it.** Dictation
        writes into whatever field has focus, in any application, so requiring
        Word to be in front would refuse the thing it is for.

        A refusal is spoken. Doing nothing at all is what this used to do, and
        to somebody who cannot see which window has focus it is identical to
        the program being dead - which is exactly how it was reported: "I
        press the shortcut, nothing works."
        """
        if not self.settings.only_when_word_focused:
            return True
        if wordio.word_is_foreground():
            return True
        self.say(t("say_word_not_in_front"), interrupt=True)
        return False

    def on_transcribe(self, _evt) -> None:
        if self._hotkey_allowed():
            self.start_transcription()

    def on_settings(self, _evt) -> None:
        self.open_settings()        # deliberately not gated on Word: you may
                                    # want to configure this before opening Word

    def on_window(self, _evt) -> None:
        self.open_window()

    def on_status(self, _evt) -> None:
        if self._hotkey_allowed():
            self.speak_status()

    def on_cancel(self, _evt) -> None:
        if self.dictation.active:
            self.dictation.cancel()
            return
        if self._hotkey_allowed():
            self.cancel_job()

    def on_dictate(self, _evt) -> None:
        # Deliberately not gated on Word being in front. Dictation is meant to
        # work in any text field in any application, so the one shortcut that
        # has to be global is this one.
        self.dictate_now()

    def dictate_now(self) -> None:
        """Start or stop a dictation. The shortcut and the tray share this.

        Stopping is always allowed, and is checked first. Gating it on Word
        being in front would leave a dictation running with no way to end it
        the moment focus moved - to a dialog, to the screen reader, to
        anything.
        """
        if self.dictation.active:
            self.dictation.toggle()
            return
        # The model may have been freed from memory, by the tray item or by
        # the setting. Load it and start recording when it arrives, rather
        # than refusing and leaving somebody who cannot see the tray icon to
        # guess when to press again.
        if self.backend is None and not self.backend_error:
            self.say(t("say_loading_then_dictate"), interrupt=True)
            self.load_model_again(then_dictate=True)
            return
        self.dictation.toggle()

    # ------------------------------------------------------------ the job
    def start_transcription(self) -> None:
        """Always allowed to submit. A running or cancelling job no longer
        blocks a new one - it queues behind it."""
        if self.dictation.active:
            # One model, one set of window constants. A file chunk takes half
            # a minute, which is half a minute of dictation going nowhere.
            self.say(t("say_transcribe_while_dictating"), interrupt=True)
            return
        if not self.model_ready.is_set():
            self.say(t("say_loading"), interrupt=True)
            return
        if self.backend_error or self.backend is None:
            msg = t("say_load_failed", error=self.backend_error or "")
            self.say(msg, interrupt=True)
            wx.MessageBox(msg, t("app_name"), wx.OK | wx.ICON_ERROR)
            return

        # Fail early and say why, rather than after the user picks a file.
        try:
            wordio.init_thread()
            app = wordio.get_word()
            wordio.active_document(app)
        except wordio.WordNotRunning as exc:
            self.say(str(exc), interrupt=True)
            wx.MessageBox(str(exc), t("app_name"), wx.OK | wx.ICON_WARNING)
            return

        with wx.FileDialog(self, t("pick_file_title"), wildcard=wildcard(),
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dlg:
            self.say(t("say_pick_file"))
            if dlg.ShowModal() != wx.ID_OK:
                self.say(t("say_pick_cancelled"))
                return
            path = dlg.GetPath()

        s = self.settings
        busy_before = self.jobs.busy
        job = self.jobs.submit(
            path,
            language=s.language_or_none,
            timestamps=s.timestamps,
            hotwords=s.hotwords or None,
            insert_mode=s.insert_mode,
            rtl_auto=s.rtl_auto,
        )
        self._last_spoken = 0.0
        self.window.show_without_focus()
        self.refresh_jobs()
        if busy_before:
            self.say(t("say_queued", name=job.filename,
                      n=self.jobs.queued_count))
        else:
            self.say(t("say_started", name=job.filename))

    # ---------------------------------------------------------- job callbacks
    def _job_changed(self, job) -> None:
        wx.CallAfter(self._paint_jobs, job)

        if not self.settings.speak or self.settings.speak_progress_every <= 0:
            return
        if job.state == "running" and job.progress.state == "transcribing":
            now = time.time()
            if now - self._last_spoken >= self.settings.speak_progress_every:
                self._last_spoken = now
                p = job.progress
                self.say(t("say_progress", pct=int(p.percent),
                           eta=dur(p.eta_seconds) if p.measured
                           else t("line_eta_calc")))

    def _paint_jobs(self, _job=None) -> None:
        self.paint_tray()
        if self.window:
            self.refresh_jobs()

    def refresh_jobs(self) -> None:
        rows = []
        for j in self.jobs.all():
            p = j.progress
            if j.state == "running":
                state = t(f"state_{p.state}") if p.state != "queued" else t("jstate_running")
            else:
                state = t(f"jstate_{j.state}")
            if j.state == "done":
                eta = t("line_words", n=(j.meta or {}).get("words", 0))
            elif j.state == "error":
                eta = j.error[:60]
            elif j.state == "running" and p.state == "transcribing":
                eta = (dur(p.eta_seconds) if p.measured else t("line_eta_calc"))
            else:
                eta = ""
            rows.append({
                "id": j.id,
                "file": j.filename,
                "state": state,
                "progress": f"{int(j.percent)}%",
                "language": p.language or "",
                "eta": eta,
            })
        self.window.set_jobs(rows)
        self.window.set_status(self.status_text())

    def _job_finished(self, job) -> None:
        wx.CallAfter(self._deliver, job)

    def _deliver_anywhere(self, job, meta, target: int = 0) -> None:
        """Put a dictation into the focused field of whatever application it is.

        Separate from the Word path rather than folded into it, because almost
        nothing is shared: there is no document, no undo record, no reading
        direction to set and no insert mode. What there is instead is a user
        still holding Ctrl+Shift, a clipboard that belongs to them, and a field
        that might be a password box - all of which app/sendtext.py handles.
        """
        text = (meta.get("text") or "").strip()
        if not text:
            self.refresh_jobs()
            return

        # A blank line before the text when this is not the first dictation
        # into this same window, so consecutive ones do not run together -
        # which is what Word does too. Not on the first one: a message box
        # that opens with two empty lines above the sentence is worse than no
        # separation at all, and in that case there is nothing above it to
        # separate from.
        lead = "\n\n" if target and target == self._last_insert_hwnd else ""
        result = sendtext.send(lead + text + " ")
        self._last_insert_hwnd = target if result["ok"] else 0

        if result["refused"] == "password":
            job.state = "error"
            job.error = t("insert_refused_password")
            self.say(t("insert_refused_password"), interrupt=True)
            self.refresh_jobs()
            return
        if not result["ok"]:
            job.state = "error"
            job.error = t("insert_failed_elsewhere", app=result["window"])
            self.say(t("say_error", error=job.error), interrupt=True)
            self.refresh_jobs()
            return

        job.inserted = True
        applog.write(
            f"job {job.id}: {meta.get('words', 0)} words, "
            f"{len(text)} characters, language {meta.get('language', '?')} -> "
            f"{result['window']} by {result['method']}", where="dictation")
        self.refresh_jobs()
        self.say(t("say_done_elsewhere", words=meta.get("words", 0),
                   lang=meta.get("language", ""), app=result["window"]),
                 interrupt=True)

        if (not self.settings.dictation_keep_model
                and not self.jobs.busy and not self.jobs.queued_count):
            self.unload_model()

    def _deliver(self, job) -> None:
        """Insert a finished job's text into Word, or report why not."""
        if job.state == "cancelled":
            self.say(t("say_cancelled"), interrupt=True)
            self.refresh_jobs()
            return
        if job.state == "error":
            self.say(t("say_error", error=job.error), interrupt=True)
            self.refresh_jobs()
            return
        if job.state != "done" or not job.meta:
            self.refresh_jobs()
            return

        meta = job.meta
        rtl = bool(meta.get("rtl")) and job.rtl_auto

        # A dictation goes back to the window it was started from: Word,
        # Notepad, a browser, a message box in Teams or WhatsApp. **The window
        # it was started from, not the one in front now** - the transcription
        # takes seconds, and in that time the progress window appears or a
        # notification steals the foreground, and the sentence lands somewhere
        # the user was not looking.
        #
        # Word still gets the COM path, because that path is better in ways the
        # others cannot be: one undo record, and the paragraph's reading
        # direction set from the detected language. Everything else is reached
        # through the clipboard, which is the only interface every application
        # on Windows agrees on.
        #
        # Transcribing a file is unchanged: it goes to Word, as it always has.
        # A three-hour transcript is not something to paste into a chat box.
        if job.dictation:
            target = job.target_hwnd
            process = sendtext.window_process(target) if target else ""
            if not process:
                # The window has closed. Fall back to whatever is in front,
                # which is the best guess left and is at least a window the
                # user can see.
                target, _cls, process = sendtext.focused_window()
                applog.write(f"job {job.id}: the window it started in has "
                             f"gone; using {process or '?'}", where="insert")
            if target and sendtext.focus_window(target):
                time.sleep(0.05)        # let the restored window settle
            elif target:
                applog.write(f"job {job.id}: could not bring {process} back to "
                             f"the front", where="insert")
            if process.lower() != "winword.exe":
                self._deliver_anywhere(job, meta, target)
                return

        try:
            wordio.init_thread()
            app = wordio.get_word()
            where = wordio.insert_transcript(
                app, meta.get("text", ""), rtl=rtl, mode=job.insert_mode)
        except wordio.WordNotRunning as exc:
            applog.write(f"job {job.id}: Word is not running - {exc}", where="word")
            job.state = "error"
            job.error = t("word_saved_instead", message=str(exc))
            self.say(t("say_error", error=job.error), interrupt=True)
            self.refresh_jobs()
            return
        except Exception as exc:
            applog.failed(f"job {job.id}: inserting into Word", exc, where="word")
            job.state = "error"
            job.error = t("word_insert_failed", error=exc)
            self.say(t("say_error", error=job.error), interrupt=True)
            self.refresh_jobs()
            return

        job.inserted = True
        applog.write(
            f"job {job.id}: {meta.get('words', 0)} words, "
            f"{len(meta.get('text', ''))} characters, "
            f"language {meta.get('language', '?')}, "
            f"{meta.get('wall_seconds', '?')}s for "
            f"{meta.get('duration_seconds', '?')}s of audio -> "
            f"{where.get('document', '?')}",
            where="dictation" if job.dictation else "transcript")
        self.refresh_jobs()
        self.say(t("say_done", words=meta.get("words", 0),
                   lang=meta.get("language", ""),
                   doc=where.get("document", "")), interrupt=True)
        try:
            wordio.get_word().Activate()
        except Exception:
            pass

        # The memory setting, applied where it is safe to apply: the text is in
        # the document, the queue is empty, and nothing is recording. The next
        # dictation loads the model again by itself.
        if (job.dictation and not self.settings.dictation_keep_model
                and not self.jobs.busy and not self.jobs.queued_count):
            self.unload_model()

    # ----------------------------------------------------------- user actions
    def status_text(self) -> str:
        """The same facts the spoken status gives, for the window to display."""
        if not self.model_ready.is_set():
            # Freed from memory on purpose is not the same as still loading,
            # and reading out the wrong one of those is how a setting starts
            # looking like a fault.
            return t("say_loading") if self._loading else t("tray_unloaded")
        if self.backend_error:
            return t("say_load_failed", error=self.backend_error)

        running = next((j for j in self.jobs.all() if j.state in
                        ("running", "cancelling")), None)
        if running is None:
            # Which screen reader was detected is not reported here. It is a
            # setup detail, not something to re-read after every transcription;
            # Settings still shows it, where it is actually useful.
            return "\n".join([
                t("tray_ready"),
                t("line_model", model=self.settings.model),
                t("line_runs_on", device=self.device.label),
            ])

        p = running.progress
        lines = [t("line_file", name=running.filename)]
        if running.state == "cancelling":
            lines.append(t("jstate_cancelling"))
            return "\n".join(lines)
        if p.language:
            lines.append(t("line_language", lang=p.language))
        if p.duration:
            lines.append(t("line_duration", dur=dur(p.duration)))
        if p.state == "transcribing":
            lines.append(t("line_progress", pct=int(p.percent)))
            # Before a rate has actually been measured, say so rather than
            # printing "0.0x real time" and "less than a second remaining".
            if p.measured:
                lines.append(t("line_eta", eta=dur(p.eta_seconds)))
                lines.append(t("line_speed", x=p.realtime_factor))
            else:
                lines.append(t("line_eta_calc"))
                lines.append(t("line_speed_calc"))
        else:
            lines.append(t(f"state_{p.state}"))
        if self.jobs.queued_count:
            lines.append(t("line_queue", n=self.jobs.queued_count))
        return "\n".join(lines)

    def speak_status(self) -> None:
        """The on-demand status the accessibility notes call for: quiet by
        default, available whenever the user asks."""
        if not self.model_ready.is_set():
            self.say(t("say_loading"), interrupt=True)
            return
        if self.backend_error:
            self.say(t("say_load_failed", error=self.backend_error), interrupt=True)
            return

        running = next((j for j in self.jobs.all() if j.state in
                        ("running", "cancelling")), None)
        if running is None:
            self.say(t("say_ready", model=self.settings.model,
                       device=self.device.label), interrupt=True)
            return

        p = running.progress
        if running.state == "cancelling":
            self.say(t("say_job_cancelling", name=running.filename), interrupt=True)
            return
        if p.state == "transcribing":
            self.say(t("say_working", name=running.filename, pct=int(p.percent),
                       lang=p.language,
                       eta=dur(p.eta_seconds) if p.measured
                       else t("line_eta_calc")), interrupt=True)
        else:
            self.say(t(f"state_{p.state}"), interrupt=True)

    def cancel_job(self) -> None:
        """Cancels the job that is running. Submitting stays open while it
        unwinds, which is the whole point."""
        running = next((j for j in self.jobs.all() if j.state == "running"), None)
        target = running or next((j for j in self.jobs.all()
                                  if j.state == "queued"), None)
        if target is None:
            self.say(t("say_nothing_to_cancel"), interrupt=True)
            return
        self.jobs.cancel(target.id)
        self.say(t("say_job_cancelling", name=target.filename), interrupt=True)
        self.refresh_jobs()

    def cancel_selected_job(self) -> None:
        job_id = self.window.selected_job_id()
        if job_id is None:
            self.say(t("say_nothing_selected"), interrupt=True)
            return
        job = self.jobs.get(job_id)
        if job is None or not self.jobs.cancel(job_id):
            self.say(t("say_nothing_to_cancel"), interrupt=True)
            return
        self.say(t("say_job_cancelling", name=job.filename), interrupt=True)
        self.refresh_jobs()

    def clear_finished_jobs(self) -> None:
        self.jobs.clear_finished()
        self.refresh_jobs()

    def open_window(self, select: int | None = None) -> None:
        """Bring the one window up. `select` puts a particular job in view,
        which is what the tray's jobs submenu asks for."""
        self.window.refresh_labels()
        self.window.set_status(self.status_text())
        self.refresh_jobs()
        self.window.show_and_focus()
        if select is not None:
            self.window.select_job(select)

    def open_settings(self) -> None:
        before = (self.settings.model, self.settings.device)
        dlg = SettingsDialog(self, self.settings, self.speaker.describe(),
                             on_announce=self.say)
        result = dlg.ShowModal()
        language_changed = dlg.language_changed
        dlg.Destroy()
        if result != wx.ID_OK:
            self.say(t("say_settings_cancelled"))
            return

        self.speaker = Speaker(self.settings.speak, self.settings.speak_via_sapi)

        if language_changed:
            # Rebuild rather than relabel: a right-to-left interface needs a
            # mirrored layout, which an existing window cannot adopt cleanly.
            was_shown = self.window.IsShown()
            self.window.Destroy()
            self.window = MainWindow(self)
            self.paint_tray()
            if was_shown:
                self.window.set_status(self.status_text())
                self.window.show_and_focus()
        else:
            self.window.refresh_labels()

        failed = self.hotkey_failures = self.register_hotkeys()
        if failed:
            msg = t("hotkeys_failed", keys=", ".join(failed))
            self.say(msg, interrupt=True)
            wx.MessageBox(msg, t("app_name"), wx.OK | wx.ICON_WARNING)
        else:
            self.say(t("say_settings_saved"))

        if (self.settings.model, self.settings.device) != before:
            if self.jobs.active():
                # Swapping the model out from under a running job would make the
                # second half of a transcript come from a different model.
                self.say(t("say_model_switch_later"))
            else:
                self.say(t("say_model_switch"))
                self.paint_tray()
                self.model_ready.clear()
                self.backend = None
                self.backend_error = None
                self.device = pick_device(self.settings.device)
                threading.Thread(target=self._load_model, daemon=True).start()

    def open_help(self) -> None:
        parent = self.window if self.window.IsShown() else self
        dlg = HelpDialog(parent)
        dlg.ShowModal()
        dlg.Destroy()

    def quit_app(self) -> None:
        # The microphone is an exclusive device on some drivers: leaving it
        # open would keep it unusable for everything else until the process
        # actually died.
        self.dictation.cancel()
        if self.jobs.active():
            if wx.MessageBox(t("quit_while_busy"), t("app_name"),
                             wx.YES_NO | wx.ICON_QUESTION) != wx.YES:
                return
            self.jobs.cancel_all()
        self.jobs.shutdown()
        self.speaker.stop()
        self.tray.RemoveIcon()
        self.tray.Destroy()
        if self.window:
            self.window.Destroy()
        self.Destroy()
        wx.GetApp().ExitMainLoop()


class TranscriberApp(wx.App):
    def OnInit(self) -> bool:
        self.instance = wx.SingleInstanceChecker(SINGLE_INSTANCE)
        if self.instance.IsAnotherRunning():
            # load() applies the saved language so even this message is localised
            config.load()
            wx.MessageBox(t("already_running"), t("app_name"),
                          wx.OK | wx.ICON_INFORMATION)
            return False

        self.frame = AppFrame()
        self.frame.Hide()

        s = self.frame.settings
        # The window first, so a screen reader reads the heading and then the
        # welcome spoken over it rather than the other way round.
        if s.show_window_on_start:
            self.frame.open_window()
        self.frame.say(t("say_welcome", app=t("app_name"),
                         k_start=s.hotkey_transcribe,
                         k_dictate=s.hotkey_dictate,
                         k_window=s.hotkey_window))
        return True


def main() -> int:
    app = TranscriberApp(False)
    if not getattr(app, "frame", None):
        return 0
    app.MainLoop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
