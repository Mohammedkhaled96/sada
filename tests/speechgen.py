"""Make test speech with the voices Windows already has.

Mixed-language dictation cannot be tested from the one Arabic recording in the
repository, and committing audio for every language would put megabytes of
binaries in git for something the operating system can produce on demand.

SAPI voices are not people - they are clearer and flatter than real speech - so
these files prove that the language *boundaries* are found and that each side
is written in its own script. They do not say anything about accuracy on real
voices.
"""
from __future__ import annotations

import contextlib
import os
import tempfile
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000


def voices() -> list[str]:
    try:
        import win32com.client

        speaker = win32com.client.Dispatch("SAPI.SpVoice")
        return [v.GetDescription() for v in speaker.GetVoices()]
    except Exception:
        return []


def pick(*hints: str) -> int | None:
    """The first voice whose description mentions any of these words."""
    for index, description in enumerate(voices()):
        low = description.lower()
        if any(hint.lower() in low for hint in hints):
            return index
    return None


@contextlib.contextmanager
def _in_temp_dir():
    """Speak from a scratch directory, not from the project.

    The Arabic voice writes a pronunciation dictionary - Leila22k_HQ.dic -
    into whatever the working directory happens to be, the first time it is
    asked to say anything. Left alone it lands in the repository root, where
    it is nothing to do with this program and has to be explained to anybody
    who finds it. The voice can have it; it just cannot have it here.
    """
    here = os.getcwd()
    with tempfile.TemporaryDirectory(prefix="sada-speechgen-") as scratch:
        os.chdir(scratch)
        try:
            yield Path(scratch)
        finally:
            os.chdir(here)


def say(text: str, voice_index: int | None = None, rate: int = 0) -> np.ndarray:
    """One utterance, as 16 kHz mono float32."""
    import win32com.client

    speaker = win32com.client.Dispatch("SAPI.SpVoice")
    if voice_index is not None:
        speaker.Voice = speaker.GetVoices().Item(voice_index)
    speaker.Rate = rate

    path = Path(tempfile.gettempdir()) / f"sada-speechgen-{abs(hash(text))}.wav"
    with _in_temp_dir():
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
        fmt.Type = 22                               # 16 kHz, 16 bit, mono
        stream.Format = fmt
        stream.Open(str(path), 3)                   # SSFMCreateForWrite
        speaker.AudioOutputStream = stream
        speaker.Speak(text)
        stream.Close()

    with wave.open(str(path), "rb") as wav:
        raw = wav.readframes(wav.getnframes())
        rate_hz = wav.getframerate()
    path.unlink(missing_ok=True)

    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if rate_hz != SAMPLE_RATE:
        from engine.mic import resample

        audio = resample(audio, rate_hz, SAMPLE_RATE)
    return audio


def silence(seconds: float) -> np.ndarray:
    # Not digital zero: a real pause has room tone in it, and Silero is more
    # honest about a pause than about an impossible absence of sound.
    n = int(seconds * SAMPLE_RATE)
    return (np.random.default_rng(0).normal(0, 0.0008, n)).astype(np.float32)


def conversation(parts: list[tuple[str, int | None]], gap: float = 0.9):
    """Utterances separated by pauses long enough for the segmenter to cut."""
    pieces = [silence(0.4)]
    for text, voice in parts:
        pieces.append(say(text, voice))
        pieces.append(silence(gap))
    return np.concatenate(pieces).astype(np.float32)
