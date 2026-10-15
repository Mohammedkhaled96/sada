"""Microphone capture.

The streaming path is checked against a real ffmpeg with a synthetic input
rather than a microphone: a CI runner has no audio device, and faking the pipe
would only test the fake. `input_args` swaps the capture device for an lavfi
tone, so everything after it - the pipe, the reader thread, the block size, the
float conversion, end of stream, failure reporting - is the code that ships.

Run:  PYTHONUTF8=1 .venv/Scripts/python.exe tests/test_mic.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from engine import mic
from engine.audio import SAMPLE_RATE

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))
    if not ok:
        failures.append(label)


def ffmpeg_available() -> bool:
    try:
        mic.find_ffmpeg()
        return True
    except Exception:
        return False


TONE_1S = ["-f", "lavfi", "-i", "sine=frequency=440:duration=1"]
TONE_FOREVER = ["-f", "lavfi", "-i", "sine=frequency=440"]


# --------------------------------------------------------------- device names
print("--- device enumeration ---")

stderr = (
    '[dshow @ 0001] "Integrated Camera" (video)\n'
    '[dshow @ 0001]   Alternative name "@device_pnp_\\\\?\\usb#vid_0001"\n'
    '[dshow @ 0001] "Microphone (Realtek(R) Audio)" (audio)\n'
    '[dshow @ 0001]   Alternative name "@device_cm_{33D9A762}"\n'
    '[dshow @ 0001] "Headset (Jabra EVOLVE)" (audio)\n'
)
names = mic._parse_dshow_devices(stderr)
check("audio devices parsed, video ignored",
      names == ["Microphone (Realtek(R) Audio)", "Headset (Jabra EVOLVE)"],
      str(names))
check("an alternative name is not offered as a device",
      not any(n.startswith("@device") for n in names))
check("nothing listed is an empty list, not an error",
      mic._parse_dshow_devices("") == []
      and mic._parse_dshow_devices("[dshow @ 1] Could not enumerate") == [])
check("a duplicate name is reported once",
      mic._parse_dshow_devices('[dshow @ 1] "Mic" (audio)\n'
                               '[dshow @ 1] "Mic" (audio)\n') == ["Mic"])

m = mic.Microphone()
m.device = None
saved, mic.default_input_device = mic.default_input_device, lambda: None
try:
    try:
        m._command()
        check("no microphone gives a clear message", False, "no error raised")
    except mic.MicrophoneError as exc:
        check("no microphone gives a clear message", "No microphone" in str(exc),
              str(exc))
finally:
    mic.default_input_device = saved

m = mic.Microphone(input_args=TONE_1S)
m._proc = object()                      # pretend a capture is running
try:
    m.start()
    check("starting twice is refused", False, "no error raised")
except mic.MicrophoneError:
    check("starting twice is refused", True)
m._proc = None


# ------------------------------------------------------------------ streaming
if not ffmpeg_available():
    print("\n--- streaming: SKIPPED, ffmpeg not available ---")
else:
    print("\n--- streaming ---")

    m = mic.Microphone(input_args=TONE_1S, block_ms=100)
    m.start()
    blocks = []
    ended = False
    deadline = time.monotonic() + 30
    try:
        while time.monotonic() < deadline:
            block = m.read(timeout=1.0)
            if block is None:
                ended = True
                break
            if block.size:
                blocks.append(block)
    finally:
        m.stop()

    check("the stream reports its end", ended)
    check("audio arrived", bool(blocks), f"{len(blocks)} blocks")
    if blocks:
        audio = np.concatenate(blocks)
        check("samples are float32", audio.dtype == np.float32, str(audio.dtype))
        check("one second of input is about one second of samples",
              SAMPLE_RATE * 0.8 <= audio.size <= SAMPLE_RATE * 1.2,
              f"{audio.size} samples")
        check("samples are normalised into [-1, 1]", float(np.abs(audio).max()) <= 1.0)
        check("a 440 Hz tone is not silence", float(np.abs(audio).max()) > 0.1,
              f"peak {float(np.abs(audio).max()):.2f}")

    m = mic.Microphone(input_args=TONE_1S, block_ms=250)
    check("block size follows block_ms",
          m.block_bytes == int(SAMPLE_RATE * 0.25) * 2, str(m.block_bytes))
    m.start()
    first = np.empty(0, dtype=np.float32)
    try:
        for _ in range(20):
            first = m.read(timeout=2.0)
            if first is None or first.size:
                break
        check("a full block is 250 ms of samples",
              first is not None and first.size == int(SAMPLE_RATE * 0.25),
              "end of stream" if first is None else f"{first.size} samples")
    finally:
        m.stop()

    m = mic.Microphone(input_args=TONE_FOREVER)
    m.start()
    m.read(timeout=2.0)
    m.stop()
    m.stop()                            # must not raise
    check("stop is idempotent and releases the process", m._proc is None)

    m = mic.Microphone(input_args=TONE_FOREVER)
    m.start()
    m.stop()
    reached_end = False
    for _ in range(50):
        if m.read(timeout=0.2) is None:
            reached_end = True
            break
    check("read reaches end of stream after stop instead of hanging", reached_end)

    # A capture that produces nothing must say why. Before this, a missing
    # device was indistinguishable from a microphone recording silence.
    m = mic.Microphone(input_args=["-f", "dshow", "-i",
                                   "audio=NoSuchDevice_SadaTest"])
    m.start()
    reported = ""
    try:
        for _ in range(100):
            if m.read(timeout=0.5) is None:
                break
    except mic.MicrophoneError as exc:
        reported = str(exc)
    finally:
        m.stop()
    if sys.platform == "win32":
        check("a missing device reports why, not silence",
              "Recording stopped" in reported, reported[:70] or "no error raised")
    else:
        print("  skip  a missing device reports why   [dshow is Windows-only]")


# ----------------------------------------------------------------------- done
print("\n" + "=" * 60)
if failures:
    print(f"FAILED: {len(failures)}")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("all checks passed")
print("=" * 60)
