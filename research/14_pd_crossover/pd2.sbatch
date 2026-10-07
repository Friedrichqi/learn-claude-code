#!/bin/bash
#SBATCH --job-name=pd2
#SBATCH --partition=alpha
#SBATCH --account=co_jlin4_agenticllm
#SBATCH --qos=standard
#SBATCH --gres=gpu:2
#SBATCH --constraint=h200
#SBATCH --cpus-per-task=32
#SBATCH --mem=192G
#SBATCH --time=12:00:00
#SBATCH --output=/mnt/home/yqi10/learn-claude-code/research/14_pd_crossover/data/jobs/slurm-pd2-%j.log
# Two-GPU cells of research/14_pd_crossover (every arm needs both GPUs: DP=2, TP=2 or 1P1D). PLAN.md §5:
#   sbatch --export=NONE --time=04:00:00 research/14_pd_crossover/pd2.sbatch s1
#   sbatch --export=NONE --time=08:30:00 research/14_pd_crossover/pd2.sbatch l2a
exec bash /mnt/home/yqi10/learn-claude-code/research/14_pd_crossover/pd_pipeline.sh "$@"
