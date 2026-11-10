#!/usr/bin/env bash
# LMCache's multiprocess server for topic 23 (lmcache 0.5.6 in /data/fritz/venv23), its L1 (pinned host memory) on
# NUMA node 1 next to GPUs 4-5. It needs the same GPU as its vLLM (CUDA IPC); the caller sets CUDA_VISIBLE_DEVICES
# and pins the CPUs.
export LMCACHE_DISABLE_BANNER=1
exec numactl --membind=1 /data/fritz/venv23/bin/lmcache server "$@"
