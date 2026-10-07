#!/bin/bash
#SBATCH --job-name=tm1
#SBATCH --partition=alpha
#SBATCH --account=co_jlin4_agenticllm
#SBATCH --qos=standard
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=16
#SBATCH --mem=160G
#SBATCH --time=03:00:00
#SBATCH --output=/mnt/home/yqi10/learn-claude-code/research/13_tiered_memory/data/jobs/slurm-tm1-%j.log
# One-GPU cells of research/13_tiered_memory (Slurm puts 1-GPU jobs on an RTX PRO 6000):
#   sbatch --export=NONE research/13_tiered_memory/tm1.sbatch s2d,s2b
exec bash /mnt/home/yqi10/learn-claude-code/research/13_tiered_memory/tm_pipeline.sh "$@"
