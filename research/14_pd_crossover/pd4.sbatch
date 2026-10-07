#!/bin/bash
#SBATCH --job-name=pd4
#SBATCH --partition=alpha
#SBATCH --account=co_jlin4_agenticllm
#SBATCH --qos=standard
#SBATCH --nodes=1
#SBATCH --gres=gpu:4
#SBATCH --constraint=h200
#SBATCH --cpus-per-task=48
#SBATCH --mem=320G
#SBATCH --time=12:00:00
#SBATCH --output=/mnt/home/yqi10/learn-claude-code/research/14_pd_crossover/data/jobs/slurm-pd4-%j.log
# Four-GPU cells of research/14_pd_crossover (DP=4, TP=2x2, TP=4, xPyD). PLAN.md §5:
#   sbatch --export=NONE --time=03:30:00 research/14_pd_crossover/pd4.sbatch s3
#   sbatch --export=NONE research/14_pd_crossover/pd4.sbatch l4a
exec bash /mnt/home/yqi10/learn-claude-code/research/14_pd_crossover/pd_pipeline.sh "$@"
