# Accuracy notes

Why the engine is configured the way it is. Every setting here exists because
the default caused a visible failure on long files.

## Hallucination on long audio

Whisper invents text when it meets silence, music or noise, and once it starts
it feeds its own invention back in as context and keeps going. On a 3-hour file
this is the difference between a usable transcript and garbage.

| Setting | Value | Why |
|---|---|---|
| `condition_on_previous_text` | `False` | The single most important one. Stops a bad segment from poisoning everything after it. Costs a little coherence across sentence boundaries; worth it. |
| `vad_filter` | `True` | Silero VAD removes silence *before* the model sees it, so there is nothing to hallucinate over. |
| `hallucination_silence_threshold` | `2.0` | Skips a segment when the model emits text across a long silence. |
| `compression_ratio_threshold` | `2.4` | Rejects a segment whose text compresses too well — the signature of "thanks for watching thanks for watching…". |
| `no_speech_threshold` | `0.6` | Drops segments the model itself thinks are not speech. |
| `log_prob_threshold` | `-1.0` | Triggers the temperature fallback on low-confidence output. |
| `temperature` | `[0.0 … 1.0]` | On rejection, retry hotter instead of emitting junk. |
| `repetition_penalty` | `1.05` | Mild; higher values start mangling legitimately repetitive speech (Quran recitation, lists, names). |

A post-pass in `textout.py` also drops known closing-credit hallucinations and
collapses stuttered repeats, because no decoding setting catches all of them.

## Language detection

`detect_language` only looks at the opening 30 seconds by default. That misreads
any file starting with music, a bismillah, silence, or an English greeting on an
otherwise Arabic recording.

`langdetect.py` instead samples 5 windows spread across the middle 90% of the
file, sums the confidence per language, and picks the winner. The result is then
**locked** and passed explicitly to `transcribe()`, so the language cannot flap
mid-file — a known failure where Whisper switches to transliterating Arabic in
Latin script halfway through.

Open question for Phase 1: Arabic/English code-switching. Current plan is to
re-detect per chunk when `avg_logprob` is low, which needs explicit chunking
rather than the built-in VAD loop.

## Chunking and resume

`vad_filter=True` makes faster-whisper chunk on speech boundaries internally,
so words are not cut in half the way fixed 30-second windows cut them.

Every segment is appended to `partial.jsonl` and `fsync`ed immediately. A crash
at minute 150 of a 180-minute file loses at most one segment, and `--resume`
restarts from the last checkpoint using `clip_timestamps`. `load_checkpoint`
stops at the first unparseable line, which is how a half-written final record
from a hard power cut is tolerated.

## Paragraphs

Raw Whisper output is one short segment per utterance and reads like a wall of
fragments. `build_paragraphs` starts a new paragraph on a pause of 1.5s or more,
and force-breaks past 700 characters but only at a sentence end.

## Encoding

The Windows console defaults to cp1252 here and dies on Arabic. The engine
always writes files as UTF-8 explicitly, and the CLI needs `PYTHONUTF8=1` (or
`PYTHONIOENCODING=utf-8`) to print Arabic. The daemon in Phase 2 must set this
in its own environment rather than inheriting it.

## Model sizes, measured

Downloads, not RAM:

| Format | large-v3-turbo | large-v3 |
|---|---|---|
| OpenAI `.pt` (openaipublic CDN) | 1.62 GB | 3.09 GB |
| CTranslate2 float16 | ~1.6 GB | ~3.1 GB |
| CTranslate2 int8 | ~800 MB | ~1.5 GB |
| whisper.cpp GGML q5_0 | ~574 MB | ~1.1 GB |

The 574 MB figure quoted early in planning is the whisper.cpp number, not the
CTranslate2 one. For shipping, convert once to CT2 int8 (~800 MB) and
distribute that, rather than making every user download float16 and quantize.

## Model hosting

`huggingface.co` is TLS-blocked on the development network, and
`hf-mirror.com` just redirects back to it. Reachable: pypi.org, github.com,
objects.githubusercontent.com, kaggle.com, and `openaipublic.azureedge.net`.

So the installer must **not** depend on huggingface.co. Ship the converted
int8 model as a GitHub Release asset (free, CDN-backed, 2 GB per-file limit)
with HF as a secondary fallback only.

## PyAV incompatibility (found in Phase 0)

`faster_whisper.decode_audio` calls `av.open(..., metadata_errors="ignore")`,
and PyAV 19 removed that argument, so the call raises
`TypeError: open() got an unexpected keyword argument 'metadata_errors'`
with faster-whisper 1.2.1 + av 19.0.1.

Rather than pin PyAV and have it break again on the next release, `engine/audio.py`
decodes by piping ffmpeg to `s16le` and converting to float32 ourselves. The
installer bundles ffmpeg anyway, ffmpeg reads more containers than PyAV
(including video files), and `CREATE_NO_WINDOW` keeps a console from flashing
when the daemon calls it.

Verified decoding: mp3 34.2s, m4a 510.6s, m4a 1076.4s — all float32 mono 16 kHz
with sane peak levels.

## Unblocked model path (what we actually use)

With `huggingface.co` blocked, the working chain is:

| Need | Source | Status |
|---|---|---|
| Python packages | pypi.org | reachable |
| torch (CPU-only) | download.pytorch.org/whl/cpu | reachable |
| Whisper `.pt` weights | openaipublic.azureedge.net | reachable |
| tokenizer / vocabulary | bundled inside the `openai-whisper` wheel | no network |
| whisper.cpp converter | raw.githubusercontent.com | reachable |

`openai-whisper` needs nothing from HF: it reads OpenAI's own `.pt` checkpoints
and carries its tiktoken vocabulary inside the package. `engine/backends.py`
therefore exposes both engines behind one interface, and `--backend auto`
prefers faster-whisper, falling back to openai-whisper when the CTranslate2
weights are absent.

Measured: `tiny` fetched from the OpenAI CDN, Arabic detected at 98%
confidence, 34s file transcribed in 8s (4.0x realtime) on 10 CPU threads, all
three outputs written as UTF-8 with the RTL flag set. Accuracy at `tiny` is
poor, as expected at 39M parameters — `بسم الله رحمة رحمة` for
`بسم الله الرحمن الرحيم` — which is what `large-v3-turbo` is for.

For shipping, whisper.cpp remains attractive: GGML embeds the vocabulary, so a
converted model has no HF dependency ever, and q5_0 is the smallest download at
~574 MB. whisper.cpp stopped publishing prebuilt binaries as release assets
(v1.9.4 has none), so that route needs compiling from source.

## Measured: beam search buys nothing here

On 34s of clear Arabic speech with `large-v3-turbo`, four decoding
configurations produced **byte-identical transcripts**:

| Config | Time | Realtime | Relative speed |
|---|---|---|---|
| beam_size=5, best_of=5 | 96.9s | 0.35x | 1.00x |
| beam_size=2, best_of=2 | 89.1s | 0.38x | 1.09x |
| greedy + temperature fallback | 74.6s | 0.46x | **1.30x** |
| greedy, no fallback | 62.3s | 0.55x | 1.56x |

So the default is now **greedy with the temperature fallback kept**. The
fallback is the anti-hallucination net and only fires on a rejected segment, so
it costs little; dropping it for another 1.2x is not worth reintroducing
hallucination risk on long files. Re-run `tools/bench.py` on messier audio
(overlapping speakers, noise) before assuming this holds everywhere — beam
search earns its cost when the audio is ambiguous.

## Measured: things that did *not* help

**torch dynamic int8 quantization** (`quantize_dynamic` over `nn.Linear`):
0.93x — very slightly *slower* than fp32, with identical text. Not worth it.

**whisper.cpp f16 via pywhispercpp**: 0.68x realtime, barely above the torch
path, because f16 GGML is not quantized. It also defaulted to English and
returned nonsense until the language was passed explicitly — whisper.cpp does
not auto-detect unless asked. Would need q5_0 quantization to be worth using,
and `pywhispercpp` ships no quantizer (no binaries in the package at all).

The `.pt` → GGML conversion itself **works offline**, which is worth keeping:
`tools/convert-pt-to-ggml.py` with the `openai-whisper` package as the asset
directory produced a valid 1.6 GB f16 GGML file using only locally installed
files. GGML embeds the vocabulary, so that route never needs HF.

## The remaining speed lever

CPU ceiling measured on 10 threads, `large-v3-turbo`:

| Engine | Realtime factor |
|---|---|
| openai-whisper fp32, beam5 | 0.35x |
| openai-whisper fp32, greedy | 0.65x |
| whisper.cpp f16 | 0.68x |
| **CTranslate2 int8 (expected)** | **3–5x** |

CTranslate2 int8 is the real win and the reason faster-whisper was the original
choice. It needs `tokenizer.json`, which normally comes from HF. Building one
offline from the `multilingual.tiktoken` that ships inside `openai-whisper` is
possible — recover the BPE merges from the tiktoken ranks — and is verifiable by
asserting token-for-token agreement with openai-whisper's own tokenizer over a
large sample. That is the next optimisation, not a blocker: at 0.65x the tool
already works, it is just slower than it should be.

## Bug: the transcript was inserted twice

Reported after the first real use: transcribing a file put the whole transcript
into the document twice. The checkpoint held 32 segments where there should have
been 16, with every `(start, end)` pair appearing exactly twice.

Three faults lined up:

1. `app/main.py` passed `resume=True` on **every** run, not only after an
   interruption.
2. `run_transcription` kept the existing checkpoint when resuming and opened it
   in **append** mode.
3. It set `clip_timestamps` to skip the finished part — but
   `OpenAIWhisperBackend.DROP` contains `clip_timestamps`, because the reference
   implementation has no such option, so it was **silently discarded**. Whisper
   re-transcribed the whole file and the new segments were appended to the old.

The underlying mistake is a backend silently dropping an option that affects
**correctness** rather than quality. Fixes:

- Backends now declare `supports_resume`; `openai-whisper` sets it to `False`
  and `run_transcription` refuses to resume onto a backend that cannot honour
  the request, rather than quietly doing the wrong thing.
- A **completed run deletes its checkpoint**, so "a checkpoint exists" now means
  "the last run was interrupted" — the only state resuming should act on.
- A checkpoint that already reaches the end of the audio is treated as finished
  work, not resumable work.
- `load_checkpoint` de-duplicates by `(start, end, text)` and sorts, as a safety
  net. Silently doubling someone's transcript is far worse than losing a segment.

Covered by 18 checks in `tests/test_resume.py`, including running the same file
four times in a row and asserting the text does not grow.

## The progress window was reporting nonsense

Reported on an 18-minute file: *"Progress: 0 percent, Time remaining: less than a
second, Speed: 0.0 times real time"*.

Two separate faults:

**It had nothing to report.** `openai-whisper`'s `transcribe()` blocks until the
whole file is finished and only then returns every segment, so handing it a
complete file produced exactly **one** progress update — at position zero, before
any work — and then silence until the end. The job looked stalled at 0% for the
entire run.

Fixed by `engine/chunking.py`: Silero VAD finds the speech, chunks are grown to
about 90 seconds and closed at the next pause (never at a fixed interval, so a
word is not cut in half), and `OpenAIWhisperBackend.stream_segments` transcribes
them one at a time, yielding as it goes. Measured: the 18-minute file goes from
**1 progress report to 11**.

A useful side effect: because we control the chunks, we can skip the ones a
previous run finished — so `openai-whisper` now genuinely supports resume, which
is why `supports_resume` flipped to `True`.

**It extrapolated from nothing.** The rate was computed on the very first
callback, where `elapsed` and `done` are both near zero, which produced `0.0x`
and an ETA that rounded to "less than a second". `Progress` now carries a
`measured` flag, set only once at least 2 seconds of wall time and 1 second of
audio are behind it, and the UI says "still measuring" until then. Guessing
quietly was worse than admitting ignorance — especially read aloud, where "less
than a second remaining" on an 18-minute file is actively misleading.

## Cancelling blocked the next job

`start_transcription` refused while the worker thread was alive, and a cancel can
take a while to unwind inside a model call — so cancelling appeared to lock the
program.

`app/jobs.py` replaces the single worker with a queue. Submitting always
succeeds; a cancelled job keeps unwinding in the background while the next one
waits its turn. Jobs still run **one at a time on purpose**: transcription
saturates the CPU and holds the model's working set, so two at once makes both
slower and risks exhausting RAM. Every job is listed with its own state, percent,
language and estimate, in a report-mode `ListCtrl` that NVDA and JAWS navigate
row by row.

## Models that were not there

Settings offered four models while only one was on disk. Choosing another started
a multi-gigabyte download inside the model-loading thread: no progress, no size,
the window frozen, and on failure a **zero-byte file** left behind that then
looked installed. The user's report was simply "the model will not load".

`app/models.py` makes it explicit. The list shows every model with its size and
whether it is installed, incomplete or absent. Downloading is a button with a
progress bar that can be stopped. The file is written to `.pt.part` and renamed
into place **only after the SHA-256 matches the one in the URL**, so a failed or
cancelled download leaves nothing that can be mistaken for a working model, and
`cleanup_partials()` removes the zero-byte files the old path created.

Sizes, from the CDN's `Content-Length`:

| Checkpoint | Download |
|---|---|
| small | 461 MB |
| medium | 1.4 GB |
| large-v3-turbo | 1.5 GB |
| large-v3 | 2.9 GB |
