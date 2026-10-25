#!/usr/bin/env bash
# EPIC round, stage C (design and selftests only): DeepSeek-V4-Flash with the design-B connector (epic/epic_dsv4.py) on
# GPUs 0-3 (TP=4, eager). Waits for the GPUs to be empty, starts the server, runs the live selftests (--format dsv4),
# saves the per-rank connector counters, stops the server. Exit code = the selftests' (1 also if the server fails).
#
#   setsid nohup bash run/stage_c.sh >> runs/epic/stage_c.log 2>&1 < /dev/null &
set -u
cd /data/fritz/learn-claude-code/research/16_epic_harness/harness_eval
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }
until .venv/bin/python serve/serve.py empty 0,1,2,3; do sleep 60; done
log "starting deepseek-v4-flash-epic on GPUs 0-3"
if ! .venv/bin/python serve/serve.py start deepseek-v4-flash-epic --gpus 0,1,2,3; then
  log "server failed to start"; exit 1
fi
URL=$(python3 -c "import json;print(json.load(open('runs/servers/deepseek-v4-flash-epic.json'))['url'])")
log "ready on $URL; selftests"
/data/fritz/venv/bin/python epic/selftest_live.py --url "$URL" --format dsv4 --out runs/epic/selftest_dsv4.json
rc=$?
curl -s --max-time 120 "$URL/epic/workers" > runs/epic/workers_dsv4.json
curl -s --max-time 30 "$URL/epic/stats" > runs/epic/stats_dsv4.json
log "selftests rc=$rc; stopping the server"
.venv/bin/python serve/serve.py stop deepseek-v4-flash-epic
log "stage C done"
exit $rc
