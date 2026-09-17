# The Lead does maintain a dependency graph -- last week's empty boards were the prompt's doing

Weekly progress, 2026-09-23. Full report: `s15_integrated_harness/dag_census_profile.md`.
Tooling: `s15_integrated_harness/scripts/census_workloads.py` (twelve workloads across six task
categories x two latent structures, plus the contamination gate), `scripts/dag_census.py` (board
reconstruction, reference-graph matching, the "where did the ordering go" measures),
`scripts/census_score.py` (re-runs each archived sandbox's own tests). Traces:
`s15_integrated_harness/traces/dag_census/` and `traces/dag_census_ds/`. Providers: z.ai
`glm-5.3-flash` and DeepSeek `deepseek-v4-flash`. Companion note: `dag_kv_reuse.md`, whose
finding on instruction levels this corrects.

Terminology as in the 2026-09-09 deck: a *turn* is one Lead activation, a *round* is one agent-loop
iteration.

## 1. Questions

1. Across task categories -- coding, mathematics, file Q&A and others -- does the Lead maintain a
   dependency graph on the task board, or does it leave `blockedBy` empty and keep a flat list?
2. If the graph is empty, where does the ordering go instead, and could a scheduler still recover it?
3. Is the answer a property of the harness or of the model behind the Lead?

The constraint that makes this a measurement: **nothing may ask the Lead for an edge.** Edge
emission is the dependent variable, so no prompt names `update_task`, `addBlockedBy`, "dependency"
or "blocked", and the harness is unmodified. `census_workloads.py --check-prompts` refuses to run
otherwise, and it is the first step of the reproduction.

## 2. What was run

Six categories x {DEP, FLAT} x reps, on two providers. DEP work genuinely has stages; FLAT work
genuinely does not, and is the specificity control -- without it, zero edges on DEP cannot be told
apart from a Lead that never emits edges at all.

| category | DEP variant | FLAT variant | shape |
|---|---|---|---|
| FQA | glossary definitions -> locate in `ARCHITECTURE.md` -> reconciliation note | three independent questions over three modules | chain |
| CODE | spec checklist -> implement `solution.py` -> run tests and fix | three independent bench problems | chain |
| MATH | count S -> sum S -> that sum mod 7 | three independent AIME problems | chain |
| REFACTOR | inventory call sites -> rename -> run tests | three independent read-only audits | chain |
| DATA | per-file counts -> totals and percentages -> ranking | three independent per-file counts | chain |
| RESEARCH | three module surveys -> one synthesis consuming all three | three independent summaries | fan-in |

Each DEP workload ships a hand-authored reference graph; tasks are matched to items by token
overlap and the match quality is reported so a zero recall can never be confused with a bad match.
Final corpus: **44 usable runs** (26 DeepSeek, 18 glm), plus 10 DeepSeek runs the provider refused
and the analyzer excludes by name. Both sweeps were stopped deliberately once every cell was giving
the same answer, so the glm arm covers 18 of a planned 36 cells.

## 3. Findings

**1. The board is built, and built exactly right.** Every DEP run on both providers reproduced its
reference graph with **edge recall 1.00 and zero false edges** -- 21 of 21 runs, 45 of 45 edges:
13 DeepSeek runs (28 edges) and 8 glm runs (17 edges), across all six categories. The three-predecessor fan-in is emitted as a
single `update_task` call carrying all three blockers, which is why `update_task` calls (39) run
below edges (45).

**2. And left empty when it should be.** Zero edges in **23 of 23** FLAT runs. The Lead relates
items when the work relates them and not otherwise: perfect separation on a 21-vs-23 split,
**p = 5.0e-13** on a one-sided Fisher exact test. A length confound is ruled out by the matrix
itself: `CODE-FLAT` is the longest prompt of all twelve (1,876 chars) and emits nothing, while the
shorter `CODE-DEP` (1,254) emits two.

**3. This corrects last week's conclusion.** `dag_kv_reuse.md` reported that the Lead "does not emit
edges unless the prompt names the mechanism", and that `blockedBy` was empty in all 31 team runs
with byte-level data. The prompts here are L0 by that report's own definition -- narrative order
only, mechanism never named -- and they produce complete graphs. The earlier L0 arm used the W1/W4
shapes (six items over two branches, one a crossed placebo) where the chain is far less salient, and
carried a team block explaining that `spawn_teammate` refuses blocked tasks, which reads as "do not
create blocked items". Which of the two mattered needs a direct re-run; the blanket claim does not
hold.

**4. One word explains the 35 empty boards.** Every earlier "natural" workload told the Lead its
items were independent -- `latency_workloads.py:40` and `redundancy_workloads.py:37, 50, 62, 76` all
literally begin *"Create three independent task-board items"* -- and they were: three unrelated
documents, three unrelated bench problems, three unrelated AIME problems. An empty `blockedBy` is
the **correct** board for that work. Those 59 runs (35 with a board, 133 tasks, 0 edges, 0
`update_task` calls) were never evidence about whether a Lead maintains a graph.

**5. The whole graph exists before any work starts.** Creation waves 1.0, tasks created after the
first completion 0.0, spawns after the first completion 0.0 -- in every run of both arms. The Lead
never withheld a successor to create it later and never delayed a spawn to enforce an order. So
staged creation and spawn gating are not how ordering is carried; the edges are, backed partly by
prose in the descriptions (0.25-1.00 of DEP tasks). A scheduler reading a board snapshot at spawn
time therefore sees the complete graph.

**6. The graphs were executed, and the work was correct.** Zero order violations across all DEP
runs: no successor was claimed before its predecessor completed. Every task in every run was claimed
and completed. `census_score.py` re-ran each archived sandbox's own tests: **10 of 10 attempted
sandboxes passed**, including REFACTOR-DEP, whose tests only go green if all 7 call sites moved.

**7. Cross-provider.** Both providers behave identically on board shape (recall 1.00, FLAT 0). They
differ 4x in cost: DeepSeek 118 s mean per DEP run against glm 488 s. That is a provider-and-model
difference together, not a model property.

## 4. Where this leaves the argument

- The board **is** a usable routing signal. Last week's report set the dependency graph aside partly
  because it was believed absent unless demanded; it is not absent, and it is available early enough
  (before the first teammate starts) to compute a retention directive at spawn time.
- This does **not** by itself resurrect DAG-directed retention. Last week's separate finding that
  *broadcast beats the graph* (all-earlier 85.6% byte recall against direct-predecessors 36.0%)
  measured what successors read, and nothing here touches it. What changes is the premise: that
  comparison now has to be re-run on boards that actually carry edges, rather than on boards whose
  edges had to be demanded into existence.
- Any future claim about what Leads do with the board must quote the prompt that produced it. One
  adjective moved 35 runs.

## 5. Next steps

1. **Re-run the prefetch-policy comparison on unprompted boards.** `dag_redundancy.py`'s policy
   table (direct_pred 36.0% vs all_earlier 85.6% byte recall) was computed on L2-forced boards from
   the W1-W5 shapes. Repeat it over `traces/dag_census/` DEP runs, where the graph is the Lead's own
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

## 6. Housekeeping

Untracked and ready to commit: `s15_integrated_harness/scripts/census_workloads.py`,
`scripts/dag_census.py`, `scripts/census_score.py`, `scripts/census_bench/refactor/`,
`s15_integrated_harness/dag_census_profile.md`, this note, and the two trace directories
`traces/dag_census/`, `traces/dag_census_ds/`, `traces/dag_census_smoke/` and
`traces/dag_census_partial/` (one glm run killed at shutdown; it wrote a `profile_end` while
terminating, so a truncated run is **not** detectable from `status` alone and must be quarantined
by hand).

Corrections to older documents:

- `dag_kv_reuse_profile.md` section 2.2 and `092326/dag_kv_reuse.md` ("the Lead does not emit edges
  unless the prompt names the mechanism") are contradicted by finding 1 and should carry a pointer
  to this note.
- `dag_workloads.py:8` ("`blockedBy` stayed empty in all 31 team runs") is factually right but
  misleading without finding 4: those workloads declared their items independent.
- **`--model` on `dag_workloads.py` does not work.** `code.py:76` calls `load_dotenv(override=True)`,
  so `.env` overwrites any `MODEL_ID` set in the child environment -- verified by direct test.
  Cross-model runs need a lane with its own `.env`. No published result is affected: no shipped
  trace label carries a model tag, so the flag appears never to have been used.
- `traces/tool_latency/` must be excluded from any board census: its 133 tasks and 50 edges are
  written by `tool_latency_probe.py:376-384` calling the task tools directly from Python, not by a
  model.
