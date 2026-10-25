#!/usr/bin/env bash
# Profile one model on its own server, end to end.
#   profile.sh MODEL[@INSTANCE] GPUS "swe=30,swepro=30,tbench=30" [CONC] [ROUNDS]
#
# 1. Adopt the server if it is already up (runs/servers/KEY.json); otherwise wait for GPUS to be empty and
#    start it.
# 2. Per benchmark, in parallel:
#    - wait for that benchmark's batches already on the server, and for the runner pid named in
#      WAIT_PIDS="bench=pid,...";
#    - plan the missing trials with run/plan_p4.py (every task of the first N reaches 3 valid trials;
#      infrastructure failures do not count) and run them;
#    - repeat for up to ROUNDS rounds. Each round gets a phase name (q4, q5, ...) that no earlier batch of this
#      model used for that benchmark, so batch.py never mistakes a new chunk for a finished one.
# 3. Once every benchmark is done and no batch is left on the server, stop the server. The server is also
#    stopped when this script is terminated (not on SIGKILL).
set -u
cd /data/fritz/learn-claude-code/research/16_epic_harness/harness_eval
KEY=$1; GPUS=$2; N=$3; CONC=${4:-"swe=6,swepro=4,tbench=8"}; ROUNDS=${5:-3}
MODEL=${KEY%@*}; INSTANCE=""; [ "$KEY" != "$MODEL" ] && INSTANCE=${KEY#*@}
STOP=run/state/stop
PY=.venv/bin/python
log() { echo "$(date -u +%FT%TZ) $*"; }
stopped() { [ -e $STOP/all ] || [ -e "$STOP/$MODEL" ] || [ -e "$STOP/$KEY" ]; }

trap 'exit 143' TERM INT
trap 'log "stopping $KEY: $($PY serve/serve.py stop "$KEY" 2>&1 | tail -1)"' EXIT
if [ -f "runs/servers/$KEY.json" ]; then
  URL=$(python3 -c "import json;print(json.load(open('runs/servers/$KEY.json'))['url'])")
  log "adopting the running $KEY server on $URL"
else
  until $PY serve/serve.py empty "$GPUS"; do sleep 120; done
  if stopped; then log "stop file present; not starting $KEY"; exit 0; fi
  log "starting $KEY on GPUs $GPUS"
  URL=$($PY serve/serve.py start "$MODEL" --gpus "$GPUS" ${INSTANCE:+--instance "$INSTANCE"} | tail -1 |
        python3 -c "import json,sys;print(json.load(sys.stdin)['url'])" 2>/dev/null)
  if [ -z "$URL" ]; then log "server $KEY did not start"; exit 1; fi
  log "server $KEY ready on $URL"
fi
busy() { ps -eo args | grep -q "[r]un/batch.py --model $MODEL --url $URL${1:+ --bench $1 }"; }
declare -A WAITP
W=${WAIT_PIDS:-}
for kv in ${W//,/ }; do WAITP[${kv%%=*}]=${kv#*=}; done

log "profile $KEY: $N, concurrency $CONC, up to $ROUNDS rounds"
pids=()
for item in ${N//,/ }; do
  bench=${item%%=*}
  (
    w=${WAITP[$bench]:-}
    if [ -n "$w" ]; then while kill -0 "$w" 2>/dev/null; do sleep 30; done; fi
    while busy "$bench"; do sleep 30; done
    # a stop file for this benchmark alone may be a short pause (ending one batch early): give it 10 min
    for _ in $(seq 20); do [ -e "$STOP/$bench" ] || break; sleep 30; done
    n=4
    for round in $(seq "$ROUNDS"); do
      if stopped || [ -e "$STOP/$bench" ]; then log "$KEY $bench: stop file present"; break; fi
      while compgen -G "jobs/$MODEL/$bench/q${n}k*" >/dev/null; do n=$((n + 1)); done
      specs=$($PY run/plan_p4.py --model "$MODEL" --n "$item" --conc "$CONC" --phase "q$n")
      if [ -z "$specs" ]; then log "$KEY $bench: every task has its trials"; break; fi
      for spec in $specs; do
        IFS=: read -r b phase tasks k conc <<< "$spec"
        log "$KEY $bench round $round: $phase, $k trial(s) on $(tr ',' '\n' <<< "$tasks" | wc -l) task(s)"
        $PY run/batch.py --model "$MODEL" --url "$URL" --bench "$b" --phase "$phase" --tasks "$tasks" --k "$k" \
          --concurrency "$conc"
      done
    done
    log "$KEY $bench: done"
  ) &
  pids+=($!)
done
wait "${pids[@]}"
while busy; do sleep 30; done
log "all $KEY batches returned"
