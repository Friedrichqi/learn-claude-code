#!/bin/bash
#SBATCH --job-name=rope-latency
#SBATCH --partition=alpha
#SBATCH --account=co_jlin4_agenticllm
#SBATCH --qos=priority
#SBATCH --gres=gpu:2
#SBATCH --constraint=h200
#SBATCH --cpus-per-task=12
#SBATCH --mem=64G
#SBATCH --time=05:00:00
#SBATCH --output=/mnt/home/yqi10/learn-claude-code/research/10_rope_shift/data/rope_shift_repair/latency/slurm-%j.log
# Part B latency (latency.py): time to first token of every arm, one H200 per model at TP=1:
# Qwen3-32B on GPU 0; Qwen3-8B, then GLM-4.7-Flash, on GPU 1. Resumable: timed events are skipped.
#   sbatch --export=NONE research/10_rope_shift/latency.sbatch [events per model, default 200]
EVENTS=${1:-200}
cd /mnt/home/yqi10/learn-claude-code || exit 1
export HF_HOME=/projects/co/jlin4/agenticllm/hf_cache HF_HUB_OFFLINE=1 TOKENIZERS_PARALLELISM=false
export PYTHONUNBUFFERED=1 VLLM_LOGGING_LEVEL=WARNING
R=research/10_rope_shift; L=$R/data/rope_shift_repair/latency
nvidia-smi --query-gpu=index,name,memory.total --format=csv > "$L/gpus.txt"
CUDA_VISIBLE_DEVICES=0 "$HOME/.venv/bin/python" $R/latency.py run --model qwen32b --events "$EVENTS" \
  > "$L/run_qwen32b.log" 2>&1 &
( CUDA_VISIBLE_DEVICES=1 "$HOME/.venv/bin/python" $R/latency.py run --model qwen8b --events "$EVENTS" \
    > "$L/run_qwen8b.log" 2>&1
  CUDA_VISIBLE_DEVICES=1 "$HOME/.venv/bin/python" $R/latency.py run --model glm47flash --events "$EVENTS" \
    > "$L/run_glm47flash.log" 2>&1 ) &
wait
