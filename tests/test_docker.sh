#!/usr/bin/env bash
# Checks that the container image actually works, end to end.
#
# Reading a Dockerfile proves nothing: this builds the image, starts the service,
# transcribes a real file through the HTTP API, and verifies the transcript
# reaches the host. It also checks the things that are easy to get wrong and
# silent when wrong - that the model volume is seen, that auth is enforced, and
# that output does not land inside the container where it is lost on removal.
#
# Needs: Docker running, ./models holding a checkpoint, ./audio/test.mp3.
#
#   bash tests/test_docker.sh
#   IMAGE_TAG=user/sada:1.0.0 bash tests/test_docker.sh

set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

# Overridable so docker/publish.sh can test the tagged image it is about to push.
IMAGE="${IMAGE_TAG:-sada:test}"
PASS=0
FAIL=0
FAILED_NAMES=()

# Git Bash and MSYS rewrite anything in an argument that looks like a Unix path,
# which turns `-v "$HOST/models:/models"` into a mount of the wrong target. It
# fails silently: the container starts with an EMPTY /models and then tries to
# download the model itself. So disable the rewriting and hand Docker a Windows
# path. Anyone running this on Linux, macOS or WSL takes the plain branch.
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    export MSYS_NO_PATHCONV=1
    export MSYS2_ARG_CONV_EXCL='*'
    HOST="$(pwd -W 2>/dev/null || pwd)"
    ;;
  *)
    HOST="$PWD"
    ;;
esac

check() {
  local name="$1" ok="$2" detail="${3:-}"
  if [ "$ok" = "0" ]; then
    printf '[  ok  ] %s%s\n' "$name" "${detail:+  — $detail}"
    PASS=$((PASS + 1))
  else
    printf '[ FAIL ] %s%s\n' "$name" "${detail:+  — $detail}"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

cleanup() {
  docker rm -f sada-test >/dev/null 2>&1
  rm -f audio/_synthetic.wav 2>/dev/null
}
trap cleanup EXIT

echo "=== prerequisites ==="
docker info >/dev/null 2>&1
check "docker engine is reachable" $?

# A test must not depend on a file that is not in the repository. `audio/` is
# gitignored, so on a fresh clone there is nothing in it - and this suite is the
# gate docker/publish.sh runs before pushing, so a missing sample must not block
# a release. Drop in audio/test.mp3 for the full check; otherwise a synthetic
# clip is generated with the container's own ffmpeg and the assertions that judge
# transcript *content* are skipped, while the ones that caught the real bugs -
# the bind mount and the output directory - still run.
mkdir -p audio out
REAL_AUDIO=1
AUDIO_FILE=test.mp3
if [ ! -f audio/test.mp3 ]; then
  REAL_AUDIO=0
  AUDIO_FILE=_synthetic.wav
  docker run --rm -v "$HOST/audio:/audio" "$IMAGE" \
    ffmpeg -nostdin -hide_banner -loglevel error -y \
      -f lavfi -i "sine=frequency=180:duration=8" \
      -ar 16000 -ac 1 /audio/_synthetic.wav >/dev/null 2>&1
  [ -f audio/_synthetic.wav ]
  check "a synthetic clip is generated (no sample supplied)" $? \
    "content assertions will be skipped"
else
  check "a real audio sample is present" 0 "audio/test.mp3 — full checks"
fi

ls models/*.pt >/dev/null 2>&1
check "a model checkpoint is on the host" $? "$(ls models/*.pt 2>/dev/null | head -1)"

echo
echo "=== image ==="
docker image inspect "$IMAGE" >/dev/null 2>&1
check "image exists" $? "$IMAGE"

SIZE=$(docker image inspect "$IMAGE" --format '{{.Size}}' 2>/dev/null)
[ -n "$SIZE" ] && [ "$SIZE" -gt 0 ]
check "image reports a size" $? "$(( ${SIZE:-0} / 1000000 )) MB"

# The desktop program must not be in the image: it cannot run on Linux and
# would only add weight and confusion.
docker run --rm "$IMAGE" sh -c '[ ! -d /app/app ]'
check "the Windows desktop app is NOT in the image" $? "only engine/ is copied"

docker run --rm "$IMAGE" python -c "import engine.daemon, engine.job, engine.chunking" 2>/dev/null
check "engine modules import inside the container" $?

docker run --rm "$IMAGE" sh -c 'command -v ffmpeg >/dev/null && command -v ffprobe >/dev/null'
check "ffmpeg and ffprobe are installed" $?

docker run --rm "$IMAGE" python -c "
import torch, whisper, numpy
assert '+cpu' in torch.__version__ or not torch.cuda.is_available()
print(torch.__version__)
" >/dev/null 2>&1
check "torch is the CPU build" $?

# triton is a torch dependency on Linux that weighs more than torch itself and
# serves only torch.compile, which Whisper never calls. Keep it out.
docker run --rm "$IMAGE" sh -c '! python -c "import triton" 2>/dev/null'
check "triton is not in the image" $? "897 MB of torch.compile support, unused"

# ...but torch has to still work without it.
docker run --rm "$IMAGE" python -c "
import torch
x = torch.randn(4, 4)
assert torch.matmul(x, x).shape == (4, 4)
" >/dev/null 2>&1
check "torch still works without triton" $?

# Every module must resolve the same models directory, and it must follow the
# environment. Five separate definitions once disagreed, and the container
# re-downloaded a 1.5 GB model that was already mounted.
docker run --rm "$IMAGE" python -c "
import engine.paths as p, engine.backends as b, engine.models as m
import engine.daemon as d, engine.transcribe as t
assert b.MODELS_DIR == m.MODELS_DIR == p.MODELS_DIR == __import__('pathlib').Path('/models'),     (b.MODELS_DIR, m.MODELS_DIR, p.MODELS_DIR)
assert d.OUT_DIR == t.OUT_DIR == p.OUT_DIR == __import__('pathlib').Path('/out'),     (d.OUT_DIR, t.OUT_DIR, p.OUT_DIR)
" 2>/dev/null
check "every module resolves /models and /out identically" $?   "one definition in engine/paths.py"

# Silero VAD must be bundled: it is what chunking uses, and downloading it would
# need huggingface.co, which is blocked on the network this was built on.
docker run --rm "$IMAGE" python -c "
from faster_whisper.vad import get_vad_model
get_vad_model()
" >/dev/null 2>&1
check "Silero VAD loads with no network" $? "bundled in the package"

echo
echo "=== model volume ==="
# Prove the bind mount works before trusting anything that depends on it. An
# empty /models makes the container download the model itself, which looks like
# a slow start rather than a broken mount.
COUNT=$(docker run --rm -v "$HOST/models:/models" "$IMAGE"         sh -c 'ls /models/*.pt 2>/dev/null | wc -l' 2>/dev/null | tr -d '[:space:]')
[ "${COUNT:-0}" -ge 1 ]
check "the bind mount actually reaches the container" $?   "${COUNT:-0} checkpoint(s) visible in /models"

OUT=$(docker run --rm -v "$HOST/models:/models" "$IMAGE" python -m engine.fetch --list 2>&1)
# Match "installed" only when it is NOT preceded by "not".
echo "$OUT" | grep -qE '[0-9] (G|M)B[[:space:]]+installed$'
check "the model is reported installed, not missing" $?   "$(echo "$OUT" | grep -E 'installed$' | head -1 | tr -s ' ')"

echo "$OUT" | grep -q "models directory: /models"
check "it looks in /models, not inside the image" $?   "$(echo "$OUT" | grep -o 'models directory:.*' | head -1)"

echo
echo "=== service ==="
docker rm -f sada-test >/dev/null 2>&1
docker run -d --name sada-test \
  -p 127.0.0.1:18765:8765 \
  -v "$HOST/models:/models" \
  -v "$HOST/audio:/audio:ro" \
  -v "$HOST/out:/out" \
  -e SADA_TOKEN=test-token-for-the-suite \
  -e OMP_NUM_THREADS=6 \
  "$IMAGE" >/dev/null 2>&1
check "container starts" $?

API=http://127.0.0.1:18765
TOKEN=test-token-for-the-suite

# /health answers before the model is ready, which is the point of it.
for _ in $(seq 1 60); do
  curl -sf "$API/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -sf "$API/health" >/dev/null 2>&1
check "/health answers without a token" $?

H=$(curl -s "$API/health")
echo "$H" | grep -q '"device"'
check "/health reports the device" $? "$(echo "$H" | grep -o '"device_label": *"[^"]*"' | head -1)"

curl -s "$API/health?format=text" | grep -q "ready="
check "format=text returns KEY=VALUE" $?

# Auth, before anything heavier.
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/jobs" \
       -H 'Content-Type: application/json' -d "{\"path\":\"/audio/$AUDIO_FILE\"}")
[ "$CODE" = "401" ]
check "a job without a token is refused" $? "HTTP $CODE"

CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/jobs" \
       -H "X-Token: wrong" -H 'Content-Type: application/json' \
       -d "{\"path\":\"/audio/$AUDIO_FILE\"}")
[ "$CODE" = "401" ]
check "a job with a wrong token is refused" $? "HTTP $CODE"

CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/jobs" \
       -H "X-Token: $TOKEN" -H 'Content-Type: application/json' \
       -d '{"path":"/audio/does-not-exist.mp3"}')
[ "$CODE" = "400" ]
check "a missing file is refused" $? "HTTP $CODE"

echo
echo "waiting for the model to load in the container (up to 6 minutes)..."
READY=1
for _ in $(seq 1 180); do
  if curl -s "$API/health" | grep -q '"ready": *true'; then READY=0; break; fi
  if curl -s "$API/health" | grep -q '"error": *"[^"]'; then break; fi
  sleep 2
done
check "the model loads inside the container" $READY \
  "$(curl -s "$API/health" | grep -o '"model": *"[^"]*"' | head -1)"

if [ "$READY" != "0" ]; then
  echo
  echo "--- container logs ---"
  docker logs sada-test 2>&1 | tail -20
else
  echo
  echo "=== a real transcription through the API ==="
  JOB=$(curl -s -X POST "$API/jobs" -H "X-Token: $TOKEN" \
        -H 'Content-Type: application/json' -d "{\"path\":\"/audio/$AUDIO_FILE\"}" \
        | grep -o '"id": *"[^"]*"' | cut -d'"' -f4)
  [ -n "$JOB" ]
  check "the job is accepted" $? "id $JOB"

  STATE=""
  for _ in $(seq 1 240); do
    STATE=$(curl -s "$API/jobs/$JOB" -H "X-Token: $TOKEN" \
            | grep -o '"state": *"[^"]*"' | cut -d'"' -f4)
    case "$STATE" in done|error|cancelled) break ;; esac
    sleep 2
  done
  [ "$STATE" = "done" ]
  check "it reaches done" $? "state $STATE"

  SNAP=$(curl -s "$API/jobs/$JOB" -H "X-Token: $TOKEN")
  echo "$SNAP" | grep -q '"language": *"[a-z]'
  check "it reports a detected language" $?     "$(echo "$SNAP" | grep -o '"language": *"[^"]*"' | head -1)"

  TEXT=$(curl -s "$API/jobs/$JOB/text" -H "X-Token: $TOKEN")
  if [ "$REAL_AUDIO" = "1" ]; then
    echo "$SNAP" | grep -q '"language": *"ar"'
    check "it detects Arabic" $? "$(echo "$SNAP" | grep -o '"language": *"[^"]*"' | head -1)"

    echo "$SNAP" | grep -q '"rtl": *true'
    check "it flags right-to-left" $?

    [ ${#TEXT} -gt 100 ]
    check "the transcript comes back over HTTP" $? "${#TEXT} bytes"

    printf '%s' "$TEXT" | grep -q "السلام"
    check "the transcript is correct UTF-8 Arabic" $?       "$(printf '%s' "$TEXT" | cut -c1-40)"
  else
    echo "[ skip ] content assertions (synthetic clip, not speech)"
  fi

  # The bug this test exists for: the daemon used to write to /app/out, so a
  # transcript never reached the host and vanished with the container.
  ls out/test/test.txt >/dev/null 2>&1
  check "the transcript reaches the HOST, not just the container" $? \
    "out/test/test.txt"

  if [ -f out/test/test.txt ]; then
    grep -q "السلام" out/test/test.txt
    check "the file on the host holds the text" $? \
      "$(wc -c < out/test/test.txt | tr -d ' ') bytes"
  fi

  # A finished run must not leave a checkpoint, or the next run would resume
  # from a complete one.
  [ ! -f out/test/partial.jsonl ]
  check "no checkpoint is left after a finished run" $?
fi

echo
echo "=== healthcheck ==="
HS=$(docker inspect sada-test --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' 2>/dev/null)
[ "$HS" = "healthy" ] || [ "$HS" = "starting" ]
check "the healthcheck is wired up" $? "status: $HS"

echo
echo "=== one-shot CLI, no service ==="
rm -rf out/cli-check 2>/dev/null
docker run --rm \
  -v "$HOST/models:/models" -v "$HOST/audio:/audio:ro" -v "$HOST/out:/out" \
  -e OMP_NUM_THREADS=6 "$IMAGE" \
  python -m engine.transcribe "/audio/$AUDIO_FILE" --out /out/cli-check   >/dev/null 2>&1
check "the CLI transcribes and exits cleanly" $?
ls "out/cli-check/${AUDIO_FILE%.*}/${AUDIO_FILE%.*}.txt" >/dev/null 2>&1
check "the CLI writes to the mounted volume" $?   "out/cli-check/${AUDIO_FILE%.*}/"

echo
echo "============================================================"
if [ "$FAIL" -gt 0 ]; then
  echo "FAILED: $FAIL   passed: $PASS"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
echo "all $PASS container checks passed"
echo "============================================================"
