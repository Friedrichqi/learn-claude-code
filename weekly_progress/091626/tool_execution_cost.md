# The harness-side price of one tool call: every tool the lead and a teammate can call

2026-09-15. Full report: `s15_integrated_harness/tool_latency_profile.md`. Tables:
`s15_integrated_harness/traces/tool_latency/tool_latency_tables.md`. Provider z.ai,
`glm-5.3-flash` (irrelevant here: the tools are local Python and shell, so this prices the
harness and the host, not the model).

## 1. Why

The latency breakdown priced tool execution at 0.1-0.5% of a round, but its table pooled lead
and teammate spans together and only saw the 13 tools those workloads happened to call -- 16 of
the 26 lead tools had never been called in any measured run, and an observational mean confuses
a tool's cost with whatever arguments the model happened to pass. The tiered-memory design needs
a per-tool, per-role price list for the harness's cost model, and the advertised-cost experiment
showed the same list is safe to publish to the model. Both need controlled numbers.

## 2. What was run

Two bases, one question.

1. **Controlled micro-benchmark** (`scripts/tool_latency_probe.py`, new): every tool of both
   pools executed through *the same dispatch code the agent loops run* -- trace span, PreToolUse
   permission hooks, handler, PostToolUse hook for the lead; the plan gate and cwd-resolving
   wrappers of `_run_teammate_tool` for teammates. 54 case labels (42 lead-side, 12
   teammate-side) with representative argument classes: four `read_file` sizes plus a `limit`
   variant, three bash shapes plus background and a denial, shallow and recursive glob,
   task-board cycles as both roles, plan submission and approval, a real `git worktree add`
   against the lane's `.git`, cold `connect_mcp`, the four mock-MCP tools. The provider client
   is patched to raise, so no model call is ever spent and spawned agents exit on their first
   (blocked) call. One warm-up pass + 24 timed dispatches per case, shuffled, two independent
   runs: 2,556 timed dispatches, zero errors, about 15 s per run.
2. **Observational split** (`scripts/tool_latency_analyze.py`, new): the `tool_start ->
   tool_end` spans of the 24 runs behind `latency_profile.md`, paired by span id and split by
   `agent_kind` and status -- real arguments, real load, but only the tools those workloads
   called.

## 3. Findings

**Short answer: every tool in both pools costs 0.2-10 ms per call except four, and even the
most expensive tool in the harness is six times cheaper than the fixed provider component of a
single model call.**

- **The pools have a flat floor of half a millisecond to a few milliseconds.** Task board:
  create 1.4, update 2.1, list 1.6, get 1.1, claim 2.8, complete 2.5 ms. Team protocol:
  spawn_teammate 1.9, send_message 1.2, request_plan 0.9, request_shutdown 1.1, review_plan
  1.2, list_teammates 0.5 ms. Cron 0.4-0.7 ms, todo_write 0.6 ms, load_skill 0.5-1.7 ms,
  compact marker 0.2 ms, mock-MCP tools 0.4-0.8 ms.
- **Four tools scale with their arguments, and they are the whole story.** `read_file` is
  ~3.5 ms + 0.23 ms per KB returned (6.4 ms for the 13 KB glossary, 37.3 ms for all 146 KB of
  `code.py`; `limit=50` costs 2.9 ms -- the price is on returned bytes, so a windowed read is
  2-12x cheaper at these sizes). `bash` is the command: `ls` 8.7 ms, scoped grep 9-11 ms,
  `python3 <unit-test file>` 135 ms, background dispatch 2.4 ms. `glob` is 2.1 ms shallow and
  23-27 ms recursive. And `create_worktree`, measured against real git for the first time, is
  the most expensive tool in the harness at **607 ms** -- an order of magnitude above
  everything else, which is why the profiling driver has always denied it (0.27 ms).
- **Denial is 10-100x cheaper than execution.** Mutating bash denied 0.3 ms, worktree denied
  0.3 ms, teammate async bash denied 1.0 ms, plan-gate block 0.4 ms. The cost of a refused call
  lives in the extra model round it triggers, never in the refusal itself.
- **The teammate tax is permission, not machinery.** Identical operations through the
  teammate dispatch path land within a millisecond of the lead's: recursive glob 24.4 vs
  23.1 ms, 13 KB read 7.9 vs 6.4 ms, claim 3.2 vs 2.8 ms. What distinguishes a teammate is its
  10-tool pool and the async rule that denies its non-display shell commands in 1.0 ms.
- **Observation agrees.** Real spans: lead bash 102 ms (it ran unit tests), spawn_teammate
  21.1 ms, read_file 10.7 ms; teammate bash 70.9 ms over 36 ok + 19 denied, read_file 13.5 ms,
  write 8.5 ms, complete_task 4.8 ms. The gaps to the probe are argument mix and in-run load,
  on the same order of magnitude everywhere.
- **Scale.** Median tool call 1-3 ms is three orders of magnitude below one model call; the
  worst realistic case (a test run, 135 ms) is 3.6% of the round's 3.72 s fixed provider
  component; the worktree is one sixth. Tool-side optimisation stays closed; the levers remain
  rounds, decode and turn-end memory work.

## 4. Consequence for the harness

A per-tool cost table is now measured rather than guessed, in the shape both consumers need:
table lookup for the 22 flat tools, argument-aware pricing for `bash`, `read_file`, `glob` and
`create_worktree` (their realistic classes span 10-300x, so a single number per tool would be
wrong in both directions). Publication to the model remains pointless on its own -- last week's
result -- but the pool shape that would make it pay (a hot and a cold route to the same bytes)
now has exact prices to advertise.

## 5. Reproduction

```bash
rsync -a --delete --exclude 's15_integrated_harness/traces' --exclude profiling_sandbox \
      --exclude __pycache__ --exclude '.memory' --exclude '.tasks*' --exclude .mailboxes \
      --exclude .transcripts --exclude .task_outputs --exclude .worktrees \
      /home/yq335/learn-claude-code/ /home/yq335/lanes/toolprobe/   # keep .git: worktree case
cd /home/yq335/lanes/toolprobe
/home/yq335/myenv/bin/python s15_integrated_harness/scripts/tool_latency_probe.py --reps 24 --label r1
/home/yq335/myenv/bin/python s15_integrated_harness/scripts/tool_latency_probe.py --reps 24 --label r2
python3 s15_integrated_harness/scripts/tool_latency_analyze.py \
    --records 's15_integrated_harness/traces/tool_latency/*.records.jsonl' \
    --traces s15_integrated_harness/traces/latency_profiling \
    --md s15_integrated_harness/traces/tool_latency/tool_latency_tables.md \
    --json s15_integrated_harness/traces/tool_latency/tool_latency_tables.json
```

Artefacts: `traces/tool_latency/` (probe traces + records sidecars per run, tables md/json).
