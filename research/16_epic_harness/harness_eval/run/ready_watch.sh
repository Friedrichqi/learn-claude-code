#!/usr/bin/env bash
# Touch runs/ready_<model> once its download has completed (the downloader logs "<repo> -> <path>").
cd /data/fritz/learn-claude-code/research/16_epic_harness/harness_eval
declare -A REPO=([qwen3.8-27b]="Qwen/Qwen3.8-27B-FP8" [deepseek-v4-flash]="deepseek-ai/DeepSeek-V4-Flash-0731" [minimax-m2.7]="MiniMaxAI/MiniMax-M2.7")
while true; do
  left=0
  for m in "${!REPO[@]}"; do
    [ -e "runs/ready_$m" ] && continue
    if cat runs/download*.log 2>/dev/null | tr '\r' '\n' | grep -q "${REPO[$m]} -> "; then touch "runs/ready_$m"; echo "$(date -u +%T) ready $m"; else left=1; fi
  done
  [ $left -eq 0 ] && exit 0
  sleep 60
done
