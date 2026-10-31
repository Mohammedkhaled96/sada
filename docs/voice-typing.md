# Voice typing

Press `Ctrl+Shift+D` in Word, speak, press it again, and the words appear at
the cursor. "Voice typing" and "dictation" are the same feature; this is the
one document for it.

Everything runs on the machine it is installed on. Nothing is sent anywhere,
there is no key to obtain, no account, and no setting that would change that.

```
Ctrl+Shift+D ─► record ─► Whisper ─► tidy up ─► local model ─► Word
                (mic)     (models/)  (rules)    (nlm/, optional)
```

---

## 1. What this was modelled on, and what could not be taken from it

The feature was asked for after seeing Gboard's "Rambler" voice typing in
[PixelBoard](https://github.com/Akshayykadam/PixelBoard), so the first job was
to read that project. **It contains no speech recognition.** Every blob was
scanned — 282 files, 140 Java, 70 Kotlin:

| Looked for | Found |
|---|---|
| `AudioRecord`, `MediaRecorder` | 0 |
| mel, FFT, any signal processing | 0 |
| `.tflite`, `.onnx`, any weights | 0 |
| `SpeechRecognizer` (the Android service) | 19 references |

It is four patches against Gboard that flip Google's internal feature flags.
Rambler itself runs on Google's servers, on Android, with no API.

### The most useful thing in it is a warning

`GboardRamblerDictationRuntime.java` exists to *undo* what Google's model
does. Its own comment: the backend may classify the user's speech as an
agentic command and put an **AI-written answer** where the transcript should
go, so the patch reflects into the proto to force the verbatim transcript
back and reset the intent to dictation.

That is the rule this feature is built on: **write what was said.** Tidying
what the recogniser produced is in scope. Deciding what the speaker meant is
not.

---

## 2. How it works

A dictation is a recording, and the recording becomes **a job in the same
queue that transcribes files**.

```
Ctrl+Shift+D ──► microphone opens ──► "start typing"
                       │
                 audio buffered
                       │
Ctrl+Shift+D ──► microphone closes ──► "stop"
                       │
                 written to a .wav in out/
                       │
                 jobs.submit(...)  ◄── the same call the file picker makes
                       │
   ┌───────────────────┴────────────────────┐
   │ queue · progress · time left · speed    │  the window the user
   │ language · model name · cancel          │  already knows
   └───────────────────┬────────────────────┘
                       │
                 inserted into Word: one insertion, one undo
```

Sharing the queue is the point. Progress, the estimate, the speed, the model
name, the jobs list, cancelling, and the insertion into Word already existed
and were tested for file transcription. A dictation with its own path would be
a second implementation of every one of them, and the two would drift apart at
the first change to either. `app/dictate.py` records, refuses to hand over a
recording with nothing in it, and speaks; it does not transcribe.

A dictation queued while a file is being transcribed waits its turn and says
so, exactly as a second file would.

### Why the recording is kept as a file

It goes to `out/` as `dictation-HH-MM-SS.wav`. That makes it an ordinary job
with a name the jobs list can show and the progress window can report, and it
means a dictation that went wrong can be played back or re-run rather than
being gone the moment it failed.

---

## 3. Speed, and the measurement the design rests on

Whisper pads every clip to a **30-second window**, so the cost of a call is
almost all fixed. Measured on this CPU with `large-v3-turbo`:

| Clip | Wall |
|---|---|
| 2 s | 17.9 s |
| 5 s | 18.5 s |
| 10 s | 21.0 s |
| 20 s | 23.3 s |

A floor of about 17.5 s plus 0.3 s per second of speech. A four-second
dictation paying twenty seconds is why the first attempt at this feature was
unusable.

**The fix.** The encoder's positional embedding is `sinusoids(n_ctx, n_state)`
— fixed, not learned — so using only its first *n* frames is well defined, and
attention then runs over the frames that exist instead of 1500 of which 1400
are padding. Measured, same model, same text:

| | Wall |
|---|---|
| 30 s window, language auto-detected | 18.5 s |
| 30 s window, language passed in | 9.3 s |
| 14 s window, language passed in | 4.2 s |
| **8 s window, language passed in** | **2.7 s** |
| 6 s window, language passed in | 3.1 s, and it repeats itself |

`engine/window.py` offers two window sizes, 8 s and 14 s, both checked against
the full window on real speech, and both warmed up at startup because each
tensor shape costs several seconds to build the first time it is used. Any
chunk longer than they hold falls back to the full thirty, so transcribing an
ordinary file is untouched.

### What stage 0 found before any of this shipped

Four things that a short benchmark had not shown, each now a test:

**Headroom grows with the content.** 5 s of speech was right in an 8 s window;
11 s in a 14 s window appended a repeat of the opening phrase.

**The full window is not ground truth.** At 10 s it stopped at its own
boundary and dropped the next word, which the short window caught. So the test
asserts that nothing is *lost* and nothing is *repeated*, not that the two
agree.

**A trailing hallucination passes every quality check.** A 25-second clip
produced `ترجمة نانسي قنقر` — a subtitle translator's credit — at an
unremarkable 0.77 compression ratio and 0.00 no-speech probability. What gave
it away was the timestamp: it began after the audio ended.

**A cut inside a word is the most expensive thing that can happen.** Whisper
answers a segment it cannot make sense of by retrying at six rising
temperatures, and no retry puts back half a word: one segment took **56.6 s**
instead of 7.0 s.

---

## 4. More than one language in one dictation

Say a sentence in Arabic, the next in English and the next in Spanish, and
each is written in its own script.

This is not something the model does by being asked. **Whisper decides one
language per call and writes the whole call in that script.** `language` is
not a hint; there is only one answer per call to be had. Handed all three
sentences in a single call it did worse than write them in the wrong script:
it detected Arabic, wrote the Arabic sentence, and **stopped** - ten seconds
of speech lost, measured on exactly that recording.

So the chunk is the unit a speaker can change language in, and for a short
recording the chunks are **one sentence each**: `engine/chunking.py` closes a
chunk at the first pause after 2.5 seconds when the recording is under three
minutes. Each chunk is then handed to the model with no language at all and
decides its own.

```
speech regions   0.3-2.2   2.4-4.8   6.0-8.6   10.1-14.0
chunks           0.1-5.0   5.8-8.8   9.9-14.2
                 Arabic    English   Spanish
```

Nothing is forced and nothing is voted on. The whole-file language vote that
used to happen first is why this failed before: it sampled the opening,
decided "Arabic", and forced Arabic on the English half, which came back in
Arabic letters. One answer per file cannot describe a file with three
languages in it.

The cost is one extra encoder pass per chunk, because detecting is a pass.
The shortened window in §3 is what makes that affordable: a 2.5-second chunk
is encoded in an 8-second window, not a 30-second one.

A language the user has chosen in Settings is still forced on every chunk, and
skips detection entirely. Automatic is the default.

Longer recordings keep 90-second chunks, so transcribing an hour of audio is
not turned into 1,400 calls.

---

## 5. The emoji generator

Say the name of an emoji with the word "emoji" — or "ايموجي" — and the emoji
is written, not the words naming it.

```
"Hello, how are you? Smile face emoji."   →   Hello, how are you? 🙂
"thanks a lot thumbs up emoji"            →   thanks a lot 👍
"شكرا جزيلا ايموجي قلب"                    →   شكرا جزيلا ❤️
"علامة صح ايموجي"                          →   ✅
```

**Counts work.** A number before the name writes that many:

```
"put 3 smile face emoji"   →   put 🙂🙂🙂
"three smile face emoji"   →   🙂🙂🙂
"خمس ايموجي ابتسامة"        →   🙂🙂🙂🙂🙂
"حط ٣ ايموجي قلب"           →   حط ❤️❤️❤️
```

Digits and spelled-out numbers, English and Arabic, Western and Arabic-Indic
digits, in either word order — English usually puts the marker last
("three smile face emoji"), Arabic first ("٣ ايموجي قلب"), and both are
accepted in both languages. **Ten is the ceiling**, however many were heard: a
speech model mishears numbers, and a mishearing should cost a wrong count
rather than a thousand characters pasted into a document.

**The marker word is the whole safety of it.** Nothing is replaced unless
"emoji" or "ايموجي" is in the phrase, so "my heart is fine" and "قلبي تعبان"
are untouched and only "heart emoji" becomes a heart. Nothing is inferred from
sentiment, and nothing is inserted that was not asked for by name.

The table is in `engine/emoji.py`: **38 emoji, 164 spellings of their names**,
longest name first so "crying laughing emoji" is 😂 rather than 😢 followed by
a stray word. A test walks the whole table and fails on any name no phrasing
can reach, because a typo in there would otherwise be a dead entry nothing
ever notices. `please` is deliberately not a name for 🙏 — "add an emoji,
please" is a request for any emoji.

This runs with no network and no language model, like everything else here.

---

## 6. Correcting the text with a local language model

Optional, local, and off the moment it is not there.

Whisper writes down what it heard. It does not punctuate a long sentence the
way a typist would, it keeps every "يعني" and "اه", and it has no idea that
the word it just wrote is spelled differently three lines up. A small language
model running on the same machine fixes all three, and `engine/refine.py` is
how it is reached.

### What it needs, and what happens without it

It talks to **Ollama** on `127.0.0.1:11434`. With Ollama absent, or no model
pulled, or the server stopped, `available` is False and dictation behaves
exactly as it does with the box unticked. There is nothing to configure before
it works and nothing to switch off after.

```
ollama pull qwen3:4b
```

Models are kept in **`nlm/`**, not `models/`. The speech checkpoints and the
language model are fetched by different tools, sized in different units and
deleted for different reasons; somebody clearing one must not take the other
with it. Point Ollama at it with `OLLAMA_MODELS`.

### Which model, and how to install it

```
ollama pull qwen3:4b          # no graphics card - most laptops
ollama pull qwen3:8b          # with an NVIDIA card
```

| Model | Download | For |
|---|---|---|
| `qwen3:8b` | ~5.2 GB | Best Arabic of the practical sizes. Wants a GPU |
| `falcon3:7b` | ~5 GB | Arabic-native alternative |
| **`qwen3:4b`** | **~2.5 GB** | **A machine with no graphics card** |
| `qwen3:1.7b` | ~1.4 GB | A slow machine, or a small disk |

**Note the tag.** `ollama pull qwen3` with no tag fetches the 8B.

**The machine decides, not the user.** `engine/refine.preferred()` asks
`engine.device` whether there is a CUDA card and picks in a different order
either way: quality first with a GPU, size first without. Somebody who pulls
an 8B model onto a laptop with no graphics card should not have to work out
for themselves why dictation started taking half a minute - with both
installed, a CPU machine uses the 4B.

Matching is by exact tag over the whole list before any family fallback.
Matching `qwen3:4b` by family would accept `qwen3:8b`, which is the one thing
this ordering exists to avoid.

A model installed but not on the list is used anyway when it is the only one:
somebody who pulled a model on purpose meant to use it.

Jais 30B has the best Arabic of all and needs about 20 GB of VRAM. It is not
on the list because a dictation tool that needs a workstation is not a
dictation tool.

### Ollama Cloud is not an option

Its hosted API at `ollama.com/api` and `ollama.com/v1` requires an API key as
a Bearer token, and cloud models through a local install require
`ollama signin`. There is no anonymous tier, and as of late 2026 the free
plan was replaced by metered pay-as-you-go. Using it would also send dictated
text off the machine, which is the thing this feature is built not to do.

### What it is told

The prompt is in `engine/refine.SYSTEM_PROMPT`, in Arabic and English, and it
is short on purpose — a long prompt is a model given more ways to be creative.
Correct spelling, punctuation and grammar; remove hesitation words and
unintended repetition; **keep the meaning, the style and the dialect, and
never turn colloquial speech into formal writing**; do not translate,
summarise, explain, or answer anything in the text; write each language in its
own script; return only the corrected text.

Temperature `0.1`. Qwen3 is told `/no_think`, because it reasons out loud
otherwise and there is nothing to reason about in fixing a comma — and any
reasoning block that arrives anyway is stripped rather than written into the
document. `keep_alive` is 30 minutes, so a second dictation a minute later
does not pay the load again.

One paragraph per request, not the whole dictation.

### And it is checked, not trusted

The same line as everywhere else here: tidying what the recogniser produced is
in scope, deciding what the speaker meant is not. The reply is thrown away and
the original kept when it

- opens like an assistant — "إليك النص المصحح", "بالطبع", "Here is";
- has grown or shrunk beyond what a correction explains;
- has changed script, which is a translation wearing a correction's clothes.

### It never touches a file you transcribe

The job carries a `refine` flag that only dictation sets. A recording of a
meeting is evidence: it is transcribed, not improved.

### What it costs

Seconds, on a CPU. That is why the job reports **Correcting the text** as its
own state - a progress window sitting at "transcribing, 100 percent" while
something else happens is a window that looks hung.

---

## 7. What else happens to the text

In order, after the model has written it and before it reaches Word:

1. **Hesitation sounds removed** — `engine/fillers.py`. A list of sounds that
   are not words in either language, matched whole. **No patterns.** The
   previous attempt's `[اوي]{3,}` deleted **راوي** from somebody's dictation
   and `^m{2,}$` ate the "mm" in "5 mm"; more than half the tests now assert
   what *survives*. "يعني", "like" and "you know" are deliberately absent —
   deciding whether this particular one meant something takes a language
   model, and §1 is about what happens when one is allowed to decide.
2. **Repetition the decoder produced** — `engine/cleanup.py`. A word three or
   more times in a row leaves one; a phrase of two or more words repeated
   immediately after itself is collapsed. Twice stays twice, because "very
   very", "لا لا" and "no no" are things people say.
3. **Emoji** — §5.
4. **The user's own replacements** — Settings, *Always correct*, one per line
   as `wrong = right`. Whole words, longest match first, applied to the
   finished text so a replacement can span a sentence. A speech model
   mishears a particular name the same way every time, and the person
   dictating is the one who knows which way. Nothing is inferred; only what
   they wrote is applied.
5. **Into Word** over COM — one insertion, one undo with a single `Ctrl+Z`,
   right-to-left set from the detected language. Never synthetic keystrokes,
   so dictation physically cannot type into a password box or another
   application.

### What is deliberately not done

Rewriting self-corrections ("two — no, three" → "3"), rephrasing, tone,
summarising. All of it needs a model deciding what you meant, and §1 is the
argument against letting one near a document the user cannot see.

---

## 8. Making sure it works: the dictation check

Settings → Dictation → **Check dictation**. It **records and measures**;
reading the Windows settings is not enough. On the machine this was developed
on, all three microphone-consent keys say `Allow` and every capture endpoint
reports OK — while the built-in array records a peak of 0.00003, which is
silence, and the USB headset records 0.03.

| # | Check | On failure it says |
|---|---|---|
| 1 | A microphone exists; names the one in use | which devices were found |
| 2 | Windows permission, user / desktop apps / machine | the exact Settings page |
| 3 | The stream opened at 16 kHz mono | in use, or an unsupported rate |
| 4 | **Three seconds recorded**: peak, average, clipping | muted, too quiet, too loud |
| 5 | A voice was found in it, not just noise | heard sound but no speech |
| 6 | **The words, written back** | the end-to-end proof |
| 7 | How long that took **on this computer** | sets the expectation honestly |
| 8 | Windows accepted the shortcut | which program likely holds it |
| 9 | Word is open with a document | how to open one |

Step 4 is the one that matters. It is the check that would have caught the
program being deaf and saying nothing — which is what happened, and is why
this exists.

If nothing was heard, it tries the other input devices and **names one that
did hear something**, because a user whose default input is a dead built-in
array cannot be expected to guess that the headset two entries down works.

Windows exposes each microphone once per sound interface — eighteen entries
for two microphones on this machine. Read aloud one arrow press at a time that
is a maze, not a choice, so the list shows one entry per device on the
interface most likely to work.

---

## 9. Accessibility

The intended users are blind and use NVDA or JAWS. These are constraints, not
features added afterwards.

- **The text is inserted once, at the end**, never progressively. Moving the
  insertion point while someone is reading drags their screen-reader cursor
  away mid-sentence.
- **Every state change gets a tone as well as a spoken word.** A tone arrives
  immediately; speech waits for the screen reader to be free. Rising to start,
  falling to stop, low for a problem.
- **Nothing heard for five seconds says so**, rather than failing silently at
  the end of a minute of talking.
- **A refused shortcut says why.** With "only when Word is in front" on, a
  press while something else has focus used to do nothing at all — which, to
  somebody who cannot see which window has focus, is identical to the program
  being dead, and is exactly how it was reported.
- **The main window has a Dictate button**, listed directly after Start
  transcribing. The shortcut most likely to be taken by another program is the
  one Word itself uses, and a feature reachable only by a shortcut is not
  reachable.
- **The progress window does not take focus**, so Word keeps the caret where
  the text will land.

---

## 10. Known conflict

`Ctrl+Shift+D` is Word's own shortcut for double underline. A global hotkey
takes it before Word sees it, so while Sada is running, Sada gets it. This was
asked for specifically, it is documented in the in-app help in both languages,
and it is changeable like every other shortcut. The dictation check reports it
when Windows refuses it.

---

## 11. Settings that affect voice typing

| Setting | |
|---|---|
| **Microphone** | Which input to record from. The system default unless chosen. |
| **Always correct** | `wrong = right`, one per line. §7. |
| **Correct with a local model** | §6. On by default; does nothing without Ollama. |
| **Model** | The same model that transcribes files. One list, one choice. |
| **Audio language** | Automatic unless set. A set language is passed straight to the model and skips detection. |
| **Names and terms** | Fed to the model as context, so a name it would otherwise spell by sound is already in front of it. |
| **Start and stop dictation** | The shortcut. |
| **Only when Word is in front** | On by default. Off makes the shortcuts work everywhere. |

---

## 12. A bug worth recording

`engine/backends.py` listed `hotwords` in `OpenAIWhisperBackend.DROP` with
nothing mapping it to `initial_prompt`, which is the reference
implementation's equivalent — and the reference implementation is the one that
ships, because faster-whisper's CTranslate2 weights cannot be fetched on this
network. So **"Names and terms" did nothing at all in every released version.**
It is translated now. It matters more to dictation, where names and jargon are
most of what goes wrong, than any cleanup rule in §5.
