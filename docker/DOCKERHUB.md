# Sada

Local speech-to-text: an offline transcription engine with an HTTP API, built on
OpenAI's Whisper.

Audio goes in, text comes out. Nothing is uploaded, no API key is needed, no
account, and there is no limit on hours or file size.

**This image is the engine.** The Microsoft Word integration that Sada is built
around is a separate Windows desktop program and is not in here — Word automation
needs Word itself, and the interface needs Windows. Use this image to transcribe
on a server, in CI, or from a machine that does not have Word.

---

## Quick start

```bash
mkdir -p models audio out

# Download a model once (1.5 GB). It lives in ./models, not in the image.
docker run --rm -v "$PWD/models:/models" \
  mohammedkhaledmahmoud/sada python -m engine.fetch turbo

# Transcribe a file
cp /path/to/recording.m4a audio/
docker run --rm \
  -v "$PWD/models:/models" -v "$PWD/audio:/audio:ro" -v "$PWD/out:/out" \
  mohammedkhaledmahmoud/sada \
  python -m engine.transcribe /audio/recording.m4a --out /out
```

The transcript appears in `out/recording/` as `.txt`, `.srt` and `.json`.

## As a service

```bash
docker run -d --name sada \
  -p 127.0.0.1:8765:8765 \
  -v "$PWD/models:/models" -v "$PWD/audio:/audio:ro" -v "$PWD/out:/out" \
  -e SADA_TOKEN=pick-your-own-token \
  mohammedkhaledmahmoud/sada
```

```bash
curl localhost:8765/health

curl -X POST localhost:8765/jobs \
  -H 'X-Token: pick-your-own-token' -H 'Content-Type: application/json' \
  -d '{"path": "/audio/recording.m4a"}'
# -> {"id": "a1b2c3d4e5f6", "state": "queued"}

curl localhost:8765/jobs/a1b2c3d4e5f6   -H 'X-Token: pick-your-own-token'
curl localhost:8765/jobs/a1b2c3d4e5f6/text -H 'X-Token: pick-your-own-token'
```

| Endpoint | Purpose |
|---|---|
| `GET /health` | model loaded, backend, device — no token needed |
| `POST /jobs` | `{"path": "/audio/file.m4a"}` → job id |
| `GET /jobs/<id>` | state, percent, language, estimate |
| `GET /jobs/<id>/text` | the transcript, once the state is `done` |
| `DELETE /jobs/<id>` | cancel |

Add `?format=text` to any endpoint for `KEY=VALUE` lines instead of JSON.
Every endpoint except `/health` requires the token.

Paths in a request are paths **inside the container**, so put files in `./audio`
on the host and ask for `/audio/<name>`.

---

## What it does well

**Detects the language itself.** Five windows are sampled from across the file
and voted on, then the result is locked for the whole file. A recording that opens
with music, silence or a greeting in another language is still read correctly, and
the language cannot flip halfway through. Over ninety languages, with no language
parameter to set.

**Handles long recordings.** Every segment is written to disk as it finishes, so a
three-hour file that is interrupted resumes from where it stopped instead of
starting over.

**Resists the usual Whisper failure.** Long files make Whisper invent text in
silence and then repeat itself. The decoder settings, voice-activity filtering and
a post-pass are tuned against exactly that.

**Produces readable text,** grouped into paragraphs on natural pauses rather than
a wall of fragments, with subtitles and per-segment timings alongside.

---

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SADA_TOKEN` | generated per start | Auth token. Set it; otherwise it changes on restart and is only in the logs. |
| `SADA_MODEL` | `turbo` | Checkpoint to load |
| `SADA_MODELS_DIR` | `/models` | Where checkpoints live |
| `SADA_OUT_DIR` | `/out` | Where transcripts are written |
| `OMP_NUM_THREADS` | all cores | Inference threads |

### Models

| Checkpoint | Download | Notes |
|---|---|---|
| `small` | 461 MB | fastest, fair accuracy |
| `medium` | 1.4 GB | middle ground |
| `large-v3-turbo` | 1.5 GB | **recommended** — about 6x faster than large-v3 |
| `large-v3` | 2.9 GB | most accurate, slowest |

```bash
docker run --rm -v "$PWD/models:/models" \
  mohammedkhaledmahmoud/sada python -m engine.fetch --list
```

Models are mounted rather than baked in, so the image stays small and the weights
can be changed without a rebuild.

---

## Requirements

| | |
|---|---|
| RAM | **at least 6 GB for the container** — the model is held in float32 |
| CPU | all of it, while a job runs; jobs are queued, never run in parallel |
| Disk | ~1.5 GB image, plus the model |

Speed on CPU is roughly **0.7x real time**, so an hour of audio takes about 80
minutes. This image installs the CPU-only build of torch on purpose: the CUDA
build adds several gigabytes that a CPU-only container can never use. For GPU
inference, change the torch install in the Dockerfile to a CUDA wheel and run
with `--gpus all`.

## Security

Bind to loopback (`-p 127.0.0.1:8765:8765`) unless you mean to expose it, and set
`SADA_TOKEN` to a value you chose before you do. The service reads any path a
request names inside the container, so mount your audio **read-only** and do not
mount anything you would not want a transcript of.

## Tags

| Tag | |
|---|---|
| `latest` | the current release |
| `1.0.0` | pinned version |
