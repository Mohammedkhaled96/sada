# Voice typing

Press `Ctrl+Shift+D` in any text field, speak, press it again, and the words
appear at the cursor. Word, Notepad, a browser, a message box in Teams or
WhatsApp — wherever the cursor is. "Voice typing" and "dictation" are the same
feature; this is the one document for it.

Everything runs on the machine it is installed on. Nothing is sent anywhere,
there is no key to obtain, no account, and no setting that would change that.

```
Ctrl+Shift+D ─► record ─► Whisper ─► tidy up ─► wherever the cursor is
                (mic)     (models/)  (rules)     (Word over COM, or a paste)
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

### The second project read: voicetype-win

[voicetype-win](https://github.com/mlutonsky/voicetype-win) is the same idea on
Windows — press a hotkey, speak, press again, the text appears — in about 1200
lines. Its engine is `onnx-asr` with NVIDIA **Parakeet TDT 0.6B v3**, which
does language identification, punctuation and capitalisation inside the model
and for free, and that is the whole reason it is faster than this. It is also
why none of it could be taken: Parakeet does not download on this network (the
same `huggingface.co` TLS failure that rules out faster-whisper's own weights),
and its 25 languages do not include Arabic.

What it insists on, which is not taken either: it pastes through the clipboard
with `Ctrl+V` into whatever window has focus. That is how it reaches any
application, and it is also why it needs a routine that waits for the user to
let go of `Alt` before pasting — `Alt` still held turns `Ctrl+V` into
`Ctrl+Alt+V` and nothing arrives. Writing into Word over COM has none of that
class of problem, keeps one undo record, and sets the reading direction.

What *was* taken from it is in §11, and none of it is about recognition: pausing
the music, freeing the model from memory, and above all keeping a log file.

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
    inserted where the cursor is: Word over COM, anything
    else through the clipboard
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

## 4. One language per dictation

The language is detected **over the whole recording**, by a vote on samples
taken from several places, and that one answer is handed to every chunk. The
reading direction follows it: Arabic from the right, English and the other
Latin scripts from the left. Exactly what transcribing a file does, and the
same code doing it.

A language chosen in Settings skips the detection altogether and is simply
passed in.

### It used to allow several, and why that was removed

Every chunk was handed over with no language and chose its own, so a speaker
could move between Arabic, English and Spanish in one breath and each sentence
came back in its own script. It worked. It was removed on request, and the
reasons are worth keeping written down, because they are the reasons not to
put it back without deciding to:

**It cost the detection on every chunk.** Measured on a real 5.5-second Arabic
dictation, same recording, same chunking:

| | Wall |
|---|---|
| no language given, so the chunk detects its own | 16.3 s |
| one language given | 5.6 s |

Byte-identical text, for roughly three times the time.

**And it could not be made cheaper by restructuring.** Detecting explicitly
before each chunk, to pass the answer in, measured on three sentences in
Arabic, English and Spanish - 12 seconds of speech, 3 chunks:

| | Wall |
|---|---|
| no language given, whisper detects internally | 25.2 s |
| detecting ourselves, then transcribing with that language | 27.0 s |

Slower, because whisper's own detection reuses the encoder output it has
already computed for the chunk and a separate call cannot.

**The reading direction had no single answer.** A paragraph faces one way. With
the chunks disagreeing, the best available was "whichever way most of it
faced", which is wrong for the rest of it.

**And a chunk heard as the wrong language is written in the wrong script.**
That is the failure that ended an hour of Arabic with

> ...إلى ملفات نصية **Wassalamualaikum warahmatullahi wabarakatuh**

A vote over the whole recording cannot do that.

### The chunks are still one sentence each

For speed, not for language. `engine/chunking.py` closes a chunk at the first
pause after 2.5 seconds so each one fits an 8-second encoder window instead of
the full thirty - see §3. A file keeps the 90-second default, because a file is
long enough that the window saving does not pay for the extra calls.

---

## 5. What else happens to the text

All of it is `polish()` in `engine/job.py`, called from the job and **only when
the job is a dictation**. In order, per chunk:

1. **Repetition the decoder produced** — `engine/cleanup.py`. A word three or
   more times in a row leaves one; a phrase of two or more words repeated
   immediately after itself is collapsed. Twice stays twice, because "very
   very", "لا لا" and "no no" are things people say.
2. **Hesitation sounds removed** — `engine/fillers.py`. A list of sounds that
   are not words in either language, matched whole. **No patterns.** The
   previous attempt's `[اوي]{3,}` deleted **راوي** from somebody's dictation
   and `^m{2,}$` ate the "mm" in "5 mm"; more than half the tests now assert
   what *survives*. "يعني", "like" and "you know" are deliberately absent —
   deciding whether this particular one meant something takes a language
   model, and §1 is about what happens when one is allowed to decide. A chunk
   that was nothing but hesitation is dropped rather than left as a blank line.
3. **The user's own replacements** — Settings, *Always correct*, one per line
   as `wrong = right`. Whole words, longest match first. A speech model
   mishears a particular name the same way every time, and the person
   dictating is the one who knows which way. Nothing is inferred; only what
   they wrote is applied. Last, so a correction wins over everything above it.

Then §6 puts it where the cursor is.

### None of this ran for a while

`cleanup.py` and `fillers.py` were written, documented here, fully tested —
and imported by nothing but their own test file. The correction list was never
applied to anything, and every unit test passed, because a test of a part
cannot see that no whole calls it.

Two things now make that impossible to repeat quietly: `tests/test_text.py`
runs the **real job function** over a stand-in backend and asserts the text
that comes out of it, and that file runs in CI. The dictation check (§7) also
calls `polish()` on the words just recorded and shows the result, so the
failure would be visible to the user and not only to a test.

### What is deliberately not done

Rewriting self-corrections ("two — no, three" → "3"), rephrasing, tone,
summarising. All of it needs a model deciding what you meant, and §1 is the
argument against letting one near a document the user cannot see.

**Spoken emoji** used to be here: say "heart emoji" and get a heart, with
counts, in both languages, from a table of 38 emoji under 164 names. It was
removed on the evidence of using it. Every speaker names an emoji differently,
a table can only hold the namings somebody thought of, and the failure mode is
not a missing emoji - it is the words `three heart emoji` printed into the
document, which is worse than never having offered it.

---

## 6. Where the text lands

**In Word, over COM.** One insertion, one undo with a single `Ctrl+Z`, and the
paragraph's reading direction set from the detected language. No synthetic
keystrokes at all. This is the better path and it is taken whenever Word is
the window in front.

**Every insertion has a blank line before it, and the caret is left after
it.** Both halves are bug fixes, not preferences:

* without the separation, a second job lands against the end of the first and
  the two run together as one sentence;
* without the caret moving to the end, the second insertion happens at the
  *start* of the first, so two jobs come out in the opposite order to the one
  they were spoken in.

The blank line is *made up to* rather than added blindly: `breaks_before()`
counts the paragraph marks already in front of the insertion point, so a caret
sitting mid-sentence gets two breaks, one already on an empty line gets none,
and the result is one blank line in every case rather than a growing stack.

**In every other application, through the clipboard** — `app/sendtext.py`.
Notepad, a browser, a message box in Teams or WhatsApp. Four things make that
work rather than silently do nothing:

1. **The text goes back to the window the dictation *started* in.** Not the
   one in front when it finishes. The transcription takes seconds, and in
   those seconds the progress window appears or a notification takes the
   foreground - so the sentence meant for Teams was aimed at whatever had
   replaced it. The window handle is captured when the recording starts,
   carried on the job, and restored with `focus_window()` before anything is
   sent; if it cannot be restored, the log says so instead of the text going
   somewhere unintended. The progress window is also shown with `SW_SHOWNA`
   now, so it does not take the foreground in the first place - two defences,
   because the second one does not depend on the first being right.
2. **The modifiers are released first.** The user is still holding
   `Ctrl+Shift` when the shortcut fires, and `Ctrl+V` sent while `Shift` is
   down arrives as `Ctrl+Shift+V` - "paste without formatting" in some
   applications and nothing at all in others. So it waits up to 0.7 s for the
   keys to come up, then forces them up. This is the one idea taken whole from
   voicetype-win (§1), which found it the hard way with `Alt`.
3. **`SendInput`'s return value is checked.** It refuses silently when there is
   nothing to send to - a locked screen, an elevated window - and returns
   zero. A caller that ignores that reports a dictation as written when not
   one character arrived. (Getting `sizeof(INPUT)` wrong does the same thing,
   which is how this was found: the union has to be sized by `MOUSEINPUT`, its
   largest member, for 40 bytes on x64.)
4. **The clipboard is put back.** Theirs, not ours. Borrowing it for a third
   of a second is acceptable; keeping it means somebody who copied a paragraph
   and then dictated a sentence has lost the paragraph.

If the paste is refused, the text is typed character by character instead.

Outside Word the separation is the other way round: the text ends with a
**space**, and a blank line is put in *front* of it only when the same window
received the previous dictation. There is nothing above a first dictation to
separate it from, and a message box that opens with two empty lines above the
sentence is worse than no separation at all. What cannot be done out there is
read back what is already in the field - no application but Word will say -
so this is tracked rather than measured.

### A password box is refused

Out loud, and nothing is written. `looks_like_password_field()` asks the
foreground window's own GUI thread what has focus and reads `ES_PASSWORD` off
it. Only standard controls answer honestly - a password field drawn by a web
page has no class to read - so this is a floor and not a guarantee. It is here
because the cost of being wrong in the other direction is somebody's spoken
password pasted into a chat window.

---

## 7. Making sure it works: the dictation check

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
| 8 | **The same text after `polish()`** | the tidying stage is not connected |
| 9 | Windows accepted the shortcut | which program likely holds it |
| 10 | Word is open with a document | how to open one |
| 11 | Where the log file is | said always, pass or fail |

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

## 8. Accessibility

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

## 9. Known conflict

`Ctrl+Shift+D` is Word's own shortcut for double underline. A global hotkey
takes it before Word sees it, so while Sada is running, Sada gets it. This was
asked for specifically, it is documented in the in-app help in both languages,
and it is changeable like every other shortcut. The dictation check reports it
when Windows refuses it.

It is also the one shortcut that is **not** gated on "only when Word is in
front", and cannot be: dictating into Teams or Notepad is the point of it. So
`Ctrl+Shift+D` is taken system-wide for as long as Sada runs, and any other
application's use of it is taken with it. The same trade, more widely.

---

## 10. Settings that affect voice typing

| Setting | |
|---|---|
| **Microphone** | Which input to record from. The system default unless chosen. |
| **Always correct** | `wrong = right`, one per line. §5. |
| **Model** | The same model that transcribes files. One list, one choice. |
| **Audio language** | Automatic unless set. A set language is passed straight to the model and skips detection. |
| **Names and terms** | Fed to the model as context, so a name it would otherwise spell by sound is already in front of it. |
| **Start and stop dictation** | The shortcut. |
| **Only when Word is in front** | On by default, and it does **not** apply to dictation - the dictation shortcut is always global, because dictating into another application is what it is for. It gates the file-transcription, status and cancel shortcuts. |
| **Pause what is playing while I dictate** | On by default. §11. |
| **Keep the model in memory between dictations** | On by default. Off returns about 3 GB after each dictation and loads it again on the next. |
| **Start Sada when Windows starts** | A shortcut in the Startup folder, which the user can see and delete. |

---

## 11. Around the recording

Three things that are not about recognition at all, and each of which came out
of reading another project (§1) rather than out of a bug report.

**Whatever is playing is paused for the length of the recording** —
`app/mediapause.py`, through the System Media Transport Controls, the interface
the keyboard's media keys use. The microphone hears the music too, and a line
of a song in the middle of a dictated sentence is not something the user can
predict or explain. Two rules make it safe to do automatically: only what is
**playing right now** is paused, and only those exact players are resumed, and
only if they are still paused — so a dictation never starts something the user
had stopped. The music comes back the moment the microphone closes, not when
the transcription finishes. Sending the play/pause media key instead was
rejected: it cannot tell whether anything was playing, so when nothing was it
would *start* the music.

**The model can be freed from memory** — the tray's *Free the memory*, or the
setting, which frees it after every dictation. Pressing the dictation shortcut
with no model loaded starts the load, says so, and **begins recording by itself
when it lands**, rather than refusing and leaving somebody who cannot see the
tray icon to guess when to press again. What makes this safe rather than clever
is that loading was already asynchronous behind one flag that everything checks,
so unloading just puts the program back into a state it already handles.

**There is a log file** — `engine/applog.py`, beside `settings.json`, one
megabyte with a single `.1` backup. There was none at all, which in an
installed build with no console meant every problem had to be reproduced live
to be seen at all. It records the shortcut registrations Windows refused, the
device each recording opened and at what rate, the peak and the average level
and any dropped blocks, every refusal and why, the language and timing of each
job, and the full traceback of anything caught. Not the dictated text: that is
the user's document, so only its length is written down. The dictation check
names the file, so finding it needs no file manager.

---

## 12. What is still worth doing

**Transcribe each sentence while the user is still speaking.** Nothing here
starts work until the recording is finished, so the wait after a dictation is
the whole of it. Chunks one and two could have been transcribed during the
pauses the speaker left, leaving only the last one to wait for — the same
words, the same accuracy, a fraction of the wait.

It is not done because it is the one change that is genuinely architectural:
the job takes a finished file, and feeding it sentences as they arrive touches
the queue, the cancel path, and the order text reaches the document. Worth
doing deliberately, not alongside other work.

---

## 13. A bug worth recording

`engine/backends.py` listed `hotwords` in `OpenAIWhisperBackend.DROP` with
nothing mapping it to `initial_prompt`, which is the reference
implementation's equivalent — and the reference implementation is the one that
ships, because faster-whisper's CTranslate2 weights cannot be fetched on this
network. So **"Names and terms" did nothing at all in every released version.**
It is translated now. It matters more to dictation, where names and jargon are
most of what goes wrong, than any cleanup rule in §5.
