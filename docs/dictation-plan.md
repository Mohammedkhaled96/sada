# Dictation: what PixelBoard is, what is measurable, and what we build

The request: in Word, press `Ctrl+Shift+D`, hear "start typing", speak, press the
same key, and find the text already written at the cursor — with the quality of
Gboard's "Rambler" voice typing as shown in
[PixelBoard](https://github.com/Akshayykadam/PixelBoard).

This document is the study that has to come before the code, because the last
attempt at this feature was reverted in full. Every number below was measured on
this machine, not estimated.

## 1. What PixelBoard actually contains

It contains no speech recognition. That is not a judgement of the project — it
is a patch set for an Android keyboard, and the engine was never its subject.

Scanned every blob in the repository (282 files, 140 `.java`, 70 `.kt`):

| Looked for | Found |
|---|---|
| `AudioRecord`, `MediaRecorder` | 0 |
| mel / FFT / any signal processing | 0 |
| `.tflite`, `.onnx`, any model weights | 0 |
| `SpeechRecognizer` (the Android system service) | 19 references |

The whole project is four patches against Gboard: *AI Writing Tools*, *Gboard
Signature Bypass*, *Advanced Voice Typing*, *Package Rename*. They flip Google's
internal feature flags (`enable_jetson`, `enable_hide_rambler_al_idling_state`,
…) and rename the package so the result installs beside the stock keyboard.

**Rambler itself runs on Google's servers**, reached through Gboard and Play
Services, on Android. There is no API, no offline mode, and nothing to port to
Windows.

### The most useful thing in the repository is a warning

`GboardRamblerDictationRuntime.java` exists to *undo* what Google's model does.
Its own comment:

> the backend server may classify the user's intent as an agentic command (e.g.
> INTENT_COMPOSE = 4, INTENT_REWRITE = 3) and generate an AI answer, placing the
> answer into `aajg.c` and the user's verbatim speech transcript into `aajf.d`

So Rambler sometimes **answers you instead of transcribing you**, and PixelBoard
reflects into the proto to force the verbatim transcript back and reset the
intent to dictation.

That is exactly the failure this project already hit, when the model rewrote and
transliterated instead of transcribing. Google, with a far larger model, got it
wrong in the same direction and had to be patched back to verbatim. It is the
strongest argument available for the rule below: **we transcribe what was said.
We do not improve it.**

## 2. What transfers, and what does not

| Rambler feature | Us | How |
|---|---|---|
| Automatic punctuation and capitalisation | **Yes** | Whisper emits both natively |
| Mixing languages in one sentence | **Yes** | `engine/langmap.py` — the segment is split at the language boundary *before* it is transcribed |
| Names and jargon spelled right | **Yes** | `hotwords` as `initial_prompt`, plus the end of what has already been written |
| A personal dictionary | **Yes, better** | A list of replacements the user writes, applied literally — nothing is inferred |
| Filler removal ("أمم", "um") | **Carefully** | Exact words on a short list, never repetition regexes — §5 |
| Stutters and repeated phrases | **Yes** | `engine/cleanup.py`, and only where the decoder, not the speaker, repeated itself |
| Single undo of the whole insertion | **Yes, already** | `wordio.insert_transcript` |
| Screen reader labels and focus | **Yes, better** | Native Win32 + the NVDA/JAWS APIs we already use |
| Releasing the engine when idle | **Yes** | PixelBoard issue #3 was exactly this |
| Rewriting self-corrections ("two — no, three") | **No, deliberately** | Needs an LLM rewriting your words; see §1 |
| Server-side AI intent, tone switching, proofreading | **No** | Offline is the product |

### Why mixing languages needs a split, not a better guess

`language` is not a hint to Whisper. It decides once per call and writes the
whole call in that script, so a sentence that changes language comes back
transliterated — "the quarterly report" in Arabic letters. Detecting the
language more cleverly cannot fix that, because there is only one answer per
call to be had.

So `engine/langmap.py` asks Silero where the phrases are, asks `tiny` what
language each phrase is in, joins neighbouring phrases that agree into runs,
and transcribes each run as its own call with its own language. Boundaries land
in the pauses, never inside a phrase. Runs too short to be worth a window fold
into the longer side, and a segment is capped at four calls however mixed it
is.

It costs nothing in the ordinary case: one language means one run, which is one
call, which is what happened before.

## 3. Measured on this machine

i7-1255U, 12 logical cores, 15.7 GB RAM, **no NVIDIA GPU** — `torch 2.14.1+cpu`.
Intel UHD only, so CUDA is not an option here. `large-v3-turbo`, 5 s of Arabic
speech unless stated.

### 3a. Cost per call is almost all fixed

Whisper pads every clip to a 30-second window, so the length of the clip barely
matters:

| Clip | Wall |
|---|---|
| 2 s | 17.9 s |
| 5 s | 18.5 s |
| 10 s | 21.0 s |
| 20 s | 23.3 s |

A fixed floor of ≈17.5 s, plus ≈0.3 s per second of speech. **This is why the
previous attempt felt unusable, and no amount of restructuring fixes it.**

### 3b. Three levers, all measured

| Configuration | Wall | Text |
|---|---|---|
| 30 s window, language auto-detected | 18.5 s | correct |
| 30 s window, language passed in | 9.3 s | identical |
| 15 s window, language passed in | 4.2 s | identical |
| **8 s window, language passed in** | **2.7 s** | **identical** |
| 6 s window, language passed in | 3.1 s | ✗ repeats itself |

**Lever 1 — don't make the model detect the language (2.0x).** Auto-detection is
a second full encoder pass. Run detection separately on `tiny`, then pass the
answer in.

**Lever 2 — encode a shorter window (3.4x).** Whisper's positional embedding is
`sinusoids(n_ctx, n_state)` — fixed, not learned — so using only the first *n*
frames of it is well defined. A 5-second phrase does not need a 30-second
window. Text came back byte-identical down to 8 s.

**Lever 3 — the floor.** At 6 s the window has no headroom over the 5 s clip and
the decoder runs on into repetition. So: `window = speech + 3 s`, minimum 8 s,
maximum 30 s.

Together: **18.5 s → 2.7 s, a 6.8x speed-up with the same model and the same
text.** This is what makes the feature possible without a second model, which is
what was asked for.

### 3c. For comparison, smaller models

`tiny` measured, `small` and `medium` predicted by fitting the two measured
points against encoder size (layers × width², which `tiny` vs turbo confirms):

| Model | Fixed cost per 30 s call | Note |
|---|---|---|
| `tiny` | **0.65 s** (measured) | Accuracy too poor for text; ideal for language detection |
| `small` | ≈2.8 s (predicted) | Fallback if the window trick disappoints on real speech |
| `medium` | ≈8.9 s (predicted) | No reason to use it |
| `large-v3-turbo` | 17.9 s (measured) | 2.7 s with §3b applied |

So the plan needs **no new model download** for text. `tiny` (75 MB) is wanted
for language detection, and is already downloaded.

### 3d. What this means for the user's question

> press stop and find the text in front of me immediately

Honest answer: **about one second after a short phrase, and about three seconds
after a long one** — not zero. The design in §4 does the work while you are
still talking, so at `stop` only the last phrase is outstanding.

## 4. Architecture

```
 microphone ──► ring buffer ──► VAD segmenter ──► window queue ──► transcriber
   16 kHz        in memory        Silero            closed            turbo
   mono          never blocks     pauses            segments        short window
                                                                        │
  Ctrl+Shift+D (again) ──► flush ──► wait for queue ──► clean ──► Word ─┘
```

**Cut at pauses, transcribe as you speak.** A window closes when a pause ≥700 ms
follows ≥4 s of speech, or when 15 s of speech has accumulated. It is handed to
the transcriber immediately, which runs ≈3.5x faster than you talk (0.28x
realtime marginal), so it never falls behind. Short phrases are *not* sent one
at a time: ten 2-second phrases sent separately cost 27 s of compute for 20 s of
speech, which does fall behind.

**Insert once, at stop.** Not progressively. `docs/accessibility.md` already
settled this: moving the insertion point while someone is reading yanks the
screen reader cursor away mid-sentence. Work live, insert once.

**Insert over COM, never synthetic keystrokes.** `wordio.insert_transcript`
already does the RTL reading order and the single undo. It also means dictation
physically cannot type into a password box or any other application.

**One engine at a time.** The window length is process-wide state in the
`whisper` package, so file transcription (30 s windows) and dictation (8-15 s
windows) must not run concurrently. A lock around the backend, and a context
manager that sets the window and restores it.

**Language.** `tiny` detects per closed window, 0.65 s, and the answer is passed
to the big model. Per window, not per session — forcing one language for the
whole dictation is what previously wrote English sentences in Arabic letters.

**Release when idle.** Microphone closed the moment dictation stops; dictation
model released after a few minutes of disuse. PixelBoard issue #3 was the same
mistake in the other direction.

## 5. Text cleanup — the narrow version

Whisper already punctuates and capitalises. What is left:

1. **Trailing-silence hallucinations.** With a tight window the decoder can run
   on. Guards: a segment whose compression ratio or `no_speech_prob` is out of
   range, or which begins after the audio ended, is thrown away.
2. **Fillers.** Exact, whole-word matches from a short per-language list.
   **No repetition regexes.** The last attempt's `[اوي]{3,}` deleted the word
   **راوي**, and `^m{2,}$` ate the "mm" in "5 mm". More than half of the filler
   tests assert what survives.
3. **Repetition the decoder produced.** A word three or more times in a row
   leaves one; a phrase of two or more words repeated immediately after itself
   is collapsed. Twice is left alone, because "very very", "لا لا" and "no no"
   are things people say.
4. **The user's own replacements.** `wrong = right`, one per line, whole words,
   applied literally to the finished text. A speech model mishears a particular
   name the same way every time, and the person dictating is the one who knows
   which way.
5. **Nothing else.** No rephrasing, no self-correction, no tone. §1.

The line is between *tidying what the model emitted* and *deciding what the
speaker meant*. Everything above is the first. The second needs a language
model, and PixelBoard's own source is the argument against letting one near
this: Google's Rambler does it, and sometimes answers the user instead of
transcribing them.

## 6. The verification the user asked for

A **Dictation check** in Settings, keyboard-reachable, spoken step by step, and
run automatically the first time dictation is used. It must *measure*, not read
settings: on this machine all three Windows microphone-consent keys say `Allow`
and two capture devices report OK — and the last attempt still recorded digital
silence, peak 0.0000, with the program silently deaf.

| # | Check | On failure, say |
|---|---|---|
| 1 | An input device exists; name the default | which devices were found |
| 2 | `…\ConsentStore\microphone` = Allow, in HKCU, HKCU\NonPackaged and HKLM | the exact Settings page to open |
| 3 | The stream opens at 16 kHz mono | the error — in use, or unsupported rate |
| 4 | **Record 3 s and measure** peak, RMS, DC offset, clipping | silent → wrong device or muted; low → raise input volume; clipping → lower it |
| 5 | Silero VAD finds speech in it | heard sound but no voice |
| 6 | **Transcribe it and show the words back** | the end-to-end proof |
| 7 | Time step 6 and report it | "a short dictation takes about N seconds on this computer" |
| 8 | `RegisterHotKey` accepts the shortcut | which program is likely holding it |
| 9 | Word is running, a document is open | how to open one |
| 10 | Model installed, RAM free | what is missing |

Step 7 matters as much as the rest: it sets the expectation honestly, computed
on the user's own hardware rather than promised in a README.

## 7. Known conflict

`Ctrl+Shift+D` is **Word's own shortcut for double underline**. A global hotkey
takes it before Word sees it, so while Sada runs, double underline is gone. The
user asked for this combination specifically, so it is the default — but it is
configurable like every other shortcut, and the Dictation check reports it.

## 8. A bug found while studying this

`engine/backends.py` lists `hotwords` in `OpenAIWhisperBackend.DROP`, and
nothing maps it to `initial_prompt` — which is the openai-whisper equivalent.
faster-whisper's CTranslate2 weights are unreachable on this network, so the
openai backend is the one that ships. **The "Names and terms" setting therefore
does nothing in every shipped release.** It is a two-line fix, it is worth more
to dictation than any cleanup rule, and it is tested separately from this work.

## 9. What stage 0 found, and what it changed

The gate in §3b nearly did not hold. Four things turned up that the
five-second benchmark had not shown, each of which would have shipped a
feature that works in a demonstration and not in use. All four are now in
`tests/test_window.py` and `tests/test_dictation.py`.

**Headroom has to grow with the content.** 5 s of speech was right in an 8 s
window. 11 s in a 14 s window appended a repeat of the opening phrase. So the
room is proportional, and the ceiling on a segment — 10 s — is where the
measurement put it rather than where the arithmetic did.

**The full window is not ground truth.** At 10 s it stopped at its own boundary
and dropped the word after it, which the short window caught. The test
therefore asserts that nothing is *lost* and nothing is *repeated*, not that
the two agree.

**A trailing hallucination passes every quality check.** A 25-second clip
produced `ترجمة نانسي قنقر` — a subtitle translator's credit — with an
unremarkable 0.77 compression ratio and 0.00 no-speech probability. What gave
it away was the timestamp: it began after the audio ended. That is its own
check now.

**A cut in the middle of a word is the most expensive thing that can happen.**
Whisper answers a segment it cannot make sense of by retrying at six rising
temperatures, and no amount of retrying puts back half a word: one 10-second
segment took **56.6 s** instead of 7.0 s. Two changes together — the retries
stop at two, and a cut forced by the length limit looks for the quietest
instant nearby instead of landing exactly on it.

Measured end to end over 20 seconds of continuous Arabic, through the real
pipeline rather than a benchmark:

| | Compute for 20 s of speech |
|---|---|
| Asking the large model for the language, per segment | 38.8 s |
| `tiny` answering that instead | 21.1 s |
| Capping segments by audio length, not speech length | 29.4 s ✗ |
| Two temperatures, and cutting at a quiet point | **13.0 s** |

13.0 s of work for 20.0 s of speech is 0.65x realtime: the engine stays ahead
of the speaker with a third to spare, so pressing stop leaves only the last
phrase outstanding. A phrase that ends at a pause — the normal case — is a 4-6
second segment in an 8-second window, which measures **2.8 s**.

## 10. Stages

| # | Stage | State |
|---|---|---|
| 0 | Confirm §3b on real speech | done — §9 |
| 1 | `engine/mic.py`: devices, levels, resampling | done |
| 2 | `engine/window.py`: short windows, warm-up, the backend lock | done |
| 3 | `engine/dictation.py`: segmenter, queue, assembly | done |
| 4 | `app/dictate.py`: hotkey, spoken states, tones, Word insertion | done |
| 5 | `app/diagnose.py` and the check dialog | done |
| 6 | `engine/fillers.py` and the survivors corpus | done |
| 7 | Settings, i18n, PortAudio in the spec, help text | done |

Stages 1-3 are tested without a GUI and without a microphone, from recorded
files — which is how this was verified rather than demonstrated.

## 11. What we are not doing, and why

- **No cloud engine.** Offline is the product. Gemini was tried: no keyless
  access, rate limits on the key rather than the model, and 16 s of Arabic
  dropped from a 26 s request.
- **No rewriting of what was said.** §1.
- **No progressive insertion into Word.** `docs/accessibility.md`.
- **No second text model** unless stage 0 fails, in which case `small`.

## 12. The larger prize, out of scope here

CTranslate2 int8 is measured in `docs/accuracy-notes.md` at 3-5x realtime
against the 0.65x we get today, and it would speed up **file transcription**,
not just dictation. It needs a `tokenizer.json`, which normally comes from
huggingface.co and is TLS-blocked here; `tokenizers` is already installed and
the vocabulary ships inside `openai-whisper`, so building one offline is
plausible. A separate piece of work, with its own measurements.
