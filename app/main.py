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

from app import config, i18n, wordio
from app.gui import HelpDialog, MainWindow, SettingsDialog
from app.i18n import duration as dur
from app.i18n import t
from app.jobs import JobManager
from app.speech import Speaker
from engine.backends import load_backend
from engine import models, paths
from engine.device import pick_device

from engine.paths import OUT_DIR
SINGLE_INSTANCE = "Sada-single-instance"

AUDIO_EXT = "*.mp3;*.m4a;*.wav;*.ogg;*.opus;*.wma;*.flac;*.aac"
VIDEO_EXT = "*.mp4;*.mkv;*.avi;*.mov;*.webm"

HK_TRANSCRIBE, HK_SETTINGS, HK_STATUS, HK_CANCEL, HK_WINDOW = 1, 2, 3, 4, 5

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


def wildcard() -> str:
    return (f"{t('wildcard_all_media')}|{AUDIO_EXT};{VIDEO_EXT}"
            f"|{t('wildcard_audio')}|{AUDIO_EXT}"
            f"|{t('wildcard_video')}|{VIDEO_EXT}"
            f"|{t('wildcard_any')}|*.*")


class TrayIcon(wx.adv.TaskBarIcon):
    """A convenience, never the only way to reach anything - every item here
    also has a hotkey, because a tray icon is unreachable without a mouse."""

    def __init__(self, frame: "AppFrame"):
        super().__init__()
        self.frame = frame
        self.set_state("tray_ready")

    def set_state(self, key: str) -> None:
        self.SetIcon(app_icon(), f"{t('app_name')} - {t(key)}")

    def CreatePopupMenu(self):
        m = wx.Menu()
        s = self.frame.settings
        for label, handler in (
            (f"{t('tray_working')}\t{s.hotkey_transcribe}",
             self.frame.start_transcription),
            (f"{t('field_k_status').rstrip(':')}\t{s.hotkey_status}",
             self.frame.speak_status),
            (f"{t('btn_cancel')}\t{s.hotkey_cancel}", self.frame.cancel_job),
        ):
            item = m.Append(wx.ID_ANY, label)
            self.Bind(wx.EVT_MENU, lambda e, h=handler: h(), item)
        m.AppendSeparator()
        for label, handler in (
            (f"{t('tray_window')}\t{s.hotkey_window}", self.frame.open_window),
            (f"{t('tray_settings')}\t{s.hotkey_settings}", self.frame.open_settings),
            (t("tray_help"), self.frame.open_help),
            (t("tray_quit"), self.frame.quit_app),
        ):
            item = m.Append(wx.ID_ANY, label)
            self.Bind(wx.EVT_MENU, lambda e, h=handler: h(), item)
        return m


class AppFrame(wx.Frame):
    """Hidden frame that owns the hotkeys. wx routes EVT_HOTKEY to a window, so
    there has to be one, but the user never sees it."""

    def __init__(self):
        super().__init__(None, title=t("app_name"), size=(1, 1),
                         style=wx.FRAME_NO_TASKBAR)
        self.settings = config.load()          # also applies the saved language
        self.speaker = Speaker(self.settings.speak, self.settings.speak_via_sapi)

        self.backend = None
        self.backend_error: str | None = None
        self.device = pick_device(self.settings.device)
        self.model_ready = threading.Event()

        # Clear leftovers from a download that was cancelled or interrupted:
        # a zero-byte or truncated checkpoint otherwise sits in the models
        # folder forever, taking space and looking like a half-installed model.
        try:
            models.cleanup_partials()
        except Exception:
            pass            # housekeeping must never stop the app from starting

        # One location for this program's data, not two. Settings moved out of
        # the user profile and into the program's own folder, so an older copy
        # could be left behind there - a second settings.json that nothing
        # reads, and perhaps gigabytes of models nothing uses.
        #
        # This runs after config.load(), which has already copied those
        # settings across, and never touches the folder currently in use.
        self.state_cleanup = {"removed": [], "adopted": [], "failed": []}
        try:
            self.state_cleanup = paths.cleanup_duplicate_state()
        except Exception:
            pass            # housekeeping must never stop the app from starting

        self._last_spoken = 0.0
        self.jobs = JobManager(lambda: self.backend, OUT_DIR,
                               on_change=self._job_changed,
                               on_finished=self._job_finished)

        self.window = MainWindow(self)
        self.window.SetIcon(app_icon())
        self.SetIcon(app_icon())
        self.tray = TrayIcon(self)
        self._registered: list[int] = []
        self.register_hotkeys()
        self.Bind(wx.EVT_HOTKEY, self.on_transcribe, id=HK_TRANSCRIBE)
        self.Bind(wx.EVT_HOTKEY, self.on_settings, id=HK_SETTINGS)
        self.Bind(wx.EVT_HOTKEY, self.on_status, id=HK_STATUS)
        self.Bind(wx.EVT_HOTKEY, self.on_cancel, id=HK_CANCEL)
        self.Bind(wx.EVT_HOTKEY, self.on_window, id=HK_WINDOW)

        threading.Thread(target=self._load_model, daemon=True,
                         name="model-loader").start()

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
        try:
            self.backend = load_backend("auto", self.settings.model, self.device)
        except Exception as exc:
            if self.device.device == "cuda":
                try:
                    self.device = pick_device("cpu")
                    self.backend = load_backend("auto", self.settings.model,
                                                self.device)
                except Exception as exc2:
                    self.backend_error = str(exc2)
            else:
                self.backend_error = str(exc)
        finally:
            self.model_ready.set()
        wx.CallAfter(self._model_loaded)

    def _model_loaded(self) -> None:
        if self.backend_error:
            self.tray.set_state("tray_load_failed")
            self.say(t("say_load_failed", error=self.backend_error))
        else:
            self.tray.set_state("tray_ready")
        if self.window and self.window.IsShown():
            self.window.set_status(self.status_text())

    # --------------------------------------------------------------- speech
    def say(self, text: str, interrupt: bool = False) -> None:
        if self.settings.speak:
            self.speaker.say(text, interrupt)

    # -------------------------------------------------------- hotkey handlers
    def _hotkey_allowed(self) -> bool:
        """Honour "only when Word is in front" so these shortcuts do not eat a
        key combination system-wide while the user works elsewhere."""
        if not self.settings.only_when_word_focused:
            return True
        return wordio.word_is_foreground()

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
        if self._hotkey_allowed():
            self.cancel_job()

    # ------------------------------------------------------------ the job
    def start_transcription(self) -> None:
        """Always allowed to submit. A running or cancelling job no longer
        blocks a new one - it queues behind it."""
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
        self.tray.set_state("tray_working" if self.jobs.active()
                            else ("tray_load_failed" if self.backend_error
                                  else "tray_ready"))
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
        try:
            wordio.init_thread()
            app = wordio.get_word()
            where = wordio.insert_transcript(
                app, meta.get("text", ""), rtl=rtl, mode=job.insert_mode)
        except wordio.WordNotRunning as exc:
            job.state = "error"
            job.error = t("word_saved_instead", message=str(exc))
            self.say(t("say_error", error=job.error), interrupt=True)
            self.refresh_jobs()
            return
        except Exception as exc:
            job.state = "error"
            job.error = t("word_insert_failed", error=exc)
            self.say(t("say_error", error=job.error), interrupt=True)
            self.refresh_jobs()
            return

        job.inserted = True
        self.refresh_jobs()
        self.say(t("say_done", words=meta.get("words", 0),
                   lang=meta.get("language", ""),
                   doc=where.get("document", "")), interrupt=True)
        try:
            wordio.get_word().Activate()
        except Exception:
            pass

    # ----------------------------------------------------------- user actions
    def status_text(self) -> str:
        """The same facts the spoken status gives, for the window to display."""
        if not self.model_ready.is_set():
            return t("say_loading")
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

    def open_window(self) -> None:
        self.window.refresh_labels()
        self.window.set_status(self.status_text())
        self.window.show_and_focus()

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
            self.tray.set_state("tray_ready" if not self.backend_error
                                else "tray_load_failed")
            if was_shown:
                self.window.set_status(self.status_text())
                self.window.show_and_focus()
        else:
            self.window.refresh_labels()

        failed = self.register_hotkeys()
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
                self.tray.set_state("tray_loading")
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
        self.frame.say(t("say_welcome", app=t("app_name"),
                         k_start=s.hotkey_transcribe, k_window=s.hotkey_window,
                         k_settings=s.hotkey_settings, k_status=s.hotkey_status))
        return True


def main() -> int:
    app = TranscriberApp(False)
    if not getattr(app, "frame", None):
        return 0
    app.MainLoop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
