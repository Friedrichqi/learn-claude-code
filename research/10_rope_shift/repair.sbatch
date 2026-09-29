#!/bin/bash
#SBATCH --job-name=rope-repair
#SBATCH --partition=alpha
#SBATCH --account=co_jlin4_agenticllm
#SBATCH --qos=priority
#SBATCH --gres=gpu:2
#SBATCH --constraint=h100|h200
#SBATCH --cpus-per-task=12
#SBATCH --mem=64G
#SBATCH --time=12:00:00
#SBATCH --signal=B:TERM@600
#SBATCH --output=/mnt/home/yqi10/learn-claude-code/research/10_rope_shift/data/rope_shift_repair/pipeline/slurm-%j.log
# One shard of 10_rope_shift Part B (repair arms) on any 2-GPU H100 or H200 alpha node.
#   sbatch --export=NONE research/10_rope_shift/repair.sbatch 0 4 [step ...]
# Steps default to "selftest repair32 repair8 repairglm" (--export=NONE cannot carry variables, so
# they are arguments). Resubmitting a shard resumes it: every step skips finished events.
SHARD=${1:?shard}; SHARDS=${2:?shards}; shift 2
[ $# -gt 0 ] || set -- selftest repair32 repair8 repairglm
cd /mnt/home/yqi10/learn-claude-code || exit 1
exec bash research/10_rope_shift/repair_pipeline.sh "$SHARD" "$SHARDS" "$@"
