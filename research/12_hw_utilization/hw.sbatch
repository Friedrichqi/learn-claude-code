#!/bin/bash
#SBATCH --job-name=hw-util
#SBATCH --partition=alpha
#SBATCH --account=co_jlin4_agenticllm
#SBATCH --qos=standard
#SBATCH --gres=gpu:2
#SBATCH --constraint=h200
#SBATCH --cpus-per-task=24
#SBATCH --mem=160G
#SBATCH --time=06:00:00
#SBATCH --signal=B:TERM@900
#SBATCH --output=/mnt/home/yqi10/learn-claude-code/research/12_hw_utilization/data/jobs/slurm-%j.log
# One research/12_hw_utilization job on two H200s (the job-submit plugin routes every 1-GPU job to the RTX
# pool): job-wide steps first (the probe, TP=2 cells), then one lane of steps per GPU. 24 cores = 12 per
# GPU, the H200 node's own ratio (96 cores / 8 GPUs). QoS standard (user, 2026-10-02: not priority).
#   sbatch --export=NONE research/12_hw_utilization/hw.sbatch probe ab,p1a p1b                 # J0
#   sbatch --export=NONE research/12_hw_utilization/hw.sbatch - p2a,p2b p2c                    # J1
#   sbatch --export=NONE research/12_hw_utilization/hw.sbatch - k1h,k1l k2,k3                  # J2
#   sbatch --export=NONE --mem=320G research/12_hw_utilization/hw.sbatch t2,k2t o1 -          # J3
# Resubmitting the same arguments resumes (finished cells and levels are skipped).
cd /mnt/home/yqi10/learn-claude-code || exit 1
exec bash research/12_hw_utilization/hw_pipeline.sh "${1:--}" "${2:--}" "${3:--}"
