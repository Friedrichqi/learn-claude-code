#!/bin/bash
#SBATCH --job-name=pd-cpu
#SBATCH --partition=alpha
#SBATCH --account=co_jlin4_agenticllm
#SBATCH --qos=standard
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=03:00:00
#SBATCH --output=/mnt/home/yqi10/learn-claude-code/research/14_pd_crossover/data/jobs/slurm-cpu-%j.log
# Offline work of research/14_pd_crossover (the login node allows 2 cores / 8 GB per user). No GPU.
#   sbatch --export=NONE research/14_pd_crossover/pd_cpu.sbatch pd_predict.py
#   sbatch --export=NONE research/14_pd_crossover/pd_cpu.sbatch pd_replay.py prepare --levels DIR... --pool l2
cd /mnt/home/yqi10/learn-claude-code || exit 1
export PYTHONUNBUFFERED=1 HF_HOME=/projects/co/jlin4/agenticllm/hf_cache HF_HUB_OFFLINE=1 TOKENIZERS_PARALLELISM=false
script="$1"; shift
exec "$HOME/.venv/bin/python" "research/14_pd_crossover/$script" "$@"
