#!/usr/bin/env bash
# One run of the C2 controlled sweep (research/06_tool_cost Part C): the same probe
# (tool_sweep_probe.py) under six host conditions, one process each, in this order:
#   nfs-sum    lane on NFS ($HOME/lanes/toolsweep-<run>), trace summary into the NFS data dir   [+ heavy cases]
#   loc-sum    lane on node-local disk, trace summary on local disk                           [+ heavy, + full-repo git]
#   loc-nfstr  lane on node-local disk, trace summary written to NFS    (isolates the trace-write cost)
#   loc-full   lane on node-local disk, trace output full, local        (the full-output trace cost)
#   loc-off    lane on node-local disk, tracing off (HARNESS_TRACE=0)   (the harness without its tracer)
#   nfs-full   lane on NFS, trace output full on NFS                    (05 latency_workloads' condition)
# Each lane is a code-only rsync of the repository with a dummy .env (no provider: the probe disables
# the client) and its own small git repository, so A:create_worktree_real runs the real handler.
# Outputs: research/06_tool_cost/data/tool_sweep/<run>/<cond>/{<run>-<cond>.records.jsonl, run_*.jsonl, probe.log}
#   bash research/06_tool_cost/tool_sweep_run.sh r1        (normally via tool_share_cpu.sbatch sweep r1)
set -uo pipefail
RUN=${1:?run label, e.g. r1}
REPO=/mnt/home/yqi10/learn-claude-code
DATA=$REPO/research/06_tool_cost/data/tool_sweep/$RUN
PY=$HOME/.venv/bin/python
JOB=${SLURM_JOB_ID:-local$$}
NFS_LANE=${NFS_LANE:-$HOME/lanes/toolsweep-$RUN}
LOCAL=${LOCAL_ROOT:-/tmp/$USER/toolsweep/$JOB}
REPS=${REPS:-24}
HEAVY_REPS=${HEAVY_REPS:-3}
CONDS=${CONDS:-"nfs-sum loc-sum loc-nfstr loc-full loc-off nfs-full"}
EXCLUDES=(.git s15_integrated_harness/traces 'research/*/data' profiling_sandbox __pycache__ .memory '.tasks*'
          .mailboxes .transcripts .task_outputs .worktrees .scheduled_tasks.json .teams '*.log' .env
          weekly_progress .zcode .claude s16_workflow_runtime/.runtime)
export PATH="$HOME/.venv/bin:$PATH" PYTHONUNBUFFERED=1
mkdir -p "$DATA" "$LOCAL"
log() { echo "$(date -u +%FT%TZ) job=$JOB run=$RUN $*" | tee -a "$DATA/sweep_run.log"; }

make_lane() {  # dir
  local ex=(); for e in "${EXCLUDES[@]}"; do ex+=(--exclude "$e"); done
  mkdir -p "$1"; rsync -a --delete "${ex[@]}" "$REPO/" "$1/"
  printf 'ANTHROPIC_API_KEY=EMPTY\nANTHROPIC_BASE_URL=http://127.0.0.1:9\nMODEL_ID=probe\n' > "$1/.env"
  (cd "$1" && rm -rf .git && git init -q && git add -A && git -c user.name=probe -c user.email=probe@local commit -q -m "sweep lane $RUN")
}

# preflight: node, filesystems, outbound HTTPS, the lesson suite's own wall time in a local lane
make_lane "$NFS_LANE"; make_lane "$LOCAL/lane"
{
  echo "host=$(hostname) cpus=$(nproc) job=$JOB loadavg=$(cut -d' ' -f1-3 /proc/loadavg)"
  df -T "$NFS_LANE" "$LOCAL/lane" "$DATA" | sed 's/^/df: /'
  if curl -sI --max-time 10 https://arxiv.org/abs/2601.12967 >/dev/null 2>&1; then echo "outbound_https=1"; else echo "outbound_https=0"; fi
  echo "git_head=$(git -C "$REPO" rev-parse HEAD)"
  echo "lane_files=$(cd "$LOCAL/lane" && git ls-files | wc -l) lane_bytes=$(du -sb "$LOCAL/lane" | cut -f1)"
} > "$DATA/preflight_$JOB.txt" 2>&1
(cd "$LOCAL/lane" && /usr/bin/time -f "suite_wall_s=%e" "$PY" -m pytest -q -p no:cacheprovider tests 2>&1 | tail -3) >> "$DATA/preflight_$JOB.txt" 2>&1
log "preflight: $(tr '\n' ' ' < "$DATA/preflight_$JOB.txt" | cut -c1-400)"

for cond in $CONDS; do
  out=$DATA/$cond
  if [ -f "$out/DONE" ]; then log "$cond already done"; continue; fi
  mkdir -p "$out"
  lane=$LOCAL/lane; tdir=$LOCAL/out/$cond; trace=1; mode=summary; extra=(--no-heavy)
  case $cond in
    nfs-sum)   lane=$NFS_LANE; tdir=$out; extra=() ;;
    loc-sum)   extra=(--full-repo "$REPO") ;;
    loc-nfstr) tdir=$out ;;
    loc-full)  mode=full ;;
    loc-off)   trace=0 ;;
    nfs-full)  lane=$NFS_LANE; tdir=$out; mode=full ;;
  esac
  mkdir -p "$tdir"
  log "$cond: lane=$lane trace_dir=$tdir trace=$trace output=$mode ${extra[*]}"
  (cd "$lane" && TMPDIR=$LOCAL HARNESS_TRACE=$trace HARNESS_TRACE_OUTPUT=$mode \
      "$PY" research/06_tool_cost/tool_sweep_probe.py --label "$RUN-$cond" --reps "$REPS" --heavy-reps "$HEAVY_REPS" \
      --trace-dir "$tdir" "${extra[@]}") > "$out/probe.log" 2>&1
  rc=$?
  [ "$tdir" != "$out" ] && cp -a "$tdir"/. "$out"/
  [ "$rc" = 0 ] && touch "$out/DONE"
  log "$cond: exit $rc, $(wc -l < "$out/$RUN-$cond.records.jsonl" 2>/dev/null) records"
done
rm -rf "$LOCAL"
log "sweep run done"
