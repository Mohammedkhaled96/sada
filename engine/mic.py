"""Microphone capture, and the measurements that prove it is working.

The program was once deaf without saying so: both microphones recorded digital
silence, peak 0.0000, and dictation produced nothing with no explanation. That
is the failure this module exists to make impossible, so almost everything here
is about measuring the signal rather than opening the stream.

Reading the Windows settings is not enough, and this machine is the proof. All
three microphone-consent keys say Allow and every capture endpoint reports OK,
yet the built-in array records 0.00003 peak - silence - while the USB headset
records 0.03. Only recording and measuring tells them apart.

PortAudio is reached through sounddevice. Devices are opened at 16 kHz when
they accept it (MME and DirectSound do) and at their own rate otherwise (WASAPI
refuses anything else), in which case the audio is resampled here.
"""
from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field

import numpy as np

SAMPLE_RATE = 16000

# Below this peak, over a whole recording, nothing was heard. Room tone on a
# working microphone measures around 0.003; a muted one measures 0.00003.
SILENT_PEAK = 0.001
# Below this, something is arriving but far too quietly to transcribe.
QUIET_PEAK = 0.02
# At or above this a sample is at the top of the scale and the waveform is
# being flattened; a few are normal, a tenth of a second of them is not.
CLIP_LEVEL = 0.99
CLIP_FRACTION = 0.01


class MicError(RuntimeError):
    """Something went wrong that the user can be told about in one sentence."""


@dataclass
class Device:
    index: int
    name: str
    default_samplerate: int
    hostapi: str
    is_default: bool = False

    def __str__(self) -> str:
        return self.name


@dataclass
class Levels:
    """What a recording actually contained."""

    peak: float
    rms: float
    dc_offset: float
    clipped: float
    seconds: float

    @property
    def silent(self) -> bool:
        return self.peak < SILENT_PEAK

    @property
    def quiet(self) -> bool:
        return not self.silent and self.peak < QUIET_PEAK

    @property
    def clipping(self) -> bool:
        return self.clipped > CLIP_FRACTION


def _sd():
    try:
        import sounddevice as sd
    except Exception as exc:                      # pragma: no cover - import guard
        raise MicError(f"sounddevice is not available: {exc}") from exc
    return sd


# --------------------------------------------------------------------- devices

# Windows exposes every microphone once per sound interface. They are the same
# hardware and do not behave the same: WASAPI refuses any rate but its own, MME
# truncates the device name at 31 characters, and WDM-KS is a low-level path
# that every real device also appears on elsewhere. Listing all of them put
# eighteen entries in the box for two microphones - which, read aloud one arrow
# press at a time, is not a choice, it is a maze.
API_ORDER = ["Windows DirectSound", "MME", "Windows WASAPI", "Windows WDM-KS"]

# Aliases for "whatever Windows is set to", which this list already offers as
# its first entry under a name people recognise.
ALIASES = ("sound mapper", "primary sound capture driver")


def all_input_devices() -> list[Device]:
    """Every recording device on every sound interface, unfiltered.

    The check uses this: when the chosen microphone records silence, the one
    that would have worked may only exist on another interface.
    """
    sd = _sd()
    try:
        default_index = sd.default.device[0]
    except Exception:
        default_index = None
    apis = {i: a["name"] for i, a in enumerate(sd.query_hostapis())}

    found = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] < 1:
            continue
        found.append(Device(
            index=i,
            name=str(d["name"]).strip(),
            default_samplerate=int(d["default_samplerate"]),
            hostapi=apis.get(d["hostapi"], "?"),
            is_default=(i == default_index),
        ))
    return found


def input_devices() -> list[Device]:
    """One entry per actual microphone, on the interface most likely to work."""
    def rank(device: Device) -> int:
        return (API_ORDER.index(device.hostapi)
                if device.hostapi in API_ORDER else len(API_ORDER))

    every = all_input_devices()
    # WDM-KS is the kernel path. It spells the same hardware differently
    # ("Microphone Array 1 (Realtek HD Audio Mic input with SST)" against
    # "Microphone Array (2- Realtek(R) Audio)"), so it cannot be folded in by
    # name - and it exposes outputs as inputs besides. Everything real is on
    # one of the other interfaces, so it is only offered when nothing else is.
    usable = [d for d in every if d.hostapi != "Windows WDM-KS"] or every

    best: dict[str, Device] = {}
    for device in sorted(usable, key=rank):
        if any(alias in device.name.lower() for alias in ALIASES):
            continue
        # MME cuts names short, so the same microphone appears under a prefix
        # of its own name. Longest spelling wins, and shorter ones fold into it.
        key = next((k for k in best
                    if k.startswith(device.name) or device.name.startswith(k)),
                   device.name)
        if key not in best:
            best[key] = device
        elif device.is_default:
            best[key].is_default = True
    return list(best.values())


def default_input() -> Device | None:
    for d in input_devices():
        if d.is_default:
            return d
    return None


# ----------------------------------------------------------------- measurement

def measure(audio: np.ndarray) -> Levels:
    """What is in this recording, as numbers a message can be written from."""
    if audio.size == 0:
        return Levels(0.0, 0.0, 0.0, 0.0, 0.0)
    audio = audio.reshape(-1).astype(np.float32)
    return Levels(
        peak=float(np.abs(audio).max()),
        rms=float(np.sqrt(np.mean(audio ** 2))),
        dc_offset=float(np.mean(audio)),
        clipped=float(np.mean(np.abs(audio) >= CLIP_LEVEL)),
        seconds=len(audio) / SAMPLE_RATE,
    )


def resample(audio: np.ndarray, src_rate: int, dst_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Rate conversion good enough for speech.

    Decimating 48 kHz by simply taking every third sample folds everything
    above 8 kHz back into the band as noise, so the signal is averaged over the
    decimation window first - a crude low-pass, but a low-pass.
    """
    if src_rate == dst_rate or audio.size == 0:
        return audio.astype(np.float32)
    audio = audio.reshape(-1).astype(np.float32)
    if src_rate > dst_rate:
        factor = int(round(src_rate / dst_rate))
        if factor > 1:
            usable = (len(audio) // factor) * factor
            if usable:
                audio = audio[:usable].reshape(-1, factor).mean(axis=1)
                src_rate = src_rate / factor
    n = int(round(len(audio) * dst_rate / src_rate))
    if n <= 1:
        return audio
    return np.interp(np.linspace(0, len(audio) - 1, n),
                     np.arange(len(audio)), audio).astype(np.float32)


# -------------------------------------------------------------------- recording

def _open_rate(sd, device: int | None, wanted: int) -> int:
    """The rate this device will actually accept, preferring ours.

    WASAPI devices refuse everything but their own rate, and the error says
    only "Invalid sample rate" - which is why this is asked before opening a
    stream for real rather than discovered in the middle of dictation.
    """
    try:
        sd.check_input_settings(device=device, samplerate=wanted, channels=1,
                                dtype="float32")
        return wanted
    except Exception:
        pass
    try:
        info = sd.query_devices(device if device is not None else sd.default.device[0])
        native = int(info["default_samplerate"])
        sd.check_input_settings(device=device, samplerate=native, channels=1,
                                dtype="float32")
        return native
    except Exception as exc:
        raise MicError(str(exc)) from exc


def record(seconds: float, device: int | None = None) -> np.ndarray:
    """Record for a fixed time. Used by the check, never by dictation."""
    sd = _sd()
    rate = _open_rate(sd, device, SAMPLE_RATE)
    try:
        data = sd.rec(int(seconds * rate), samplerate=rate, channels=1,
                      dtype="float32", device=device)
        sd.wait()
    except Exception as exc:
        raise MicError(str(exc)) from exc
    return resample(np.asarray(data).reshape(-1), rate)


class Recorder:
    """A microphone kept open, handing out audio as it arrives.

    The audio callback runs on PortAudio's own thread and must never block, so
    it only puts blocks on a queue. Everything else happens on the reader's
    thread.
    """

    def __init__(self, device: int | None = None, blocksize: int = 1600):
        self.device = device
        self.blocksize = blocksize
        self._q: "queue.Queue[np.ndarray]" = queue.Queue()
        self._stream = None
        self._rate = SAMPLE_RATE
        self._lock = threading.Lock()
        self.overflows = 0
        self.error: str | None = None

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        sd = _sd()
        with self._lock:
            if self._stream is not None:
                return
            self._rate = _open_rate(sd, self.device, SAMPLE_RATE)
            block = int(self.blocksize * self._rate / SAMPLE_RATE)

            def callback(indata, frames, time_info, status):
                if status and getattr(status, "input_overflow", False):
                    self.overflows += 1
                self._q.put(np.asarray(indata, dtype=np.float32).reshape(-1).copy())

            try:
                self._stream = sd.InputStream(
                    samplerate=self._rate, channels=1, dtype="float32",
                    blocksize=block, device=self.device, callback=callback)
                self._stream.start()
            except Exception as exc:
                self._stream = None
                raise MicError(str(exc)) from exc

    def stop(self) -> None:
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass                      # closing a dead stream is not an error

    @property
    def running(self) -> bool:
        return self._stream is not None

    # -- reading -----------------------------------------------------------
    def read(self, timeout: float = 0.1) -> np.ndarray:
        """Whatever has arrived since the last call, at 16 kHz."""
        blocks = []
        try:
            blocks.append(self._q.get(timeout=timeout))
        except queue.Empty:
            return np.empty(0, dtype=np.float32)
        while True:
            try:
                blocks.append(self._q.get_nowait())
            except queue.Empty:
                break
        audio = np.concatenate(blocks)
        return resample(audio, self._rate) if self._rate != SAMPLE_RATE else audio

    def __enter__(self) -> "Recorder":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
