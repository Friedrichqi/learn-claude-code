#!/usr/bin/env bash
# One 1-GPU job of research/13_tiered_memory: runs cells of tm_workloads.CELLS in order on GPU0.
#   bash research/13_tiered_memory/tm_pipeline.sh s2d,s2b
# Modelled on research/12_hw_utilization/hw_pipeline.sh (copied, not edited). Per level: the server is (re)started
# when the level needs different server settings (tm_workloads.py server), with tm_scheduler.TMScheduler, the
# tm_server_ext middleware, 12's hw_sampler.py beside it, and logs on local disk; mu_sweep runs N agents (think time
# 0) under bin/tmpython with the level's client environment, until the window ends. Levels and cells are resumable
# (WINDOW_DONE / COMPLETE). Raw logs are xz-compressed onto NFS when a server stops.
set -uo pipefail
{   # one compound command: editing this file cannot change a running job

CELLS_ARG=${1:-"-"}
REPO=/mnt/home/yqi10/learn-claude-code
DIR=$REPO/research/13_tiered_memory
DIR12=$REPO/research/12_hw_utilization
DIR11=$REPO/research/11_multiuser_kv
DATA=${TM_DATA:-$DIR/data}
CELLS=$DATA/cells
PY="$HOME/.venv/bin/python"
VLLM=${VLLM_BIN:-$HOME/.venv/bin/vllm}
JOB=${SLURM_JOB_ID:-local$$}
HARD_MARGIN=${HARD_MARGIN:-600}
LOCAL=${LOCAL_ROOT:-/tmp/$USER/tm/$JOB}
START=$(date +%s); END=$((START + ${JOB_SECONDS:-10800}))
if [ -n "${SLURM_JOB_ID:-}" ]; then
  slurm_end=$(scontrol show job "$SLURM_JOB_ID" 2>/dev/null | grep -o 'EndTime=[^ ]*' | cut -d= -f2)
  [ -n "$slurm_end" ] && [ "$slurm_end" != "Unknown" ] && END=$(date -d "$slurm_end" +%s)
fi
mkdir -p "$DATA/jobs" "$CELLS" "$LOCAL"
JOBLOG=$DATA/jobs/$JOB.log
cd "$REPO" || exit 1
export HF_HOME=/projects/co/jlin4/agenticllm/hf_cache HF_HUB_OFFLINE=1 TOKENIZERS_PARALLELISM=false
export PYTHONUNBUFFERED=1 VLLM_LOGGING_LEVEL=${VLLM_LOGGING_LEVEL:-INFO} CUDA_DEVICE_ORDER=PCI_BUS_ID
export PATH="$HOME/.venv/bin:$PATH" VIRTUAL_ENV="$HOME/.venv"
export MU_EXTRA_CONDITIONS=$DATA/conditions.json
SESSIONS=$DIR12/data/sessions.jsonl
G=${CUDA_VISIBLE_DEVICES:-0}; G=${G%%,*}

log() { echo "$(date -u +%FT%TZ) job=$JOB $*" | tee -a "$JOBLOG"; }
mark() { printf '{"wall": %s, "ev": "%s"%s}\n' "$(date +%s.%N)" "$2" "${3:+, $3}" >> "$1"; }
free_port() {
  "$PY" - <<'EOF'
import socket
while True:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    if "429" not in str(p) and "529" not in str(p):
        print(p); break
EOF
}
put_pid() { echo "$2" > "$1.tmp" && mv -f "$1.tmp" "$1"; }
reset_cache() {
  local i
  for i in $(seq 1 45); do
    curl -sf -X POST "$1/reset_prefix_cache?reset_external=true" 2>/dev/null | grep -Eq '"success": ?true' && return 0
    sleep 2
  done
  return 1
}
CPUS=$("$PY" -c 'import os; print(",".join(map(str, sorted(os.sched_getaffinity(0)))))')
env -u MU_EXTRA_CONDITIONS "$PY" "$DIR/tm_workloads.py" build --out "$MU_EXTRA_CONDITIONS" >> "$JOBLOG" 2>&1 || { log "tm_workloads build failed"; exit 1; }

stop_server() {
  local SRV=$1 vpid left p
  vpid=$(cat "$SRV/server.pid" 2>/dev/null) || return 0
  kill -TERM -- "-$vpid" 2>/dev/null || kill -TERM "$vpid" 2>/dev/null
  for _ in $(seq 1 30); do kill -0 "$vpid" 2>/dev/null || break; sleep 2; done
  kill -KILL -- "-$vpid" 2>/dev/null
  sleep 3
  left=$(nvidia-smi -i "$G" --query-compute-apps=pid --format=csv,noheader 2>/dev/null | tr -d ' ')
  for p in $left; do
    if [ "$(ps -o user= -p "$p" 2>/dev/null)" = "$USER" ]; then log "killing leftover GPU process $p"; kill -KILL "$p" 2>/dev/null; fi
  done
}

start_server() {  # cell level srvdir model vcpus -> 0 when healthy
  local C=$1 L=$2 SRV=$3 MODEL=$4 VC=$5 port base line envs flags tag
  tag=$(basename "$SRV"); port=$(cat "$SRV/port"); base="http://127.0.0.1:$port"
  line=$("$PY" "$DIR/tm_workloads.py" server "$L"); envs=${line%%$'\t'*}; flags=${line#*$'\t'}
  echo "$line" > "$SRV/server_config.txt"
  log "$C/$L serve $MODEL on :$port cpus=$VC env: $envs"
  # shellcheck disable=SC2086
  eval "env CUDA_VISIBLE_DEVICES=$G PYTHONPATH=$DIR:$DIR12:$DIR11 MU_SCHED_LOG=$LOCAL/$tag.sched.jsonl \
      MU_POOL_WALK_S=2 VLLM_SERVER_DEV_MODE=1 TM_HINTS=ipc://$LOCAL/$tag.hints TM_EXT_LOG=$LOCAL/$tag.ext.jsonl $envs \
      setsid taskset -c $VC $VLLM serve $MODEL --host 127.0.0.1 --port $port --served-model-name $MODEL $flags" \
      > "$SRV/vllm.log" 2>&1 &
  local vpid=$!
  put_pid "$SRV/server.pid" "$vpid"
  mark "$SRV/markers.jsonl" launch "\"cpus\": \"$VC\", \"gpus\": \"$G\", \"level\": \"$L\""
  local ok=0
  for _ in $(seq 1 180); do
    if curl -sf "$base/health" >/dev/null; then ok=1; break; fi
    if ! kill -0 "$vpid" 2>/dev/null; then break; fi
    sleep 10
  done
  if [ "$ok" != 1 ]; then log "$C/$L server never became healthy (see $SRV/vllm.log)"; return 1; fi
  mark "$SRV/markers.jsonl" healthy
  curl -sf "$base/metrics" | grep -m1 '^vllm:cache_config_info' > "$SRV/cache_config.txt" 2>/dev/null
  return 0
}

start_sampler() {  # srvdir sampler_cpu
  local SRV=$1 tag; tag=$(basename "$SRV")
  taskset -c "$2" "$PY" "$DIR12/hw_sampler.py" run --out "$LOCAL/$tag.samp" --gpus "$G" --cpus "$CPUS" \
    --metrics-url "http://127.0.0.1:$(cat "$SRV/port")/metrics" --server-pid-file "$SRV/server.pid" \
    --driver-pid-file "$SRV/driver.pid" > "$SRV/sampler.log" 2>&1 &
  put_pid "$SRV/sampler.pid" "$!"
}

store_server() {  # srvdir: stop the sampler, compress local logs onto NFS
  local SRV=$1 tag f spid; tag=$(basename "$SRV")
  spid=$(cat "$SRV/sampler.pid" 2>/dev/null)
  if [ -n "$spid" ]; then kill -TERM "$spid" 2>/dev/null; for _ in $(seq 1 20); do kill -0 "$spid" 2>/dev/null || break; sleep 1; done; fi
  rm -f "$SRV/sampler.pid"
  if [ -d "$LOCAL/$tag.samp" ]; then
    mkdir -p "$SRV/samp"; cp "$LOCAL/$tag.samp/meta.json" "$SRV/samp/" 2>/dev/null
    for f in "$LOCAL/$tag.samp"/*.jsonl; do [ -f "$f" ] && xz -T2 -c "$f" > "$SRV/samp/$(basename "$f").xz"; done
    rm -rf "$LOCAL/$tag.samp"
  fi
  for f in sched ext; do
    [ -f "$LOCAL/$tag.$f.jsonl" ] && xz -T2 -c "$LOCAL/$tag.$f.jsonl" > "$SRV/$f.jsonl.xz" && rm -f "$LOCAL/$tag.$f.jsonl"
  done
  curl -sf "$(cat "$SRV/base" 2>/dev/null)/tm/stats" > "$SRV/tm_stats.json" 2>/dev/null
}

run_level() {  # cell level srvdir -> rc
  local C=$1 L=$2 SRV=$3 LV=$CELLS/$1/levels/$2 vc sc ac wc window warmup n now hard rc cenv
  { read -r vc; read -r sc; read -r ac; read -r wc; } < <("$PY" "$DIR/tm_workloads.py" layout "$L" "$CPUS")
  window=$("$PY" "$DIR/tm_workloads.py" level "$L" --field window_s)
  warmup=$("$PY" "$DIR/tm_workloads.py" level "$L" --field warmup_s)
  n=$("$PY" "$DIR/tm_workloads.py" level "$L" --field n)
  cenv=$("$PY" "$DIR/tm_workloads.py" level "$L" --field client_env)
  if [ -n "${TM_WINDOW_SCALE:-}" ]; then
    window=$("$PY" -c "print(max(1, int($window * $TM_WINDOW_SCALE)))"); warmup=$("$PY" -c "print(int($warmup * $TM_WINDOW_SCALE))")
  fi
  if [ -d "$LV" ]; then mv "$LV" "$CELLS/$C/levels/aborted_${L}_$(date +%H%M%S)"; fi
  mkdir -p "$LV"
  now=$(date +%s); hard=$((now + warmup + window))
  if [ "$hard" -gt $((END - HARD_MARGIN)) ]; then log "$C/$L: window does not fit in the job"; return 4; fi
  local base; base=$(cat "$SRV/base")
  if reset_cache "$base"; then log "$C/$L prefix cache reset"; else log "$C/$L prefix cache reset FAILED (continuing)"; fi
  mark "$SRV/markers.jsonl" level_start "\"level\": \"$L\", \"n\": $n, \"window_s\": $window, \"warmup_s\": $warmup, \"hard\": $hard"
  log "$C/$L start N=$n window=${window}s agent cpus=$ac client env: ${cenv:-none}"
  # shellcheck disable=SC2086
  eval "env $cenv TM_CLIENT_LOG=$LV/client.jsonl taskset -c $ac $PY $DIR11/mu_sweep.py run --cond $L --cell g0 \
    --base-url $base --lane-root $LOCAL/lanes/$C --sessions $SESSIONS --cells-root $CELLS/$C/levels --fillers off \
    --think-scale 0 --stagger-s $warmup --dispatch-until $hard --hard-stop $hard --python $DIR/bin/tmpython" \
    >> "$LV/sweep.log" 2>&1 &
  local wpid=$!
  put_pid "$SRV/driver.pid" "$wpid"
  wait "$wpid"; rc=$?
  : > "$SRV/driver.pid"
  mark "$SRV/markers.jsonl" level_end "\"level\": \"$L\", \"rc\": $rc"
  curl -sf "$base/tm/stats" > "$LV/tm_stats.json" 2>/dev/null
  log "$C/$L sweep exit $rc"
  if [ "$rc" = 4 ] || [ "$rc" = 0 ]; then
    if [ "$(date +%s)" -ge "$hard" ]; then echo "$hard" > "$LV/WINDOW_DONE"; return 0; fi
    log "$C/$L ended before its window ($rc)"; return 4
  fi
  return "$rc"
}

run_cell() {  # cell
  local C=$1 CD=$CELLS/$1 MODEL levels L SRV="" cur="" want rc=0
  if [ -f "$CD/COMPLETE" ]; then log "$C already complete"; return 0; fi
  mkdir -p "$CD/server" "$CD/levels"
  MODEL=$("$PY" "$DIR/tm_workloads.py" cell "$C" --field model)
  read -ra levels <<< "$("$PY" "$DIR/tm_workloads.py" cell "$C" --field levels)"
  if [ "${#levels[@]}" -eq 0 ] || [ -z "$MODEL" ]; then log "$C: unknown cell or no levels"; return 1; fi
  for L in "${levels[@]}"; do
    if [ -f "$CD/levels/$L/WINDOW_DONE" ]; then log "$C/$L already done"; continue; fi
    if [ "$(date +%s)" -gt $((END - HARD_MARGIN - 900)) ]; then log "$C: no time left for $L"; rc=4; break; fi
    want=$("$PY" "$DIR/tm_workloads.py" server "$L")
    if [ -n "$SRV" ] && [ "$want" != "$cur" ]; then store_server "$SRV"; stop_server "$SRV"; SRV=""; fi
    if [ -z "$SRV" ]; then
      local vc sc ac wc tag
      { read -r vc; read -r sc; read -r ac; read -r wc; } < <("$PY" "$DIR/tm_workloads.py" layout "$L" "$CPUS")
      tag="${JOB}_${C}_$(date +%H%M%S)"; SRV=$CD/server/$tag
      mkdir -p "$SRV"; free_port > "$SRV/port"; : > "$SRV/driver.pid"; echo "http://127.0.0.1:$(cat "$SRV/port")" > "$SRV/base"
      start_sampler "$SRV" "$sc"
      if ! start_server "$C" "$L" "$SRV" "$MODEL" "$vc"; then store_server "$SRV"; stop_server "$SRV"; SRV=""; rc=1; break; fi
      cur=$want
    fi
    run_level "$C" "$L" "$SRV"; rc=$?
    [ "$rc" != 0 ] && log "$C/$L rc=$rc (continuing with the next level)"
  done
  if [ -n "$SRV" ]; then store_server "$SRV"; stop_server "$SRV"; fi
  local all=1
  for L in "${levels[@]}"; do [ -f "$CD/levels/$L/WINDOW_DONE" ] || all=0; done
  [ "$all" = 1 ] && echo "$(date +%FT%T)" > "$CD/COMPLETE" && log "$C COMPLETE"
  return 0
}

log "start cells=$CELLS_ARG gpu=$G cpus=$CPUS host=$(hostname)"
nvidia-smi -i "$G" --query-gpu=name,memory.total --format=csv,noheader >> "$JOBLOG" 2>&1
for C in ${CELLS_ARG//,/ }; do
  [ "$C" = "-" ] && continue
  run_cell "$C"
done
log "done"
rm -rf "$LOCAL/lanes"
exit 0
}
