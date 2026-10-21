"""User settings, stored as JSON beside the rest of the program's data.

Every value has a default that works, so a missing or corrupt file never stops
the app from starting - it falls back and rewrites a clean one.

The location comes from engine.paths, so settings sit wherever models do:
beside the project when run from a checkout, and under the user profile in
an installed release. There is no second definition of it here - this file
used to carry its own copy, which is how the project ended up with five
definitions of where its data lives.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from app import i18n
from engine import paths

# Re-exported rather than redefined: tests replace these to write into a
# temporary directory, and engine.paths is the single source of truth.
STATE_DIR = paths.STATE_DIR
SETTINGS_FILE = paths.SETTINGS_FILE

# Where releases up to 1.0.1 kept the settings file. Read once, if the new
# location has nothing, so moving the file does not silently reset a
# configuration the user had already chosen.
LEGACY_SETTINGS_FILE = paths.LEGACY_STATE_DIR / "settings.json"

MODELS = ["turbo", "large-v3", "medium", "small"]
DEVICES = ["auto", "cuda", "cpu"]
INSERT_MODES = ["cursor", "end", "new"]
LANGUAGES = ["auto", "ar", "en", "fr", "de", "es", "tr", "ur", "fa"]
UI_LANGUAGES = i18n.UI_LANGUAGES


def model_labels() -> list[str]:
    return [i18n.t(f"model_{m}") for m in MODELS]


def device_labels() -> list[str]:
    return [i18n.t(f"device_{d}") for d in DEVICES]


def insert_labels() -> list[str]:
    return [i18n.t(f"insert_{m}") for m in INSERT_MODES]


def language_labels() -> list[str]:
    return [i18n.t(f"lang_{l}") for l in LANGUAGES]


def ui_language_labels() -> list[str]:
    return [i18n.UI_LANGUAGE_NAMES[c] for c in UI_LANGUAGES]


@dataclass
class Settings:
    # interface - English by default, Arabic is a setting
    ui_language: str = "en"

    # engine
    model: str = "turbo"
    device: str = "auto"
    language: str = "auto"
    hotwords: str = ""

    # output
    insert_mode: str = "cursor"
    timestamps: bool = False
    rtl_auto: bool = True
    save_side_files: bool = True

    # shortcuts
    hotkey_transcribe: str = "Ctrl+Shift+T"
    hotkey_settings: str = "Ctrl+Shift+O"
    hotkey_status: str = "Ctrl+Shift+U"
    hotkey_cancel: str = "Ctrl+Shift+X"
    hotkey_window: str = "Ctrl+Shift+W"

    # screen reader behaviour
    speak: bool = True
    speak_progress_every: int = 30          # seconds of wall time, 0 = never
    speak_via_sapi: bool = False            # only when no screen reader is found
    only_when_word_focused: bool = True

    def validate(self) -> "Settings":
        """Clamp anything out of range rather than refusing to start."""
        if self.ui_language not in UI_LANGUAGES:
            self.ui_language = "en"
        if self.model not in MODELS:
            self.model = "turbo"
        if self.device not in DEVICES:
            self.device = "auto"
        if self.language not in LANGUAGES:
            self.language = "auto"
        if self.insert_mode not in INSERT_MODES:
            self.insert_mode = "cursor"
        self.speak_progress_every = max(0, min(600, int(self.speak_progress_every)))
        for f in ("hotkey_transcribe", "hotkey_settings", "hotkey_status",
                  "hotkey_cancel", "hotkey_window"):
            if not parse_hotkey(getattr(self, f)):
                setattr(self, f, Settings.__dataclass_fields__[f].default)
        return self

    @property
    def language_or_none(self) -> str | None:
        return None if self.language == "auto" else self.language


def _source_file() -> Path:
    """The settings file to read.

    The new location if it holds anything, otherwise the one releases up to
    1.0.1 used. Without this, moving the file would quietly hand every
    existing user a fresh set of defaults - their model, their shortcuts
    and their language, all gone, with nothing to say why.
    """
    if SETTINGS_FILE.exists():
        return SETTINGS_FILE
    if LEGACY_SETTINGS_FILE != SETTINGS_FILE and LEGACY_SETTINGS_FILE.exists():
        return LEGACY_SETTINGS_FILE
    return SETTINGS_FILE


def load() -> Settings:
    """Also applies the stored interface language, so every string produced
    after this call is in the user's chosen language.

    Settings read from the old location are written straight back to the new
    one. Without that, the folder cleanup that runs next at startup would
    remove the only copy of them.
    """
    source = _source_file()
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
        known = {f.name for f in fields(Settings)}
        s = Settings(**{k: v for k, v in raw.items() if k in known}).validate()
    except Exception:
        s = Settings()
    i18n.set_language(s.ui_language)
    if source != SETTINGS_FILE:
        try:
            save(s)
        except OSError:
            pass        # a read-only location; the settings still apply
    return s


def save(s: Settings) -> Path:
    i18n.set_language(s.ui_language)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(s), ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(SETTINGS_FILE)              # atomic, so a crash cannot truncate it
    return SETTINGS_FILE


# --------------------------------------------------------------------------
# hotkey strings <-> Win32 modifiers and virtual key codes
# --------------------------------------------------------------------------
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x0001, 0x0002, 0x0004, 0x0008

_MODS = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "shift": MOD_SHIFT,
    "alt": MOD_ALT,
    "win": MOD_WIN, "windows": MOD_WIN,
}
_NAMED_KEYS = {
    "space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09,
    "escape": 0x1B, "esc": 0x1B, "backspace": 0x08, "delete": 0x2E,
    "insert": 0x2D, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},          # F1..F12 = 0x70..0x7B
}


def parse_hotkey(text: str) -> tuple[int, int] | None:
    """"Ctrl+Shift+T" -> (modifiers, virtual key code). None when unparseable.

    A hotkey with no modifier is rejected: registering a bare letter globally
    would swallow it from every application on the machine.
    """
    if not text or not text.strip():
        return None
    parts = [p.strip().lower() for p in text.replace("-", "+").split("+") if p.strip()]
    if not parts:
        return None

    mods, key = 0, None
    for p in parts:
        if p in _MODS:
            mods |= _MODS[p]
        elif p in _NAMED_KEYS:
            key = _NAMED_KEYS[p]
        elif len(p) == 1 and (p.isalpha() or p.isdigit()):
            key = ord(p.upper())
        else:
            return None

    if key is None or mods == 0:
        return None
    return mods, key


def format_hotkey(text: str) -> str:
    """Normalised for display, so the settings dialog shows "Ctrl+Shift+T"
    whatever the user typed."""
    parsed = parse_hotkey(text)
    if not parsed:
        return text
    mods, key = parsed
    out = []
    if mods & MOD_CONTROL:
        out.append("Ctrl")
    if mods & MOD_ALT:
        out.append("Alt")
    if mods & MOD_SHIFT:
        out.append("Shift")
    if mods & MOD_WIN:
        out.append("Win")
    name = next((k for k, v in _NAMED_KEYS.items() if v == key), None)
    out.append(name.upper() if name else chr(key))
    return "+".join(out)
