#!/usr/bin/env bash
# One Slurm job of research/14_pd_crossover (PLAN.md §5, §7): runs cells of pd_workloads.CELLS on this job's GPUs.
#   bash research/14_pd_crossover/pd_pipeline.sh l2a          (pd2.sbatch / pd4.sbatch wrap this)
# Per level, in the cell's order: (re)start the arm's layout when the server key changes (pd_launch.py starts every
# vLLM instance as its child; then pd_router.py, 12's hw_sampler.py over all GPUs with the launcher as server root,
# pd_scrape.py), reset every instance's prefix cache, snapshot /metrics, run the level (live: 11's mu_sweep with 13's
# bin/tmpython; replay: pd_replay.py run) until its hard stop, snapshot /metrics again. Raw logs stay node-local and
# are xz'd onto NFS when the layout stops. Levels are resumable (WINDOW_DONE), cells too (COMPLETE).
# Cell s1 / s3 first run the calibration (pd_calib.py; s1 writes data/decisions.json from gates G1-G4); a failed
# stop gate (G1 NVLink path, G2 correctness) skips the cell's levels.
# Core split (sorted affinity list): 4 cores per GPU for vLLM, 1 router, 1 sampler + scraper, the rest agents.
set -uo pipefail
{   # one compound command: editing this file cannot change a running job
CELLS_ARG=${1:-"-"}
REPO=/mnt/home/yqi10/learn-claude-code
DIR=$REPO/research/14_pd_crossover
DIR13=$REPO/research/13_tiered_memory
DIR12=$REPO/research/12_hw_utilization
DIR11=$REPO/research/11_multiuser_kv
DATA=${PD_DATA:-$DIR/data}
CELLS=$DATA/cells
PY="$HOME/.venv/bin/python"
JOB=${SLURM_JOB_ID:-local$$}
HARD_MARGIN=${HARD_MARGIN:-600}
LOCAL=${LOCAL_ROOT:-/tmp/$USER/pd/$JOB}
START=$(date +%s); END=$((START + ${JOB_SECONDS:-43200}))
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
export PD_DATA=$DATA MU_EXTRA_CONDITIONS=$DATA/conditions.json
SESSIONS=$DIR12/data/sessions.jsonl
GPULIST=${CUDA_VISIBLE_DEVICES:-0,1}
IFS=',' read -r -a GPUS <<< "$GPULIST"
WS=${PD_WINDOW_SCALE:-}

log() { echo "$(date -u +%FT%TZ) job=$JOB $*" | tee -a "$JOBLOG"; }
mark() { printf '{"wall": %s, "ev": "%s"%s}\n' "$(date +%s.%N)" "$2" "${3:+, $3}" >> "$1"; }
wl() { "$PY" "$DIR/pd_workloads.py" "$@"; }
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
wait_healthy() {  # base pid tries
  local _
  for _ in $(seq 1 "${3:-120}"); do
    curl -sf "$1/health" >/dev/null && return 0
    kill -0 "$2" 2>/dev/null || return 1
    sleep 5
  done
  return 1
}
reset_cache() {  # base -> 0 when the reset succeeded within 90 s
  local i
  for i in $(seq 1 45); do
    curl -sf -X POST "$1/reset_prefix_cache?reset_external=true" 2>/dev/null | grep -Eq '"success": ?true' && return 0
    sleep 2
  done
  return 1
}
CPUS=$("$PY" -c 'import os; print(",".join(map(str, sorted(os.sched_getaffinity(0)))))')
env -u MU_EXTRA_CONDITIONS "$PY" "$DIR/pd_workloads.py" build --out "$MU_EXTRA_CONDITIONS" >> "$JOBLOG" 2>&1 || { log "build failed"; exit 1; }

split_cores() {  # g -> sets VC RC SC AC
  read -r VC RC SC AC < <("$PY" - "$CPUS" "$1" <<'EOF'
import sys
c, g = sys.argv[1].split(","), int(sys.argv[2])
j = lambda xs: ",".join(xs) or c[-1]
v = 4 * g
print(j(c[:v]), j(c[v:v + 1]), j(c[v + 1:v + 2]), j(c[v + 2:]))
EOF
)
}

# -- layout lifecycle ------------------------------------------------------------------------------------------------
start_layout() {  # cell level [launch args...] -> sets SRV; 0 when every instance and the router are up
  local C=$1 L=$2; shift 2
  local tag mode idle rport args inst
  tag="${JOB}_${C}_$(date +%H%M%S)"; SRV=$CELLS/$C/server/$tag; mkdir -p "$SRV"; : > "$SRV/driver.pid"
  mode=$(wl level "$L" --field router); idle=$(wl level "$L" --field idle_s)
  echo "$L" > "$SRV/first_level"
  log "$C/$L layout start ($mode) tag=$tag gpus=$GPULIST vllm_cpus=$VC"
  setsid taskset -c "$VC" "$PY" "$DIR/pd_launch.py" --level "$L" --gpus "$GPULIST" --cpus "$VC" --out "$SRV" \
    --local "$LOCAL" --tag "$tag" "$@" > "$SRV/launch.log" 2>&1 &
  put_pid "$SRV/launcher.pid" "$!"
  mark "$SRV/markers.jsonl" launch "\"level\": \"$L\", \"gpus\": \"$GPULIST\""
  local t
  for t in $(seq 1 300); do
    [ -f "$SRV/READY" ] && break
    [ -f "$SRV/FAILED" ] && { log "$C/$L layout FAILED: $(cat "$SRV/FAILED")"; return 1; }
    kill -0 "$(cat "$SRV/launcher.pid")" 2>/dev/null || { log "$C/$L launcher exited"; return 1; }
    sleep 5
  done
  [ -f "$SRV/READY" ] || { log "$C/$L layout not ready"; return 1; }
  mark "$SRV/markers.jsonl" healthy "\"ready_s\": $(cat "$SRV/READY")"
  inst=$SRV/instances.json
  case $mode in
    pd) args=(--mode pd --prefill "$("$PY" -c "import json;print(','.join(i['base'] for i in json.load(open('$inst')) if i['role']=='P'))")"
              --decode "$("$PY" -c "import json;print(','.join(i['base'] for i in json.load(open('$inst')) if i['role']=='D'))")") ;;
    *) args=(--mode "$mode" --backends "$("$PY" -c "import json;print(','.join(i['base'] for i in json.load(open('$inst'))))")") ;;
  esac
  rport=$(free_port)
  setsid taskset -c "$RC" "$PY" "$DIR/pd_router.py" --port "$rport" "${args[@]}" --idle-s "$idle" \
    --log "$LOCAL/$tag.router.jsonl" > "$SRV/router.log" 2>&1 &
  put_pid "$SRV/router.pid" "$!"
  echo "http://127.0.0.1:$rport" > "$SRV/base"
  wait_healthy "$(cat "$SRV/base")" "$(cat "$SRV/router.pid")" 60 || { log "$C/$L router not healthy"; return 1; }
  taskset -c "$SC" "$PY" "$DIR12/hw_sampler.py" run --out "$LOCAL/$tag.samp" --gpus "$GPULIST" --cpus "$CPUS" \
    --metrics-url "$(cat "$SRV/base")/metrics" --server-pid-file "$SRV/launch.pid" --driver-pid-file "$SRV/driver.pid" \
    > "$SRV/sampler.log" 2>&1 &
  put_pid "$SRV/sampler.pid" "$!"
  taskset -c "$SC" "$PY" "$DIR/pd_scrape.py" --instances "$inst" --out "$LOCAL/$tag.scrape" --period 5 \
    > "$SRV/scrape.log" 2>&1 &
  put_pid "$SRV/scrape.pid" "$!"
  log "$C/$L router $mode on $(cat "$SRV/base") -> $("$PY" -c "import json;print(' '.join(i['role']+str(i['idx'])+'@'+i['base'] for i in json.load(open('$inst'))))")"
  return 0
}

store_layout() {  # srvdir: stop sampler and scraper, compress node-local logs onto NFS
  local SRV=$1 tag f p idx; tag=$(basename "$SRV")
  for p in sampler scrape; do
    f=$SRV/$p.pid; [ -f "$f" ] || continue
    kill -TERM "$(cat "$f")" 2>/dev/null; for _ in $(seq 1 20); do kill -0 "$(cat "$f")" 2>/dev/null || break; sleep 1; done
    rm -f "$f"
  done
  if [ -d "$LOCAL/$tag.samp" ]; then
    mkdir -p "$SRV/samp"; cp "$LOCAL/$tag.samp/meta.json" "$SRV/samp/" 2>/dev/null
    for f in "$LOCAL/$tag.samp"/*.jsonl; do [ -f "$f" ] && xz -T2 -c "$f" > "$SRV/samp/$(basename "$f").xz"; done
    rm -rf "$LOCAL/$tag.samp"
  fi
  if [ -d "$LOCAL/$tag.scrape" ]; then
    for f in "$LOCAL/$tag.scrape"/*.jsonl; do [ -f "$f" ] && xz -T2 -c "$f" > "$SRV/$(basename "$f").xz"; done
    rm -rf "$LOCAL/$tag.scrape"
  fi
  for f in "$LOCAL/$tag".*.sched.jsonl; do
    [ -f "$f" ] || continue
    idx=$(basename "$f" | sed "s/^$tag\.\([0-9]*\)\.sched\.jsonl$/\1/")
    mkdir -p "$SRV/i$idx"; xz -T2 -c "$f" > "$SRV/i$idx/sched.jsonl.xz" && rm -f "$f"
  done
  [ -f "$LOCAL/$tag.router.jsonl" ] && xz -T2 -c "$LOCAL/$tag.router.jsonl" > "$SRV/router.jsonl.xz" && rm -f "$LOCAL/$tag.router.jsonl"
}

stop_layout() {  # srvdir
  local SRV=$1 f p g left
  for f in "$SRV/router.pid" "$SRV/launcher.pid"; do
    [ -f "$f" ] || continue
    p=$(cat "$f"); kill -TERM -- "-$p" 2>/dev/null || kill -TERM "$p" 2>/dev/null
  done
  for f in "$SRV/router.pid" "$SRV/launcher.pid"; do
    [ -f "$f" ] || continue
    p=$(cat "$f"); for _ in $(seq 1 45); do kill -0 "$p" 2>/dev/null || break; sleep 2; done
    kill -KILL -- "-$p" 2>/dev/null; rm -f "$f"
  done
  sleep 3
  for g in "${GPUS[@]}"; do
    left=$(nvidia-smi -i "$g" --query-compute-apps=pid --format=csv,noheader 2>/dev/null | tr -d ' ')
    for p in $left; do [ "$(ps -o user= -p "$p" 2>/dev/null)" = "$USER" ] && { log "killing leftover GPU process $p"; kill -KILL "$p" 2>/dev/null; }; done
  done
}

snapshot() {  # srvdir out-file
  "$PY" "$DIR/pd_scrape.py" --instances "$1/instances.json" --out /dev/null --once > "$2" 2>/dev/null
}

# -- one level -------------------------------------------------------------------------------------------------------
run_level() {  # cell level srvdir -> rc
  local C=$1 L=$2 SRV=$3 LV=$CELLS/$1/levels/$2 window warmup n now hard rc kind think cenv b
  window=$(wl level "$L" --field window_s); warmup=$(wl level "$L" --field warmup_s)
  n=$(wl level "$L" --field n); kind=$(wl level "$L" --field kind); think=$(wl level "$L" --field think_scale)
  cenv=$(wl level "$L" --field client_env)
  if [ -n "$WS" ]; then
    window=$("$PY" -c "print(max(1, int($window * $WS)))"); warmup=$("$PY" -c "print(int($warmup * $WS))")
  fi
  [ -d "$LV" ] && mv "$LV" "$CELLS/$C/levels/aborted_${L}_$(date +%H%M%S)"
  mkdir -p "$LV"
  now=$(date +%s); hard=$((now + warmup + window))
  [ "$hard" -gt $((END - HARD_MARGIN)) ] && { log "$C/$L: window does not fit"; return 4; }
  for b in $("$PY" -c "import json;print(' '.join(i['base'] for i in json.load(open('$SRV/instances.json'))))"); do
    reset_cache "$b" || log "$C/$L reset failed on $b"
  done
  echo "$SRV" > "$LV/server_dir"
  snapshot "$SRV" "$LV/metrics_start.json"
  mark "$SRV/markers.jsonl" level_start "\"level\": \"$L\", \"n\": $n, \"window_s\": $window, \"warmup_s\": $warmup, \"hard\": $hard"
  printf '{"level": "%s", "cell": "%s", "server_dir": "%s", "start": %s, "warmup_s": %s, "window_s": %s, "hard": %s}\n' \
    "$L" "$C" "$SRV" "$now" "$warmup" "$window" "$hard" > "$LV/level.json"
  log "$C/$L start N=$n kind=$kind window=${window}s warmup=${warmup}s via $(cat "$SRV/base")"
  if [ "$kind" = replay ] || [ "$kind" = rsmoke ]; then
    taskset -c "$AC" "$PY" "$DIR/pd_replay.py" run --level "$L" --base-url "$(cat "$SRV/base")" --out-dir "$LV" \
      --hard-stop "$hard" >> "$LV/replay.log" 2>&1 &
  else
    # shellcheck disable=SC2086
    eval "env $cenv TM_CLIENT_LOG=$LV/client.jsonl taskset -c $AC $PY $DIR11/mu_sweep.py run --cond $L --cell pd$JOB \
      --base-url $(cat "$SRV/base") --lane-root $LOCAL/lanes/$C --sessions $SESSIONS --cells-root $CELLS/$C/levels \
      --fillers off --think-scale $think --stagger-s $warmup --dispatch-until $hard --hard-stop $hard \
      --python $DIR13/bin/tmpython" >> "$LV/sweep.log" 2>&1 &
  fi
  local wpid=$!; put_pid "$SRV/driver.pid" "$wpid"; wait "$wpid"; rc=$?; : > "$SRV/driver.pid"
  snapshot "$SRV" "$LV/metrics_end.json"
  curl -sf "$(cat "$SRV/base")/router/stats" > "$LV/router_stats.json" 2>/dev/null
  mark "$SRV/markers.jsonl" level_end "\"level\": \"$L\", \"rc\": $rc"
  log "$C/$L driver exit $rc"
  if [ "$rc" = 4 ] || [ "$rc" = 0 ]; then
    [ "$(date +%s)" -ge "$hard" ] && { echo "$hard" > "$LV/WINDOW_DONE"; return 0; }
    return 4
  fi
  return "$rc"
}

# -- calibration (cells s1, s3) ----------------------------------------------------------------------------------------
calib_layout() {  # cell arm out-json extra-env... : start one arm, run pd_calib.py, stop
  local C=$1 ARM=$2 OUT=$3; shift 3
  local tag; tag="${JOB}_${C}_cal_${ARM}_$(date +%H%M%S)"
  SRV=$CELLS/$C/server/$tag; mkdir -p "$SRV"; : > "$SRV/driver.pid"
  log "$C calib: $ARM ($*) -> $OUT"
  env "$@" setsid taskset -c "$VC" "$PY" "$DIR/pd_launch.py" --arm "$ARM" --gpus "$GPULIST" --cpus "$VC" --out "$SRV" \
    --local "$LOCAL" --tag "$tag" > "$SRV/launch.log" 2>&1 &
  put_pid "$SRV/launcher.pid" "$!"
  local t
  for t in $(seq 1 300); do [ -f "$SRV/READY" ] || [ -f "$SRV/FAILED" ] && break; sleep 5; done
  if [ ! -f "$SRV/READY" ]; then log "$C calib $ARM: layout failed"; stop_layout "$SRV"; return 1; fi
  taskset -c "$SC" "$PY" "$DIR12/hw_sampler.py" run --out "$LOCAL/$tag.samp" --gpus "$GPULIST" --cpus "$CPUS" \
    --metrics-url "http://127.0.0.1:1/metrics" --server-pid-file "$SRV/launch.pid" --driver-pid-file "$SRV/driver.pid" \
    > "$SRV/sampler.log" 2>&1 &
  put_pid "$SRV/sampler.pid" "$!"
  sleep 5
  return 0
}

run_calib() {  # cell
  local C=$1 OUTD=$DATA/calib/$JOB rc
  mkdir -p "$OUTD"; echo "$OUTD" > "$CELLS/$C/CALIB_DIR"
  nvidia-smi topo -m > "$OUTD/topo.txt" 2>&1
  PYTHONPATH="$DIR/.deps" "$PY" -c "import nixl._api, nixl._bindings, nixl; print('nixl ok', nixl.__file__)" \
    > "$OUTD/nixl_check.txt" 2>&1; log "$C nixl: $(tail -1 "$OUTD/nixl_check.txt")"
  if [ "$C" = s1 ]; then
    # (b) transfer / delta / correctness on 1p1d, visibility A; then B at 8k and 24k
    if calib_layout "$C" 1p1d "$OUTD/calib_A.json" PD_VISIBILITY=A; then
      "$PY" "$DIR/pd_calib.py" pd --instances "$SRV/instances.json" --out "$OUTD/calib_A.json" --all \
        --prompts "$DATA/calib/prompts24.json" > "$OUTD/calib_A.log" 2>&1; log "s1 calib A rc=$?"
      store_layout "$SRV"; echo "$SRV" > "$OUTD/srv_A"; stop_layout "$SRV"
    fi
    if calib_layout "$C" 1p1d "$OUTD/calib_B.json" PD_VISIBILITY=B; then
      "$PY" "$DIR/pd_calib.py" pd --instances "$SRV/instances.json" --out "$OUTD/calib_B.json" --lengths 8192,24576 \
        > "$OUTD/calib_B.log" 2>&1; log "s1 calib B rc=$?"
      store_layout "$SRV"; echo "$SRV" > "$OUTD/srv_B"; stop_layout "$SRV"
    fi
    "$PY" "$DIR/pd_calib.py" decide --calib "$OUTD" > "$OUTD/decide.log" 2>&1; rc=$?
    log "s1 decide rc=$rc: $(tail -2 "$OUTD/decide.log" | tr '\n' ' ')"
    if [ "$rc" = 3 ]; then                                     # G1 failed both ways: explicit UCX transports
      if calib_layout "$C" 1p1d "$OUTD/calib_U.json" PD_VISIBILITY=A PD_UCX_TLS=cuda_ipc,cuda_copy,sm,self,tcp; then
        "$PY" "$DIR/pd_calib.py" pd --instances "$SRV/instances.json" --out "$OUTD/calib_U.json" --lengths 8192,24576 \
          > "$OUTD/calib_U.log" 2>&1
        store_layout "$SRV"; echo "$SRV" > "$OUTD/srv_U"; stop_layout "$SRV"
      fi
      "$PY" "$DIR/pd_calib.py" decide --calib "$OUTD" > "$OUTD/decide.log" 2>&1; rc=$?
      log "s1 decide (UCX) rc=$rc: $(tail -2 "$OUTD/decide.log" | tr '\n' ' ')"
    fi
    [ "$rc" = 0 ] || [ "$rc" = 1 ] || { log "s1 STOP GATE FAILED (rc=$rc): see $OUTD/gates.json"; return 1; }
    # (c) per-instance rates: plain TP1, P-configured TP1, TP2 (13's tm_vllm_probe)
    local spec arm only name
    for spec in "dp2:0:c1" "1p1d:0:p1" "tp2::c2"; do
      IFS=: read -r arm only name <<< "$spec"
      calib_layout "$C" "$arm" "$OUTD/probe_$name.json" PD_ONLY="$only" || continue
      "$PY" "$DIR13/tm_vllm_probe.py" --base "$("$PY" -c "import json;print(json.load(open('$SRV/instances.json'))[0]['base'])")" \
        --model Qwen/Qwen3-32B --log "$SRV/vllm0.log" --out "$OUTD/probe_$name.json" > "$OUTD/probe_$name.log" 2>&1
      log "s1 probe $name rc=$?"
      if [ "$name" = c1 ]; then                                # (d) router hop overhead (pass vs direct)
        "$PY" "$DIR/pd_calib.py" hop --instances "$SRV/instances.json" --out "$OUTD/hop.json" > "$OUTD/hop.log" 2>&1
        log "s1 hop rc=$?"
      fi
      store_layout "$SRV"; stop_layout "$SRV"
    done
  else
    # s3: G6 correctness of the mixed-TP arm, then the per-arm smoke levels run as the cell's levels
    if calib_layout "$C" 2p1dt2 "$OUTD/calib_2p1dt2.json"; then
      "$PY" "$DIR/pd_calib.py" pd --instances "$SRV/instances.json" --out "$OUTD/calib_2p1dt2.json" --correct-only \
        --n-prompts 8 --prompts "$DATA/calib/prompts24.json" > "$OUTD/calib_2p1dt2.log" 2>&1; log "s3 G6 rc=$?"
      store_layout "$SRV"; stop_layout "$SRV"
    fi
  fi
  return 0
}

gate_cell() {  # smoke cell: G5 + analysis sanity (+ G6) -> data/PASS_<cell>; s1 also freezes the S1 predictions
  local C=$1 cdir
  cdir=$(cat "$CELLS/$C/CALIB_DIR" 2>/dev/null)
  "$PY" "$DIR/pd_analyze.py" gates --cell "$C" --apply > "$CELLS/$C/gates.log" 2>&1
  log "$C gates: $(tail -1 "$CELLS/$C/gates.log")"
  if [ "$C" = s1 ] && [ -n "$cdir" ]; then
    "$PY" "$DIR/pd_analyze.py" calib --calib "$cdir" > "$CELLS/$C/calib_table.log" 2>&1
    [ -f "$DATA/tables/S1_predictions.md" ] || "$PY" "$DIR/pd_predict.py" --calib "$cdir" \
      --out "$DATA/tables/S1_predictions.md" > "$CELLS/$C/predict.log" 2>&1
    log "s1 calibration table and S1 predictions written (rc=$?)"
  fi
}

run_cell() {  # cell
  local C=$1 CD=$CELLS/$1 levels L SRV="" cur="" want g restart need src
  g=$(wl cell "$C" --field g)
  split_cores "$g"
  [ -f "$CD/COMPLETE" ] && { log "$C already complete"; return 0; }
  need=$(wl cell "$C" --field needs)
  if [ -n "$need" ] && [ ! -f "$DATA/PASS_$need" ]; then log "$C: $need has not passed its gates (no $DATA/PASS_$need); nothing run"; return 1; fi
  mkdir -p "$CD/server" "$CD/levels"
  if [ "$(wl cell "$C" --field calib)" = 1 ] || [ "$(wl cell "$C" --field calib4)" = 1 ]; then
    if [ ! -f "$CD/CALIB_DONE" ]; then
      run_calib "$C" || { log "$C: calibration stop gate failed; levels skipped"; return 1; }
      date +%FT%T > "$CD/CALIB_DONE"
    fi
  fi
  read -ra levels <<< "$(wl cell "$C" --field levels)"
  [ "${#levels[@]}" -eq 0 ] && { log "$C: unknown cell or no levels"; return 1; }
  for L in "${levels[@]}"; do
    if [ "$(wl level "$L" --field kind)" = replay ]; then
      src=$("$PY" -c "import json,sys; print(json.load(open('$MU_EXTRA_CONDITIONS'))['$L']['replay']['src'])")
      [ -f "$DATA/replay/plans/$src/plan.jsonl.xz" ] || { log "$C: replay plan $src missing; nothing run"; return 1; }
    fi
  done
  restart=$(wl decision restart_each_level 2>/dev/null || echo 0)
  for L in "${levels[@]}"; do
    [ -f "$CD/levels/$L/WINDOW_DONE" ] && { log "$C/$L already done"; continue; }
    [ "$(date +%s)" -gt $((END - HARD_MARGIN - 1200)) ] && { log "$C: no time left for $L"; break; }
    want="$(wl key "$L")|$(wl level "$L" --field idle_s)"
    if [ -n "$SRV" ] && { [ "$want" != "$cur" ] || [ "$restart" = 1 ]; }; then store_layout "$SRV"; stop_layout "$SRV"; SRV=""; fi
    if [ -z "$SRV" ]; then
      if ! start_layout "$C" "$L"; then store_layout "$SRV"; stop_layout "$SRV"; SRV=""; continue; fi
      cur=$want
    fi
    run_level "$C" "$L" "$SRV" || log "$C/$L rc=$?"
  done
  [ -n "$SRV" ] && { store_layout "$SRV"; stop_layout "$SRV"; }
  local all=1
  for L in "${levels[@]}"; do [ -f "$CD/levels/$L/WINDOW_DONE" ] || all=0; done
  [ "$all" = 1 ] && echo "$(date +%FT%T)" > "$CD/COMPLETE" && log "$C COMPLETE"
  if [ "$all" = 1 ] && { [ "$(wl cell "$C" --field calib)" = 1 ] || [ "$(wl cell "$C" --field calib4)" = 1 ]; }; then
    gate_cell "$C"
  fi
  return 0
}

log "start cells=$CELLS_ARG gpus=$GPULIST cpus=$CPUS host=$(hostname)"
nvidia-smi topo -m >> "$JOBLOG" 2>&1
for C in ${CELLS_ARG//,/ }; do [ "$C" = "-" ] || run_cell "$C"; done
log "done"
rm -rf "$LOCAL/lanes"
exit 0
}
