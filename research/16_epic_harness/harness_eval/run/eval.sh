#!/usr/bin/env bash
# Run a command inside the s15 evaluation image: host network (vLLM on 127.0.0.1), the Docker socket (Harbor
# starts task containers on the host daemon), and the project dir mounted at the same absolute path so that
# Harbor's compose bind mounts resolve on the host. Containers are named s15eval-* so they are easy to find.
set -euo pipefail
ROOT=/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval
TAG=${S15_EVAL_TAG:-$(cat "$ROOT/docker/CURRENT_TAG")}
NAME=${S15_EVAL_NAME:-s15eval-$(date +%Y%m%d%H%M%S)-$$}
# S15_DOCKER_SOCK: the daemon Harbor starts task containers on (default: the host daemon; for Docker-in-Docker
# mode /data/fritz/dind-run/docker.sock, see run/dind.md). The eval container itself always runs on the host
# daemon, where the s15-eval image is.
SOCK=${S15_DOCKER_SOCK:-/var/run/docker.sock}
ENVARGS=()
[ -n "${S15_ENV_FILE:-}" ] && ENVARGS+=(-e "S15_ENV_FILE=$S15_ENV_FILE")
exec env -u DOCKER_HOST docker run --rm --network host --name "$NAME" \
  --user "$(id -u):$(stat -c %g "$SOCK")" \
  -e HOME="$ROOT/home" -e TERM=dumb "${ENVARGS[@]}" \
  -v "$SOCK":/var/run/docker.sock -v "$ROOT:$ROOT" -w "$ROOT" \
  "s15-eval:$TAG" "$@"
