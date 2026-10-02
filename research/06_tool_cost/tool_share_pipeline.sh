#!/usr/bin/env bash
# GPU cells of research/06_tool_cost Part C: one job runs two independent cells, one per GPU (the
# job-submit plugin routes every 1-GPU job to the RTX 6000 pool, so H100/H200 cells come in pairs,
# as in research/11_multiuser_kv/mu_pipeline.sh).  A cell is <arm>-<part>:
#   arm   q27  Qwen/Qwen3.8-27B, thinking on, qwen3_coder tool parser (the 05 Part B server flags)
#         q8   Qwen/Qwen3-8B, thinking off server-wide, hermes tool parser (the 10/11 server flags)
#   part  model  tool_model_probe.py: task x 3 classes x 10, Workflow x 5, compact summary x 3 sizes x 10
#         delay  05 latency_workloads.py FQA-solo, CODE-solo, FQA-team with --tool-delay at
#                d0000 / d0290 (fixed 0.29 s) / d1090 (fixed 1.09 s) / d6000 (lognormal mean 6 s, CV 1), r1 r2
#         heavy  tool_heavy_workloads.py HTEST, HSEARCH (+ HWEB if the node has outbound HTTPS), r1 r2 r3
# Per cell: node-local lane (rsync of the repo, own .env), vLLM on this GPU (CPU-pinned), the part's
# drivers, stop.  Outputs: research/06_tool_cost/data/tool_e2e/<cell>/ (resumable: labels with a
# completed score.json and parts with a COMPLETE marker are skipped).
#   bash research/06_tool_cost/tool_share_pipeline.sh q27-delay q8-heavy
set -uo pipefail

CELL0=${1:?cell for GPU0}; CELL1=${2:-"-"}
REPO=/mnt/home/yqi10/learn-claude-code
DATA=$REPO/research/06_tool_cost/data/tool_e2e
PY="$HOME/.venv/bin/python"
VLLM="$HOME/.venv/bin/vllm"
JOB=${SLURM_JOB_ID:-local$$}
DELAY_REPS=${DELAY_REPS:-"r1 r2"}          # delay part: reps; 24 score files = 4 delays x 2 reps x 3 sessions
HEAVY_REPS=${HEAVY_REPS:-"r1 r2 r3"}
MODEL_PROBE_ARGS=${MODEL_PROBE_ARGS:-}      # e.g. "--task-reps 1 --workflow-reps 1 --compact-reps 1"
DATA=${DATA_ROOT:-$DATA}
FAKE_SERVER=${FAKE_SERVER:-}                # plumbing smoke test: a scripted /v1/messages server instead of vLLM
LOCAL=${LOCAL_ROOT:-/tmp/$USER/tool_e2e/$JOB}
mkdir -p "$DATA/jobs" "$LOCAL"
JOBLOG=$DATA/jobs/$JOB.log
cd "$REPO" || exit 1
export HF_HOME=/projects/co/jlin4/agenticllm/hf_cache HF_HUB_OFFLINE=1 TOKENIZERS_PARALLELISM=false
export PYTHONUNBUFFERED=1 VLLM_LOGGING_LEVEL=${VLLM_LOGGING_LEVEL:-INFO}
export PATH="$HOME/.venv/bin:$PATH" VIRTUAL_ENV="$HOME/.venv"
# not exported: 05 latency_workloads.py builds its own env with PROFILE_GIT_HEAD and rejects a duplicate
GIT_HEAD=$(git -C "$REPO" rev-parse HEAD 2>/dev/null)
EXCLUDES=(.git s15_integrated_harness/traces 'research/*/data' profiling_sandbox __pycache__ .memory '.tasks*'
          .mailboxes .transcripts .task_outputs .worktrees .scheduled_tasks.json .teams '*.log' .env
          weekly_progress .zcode .claude s16_workflow_runtime/.runtime)

log() { echo "$(date -u +%FT%TZ) job=$JOB $*" | tee -a "$JOBLOG"; }
free_port() {  # digits contain neither 429 nor 529 (the harness retries on those substrings)
  "$PY" - <<'EOF'
import socket
while True:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    if "429" not in str(p) and "529" not in str(p):
        print(p); break
EOF
}
read -r CPUS0_V CPUS0_S CPUS1_V CPUS1_S < <("$PY" - <<'EOF'
import os
cpus = sorted(os.sched_getaffinity(0)); h = len(cpus) // 2
halves = [cpus[:h], cpus[h:]]
fmt = lambda xs: ",".join(map(str, xs)) or "0"
print(*[fmt(x) for half in halves for x in (half[:4], half[4:] or half[:4])])
EOF
)

vllm_args() {  # arm maxlen
  case $1 in
    q27) echo "Qwen/Qwen3.8-27B --language-model-only --reasoning-parser qwen3 --enable-auto-tool-choice
               --tool-call-parser qwen3_coder --enable-prompt-tokens-details --enable-prefix-caching
               --max-model-len $2 --gpu-memory-utilization 0.90 --max-num-seqs 32 --max-num-batched-tokens 16384" ;;
    q8)  echo "Qwen/Qwen3-8B --reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser hermes
               --enable-prompt-tokens-details --enable-prefix-caching --max-model-len 40960
               --gpu-memory-utilization 0.90 --max-num-seqs 32" ;;
  esac
}

run_cell() {  # gpu cell -- skip done cells; take the cell's lock so two jobs never run the same cell
  local G=$1 C=$2 OUT=$DATA/$2 lock=$DATA/$2/.lock owner rc=0
  mkdir -p "$OUT"
  if [ -f "$OUT/COMPLETE" ]; then log "g$G $C already complete"; return 0; fi
  if ! mkdir "$lock" 2>/dev/null; then
    owner=$(cat "$lock/job" 2>/dev/null)
    if [ -n "$owner" ] && [ "$owner" != "$JOB" ] && squeue -h -j "$owner" 2>/dev/null | grep -q .; then
      log "g$G $C is being run by job $owner; skipping"; return 0
    fi
    log "g$G $C: stale lock of job ${owner:-?}, taking it over"
  fi
  echo "$JOB" > "$lock/job"
  _run_cell "$G" "$C" || rc=$?
  rm -rf "$lock"
  return $rc
}

_run_cell() {  # gpu cell (holding the cell's lock)
  local G=$1 C=$2 arm=${2%%-*} part=${2#*-}
  local OUT=$DATA/$C PIPE=$DATA/$C/pipeline LANE=$LOCAL/$C/lane vcpus scpus port base pid model
  [ "$G" = 0 ] && { vcpus=$CPUS0_V; scpus=$CPUS0_S; } || { vcpus=$CPUS1_V; scpus=$CPUS1_S; }
  mkdir -p "$PIPE"
  if [ -f "$OUT/COMPLETE" ]; then log "g$G $C already complete"; return 0; fi
  model=$(vllm_args "$arm" 0 | awk '{print $1; exit}')
  local mem maxlen=131072
  mem=$(nvidia-smi -i "$G" --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ')
  [ "${mem:-0}" -ge 90000 ] 2>/dev/null && maxlen=262144
  # lane
  local ex=(); for e in "${EXCLUDES[@]}"; do ex+=(--exclude "$e"); done
  mkdir -p "$LANE"; rsync -a --delete "${ex[@]}" "$REPO/" "$LANE/"
  port=$(free_port); base="http://127.0.0.1:$port"
  printf 'ANTHROPIC_API_KEY=EMPTY\nANTHROPIC_BASE_URL=%s\nMODEL_ID=%s\n' "$base" "$model" > "$LANE/.env"
  # outbound HTTPS from this node (gates HWEB)
  local net=0; curl -sI --max-time 10 https://arxiv.org/abs/2601.12967 >/dev/null 2>&1 && net=1
  "$PY" - "$PIPE/meta_$JOB.json" "$C" "$G" "$model" "$maxlen" "$port" "$net" "$vcpus" "$scpus" "$LANE" "$GIT_HEAD" <<'EOF'
import json, os, platform, subprocess, sys, time
out, cell, g, model, maxlen, port, net, vcpus, scpus, lane, git_head = sys.argv[1:]
try:
    gpu = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader", "-i", g],
                         capture_output=True, text=True).stdout.strip()
except OSError as exc:
    gpu = f"unavailable: {exc}"
meta = {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "host": platform.node(), "job": os.environ.get("SLURM_JOB_ID"),
        "cell": cell, "gpu_index": int(g), "gpu": gpu, "model": model, "max_model_len": int(maxlen),
        "port": int(port), "outbound_https": bool(int(net)), "vllm_cpus": vcpus, "driver_cpus": scpus,
        "lane": lane, "git_head": git_head, "loadavg": os.getloadavg()}
json.dump(meta, open(out, "w"), indent=1); print(json.dumps(meta))
EOF
  # server (one retry at 65536 tokens if the first start fails, e.g. KV cache too small on an 80 GB card)
  local vlog attempt waited=0 healthy=0
  for attempt in 1 2; do
    vlog=$PIPE/vllm_${JOB}_g${G}_a$attempt.log
    if [ -n "$FAKE_SERVER" ]; then
      setsid "$PY" "$FAKE_SERVER" "$port" 0.05 > "$vlog" 2>&1 &
    else
      # shellcheck disable=SC2046
      CUDA_VISIBLE_DEVICES=$G setsid taskset -c "$vcpus" "$VLLM" serve $(vllm_args "$arm" "$maxlen") \
          --host 127.0.0.1 --port "$port" --served-model-name "$model" --uvicorn-log-level warning \
          $( [ "$arm" = q8 ] && printf '%s' "--default-chat-template-kwargs {\"enable_thinking\":false}" ) \
          > "$vlog" 2>&1 &
    fi
    pid=$!
    echo "$pid" > "$PIPE/vllm_${JOB}_g$G.pid"
    waited=0
    while :; do
      if curl -sf "$base/health" >/dev/null 2>&1; then healthy=1; break; fi
      if ! kill -0 "$pid" 2>/dev/null; then log "g$G $C vLLM exited during startup (attempt $attempt, maxlen $maxlen)"; tail -30 "$vlog" | tee -a "$JOBLOG"; break; fi
      if [ "$waited" -ge 1800 ]; then log "g$G $C vLLM not healthy after ${waited}s"; kill -TERM -- "-$pid" 2>/dev/null; sleep 10; break; fi
      sleep 10; waited=$((waited + 10))
    done
    [ "$healthy" = 1 ] && break
    kill -KILL -- "-$pid" 2>/dev/null; sleep 5
    [ "$arm" = q27 ] && [ "$maxlen" -gt 65536 ] && maxlen=65536 || break
  done
  [ "$healthy" = 1 ] || return 1
  echo "{\"max_model_len_final\": $maxlen, \"attempts\": $attempt}" > "$PIPE/server_start_$JOB.json"
  log "g$G $C server healthy after ${waited}s on $base ($model, maxlen $maxlen, vllm cpus $vcpus)"
  curl -s "$base/version" > "$PIPE/server_version_$JOB.json"
  local common=("--driver-arg=--vllm-metrics=$base/metrics" "--driver-arg=--client-max-retries=0" "--driver-arg=--server-info"
                "--driver-arg=--hard-deadline")   # enforce the session cap inside a looping turn too
  # harness compaction limit (chars) below the server's window: the default 512,000 chars (~150k Qwen
  # tokens) only fits the 262,144-token window of a >= 90 GB card (as in 05 Part B)
  if [ "$arm" = q8 ]; then common+=("--driver-arg=--context-limit=134000")
  elif [ "$maxlen" -lt 262144 ]; then common+=("--driver-arg=--context-limit=$((maxlen * 3))"); fi
  local rc=0
  # session cap: Qwen3-8B loops on long-context tasks (it re-issued the same four reads for 476 rounds in
  # the first q8 FQA-solo session, 2026-09-30), so its sessions are capped at Q8_MAX_SECONDS (600)
  local maxsec=3600; [ "$arm" = q8 ] && maxsec=${Q8_MAX_SECONDS:-600}
  cd "$LANE" || return 1
  case $part in
    model)
      local extra=(); [ "$arm" = q27 ] && extra=(--workflow-nothink)
      taskset -c "$scpus" "$PY" research/06_tool_cost/tool_model_probe.py --label "$C" --trace-dir "$OUT" "${extra[@]}" $MODEL_PROBE_ARGS \
          >> "$OUT/model_probe_$JOB.log" 2>&1 || rc=$?
      [ "$rc" = 0 ] && touch "$OUT/COMPLETE" ;;
    delay)
      declare -A specmap=([d0000]="" [d0290]="*=fixed:0.29" [d1090]="*=fixed:1.09" [d6000]="*=lognormal:6:1")
      local order=(d0290 d6000 d0000 d1090) rep d mode
      for rep in $DELAY_REPS; do
        for d in "${order[@]}"; do
          local dargs=("${common[@]}" "--driver-arg=--no-timestamp")
          [ -n "${specmap[$d]}" ] && dargs+=("--driver-arg=--tool-delay=${specmap[$d]}")
          for mode in solo team; do
            local only=FQA,CODE; [ "$mode" = team ] && only=FQA
            # a session with any score.json is done, including one that hit the cap (status timeout)
            local todo="" cat
            for cat in ${only//,/ }; do [ -f "$OUT/$d/$cat-$mode-$rep.score.json" ] || todo="$todo,$cat"; done
            todo=${todo#,}; [ -z "$todo" ] && continue
            log "g$G $C $d $mode $rep ($todo, cap ${maxsec}s)"
            taskset -c "$scpus" "$PY" research/05_latency_breakdown/latency_workloads.py --rep "$rep" --mode "$mode" \
                --only "$todo" --repo "$LANE" --trace-dir "$OUT/$d" --max-seconds "$maxsec" --quiet-seconds 30 --pause 5 \
                --skip-existing "${dargs[@]}" >> "$OUT/delay_$JOB.log" 2>&1 || rc=$?
          done
        done
        order=(d1090 d0000 d6000 d0290)
      done
      [ "$(ls "$OUT"/d*/*.score.json 2>/dev/null | wc -l)" -ge 24 ] && touch "$OUT/COMPLETE" ;;
    heavy)
      local only=HTEST,HSEARCH; [ "$net" = 1 ] && only=HTEST,HSEARCH,HWEB
      for rep in $HEAVY_REPS; do
        local todo="" cat
        for cat in ${only//,/ }; do [ -f "$OUT/$cat-solo-$rep.score.json" ] || todo="$todo,$cat"; done
        todo=${todo#,}; [ -z "$todo" ] && continue
        log "g$G $C heavy $rep ($todo, cap ${maxsec}s)"
        taskset -c "$scpus" "$PY" research/06_tool_cost/tool_heavy_workloads.py --rep "$rep" --only "$todo" \
            --repo "$LANE" --trace-dir "$OUT" --max-seconds "$maxsec" --skip-existing "${common[@]}" \
            >> "$OUT/heavy_$JOB.log" 2>&1 || rc=$?
      done
      touch "$OUT/COMPLETE" ;;
    *) log "unknown part $part"; rc=2 ;;
  esac
  cd "$REPO" || true
  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
  sleep 15; kill -KILL -- "-$pid" 2>/dev/null
  log "g$G $C finished rc=$rc"
  return $rc
}

stop_all() {
  for f in "$DATA"/*/pipeline/vllm_${JOB}_g*.pid; do   # only this job's servers
    [ -f "$f" ] || continue
    local p; p=$(cat "$f"); kill -TERM -- "-$p" 2>/dev/null; sleep 5; kill -KILL -- "-$p" 2>/dev/null; rm -f "$f"
  done
}
# TERM (15 min before the limit): stop the servers; the drivers then fail fast on their next call, and a
# resubmission reruns only the sessions without a completed score.json (and parts without COMPLETE)
trap 'log "TERM received: stopping servers"; stop_all; exit 143' TERM INT

log "start cells g0=$CELL0 g1=$CELL1 on $(hostname) cpus v0=$CPUS0_V s0=$CPUS0_S v1=$CPUS1_V s1=$CPUS1_S"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader | tee -a "$JOBLOG"
[ "$CELL0" != "-" ] && { run_cell 0 "$CELL0" & }
[ "$CELL1" != "-" ] && { sleep 20; run_cell 1 "$CELL1" & }
wait
log "all cells done"
