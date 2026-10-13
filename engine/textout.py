"""Turn raw Whisper segments into readable paragraphs and write the outputs.

Whisper emits one short segment per utterance, which reads like a wall of
fragments. We group them into paragraphs on natural pauses so the text that
lands in Word looks like something a person wrote.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

PARAGRAPH_GAP_S = 1.5        # a pause this long starts a new paragraph
PARAGRAPH_MAX_CHARS = 700    # never let a paragraph run longer than this
SENTENCE_END = tuple(".!?؟۔…")

RTL_LANGUAGES = {"ar", "he", "fa", "ur", "ps", "sd", "yi", "dv"}

# Whisper's classic closing-credits hallucinations in silence.
JUNK = [
    re.compile(r"^\s*(thanks for watching|thank you for watching)[.!]?\s*$", re.I),
    re.compile(r"^\s*(subscribe|please subscribe)[^.!?]{0,40}[.!]?\s*$", re.I),
    re.compile(r"^\s*ترجمة\s+نانسي\s+قنقر\s*$"),
    re.compile(r"^\s*اشترك\s+في\s+القناة\s*[.!]?\s*$"),
    re.compile(r"^[\s.,،؟?!-]*$"),
]


def is_rtl(language: str) -> bool:
    return (language or "").split("-")[0].lower() in RTL_LANGUAGES


def _is_junk(text: str) -> bool:
    return any(p.match(text) for p in JUNK)


def _collapse_repeats(text: str) -> str:
    """Whisper sometimes stutters a word or phrase; keep one copy."""
    text = re.sub(r"\b(\w+)(\s+\1\b){2,}", r"\1", text, flags=re.UNICODE)
    text = re.sub(r"(\b[\w\s]{4,40}?\b)(\s*\1){2,}", r"\1", text, flags=re.UNICODE)
    return re.sub(r"\s{2,}", " ", text).strip()


def build_paragraphs(segments: list[dict]) -> list[dict]:
    """Group segments into paragraphs. Each paragraph: {start, end, text}."""
    paragraphs: list[dict] = []
    cur: list[dict] = []

    def flush() -> None:
        if not cur:
            return
        text = _collapse_repeats(" ".join(s["text"] for s in cur))
        if text:
            paragraphs.append({"start": cur[0]["start"], "end": cur[-1]["end"],
                               "text": text})
        cur.clear()

    prev_end = None
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text or _is_junk(text):
            continue

        gap = seg["start"] - prev_end if prev_end is not None else 0.0
        length = sum(len(s["text"]) for s in cur)
        ends_sentence = bool(cur) and cur[-1]["text"].endswith(SENTENCE_END)

        if cur and (gap >= PARAGRAPH_GAP_S
                    or (length > PARAGRAPH_MAX_CHARS and ends_sentence)):
            flush()

        cur.append({**seg, "text": text})
        prev_end = seg["end"]

    flush()
    return paragraphs


def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _hms(t: float) -> str:
    t = int(t)
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


def write_outputs(job_dir: Path, stem: str, segments: list[dict],
                  paragraphs: list[dict], meta: dict,
                  with_timestamps: bool = False) -> list[Path]:
    """Write .txt, .srt and .json. Everything is UTF-8 so Arabic survives."""
    job_dir.mkdir(parents=True, exist_ok=True)
    written = []

    txt = job_dir / f"{stem}.txt"
    lines = []
    for p in paragraphs:
        lines.append(f"[{_hms(p['start'])}] {p['text']}" if with_timestamps
                     else p["text"])
    txt.write_text("\n\n".join(lines) + "\n", encoding="utf-8")
    written.append(txt)

    srt = job_dir / f"{stem}.srt"
    srt.write_text(
        "\n".join(
            f"{i}\n{_srt_time(s['start'])} --> {_srt_time(s['end'])}\n{s['text']}\n"
            for i, s in enumerate(
                (s for s in segments if s.get("text") and not _is_junk(s["text"])), 1)
        ),
        encoding="utf-8",
    )
    written.append(srt)

    js = job_dir / f"{stem}.json"
    js.write_text(
        json.dumps({"meta": {**meta, "rtl": is_rtl(meta.get("language", ""))},
                    "paragraphs": paragraphs, "segments": segments},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    written.append(js)

    return written
