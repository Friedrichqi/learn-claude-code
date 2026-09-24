# 06 · What tools cost: execution latency and cost exposure

> **Status:** complete — two finished experiments, nothing pending. **Dates:** Part B ran 2026-09-14,
> Part A 2026-09-15. **Model / provider:** z.ai `glm-5.3-flash` (Part A's probe makes no model calls —
> the tools are local Python and shell — and its observational basis reuses the 2026-09-12
> latency-profiling runs of `research/05_latency_breakdown/`). **Commit:** e0e522b (2026-09-15, "tool-use
> average latency profiling") holds both reports, both weekly digests, the scripts and the data.
> **Presented:** 2026-09-16 deck (`weekly_progress/091626/slides.html`), slide 7 (Part A, the per-tool
> price list) and slide 8 (Part B, advertised cost vs tool choice; published page
> claude.ai/code/artifact/6de7f01b-27ae-4151-911f-42293a286e98, source `tool_cost_page.html`).

**Contents**

- **Part A — The harness-side price of one tool call, for every tool the lead and a teammate can call.**
  What the harness charges per tool call: a 54-case probe (2,556 timed dispatches) plus the observed
  tool spans of the 24 latency-profiling runs.
  - A.W Additions from `weekly_progress/091626/tool_execution_cost.md`
- **Part B — Exposing hardware cost to the harness: does an advertised tool latency change the tool_use
  the model generates?** 893 single-shot trials, 80 multi-round runs, 24 s15 sessions.
  - B.W Additions from `weekly_progress/091626/tool_cost_exposure.md`
- Data inventory · Source map · Cleanup notes

**Files in this folder** (`research/06_tool_cost/`)

| path | purpose |
|---|---|
| `tool_latency_probe.py` | Part A basis A: times every tool of both pools through the agent loops' own dispatch code; the provider client is patched to raise, so no model calls |
| `tool_latency_analyze.py` | Part A tables: probe records (basis A) and the tool spans of the latency-profiling traces (basis B) |
| `tool_cost_probe.py` | Part B stage 1: single-shot tool-choice probes A–D (calls the provider) |
| `tool_cost_loop.py` | Part B stage 2: multi-round loop with real tool execution, 2x2 advertised × enforced cost (calls the provider) |
| `tool_cost_harness.py` | Part B stage 3: real s15 sessions through `research/common/profile_run.py --tool-cost` (starts harness sessions) |
| `tool_cost_analyze.py` | Part B tables for stages 1 and 2 (the stage-3 rows live in `data/tool_cost/harness/harness_runs_*.json`) |
| `tool_cost_page.html` | HTML source of the published Part B page |
| `data/tool_latency/` | the two probe runs and their tables (Part A) |
| `data/tool_cost/` | stage-1 and stage-2 JSONL, their tables, and `harness/` with the 24 stage-3 sessions (Part B) |

Shared code outside this folder: `research/common/profile_run.py` (the `--tool-cost*` flags of Part B
stage 3; it also wrote the latency-profiling traces that Part A's basis B reads from
`research/05_latency_breakdown/data/latency_profiling/`).

---

# Part A — The harness-side price of one tool call, for every tool the lead and a teammate can call

_Formerly `s15_integrated_harness/tool_latency_profile.md` (2026-09-15)._

Profiling question (2026-09-15): what does the harness itself charge for executing each tool-use
function -- from the moment the agent loop takes a `tool_use` block (permission hooks, handler,
post hooks, trace bookkeeping) to the moment the `tool_result` is ready -- for **all 26 built-in
tools of the lead pool** (plus the four mock-MCP tools once `connect_mcp` has run) and **all ten
tools of the teammate pool**? Provider `glm-5.3-flash` via z.ai as in every measurement of this
series; the tools are pure Python and shell, so this is a property of the harness and the host,
not of the model or the provider.

## Short answer

**Every tool in both pools costs between 0.2 and 10 ms per call except four [corrected 2026-09-23: earlier tool_latency_profile.md said "except five"; in `research/06_tool_cost/data/tool_latency/tool_latency_tables.md` only four tools have an argument class above 10 ms -- bash, read_file, glob and create_worktree, the four exceptions of §4 -- while write_file (6.10 ms at most) and edit_file (2.70 ms at most), listed with the argument-scaling tools below, stay inside the band], and even the most
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
  (`research/05_latency_breakdown/README.md` Part A), the median tool call of either pool (1-3 ms) is **three orders of
  magnitude below one model call**, and the most expensive tool call in the harness (a real git
  worktree, 607 ms) is still one sixth of one round's *fixed* component. Tools took 9.4 s of
  6,100 s of measured run time (0.15%); nothing in this table changes that conclusion -- it
  prices it.

## 1. The question

`research/05_latency_breakdown/README.md` Part A section 2.9 pooled all tool spans over lead and teammates and only saw the
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
(Part B) showed such a list is also safe to publish to the model. Both need
numbers that are per-tool, per-role, and not polluted by argument choice.

## 2. Design

Two bases, in the style of `provider_probe.py` + trace analysis.

### 2.1 Basis A: controlled micro-benchmark (`research/06_tool_cost/tool_latency_probe.py`)

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
(`research/05_latency_breakdown/data/latency_profiling/`, 2026-09-12), paired by span id, split by `agent_kind`
(lead / teammate) and status (ok / denied / error). Real arguments, real in-run conditions
(the lead holds the agent lock while three teammate threads run), but only the tools those
workloads called and no control over arguments.

## 3. Results

Full tables: `research/06_tool_cost/data/tool_latency/tool_latency_tables.md` (and `.json`), regenerate with
`research/06_tool_cost/tool_latency_analyze.py`. Probe numbers are pooled over r1+r2, success paths unless a
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
nothing to offer at this scale; the levers remain the ones `research/05_latency_breakdown/README.md` Part A identified
(rounds, decode, turn-end memory work).

**A cost table for the model or the scheduler is now cheap to build -- and must be
argument-aware for exactly four tools.** The measured medians are stable enough to publish
(to 0.5 ms precision, in the order the model only needs to be right about -- see
Part B). But `bash`, `read_file`, `glob` and `create_worktree` have 10-300x
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
  category; see `research/05_latency_breakdown/README.md` Part A section 4) and adds none of its own; span pairing by span
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
/home/yq335/myenv/bin/python research/06_tool_cost/tool_latency_probe.py --reps 24 --label r1
# (repeat with --label r2; the analyzer pools both)

# tables (Basis A from the records sidecars, Basis B from the latency-profiling traces)
python3 research/06_tool_cost/tool_latency_analyze.py \
    --records 'research/06_tool_cost/data/tool_latency/*.records.jsonl' \
    --traces research/05_latency_breakdown/data/latency_profiling \
    --md research/06_tool_cost/data/tool_latency/tool_latency_tables.md \
    --json research/06_tool_cost/data/tool_latency/tool_latency_tables.json
```

Artefacts: probe traces and records sidecars in `research/06_tool_cost/data/tool_latency/` (one run_*.jsonl plus
run_*.records.jsonl per probe run), tables in `research/06_tool_cost/data/tool_latency/tool_latency_tables.{md,json}`.
The probe reuses the harness's own trace runtime, so any run can also be inspected with
`trace_view.py` like an interactive session.

## A.W Additions from `weekly_progress/091626/tool_execution_cost.md` (2026-09-15)

_(was §1)_

The latency breakdown priced tool execution at 0.1-0.5% of a round, but its table pooled lead
and teammate spans together and only saw the 13 tools those workloads happened to call -- 16 of
the 26 lead tools had never been called in any measured run, and an observational mean confuses
a tool's cost with whatever arguments the model happened to pass. The tiered-memory design needs
a per-tool, per-role price list for the harness's cost model, and the advertised-cost experiment
showed the same list is safe to publish to the model. Both need controlled numbers.

_(was §4)_

A per-tool cost table is now measured rather than guessed, in the shape both consumers need:
table lookup for the 22 flat tools, argument-aware pricing for `bash`, `read_file`, `glob` and
`create_worktree` (their realistic classes span 10-300x, so a single number per tool would be
wrong in both directions). Publication to the model remains pointless on its own -- last week's
result -- but the pool shape that would make it pay (a hot and a cold route to the same bytes)
now has exact prices to advertise.

# Part B — Exposing hardware cost to the harness: does an advertised tool latency change the tool_use the model generates?

_Formerly `s15_integrated_harness/tool_cost_profile.md` (2026-09-14)._

Run 2026-09-14. Provider z.ai, model `glm-5.3-flash`, the same endpoint as every other measurement
in this series.

## 0. Summary

**Telling the model that a tool is slow does not stop it from using that tool, as long as that tool
is the right one for the job.** Asked in its original form -- if `read_file` is advertised at 5 s,
does the model still generate `read_file` tool_use? -- the answer is yes, at the same rate. Labelled
at anything from a twentieth of a second to a full minute, in three wordings, the file reader kept
its 44-45% share of first moves over 160 trials, and the model's own reasoning almost never
mentioned the cost at all. It picked the tool that fit the task and moved on.

The same model is not blind to the numbers. Given two tools described as identical except for cost,
it chose the cheaper one 97-100% of the time against a 53% baseline, wiping out a strong habit of
picking one particular name. That held whether the cost was written in seconds, described only as
hardware and left for the model to work out, or written as the words "fast" and "slow", and whether
it sat in the tool description or the system prompt. But it is a tiebreaker, not a budget: a tool
five times slower and a tool thousands of times slower were avoided at the same rate, and an equal
cost returned the model to a coin flip.

What changes behaviour in a realistic pool is being told to care. One added sentence -- "Tool calls
are not free. Prefer the cheapest tool that can still answer the question correctly" -- cut file
reads from about a third of first moves to under a tenth. The cost table on its own moved nothing.

In the real s15 harness there was nothing to fix in either direction. On questions asking for
specific values the lead already answered with a grep through the shell and never opened the file,
in 12 runs of 12, with or without the labels. On questions asking what a document says it read the
whole file in 12 runs of 12, again with or without the labels, which is correct: a handful of
matching lines cannot summarise a document.

Practical consequence: cost-aware routing stays in the harness. Publishing measured costs to the
model is nearly free -- about 230 tokens in the stable prefix, cached after the first call -- and
buys nothing on its own. It pays only in a pool that deliberately offers a hot and a cold route to
the same bytes, which is the shape a tiered-memory tool pool would have, and only if the harness
also says that cheaper is preferred.

## 1. The question

Every cost this project has measured is invisible to the agent that causes it. The latency
breakdown (`research/05_latency_breakdown/README.md` Part A) prices an agentic
round at 3.72 s of fixed provider time, 0.033-0.042 ms per uncached prompt token, 17.4 ms per
output token, and 13-21 s of turn-end memory extraction. The byte-level redundancy work prices the
same file content being re-sent 33-86% of the time. The harness knows all of this. The model does
not: a tool description says what a tool does and never what it costs.

So: if the harness published its own measured costs -- or the hardware facts behind them, such as
the DRAM-to-HBM staging latency of the data a tool must touch -- in the tool descriptions or in the
system prompt, would the model route around the expensive tools? Put in the concrete form the
question was asked in: if `read_file` is advertised at 5 s per call, does the model still generate
`read_file` tool_use blocks?

The answer has a direct design consequence. If advertised cost steers tool choice, a harness can
publish a live cost table and let the model do its own scheduling, which is cheaper than building a
cost-aware planner into the harness. If it does not, cost-aware routing has to stay in the harness,
where it already is.

## 2. Design

Three stages, from a fully controlled single-shot choice to the real s15 harness. Each stage
answers a different objection to the one before it.

### 2.1 Stage 1: single-shot probes (`research/06_tool_cost/tool_cost_probe.py`)

One model call per trial, no tool execution; the outcome is the tool named in the first `tool_use`
block. Four probes:

**A. Discrimination.** Two tools, `read_via_alpha` and `read_via_beta`, described as interchangeable
("both return the same bytes for the same path"), differing only in advertised cost. This is the
most favourable case possible: choosing the cheap one costs nothing, because the tools are
identical. Crossed factors:

* *framing* -- how the cost is worded. `seconds` states it ("Measured latency: 5.00 s per call");
  `hardware` states only the hardware and leaves the division to the model (a 128 GiB working set
  either resident in HBM3 at 3.35 TB/s, giving 0.04 s, or staged across PCIe 4.0 x16 at 25 GB/s,
  giving 5.5 s); `qualitative` says only "This channel is fast" / "This channel is slow". All three
  describe the same ~125x ratio.
* *placement* -- in each tool's `description`, in a cost table appended to the system prompt, or
  both.
* *baseline* -- no cost shown anywhere.

Two nuisance variables are counterbalanced in balanced blocks of four: which of the two names
carries the expensive label, and the order of the two tools in the `tools` array. Both matter; the
baseline cell measures how much.

**B. Dose-response.** Probe A with the cheap channel pinned at 0.04 s and the expensive one swept
over 0.04 (a tie), 0.2, 1, 5, 30 and 300 s. How large must the advertised gap be before the model
acts on it, and does a larger gap produce a stronger preference?

**C. Substitution.** A realistic pool -- `read_file`, `grep`, `glob`, `bash`, `recall_summary` --
and a real question about a real repository file ("what does GLOSSARY.md say 'compaction' means?").
Here the tools are *not* interchangeable: `grep` returns only matching lines and `recall_summary`
returns a deliberately vague cached blurb, so the cheap routes trade information for speed.
`read_file` is annotated at 0.05, 5 or 60 s. A placebo arm annotates `grep` at 5 s instead, so a
drop in `read_file` can be told apart from "something in this prompt is expensive".

**D. Information or objective?** The same pool and question as C, but the cost table is accompanied
by a one-line policy ("Tool calls are not free. Prefer the cheapest tool that can still answer the
question correctly") and, in one cell, a hard budget ("You have a total tool-latency budget of
1.00 s for this task"). If C is null, this separates "the model was not told the cost" from "the
model was not told to care".

### 2.2 Stage 2: multi-round loop with real execution (`research/06_tool_cost/tool_cost_loop.py`)

The tools actually run against real repository files, so a route has consequences: rounds, bytes
pulled into context, wall time, and whether the answer is right. A 2x2 factorial separates the
advertised cost from the real one, which only the harness can tell apart:

| arm | `read_file` advertised at 5 s | `read_file` really sleeps 5 s |
|---|---|---|
| `plain` | no | no |
| `advertise` | yes | no |
| `enforce` | no | yes |
| `both` | yes | yes |

`advertise` minus `plain` is the effect of the text alone -- nothing is actually slower, so any
change in the route is caused by the description. `both` minus `enforce` is the payoff: the seconds
that telling the agent about a real cost actually saves. `advertise` minus `plain` on correctness
is the risk, because the cheap routes here are lossy.

Four questions with exact gradeable answers, chosen so the whole-file read and the targeted search
both work but differ by two orders of magnitude in returned bytes (GLOSSARY.md is 12.8 KB, code.py
is 146 KB). T4 is the accuracy-risk task: its answer needs the whole Compaction entry, so the cheap
routes are genuinely insufficient.

### 2.3 Stage 3: the real s15 harness (`research/06_tool_cost/tool_cost_harness.py`)

`profile_run.py` gained a `--tool-cost` intervention: it rewrites the `tools` argument of every
outgoing request, so the lead, the teammates and the one-shot subagents are annotated in one place
and the auxiliary calls that carry no tools (memory extraction, summarisation) are left alone.
Nothing else about s15 changes: the lead keeps its full pool, and in particular it keeps `bash`,
so it can run `grep` itself if it wants to avoid an expensive `read_file`.

## 3. Results

Full tables: `research/06_tool_cost/data/tool_cost/tool_cost_tables.md` (regenerate with `research/06_tool_cost/tool_cost_analyze.py`).
Percentages carry 95% Wilson intervals; `p` is a two-sided Fisher exact test. Stage 1 is 893
successful trials of 928 attempted (35 lost to provider rate limiting and re-run afterwards in
balanced top-up, so every cell holds 30-32 trials).

### 3.1 Between interchangeable tools the advertised cost decides everything

| framing | placement | chose the cheap channel | chose `read_via_alpha` | cost mentioned in the reply |
|---|---|---|---|---|
| none (baseline) | -- | 53% [36-69] | 91% [76-97] | 0% |
| seconds | tool description | 100% [89-100] | 50% | 100% |
| seconds | system prompt | 100% [89-100] | 50% | 100% |
| hardware | tool description | 97% [84-99] | 53% | 75% |
| hardware | system prompt | 100% [89-100] | 50% | 100% |
| qualitative | tool description | 100% [89-100] | 50% | 100% |
| qualitative | system prompt | 100% [89-100] | 50% | 100% |

Every annotated cell is at 97-100% against a 53% baseline, p = 7.1e-06 in eight of the nine cells
and 7.7e-05 in the ninth. Three things follow.

*The cost overrides a strong prior.* Unannotated, the model picks `read_via_alpha` 91% of the time
-- it is simply biased towards that name. Annotated, `alpha` is chosen exactly 50% of the time,
which is the rate the counterbalancing produces when the choice is made purely on cost. The
advertised number does not nudge the prior, it replaces it.

*The model will do the arithmetic.* The `hardware` framing never states a latency: it gives a
working-set size and two bandwidths. The model divides and acts on the result, at 97-100%, the same
as when it is handed the seconds. Its reasoning shows the derivation ("Alpha is faster (HBM3
resident, 3.35 TB/s) vs beta (PCIe staging)").

*Placement does not matter.* Tool description, system-prompt cost table and both together are
indistinguishable. The question of where to put the information has no measurable answer, because
the information is not what is scarce.

### 3.2 There is no dose-response: this is a tie-break, not a calculation

| advertised cost of the expensive channel | ratio to the cheap one | chose the cheap channel |
|---|---|---|
| 0.04 s | 1x (a tie) | 47% [31-64] |
| 0.2 s | 5x | 100% [89-100] |
| 1 s | 25x | 100% [89-100] |
| 5 s | 125x | 100% [89-100] |
| 30 s | 750x | 100% [89-100] |
| 300 s | 7500x | 100% [89-100] |

A genuine tie returns the model to chance, which confirms the outcome measures cost and nothing
else. Every non-tie is saturated: a 5x gap of 0.16 s produces the same 100% as a 7500x gap of
299.96 s. The data are completely separated, so no threshold is estimable; it lies at or below 5x.
The model is not weighing a cost against a benefit. It is applying a rule -- when two options are
otherwise equal, take the one labelled cheaper -- and the size of the label is irrelevant.

### 3.3 Between tools that differ in function, the advertised cost does nothing

Probe C replaces the two interchangeable channels with a realistic pool and a real question.

| annotation on `read_file` | first move is `read_file` | p vs baseline |
|---|---|---|
| none | 44% [28-61] | -- |
| 0.05 s | 53% [36-69] | 0.617 |
| 5 s | 44% [28-61] | 1.000 |
| 60 s | 34% [20-52] | 0.609 |
| 5 s, hardware framing | 59% [42-74] | 0.317 |
| 5 s, qualitative framing | 34% [20-52] | 0.609 |
| (placebo: 5 s on `grep` instead) | 62% [45-77] | 0.210 |

Pooled over all five `read_file` annotations, 45% [37-53] of 160 trials against 44% of 32
unannotated trials, p = 1.000. A 60-second advertised latency on the tool the task obviously needs
moves its selection rate by less than the noise between two samples of the same condition (the
probe C and probe D baselines are the identical prompt run at different times and differ by 12
percentage points, p = 0.44).

The reasoning traces say why, and the contrast with probe A is total. In probe A the model
mentioned the cost in 75-100% of trials. In probe C it mentions it in **0-3%**. It does not weigh
the cost and decide to pay it; it never raises the subject. A representative trace from the 60 s
cell reads in full: "The user wants to know what the glossary says about 'compaction'. Let me read
that file." The tool is chosen on fit, and once one tool fits, the cost table is not consulted.

Stage 2 reproduces this where the tools really run and the route has consequences. Across 80
multi-round runs, `read_file` was used in 32 of 40 runs that were told it costs 5 s and 33 of 40
that were not (p = 1.000); per task the split is 10/10 vs 9/10, 2/10 vs 4/10, 10/10 vs 10/10 and
10/10 vs 10/10. Rounds (2.6-2.8), median bytes pulled into context (1.2-1.5 KB) and correctness
(95-100%) are flat across all four arms.

Stage 2 also carries its own noise gauge. The `enforce` arm makes `read_file` really sleep 5 s
without telling the model, and a model cannot perceive wall-clock time inside a request, so any
`plain`-to-`enforce` difference must be noise. It is 90% versus 75%, a 15-point gap -- larger than
any difference the annotation produced. That is the scale at which this outcome can be read.

One caveat against over-reading a tail: the mean bytes returned in the `plain` arm is 10.0 KB
against 3.3-4.9 KB in the others, but that is one single run that pulled all 143 KB of `code.py`
into context. The medians are identical. With one run it is an anecdote, not an effect.

### 3.4 What changes behaviour is an objective, not information

Probe D holds the pool, the question and the cost table fixed and adds a one-line policy.

| cost table | policy line | hard budget | first move is `read_file` | cost mentioned | p vs "nothing said" |
|---|---|---|---|---|---|
| no | no | no | 31% [18-49] | 0% | -- |
| **yes** | no | no | 32% [19-50] | 3% | 1.000 |
| no | **yes** | no | 17% [7-34] | 0% | 0.240 |
| **yes** | **yes** | no | 9% [3-24] | 16% | 0.060 |
| **yes** | **yes** | **yes** | 9% [3-24] | 69% | 0.060 |

Publishing the cost table alone moves the selection rate by one point. Adding "Tool calls are not
free. Prefer the cheapest tool that can still answer the question correctly" takes it from 31% to
9%; pooling the two cells that carry both the table and the policy gives 6 of 64 against 10 of 32,
p = 0.0098. The hard budget changes the choice no further but changes the reasoning: mentions of
cost jump from 16% to 69%, so the budget is what makes the model reason about the number out loud,
while the policy is what makes it act.

The asymmetry is the finding. The model was never short of information -- in probe A it used the
same numbers perfectly. What it lacked in probe C was a reason to trade correctness for speed, and
one sentence supplies it.

### 3.5 What the intervention costs to run

Annotating the two-tool pool adds, per request: 12 tokens for the qualitative wording, 18 for the
seconds wording, 84 for the hardware wording. At the measured 0.038 ms per uncached prompt token
that is 0.5-3.2 ms of prefill, and it sits in the stable prefix, so after the first call it is
served from the prompt cache.

The larger cost is on the output side, and only appears when the annotation is actually used. In
probe A the mean reply grows from 53 output tokens unannotated to 71 with seconds and 84 with the
hardware framing, because the model narrates the comparison and, for the hardware framing, does the
division. At the measured 17.4 ms per output token that is 0.30 s and 0.53 s of extra decode per
call. In probe C, where the model ignores the cost, output tokens barely move (50 to 50-62).

So the intervention is close to free when it is ignored, costs about half a second of decode per
call when it is used, and the hardware framing is the most expensive way to say the same thing.

### 3.6 In the real s15 harness the intervention has no room in either direction

Two workloads, three arms (`plain`, `advertise` = `read_file` at 5.00 s and everything else at
0.05 s, `policy` = the same table plus the cost-aware sentence), four runs each, run interleaved.

| workload | arm | rounds | `read_file` calls | `bash` calls | KB read | wall s | coverage |
|---|---|---|---|---|---|---|---|
| constants | plain | 3.5 | 0.00 | 2.00 | 0.0 | 39.0 | 1.00 |
| constants | advertise | 3.0 | 0.00 | 1.50 | 0.0 | 38.5 | 1.00 |
| constants | policy | 3.2 | 0.00 | 2.00 | 0.0 | 39.0 | 1.00 |
| summary | plain | 3.2 | 1.00 | 0.25 | 12.5 | 44.5 | 0.88 |
| summary | advertise | 3.2 | 1.00 | 0.25 | 12.5 | 44.2 | 0.69 |
| summary | policy | 3.2 | 1.00 | 0.25 | 12.5 | 42.1 | 0.90 |

Neither workload moves at all, and the two fail in opposite directions.

On `constants` -- three questions with specific values as answers -- the lead answered with `bash`
and a grep-shaped command in 12 runs out of 12, in every arm, and never called `read_file` once.
The unannotated harness was already taking the cheap route, so there was nothing for an advertised
cost to remove. This is the floor that motivated the second workload.

On `summary` -- explain what three glossary entries say -- the lead called `read_file` exactly once
and pulled all 12.5 KB of the file in 12 runs out of 12, in every arm, including the arm that was
told the call costs 5 s and instructed to prefer the cheapest tool that still works. This is a
ceiling, and it is the correct behaviour: `grep` returns matching lines, and no set of matching
lines answers "explain what this entry says". The model declined to trade the answer for the
latency, which is what the policy sentence asked for ("the cheapest tool that can still complete the
step correctly").

The coverage column varies (0.54 to 1.00) but not with the arm. It is graded by looking for the
five literal stage identifiers, and a prompt that says "in your own words" sometimes gets
paraphrase instead; every missing item in every run is one of those five names. The route --
one `read_file`, 12.5 KB, three to four rounds -- is identical across all twelve runs.

Together with stage 2 this bounds where the effect found in probes A and D can live. It needs a
pool where a cheaper route exists *and* can actually do the job. On the two real workloads tested,
the harness either already took the cheap route without being told, or the cheap route could not
answer the question.

The annotation's footprint in the real pool: the s15 tool definitions grow from 6,103 to 7,013
characters (+910, about 230 tokens, 8.7 ms of prefill at 0.038 ms/token, cached after the first
call), and the policy sentence adds 95 characters to the system prompt.

## 4. What this means for the harness

**Publishing a cost table is not a scheduling mechanism.** The result that matters is the gap
between probe A and probe C. Given two tools that do the same thing, the advertised cost decides
the choice completely, overriding a 91% name prior, in every framing and at every magnitude above a
5x ratio. Given a pool of tools that do different things, the same advertised cost changes nothing
and is not even mentioned in the model's reasoning. Real harness tool pools are the second kind.
`read_file`, `grep` and `bash` are not substitutes from the model's point of view: they answer
different questions, and the model picks on fit.

**Cost information is cheap; a cost objective is what is scarce.** Annotating the real s15 pool
costs 910 characters of tool definitions (about 230 tokens, 8.7 ms of prefill at the measured
0.038 ms/token) and it sits in the stable prefix, so it is cached after the first call. One extra
sentence of policy costs 95 characters. The sentence is what moves behaviour: cost table alone
31% -> 32%, cost table plus policy 31% -> 9% (p = 0.0098). If a harness wants cost-aware routing
from the model, it has to state the objective, not just the numbers.

**The model applies a rule, it does not optimise.** Probe B shows a 5x advertised gap and a 7500x
advertised gap produce identical behaviour, and a genuine tie returns the model to chance. There is
no cost-benefit arithmetic happening, so the numbers a harness publishes do not need to be
accurate; they need to be ordered. That is convenient for implementation and a warning for
interpretation: an agent that "respects" a published cost is not thereby making good scheduling
decisions, and cannot be expected to trade a 60 s tool against an answer that is 10% better.

**Where this leaves the tiered-memory argument.** The costs this project has priced -- prefill per
uncached token, decode per output token, the fixed per-round provider cost, memory extraction at the
turn boundary -- cannot be delegated to the model by writing them into the prompt. In a realistic
pool, the model routes on task fit and ignores the price. Cost-aware routing therefore stays where
it already is, in the harness: batching reads, demoting outlines, keeping a stable prefix, taking
memory extraction off the critical path. The one place model-side cost exposure does pay is a pool
that deliberately contains substitutable tiers -- a hot and a cold route to the same bytes -- which
is exactly the shape a tiered-memory tool pool would have. There, one sentence in the description
moves the model from 53% to 100%.

## 5. Threats to validity

* **One model, one provider.** Everything here is `glm-5.3-flash` on z.ai. The size of the probe A
  effect is unlikely to be model-specific (it is a trivial discrimination), but the probe C null is
  a claim about how a particular model weighs task fit against a stated cost, and a model trained
  with more explicit budget-following could behave differently. The stage-1 probes are cheap
  (about 15 minutes for 900 trials) and are the right thing to re-run against another model.
* **Stage 1 is single-shot.** It measures the first `tool_use` block, not a whole trajectory.
  Stage 2 and stage 3 cover the multi-round case and agree with it.
* **Stage 2 and 3 are small.** 20 runs per stage-2 arm and 4 per stage-3 arm. The stage-2 negative
  control quantifies what that buys: the `plain`-to-`enforce` difference, which must be noise
  because a model cannot perceive an injected sleep, is 15 percentage points. The stage-2 system
  prompt carries no clock and the tool results carry no timing, so there is genuinely no channel
  through which the sleep could reach the model (the s15 lead prompt does carry a `Current time`
  line, but stage 3 has no enforce arm). Differences smaller
  than that cannot be read, and none of the annotation effects in stage 2 or 3 exceed it.
* **Stage-1 cells lost 3.8% of trials to provider rate limiting.** Losses were spread over a
  shuffled schedule and the missing (cell, rep) pairs were re-run afterwards, so the cells are
  balanced; but the re-run trials were sent later than the rest.
* **Probe D and the C top-up ran while stage 2 was running**, so all three share provider load.
  Stage 2's run order is shuffled across arms, which protects the between-arm contrasts, but its
  absolute wall times are measured under variable load and should not be compared with the wall
  times in `research/05_latency_breakdown/README.md` Part A.
* **The stage-3 `constants` workload has a floor.** The lead answered it with `bash` and grep in
  all 12 runs, in all three arms, so there was no `read_file` for the intervention to remove. The
  `summary` workload was added for exactly this reason.
* **The enforced delay is a flat 5 s per call.** A real tier boundary costs time in proportion to
  bytes moved, which would also reward reading less of a file rather than not reading it. That
  variant is not tested here.
* **`advertised_bill_s` in stage 2 is a counterfactual for the unannotated arms**: it prices the
  route the agent actually took using the same cost model, whether or not the agent was shown it.
  It measures the policy, not what the agent was charged.

## 6. Reproduction

```
# stage 1: about 900 single-shot trials, about 15 minutes at concurrency 8
python3 research/06_tool_cost/tool_cost_probe.py --probe all --reps 32 \
    --concurrency 8 --out research/06_tool_cost/data/tool_cost/stage1.jsonl
# re-run whatever the provider rate-limited, keeping cells balanced
python3 research/06_tool_cost/tool_cost_probe.py --probe all --reps 32 \
    --topup --out research/06_tool_cost/data/tool_cost/stage1.jsonl
# see what any cell actually sends
python3 research/06_tool_cost/tool_cost_probe.py --probe D --reps 1 --dry-run

# stage 2: 80 multi-round runs with real tool execution
python3 research/06_tool_cost/tool_cost_loop.py --reps 5 --concurrency 4 \
    --out research/06_tool_cost/data/tool_cost/stage2.jsonl

# stage 3: the real harness, sequential (each run wipes .memory/.tasks/... at the repository root)
python3 research/06_tool_cost/tool_cost_harness.py --reps 4 --workload constants
python3 research/06_tool_cost/tool_cost_harness.py --reps 4 --workload summary

# tables
python3 research/06_tool_cost/tool_cost_analyze.py \
    --stage1 research/06_tool_cost/data/tool_cost/stage1.jsonl \
    --stage2 research/06_tool_cost/data/tool_cost/stage2.jsonl \
    --md research/06_tool_cost/data/tool_cost/tool_cost_tables.md \
    --json research/06_tool_cost/data/tool_cost/tool_cost_tables.json
```

The intervention itself is `profile_run.py --tool-cost 'read_file=5.0' --tool-cost-default 0.05
[--tool-cost-framing seconds|qualitative|hardware] [--tool-cost-placement tool_desc|system_prompt|both]
[--tool-cost-policy]`. It rewrites the `tools` argument of every outgoing request rather than the
harness's tool tables, so the lead, the teammates and the one-shot subagents are covered in one
place and the auxiliary calls that carry no tools are left alone; `profile_meta` records the
settings, and the existing `tools_chars`/`system_chars` fields of the inputs sidecar measure what
the annotation added. With no `--tool-cost` flag the request is untouched, so the driver's default
behaviour is unchanged (the diff is additive).

The results also exist as a page: https://claude.ai/code/artifact/6de7f01b-27ae-4151-911f-42293a286e98

Artefacts: traces, logs and per-run JSON in `research/06_tool_cost/data/tool_cost/`
(`stage1.jsonl`, `stage2.jsonl`, `harness/` with one console log and trace per stage-3 run,
`tool_cost_tables.md` and `.json`).

[cleanup note: `research/06_tool_cost/data/tool_cost/harness/` holds no console logs, neither committed nor as git-ignored local files; each of the 24 stage-3 runs has its trace plus `.inputs.jsonl` and `.reads.jsonl` sidecars, and the per-run rows behind the §3.6 table are `harness_runs_constants.json` and `harness_runs_summary.json` in the same folder]

## B.W Additions from `weekly_progress/091626/tool_cost_exposure.md` (2026-09-14)

_(was §4, second paragraph)_

There is one exception, and it is the tiered-memory case exactly. Exposing costs to the model works
when the pool deliberately offers two routes to the same bytes, a hot one and a cold one, because
those are genuine substitutes. That is the shape a tiered-memory tool pool would have. There, one
sentence in a tool description moved the model from a coin flip to the cheap route every time, for
nine tokens and no loss of accuracy. A prototype that exposes a resident and a staged route to the
same working set should publish the costs, keep them in the right order rather than precise, and
say alongside them that cheaper is preferred.

_(was §5 Next)_

* Re-run stage 1 against a second model. It is 15 minutes for 900 trials, and finding 4 is the one
  claim here that could be model-specific.
* Make the enforced cost proportional to bytes moved rather than flat per call, which would also
  reward reading less of a file rather than not reading it (`read_file` with offset/limit) -- the
  behaviour a real tier boundary should incentivise.
* When the shared-file-block or KV-re-attach prototype exists, give it two advertised routes to the
  same bytes and measure whether finding 1 survives outside a synthetic pool.

[cleanup note: the digest's §3 findings are unnumbered bullets. From the context, "finding 4" is the realistic-pool null (Part B §3.3, the claim Part B §5 names as the one that could be model-specific) and "finding 1" is the interchangeable-tools result (Part B §3.1, probe A)]

---

## Data inventory

All paths are under `research/06_tool_cost/`. Everything listed is committed (e0e522b, 2026-09-15,
then moved here in the 2026-09-23 cleanup). `git status --ignored` lists no git-ignored local files in
this folder: there are no console logs or other local-only files for either part.

| path | what it holds | written by | used in | status |
|---|---|---|---|---|
| `data/tool_latency/` | Two probe runs on 2026-09-15, r1 = `run_20260915T201117_531820Z_313a25f7` (20:11:17–20:11:32 UTC) and r2 = `run_20260915T201133_281733Z_97e47bdf` (20:11:33–20:11:47 UTC), lane `/home/yq335/lanes/toolprobe`; `run_start` names `glm-5.3-flash` but the client is disabled, so no model calls. Per run: the trace `run_*.jsonl` (1,332 `tool_start`/`tool_end` pairs, plus the script-created task board of 133 tasks and 25 edges that the task-DAG census in `research/07_task_dag/` Part B §2.1 excludes) and the sidecar `run_*.records.jsonl` (1,332 records = 54 warm-up + 1,278 timed; 2 × 1,278 = 2,556 timed dispatches). | `tool_latency_probe.py --reps 24 --label r1` / `--label r2` | Part A §2.1, §3 | published |
| `data/tool_latency/tool_latency_tables.md`, `.json` | Basis A per case and pooled per tool (lead and teammate pools), basis B observed spans of the 24 latency-profiling traces in `research/05_latency_breakdown/data/latency_profiling/`, and the A-vs-B comparison. Regenerate byte-identically (verified 2026-09-23). | `tool_latency_analyze.py` | Part A §3.1–3.4 | published |
| `data/tool_cost/` | Part B stages 1 and 2 plus `harness/` (stage 3). | — | Part B | published |
| `data/tool_cost/stage1.jsonl` | 928 single-shot trials from 2026-09-14: 893 successful + 35 rate-limited errors (rows per probe: A 331, B 202, C 235, D 160). | `tool_cost_probe.py --probe all --reps 32`, then `--topup` | Part B §3.1–3.5 | published |
| `data/tool_cost/stage2.jsonl` | 80 multi-round runs, 4 arms (`plain`, `advertise`, `enforce`, `both`) × 20 (4 tasks × 5 reps). | `tool_cost_loop.py --reps 5` | Part B §3.3 (stage-2 paragraphs) | published |
| `data/tool_cost/tool_cost_tables.md`, `.json` | Stage-1 tables A–D (with the Fisher p per cell) and the stage-2 tables. Regenerate byte-identically. | `tool_cost_analyze.py` | Part B §3.1–3.4 | published |
| `data/tool_cost/harness/` | Stage 3: 24 s15 sessions on 2026-09-14, 22:10–22:29 UTC — 12 `constants` runs (`plain` / `advertise` / `policy` × r0–r3, interleaved), then 12 `summary` runs in the same pattern; each run is a trace plus `.inputs.jsonl` and `.reads.jsonl`. | `tool_cost_harness.py` through `research/common/profile_run.py --tool-cost` | Part B §3.6 | published |
| `data/tool_cost/harness/harness_runs_constants.json` | Per-run rows of the 12 `constants` sessions (arm, rep, model calls, tool counts, read/bash chars, coverage, wall); the only committed source of the §3.6 `constants` rows. | `tool_cost_harness.py --workload constants` | Part B §3.6 | published |
| `data/tool_cost/harness/harness_runs_summary.json` | The same for the 12 `summary` sessions (per-run coverage 0.538–1.0, 12,815 chars read in every run). | `tool_cost_harness.py --workload summary` | Part B §3.6 | published |
| `data/tool_cost/harness/harness_runs.json` | The 12 `constants` sessions again: rows identical to `harness_runs_constants.json` except that the `workload` field is missing. | an earlier version of `tool_cost_harness.py`, before its per-workload output name (the script has a single commit, so the exact version is not determinable) | none | duplicate / superseded |
| `tool_cost_page.html` | "Telling the Agent What It Costs": HTML source of the published page claude.ai/code/artifact/6de7f01b-27ae-4151-911f-42293a286e98, six sections summarising Part B. | page source, committed in e0e522b | Part B §6; 2026-09-16 deck slide 8 | published |

**Previously uncited items**

- `data/tool_cost/harness/` — why: stage 3 of Part B, the same annotation run inside the real s15 harness
  (two workloads × three arms × four runs); what became of it: it is the whole basis of Part B §3.6, which
  cited it only as `harness/` in its Artefacts line.
- `harness_runs_constants.json` — why: the per-run summary `tool_cost_harness.py` writes for the
  `constants` workload; what became of it: the §3.6 `constants` rows are its per-arm means.
- `harness_runs_summary.json` — why: the same for the `summary` workload, added because `constants` had a
  floor; what became of it: the §3.6 `summary` rows are its per-arm means.
- `harness_runs.json` — why: the first summary of the `constants` sessions, written before the output file
  was named per workload; what became of it: superseded by `harness_runs_constants.json`, which repeats it
  with the `workload` field added; nothing cites it.
- `tool_cost_page.html` — why: the shareable version of Part B; what became of it: published as
  claude.ai/code/artifact/6de7f01b-27ae-4151-911f-42293a286e98, linked from Part B §6, the weekly digest
  and slide 8 of the 2026-09-16 deck.

## Source map

One row per `##` (and numbered `###`) section of every source; new locations are sections of this README (`research/06_tool_cost/README.md`).

| old file | old section | new location | status |
|---|---|---|---|
| `s15_integrated_harness/tool_latency_profile.md` | # title: The harness-side price of one tool call, for every tool the lead and a teammate can call | Part A (heading) | kept (H1 became the Part A heading) |
| `s15_integrated_harness/tool_latency_profile.md` | preamble (profiling question, 2026-09-15) | Part A, preamble | kept |
| `s15_integrated_harness/tool_latency_profile.md` | ## Short answer | Part A, Short answer | kept; "except five" corrected to "except four" |
| `s15_integrated_harness/tool_latency_profile.md` | ## 1. The question | Part A §1 | kept |
| `s15_integrated_harness/tool_latency_profile.md` | ## 2. Design | Part A §2 | kept |
| `s15_integrated_harness/tool_latency_profile.md` | ### 2.1 Basis A: controlled micro-benchmark | Part A §2.1 | kept |
| `s15_integrated_harness/tool_latency_profile.md` | ### 2.2 Basis B: observational spans of the real runs | Part A §2.2 | kept |
| `s15_integrated_harness/tool_latency_profile.md` | ## 3. Results | Part A §3 | kept |
| `s15_integrated_harness/tool_latency_profile.md` | ### 3.1 Lead pool, pooled by tool | Part A §3.1 | kept |
| `s15_integrated_harness/tool_latency_profile.md` | ### 3.2 Teammate pool, pooled by tool | Part A §3.2 | kept |
| `s15_integrated_harness/tool_latency_profile.md` | ### 3.3 Argument scaling is the whole story for the expensive tools | Part A §3.3 | kept |
| `s15_integrated_harness/tool_latency_profile.md` | ### 3.4 Observational spans agree, with explained gaps | Part A §3.4 | kept |
| `s15_integrated_harness/tool_latency_profile.md` | ## 4. What this means for the harness | Part A §4 | kept |
| `s15_integrated_harness/tool_latency_profile.md` | ## 5. Threats to validity | Part A §5 | kept |
| `s15_integrated_harness/tool_latency_profile.md` | ## 6. Reproduction | Part A §6 | kept |
| `weekly_progress/091626/tool_execution_cost.md` | # title + header paragraph (date, full report, tables, provider) | Part A, heading and preamble | dropped (duplicate of Part A heading and preamble) |
| `weekly_progress/091626/tool_execution_cost.md` | ## 1. Why | Part A, A.W (was §1) | kept |
| `weekly_progress/091626/tool_execution_cost.md` | ## 2. What was run | Part A §2.1–2.2 | dropped (duplicate of Part A §2.1–2.2) |
| `weekly_progress/091626/tool_execution_cost.md` | ## 3. Findings | Part A, Short answer, §3.1–3.4 and §4 | dropped (duplicate of Part A Short answer, §3 and §4; its "except four" is the value the Short-answer correction adopts) |
| `weekly_progress/091626/tool_execution_cost.md` | ## 4. Consequence for the harness | Part A, A.W (was §4) | kept |
| `weekly_progress/091626/tool_execution_cost.md` | ## 5. Reproduction | Part A §6 | dropped (duplicate of Part A §6) |
| `s15_integrated_harness/tool_cost_profile.md` | # title: Exposing hardware cost to the harness: does an advertised tool latency change the tool_use the model generates? | Part B (heading) | kept (H1 became the Part B heading) |
| `s15_integrated_harness/tool_cost_profile.md` | preamble (run 2026-09-14, provider) | Part B, preamble | kept |
| `s15_integrated_harness/tool_cost_profile.md` | ## 0. Summary | Part B §0 | kept |
| `s15_integrated_harness/tool_cost_profile.md` | ## 1. The question | Part B §1 | kept |
| `s15_integrated_harness/tool_cost_profile.md` | ## 2. Design | Part B §2 | kept |
| `s15_integrated_harness/tool_cost_profile.md` | ### 2.1 Stage 1: single-shot probes | Part B §2.1 | kept |
| `s15_integrated_harness/tool_cost_profile.md` | ### 2.2 Stage 2: multi-round loop with real execution | Part B §2.2 | kept |
| `s15_integrated_harness/tool_cost_profile.md` | ### 2.3 Stage 3: the real s15 harness | Part B §2.3 | kept |
| `s15_integrated_harness/tool_cost_profile.md` | ## 3. Results | Part B §3 | kept |
| `s15_integrated_harness/tool_cost_profile.md` | ### 3.1 Between interchangeable tools the advertised cost decides everything | Part B §3.1 | kept |
| `s15_integrated_harness/tool_cost_profile.md` | ### 3.2 There is no dose-response: this is a tie-break, not a calculation | Part B §3.2 | kept |
| `s15_integrated_harness/tool_cost_profile.md` | ### 3.3 Between tools that differ in function, the advertised cost does nothing | Part B §3.3 | kept |
| `s15_integrated_harness/tool_cost_profile.md` | ### 3.4 What changes behaviour is an objective, not information | Part B §3.4 | kept |
| `s15_integrated_harness/tool_cost_profile.md` | ### 3.5 What the intervention costs to run | Part B §3.5 | kept |
| `s15_integrated_harness/tool_cost_profile.md` | ### 3.6 In the real s15 harness the intervention has no room in either direction | Part B §3.6 | kept |
| `s15_integrated_harness/tool_cost_profile.md` | ## 4. What this means for the harness | Part B §4 | kept |
| `s15_integrated_harness/tool_cost_profile.md` | ## 5. Threats to validity | Part B §5 | kept |
| `s15_integrated_harness/tool_cost_profile.md` | ## 6. Reproduction | Part B §6 | kept; cleanup note added (no console logs exist) |
| `weekly_progress/091626/tool_cost_exposure.md` | # title + header (date, full report, tables, page link, provider) | Part B, preamble and §6 (page link) | dropped (duplicate of Part B preamble; the page link is in Part B §6) |
| `weekly_progress/091626/tool_cost_exposure.md` | ## 1. Why | Part B §1 | dropped (duplicate of Part B §1) |
| `weekly_progress/091626/tool_cost_exposure.md` | ## 2. What was run | Part B §2.1–2.3 and §3 (counts) | dropped (duplicate of Part B §2 and the trial counts of §3) |
| `weekly_progress/091626/tool_cost_exposure.md` | ## 3. Findings (bullets, cautions, reference table) | Part B §0, §3.1–3.6, §5 | dropped (duplicate of Part B §0, §3 and §5; the table's "p = 7e-06" rounds §3.1's 7.1e-06 / 7.7e-05) |
| `weekly_progress/091626/tool_cost_exposure.md` | ## 4. Consequence for the argument, paragraph 1 | Part B §4 (last paragraph) | dropped (duplicate of Part B §4) |
| `weekly_progress/091626/tool_cost_exposure.md` | ## 4. Consequence for the argument, paragraph 2 | Part B, B.W (was §4, second paragraph) | kept |
| `weekly_progress/091626/tool_cost_exposure.md` | ## 5. Next | Part B, B.W (was §5 Next) | kept; cleanup note on its finding numbers |

## Cleanup notes

- **Part A, Short answer — corrected** "except five" to "except four" [corrected 2026-09-23]. Evidence:
  in `research/06_tool_cost/data/tool_latency/tool_latency_tables.md` only bash (`bash_grep` 10.9 ms,
  `bash_python_file` 135 ms), read_file (45 KB 16.4 ms, 146 KB 37.3 ms), glob (recursive 23.1 ms lead,
  24.4 ms teammate) and create_worktree (607 ms) have an argument class above 10 ms; write_file peaks at
  6.10 ms and edit_file at 2.70 ms. §4 says "exactly four exceptions" and the weekly digest's short answer
  said "except four". The bullet "Five tools scale with their arguments" is left as written: it counts
  write_file and edit_file among the argument-scaling tools (write_file does scale, 2.13 → 6.10 ms from
  200 B to 20 KB), which is a different count from the tools outside the 0.2–10 ms band.
- **Part B §6 — flagged**: the Artefacts line promises "one console log and trace per stage-3 run"; no
  console log exists, committed or local.
- **Part B, B.W (was §5 Next) — flagged**: "finding 4" and "finding 1" refer to unnumbered bullets of the
  digest; the note maps them to Part B §3.3 and §3.1.
- **Dropped digest blocks** (duplicates of the bases, listed in the source map): `tool_execution_cost.md`
  header, §2, §3 and §5; `tool_cost_exposure.md` header, §1, §2, §3 (bullets, cautions and the reference
  table) and §4 paragraph 1. Two of them stated a base number differently, and the base value is the one
  kept: `tool_execution_cost.md` §3 rounds the denial costs to "0.3 ms" (mutating bash, worktree) and
  "0.4 ms" (plan gate) where Part A has 0.25–0.31, 0.27 and 0.44 ms; `tool_cost_exposure.md` §3's table
  gives "p = 7e-06" for all nine annotated cells where Part B §3.1 (and `tool_cost_tables.md`) has
  7.1e-06 in eight cells and 7.7e-05 in the ninth. The only digest tokens that do not survive verbatim are
  those rounded values ("0.3 ms", "0.4 ms") and two reference-table fragments whose numbers Part B keeps in
  full ("33 of 40 | 32 of 40" and the "stage 3" row label).
