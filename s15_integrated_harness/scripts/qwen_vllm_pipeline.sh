#!/usr/bin/env bash
# End-to-end rerun of the latency-breakdown experiment (scripts/latency_workloads.py) against a LOCAL vLLM
# server serving Qwen3.8-27B, followed by the tables and the GLM-vs-Qwen comparison:
#
#   env -> download -> serve -> lane -> probe -> smoke -> matrix -> tables -> compare -> stop
#
# Every step is idempotent (skips work whose output exists), so the pipeline is resumable: re-run it after a
# crash or a Slurm time limit and it continues where it stopped.  Meant to run inside one Slurm job
# (scripts/qwen_vllm.sbatch) but works in any shell that owns a free GPU.
#
#   bash s15_integrated_harness/scripts/qwen_vllm_pipeline.sh              # all steps
#   bash s15_integrated_harness/scripts/qwen_vllm_pipeline.sh serve probe  # selected steps (env is implicit)
#
# Environment overrides (defaults in brackets):
#   MODEL [Qwen/Qwen3.8-27B]   OUT [<repo>/s15_integrated_harness/traces/latency_profiling_qwen]
#   LANE [~/lanes/latency_qwen]  PORT [free port, persisted in $OUT/pipeline/port]
#   TEAM_REPS [r1 r2 r3 r4 r5]  SOLO_REPS [r1 r2 r3]  CATEGORIES [FQA CODE MATH]
#   MAX_SECONDS [3600]  QUIET_SECONDS [30]  PAUSE [5]
#   GPU_UTIL [0.90]  MAXLEN [262144 on >= 90 GiB cards, else 131072]  MAX_NUM_SEQS [32]  MAX_BATCHED [16384]
#   EXTRA_VLLM_ARGS []          e.g. --default-chat-template-kwargs '{"reasoning_effort":"low"}' for a second arm
#   WAIT_READY [1]              wait for $OUT/pipeline/READY before copying the lane (code may still be edited
#                               while the job queues and the server compiles); READY_WAIT_MAX [7200] seconds
#   HEALTH_WAIT [1500]          seconds to wait for /health on a cold start (torch.compile + CUDA graphs)
#   SMOKE [1]                   run the FQA smoke gate before the matrix;  SMOKE_MIN_COVERAGE [0.7]
#   KEEP_SERVER [0]             leave the vLLM server running at exit (manual probing)
#   HF_HOME [/projects/co/jlin4/agenticllm/hf_cache]  VENV [~/.venv]
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MODEL="${MODEL:-Qwen/Qwen3.8-27B}"
OUT="${OUT:-$REPO/s15_integrated_harness/traces/latency_profiling_qwen}"
LANE="${LANE:-$HOME/lanes/latency_qwen}"
PORT="${PORT:-}"
TEAM_REPS="${TEAM_REPS:-r1 r2 r3 r4 r5}"
SOLO_REPS="${SOLO_REPS:-r1 r2 r3}"
CATEGORIES="${CATEGORIES:-FQA CODE MATH}"
MAX_SECONDS="${MAX_SECONDS:-3600}"
QUIET_SECONDS="${QUIET_SECONDS:-30}"
PAUSE="${PAUSE:-5}"
GPU_UTIL="${GPU_UTIL:-0.90}"
MAXLEN="${MAXLEN:-}"
MAX_NUM_SEQS="${MAX_NUM_SEQS:-32}"
MAX_BATCHED="${MAX_BATCHED:-16384}"
EXTRA_VLLM_ARGS="${EXTRA_VLLM_ARGS:-}"
WAIT_READY="${WAIT_READY:-1}"
READY_WAIT_MAX="${READY_WAIT_MAX:-7200}"
HEALTH_WAIT="${HEALTH_WAIT:-1500}"
SMOKE="${SMOKE:-1}"
SMOKE_MIN_COVERAGE="${SMOKE_MIN_COVERAGE:-0.7}"
KEEP_SERVER="${KEEP_SERVER:-0}"
VENV="${VENV:-$HOME/.venv}"
export HF_HOME="${HF_HOME:-/projects/co/jlin4/agenticllm/hf_cache}"
export HF_HUB_ENABLE_HF_TRANSFER=0 TOKENIZERS_PARALLELISM=false

PIPE="$OUT/pipeline"
mkdir -p "$PIPE" "$OUT/smoke"
LOG="$PIPE/pipeline_$(date +%Y%m%dT%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { log "FATAL: $*"; exit 1; }

STOP_REQUESTED=0
CHILD_PID=""
on_term() {
  log "TERM/INT received: stopping after the current run (child ${CHILD_PID:-none} gets INT), then tables"
  STOP_REQUESTED=1
  if [ -n "$CHILD_PID" ] && kill -0 "$CHILD_PID" 2>/dev/null; then kill -INT "$CHILD_PID" 2>/dev/null || true; fi
}
trap on_term TERM INT

BASE_URL=""
VLLM_LOG=""

port_free() { python3 - "$1" <<'EOF'
import socket, sys
s = socket.socket()
try:
    s.bind(("127.0.0.1", int(sys.argv[1]))); s.close(); sys.exit(0)
except OSError:
    sys.exit(1)
EOF
}

# -------------------------------------------------------------------------------------------- env
step_env() {
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  if [ -z "$PORT" ] && [ -f "$PIPE/port" ]; then PORT=$(cat "$PIPE/port"); fi
  if [ -z "$PORT" ] || { ! curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1 && ! port_free "$PORT"; }; then
    PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
  fi
  echo "$PORT" > "$PIPE/port"
  BASE_URL="http://127.0.0.1:$PORT"
  local gpu_mem gpu_name
  gpu_mem=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ' || echo 0)
  gpu_name=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo unknown)
  if [ -z "$MAXLEN" ]; then
    if [ "${gpu_mem:-0}" -ge 90000 ]; then MAXLEN=262144; else MAXLEN=131072; fi
  fi
  python3 - "$PIPE/meta.json" <<EOF
import json, os, platform, subprocess, sys, time
meta = {
  "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "host": platform.node(),
  "slurm_job_id": os.environ.get("SLURM_JOB_ID"), "slurm_cpus": os.environ.get("SLURM_CPUS_PER_TASK"),
  "gpu_name": """$gpu_name""".strip(), "gpu_mem_mib": int("${gpu_mem:-0}" or 0), "nproc": os.cpu_count(),
  "model": "$MODEL", "port": $PORT, "max_model_len": $MAXLEN, "gpu_util": $GPU_UTIL,
  "max_num_seqs": $MAX_NUM_SEQS, "max_num_batched_tokens": $MAX_BATCHED, "extra_vllm_args": """$EXTRA_VLLM_ARGS""",
  "team_reps": "$TEAM_REPS", "solo_reps": "$SOLO_REPS", "categories": "$CATEGORIES", "max_seconds": $MAX_SECONDS,
  "lane": "$LANE", "out": "$OUT", "hf_home": os.environ.get("HF_HOME"),
}
for mod in ("vllm", "torch", "transformers", "anthropic"):
    try:
        meta[mod] = __import__(mod).__version__
    except Exception as exc:
        meta[mod] = f"unavailable: {exc}"
try:
    meta["git_head"] = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd="$REPO").stdout.strip()
except Exception:
    meta["git_head"] = None
json.dump(meta, open(sys.argv[1], "w"), indent=1)
print(json.dumps(meta))
EOF
  log "env: repo=$REPO out=$OUT lane=$LANE base_url=$BASE_URL gpu='$gpu_name' ${gpu_mem}MiB maxlen=$MAXLEN"
}

# --------------------------------------------------------------------------------------- download
step_download() {
  log "download: ensuring $MODEL is complete in $HF_HOME (idempotent)"
  local i
  for i in 1 2 3; do
    if python3 -c "from huggingface_hub import snapshot_download; print(snapshot_download('$MODEL', max_workers=8))"; then
      return 0
    fi
    log "download: attempt $i failed; retrying in 30 s"; sleep 30
  done
  die "model download failed"
}

# ------------------------------------------------------------------------------------------ serve
server_alive() { curl -sf "$BASE_URL/health" >/dev/null 2>&1; }

start_vllm() {
  if server_alive; then log "serve: a server already answers on $BASE_URL, reusing it"; return 0; fi
  local ts; ts=$(date +%Y%m%dT%H%M%S)
  VLLM_LOG="$OUT/vllm_server_$ts.log"
  log "serve: starting vLLM $MODEL on $BASE_URL (max-model-len $MAXLEN, util $GPU_UTIL, seqs $MAX_NUM_SEQS, batched $MAX_BATCHED) -> $VLLM_LOG"
  # A new session so the whole tree (API server + EngineCore) can be stopped by process group.
  # shellcheck disable=SC2086
  setsid bash -c 'echo $$ > "$1"; shift; exec "$@"' _ "$PIPE/vllm.pid" \
      vllm serve "$MODEL" --host 127.0.0.1 --port "$PORT" --served-model-name "$MODEL" \
      --language-model-only \
      --reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser qwen3_coder \
      --enable-prompt-tokens-details --enable-prefix-caching \
      --max-model-len "$MAXLEN" --gpu-memory-utilization "$GPU_UTIL" \
      --max-num-seqs "$MAX_NUM_SEQS" --max-num-batched-tokens "$MAX_BATCHED" \
      --uvicorn-log-level warning $EXTRA_VLLM_ARGS > "$VLLM_LOG" 2>&1 &
  sleep 3
  local pid; pid=$(cat "$PIPE/vllm.pid" 2>/dev/null || echo "")
  ln -sfn "$VLLM_LOG" "$OUT/vllm_server.log"
  local waited=0
  until server_alive; do
    if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then tail -60 "$VLLM_LOG"; die "vLLM exited during startup (see $VLLM_LOG)"; fi
    if [ "$waited" -ge "$HEALTH_WAIT" ]; then tail -60 "$VLLM_LOG"; die "vLLM not healthy after ${HEALTH_WAIT}s"; fi
    sleep 10; waited=$((waited + 10))
  done
  log "serve: healthy after ${waited}s (pid $pid)"
  {
    echo "# $(date -Is) pid=$pid log=$VLLM_LOG"
    grep -E "KV cache size|Maximum concurrency|block size|[Pp]refix cach|Loading weights took|Model loading took|Graph capturing finished|torch.compile|Available KV cache memory|backend|Chunked prefill|max_num_batched_tokens|mamba" "$VLLM_LOG" | head -60
  } >> "$PIPE/server_startup.txt"
  python3 - "$BASE_URL" "$PIPE/server_info.json" "$MODEL" <<'EOF'
import json, sys, urllib.request
base, out, model = sys.argv[1:4]
def get(path):
    try:
        with urllib.request.urlopen(base + path, timeout=15) as r:
            return r.read().decode()
    except Exception as exc:
        return f"ERROR {type(exc).__name__}: {exc}"
info = {"base_url": base, "model": model}
for key, path in (("version", "/version"), ("models", "/v1/models")):
    raw = get(path)
    try:
        info[key] = json.loads(raw)
    except Exception:
        info[key] = raw
metrics = get("/metrics")
info["metrics_families"] = sorted({l.split("{")[0].split(" ")[0] for l in metrics.splitlines() if l.startswith("vllm:")})
json.dump(info, open(out, "w"), indent=1)
print("server:", json.dumps(info.get("version")), "| metric families:", len(info["metrics_families"]))
EOF
  echo "$(date -Is) started pid=$pid log=$VLLM_LOG" >> "$PIPE/restarts.log"
}

stop_vllm() {
  local pid; pid=$(cat "$PIPE/vllm.pid" 2>/dev/null || true)
  [ -n "$pid" ] || return 0
  if kill -0 "$pid" 2>/dev/null; then
    log "stop: terminating vLLM (session $pid)"
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    local i
    for i in $(seq 1 30); do kill -0 "$pid" 2>/dev/null || break; sleep 2; done
    kill -KILL -- "-$pid" 2>/dev/null || true
  fi
  rm -f "$PIPE/vllm.pid"
}

ensure_vllm() {
  if server_alive; then return 0; fi
  log "server not healthy on $BASE_URL: (re)starting"
  echo "$(date -Is) restart: server not healthy" >> "$PIPE/restarts.log"
  stop_vllm || true
  start_vllm
}

# ------------------------------------------------------------------------------------------- lane
step_lane() {
  if [ "$WAIT_READY" = 1 ]; then
    local waited=0
    while [ ! -f "$PIPE/READY" ]; do
      [ "$waited" -ge "$READY_WAIT_MAX" ] && die "READY marker $PIPE/READY not found after ${READY_WAIT_MAX}s"
      if [ $((waited % 300)) -eq 0 ]; then log "lane: waiting for $PIPE/READY (touch it when the code edits are final)"; fi
      sleep 30; waited=$((waited + 30))
    done
  fi
  log "lane: rsync $REPO/ -> $LANE/"
  mkdir -p "$LANE"
  rsync -a --delete --exclude .git --exclude 's15_integrated_harness/traces' --exclude profiling_sandbox \
        --exclude __pycache__ --exclude '.memory' --exclude '.tasks*' --exclude .mailboxes --exclude .transcripts \
        --exclude .task_outputs --exclude .worktrees --exclude '.scheduled_tasks.json' "$REPO/" "$LANE/"
  cat > "$LANE/.env" <<EOF
ANTHROPIC_API_KEY=EMPTY
ANTHROPIC_BASE_URL=$BASE_URL
MODEL_ID=$MODEL
EOF
  log "lane: .env -> $BASE_URL $MODEL"
}

# ------------------------------------------------------------------------------------------ probe
step_probe() {
  if [ -s "$OUT/provider_probe.json" ]; then log "probe: $OUT/provider_probe.json exists, skipping"; return 0; fi
  ensure_vllm
  log "probe: provider_probe.py --all (prefill sweep, cached resends, decode rate, cache semantics)"
  if (cd "$LANE" && python3 s15_integrated_harness/scripts/provider_probe.py --all --json "$OUT/provider_probe.json") > "$OUT/provider_probe.log" 2>&1; then
    tail -25 "$OUT/provider_probe.log"
  else
    log "probe: FAILED (see $OUT/provider_probe.log); continuing"
  fi
}

# ------------------------------------------------------------------------------------- workloads
run_workload() {   # rep mode category trace_dir
  local rep=$1 mode=$2 cat=$3 tdir=$4 rc=0
  ensure_vllm
  log "run: $cat-$mode-$rep -> $tdir"
  python3 "$REPO/s15_integrated_harness/scripts/latency_workloads.py" --rep "$rep" --mode "$mode" --only "$cat" \
      --repo "$LANE" --trace-dir "$tdir" --max-seconds "$MAX_SECONDS" --quiet-seconds "$QUIET_SECONDS" \
      --pause "$PAUSE" --skip-existing \
      --driver-arg="--vllm-metrics=$BASE_URL/metrics" --driver-arg=--client-max-retries=0 --driver-arg=--server-info &
  CHILD_PID=$!
  wait "$CHILD_PID" || rc=$?
  CHILD_PID=""
  return $rc
}

step_smoke() {
  [ "$SMOKE" = 1 ] || { log "smoke: disabled"; return 0; }
  if [ -f "$PIPE/smoke.ok" ]; then log "smoke: already passed ($PIPE/smoke.ok)"; return 0; fi
  log "smoke: one FQA team run as the gate before the matrix"
  run_workload smoke team FQA "$OUT/smoke" || log "smoke: workload runner exited nonzero"
  python3 "$REPO/s15_integrated_harness/scripts/smoke_check.py" "$OUT/smoke" FQA-team-smoke --min-coverage "$SMOKE_MIN_COVERAGE" \
      || die "smoke gate failed; not starting the matrix (inspect $OUT/smoke)"
  touch "$PIPE/smoke.ok"
}

step_matrix() {
  local rep cat
  for rep in $TEAM_REPS; do
    for cat in $CATEGORIES; do
      [ "$STOP_REQUESTED" = 1 ] && { log "matrix: stop requested"; return 0; }
      run_workload "$rep" team "$cat" "$OUT" || log "matrix: $cat-team-$rep runner exited nonzero"
    done
  done
  for rep in $SOLO_REPS; do
    for cat in $CATEGORIES; do
      [ "$STOP_REQUESTED" = 1 ] && { log "matrix: stop requested"; return 0; }
      run_workload "$rep" solo "$cat" "$OUT" || log "matrix: $cat-solo-$rep runner exited nonzero"
    done
  done
  log "matrix: done ($(ls "$OUT"/*.score.json 2>/dev/null | wc -l) scored runs)"
}

# ----------------------------------------------------------------------------------------- tables
step_tables() {
  log "tables: latency_breakdown.py $OUT"
  python3 "$REPO/s15_integrated_harness/scripts/latency_breakdown.py" "$OUT" --per-run --tools \
      --json "$OUT/latency_tables.json" > "$OUT/latency_tables.md" || log "tables: FAILED"
  log "tables: $(wc -l < "$OUT/latency_tables.md") lines -> $OUT/latency_tables.md"
}

step_compare() {
  log "compare: latency_compare.py (GLM traces vs $OUT)"
  python3 "$REPO/s15_integrated_harness/scripts/latency_compare.py" \
      --a "$REPO/s15_integrated_harness/traces/latency_profiling" --a-name glm-5.3-flash \
      --b "$OUT" --b-name "$MODEL" \
      --out "$OUT/compare_glm_vs_qwen.md" --json "$OUT/compare_glm_vs_qwen.json" || log "compare: FAILED"
}

# ------------------------------------------------------------------------------------------- main
STEPS=("$@")
if [ ${#STEPS[@]} -eq 0 ]; then STEPS=(download serve lane probe smoke matrix tables compare stop); fi
trap 'if [ "$KEEP_SERVER" != 1 ]; then stop_vllm; fi' EXIT
step_env
for s in "${STEPS[@]}"; do
  case "$s" in
    env) ;;
    download) step_download ;;
    serve) start_vllm ;;
    lane) step_lane ;;
    probe) step_probe ;;
    smoke) step_smoke ;;
    matrix) step_matrix ;;
    tables) step_tables ;;
    compare) step_compare ;;
    stop) stop_vllm ;;
    *) die "unknown step '$s' (env download serve lane probe smoke matrix tables compare stop)" ;;
  esac
done
log "pipeline finished (steps: ${STEPS[*]})"
