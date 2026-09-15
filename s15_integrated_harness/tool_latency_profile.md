# The harness-side price of one tool call, for every tool the lead and a teammate can call

Profiling question (2026-09-15): what does the harness itself charge for executing each tool-use
function -- from the moment the agent loop takes a `tool_use` block (permission hooks, handler,
post hooks, trace bookkeeping) to the moment the `tool_result` is ready -- for **all 26 built-in
tools of the lead pool** (plus the four mock-MCP tools once `connect_mcp` has run) and **all ten
tools of the teammate pool**? Provider `glm-5.3-flash` via z.ai as in every measurement of this
series; the tools are pure Python and shell, so this is a property of the harness and the host,
not of the model or the provider.

## Short answer

**Every tool in both pools costs between 0.2 and 10 ms per call except five, and even the most
expensive tool in the harness is six times cheaper than the *fixed provider component of one
model call*.** The ranking is stable across two independent probe runs and matches the
observational spans of the 24 latency-profiling runs to the same order of magnitude.

- **The lead pool has a flat floor of roughly half a millisecond to a few milliseconds.** The
  task board (create 1.4, update 2.1, list 1.6, get 1.1, claim 2.8, complete 2.5 ms), the team
  protocol (spawn_teammate 1.9, send_message 1.2, request_plan 0.9, request_shutdown 1.1,
  review_plan 1.2, list_teammates 0.5 ms), cron (0.4-0.7 ms), todo_write 0.6 ms, load_skill
  0.5-1.7 ms, the compact marker 0.2 ms and the mock-MCP tools 0.4-0.8 ms. Nothing here is ever
  measurable against a 7-17 s round.
- **Five tools scale with their arguments.** `read_file` costs about 0.23 ms per KB plus ~3.5 ms
  of fixed work: 6.4 ms for the 13 KB glossary, 16.4 ms for a 45 KB document, 37.3 ms for all
  146 KB of `code.py` (a `limit` argument that returns 50 lines costs 2.9 ms, i.e. the price is
  paid on the bytes *returned*, not the bytes on disk). `bash` costs whatever the command costs:
  8.7 ms for `ls`, 9-11 ms for a scoped grep, 135 ms for `python3 <unit-test file>`, and 2.4 ms
  for the `run_in_background` dispatch itself. `glob` is 2.1 ms shallow and 23-27 ms for a
  recursive `**/*.md` sweep of the tree. `write_file` is 2.1-6.1 ms and `edit_file` 2.3-3.0 ms
  for realistic payloads. And `create_worktree`, measured for real against the lane's git, is
  the single most expensive tool in the harness: **607 ms mean (median 581)** -- because it runs
  `git worktree add`.
- **Refusals are an order of magnitude cheaper than executions.** A mutating bash command denied
  by the driver's read-only filter costs 0.25-0.31 ms, `create_worktree` denied 0.27 ms, a
  teammate's non-display shell command denied by the async-permission rule 1.0 ms, and a write
  blocked by the plan gate 0.44 ms. Denial is never the expensive outcome.
- **The teammate dispatch path is as cheap as the lead's.** Identical operations through
  `_run_teammate_tool` (plan-gate check, cwd resolution through the task assignment, hooks,
  wrapped handler) land within a millisecond of the lead's numbers for the same work:
  claim_task 3.2 vs 2.8 ms, complete_task 2.9 vs 2.5 ms, list_tasks 1.4 vs 1.6 ms, recursive
  glob 24.4 vs 23.1 ms, 13 KB read 7.9 vs 6.4 ms. The teammate's *pool* is smaller (10 tools,
  no orchestration) and its *permission regime* differs (async bash denial), but the per-call
  dispatch cost does not distinguish the two roles.
- **Observationally (24 runs, lead + 3 teammates each), the same tools cost the same order.**
  Lead: bash 102 ms mean (it ran unit tests), spawn_teammate 21.1 ms, read_file 10.7 ms,
  request_shutdown 5.0 ms, write_file 3.1 ms, create_task 2.4 ms, todo_write 0.95 ms. Teammate:
  bash 70.9 ms (36 ok incl. test runs, 19 denied by the async rule), read_file 13.5 ms,
  write_file 8.5 ms, complete_task 4.8 ms, claim_task 1.6 ms. The systematic gaps to the probe
  (spawn 21 vs 1.9 ms, request_shutdown 5.0 vs 1.1 ms) are argument mix and in-run load, not
  different code paths.
- **Scale reference.** With the round priced at 3.72 s of fixed provider time plus decode
  (`latency_profile.md`), the median tool call of either pool (1-3 ms) is **three orders of
  magnitude below one model call**, and the most expensive tool call in the harness (a real git
  worktree, 607 ms) is still one sixth of one round's *fixed* component. Tools took 9.4 s of
  6,100 s of measured run time (0.15%); nothing in this table changes that conclusion -- it
  prices it.

## 1. The question

`latency_profile.md` section 2.9 pooled all tool spans over lead and teammates and only saw the
13 tools those workloads happened to call. Three things were missing: the split by agent role
(the lead and a teammate dispatch tools through different code paths -- the lead loop on the
main thread against `assemble_tool_pool`, the teammate thread through `_run_teammate_tool` with
a plan gate and cwd-resolved handlers), the tools the workloads never exercised (16 of the 26
lead tools had never been called in any measured run: task, load_skill, compact, update_task,
get_task, the lead's own claim_task/complete_task, the cron trio, request_plan, review_plan,
list_teammates, create_worktree, connect_mcp), and controlled arguments, because an
observational mean confuses the tool's cost with whatever the model happened to pass it.

The use case is the tiered-memory design this series is building towards: routing decisions need
a per-tool price list for the harness's own cost model, and the advertised-cost experiment
(`tool_cost_profile.md`) showed such a list is also safe to publish to the model. Both need
numbers that are per-tool, per-role, and not polluted by argument choice.

## 2. Design

Two bases, in the style of `provider_probe.py` + trace analysis.

### 2.1 Basis A: controlled micro-benchmark (`scripts/tool_latency_probe.py`)

The probe imports the harness exactly as the profiling driver does (fresh state wipe, lane copy
at `/home/yq335/lanes/toolprobe`, `.env` client, real JSONL trace), then times each tool by
executing **the same dispatch code the agent loops execute**: a `tool_start`/`tool_end` trace
span around PreToolUse hooks (the driver's read-only/budget policy with the harness permission
hook beneath it), `call_tool_handler`, and the PostToolUse hook -- for the lead; and
`_run_teammate_tool` (plan gate, hooks, wrapped handlers) -- for teammates, under a teammate
`agent_scope`. 54 case labels (42 lead-side, 12 teammate-side) cover every tool of both pools
with representative argument classes (four `read_file` sizes + a `limit` variant, three bash
shapes + background + denial, shallow vs recursive glob, task-board cycles as both roles, plan
submission/approval, a real `git worktree add` against the lane's `.git`, cold `connect_mcp`,
the four mock-MCP tools). The provider client is patched to raise, so `task` and `spawn_teammate`
measure pure dispatch overhead -- their real cost is whole agent runs, which is the round
model's business, and spawned threads exit on their first (blocked) model call without spending
tokens.

One warm-up pass plus 24 timed dispatches per case, shuffled with a fixed seed, two independent
runs (r1, r2) -- 2,556 timed dispatches in total, no case errors. Per-case reps for
`create_worktree_real` are 6+6 (git is slow). The analyzer drops warm-ups and reports
mean/median/p90 per case, pooled per tool, plus status splits.

### 2.2 Basis B: observational spans of the real runs

The `tool_start`/`tool_end` span pairs of the 24 latency-profiling runs
(`traces/latency_profiling/`, 2026-09-12), paired by span id, split by `agent_kind`
(lead / teammate) and status (ok / denied / error). Real arguments, real in-run conditions
(the lead holds the agent lock while three teammate threads run), but only the tools those
workloads called and no control over arguments.

## 3. Results

Full tables: `traces/tool_latency/tool_latency_tables.md` (and `.json`), regenerate with
`scripts/tool_latency_analyze.py`. Probe numbers are pooled over r1+r2, success paths unless a
status is shown.

### 3.1 Lead pool, pooled by tool

| tool | n | mean ms | median ms | p90 ms | what is in the mean |
|---|---:|---:|---:|---:|---|
| create_worktree | 60 | 607 | 581 | 680 | real `git worktree add` (12 calls); denial path 0.27 ms (48) |
| bash | 240 | 51.5 | 8.8 | 135 | ls 8.7 / grep 9-11 / python3 test file 135 / background start 2.4 / denied 0.3 |
| read_file | 240 | 14.6 | 9.4 | 38.3 | 13 KB 6.4 / 26 KB 10.0 / 45 KB 16.4 / 146 KB 37.3 / limit=50 2.9 |
| glob | 96 | 12.6 | 10.9 | 34.0 | shallow `*.md` 2.1 / recursive `**/*.md` 23-27 |
| write_file | 96 | 4.1 | 3.2 | 7.4 | 200 B 2.1 / 20 KB 6.1 |
| claim_task | 48 | 2.8 | 3.4 | 3.9 | claim against a live board |
| complete_task | 48 | 2.5 | 3.2 | 3.5 | complete of the claimed task |
| edit_file | 48 | 2.3 | 2.8 | 3.1 | one-line replace in a small file |
| update_task | 48 | 2.1 | 2.3 | 3.1 | add one dependency between two fresh tasks |
| spawn_teammate | 48 | 1.9 | 1.9 | 2.2 | thread start only; the teammate's model calls are its own |
| list_tasks | 48 | 1.6 | 1.4 | 1.5 | board of 2-5 tasks |
| create_task | 48 | 1.4 | 1.7 | 1.9 | fresh task file |
| send_message | 48 | 1.2 | 1.3 | 1.5 | mailbox write to a teammate |
| review_plan | 48 | 1.2 | 1.4 | 1.5 | approve a pending plan request |
| request_shutdown | 48 | 1.1 | 1.4 | 1.6 | protocol enqueue + mailbox write |
| load_skill | 96 | 1.1 | 0.7 | 2.3 | hit 1.7 / miss 0.5 |
| get_task | 48 | 1.1 | 1.1 | 1.5 | read one task file |
| request_plan | 48 | 0.9 | 0.9 | 1.4 | protocol enqueue |
| task | 48 | 0.9 | 0.9 | 1.2 | dispatch overhead only (client disabled); real cost = a subagent's rounds |
| schedule_cron | 48 | 0.7 | 0.7 | 0.9 | one-shot, session-durable |
| todo_write | 48 | 0.6 | 0.5 | 0.9 | five-item list |
| list_crons | 48 | 0.4 | 0.4 | 0.6 | board of 3-4 jobs |
| connect_mcp | 48 | 0.5 | 0.5 | 0.7 | cold connect to the mock docs server |
| mcp__* | 192 | 0.5 | 0.5 | 0.8 | docs.search / docs.get_version / deploy.status / deploy.trigger (confirm-ask included) |
| cancel_cron | 48 | 0.4 | 0.4 | 0.6 | cancel of the scheduled job |
| list_teammates | 48 | 0.5 | 0.4 | 0.6 | two teammates active |
| compact | 48 | 0.2 | 0.2 | 0.3 | marker path; the summarisation model call is the turn's business |

### 3.2 Teammate pool, pooled by tool

| tool | n | mean ms | median ms | p90 ms | note |
|---|---:|---:|---:|---:|---|
| glob | 48 | 24.4 | 20.7 | 35.6 | recursive `**/*.md`, same cost as the lead's |
| bash | 96 | 5.0 | -- | -- | display command ok 9.0 ms (48); non-display denied 1.0 ms (48) |
| read_file | 48 | 7.9 | 8.5 | 9.9 | 13 KB through the assignment cwd |
| write_file | 48 | 3.7 | 4.3 | 4.8 | 2 KB into the assignment cwd |
| claim_task | 48 | 3.2 | 3.5 | 4.3 | as the teammate owner |
| complete_task | 48 | 2.9 | 3.1 | 3.8 | incl. the plan-gate check |
| edit_file | 48 | 2.7 | 2.6 | 4.0 | one-line replace |
| list_tasks | 48 | 1.4 | 1.6 | 1.7 | |
| send_message | 48 | 1.6 | 1.2 | 1.9 | to "lead" |
| submit_plan | 48 | 1.2 | 1.1 | 1.7 | teammate-only tool: enqueue + mailbox |
| (plan gate) | 48 | 0.4 | 0.5 | 0.6 | write blocked by `plan status is required` |

The teammate wrappers cost sub-millisecond over the lead equivalents (read 7.9 vs 6.4 ms for
the same 13 KB; claim/complete ~+0.4 ms; the gate check itself is 0.44 ms). What separates the
roles is the *pool* (10 vs 26+ tools) and the *permission regime* (a teammate's non-display
shell command is denied in 1.0 ms off the main thread, where the lead's would be approved and
executed) -- not the dispatch machinery.

### 3.3 Argument scaling is the whole story for the expensive tools

- `read_file` fits `~3.5 ms + 0.23 ms/KB` over 13-146 KB (r2 on the four sizes). The fixed part
  is path resolution, decode and the trace summarisation of the result; the slope is the read
  itself plus result formatting. `limit=50` on the 13 KB file returns to 2.9 ms: the price is
  paid on returned bytes, so an outline or a windowed read is 2-12x cheaper than a full read at
  these document sizes.
- `bash` is the command. The three shapes measured bracket what the workloads actually ran
  (`ls`/display 8.7-10.9 ms, scoped grep 9-11 ms, `python3 <test file>` 135 ms); the
  observational means of 102 ms (lead) and 97 ms (teammate, ok) sit exactly where the python
  test shape predicts, because iterating on unit tests was the dominant real bash use.
- `glob` pays for tree coverage: 2.1 ms shallow vs 23-27 ms for `**/*.md` over the whole
  working tree.
- `create_worktree` pays for git: 581-607 ms of `git worktree add` + branch creation, an order
  of magnitude above anything else in the harness. The profiling driver's blanket denial
  (0.27 ms) is what real runs have always paid instead.

### 3.4 Observational spans agree, with explained gaps

| tool | probe lead | observed lead | probe teammate | observed teammate |
|---|---:|---:|---:|---:|
| bash | 51.5 | 120 | 9.0 (display) | 97.1 (ok) / 1.0-class denials |
| read_file | 14.6 | 10.7 | 7.9 | 13.5 |
| glob | 12.6 | 5.5 | 24.4 | 9.4 |
| write_file | 4.1 | 3.1 | 3.7 | 8.5 |
| edit_file | 2.3 | 2.9 | 2.7 | 5.2 |
| create_task | 1.4 | 2.4 | -- | -- |
| claim_task | 2.8 | -- | 3.2 | 1.6 |
| complete_task | 2.5 | -- | 2.9 | 4.8 |
| send_message | 1.2 | 3.0 | 1.6 | 4.4 |
| list_tasks | 1.6 | -- | 1.4 | 2.8 |
| todo_write | 0.6 | 0.95 | -- | -- |
| request_shutdown | 1.1 | 5.0 | -- | -- |
| spawn_teammate | 1.9 | 21.1 | -- | -- |

Same order of magnitude everywhere. The two visible gaps are understood: observed
`spawn_teammate` (21.1 ms) includes the internal task claim, mailbox write and trace events for
a spawn that actually created a working teammate under concurrent load, while the probe spawns a
teammate whose thread dies immediately; observed teammate `write_file`/`edit_file`/`read_file`
ran on real solution and document files rather than the probe's fixed payloads. Denials in the
observed data (19 teammate bash, 4 lead bash, 1 teammate edit) cost the same sub-ms as the
probe's denial cases, on both bases.

## 4. What this means for the harness

**The tool side of a round is priced and closed.** Across both roles and every argument class,
a successful tool call costs 0.2-10 ms, with exactly four exceptions that scale with arguments
(bash, read_file, glob) or with git (create_worktree). Against the 3.72 s fixed provider
component of one round, the median tool call is ~0.05-0.1%, the worst common case (a python
test run, 135 ms) is ~3.6%, and the never-yet-used worktree is ~16%. Tool-side optimisation has
nothing to offer at this scale; the levers remain the ones `latency_profile.md` identified
(rounds, decode, turn-end memory work).

**A cost table for the model or the scheduler is now cheap to build -- and must be
argument-aware for exactly four tools.** The measured medians are stable enough to publish
(to 0.5 ms precision, in the order the model only needs to be right about -- see
`tool_cost_profile.md`). But `bash`, `read_file`, `glob` and `create_worktree` have 10-300x
spreads across their realistic argument classes, so a single number per tool would be wrong in
both directions for them; a cost model should price those by arguments (bytes returned, command
class, pattern depth) and everyone else by table lookup.

**Denial is cheap; do not route around it speculatively.** Every refusal path measured
(driver bash filter, async bash rule, plan gate, worktree policy) costs 0.25-1.0 ms -- 10-100x
below a successful file operation. A harness that pre-filters tool_use blocks to avoid "wasted"
executions is optimising microseconds; the cost of a denied call is in the extra model round it
triggers, never in the denial itself.

**The teammate tax is a permission regime, not a dispatch cost.** Teammates run the same
handler code behind a wrapper that adds <1 ms. What actually distinguishes a teammate's tool
budget is that half of its bash surface (non-display commands) is denied in 1.0 ms off the main
thread -- which is a policy lever, priced here, not a performance property.

## 5. Threats to validity

- **One host, load-free conditions.** The probe ran alone on the machine; the observational
  basis ran with lead + 3 teammate threads live. The comparison table bounds the load effect at
  the spans' scale (same order of magnitude, up to ~10x on the cheapest coordination tools:
  spawn_teammate, request_shutdown), so the probe numbers are lower bounds for loaded runs.
- **`task` and `spawn_teammate` measure dispatch only.** Their real cost is whole agent runs
  (the one-shot subagent's rounds; the teammate's task execution), which belong to the round
  model, not to a tool-call price list. The probe's patched client is what keeps this clean;
  the observed spawn span (21.1 ms) is the honest in-run number for the tool span itself.
- **`compact` is the marker path by design** -- the loop special-cases it before hooks and the
  summarisation model call happens at turn end. Its price here is the price of *asking*.
- **Argument classes are representative, not exhaustive.** Four read sizes, three bash shapes,
  two glob depths, one edit size. The byte-scaling fit for `read_file` interpolates well but is
  only measured to 146 KB; a 1 MB read would pay more than the fit predicts if the result
  summariser's behaviour changes at the persist threshold (30 KB results are the ones the
  compaction pipeline later rewrites).
- **The worktree case ran 12 times, not 48** (git is slow); its spread across runs was small
  (571-707 ms) and it is an order of magnitude from every other tool, so 12 reps are enough for
  the ranking even if the third digit is not settled.
- **Basis B inherits the 2026-09-12 runs' limitations** (five team + three solo runs per
  category; see `latency_profile.md` section 4) and adds none of its own; span pairing by span
  id is exact and the four buckets of that report already reconcile against the same events.

## 6. Reproduction

```bash
# lane copy (runs must not write into the repository); keep .git so create_worktree can run
rsync -a --delete --exclude 's15_integrated_harness/traces' --exclude profiling_sandbox \
      --exclude __pycache__ --exclude '.memory' --exclude '.tasks*' --exclude .mailboxes \
      --exclude .transcripts --exclude .task_outputs --exclude .worktrees \
      /home/yq335/learn-claude-code/ /home/yq335/lanes/toolprobe/
# one probe run: 54 case labels x (1 warm-up + 24 timed dispatches), about 15 s, no model calls;
# the script anchors itself to its own lane copy (REPO = two levels up), so run the lane's file
cd /home/yq335/lanes/toolprobe
/home/yq335/myenv/bin/python s15_integrated_harness/scripts/tool_latency_probe.py --reps 24 --label r1
# (repeat with --label r2; the analyzer pools both)

# tables (Basis A from the records sidecars, Basis B from the latency-profiling traces)
python3 s15_integrated_harness/scripts/tool_latency_analyze.py \
    --records 's15_integrated_harness/traces/tool_latency/*.records.jsonl' \
    --traces s15_integrated_harness/traces/latency_profiling \
    --md s15_integrated_harness/traces/tool_latency/tool_latency_tables.md \
    --json s15_integrated_harness/traces/tool_latency/tool_latency_tables.json
```

Artefacts: probe traces and records sidecars in `traces/tool_latency/` (one run_*.jsonl plus
run_*.records.jsonl per probe run), tables in `traces/tool_latency/tool_latency_tables.{md,json}`.
The probe reuses the harness's own trace runtime, so any run can also be inspected with
`trace_view.py` like an interactive session.
