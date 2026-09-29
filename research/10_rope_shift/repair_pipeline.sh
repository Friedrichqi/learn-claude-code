#!/usr/bin/env bash
# Part B of 10_rope_shift (repair arms, repair.py) inside a Slurm allocation: 2 GPUs, any H100/H200 node.
#   selftest   override-mechanism gates R1-R5 per model at this node's TP layout; a failure stops the job
#   repair32 / repair8 / repairglm   the repair arms on Qwen3-32B / Qwen3-8B / GLM-4.7-Flash (resumable)
# Usage: bash research/10_rope_shift/repair_pipeline.sh SHARD SHARDS step [step ...]
# Events are split SHARD-of-SHARDS, then over the node's engines (one per GPU on >=120 GB cards, else
# TP=2 for the 32B and GLM). Progress: data/rope_shift_repair/pipeline/shard<S>/{log.txt,stage}.
set -uo pipefail

SHARD=${1:?shard}; SHARDS=${2:?shards}; shift 2
REPO=/mnt/home/yqi10/learn-claude-code
DIR=$REPO/research/10_rope_shift
DATA=$DIR/data/rope_shift_repair
PIPE=$DATA/pipeline/shard$SHARD
PY="$HOME/.venv/bin/python"
mkdir -p "$PIPE"
cd "$REPO" || exit 1
export HF_HOME=/projects/co/jlin4/agenticllm/hf_cache HF_HUB_OFFLINE=1 TOKENIZERS_PARALLELISM=false
export PYTHONUNBUFFERED=1 VLLM_LOGGING_LEVEL=${VLLM_LOGGING_LEVEL:-WARNING}

log() { echo "$(date -u +%FT%TZ) shard=$SHARD $*" | tee -a "$PIPE/log.txt"; }
stage() { echo "$1" > "$PIPE/stage"; log "stage $1"; }
gpu_mem_gb() { nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1 | awk '{print int($1/1024)}'; }
ngpus() { nvidia-smi -L | wc -l; }
tp_for() { local mem; mem=$(gpu_mem_gb); if [ "$1" = qwen8b ] || [ "$mem" -ge 120 ]; then echo 1; else echo 2; fi; }
trap 'log "TERM received"; trap - TERM INT; kill 0; exit 143' TERM INT

repair_step() {   # model
  local model=$1 tp n procs sub devs
  tp=$(tp_for "$model"); n=$(ngpus); procs=$((n / tp))
  log "repair $model: $procs engine(s) at TP=$tp"
  for sub in $(seq 0 $((procs - 1))); do
    devs=$(seq -s, $((sub * tp)) $((sub * tp + tp - 1)))
    CUDA_VISIBLE_DEVICES=$devs "$PY" "$DIR/repair.py" run --model "$model" --tp "$tp" \
      --shard "$SHARD" --shards "$SHARDS" --subshard "$sub" --subshards "$procs" \
      > "$PIPE/repair_${model}_$sub.log" 2>&1 &
  done
  wait
  log "repair $model done"
}

for step in "$@"; do
  case $step in
    selftest) stage selftest
              for m in qwen8b qwen32b glm47flash; do
                tp=$(tp_for "$m"); ok="$DATA/selftest_${m}_tp$tp.json"
                if [ -f "$ok" ] && grep -q '"ok": true' "$ok"; then log "selftest $m tp=$tp passed earlier"; continue; fi
                CUDA_VISIBLE_DEVICES=$(seq -s, 0 $((tp - 1))) "$PY" "$DIR/repair.py" selftest --model "$m" --tp "$tp" \
                  --out "$PIPE/selftest_${m}_tp$tp.json" > "$PIPE/selftest_$m.log" 2>&1 \
                  || { log "selftest $m tp=$tp FAILED -- stopping before the repair arms"; exit 3; }
                cp "$PIPE/selftest_${m}_tp$tp.json" "$ok"
                log "selftest $m tp=$tp ok"
              done ;;
    repair32)  stage repair32; repair_step qwen32b ;;
    repair8)   stage repair8; repair_step qwen8b ;;
    repairglm) stage repairglm; repair_step glm47flash ;;
    *) log "unknown step $step"; exit 2 ;;
  esac
done
stage done
