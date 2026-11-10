#!/usr/bin/env bash
# vLLM for topic 23: the topic venv (/data/fritz/venv23 = /data/fritz/venv's packages plus LMCache 0.5.6), its memory on
# NUMA node 1 next to GPUs 4-5. The caller pins the CPUs (taskset) and keeps them pinned against the host's numad
# (wb_cell.pin_tree).
# For the offload setups (lmc, lmc_all) ET_LMCACHE_ARGS holds `lmcache server` arguments: LMCache's multiprocess
# server starts first, on the same GPU, CPUs and node, in this process group (rd_serve stops the whole group), and
# vLLM starts once its health check answers (ET_LMCACHE_HTTP: its HTTP port; ET_LMCACHE_LOG: its log).
set -euo pipefail
if [ -n "${ET_LMCACHE_ARGS:-}" ]; then
  # shellcheck disable=SC2086
  LMCACHE_DISABLE_BANNER=1 numactl --membind=1 /data/fritz/venv23/bin/lmcache server $ET_LMCACHE_ARGS \
    > "${ET_LMCACHE_LOG:-/dev/null}" 2>&1 &
  LMC=$!
  up=0
  for _ in $(seq 1 150); do
    if ! kill -0 "$LMC" 2>/dev/null; then break; fi
    if curl -sf -m 2 "http://127.0.0.1:${ET_LMCACHE_HTTP}/healthcheck" >/dev/null; then up=1; break; fi
    sleep 2
  done
  if [ "$up" != 1 ]; then
    echo "et_vllm.sh: LMCache's server did not come up (see ${ET_LMCACHE_LOG:-its log}); not starting vLLM" >&2
    kill "$LMC" 2>/dev/null || true
    exit 1
  fi
fi
exec numactl --membind=1 /data/fritz/venv23/bin/vllm "$@"
