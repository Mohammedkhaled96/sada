"""Audio decoding via ffmpeg.

faster-whisper's own `decode_audio` goes through PyAV, and PyAV 19 removed the
`metadata_errors` argument it passes, so that path raises TypeError. We decode
with ffmpeg ourselves instead: the installer bundles ffmpeg regardless, it
handles more containers than PyAV (including video), and it removes a version
coupling that would break again on the next PyAV release.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000


def _creationflags() -> int:
    """Keep ffmpeg from flashing a console window when run from the daemon."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def find_ffmpeg(name: str = "ffmpeg") -> str:
    """Prefer a copy shipped with the application, fall back to PATH.

    `sys._MEIPASS` is where PyInstaller unpacks a frozen build, so a packaged
    copy is found there first; otherwise look in bin/ beside the project.
    """
    roots = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots += [Path(meipass) / "bin", Path(meipass)]
    roots.append(Path(__file__).resolve().parent.parent / "bin")
    for root in roots:
        candidate = root / f"{name}.exe"
        if candidate.exists():
            return str(candidate)
    bundled = Path(__file__).resolve().parent.parent / "bin" / f"{name}.exe"
    if bundled.exists():
        return str(bundled)
    found = shutil.which(name)
    if not found:
        raise RuntimeError(
            f"{name} not found. Install it and put it on PATH, or drop {name}.exe "
            f"in {bundled.parent}"
        )
    return found


def decode_audio(path: str | Path, sampling_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Decode any audio or video file to float32 mono at `sampling_rate`.

    Returns samples in [-1, 1], which is what Whisper expects.
    """
    path = Path(path)
    cmd = [
        find_ffmpeg(),
        "-nostdin",
        "-hide_banner",
        "-loglevel", "error",
        "-threads", "0",
        "-i", str(path),
        "-vn",                      # ignore any video stream
        "-map", "a:0",              # first audio stream only
        "-f", "s16le",
        "-acodec", "pcm_s16le",
        "-ac", "1",                 # mono
        "-ar", str(sampling_rate),
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, creationflags=_creationflags())

    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"ffmpeg failed to decode {path.name}:\n{err}")
    if not proc.stdout:
        raise RuntimeError(f"{path.name} contains no decodable audio stream")

    pcm = np.frombuffer(proc.stdout, dtype=np.int16)
    return (pcm.astype(np.float32) / 32768.0).copy()
