#!/bin/bash
#SBATCH --job-name=tm-probe
#SBATCH --partition=alpha
#SBATCH --account=co_jlin4_agenticllm
#SBATCH --qos=standard
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=16
#SBATCH --mem=160G
#SBATCH --time=02:00:00
#SBATCH --output=/mnt/home/yqi10/learn-claude-code/research/13_tiered_memory/data/jobs/slurm-probe-%j.log
# Stage S2a of research/13_tiered_memory on ONE GPU (Slurm puts 1-GPU jobs on an RTX PRO 6000):
#   sbatch --export=NONE research/13_tiered_memory/tm_probe.sbatch [models...]
# 1. tm_calib.py all: node facts, PCIe, vLLM's block-copy kernel, local flash (O_DIRECT), peer (skipped on 1 GPU)
# 2. per model (default Qwen/Qwen3-32B Qwen/Qwen3-8B): vLLM with 11/12's flags plus a 64 GiB native CPU tier;
#    tm_vllm_probe.py: KV capacity, prefill / decode rate, restore from DRAM vs recompute.
set -uo pipefail
REPO=/mnt/home/yqi10/learn-claude-code
DIR=$REPO/research/13_tiered_memory
JOB=${SLURM_JOB_ID:-local$$}
OUT=$DIR/data/probe/$JOB
PY=$HOME/.venv/bin/python
mkdir -p "$OUT"
cd "$REPO" || exit 1
export HF_HOME=/projects/co/jlin4/agenticllm/hf_cache HF_HUB_OFFLINE=1 TOKENIZERS_PARALLELISM=false
export PYTHONUNBUFFERED=1 CUDA_DEVICE_ORDER=PCI_BUS_ID PATH="$HOME/.venv/bin:$PATH" VIRTUAL_ENV="$HOME/.venv"
log() { echo "$(date -u +%FT%TZ) job=$JOB $*"; }
log "host $(hostname) gpus=${CUDA_VISIBLE_DEVICES:-?} cpus=$(nproc)"
"$PY" "$DIR/tm_calib.py" all --out "$OUT" --gib 8 > "$OUT/calib.log" 2>&1
log "calib done: $(ls "$OUT")"
MODELS=("$@"); [ ${#MODELS[@]} -eq 0 ] && MODELS=(Qwen/Qwen3-32B Qwen/Qwen3-8B)
for M in "${MODELS[@]}"; do
  tag=$(basename "$M" | tr 'A-Z' 'a-z')
  port=$("$PY" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
  base=http://127.0.0.1:$port
  log "serve $M on :$port"
  VLLM_SERVER_DEV_MODE=1 setsid vllm serve "$M" --host 127.0.0.1 --port "$port" --served-model-name "$M" \
    --max-model-len 40960 --enable-prefix-caching --enable-prompt-tokens-details --enable-auto-tool-choice \
    --tool-call-parser hermes --reasoning-parser qwen3 --default-chat-template-kwargs '{"enable_thinking": false}' \
    --kv-offloading-size 64 --kv-offloading-backend native --uvicorn-log-level warning > "$OUT/vllm_$tag.log" 2>&1 &
  vpid=$!
  ok=0
  for _ in $(seq 1 180); do
    if curl -sf "$base/health" >/dev/null; then ok=1; break; fi
    kill -0 "$vpid" 2>/dev/null || break
    sleep 10
  done
  if [ "$ok" = 1 ]; then
    log "$M healthy"
    timeout 1800 "$PY" "$DIR/tm_vllm_probe.py" --base "$base" --model "$M" --log "$OUT/vllm_$tag.log" \
      --out "$OUT/probe_$tag.json" --offload > "$OUT/probe_$tag.log" 2>&1
    log "$M probe rc=$?"
  else
    log "$M never became healthy (see $OUT/vllm_$tag.log)"
  fi
  kill -TERM -- "-$vpid" 2>/dev/null; for _ in $(seq 1 30); do kill -0 "$vpid" 2>/dev/null || break; sleep 2; done
  kill -KILL -- "-$vpid" 2>/dev/null; sleep 5
done
log "done"
