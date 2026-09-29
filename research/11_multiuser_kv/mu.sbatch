#!/bin/bash
#SBATCH --job-name=mu-kv
#SBATCH --partition=alpha
#SBATCH --account=co_jlin4_agenticllm
#SBATCH --qos=priority
#SBATCH --gres=gpu:2
#SBATCH --constraint=h200
#SBATCH --cpus-per-task=32
#SBATCH --mem=160G
#SBATCH --time=12:00:00
#SBATCH --signal=B:TERM@900
#SBATCH --output=/mnt/home/yqi10/learn-claude-code/research/11_multiuser_kv/data/multiuser_live/jobs/slurm-%j.log
# One 11_multiuser_kv job: two H200s, one independent cell per GPU (the job-submit plugin routes every
# 1-GPU job to the RTX 6000 pool, so single-H200 cells come in pairs). Conditions per GPU, in order:
#   sbatch --export=NONE research/11_multiuser_kv/mu.sbatch n01a,n04 n01b,n08
# Resubmitting the same arguments resumes (finished cells are skipped, sessions resume by label).
# Replay jobs with the CPU-offload arm need more host memory: sbatch --mem=320G ...
cd /mnt/home/yqi10/learn-claude-code || exit 1
exec bash research/11_multiuser_kv/mu_pipeline.sh "${1:?GPU0 conditions}" "${2:--}"
