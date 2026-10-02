# 06 · What tools cost: execution latency and cost exposure

> **Status:** complete — four parts, nothing pending. **Dates:** Part B ran 2026-09-14, Part A 2026-09-15,
> Parts C and D 2026-09-29 → 10-01. **Model / provider:** Parts A and B z.ai `glm-5.3-flash` (Part A's probe
> makes no model calls — the tools are local Python and shell — and its observational basis reuses the
> 2026-09-12 latency-profiling runs of `research/05_latency_breakdown/`); Part C's census reads every trace
> in the repository (GLM, DeepSeek, Qwen3.8-27B, Qwen3-32B), its sweep makes no model calls, and its
> end-to-end cells ran Qwen3.8-27B (thinking) and Qwen3-8B (no thinking) on vLLM 0.29, one RTX PRO 6000
> each (the 05 Part B card).
> **Commit:** e0e522b (2026-09-15, "tool-use average latency profiling") holds Parts A and B, both weekly
> digests, their scripts and data; Parts C and D are not committed yet.
> **Presented:** 2026-09-16 deck (`weekly_progress/091626/slides.html`), slide 7 (Part A, the per-tool
> price list) and slide 8 (Part B, advertised cost vs tool choice; published page
> claude.ai/code/artifact/6de7f01b-27ae-4151-911f-42293a286e98, source `tool_cost_page.html`). Parts C
> and D: not presented yet.

**Contents**

- **Part A — The harness-side price of one tool call, for every tool the lead and a teammate can call.**
  What the harness charges per tool call: a 54-case probe (2,556 timed dispatches) plus the observed
  tool spans of the 24 latency-profiling runs.
  - A.W Additions from `weekly_progress/091626/tool_execution_cost.md`
- **Part B — Exposing hardware cost to the harness: does an advertised tool latency change the tool_use
  the model generates?** 893 single-shot trials, 80 multi-round runs, 24 s15 sessions.
  - B.W Additions from `weekly_progress/091626/tool_cost_exposure.md`
- **Part C — Every supported tool, every path, and the end-to-end share: is it really under 1 %?**
  A census of all 173,380 tool calls in the repository's 1,801 traced runs, a controlled sweep of every
  supported tool under six host conditions (2 × 19k dispatches), the model-bearing tools with a real
  model, the same sessions with injected tool latency, and tool-heavy sessions; tables T0–T8.
- **Part D — Do tools take 30–85 % of agent latency? Sutradhara's claim, its reception, and the
  independent evidence.** Background research with verified quotes.
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
| `tool_census.py` | Part C C1: every tool call in every trace of the repository -- per call, per run, per request (Sutradhara's unit); offline |
| `tool_sweep_probe.py` | Part C C2: controlled probe of every supported tool, dispatch path, argument class, task-board size and heavy command; no model calls (reuses `tool_latency_probe.py`) |
| `tool_sweep_run.sh` | Part C C2: one sweep run = the probe under six host conditions (NFS vs node-local lane, trace summary / full / off / on NFS) |
| `tool_model_probe.py` | Part C C3: `task`, `Workflow` and the `compact` summary call against a real model |
| `tool_heavy_workloads.py` | Part C C4b: tool-heavy sessions (test-driven bug fix, site-packages search, web lookup) through `profile_run.py` |
| `tool_share_pipeline.sh` | Part C C3/C4: one GPU job = two independent cells (vLLM on each GPU, node-local lane, the cell's drivers) |
| `tool_share_gpu.sbatch`, `tool_share_cpu.sbatch` | Slurm entry points (GPU cells; census / sweep / tables) |
| `tool_share_analyze.py` | Part C tables T0–T8 |
| `test_tool_census.py` | unit tests of Part C's definitions (span pairing, waits, blocked-on-tools, FTR bounds, nested model time) and of `--tool-delay` |
| `data/tool_latency/` | the two probe runs and their tables (Part A) |
| `data/tool_cost/` | stage-1 and stage-2 JSONL, their tables, and `harness/` with the 24 stage-3 sessions (Part B) |
| `data/tool_census/`, `data/tool_sweep/`, `data/tool_e2e/`, `data/tool_share/` | Part C (see the Data inventory) |

Shared code outside this folder: `research/common/profile_run.py` (the `--tool-cost*` flags of Part B
stage 3 and the `--tool-delay` flag of Part C; it also wrote the latency-profiling traces that Part A's
basis B reads from `research/05_latency_breakdown/data/latency_profiling/`);
`research/common/fixtures/tool_heavy/` (Part C's frozen inputs); `research/05_latency_breakdown/latency_workloads.py`
(the workloads of Part C's injected-latency cells).

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

# Part C — Every supported tool, every path, and the end-to-end share: is it really under 1 %?

_Run 2026-09-29 → 30, prompted by arXiv 2601.12967 (Sutradhara), which reports tool calls at 30–85 % of
agent latency where Part A and research/05 found under 1 %. Part D is the background research on that
paper; this part is the measurement._

## C.0 Short answer

**For the workloads this repository runs, yes: a tool call costs milliseconds, and tools are well under
1 % of agent latency by every definition — 0.09 % of the corpus's wall time, under 1.05 % in 99 % of its
1,801 runs (2.8 % at most, once deliberate waits and `find /` sweeps are set aside), and two to three
orders of magnitude below the paper at every percentile of the per-request distribution it uses. But the
share belongs to the workload and the serving stack, not to the harness: the same harness lands in the
paper's range when either changes. Given heavy work for its own tools (a repository-wide grep, a test
suite) it spends 15–45 % of a session in tools on Qwen3.8-27B; given a fast non-thinking model (Qwen3-8B)
and the paper's tool latencies, 35–89 %.**

- **Every supported tool, priced (§C.4.1–C.4.2).** Of the 32 tools the s15 + s16 harness exposes, 26
  cost 0.2–20 ms per call at their median argument class on today's NFS lanes (0.2–10 ms node-local); the
  exceptions are `task` (a subagent's whole run), `glob` over the whole tree (~135 ms), `Workflow`'s
  orchestration (~90 ms), `create_worktree` (4.2 s on NFS, 0.23 s node-local), and `read_file` and
  `claim_task` at 20–25 ms. `read_file`, `write_file` and `bash` scale with their arguments; the dearest
  supported-tool calls measured are the full lesson test suite (34 s) and a content grep over all of
  site-packages (42 s). Over the corpus the call-weighted
  **average tool call costs 12.4 ms (median 1.0 ms, p99 31 ms)**, 3.4 ms without `task`, whose spans are
  subagent runs and 99.95 % model time.
- **End to end (§C.4.5).** Tools are 0.09 % of the corpus's 1,042 h of wall time (0.016 % without
  subagent spans and deliberate waits); per request the FTR share is 0.004–0.3 % at the median of every
  run set, no request of 3,287 exceeds 1.8 % except two whose `find /` sweep took 9–10 s (5.1 %, 2.4 %)
  — against the paper's 32 % / 61 % / 85 %.
- **What the harness adds is not the tools' work (§C.4.3).** On NFS every file tool pays a task-board
  scan of 2.35 ms per task (0.1 ms locally) and metadata-heavy tools cost 2–20× their local price; for
  large results the dominant cost is the tracer (a 4 MB read: 28 ms untraced, 474 ms traced, 907 ms in
  full-output mode). None of this changes the share by more than a fraction of a percent.
- **The tools whose price is a model's work (§C.4.4).** `task`, `Workflow` and the `compact` summary
  are model calls: 95–100 % of their spans is model time — on Qwen3.8-27B (thinking) 19–66 s per `task`,
  22 s per `Workflow`, ~80 s per summary; on Qwen3-8B 3–12 s, 4 s, 5–8 s. They are not "tools" in the
  paper's sense and are excluded from every share below.
- **Injected tool latency (§C.4.6).** On Qwen3.8-27B the share follows the replay exactly (≈ 6 s per
  call: 10.7–14.1 % measured against 9.6–13.5 % predicted; calls per session unchanged, because the
  model has no clock); reaching 30 % would take 5–21 s per call, 85 % 65–272 s. On Qwen3-8B, which runs
  short rounds with several calls each (and loops on the long-context tasks), 1.09 s per call already
  takes 22–63 % of a session and ~6 s 35–89 %.
- **Heavy work for the supported tools (§C.4.7).** A site-packages search: 45 % of the session in
  tools on Qwen3.8-27B (94 % on Qwen3-8B); a test-driven bug fix with the full lesson suite: 15 % / 20 %;
  a curl lookup: 1–5 %.
- **Where the gap comes from (§C.4.8).** Share ≈ t / (t + L + o) per tool call. On the workloads of this
  repository the harness spends L = 3.8–48 s of model time per tool call (3.8–5.2 s on Qwen3-32B without
  thinking, 8.7–48 s on GLM or the thinking Qwen3.8-27B), while the paper's own points imply
  L = 0.7–1.6 s — which only the non-thinking Qwen3-8B matches (0.8–5.8 s); and its tools are local
  (1–130 ms per call on average) where the paper's are remote services (~6 s). Change both and the
  paper's numbers appear.

## C.1 The question

arXiv 2601.12967 (Sutradhara, Microsoft; v3 2026-04-22) says that "tool calls account for 30-85% of FTR
latency" of production agentic requests. Part A priced every tool of this harness at 0.2-10 ms (four
argument-dependent exceptions) and found tools at 0.24 % of wall time on GLM; the Qwen/vLLM reruns found
0.35 % (05 Part B) and 0.64 % (09). Two orders of magnitude apart, so either one of the two numbers is
wrong or they measure different things. Part A left four doors open that this part closes:

1. **Coverage.** Part A timed `task` with the model disabled (0.9 ms of dispatch), `compact` as its
   marker (0.2 ms) and `spawn_teammate` without a task; it never ran s16's `Workflow`, the one-shot
   subagent pool, the lead's `lead-events` thread (team turns, where the permission rules differ), s17's
   tools, background bash to completion, or any task-board size above five tasks.
2. **Host.** Part A ran on the old host (local disk). Every run since 2026-09-21 ran on the Slurm cluster,
   whose home directory is NFS; the repository is 8x larger (941 MB) and `read_file` gained a residency
   guard (0b65c467).
3. **Workloads.** Every workload in the repository is light on tools. "Under 1 %" was never tested in the
   regime the paper describes: remote tools that take seconds.
4. **Metric.** Part A priced calls and summed spans over wall time. The paper reports a per-request share
   of FTR -- the time to the first token of the final answer -- as a CDF, and quotes its median and tail.

## C.2 Design

Five pieces, each answering one of the doors above (scripts beside this README):

| piece | script | what it measures | model |
|---|---|---|---|
| C1 corpus census | `tool_census.py` | every tool call in every trace of the repository (1,801 runs, 173,380 calls), per tool / agent kind / model / host; per-run and per-request tool share under several definitions | none (offline) |
| C2 controlled sweep | `tool_sweep_probe.py`, `tool_sweep_run.sh` | every supported tool, every dispatch path, argument sweeps, task-board sizes, heavy commands; six host conditions (NFS vs node-local lane, trace summary/full/off, trace on NFS); two runs | none (client disabled) |
| C3 model-bearing tools | `tool_model_probe.py` | `task` (3 prompt classes), `Workflow` (review-changes), the `compact` summary call (3 history sizes), with a real model, split into nested model time / nested tool time / the rest | Qwen3.8-27B, Qwen3-8B |
| C4a injected latency | `profile_run.py --tool-delay`, 05 `latency_workloads.py` | the same sessions with every executed tool call slowed by 0 / 0.29 / 1.09 / ~6 s (the paper's SWE-bench, BFCL and production means) -- measured share vs replay prediction | both |
| C4b heavy workloads | `tool_heavy_workloads.py` | the supported tools doing heavy real work: a SWE-bench-like test-driven bug fix (pytest file + the full lesson suite), repository-scale greps over site-packages, a curl-based web lookup | both |
| C5 tables | `tool_share_analyze.py`, `tool_share_pipeline.sh`, `*.sbatch` | T0-T8 below | -- |

**Supported tools** means what the s15 + s16 harness exposes, because that is the harness every
experiment in this repository runs: the lead pool (26 built-ins + `Workflow` + the 4 mock-MCP tools once
`connect_mcp` has run), the teammate pool (10, `submit_plan` teammate-only) and the one-shot subagent pool
(5). s17's five tools are timed as a cross-check; `agents/` and s01-s14 are earlier copies of the same
tools and are left out. The harness has no network tool; its MCP servers are in-process mocks.

**Backends.** Qwen3.8-27B (thinking on, the 05 Part B server flags, 262,144-token window) and Qwen3-8B
(thinking off, the 10/11 server flags, 40,960-token window) on vLLM 0.29, one RTX PRO 6000 per cell — the
05 Part B card — in single-cell Slurm jobs (2026-09-30 20:02 → 10-01 01:14 UTC, alphagpu51 and 54; the RTX
nodes had been drained for maintenance for a day, and 2-GPU H100/H200 jobs queued as a fallback were
cancelled unused). Qwen3-8B sessions are capped at 10 minutes (`--max-seconds 600 --hard-deadline`)
because the model loops on long contexts (§C.4.6). GLM-5.3-flash enters through the corpus (05 Part A and
every GLM run before it).

## C.3 Measurement definitions (fixed before any run)

- **Per call.** *span* = `tool_start -> tool_end`: the harness-side cost the agent loop pays (PreToolUse
  hooks, the handler, PostToolUse, trace writes); *net* = span minus nested `permission_wait` spans (a
  human answering a prompt in the interactive traces of 01 and the s15 samples; elsewhere the driver's
  automatic approval, a fraction of a millisecond); *handler* = the nested `tool_execution` span
  (`call_tool_handler`, code.py:1164). For `task` and `Workflow` the span contains model calls: it is split
  into the union of the model calls of the agents created inside it, the tool calls of those agents, and
  the rest. Background bash is counted at dispatch (the span) and, in C2, to completion.
- **Artifacts** are flagged, not dropped: bash commands that wait on purpose (`sleep`, `time.sleep`),
  `find /` sweeps, and bash calls that hit the 120 s cap.
- **Per run.** tool / wall; *plain* excludes `task`/`Workflow`; *clean* also excludes artifacts;
  *blocked-on-tools* = the time in which some plain tool span is open and no main-loop model call (purpose
  lead, teammate, one_shot, workflow_agent) is in flight -- the tool time on the critical path, which for a
  solo run equals its tool time and for a team run removes tool calls hidden behind other agents' model
  calls.
- **Per request (Sutradhara's unit).** A user-triggered lead turn plus the team/background/cron turns that
  follow it until the next user turn. Its final call is the last lead call without `tool_use`. FTR ends at
  that call's first visible token (streamed runs: `first_visible_ms` from the `.inputs` sidecar); for
  unstreamed traces FTR is bounded below by the final call's request start (which bounds the share from
  above). The share is blocked-on-tools time inside [request start, FTR] over FTR.

## C.4 Results

### C.4.1 Every supported tool in one table (T0)

Full tables: `data/tool_share/tool_share_tables.md` (T0-T8, and `.json`), `data/tool_census/census_tables.md`
(the corpus). *Corpus* = every call of that tool in the repository's 1,801 traced runs (artifacts
excluded, all statuses, all hosts and models); *controlled* = today's sweep on the production-like
condition (`nfs-sum`: lane and trace on NFS, as in the 05/09 Qwen runs), the median of the tool's
argument-class medians and the range from its cheapest to its dearest class; runs r1 + r2 pooled.

| tool | corpus calls | corpus mean ms | corpus p99 ms | controlled: median class ms | argument-class range ms |
|---|---:|---:|---:|---:|---|
| read_file | 119,001 | 2.6 | 19.4 | 25.0 | 2.7 (s17, 200-line cap) – 564 (4 MB, cold) |
| edit_file | 21,488 | 0.8 | 4.7 | 8.7 | 6.0 – 28.8 (1 MB file) |
| todo_write | 9,070 | 0.3 | 1.1 | 0.67 | – |
| bash | 5,982 | 26.4 | 361 | 16.3 | 4.2 (echo) – 42,036 (grep over all of site-packages) |
| glob | 4,603 | 3.9 | 36.7 | 135 | 4.3 (`*.md`) – 140 (`**/*.py`) |
| list_tasks | 4,130 | 6.0 | 47.0 | 3.6 | 3.3 – 4.0 (board of 1–5; see T3b) |
| write_file | 3,468 | 1.4 | 26.0 | 10.4 | 5.1 – 182 (1 MB) |
| send_message | 960 | 5.6 | 46.2 | 4.4 | – |
| claim_task | 911 | 4.3 | 36.2 | 20.1 | – |
| create_task | 839 | 5.5 | 26.6 | 11.6 | – |
| complete_task | 748 | 12.6 | 70.8 | 19.4 | – |
| spawn_teammate | 693 | 25.1 | 97.1 | 11.3 | 1.7 (no task) – 20.9 (with task_id) |
| request_shutdown | 497 | 7.4 | 36.9 | 4.2 | – |
| get_task | 372 | 1.5 | 4.9 | 3.1 | – |
| update_task | 232 | 8.9 | 30.4 | 14.7 | – |
| list_teammates | 82 | 0.5 | 1.0 | 0.44 | – |
| task | 46 | 33,924 | 373,832 | (C3) | the span is a subagent's whole run: 99.95 % model time |
| compact | 16 | 0.1 | 0.3 | 0.21 | marker only; the summary call follows the span (C3) |
| submit_plan | 16 | 4.1 | 5.5 | 4.4 | – |
| review_plan | 13 | 2.5 | 3.6 | 4.2 | – |
| list_crons / schedule_cron / cancel_cron | 5 / 3 / 2 | 0.2 / 2.4 / 0.9 | – | 0.47 / 0.73 / 0.43 | – |
| load_skill | 0 | – | – | 1.2 | 0.5 (miss) – 2.0 (hit) |
| request_plan | 0 | – | – | 4.5 | – |
| connect_mcp | 0 | – | – | 0.51 | – |
| mcp__docs__search / get_version, mcp__deploy__status / trigger | 0 | – | – | 0.41 – 0.61 | – |
| Workflow | 0 | – | – | 87.0 | 82 (resume) – 92 (mock run, 8 agents); real runners in C3 |
| create_worktree | 0 | – | – | 4,216 | 230 on a node-local lane |

Call-weighted over the whole corpus (173,177 calls of supported tools, artifacts excluded, `task`
included) the **average tool call costs 12.4 ms: median 1.0 ms, p99 31 ms**; without `task` it is
3.4 ms (median 1.0, p99 31). The corpus never called five tools (`load_skill`, `request_plan`,
`connect_mcp`, the mock-MCP tools, `create_worktree` for real, `Workflow`); the sweep is the only price
for them. Three names in the corpus are not supported tools: `read_files` (95 calls, the archived 03
batch-read intervention) and `grep` / `append_file` (27 calls in 11, names the model invented; each
failed as an unknown tool in 0.1 ms).

### C.4.2 The price list today, by argument class (T1)

Medians on the NFS lane (`nfs-sum`), node-local lane in parentheses; ms; r1 + r2.

- **read_file scales with bytes returned, not with the page cache.** 1 KB 6.2 (1.1), 64 KB 25.8 (15.4),
  512 KB 115 (82), 2 MB 292 (250), 4 MB 522 (474); a cold read after `posix_fadvise(DONTNEED)` costs the
  same within 10 % (4 MB: 564 vs 522), a re-read under the residency guard the same (516), and
  `offset/limit` on the 2 MB file returns to 20.5 (9.7). The slope is ~0.12 ms per KB on either lane --
  and it is the **tracer**, not the disk: with `HARNESS_TRACE=0` the 4 MB read costs 27.8 ms (T3).
- **write_file / edit_file**: 100 B 8.3 (1.3) … 1 MB 182 (135); an edit is ~9 (1.5–1.9) up to 146 KB and
  29 (3.4) on a 1 MB file.
- **glob pays for tree coverage**: `*.md` 4.3 (1.3), `research/**/*.py` 40 (15), `**/*.md` 136 (25).
- **bash is the command**: echo 8.0 (4.7), ls 13.6, scoped grep 13.7, `python3 -c` 97 (94),
  a unittest file 269 (261), a pytest file 1,755 (1,667), 60 KB of output 26.5, a non-zero exit 11.7;
  greps over installed packages 0.9–4.2 s, a `find` over site-packages 3.1 s, **the full lesson suite
  34.4 s (29.8 s)** and **a content grep over all of site-packages 42 s (41 s)** -- the two most
  expensive supported-tool calls measured. A `sleep 1` costs 1,011.6 (1,007.9): the harness adds 8–12 ms
  around a subprocess. Background bash: dispatch 4.7 (1.9); a 0.2 s job's result is collected 214 ms after
  dispatch.
- **Coordination tools** (task board, team protocol, cron, todo, skills, MCP mocks): 0.2–20 ms; the
  board tools cost 3–20 ms on NFS against 0.8–2.7 ms locally (their file locks and task-file reads and
  writes go to the NFS server).
- **create_worktree** runs five git commands and a checkout: 4.2 s on NFS, 230 ms node-local (in each
  lane's own 575-file repository); raw `git worktree add` of the full 941 MB repository takes 3.9 s on
  local disk. `spawn_teammate` with a `task_id` (claims the task inside the span) 21 (3.6) vs 1.7
  without.
- **Workflow** with the mock runner -- pure orchestration of 8 workflow agents, journal and locks -- 92
  (47); a journal resume 82 (35).
- **The other dispatch paths cost what the lead's costs**: the `lead-events` thread (team turns)
  12.9 for a 13 KB read vs 11.0 on the main thread, and denies non-display bash and confirm-MCP calls
  in 0.2–0.4; the one-shot subagent pool 9.5 (read) / 136 (deep glob); the teammate pool with the real
  claim guard 13.9 (the guard itself refuses an unclaimed teammate in 1.0); s17's own tools 2.7 (read,
  capped at 200 lines) / 135 (glob).

### C.4.3 What the host adds (T2, T3, T3b)

- **Drift since Part A (T2).** On a node-local lane today's medians match or beat the old host's
  (read_file 13 KB 3.5 vs 6.8, glob deep 17–25 vs 26, claim/complete 2.3–2.7 vs 3.2–3.4). On the NFS
  lane the same cases cost more: reads 1.1–2.1×, writes and edits 1.5–4.5×, glob 5–6×, board tools
  5–7×, `create_worktree` 7.3×.
  Part A's "0.2–10 ms except four" holds for a local lane and needs "except glob, create_worktree and
  every board-scanning call" on NFS.
- **The task board is on every file tool's path (T3b).** `run_agent_*` resolves its cwd through
  `assignment_cwd` → `_owner_in_progress`, which loads every task file. Each task on the board costs
  every lead and teammate file tool **2.35 ms on NFS, 0.098 ms locally**: 1,000 tasks turn a 13 KB read
  into 2.34 s (101 ms locally); `create_task` alone does not scan (flat 10–12 ms). The workload runs keep 3–4
  tasks (7–10 ms per file call on NFS); the 28-teammate stress run of the s15 samples created 57 (~130 ms).
- **The tracer is the largest harness-side cost for big results (T3).** Every tool result is
  redacted (four regular expressions) and SHA-256-hashed in full before it is summarised
  (trace_runtime.py `summarize_output`), and full-output mode redacts it a second time and writes it
  out. A 4 MB read costs 27.8 ms with `HARNESS_TRACE=0`, 474 ms with the default summary trace and
  907 ms with full output; a 1 MB write 2.1 / 135 / 142 ms; 60 KB of bash output 7.4 / 22.8 / 36.2 ms.
  Tracing is on by default in s15 (`HARNESS_TRACE` defaults to 1), and every profiling run in this
  repository used it (05/09 in full mode), so every tool span measured here and in Part A includes it.
  Writing the trace to NFS instead of local disk adds 0.2–2 ms per call.

### C.4.4 Tools whose price is a model's work (T4)

One dispatch at a time through the lead loop's own path (`tool_model_probe.py`), the span split into the
model calls of the agents created inside it (an interval union for the concurrent workflow agents), their
tool calls, and the rest; medians over 10 reps (5 for `Workflow`); prompts start with a per-rep nonce so
reps do not share a prefix cache beyond the system prompt.

| tool / case | Qwen3.8-27B (thinking): median s | model share | Qwen3-8B (no thinking): median s | model share |
|---|---:|---:|---:|---:|
| `task`: summarise a document (read_file) | 18.8 | 100.0 % | 3.1 | 99.7 % |
| `task`: multi-file search (glob + grep) | 65.5 | 99.9 % | 9.9 | 99.4 % |
| `task`: implement a spec, iterate on its unittest | 57.8 (tests pass 10/10) | 99.3 % | 12.4 (7/10) | 95.1 % |
| `Workflow` review-changes (4 audits + verifiers) | 21.5 (4 of 5 completed) | 99.9 % | 4.0 (5/5) | 99.3 % |
| `compact` summary of a 26k / 53k / 86k-char history | 77.7 / 79.7 / 81.7 | 100 % | 4.8 / 5.9 / 8.0 | 100 % |

These spans are model time: the subagent's or workflow agents' own tool calls add 0.01–1.5 s per span,
the harness 0.01–0.03 s. Part A's dispatch-only prices (0.9 ms for `task`, 0.2 ms for the compact marker)
are four to five orders of magnitude below what the calls cost, and counting them as tool time would turn
model time into "tool time" — which is why every share in this part excludes them (the paper's tools are
external services). The summary call is decode-bound and costs about the same at every history size
(~80 s with thinking, 5–8 s without). One 27B workflow failed after 42 s ("agent({schema}) invalid
output: expected object" — a workflow agent's reply failed the schema check and its one retry), although
thinking was off for workflow agents.

### C.4.5 The whole corpus, measured the way the paper measures (T5; census_tables.md C1.1–C1.6)

1,782 non-smoke runs (1,801 with smoke), 1,042 h of wall time (of it 23.6 h simulated think time in
research/11), four models (GLM-5.3-flash and DeepSeek-v4-flash hosted, 290 runs; Qwen3.8-27B and
Qwen3-32B self-hosted on vLLM, 1,511 runs), three hosts (the old host's local disk, cluster lanes on NFS,
node-local cluster lanes). 173,380 tool calls.

- **Aggregate.** Tools took 3,204 s = **0.09 % of wall time**; 1,560 s of that is 46 `task` spans, which
  are subagent runs (their nested model calls cover 99.95 % of the span). Without `task`: 0.04 %;
  without the flagged artifacts too (60 sleep/wait commands, 20 `find /` sweeps, 5 bash calls at the
  120 s cap; 1,062 s together): **0.016 %**. On the critical path (blocked-on-tools): 0.022 %.
- **Per set** the share is at most 3.1 %, and every value above 1 % is `task` or an artifact:
  02 reuse 1.37 % (107 s of `task`; 0.12 % clean), 08 codebase 2.99 % (the lead sleeping 30–120 s for
  teammates; 0.16 % clean), s15 samples 3.10 % (420 s of `task`; 0.14 % plain). The highest clean set is
  07's DeepSeek census at 0.85 % (a fast hosted model on short runs).
- **Per run** (blocked-on-tools over wall minus think time): median 0.011 %, p90 0.10 %, p99 0.50 %,
  max 27.6 % -- the max is a research/11 session whose `find /` hit the 120 s cap; without artifacts
  the max is 1.75 % (09's optimized CODE-solo run, which iterates on unit tests).
- **Per request, Sutradhara's unit** (3,287 requests; streamed FTR where the run recorded it, else the
  upper bound):

  | set | requests | FTR share p50 / p90 / p99 |
  |---|---:|---|
  | 05 GLM (streamed) | 26 | 0.04 % / 0.43 % / 0.66 % |
  | 05 Qwen3.8-27B RTX (streamed) | 25 | 0.12 % / 0.52 % / 0.73 % |
  | 09 Qwen3.8-27B optimized (streamed) | 26 | 0.24 % / 1.12 % / 1.74 % |
  | 10 Qwen3-32B H200, no thinking (bound) | 340 | 0.05 % / 0.22 % / 0.51 % |
  | 11 Qwen3-32B H200, 1–32 users (bound) | 2,632 | 0.004 % / 0.06 % / 0.23 % |
  | all 96 streamed requests | 96 | 0.15 % / 0.65 % / 1.58 % |
  | **Sutradhara v3, Fig. 3(e)** | 6,000 | **32 % / 61 % / 85 %** |

  The gap is two to three orders of magnitude at every percentile, not only at the median. The tail
  of our per-request CDF is the requests that ran unit tests or read large files: none of the 3,287
  exceeds 1.8 % except two whose `find /` sweep over the whole filesystem took 9–10 s (02 reuse 5.1 %,
  03 interventions 2.4 %).

### C.4.6 Injected tool latency: measured share vs replay (T6, T6b)

The 05 workloads (FQA-solo, CODE-solo, FQA-team) with every executed tool call slowed inside its
`tool_execution` span by 0, 0.29 s (Sutradhara's SWE-bench mean), 1.09 s (its BFCL web-search mean) or a
lognormal with mean 6 s and CV 1 (its production mean), two reps each, `--no-timestamp` so the model has
no clock. *Predicted* replays the d0000 sessions with each run's actually injected latency D added.

**Qwen3.8-27B (thinking), 24 sessions, all completed:**

| workload | d0000 | 0.29 s | 1.09 s | ≈ 6 s | predicted at ≈ 6 s |
|---|---:|---:|---:|---:|---:|
| CODE-solo (17–20 calls) | 0.3 % | 1.6 % | 5.5 % | 14.1 % | 13.5 % |
| FQA-solo (5–10 calls) | 0.0 % | 1.1 % | 2.2 % | 10.7 % | 9.6 % |
| FQA-team, summed spans (27–38 calls) | 0.1 % | 3.0 % | 8.3 % | 30.0 % | 27.2 % |
| FQA-team, critical path (blocked-on-tools) | 0.0 % | 1.2 % | 3.0 % | 10.8 % | – |

The delay adds itself and nothing else: calls per session do not move with it (CODE-solo 17.5–20),
measured shares sit within 0–3 points of the replay, and the measured wall minus (W0 + D) stays within
±45 s, while two reps of the same cell differ by 9–166 s (median 67 s). In the team runs three teammates' tool waits overlap with
each other's model calls, so a third of the summed tool time reaches the critical path. Solved from the
d0000 sessions (T6b), the share would reach 30 % only at 4.9 s (FQA-team) to 20.5 s (FQA-solo) per tool
call, and 85 % at 65–272 s.

**Qwen3-8B (no thinking), 24 sessions, 18 capped:** the 8B loops on the long-context tasks — every
FQA-solo session re-reads the same four documents until the 10-minute cap (answer coverage 0.07), most
FQA-team sessions do the same (7 of 8), CODE-solo completes in 5 of 8 sessions with 0–2 of 3 problems
solved.
In a capped session the wall time is fixed, so injected latency displaces model rounds instead of adding
to them, and the share is the share of a time-boxed window: 16–38 % at 0.29 s, 22–63 % at 1.09 s and
35–89 % at ≈ 6 s (CODE-solo 34.7 %, FQA 84–89 %). T6b from its d0000 rounds (0.8–5.8 s of model time per
tool call, several calls per round): 30 % at 0.4–2.5 s per call, 85 % at 4.8–33 s. This is the paper's
operating point — a fast model, short rounds, several tool calls each, tools of a second or more —
reached by changing only the model.

### C.4.7 Tool-heavy sessions (T7)

Solo sessions, three reps, no injected delay; the supported tools do the heavy work themselves.

| workload | Qwen3.8-27B: correct · wall · tools / wall · FTR share | Qwen3-8B: correct · wall · tools / wall |
|---|---|---|
| HSEARCH: three counts over site-packages (grep, find) | 6/9 · 258 s · **45.3 %** · 41.8 % (grep 81.5 s per session) | 0/9 · 240 s · **93.9 %** (find 206 s per session) |
| HTEST: fix two bugs, rerun pytest, run the 517-test suite | 3/3 · 243 s · **14.6 %** · 17.1 % (pytest 33.8 s per session: one suite run + 1–2 file runs) | 0/3 · 223 s · 20.1 % (one session looped through 146 test runs to the cap) |
| HWEB: two arXiv pages with curl | 6/6 · 46 s · 1.2 % · 1.5 % | 6/6 · 7 s · 5.0 % |

When the task needs heavy commands, the harness's own tools put it in the paper's range on the thinking
model: a repository-scale search spends almost half the session in `grep`, a test-driven fix a sixth in
pytest. The 8B's 94 % is a `find` over all of site-packages repeated per question, with every answer wrong.
Fetching web pages is not slow here (72–305 ms per curl call from the compute node), so the web lookup
stays at 1–5 %. "Under 1 %" is a property of file Q&A and small coding tasks, not of the harness.

### C.4.8 Reconciliation grid (T8)

Per tool call, the share of wall time is t / (t + L + o): t the tool's latency, L the model time per
tool call (all main-loop model calls of the session divided by its tool calls), o everything else per
call (context preparation, memory work, waits). L and o are measured per workload and serving setup
from the corpus; the paper's setup is backed out of its own two reported points with o = 0
(L = t (1 − s) / s): SWE-bench 0.29 s → 30 % gives L = 0.68 s, BFCL 1.09 s → 40 % gives L = 1.64 s.

| workload | serving | L s/call | o s/call | t = ours | t = 0.29 s | t = 1.09 s | t = 6 s | t for 30 % | t for 85 % |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 4-turn codebase sessions, 1 user (11) | Qwen3-32B, no thinking, H200 | 3.84 | 0.17 | 1.2 ms → 0.03 % | 6.7 % | 21.3 % | 59.9 % | 1.7 s | 23 s |
| codebase E/M tasks (10) | Qwen3-32B, no thinking, H200 | 5.17 | 0.10 | 2.9 ms → 0.1 % | 5.2 % | 17.1 % | 53.2 % | 2.3 s | 30 s |
| CODE-solo (05 A) | GLM-5.3-flash, hosted | 8.73 | 0.72 | 42 ms → 0.4 % | 3.0 % | 10.3 % | 38.8 % | 4.1 s | 54 s |
| FQA-solo (05 A) | GLM-5.3-flash, hosted | 12.15 | 3.83 | 14 ms → 0.1 % | 1.8 % | 6.4 % | 27.3 % | 6.9 s | 91 s |
| CODE-solo (05 B) | Qwen3.8-27B, thinking, RTX PRO 6000 | 19.70 | 0.51 | 131 ms → 0.6 % | 1.4 % | 5.1 % | 22.9 % | 8.7 s | 115 s |
| FQA-solo (05 B) | Qwen3.8-27B, thinking, RTX PRO 6000 | 17.93 | 2.40 | 10 ms → 0.1 % | 1.4 % | 5.1 % | 22.8 % | 8.7 s | 115 s |
| CODE-solo (C4a d0000) | Qwen3.8-27B, thinking, RTX PRO 6000 | 22.65 | 0.14 | 73 ms → 0.3 % | 1.3 % | 4.6 % | 20.8 % | 9.7 s | 129 s |
| FQA-team (C4a d0000) | Qwen3.8-27B, thinking, RTX PRO 6000 | 19.30 | 0.00 | 8 ms → 0.0 % | 1.5 % | 5.3 % | 23.7 % | 4.9 s | 65 s |
| HSEARCH (C4b) | Qwen3.8-27B, thinking, RTX PRO 6000 | 17.73 | 0.28 | 14.2 s → 44.1 % | – | – | – | – | – |
| HTEST (C4b) | Qwen3.8-27B, thinking, RTX PRO 6000 | 22.18 | 0.25 | 3.6 s → 13.9 % | – | – | – | – | – |
| CODE-solo (C4a d0000) | Qwen3-8B, no thinking, RTX PRO 6000 | 5.78 | 0.05 | 44 ms → 0.8 % | 4.7 % | 15.8 % | 50.7 % | 2.5 s | 33 s |
| FQA-team (C4a d0000, capped loops) | Qwen3-8B, no thinking, RTX PRO 6000 | 0.84 | 0.01 | 2 ms → 0.3 % | 25.6 % | 56.3 % | 87.7 % | 0.36 s | 4.8 s |
| paper, SWE-bench trace | Qwen3-14B / A100 (backed out) | 0.68 | 0 | – | 30.0 % | 61.7 % | 89.9 % | 0.29 s | 3.9 s |
| paper, BFCL web search | Qwen3-14B / A100 (backed out) | 1.64 | 0 | – | 15.1 % | 40.0 % | 78.6 % | 0.70 s | 9.3 s |

(MATH-solo rows, with one or two tool calls per session, are in T8: L = 48–134 s per call.) Two
multipliers separate the two regimes, and neither is the harness's orchestration: **tools 100–1,000×
faster** (local files and processes vs web search, email and SaaS APIs), and **3–30× more model time
per tool call** (long contexts, thinking, one tool per round; the paper's requests fan out to two tool
calls per iteration and run a 14B model at batch one). The C4 rows close the loop from both sides: the
thinking 27B on this card spends 18–23 s of model time per tool call and needs 5–10 s tool calls for 30 %;
the non-thinking 8B spends 0.8–5.8 s — the paper's range — and its sessions reach 16–88 % at the paper's
tool latencies; and heavy local work (14 s per search call) makes the 27B's own tools 44 % of a session
with no injection at all. (The C4b rows have no "t =" columns: their tools are the workload.)

## C.5 What this means for the harness

**Part A's conclusion stands for the workloads it was drawn from, and now covers every tool and every
path.** Tool execution is not a latency lever for this harness on file-and-shell work: the average call (12.4 ms) costs less than one decoded token (17 ms on
GLM, 39–43 ms on Qwen3.8-27B), and the dearest supported-tool calls (a full test suite, a
repository-wide grep) are the work the task asks for, not overhead. Optimisations that overlap tool
execution with the next model call — the paper's contribution — have 0.004–0.3 % of FTR to hide in a
median request here and under 2 % in nearly all; the model calls that research/05 and 09 attack are
93–99 % of every round.

**Three harness costs that are not the tools' work are worth fixing anyway**, because they grow with the
deployment, not with the task:
- the task-board scan on every file tool (`assignment_cwd` → `_owner_in_progress` loads every task
  file): 2.35 ms per task on NFS, so a long-lived board of a few hundred tasks costs every read and
  every bash call a quarter of a second or more — cache the owner's in-progress task, or index the board;
- the tracer's full-output redaction and hashing (§C.4.3): ~0.11 ms per KB of result, twice in
  full-output mode — hash and redact the preview only, or stream large results to disk;
- NFS for lanes and task boards: 2–20× for every metadata-bound tool; the node-local lanes of research/10
  and 11 already avoid it.

**When the paper's regime applies to this harness — measured, not assumed.** Two changes each put it in
the paper's range: heavy work for its own tools (a repository-wide grep: 45 % of a session on the thinking
27B; a test suite: 15 %), and a fast model facing seconds-long tools (Qwen3-8B at 1–6 s per call: 22–89 %;
the thinking 27B only reaches 11–14 % at 6 s). That is where tool-aware serving (overlapping tool
execution with prefill, streaming dispatch, KV time-to-live across tool pauses, research/related_work
Continuum) starts to pay. For the file-and-shell work of this repository's workloads, served by thinking
or hosted models, it does not.

## C.6 Threats to validity

- **One card type, one cell per GPU.** All six end-to-end cells ran on RTX PRO 6000s (alphagpu51, the
  last 8B heavy sessions on alphagpu54), each vLLM server alone on its GPU, as in 05 Part B; the d0000 cells are a second sample
  of 05 Part B's setup on the current harness (async memory extraction, re-read guard), not a replication
  of its runs. Faster serving would raise the shares (the direction that favours the paper).
- **The 8B arm is a degenerate agent on long contexts.** Qwen3-8B loops on FQA (and on one HTEST run),
  so 18 of its 24 delay sessions and 1 of its 9 heavy sessions end at the 10-minute cap; their shares are
  shares of a time-boxed window, and its heavy-workload answers are wrong. The arm still prices what the
  paper's operating point does to this harness (short rounds, several calls each), but not a working
  agent. Two earlier, uncapped 8B sessions (69 and 62 minutes of looping, before `--hard-deadline`
  existed) are kept in `aborted_uncapped/`.
- **Two reps per delay cell.** Two reps of the same 27B cell differ by 9–166 s of wall time (median
  67 s) because the model takes different paths; the replay check works per cell, and the 27B's measured
  shares sit within 0–3 points of it, but single cells should not be over-read.
- **One node for both sweep runs.** r1 and r2 were meant for different nodes and both landed on
  alphagpu08 (32 CPUs allotted, load average ~25 from other users' jobs). They agree (median case ratio
  1.00 on NFS, 1.03 locally; 80 % of cases within 0.96–1.06 on NFS, 0.82–1.38 locally), but
  node-to-node variation is not sampled; millisecond-scale medians on the local lane carry that noise.
- **"Cold" means cold at the NFS client.** `posix_fadvise(DONTNEED)` drops the client's cached pages of
  the file; the NFS server's cache and the attribute cache stay warm. The site-packages greps ran warm
  and cold in the same session and ranged 8–75 s across the day (42 s median in the sweep).
- **Board sizes of 100 and 1,000 are synthetic.** They price the O(n) scan; the workload runs keep 3–4 tasks
  (05, 07), and the 28-teammate stress run of the s15 samples created 57.
- **The injected latency is a sleep.** It costs no client CPU and returns the tool's normal result;
  with `--no-timestamp` the model has no clock, so its behaviour is independent of the delay (which
  is what makes the replay prediction checkable). Remote tools with content that depends on time, or
  failure modes that depend on latency, are outside this design.
- **The heavy workloads are ours.** HTEST mirrors a SWE-bench test loop and HSEARCH a repository-scale
  search, but neither is drawn from a benchmark; they bound what the supported tools can cost in a real
  session, they do not estimate a population.
- **FTR for unstreamed traces is a bound.** The corpus's per-request share uses the final call's request
  start where no stream timing exists, which overstates the share (conservative towards the paper).
- **Every span includes the tracer.** All tool costs in Parts A and C include the default tracer's
  redaction and hashing (§C.4.3); the tracing-off condition prices the harness without it.
- **The 8B arm runs with a 40,960-token window** and a 134,000-character compaction limit (as in 10/11):
  long sessions compact earlier, which changes rounds, not per-call tool cost.

## C.7 Reproduction

All commands from the repository root with `~/.venv/bin/python` (pytest 9.1.1 was added to that venv
on 2026-09-29 for the lesson suite). CPU work goes through `tool_share_cpu.sbatch`, because the login
node caps a user at 2 cores / 8 GB.

```bash
# C1 census of every trace in the repository (~1 min on 30 CPUs)
sbatch --export=NONE research/06_tool_cost/tool_share_cpu.sbatch census

# C2 controlled sweep: six host conditions per run, two runs on (possibly) different nodes
J=$(sbatch --parsable --export=NONE research/06_tool_cost/tool_share_cpu.sbatch sweep r1)
sbatch --export=NONE --dependency=afterany:$J research/06_tool_cost/tool_share_cpu.sbatch sweep r2

# C3 + C4: one cell per 1-GPU job (routed to the RTX PRO 6000 pool, as these ran); resubmit to resume --
# finished cells and scored sessions are skipped, a cell lock keeps two jobs off the same cell
for c in q8-model q8-heavy q27-heavy q8-delay q27-model q27-delay; do
  sbatch --export=NONE --gres=gpu:1 --constraint=rtx6000 --cpus-per-task=24 --mem=160G --time=10:00:00 \
         research/06_tool_cost/tool_share_gpu.sbatch $c -
done
# (or two cells per 2-GPU H100/H200 job: sbatch --export=NONE research/06_tool_cost/tool_share_gpu.sbatch q27-delay q8-heavy)

# C5 tables
sbatch --export=NONE research/06_tool_cost/tool_share_cpu.sbatch tables

# unit tests of the definitions and of --tool-delay
~/.venv/bin/python -m pytest -q research/06_tool_cost/test_tool_census.py
```

A plumbing smoke test of the GPU pipeline needs no GPU: `FAKE_SERVER=<a scripted /v1/messages server>
DATA_ROOT=<scratch> LOCAL_ROOT=<scratch> DELAY_REPS=r1 HEAVY_REPS=r1 MODEL_PROBE_ARGS="--task-reps 1
--workflow-reps 1 --compact-reps 1" bash research/06_tool_cost/tool_share_pipeline.sh q27-model q8-delay`
(how every step of this part was checked before the GPU jobs were submitted).

The intervention on its own: `profile_run.py --tool-delay 'TOOLS=fixed:S|lognormal:MEAN:CV[;...]'
[--tool-delay-seed N]` (use with `--no-timestamp`); through the 05 driver as
`--driver-arg=--tool-delay=*=fixed:1.09`. `--hard-deadline` makes `--max-seconds` end a turn that never
returns (the pipeline passes it to every C4 session; Qwen3-8B sessions get `--max-seconds 600`, the
others 3600).

# Part D — Do tools take 30–85 % of agent latency? Sutradhara's claim, its reception, and the independent evidence

Background research of 2026-09-29, done alongside Part C. Every quote below was fetched from the
primary source (arXiv HTML/PDF of each version, the project pages) and checked word for word against
the fetched text; the four items that could not be fetched are marked as such. The source copies used
for the check are not committed (they are third-party documents); every row names its URL.

## D.0 Short answer

**No one outside the author team has replicated the 30–85 %, and the two published comments on it
question its basis; the independent measurements agree with its direction only in the setting it was
drawn from (slow remote tools, fast open models) and disagree for coding agents on hosted models.**

- **What the number is.** A *per-request share of FTR* — the time to the first token of the final
  answer, which excludes that answer's decode — over one workload, quoted from the median to the 99th
  percentile of its CDF: 32 % median, 61 % p90, 85 % p99 (v3 §3.3, Fig. 3(e)). It is not a range over
  workloads, and it is not end-to-end latency (the paper's own baseline has FTR 37.45 s against E2E
  61.57 s, Table 2 of v2/v3), although v3's conclusion restates it as "end-to-end response time".
- **Where it comes from.** 6,000 *synthetic* requests on a proprietary enterprise platform whose 20+
  tools are remote services (web search, enterprise chat, email, file search, code execution, knowledge
  bases, SaaS; mean call about 6 s). The v1 version of the figure (27 / 68 / 83 %) was a replay of 60 of
  these requests on Qwen3-14B / one A100 with tool latencies *simulated by "proportional scaling"* of the
  production tool-to-LLM ratio. v2/v3 drop that subsection and the word "synthetic" from the abstract,
  present 32 / 61 / 85 % as findings "on the production workload" without a sample size, and still say in
  §5.1 that tool latencies are normalised "as described in §3" — a reference that now points nowhere.
  The public code emulates tools with `asyncio.sleep`.
- **The paper's own measurements are lower.** In its evaluation replay tools are **12.9 %** of FTR on
  Qwen3-14B and 9.0 % on Gemma-3-27B (§5.3); on open traces, BFCL web search (1.09 s per call) is "about
  40 % of E2E request time" and SWE-bench (0.29 s per call) "roughly 30 % of overall execution time"
  (§5.1).
- **Reception.** No peer review exists (no OpenReview record, no venue); alphaXiv shows 0 comments; the
  only review is a machine review (pith.science, grok-4.3) that repeats the number. Two papers comment
  on it: the GitHub Copilot production study (Microsoft Azure Research, 13M sessions, tools 4.7 % of
  wall-clock) groups it with designs "motivated and evaluated using synthetic benchmarks", and the vLLM
  Semantic Router vision paper notes that "Sutradhara's headline latencies come from synthetic request
  generation rather than unstructured customer logs". At least six papers cite it (Google Scholar: 6); none
  replicates the figure.
- **Independent evidence** splits exactly along the paper's two favourable conditions. Slow remote or
  CPU-heavy tools with fast open models, mostly one request at a time: 13–62 % in most studies (PASTE,
  SPORK, Cost of Dynamic Reasoning, Aries, Not All AI Agents Are Equal), up to 88–91 % for exact
  nearest-neighbour retrieval on the CPU (CPU-centric agentic AI) and over 70 % for a GUI sandbox
  (AgentSysBench). Production coding agents on hosted frontier models: 2–5 % of session wall-clock
  (Copilot, TraceLab), and 71–98 % LLM time for a Claude Code harness on open thinking models
  (UIUC/Intel).
- **This harness** has no remote tool; Part C prices every one of its 32 tools at 0.2 ms to 42 s per
  call (average 12.4 ms) and finds tools at 0.09 % of 1,042 h of traced runs (≤ 1.8 % of FTR for all but
  two requests). It reaches the paper's range when the work or the model changes: 15–45 % of a session
  with heavy tool work on Qwen3.8-27B, 35–89 % with the paper's tool latencies on a fast Qwen3-8B
  (§C.4.6–C.4.8).

## D.1 The claim, version by version

| item | v1 (2026-01-19) | v3 (2026-04-22; v2 of 04-17 nearly identical) |
|---|---|---|
| abstract | "Through analysis of **synthetic** requests at production scale, … tool calls account for **30-80%** of FTR latency" | "Through analysis of requests at production scale, … tool calls account for **30-85%** of FTR latency" |
| basis of the CDF | "replaying a subset of 60 requests from this trace on Qwen3-14B on a A100 GPU" (§3.5); tool times from "a proportional scaling approach" (§3.2.1) | "We present our key findings on the production workload" (§3.3); §3.2.1 removed; no sample size |
| median / p90 / p99 | 27 % / 68 % / 83 % (Fig. 4a) | 32 % / 61 % / 85 % (Fig. 3(e)) |
| still says synthetic | §1, §3.1 | §1 ("using synthetic traces from production workloads"), §3.1 ("synthetic user profiles and data") |
| evaluation normalisation | — | "Tool-call latencies are normalized according to the observed tool-to-LLM ratio within the original traces as described in §3" (§5.1; §3 no longer describes it) |

The p90 fell while the median and the p99 rose between versions, so these are two different
measurements, not a refinement of one. Read from v1's figure, the CDF moves in steps that fit about 60
points, which makes its "p99" the largest single request.

**Workload (v3 §3.1–3.2).** "We analyze workloads issued on this platform with synthetic user profiles
and data, instead of the real customer-facing queries as its prompt tokens are eyes-off for privacy
reasons"; 6,000 requests for "document retrieval, summarization, and information search"; a median of
about 2 LLM iterations (max 7), a median fan-out of 2 tool calls per iteration (max 21), prompts of about
20K tokens. "We also validate all the observations gained from synthetic workloads against
customer-facing production workload" — without numbers. The only absolute production figure is the
mean tool call, about 6 s, used to set Continuum's TTL (§5.1, §6).

**Metric (v3 §3.1.1).** "FTR is defined as the elapsed time from user request submission to the
rendering of the first token of the final user-visible response." Figure 1's legend calls the tool
component "Critical Path Tool Time" (presumably parallel calls of one iteration counted once; the text does
not define it). How tool time was obtained is described only in v1 (§3.2.1): "Since we lack access to production tool implementations, we simulate tool execution
latencies using a proportional scaling approach."

**The paper's own lower numbers (v3).** §5.3: "This increased compute time reduces the tool fraction of
the overall FTR from 12.9% down to 9.0%" (Qwen3-14B → Gemma-3-27B, whose prefill is 1.34× and decode
1.60× slower — the share falls as the LLM slows down). §5.1: BFCL v4 web search "averaging 1.09 s per
call (variance 1.7 s) and accounting for approximately 40% of E2E request time"; SWE-bench "(0.29 s,
variance 1.14 s) and contribute roughly 30% of overall execution time".

**The "conventional assumption" it overturns.** Finding 1 says the tail "contradicts the conventional
assumption that tool calls are lightweight I/O operations contributing marginally to end-to-end latency"
and cites Autellix and Continuum. Neither says so: Autellix sets tool time aside ("Since component (3)
is unrelated to LLM serving…"), and Continuum says "many tool calls have much shorter durations than human
response" and designs for their variance ("the tool call times have a varying distribution, but many are
short").

**Headline results, as printed.** "up to 77% higher load" at the same p50 FTR or "up to a 15% reduction
in p50 FTR latency" (§5.2) — while App. A.1 says "This directly confirms the 25% higher serving capacity
reported in §5.2"; "reducing end-to-end latency by up-to 11%" (abstract) — while §5.2 says "up to a 9%
reduction in p90 E2E latency" (the 11 % matches Table 2's cumulative +10.8 % and App. A.2's "6–11%").

## D.2 Reception

| venue | what exists | status |
|---|---|---|
| peer review | no OpenReview record (API search: 0 notes), no venue on arXiv or at Microsoft Research ("unpublished preprint", still showing the v1 abstract) | verified |
| alphaXiv | page, 0 comments, 5 votes, 178 views; an AI overview repeating 30–85 % | verified |
| Hugging Face papers | no page ("Paper not found") | verified |
| pith.science | machine review "T0 … reviewed 2026-05-16 · grok-4.3", "No signed human review yet"; desk editor: "The production trace analysis that surfaces the 30-85% tool-call share of FTR latency … grounds the work in actual usage rather than synthetic cases"; its "author's rebuttal" is simulated (it names a vLLM version the paper does not use) | verified |
| Hacker News | 0 hits (Algolia) | verified |
| Reddit, X/Twitter | login walls; web searches found no posts | not reachable |
| citations | Google Scholar "Cited by 6"; Semantic Scholar 5 (misses Copilot; count read through a summarising fetch after repeated HTTP 429); each citing paper checked | verified / partial |
| code | microsoft/sutradhara (created 2026-06-24, 0 stars): releases the BFCL trace only; its tool simulator does `await asyncio.sleep(execution_time_ms / 1000.0)` | verified |

What the citing papers say about the number:

- **GitHub Copilot in production** (arXiv 2608.00101; Microsoft Azure Research + UIUC; 3.2M users, 13M
  sessions): "Sutradhara (Biswas et al., 2026) co-designs agent orchestrators and inference engines for
  tool-augmented workloads. … However, existing designs are largely motivated and evaluated using
  synthetic benchmarks (e.g., SWE-bench …) or small-scale workloads" — a critique of the group, and its
  own measurement is the opposite regime: "LLM execution takes 85.4% of wall-clock time … whereas tool
  calls take only 4.7% of time".
- **vLLM Semantic Router / WRP vision paper** (arXiv 2603.21354 v2): "Sutradhara's headline latencies
  come from synthetic request generation rather than unstructured customer logs" (it cites v1's 30–80 %).
- **IdleSpec** (2605.22154) repeats "tools dominate" without numbers; **Observation, Not Prediction**
  (2606.01839), the **multi-model agentic characterisation** (2606.01725) and **Ask the Tool, Don't Guess**
  (2609.18849) cite it for its techniques, not its share. Two further citing documents (a preprints.org
  survey and an SSRN paper) returned HTTP 403.
- Practitioner write-ups repeat its techniques, not its number: a note by Arpit Bhayani (2026-08-06)
  quotes v1's "10%" E2E gain; a vendor blog (General Compute, 2026-05-07), not citing it, estimates "60%
  to 80% … sequential LLM inference" and "10% to 30% … tool execution" from experience.

## D.3 Independent measurements of the tool share

| study | agents / tools | serving | tool share | regime |
|---|---|---|---|---|
| PASTE (SJTU, MSR; 2603.18897) | gemini-cli on SWE-bench, deep research, scientific agents | Gemini-2.5 / GPT-5.2 APIs + Qwen-DeepResearch-30B on 8×A100 | v1: 60 % coding, 50 % deep research, 36 % scientific ("35% to 61%" in its intro); v3: "45%–57% of agent E2E latency", one request at a time | remote tools, large |
| SPORK (Tsinghua, Meituan; 2607.03333) | tau2-bench (2 s simulated floor), GAIA (real web search, mean 4.8 s), BrowseComp | Qwen3-32B, vLLM, H20 | 16 % / 19 % / 37 % | remote tools, moderate |
| Cost of Dynamic Reasoning (KAIST; 2506.04301, HPCA-32) | ReAct, Reflexion, LATS on HotpotQA, WebShop, MATH, HumanEval | Llama-3.1-8B, vLLM, A100 | "LLM inference and tool execution account for 69.4% and 30.2%" | small fast model, moderate |
| CPU-centric agentic AI (Georgia Tech, Intel; 2511.00739) | RAG with exact NN search, Toolformer, ChemCrow, mini-SWE-agent | B200 / RTX Pro 6000 / H200, batch 1 | v2 "up to 90.6%"; v3 "up to 88%" (SWE-agent 25–65 %) | CPU-heavy tools, large |
| Aries (NTU, AWS, Microsoft, AMD; 2607.29069) | OpenHands, Hermes Agent, OpenClaw on SWE-Bench Pro, Terminal-Bench 2, DeepResearch | Qwen3.6-35B-A3B-FP8, SGLang, H100 | "from 13% to 48%"; harness "up to 9%" | mixed |
| AgentSysBench (HKUST, Alibaba, ByteDance; 2608.15127) | 10 apps incl. GUI sandbox, mini-SWE-agent, deep research | DeepSeek-V4-Pro (SGLang) + API models | "In 5 of 10 applications, tools and environments dominate or co-dominate"; GUI sandbox > 70 % | sandboxes, large in half |
| Not All AI Agents Are Equal (Korea Univ., MSRA; 2609.19947) | RAG QA, web search, summarisation, coding | — | RAG QA 62 % (local retrieval), web search 49 % (web API), summarisation 10.5 %, coding 39 % (local bash) | per workload |
| What Limits Agentic Systems Efficiency? (2510.16276) | web agents | — | web environment "as much as 53.7%" | remote tools |
| AssetOps dialogs (Columbia; 2605.24953) | industrial agent, 16 dialogs | — | 47.3 % of wall time | small sample |
| Agentic workload characterisation (UIUC, Gimlet Labs, Intel; 2605.26297, IISWC 2026) | Claude Code harness on GAIA, SWE-bench Pro, Terminal-Bench, DABStep, ADE-Bench | Gemma 4-31B / Qwen 3.6-27B (thinking and instant), vLLM, H100 | "LLM inference accounting for 71–98%"; tools 2–29 % (GAIA 28.7 %) | LLM-dominated |
| GitHub Copilot (Microsoft Azure Research; 2608.00101) | 13M production sessions (get_file, run_command…) | 27 hosted models | tools 4.7 % of wall-clock (LLM 85.4 %); multi-turn medians 2 % vs 13.7 % (user idle 80.1 %); tool batches < 500 ms are > 99 % hidden behind LLM calls | production, small |
| TraceLab (UW; 2606.30560) | ~4,300 of the authors' Claude Code / Codex sessions | hosted | session wall-clock: tools 4.8 %, LLM 3.3 %, human 92.3 %; within requests "tools account for 59.8% versus 41.0%"; site: calls > 1 min are 3.4 % of calls, 83.6 % of tool time | production, small overall |
| RollArt (HKUST, Alibaba; 2512.22560) | agentic RL rollouts, SWE-bench, Docker | Qwen3-8B, 32×H800 | env. initialisation 15 %; "env.reset alone consumes 78%" under failures | RL training, not serving |
| Continuum (UC Berkeley; 2511.02230) | mini-swe-agent SWE-bench, BFCL v4 (GPT-5 traces) | — | tool time mean 925 ms (sd 3,550) and 1,923 ms (sd 2,133); no share | per-call only |
| INFERCEPT (UCSD; ICML 2024) | math, QA, virtual env., chatbot | GPT-J / Vicuna / Llama-3-70B, A100 | interception (mean, variance): 9e-5 s, 0.69 s, 0.09 s, 28.6 s (estimated) | per-call only |
| Conveyor (Duke; 2406.00059) | code, search, planning, database, calculator | Mistral-7B, RTX 3090 | database / calculator tool time "already very small compared to the decoding latency" | local tools, negligible |
| AsyncLM (Yale; 2412.07017) | BFCL functions | Llama-3.2 1B/3B, GPT-4o | functions "30 ms to 500 ms, with an average of 110 ms" | per-call only |
| AOSpec (Imperial; 2608.00881) | Terminal-Bench computer-use agents | vLLM | "As decoding accelerates, tool execution becomes a growing bottleneck"; 17 % of calls ≥ 1 s are 97 % of tool time | trend |

Two regularities run through all of them. The share is a property of the *pair* (tool latency, LLM
speed): it rises with remote or CPU-heavy tools (web search 1–5 s, retrieval, sandboxes) and with fast
models at batch 1, and falls with slow hosted or thinking models and with human idle time in the
denominator (AOSpec and Sutradhara's own Gemma result show it rising as decode accelerates). And tool
time is concentrated in a few long calls (AOSpec 17 % → 97 %, MORI 13 % → 58 %, TraceLab 3.4 % →
83.6 %), which is why tail percentiles of a per-request share can be large while the aggregate is small.

## D.4 Verdict, and where this harness sits

**Do online reviewers support the claim? No — there is no reviewer support to find.** The paper has no
peer review, no public discussion (0 comments on alphaXiv, nothing on OpenReview, Hacker News, Hugging
Face; Reddit and X unreachable but nothing surfaced by search), and one machine review that repeats it.
The two papers that comment on its evidence both point at the same weakness, its synthetic requests;
of the other four citing papers, one repeats that tools dominate (without numbers) and three cite its
techniques. No one has replicated or rebutted the 30–85 %.

**Is the claim supported by independent evidence? Only in its own regime.** Agents whose tools are
remote services or heavy local computation, served by a fast open model one request at a time, spend
13–62 % of their time in tools in five independent studies, and more with CPU-bound retrieval (up to
88–91 %) or a GUI sandbox (over 70 %) — the paper's number is plausible *there*,
although its own evaluation replay measured 12.9 %, and its headline figure rests on rescaled rather
than measured tool latencies whose provenance was dropped between v1 and v3. Coding agents on hosted
models in production spend 2–5 % of wall-clock time in tools (Copilot over 13M sessions, TraceLab), and
a Claude Code harness on open thinking models 71–98 % in the LLM.

**This harness reproduces both ends of that range, for measurable reasons** (Part C). On the workloads
this repository runs its tools are local (the average call 12.4 ms; tools 0.09 % of 1,042 h of runs;
per-request FTR share ≤ 1.8 % for every request but two `find /` sweeps) and its model calls are long
(3.8–48 s of model time per tool call). Give it heavy work for those tools — a repository-wide grep, a
test suite — and tools take 15–45 % of a session on Qwen3.8-27B; give it a fast non-thinking model
(Qwen3-8B, 0.8–5.8 s per tool call, the paper's implied 0.7–1.6 s) and the paper's tool latencies
(0.29–6 s) and they take 16–89 %. The two results are consistent: the share follows the ratio of per-call
tool time to per-call model time, and the paper measured where that ratio is large.

**How to cite the 30–85 %.** As "the median-to-p99 share of FTR for synthetic enterprise requests whose
remote tools average ~6 s" — v1's version of the figure (30–80 %) came from 60 requests replayed on
Qwen3-14B / A100 with rescaled tool latencies; v3 does not state how its version was obtained — and not as
a general property of agentic workloads, nor as end-to-end latency.

## D.5 Sources

Primary: arXiv 2601.12967 v1/v2/v3 (abs and HTML); microsoft/sutradhara on GitHub; the pages and APIs
listed in D.2; every paper in D.3 at the arXiv ID given (HTML or PDF of the version cited). Checked
2026-09-29. Could not be fetched: Semantic Scholar's raw JSON (HTTP 429; count read through a summarising
fetch), preprints.org 202608.0365 and SSRN 7344668 (HTTP 403), themoonlight.io (429), X and Reddit (login
walls). The Microsoft Research page was read through a summarising fetch (direct request 403).

---

## Data inventory

All paths are under `research/06_tool_cost/`. Parts A and B's data are committed (e0e522b, 2026-09-15,
then moved here in the 2026-09-23 cleanup); Part C's rows (the last eight) are not committed yet. `git status --ignored` lists no git-ignored local files in
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
| `data/tool_census/` | Part C C1: every tool call of the 1,803 traces under `research/*/data/**` and `s15_integrated_harness/traces` (1,801 runs; the 2 Part A probe traces skipped): `calls.jsonl.xz` (173,380 calls), `runs.json` (1,801 runs), `requests.json` (3,313 requests), `census_tables.{md,json}`; `slurm-*.log` job logs. Regenerate with `tool_share_cpu.sbatch census` (the last run, job 109702, is the one committed). | `tool_census.py` | Part C §C.4.1, §C.4.5 | published |
| `data/tool_sweep/r1/`, `r2/` | Part C C2: two runs (Slurm jobs 109697 and 109698, both on alphagpu08, 2026-09-29 22:32–23:58 UTC) of the controlled sweep under six conditions each (`nfs-sum`, `loc-sum`, `loc-nfstr`, `loc-full`, `loc-off`, `nfs-full`); per condition `<run>-<cond>.records.jsonl` (3,203–3,212 records), the probe trace (none for `loc-off`), `probe.log`, `DONE`; per run `preflight_<job>.txt` (node, filesystems, outbound HTTPS, the lesson suite's own wall time) and `sweep_run.log`. | `tool_sweep_probe.py` via `tool_sweep_run.sh` | Part C §C.4.1–C.4.3 | published |
| `data/tool_e2e/<arm>-model/` | Part C C3 (arm `q27` = Qwen3.8-27B, `q8` = Qwen3-8B; Slurm 110528 and 110524, RTX PRO 6000, 2026-09-30): one `run_*.jsonl` + `run_*.records.jsonl` of the model-bearing-tool probe each (65 records: 30 `task`, 5 `Workflow`, 30 summaries; q27 64 ok + 1 failed workflow, q8 65 ok), `model_probe_<job>.log`, `pipeline/` (server meta, vLLM log, start record), `COMPLETE`. | `tool_model_probe.py` via `tool_share_pipeline.sh` | Part C §C.4.4 | published |
| `data/tool_e2e/<arm>-delay/d0000 … d6000/` | Part C C4a: 05 workloads FQA-solo, CODE-solo, FQA-team × r1, r2 at each injected tool latency, 24 sessions per arm (traces with `.inputs`/`.reads` sidecars, console logs, `.score.json`, CODE sandboxes); `delay_<job>.log`, `pipeline/`. q27: Slurm 110529, all 24 completed. q8: Slurm 111176 (10-minute cap, `--hard-deadline`), 6 completed + 18 timeout. | 05 `latency_workloads.py` + `profile_run.py --tool-delay` | Part C §C.4.6 | published |
| `data/tool_e2e/q8-delay/aborted_uncapped/`, `data/tool_e2e/q8-heavy/aborted_uncapped/` | The two Qwen3-8B sessions that looped before the cap existed (Slurm 110527 and 110525, cancelled): FQA-solo-r1 at d0290 (4,147 s, 586 rounds re-reading the same four files) and HWEB-solo-r2 (3,750 s); traces, sidecars, console logs, scores. | as above | Part C §C.4.6, §C.6 (not in the tables) | kept as evidence |
| `data/tool_e2e/<arm>-heavy/` | Part C C4b: HTEST, HSEARCH, HWEB × r1–r3 (traces, sidecars, console logs, `.score.json`, HTEST sandboxes); `heavy_<job>.log`, `pipeline/`. q27: Slurm 110526, 9 completed. q8: Slurm 110525 (r1–r2, cancelled while HWEB-r2 looped) + 111178 (the rest, 10-minute cap), 8 completed + 1 timeout. | `tool_heavy_workloads.py` | Part C §C.4.7 | published |
| `data/tool_e2e/jobs/` | Slurm logs and the pipeline's per-job logs of the GPU jobs 110524–110529, 111176, 111178 (the 2-GPU H100/H200 jobs 110001–110003 were cancelled before they started and left no files). | `tool_share_gpu.sbatch` | Part C §C.7 | published |
| `data/tool_share/tool_share_tables.{md,json}` | Part C T0–T8. | `tool_share_analyze.py` | Part C §C.4 | published |
| `../common/fixtures/tool_heavy/` | Frozen inputs of Part C: `task_system/` (the s10 lesson with two injected bugs and its test file, HTEST) and `review_changes.diff` (the diff the Workflow cases review). | written for Part C | Part C §C.2 | published |

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
| (none: written here, 2026-09-29 → 30) | Part C — the tool-call census, controlled sweep, model-bearing tools, injected latency and heavy workloads | Part C | new |
| (none: written here, 2026-09-29) | Part D — Sutradhara's claim, its reception and the independent evidence | Part D (pointer in `research/related_work/README.md`) | new |

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

- **Part A §6 (Reproduction) — flagged** [cleanup note 2026-09-29]: its commands name the old host's paths
  (`/home/yq335/learn-claude-code`, `/home/yq335/lanes/toolprobe`, `/home/yq335/myenv/bin/python`), which
  do not exist on the cluster. The equivalents are `/mnt/home/yqi10/learn-claude-code`, a lane such as
  `$HOME/lanes/toolsweep-r1` (as `tool_sweep_run.sh` builds it) and `~/.venv/bin/python`. Part A's commands
  are otherwise unchanged; `tool_latency_analyze.py` still regenerates `tool_latency_tables.md`
  byte-identically (verified 2026-09-29; the `.json` differs only in the two input paths it records).
- **`tool_latency_probe.py` quirks — flagged** [cleanup note 2026-09-29]: its `--warmup` flag *drops* the
  warm-up pass (the name reads the other way round); the `rep` field of its records is the global call
  counter, not the repetition index; its docstring's `<label>.records.jsonl` is written as
  `run_*.records.jsonl`. `tool_sweep_probe.py` keeps `--warmup` for compatibility and adds `rep_index`
  and `phase` to every record and the label to the file name.
- **Part A's host is gone — flagged** [cleanup note 2026-09-29]: Part A's numbers were measured on the old
  host's local disk; Part C T2 compares them with today's NFS and node-local lanes.

- **GPU jobs 110528 and 110529 — flagged** [cleanup note 2026-10-02]: Slurm records them as FAILED (exit 2),
  but both cells finished (`finished rc=0`, `all cells done`, `COMPLETE`). The pipeline script was replaced on
  NFS from the login node while they ran (to add the 8B cap); after its last command the job's shell could not
  read the rest of its old copy ("Stale file handle"). Nothing after `all cells done` was lost. Lesson kept in
  `tool_share_pipeline.sh`'s history: do not edit a pipeline script while jobs run from it.
- **Cancelled jobs — noted** [cleanup note 2026-10-02]: 110525 (q8-heavy) and 110527 (q8-delay) were cancelled
  when their sessions looped past the 1-hour cap, which `--max-seconds` only enforced between turns; their two
  runaway sessions are in `aborted_uncapped/`, and `profile_run.py` gained `--hard-deadline`. 110001–110003
  (2-GPU H100/H200 fallbacks) were cancelled unused once the RTX nodes returned from maintenance.
