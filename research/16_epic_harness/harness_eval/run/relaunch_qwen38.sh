#!/usr/bin/env bash
# Wait for the old 150-call Qwen3.8 qualification stage to drain (stop file), then start the 250-call one.
cd /data/fritz/learn-claude-code/research/16_epic_harness/harness_eval
while kill -0 3870508 2>/dev/null; do sleep 20; done
rm -f run/state/stop/qwen3.8-27b
echo "$(date -u +%FT%TZ) old stage gone; launching q3 stage"
exec .venv/bin/python run/stage.py --model qwen3.8-27b --instance g1 --gpus 1 \
  --claim "2026-10-11 22:00" "2026-10-12 23:30" \
  --batch tbench:q3:qual:1:6 --batch swe:q3:qual:1:4 --batch swepro:q3:qual:1:2
