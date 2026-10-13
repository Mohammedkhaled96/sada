# Sada - the transcription engine as a container.
#
# WHAT THIS IMAGE IS NOT: it is not the desktop application. The Word
# integration needs Microsoft Word over COM, the interface needs wxPython and a
# display, and the shortcuts need Windows global hotkeys. None of that can run
# in a Linux container, and pretending otherwise would be dishonest.
#
# WHAT IT IS: the engine and its HTTP API, so transcription can run on a server,
# in CI, or on a machine other than the one holding Word. The desktop app stays
# a Windows program; this is for everything else.
#
# Build:  docker build -t sada .
# Run:    docker compose up
FROM python:3.12-slim AS base

# ffmpeg does all audio and video decoding. engine/audio.py calls it directly
# rather than going through PyAV, whose API broke the decoder faster-whisper
# uses - see docs/accuracy-notes.md.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1 \
    SADA_MODELS_DIR=/models \
    SADA_OUT_DIR=/out \
    SADA_HOST=0.0.0.0

WORKDIR /app

# torch comes from the CPU wheel index on purpose. The default PyPI build bundles
# CUDA and adds several gigabytes that a CPU-only image can never use.
#
# triton is then removed. It is a torch dependency on Linux, it weighs 897 MB
# installed - more than torch itself - and it exists only to serve
# `torch.compile`, which Whisper never calls. It has to go in the SAME layer as
# the install: Docker layers are additive, so deleting it later would leave the
# files in the image and save nothing.
COPY docker/requirements.txt ./docker/requirements.txt

# A BuildKit cache mount rather than --no-cache-dir: the cache lives outside the
# image, so nothing is added to it, and a build that dies partway (torch is a
# 196 MB download and these indexes time out on a slow line) resumes from what it
# already fetched instead of starting the whole download again.
#
# Separate statements under `set -eu`, not a long && chain: a chain ending in
# `|| true` makes an EARLIER failure look like success, and that is exactly what
# happened here - a torch download timeout was swallowed and only caught by the
# import check at the end.
RUN --mount=type=cache,target=/root/.cache/pip \
    set -eux; \
    pip install --retries 10 --timeout 120 \
        torch --index-url https://download.pytorch.org/whl/cpu; \
    pip install --retries 10 --timeout 120 -r docker/requirements.txt; \
    pip uninstall -y triton || true; \
    find /usr/local/lib/python3.12/site-packages -name '__pycache__' -type d \
         -prune -exec rm -rf {} + ; \
    python -c "import torch, whisper; print('torch', torch.__version__, 'ok')"; \
    ! python -c "import triton" 2>/dev/null

# Only the engine. app/ is the Windows desktop program and has no place here.
COPY engine/ ./engine/

# Metadata only, so this costs no image size and does not invalidate the pip
# layers above. No licenses label until the project actually has a LICENSE file.
ARG VERSION=1.0.0
LABEL org.opencontainers.image.title="Sada" \
      org.opencontainers.image.description="Local speech-to-text engine and HTTP API, built on Whisper. Runs offline, detects the language automatically, and has no limit on hours or file size. The Microsoft Word integration is a separate Windows desktop program and is not in this image." \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.authors="Mohammed Khaled" \
      org.opencontainers.image.documentation="https://hub.docker.com/r/mohammedkhaledmahmoud/sada"

# Models are mounted, not baked in: large-v3-turbo alone is 1.5 GB, which would
# make the image enormous and the weights impossible to update without a rebuild.
VOLUME ["/models", "/audio", "/out"]
EXPOSE 8765

# Refuse to start silently with no model. A clear message beats a container that
# accepts jobs and fails every one of them.
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
  CMD python -c "import urllib.request,json,sys; \
r=json.load(urllib.request.urlopen('http://127.0.0.1:8765/health',timeout=4)); \
sys.exit(0 if r.get('ready') else 1)"

# Binds 0.0.0.0 because a container is unreachable otherwise; the published port
# and the auth token are what actually control access.
CMD ["python", "-m", "engine.daemon", "--model", "turbo", "--port", "8765", \
     "--host", "0.0.0.0", "--print-token"]
