#!/usr/bin/env bash
# vLLM for topic 22's server, with its memory on NUMA node 0 next to GPUs 0-3. rd_serve runs it under
# `taskset -c wb_cell.SERVER_CPUS`; both pass on to vLLM's worker processes at start. The host's numad daemon can
# later move them and their memory to either node (wb_cell.pin_tree).
exec numactl --membind=0 /data/fritz/venv/bin/vllm "$@"
