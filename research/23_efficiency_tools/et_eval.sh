#!/usr/bin/env bash
# Run a command in the s15 evaluation image for topic 23: topic 22's wb_eval.sh with its paths and placement changed.
#   - the whole repo is mounted at the same path (topic 23's jobs and vendor copy and topic 22's task links live
#     outside harness_eval, and Harbor's compose bind mounts must resolve inside the docker-in-docker daemon, which
#     mounts the same repo);
#   - PYTHONPATH puts topic 23's agent module (et_agent) first, then topic 22's (wb_agent, wb_cap, wb_tasks), then the
#     image's adapter; S15_VENDOR points at topic 23's pinned copy of today's s15;
#   - the container runs on socket 1, next to GPUs 4-5 (the user, 2026-11-02): the eval-container CPU set and NUMA
#     node 1's memory, with no memory cap ("unlimited DRAM").
# Harbor starts task containers on the docker-in-docker daemon (S15_DOCKER_SOCK, harness_eval run/dind.md).
set -euo pipefail
REPO=/data/fritz/learn-claude-code
ET=$REPO/research/23_efficiency_tools
WB=$REPO/research/22_wait_bench
TAG=${S15_EVAL_TAG:-b4e57657ba6d}            # rebuilt 2026-11-02 by harness_eval's docker/build.sh, same inputs as topic 22
NAME=${ET_EVAL_NAME:-eteval-$(date +%Y%m%d%H%M%S)-$$}
SOCK=${S15_DOCKER_SOCK:-/data/fritz/dind-run/docker.sock}
CPUS=${ET_EVAL_CPUS:-64-71,168-175}
exec env -u DOCKER_HOST docker run --rm --network host --name "$NAME" --cpuset-cpus "$CPUS" --cpuset-mems 1 \
  --user "$(id -u):$(stat -c %g "$SOCK")" \
  -e HOME="$ET/home" -e TERM=dumb -e PYTHONPATH="$ET:$WB:/opt/harness_eval" -e S15_VENDOR="$ET/vendor/s15" \
  -v "$SOCK":/var/run/docker.sock -v "$REPO:$REPO" -w "$ET" \
  "s15-eval:$TAG" "$@"
