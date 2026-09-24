#!/bin/bash
#SBATCH --job-name=qwen-inherit
#SBATCH --partition=alpha
#SBATCH --account=co_jlin4_agenticllm
#SBATCH --qos=priority
#SBATCH --gres=gpu:rtx_pro_6000_blackwell:1
#SBATCH --cpus-per-task=16
#SBATCH --mem=160G
#SBATCH --time=03:00:00
#SBATCH --signal=B:TERM@900
#SBATCH --output=/mnt/home/yqi10/learn-claude-code/s15_integrated_harness/traces/latency_profiling_qwen_opt/slurm-inherit-%j.out
# Step 3 fix-up: the first pass of the inherit arms ran under the system python (no anthropic
# module) because only the pipeline script activates the venv.  This rerun uses $VENV/bin/python
# explicitly.  Server flags are identical to the matrix (LMCACHE=0: the connector was already
# rejected by the hybrid model and the matrix ran on native prefix caching).
cd /mnt/home/yqi10/learn-claude-code || exit 1
VENV=$HOME/.venv
REPO=/mnt/home/yqi10/learn-claude-code
OUT=$REPO/s15_integrated_harness/traces/latency_profiling_qwen_opt
export OPT=1 LMCACHE=0 KEEP_SERVER=1
bash $REPO/s15_integrated_harness/scripts/qwen_vllm_pipeline.sh serve || exit 1
PORT=$(cat "$OUT/pipeline/port")
"$VENV/bin/python" $REPO/s15_integrated_harness/scripts/inherit_workloads.py \
    --arms none,ancestors --reps 2 \
    --repo "$HOME/lanes/latency_qwen" \
    --trace-dir "$OUT/inherit" \
    --driver-arg=--prewarm-evict=lfu --driver-arg=--prewarm-budget=32k \
    --driver-arg=--no-timestamp \
    --driver-arg="--vllm-metrics=http://127.0.0.1:$PORT/metrics"
bash $REPO/s15_integrated_harness/scripts/qwen_vllm_pipeline.sh stop
