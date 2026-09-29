#!/bin/bash
#SBATCH --job-name=mu-cpu
#SBATCH --partition=alpha
#SBATCH --account=co_jlin4_agenticllm
#SBATCH --qos=priority
#SBATCH --cpus-per-task=32
#SBATCH --mem=96G
#SBATCH --time=02:00:00
#SBATCH --output=/mnt/home/yqi10/learn-claude-code/research/11_multiuser_kv/data/multiuser_live/jobs/slurm-cpu-%j.log
# CPU-only work of 11_multiuser_kv that the login node cannot do (2 cores and 8 GB per user there):
#   sbatch --export=NONE research/11_multiuser_kv/mu_cpu.sbatch prepare n16 n32     # replay plans
#   sbatch --export=NONE research/11_multiuser_kv/mu_cpu.sbatch analyze [--cond ...] # live tables
#   sbatch --export=NONE research/11_multiuser_kv/mu_cpu.sbatch replay               # replay tables
cd /mnt/home/yqi10/learn-claude-code || exit 1
export HF_HOME=/projects/co/jlin4/agenticllm/hf_cache HF_HUB_OFFLINE=1 TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1
PY=$HOME/.venv/bin/python
cmd=${1:?prepare|analyze|replay}; shift
case $cmd in
  prepare) for src in "$@"; do $PY research/11_multiuser_kv/mu_replay.py prepare --src "$src" --workers 24 || exit 1; done ;;
  analyze) $PY research/11_multiuser_kv/mu_analyze.py "$@" ;;
  replay)  $PY research/11_multiuser_kv/mu_analyze.py --replay "$@" ;;
  *) echo "unknown command $cmd"; exit 2 ;;
esac
