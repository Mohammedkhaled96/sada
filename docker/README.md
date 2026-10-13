# Running the engine in Docker

## What the container is, and is not

**It is not the desktop application.** The Word integration talks to Microsoft
Word over COM, the interface is built from native Windows controls, and the
shortcuts are Windows global hotkeys. None of that exists in a Linux container,
so the image does not pretend to provide it.

**It is the transcription engine and its HTTP API.** Use it to transcribe on a
server, in a CI job, in a batch over a folder, or from a machine that is not the
one running Word. The desktop program stays a Windows program.

| | Desktop app (Windows) | Container |
|---|---|---|
| Transcription engine | yes | yes |
| Automatic language detection | yes | yes |
| Resume after interruption | yes | yes |
| HTTP API | via `engine.daemon` | yes, the point of it |
| Insert into Word | yes | no — needs Word |
| Interface, shortcuts, screen reader | yes | no — needs Windows |

## First run

```bash
docker compose run --rm fetch --list      # what is installed
docker compose run --rm fetch turbo       # 1.5 GB, once
docker compose up -d
docker compose logs sada           # the auth token is printed here
```

Models live in `./models` on the host, mounted at `/models`. They are **not**
baked into the image: `large-v3-turbo` alone is 1.5 GB, which would make the
image enormous and the weights impossible to change without a rebuild.

The first start loads the model into RAM, which takes around half a minute, and
the healthcheck allows three minutes before it reports unhealthy.

## Using the API

Every endpoint except `/health` needs the token, as `X-Token` or `?token=`.
Add `?format=text` to any of them to get `KEY=VALUE` lines instead of JSON.

```bash
TOKEN=$(docker compose logs sada | grep -oP 'token\s+\K\S+' | tail -1)

curl -s localhost:8765/health

curl -s -X POST localhost:8765/jobs \
     -H "X-Token: $TOKEN" -H 'Content-Type: application/json' \
     -d '{"path": "/audio/meeting.m4a"}'
# -> {"id": "a1b2c3d4e5f6", "state": "queued"}

curl -s "localhost:8765/jobs/a1b2c3d4e5f6" -H "X-Token: $TOKEN"
curl -s "localhost:8765/jobs/a1b2c3d4e5f6/text" -H "X-Token: $TOKEN"
curl -s -X DELETE "localhost:8765/jobs/a1b2c3d4e5f6" -H "X-Token: $TOKEN"
```

Paths in a request are paths **inside the container**, so put the file in
`./audio` on the host and ask for `/audio/<name>`.

## One-off transcription, no service

```bash
docker compose run --rm cli /audio/meeting.m4a
docker compose run --rm cli /audio/lecture.mp3 --model large-v3 --timestamps
```

Results appear in `./out/<filename>/` as `.txt`, `.srt` and `.json`.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SADA_TOKEN` | generated per start | Fixed auth token, so clients know it in advance |
| `SADA_MODEL` | `turbo` | Which checkpoint the service loads |
| `SADA_MODELS_DIR` | `/models` | Where checkpoints are kept |
| `SADA_HOST` | `0.0.0.0` in the image | Bind address |
| `OMP_NUM_THREADS` | `6` | CPU threads for inference |

## Security

The port is published on `127.0.0.1` only. Before exposing it on a network,
set `SADA_TOKEN` to a value you chose — otherwise the token changes on every
restart, and anything that reaches the port can submit jobs and read
transcripts. The token is compared with `secrets.compare_digest`, and the
container binds `0.0.0.0` only because a container is unreachable otherwise;
the published port is what decides who can connect.

The service reads whatever path a request names, within the container, so mount
`./audio` read-only (as the compose file does) and do not mount anything you
would not want a transcript of.

## Resources

| | |
|---|---|
| Image | roughly 1.5 GB (Python, ffmpeg, CPU-only torch) |
| Models volume | 461 MB to 2.9 GB per checkpoint |
| RAM while running | about 5 GB with `large-v3-turbo` |
| CPU | all of it, for the duration — jobs run one at a time on purpose |

RAM is the constraint worth planning for. The torch backend holds the model in
float32, so give the container at least 6 GB.

## No GPU support here

The engine detects CUDA and uses it on the desktop, but this image installs the
CPU-only torch build on purpose: the CUDA build adds several gigabytes, and
passing a GPU through needs the NVIDIA container toolkit and a base image to
match. If you need GPU inference in a container, change the `torch` install in
the `Dockerfile` to a CUDA wheel and run with `--gpus all`.

## Publishing the image

`docker/publish.sh` builds, tags, **runs the container test suite**, and only then
pushes. It refuses to publish an image that failed its own tests.

```bash
bash docker/publish.sh                      # prompts for the token
VERSION=1.0.1 bash docker/publish.sh        # a different version
DOCKERHUB_TOKEN=... bash docker/publish.sh  # for CI
```

The token is read from a prompt or an environment variable and piped to
`docker login --password-stdin`. Do not use `docker login -u user -p token`:
anything passed as an argument lands in your shell history and is visible in the
process list to every other process on the machine. Docker itself warns about
`-p` for this reason.

There is **no separate step to create the repository** — the first push creates
it, as a **public** repository. To publish privately, create it in the Docker Hub
web interface first and set it to private, then push.

After publishing, paste `docker/DOCKERHUB.md` into the repository description on
Docker Hub; that is the page people see before they pull.

When you are finished on a machine, sign out: `docker logout`.

### Testing the image

```bash
bash tests/test_docker.sh                        # the local build
IMAGE_TAG=user/sada:1.0.0 bash tests/test_docker.sh   # a tagged one
```

It builds nothing itself. It checks the image contents (no Windows app inside,
ffmpeg present, CPU-only torch, Silero VAD loadable with no network), that the
mounted model volume is seen, that the API enforces its token, that a real file
transcribes correctly through HTTP, and that the transcript **reaches the host**
rather than being written inside the container where it would be lost.
