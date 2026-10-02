#!/bin/bash
#SBATCH --job-name=tool-share-cpu
#SBATCH --partition=alpha
#SBATCH --account=co_jlin4_agenticllm
#SBATCH --qos=priority
#SBATCH --cpus-per-task=32
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --output=/mnt/home/yqi10/learn-claude-code/research/06_tool_cost/data/tool_census/slurm-%x-%j.log
# CPU-only work of research/06_tool_cost Part C that the login node (2 cores, 8 GB) cannot do:
#   sbatch --export=NONE research/06_tool_cost/tool_share_cpu.sbatch census            # C1, ~20 min
#   sbatch --export=NONE research/06_tool_cost/tool_share_cpu.sbatch sweep r1          # C2, one run (~2 h)
#   sbatch --export=NONE research/06_tool_cost/tool_share_cpu.sbatch tables            # C5 tables
cd /mnt/home/yqi10/learn-claude-code || exit 1
export PYTHONUNBUFFERED=1 PATH="$HOME/.venv/bin:$PATH"
PY=$HOME/.venv/bin/python
cmd=${1:?census|sweep|tables}; shift
case $cmd in
  census) $PY research/06_tool_cost/tool_census.py --workers 30 "$@" ;;
  sweep)  bash research/06_tool_cost/tool_sweep_run.sh "$@" ;;
  tables) $PY research/06_tool_cost/tool_share_analyze.py "$@" ;;
  *) echo "unknown command $cmd"; exit 2 ;;
esac
