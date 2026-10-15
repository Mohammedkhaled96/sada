"""Live microphone capture, through the ffmpeg we already ship.

No new dependency. PortAudio (sounddevice, pyaudio) would be the obvious
choice, but it means another native binary inside a build that is already
211 MB, and another thing that can fail to load on a machine we cannot test.
ffmpeg is bundled regardless, `engine.audio` already drives it over a pipe, and
`find_ffmpeg` already knows where it lives in a frozen build.

What this costs: device *names*. ffmpeg's DirectShow input has no "default
device", so a name has to be discovered and passed in, and the names are
whatever the driver calls itself, in the system language. `list_input_devices`
reads them back out of ffmpeg, and `default_input_device` takes the first.

Output is always float32 mono in [-1, 1] at 16 kHz, which is what Whisper
wants, so nothing downstream has to resample.
"""
from __future__ import annotations

import queue
import re
import subprocess
import sys
import threading

import numpy as np

from engine.audio import SAMPLE_RATE, _creationflags, find_ffmpeg

# 100 ms of audio per block. Small enough that stopping feels immediate, large
# enough that the reader thread is not woken thousands of times a second.
BLOCK_MS = 100

# How long ffmpeg is given to shut down politely before being killed.
STOP_TIMEOUT_S = 2.0


class MicrophoneError(RuntimeError):
    """Capture could not start, or died while running.

    The message is a full sentence because it is shown and spoken as-is.
    """


def _input_format() -> str:
    """ffmpeg's capture backend for this platform.

    Only `dshow` is exercised: dictation is part of the Windows desktop
    application. The others are here so this module fails with a real error
    message instead of pretending Windows everywhere.
    """
    if sys.platform == "win32":
        return "dshow"
    if sys.platform == "darwin":
        return "avfoundation"
    return "alsa"


def _device_argument(device: str) -> str:
    """How the chosen device is spelled for the active input format."""
    if sys.platform == "win32":
        return f"audio={device}"
    return device


# ffmpeg lists DirectShow devices on stderr, one per line, as:
#   [dshow @ 000001] "Microphone (Realtek(R) Audio)" (audio)
#   [dshow @ 000001]   Alternative name "@device_cm_{...}"
# The quoted name on a line tagged (audio) is the one `-i audio=` accepts. The
# alternative name is a device path, which also works but is unreadable, so it
# is deliberately skipped.
_DSHOW_LINE = re.compile(r'"([^"]+)"\s*\((audio|video)\)')


def _parse_dshow_devices(stderr: str) -> list[str]:
    """Audio device names, in the order ffmpeg reported them."""
    names: list[str] = []
    for match in _DSHOW_LINE.finditer(stderr):
        name, kind = match.group(1), match.group(2)
        if kind == "audio" and name not in names:
            names.append(name)
    return names


def list_input_devices() -> list[str]:
    """Capture devices ffmpeg can see, best first.

    Returns an empty list rather than raising: a machine with no microphone is
    a normal state the caller has to handle anyway, and it is not an error
    until someone actually tries to record.
    """
    if sys.platform != "win32":
        return []
    cmd = [
        find_ffmpeg(),
        "-hide_banner",
        "-list_devices", "true",
        "-f", "dshow",
        "-i", "dummy",
    ]
    try:
        # Listing devices is not a conversion, so ffmpeg always exits non-zero
        # here ("Immediate exit requested"). The names are on stderr either way.
        proc = subprocess.run(cmd, capture_output=True, timeout=20,
                              creationflags=_creationflags())
    except Exception:
        return []
    return _parse_dshow_devices(proc.stderr.decode("utf-8", "replace"))


def default_input_device() -> str | None:
    """The device to use when the user has not chosen one."""
    devices = list_input_devices()
    return devices[0] if devices else None


class Microphone:
    """A running capture, read as float32 blocks.

    Usage:

        mic = Microphone()
        mic.start()
        while recording:
            block = mic.read(timeout=0.5)   # float32 samples, or None
        mic.stop()

    One instance is one recording. `start` after `stop` is not supported;
    make a new Microphone, because ffmpeg's process is gone.

    `input_args` replaces the capture device with any ffmpeg input, which is
    how the streaming path is tested without a microphone - for example
    `["-f", "lavfi", "-i", "sine=frequency=440:duration=2"]`.
    """

    def __init__(self, device: str | None = None,
                 sampling_rate: int = SAMPLE_RATE,
                 block_ms: int = BLOCK_MS,
                 input_args: list[str] | None = None):
        self.device = device
        self.sampling_rate = sampling_rate
        self.block_bytes = max(2, int(sampling_rate * block_ms / 1000)) * 2
        self._input_args = input_args
        self._proc: subprocess.Popen | None = None
        self._blocks: queue.Queue = queue.Queue()
        self._stderr: list[str] = []
        self._reader: threading.Thread | None = None
        self._draining: threading.Thread | None = None
        self._stopping = threading.Event()

    # ------------------------------------------------------------------ start
    def _command(self) -> list[str]:
        if self._input_args is not None:
            source = list(self._input_args)
        else:
            device = self.device or default_input_device()
            if not device:
                raise MicrophoneError(
                    "No microphone was found. Connect one, or pick an input "
                    "device in Settings."
                )
            source = [
                "-f", _input_format(),
                # 50 ms of driver buffering. The default is 500 ms on dshow,
                # which delays the end of every phrase by half a second.
                *(["-audio_buffer_size", "50"] if sys.platform == "win32" else []),
                "-i", _device_argument(device),
            ]
        return [
            find_ffmpeg(),
            "-nostdin",
            "-hide_banner",
            "-loglevel", "error",
            *source,
            "-vn",
            "-f", "s16le",
            "-acodec", "pcm_s16le",
            "-ac", "1",
            "-ar", str(self.sampling_rate),
            "-",
        ]

    def start(self) -> None:
        if self._proc is not None:
            raise MicrophoneError("This recording has already been started.")
        cmd = self._command()
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=_creationflags(),
            )
        except Exception as exc:
            raise MicrophoneError(f"Recording could not start: {exc}") from exc

        self._reader = threading.Thread(target=self._read_loop, daemon=True,
                                        name="mic-reader")
        self._reader.start()
        # stderr is drained on its own thread: ffmpeg blocks once the pipe
        # fills, and a stalled ffmpeg looks exactly like a silent microphone.
        self._draining = threading.Thread(target=self._drain_stderr, daemon=True,
                                          name="mic-stderr")
        self._draining.start()

    # ------------------------------------------------------------------- read
    def _read_loop(self) -> None:
        stdout = self._proc.stdout if self._proc else None
        if stdout is None:
            return
        try:
            while not self._stopping.is_set():
                data = stdout.read(self.block_bytes)
                if not data:
                    break               # ffmpeg closed the pipe
                self._blocks.put(data)
        except Exception:
            pass                        # stop() closes the pipe under us
        finally:
            self._blocks.put(None)      # end of stream

    def _drain_stderr(self) -> None:
        stderr = self._proc.stderr if self._proc else None
        if stderr is None:
            return
        try:
            for line in iter(stderr.readline, b""):
                text = line.decode("utf-8", "replace").strip()
                if text:
                    self._stderr.append(text)
                    del self._stderr[:-20]      # keep only the last few
        except Exception:
            pass

    def read(self, timeout: float = 0.5) -> np.ndarray | None:
        """The next block of audio, or None when the stream has ended.

        Raises MicrophoneError if ffmpeg failed, so a capture that never
        produced a sample reports why instead of looking like silence.
        """
        try:
            data = self._blocks.get(timeout=timeout)
        except queue.Empty:
            return np.empty(0, dtype=np.float32)

        if data is None:
            if not self._stopping.is_set():
                code = self._exit_code()
                if code not in (0, None):
                    raise MicrophoneError(self._failure_message(code))
            return None

        pcm = np.frombuffer(data, dtype=np.int16)
        return (pcm.astype(np.float32) / 32768.0).copy()

    def _exit_code(self) -> int | None:
        """ffmpeg's exit code, waiting briefly for it to actually exit.

        The reader thread signals end of stream the moment stdout closes,
        which happens before the process is reaped - so polling here returns
        None for a run that is about to report a failure, and a missing device
        looked exactly like a microphone recording silence.
        """
        proc = self._proc
        if proc is None:
            return None
        try:
            code = proc.wait(timeout=STOP_TIMEOUT_S)
        except Exception:
            return proc.poll()
        # stderr carries the reason; give its reader a moment to append it,
        # otherwise the message is empty exactly when it is most needed.
        if self._draining is not None:
            self._draining.join(timeout=0.5)
        return code

    def _failure_message(self, code: int | None) -> str:
        detail = "; ".join(self._stderr[-3:]).strip()
        if detail:
            return f"Recording stopped: {detail}"
        return f"Recording stopped unexpectedly (ffmpeg exit code {code})."

    # ------------------------------------------------------------------- stop
    def stop(self) -> None:
        """Idempotent, and safe to call from another thread while read blocks."""
        self._stopping.set()
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=STOP_TIMEOUT_S)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass
        self._blocks.put(None)

    def __enter__(self) -> "Microphone":
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()
