#!/bin/bash
#SBATCH --job-name=tm-cpu
#SBATCH --partition=alpha
#SBATCH --account=co_jlin4_agenticllm
#SBATCH --qos=standard
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=02:00:00
#SBATCH --output=/mnt/home/yqi10/learn-claude-code/research/13_tiered_memory/data/jobs/slurm-cpu-%j.log
# Offline work of research/13_tiered_memory (the login node allows 2 cores / 8 GB per user). No GPU.
#   sbatch --export=NONE research/13_tiered_memory/tm_cpu.sbatch tm_ceilings.py [--cells p2a,n16]
#   sbatch --export=NONE research/13_tiered_memory/tm_cpu.sbatch tm_predict.py
cd /mnt/home/yqi10/learn-claude-code || exit 1
export PYTHONUNBUFFERED=1
script="$1"; shift
exec "$HOME/.venv/bin/python" "research/13_tiered_memory/$script" "$@"
