"""The windows the user sees, built for a screen reader first.

Rules applied throughout, from docs/accessibility.md:

* Native wx controls only. wxPython wraps real Win32 controls, which is why NVDA
  itself is written with it - a custom-drawn widget would announce nothing.
* Every control has a visible label *and* an accessible name, because proximity
  alone is not a reliable association.
* Nothing steals focus. The progress window is modeless and never re-focuses
  itself on an update.
* Long text lives in a read-only multiline field, which a screen reader can
  navigate line by line, rather than in a static block it can only read whole.
* Every dialog closes on Escape and has a default button.

All strings come from app/i18n.py. English is the default and Arabic is a
setting; when Arabic is active the layout direction is flipped too, because a
right-to-left interface is not just translated text.
"""
from __future__ import annotations

import time

import wx

from app import config, i18n
from engine import models
from app.config import (DEVICES, DICTATION_MODELS, INSERT_MODES, LANGUAGES, MODELS, UI_LANGUAGES,
                        Settings, device_labels, format_hotkey, insert_labels,
                        language_labels, model_labels, parse_hotkey,
                        ui_language_labels)
from app.i18n import t

PAD = 8


def apply_direction(window) -> None:
    """Mirror the layout for a right-to-left interface language."""
    try:
        window.SetLayoutDirection(
            wx.Layout_RightToLeft if i18n.is_rtl_ui() else wx.Layout_LeftToRight)
    except Exception:
        pass            # not fatal; the text is still correct, only mirrored wrongly


class _NamedAccessible(wx.Accessible):
    """Forces the MSAA name a screen reader reads for a control.

    `SetName` alone is not enough on Windows: it sets the wx window name, which
    is not what MSAA reports. Windows then guesses the name from a nearby label,
    and in a two-column grid it guesses wrong - so a shortcut field announced
    only its value, "Ctrl Shift T", with no hint that it was the one for
    starting a transcription. Setting the name explicitly is the fix.
    """

    def __init__(self, name: str):
        super().__init__()
        self._name = name

    def GetName(self, childId):
        return (wx.ACC_OK, self._name)


# Enumerating capture devices runs ffmpeg, so the answer is cached for the
# process: opening Settings should not pay for a subprocess every time, and a
# microphone being plugged in mid-session is rare enough to not be worth
# re-checking on every repaint. A failure returns an empty list, which the
# dialog shows as "no microphone was found" rather than an error.
_mic_cache: list[str] | None = None


def mic_devices() -> list[str]:
    global _mic_cache
    if _mic_cache is None:
        try:
            from engine.mic import list_input_devices

            _mic_cache = list_input_devices()
        except Exception:
            _mic_cache = []
    return list(_mic_cache)


def _set_accessible_name(control, name: str) -> None:
    control.SetName(name)
    acc = _NamedAccessible(name)
    # Keep a reference on the control: wx does not own the accessible object,
    # and letting it be garbage collected crashes the process when a screen
    # reader next queries the control.
    control._sada_accessible = acc
    try:
        control.SetAccessible(acc)
    except Exception:
        pass            # no MSAA on this platform; SetName above still applies


def _row(sizer, parent, label_text: str, control, help_text: str = "") -> None:
    """A labelled row whose control announces what it is *for*, not just its
    value. The label text becomes the control's MSAA name."""
    lbl = wx.StaticText(parent, label=label_text)
    name = label_text.replace("&", "").rstrip(":").strip()
    _set_accessible_name(control, name)
    if help_text:
        control.SetHelpText(help_text)
        control.SetToolTip(help_text)
    sizer.Add(lbl, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, PAD)
    sizer.Add(control, 1, wx.EXPAND)


class SettingsDialog(wx.Dialog):
    """Grouped into labelled boxes so a screen reader announces which section
    the focused control belongs to."""

    def __init__(self, parent, settings: Settings, speaker_desc: str = "",
                 on_announce=None):
        super().__init__(parent, title=t("settings_title"),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        apply_direction(self)
        self.settings = settings
        self.on_announce = on_announce
        self.language_changed = False
        self.was_reset = False
        self._dl_thread = None
        self._dl_cancel = None
        self._model_rows: list = []
        panel = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)

        # ------------------------------------------------------- interface
        ui = wx.StaticBoxSizer(wx.VERTICAL, panel, t("group_interface"))
        g0 = wx.FlexGridSizer(0, 2, PAD, PAD)
        g0.AddGrowableCol(1, 1)
        self.ui_language = wx.Choice(ui.GetStaticBox(), choices=ui_language_labels())
        self.ui_language.SetSelection(UI_LANGUAGES.index(settings.ui_language))
        _row(g0, ui.GetStaticBox(), t("field_ui_language"), self.ui_language,
             t("help_ui_language"))
        ui.Add(g0, 0, wx.EXPAND | wx.ALL, PAD)
        outer.Add(ui, 0, wx.EXPAND | wx.ALL, PAD)

        # ---------------------------------------------------------- engine
        eng = wx.StaticBoxSizer(wx.VERTICAL, panel, t("group_engine"))
        g = wx.FlexGridSizer(0, 2, PAD, PAD)
        g.AddGrowableCol(1, 1)

        self.model = wx.Choice(eng.GetStaticBox(), choices=model_labels())
        self.model.SetSelection(MODELS.index(settings.model))
        _row(g, eng.GetStaticBox(), t("field_model"), self.model, t("help_model"))

        self.device = wx.Choice(eng.GetStaticBox(), choices=device_labels())
        self.device.SetSelection(DEVICES.index(settings.device))
        _row(g, eng.GetStaticBox(), t("field_device"), self.device, t("help_device"))

        self.language = wx.Choice(eng.GetStaticBox(), choices=language_labels())
        self.language.SetSelection(LANGUAGES.index(settings.language))
        _row(g, eng.GetStaticBox(), t("field_language"), self.language,
             t("help_language"))

        self.hotwords = wx.TextCtrl(eng.GetStaticBox(), value=settings.hotwords)
        _row(g, eng.GetStaticBox(), t("field_hotwords"), self.hotwords,
             t("help_hotwords"))

        eng.Add(g, 0, wx.EXPAND | wx.ALL, PAD)
        outer.Add(eng, 0, wx.EXPAND | wx.ALL, PAD)

        # ---------------------------------------------------------- output
        out = wx.StaticBoxSizer(wx.VERTICAL, panel, t("group_output"))
        g2 = wx.FlexGridSizer(0, 2, PAD, PAD)
        g2.AddGrowableCol(1, 1)
        self.insert_mode = wx.Choice(out.GetStaticBox(), choices=insert_labels())
        self.insert_mode.SetSelection(INSERT_MODES.index(settings.insert_mode))
        _row(g2, out.GetStaticBox(), t("field_insert"), self.insert_mode)
        out.Add(g2, 0, wx.EXPAND | wx.ALL, PAD)

        self.timestamps = wx.CheckBox(out.GetStaticBox(), label=t("check_timestamps"))
        self.timestamps.SetValue(settings.timestamps)
        self.rtl_auto = wx.CheckBox(out.GetStaticBox(), label=t("check_rtl"))
        self.rtl_auto.SetValue(settings.rtl_auto)
        self.side_files = wx.CheckBox(out.GetStaticBox(), label=t("check_side_files"))
        self.side_files.SetValue(settings.save_side_files)
        for c in (self.timestamps, self.rtl_auto, self.side_files):
            out.Add(c, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, PAD)
        outer.Add(out, 0, wx.EXPAND | wx.ALL, PAD)

        # -------------------------------------------------------- shortcuts
        keys = wx.StaticBoxSizer(wx.VERTICAL, panel, t("group_shortcuts"))
        g3 = wx.FlexGridSizer(0, 2, PAD, PAD)
        g3.AddGrowableCol(1, 1)
        box = keys.GetStaticBox()
        self.k_transcribe = wx.TextCtrl(box, value=format_hotkey(settings.hotkey_transcribe))
        self.k_window = wx.TextCtrl(box, value=format_hotkey(settings.hotkey_window))
        self.k_settings = wx.TextCtrl(box, value=format_hotkey(settings.hotkey_settings))
        self.k_status = wx.TextCtrl(box, value=format_hotkey(settings.hotkey_status))
        self.k_cancel = wx.TextCtrl(box, value=format_hotkey(settings.hotkey_cancel))
        self.k_dictate = wx.TextCtrl(box, value=format_hotkey(settings.hotkey_dictate))
        _row(g3, box, t("field_k_transcribe"), self.k_transcribe,
             t("help_k_transcribe"))
        _row(g3, box, t("field_k_window"), self.k_window)
        _row(g3, box, t("field_k_settings"), self.k_settings)
        _row(g3, box, t("field_k_status"), self.k_status, t("help_k_status"))
        _row(g3, box, t("field_k_cancel"), self.k_cancel)
        _row(g3, box, t("field_k_dictate"), self.k_dictate, t("help_k_dictate"))
        keys.Add(g3, 0, wx.EXPAND | wx.ALL, PAD)
        outer.Add(keys, 0, wx.EXPAND | wx.ALL, PAD)

        # -------------------------------------------------------- dictation
        dic = wx.StaticBoxSizer(wx.VERTICAL, panel, t("group_dictation"))
        dbox = dic.GetStaticBox()
        g5 = wx.FlexGridSizer(0, 2, PAD, PAD)
        g5.AddGrowableCol(1, 1)

        # Device names come from the driver, so they are whatever it calls
        # itself. Listing them is a subprocess call, so it happens once here
        # rather than every time the dialog repaints.
        self.dictation_model = wx.Choice(
            dbox, choices=config.dictation_model_labels())
        self.dictation_model.SetSelection(
            DICTATION_MODELS.index(settings.dictation_model)
            if settings.dictation_model in DICTATION_MODELS else 0)
        _row(g5, dbox, t("field_dictation_model"), self.dictation_model,
             t("help_dictation_model"))

        self._mics = mic_devices()
        self.mic = wx.Choice(dbox, choices=[t("mic_auto")] + self._mics)
        self.mic.SetSelection(
            self._mics.index(settings.mic_device) + 1
            if settings.mic_device in self._mics else 0)
        if not self._mics:
            self.mic.Enable(False)
            self.mic.SetToolTip(t("mic_none"))
        _row(g5, dbox, t("field_mic"), self.mic, t("help_mic"))

        self.pause_ms = wx.SpinCtrl(dbox, min=200, max=3000,
                                    initial=settings.dictation_pause_ms)
        _row(g5, dbox, t("field_pause"), self.pause_ms, t("help_pause"))

        self.target_s = wx.SpinCtrl(dbox, min=2, max=25,
                                    initial=settings.dictation_target_s)
        _row(g5, dbox, t("field_target"), self.target_s, t("help_target"))
        dic.Add(g5, 0, wx.EXPAND | wx.ALL, PAD)

        self.fillers = wx.CheckBox(dbox, label=t("check_fillers"))
        self.fillers.SetValue(settings.dictation_remove_fillers)
        self.fillers.SetToolTip(t("help_fillers"))
        dic.Add(self.fillers, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, PAD)

        self.speak_phrases = wx.CheckBox(dbox, label=t("check_speak_phrases"))
        self.speak_phrases.SetValue(settings.dictation_speak_phrases)
        self.speak_phrases.SetToolTip(t("help_speak_phrases"))
        dic.Add(self.speak_phrases, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, PAD)
        outer.Add(dic, 0, wx.EXPAND | wx.ALL, PAD)

        # ------------------------------------------------------ screen reader
        sr = wx.StaticBoxSizer(wx.VERTICAL, panel, t("group_reader"))
        srbox = sr.GetStaticBox()
        self.speak = wx.CheckBox(srbox, label=t("check_speak"))
        self.speak.SetValue(settings.speak)
        sr.Add(self.speak, 0, wx.ALL, PAD)

        g4 = wx.FlexGridSizer(0, 2, PAD, PAD)
        g4.AddGrowableCol(1, 1)
        self.progress_every = wx.SpinCtrl(
            srbox, min=0, max=600, initial=settings.speak_progress_every)
        _row(g4, srbox, t("field_progress_every"), self.progress_every,
             t("help_progress_every"))
        sr.Add(g4, 0, wx.EXPAND | wx.ALL, PAD)

        self.sapi = wx.CheckBox(srbox, label=t("check_sapi"))
        self.sapi.SetValue(settings.speak_via_sapi)
        sr.Add(self.sapi, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, PAD)

        self.only_word = wx.CheckBox(srbox, label=t("check_only_word"))
        self.only_word.SetValue(settings.only_when_word_focused)
        sr.Add(self.only_word, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, PAD)

        if speaker_desc:
            sr.Add(wx.StaticText(srbox, label=t("detected_reader", name=speaker_desc)),
                   0, wx.ALL, PAD)
        outer.Add(sr, 0, wx.EXPAND | wx.ALL, PAD)

        # ------------------------------------------------------ model files
        # Choosing a model used to silently start a multi-gigabyte download with
        # the window frozen, so a missing model looked like a broken program.
        # Here what is on disk is visible and downloading is an explicit act.
        mf = wx.StaticBoxSizer(wx.VERTICAL, panel, t("group_models"))
        mfbox = mf.GetStaticBox()
        self.model_list = wx.ListCtrl(
            mfbox, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_HRULES)
        _set_accessible_name(self.model_list, t("group_models"))
        for i, col in enumerate((t("col_model"), t("col_size"), t("col_state"))):
            self.model_list.InsertColumn(i, col, width=[210, 110, 180][i])
        self.model_list.SetMinSize((-1, 128))
        self.model_list.Bind(wx.EVT_LIST_ITEM_SELECTED,
                             lambda e: self._update_model_buttons())
        mf.Add(self.model_list, 1, wx.EXPAND | wx.ALL, PAD)

        # A read-only field, not a StaticText. A screen reader cannot be moved
        # into static text and is not told when its label changes, so the real
        # progress of a 1.5 GB download was invisible to the people this is for.
        # This can be read at any moment with the normal reading keys, and the
        # milestones are spoken as well.
        self.dl_status = wx.TextCtrl(
            mfbox, value="", style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL)
        _set_accessible_name(self.dl_status, t("dl_progress_name"))
        self.dl_status.SetMinSize((-1, 76))
        self.dl_status.Hide()
        mf.Add(self.dl_status, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, PAD)

        self.dl_gauge = wx.Gauge(mfbox, range=1000)
        _set_accessible_name(self.dl_gauge, t("col_progress"))
        self.dl_gauge.Hide()
        mf.Add(self.dl_gauge, 0, wx.EXPAND | wx.ALL, PAD)

        mrow = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_download = wx.Button(mfbox, label=t("btn_download"))
        self.btn_delete = wx.Button(mfbox, label=t("btn_delete"))
        self.btn_download.Bind(wx.EVT_BUTTON, self.on_download)
        self.btn_delete.Bind(wx.EVT_BUTTON, self.on_delete)
        mrow.Add(self.btn_download, 0, wx.RIGHT, PAD)
        mrow.Add(self.btn_delete, 0)
        mf.Add(mrow, 0, wx.ALL, PAD)
        outer.Add(mf, 0, wx.EXPAND | wx.ALL, PAD)
        self.refresh_models()

        # ---------------------------------------------------------- buttons
        reset = wx.Button(panel, label=t("btn_reset"))
        reset.Bind(wx.EVT_BUTTON, self.on_reset)
        outer.Add(reset, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, PAD)

        btns = wx.StdDialogButtonSizer()
        ok = wx.Button(panel, wx.ID_OK, t("btn_save"))
        ok.SetDefault()
        btns.AddButton(ok)
        btns.AddButton(wx.Button(panel, wx.ID_CANCEL, t("btn_cancel")))
        btns.AddButton(wx.Button(panel, wx.ID_HELP, t("btn_help")))
        btns.Realize()
        outer.Add(btns, 0, wx.EXPAND | wx.ALL, PAD)

        panel.SetSizer(outer)
        outer.SetSizeHints(self)
        self.SetSize((660, min(940, self.GetBestSize().height + 40)))
        self.CentreOnScreen()

        self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)
        self.Bind(wx.EVT_BUTTON, lambda e: HelpDialog(self).ShowModal(), id=wx.ID_HELP)
        self.SetEscapeId(wx.ID_CANCEL)
        self.ui_language.SetFocus()

    # ---------------------------------------------------------------- saving
    def on_ok(self, _evt) -> None:
        keys = {
            "hotkey_transcribe": self.k_transcribe,
            "hotkey_window": self.k_window,
            "hotkey_settings": self.k_settings,
            "hotkey_status": self.k_status,
            "hotkey_cancel": self.k_cancel,
            "hotkey_dictate": self.k_dictate,
        }
        seen: dict[tuple[int, int], str] = {}
        for name, ctrl in keys.items():
            parsed = parse_hotkey(ctrl.GetValue())
            if not parsed:
                self._complain(t("bad_hotkey", key=ctrl.GetValue()), ctrl)
                return
            if parsed in seen:
                self._complain(
                    t("dup_hotkey", key=format_hotkey(ctrl.GetValue())), ctrl)
                return
            seen[parsed] = name

        s = self.settings
        new_ui = UI_LANGUAGES[self.ui_language.GetSelection()]
        self.language_changed = new_ui != s.ui_language
        s.ui_language = new_ui
        s.model = MODELS[self.model.GetSelection()]
        s.device = DEVICES[self.device.GetSelection()]
        s.language = LANGUAGES[self.language.GetSelection()]
        s.hotwords = self.hotwords.GetValue().strip()
        s.insert_mode = INSERT_MODES[self.insert_mode.GetSelection()]
        s.timestamps = self.timestamps.GetValue()
        s.rtl_auto = self.rtl_auto.GetValue()
        s.save_side_files = self.side_files.GetValue()
        for name, ctrl in keys.items():
            setattr(s, name, format_hotkey(ctrl.GetValue()))
        s.speak = self.speak.GetValue()
        s.speak_progress_every = int(self.progress_every.GetValue())
        s.speak_via_sapi = self.sapi.GetValue()
        s.only_when_word_focused = self.only_word.GetValue()
        chosen = self.mic.GetSelection()
        s.mic_device = self._mics[chosen - 1] if chosen > 0 else ""
        s.dictation_model = DICTATION_MODELS[self.dictation_model.GetSelection()]
        s.dictation_pause_ms = int(self.pause_ms.GetValue())
        s.dictation_target_s = int(self.target_s.GetValue())
        s.dictation_remove_fillers = self.fillers.GetValue()
        s.dictation_speak_phrases = self.speak_phrases.GetValue()
        s.validate()
        config.save(s)              # also switches the active language
        self.EndModal(wx.ID_OK)

    # ----------------------------------------------------------- model files
    def refresh_models(self) -> None:
        # Note which model is selected before the rows are thrown away.
        selected = self._selected_model() if self._model_rows else None
        keep = selected.key if selected else None

        self.model_list.DeleteAllItems()
        self._model_rows = []
        for m in models.all_info():
            state = (t("model_installed") if m.installed
                     else t("model_partial") if m.partial
                     else t("model_not_installed"))
            row = self.model_list.InsertItem(self.model_list.GetItemCount(),
                                             m.checkpoint)
            self.model_list.SetItem(row, 1, m.size_text)
            self.model_list.SetItem(row, 2, state)
            self._model_rows.append(m)

        # Keep whatever the user had selected. This list is rebuilt after every
        # download and delete, and it used to jump the selection back to the
        # model configured in Settings - so pressing Delete after a download
        # removed a different model than the one that appeared to be selected,
        # with nothing to see or hear that the selection had moved.
        target = keep if keep in {m.key for m in self._model_rows} else None
        if target is None:
            target = self.settings.model

        for i, m in enumerate(self._model_rows):
            if m.key == target:
                self.model_list.Select(i)
                self.model_list.Focus(i)
                break
        else:
            if self._model_rows:
                self.model_list.Select(0)
                self.model_list.Focus(0)
        self._update_model_buttons()

    def _selected_model(self):
        i = self.model_list.GetFirstSelected()
        return self._model_rows[i] if 0 <= i < len(self._model_rows) else None

    def _downloading(self) -> bool:
        return self._dl_thread is not None and self._dl_thread.is_alive()

    def _update_model_buttons(self) -> None:
        m = self._selected_model()
        busy = self._downloading()
        self.btn_download.SetLabel(t("btn_stop_download") if busy
                                   else t("btn_download"))
        self.btn_download.Enable(busy or (bool(m) and not m.installed))
        self.btn_delete.Enable(bool(m) and (m.installed or m.partial) and not busy)

    def on_download(self, _evt) -> None:
        import threading

        if self._downloading():
            if self._dl_cancel:
                self._dl_cancel.set()
            return
        m = self._selected_model()
        if m is None or m.installed:
            return

        self._dl_cancel = threading.Event()
        self._dl_started = time.monotonic()
        self._dl_last_paint = 0.0
        self._dl_spoken_pct = 0
        self.dl_status.Show()
        self.dl_status.SetValue(t("dl_line_file", name=m.checkpoint))
        self.dl_gauge.Show()
        self.dl_gauge.SetValue(0)
        self.Layout()
        if self.on_announce:
            self.on_announce(t("say_dl_start", name=m.checkpoint,
                               total=models.human(m.expected_size)))

        def progress(done, total):
            wx.CallAfter(self._dl_progress, m.checkpoint, done, total)

        def work():
            try:
                models.download(m.key, on_progress=progress, cancel=self._dl_cancel)
                wx.CallAfter(self._dl_finished, m.checkpoint, None)
            except models.DownloadCancelled:
                wx.CallAfter(self._dl_finished, m.checkpoint, "cancelled")
            except Exception as exc:
                wx.CallAfter(self._dl_finished, m.checkpoint, str(exc))

        self._dl_thread = threading.Thread(target=work, daemon=True,
                                           name="model-download")
        self._dl_thread.start()
        self._update_model_buttons()

    def _dl_progress(self, name, done, total) -> None:
        """Real numbers, measured rather than guessed.

        The callback fires once per megabyte - about 1500 times for the
        recommended model - so the window is repainted at most a few times a
        second, and speech is saved for ten-percent milestones. Updating on every
        block would flood both the UI and the screen reader.
        """
        now = time.monotonic()
        elapsed = max(1e-6, now - self._dl_started)
        pct = (done / total * 100) if total else 0.0
        rate = done / elapsed                       # bytes per second, measured

        if now - self._dl_last_paint >= 0.25 or done >= total:
            self._dl_last_paint = now
            lines = [
                t("dl_line_file", name=name),
                t("dl_line_amount", done=models.human(done),
                  total=models.human(total), pct=int(pct)),
            ]
            # Only claim a rate once there is enough behind it to mean anything.
            if elapsed >= 2.0 and done > 0:
                lines.append(t("dl_line_speed", speed=models.human(int(rate))))
                remaining = max(0, total - done)
                lines.append(t("dl_line_eta",
                               eta=i18n.duration(remaining / rate)))
            else:
                lines.append(t("dl_line_eta_calc"))
            self.dl_status.SetValue("\n".join(lines))
            self.dl_gauge.SetValue(int(max(0, min(1000, pct * 10))))

        # Spoken progress: every ten percent, which is frequent enough to be
        # reassuring and rare enough not to drown out everything else.
        milestone = int(pct // 10) * 10
        if milestone > self._dl_spoken_pct and milestone < 100 and self.on_announce:
            self._dl_spoken_pct = milestone
            if elapsed < 2.0 or rate <= 0:
                # Say so plainly. Dropping "still measuring" into the "{eta}"
                # slot of "about {eta} remaining" reads as gibberish.
                self.on_announce(t("say_dl_progress_calc", pct=milestone))
            else:
                seconds_left = max(0, total - done) / rate
                if seconds_left < 3:
                    self.on_announce(t("say_dl_almost", pct=milestone))
                else:
                    self.on_announce(t("say_dl_progress", pct=milestone,
                                       eta=i18n.duration(seconds_left)))

    def _dl_finished(self, name, error) -> None:
        elapsed = time.monotonic() - getattr(self, "_dl_started", time.monotonic())
        self.dl_gauge.Hide()
        self._dl_thread = None

        if error == "cancelled":
            message = t("download_cancelled")
        elif error:
            message = t("download_failed", name=name, error=error)
        else:
            size = models.human(models.info(
                next(k for k, v in models.MODEL_NAMES.items() if v[1] == name)
            ).actual_size) if any(v[1] == name for v in models.MODEL_NAMES.values()) else ""
            message = t("say_dl_done", name=name, total=size,
                        elapsed=i18n.duration(elapsed))

        self.dl_status.SetValue(message)
        if self.on_announce:
            self.on_announce(message)
        if error and error != "cancelled":
            wx.MessageBox(message, t("app_name"), wx.OK | wx.ICON_ERROR, self)
        self.refresh_models()
        self.Layout()

    def on_delete(self, _evt) -> None:
        m = self._selected_model()
        if m is None:
            return
        if wx.MessageBox(t("delete_confirm", name=m.checkpoint), t("app_name"),
                         wx.YES_NO | wx.ICON_QUESTION, self) != wx.YES:
            return

        # Removing a 1.5 GB file is not always instant, and it used to happen
        # with no sign that anything was underway. Say what is starting, do it
        # off the UI thread so the window cannot freeze, then report the result.
        import threading

        size = models.human(m.actual_size)
        self.dl_status.Show()
        self.dl_status.SetValue(t("deleting", name=m.checkpoint))
        self.dl_gauge.Show()
        self.dl_gauge.Pulse()           # indeterminate: a file delete has no
                                        # measurable fraction to report
        self.Layout()
        if self.on_announce:
            self.on_announce(t("say_deleting", name=m.checkpoint, size=size))

        self.btn_download.Enable(False)
        self.btn_delete.Enable(False)

        def work():
            ok = models.delete(m.key)
            wx.CallAfter(self._delete_finished, m.checkpoint, size, ok)

        threading.Thread(target=work, daemon=True, name="model-delete").start()

    def _delete_finished(self, name: str, size: str, ok: bool) -> None:
        self.dl_gauge.SetValue(0)
        self.dl_gauge.Hide()
        # Confirm either way. A silent success is indistinguishable from a
        # silent failure, and for a screen reader user there is not even a
        # visual change to notice.
        message = (t("delete_done_detail", name=name, size=size) if ok
                   else t("delete_failed", name=name))
        self.dl_status.SetValue(message)
        if self.on_announce:
            self.on_announce(message)
        if not ok:
            wx.MessageBox(message, t("app_name"), wx.OK | wx.ICON_ERROR, self)
        self.refresh_models()
        self.Layout()

    # ---------------------------------------------------------------- reset
    def on_reset(self, _evt) -> None:
        """Puts the controls back to the defaults without saving, so the user can
        still look at them and press Cancel."""
        if wx.MessageBox(t("reset_confirm"), t("reset_confirm_title"),
                         wx.YES_NO | wx.ICON_QUESTION, self) != wx.YES:
            return
        d = Settings()
        self.ui_language.SetSelection(UI_LANGUAGES.index(d.ui_language))
        self.model.SetSelection(MODELS.index(d.model))
        self.device.SetSelection(DEVICES.index(d.device))
        self.language.SetSelection(LANGUAGES.index(d.language))
        self.hotwords.SetValue(d.hotwords)
        self.insert_mode.SetSelection(INSERT_MODES.index(d.insert_mode))
        self.timestamps.SetValue(d.timestamps)
        self.rtl_auto.SetValue(d.rtl_auto)
        self.side_files.SetValue(d.save_side_files)
        self.k_transcribe.SetValue(d.hotkey_transcribe)
        self.k_window.SetValue(d.hotkey_window)
        self.k_settings.SetValue(d.hotkey_settings)
        self.k_status.SetValue(d.hotkey_status)
        self.k_cancel.SetValue(d.hotkey_cancel)
        self.k_dictate.SetValue(d.hotkey_dictate)
        self.speak.SetValue(d.speak)
        self.progress_every.SetValue(d.speak_progress_every)
        self.sapi.SetValue(d.speak_via_sapi)
        self.only_word.SetValue(d.only_when_word_focused)
        self.mic.SetSelection(0)
        self.dictation_model.SetSelection(
            DICTATION_MODELS.index(d.dictation_model))
        self.pause_ms.SetValue(d.dictation_pause_ms)
        self.target_s.SetValue(d.dictation_target_s)
        self.fillers.SetValue(d.dictation_remove_fillers)
        self.speak_phrases.SetValue(d.dictation_speak_phrases)
        self.was_reset = True
        self.ui_language.SetFocus()
        if self.on_announce:
            self.on_announce(t("say_reset"))

    def _complain(self, message: str, focus_ctrl) -> None:
        """A dialog, not a status line: it takes focus, so the screen reader
        reads the problem immediately."""
        wx.MessageBox(message, t("bad_setting_title"), wx.OK | wx.ICON_ERROR, self)
        focus_ctrl.SetFocus()
        focus_ctrl.SelectAll()


class HelpDialog(wx.Dialog):
    """Help in a read-only multiline field so the screen reader can navigate it
    line by line and word by word, which a static text block does not allow."""

    def __init__(self, parent):
        super().__init__(parent, title=t("help_title"),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        apply_direction(self)
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        txt = wx.TextCtrl(
            panel, value=i18n.help_text(),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2 | wx.HSCROLL)
        _set_accessible_name(txt, t("help_name"))
        txt.SetInsertionPoint(0)
        sizer.Add(txt, 1, wx.EXPAND | wx.ALL, PAD)

        btns = wx.StdDialogButtonSizer()
        close = wx.Button(panel, wx.ID_OK, t("btn_close"))
        close.SetDefault()
        btns.AddButton(close)
        btns.Realize()
        sizer.Add(btns, 0, wx.EXPAND | wx.ALL, PAD)

        panel.SetSizer(sizer)
        self.SetSize((720, 640))
        self.CentreOnScreen()
        self.SetEscapeId(wx.ID_OK)
        txt.SetFocus()


class MainWindow(wx.Frame):
    """The program's front door.

    A blind user needs one reachable place that lists everything the program
    can do - including a way to quit it, which a tray icon does not give them.
    Every button here also has a shortcut, so this window is a convenience and
    never the only route.
    """

    def __init__(self, app_frame):
        super().__init__(None, title=t("window_title"),
                         style=wx.DEFAULT_FRAME_STYLE & ~wx.MAXIMIZE_BOX)
        apply_direction(self)
        self.app_frame = app_frame
        panel = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)

        box = wx.StaticBoxSizer(wx.VERTICAL, panel, t("group_status"))
        self.status = wx.TextCtrl(box.GetStaticBox(), value=t("preparing"),
                                  style=wx.TE_READONLY | wx.TE_MULTILINE)
        _set_accessible_name(self.status, t("status_name"))
        self.status.SetMinSize((560, 96))
        box.Add(self.status, 1, wx.EXPAND | wx.ALL, PAD)
        outer.Add(box, 0, wx.EXPAND | wx.ALL, PAD)

        # ------------------------------------------------------------- jobs
        # Several files can be queued, and a cancelled one keeps unwinding in
        # the background, so the user needs to see every job and what each is
        # doing. A report-mode ListCtrl is used because it is a real Win32
        # list view that NVDA and JAWS navigate row by row.
        jb = wx.StaticBoxSizer(wx.VERTICAL, panel, t("group_jobs"))
        jbox = jb.GetStaticBox()
        self.jobs_list = wx.ListCtrl(
            jbox, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_HRULES)
        _set_accessible_name(self.jobs_list, t("jobs_name"))
        for i, (col, w) in enumerate((
            (t("col_file"), 200), (t("col_state"), 130),
            (t("col_progress"), 90), (t("col_language"), 80),
            (t("col_eta"), 150),
        )):
            self.jobs_list.InsertColumn(i, col, width=w)
        self.jobs_list.SetMinSize((-1, 150))
        jb.Add(self.jobs_list, 1, wx.EXPAND | wx.ALL, PAD)

        jrow = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_cancel_sel = wx.Button(jbox, label=t("btn_cancel_selected"))
        self.btn_clear_done = wx.Button(jbox, label=t("btn_clear_finished"))
        self.btn_cancel_sel.Bind(wx.EVT_BUTTON,
                                 lambda e: self.app_frame.cancel_selected_job())
        self.btn_clear_done.Bind(wx.EVT_BUTTON,
                                 lambda e: self.app_frame.clear_finished_jobs())
        jrow.Add(self.btn_cancel_sel, 0, wx.RIGHT, PAD)
        jrow.Add(self.btn_clear_done, 0)
        jb.Add(jrow, 0, wx.ALL, PAD)
        outer.Add(jb, 1, wx.EXPAND | wx.ALL, PAD)

        acts = wx.StaticBoxSizer(wx.VERTICAL, panel, t("group_actions"))
        self.buttons = {}
        for key, handler in (
            ("transcribe", app_frame.start_transcription),
            ("window_status", app_frame.speak_status),
            ("cancel", app_frame.cancel_job),
            ("settings", app_frame.open_settings),
            ("help", app_frame.open_help),
            ("about", self.on_about),
            ("quit", app_frame.quit_app),
        ):
            btn = wx.Button(acts.GetStaticBox(), label="")
            btn.Bind(wx.EVT_BUTTON, lambda e, h=handler: h())
            acts.Add(btn, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, PAD)
            self.buttons[key] = btn
        outer.Add(acts, 0, wx.EXPAND | wx.ALL, PAD)

        hide = wx.Button(panel, wx.ID_CANCEL, t("btn_hide"))
        hide.Bind(wx.EVT_BUTTON, lambda e: self.Hide())
        outer.Add(hide, 0, wx.EXPAND | wx.ALL, PAD)

        panel.SetSizer(outer)
        self.refresh_labels()
        outer.SetSizeHints(self)
        self.CentreOnScreen()

        self.Bind(wx.EVT_CLOSE, lambda e: self.Hide())
        self.SetAcceleratorTable(wx.AcceleratorTable([
            wx.AcceleratorEntry(wx.ACCEL_NORMAL, wx.WXK_F1, wx.ID_HELP),
            wx.AcceleratorEntry(wx.ACCEL_NORMAL, wx.WXK_ESCAPE, wx.ID_CANCEL),
        ]))
        self.Bind(wx.EVT_MENU, lambda e: self.app_frame.open_help(), id=wx.ID_HELP)
        self.Bind(wx.EVT_MENU, lambda e: self.Hide(), id=wx.ID_CANCEL)

        self.buttons["transcribe"].SetDefault()

    def on_about(self) -> None:
        dlg = wx.Dialog(self, title=t("about_title"),
                        style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        apply_direction(dlg)
        p = wx.Panel(dlg)
        sz = wx.BoxSizer(wx.VERTICAL)
        txt = wx.TextCtrl(p, value=i18n.about_text(),
                          style=wx.TE_MULTILINE | wx.TE_READONLY)
        _set_accessible_name(txt, t("about_name"))
        txt.SetInsertionPoint(0)
        sz.Add(txt, 1, wx.EXPAND | wx.ALL, PAD)
        b = wx.StdDialogButtonSizer()
        ok = wx.Button(p, wx.ID_OK, t("btn_close"))
        ok.SetDefault()
        b.AddButton(ok)
        b.Realize()
        sz.Add(b, 0, wx.EXPAND | wx.ALL, PAD)
        p.SetSizer(sz)
        dlg.SetSize((580, 440))
        dlg.CentreOnScreen()
        dlg.SetEscapeId(wx.ID_OK)
        txt.SetFocus()
        dlg.ShowModal()
        dlg.Destroy()

    def refresh_labels(self) -> None:
        """Shortcuts are printed on the buttons, so the labels must follow a
        change in the settings."""
        s = self.app_frame.settings
        self.SetTitle(t("window_title"))
        for key, string, hotkey in (
            ("transcribe", "btn_transcribe", s.hotkey_transcribe),
            ("window_status", "btn_status", s.hotkey_status),
            ("cancel", "btn_cancel_job", s.hotkey_cancel),
            ("settings", "btn_settings", s.hotkey_settings),
            ("help", "btn_help_f1", None),
            ("about", "btn_about", None),
            ("quit", "btn_quit", None),
        ):
            self.buttons[key].SetLabel(
                t(string, key=hotkey) if hotkey else t(string))

    def set_status(self, text: str) -> None:
        self.status.SetValue(text)

    def selected_job_id(self) -> int | None:
        i = self.jobs_list.GetFirstSelected()
        if i < 0:
            return None
        data = self.jobs_list.GetItemData(i)
        return data or None

    def set_jobs(self, rows: list[dict]) -> None:
        """Rebuild the list, keeping the selection on the same job.

        Each row is {id, file, state, progress, language, eta}. Rebuilding is
        cheap at this size and avoids the bookkeeping of in-place updates going
        out of step with the queue.
        """
        keep = self.selected_job_id()
        self.jobs_list.DeleteAllItems()
        select_row = -1
        for r in rows:
            i = self.jobs_list.InsertItem(self.jobs_list.GetItemCount(), r["file"])
            self.jobs_list.SetItem(i, 1, r["state"])
            self.jobs_list.SetItem(i, 2, r["progress"])
            self.jobs_list.SetItem(i, 3, r["language"])
            self.jobs_list.SetItem(i, 4, r["eta"])
            self.jobs_list.SetItemData(i, r["id"])
            if r["id"] == keep:
                select_row = i
        if select_row >= 0:
            self.jobs_list.Select(select_row)
        elif rows:
            self.jobs_list.Select(0)

    def show_without_focus(self) -> None:
        """Make the window visible when work starts, without taking focus - the
        user did not ask to go there, and moving focus would lose their place."""
        if not self.IsShown():
            self.Show()

    def show_and_focus(self) -> None:
        """Called from a hotkey, so it must come to the front and take focus -
        the user asked for it. That is the opposite of the progress window,
        which must never steal focus."""
        self.Show()
        self.Iconize(False)
        self.Raise()
        self.SetFocus()
        self.buttons["transcribe"].SetFocus()
