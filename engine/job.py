"""The transcription job itself, shared by the CLI and the daemon.

Both callers need the same thing — decode, detect the language, stream segments,
checkpoint each one, build paragraphs, write the outputs — so it lives here once
instead of being duplicated with two slightly different sets of bugs.

Progress is reported through a callback and cancellation through an Event, so the
caller decides how to present it: the CLI prints lines, the daemon updates state
that Word polls.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from engine.audio import SAMPLE_RATE, decode_audio
from engine.langdetect import detect_language
from engine.textout import build_paragraphs, is_rtl, write_outputs

# These specific values are what keeps Whisper from hallucinating on long
# files - see docs/accuracy-notes.md before changing any of them.
DECODE_OPTS = dict(
    # Greedy, not beam search. Measured on Arabic speech: beam_size=5 and
    # beam_size=1 produced byte-identical transcripts while beam search cost
    # 1.30x the time. The temperature fallback below is kept - that is the
    # anti-hallucination safety net, and it is cheap because it only fires on a
    # rejected segment. See tools/bench.py.
    beam_size=1,
    best_of=1,
    temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
    condition_on_previous_text=False,     # the single most important setting
    compression_ratio_threshold=2.4,
    log_prob_threshold=-1.0,
    no_speech_threshold=0.6,
    hallucination_silence_threshold=2.0,
    repetition_penalty=1.05,
    no_repeat_ngram_size=0,
    word_timestamps=True,
    vad_filter=True,
    vad_parameters=dict(
        threshold=0.5,
        min_speech_duration_ms=250,
        max_speech_duration_s=30,
        min_silence_duration_ms=500,
        speech_pad_ms=200,
    ),
)


@dataclass
class Progress:
    """A snapshot the caller can render or serve. Times are audio seconds.

    `measured` says whether `realtime_factor` and `eta_seconds` mean anything
    yet. Before the first chunk finishes there is nothing to extrapolate from,
    and reporting "0.0x real time, less than a second remaining" on a long file
    is worse than admitting we do not know.
    """
    state: str = "queued"            # queued|decoding|detecting|transcribing|writing|done|error|cancelled
    position: float = 0.0
    duration: float = 0.0
    language: str = ""
    language_confidence: float = 0.0
    realtime_factor: float = 0.0
    eta_seconds: float = 0.0
    measured: bool = False
    segments: int = 0
    message: str = ""

    @property
    def percent(self) -> float:
        return (self.position / self.duration * 100) if self.duration else 0.0


class Cancelled(Exception):
    pass


def load_checkpoint(ckpt: Path) -> tuple[list[dict], float]:
    """Return (segments, resume_from). Tolerates a half-written final line,
    which is what a hard power cut leaves behind.

    Duplicate spans are dropped. That should never happen now that a completed
    run deletes its checkpoint, but it was once possible to append a second full
    pass onto a finished one and emit the whole transcript twice, so the guard
    stays: silently doubling someone's transcript is far worse than losing a
    segment.
    """
    if not ckpt.exists():
        return [], 0.0
    segments: list[dict] = []
    seen: set[tuple] = set()
    for line in ckpt.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            break                      # truncated by a crash; stop here
        key = (round(row.get("start", 0), 2), round(row.get("end", 0), 2),
               row.get("text", ""))
        if key in seen:
            continue
        seen.add(key)
        segments.append(row)
    segments.sort(key=lambda r: (r.get("start", 0), r.get("end", 0)))
    return segments, (segments[-1]["end"] if segments else 0.0)


def run_transcription(
    audio_path: str | Path,
    backend,
    out_dir: str | Path,
    *,
    language: str | None = None,
    timestamps: bool = False,
    hotwords: str | None = None,
    resume: bool = False,
    on_progress=None,
    cancel: threading.Event | None = None,
) -> dict:
    """Transcribe one file. Returns the metadata dict.

    Raises Cancelled when `cancel` is set, having kept the checkpoint so the
    work done so far is not lost.
    """
    src = Path(audio_path)
    if not src.exists():
        raise FileNotFoundError(src)

    job_dir = Path(out_dir) / src.stem
    job_dir.mkdir(parents=True, exist_ok=True)
    ckpt = job_dir / "partial.jsonl"

    prog = Progress()

    def report(**kw) -> None:
        for k, v in kw.items():
            setattr(prog, k, v)
        if on_progress:
            on_progress(prog)

    def check_cancel() -> None:
        if cancel is not None and cancel.is_set():
            report(state="cancelled", message="cancelled by the user")
            raise Cancelled()

    # ---------------------------------------------------------------- decode
    check_cancel()
    report(state="decoding", message="reading the audio file")
    audio = decode_audio(src, sampling_rate=SAMPLE_RATE)
    duration = len(audio) / SAMPLE_RATE
    report(duration=duration)

    # -------------------------------------------------------------- language
    check_cancel()
    if language:
        report(state="detecting", language=language, language_confidence=1.0)
    else:
        report(state="detecting", message="detecting the language")
        lang, conf, _votes = detect_language(backend, audio, log=lambda *a: None)
        language = lang
        report(language=lang, language_confidence=conf)

    # ------------------------------------------------------------ transcribe
    # Resuming is only safe when the backend can actually be told to skip the
    # part already done. openai-whisper has no clip_timestamps, so asking it to
    # resume made it re-transcribe the whole file and append a second copy of
    # the transcript onto the first. Never resume onto a backend that cannot
    # honour the request.
    can_resume = resume and bool(getattr(backend, "supports_resume", False))

    segments, resume_from = load_checkpoint(ckpt) if can_resume else ([], 0.0)

    # A checkpoint that already reaches the end is finished work, not
    # interrupted work; there is nothing to resume.
    if resume_from >= duration - 0.5:
        segments, resume_from = [], 0.0

    if not segments and ckpt.exists():
        ckpt.unlink()                  # start from a clean file, never append
    if segments:
        report(segments=len(segments), position=resume_from)

    opts = dict(DECODE_OPTS)
    if resume_from > 0:
        opts["clip_timestamps"] = [resume_from, duration]
    if hotwords:
        opts["hotwords"] = hotwords

    report(state="transcribing", message="transcribing")
    t0 = time.time()
    ck = ckpt.open("a", encoding="utf-8")
    try:
        for row in backend.stream_segments(audio, language, opts):
            segments.append(row)
            ck.write(json.dumps(row, ensure_ascii=False) + "\n")
            ck.flush()
            os.fsync(ck.fileno())          # survive a power cut, not just a crash

            elapsed = time.time() - t0
            done = row["end"] - resume_from
            # A rate needs both a little audio and a little time behind it;
            # extrapolating from the first fraction of a second produces the
            # nonsense the progress window used to show.
            measured = elapsed >= 2.0 and done >= 1.0
            speed = (done / elapsed) if (measured and elapsed > 0) else 0.0
            report(
                position=row["end"],
                segments=len(segments),
                measured=measured,
                realtime_factor=round(speed, 2) if measured else 0.0,
                eta_seconds=(round(max(0.0, duration - row["end"]) / speed, 1)
                             if speed > 0 else 0.0),
            )
            check_cancel()
    finally:
        ck.close()

    wall = time.time() - t0

    # ---------------------------------------------------------------- output
    report(state="writing", message="writing the transcript")
    paragraphs = build_paragraphs(segments)
    meta = {
        "source": str(src),
        "duration_seconds": round(duration, 2),
        "language": language,
        "language_confidence": round(prog.language_confidence, 4),
        "rtl": is_rtl(language),
        "model": getattr(backend, "label", "?"),
        "backend": getattr(backend, "name", "?"),
        "segments": len(segments),
        "paragraphs": len(paragraphs),
        "words": sum(len(s["text"].split()) for s in segments),
        "wall_seconds": round(wall, 1),
        "realtime_factor": round(duration / wall, 2) if wall else None,
        "output_dir": str(job_dir),
        "timestamps": timestamps,
    }
    written = write_outputs(job_dir, src.stem, segments, paragraphs, meta,
                            with_timestamps=timestamps)
    meta["files"] = [str(f) for f in written]
    meta["text"] = "\n\n".join(
        (f"[{int(p['start']) // 3600:02d}:{int(p['start']) % 3600 // 60:02d}:"
         f"{int(p['start']) % 60:02d}] {p['text']}") if timestamps else p["text"]
        for p in paragraphs
    )

    # The run finished, so the checkpoint is no longer unfinished work. Removing
    # it is what makes "a checkpoint exists" mean "the last run was interrupted",
    # which is the only state resuming should ever act on.
    try:
        ckpt.unlink(missing_ok=True)
    except OSError:
        pass                            # a leftover is harmless now that loading
                                        # de-duplicates and refuses a complete one

    report(state="done", message="finished", position=duration)
    return meta
