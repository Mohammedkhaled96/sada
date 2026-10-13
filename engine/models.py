"""Which model checkpoints are on this machine, and fetching the ones that are not.

Choosing a model in Settings used to start a multi-gigabyte download inside the
model-loading thread: no progress, no size warning, the window frozen, and if it
failed a zero-byte file was left behind that then looked installed. So the user
saw "the model will not load" with nothing to explain it.

Here the download is explicit, shows progress and a size, can be cancelled, and
writes to a `.part` file that is only renamed into place once the SHA-256 matches
the one in the URL. A failed or cancelled download therefore leaves nothing
behind that can be mistaken for an installed model.
"""
from __future__ import annotations

import hashlib
import os
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from engine.backends import MODEL_NAMES, OPENAI_SHA, openai_url
from engine.paths import MODELS_DIR

# Content-Length as served by OpenAI's CDN, so a size can be shown before any
# network call and a truncated file can be spotted without hashing 3 GB.
SIZES = {
    "tiny": 75_572_083,
    "small": 483_617_219,
    "medium": 1_528_008_539,
    "large-v3": 3_087_371_615,
    "large-v3-turbo": 1_617_941_637,
}


def human(size: int) -> str:
    if size >= 1 << 30:
        return f"{size / (1 << 30):.1f} GB"
    return f"{size / (1 << 20):.0f} MB"


@dataclass
class ModelInfo:
    key: str                 # the name used in settings, e.g. "turbo"
    checkpoint: str          # the OpenAI checkpoint name, e.g. "large-v3-turbo"
    path: Path
    expected_size: int
    installed: bool
    actual_size: int

    @property
    def size_text(self) -> str:
        return human(self.expected_size)

    @property
    def partial(self) -> bool:
        """On disk but the wrong size: a previous download did not finish."""
        return self.actual_size > 0 and not self.installed


def info(model_key: str) -> ModelInfo:
    checkpoint = MODEL_NAMES[model_key][1]
    path = MODELS_DIR / f"{checkpoint}.pt"
    expected = SIZES.get(checkpoint, 0)
    actual = path.stat().st_size if path.exists() else 0
    # Size is enough to call it installed; the hash is checked at download time
    # and hashing gigabytes on every settings open would be unkind.
    return ModelInfo(model_key, checkpoint, path, expected,
                     installed=bool(expected) and actual == expected,
                     actual_size=actual)


def all_info() -> list[ModelInfo]:
    return [info(k) for k in MODEL_NAMES if k != "tiny"]


def installed_keys() -> list[str]:
    return [m.key for m in all_info() if m.installed]


class DownloadCancelled(Exception):
    pass


def download(model_key: str, on_progress=None,
             cancel: threading.Event | None = None) -> Path:
    """Fetch a checkpoint to models/, verifying it before putting it in place.

    on_progress(done_bytes, total_bytes) is called as it goes.
    Raises DownloadCancelled if `cancel` is set, leaving no partial file behind.
    """
    m = info(model_key)
    if m.installed:
        return m.path

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    part = m.path.with_suffix(".pt.part")
    url = openai_url(m.checkpoint)
    expected_sha = OPENAI_SHA[m.checkpoint]

    sha = hashlib.sha256()
    done = 0
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length") or m.expected_size or 0)
            with part.open("wb") as f:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise DownloadCancelled()
                    block = resp.read(1 << 20)
                    if not block:
                        break
                    f.write(block)
                    sha.update(block)
                    done += len(block)
                    if on_progress:
                        on_progress(done, total)

        if sha.hexdigest() != expected_sha:
            raise OSError(
                f"the downloaded file does not match its checksum "
                f"({done} bytes); it may be corrupt or truncated")

        part.replace(m.path)        # atomic: only now does it look installed
        return m.path
    except BaseException:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def delete(model_key: str) -> bool:
    m = info(model_key)
    try:
        m.path.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def cleanup_partials() -> list[str]:
    """Remove leftovers from an interrupted download, including the zero-byte
    files the old blocking download could leave behind."""
    removed = []
    if not MODELS_DIR.exists():
        return removed
    for f in MODELS_DIR.glob("*.part"):
        try:
            f.unlink()
            removed.append(f.name)
        except OSError:
            pass
    for m in all_info():
        if m.actual_size == 0 and m.path.exists():
            try:
                m.path.unlink()
                removed.append(m.path.name)
            except OSError:
                pass
    return removed
