#!/bin/bash
#SBATCH --job-name=hw-cpu
#SBATCH --partition=alpha
#SBATCH --account=co_jlin4_agenticllm
#SBATCH --qos=standard
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=02:00:00
#SBATCH --output=/mnt/home/yqi10/learn-claude-code/research/12_hw_utilization/data/jobs/slurm-cpu-%j.log
# Offline analysis of research/12_hw_utilization (the login node allows 2 cores / 8 GB per user):
#   sbatch --export=NONE research/12_hw_utilization/hw_cpu.sbatch tables [--cells p1a,p2a]
#   sbatch --export=NONE research/12_hw_utilization/hw_cpu.sbatch ab
cd /mnt/home/yqi10/learn-claude-code || exit 1
export PYTHONUNBUFFERED=1
exec "$HOME/.venv/bin/python" research/12_hw_utilization/hw_analyze.py "$@"
