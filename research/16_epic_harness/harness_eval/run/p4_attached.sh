#!/usr/bin/env bash
# Profile a model on an already-running server (adopted from its qualification stage): plan the missing trials
# per benchmark (run/plan_p4.py), run each benchmark's batch specs in sequence (benchmarks in parallel), and
# stop the server when every batch has returned.
#   p4_attached.sh MODEL "swe=30,swepro=30,tbench=30" [CONC]
set -u
cd /data/fritz/learn-claude-code/research/16_epic_harness/harness_eval
MODEL=$1; N=$2; CONC=${3:-"swe=6,swepro=4,tbench=8"}
URL=$(python3 -c "import json;print(json.load(open('runs/servers/$MODEL.json'))['url'])")
log() { echo "$(date -u +%FT%TZ) $*"; }
log "profile $MODEL on $URL: $N"
pids=()
for item in ${N//,/ }; do
  bench=${item%%=*}
  (
    for spec in $(.venv/bin/python run/plan_p4.py --model "$MODEL" --n "$item" --conc "$CONC"); do
      IFS=: read -r b phase tasks k conc <<< "$spec"
      log "$MODEL $bench: $phase k=$k"
      .venv/bin/python run/batch.py --model "$MODEL" --url "$URL" --bench "$b" --phase "$phase" --tasks "$tasks" --k "$k" --concurrency "$conc"
    done
    log "$MODEL $bench: done"
  ) &
  pids+=($!)
done
wait "${pids[@]}"
# the qualification stage's own batch may still be running on this server: wait for it too
while ps -eo args | grep -q "[r]un/batch.py --model $MODEL --url $URL"; do sleep 30; done
log "all $MODEL batches returned; stopping its server"
.venv/bin/python serve/serve.py stop "$MODEL"
