# Sada — User Guide

*Transcription for Microsoft Word*

Everything the program does, and how to make it do it.

- [Getting started](#getting-started)
- [Your first transcript](#your-first-transcript)
- [The keyboard shortcuts](#the-keyboard-shortcuts)
- [The program window](#the-program-window)
- [Working with several files](#working-with-several-files)
- [Settings](#settings)
- [Model files](#model-files)
- [For screen reader users](#for-screen-reader-users)
- [Long recordings](#long-recordings)
- [Getting better results](#getting-better-results)
- [Where your files go](#where-your-files-go)
- [Command line](#command-line)
- [When something goes wrong](#when-something-goes-wrong)
- [Privacy](#privacy)

---

## Getting started

### What you need

| | |
|---|---|
| Windows | 10 or 11 |
| Microsoft Word | 2016 or newer, including the free desktop version |
| Python | 3.12 |
| ffmpeg | on `PATH`, or `ffmpeg.exe` and `ffprobe.exe` in the `bin` folder |
| Disk | about 2 GB for the program and one model |
| RAM | 8 GB, of which the program uses about 5 GB while transcribing |

A graphics card is not required. If you have an NVIDIA one it is used
automatically and everything is far faster.

### Install

```bash
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m engine.fetch turbo
```

The last line downloads the speech model, 1.5 GB, once. You never download it
again.

### Start it

Run **`Sada.vbs`**. Nothing appears to happen: the program lives in
the notification area and announces itself when it is ready. Loading the model
takes about half a minute on the first start; after that it stays in memory so
transcription begins the moment you press the shortcut.

If it seems not to start, run **`run-with-console.bat`** instead — it keeps a
window open showing any error.

### Start it with Windows

Press `Win+R`, type `shell:startup`, press Enter, and put a shortcut to
`Sada.vbs` in the folder that opens. The program then runs whenever
you log in, and you never have to think about it again.

---

## Your first transcript

1. **Open Word** and a document — a blank one is fine.
2. **Press `Ctrl+Shift+T`.** The program says "Choose the audio file" and a
   standard Windows file chooser opens.
3. **Pick your file** and press Enter.
4. The program reads the audio, works out the language, and says which one it
   found.
5. **Progress is reported** as it goes. Press `Ctrl+Shift+U` at any time to hear
   where it is and how long is left.
6. **When it finishes**, the text is inserted into your document and the cursor
   is placed at the start of it, so you can begin reading immediately.

That is the whole flow. There is nothing to configure to get a good result.

---

## The keyboard shortcuts

| Shortcut | What it does |
|---|---|
| `Ctrl+Shift+T` | Start transcribing — opens the file chooser |
| `Ctrl+Shift+W` | Open the program window |
| `Ctrl+Shift+O` | Open Settings |
| `Ctrl+Shift+U` | Speak the status: what is happening, progress, time left |
| `Ctrl+Shift+X` | Cancel the job that is running |

All five can be changed in Settings.

### Why some only work in Word

By default **Start transcribing**, **Read the status** and **Cancel** work only
while Word is the front window. This keeps the program from taking those key
combinations away from every other application on your machine.

**Open the program window** and **Open Settings** always work, so you can
configure the program before Word is even open.

To make them all work everywhere, turn off *"Shortcuts work only while Word is
in front"* in Settings.

### Two rules on choosing a shortcut

The program will refuse a shortcut with **no modifier key** — plain `T`, say —
because registering that globally would swallow the letter from every program on
the machine. It also refuses the **same shortcut for two commands**. In both
cases it tells you which field is the problem and puts you back in it.

If Windows says a shortcut is already taken, another program owns it. The program
tells you which ones failed; pick different ones.

---

## The program window

`Ctrl+Shift+W`. Three sections.

**Status** — which model is loaded, what it is running on, and which screen
reader was found. While a job is running it shows that job's detail instead.

**Jobs** — every job and exactly what it is doing:

| Column | Shows |
|---|---|
| File | the file name |
| State | waiting its turn, working, cancelling, finished, cancelled, failed |
| Progress | percent complete |
| Language | the language that was detected |
| Time remaining | the estimate, or "still measuring", or the word count once done |

Move through the list with the arrow keys. Below it: **Cancel the selected job**
and **Clear finished jobs**.

**Commands** — Start transcribing, Read the status, Cancel, Settings, Help
(`F1`), About, and **Exit the program**.

Every button has an access key, so `Alt` plus the underlined letter activates it.
`Escape` hides the window and the program keeps running. Exit is here because a
tray icon is no use without a mouse.

---

## Working with several files

**You are never made to wait.**

Press `Ctrl+Shift+T` while a job is running and the new file joins the **queue**.
It starts when the current one finishes. The program tells you how many are
waiting.

**Cancel does not block anything.** Press `Ctrl+Shift+X` and you can start
another file straight away — the cancelled job finishes unwinding in the
background on its own. You will see it as "cancelling" in the job list for a
moment, then "cancelled".

**Jobs run one at a time, deliberately.** Transcription uses the whole processor
and holds the model's working memory. Running two at once would make both slower
and could run the machine out of RAM, so the queue is honest about waiting
instead of pretending to do both.

To cancel a job that has not started yet, select it in the list and press
**Cancel the selected job**. It is dropped immediately, having never begun.

---

## Settings

`Ctrl+Shift+O`. Grouped into sections; every control has a spoken label.

### Interface

**Interface language** — `English` (the default) or `العربية`. Applies as soon as
you save, and changes the spoken messages as well as the windows. Choosing Arabic
also mirrors the layout right to left.

### Engine and accuracy

**Model** — which speech model to use.

| Model | Accuracy | Speed | Download |
|---|---|---|---|
| `turbo` | very good | ~6x faster than large-v3 | 1.5 GB |
| `large-v3` | the best | slowest | 2.9 GB |
| `medium` | good | faster | 1.4 GB |
| `small` | fair | fastest | 461 MB |

`turbo` is the recommended balance and the default. Pick `large-v3` when a
recording is hard — several speakers, background noise, heavy accents. Pick
`small` or `medium` on a slow machine.

A model has to be downloaded before it can be used; see
[Model files](#model-files) below.

**Run on** — `Automatic` uses an NVIDIA graphics card if there is one and the
processor otherwise. The other two force one or the other. Leave it automatic
unless you are testing something.

**Audio language** — `Detect automatically` is the default and is almost always
right. Force a language only if detection gets a particular file wrong.

**Names and terms** — words that help the model spell things correctly. If you
transcribe meetings with the same people or the same jargon, put those names here
and they will be spelled consistently.

### Transcript

**Insert the text** — at the cursor, at the end of the document, or in a new
document.

**Put the time before each paragraph** — prefixes each paragraph with
`[00:12:34]`. Useful for interviews and lectures where you need to find a moment
again.

**Set right-to-left reading order automatically for Arabic** — leave this on. It
is what makes a screen reader read Arabic in the correct order.

**Save a text file and a subtitle file next to the audio** — also writes `.txt`,
`.srt` and `.json` into the `out` folder. The `.srt` is a subtitle file you can
load alongside a video.

### Keyboard shortcuts

All five, editable. The two rules above apply.

### Screen reader

**Speak the status and progress automatically** — turn off for a silent program
that still answers `Ctrl+Shift+U`.

**Speak progress every (seconds)** — how often progress is announced. Set it to
**0** to never announce progress; you can still ask with the shortcut. This is
the setting to reach for if the program talks too much.

**Use the Windows voice if no screen reader is found** — only takes effect when
neither NVDA nor JAWS is detected. Leave it off if you use a screen reader; a
second voice talking over it is worse than silence.

**Shortcuts work only while Word is in front** — described above.

The section ends by telling you which screen reader was actually detected, which
is the quickest way to confirm the program can talk to yours.

### Reset to defaults

At the bottom. Puts every control back to its default **without saving**, so you
can look at the result and still press Cancel if you change your mind.

---

## Model files

The Settings window lists every model with its size and its real state:
**installed**, **not installed**, or **incomplete download**.

Select one and press **Download**. A progress bar appears and you can stop it at
any time. The file is only put into place once its checksum matches, so a failed
or stopped download never leaves behind something that looks like a working
model.

Press **Delete** to remove a model you no longer want; you can always download it
again.

From the command line:

```bash
.venv\Scripts\python.exe -m engine.fetch --list
.venv\Scripts\python.exe -m engine.fetch large-v3
```

---

## For screen reader users

The program was built for NVDA and JAWS from the start, not adapted afterwards.
Several things work the way they do specifically because of that.

**Everything is reachable from the keyboard.** Shortcut, file chooser, progress,
result. The file chooser is Word's own standard dialog, which your screen reader
already handles well, rather than a custom one that would need its own
accessibility work.

**The transcript is inserted once, at the end** — not paragraph by paragraph as
it is produced. Inserting progressively would move the reading cursor while you
were reading, pulling you out of the middle of a sentence.

**The whole insertion is a single undo.** One `Ctrl+Z` removes the entire
transcript, instead of hundreds of presses.

**The cursor is left at the start of the new text**, so you can begin reading
immediately with your normal reading keys.

**Arabic gets right-to-left reading order**, which is not cosmetic: navigating by
word follows the paragraph's reading order, so getting this wrong would make
Arabic read backwards.

**No window ever steals focus.** The program window appears when a job starts but
does not take focus, because you did not ask to go there. Nothing re-focuses
itself on an update.

**Progress is announced on a timer, not per segment**, and you control the
interval — including turning it off entirely while keeping `Ctrl+Shift+U`.

**Status is available on demand.** `Ctrl+Shift+U` says what is happening, how far
along, and how long is left, whenever you want it, without reading the window.

**Errors are spoken sentences**, not codes. "Word is not open. Open Word and a
document, then try again." — not a number.

**The job list is a real Windows list view**, so you move through it row by row
with the arrow keys and your screen reader reads each row.

**Durations are spoken as words.** "1 hour and 2 minutes", not "01:02:00" read as
a string of digits.

**When the program does not know something, it says so.** While it is still
working out how fast it is going it says "still measuring" rather than inventing a
number.

---

## Long recordings

There is no limit on length. A three-hour recording works the same way as a
three-minute one; it only takes longer.

**Progress is saved continuously.** Every segment is written to disk and flushed
the moment it is finished. If the machine sleeps, the program is closed, Windows
restarts, or the power goes out, nothing before that point is lost.

**Transcribing the same file again resumes it.** The program notices the earlier
work and carries on from where it stopped rather than starting over.

**A cancelled job is resumable too.** Cancel a long file, do something else, and
start it again later — it picks up from where you stopped it.

A rough plan for a long file: start it, press `Ctrl+Shift+U` when you want to
know how it is going, and leave the machine alone. Progress is announced at
whatever interval you set.

---

## Getting better results

**Give the model the names.** Put recurring names, places and jargon into *Names
and terms* in Settings. This is the single most effective setting for meetings and
lectures.

**Try `large-v3` on a hard recording.** It is roughly six times slower than
`turbo` but better with overlapping speakers, noise and strong accents.

**Better audio beats a better model.** A recording made close to the speaker with
little background noise will transcribe better on `small` than a distant noisy one
will on `large-v3`.

**Mixed languages are handled, within limits.** The language is detected and then
locked for the whole file, which is what stops it flipping mid-transcript. A file
that genuinely alternates between two languages throughout will come out in the
dominant one.

**Let detection do its job.** It samples five windows from across the file and
votes, so an intro in another language will not fool it. Force a language only
when you have seen it get a specific file wrong.

---

## Where your files go

| | |
|---|---|
| Transcripts | `out\<file name>\` next to the program |
| `.txt` | the transcript in paragraphs |
| `.srt` | subtitles, for playing alongside a video |
| `.json` | every segment with timings and confidence, for other tools |
| `partial.jsonl` | the resume checkpoint — present only if a run was interrupted |
| Settings | `%LOCALAPPDATA%\Sada\settings.json` |
| Models | `models\` next to the program |

A finished run deletes its checkpoint, so if `partial.jsonl` exists, that file was
interrupted and will resume.

---

## Command line

For scripting and batches, without the tray program.

```bash
transcribe.bat "C:\path\to\audio.m4a"
```

Or directly:

```bash
.venv\Scripts\python.exe -m engine.transcribe "audio.m4a" --model large-v3 --timestamps
```

| Flag | Meaning |
|---|---|
| `--model turbo \| large-v3 \| medium \| small` | default `turbo` |
| `--device auto \| cuda \| cpu` | default `auto` |
| `--language ar` | force a language instead of detecting it |
| `--timestamps` | put the time before each paragraph |
| `--hotwords "..."` | names and terms to bias towards |
| `--resume` | continue from the checkpoint |
| `--out DIR` | where to write |

There is also an HTTP API for scripting — see
[docker/README.md](docker/README.md), which documents the same endpoints the
container exposes:

```bash
.venv\Scripts\python.exe -m engine.daemon --model turbo
```

---

## When something goes wrong

| What you hear or see | What to do |
|---|---|
| Nothing at all when you press the shortcut | The program is not running. Start `Sada.vbs`. Press `Ctrl+Shift+W` to confirm it is alive. |
| "Word is not open" | Open Word and a document, then try again. |
| "No document is open in Word" | Press `Ctrl+N` in Word. |
| "The transcription model is still loading" | Wait about half a minute after starting the program. |
| "The transcription model could not be loaded" | The model file is missing or damaged. Open Settings, select it under Model files, and download it again. |
| "These shortcuts could not be registered" | Another program owns them. Change them in Settings. |
| "The program is already running" | It is in the notification area. Press `Ctrl+Shift+W`. |
| "Time remaining: still measuring" | Normal at the start of a file — there is nothing to measure from yet. |
| Very slow | Expected on a processor without a graphics card: about 80 minutes per hour of audio. Choose a smaller model to trade accuracy for speed. |
| Text that makes no sense | Try `large-v3`, and put the names into *Names and terms*. |
| The transcript stops early | It was interrupted. Transcribe the same file again and it resumes. |

If the program will not start at all, run `run-with-console.bat`, which shows the
error instead of hiding it.

---

## Privacy

The audio never leaves your machine. There is no account, no API key, no
telemetry, and no network connection in use while transcribing. The only time the
program uses the network is when you ask it to download a model.

Everything it writes — transcripts, checkpoints, settings — stays in the folders
listed in [Where your files go](#where-your-files-go), on your own disk.
