#!/usr/bin/env bash
# Build the s15 evaluation image from the current adapter/vendor/tests and tag it by content: the tag is the
# first 12 hex digits of a SHA-256 over every build input, so the same inputs give the same tag. Writes the tag
# to docker/CURRENT_TAG (run/eval.sh reads it). Builds on the host daemon; layers below the COPY steps are
# reused from earlier builds.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
TAG=$(find docker/Dockerfile.eval docker/bin vendor/s15 adapter tests -type f ! -path '*/__pycache__/*' -print0 \
      | sort -z | xargs -0 sha256sum | sha256sum | cut -c1-12)
if docker image inspect "s15-eval:$TAG" >/dev/null 2>&1; then
  echo "s15-eval:$TAG exists"
else
  docker build -q -f docker/Dockerfile.eval -t "s15-eval:$TAG" .
fi
echo "$TAG" > docker/CURRENT_TAG
echo "CURRENT_TAG=$TAG"
