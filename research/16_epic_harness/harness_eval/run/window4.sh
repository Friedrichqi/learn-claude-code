#!/usr/bin/env bash
# The 4-GPU window: once the SWE-bench pilot (GPU 3) is done, drain the 1-GPU stages at chunk boundaries
# (stop files), then qualify DeepSeek-V4-Flash at TP=4 (backup GLM-4.7-Flash if it will not serve), then
# MiniMax-M2.7. Every stage waits for its GPUs to be empty and stops its server when its batches end.
cd /data/fritz/learn-claude-code/research/16_epic_harness/harness_eval
PILOT_PID=${1:-3869070}
CLAIM=("2026-10-11 22:00" "2026-10-12 23:30")
log() { echo "$(date -u +%FT%TZ) $*"; }

while kill -0 "$PILOT_PID" 2>/dev/null; do sleep 30; done
log "pilot stage finished; draining 1-GPU stages"
touch run/state/stop/qwen3-32b run/state/stop/qwen3.8-27b

log "DeepSeek-V4-Flash qualification (TP=4)"
.venv/bin/python run/stage.py --model deepseek-v4-flash --gpus 0,1,2,3 --claim "${CLAIM[@]}" \
  --batch tbench:q3:qual:1:8 --batch swe:q3:qual:1:6 --batch swepro:q3:qual:1:4 > runs/stage_q3_deepseek.log 2>&1
if grep -q "exited during startup\|not healthy" runs/stage_q3_deepseek.log; then
  log "DeepSeek-V4-Flash did not serve on GPUs 0-3; GLM-4.7-Flash is the backup (1 GPU, started after MiniMax)"
  touch runs/deepseek_failed
fi

log "MiniMax-M2.7 qualification (TP=4)"
.venv/bin/python run/stage.py --model minimax-m2.7 --gpus 0,1,2,3 --claim "${CLAIM[@]}" \
  --batch tbench:q3:qual:1:8 --batch swe:q3:qual:1:6 --batch swepro:q3:qual:1:4 > runs/stage_q3_minimax.log 2>&1
log "4-GPU window done; removing stop files"
rm -f run/state/stop/qwen3-32b run/state/stop/qwen3.8-27b
