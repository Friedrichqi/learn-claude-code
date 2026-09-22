#!/bin/bash
#SBATCH --job-name=qwen-latency
#SBATCH --partition=alpha
#SBATCH --account=co_jlin4_agenticllm
#SBATCH --qos=priority
#SBATCH --gres=gpu:rtx_pro_6000_blackwell:1
#SBATCH --cpus-per-task=16
#SBATCH --mem=160G
#SBATCH --time=20:00:00
#SBATCH --signal=B:TERM@900
#SBATCH --output=/mnt/home/yqi10/learn-claude-code/s15_integrated_harness/traces/latency_profiling_qwen/slurm-%j.out
# One Slurm job for the whole Qwen3.8-27B / vLLM rerun of the latency-breakdown experiment.
#   sbatch s15_integrated_harness/scripts/qwen_vllm.sbatch            # all steps
#   sbatch s15_integrated_harness/scripts/qwen_vllm.sbatch matrix tables compare   # resume
# The pipeline waits for traces/latency_profiling_qwen/pipeline/READY before copying the code into the lane,
# so scripts can still be edited while the job queues and the server compiles:  touch .../pipeline/READY
# TERM arrives 15 min before the time limit: the current run is interrupted, tables are written, vLLM stops.
cd /mnt/home/yqi10/learn-claude-code || exit 1
exec bash s15_integrated_harness/scripts/qwen_vllm_pipeline.sh "$@"
