#!/bin/bash
set -eo pipefail

cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
[ -z "$(git status --porcelain)" ] || { echo "Commit or stash your changes first." >&2; exit 1; }
echo "Building $(git rev-parse --short HEAD) ($(git log -1 --format=%s))"

docker build -t wisnia74/moonlight-tizen --build-arg BUILD_TYPE=development \
  --build-arg REPO_OWNER=wisnia74 --build-arg REPO_NAME=moonlight-tizen .

mkdir -p artifacts
docker rm -f moonlight-tizen-extract >/dev/null 2>&1 || true
docker create --name moonlight-tizen-extract wisnia74/moonlight-tizen >/dev/null
docker cp moonlight-tizen-extract:/home/moonlight/Moonlight.wgt artifacts/
docker rm moonlight-tizen-extract >/dev/null
ls -l artifacts/Moonlight.wgt
