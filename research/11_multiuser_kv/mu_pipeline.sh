#!/usr/bin/env bash
# One job of the 11_multiuser_kv experiment: two independent cells, one per H200 GPU.
# Each GPU lane runs its conditions in order; per condition:
#   serve    vLLM (TP=1) on this GPU with the condition's flags and the MU scheduler (log on local disk)
#   scrape   mu_scrape.py (1 Hz /metrics + GPU + CPU)
#   sweep    mu_sweep.py (N users, closed loop; resumable) -- or a replay (mu_replay.py, conditions r_*)
#   stop     scraper, then the server's process group; leftover GPU processes are killed by pid
#   store    sched log xz'd onto NFS next to the scrape and the server log
# A sweep that exits 3 (watchdog: stalled server) is restarted once on a fresh server.
# Usage: bash research/11_multiuser_kv/mu_pipeline.sh "<GPU0 conds, comma-separated>" "<GPU1 conds>"
#   e.g. bash research/11_multiuser_kv/mu_pipeline.sh n01a,n04 n01b,n08      ("-" = leave the GPU idle)
set -uo pipefail

LANE0=${1:?GPU0 conditions}; LANE1=${2:-"-"}
REPO=/mnt/home/yqi10/learn-claude-code
DIR=$REPO/research/11_multiuser_kv
DATA=$DIR/data/multiuser_live
PY="$HOME/.venv/bin/python"
VLLM="$HOME/.venv/bin/vllm"
MODEL=Qwen/Qwen3-32B
JOB=${SLURM_JOB_ID:-local$$}
JOB_SECONDS=${JOB_SECONDS:-43200}
DISPATCH_MARGIN=${DISPATCH_MARGIN:-5400}   # no counted session starts in the job's last 90 min
HARD_MARGIN=${HARD_MARGIN:-1200}           # stop everything 20 min before the job ends
LOCAL=${LOCAL_ROOT:-/tmp/$USER/mu/$JOB}
START=$(date +%s)
END=$((START + JOB_SECONDS))
if [ -n "${SLURM_JOB_ID:-}" ]; then     # the job's real end (sbatch --time), not a guess
  slurm_end=$(scontrol show job "$SLURM_JOB_ID" 2>/dev/null | grep -o 'EndTime=[^ ]*' | cut -d= -f2)
  [ -n "$slurm_end" ] && [ "$slurm_end" != "Unknown" ] && END=$(date -d "$slurm_end" +%s)
fi
mkdir -p "$DATA/jobs" "$LOCAL"
JOBLOG=$DATA/jobs/$JOB.log
cd "$REPO" || exit 1
export HF_HOME=/projects/co/jlin4/agenticllm/hf_cache HF_HUB_OFFLINE=1 TOKENIZERS_PARALLELISM=false
export PYTHONUNBUFFERED=1 VLLM_LOGGING_LEVEL=${VLLM_LOGGING_LEVEL:-INFO}

log() { echo "$(date -u +%FT%TZ) job=$JOB $*" | tee -a "$JOBLOG"; }
free_port() {  # a free port whose digits contain neither 429 nor 529 (the harness retries on those substrings)
  "$PY" - <<'EOF'
import socket
while True:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    if "429" not in str(p) and "529" not in str(p):
        print(p); break
EOF
}

# CPU halves: first half of the allowed CPUs for GPU0's cell, second half for GPU1's; in each half the
# first 4 go to vLLM (API server + EngineCore), the rest to the sweep, its sessions and the scraper
read -r CPUS0_V CPUS0_S CPUS1_V CPUS1_S < <("$PY" - <<'EOF'
import os
cpus = sorted(os.sched_getaffinity(0)); h = len(cpus) // 2
halves = [cpus[:h], cpus[h:]]
fmt = lambda xs: ",".join(map(str, xs)) or "0"
print(*[fmt(x) for half in halves for x in (half[:4], half[4:] or half[:4])])
EOF
)

stock_blocks() {  # num_gpu_blocks of a stock (no override) server from earlier cells
  "$PY" - "$DATA" <<'EOF'
import json, sys, glob
best = 0
for p in glob.glob(f"{sys.argv[1]}/cells/*/server/*/server_info.json"):
    info = json.load(open(p))
    if not info.get("override") and info.get("num_gpu_blocks"):
        best = max(best, int(info["num_gpu_blocks"]))
print(best)
EOF
}

run_cond() {  # gpu cond
  local G=$1 C=$2 attempt=${3:-1}
  local CELL=$DATA/cells/$C SRV tag port base vcpus scpus
  if [ -f "$CELL/COMPLETE" ]; then log "g$G $C already complete"; return 0; fi
  tag="${JOB}_g${G}_$(date +%H%M%S)"; SRV=$CELL/server/$tag; mkdir -p "$SRV"
  if [ "$G" = 0 ]; then vcpus=$CPUS0_V; scpus=$CPUS0_S; else vcpus=$CPUS1_V; scpus=$CPUS1_S; fi
  port=$(free_port); base="http://127.0.0.1:$port"
  local args=() envs=() override="" kvport=""
  eval "args=($("$PY" "$DIR/mu_workloads.py" cond "$C" --field vllm))"
  mapfile -t envs < <("$PY" "$DIR/mu_workloads.py" cond "$C" --field env)
  local frac; frac=$("$PY" "$DIR/mu_workloads.py" cond "$C" --field kv_frac)
  if [ -n "$frac" ]; then
    local full; full=$(stock_blocks)
    if [ "$full" -le 0 ]; then log "g$G $C needs a stock server_info first (kv_frac=$frac)"; return 2; fi
    override=$("$PY" -c "print(max(2600, int($full * $frac)))")
    args+=(--num-gpu-blocks-override "$override")
  fi
  if printf '%s\n' "${args[@]}" | grep -q '{kv_events_port}'; then
    kvport=$(free_port)
    for i in "${!args[@]}"; do args[$i]=${args[$i]//\{kv_events_port\}/$kvport}; done
  fi
  log "g$G $C serve on :$port cpus=$vcpus args: ${args[*]} ${envs[*]}"
  env CUDA_VISIBLE_DEVICES=$G PYTHONPATH="$DIR" MU_SCHED_LOG="$LOCAL/$tag.sched.jsonl" "${envs[@]}" \
    setsid taskset -c "$vcpus" "$VLLM" serve "$MODEL" --host 127.0.0.1 --port "$port" \
    --served-model-name "$MODEL" "${args[@]}" > "$SRV/vllm.log" 2>&1 &
  local vpid=$!
  echo "$vpid" > "$SRV/vllm.pid"
  local ok=0
  for _ in $(seq 1 150); do
    if curl -sf "$base/health" >/dev/null; then ok=1; break; fi
    if ! kill -0 "$vpid" 2>/dev/null; then break; fi
    sleep 10
  done
  if [ "$ok" != 1 ]; then log "g$G $C server never became healthy (see $SRV/vllm.log)"; stop_server "$G" "$vpid"; return 1; fi
  "$PY" - "$base" "$SRV/server_info.json" "$G" "$override" "${args[*]}" <<'EOF'
import json, re, subprocess, sys, urllib.request, socket
base, out, gpu, override, argv = sys.argv[1:6]
get = lambda p: urllib.request.urlopen(base + p, timeout=10).read().decode()
info = {"base_url": base, "host": socket.gethostname(), "gpu": int(gpu), "override": override or None,
        "argv": argv, "version": json.loads(get("/version")), "models": json.loads(get("/v1/models"))}
m = get("/metrics")
cc = re.search(r"^vllm:cache_config_info\{([^}]*)\}", m, re.M)
if cc:
    info["cache_config"] = dict(re.findall(r'(\w+)="([^"]*)"', cc.group(1)))
    info["num_gpu_blocks"] = int(info["cache_config"].get("num_gpu_blocks") or 0)
    info["block_size"] = int(info["cache_config"].get("block_size") or 0)
try:
    info["gpu_name"] = subprocess.run(["nvidia-smi", "-i", gpu, "--query-gpu=name,memory.total",
                                       "--format=csv,noheader"], capture_output=True, text=True).stdout.strip()
except Exception:
    pass
json.dump(info, open(out, "w"), indent=2)
print(json.dumps({k: info.get(k) for k in ("num_gpu_blocks", "block_size", "gpu_name")}))
EOF
  local now dispatch hard; now=$(date +%s)
  dispatch=$((END - DISPATCH_MARGIN)); hard=$((END - HARD_MARGIN))
  local kvpid=""
  if [ -n "$kvport" ]; then
    "$PY" "$DIR/mu_scrape.py" kvevents --endpoint "tcp://127.0.0.1:$kvport" --out "$SRV/kv_events.json" &
    kvpid=$!
  fi
  local rc
  if [[ "$C" == r_* ]]; then
    taskset -c "$scpus" "$PY" "$DIR/mu_replay.py" run --cond "$C" --base-url "$base" --out-dir "$SRV" \
      --hard-stop "$hard" >> "$SRV/replay.log" 2>&1 &
  else
    taskset -c "$scpus" "$PY" "$DIR/mu_sweep.py" run --cond "$C" --cell "g$G" --base-url "$base" \
      --lane-root "$LOCAL/g$G" --dispatch-until "$dispatch" --hard-stop "$hard" >> "$CELL/sweep.log" 2>&1 &
  fi
  local wpid=$!
  taskset -c "$scpus" "$PY" "$DIR/mu_scrape.py" run --metrics-url "$base/metrics" --gpu "$G" \
    --out "$SRV/scrape.jsonl" --server-pid "$vpid" --sweep-pid "$wpid" &
  local spid=$!
  echo "$wpid $spid $kvpid" > "$SRV/child.pids"
  wait "$wpid"; rc=$?
  log "g$G $C worker exit $rc"
  kill -TERM "$spid" 2>/dev/null; wait "$spid" 2>/dev/null
  [ -n "$kvpid" ] && { sleep 3; kill -TERM "$kvpid" 2>/dev/null; wait "$kvpid" 2>/dev/null; }
  stop_server "$G" "$vpid"
  if [ -f "$LOCAL/$tag.sched.jsonl" ]; then
    xz -T2 -c "$LOCAL/$tag.sched.jsonl" > "$SRV/sched.jsonl.xz" && rm -f "$LOCAL/$tag.sched.jsonl"
  fi
  if [ "$rc" = 3 ] && [ "$attempt" = 1 ]; then log "g$G $C stalled: restarting once"; run_cond "$G" "$C" 2; return $?; fi
  return "$rc"
}

stop_server() {  # gpu pid: TERM the server's process group, then KILL; then any leftover process on the GPU
  local G=$1 vpid=$2
  kill -TERM -- "-$vpid" 2>/dev/null || kill -TERM "$vpid" 2>/dev/null
  for _ in $(seq 1 30); do kill -0 "$vpid" 2>/dev/null || break; sleep 2; done
  kill -KILL -- "-$vpid" 2>/dev/null
  sleep 3
  local left
  left=$(nvidia-smi -i "$G" --query-compute-apps=pid --format=csv,noheader 2>/dev/null | tr -d ' ')
  for p in $left; do
    if [ "$(ps -o user= -p "$p" 2>/dev/null)" = "$USER" ]; then log "g$G killing leftover GPU process $p"; kill -KILL "$p" 2>/dev/null; fi
  done
}

lane() {  # gpu "c1,c2,..."
  local G=$1 list=$2 C
  [ "$list" = "-" ] && return 0
  IFS=',' read -ra CONDS <<< "$list"
  for C in "${CONDS[@]}"; do
    if [ "$(date +%s)" -gt $((END - DISPATCH_MARGIN)) ]; then log "g$G no time left for $C"; break; fi
    run_cond "$G" "$C"
  done
}

on_term() {
  log "TERM received: stopping both lanes"
  for f in "$DATA"/cells/*/server/"${JOB}"_g*/child.pids; do
    [ -f "$f" ] && for p in $(cat "$f"); do kill -TERM "$p" 2>/dev/null; done
  done
  sleep 120
  for f in "$DATA"/cells/*/server/"${JOB}"_g*/vllm.pid; do
    [ -f "$f" ] && kill -TERM -- "-$(cat "$f")" 2>/dev/null
  done
  exit 143
}
trap on_term TERM INT

log "start: GPU0=[$LANE0] GPU1=[$LANE1] cpus g0=$CPUS0_V|$CPUS0_S g1=$CPUS1_V|$CPUS1_S host=$(hostname)"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader | tee -a "$JOBLOG"
lane 0 "$LANE0" & L0=$!
lane 1 "$LANE1" & L1=$!
wait "$L0"; wait "$L1"
log "done"
