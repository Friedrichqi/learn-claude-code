#!/bin/bash
#SBATCH --job-name=rope-shift
#SBATCH --partition=alpha
#SBATCH --account=co_jlin4_agenticllm
#SBATCH --qos=priority
#SBATCH --gres=gpu:2
#SBATCH --constraint=h100|h200
#SBATCH --cpus-per-task=12
#SBATCH --mem=64G
#SBATCH --time=12:00:00
#SBATCH --signal=B:TERM@600
#SBATCH --output=/mnt/home/yqi10/learn-claude-code/research/10_rope_shift/data/rope_shift_live/pipeline/slurm-%j.log
# One shard of the 10_rope_shift sweep on any 2-GPU H100 or H200 alpha node (H100 pairs run TP=2, H200s
# one replica per GPU; the pipeline decides). The splice connector is verified on Hopper backends only.
# Submit with --export=NONE, the shard and the shard count:
#   sbatch --export=NONE research/10_rope_shift/rope_shift.sbatch 0 6
# Resubmitting the same shard resumes it (runs, events and arms all skip finished work).
SHARD=${1:?shard}; SHARDS=${2:?shards}
cd /mnt/home/yqi10/learn-claude-code || exit 1
exec bash research/10_rope_shift/rope_shift_pipeline.sh "$SHARD" "$SHARDS" \
  ${STEPS:-serve runs stop events selftest arms32 arms8 armsglm}
