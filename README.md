# Sada

**Transcription for Microsoft Word.**

[![Docker Hub](https://img.shields.io/badge/docker-mohammedkhaledmahmoud%2Fsada-2496ED?logo=docker&logoColor=white)](https://hub.docker.com/r/mohammedkhaledmahmoud/sada)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Windows-10%20%7C%2011-0078D6?logo=windows&logoColor=white)](#install)
[![Whisper large-v3-turbo](https://img.shields.io/badge/Whisper-large--v3--turbo-412991?logo=openai&logoColor=white)](#how-it-is-put-together)
[![100% local](https://img.shields.io/badge/audio-never%20leaves%20your%20PC-success)](#why-it-exists)
[![Accessible](https://img.shields.io/badge/screen%20readers-NVDA%20%7C%20JAWS-5A2D82)](docs/accessibility.md)
[![Price](https://img.shields.io/badge/price-free-brightgreen)](#why-it-exists)
[![Latest release](https://img.shields.io/github/v/release/Mohammedkhaled96/sada?label=latest&logo=github)](https://github.com/Mohammedkhaled96/sada/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/Mohammedkhaled96/sada/total?logo=github&label=downloads)](https://github.com/Mohammedkhaled96/sada/releases)
[![winget](https://img.shields.io/badge/winget-install%20sada-0078D6?logo=windows&logoColor=white)](#download)

A free, fully local replacement for Microsoft 365's paid Transcribe feature, for
ordinary desktop Word.

Press a shortcut in Word, pick an audio or video file, and the transcript is
inserted at your cursor. No subscription, no API key, no account, no upload — the
audio never leaves the machine.

Built to be fully usable from the keyboard and with the **NVDA** and **JAWS**
screen readers.

---

## Download

**[⬇️ Download the latest installer](https://github.com/Mohammedkhaled96/sada/releases/latest)** — Windows 10/11, no Python needed.

| | |
|---|---|
| **Direct download** | [`Sada-Setup-1.0.0.exe`](https://github.com/Mohammedkhaled96/sada/releases/download/v1.0.0/Sada-Setup-1.0.0.exe) (238 MB) |
| **All releases** | [releases page](https://github.com/Mohammedkhaled96/sada/releases) |
| **Checksum** | `SHA256SUMS.txt` is attached to each release |

### Or install from the command line

```powershell
winget install sada
```

Unattended, with no prompts — handy for scripted or remote setups:

```powershell
winget install sada --silent --accept-package-agreements --accept-source-agreements
```

Upgrade later with `winget upgrade sada`, and remove it with `winget uninstall sada`.
The installer needs no administrator rights: it installs for the current user
only, and the speech model is downloaded afterwards from the settings window.

---

## Why it exists

Microsoft 365's Transcribe needs a paid subscription, uploads your audio to its
servers, and caps you at **300 minutes per month** and **200 MB per file**. The
free desktop Word that most people have does not include it at all, and no add-in
in the store does the job well for free.

This has no cap on minutes, no cap on file size, no network dependency once
installed, and keeps the audio on your own disk.

| | Microsoft 365 Transcribe | Sada |
|---|---|---|
| Cost | paid subscription | free |
| Monthly limit | 300 minutes | none |
| File size limit | 200 MB | none |
| Audio leaves your machine | yes | **no** |
| Works offline | no | yes, after install |
| Needs an account or key | yes | no |
| Screen reader support | partial | designed for it |

---

## Features

**Accurate transcription.** OpenAI's Whisper `large-v3-turbo`, running locally.
On clear Arabic speech it writes `بسم الله الرحمن الرحيم` and `المكفوفين`
correctly, where a small model produces `بسم الله رحمة رحمة` and `المكففين`.

**The language is detected for you.** There is no language picker. Five windows
are sampled from across the file and voted on, then the result is locked — so a
file that opens with music, silence or a greeting in another language is still
read correctly, and the language cannot flip halfway through.

**Hours of audio.** Every segment is written to disk the moment it is finished.
If the machine sleeps, the program is closed, or the power goes out, transcribing
the same file again carries on from where it stopped instead of starting over.

**A queue, not a wait.** Start another file while one is running and it queues
behind it. Cancel a job and you can start a new one immediately — the cancelled
one unwinds in the background. Every job is listed with its own state, progress,
language and estimate.

**It writes into Word properly.** The whole insertion is a single undo, Arabic
paragraphs get right-to-left reading order, and the cursor is left at the start
of the new text so you can begin reading at once.

**Interface in English or Arabic**, switchable in Settings, including the spoken
messages. Choosing Arabic mirrors the layout as well as translating it.

**Any format.** Anything ffmpeg reads: mp3, m4a, wav, ogg, opus, wma, flac, aac,
and video files such as mp4, mkv, avi, mov and webm, whose audio is extracted
automatically.

---

## Install

> Most people want the installer instead — see [Download](#download). The steps
> below are for running from source.

Needs **Windows**, **Microsoft Word** (2016 or newer, including the free desktop
version), and **Python 3.12**.

```bash
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Download a model once — 1.5 GB, and never again:

```bash
.venv\Scripts\python.exe -m engine.fetch turbo
```

ffmpeg must be on `PATH`, or put `ffmpeg.exe` and `ffprobe.exe` in `bin/`.

Then run **`Sada.vbs`**. It sits in the tray and announces itself. To
start it with Windows, put a shortcut to it in the folder `shell:startup` opens.

**Only want to transcribe files, without the Word integration?** Skip all of the
above and use the container: `docker pull mohammedkhaledmahmoud/sada`. See
[Docker](#docker).

---

## Using it

1. Open Word and a document.
2. Press **`Ctrl+Shift+T`**.
3. Pick the file.
4. The program says which language it found, then reports progress.
5. The transcript is inserted, with the cursor at the start of it.

| Shortcut | Does |
|---|---|
| `Ctrl+Shift+T` | Start transcribing — opens the file chooser |
| `Ctrl+Shift+W` | Program window — status, the job list, every command |
| `Ctrl+Shift+O` | Settings |
| `Ctrl+Shift+U` | Speak the status: progress and time remaining |
| `Ctrl+Shift+X` | Cancel the running job |

All five are configurable. The full guide is **[USER_GUIDE.md](USER_GUIDE.md)**.

---

## Speed

For one hour of audio:

| Hardware | turbo | large-v3 |
|---|---|---|
| NVIDIA, 6 GB or more | 2–4 min | 5–8 min |
| NVIDIA, 4 GB | 4–6 min | ~12 min |
| CPU, 8–12 cores | 60–90 min | 3 h or more |
| CPU, 4 cores | 2–3 h | much longer |

The CPU figures are honest rather than hopeful: measured at **0.7x real time** on
a 12-core machine, so an hour of audio takes about 80 minutes, and the process
holds roughly **5 GB of RAM** while it runs.

Both numbers come from one limitation. CTranslate2 int8 would be four to five
times faster at about a quarter of the memory, but its weights are distributed
only through `huggingface.co`, which is TLS-blocked on the network this was built
on, so the engine runs on the reference torch implementation instead. Accuracy is
unaffected — this costs speed and memory, not quality. The route out is written up
in [docs/accuracy-notes.md](docs/accuracy-notes.md#the-remaining-speed-lever).

Long files are a question of time, not of risk: progress is checkpointed, so an
interrupted three-hour file resumes rather than restarting.

---

## How it is put together

```
app/            the Windows desktop program
  main.py       tray app, global hotkeys, hand-off to Word
  gui.py        main window, settings, help, about
  jobs.py       the job queue; submitting is never blocked by a cancel
  wordio.py     Word over COM: insert, reading order, single undo
  speech.py     announcements via JAWS COM, the NVDA DLL, or SAPI
  i18n.py       every user-visible string as an (english, arabic) pair
  config.py     settings and keyboard-shortcut parsing

engine/         platform-independent; this is what the container runs
  job.py        one transcription: decode, detect, stream, checkpoint, write
  chunking.py   splits on silence, so progress is real and resume is possible
  backends.py   openai-whisper and faster-whisper behind one interface
  models.py     which checkpoints are installed, and verified downloads
  audio.py      ffmpeg decoding to float32 mono 16 kHz
  langdetect.py language voting across windows spread over the file
  textout.py    paragraphs, junk filtering, .txt/.srt/.json
  device.py     GPU detection with automatic CPU fallback
  daemon.py     HTTP API on 127.0.0.1, for scripting and the container
  transcribe.py command line interface
  fetch.py      download a checkpoint from the command line
```

**No VBA and no `.dotm`.** A macro template would need Word's *Trust access to
the VBA project object model* turned on — a security setting this program has no
business changing — and MSForms is weaker for a screen reader. The app attaches to
a running Word over COM instead, so Word's macro security is never touched.

**wxPython for the interface**, because it wraps real Win32 controls. NVDA itself
is written with wxPython; a custom-drawn widget would announce nothing.

**The model lives in the app process**, so pressing the shortcut starts work
immediately instead of paying a 30-second load each time. `engine/daemon.py`
exposes the same engine over HTTP for scripting, and is what the container runs.

---

## Docker

Published on Docker Hub, so transcription works on any machine with Docker and
nothing else installed — no Python, no ffmpeg, no setup.

```bash
docker pull mohammedkhaledmahmoud/sada
```

[![Docker Hub](https://img.shields.io/badge/docker%20hub-mohammedkhaledmahmoud%2Fsada-blue)](https://hub.docker.com/r/mohammedkhaledmahmoud/sada)

**The image is the engine, not the desktop app.** The Word integration needs Word
itself over COM, and the interface and shortcuts need Windows, so none of that
can run in a Linux container. Use the image to transcribe on a server, in CI, in
a batch over a folder, or from a machine that has no Word. The desktop program
stays a Windows program.

| | Desktop app | Container |
|---|---|---|
| Transcription, automatic language detection, resume | yes | yes |
| HTTP API | via `engine.daemon` | yes, the point of it |
| Insert into Word | yes | no — needs Word |
| Interface, shortcuts, screen reader | yes | no — needs Windows |

| | |
|---|---|
| Download | **613 MB** compressed (2.65 GB on disk) |
| RAM | **at least 6 GB for the container** — the model is held in float32 |
| Tags | `latest`, `1.0.0` |

---

### For users: transcribe a file

Three folders are shared with the container: `models` for the weights, `audio`
for your files, `out` for the transcripts.

```bash
mkdir -p models audio out

# Download a model once. 1.5 GB, and never again - it lives in ./models,
# not inside the image, so it survives upgrades.
docker run --rm -v "$PWD/models:/models" \
  mohammedkhaledmahmoud/sada python -m engine.fetch turbo

# Put your recording in ./audio and transcribe it
docker run --rm \
  -v "$PWD/models:/models" -v "$PWD/audio:/audio:ro" -v "$PWD/out:/out" \
  mohammedkhaledmahmoud/sada \
  python -m engine.transcribe /audio/meeting.m4a --out /out
```

```
language    ar  (100% confidence)  [rtl]
words       62   paragraphs 2
-> /out/meeting/meeting.txt   .srt   .json
```

**Paths in the command are paths inside the container.** The file goes in
`./audio` on your machine, and you ask for `/audio/<name>`.

Other models: `docker run --rm -v "$PWD/models:/models"
mohammedkhaledmahmoud/sada python -m engine.fetch --list`

#### Windows

PowerShell needs `${PWD}` and no backslash continuations:

```powershell
docker run --rm -v "${PWD}/models:/models" -v "${PWD}/audio:/audio:ro" -v "${PWD}/out:/out" mohammedkhaledmahmoud/sada python -m engine.transcribe /audio/meeting.m4a --out /out
```

**Git Bash needs two extra things,** or the bind mount silently resolves to the
wrong place: the container starts with an empty `/models` and downloads the model
again instead of using yours. Git Bash rewrites anything in an argument that
looks like a Unix path, so turn that off and give Docker a Windows path:

```bash
export MSYS_NO_PATHCONV=1
H="$(pwd -W)"
docker run --rm -v "$H/models:/models" -v "$H/audio:/audio:ro" -v "$H/out:/out" mohammedkhaledmahmoud/sada python -m engine.transcribe /audio/meeting.m4a --out /out
```

---

### For developers: the HTTP API

Run it as a service and submit jobs over HTTP. The model stays loaded in RAM, so
jobs start immediately instead of paying a 30-second load each time.

```bash
docker run -d --name sada \
  -p 127.0.0.1:8765:8765 \
  -v "$PWD/models:/models" -v "$PWD/audio:/audio:ro" -v "$PWD/out:/out" \
  -e SADA_TOKEN=choose-your-own-token \
  mohammedkhaledmahmoud/sada
```

`127.0.0.1:` in the port mapping keeps it on your own machine. Drop it and the
service is reachable from the whole network — **set `SADA_TOKEN` to something you
chose before you do**, or the token changes on every restart and only appears in
the logs.

```bash
curl localhost:8765/health

curl -X POST localhost:8765/jobs \
     -H 'X-Token: choose-your-own-token' -H 'Content-Type: application/json' \
     -d '{"path": "/audio/meeting.m4a"}'
# -> {"id": "a9b0194048eb", "state": "queued"}

curl localhost:8765/jobs/a9b0194048eb        -H 'X-Token: choose-your-own-token'
curl localhost:8765/jobs/a9b0194048eb/text   -H 'X-Token: choose-your-own-token'
curl -X DELETE localhost:8765/jobs/a9b0194048eb -H 'X-Token: choose-your-own-token'
```

| Endpoint | Purpose |
|---|---|
| `GET /health` | model loaded, backend, device — the only one needing no token |
| `POST /jobs` | `{"path": "/audio/file.m4a"}` → job id |
| `GET /jobs/<id>` | state, percent, language, estimate, word count |
| `GET /jobs/<id>/text` | the transcript, once the state is `done` |
| `DELETE /jobs/<id>` | cancel |
| `POST /shutdown` | stop the service |

Add `?format=text` to any endpoint for `KEY=VALUE` lines instead of JSON, which
is easier to parse from a shell:

```bash
curl "localhost:8765/jobs/a9b0194048eb?format=text" -H 'X-Token: ...'
# state=done
# percent=100.0
# language=ar
# words=62
```

Jobs run **one at a time on purpose**: transcription saturates the CPU and holds
the model's working set, so running two at once makes both slower and risks
running the machine out of memory. Extra jobs queue.

```bash
docker logs -f sada     # progress, and the token if you did not set one
docker stop sada
docker rm -f sada
```

### With compose

```bash
docker compose run --rm fetch turbo     # download a model, once
docker compose up -d                    # start the service
docker compose logs -f sada             # the token is printed here
docker compose run --rm cli /audio/meeting.m4a   # one-shot, no service
```

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SADA_TOKEN` | generated per start | Auth token. Set it, or it changes on restart. |
| `SADA_MODEL` | `turbo` | Which checkpoint to load |
| `SADA_MODELS_DIR` | `/models` | Where checkpoints live |
| `SADA_OUT_DIR` | `/out` | Where transcripts are written |
| `OMP_NUM_THREADS` | all cores | Inference threads |

### Building it yourself

```bash
docker build -t sada .
bash tests/test_docker.sh          # 37 checks against the built image
```

The test suite checks the image contents, that a mounted model is actually seen,
that the API enforces its token, that a real file transcribes correctly through
HTTP, and that the transcript reaches the host rather than being written inside
the container. `docker/publish.sh` runs it as a gate and refuses to push an image
that fails.

No GPU in the image: it installs the CPU-only torch build, because the CUDA build
adds several gigabytes a CPU-only container can never use. For GPU inference,
change the torch install in the `Dockerfile` to a CUDA wheel and run with
`--gpus all`.

Full detail in **[docker/README.md](docker/README.md)**.

---

## Building the releases

Two artifacts, both from the same compiled output:

| | |
|---|---|
| `Sada-<version>-portable.zip` | unzip and run, nothing installed |
| `Sada-Setup-<version>.exe` | per-user installer, no admin rights needed |

Needs the project venv, `binfmpeg.exe`, PyInstaller, and
[Inno Setup 6](https://jrsoftware.org/isdl.php)
(`winget install JRSoftware.InnoSetup`).

```bash
.venv\Scripts\python.exe -m pip install pyinstaller

# 1. Compile. Use `python -m PyInstaller`, not pyinstaller.exe - the launcher
#    exe is not executable from some shells and fails with "Permission denied".
.venv\Scripts\python.exe -m PyInstaller Sada.spec --noconfirm

# 2. Portable release
powershell -Command "Compress-Archive -Path 'dist\Sada\*' -DestinationPath 'release\Sada-1.0.0-portable.zip' -Force"

# 3. Installer
"%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" installer\Sada.iss
```

`binfmpeg.exe` must be present before step 1 or the spec stops with a
message: it does all the decoding, and without it the application cannot open a
single file. Take the "essentials" build from
[gyan.dev](https://www.gyan.dev/ffmpeg/builds/).

The speech model is deliberately **not** bundled. The recommended checkpoint is
1.5 GB, which would make the installer a 2 GB download repeated on every update.
It is fetched on first run and kept outside the installation, so upgrading or
reinstalling never downloads it again.

### Code signing

Neither artifact is signed, and that is a real distribution problem rather than
cosmetic polish. SmartScreen warns on an unsigned download, and a managed
Windows machine can refuse to run it outright: the Defender ASR rule *"block
executable files from running unless they meet a prevalence, age, or trusted
list criterion"* blocks any freshly built unsigned binary, with
`Access is denied` and an event 1121 as the only explanation. A code-signing
certificate is the only fix; an EV certificate carries reputation immediately,
while an OV one needs weeks of downloads to build it.

## Tests

```bash
run-tests.bat
```

**192 checks** across five files. The Word tests launch Word, write to a **new**
document, and close it without saving — they never touch a document you have open.

| File | Covers |
|---|---|
| `test_pipeline.py` | decoding real files, language voting, checkpoints, output |
| `test_resume.py` | the duplicated-transcript bug, and that it cannot return |
| `test_jobs.py` | the queue, non-blocking cancel, honest progress, models |
| `test_app.py` | settings, shortcuts, both languages, every window, Word |
| `test_end_to_end.py` | audio in, text in a Word document out |

---

## Documentation

| | |
|---|---|
| [USER_GUIDE.md](USER_GUIDE.md) | Everything a user needs, in detail |
| [docker/README.md](docker/README.md) | Running the engine in a container |
| [docs/accessibility.md](docs/accessibility.md) | The screen-reader requirements, and what they changed |
| [docs/accuracy-notes.md](docs/accuracy-notes.md) | Why each engine setting is what it is, with measurements |

---

## Credits

Created by Mohammed Khaled.


Speech recognition by [Whisper](https://github.com/openai/whisper) from OpenAI.
Voice activity detection by [Silero VAD](https://github.com/snakers4/silero-vad),
through [faster-whisper](https://github.com/SYSTRAN/faster-whisper).
Decoding by [ffmpeg](https://ffmpeg.org/).
