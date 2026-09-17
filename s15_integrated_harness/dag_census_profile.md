# Does the Lead maintain a dependency graph, or is the task board just a list?

Profiling report, 2026-09-17. Weekly note: `weekly_progress/092326/dag_census.md`.
Tooling: `scripts/census_workloads.py` (twelve workloads across six task categories x two latent
structures, plus the contamination check that keeps the measurement honest), `scripts/dag_census.py`
(board reconstruction, reference-graph matching, the "where did the ordering go" measures),
`scripts/census_score.py` (re-runs each archived sandbox's own tests).
Traces: `traces/dag_census/` (z.ai `glm-5.3-flash`), `traces/dag_census_ds/` (DeepSeek
`deepseek-v4-flash`), `traces/dag_census_smoke/` (the two pilot runs).
Companion: `dag_kv_reuse_profile.md` (2026-09-16), whose section 2.2 this report corrects.

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
| one interactive session, Qwen3.8-27B (`traces/run_20260902T004901_584576Z_dc6a9685.jsonl`) | 1 | 1 | 57 | 96 | 1 | 39 |

Excluded as script-synthesized rather than model-driven: `traces/tool_latency/`, whose 133 tasks and
50 edges are created by `scripts/tool_latency_probe.py:376-384` calling `create_task` / `update_task`
directly from Python. Its task subjects are literally `probe anchor` and `probe dep a 0`. Counting
them would have inflated the "Leads do build graphs" side with rows no model ever produced.

### 2.2 The word that explains the zero

The 35 natural runs did not have empty boards because the Lead refused to relate their items. They
had empty boards because **their prompts declared the items independent, and the items were**:

- `scripts/latency_workloads.py:40` — *"Create three **independent** task-board items, one per item
  below, and delegate each to its own teammate."* (all FQA / CODE / MATH runs)
- `scripts/redundancy_workloads.py:37, 50, 62, 76` — *"Create three **independent** task-board
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

The REFACTOR fixture (`scripts/census_bench/refactor/`) is new: `pkg.py` defines `normalize_key`,
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
dependencies, and spawn the teammates now"* (`scripts/dag_workloads.py:390`), which would have
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
  `traces/dag_census_partial/`. It wrote a `profile_end` while terminating, so it would otherwise
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
python3 s15_integrated_harness/scripts/census_workloads.py --check-prompts

# 1. lanes (the cross-provider arm needs its own .env; --model does not work, see section 6)
rsync -a --delete --exclude .git --exclude 's15_integrated_harness/traces' \
      --exclude profiling_sandbox --exclude __pycache__ --exclude '.memory' --exclude '.tasks*' \
      --exclude .mailboxes --exclude .transcripts --exclude .task_outputs --exclude .worktrees \
      /home/yq335/learn-claude-code/ /home/yq335/lanes/census/
#    lanes/census_ds/.env: MODEL_ID=deepseek-v4-flash
#                          ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
#                          ANTHROPIC_API_KEY=<the DeepSeek key>

# 2. the two arms (sequential within a provider, concurrent across providers)
python3 s15_integrated_harness/scripts/census_workloads.py --reps 3 --provider-tag glm \
    --repo /home/yq335/lanes/census    --trace-dir s15_integrated_harness/traces/dag_census
python3 s15_integrated_harness/scripts/census_workloads.py --reps 3 --provider-tag ds \
    --repo /home/yq335/lanes/census_ds --trace-dir s15_integrated_harness/traces/dag_census_ds

# 3. tables, and the sandbox scores
python3 s15_integrated_harness/scripts/dag_census.py \
    s15_integrated_harness/traces/dag_census s15_integrated_harness/traces/dag_census_ds --per-run
python3 s15_integrated_harness/scripts/census_score.py s15_integrated_harness/traces/dag_census_ds

# 4. detector check -- the analyzer must find edges where they exist and none where they do not
python3 s15_integrated_harness/scripts/dag_census.py s15_integrated_harness/traces/dag_redundancy
#   -> 9 runs, 22 edges
python3 s15_integrated_harness/scripts/dag_census.py s15_integrated_harness/traces/latency_profiling
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
