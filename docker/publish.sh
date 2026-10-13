#!/usr/bin/env bash
# Build, tag and publish the Sada engine image to Docker Hub.
#
# Run this yourself: it authenticates as you, and the token is read from a prompt
# or an environment variable rather than from a command-line argument.
#
#   bash docker/publish.sh                    # prompts for the token
#   DOCKERHUB_TOKEN=... bash docker/publish.sh    # for CI
#
# Why not `docker login -u user -p token`: anything passed as an argument lands
# in your shell history and is visible in the process list to every other
# process on the machine. `--password-stdin` avoids both, and Docker itself warns
# about -p for exactly this reason.
#
# There is no separate "create the repository" step: the first push creates it.
# It is created PUBLIC. To publish privately, create the repository in the Docker
# Hub web interface first and set it to private, then push.

set -euo pipefail
cd "$(dirname "$0")/.."

USER_NAME="${DOCKERHUB_USER:-mohammedkhaledmahmoud}"
IMAGE="${DOCKERHUB_IMAGE:-sada}"
VERSION="${VERSION:-1.0.0}"
REPO="${USER_NAME}/${IMAGE}"

echo "repository : ${REPO}"
echo "tags       : ${VERSION}, latest"
echo

# ---------------------------------------------------------------- build
echo "==> building"
docker build --build-arg "VERSION=${VERSION}" -t "${REPO}:${VERSION}" .
docker tag "${REPO}:${VERSION}" "${REPO}:latest"

SIZE=$(docker image inspect "${REPO}:${VERSION}" --format '{{.Size}}')
echo "    image size: $((SIZE / 1000000)) MB"

# ---------------------------------------------------------------- verify
# Never push an image that has not been exercised. This is the same suite that
# tests the local build, pointed at the tagged one.
echo
echo "==> verifying the image before publishing"
if ! IMAGE_TAG="${REPO}:${VERSION}" bash tests/test_docker.sh; then
    echo
    echo "the image failed its own tests; nothing was pushed" >&2
    exit 1
fi

# ---------------------------------------------------------------- login
echo
if [ -n "$(docker info --format '{{.Username}}' 2>/dev/null)" ]; then
    echo "==> already signed in to Docker Hub as $(docker info --format '{{.Username}}')"
elif [ -n "${DOCKERHUB_TOKEN:-}" ]; then
    echo "==> signing in to Docker Hub as ${USER_NAME}"
    printf '%s' "${DOCKERHUB_TOKEN}" | docker login -u "${USER_NAME}" --password-stdin
else
    echo "==> signing in to Docker Hub as ${USER_NAME}"
    # No -p: Docker prompts and hides the input itself. Passing a token as an
    # argument puts it in the shell history and the process list.
    docker login -u "${USER_NAME}"
fi

# ---------------------------------------------------------------- push
echo
echo "==> pushing"
docker push "${REPO}:${VERSION}"
docker push "${REPO}:latest"

echo
echo "==> published"
echo "    https://hub.docker.com/r/${REPO}"
echo
echo "Anyone can now run it with:"
echo "    docker pull ${REPO}:latest"
echo "    docker run --rm -v \$PWD/models:/models ${REPO}:latest python -m engine.fetch turbo"
echo
echo "Sign out of this machine when you are done:  docker logout"
