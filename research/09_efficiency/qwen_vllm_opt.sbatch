#!/bin/bash
#SBATCH --job-name=qwen-opt
#SBATCH --partition=alpha
#SBATCH --account=co_jlin4_agenticllm
#SBATCH --qos=priority
#SBATCH --gres=gpu:rtx_pro_6000_blackwell:1
#SBATCH --cpus-per-task=16
#SBATCH --mem=160G
#SBATCH --time=08:00:00
#SBATCH --signal=B:TERM@900
#SBATCH --output=/mnt/home/yqi10/learn-claude-code/research/09_efficiency/data/latency_profiling_qwen_opt/slurm-%j.out
# Optimized arm of the efficiency methodology (research/09_efficiency/README.md Part A),
# A/B against the existing baseline in research/05_latency_breakdown/data/latency_profiling_qwen:
#   Step 1  async memory extraction + per-turn recall      (harness edits, always on)
#   Step 2  per-round reasoning-effort policy              (EFFORT=1, probed knob)
#   Step 3  complete-coverage inheritance, LFU/32k         (INHERIT=1 + inherit arms below)
#   Step 4  re-acquisition guard + stable prefix           (PREFIX_STABLE=1; guard always on)
#   Step 5  batching hint                                  (harness edit, always on)
#   Step 6  vLLM + LMCache + teammate concurrency cap      (LMCACHE=1, TEAMMATE_CAP=2)
# The pipeline is idempotent/resumable: resubmit the same sbatch after a crash.
cd /mnt/home/yqi10/learn-claude-code || exit 1
export OPT=1
export KEEP_SERVER=1   # the inherit arms below reuse the server; the final `stop` step shuts it down
bash research/05_latency_breakdown/qwen_vllm_pipeline.sh download serve effprobe lane probe smoke matrix tables compare

REPO=/mnt/home/yqi10/learn-claude-code
OUT=$REPO/research/09_efficiency/data/latency_profiling_qwen_opt
PORT=$(cat "$OUT/pipeline/port")
# Step 3 on dependency-shaped work the matrix cannot express (independent tasks by design):
# none vs ancestors(LFU, 32k), 2 reps, identical optimized harness in both arms.
python3 $REPO/research/08_context_inheritance/inherit_workloads.py \
    --arms none,ancestors --reps 2 \
    --repo "$HOME/lanes/latency_qwen" \
    --trace-dir "$OUT/inherit" \
    --driver-arg=--prewarm-evict=lfu --driver-arg=--prewarm-budget=32k \
    --driver-arg=--no-timestamp \
    --driver-arg="--vllm-metrics=http://127.0.0.1:$PORT/metrics" \
  || echo "[opt] inherit arms failed (see $OUT/inherit)"
bash $REPO/research/05_latency_breakdown/qwen_vllm_pipeline.sh stop
