#!/bin/bash
#SBATCH --job-name=tm2
#SBATCH --partition=alpha
#SBATCH --account=co_jlin4_agenticllm
#SBATCH --qos=standard
#SBATCH --gres=gpu:2
#SBATCH --constraint=h200
#SBATCH --cpus-per-task=32
#SBATCH --mem=320G
#SBATCH --time=12:00:00
#SBATCH --output=/mnt/home/yqi10/learn-claude-code/research/13_tiered_memory/data/jobs/slurm-tm2-%j.log
# Two-GPU cells of research/13_tiered_memory, ONLY for configurations that need both GPUs (DP=2, P/D, TP=2,
# a peer-HBM tier): S4 calibration (cell s4), S5 B2 / B3.
#   sbatch --export=NONE research/13_tiered_memory/tm2.sbatch s4
#   sbatch --export=NONE research/13_tiered_memory/tm2.sbatch b3
exec bash /mnt/home/yqi10/learn-claude-code/research/13_tiered_memory/tm2_pipeline.sh "$@"
