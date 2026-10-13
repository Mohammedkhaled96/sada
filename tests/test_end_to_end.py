"""The whole flow the user will actually perform, minus the keypress.

Loads the real model, transcribes a real file, and writes the result into a real
Word document — the same calls app/main.py makes when the hotkey fires. The file
dialog is the only step skipped, because it needs a human.

Writes to a NEW document and closes it without saving.

Run:  PYTHONUTF8=1 .venv/Scripts/python.exe tests/test_end_to_end.py [audio] [--model turbo]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, i18n, wordio
from app.speech import Speaker
from engine.backends import load_backend
from engine.device import pick_device
from engine.job import run_transcription

PASS, FAIL = "  ok  ", " FAIL "
failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""),
          flush=True)
    if not cond:
        failures.append(name)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio", nargs="?", default="E:/test.mp3")
    ap.add_argument("--model", default="turbo")
    args = ap.parse_args()

    audio = Path(args.audio)
    if not audio.exists():
        print(f"need an audio file; {audio} is not there")
        return 2

    settings = config.load()
    print(f"settings: model={settings.model} device={settings.device} "
          f"insert={settings.insert_mode} language={settings.language}")

    speaker = Speaker(settings.speak, settings.speak_via_sapi)
    print(f"screen reader channel: {speaker.describe()}")

    # ------------------------------------------------------------ load model
    dev = pick_device(settings.device)
    print(f"\nloading {args.model} on {dev.label} ...", flush=True)
    t0 = time.time()
    backend = load_backend("auto", args.model, dev)
    load_s = time.time() - t0
    check("model loads", backend is not None, f"{backend.label} in {load_s:.1f}s")

    # ------------------------------------------------------------ transcribe
    states: list[str] = []
    last = {}

    def on_progress(p):
        if p.state not in states:
            states.append(p.state)
        last["p"] = p

    print(f"\ntranscribing {audio.name} ...", flush=True)
    t0 = time.time()
    meta = run_transcription(
        audio, backend, ROOT / "out",
        language=settings.language_or_none,
        timestamps=settings.timestamps,
        hotwords=settings.hotwords or None,
        resume=False,
        on_progress=on_progress,
    )
    wall = time.time() - t0

    check("transcription completes", meta.get("segments", 0) > 0,
          f"{meta['segments']} segments, {meta['words']} words")
    check("passes through every state",
          states == ["decoding", "detecting", "transcribing", "writing", "done"],
          " -> ".join(states))
    check("detects the language without being told", bool(meta["language"]),
          f"{meta['language']} at {meta['language_confidence']:.0%}")
    check("marks arabic as right-to-left", meta["rtl"] is (meta["language"] == "ar"),
          f"rtl={meta['rtl']}")
    check("produces readable paragraphs", meta["paragraphs"] > 0,
          f"{meta['paragraphs']} paragraphs")
    check("writes the side files", len(meta.get("files", [])) == 3,
          ", ".join(Path(f).name for f in meta.get("files", [])))
    check("reports its speed", (meta.get("realtime_factor") or 0) > 0,
          f"{meta['realtime_factor']}x realtime, took {i18n.duration(wall)}")

    text = meta.get("text", "")
    check("transcript is not empty", len(text) > 20, f"{len(text)} chars")

    # --------------------------------------------------------------- into word
    print("\ninserting into Word ...", flush=True)
    wordio.init_thread()
    word = doc = None
    try:
        word = wordio.get_word(start_if_needed=True)
        word.Visible = False
        doc = word.Documents.Add()

        where = wordio.insert_transcript(
            word, text, rtl=meta["rtl"] and settings.rtl_auto,
            mode="cursor")
        body = doc.Content.Text

        first_words = text.split()[:3]
        check("the transcript reaches the document",
              all(w in body for w in first_words), " ".join(first_words))
        check("nothing is truncated", len(body) >= len(text) * 0.9,
              f"doc {len(body)} chars vs transcript {len(text)}")
        check("reading order was applied", where["reading_order_set"] is True)
        if meta["rtl"]:
            rng = doc.Range(where["start"], where["start"] + 5)
            check("arabic paragraphs read right-to-left",
                  int(rng.ParagraphFormat.ReadingOrder)
                  == wordio.WD_READING_ORDER_RTL)
        check("cursor waits at the start of the text for the reader",
              word.Selection.Range.Start == where["start"])

        doc.Undo()
        check("one undo removes the whole insertion",
              first_words[0] not in doc.Content.Text)
    except Exception as exc:
        check("word insertion", False, f"{type(exc).__name__}: {exc}")
    finally:
        try:
            if doc is not None:
                doc.Close(SaveChanges=0)
            if word is not None and word.Documents.Count == 0:
                word.Quit(SaveChanges=0)
        except Exception:
            pass
        doc = word = None
        import gc

        gc.collect()
        wordio.uninit_thread()

    # ------------------------------------------------------------ the summary
    print("\n" + "-" * 68)
    print("TRANSCRIPT")
    print("-" * 68)
    print(text[:1200] + ("…" if len(text) > 1200 else ""))
    print("-" * 68)
    print(f"language {meta['language']} ({meta['language_confidence']:.0%})  "
          f"words {meta['words']}  speed {meta['realtime_factor']}x  "
          f"model {meta['model']}")

    print("\n" + "=" * 60)
    if failures:
        print(f"FAILED: {len(failures)}")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("end-to-end flow works")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
