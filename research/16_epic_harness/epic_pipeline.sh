#!/usr/bin/env bash
# Stage runner of research/16_epic_harness on ONE GPU, with guaranteed engine shutdown.
#
#   research/16_epic_harness/epic_pipeline.sh <stage> [args...]
#     e0            E-0: reuse of the original system (A/B/C) + template-shift KL + report
#     selftest      offline GPU selftests of the EPIC connector
#     sweep SCHED OUT [extra epic_sweep args]   live runs of a schedule
#     --dry-run     print what would run
#
# Every stage runs in its own process group; a trap on EXIT/INT/TERM/HUP kills the group (TERM, 30 s,
# KILL) and then waits until the pinned GPU has no compute process. The Python drivers stop their own
# engines the same way (epic_common.served / run_isolated); this is the outer belt.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PY=/data/fritz/venv/bin/python
HERE="$REPO/research/16_epic_harness"
export HF_HOME=/data/shared/huggingface HF_HUB_OFFLINE=1 TOKENIZERS_PARALLELISM=false
cd "$REPO"

if [[ "${1:-}" == "--dry-run" ]]; then
  echo "stages: e0 | selftest | sweep SCHED OUT; GPU picked by epic_common.pick_gpu (EPIC_GPU pins it)"
  "$PY" "$HERE/epic_common.py"
  exit 0
fi

GPU="${EPIC_GPU:-$("$PY" -c 'import sys; sys.path.insert(0, "research/16_epic_harness"); import epic_common as ec; print(ec.pick_gpu())')}"
export EPIC_GPU="$GPU"
CHILD=""
cleanup() {
  local code=$?
  if [[ -n "$CHILD" ]] && kill -0 "$CHILD" 2>/dev/null; then
    kill -TERM -- "-$CHILD" 2>/dev/null
    for _ in $(seq 30); do kill -0 "$CHILD" 2>/dev/null || break; sleep 1; done
    kill -KILL -- "-$CHILD" 2>/dev/null
  fi
  for _ in $(seq 90); do
    apps=$(nvidia-smi --id="$GPU" --query-compute-apps=pid --format=csv,noheader | tr -d ' ')
    [[ -z "$apps" ]] && break
    sleep 2
  done
  echo "[pipeline] exit $code; GPU $GPU: $(nvidia-smi --id="$GPU" --query-gpu=memory.used --format=csv,noheader) used, apps='${apps:-}'"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

run() {                                   # run one stage in its own process group and wait for it
  setsid "$@" &
  CHILD=$!
  wait "$CHILD"
  local code=$?
  CHILD=""
  return $code
}

stage="${1:-}"; shift || true
case "$stage" in
  e0)
    run "$PY" "$HERE/epic_reuse.py" run && run "$PY" "$HERE/epic_reuse.py" kl && "$PY" "$HERE/epic_reuse.py" report ;;
  selftest)
    run "$PY" "$HERE/epic_selftest.py" ;;
  sweep)
    sched="$1"; out="$2"; shift 2
    run "$PY" "$HERE/epic_sweep.py" --schedule "$sched" --out "$out" "$@" ;;
  *)
    echo "usage: $0 e0|selftest|sweep SCHED OUT [args]|--dry-run" >&2; exit 2 ;;
esac
