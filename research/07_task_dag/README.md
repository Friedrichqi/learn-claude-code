# 07 · The task DAG: census and reuse signal

> **Status:** complete. Part A (reuse signal) ran first; Part B (census) contradicts Part A's §2.2 claim
> that the Lead emits dependency edges only when the prompt names the mechanism — see the note there.
> **Dates:** Part A 2026-09-16/17 (runs on 2026-09-17, 01:04–08:30 UTC), Part B 2026-09-17 (runs
> 18:19–20:32 UTC); the weekly notes are dated 2026-09-23. **Models / providers:** z.ai `glm-5.3-flash`
> throughout; Part B adds DeepSeek `deepseek-v4-flash`. **Commits:** f17e2a4 (2026-09-17) holds both
> reports, both weekly notes, the scripts and the data; aded0d0 (2026-09-21) added "§0 In one page" to
> `weekly_progress/092326/dag_kv_reuse.md`. **Presented:** 2026-09-23 deck
> (`weekly_progress/092326/slides.html`): slide 3 (weekly finding 8 → Part A §5.7), slide 4 (findings 1–4
> → Part A §5.3–5.5, plus the board-shape statistics now in A.W, data in `data/dag_redundancy/`), slide 5
> (finding 8 → Part A §5.7, data in `data/prewarm/`); the per-note next steps (A.W) feed slides 13–14. No
> slide cites Part B's files.

**Contents**

- **Part A — Does the task DAG tell a serving system which bytes to keep, and is handing them over worth a
  round?** Experiment 1: does a dependency edge predict which bytes a successor reads? Experiment 2
  ("prewarm"): is handing the successor those bytes worth a round?
  - A.W Additions from `weekly_progress/092326/dag_kv_reuse.md` (with the map of its findings 1–8)
- **Part B — Does the Lead maintain a dependency graph, or is the task board just a list?** A census of
  12 workloads (6 categories × DEP/FLAT) on `glm-5.3-flash` and `deepseek-v4-flash`.
  - B.W Additions from `weekly_progress/092326/dag_census.md` (with the map of its findings 1–7)
- Data inventory · Source map · Cleanup notes

**Files in this folder** (`research/07_task_dag/`)

| path | purpose |
|---|---|
| `dag_workloads.py` | Part A: the five dependency-shaped workloads W1–W5 at instruction levels L0–L2 and the DAG-execution gate; `--probe` now writes to the sibling `data/dag_redundancy_probe/` instead of nesting under `data/dag_redundancy/` (starts harness sessions) |
| `dag_redundancy.py` | Part A experiment 1: read attribution to tasks, matched-pair contrast, prefetch-policy and pricing tables (offline) |
| `prewarm_smoke.py` | Part A provider gate: does the endpoint accept a fabricated tool_use? (calls the provider; it has no argparse, so never run it with `--help`) |
| `prewarm_loop.py` | Part A experiment 2, stage 1: scripted successor micro-loop (calls the provider) |
| `prewarm_harness.py` | Part A experiment 2, stage 2: the arms in the real team harness via `dag_workloads.py` and `profile_run.py --prewarm` (starts harness sessions) |
| `prewarm_analyze.py` | Part A experiment 2 tables, stages 1 and 2 (offline) |
| `census_workloads.py` | Part B: the twelve census workloads and the prompt-contamination gate; `--check-prompts` and `--print-prompts` make no calls, a sweep starts harness sessions |
| `dag_census.py` | Part B analyzer: board reconstruction, reference-graph matching, where-the-ordering-went measures (offline) |
| `census_score.py` | Part B: re-runs each archived sandbox's own tests in a scratch copy (offline) |
| `data/dag_redundancy/`, `data/dag_redundancy_probe/`, `data/prewarm/` | Part A: the nine experiment-1 runs, the three gate probes, and the handoff data (gate, stage 1, stage 2) |
| `data/dag_census/`, `data/dag_census_ds/`, `data/dag_census_smoke/`, `data/dag_census_partial/` | Part B: glm runs, DeepSeek runs, the two pilots, the one aborted run |

Shared, outside this folder: `research/common/profile_run.py` (the `--prewarm*` flags),
`research/common/input_redundancy.py` (read provenance for `dag_redundancy.py`), and the fixtures
`research/common/fixtures/dag_bench/` (the W3 pipeline seed, used by `dag_workloads.py`),
`research/common/fixtures/census_bench/` (the REFACTOR fixture) and `research/common/fixtures/latency_bench/`
(the CODE bench problems), the last two used by `census_workloads.py`.

---

# Part A — Does the task DAG tell a serving system which bytes to keep, and is handing them over worth a round?

_Formerly `s15_integrated_harness/dag_kv_reuse_profile.md` (2026-09-16/17)._

2026-09-16/17. Two experiments, one question each, both aimed at the two next steps the
2026-09-16 deck ends on (eviction order at the harness tier, DAG-aware retention at the engine tier).

Tooling: `research/07_task_dag/dag_workloads.py` (five dependency-shaped workloads + the DAG-execution gate),
`research/07_task_dag/dag_redundancy.py` (task attribution, matched-pair contrast, prefetch-policy table),
`research/07_task_dag/prewarm_smoke.py` (does the provider accept a fabricated tool_use?),
`research/07_task_dag/prewarm_loop.py` (stage 1 micro-loop), `research/07_task_dag/prewarm_harness.py` (stage 2 in the real
harness), `research/07_task_dag/prewarm_analyze.py` (tables), and additive `--prewarm*` flags in
`research/common/profile_run.py`. Traces: `research/07_task_dag/data/dag_redundancy/` and `research/07_task_dag/data/prewarm/`.
Provider: z.ai `glm-5.3-flash`, the same model as every earlier experiment in this series.

Terminology as in the 2026-09-09 deck: a *turn* is one lead activation, a *round* is one agent-loop
iteration. Constants carried in from `research/05_latency_breakdown/README.md` Part A: a model call costs
3.72 s + 0.033 ms per uncached prompt token + 17.44 ms per output token.

## 1. Questions

1. **When to reuse.** Given a predecessor/successor pair on the lead's task board, how likely is the
   successor to read content the predecessor already read — and is that likelihood *higher than for
   an unrelated pair in the same run*? If it is not, the dependency graph carries no prefetch signal
   and a serving system gains nothing by being shown it.
2. **Is the graph the predictor, or the text?** The successor's task description often names the
   same file. A serving system sees only the graph.
3. **How to reuse, and what it is worth.** If a successor is handed the predecessor's file content
   instead of opening the file itself, how many agent-loop rounds disappear, what does that cost in
   prefill, and does the answer survive a negative control?

## 2. What had to be fixed before anything could be measured

Three harness facts decided the design, and all three were found by running, not by reading.

**2.1 The existing corpus cannot answer question 1.** `blockedBy` is empty in all 31 team runs that
carry byte-level `.reads.jsonl` data (`research/05_latency_breakdown/data/latency_profiling/` 15, `research/02_input_redundancy/data/redundancy_profiling/`
8, `research/02_input_redundancy/data/redundancy_profiling_ctx512k/` 8). Exactly one run in the whole tree has a lead-authored
DAG — `s15_integrated_harness/traces/run_20260902T004901_584576Z_dc6a9685.jsonl`, 57 tasks and 96 edges — and it was
recorded in `summary` output mode against another machine's paths. Every workload written for this
series so far was deliberately flat and parallel, which is why the question had never come up.

**2.2 The lead does not emit edges unless the prompt names the mechanism.** Three instruction
strengths were probed (`research/07_task_dag/dag_workloads.py` `LEVELS`): L0 narrative order only, L1 an explicit
ordering constraint, L2 the ordering constraint plus "create all task nodes first, then call
update_task with addBlockedBy". Only L2 produced `task_update` events with a non-empty
`blocked_by_task_ids`. The harness's own system prompt already carries that sentence
(`code.py:893-897`); it is not enough on its own.

[cleanup note: contradicted by Part B §4.1. In the 2026-09-17 census, prompts at this report's L0 -- narrative order only, the mechanism never named -- produced the complete reference graph in 21 of 21 DEP runs on two providers and no edge in 23 of 23 FLAT runs; Part B §4.1 gives two candidate reasons the L0 probe here saw none (the W1/W4 shapes, and a team block explaining that `spawn_teammate` refuses blocked tasks), and which one mattered is unsettled. No L0 or L1 probe trace is committed: `research/07_task_dag/data/dag_redundancy_probe/` holds only the three L2 gate probes.]

**2.3 Creating an edge and executing one are different things.** `spawn_teammate` claims the task in
the caller's thread before it starts the agent (`code.py:1515-1521`), and `claim_task` refuses a task
whose blockers are unfinished (`code.py:454-455`). So `spawn_teammate(task_id=<blocked task>)` fails
outright: in the first probe the lead spawned a teammate for the blocked synthesis item, the spawn
was refused, and that item was never claimed by anyone. A successor can only be picked up by an idle
teammate's auto-claim (`claim_next_task`, `code.py:1376`). The workloads therefore instruct the lead
to pass `task_id` only for unblocked items, to spawn exactly three teammates whatever the number of
items, to have each teammate call `complete_task` as soon as its findings are written, and never to
shut a teammate down while the board still has pending work. **Executed-edge yield** — both endpoints
claimed, predecessor completed before the successor was claimed — is reported for every run and
gates the analysis.

## 3. Experiment 1 — the DAG against byte-level redundancy

### 3.1 Workloads

Five shapes, all at L2, all over small files (`trace_runtime.py` 638 lines, `trace_view.py` 647
lines, `GLOSSARY.md` 256 lines) because an audit of the 3,771-line `code.py` does not finish inside a
deadline — the first two probes both timed out with the last item unclaimed.

| id | shape | tasks / edges | what it is for |
|----|-------|---------------|----------------|
| W1 CHAIN | 2 subsystems x (inventory -> risk review) + 2 standalone audits | 6 / 2 | edge pairs and non-edge pairs **on the same file**, in the same run |
| W2 FANIN | 3 audits of one file + a synthesis blocked by all three + 1 standalone | 5 / 3 | the join case: does a synthesis re-read its inputs? |
| W3 PIPELINE | each stage's declared input is the predecessor's **output** + 1 standalone on the stage-1 input | 4 / 2 | control: edges whose corpora are disjoint by construction |
| W4 PLACEBO | W1's text, but every board edge points at the **other** branch | 6 / 2 | the direct test of question 2: does overlap follow the edge or the words? |
| W5 FANIN-DISJOINT | 3 inventories of **three different files** + a synthesis blocked by all three + 1 standalone | 5 / 3 | added after W2 ran: there, all three predecessors read the same file, so whichever auditor took the synthesis already held it and a handoff had nothing to add. Here the successor holds one file of three |

The standalone items exist because of the single most dangerous confound here: if every edge pair is
also the only same-file pair, then "is an edge" and "is about the same file" are the same variable
and nothing is measurable. Each run must contain all four cells of (edge) x (same corpus).

A per-run nonce is stamped into every item so that two runs of the same workload cannot serve each
other's teammate prefix out of the provider's prefix cache.

### 3.2 Attributing reads to tasks

No trace event carries a task id on a tool call. Reads are attributed through per-owner ownership
intervals: `task_claim` opens `(owner, task_id, t)`, `task_complete` (else `agent_end`, else run end)
closes it, `agent_create.data.name` maps the owner back to the `agent_id` the tool events carry, and
a read belongs to the interval of its own agent that contains its timestamp. Bytes and provenance
come from `input_redundancy.Run`, so reads issued through read-only `bash` are counted as well as
`read_file` — teammates *can* run `cat`/`grep` off the main thread (`code.py:2016-2019`, which
corrects `research/02_input_redundancy/README.md` Part A (was `file_read_reuse_profile.md:44`)).

### 3.3 The contrast

Raw overlap rates are not evidence: every earlier workload in this series produced exactly the
redundancy its task cut implied. The headline is therefore a **matched contrast**: for each edge
(p, s), a control (p', s) with the *same successor*, p' non-adjacent, finished before s was claimed,
closest in gap. Holding the successor fixed cancels its read volume, its corpus and its position in
the run. Significance is a run-clustered sign-flip randomisation (pairs inside a run are not
independent), with a cluster bootstrap for the interval and an exact sign test over per-run means.

A validity block is printed above every number: within-run variance of the non-adjacent pairs (zero
means the workload already fixed the answer), the non-adjacent base rate (a ceiling/floor flag),
executed-edge yield, the same-owner share of edges (a successor handled by the agent that already
holds the bytes is not a test of transfer), resolvable-provenance share, unattributed reads, and the
rank correlations of edge with description similarity and with time gap.

## 4. Experiment 2 — handing the successor the bytes

### 4.1 The emulation, and why it is faithful

The provider cannot transfer KV, so the successor is handed the same **tokens** instead: the exact
assistant `tool_use` / user `tool_result` pair a real `read_file` would have produced, prepended to
its history after its opening user turn, rebuilt byte-identically on every call so the injected block
sits in a stable prefix. Content is produced by the harness's own `run_read`, so the injected bytes
are what a real read would have returned, truncation marker included.

`research/07_task_dag/prewarm_smoke.py` checked first that the endpoint accepts this at all — five shapes, one
call each. All five were accepted, and in every one the model answered from the injected content
without calling a tool: provider-shaped id, a distinctive `prewarm_*` id, a text block before the
tool_use, a tool name absent from the `tools` array, and the plain-user-message control. The
distinctive id is used, so injected bytes stay separable in `.inputs.jsonl` and `.reads.jsonl`.

Injection lives in the driver (`profile_run.py` `profiled_create`, immediately after
`trace.capture_context()`), gated on `agent_kind == "teammate"`, never in `code.py`: the harness is
the artefact under measurement and every other experiment in this series depends on it.

### 4.2 Arms

| arm | what is injected | role |
|---|---|---|
| baseline | nothing | the experiment-1 runs, reused |
| dag | the direct predecessors' reads, captured **live in the same run** | the deployable policy |
| oracle | the successor's own eventual read set, frozen from a matched baseline run | upper bound |
| pollute | an equal-size window of an unrelated file | negative control |
| summary | the predecessors' result text instead of the bytes | what a harness can already do with no KV machinery |

Stage 1 is a scripted single agent shaped like a successor — asked a question whose answer lives in
files it is not told the path of — so round counts get many more repetitions than a team run affords.
Stage 2 puts the same arms in the real harness and measures the successor task end to end.

## 5. Results

### 5.1 The three gates

**Does the provider accept a fabricated tool_use?** Yes, in all five shapes probed
(`research/07_task_dag/data/prewarm/smoke.json`), and in every one the model answered from the injected content without
calling a tool: a provider-shaped `call_<24 hex>` id, a distinctive `prewarm_0001` id, a text block
before the tool_use, a tool name absent from the `tools` array, and a plain-user-message control. The
distinctive id is used from here on, so injected bytes remain separable in both sidecars. Injected
prompts were 915-947 tokens against a 2,928-character injection, and the five calls took 4.7-6.1 s.

**Does the lead emit dependency edges?** Only at instruction level L2. The probe at L2 produced a
board with the intended chain on the first attempt (`research/07_task_dag/data/dag_redundancy_probe/`, 3 tasks, 2 edges,
successor claimed with `blocked_by_task_ids` recorded).

[cleanup note: "only at L2" does not generalise -- see the note under §2.2 and Part B §4.1.]

**Do the edges execute?** This is the gate that matters, and the first two probes failed it: the
lead passed `task_id` to `spawn_teammate` for a blocked item, the spawn was refused, and that item
was never claimed at all. After the workloads were rewritten to spawn three teammates and to pass
`task_id` only for unblocked items, executed-edge yield over the first two full runs was 4/5 = 80%,
above the 75% gate.

### 5.2 The base rate an edge has to beat

Over the 8 `redundancy_profiling_ctx512k` runs, which have byte-level read data and **no** edges at
all, an *unrelated* earlier task already covers **52.0%** of a later task's read bytes, shares a path
with it 75% of the time, and could have removed **90 of 141 (63.8%)** of its read-issuing rounds.
That is the number any dependency-aware policy has to beat, and it is why the headline below is a
matched contrast rather than a raw overlap rate.

It also has a consequence for the *strict* reading of a handoff. If "u could have supplied v" is
read as "u completed before v was claimed", then **no pair anywhere in the existing corpus
qualifies**: those runs are fully parallel, so nothing ever finishes before anything else starts. The
analyzer therefore uses the physically meaningful condition -- u had already read something when v
began reading -- and keeps the stricter one as a per-pair flag.

### 5.3 What an agent that inherits a dependency actually does

Nine runs (W1-W5 at rep 1; W1, W2, W4, W5 at rep 2), 20 edges, 19 executed, 14 successor tasks of
which 13 were claimed. Full tables in `research/07_task_dag/data/dag_redundancy/dag_tables.md`.

[cleanup note: no committed table gives 20 edges. Table A of `research/07_task_dag/data/dag_redundancy/dag_tables.md` sums to 22 edges over these nine runs, 19 of them executed; Table B counts 19 edge pairs; the runs' own `.dag.json` gate summaries also sum to 22 edges; and Part B §7's detector check (`dag_census.py` on the same folder) reports 9 runs, 22 edges.]

| group | successors | mean reads | mean bytes read | predecessor bytes | byte coverage by predecessors | removable rounds |
|---|---|---|---|---|---|---|
| successor is the same agent as a predecessor | 6 | 3.7 | 9,477 | 34,018 | 44.7% | 5/10 (50.0%) |
| successor is a different agent | 7 | 5.7 | 11,491 | 21,984 | 29.9% | 6/12 (50.0%) |

Two things to take from this. First, the split is roughly even, so the transferable case is not rare:
the very first run measured -- where the predecessor's own agent took the successor and read nothing
at all -- was not representative. Only 2 of 13 successors read nothing.

Second, and against expectation, a successor handled by a *different* agent gets **less** of its
bytes from its predecessors (29.9%) than one handled by the same agent (44.7%), despite reading more.
What a fresh agent goes and fetches is substantially not what its predecessors read.

### 5.4 The edge predicts -- but only when it agrees with the task text

Pooled over every workload the contrast is flat (edge pairs cover 31.8% of the successor's read
bytes, unrelated pairs 32.9%; removable rounds 31.6% against 31.3%). That pooled number is an
artefact and should not be quoted: **two of the five workloads are controls built to show zero.**
W3's edges span disjoint corpora by construction, and W4's board edges deliberately contradict its
own narrative. Averaging them with the aligned workloads cancels exactly the effect the design was
built to isolate.

Split by group, matched on the successor:

| group | measure | edges | mean delta | 95% CI | sign-flip p | runs positive |
|---|---|---|---|---|---|---|
| aligned boards (W1, W2, W5) | byte coverage | 13 | **+0.461** | [+0.273, +0.676] | 0.061 | 5/5 |
| aligned boards (W1, W2, W5) | removable-round share | 13 | **+0.372** | [+0.122, +0.682] | 0.124 | 4/5 |
| board contradicts the text (W4) | byte coverage | 4 | **-1.000** | [-1.000, -1.000] | 0.497 | 0/2 |
| board contradicts the text (W4) | removable-round share | 4 | **-1.000** | [-1.000, -1.000] | 0.497 | 0/2 |
| disjoint corpora (W3 control) | byte coverage | 2 | +0.000 | [+0.000, +0.000] | 1.000 | 0/1 |
| all workloads pooled | byte coverage | 19 | +0.105 | [-0.366, +0.471] | 0.675 | 5/8 |

and raw, per workload:

| workload | edges | edge byte coverage | unrelated byte coverage | edge removable rounds | unrelated removable rounds |
|---|---|---|---|---|---|
| W1 CHAIN | 1 | 100.0% | 31.8% | 100.0% | 27.5% |
| W2 FANIN | 6 | 77.1% | 69.7% | 55.6% | 66.7% |
| W3 PIPELINE (control) | 2 | 0.0% | 44.6% | 0.0% | 33.3% |
| W4 PLACEBO | 4 | 0.0% | 26.7% | 0.0% | 32.1% |
| W5 FANIN-DISJOINT | 6 | 26.9% | 6.2% | 6.7% | 5.1% |

Read together these say something sharper than either a null or a confirmation:

**When the board agrees with the task text, a dependency edge is a strong predictor** -- +46 points of
byte coverage over a matched control that finished before the same successor started, with the
bootstrap interval clear of zero and all five runs pointing the same way. (The sign-flip p of 0.061
is at the resolution floor for five clusters, where the smallest attainable p is 1/32; it is a
statement about how many runs there were, not about the size of the effect.)

**When the board contradicts the text, the edge predicts nothing at all.** W4 is W1 word for word
with the `addBlockedBy` wiring crossed between branches, and its edges cover 0.0% of their
successors' bytes while the matched controls cover everything -- a delta of exactly -1.0 in both
reps. The successors kept reading the file their *description* pointed at, not the one their
*predecessor* had read.

So the dependency graph is not the carrier of the signal; it is a shadow of the task text, which is
what actually determines what a teammate opens. A serving system that is shown only the graph -- the
proposal the 2026-09-16 deck ends on -- gets the W4 case: a structure that looks predictive and is
not. The cheaper mechanism is also the better one, because the task description is already a string
the harness has in hand.

### 5.5 Prefetch policies: broadcast beats the graph

Over the 11 successor tasks that read anything:

| policy | byte recall | round recall | precision (used/sent) | sent KB median | tokens per round removed |
|---|---|---|---|---|---|
| direct predecessors | 36.0% | 11/22 (50.0%) | 16.3% | 26.2 | 6,911 |
| most text-similar earlier task | 46.1% | 9/22 (40.9%) | 34.5% | 12.6 | 5,096 |
| all ancestors | 36.0% | 11/22 (50.0%) | 15.6% | 26.2 | 7,204 |
| **all earlier tasks** | **85.6%** | **17/22 (77.3%)** | 24.7% | 49.3 | 7,009 |
| same-owner history | 14.6% | 3/22 (13.6%) | 9.6% | 20.0 | 17,301 |
| the lead's own reads | 0.0% | 0/22 | - | 0.0 | - |
| one random earlier task | 26.8% | 3/22 (13.6%) | 21.4% | 12.6 | 14,342 |
| oracle (the successor's own reads) | 100% | 21/22 (95.5%) | 100% | 8.1 | 1,635 |

This is the pre-registered decision rule, and it fires against the hypothesis: **keeping everything
earlier recalls more than twice the bytes and half again the rounds that the direct predecessors do**
(85.6% vs 36.0%; 77.3% vs 50.0%). Restricting to cross-owner successors does not change it
(45.1% / 55.0% against 85.6% / 77.3%). A retention directive derived from the board would keep less,
and serve worse, than one that simply keeps whatever fits.

Priced with the measured constants, the reason selectivity does not pay is plain:

| policy | removable rounds | saved s (rounds x 3.72 s) | sent tokens | prefill paid s | net s |
|---|---|---|---|---|---|
| direct predecessors | 11 | 40.9 | 76,025 | 2.51 | **+38.4** |
| all earlier tasks | 17 | 63.2 | 119,151 | 3.93 | **+59.3** |
| oracle | 21 | 78.1 | 34,326 | 1.13 | **+77.0** |

Sending 119k tokens of largely unused context costs 3.9 s of prefill against 63 s of rounds removed.
**Precision is nearly free to give up; recall is the whole game.** The binding constraint on a
retention policy is memory capacity, not prediction quality -- so the engine-tier question is "how
much can I hold?", not "what should I hold?".

### 5.6 Handing the successor the bytes: stage 1, scripted agent

128 trials, 4 arms x 4 successor-shaped tasks x 8 repetitions, order shuffled with a fixed seed
(`research/07_task_dag/data/prewarm/stage1.jsonl`, tables in `research/07_task_dag/data/prewarm/prewarm_tables.md`).

| arm | n | rounds | censored | read_file calls | locate calls | correct | injected tok | prompt tok | output tok | wall s |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 32 | 7.22 (sd 2.94) | 3 | 2.31 | 8.19 | 91% | 0 | 12,998 | 1,067 | 62.4 |
| **oracle** | 32 | **2.50** (sd 2.18) | 0 | 0.59 | 1.22 | 100% | 644 | 2,037 | 316 | **18.4** |
| pollution | 32 | 7.53 (sd 2.63) | 0 | 2.47 | 6.84 | 100% | 644 | 14,172 | 772 | 52.4 |
| summary | 32 | 6.41 (sd 2.82) | 1 | 2.22 | 6.88 | 97% | 45 | 13,320 | 1,027 | 57.1 |

| arm | delta rounds vs baseline | 95% CI | delta read_file | delta correct | delta wall |
|---|---|---|---|---|---|
| **oracle** | **-4.72** | **[-5.97, -3.47]** | -1.72 | +9 pts | **-43.9 s** |
| pollution | +0.31 | [-1.03, +1.66] | +0.16 | +9 pts | -10.0 s |
| summary | -0.81 | [-2.22, +0.59] | -0.09 | +6 pts | -5.3 s |

Four of 128 trials hit the 12-round cap (three baseline, one summary), so every difference above is a
lower bound.

**The handoff removes 4.72 of 7.22 rounds and 70% of the wall clock, and correctness goes up, not
down** (91% -> 100%: the baseline sometimes gave up searching before it found the answer).

**The negative control is clean.** An equal-size window of an unrelated file moves rounds by +0.31
with an interval straddling zero. Whatever is working is the content, not the presence of a bulky
early tool result and not an implicit "prior work happened" cue.

**A summary does not substitute for the bytes.** The condensed predecessor note -- the thing a harness
can already do today with no KV machinery at all -- is worth -0.81 rounds, interval straddling zero.
This is the comparison that decides whether any of this needs a cache: it does.

**The injection is cheaper in tokens than the search it replaces.** The baseline spends 12,998 prompt
tokens getting to its answer, mostly tool results from 8.19 locate calls; the oracle arm spends 2,037
including the 644 injected. Priced at 0.033 ms per uncached token, the injection costs 0.02 s and
saves about 0.36 s of prefill on top of the rounds. The cost side of a context handoff is negative at
this scale -- which is why precision did not matter in section 5.5.

**The ceiling is behavioural, and it has a cause.** 25% of oracle trials issued a `read_file` anyway
despite already holding the content -- but that number is not spread evenly. Per task:

| task | injected | oracle rounds | baseline rounds | oracle re-read rate |
|---|---|---|---|---|
| T1 | one window, 1,495 chars | 1.88 | 9.00 | 12% |
| T2 | one window, 4,936 chars | 1.50 | 7.62 | 0% |
| T3 | **two disjoint windows of one file**, 2,038 chars | 5.38 | 8.25 | **88%** |
| T4 | one window, 1,842 chars | 1.25 | 4.00 | 0% |

T3 is the only task whose handoff was assembled from two non-contiguous slices of the same file, and
it is the only task that got re-read -- 88% of the time, which drags the pooled rate from near zero to
25% single-handedly, and cuts its own round saving from about 6 to 2.9. **A fragmented handoff is
distrusted; a contiguous one is not.** That is a design rule for any transfer mechanism: hand over
whole spans, not a stitched selection, even when the stitched version is smaller.

### 5.7 Handing the successor the bytes: stage 2, the real harness

Sixteen W5 runs (fan-in over three different files, so the successor holds one of three), four arms
interleaved inside each repetition on a seeded shuffle, four repetitions. Both injection paths were
verified live in the trace: `[prewarm] inv-c task=... arm=summary pairs=1 chars=1,288` and
`[prewarm] inventory-a task=... arm=dag pairs=9 chars=47,300`, the latter being the deduplicated
union of three predecessors' reads minus what that agent already held.

[cleanup note: `research/07_task_dag/data/prewarm/harness/` holds 24 W5 traces, not 16: a first batch of 8 (r1–r2 of each arm, 2026-09-17 03:49–05:22 UTC) and a second of 16 (r1–r4, 05:24–08:30 UTC), so r1 and r2 exist twice per arm. `prewarm_analyze.py` pools every trace in the folder, so the tables below are over all 24 -- six runs per arm, 22 claimed successors in `prewarm_rows.json` (6 baseline, 6 dag, 5 pollute, 5 summary). The two `[prewarm]` lines quoted above come from the first batch, and the §7 command (`--reps 2`, "8 harness runs") reproduces only that batch.]

| arm | successors | rounds | reads | active s | prompt tok | out tok | injected tok | files cited | completed |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 6 | 5.17 | 4.50 | 146.9 | 14,754 | 6,451 | 0 | 5.5 | 100% |
| **dag (live prediction)** | 6 | **3.00** | 3.33 | **113.0** | 30,009 | 4,507 | 11,114 | 3.7 | 100% |
| pollute | 5 | 5.00 | 4.60 | 142.3 | 37,696 | 5,971 | 11,075 | 2.4 | 60% |
| summary | 5 | 5.80 | 5.20 | 161.5 | 35,669 | 6,267 | 480 | 3.6 | 100% |

| arm | delta rounds | 95% CI | delta reads | delta active s | net s after prefill |
|---|---|---|---|---|---|
| **dag** | **-2.17** | **[-3.50, -0.83]** | -1.17 | -33.8 | +54.9 |
| pollute | -0.17 | [-1.60, +1.27] | +0.10 | -4.6 | +3.9 |
| summary | +0.63 | [-1.07, +2.57] | +0.70 | +14.6 | -16.2 |

Every successor here is cross-owner, so this is the transferable case throughout. The result matches
stage 1 in direction and in ordering, and the deployable arm's interval clears zero: **handing a
successor the content its predecessors read removes 2.17 of 5.17 rounds and 34 s of active model
time.** The two controls behave as they did in the micro-benchmark -- the equal-size irrelevant file
moves nothing (-0.17, straddling zero), and the summary moves nothing (+0.63, straddling zero), so a
condensed note is not a substitute for the bytes in the real harness either.

Three things the harness shows that the micro-benchmark could not:

* **The token argument does not carry over.** In stage 1 the injection was cheaper than the search it
  replaced (2,037 against 12,998 prompt tokens). Here the injected arms spend *more* than the
  baseline (30,009 against 14,754), because the `dag` policy hands over 11,114 tokens -- three
  predecessors' whole read sets -- and the agent still does its own work on top. Latency improves
  anyway, since 11.1k tokens is 0.37 s of prefill against 34 s of active time removed. Prefill is not
  the cost that matters here; context budget is.
* **The pollution arm is not merely neutral, it is harmful.** Only 3 of its 5 successors completed
  (60%, against 100% everywhere else) and its reports cite the fewest files (2.4 against the
  baseline's 5.5 and the task's target of 3). Filling a successor's context with an equal volume of
  irrelevant content costs it the task, which is the cleanest possible demonstration that the
  *content*, not the bulk, is what the working arms are delivering.
* **The targeting improves.** W5's synthesis asks about exactly three modules. The baseline cites 5.5
  distinct files, the `dag` arm 3.7 -- closer to the answer that was asked for.

## 6. What this means for the tiered-memory argument

The 2026-09-16 deck ends on a proposal: the task board is a dependency graph the engine cannot see,
so emit a per-session retention directive from it. These two experiments say the second half of that
sentence is right and the first half is not.

**Reuse is worth a lot.** A successor handed the bytes its predecessor read finishes in 2.50 rounds
instead of 7.22 and 18.4 s instead of 62.4 s, with correctness no worse and in fact better; in the
real harness the same policy ran its successor in 3.00 rounds against 5.17 (95% CI [-3.50, -0.83])
and cut active model time from 146.9 s to 113.0 s. In the micro-benchmark the handoff is even cheaper
in tokens than the search it replaces (2,037 against 12,998 prompt tokens) -- but that does not
survive into the harness, where handing over all three predecessors' read sets costs 11,114 tokens
and takes the arm's prompt total *above* the baseline's. The latency still improves, because 11.1k
tokens is 0.37 s of prefill against 34 s of active time removed. Prefill is not the cost that
matters; context budget is.

The 43.9 s saving decomposes roughly as the measured constants predict: 4.72 rounds removed at the
3.72 s fixed component is 17.6 s, the 751 fewer output tokens at 17.44 ms are 13.1 s, and the 10,961
fewer uncached prompt tokens at 0.033 ms are 0.4 s -- about 31 s of the 44, with the remainder in
tool execution and run-to-run variance. Prefill is 1% of the saving. **What a handoff buys is round
count and the decode that goes with it**, exactly as finding 7 of last week's synthesis predicted.

**The dependency graph is the wrong place to get the routing from.** It predicts well when it agrees
with the task text and not at all when it does not (the W4 placebo, -1.0), so it is a shadow of the
text rather than a signal in its own right. And even used at its best it is beaten on recall by the
crudest possible policy: keeping every earlier task's reads recalls 85.6% of a successor's bytes
against the direct predecessors' 36.0%. Since transferring three quarters more content than needed
costs 3.9 s of prefill against 63 s of rounds removed, there is no precision argument to set against
that recall gap.

**So the engine-tier question is capacity, not prediction.** A retention policy should hold as much
of the session's read history as it can afford and evict on capacity alone; the elaborate part --
working out from the board which block the next agent will want -- buys nothing that "keep it all"
does not already buy, and it can be actively wrong. If something must be prioritised, the task
description is a better and far cheaper key than the graph.

**Three limits worth stating.** The behavioural ceiling: a quarter of injected trials went and read
the file anyway -- but that is not a constant tax, it is one task's 88%, the only one handed two
non-contiguous slices instead of a whole window (section 5.6). Hand over whole spans and the ceiling
largely disappears; stitch a selection together and the agent goes and fetches the file itself. The
second limit is power: the end-to-end half rests on five or six successors per arm across sixteen
runs, enough for the deployable arm's interval to clear zero but not for fine distinctions between
the two controls. And the third is scale: every prompt here is 2-14k tokens. Where a resident set runs past
50k tokens the prefill term stops being negligible and the precision argument may come back --
which is next step 9 of the 2026-09-16 synthesis, still open.

[cleanup note: the stage-2 tables pool 24 runs, six per arm, not sixteen -- see the note in §5.7.]

## 7. Reproduction

```bash
cd /home/yq335/learn-claude-code

# gates, in the order they have to pass
python3 research/07_task_dag/prewarm_smoke.py                 # does the provider accept a fabricated tool_use?
python3 research/07_task_dag/dag_workloads.py --probe --level L2   # does the lead emit edges, and do they execute?

# experiment 1: nine team runs over five dependency-shaped workloads
python3 research/07_task_dag/dag_workloads.py --rep r1 --level L2 \
        --only W1,W2,W3,W4,W5 --max-seconds 900
python3 research/07_task_dag/dag_workloads.py --rep r2 --level L2 \
        --only W2,W5,W1,W4 --max-seconds 900
python3 research/07_task_dag/dag_redundancy.py \
        research/07_task_dag/data/dag_redundancy --tables --perms 20000 \
        --json research/07_task_dag/data/dag_redundancy/dag_tables.json

# the zero-edge base rate, from the existing flat corpus (no API calls)
python3 research/07_task_dag/dag_redundancy.py \
        research/02_input_redundancy/data/redundancy_profiling_ctx512k --tables

# experiment 2, stage 1 (128 trials) and stage 2 (8 harness runs)
python3 research/07_task_dag/prewarm_loop.py --reps 8 --concurrency 3
python3 research/07_task_dag/prewarm_harness.py --reps 2 --only W5 \
        --arms baseline,dag,pollute,summary --max-seconds 900
python3 research/07_task_dag/prewarm_analyze.py \
        --stage1 research/07_task_dag/data/prewarm/stage1.jsonl \
        --stage2 research/07_task_dag/data/prewarm/harness
```

Notes for anyone repeating this:

* Runs must be **sequential**. One team run is a lead plus three teammates, and the provider
  rate-limits at roughly five concurrent sessions.
* `--level L2` is required. Below it the lead writes the tasks but no edges, and the study has no
  independent variable.
* Do not pool the workloads. W3 and W4 are designed to show zero; averaging them with W1/W2/W5
  cancels the effect (section 5.4).
* Judge any edge effect against the base rate in section 5.2, never against zero, and read the
  validity block before any p-value.
* `dag_workloads.py --print-prompts` shows every prompt without calling the API; `prewarm_loop.py
  --dry-run` shows what each arm would inject and the token match between oracle and pollution.

## A.W Additions from `weekly_progress/092326/dag_kv_reuse.md` (2026-09-23)

[cleanup note: this weekly note numbers its §3 findings 1–8; the 2026-09-23 deck cites findings 1–4 (slide 4) and finding 8 (slides 3 and 5). All eight repeat Part A and are not copied here: finding 1 → §5.4, 2 → §5.5, 3 → §5.5, 4 → §5.3, 5 → §5.6, 6 → §5.6, 7 → §5.6, 8 → §5.7. A bare "finding N" below (as in "the main threat to finding 3") uses this numbering; "finding 7 of last week's synthesis" is finding 7 in §2 of `weekly_progress/091626/tiered_memory_conclusion.md`, as in Part A §6.]

_(was the header paragraph)_

Weekly progress, 2026-09-23. Full report: Part A.
Tooling: `research/07_task_dag/dag_workloads.py` (five [corrected 2026-09-23: earlier dag_kv_reuse.md said "four"; Part A §3.1 and `research/07_task_dag/data/dag_redundancy/` have five, W1–W5] dependency-shaped workloads and the
DAG-execution gate), `research/07_task_dag/dag_redundancy.py` (task attribution, matched-pair contrast, prefetch
policies), `research/07_task_dag/prewarm_smoke.py` (provider gate), `research/07_task_dag/prewarm_loop.py` and
`research/07_task_dag/prewarm_harness.py` (the two stages of the handoff experiment),
`research/07_task_dag/prewarm_analyze.py` (tables), and additive `--prewarm*` flags in `research/common/profile_run.py`.
Traces: `research/07_task_dag/data/dag_redundancy/` and `research/07_task_dag/data/prewarm/`.
Provider: z.ai `glm-5.3-flash`. Companion notes: `weekly_progress/091626/tiered_memory_conclusion.md` next steps
5, 7 and 13.

_(was §0 "In one page", the board-shape paragraph)_

**What a board actually looks like.** Small, wide and shallow -- median 5 tasks, 3 edges, 2 layers,
and **only 23% of tasks have any predecessor at all** (163 tasks over 32 runs). A typical W5 board:

```
#1 inventory trace_runtime.py  (scout-runtime, done 261s) ┐
#2 inventory trace_view.py     (scout-view,    done 317s) ├─► #4 synthesis (scout-runtime, starts 414s)
#3 inventory trace_stats.py    (scout-stats,   done 412s) ┘
#5 standalone item             (scout-runtime)   -- no edges
```

Three facts in that one picture, and they matter more than the graph theory:
* the join waits for its **slowest** predecessor, so #1's output is already 153 s old when #4 starts;
* #4 was claimed by **scout-runtime, which did #1 itself** -- it already holds one of the three files
  and is missing the other two. Across 35 successors, 2.5 predecessors each, **67% of those
  predecessor links cross agents**, and 33 of 35 successors are missing something;
* three teammates handle five items, so agents are reused and their context is never cleared.

_(was §2, item 3 of "Three things had to be fixed")_

3. Creating an edge and executing one are different things. `spawn_teammate` claims a task before
   starting the agent, and a claim on a blocked task is refused, so `spawn_teammate(task_id=<blocked>)`
   fails silently and that item is never picked up. Successors reach an agent only through an idle
   teammate's auto-claim. After rewriting the workloads around that, executed-edge yield was 1.0 in
   seven of nine runs.

_(was §4, second paragraph)_

**Reuse is worth a lot, and it is worth it in rounds.** 4.72 rounds and 44 s per successor task in
the micro-benchmark, 2.17 rounds and 34 s of active time in the harness, at no cost to correctness. That is exactly the shape finding 7 of last
week's synthesis predicted: prefill is under 2% of a call, so what a cache buys is round count.

_(was §5 Next steps)_

1. **Re-run where prefill matters.** Every prompt here is 2-14k tokens, where 0.033 ms/token is
   noise. Above ~50k tokens per call the precision argument may come back. This is next step 9 of
   `weekly_progress/091626/tiered_memory_conclusion.md`, still open, and it is now the main threat to finding 3.
2. **Attack the 25% behavioural ceiling.** A quarter of injected trials read the file anyway. That
   part is a prompt problem, not a cache problem: tell the agent what it already holds, and measure
   whether the re-read rate moves. Cheap -- it is a stage-1 variant.
3. **Test "keep everything that fits" against a capacity bound.** All the recall numbers here assume
   the whole earlier read history is available. Re-run the policy table under a byte budget (the
   session's real HBM share) and find the budget at which broadcast stops dominating the graph.
4. **Retire the DAG-directed retention idea, or re-scope it to the task text.** Next step 13 of the
   2026-09-16 synthesis should be rewritten: the artefact worth shipping to the engine is the task
   description, not the `blockedBy` graph.
5. **Fix the harness scheduling fact this uncovered.** `spawn_teammate` silently refuses a blocked
   task, so a lead that follows its own system prompt loses every successor it tries to assign at
   spawn time. Either make the refusal visible to the lead or let a spawn bind a blocked task and
   wait.

_(was §6 Housekeeping)_

Untracked and ready to commit: `research/07_task_dag/dag_workloads.py`, `research/07_task_dag/dag_redundancy.py`,
`research/07_task_dag/prewarm_smoke.py`, `research/07_task_dag/prewarm_loop.py`, `research/07_task_dag/prewarm_harness.py`,
`research/07_task_dag/prewarm_analyze.py`, the additive `--prewarm*` diff in `research/common/profile_run.py`, a one-line
`call_id` addition and a `collect()` sidecar-filter fix in `research/common/input_redundancy.py`,
Part A, this note, and the traces under
`research/07_task_dag/data/dag_redundancy/`, `research/07_task_dag/data/dag_redundancy_probe/` and `research/07_task_dag/data/prewarm/`.

[cleanup note: all of this was committed in f17e2a4 (2026-09-17); the scripts now live in `research/07_task_dag/` and `research/common/`, the traces in `research/07_task_dag/data/`.]

One correction to an older document: `research/02_input_redundancy/README.md` Part A (was `file_read_reuse_profile.md:44`) says teammates are always denied
bash. Since commit `968a33c` read-only commands are auto-allowed off the main thread
(`code.py:2016-2019`), so redundancy measurements must count bash reads, which this analyzer does.

# Part B — Does the Lead maintain a dependency graph, or is the task board just a list?

_Formerly `s15_integrated_harness/dag_census_profile.md` (2026-09-17)._

Profiling report, 2026-09-17. Weekly note: Part B.
Tooling: `research/07_task_dag/census_workloads.py` (twelve workloads across six task categories x two latent
structures, plus the contamination check that keeps the measurement honest), `research/07_task_dag/dag_census.py`
(board reconstruction, reference-graph matching, the "where did the ordering go" measures),
`research/07_task_dag/census_score.py` (re-runs each archived sandbox's own tests).
Traces: `research/07_task_dag/data/dag_census/` (z.ai `glm-5.3-flash`), `research/07_task_dag/data/dag_census_ds/` (DeepSeek
`deepseek-v4-flash`), `research/07_task_dag/data/dag_census_smoke/` (the two pilot runs).
Companion: Part A (2026-09-16), whose section 2.2 this report corrects.

Terminology as in the 2026-09-09 deck: a *turn* is one Lead activation, a *round* is one agent-loop
iteration.

---

## 1. The question

Last week's KV-reuse study proposed reading a retention directive off the Lead's task board. That
only works if the board carries structure. Its section 2.1 reported that `blockedBy` was empty in
all 31 team runs carrying byte-level data, and its section 2.2 concluded that **the Lead does not
emit edges unless the prompt names the mechanism**. If that were true the board would be useless as
a signal, and the whole tiered-memory routing idea would have to come from somewhere else.

So: is a maintained dependency graph the common case, or is the board almost always a flat list?

Two facts from `code.py` frame the answer:

- `create_task` (`code.py:3246-3252`) accepts only `subject` and `description`, and hard-codes
  `blockedBy=[]` (`code.py:299`). An edge needs a **second** call, `update_task`
  (`code.py:3254-3265`), and only while the task is still `pending` and unowned
  (`code.py:333-337`). There is no edge-removal path anywhere.
- The shipped system prompt already asks for edges (`code.py:892-896`): *"Create all task nodes
  first. Only after create_task returns runtime-generated IDs, use update_task with those exact IDs
  to add dependencies."*

The affordance and the instruction both exist. What was never measured is whether the Lead uses
them when the work actually has stages.

**The rule this experiment runs under.** Edge emission is the dependent variable, so no prompt may
name the mechanism. `census_workloads.check_prompts()` refuses to run if any assembled prompt
matches `update_task|addBlockedBy|blocked|depend|prerequisite|predecessor|successor` at a word
boundary, and the followup turn is held to the same rule. The staged workloads state their order
the way a person would ("using the inventory produced by the previous item"); translating that into
board edges is precisely what is being measured and is never requested.

---

## 2. Why the existing corpus could not answer it

### 2.1 The retrospective census

Replaying `task_create` / `task_update` over every trace in the tree:

| corpus | runs | runs with a board | tasks | edges | runs with >=1 edge | update_task calls |
|---|---:|---:|---:|---:|---:|---:|
| "natural" workloads: `latency_profiling`, `redundancy_profiling`, `redundancy_profiling_ctx512k`, `reuse_profiling` | 59 | 35 | 133 | **0** | **0** | **0** |
| dependency-instructed (L2): `dag_redundancy`, `dag_redundancy_probe`, `prewarm/harness` | 36 | 35 | 171 | 96 | 35 | 41 |
| one interactive session, Qwen3.8-27B (`s15_integrated_harness/traces/run_20260902T004901_584576Z_dc6a9685.jsonl`) | 1 | 1 | 57 | 96 | 1 | 39 |

Excluded as script-synthesized rather than model-driven: `research/06_tool_cost/data/tool_latency/`, whose 133 tasks and
50 edges are created by `research/06_tool_cost/tool_latency_probe.py:376-384` calling `create_task` / `update_task`
directly from Python. Its task subjects are literally `probe anchor` and `probe dep a 0`. Counting
them would have inflated the "Leads do build graphs" side with rows no model ever produced.

[cleanup note: each of the two probe traces committed in `research/06_tool_cost/data/tool_latency/` holds 133 created tasks and 25 edges, i.e. 266 tasks and 50 edges over both runs; "133 tasks and 50 edges" mixes a per-run and a two-run count. The exclusion stands either way.]

### 2.2 The word that explains the zero

The 35 natural runs did not have empty boards because the Lead refused to relate their items. They
had empty boards because **their prompts declared the items independent, and the items were**:

- `research/05_latency_breakdown/latency_workloads.py:40` — *"Create three **independent** task-board items, one per item
  below, and delegate each to its own teammate."* (all FQA / CODE / MATH runs)
- `research/02_input_redundancy/redundancy_workloads.py:37, 50, 62, 76` — *"Create three **independent** task-board
  items…"* (all four PF/DC workloads)

Every one of those 35 runs was told the work was independent, and it was: three unrelated documents,
three unrelated bench problems, three unrelated AIME problems. An empty `blockedBy` is the *correct*
board for that work. That corpus was never evidence about whether a Lead maintains a graph, and it
should not have been read as such — including by last week's report.

### 2.3 What this leaves

The Qwen row shows a Lead building a 57-node, 96-edge graph on an open-ended repository goal with no
dependency instruction available to it — the one existing data point suggesting the capability is
there. It is confounded three ways at once (different model, different serving stack, far larger
session), which is why it motivated a second provider here rather than settling anything.

---

## 3. Design

### 3.1 Twelve workloads

Six categories, each in two matched variants. DEP work genuinely has stages; FLAT work genuinely
does not. FLAT is the specificity control: without it, zero edges on DEP cannot be told apart from a
Lead that never emits edges at all.

| category | DEP variant (a correct board HAS edges) | FLAT variant (a correct board has NONE) | shape |
|---|---|---|---|
| FQA | quote three glossary definitions -> locate each in `ARCHITECTURE.md` -> write the reconciliation note | three independent questions over `trace_runtime.py`, `trace_view.py`, `trace_stats.py` | chain |
| CODE | build the spec checklist -> implement `solution.py` against it -> run tests and fix | three independent bench problems (`intervals`, `ttl_cache`, `expr`) | chain |
| MATH | count S = {n <= 1000, not divisible by 2 or 5} -> sum S -> that sum mod 7 | three independent AIME problems | chain |
| REFACTOR | inventory every `normalize_key` call site -> rename to `canonical_key` -> run the tests | three independent read-only error-handling audits | chain |
| DATA | per-file function/class counts -> totals and percentages -> ranking | three independent per-file counts | chain |
| RESEARCH | three surveys of three modules -> one synthesis consuming all three | three independent document summaries | fan-in |

`REFERENCE_EDGES` per DEP workload is hand-authored: `[(1,0), (2,1)]` for a chain, `[(3,0), (3,1),
(3,2)]` for the fan-in. Answer keys are verified independently (MATH-DEP: 400 -> 200000 -> 3).

The REFACTOR fixture (`research/common/fixtures/census_bench/refactor/`) is new: `pkg.py` defines `normalize_key`,
`store.py` and `report.py` import and call it (7 references across 3 files), and `test_pkg.py`
imports `canonical_key`, so the tests pass only once every call site has moved. It sets
`sys.dont_write_bytecode = True` before importing the package, because a stale `.pyc` written by an
early test run otherwise shadows the renamed module and makes a correct rename look broken.

### 3.2 The neutral frame

Every run carries the same frame, which names the board but never its shape — not how many items,
not how they relate:

> Put this work on the task board and delegate it to a team. I confirm the team now: spawn the
> teammates immediately without asking for confirmation. Do not create worktrees. Spawn exactly
> THREE teammates, no more, whatever the number of items. Each teammate must call complete_task as
> soon as its findings are written, and then claim the next available item on the board.

"claim the next available item" is plain work acquisition: it lets a graph *execute* if the Lead
builds one, without hinting that it should. The followup is `"Confirmed, proceed: spawn the
teammates now."` — deliberately dropping the existing driver's *"create the task nodes, add the
dependencies, and spawn the teammates now"* (`research/07_task_dag/dag_workloads.py:390`), which would have
contaminated every run.

### 3.3 Measures

*Primary.* `tasks`, `edges`, `update_task` calls, and for DEP runs `edge_recall` against the
reference graph, plus `false_edges`. Tasks are matched to workload items by greedy one-to-one token
overlap, and the mean overlap is reported as `match` so a zero recall can never be confused with a
bad match.

*Secondary — where the ordering goes when the graph is empty.* An empty graph would not mean the
ordering vanished, only that it is carried somewhere else, and where it is carried decides whether a
scheduler could still recover it: **prose** (written into the task text), **staged creation** (later
tasks withheld until earlier ones finish), **spawn gating**, **Lead-side execution** (the Lead does
the dependent stage itself). Plus the cost: **order violations**, a task claimed before its true
predecessor completed.

---

## 4. Results

Final corpus: **44 usable runs** (26 DeepSeek, 18 glm). Both sweeps were stopped deliberately once
the result was unambiguous and had been stable across every cell, so the glm arm covers 18 of a
planned 36 cells. Ten further DeepSeek runs were refused by the provider and are excluded by name
(section 6).

### 4.1 The Lead builds the graph, and builds it exactly right

| model | DEP runs | tasks | edges emitted | reference edges | runs with >=1 edge | edge recall | false edges |
|---|---:|---:|---:|---:|---:|---:|---:|
| `deepseek-v4-flash` | 13 | 41 | 28 | 28 | 13 | **1.00** | 0 |
| `glm-5.3-flash` | 8 | 25 | 17 | 17 | 8 | **1.00** | 0 |
| **both** | **21** | **66** | **45** | **45** | **21** | **1.00** | **0** |

Every DEP run in every category on both providers reproduced its reference graph exactly — no
missing edge, no spurious edge. Per category (both arms pooled): FQA 3/3 runs, CODE 3/3, MATH 4/4,
REFACTOR 3/3, DATA 5/5, RESEARCH 3/3.

The fan-in case is worth its own sentence: RESEARCH-DEP's three-predecessor join is emitted as a
**single** `update_task` call carrying all three blockers in one `addBlockedBy` list, which is why
`update_task` calls (39) is lower than edges (45) across the corpus.

**This contradicts section 2.2 of last week's report.** These prompts are L0 by that report's own
definition — narrative order only, mechanism never named — and they produce complete graphs. The
likeliest reason for the disagreement is that the earlier L0 arm ran the W1/W4 shapes (six items
across two branches, one of them a deliberately crossed placebo) where the chain is far less
salient than in a clean three-item chain, and it carried a team block explaining that
`spawn_teammate` refuses blocked tasks — operational advice that reads as "do not create blocked
items". Settling which of the two it was needs a direct re-run; what is settled is that the blanket
claim does not hold.

### 4.2 The specificity control

| model | FLAT runs | tasks | edges emitted |
|---|---:|---:|---:|
| `deepseek-v4-flash` | 13 | 39 | **0** |
| `glm-5.3-flash` | 10 | 30 | **0** |
| **both** | **23** | **69** | **0** |

Zero edges in 23 of 23 FLAT runs. The Lead is not relating items indiscriminately; it relates them
when the work relates them. Taken with 4.1 that is perfect separation — 21 DEP runs all with the
full graph, 23 FLAT runs all with none. A one-sided Fisher exact test on that 21-vs-23 split gives
**p = 5.0e-13**.

A length confound is ruled out by the matrix itself: `CODE-FLAT` is the **longest** prompt of all
twelve (1,876 chars) and emits zero edges, while `CODE-DEP` is shorter (1,254) and emits two. More
prompt does not mean more edges.

### 4.3 Where the ordering lives

| measure | DEP runs | FLAT runs |
|---|---:|---:|
| creation waves | 1.0 | 1.0 |
| tasks created after the first completion | 0.0 | 0.0 |
| spawns after the first completion | 0.0 | 0.0 |
| prose ordering in the task text | 0.25 - 1.00 | 0.00 - 1.00 |
| the constraint stated in words ("depends on") | 0.00 | 0.00 |
| order violations | **0.00** | - |

The Lead creates the **whole board up front, in a single wave**, in every run of both arms. It never
withheld a successor and created it later, and never delayed a spawn to enforce an order. So staged
creation and spawn gating are *not* how ordering is carried here — the board edges are, backed
partially by prose in the descriptions. That matters for the routing argument: a scheduler reading a
board snapshot at spawn time sees the complete graph, because the complete graph exists before any
work starts.

Zero order violations across all DEP runs: no successor was ever claimed before its predecessor
completed. Every task in every run was claimed and completed (`unclaimed` 0, `incomplete` 0). The
graphs were not merely created, they were executed.

### 4.4 The work came out right

`census_score.py` re-runs each archived sandbox's own tests: **10 of 10 attempted sandboxes passed**
(7 DeepSeek, 3 glm). CODE-DEP carries one problem through checklist -> implementation -> green tests
and correctly leaves the other two problems untouched; CODE-FLAT solves all three; REFACTOR-DEP's
tests pass only if every one of the 7 call sites moved. A tidy graph is not a Lead that planned and
never delivered.

### 4.5 Cost

| model | DEP wall (mean) | runs |
|---|---:|---:|
| `deepseek-v4-flash` | 118 s | 13 |
| `glm-5.3-flash` | 488 s | 8 |

DeepSeek is ~4x faster end to end on identical prompts. This is a provider-and-model difference
together and should not be read as a model property.

---

## 5. What this means for the tiered-memory argument

1. **The board is a usable routing signal after all.** Last week's report concluded the dependency
   graph is the wrong place to get retention from, partly because the graph was believed to be
   absent unless demanded. It is not absent: where the work has structure the Lead states that
   structure, completely and correctly, on both providers tested, in all six task categories.
2. **The signal is available early.** The whole graph exists before the first teammate starts, in
   one creation wave. A retention directive can therefore be computed at spawn time rather than
   inferred as the run unfolds.
3. **This does not resurrect DAG-directed retention on its own.** Last week's finding that
   *broadcast beats the graph* (all-earlier 85.6% byte recall vs direct-predecessors 36.0%) was a
   measurement of what successors read, and nothing here touches it. What changes is the premise:
   the graph's absence is no longer a reason to dismiss it, so the comparison has to be re-run on
   boards that actually carry edges.
4. **Prompt wording dominates board shape.** One word — "independent" — accounts for 35 runs of
   empty boards across three prior experiments. Any future claim about what Leads do with the board
   has to quote the prompt that produced it.

---

## 6. Threats and limits

- **Both sweeps were stopped early, on purpose.** The glm arm covers 18 of a planned 36 cells and
  DeepSeek 26 usable of 36. Every cell from the first four onward gave the same answer, so the rest
  would have added reps rather than information. The cost is uneven per-category counts (DATA-DEP
  has 5 runs, REFACTOR-FLAT 3) and no cell with the planned 3 reps on both providers.
- **One glm run was killed mid-flight** at the shutdown (`CODE-DEP-glm-r2`) and is quarantined in
  `research/07_task_dag/data/dag_census_partial/`. It wrote a `profile_end` while terminating, so it would otherwise
  have passed as a complete row: a truncated run is not detectable from `status` alone.
- **Ten DeepSeek runs were refused by the provider** with `402 Insufficient Balance` (all of rep 3
  bar two). They are detected as `provider_failed` (no board, fewer than two model responses) and
  excluded by name; pooling them would have manufactured an empty-board result out of an unpaid
  invoice. Reps 1 and 2 are complete.
- **Two providers, one evening, one harness.** Both models are "flash"-tier. Nothing here says
  anything about a Lead under context pressure, over a long session, or with many more than four
  items — and the Qwen counterexample had 57 tasks, an order of magnitude more than these.
- **DEP prompts are on average slightly longer** than their FLAT twins (mean 1,254 vs 1,189 chars),
  since staged work takes more words to describe. The CODE pair reverses the direction and still
  shows the effect, which is the best available check that length is not driving it.
- **Three reps per cell** detects only large effects. It is ample for a proportion at 0 or 1 and
  inadequate for anything in between.
- **`--model` on `dag_workloads.py` is silently broken.** `code.py:76` calls
  `load_dotenv(override=True)`, so `.env` overwrites any `MODEL_ID` a parent process sets in the
  child environment — verified: passing `MODEL_ID=deepseek-v4-flash` yields `glm-5.3-flash` after
  the load. Cross-model work must use a lane with its own `.env`, as this experiment does. No
  published result is invalidated: no shipped trace label carries a model tag, so the flag appears
  never to have been exercised.

---

## 7. Reproduction

```bash
# 0. integrity gate -- must print "all prompts clean" and exit 0, BEFORE any API call
python3 research/07_task_dag/census_workloads.py --check-prompts

# 1. lanes (the cross-provider arm needs its own .env; --model does not work, see section 6)
rsync -a --delete --exclude .git --exclude 's15_integrated_harness/traces' \
      --exclude profiling_sandbox --exclude __pycache__ --exclude '.memory' --exclude '.tasks*' \
      --exclude .mailboxes --exclude .transcripts --exclude .task_outputs --exclude .worktrees \
      /home/yq335/learn-claude-code/ /home/yq335/lanes/census/
#    lanes/census_ds/.env: MODEL_ID=deepseek-v4-flash
#                          ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
#                          ANTHROPIC_API_KEY=<the DeepSeek key>

# 2. the two arms (sequential within a provider, concurrent across providers)
python3 research/07_task_dag/census_workloads.py --reps 3 --provider-tag glm \
    --repo /home/yq335/lanes/census    --trace-dir research/07_task_dag/data/dag_census
python3 research/07_task_dag/census_workloads.py --reps 3 --provider-tag ds \
    --repo /home/yq335/lanes/census_ds --trace-dir research/07_task_dag/data/dag_census_ds

# 3. tables, and the sandbox scores
python3 research/07_task_dag/dag_census.py \
    research/07_task_dag/data/dag_census research/07_task_dag/data/dag_census_ds --per-run
python3 research/07_task_dag/census_score.py research/07_task_dag/data/dag_census_ds

# 4. detector check -- the analyzer must find edges where they exist and none where they do not
python3 research/07_task_dag/dag_census.py research/07_task_dag/data/dag_redundancy
#   -> 9 runs, 22 edges
python3 research/07_task_dag/dag_census.py research/05_latency_breakdown/data/latency_profiling
#   -> 15 boards, 0 edges
```

Notes for anyone repeating this:

- **Run the integrity gate first.** A prompt that names the mechanism turns the dependent variable
  into a constant, and the failure is invisible in the results.
- Runs must be **sequential within a provider** — one team run is a Lead plus three teammates and
  the provider rate-limits around five concurrent sessions. Two providers can run concurrently.
- `--print-prompts` and `--check-prompts` cost no API calls; use them for every prompt edit.
- A run the provider refused looks like an empty board. Always read the exclusion line the analyzer
  prints above Table A before quoting any zero.

## B.W Additions from `weekly_progress/092326/dag_census.md` (2026-09-23)

[cleanup note: this weekly note numbers its §3 findings 1–7; all seven repeat Part B and are not copied here: finding 1 → §4.1, 2 → §4.2, 3 → §4.1 (last paragraph), 4 → §2.1–2.2, 5 → §4.3, 6 → §4.3–4.4, 7 → §4.5. "Finding N" in the blocks below refers to this numbering.]

_(was §5 Next steps)_

1. **Re-run the prefetch-policy comparison on unprompted boards.** `dag_redundancy.py`'s policy
   table (direct_pred 36.0% vs all_earlier 85.6% byte recall) was computed on L2-forced boards from
   the W1-W5 shapes. Repeat it over `research/07_task_dag/data/dag_census/` DEP runs, where the graph is the Lead's own
   and matches the work, and see whether direct predecessors do better when the edges were not
   coerced. This is the finding that decides whether DAG-directed retention is worth anything.
2. **Settle why the earlier L0 arm saw nothing.** Two candidate causes are named in finding 3.
   Re-run W1 and W2 at L0 with the neutral frame from `census_workloads.py` and with the
   `spawn_teammate`-refuses sentence removed, one factor at a time.
3. **Push on session scale.** Everything here is 3-4 items. The Qwen counterexample had 57 tasks and
   96 edges. Measure whether recall holds as the board grows past roughly ten items, which is where
   a Lead has to decide what *not* to relate.
4. **Even out the cells if the result is ever challenged.** Both sweeps were stopped early once the
   answer was stable, leaving uneven per-category counts (DATA-DEP 5 runs, REFACTOR-FLAT 3) and no
   cell with 3 reps on both providers. DeepSeek's rep 3 additionally died on `402 Insufficient
   Balance` and needs a credit top-up. Nothing here is close enough to call to need this, but a
   reviewer asking for balanced reps is asking for something reasonable.

_(was §6 Housekeeping)_

Untracked and ready to commit: `research/07_task_dag/census_workloads.py`,
`research/07_task_dag/dag_census.py`, `research/07_task_dag/census_score.py`, `research/common/fixtures/census_bench/refactor/`,
Part B, this note, and the two trace directories
`research/07_task_dag/data/dag_census/`, `research/07_task_dag/data/dag_census_ds/`, `research/07_task_dag/data/dag_census_smoke/` and
`research/07_task_dag/data/dag_census_partial/` (one glm run killed at shutdown; it wrote a `profile_end` while
terminating, so a truncated run is **not** detectable from `status` alone and must be quarantined
by hand).

[cleanup note: committed in f17e2a4 (2026-09-17); the scripts and traces now live in `research/07_task_dag/`, the REFACTOR fixture in `research/common/fixtures/census_bench/refactor/`.]

Corrections to older documents:

- Part A section 2.2 and Part A ("the Lead does not emit edges
  unless the prompt names the mechanism") are contradicted by finding 1 and should carry a pointer
  to this note.
  [cleanup note: done in this README -- see the notes under Part A §2.2 and §5.1.]
- `dag_workloads.py:8` ("`blockedBy` stayed empty in all 31 team runs") is factually right but
  misleading without finding 4: those workloads declared their items independent.

---

## Data inventory

All paths are under `research/07_task_dag/`. Everything listed is committed (f17e2a4, 2026-09-17, then
moved here in the 2026-09-23 cleanup); `git status --ignored` lists no git-ignored local files in this
folder (no console or pipeline logs exist for either part). Every run directory holds, per run, a trace
`run_*.jsonl` and its `.inputs.jsonl` / `.reads.jsonl` sidecars written by `research/common/profile_run.py`;
the rows below say what else each holds. Times are UTC, all on 2026-09-17.

| path | what it holds | written by | used in | status |
|---|---|---|---|---|
| `data/dag_redundancy/` | 9 L2 team runs, 01:23–03:12: W1–W5 at r1, then W2, W5, W1, W4 at r2 (W1-r1, W2-r1 and W1-r2 ended on timeout, the rest completed). Per label: `<label>.dag.json` (board summary: tasks, edges, executed edges, status) and `<label>.prewarm.json` (read-set dump from `--prewarm-dump`, the file `prewarm_harness.py` reads for an oracle arm). W5-L2-r1 is the "typical W5 board" drawn in A.W. | `dag_workloads.py --level L2` | Part A §5.3–5.5; 9 of the 36 runs in Part B §2.1's L2 row; Part B §7 detector check; 9 of the 32 runs behind the A.W board-shape statistics | published |
| `data/dag_redundancy/W3-L2-r1.sandbox/` | The W3 PIPELINE run's working files: `README.md` (seed), `stage1_notes.md`, `stage2_tests.md`, `stage3_review.md`, `summary.md`. | the run's teammates, seeded from `research/common/fixtures/dag_bench/pipeline/` | Part A §3.1 (W3 control) | archive of a published run |
| `data/dag_redundancy/dag_tables.md`, `.json` | Tables A (runs), V (validity), S (successors), B (pairs), C/C2 (matched contrast), D (prefetch policies), E (pricing). Regenerate byte-identically. | `dag_redundancy.py --tables --perms 20000 --json` | Part A §5.3–5.5 | published |
| `data/dag_redundancy_probe/` | 3 gate probes, 01:04–01:22, run before the workloads were rewritten: W1-L2-r1 (timeout at 518 s; 3 tasks, 2 edges), W2-L2-r1 (timeout at 455.9 s; 3 tasks, 2 edges), W3-L2-r1 (no `profile_end`, stopped after ~47 s with 2 tasks and 1 edge; no `.dag.json`). Their `.dag.json` record the original location `traces/dag_redundancy/probe/`. No L0 or L1 probe of Part A §2.2 is committed. | `dag_workloads.py --probe --level L2` (the `--probe` default now writes to this sibling folder) | Part A §2.2–2.3 and §5.1 (execution gate); 3 of the 36 runs in Part B §2.1's L2 row | smoke (gate probes) |
| `data/prewarm/smoke.json` | 5 provider-gate calls (shapes `A_provider_id` … `E_plain_user`): all accepted, none called a tool, all answered from the injection; 915–947 input tokens, 4.7–6.1 s each. Content byte-identical to the committed copy. | `prewarm_smoke.py` | Part A §4.1, §5.1 | published (gate) |
| `data/prewarm/stage1.jsonl` | 128 scripted trials: 4 arms (baseline, oracle, pollution, summary) × 4 tasks × 8 reps. | `prewarm_loop.py --reps 8 --concurrency 3` | Part A §5.6 | published |
| `data/prewarm/harness/` | 24 W5 team runs: batch 1 = 8 runs (r1–r2 of baseline / dag / pollute / summary, 03:49–05:22), batch 2 = 16 runs (r1–r4, 05:24–08:30, back to back). 23 runs have a `.reads.jsonl`; W5-L2-summary-r4 (batch 2) timed out at 922.6 s without creating a board. Timeouts: W5-L2-pollute-r2 (batch 1), W5-L2-pollute-r4, W5-L2-summary-r4. Per label (16 labels) `W5-L2-<arm>-<rep>.dag.json`, `W5-L2-<arm>-<rep>.prewarm.json` and `W5-<arm>-<rep>.cell.json`; these describe batch 2 only, batch 1's same-named files having been overwritten. | `prewarm_harness.py` (which runs `dag_workloads.py` → `profile_run.py --prewarm`) | Part A §5.7 (all 24 pooled; see the flag there); Part B §2.1 L2 row (24 runs, 23 with a board); 23 of the 32 runs behind the A.W board-shape statistics | published |
| `data/prewarm/prewarm_tables.md` | Stage-1 and stage-2 tables. Regenerates identically. | `prewarm_analyze.py --stage1 … --stage2 … --md` | Part A §5.6–5.7 | published |
| `data/prewarm/prewarm_rows.json` | The 22 stage-2 successor rows (one per claimed successor: 6 baseline, 6 dag, 5 pollute, 5 summary) behind the stage-2 table; batch-1 pollute-r1 (synthesis never claimed) and the board-less summary-r4 contribute none. | `prewarm_analyze.py --stage2 … --json` | Part A §5.7 | published (supporting rows) |
| `data/dag_census/` | 18 `glm-5.3-flash` runs, 18:29–20:28, lane `/home/yq335/lanes/census`: all 12 workloads at r1, plus r2 of DATA-DEP, DATA-FLAT, FQA-FLAT, MATH-DEP, MATH-FLAT, RESEARCH-FLAT — 18 of 36 planned; the sweep was stopped and its 19th run is in `data/dag_census_partial/`. Per run a `<label>.census.json`. | `census_workloads.py --reps 3 --provider-tag glm` | Part B §4 | published |
| `data/dag_census/{CODE-DEP,CODE-FLAT,REFACTOR-DEP}-glm-r1.sandbox/` | The bench workspaces after each run (CODE: `intervals/`, `ttl_cache/`, `expr/` with `solution.py` where attempted and `checklist.md` for DEP; REFACTOR: `pkg.py`, `store.py`, `report.py`, `test_pkg.py`, `INVENTORY.md`). No committed score file; re-scored during this cleanup with `census_score.py` (stdout only): 3 of 3 pass. | the runs' teammates | Part B §4.4 (the 3 glm sandboxes of "10 of 10") | archive of published runs |
| `data/dag_census/census_tables.json` | 44 per-run rows = 18 glm + 26 DeepSeek (the 10 refused DeepSeek runs excluded). Regenerates identically. | `dag_census.py data/dag_census data/dag_census_ds --per-run --json` | Part B §4.1–4.5 | published |
| `data/dag_census_ds/` | 36 `deepseek-v4-flash` runs, 18:28–19:19, lane `/home/yq335/lanes/census_ds`: 12 workloads × r1–r3. 26 usable; 10 refused with HTTP 402 `Insufficient Balance` (all of r3 except CODE-FLAT-ds-r3 and DATA-DEP-ds-r3; 5.4–5.5 s each, no board, no model response), excluded by name by `dag_census.py`. Per run a `<label>.census.json`. | `census_workloads.py --reps 3 --provider-tag ds` | Part B §4, §6 | published (26) / refused (10) |
| `data/dag_census_ds/{CODE-DEP,CODE-FLAT,REFACTOR-DEP}-ds-r{1,2,3}.sandbox/` | 9 bench workspaces; CODE-DEP-ds-r3 and REFACTOR-DEP-ds-r3 are the untouched seeds of refused runs. | the runs' teammates | Part B §4.4 | archive of published runs |
| `data/dag_census_ds/census_scores.json` | The 9 DeepSeek sandboxes scored: 7 attempted, 7 passed, 2 not attempted. Regenerates identically. | `census_score.py data/dag_census_ds --json` | Part B §4.4 (the 7 DeepSeek passes) | published |
| `data/dag_census_ds/census_tables.json` | The 26 DeepSeek rows only, identical to the DeepSeek rows of `data/dag_census/census_tables.json`. | an earlier DeepSeek-only run of `dag_census.py --json` | none | superseded (duplicate subset) |
| `data/dag_census_smoke/glm/`, `data/dag_census_smoke/ds/` | One pilot run each of FQA-DEP (r1), 18:19, before the sweeps. glm: 3 tasks, 2 edges, but 1 task unclaimed and 2 incomplete when it ended at 558.2 s; DeepSeek: complete in 276.1 s. Their `.census.json` record the original locations `traces/dag_census/smoke/` (glm) and `traces/dag_census_ds/smoke/` (ds). | `census_workloads.py` | Part B preamble ("the two pilot runs"); in no table | pilot, superseded by the full runs of the same labels |
| `data/dag_census_partial/` | CODE-DEP-glm-r2, 20:28–20:32, killed at shutdown: all three tasks completed, then `profile_end` "completed" 4 s later with 4 model requests unanswered (96 requests, 92 responses); plus its `CODE-DEP-glm-r2.sandbox/`. | `census_workloads.py` | Part B §6 (the threat-to-validity example) | aborted, quarantined |

**Previously uncited items**

- `data/dag_census/census_tables.json` — why: the per-run table of the full census, written by `dag_census.py
  --per-run --json` over both provider folders; what became of it: it is the table behind Part B §4.1–4.5
  (44 rows; the 10 refused runs excluded).
- `data/dag_census_ds/census_scores.json` — why: `census_score.py` re-ran each archived DeepSeek sandbox's
  tests to check the work behind the boards; what became of it: its 7 of 7 attempted passes are the
  DeepSeek part of Part B §4.4's "10 of 10" (the 3 glm passes were never saved to a file).
- `data/dag_census_ds/census_tables.json` — why: an earlier `dag_census.py --json` run over the DeepSeek
  folder alone; what became of it: superseded by `data/dag_census/census_tables.json`, which contains the
  same 26 rows; nothing cites it.
- `data/dag_census_smoke/ds` — why: the DeepSeek lane's pilot of FQA-DEP before the sweep; what became of
  it: superseded by `data/dag_census_ds/` FQA-DEP-ds-r1; in no table.
- `data/dag_census_smoke/glm` — why: the glm lane's pilot of FQA-DEP (it ended with a task unclaimed);
  what became of it: superseded by `data/dag_census/` FQA-DEP-glm-r1; in no table.
- `data/prewarm/prewarm_rows.json` — why: `prewarm_analyze.py --json` dumps one row per claimed successor
  of stage 2; what became of it: it holds the 22 rows the Part A §5.7 tables average, and shows the 6/6/5/5
  split per arm.

## Source map

One row per `##` (and numbered `###`) section of every source, plus the numbered findings of the two weekly notes that decks cite; new locations are sections of this README (`research/07_task_dag/README.md`).

| old file | old section | new location | status |
|---|---|---|---|
| `s15_integrated_harness/dag_kv_reuse_profile.md` | # title: Does the task DAG tell a serving system which bytes to keep, and is handing them over worth a round? | Part A (heading) | kept (H1 became the Part A heading) |
| `s15_integrated_harness/dag_kv_reuse_profile.md` | preamble (2026-09-16/17, tooling, terminology, constants) | Part A, preamble | kept |
| `s15_integrated_harness/dag_kv_reuse_profile.md` | ## 1. Questions | Part A §1 | kept |
| `s15_integrated_harness/dag_kv_reuse_profile.md` | ## 2. What had to be fixed before anything could be measured | Part A §2 | kept |
| `s15_integrated_harness/dag_kv_reuse_profile.md` | 2.1 The existing corpus cannot answer question 1 (bold paragraph) | Part A §2.1 | kept |
| `s15_integrated_harness/dag_kv_reuse_profile.md` | 2.2 The lead does not emit edges unless the prompt names the mechanism (bold paragraph) | Part A §2.2 | kept; cleanup note added (contradicted by Part B §4.1; no L0/L1 trace committed) |
| `s15_integrated_harness/dag_kv_reuse_profile.md` | 2.3 Creating an edge and executing one are different things (bold paragraph) | Part A §2.3 | kept |
| `s15_integrated_harness/dag_kv_reuse_profile.md` | ## 3. Experiment 1 — the DAG against byte-level redundancy | Part A §3 | kept |
| `s15_integrated_harness/dag_kv_reuse_profile.md` | ### 3.1 Workloads | Part A §3.1 | kept |
| `s15_integrated_harness/dag_kv_reuse_profile.md` | ### 3.2 Attributing reads to tasks | Part A §3.2 | kept |
| `s15_integrated_harness/dag_kv_reuse_profile.md` | ### 3.3 The contrast | Part A §3.3 | kept |
| `s15_integrated_harness/dag_kv_reuse_profile.md` | ## 4. Experiment 2 — handing the successor the bytes | Part A §4 | kept |
| `s15_integrated_harness/dag_kv_reuse_profile.md` | ### 4.1 The emulation, and why it is faithful | Part A §4.1 | kept |
| `s15_integrated_harness/dag_kv_reuse_profile.md` | ### 4.2 Arms | Part A §4.2 | kept |
| `s15_integrated_harness/dag_kv_reuse_profile.md` | ## 5. Results | Part A §5 | kept |
| `s15_integrated_harness/dag_kv_reuse_profile.md` | ### 5.1 The three gates | Part A §5.1 | kept; cleanup note added (pointer to §2.2 / Part B §4.1) |
| `s15_integrated_harness/dag_kv_reuse_profile.md` | ### 5.2 The base rate an edge has to beat | Part A §5.2 | kept |
| `s15_integrated_harness/dag_kv_reuse_profile.md` | ### 5.3 What an agent that inherits a dependency actually does | Part A §5.3 | kept; cleanup note added ("20 edges" vs 22 in the tables) |
| `s15_integrated_harness/dag_kv_reuse_profile.md` | ### 5.4 The edge predicts -- but only when it agrees with the task text | Part A §5.4 | kept |
| `s15_integrated_harness/dag_kv_reuse_profile.md` | ### 5.5 Prefetch policies: broadcast beats the graph | Part A §5.5 | kept |
| `s15_integrated_harness/dag_kv_reuse_profile.md` | ### 5.6 Handing the successor the bytes: stage 1, scripted agent | Part A §5.6 | kept |
| `s15_integrated_harness/dag_kv_reuse_profile.md` | ### 5.7 Handing the successor the bytes: stage 2, the real harness | Part A §5.7 | kept; cleanup note added ("Sixteen W5 runs" vs 24 pooled traces) |
| `s15_integrated_harness/dag_kv_reuse_profile.md` | ## 6. What this means for the tiered-memory argument | Part A §6 | kept; cleanup note added ("sixteen runs") |
| `s15_integrated_harness/dag_kv_reuse_profile.md` | ## 7. Reproduction | Part A §7 | kept |
| `weekly_progress/092326/dag_kv_reuse.md` | # title | Part A (heading) | dropped (duplicate of the Part A heading) |
| `weekly_progress/092326/dag_kv_reuse.md` | header paragraph (date, full report, tooling, traces, provider, companion notes) | Part A, A.W (was the header paragraph) | kept; "four" workloads corrected to "five" |
| `weekly_progress/092326/dag_kv_reuse.md` | terminology line | Part A, preamble | dropped (duplicate of Part A preamble) |
| `weekly_progress/092326/dag_kv_reuse.md` | ## 0. In one page — "The idea being tested" | Part A §1 | dropped (duplicate of Part A §1) |
| `weekly_progress/092326/dag_kv_reuse.md` | ## 0. In one page — "What the five workloads are" (table, W3/W4 note) | Part A §3.1, §5.4 | dropped (duplicate of Part A §3.1 and §5.4) |
| `weekly_progress/092326/dag_kv_reuse.md` | ## 0. In one page — "What a board actually looks like" (board-shape statistics, example W5 board, three facts) | Part A, A.W (was §0) | kept (slide 4 of the 2026-09-23 deck quotes these statistics) |
| `weekly_progress/092326/dag_kv_reuse.md` | ## 0. In one page — "The three results, in plain terms" 1–3 | Part A §5.4, §5.5, §5.6–5.7 | dropped (duplicate of Part A §5.4–5.7) |
| `weekly_progress/092326/dag_kv_reuse.md` | ## 0. In one page — "The one operational thing to fix" | Part A §2.3 | dropped (duplicate of Part A §2.3) |
| `weekly_progress/092326/dag_kv_reuse.md` | ## 1. Questions | Part A §1, §6 (first paragraph) | dropped (duplicate of Part A §1 and §6) |
| `weekly_progress/092326/dag_kv_reuse.md` | ## 2. Experiments — Setup | Part A preamble, §5.3, §5.6 | dropped (duplicate of Part A preamble, §5.3 and §5.6) |
| `weekly_progress/092326/dag_kv_reuse.md` | ## 2. Experiments — "Three things had to be fixed", items 1–2 | Part A §2.1–2.2 | dropped (duplicate of Part A §2.1–2.2) |
| `weekly_progress/092326/dag_kv_reuse.md` | ## 2. Experiments — "Three things had to be fixed", item 3 | Part A, A.W (was §2, item 3) | kept (adds: executed-edge yield 1.0 in seven of nine runs) |
| `weekly_progress/092326/dag_kv_reuse.md` | ## 2. Experiments — Design; experiment-2 emulation paragraph | Part A §3.3, §4.1, §5.1 | dropped (duplicate of Part A §3.3, §4.1 and §5.1) |
| `weekly_progress/092326/dag_kv_reuse.md` | ## 3. Findings — finding 1 (edge predicts only where it echoes the task text) | Part A §5.4 | dropped (duplicate of Part A §5.4) |
| `weekly_progress/092326/dag_kv_reuse.md` | ## 3. Findings — finding 2 (broadcast beats the graph) | Part A §5.5 | dropped (duplicate of Part A §5.5) |
| `weekly_progress/092326/dag_kv_reuse.md` | ## 3. Findings — finding 3 (precision is nearly free; recall is the whole game) | Part A §5.5 | dropped (duplicate of Part A §5.5) |
| `weekly_progress/092326/dag_kv_reuse.md` | ## 3. Findings — finding 4 (intra-agent reuse is partial) | Part A §5.3 | dropped (duplicate of Part A §5.3) |
| `weekly_progress/092326/dag_kv_reuse.md` | ## 3. Findings — finding 5 (the handoff removes two thirds of the rounds) | Part A §5.6 | dropped (duplicate of Part A §5.6) |
| `weekly_progress/092326/dag_kv_reuse.md` | ## 3. Findings — finding 6 (negative control clean; summary fails) | Part A §5.6 | dropped (duplicate of Part A §5.6) |
| `weekly_progress/092326/dag_kv_reuse.md` | ## 3. Findings — finding 7 (cost side negative; ceiling behavioural) | Part A §5.6 | dropped (duplicate of Part A §5.6) |
| `weekly_progress/092326/dag_kv_reuse.md` | ## 3. Findings — finding 8 (the effect survives the real harness) | Part A §5.7 | dropped (duplicate of Part A §5.7, including its "Sixteen W5 team runs") |
| `weekly_progress/092326/dag_kv_reuse.md` | ## 4. Where this leaves the argument — paragraphs 1, 3, 4 | Part A §6 | dropped (duplicate of Part A §6) |
| `weekly_progress/092326/dag_kv_reuse.md` | ## 4. Where this leaves the argument — paragraph 2 | Part A, A.W (was §4, second paragraph) | kept |
| `weekly_progress/092326/dag_kv_reuse.md` | ## 5. Next steps | Part A, A.W (was §5) | kept (feeds slides 13–14 of the 2026-09-23 deck) |
| `weekly_progress/092326/dag_kv_reuse.md` | ## 6. Housekeeping | Part A, A.W (was §6) | kept; status note added |
| `s15_integrated_harness/dag_census_profile.md` | # title: Does the Lead maintain a dependency graph, or is the task board just a list? | Part B (heading) | kept (H1 became the Part B heading) |
| `s15_integrated_harness/dag_census_profile.md` | preamble (2026-09-17, tooling, traces, companion, terminology) | Part B, preamble | kept |
| `s15_integrated_harness/dag_census_profile.md` | ## 1. The question | Part B §1 | kept |
| `s15_integrated_harness/dag_census_profile.md` | ## 2. Why the existing corpus could not answer it | Part B §2 | kept |
| `s15_integrated_harness/dag_census_profile.md` | ### 2.1 The retrospective census | Part B §2.1 | kept; cleanup note added ("133 tasks and 50 edges") |
| `s15_integrated_harness/dag_census_profile.md` | ### 2.2 The word that explains the zero | Part B §2.2 | kept |
| `s15_integrated_harness/dag_census_profile.md` | ### 2.3 What this leaves | Part B §2.3 | kept |
| `s15_integrated_harness/dag_census_profile.md` | ## 3. Design | Part B §3 | kept |
| `s15_integrated_harness/dag_census_profile.md` | ### 3.1 Twelve workloads | Part B §3.1 | kept |
| `s15_integrated_harness/dag_census_profile.md` | ### 3.2 The neutral frame | Part B §3.2 | kept |
| `s15_integrated_harness/dag_census_profile.md` | ### 3.3 Measures | Part B §3.3 | kept |
| `s15_integrated_harness/dag_census_profile.md` | ## 4. Results | Part B §4 | kept |
| `s15_integrated_harness/dag_census_profile.md` | ### 4.1 The Lead builds the graph, and builds it exactly right | Part B §4.1 | kept |
| `s15_integrated_harness/dag_census_profile.md` | ### 4.2 The specificity control | Part B §4.2 | kept |
| `s15_integrated_harness/dag_census_profile.md` | ### 4.3 Where the ordering lives | Part B §4.3 | kept |
| `s15_integrated_harness/dag_census_profile.md` | ### 4.4 The work came out right | Part B §4.4 | kept |
| `s15_integrated_harness/dag_census_profile.md` | ### 4.5 Cost | Part B §4.5 | kept |
| `s15_integrated_harness/dag_census_profile.md` | ## 5. What this means for the tiered-memory argument | Part B §5 | kept |
| `s15_integrated_harness/dag_census_profile.md` | ## 6. Threats and limits | Part B §6 | kept |
| `s15_integrated_harness/dag_census_profile.md` | ## 7. Reproduction | Part B §7 | kept |
| `weekly_progress/092326/dag_census.md` | # title + header paragraph + terminology line | Part B (heading), preamble | dropped (duplicate of Part B heading and preamble) |
| `weekly_progress/092326/dag_census.md` | ## 1. Questions | Part B §1, §3.3 | dropped (duplicate of Part B §1 and §3.3) |
| `weekly_progress/092326/dag_census.md` | ## 2. What was run | Part B §3.1, §3.3, §4 (opening paragraph) | dropped (duplicate of Part B §3.1, §3.3 and §4) |
| `weekly_progress/092326/dag_census.md` | ## 3. Findings — finding 1 (the board is built exactly right) | Part B §4.1 | dropped (duplicate of Part B §4.1) |
| `weekly_progress/092326/dag_census.md` | ## 3. Findings — finding 2 (left empty when it should be) | Part B §4.2 | dropped (duplicate of Part B §4.2) |
| `weekly_progress/092326/dag_census.md` | ## 3. Findings — finding 3 (corrects last week's conclusion) | Part B §4.1 (last paragraph) | dropped (duplicate of Part B §4.1) |
| `weekly_progress/092326/dag_census.md` | ## 3. Findings — finding 4 (one word explains the 35 empty boards) | Part B §2.1–2.2 | dropped (duplicate of Part B §2.1–2.2) |
| `weekly_progress/092326/dag_census.md` | ## 3. Findings — finding 5 (the whole graph exists before any work starts) | Part B §4.3 | dropped (duplicate of Part B §4.3) |
| `weekly_progress/092326/dag_census.md` | ## 3. Findings — finding 6 (executed, and the work was correct) | Part B §4.3–4.4 | dropped (duplicate of Part B §4.3–4.4) |
| `weekly_progress/092326/dag_census.md` | ## 3. Findings — finding 7 (cross-provider) | Part B §4.5 | dropped (duplicate of Part B §4.5) |
| `weekly_progress/092326/dag_census.md` | ## 4. Where this leaves the argument | Part B §5 | dropped (duplicate of Part B §5) |
| `weekly_progress/092326/dag_census.md` | ## 5. Next steps | Part B, B.W (was §5) | kept |
| `weekly_progress/092326/dag_census.md` | ## 6. Housekeeping — paragraph 1; "Corrections to older documents" bullets 1–2 | Part B, B.W (was §6) | kept; status and done notes added |
| `weekly_progress/092326/dag_census.md` | ## 6. Housekeeping — "Corrections to older documents" bullets 3–4 (--model; tool_latency exclusion) | Part B §6 (last bullet), §2.1 | dropped (duplicate of Part B §6 and §2.1) |

## Cleanup notes

- **Part A §2.2 — pointer and flag**: contradicted by Part B §4.1 (L0 prompts gave complete graphs in 21
  of 21 DEP runs); no L0 or L1 probe trace is committed, only the three L2 gate probes.
- **Part A §5.1 — pointer**: "Only at instruction level L2" refers to the §2.2 note.
- **Part A §5.3 — flagged**: "20 edges" is not in any committed table; `dag_tables.md` Table A sums to 22
  edges (19 executed) over the nine runs, and `dag_census.py` on the same folder reports 22.
- **Part A §5.7 — flagged**: "Sixteen W5 runs … four repetitions". The folder holds 24 traces (8 + 16,
  r1–r2 run twice), `prewarm_analyze.py` pools all 24, and the tables are over six runs per arm; the two
  quoted `[prewarm]` lines come from the first batch. The dropped weekly finding 8 ("Sixteen W5 team
  runs") and the footer of slide 5 of the 2026-09-23 deck ("four repetitions") repeat the claim, while
  the weekly note's own §0 gives the real-harness arm size as "n=6/arm", which matches the 24-run pooling.
- **Part A §6 — flagged**: "across sixteen runs", same issue, pointer to §5.7.
- **A.W (was the header paragraph) — corrected** "four dependency-shaped workloads" to "five"
  [corrected 2026-09-23]; the same note's §0 and §2 say five, and the data have W1–W5.
- **A.W (was §6) and B.W (was §6) — status notes**: "Untracked and ready to commit" is stale; both were
  committed in f17e2a4.
- **B.W, "Corrections to older documents" bullet 1 — note**: the requested pointer now exists (Part A §2.2,
  §5.1).
- **A.W and B.W — finding maps**: both weekly notes number their findings (1–8, 1–7); the findings were
  dropped as duplicates, and a note at the top of each Additions section maps every number to its section.
- **Part B §2.1 — flagged**: "133 tasks and 50 edges" for the excluded tool-latency probe traces mixes a
  per-run task count (133) with a two-run edge count (50 = 2 × 25).
- **Dropped digest blocks** (duplicates of the bases, listed in the source map): from `dag_kv_reuse.md` the
  title, terminology line, most of §0, §1, most of §2, all eight findings of §3 and three paragraphs of §4;
  from `dag_census.md` the title and header, §1–§4 (all seven findings) and two of the four "Corrections to
  older documents". Numbers that only these blocks carried were rounded forms of base values: +0.27 / +0.68
  and +37 points (Part A §5.4 keeps [+0.273, +0.676] and +0.372), p = 0.68 (0.675), 34k and 78 s (§5.5 keeps
  34,326 tokens and 78.1 s), and −70% wall (§5.6: 70% of the wall clock, 43.9 of 62.4 s).
