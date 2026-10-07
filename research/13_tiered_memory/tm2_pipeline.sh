#!/usr/bin/env bash
# One 2-GPU job of research/13_tiered_memory (S4 calibration, S5 B2 / B3 cells): runs cells of tm_workloads.CELLS.
#   bash research/13_tiered_memory/tm2_pipeline.sh s4,b3
# Layouts (tm_workloads level fields):
#   dp2   two TP=1 servers; server i sees both GPUs, its own first (CUDA_VISIBLE_DEVICES=gi,gj): vLLM runs on cuda:0,
#         and a peer-HBM tier (tm_peer_spec) can reach cuda:1 = the other GPU over NVLink
#   pd    the same two servers as prefill (GPU0) and decode (GPU1), with NixlConnector (NIXL from .deps)
#   tp2   one TP=2 server on both GPUs
# Every arm goes through pd_proxy.py (affine / pd / pass), so all pay the same hop; mu_sweep drives the proxy.
# Cell s4 (layout calib): tm_calib.py all (incl. peer copies), the NIXL import check, and a Qwen3-8B server whose
# offload tier is the peer GPU's HBM, probed by tm_vllm_probe.py (restore from peer HBM vs recompute).
set -uo pipefail
{
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
export MU_EXTRA_CONDITIONS=$DATA/conditions.json
SESSIONS=$DIR12/data/sessions.jsonl
IFS=',' read -r G0 G1 _ <<< "${CUDA_VISIBLE_DEVICES:-0,1}"; G1=${G1:-1}
PYPATH="$DIR:$DIR/.deps:$DIR12:$DIR11"

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
read -r VC0 VC1 VCT SC AC < <("$PY" - "$CPUS" <<'EOF'
import sys
c = sys.argv[1].split(",")
j = lambda xs: ",".join(xs) or c[0]
print(j(c[0:4]), j(c[4:8]), j(c[0:8]), j(c[8:9]), j(c[9:]))
EOF
)
env -u MU_EXTRA_CONDITIONS "$PY" "$DIR/tm_workloads.py" build --out "$MU_EXTRA_CONDITIONS" >> "$JOBLOG" 2>&1 || { log "build failed"; exit 1; }

wait_healthy() {  # base pid
  local _
  for _ in $(seq 1 180); do
    curl -sf "$1/health" >/dev/null && return 0
    kill -0 "$2" 2>/dev/null || return 1
    sleep 10
  done
  return 1
}

start_srv() {  # srvdir idx level visible vcpus model -> 0 healthy
  local SRV=$1 I=$2 L=$3 VIS=$4 VC=$5 MODEL=$6 port base line envs flags tag
  tag=$(basename "$SRV"); port=$(free_port); base="http://127.0.0.1:$port"
  echo "$port" > "$SRV/port$I"; echo "$base" > "$SRV/base$I"
  line=$("$PY" "$DIR/tm_workloads.py" server "$L" --idx "$I"); envs=${line%%$'\t'*}; flags=${line#*$'\t'}
  echo "$line" > "$SRV/server_config$I.txt"
  log "$L server$I $MODEL on :$port gpus=$VIS cpus=$VC env: $envs"
  # shellcheck disable=SC2086
  eval "env CUDA_VISIBLE_DEVICES=$VIS PYTHONPATH=$PYPATH MU_SCHED_LOG=$LOCAL/$tag.$I.sched.jsonl MU_POOL_WALK_S=2 \
      VLLM_SERVER_DEV_MODE=1 VLLM_NIXL_SIDE_CHANNEL_PORT=$(free_port) TM_HINTS=ipc://$LOCAL/$tag.$I.hints \
      TM_EXT_LOG=$LOCAL/$tag.$I.ext.jsonl $envs setsid taskset -c $VC $VLLM serve $MODEL --host 127.0.0.1 --port $port \
      --served-model-name $MODEL $flags" > "$SRV/vllm$I.log" 2>&1 &
  local vpid=$!
  put_pid "$SRV/server$I.pid" "$vpid"
  mark "$SRV/markers.jsonl" launch "\"idx\": $I, \"gpus\": \"$VIS\", \"level\": \"$L\""
  wait_healthy "$base" "$vpid" || { log "$L server$I never became healthy (see $SRV/vllm$I.log)"; return 1; }
  mark "$SRV/markers.jsonl" healthy "\"idx\": $I"
  return 0
}

stop_all() {  # srvdir
  local SRV=$1 f vpid p left
  for f in "$SRV"/server*.pid "$SRV"/proxy.pid; do
    [ -f "$f" ] || continue
    vpid=$(cat "$f"); kill -TERM -- "-$vpid" 2>/dev/null || kill -TERM "$vpid" 2>/dev/null
  done
  sleep 5
  for f in "$SRV"/server*.pid "$SRV"/proxy.pid; do
    [ -f "$f" ] || continue
    vpid=$(cat "$f"); for _ in $(seq 1 30); do kill -0 "$vpid" 2>/dev/null || break; sleep 2; done
    kill -KILL -- "-$vpid" 2>/dev/null; rm -f "$f"
  done
  sleep 3
  for g in $G0 $G1; do
    left=$(nvidia-smi -i "$g" --query-compute-apps=pid --format=csv,noheader 2>/dev/null | tr -d ' ')
    for p in $left; do [ "$(ps -o user= -p "$p" 2>/dev/null)" = "$USER" ] && kill -KILL "$p" 2>/dev/null; done
  done
}

store_all() {  # srvdir
  local SRV=$1 tag f spid; tag=$(basename "$SRV")
  spid=$(cat "$SRV/sampler.pid" 2>/dev/null)
  if [ -n "$spid" ]; then kill -TERM "$spid" 2>/dev/null; for _ in $(seq 1 20); do kill -0 "$spid" 2>/dev/null || break; sleep 1; done; fi
  rm -f "$SRV/sampler.pid"
  if [ -d "$LOCAL/$tag.samp" ]; then
    mkdir -p "$SRV/samp"; cp "$LOCAL/$tag.samp/meta.json" "$SRV/samp/" 2>/dev/null
    for f in "$LOCAL/$tag.samp"/*.jsonl; do [ -f "$f" ] && xz -T2 -c "$f" > "$SRV/samp/$(basename "$f").xz"; done
    rm -rf "$LOCAL/$tag.samp"
  fi
  for f in "$LOCAL/$tag".*.jsonl; do [ -f "$f" ] && xz -T2 -c "$f" > "$SRV/$(basename "$f" | sed "s/^$tag\.//").xz" && rm -f "$f"; done
}

start_level_servers() {  # cell level -> sets SRV; 0 ok
  local C=$1 L=$2 MODEL layout tag
  MODEL=$("$PY" "$DIR/tm_workloads.py" cell "$C" --field model)
  layout=$("$PY" "$DIR/tm_workloads.py" level "$L" --field layout)
  tag="${JOB}_${C}_$(date +%H%M%S)"; SRV=$CELLS/$C/server/$tag; mkdir -p "$SRV"; : > "$SRV/driver.pid"
  echo "$layout" > "$SRV/layout"
  taskset -c "$SC" "$PY" "$DIR12/hw_sampler.py" run --out "$LOCAL/$tag.samp" --gpus "$G0,$G1" --cpus "$CPUS" \
    --metrics-url "http://127.0.0.1:1/metrics" --server-pid-file "$SRV/server0.pid" --driver-pid-file "$SRV/driver.pid" \
    > "$SRV/sampler.log" 2>&1 &
  put_pid "$SRV/sampler.pid" "$!"
  case $layout in
    tp2) start_srv "$SRV" 0 "$L" "$G0,$G1" "$VCT" "$MODEL" || return 1 ;;
    dp2|pd) start_srv "$SRV" 0 "$L" "$G0,$G1" "$VC0" "$MODEL" & local p0=$!
            sleep 20                                       # stagger: the first server's memory profiling runs alone
            start_srv "$SRV" 1 "$L" "$G1,$G0" "$VC1" "$MODEL" & local p1=$!
            wait "$p0" || return 1; wait "$p1" || return 1 ;;
    *) log "$L: unknown layout $layout"; return 1 ;;
  esac
  local mode weights pport args
  mode=$("$PY" "$DIR/tm_workloads.py" level "$L" --field proxy_mode)
  weights=$("$PY" "$DIR/tm_workloads.py" level "$L" --field proxy_weights)
  pport=$(free_port)
  case $mode in
    pd) args=(--mode pd --prefill "$(cat "$SRV/base0")" --decode "$(cat "$SRV/base1")") ;;
    pass) args=(--mode pass --backends "$(cat "$SRV/base0")") ;;
    *) args=(--mode affine --backends "$(cat "$SRV/base0"),$(cat "$SRV/base1")" ${weights:+--weights "$weights"}) ;;
  esac
  setsid taskset -c "$SC" "$PY" "$DIR/pd_proxy.py" --port "$pport" "${args[@]}" --log "$LOCAL/$tag.proxy.jsonl" \
    > "$SRV/proxy.log" 2>&1 &
  put_pid "$SRV/proxy.pid" "$!"
  echo "http://127.0.0.1:$pport" > "$SRV/base"
  wait_healthy "$(cat "$SRV/base")" "$(cat "$SRV/proxy.pid")" || { log "$L proxy not healthy"; return 1; }
  log "$L proxy $mode on :$pport -> $(cat "$SRV"/base[01] 2>/dev/null | tr '\n' ' ')"
  return 0
}

run_level() {  # cell level srvdir -> rc
  local C=$1 L=$2 SRV=$3 LV=$CELLS/$1/levels/$2 window warmup n now hard rc cenv b
  window=$("$PY" "$DIR/tm_workloads.py" level "$L" --field window_s)
  warmup=$("$PY" "$DIR/tm_workloads.py" level "$L" --field warmup_s)
  n=$("$PY" "$DIR/tm_workloads.py" level "$L" --field n)
  cenv=$("$PY" "$DIR/tm_workloads.py" level "$L" --field client_env)
  if [ -n "${TM_WINDOW_SCALE:-}" ]; then
    window=$("$PY" -c "print(max(1, int($window * $TM_WINDOW_SCALE)))"); warmup=$("$PY" -c "print(int($warmup * $TM_WINDOW_SCALE))")
  fi
  [ -d "$LV" ] && mv "$LV" "$CELLS/$C/levels/aborted_${L}_$(date +%H%M%S)"
  mkdir -p "$LV"
  now=$(date +%s); hard=$((now + warmup + window))
  [ "$hard" -gt $((END - HARD_MARGIN)) ] && { log "$C/$L: window does not fit"; return 4; }
  for b in "$SRV"/base0 "$SRV"/base1; do [ -f "$b" ] && { reset_cache "$(cat "$b")" || log "$C/$L reset failed on $b"; }; done
  mark "$SRV/markers.jsonl" level_start "\"level\": \"$L\", \"n\": $n, \"window_s\": $window, \"warmup_s\": $warmup, \"hard\": $hard"
  log "$C/$L start N=$n window=${window}s via $(cat "$SRV/base")"
  # shellcheck disable=SC2086
  eval "env $cenv TM_CLIENT_LOG=$LV/client.jsonl taskset -c $AC $PY $DIR11/mu_sweep.py run --cond $L --cell g01 \
    --base-url $(cat "$SRV/base") --lane-root $LOCAL/lanes/$C --sessions $SESSIONS --cells-root $CELLS/$C/levels \
    --fillers off --think-scale 0 --stagger-s $warmup --dispatch-until $hard --hard-stop $hard --python $DIR/bin/tmpython" \
    >> "$LV/sweep.log" 2>&1 &
  local wpid=$!; put_pid "$SRV/driver.pid" "$wpid"; wait "$wpid"; rc=$?; : > "$SRV/driver.pid"
  mark "$SRV/markers.jsonl" level_end "\"level\": \"$L\", \"rc\": $rc"
  curl -sf "$(cat "$SRV/base")/proxy/stats" > "$LV/proxy_stats.json" 2>/dev/null
  log "$C/$L sweep exit $rc"
  if [ "$rc" = 4 ] || [ "$rc" = 0 ]; then
    [ "$(date +%s)" -ge "$hard" ] && { echo "$hard" > "$LV/WINDOW_DONE"; return 0; }
    return 4
  fi
  return "$rc"
}

run_calib() {
  local OUTD=$DATA/probe/$JOB
  mkdir -p "$OUTD"
  log "s4: tm_calib all (2 GPUs)"
  CUDA_VISIBLE_DEVICES="$G0,$G1" "$PY" "$DIR/tm_calib.py" all --out "$OUTD" --gib 8 > "$OUTD/calib.log" 2>&1
  PYTHONPATH="$DIR/.deps" "$PY" -c "import nixl, sys; print('nixl', getattr(nixl, '__version__', '?'), nixl.__file__)" \
    > "$OUTD/nixl_check.txt" 2>&1; log "s4: nixl check: $(tail -1 "$OUTD/nixl_check.txt")"
  local SRV=$CELLS/s4/server/${JOB}_peer; mkdir -p "$SRV"
  local cfg='{"kv_connector": "OffloadingConnector", "kv_role": "kv_both", "kv_connector_extra_config": {"cpu_bytes_to_use": 17179869184, "spec_name": "PeerHBMOffloadingSpec", "spec_module_path": "tm_peer_spec", "peer_device": 1}}'
  local port; port=$(free_port)
  CUDA_VISIBLE_DEVICES="$G0,$G1" PYTHONPATH="$PYPATH" VLLM_SERVER_DEV_MODE=1 setsid taskset -c "$VC0" "$VLLM" serve Qwen/Qwen3-8B \
    --host 127.0.0.1 --port "$port" --served-model-name Qwen/Qwen3-8B --max-model-len 40960 --enable-prefix-caching \
    --kv-transfer-config "$cfg" > "$SRV/vllm0.log" 2>&1 &
  local vpid=$!; put_pid "$SRV/server0.pid" "$vpid"
  if wait_healthy "http://127.0.0.1:$port" "$vpid"; then
    "$PY" "$DIR/tm_vllm_probe.py" --base "http://127.0.0.1:$port" --model Qwen/Qwen3-8B --log "$SRV/vllm0.log" \
      --out "$OUTD/probe_peer_qwen3-8b.json" --offload > "$OUTD/probe_peer.log" 2>&1; log "s4: peer-tier probe rc=$?"
  else
    log "s4: peer-tier server failed (see $SRV/vllm0.log)"
  fi
  stop_all "$SRV"
}

run_cell() {  # cell
  local C=$1 CD=$CELLS/$1 levels L SRV="" cur="" want layout
  layout=$("$PY" "$DIR/tm_workloads.py" cell "$C" --field layout)
  if [ "$layout" = "calib" ]; then run_calib; return 0; fi
  [ -f "$CD/COMPLETE" ] && { log "$C already complete"; return 0; }
  mkdir -p "$CD/server" "$CD/levels"
  read -ra levels <<< "$("$PY" "$DIR/tm_workloads.py" cell "$C" --field levels)"
  [ "${#levels[@]}" -eq 0 ] && { log "$C: unknown cell or no levels"; return 1; }
  for L in "${levels[@]}"; do
    [ -f "$CD/levels/$L/WINDOW_DONE" ] && { log "$C/$L already done"; continue; }
    [ "$(date +%s)" -gt $((END - HARD_MARGIN - 1200)) ] && { log "$C: no time left for $L"; break; }
    want="$("$PY" "$DIR/tm_workloads.py" server "$L" --idx 0)|$("$PY" "$DIR/tm_workloads.py" server "$L" --idx 1 2>/dev/null)|$("$PY" "$DIR/tm_workloads.py" level "$L" --field layout)|$("$PY" "$DIR/tm_workloads.py" level "$L" --field proxy_weights)"
    if [ -n "$SRV" ] && [ "$want" != "$cur" ]; then store_all "$SRV"; stop_all "$SRV"; SRV=""; fi
    if [ -z "$SRV" ]; then
      if ! start_level_servers "$C" "$L"; then store_all "$SRV"; stop_all "$SRV"; SRV=""; continue; fi
      cur=$want
    fi
    run_level "$C" "$L" "$SRV" || log "$C/$L rc=$?"
  done
  [ -n "$SRV" ] && { store_all "$SRV"; stop_all "$SRV"; }
  local all=1
  for L in "${levels[@]}"; do [ -f "$CD/levels/$L/WINDOW_DONE" ] || all=0; done
  [ "$all" = 1 ] && echo "$(date +%FT%T)" > "$CD/COMPLETE" && log "$C COMPLETE"
  return 0
}

log "start cells=$CELLS_ARG gpus=$G0,$G1 cpus=$CPUS host=$(hostname)"
nvidia-smi topo -m >> "$JOBLOG" 2>&1
for C in ${CELLS_ARG//,/ }; do [ "$C" = "-" ] || run_cell "$C"; done
log "done"
rm -rf "$LOCAL/lanes"
exit 0
}
