#!/bin/bash
#SBATCH --job-name=tool-share
#SBATCH --partition=alpha
#SBATCH --account=co_jlin4_agenticllm
#SBATCH --qos=priority
#SBATCH --gres=gpu:2
#SBATCH --constraint=h100|h200
#SBATCH --cpus-per-task=32
#SBATCH --mem=200G
#SBATCH --time=12:00:00
#SBATCH --signal=B:TERM@900
#SBATCH --output=/mnt/home/yqi10/learn-claude-code/research/06_tool_cost/data/tool_e2e/jobs/slurm-%j.log
# research/06_tool_cost Part C GPU cells: two H100/H200s, one independent cell per GPU (every 1-GPU job
# is routed to the RTX 6000 pool, which is draining for maintenance since 2026-09-29).  Cells are
# <arm>-<part>, arm q27 (Qwen3.8-27B, thinking) | q8 (Qwen3-8B, no thinking), part model | delay | heavy:
#   sbatch --export=NONE research/06_tool_cost/tool_share_gpu.sbatch q27-delay q8-heavy
# Resubmitting the same arguments resumes (completed sessions and parts are skipped).
cd /mnt/home/yqi10/learn-claude-code || exit 1
exec bash research/06_tool_cost/tool_share_pipeline.sh "${1:?GPU0 cell}" "${2:--}"
