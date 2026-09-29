#!/usr/bin/env bash
# One shard of the 10_rope_shift experiment, inside a Slurm allocation (2 GPUs, any alpha node).
#   serve   start Qwen3-32B vLLM server(s): one replica per GPU on >=120 GB cards, else TP=2
#   runs    this shard's slice of the 500-run manifest through the lanes (resumable)
#   stop    stop the servers
#   events  extract this shard's compaction events (CPU)
#   selftest  connector selftests (T1-T5) with this node's TP layout; stops the shard before arms on failure
#   arms32 / arms8 / armsglm   the arms on Qwen3-32B / Qwen3-8B / GLM-4.7-Flash (resumable)
# Usage: bash research/10_rope_shift/rope_shift_pipeline.sh SHARD SHARDS step [step ...]
# Progress: data/rope_shift_live/pipeline/shard<S>/{log.txt,stage}. Merge, score and analyze run on
# the login node afterwards (see README, Reproducing).
set -uo pipefail

SHARD=${1:?shard}; SHARDS=${2:?shards}; shift 2
REPO=/mnt/home/yqi10/learn-claude-code
DIR=$REPO/research/10_rope_shift
DATA=$DIR/data/rope_shift_live
PIPE=$DATA/pipeline/shard$SHARD
PY="$HOME/.venv/bin/python"
LANES_PER_REPLICA=${LANES_PER_REPLICA:-12}
MAX_SECONDS=${MAX_SECONDS:-900}
LANE_ROOT=${LANE_ROOT:-/tmp/$USER/rope_shift_lanes}
MODEL=Qwen/Qwen3-32B
mkdir -p "$PIPE"
cd "$REPO" || exit 1
export HF_HOME=/projects/co/jlin4/agenticllm/hf_cache HF_HUB_OFFLINE=1 TOKENIZERS_PARALLELISM=false
export PYTHONUNBUFFERED=1 VLLM_LOGGING_LEVEL=${VLLM_LOGGING_LEVEL:-WARNING}

log() { echo "$(date -u +%FT%TZ) shard=$SHARD $*" | tee -a "$PIPE/log.txt"; }
stage() { echo "$1" > "$PIPE/stage"; log "stage $1"; }
gpu_mem_gb() { nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1 | awk '{print int($1/1024)}'; }
ngpus() { nvidia-smi -L | wc -l; }
free_port() { "$PY" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])'; }

start_servers() {
  local mem n; mem=$(gpu_mem_gb); n=$(ngpus)
  : > "$PIPE/base_urls"
  if [ "$mem" -ge 120 ]; then local tp=1 replicas=$n; else local tp=2 replicas=$((n / 2)); fi
  log "GPUs: $n x ${mem} GB -> $replicas replica(s) at TP=$tp"
  for r in $(seq 0 $((replicas - 1))); do
    local port devs; port=$(free_port)
    devs=$(seq -s, $((r * tp)) $((r * tp + tp - 1)))
    CUDA_VISIBLE_DEVICES=$devs setsid "$HOME/.venv/bin/vllm" serve "$MODEL" --host 127.0.0.1 --port "$port" \
      --served-model-name "$MODEL" --tensor-parallel-size "$tp" --max-model-len 40960 \
      --gpu-memory-utilization 0.90 --max-num-seqs 32 --enable-prefix-caching --enable-prompt-tokens-details \
      --enable-auto-tool-choice --tool-call-parser hermes --reasoning-parser qwen3 \
      --default-chat-template-kwargs '{"enable_thinking": false}' --uvicorn-log-level warning \
      > "$PIPE/vllm_$r.log" 2>&1 &
    echo $! >> "$PIPE/vllm.pids"
    echo "http://127.0.0.1:$port" >> "$PIPE/base_urls"
  done
  for url in $(cat "$PIPE/base_urls"); do
    for i in $(seq 1 180); do curl -sf "$url/health" >/dev/null && break; sleep 10; done
    curl -sf "$url/health" >/dev/null || { log "server $url never became healthy"; return 1; }
    curl -s "$url/version" >> "$PIPE/server_info.txt"; echo >> "$PIPE/server_info.txt"
  done
  log "servers healthy: $(tr '\n' ' ' < "$PIPE/base_urls")"
}

stop_servers() {
  [ -f "$PIPE/vllm.pids" ] || return 0
  for pid in $(cat "$PIPE/vllm.pids"); do kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null; done
  sleep 20
  for pid in $(cat "$PIPE/vllm.pids"); do kill -KILL -- "-$pid" 2>/dev/null; done
  rm -f "$PIPE/vllm.pids"
  log "servers stopped"
}
trap 'stop_servers' EXIT
trap 'log "TERM received"; stop_servers; exit 143' TERM INT

arms_step() {   # model, tp for 80 GB cards
  local model=$1 tp80=$2 mem n; mem=$(gpu_mem_gb); n=$(ngpus)
  local tp=$tp80; [ "$mem" -ge 120 ] && tp=1
  local procs=$((n / tp)) sub
  log "arms $model: $procs engine(s) at TP=$tp"
  for sub in $(seq 0 $((procs - 1))); do
    local devs; devs=$(seq -s, $((sub * tp)) $((sub * tp + tp - 1)))
    CUDA_VISIBLE_DEVICES=$devs "$PY" "$DIR/arms.py" run --model "$model" --tp "$tp" \
      --events "$DATA/events_s$SHARD.jsonl" --out "$DATA/arms_${model}_s${SHARD}_$sub.jsonl" \
      --shard "$sub" --shards "$procs" > "$PIPE/arms_${model}_$sub.log" 2>&1 &
  done
  wait
  log "arms $model done"
}

for step in "$@"; do
  case $step in
    serve)   stage serve; start_servers || exit 1 ;;
    runs)    stage runs
             urls=$(sed 's/^/--base-url /' "$PIPE/base_urls" | tr '\n' ' ')
             lanes=$(( $(wc -l < "$PIPE/base_urls") * LANES_PER_REPLICA ))
             "$PY" "$DIR/run_sweep.py" --shard "$SHARD" --shards "$SHARDS" --lanes "$lanes" \
               --max-seconds "$MAX_SECONDS" --lane-root "$LANE_ROOT/s$SHARD" $urls >> "$PIPE/sweep.log" 2>&1
             log "runs step exit $?" ;;
    stop)    stage stop; stop_servers ;;
    events)  stage events
             "$PY" "$DIR/events.py" extract --shard "$SHARD" --shards "$SHARDS" \
               --out-all "$DATA/events_all_s$SHARD.jsonl" --out "$DATA/events_s$SHARD.jsonl" >> "$PIPE/events.log" 2>&1
             log "events exit $?" ;;
    selftest) stage selftest
             mem=$(gpu_mem_gb); tp=2; [ "$mem" -ge 120 ] && tp=1
             for m in qwen32b glm47flash; do
               "$PY" "$DIR/arms.py" selftest --model "$m" --tp "$tp" \
                 --selftest-out "$PIPE/selftest_${m}_tp$tp.json" > "$PIPE/selftest_$m.log" 2>&1 \
                 || { log "selftest $m tp=$tp FAILED -- stopping before the arms"; exit 3; }
             done
             log "selftests ok (tp=$tp)" ;;
    arms32)  stage arms32; arms_step qwen32b 2 ;;
    arms8)   stage arms8; arms_step qwen8b 1 ;;
    armsglm) stage armsglm; arms_step glm47flash 2 ;;
    *) log "unknown step $step"; exit 2 ;;
  esac
done
stage done
