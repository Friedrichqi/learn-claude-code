#!/usr/bin/env bash
# Run a command in the s15 evaluation image for topic 22: harness_eval's run/eval.sh with three changes.
#   - the whole repo is mounted at the same path (topic 22's task links, jobs and vendor copy live outside
#     harness_eval, and Harbor's compose bind mounts must resolve inside the docker-in-docker daemon, which mounts
#     the same repo);
#   - PYTHONPATH puts topic 22's agent module (wb_agent) before the image's adapter, and S15_VENDOR points at topic
#     22's pinned copy of today's s15 (with hold_runtime.py);
#   - the container runs on CPU socket 0's cores 0-51 with NUMA node 0's memory, next to GPUs 0-3, and at most 100 GB
#     (the user, 2026-11-01; socket 1 belongs to other users).
# Harbor starts task containers on the docker-in-docker daemon (S15_DOCKER_SOCK, run/dind.md).
set -euo pipefail
REPO=/data/fritz/learn-claude-code
HE=$REPO/research/16_epic_harness/harness_eval
WB=$REPO/research/22_wait_bench
# built 2026-11-01 05:11 UTC by harness_eval's docker/build.sh from its current inputs (topic 16's own CURRENT_TAG,
# 15b24dda658e, is left as it ran)
TAG=${S15_EVAL_TAG:-b4e57657ba6d}
NAME=${WB_EVAL_NAME:-wbeval-$(date +%Y%m%d%H%M%S)-$$}
SOCK=${S15_DOCKER_SOCK:-/data/fritz/dind-run/docker.sock}
exec env -u DOCKER_HOST docker run --rm --network host --name "$NAME" --cpuset-cpus 0-51 --cpuset-mems 0 --memory 100g \
  --user "$(id -u):$(stat -c %g "$SOCK")" \
  -e HOME="$WB/home" -e TERM=dumb -e PYTHONPATH="$WB:/opt/harness_eval" -e S15_VENDOR="$WB/vendor/s15" \
  -v "$SOCK":/var/run/docker.sock -v "$REPO:$REPO" -w "$WB" \
  "s15-eval:$TAG" "$@"
