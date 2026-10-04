#!/usr/bin/env bash
# One job of research/12_hw_utilization on a 2-GPU H200 allocation.
#   bash research/12_hw_utilization/hw_pipeline.sh "<pre>" "<GPU0 lane>" "<GPU1 lane>"
# <pre>    job-wide steps that need both GPUs, run first, in order: probe, or a TP=2 cell (t2, k2t). "-" = none.
# <lane>   comma-separated steps for one GPU, run in order: ab (sampler-overhead A/B, Qwen3-32B replay) or a
#          TP=1 cell of hw_workloads.CELLS (p1a, p1b, p2a, ...). "-" = leave the GPU idle.
#   e.g. hw_pipeline.sh probe ab,p1a p1b          hw_pipeline.sh t2,k2t o1 -
# A cell is one server lifetime (one per level for cells with "restart"): the server is started under its
# cores with the HW scheduler (log on local disk), hw_sampler.py starts with it (so start-up is sampled),
# and each level runs mu_sweep (N agents, think time 0) on the level's agent cores, until its window ends
# (windowed levels) or its sessions are done (Phase 1). The prefix cache is reset between levels.
# Everything is resumable: finished levels carry WINDOW_DONE / COMPLETE, finished cells COMPLETE; an
# interrupted windowed level is moved to aborted_<tag>/ and rerun whole.
set -uo pipefail
{   # the whole script is one compound command: bash parses it all before running, so editing this file
    # while a job runs cannot change the running job (bash otherwise reads scripts as it goes)

PRE=${1:-"-"}; LANE0=${2:-"-"}; LANE1=${3:-"-"}
REPO=/mnt/home/yqi10/learn-claude-code
DIR=$REPO/research/12_hw_utilization
DIR11=$REPO/research/11_multiuser_kv
DATA=${HW_DATA:-$DIR/data}                  # test_hw.py uses a temporary data root
CELLS=$DATA/cells
PY="$HOME/.venv/bin/python"
VLLM=${VLLM_BIN:-$HOME/.venv/bin/vllm}       # test_hw.py points this at fake_vllm.py
JOB=${SLURM_JOB_ID:-local$$}
JOB_SECONDS=${JOB_SECONDS:-43200}
DISPATCH_MARGIN=${DISPATCH_MARGIN:-2400}   # Phase 1: no session starts in the job's last 40 min
HARD_MARGIN=${HARD_MARGIN:-900}            # stop everything 15 min before the job ends
LOCAL=${LOCAL_ROOT:-/tmp/$USER/hw/$JOB}
START=$(date +%s)
END=$((START + JOB_SECONDS))
if [ -n "${SLURM_JOB_ID:-}" ]; then
  slurm_end=$(scontrol show job "$SLURM_JOB_ID" 2>/dev/null | grep -o 'EndTime=[^ ]*' | cut -d= -f2)
  [ -n "$slurm_end" ] && [ "$slurm_end" != "Unknown" ] && END=$(date -d "$slurm_end" +%s)
fi
mkdir -p "$DATA/jobs" "$CELLS" "$LOCAL"
JOBLOG=$DATA/jobs/$JOB.log
cd "$REPO" || exit 1
export HF_HOME=/projects/co/jlin4/agenticllm/hf_cache HF_HUB_OFFLINE=1 TOKENIZERS_PARALLELISM=false
export PYTHONUNBUFFERED=1 VLLM_LOGGING_LEVEL=${VLLM_LOGGING_LEVEL:-INFO} CUDA_DEVICE_ORDER=PCI_BUS_ID
export PATH="$HOME/.venv/bin:$PATH" VIRTUAL_ENV="$HOME/.venv"   # HTEST's `python3 -m pytest` needs the venv (06)
export MU_EXTRA_CONDITIONS=${MU_EXTRA_CONDITIONS:-$DIR/data/conditions.json}
SESSIONS=${HW_SESSIONS:-$DIR/data/sessions.jsonl}

log() { echo "$(date -u +%FT%TZ) job=$JOB $*" | tee -a "$JOBLOG"; }
mark() { printf '{"wall": %s, "ev": "%s"%s}\n' "$(date +%s.%N)" "$2" "${3:+, $3}" >> "$1"; }   # file ev [json fields]
free_port() {  # digits contain neither 429 nor 529 (the harness retries on those substrings)
  "$PY" - <<'EOF'
import socket
while True:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    if "429" not in str(p) and "529" not in str(p):
        print(p); break
EOF
}
put_pid() { echo "$2" > "$1.tmp" && mv -f "$1.tmp" "$1"; }      # atomic: the sampler re-reads it
reset_cache() {  # base: empty vLLM's prefix cache (and a CPU offload tier); it fails while blocks are held, so retry
  local i
  for i in $(seq 1 45); do
    curl -sf -X POST "$1/reset_prefix_cache?reset_external=true" 2>/dev/null | grep -Eq '"success": ?true' && return 0
    sleep 2
  done
  return 1
}

# the job's cores: first half for GPU0's lane, second half for GPU1's, all of them for TP=2 cells
read -r CPUS_ALL CPUS_G0 CPUS_G1 < <("$PY" - <<'EOF'
import os
cpus = sorted(os.sched_getaffinity(0)); h = len(cpus) // 2
fmt = lambda xs: ",".join(map(str, xs))
print(fmt(cpus), fmt(cpus[:h]), fmt(cpus[h:]))
EOF
)

[ -s "$SESSIONS" ] && [ -s "$MU_EXTRA_CONDITIONS" ] || "$PY" "$DIR/hw_workloads.py" build >> "$JOBLOG" 2>&1

stop_server() {  # srvdir gpus: TERM the server's process group, then KILL; then our leftover GPU processes
  local SRV=$1 G=$2 vpid
  vpid=$(cat "$SRV/server.pid" 2>/dev/null) || return 0
  kill -TERM -- "-$vpid" 2>/dev/null || kill -TERM "$vpid" 2>/dev/null
  for _ in $(seq 1 30); do kill -0 "$vpid" 2>/dev/null || break; sleep 2; done
  kill -KILL -- "-$vpid" 2>/dev/null
  sleep 3
  local left p
  for g in ${G//,/ }; do
    left=$(nvidia-smi -i "$g" --query-compute-apps=pid --format=csv,noheader 2>/dev/null | tr -d ' ')
    for p in $left; do
      if [ "$(ps -o user= -p "$p" 2>/dev/null)" = "$USER" ]; then log "gpu $g killing leftover GPU process $p"; kill -KILL "$p" 2>/dev/null; fi
    done
  done
}

new_server_dir() {  # celldir -> prints a fresh server dir with its port chosen ($SRV/port)
  local tag SRV
  tag="${JOB}_$(basename "$1")_$(date +%H%M%S)"; SRV=$1/server/$tag
  mkdir -p "$SRV"; free_port > "$SRV/port"; : > "$SRV/driver.pid"
  echo "$SRV"
}

start_server() {  # cell gpus vcpus srvdir model -> 0 when healthy; writes $SRV/{server.pid,server_info.json}
  local C=$1 G=$2 VC=$3 SRV=$4 MODEL=$5 port base args=() senv=() tag
  tag=$(basename "$SRV")
  port=$(cat "$SRV/port"); base="http://127.0.0.1:$port"
  eval "args=($("$PY" "$DIR/hw_workloads.py" cell "$C" --field vllm_args))"
  read -r -a senv < <("$PY" "$DIR/hw_workloads.py" cell "$C" --field server_env)   # e.g. HW_STACK_MS=20
  log "$C serve $MODEL on :$port gpus=$G cpus=$VC env: ${senv[*]:-} args: ${args[*]}"
  env CUDA_VISIBLE_DEVICES="$G" PYTHONPATH="$DIR:$DIR11" MU_SCHED_LOG="$LOCAL/$tag.sched.jsonl" \
      MU_POOL_WALK_S=2 VLLM_SERVER_DEV_MODE=1 "${senv[@]}" \
    setsid taskset -c "$VC" "$VLLM" serve "$MODEL" --host 127.0.0.1 --port "$port" \
    --served-model-name "$MODEL" "${args[@]}" > "$SRV/vllm.log" 2>&1 &
  local vpid=$!
  put_pid "$SRV/server.pid" "$vpid"
  mark "$SRV/markers.jsonl" launch "\"cpus\": \"$VC\", \"gpus\": \"$G\""
  local ok=0
  for _ in $(seq 1 150); do
    if curl -sf "$base/health" >/dev/null; then ok=1; break; fi
    if ! kill -0 "$vpid" 2>/dev/null; then break; fi
    sleep 10
  done
  if [ "$ok" != 1 ]; then log "$C server never became healthy (see $SRV/vllm.log)"; return 1; fi
  mark "$SRV/markers.jsonl" healthy
  "$PY" - "$base" "$SRV/server_info.json" "$G" "$VC" <<'EOF'
import json, re, subprocess, sys, urllib.request, socket
base, out, gpus, vcpus = sys.argv[1:5]
get = lambda p: urllib.request.urlopen(base + p, timeout=10).read().decode()
info = {"base_url": base, "host": socket.gethostname(), "gpus": gpus, "vllm_cpus": vcpus,
        "version": json.loads(get("/version")), "models": json.loads(get("/v1/models"))}
m = get("/metrics")
cc = re.search(r"^vllm:cache_config_info\{([^}]*)\}", m, re.M)
if cc:
    info["cache_config"] = dict(re.findall(r'(\w+)="([^"]*)"', cc.group(1)))
    info["num_gpu_blocks"] = int(info["cache_config"].get("num_gpu_blocks") or 0)
    info["block_size"] = int(info["cache_config"].get("block_size") or 0)
try:
    info["gpu_name"] = subprocess.run(["nvidia-smi", "-i", gpus, "--query-gpu=name,memory.total,uuid",
                                       "--format=csv,noheader"], capture_output=True, text=True).stdout.strip()
except Exception:
    pass
json.dump(info, open(out, "w"), indent=2)
print(json.dumps({k: info.get(k) for k in ("num_gpu_blocks", "block_size", "gpu_name")}))
EOF
  return 0
}

start_sampler() {  # srvdir gpus_nvml watch_cpus sampler_cpu (before the server: start-up is sampled too)
  local SRV=$1 tag
  tag=$(basename "$SRV")
  taskset -c "$4" "$PY" "$DIR/hw_sampler.py" run --out "$LOCAL/$tag.samp" --gpus "$2" --cpus "$3" \
    --metrics-url "http://127.0.0.1:$(cat "$SRV/port")/metrics" --server-pid-file "$SRV/server.pid" \
    --driver-pid-file "$SRV/driver.pid" > "$SRV/sampler.log" 2>&1 &
  put_pid "$SRV/sampler.pid" "$!"
}

stop_sampler() {  # srvdir
  local spid
  spid=$(cat "$1/sampler.pid" 2>/dev/null)
  if [ -n "$spid" ]; then kill -TERM "$spid" 2>/dev/null; for _ in $(seq 1 20); do kill -0 "$spid" 2>/dev/null || break; sleep 1; done; fi
  rm -f "$1/sampler.pid"
}

store_server() {  # srvdir: stop the sampler, compress the sampler streams and the scheduler log onto NFS
  local SRV=$1 tag f
  tag=$(basename "$SRV")
  stop_sampler "$SRV"
  if [ -d "$LOCAL/$tag.samp" ]; then
    mkdir -p "$SRV/samp"
    cp "$LOCAL/$tag.samp/meta.json" "$SRV/samp/" 2>/dev/null
    for f in "$LOCAL/$tag.samp"/*.jsonl; do [ -f "$f" ] && xz -T2 -c "$f" > "$SRV/samp/$(basename "$f").xz"; done
    rm -rf "$LOCAL/$tag.samp"
  fi
  if [ -f "$LOCAL/$tag.sched.jsonl" ]; then
    xz -T2 -c "$LOCAL/$tag.sched.jsonl" > "$SRV/sched.jsonl.xz" && rm -f "$LOCAL/$tag.sched.jsonl"
  fi
}

run_level() {  # cell level gpus cpus srvdir lanerow -> rc (0 done, 4 incomplete, 3 stalled)
  local C=$1 L=$2 G=$3 CPUS=$4 SRV=$5 GTAG=$6 LV=$CELLS/$1/levels/$2 vc sc ac wc
  local window warmup n now hard dispatch rc
  { read -r vc; read -r sc; read -r ac; read -r wc; } < <("$PY" "$DIR/hw_workloads.py" layout "$C" "$L" "$CPUS")
  window=$("$PY" "$DIR/hw_workloads.py" level "$L" --field window_s)
  warmup=$("$PY" "$DIR/hw_workloads.py" level "$L" --field warmup_s)
  if [ -n "${HW_WINDOW_SCALE:-}" ] && [ "$window" != 0 ]; then         # tests only
    window=$("$PY" -c "print(max(1, int($window * $HW_WINDOW_SCALE)))"); warmup=$("$PY" -c "print(int($warmup * $HW_WINDOW_SCALE))")
  fi
  n=$("$PY" "$DIR/hw_workloads.py" level "$L" --field n)
  if [ -d "$LV" ] && [ "$window" != 0 ]; then mv "$LV" "$CELLS/$C/levels/aborted_${L}_$(date +%H%M%S)"; fi
  mkdir -p "$LV"
  now=$(date +%s)
  if [ "$window" != 0 ]; then
    hard=$((now + warmup + window)); dispatch=$hard
    if [ "$hard" -gt $((END - HARD_MARGIN)) ]; then log "$C/$L: window does not fit in the job"; return 4; fi
  else
    dispatch=$((END - DISPATCH_MARGIN)); hard=$((END - HARD_MARGIN))
  fi
  local base="http://127.0.0.1:$(cat "$SRV/port")"
  if reset_cache "$base"; then log "$C/$L prefix cache reset"; else log "$C/$L prefix cache reset FAILED (continuing)"; fi
  mark "$SRV/markers.jsonl" level_start "\"level\": \"$L\", \"n\": $n, \"window_s\": $window, \"warmup_s\": $warmup, \"hard\": $hard, \"agent_cpus\": \"$ac\", \"vllm_cpus\": \"$vc\""
  log "$C/$L start N=$n window=${window}s agent cpus=$ac (vllm $vc, sampler $sc)"
  taskset -c "$ac" "$PY" "$DIR11/mu_sweep.py" run --cond "$L" --cell "$GTAG" --base-url "$base" \
    --lane-root "$LOCAL/lanes/$C" --sessions "$SESSIONS" --cells-root "$CELLS/$C/levels" --fillers off \
    --think-scale 0 --stagger-s "$warmup" --dispatch-until "$dispatch" --hard-stop "$hard" >> "$LV/sweep.log" 2>&1 &
  local wpid=$!
  put_pid "$SRV/driver.pid" "$wpid"
  wait "$wpid"; rc=$?
  : > "$SRV/driver.pid"
  mark "$SRV/markers.jsonl" level_end "\"level\": \"$L\", \"rc\": $rc"
  log "$C/$L sweep exit $rc"
  if [ "$window" != 0 ]; then
    if [ "$rc" = 4 ] || [ "$rc" = 0 ]; then
      if [ "$(date +%s)" -ge "$hard" ]; then echo "$hard" > "$LV/WINDOW_DONE"; return 0; fi
      log "$C/$L ended before its window ($rc)"; return 4
    fi
    return "$rc"
  fi
  [ "$rc" = 0 ] && echo "$(date +%FT%T)" > "$LV/COMPLETE"
  return "$rc"
}

run_cell() {  # cell gpus cpus gtag
  local C=$1 G=$2 CPUS=$3 GTAG=$4 CD=$CELLS/$1 MODEL restart levels L SRV="" rc=0 started=0
  if [ -f "$CD/COMPLETE" ]; then log "$C already complete"; return 0; fi
  mkdir -p "$CD/server" "$CD/levels"
  MODEL=$("$PY" "$DIR/hw_workloads.py" cell "$C" --field model)
  restart=$("$PY" "$DIR/hw_workloads.py" cell "$C" --field restart)
  read -ra levels <<< "$("$PY" "$DIR/hw_workloads.py" cell "$C" --field levels)"
  for L in "${levels[@]}"; do
    if [ -f "$CD/levels/$L/WINDOW_DONE" ] || [ -f "$CD/levels/$L/COMPLETE" ]; then log "$C/$L already done"; continue; fi
    if [ "$(date +%s)" -gt $((END - HARD_MARGIN - 600)) ]; then log "$C: no time left for $L"; rc=4; break; fi
    if [ "$started" = 1 ] && [ "$restart" = 1 ]; then store_server "$SRV"; stop_server "$SRV" "$G"; started=0; fi
    local attempt
    for attempt in 1 2; do
      if [ "$started" = 0 ]; then
        local vc sc ac wc
        { read -r vc; read -r sc; read -r ac; read -r wc; } < <("$PY" "$DIR/hw_workloads.py" layout "$C" "$L" "$CPUS")
        SRV=$(new_server_dir "$CD")
        start_sampler "$SRV" "$G" "$wc" "$sc"
        if ! start_server "$C" "$G" "$vc" "$SRV" "$MODEL"; then store_server "$SRV"; stop_server "$SRV" "$G"; return 1; fi
        started=1
      fi
      run_level "$C" "$L" "$G" "$CPUS" "$SRV" "$GTAG"; rc=$?
      [ "$rc" = 3 ] || break                                 # stalled server: fresh server, rerun once
      log "$C/$L stalled: restarting the server and rerunning the level"
      store_server "$SRV"; stop_server "$SRV" "$G"; started=0
    done
    if [ "$rc" = 0 ] && [[ "$L" == hw_smoke* ]]; then
      if ! "$PY" "$DIR/hw_analyze.py" smoke --server "$SRV" --samp "$LOCAL/$(basename "$SRV").samp" \
            --level "$CD/levels/$L" --sched "$LOCAL/$(basename "$SRV").sched.jsonl" >> "$JOBLOG" 2>&1; then
        log "$C/$L SMOKE CHECK FAILED: stopping this cell"; rc=5
      fi
    fi
    [ "$rc" != 0 ] && break
  done
  if [ "$started" = 1 ]; then store_server "$SRV"; stop_server "$SRV" "$G"; fi
  local all=1
  for L in "${levels[@]}"; do
    [ -f "$CD/levels/$L/WINDOW_DONE" ] || [ -f "$CD/levels/$L/COMPLETE" ] || all=0
  done
  [ "$all" = 1 ] && echo "$(date +%FT%T)" > "$CD/COMPLETE" && log "$C COMPLETE"
  return "$rc"
}

run_ab() {  # gpus cpus: sampler-overhead A/B on Qwen3-32B with 11's pilot8 replay (10 min per arm: off, on, off)
  local G=$1 CPUS=$2 CD=$CELLS/ab SRV arm vc sc ac wc
  if [ -f "$CD/COMPLETE" ]; then log "ab already complete"; return 0; fi
  mkdir -p "$CD/server"
  { read -r vc; read -r sc; read -r ac; read -r wc; } < <("$PY" "$DIR/hw_workloads.py" layout p1a hw_p1a "$CPUS")
  SRV=$(new_server_dir "$CD")
  start_server p1a "$G" "$vc" "$SRV" Qwen/Qwen3-32B || { stop_server "$SRV" "$G"; return 1; }
  local base="http://127.0.0.1:$(cat "$SRV/port")"
  for arm in off1 on off2; do
    reset_cache "$base" || log "ab $arm: prefix cache reset FAILED"
    [ "$arm" = on ] && start_sampler "$SRV" "$G" "$wc" "$sc"
    mark "$SRV/markers.jsonl" arm_start "\"arm\": \"$arm\""
    log "ab arm $arm"
    taskset -c "$ac" "$PY" "$DIR11/mu_replay.py" run --cond r_pilot8_default --base-url "$base" \
      --out-dir "$CD/$arm" --window-s 600 --hard-stop $(( $(date +%s) + 900 )) \
      --cells-root "$CD/replay_cells" >> "$CD/replay.log" 2>&1        # keep 11's cells untouched
    mark "$SRV/markers.jsonl" arm_end "\"arm\": \"$arm\""
    [ "$arm" = on ] && stop_sampler "$SRV"
  done
  store_server "$SRV"
  stop_server "$SRV" "$G"
  echo "$(date +%FT%T)" > "$CD/COMPLETE"
}

lane() {  # gpu "step1,step2,..."
  local G=$1 list=$2 CPUS step
  [ "$list" = "-" ] && return 0
  if [ "$G" = 0 ]; then CPUS=$CPUS_G0; else CPUS=$CPUS_G1; fi
  IFS=',' read -ra STEPS <<< "$list"
  for step in "${STEPS[@]}"; do
    if [ "$(date +%s)" -gt $((END - HARD_MARGIN - 900)) ]; then log "g$G no time left for $step"; break; fi
    case $step in
      ab) run_ab "$G" "$CPUS" ;;
      *) run_cell "$step" "$G" "$CPUS" "g$G" ;;
    esac
  done
}

on_term() {
  log "TERM received: stopping everything"
  for f in "$CELLS"/*/server/"${JOB}"_*/driver.pid; do [ -s "$f" ] && kill -TERM "$(cat "$f")" 2>/dev/null; done
  sleep 60
  for d in "$CELLS"/*/server/"${JOB}"_*; do
    [ -d "$d" ] || continue
    store_server "$d"
    [ -f "$d/server.pid" ] && kill -TERM -- "-$(cat "$d/server.pid")" 2>/dev/null
  done
  exit 143
}
trap on_term TERM INT

log "start: pre=[$PRE] GPU0=[$LANE0] GPU1=[$LANE1] cpus all=$CPUS_ALL g0=$CPUS_G0 g1=$CPUS_G1 host=$(hostname)"
nvidia-smi --query-gpu=index,name,uuid,pci.bus_id,memory.total --format=csv,noheader | tee -a "$JOBLOG"
nvidia-smi topo -m 2>/dev/null | tee -a "$JOBLOG"
# every job calibrates its own node first, while both GPUs are still idle (HW_AUTOPROBE=0 to skip)
if [ "${HW_AUTOPROBE:-1}" = 1 ] && [[ ",$PRE," != *",probe,"* ]] && [ ! -s "$DATA/probe/$JOB.json" ]; then
  if [ "$PRE" = "-" ]; then PRE=probe; else PRE="probe,$PRE"; fi
fi
if [ "$PRE" != "-" ]; then
  IFS=',' read -ra PRESTEPS <<< "$PRE"
  for step in "${PRESTEPS[@]}"; do
    case $step in
      probe)
        log "probe"
        "$PY" "$DIR/hw_probe.py" all --out "$DATA/probe/$JOB.json" --gpus 0,1 --work "$LOCAL/probe" 2>&1 | tee -a "$JOBLOG" ;;
      *) run_cell "$step" "0,1" "$CPUS_ALL" "g01" ;;
    esac
  done
fi
lane 0 "$LANE0" & L0=$!
lane 1 "$LANE1" & L1=$!
wait "$L0"; wait "$L1"
log "done"
exit 0
}
