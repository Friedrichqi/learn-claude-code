# 08 · Context inheritance between dependent tasks

> **Status:** complete — results final; Part B's eviction-policy table was corrected on 2026-09-23; the live arm/budget/policy sweep on lm-eval tasks (P4/P5) was never run, because G1 found no lm-eval task on which the lead builds a board. · **Dates:** runs 2026-09-17 → 2026-09-20 UTC (scripted and harness stages 09-17/18, lm-eval census 09-18, codebase census 09-19, codebase arm sweep 09-20); Part A is headed 2026-09-23 (committed 2026-09-21), Part B 2026-09-18 (it also reports the 09-19/20 codebase runs), Part C is undated (committed 2026-09-21). · **Models/providers:** every live run is z.ai `glm-5.3-flash` through its Anthropic-messages endpoint; the replay layer reads published trajectories — `nvidia/Open-SWE-Traces` v1.1 (SWE-agent, OpenHands, mini-SWE-agent) and `agent-evals/hal_traces` (τ-bench airline under claude-3.7-sonnet, claude-opus-4.1, deepseek-v3, gemini-2.0-flash-001 and gpt-4.1; GAIA under o3-mini; CORE-bench under DeepSeek-V3-0324); lm-evaluation-harness 0.4.13 renders and grades. · **Commits:** live traces record `git_head` f17e2a4; data, scripts and all three writeups committed in aded0d0 (2026-09-21); eviction-policy correction in 0b65c46 (2026-09-23); the pre-cleanup tree is tag `pre-cleanup-2026-09-23`. · **Presented:** 092326 deck, slides 6–10 — s6: Part A §3b + `research/08_context_inheritance/data/inherit/inherit_tables.md`; s7: Part B §3 Metric 3 (corrected) + `research/08_context_inheritance/data/realworld/crossgrid_{hal,open_swe}.md` + Part A §4b (F2); s8: Part B §3 Metrics 1 and 4; s9: Part B §3 G1, G2, G2b + `research/08_context_inheritance/data/codebase/`; s10: Part B §3 E1-L + `weekly_progress/092326/conclusion.md`. The deck's conclusion and next steps (s13–s14) also draw on both notes.

**Contents**
- [Part A — How far back up the dependency graph is it worth reaching?](#part-a--how-far-back-up-the-dependency-graph-is-it-worth-reaching) — none vs `dag` vs `ancestors` on hand-authored workloads: scripted stage (§3), real harness (§3b), F2 budget sweep (§4b), F1 coverage sweep (§4c), the rule (§4d)
- [Part B — Do the inheritance arms survive contact with real harnesses and real datasets?](#part-b--do-the-inheritance-arms-survive-contact-with-real-harnesses-and-real-datasets) — Open-SWE-Traces and HAL replay (§3 Metrics 1–5), eviction-policy grid (Metric 3, corrected 2026-09-23), lm-eval census (G1), live codebase runs (G2, G2b, E1-L)
- [Part C — Real-world inheritance profiling — pipeline and reproduction](#part-c--real-world-inheritance-profiling--pipeline-and-reproduction) — Part B's modules, design decisions, environment and reproduction commands; no results
- [Data inventory](#data-inventory)
- [Source map](#source-map)
- [Cleanup notes](#cleanup-notes)

**Files in this folder**

| path | purpose |
|---|---|
| `research/08_context_inheritance/README.md` | this document |
| `research/08_context_inheritance/inherit_loop.py` | Part A §3 scripted stage: one agent with `read_file` / `glob` / `grep` over this repo, three arms; writes `stage1.jsonl`; calls the provider |
| `research/08_context_inheritance/inherit_workloads.py` | Part A §3b: the FAB-DEEP and RW-FQA team workloads and grader, one harness run per cell; calls the provider |
| `research/08_context_inheritance/inherit_analyze.py` | Part A metrics 1–5 → `inherit_tables.md`, `inherit_rows.json`; `--coverage` adds the F1 block (`coverage_tables.md`); offline |
| `research/08_context_inheritance/coverage_sweep.py` | Part A §4c F1 coverage sweep → `coverage.jsonl`; its `check()` gate refuses a task set whose facts are not window-unique; calls the provider |
| `research/08_context_inheritance/budget_sweep.py` | Part A §4b F2 budget × ordering sweep over untreated successors → `budget_sweep.md` / `.json`; offline |
| `research/08_context_inheritance/traj_ingest.py` | Parts B/C: HF trajectory corpora → canonical read-item traces; `read_key` defines "a read"; streams from HF |
| `research/08_context_inheritance/hal_ingest.py` | Parts B/C: HAL run archives → the same form, plus measured per-call latency |
| `research/08_context_inheritance/replay_sweep.py` | Parts B/C: E1-R arms, cross-task, and the budget × policy grid (E2-R / E3-R); offline |
| `research/08_context_inheritance/lmeval_agent_model.py` | Part B §2: registers `s15-agent` as an lm-eval model — one task prompt → one agentic session → its final message |
| `research/08_context_inheritance/lmeval_workloads.py` | Part B G1 census; also takes `--arms`, `--evict`, `--budgets` for the live sweep that never ran; calls the provider |
| `research/08_context_inheritance/codebase_workloads.py` | Part B G2 / G2b / E1-L: EXPLAIN and MODIFY over lab-group-56; calls the provider |
| `research/common/profile_run.py` (shared) | runs one harness session and writes the trace and its sidecars; `--prewarm none\|dag\|ancestors`, `--prewarm-evict`, `--prewarm-budget`, `--prewarm-dump`, the sandbox flags |
| `research/common/evict_policies.py` (shared) | the 16 eviction policies and measured recovery cost, used by the replay and the live injector |
| `research/common/test_evict_policies.py` (shared) | policy invariants (pytest, offline) |
| `research/common/input_redundancy.py` (shared) | `classify_bash` and the trace readers the analyzers share |
| `research/08_context_inheritance/data/inherit/` | Part A: scripted trials, 18 harness runs, F1 and F2 results |
| `research/08_context_inheritance/data/realworld/` | Part B replay: ingested corpora and result tables |
| `research/08_context_inheritance/data/lmeval/` | Part B G1 census |
| `research/08_context_inheritance/data/lmeval_smoke/` | Part B §2 end-to-end check of the lm-eval endpoint |
| `research/08_context_inheritance/data/codebase/` | Part B G2, G2b, E1-L |
| elsewhere | F2 also reads `research/07_task_dag/data/dag_redundancy/`; the Qwen reruns of the inherit workloads (`research/09_efficiency/qwen_inherit.sbatch`, `research/09_efficiency/inherit_trace_stats.py`, `research/09_efficiency/data/latency_profiling_qwen_opt/inherit/`) belong to topic 09 |

---

# Part A — How far back up the dependency graph is it worth reaching?

_Formerly `weekly_progress/092326/inheritance_depth.md` (headed 2026-09-23; committed 2026-09-21 in aded0d0)._

Weekly progress, 2026-09-23. Full tables: `research/08_context_inheritance/data/inherit/inherit_tables.md`.
Tooling: `research/08_context_inheritance/inherit_workloads.py` (the two team workloads and the
grader), `research/08_context_inheritance/inherit_loop.py` (the scripted stage), `research/08_context_inheritance/inherit_analyze.py` (all five
metrics), and a third injection arm `--prewarm ancestors` added to `research/common/profile_run.py`.
Traces: `research/08_context_inheritance/data/inherit/`. Provider: z.ai `glm-5.3-flash`.
Companion notes: `research/07_task_dag/README.md` Part A (which established that handing a successor its predecessor's
bytes removes rounds) and `research/07_task_dag/README.md` Part B (which established that the Lead builds the graph itself
on genuinely staged work).

Terminology as in the 2026-09-09 deck: a *turn* is one lead activation, a *round* is one agent-loop
iteration.

## 1. The question

`research/07_task_dag/README.md` Part A showed that handing a successor the content its **direct** predecessors read
removes about two rounds of five. It never asked how far back to reach. This note tests three
policies against each other:

| arm | what the successor is handed when it claims a blocked task |
|---|---|
| **none** | nothing — the baseline |
| **dag** | the reads of its **direct** predecessors |
| **ancestors** | the reads of its whole **transitive closure** — predecessors of predecessors too |

**The two treatment arms can only differ at depth 3 or more.** In a chain A → B → C the direct
predecessors of C are {B}; its ancestors are {A, B}. Every workload here is therefore built so the
final task genuinely needs content its direct predecessor never read — otherwise the third arm is
the second arm with extra steps.

## 2. Setup

**Two team workloads**, each run under all three arms:

- **RW-FQA** — real-world, depth 3, *edges emitted by the Lead unprompted*. The FQA-DEP chain from
  the 2026-09-17 census, reused verbatim: read GLOSSARY.md → locate the same terms in
  ARCHITECTURE.md → reconcile the two. The reconciliation needs **both** documents, but its direct
  predecessor only ever read ARCHITECTURE.md.
- **FAB-DEEP** — fabricated, depth 4 with a fan-in, edges stated explicitly in the prompt. Deeper
  and wider than anything the Lead produces naturally, because the point here is to profile
  inheritance rather than to measure emission; stating the edges removes emission variance from the
  measurement. Four documents disagree about the compaction pipeline and the last task reconciles
  all four:

```
#1 GLOSSARY.md ──► #2 ARCHITECTURE.md ──► #3 DESIGN.md ──┐
                                                         ├──► #5 reconcile all four
#4 README.md ────────────────────────────────────────────┘
#6 standalone (no edges, the within-run control)
```

  For #5 the direct predecessors are {#3, #4} — two of the four documents; the ancestors are
  {#1, #2, #3, #4} — all four. The graded answer needs one fact from each document, so the arms are
  separable by construction: a baseline must find four documents, `dag` two, `ancestors` none.

**A scripted stage** carries the statistics. One agent, real tools against this repository, shaped
like the last task of a three-stage chain: the question always needs a fact from the document the
*grand*-predecessor read **and** a fact from the document the *direct* predecessor read, and neither
path is ever named. Facts were verified to be split — every graded fact lives in exactly one of the
two windows — so the score decomposes into "the half `dag` was handed" and "the half only
`ancestors` was handed". That split is where the arms must separate; the total can hide it.
[cleanup note: the check that verified this split — `inherit_check.py`, named in `inherit_loop.py` before the cleanup — was never committed, so the split cannot be re-verified from the repo; F1's equivalent gate is committed as `check()` in `research/08_context_inheritance/coverage_sweep.py`]

**Faithfulness.** Injected content is the exact assistant `tool_use` / user `tool_result` pair a
real `read_file` would have produced, built through the harness's own `run_read`, ordered
oldest-ancestor-first, and rebuilt byte-identically on every call so the prefix stays stable.
Content the successor already holds from its own earlier work is skipped rather than re-sent.

## 3. Results — the scripted stage (96 trials, 32 per arm)

| arm | rounds | read_file | locate | wall s | prompt tok | accuracy | GRAND half | DIRECT half |
|---|---|---|---|---|---|---|---|---|
| none (baseline) | 8.00 (sd 2.27) | 3.31 | 8.12 | 68.2 | 25,183 | 88% | 84% | 91% |
| **dag** (direct preds) | **6.47** (sd 2.64) | 1.31 | 6.00 | 42.3 | 10,522 | 93% | 86% | 100% |
| **ancestors** (closure) | **1.50** (sd 2.14) | 0.06 | 0.47 | 12.5 | 828 | 99% | 98% | 100% |

### Metric 2 — rounds reduced, and where the value sits

| arm | Δ rounds vs baseline | 95% CI | share of the baseline's rounds |
|---|---|---|---|
| dag | **-1.53** | [-2.75, -0.34] | 19% |
| ancestors | **-6.50** | [-7.50, -5.34] | 81% |
| **ancestors vs dag** | **-4.97** | **[-6.06, -3.72]** | the marginal value of reaching past the direct predecessor |

**The second hop is worth 3.2x the first.** Going from nothing to the direct predecessor removes 1.5
of 8 rounds; going from the direct predecessor to the full closure removes another 5.0. That is the
opposite of a diminishing return, and it has a mechanical cause: **rounds are quantised.** A
successor that still has to find *anything* pays the same locate-and-read trip whether it is missing
one document or two, so a handoff that closes half the gap buys almost none of the latency. Only
closing the gap completely collapses the task to a single round.

### Metric 3 — end-to-end latency and its breakdown

| arm | wall s | model s | tool s | Δ wall | 95% CI |
|---|---|---|---|---|---|
| none | 68.2 | 68.0 | 0.17 | — | — |
| dag | 42.3 | 42.2 | 0.10 | **-25.9** | [-40.5, -14.2] |
| ancestors | 12.5 | 12.5 | 0.01 | **-55.7** | [-70.0, -43.9] |

Tool execution is 0.2% of wall; this is an agent waiting on model calls. Priced with the measured
constants (3.72 s fixed per call, 17.44 ms per output token, 0.033 ms per uncached prompt token):

| arm | rounds removed | = s of fixed cost | output tokens saved | = s of decode | prefill s | modelled | measured |
|---|---|---|---|---|---|---|---|
| dag | 1.53 | 5.7 | 841 | 14.7 | 0.5 | 20.8 | 25.9 |
| ancestors | 6.50 | 24.2 | 1,378 | 24.0 | 0.8 | 49.0 | 55.7 |

The constants account for ~88% of the measured saving. **Prefill is 1.6% of it**; the saving is round
count and the decode that goes with it, roughly half each. A successor that searches also *writes* --
it narrates each hop -- so removing the search removes the narration too.

### Metric 4 — accuracy, split into the half each arm was handed

This is the measurement that shows what the extra hop actually buys. Every graded fact lives in
exactly one of the two windows, so the score splits cleanly:

| arm | DIRECT half (what `dag` was handed) | GRAND half (what only `ancestors` was handed) |
|---|---|---|
| none | 91% | 84% |
| dag | **100%** | 86% |
| ancestors | **100%** | **98%** |

`dag` closes the half it was given (91% → 100%) and does **nothing measurable** for the half it was
not (84% → 86%). `ancestors` closes both. The ancestors-vs-dag difference is +12 points on the GRAND
half (95% CI [+5, +20]) and exactly 0 on the DIRECT half, where both are already at ceiling.

Accuracy never dropped: no arm is trading correctness for speed, and the fuller handoff is the most
accurate of the three.

### Metric 5 — how much of the prompt is inherited content

| arm | injected tok | share of the **opening** prompt | share of **all** prompt tokens processed | total prompt tok |
|---|---|---|---|---|
| none | 0 | 0% | 0% | 25,183 |
| dag | 349 | 73.1% | 16.4% | 10,522 |
| ancestors | 728 | 85.0% | 55.9% | 828 |

Two readings, because they say different things. The inherited block dominates the *opening* prompt
in both treatment arms — that is simply what a handoff is. But as a share of everything the trial
ever processes it is 16% for `dag` and 56% for `ancestors`, and that inversion is the point:
**the fuller handoff makes the whole trial 30x cheaper in prompt tokens** (25,183 → 828), because
the tokens it saves are the tool results of a search that never happens. Handing over 728 tokens
avoids sending 24,355.

(The provider reports `input_tokens` excluding cached tokens, so the opening-prompt share is composed
exactly from system + question + injected bytes rather than taken from `usage`; a cache hit otherwise
reports 20 tokens for a 1,428-token prompt.)

### Per task

| arm | T1 | T2 | T3 | T4 |
|---|---|---|---|---|
| none | 8.0 / 94% | 7.9 / 84% | 8.1 / 72% | 8.0 / 100% |
| dag | 6.4 / 97% | 6.4 / 100% | 7.9 / 75% | 5.2 / 100% |
| ancestors | 1.0 / 100% | 1.0 / 100% | 2.9 / 97% | 1.1 / 100% |

Three of the four tasks collapse to a single round under full inheritance. T3 is the exception at 2.9
rounds and it is also the task `dag` helps least (7.9 against a baseline 8.1) -- its direct window is
the README, the shortest and least specific of the four documents.

## 3b. Results — the real harness (18 runs, 15 successor tasks per arm)

All 18 runs executed every edge they created (3.0 edges created, 3.0 executed, in all arms). RW-FQA's
edges were emitted by the Lead unprompted, as the census predicted.
[cleanup note: this stage's `dag` and `ancestors` arms were handed less than metric 1 credits them with — `profile_run.py` recorded predecessors' `bash` reads but injected only `read_file` ones, "~45% less than the recall denominator credited them with" (Part B §6, Housekeeping); found and fixed after these runs, and carried as a correction on 092326 deck slide 6]

### Metric 1 — byte-level recall, measured on the untreated arm

This has to be measured on the baseline, because a treated successor does not read — that is the
point of treating it. For the 15 baseline successors that read anything, the share of *their* read
bytes each policy would already have held:

| policy | byte recall | round recall | precision (used/sent) | sent KB median |
|---|---|---|---|---|
| direct predecessors (`dag`) | **24.2%** | 15/63 (**23.8%**) | 29.9% | 23.8 |
| transitive closure (`ancestors`) | **39.2%** | 18/63 (**28.6%**) | 30.8% | 53.6 |
| every earlier task (reference) | 48.2% | 28/63 (44.4%) | 27.1% | 60.1 |
| oracle (ceiling) | 100.0% | 61/63 (96.8%) | 100.0% | 35.6 |

**This is the central result of the note.** Reaching past the direct predecessor raises byte recall by
**+15 points** (24.2% → 39.2%) and round recall by only **+4.8** (23.8% → 28.6%). Bytes accumulate
smoothly as you walk up the graph; rounds do not, because a round only disappears if *everything* it
would have fetched was already held. At 39% coverage the successor still has to go and look, and
having twice as many of the right bytes does not save it the trip.

### Metrics 2 and 3 — rounds and latency: directionally right, statistically absent

| arm | successors | rounds | reads | span s | model s | prompt tok | inherited share of prompt |
|---|---|---|---|---|---|---|---|
| none | 15 | 7.87 | 9.47 | 149.0 | 139.4 | 27,251 | 0% |
| dag | 15 | 8.27 | 10.00 | 101.3 | 101.1 | 45,218 | 26.1% |
| ancestors | 15 | 6.53 | 6.53 | 125.5 | 121.6 | 32,914 | 32.5% |

| contrast | Δ rounds | 95% CI | Δ span s | 95% CI | Δ prompt tok | 95% CI |
|---|---|---|---|---|---|---|
| dag − none | +0.40 | [-3.00, +3.87] | -47.7 | [-118.0, +11.7] | **+17,968** | **[+3,482, +32,139]** |
| ancestors − none | -1.33 | [-4.80, +2.40] | -23.6 | [-109.7, +64.0] | +5,663 | [-10,563, +24,053] |
| ancestors − dag | -1.67 | [-5.50, +2.20] | +24.1 | [-36.1, +99.3] | -12,304 | [-29,643, +7,075] |

**Every round and latency interval straddles zero.** With 15 successors per arm and a per-successor
round spread of ±3, this stage cannot resolve an effect of the size stage 1 predicts for partial
coverage (about 1 round). It is a direction check, and the direction is consistent — ancestors
reads least (6.53 against 9.47) and runs fewest rounds — but nothing here is a measurement.

The one interval that **does** exclude zero is the cost: `dag` adds **+17,968 prompt tokens**. Both
treatment arms spend more prompt than the baseline, because the injected block is re-sent every round
and the successor still does its own work on top of it. Per workload:

| workload | arm | rounds | reads | span s | prompt tok |
|---|---|---|---|---|---|
| FAB-DEEP (depth 4) | none / dag / ancestors | 6.67 / 7.56 / **5.11** | 7.56 / 8.22 / **5.00** | 104.8 / 80.4 / 73.6 | 22,506 / 36,211 / 31,481 |
| RW-FQA (depth 3) | none / dag / ancestors | 9.67 / 9.33 / **8.67** | 12.33 / 12.67 / **8.83** | 215.3 / 132.8 / 203.3 | 34,369 / 58,729 / 35,064 |

In both workloads `ancestors` beats `dag`, and in both `dag` fails to beat the baseline on rounds —
the same ordering stage 1 found, at a size this stage cannot certify.

### Metric 4 — accuracy

| arm | runs | gradeable | accuracy | successor unfinished |
|---|---|---|---|---|
| none | 6 | 6 | 100% | 0 |
| dag | 6 | 6 | 100% | 0 |
| ancestors | 6 | 5 | 100% | 1 |

**Accuracy is at ceiling in every arm**: the graded reconciliation was answered correctly in every
run that finished. One `ancestors` run is excluded rather than scored zero — its successor was still
working when the Lead shut the team down (only two of its three tasks ever reached `task_complete`),
so a zero there would record a scheduling artefact as an inheritance failure. The harness workloads
are therefore a ceiling for accuracy and cannot show the +12-point gain stage 1 measures; they can
only show a loss, and there was none.

### Metric 5 — inherited share of the prompt

26.1% (`dag`) and 32.5% (`ancestors`) of every prompt token the successor processes is inherited
content. Note the sign flip against stage 1: there, inheriting *reduced* total prompt tokens 30-fold
because it removed a search; here it *adds* tokens, because at 24-39% coverage the search happens
anyway and the handoff is paid on top of it.

## 4. What it means

**Bytes accumulate smoothly; rounds do not.** Walking up the dependency graph adds coverage at a
steady rate — 24.2% at the direct predecessors, 39.2% at the full closure, 48.2% if you keep every
earlier task — but round recall barely follows: 23.8%, 28.6%, 44.4%. A round only disappears when
*everything* it would have fetched is already held, so coverage converts into latency through a step
function, not a slope. This single fact explains both halves of the experiment.

**It is why the scripted stage looks transformative and the harness stage looks like nothing.** When
the ancestors happen to hold exactly what the successor needs — the scripted case, 100% coverage —
the same quantisation pays out completely: 8.00 rounds become 1.50, 68 s become 12.5, and the whole
trial costs 30x fewer prompt tokens because the search never happens. When they hold 39% — the real
case — the successor still goes looking, and the handoff is paid on top of a search it did not
prevent. Stage 1 is not the expected result of shipping this; it is the ceiling, and it tells you
what you are aiming at.

**The direct-predecessor policy is the worst of the three.** It is the one arm that reliably costs
something (+17,968 prompt tokens, the only interval in the harness stage that excludes zero) while
buying nothing measurable: +0.40 rounds against baseline in the harness, and in the scripted stage it
closes the half of the answer it was handed while leaving the other half exactly where the baseline
had it (84% → 86%). Half a handoff is not half a benefit; it is a cost with the benefit rounded off.

**So the lever is coverage completeness, not graph depth.** "Reach one hop further" is the wrong knob
— it moves recall 15 points and rounds 5. The question a retention policy should ask is not *how far
up the graph* but *am I over the line for this round*, and the honest way to get over the line at
these coverage rates is to keep everything that fits. That agrees with `research/07_task_dag/README.md` Part A, which found
the same ordering from the other direction: every-earlier-task recalls far more than the graph-derived
sets, and precision is nearly free to give up.

**Accuracy was never the constraint.** 100% in all three harness arms, and in the scripted stage the
fuller handoff was the *most* accurate (99% against 88%), gaining exactly where it was given content
the others lacked. Nothing here trades correctness for speed.

## 4b. Follow-up F2 — given a byte budget, what should the engine keep? (no API calls)

Every policy above is defined by graph position. A real engine is constrained by **bytes**. This
recomputes over the 26 baseline successors already on disk (`research/08_context_inheritance/data/inherit/` +
`research/07_task_dag/data/dag_redundancy/`) and asks the deployable question: at equal budget, does a graph-aware
ordering beat plain recency? The unit of retention is one whole read item, because an engine holds or
drops whole spans. Tables: `research/08_context_inheritance/data/inherit/budget_sweep.md`; tool: `research/08_context_inheritance/budget_sweep.py`.

**Removable rounds versus budget** (pooled, 26 successors; median candidate pool 75 KB):

| ordering | 16 KB | 32 KB | 64 KB | 128 KB | unlimited |
|---|---|---|---|---|---|
| most-recent-first (LRU) | 6% | 19% | 36% | 49% | 52% |
| graph-ordered (preds → closure → rest) | 6% | 18% | 35% | 51% | 52% |
| largest spans first | 6% | 13% | 40% | 52% | 52% |
| smallest spans first | 6% | 19% | 38% | 51% | 52% |
| **cheapest whole rounds first** | **9%** | **39%** | **51%** | 52% | 52% |

**Two findings, and the second is the useful one.**

**The graph is worth nothing as a retention ordering.** It is ahead of LRU at 1 of 11 budget points,
by 1 point, and behind at two others. This was the pre-registered decision rule, and it fires:
*the task board has nothing to offer a retention policy, and next step 13 of the 2026-09-16 deck
should be retired rather than prototyped.* Note that this is a stronger statement than the earlier
note's "broadcast beats the graph" — that compared graph-shaped *sets*; this compares the graph as a
*priority order* at matched cost, which is the only form an engine could actually use it in.

**Selecting for whole rounds roughly doubles what a budget buys.** 19% → 39% at 32 KB and 36% → 51%
at 64 KB, against LRU. That ordering is **not itself deployable** — costing a round requires knowing
which rounds will run and what they will read — so read it as a *reachable ceiling* rather than a
recipe: unlike the `oracle` row of metric 1, it is assembled only from content other tasks actually
read, so a practical predictor could aim at it. The mechanism is the same quantisation as everywhere else in this note: a
budget spent covering most of several rounds removes none of them, while the same bytes spent
completing the cheapest rounds removes all of those. The ordering that wins is the one that prices
each round and buys the cheapest — and it needs a predictor of what will be read, not a graph.

Two refinements the run forced, both worth recording:

- **A byte-density "oracle" is not an oracle for this metric.** Ranking candidate items by useful
  bytes per byte loses to cheapest-whole-rounds at tight budgets and violated the dominance check.
  Maximising covered bytes and maximising removed rounds are different problems; the first is what
  everyone measures and the second is what pays.
- **The invariant that actually tests the machinery** is that every ordering converges at an
  unlimited budget (it does, at 52%) — ordering can only matter under a constraint. The 97% figure
  from the earlier policy table is a *different* oracle (the successor's own future reads, including
  content no other task ever read) and is not reachable from a pool of other tasks' reads.

## 4c. Follow-up F1 — the coverage sweep: step or slope? (180 trials)

A task needing N=4 documents with disjoint symbols (one graded fact each, verified window-unique),
swept over k = 0..4 documents supplied, with *which* documents rotating by repetition so "how many"
is never confounded with "which one". Tool: `research/08_context_inheritance/coverage_sweep.py`; tables:
`research/08_context_inheritance/data/inherit/coverage_tables.md`.

| arm | n | rounds | sd | read_file | locate | wall s | injected tok | accuracy | facts whose doc WAS supplied | facts whose doc was MISSING |
|---|---|---|---|---|---|---|---|---|---|---|
| k=0 (nothing) | 30 | 8.00 | 2.41 | 3.13 | 10.17 | 74.3 | 0 | 99% | — | 99% |
| k=1 | 30 | 8.00 | 2.70 | 1.97 | 10.03 | 58.8 | 604 | 97% | 97% | 97% |
| k=2 | 30 | 8.53 | 3.03 | 1.90 | 8.73 | 54.3 | 1,232 | 96% | 97% | 95% |
| k=3 | 30 | 7.53 | 3.47 | 2.10 | 5.63 | 46.5 | 1,798 | 93% | 97% | **83%** |
| **k=4 (complete)** | 30 | **1.83** | 1.78 | 0.10 | 0.90 | **13.1** | 2,340 | 100% | 100% | — |
| **k=4, each truncated ~50%** | 30 | **2.13** | 2.87 | 0.13 | 1.23 | 16.0 | **1,270** | 100% | 100% | — |

**It is a step, and a nearly perfect one.** Fitting
`rounds = c + a·1[anything missing] + b·(documents missing)`:

| c (floor) | a (cost of anything missing) | b (cost per missing document) | ratio a/b |
|---|---|---|---|
| 1.83 | **5.97** | **0.09** | **69x** |

The pre-registered convexity test: the last document is worth **5.70 rounds**, the average of the
earlier three **0.16 rounds** — a ratio of **36.6x**, with the k=4-vs-k=3 difference at
−5.70 rounds (95% CI [−7.07, −4.30]).

**Handing over one, two or three of the four documents buys nothing at all.** 8.00 → 8.00 → 8.53 →
7.53 rounds against a baseline of 8.00: the k=2 arm is nominally *worse*. Only the fourth document
does anything, and it does almost all of it. This is the cleanest confirmation of the mechanism the
whole note has been circling, and it settles the deployment question: **a retention budget should be
concentrated — cover some successors completely and leave the rest untouched — because a
partially-covered successor costs the same as an uncovered one.**

**But trimming *within* a span is nearly free.** The truncated arm halves the injected content
(2,340 → 1,270 tokens) and costs +0.30 rounds, landing next to complete (1.83) and nowhere near
"one document absent" (7.53). So the step is over *documents present*, not over *bytes present*: an
engine may trim each retained span to roughly half, but must never drop a span entirely. Combined
with F2 that is a usable rule — buy the cheapest whole rounds first, and trim what you keep.

**One unwelcome side effect worth flagging.** Accuracy on facts whose document was *missing* falls
from 99% at k=0 to **83% at k=3**. Given most of what it needs, the agent stops looking for the rest:
partial context makes it complacent. Partial coverage is therefore not merely useless — it is mildly
harmful, which matches the harness stage, where the direct-predecessor arm was the one that reliably
cost tokens while buying nothing.

## 4d. The rule the three results add up to

One mechanism explains all of it: **a round disappears only when everything it would have fetched is
already held.** Bytes accumulate smoothly, rounds do not, and every result here is a consequence.

| result | where it comes from |
|---|---|
| Reaching one hop further up the graph buys +15 points of bytes but +4.8 of rounds | metric 1 |
| Supplying 1, 2 or 3 of 4 needed documents is indistinguishable from supplying none | F1, a/b = 69x |
| Supplying all 4 collapses the task from 8.00 rounds to 1.83 | F1 |
| Trimming each retained span to ~half costs +0.30 rounds | F1 truncated arm |
| The graph is worthless as a retention *ordering* at matched cost | F2, ahead at 1 of 11 budgets |
| Pricing whole rounds and buying the cheapest doubles what a budget buys | F2, 19% → 39% at 32 KB |

**The deployable rule, in three lines:**

1. **Cover a successor completely or not at all.** Partial coverage costs the same as none and is
   mildly *worse* for accuracy (83% against 99% on the facts it was not given).
2. **Allocate the budget by whole rounds rather than by bytes.** Pricing each round and buying the
   cheapest doubles what a budget buys (F2) — but note that ordering needs to know which rounds will
   run and what they will read, so it is a *reachable ceiling*, not a shippable rule. What ships is
   the principle: concentrate on completing units of work, and do not allocate by recency or from
   the task board, both of which were measured and neither of which wins.
3. **Trim what you keep, don't drop it.** Half a span is nearly as good as a whole one; three
   quarters of the documents is as bad as none.

**What this retires.** Next step 13 of the 2026-09-16 deck proposed emitting a retention directive to
the engine derived from the task board. Across two studies the board has now failed three separate
tests: it does not predict *which bytes* better than an unrelated earlier task (matched contrast,
`research/07_task_dag/README.md` Part A), reaching further along it does not convert into rounds (metric 1), and as a
priority *order* under a byte budget it never beats LRU (F2). The artefact worth shipping to the
engine is a per-round cost estimate, not the graph.

## 5. Threats and what is not established

- **The harness stage is underpowered.** 15 successors per arm, per-successor round spread ±3, and
  every round and latency interval straddles zero. The direction is consistent with stage 1 in both
  workloads, but the harness numbers are a direction check, not a measurement. Resolving a one-round
  effect there needs roughly 4x the runs.
- **Harness accuracy is at a ceiling**, so it can only show a loss, not the gain stage 1 measures.
- **Stage 1's 100% coverage is by construction.** It is the ceiling of full inheritance, not a
  forecast; metric 1 is what says how far from it reality sits.
- **One `ancestors` run is excluded**, not scored zero: its successor was still working when the Lead
  shut the team down. That is a scheduling artefact and it would otherwise have read as a 17-point
  accuracy penalty.
- Single provider (z.ai `glm-5.3-flash`), single repository as the corpus, prompts of 2-45k tokens
  where prefill is under 2% of a call.
- **F1's fit has r2 = 0.45.** The coefficients are unambiguous (a = 5.97 against b = 0.09) but
  within-arm round variance is large, so the model explains the *means* far better than individual
  trials. The convexity test, which compares means with a bootstrap interval, is the load-bearing
  claim, not the r2.
- **F2's ceiling is the candidate pool, not the oracle.** All orderings converge at 52% of rounds
  because that is what other tasks' reads can cover; the 97% figure from the earlier policy table is
  a different quantity and the two should never be quoted side by side.
- **The "cheapest whole rounds" ordering uses future knowledge** (which rounds the successor runs
  and what they read) and is a ceiling, not a policy. The deployable claim from F2 is the negative
  one — the graph never beats LRU — plus the principle that allocation should be by whole rounds.
- **F1's step is measured at N=4 documents.** Whether the floor stays at "everything" for much larger
  N, or whether it becomes "everything in the round currently being served", is untested.

## 6. Next steps

1. ~~Test the step function directly.~~ **Done — F1 above.** It is a step: a/b = 69x, and partial
   coverage buys nothing while mildly hurting accuracy.
2. ~~Stop testing depth; test budgets.~~ **Done — F2 above.** The graph is worthless as an ordering;
   buying the cheapest whole rounds doubles what a budget buys.
3. **Power the harness stage** to ~60 successors per arm if the end-to-end number needs to be
   certified, or accept the scripted stage as the measurement and the harness as the sanity check.
4. **Fix the early-shutdown race.** The Lead shut a team down with an in-progress successor; the run
   still reported `completed`. Worth a driver-side guard that refuses shutdown while the board has
   unfinished work, since it silently truncates exactly the task these experiments measure.

# Part B — Do the inheritance arms survive contact with real harnesses and real datasets?

_Formerly `weekly_progress/092326/realworld_inheritance.md` (headed 2026-09-18; corrected 2026-09-23 in 0b65c46)._

*2026-09-18. Follows Part A (three arms on hand-authored workloads) and
`research/07_task_dag/README.md` Part A. Every number below is computed over runs that real agent harnesses really made on
real benchmarks, or over live sessions driven by lm-evaluation-harness.*

## 0. In one page

The Sept 17–18 three-arm study measured its effects on workloads we wrote. Re-derived on real data,
three of its conclusions change:

1. **Whole-observation reuse on real coding work is ~1%, not 24–39%.** Across 1,200 published
   SWE-agent / OpenHands / mini-SWE-agent trajectories, a successor task on the same repository finds
   **0.8–1.2%** of its bytes already fetched by earlier tasks, removing **2.1–4.3%** of its reading
   rounds. The synthetic study's 24–39% came from agents re-reading the same windows of four small
   documents.
2. **The workload decides everything, and coding is the bad case.** On τ-bench and GAIA the same
   measurement gives **11.2% byte recall and 12.7% removable rounds from the previous task alone**,
   rising to 38.2% with every earlier task. The difference is mutation: a source file is *edited*
   between reads, a database record is not. **7.6–14.3%** of a SWE successor's bytes are *stale* —
   the same span, different content — against **0.2–1.8%** on τ-bench.
3. **Frequency-aware eviction beats recency, but only modestly** *(corrected 2026-09-23, see metric
   3)*. At an **8 KB** budget GDSF and LFU leave **20.8% and 19.2%** of τ-bench/GAIA rounds reducible
   against LRU's **15.8%**, and **2.9% and 2.6%** of SWE-bench rounds against **0.3%**; with foresight
   8 KB would reach 38.0% and 4.2%. The first cut reported LFU at 99% / 57% of the unlimited value and
   LRU at 0% / 8% — both artefacts of a replay that let LFU count future reads and never refreshed LRU.

The negative graph result does not rest on this table: the replay has no task graph, and its
`graph-ordered` row (the previous task first, in an arbitrary order) actually leads τ-bench at 8 KB.
The graph is retired on the harness evidence — `research/07_task_dag/README.md` Part A and Part A F2.

## 1. The questions

Same three arms as before — **none** / **dag** (direct predecessor) / **ancestors** (everything
earlier) — plus two dimensions the earlier work only touched offline: a **byte budget** with
eviction, and a **comparison of eviction policies**. Metrics unchanged: input prompt length, tokens
inherited from the direct predecessor and from all ancestors, precision, agentic rounds, rounds
reduced, end-to-end latency.

## 2. Setup

### lm-evaluation-harness cannot host an agentic task, so it hosts the *grading* instead

Verified against the registry, not the documentation: 221 task families, all static;
`lm_eval/api/instance.py` defines `OutputType = Literal["loglikelihood", "loglikelihood_rolling",
"generate_until", "multiple_choice"]`; `TaskConfig` has no tool, environment or step fields; the
chat-template flags render few-shot examples as turns and then flatten them
(`lm_eval.api.utils.multiturn_to_singleturn`). SWE-bench, GAIA, τ-bench, BFCL, AppWorld, WebArena,
OSWorld and Terminal-Bench are all absent and all live in their own harnesses. Issues
[#2926](https://github.com/EleutherAI/lm-evaluation-harness/issues/2926) and
[#3776](https://github.com/EleutherAI/lm-evaluation-harness/issues/3776) ask for agentic support and
are unanswered.

Models, however, are a first-class extension point, so the agent loop goes on our side of the
interface. `research/08_context_inheritance/lmeval_agent_model.py` registers `s15-agent`: lm-eval renders a task prompt, the
s15 harness runs as many rounds as it likes, and the lead's final message is returned as the
completion for the task's own `process_results` to score. **Verified end to end: 3/3 on `gsm8k`,
`exact_match` 1.0, extracted by lm-eval's own filters from multi-paragraph agent output.**

### Replay corpora

| corpus | what | n |
|---|---|---|
| `nvidia/Open-SWE-Traces` v1.1 | SWE-agent, OpenHands and mini-SWE-agent on SWE-bench-style tasks | 400 trajectories each, 1,200 total |
| `agent-evals/hal_traces` | τ-bench airline (5 models), GAIA (HAL generalist agent) | 410 tasks, 5,142 model calls |

HAL records are Weave call logs, so per-call latency is **measured**, not modelled: median 2.09 s,
p90 19.4 s, and a τ-bench task's wall time is **101.5 s of which 101.1 s is model time** — 99.6%,
matching the 2026-09-12 latency breakdown.
[cleanup note: not reproducible from the committed data — no committed script or table computes these HAL latency figures or the "5,142 model calls" in the table above, and `research/08_context_inheritance/data/realworld/hal/` holds 410 tasks (as stated) but 6,600 top-level model calls; see Cleanup notes]

### What a "read" is, defined once

`research/08_context_inheritance/traj_ingest.py::read_key` covers four shapes: an editor `view` of a file span, a shell
command `classify_bash` calls a reader, a code-execution snippet, and any API tool whose name is a
retrieval verb. Mutations are excluded by construction — holding a `book_reservation` receipt cannot
save a later round, because the act still has to happen. Two calls that fetch the same bytes get the
same key even through different tools.

### Two populations, and they answer different questions

- **Within a trajectory** the agent already holds everything it read, so a re-read measures the
  agent *wasting a round* on content it had. That is an upper bound on what inheritance can remove
  from a continuing agent, not the multi-agent question.
- **Across tasks in the same group** (SWE-bench instances on one repository; τ-bench tasks against
  one airline database) the two agents share nothing, so whatever the second reads that the first
  already read is genuinely removable. **This is the population that maps onto inheritance**, and
  it is a pure base rate: the tasks have no dependency graph, so it is what an edge must beat.
  A predecessor that is the *same benchmark task* solved by another model or another harness split
  is excluded — counting those measures self-recall, not inheritance (see §6).

## 3. Results

### The seven metrics, side by side

Cross-task population, unlimited budget. Tokens at 4 chars/token, the constant this series uses.

| metric | SWE-bench `dag` | SWE-bench `ancestors` | τ-bench+GAIA `dag` | τ-bench+GAIA `ancestors` |
|---|---|---|---|---|
| **1. input prompt length** (median, per round) | 17,794 tok | 17,794 tok | 3,060 tok | 3,060 tok |
| **2. tokens inherited from the direct predecessor** | **16,742** | — | **497** | — |
| **3. tokens inherited from all ancestors** | — | **40,601** | — | **50,858** |
| **4. precision** (inherited bytes actually used) | 0.8% | 0.3% | **11.6%** | 0.5% |
| **5. agentic rounds** (reading rounds per successor, mean) | 33.59 | 33.59 | 3.82 | 3.82 |
| **6. rounds reduced by the inherited tokens** | 0.69 (2.1%) | 1.44 (4.3%) | **0.48 (12.7%)** | 1.46 (38.2%) |
| **7. end-to-end latency saved** | ~2.6 s | ~5.4 s | **~1.0 s** | ~3.1 s |

Whole trajectories run **70 rounds** (SWE-bench, median) and **5** (τ-bench/GAIA); the rows above
count only the *reading* rounds of a successor, which is what inheritance can act on.

Latency is priced at this harness's measured 3.72 s/round for SWE-bench and at HAL's **measured**
median call latency of 2.09 s for τ-bench/GAIA. HAL gives the whole-task figure directly: **101.5 s
wall of which 101.1 s is model time** — tool execution is zero to three significant figures, so a
removed round is worth its full model call.
[cleanup note: the 2.09 s and 101.5 s / 101.1 s HAL figures are not reproducible from the committed data; see the note in §2]

**Read row 2 against row 6.** On τ-bench/GAIA the direct predecessor removes 12.7% of rounds for
**497 tokens**; the full ancestor set removes 38.2% for **50,858**. The extra 25.5 points cost
**102× the tokens**. On SWE-bench both arms cost tens of thousands of tokens and buy almost nothing.

### Metric 1 — what inheritance actually recovers (cross-task, unlimited budget)

| corpus | arm | successors | sent KB (median) | byte recall | round recall | precision | stale |
|---|---|---|---|---|---|---|---|
| SWE-bench, 234 repos | dag | 726 | 65.4 | 0.8% | 2.1% | 0.8% | 7.6% |
| | ancestors | 726 | 158.6 | 1.2% | 4.3% | 0.3% | 14.3% |
| τ-bench + GAIA | dag | 407 | **1.9** | **11.2%** | **12.7%** | **11.6%** | 0.2% |
| | ancestors | 407 | 198.7 | 27.6% | 38.2% | 0.5% | 1.8% |

**The direct-predecessor arm is rehabilitated — but read what it actually is.** The earlier study
called it "the worst of the three — the only arm that reliably costs tokens while buying nothing."
On real API-agent work it buys **12.7% of rounds for 1.9 KB at 11.6% precision**, while the ancestors
arm buys 38.2% for 198.7 KB at 0.5%. Per round removed the direct predecessor is **~35× more
byte-efficient**.

**But `dag` is not interchangeable with "some other task", and which one you pick matters very
differently in the two corpora.** Replacing "the previous instance" with a random earlier one, or
with the oldest:

| single-source arm | SWE-bench round recall | τ-bench+GAIA round recall |
|---|---|---|
| the previous instance (`dag`) | 2.1% | **12.7%** |
| one random earlier instance | 1.8% | 4.8% |
| the oldest earlier instance | 1.9% | **0.1%** |
| all earlier instances (`ancestors`) | 4.3% | 38.2% |

On SWE-bench the three single-source arms are indistinguishable — there, one earlier task is as good
as another and the ordering carries no information. On τ-bench/GAIA the spread is **127×**, and the
obvious explanation is wrong.

The tempting reading is that the fixed order makes `dag` a neighbour from the *same model's* run, and
that an agent inherits better from itself. The five τ-bench runs cover the identical 50 tasks, so
that is directly testable with the pool size held constant:

| pool = K other-task trajectories | same model | different model |
|---|---|---|
| K = 8 | 13.1% | 13.7% |
| K = 16 | 23.2% | 22.4% |
| K = 32 | 35.8% | 33.3% |

**Same-model and cross-model are within about two points at every pool size.** Inheritance transfers
across models essentially as well as within one, which also qualifies the 2026-09-16 same-owner
result (44.7% vs 29.9% byte recovery): on this workload, agent identity is not what matters.

What matters is **pool size**: 8 → 16 → 32 trajectories moves removable rounds 13% → 23% → 36%,
roughly linearly in the log. The 127× single-source spread is most likely an artefact of "the oldest"
being one *fixed* trajectory shared by every successor, so its idiosyncratic read set determines the
whole column, while "the previous" varies per successor. That is stated as the likely cause, not a
demonstrated one — it is not established here.

**Coding agents are the hard case, and the reason is mutation.** 7.6–14.3% of a SWE successor's bytes
are *stale* — the identical span, different content. Within a single trajectory it is worse: **⅓ of
paths are read more than once, and only 4–6% of those repeat reads return the same bytes.** The agent
edits and re-reads to verify. A cache keyed on path rather than content would serve those stale.

### Metric 2 — the retention unit is worth 2–3× on code and nothing on APIs

Removable rounds under whole-observation identity vs line-level content addressing:

| population | whole-observation | line-level |
|---|---|---|
| within trajectory (SWE, ancestors) | 5.0% | **16.2%** |
| cross-task (SWE, ancestors) | 4.3% | **9.6%** |
| within trajectory (τ-bench+GAIA, ancestors) | 10.9% | 12.6% |
| cross-task (τ-bench+GAIA, ancestors) | 38.2% | 38.2% |

Source files overlap *partially* — an agent views lines 1–50 and later 1–100 — so content addressing
more than doubles what is recoverable. API responses are either byte-identical or unrelated, so it
adds nothing. Whole-observation matching is also **completely flat across coverage thresholds**
while line-level is not (SWE cross-task, 24,386 rounds):

| coverage threshold | whole-observation | line-level |
|---|---|---|
| 95% | 4.3% | 9.6% |
| 80% | 4.3% | 15.0% |
| 50% | 4.3% | 25.6% |

With whole-item identity a round is either fully covered or barely covered — there is no partial
coverage at any threshold. That flatness is the real argument for content addressing: it is not that
lines recover more bytes, it is that lines are the only unit under which partial coverage exists.
[cleanup note: only the 95% row is in a committed table (`research/08_context_inheritance/data/realworld/cross_open_swe.md`, `ancestors`: 4.3% and 9.6%); the 80% and 50% rows and the 24,386-round count appear in no committed output, and `replay_sweep.py` fixes the threshold at `COVER_ITEM = 0.95`]

### Metric 3 — budgets and eviction policies (E2-R / E3-R)

Removable rounds, cross-task, by policy and budget. Ceilings are marked and are not deployable.

| policy | τ+GAIA @8 KB | @32 KB | unlimited | SWE @8 KB | @32 KB | unlimited |
|---|---|---|---|---|---|---|
| graph-ordered (previous task first) | **21.9%** | 25.8% | 38.2% | 1.4% | 2.3% | 4.3% |
| **GDSF** | 20.8% | **26.3%** | 38.2% | 2.9% | 3.8% | 4.3% |
| **LFU** | 19.2% | 23.1% | 38.2% | 2.6% | 3.1% | 4.3% |
| SIEVE | 18.1% | 25.5% | 38.2% | 2.0% | 3.0% | 4.3% |
| **LRU** | 15.8% | 22.8% | 38.2% | 0.3% | 1.4% | 4.3% |
| sliding window (last N rounds) | 15.6% | 22.8% | 38.2% | 0.3% | 1.4% | 4.3% |
| FIFO / recency truncation | 14.7% | 22.2% | 38.2% | 0.3% | 1.1% | 4.3% |
| random eviction (control) | 13.6% | 19.2% | 38.2% | 0.3% | 1.3% | 4.3% |
| smallest spans first | 13.1% | 21.2% | 38.2% | **3.5%** | **3.9%** | 4.3% |
| S3-FIFO | 10.7% | 22.7% | 38.2% | 2.0% | 2.2% | 4.3% |
| keep costliest-to-recover | 10.0% | 14.7% | 38.2% | 1.3% | 1.5% | 4.3% |
| largest spans first | 6.0% | 3.1% | 38.2% | 0.2% | 0.2% | 4.3% |
| Belady / MIN *[ceiling]* | 38.0% | 38.2% | 38.2% | 4.2% | 4.3% | 4.3% |
| byte-density oracle *[ceiling]* | 38.0% | 38.2% | 38.2% | 4.2% | 4.3% | 4.3% |

**Correction (2026-09-23).** The first version of this table was wrong for every online policy, in
LFU's favour. `replay_sweep.stage_cross` counted each item's reuse frequency over the *whole group*,
the successor and every later task included, so LFU and GDSF were in effect told what the successor
would read; and `evict_policies` offered each item to the online caches once, in first-read order, so
a re-read never refreshed LRU — it ran as FIFO and matched the random control. It also set no tier on
the replay, so `graph-ordered` ran as plain recency rather than previous-task-first. The table above
replays every earlier read, in order, through each cache, with counts of reads seen so far only
(`retained(..., accesses=...)`); offline rankings and ceilings are unchanged. The old headline —
LFU 21.9% against LRU 3.0% at 8 KB on τ-bench/GAIA, 4.0% against 0.0% on SWE-bench — is 19.2% against
15.8%, and 2.6% against 0.3%. Full grids: `research/08_context_inheritance/data/realworld/crossgrid_{hal,open_swe}.md`.

**The budget is not the binding constraint.** With perfect foresight, **8 KB already buys the entire
unlimited-budget value** in both corpora (38.0 of 38.2; 4.2 of 4.3). Every gap in the table is a
prediction failure, not a capacity failure.

**Frequency beats recency, modestly.** At 8 KB GDSF and LFU reach 55% and 51% of the reachable
ceiling on τ/GAIA, LRU 42%, and random eviction already 36%; at 32 KB LFU and LRU tie (23.1% and
22.8%). On SWE-bench the gap is larger in ratio — 2.6–2.9% against 0.3% — but the whole ceiling is 4.2%,
and plain *smallest spans first* does best there (3.5%).
This matters because recency truncation and last-N-rounds windows are what production harnesses
actually ship.

**The replay says nothing about the graph.** There is none: `graph-ordered` here means the previous
task first in a fixed arbitrary order, and on τ-bench it leads at 8 KB (21.9%) for the same reason
`dag` does below — a caveat, not a graph result. The first version read "`graph-ordered` sits near the
bottom of both tables (0.7% on SWE at 8 KB). Third independent test failed, agreeing with the F2
budget sweep"; only the F2 half of that stands.

**"Evict what is simplest to recover" does not work.** Keeping the costliest-to-recover items reaches
10.0% / 1.3% at 8 KB (corrected; first reported 11.1% / 2.3%), below LFU, GDSF and S3-FIFO everywhere. Recovery cost correlates with size, and
the large items are not the reused ones. A methodological note that cost us a wrong table first:
**recovery cost must be measured per item or the policy silently becomes "keep the largest"** — with
only the modelled cost, which is monotone in bytes, the two columns came out byte-identical.

### Metric 4 — measured latency, and how much of the waste is the model's fault

From the HAL Weave logs, per model call: median **2.09 s**, p90 **19.4 s**; a τ-bench task is
**101.5 s** wall of which **101.1 s** is model time.
[cleanup note: not reproducible from the committed data; see the note in §2]

Five models on the *identical* 50 τ-bench tasks waste very different amounts re-fetching what they
already hold (within-trajectory round recall): **gemini-2.0-flash 20.8%**, gpt-4.1 14.6%,
claude-3.7-sonnet 13.9%, claude-opus-4.1 10.7%, **deepseek-v3 5.2%**. A 4× spread on the same work —
how much a context manager can save is partly a property of the model, not only of the workload.

### G1 — the live arms cannot be run on lm-eval tasks, and that is the finding

The live half of this study was gated on one question: does the lead build a dependency graph deep
enough for the `dag` and `ancestors` arms to differ? They can only differ at **depth ≥ 3** — in a
chain A → B → C the direct predecessors of C are {B} and its ancestors are {A, B}. The Sept 17–18
study guaranteed that depth by writing workloads that had it. Here it had to come from the lead
reading a real benchmark task.

Prompts were exactly what lm-eval renders plus the answer contract. The words *depend*, *blocked*,
*independent*, *subtask* and *update_task* never appear — the 2026-09-17 census established that
prompts saying "independent" were what suppressed edges in the earlier study, so a nudge in either
direction would invalidate the measurement.

| task | expected | runs | any board | any edge | max depth | median wall s |
|---|---|---|---|---|---|---|
| `gsm8k` | FLAT-likely | 3 | 0 | 0 | 0 | 15 |
| `mmlu_pro_biology` | FLAT-likely | 3 | 0 | 0 | 0 | 30 |
| `longproc_travel_planning_2k` | DEP-likely | 3 | 0 | 0 | 0 | 103 |
| `longproc_path_traversal_2k` | DEP-likely | 3 | 0 | 0 | 0 | 122 |
| `longbench2_academic_multi` | DEP-likely | 3 | 0 | 0 | 0 | 172 |

**0 of 15 runs created a task at all** — not merely no edges, no board. (The edge and depth columns
were produced by a `board_of` that read the wrong key and would have reported any graph as edgeless;
that was found and fixed later on the codebase workloads. It does not touch this result — with no
`task_create` events there is no graph to miscount, and re-deriving every trace with the fixed
parser still gives zero boards.) The lead answered directly
every time, including on multi-document QA and on long procedural generation, and including on a
1.59 MB document that triggered the harness's own compaction before the first round.

This does not contradict the 2026-09-17 census, which found the lead emits a *complete* graph
unprompted on genuinely staged work. It locates the boundary: **a benchmark question is not staged
work, however long the document attached to it.** Decomposition needs several deliverables that
depend on each other, and an lm-eval doc is one question with one answer.

The consequence is that **the live arm comparison cannot be run on lm-eval tasks as rendered.** The
only ways to make the arms differ are to write a team framing into the prompt or to state the edges —
both of which are the fabrication this study was set up to avoid. So the live confirmation of the
replay results has to come from a workload that is genuinely multi-part; that is next step 1, not a
result reported here.

### Metric 5 — prompt length on real harnesses

Median prompt at a successor round: **87,750 chars (OpenHands)**, **63,382 (SWE-agent)**, **43,040
(mini-SWE-agent)**, **~10,000 (τ-bench)**, **17,423 (GAIA)**. Real coding trajectories run **49–76
rounds** (median), against 8 in the synthetic study — an order of magnitude more rounds and more
context than anything the earlier arms were measured on.
[cleanup note: `research/08_context_inheritance/data/realworld/arms_open_swe.md` gives per-harness medians of 51 (mini-SWE-agent), 75 (OpenHands) and 76 (SWE-agent) rounds; the 49 is not reproduced]

### G2 — codebase work, where the lead does build a graph

The lm-eval gate failed because a benchmark question is one deliverable. Real project work over a
real codebase is several that stack. Target: `lab-group-56`, an ECE 4750 lab repository (PyMTL and
Verilog — a `sim/vc` component library plus `sim/lab1_imul` and `sim/lab2_proc`). Two workloads over
the same files, prompts carrying the 2026-09-17 census's own neutral board frame and checked against
its FORBIDDEN regex, so no dependency language reaches the model:

- **EXPLAIN** — read-only comprehension: inventory the modules, trace a request, name the shared
  components, write an orientation note citing all three.
- **MODIFY** — deliberately inert edits in a sandbox copy: stamp a `BUILD_TAG` constant across two
  lab directories, add a file asserting it is consistent, write a changelog. It implements none of
  the assignment; the repository is graded and has a second author, and the original is never
  written (verified: a run whose prompt still named the original path had 21 of 22 `edit_file` calls
  denied and the tree stayed byte-identical).
  [cleanup note: no matching trace in research/08_context_inheritance/data/codebase — the only run whose prompt named an original path rather than the sandbox copy is the pilot `research/08_context_inheritance/data/codebase/MODIFY/run_20260919T005642_012386Z_5cbeea79.jsonl`, aimed at this repository's `agents/` folder, not lab-group-56: 22 `edit_file` calls, 9 denied (every attempt on `agents/*.py`; its `profile_end` records `edit_file-outside-root: 9`) and 13 applied inside `profiling_sandbox/codebase/`. Across all 24 codebase traces 10 of 168 `edit_file` and 21 of 71 `write_file` calls were denied and none succeeded outside the sandbox; `agents/` has no commit after 2026-08-26, and the lab-group-56 original is not in the repo, so "byte-identical" cannot be checked for it]

| workload | runs | any board | any edge | max depth | depth>=3 | median tasks/edges | median wall |
|---|---|---|---|---|---|---|---|
| MODIFY | 3 | 3 | 3 | 3 | **3/3** | 5 / 5 | 1166 s |
| EXPLAIN | 3 | 3 | 3 | 2 | **0/3** | 4 / 3 | 1202 s |

Against **0 of 15** on lm-eval, every codebase run builds a board with edges. But the two shapes
differ, and it is not a truncation artefact — the one EXPLAIN run that completed all four tasks
without hitting its deadline was also depth 2. **Comprehension work fans in** (inventory and trace
are independent, everything after needs both); **modification work chains** (each edit builds on the
last). At depth 2 a successor's direct predecessors *are* its ancestors, so EXPLAIN cannot separate
the `dag` and `ancestors` arms.

### G2b — mutation is not the mechanism; coverage is

The replay half attributed the coding/API gap to mutation, inferred from a difference between two
benchmarks that differ in many other ways. These two workloads differ in exactly one thing — whether
the agents write — so the inference is testable directly.

| | byte recall | round recall | oracle round recall | sent KB (median) |
|---|---|---|---|---|
| EXPLAIN (read-only) | **95.3%** | 10/11 = **90.9%** | 100% | 162.3 |
| MODIFY (writes) | **12.4%** | 5/45 = **11.1%** | 60.0% | 9.8 |

A 7.7x gap in bytes and 8.2x in rounds. But decomposing every read by why it was or was not already
held says the cause is **not** mutation:

| | identical span re-read | **stale** (same span, bytes changed) | other span of a known file | file never seen |
|---|---|---|---|---|
| EXPLAIN | 0.4% | **0.0%** | 53.9% | 45.7% |
| MODIFY | 2.6% | **0.9%** | 33.9% | 62.6% |

Stale is ~0 in both. What separates them is **coverage**: 62.6% of MODIFY's reads are files no
predecessor ever opened, against 45.7% for EXPLAIN, because EXPLAIN's inventory task sweeps the whole
codebase while MODIFY's tasks each touch different files. This agrees with the replay layer's own
pool-size result (13% -> 23% -> 36% as the pool grows 8 -> 16 -> 32 tasks) and demotes mutation from
mechanism to contributor: it is 7.6-14.3% of a SWE successor's bytes in trajectories running 70
rounds of edit-and-verify, and ~1% in these shorter team runs.

The table also settles the granularity question live, and the decisive column is rounds, not bytes.
Recomputing EXPLAIN's successors at each unit:

| retention unit | byte recall | round recall |
|---|---|---|
| whole file | 86.0% | 4/11 = **36.4%** |
| byte range | 95.3% | 10/11 = **90.9%** |
| line | 96.6% | 10/11 = 90.9% |

Going from file to range buys **+9 points of bytes but +54 points of rounds** — 2.5x more removable
rounds — and line granularity adds nothing beyond range. That is the same quantisation this series
keeps re-deriving: a round disappears only when *everything* it would have fetched is held, so a
unit coarse enough to miss one span of one file keeps the whole round alive.

Two numbers that look contradictory are measuring different things, and both matter. Agents almost
never issue the byte-identical read twice (0.4% of EXPLAIN's reads, 2.6% of MODIFY's are an exact
repeat of a span already fetched), which is why a cache keyed on exact observations recovers almost
nothing. But 86% of a successor's bytes sit in files a predecessor already opened, and 95.3% in
ranges it already covered. The content is there; only an exact-match key fails to find it.

Finally, `dag` and `ancestors` score **identically** on both workloads — same recall, same precision,
same bytes — so on boards of this size the transitive closure adds nothing over direct predecessors
even where the depth exists to allow it.

### E1-L — the three arms, measured live on a real codebase

12 cells on MODIFY (the only workload whose boards reach depth 3), arms interleaved within a
repetition and shuffled on a fixed seed. The census runs supplied the baseline arm, so nine were new.

| arm | successors | rounds | **reads** | span s | prompt tok | cross-owner |
|---|---|---|---|---|---|---|
| none | 7 | 13 | **10** | 294 | 46,822 | 7/7 |
| dag | 6 | 12 | **6** | 252 | 90,714 | 6/6 |
| ancestors | 5 | 12 | **4** | 264 | 74,258 | 5/5 |

| arm | Δ rounds | 95% CI | Δ span s | 95% CI | Δ prompt tok | 95% CI |
|---|---|---|---|---|---|---|
| dag | −0.5 | [−6.9, +5.7] | −17.8 | [−238.4, +231.0] | **+42,295** | **[+4,697, +81,923]** |
| ancestors | −3.2 | [−9.6, +2.5] | −32.3 | [−248.8, +182.0] | +20,063 | [−11,710, +47,920] |

**Inheritance removes the rounds it can reach, and reading is under half of agentic work.** Reads
per successor fall monotonically — 10 to 6 to 4, a 60% reduction under the closure — and mean rounds
fall **13.0 to 12.5 to 9.8, a 25% reduction** for the closure. The interval still straddles zero
(−3.2 [−9.6, +2.5], n=5 successors spanning 3 to 14 rounds), so this is a point estimate with a
mechanism rather than a demonstrated effect.

What makes it legible is the composition of a successor's rounds, classified by what each round
actually did (bash reads counted as reads via `classify_bash`, as everywhere else in this study):

| arm | successor rounds | reads only | reads + other work | no reads at all | rounds with >=2 reads |
|---|---|---|---|---|---|
| none | 85 | **42.4%** | 12.9% | 44.7% | **30.6%** |
| dag | 72 | 44.4% | 8.3% | 47.2% | 12.5% |
| ancestors | 44 | 31.8% | 9.1% | 59.1% | **6.8%** |

Only the **reads-only** rounds can ever disappear. At 42.4% of 13.0 rounds that is a ceiling of
**5.5 removable rounds**, and the closure removed **3.2 — 58% of everything structurally available**.
The arms are not failing; they are working against a low ceiling.

Three things cap that ceiling, and two of them are why covered reads do not always buy a round:

- **44.7% of a successor's rounds contain no read at all** — edits, test runs, task claims, messages.
  Inheritance is structurally irrelevant to them. This is the largest term.
- **12.9% mix reads with other work.** The read may be fully covered and the round still has to
  happen, because the edit or the bash command in the same round still has to run.
- **30.6% of baseline rounds issue two or more reads**, and a round dies only if *every* one of them
  is held. Partial coverage inside a multi-read round buys nothing. That these fall to 6.8% under the
  closure is the clearest evidence the mechanism is real: the multi-read rounds are exactly the ones
  complete coverage eliminates.

The single interval that excludes zero remains a **cost**: the direct-predecessor arm adds 42,295
prompt tokens while removing 0.5 rounds.

This replicates the 2026-09-17 harness stage on a different workload, a different codebase and a
different task family, where the only harness interval excluding zero was also a cost (+17,968
tokens). The mechanism is the quantisation this series keeps re-deriving: **a round disappears only
when everything it would have fetched is already held.** Removing six of ten reads removes no rounds
if each surviving round still needs one fetch. It is also exactly what MODIFY's measured headroom
predicted — 12.4% byte recall and an oracle ceiling of 60% leave little for any arm to collect.

Two smaller observations, both underpowered. The closure beats direct predecessors on every metric
(fewer reads, fewer rounds, 22,000 fewer tokens), which inverts the ordering of the synthetic study
but on n=5 versus n=6 successors. And the injected arms built slightly smaller boards (median 4-5
tasks against 5, 3-4 edges against 5): handed the content, the lead appears to see less work to
split. Neither is resolvable at this sample size.

### E1-L on EXPLAIN — the high-headroom case is too noisy to read

EXPLAIN was the fair test of coverage: 95.3% byte recall and 90.9% of rounds removable in principle,
against MODIFY's 12.4%. If handing over content ever removes rounds, it should be here. It is not
readable at this sample size.

| arm | successors (completed) | rounds, each | median rounds | median reads | median span s |
|---|---|---|---|---|---|
| none | 4 | 9, 6, 11, 6 | 7.5 | 8 | 158 |
| ancestors | 3 | **1, 36, 2** | 2 | 1 | 80 |

One successor took 36 rounds and 34 reads; the other two took 1 and 2. The median therefore says
rounds fell from 7.5 to 2 and the mean says they rose by 5.0 — **the two summaries disagree in
sign**, and at n=3 with that spread neither is a result. The round and latency intervals are
correspondingly useless (+5.0 [−8.0, +27.5]).

What survives is the same thing that survived on MODIFY: **the token cost, +95,364 [+13,316,
+163,246]** — the closure over a comprehension board hands over the whole codebase inventory, which
is why the injected arm's prompts are 195,430 tokens against 81,698. That interval is wide but it
excludes zero, and it is the third arm in this study whose only reliable effect is a cost.

So the coverage hypothesis is **not** refuted here — it is untested. Reading it would need roughly
four times these runs, which is the same conclusion the 2026-09-17 harness stage reached about
itself.

## 4. What it means

The deployable rule from the synthetic study was *cover a successor completely or not at all; buy
whole rounds, not bytes; trim what you keep*. Real data keeps the spirit and changes the mechanism:

1. **Prefer frequency-aware eviction to plain recency, but expect a modest gain.** At 8 KB it is
   19–21% against 16% of rounds on τ-bench/GAIA and 2.6–2.9% against 0.3% on SWE-bench *(corrected
   2026-09-23; the first cut said 4.0% against none)*.
2. **Spend the budget on prediction, not capacity.** 8 KB is already enough; the ceiling is reached
   at the smallest budget tested.
3. **Breadth is the ceiling, and it scales.** With the retention policy held fixed, removable rounds
   go 13% → 23% → 36% as the pool grows 8 → 16 → 32 earlier tasks, roughly linearly in the log. Since
   8 KB is enough to hold the *useful* part of any pool, the design tension is not budget against
   coverage — it is how many earlier tasks the engine can see at all. (The pool-size runs were
   computed with the first replay's policy code; the pool effect itself is an unlimited-budget
   measurement and does not depend on it.)
4. **Agent identity is not a lever, at least on this workload.** Size-matched, inheriting from a
   different model's runs is as good as inheriting from your own (within ~2 points at every pool
   size). A shared pool does not need to be partitioned per agent.
5. **Key on content, not on path** — and on code, key on *lines*, worth 2–3×. On API workloads it is
   worth nothing, so this is a per-workload decision.
6. **Do not use the task graph for retention.** Third failure, consistent with the previous two.
7. **Inheritance is a much better bet for tool-calling agents than for coding agents** — 12.7% of
   rounds from the previous task alone against 2.1%, and 38.2% against 4.3% from all of them. If this
   line of work needs a target workload, it is τ-bench-shaped, not SWE-bench-shaped.

## 5. Threats and what is not established

- **The cross-task ordering is arbitrary, but not inert.** Instances in a group have no real
  precedence, so `dag` means "the previous one in a fixed order" — it is a base rate, not a
  dependency edge, and must never be quoted as one. It is also not neutral: on τ-bench/GAIA that
  fixed order makes `dag` worth 127× the oldest instance (§3), for a reason that is not established
  — the same-model hypothesis was tested and refuted. The SWE-bench side has no such sensitivity.
  Any single-source number must therefore say *which* single source it means.
- **Rounds removed are counterfactual on the replay side.** They are computed, not observed. The
  live confirmation (P4/P5) has not run.
- **HAL cross-task has only 3 groups** (τ-bench airline, GAIA, CORE-bench), so its 407 successors are
  clustered, and the five τ-bench runs share one 50-task set — same-task pairs are excluded but the
  *domain* is still one airline database. The SWE side has 234 groups and is the better-powered half.
- **CORE-bench and SciCode ingest to zero reads** — SciCode's agent is zero-shot (no rounds) and
  CORE-bench's writes rather than retrieves. Their rounds are counted, their reads are not.
- **GAIA contributes few reads** (308 over 121 tasks) and the sampled trajectories include failure
  loops. τ-bench carries most of the non-coding signal.
- **No accuracy effect is measured on the replay side.** The synthetic study found partial coverage
  *hurts* accuracy (99% → 83%); nothing here tests that.
- One provider (z.ai `glm-5.3-flash`) for everything live.

## 6. Housekeeping

- **The repo `.env` points at `MODEL_ID=glm-5.3-flashx`, which this z.ai plan is not entitled to** —
  every call returns `429 / code 1311`. `code.py:77` calls `load_dotenv(override=True)`, so exporting
  `MODEL_ID` cannot fix it; `profile_run.py --model` now patches the loaded module instead. The
  DeepSeek lane (`/home/yq335/lanes/census_ds`) returns `Insufficient Balance` and is dead.
- **A same-task contamination in the first cut of this note, found and corrected.** HAL publishes the
  same benchmark over many models and every run reuses task ids `0..N`, so grouping by benchmark made
  the cross-task population pair a task with **itself solved by a different model**. The first
  τ-bench/GAIA numbers were inflated accordingly: `dag` read 23.2% byte / 30.5% round recall and is
  actually **11.2% / 12.7%**. The SWE side was affected too but only 11 of 732 pairs (three harness
  splits over overlapping instance sets). `hal_ingest.py` now makes instance ids unique per run and
  carries a separate `task_key`; `replay_sweep.stage_cross` excludes any predecessor sharing the
  successor's identity and prints how many slots it dropped. The qualitative conclusions did not
  change — τ-bench still beats SWE-bench by ~6× and the direct predecessor is still far more
  byte-efficient — but every number in §3 is the corrected one.
- **Two bugs fixed in the existing pipeline, both of which affected published numbers.**
  `profile_run.py` recorded predecessors' `bash` reads but only ever injected `read_file` ones
  (301 vs 373 entries across the prewarm dumps), so the `dag`/`ancestors` arms were handed ~45% less
  than the recall denominator credited them with. And `inherit_loop.py`'s
  `refetch_after_injection` counted *every* `read_file`, not re-reads of injected paths, which is
  why the "dag 75% re-fetch" line overstated distrust of the handoff.
- **The online eviction policies were replayed with future knowledge and without cache hits**
  (found and fixed 2026-09-23). Frequencies were counted over the whole group, including the successor
  and later tasks, and every item was offered to the caches once, so LRU never refreshed. Metric 3 is
  recomputed; `evict_policies.retained` now takes the read stream (`accesses`) and replays it as a real
  cache, and `test_evict_policies.py` pins both behaviours. The live path in `profile_run.py` passes no
  stream yet, so a live run with `--prewarm-evict lru` still gets FIFO; every live run reported here
  used an unlimited budget, where the policy does not matter.
- **All 16 eviction policies are non-monotone in retained bytes if they are online caches** — LRU and
  FIFO violate it in 17 of 60 random pools. This is not a bug: LRU's stack property only holds for
  equal-sized objects, and tool observations are not. The earlier study's invariant "removable rounds
  are non-decreasing in budget for every ordering" was only ever tested on offline orderings.

### Next steps

1. Live confirmation needs a genuinely multi-part workload, because the census cleared no lm-eval
   task (§3, G1). The honest options are a benchmark whose unit of work is a project rather than a
   question — SWE-bench instances driven through mini-swe-agent, or AppWorld — or accepting that the
   live check measures the *policy* rather than the arms: `none` vs `ancestors` under LFU and LRU at
   8 KB and unlimited needs no graph at all. The corrected replay puts that gap at 3–5 points on
   τ-bench, so the live run has to be sized to resolve it.
2. Fold line-level content addressing into the injector — it is the largest single lever on coding
   work and it is not implemented.
3. A staleness-aware policy: never hand over a span the successor's own base commit has moved past.
4. Ingest AppWorld and Online Mind2Web for a third non-coding regime.

# Part C — Real-world inheritance profiling — pipeline and reproduction

_Formerly `s15_integrated_harness/realworld_inheritance_profile.md` (undated; committed 2026-09-21 in aded0d0)._

Companion to Part B, which holds the results. This file
documents the pipeline and how to re-run it.

## 1. Why a new pipeline

Every earlier study in this series measured effects on workloads written to show them. This one
re-derives the same metrics from (a) published traces of real agent harnesses on real benchmarks and
(b) live sessions driven by lm-evaluation-harness. Two layers, deliberately:

| layer | source | measures | cost |
|---|---|---|---|
| **replay** | `nvidia/Open-SWE-Traces`, `agent-evals/hal_traces` | real prompts, rounds, tokens, per-call latency, re-read behaviour; rounds removed is *counterfactual* | none |
| **live** | s15 harness behind an lm-eval model endpoint | measured rounds and latency, lead-built boards, official accuracy | API |

## 2. Modules

| file | role |
|---|---|
| `research/common/evict_policies.py` | 16 eviction policies + measured recovery cost. Used by BOTH layers, so live and replayed cells stay comparable |
| `research/08_context_inheritance/traj_ingest.py` | HF trajectory corpora → canonical read-item traces. `read_key` is the single definition of "a read" |
| `research/08_context_inheritance/hal_ingest.py` | HAL run archives → the same form, plus measured per-call latency |
| `research/08_context_inheritance/replay_sweep.py` | E1-R / E2-R / E3-R: arms, cross-task, and the budget × policy grid |
| `research/08_context_inheritance/lmeval_agent_model.py` | registers `s15-agent` as an lm-eval model: one task prompt → one agentic session → its final message |
| `research/08_context_inheritance/lmeval_workloads.py` | the G1 census and the live arm/budget/policy sweep |
| `research/common/test_evict_policies.py` | the invariants every policy must satisfy |

Modified: `profile_run.py` (`--prewarm-evict`, `--prewarm-budget`, `--answer-out`, `--model`, plus
two bug fixes), `inherit_loop.py` (`refetch_after_injection`).

## 3. Design decisions that decide the numbers

**The unit of retention is one whole observation.** A fragmented handoff was measured to be re-read
88% of the time (2026-09-16), so no policy may split a span.

**Identity is content, not path.** Agents edit the files they read, so the same span fetched twice is
usually not the same bytes. Matching on path would count an edited re-read as reusable and a cache
built that way would serve it stale. The gap is reported as a `stale` column because it turned out to
be most of what looks like redundancy in a bash-only harness: within a trajectory, ⅓ of paths are
read more than once and only 4–6% of those repeats return identical bytes.

**Two ceilings, and they are ceilings only for their own metric.** Belady/MIN and the byte-density
oracle rank on what the successor will go on to read. They are computable offline and refused by
`profile_run.py --prewarm-evict`. They are *not* ceilings for the line-level metric, which they do
not optimise — the same trap the earlier study hit with a byte-density "oracle" for rounds.

**Recovery cost must be measured per item.** With only the modelled cost — which is monotone in
size — "keep the costliest to recover" becomes "keep the largest" and the two columns come out
byte-identical. The priced version carries each item's reuse count and whether a search round had to
locate it.

**Online caches are not monotone in budget.** LRU and FIFO violate it in 17 of 60 random pools
because LRU's stack property assumes equal-sized objects. Monotonicity is asserted only for the
offline family (`evict_policies.MONOTONE_IN_BUDGET`).

## 4. Environment

```bash
uv venv --python 3.12 /home/yq335/rw_env
uv pip install --python /home/yq335/rw_env/bin/python \
    "lm-eval==0.4.13" datasets huggingface_hub anthropic cryptography pytest "httpx<1" python-dotenv
export HF_HOME=/tmp/<scratch>/hf          # /home has ~12 GB free; corpora are 10-28 GB per split
```

No torch: `lm-eval`'s base dependencies do not include it (609 MB total). The corpora are streamed,
never materialised.

**Provider.** The repo `.env` pins `MODEL_ID=glm-5.3-flashx`, which this z.ai plan is not entitled to
(`429`, code `1311`). `code.py:77` calls `load_dotenv(override=True)`, so exporting `MODEL_ID` does
nothing — pass `--model glm-5.3-flash` instead. The DeepSeek lane returns `Insufficient Balance`.

## 5. Reproduce

```bash
cd /home/yq335/learn-claude-code
export HF_HOME=/tmp/<scratch>/hf
PY=/home/yq335/rw_env/bin/python

# --- invariants (no network, no API) -------------------------------------------------------
$PY -m pytest research/common/test_evict_policies.py -q

# --- ingest (streams from HF; ~35 MB on disk for 1,200 trajectories) ------------------------
for s in sweagent openhands minisweagent; do
  $PY research/08_context_inheritance/traj_ingest.py --corpus open_swe --split $s --limit 400
done

$PY research/08_context_inheritance/hal_ingest.py --list --pattern "taubench|gaia"   # pick archives
curl -sL -o T.zip "https://huggingface.co/datasets/agent-evals/hal_traces/resolve/main/<archive>"
$PY research/08_context_inheritance/hal_ingest.py --archive T.zip

# --- results (no API) ----------------------------------------------------------------------
R=research/08_context_inheritance/replay_sweep.py
for c in open_swe hal; do
  $PY $R research/08_context_inheritance/data/realworld/$c --stage arms      --json .../arms_$c.md
  $PY $R research/08_context_inheritance/data/realworld/$c --stage cross     --json .../cross_$c.md
  $PY $R research/08_context_inheritance/data/realworld/$c --stage crossgrid --json .../crossgrid_$c.md
done

# --- the lm-eval endpoint, end to end ------------------------------------------------------
$PY - <<'EOF'
import sys; sys.path.insert(0, "s15_integrated_harness/scripts")
import lmeval_agent_model, lm_eval           # the import fires @register_model
print(lm_eval.simple_evaluate(
    model="s15-agent",
    model_args="repo=/home/yq335/learn-claude-code,arm=none,model=glm-5.3-flash,max_seconds=240",
    tasks=["gsm8k"], limit=3, bootstrap_iters=0)["results"])
EOF

# --- G1 census: does the lead build a real board on real tasks? -----------------------------
$PY research/08_context_inheritance/lmeval_workloads.py --census --limit 3
```

`lm-eval 0.4.13` has no `--plugins` flag (it landed after the release), so the CLI cannot see a local
model module — use `simple_evaluate` as above.

## 6. Known gaps

- `qasper` needs a dataset script, removed in `datasets` 5.x. `gpqa` is gated and needs `HF_TOKEN`.
  `ifeval` needs `langdetect`.
- CORE-bench and SciCode ingest to zero reads: SciCode's agent is single-shot, CORE-bench's writes
  rather than retrieves. Their rounds are counted, their reads are not.
- The live arm/budget/policy sweep (P4/P5) has not run. `lmeval_workloads.py` takes `--arms`,
  `--evict` and `--budgets` and is ready for it.
- Line-level content addressing is measured but not implemented in the injector, although it is the
  largest single lever on coding work (2–3× the removable rounds).

---

## Data inventory

Everything below sits under `research/08_context_inheritance/data/`, moved there by the cleanup from `s15_integrated_harness/traces/{inherit,realworld,lmeval,lmeval_smoke,codebase}/`. Every live run is z.ai `glm-5.3-flash` and records `git_head` f17e2a4 in its `profile_meta`. A live run is `run_<UTC timestamp>_<id>.jsonl` (the event trace) plus two sidecars written by `research/common/profile_run.py`: `.inputs.jsonl` (the size breakdown of every model request) and `.reads.jsonl` (the full text of each tool result the first time it appears; absent when the run read nothing). The workload drivers add `<label>.prompt.txt` (the exact user turn), `<label>.answer.json` (the lead's final message, status, wall seconds), `<label>.prewarm.json` (per-task injections and recorded reads, `--prewarm-dump`) and, in Part A, `<label>.cell.json` (the run's board and grading summary). Status words: published (feeds a reported number), input (a corpus the published numbers are computed from), smoke, pilot, duplicate, stale, removed.

### `research/08_context_inheritance/data/inherit/` — Part A

| item | what it holds | written by | used in | status |
|---|---|---|---|---|
| `research/08_context_inheritance/data/inherit/stage1.jsonl` | Scripted stage: 96 trials = 3 arms × 4 tasks (T1–T4) × 8; 95 answered, one `none` trial stopped at the 14-round cap. Rows carry no timestamp or model; Part B dates the study to Sept 17–18. | `research/08_context_inheritance/inherit_loop.py` | Part A §3, through `inherit_tables.md` ("Stage 1") | published |
| 18 harness runs `run_20260917T215129_606205Z_d1201434` … `run_20260918T010514_417426Z_2c0efda3` (trace, `.inputs.jsonl`, `.reads.jsonl`) with `FAB-DEEP-*` / `RW-FQA-*` `.cell.json` and `.prewarm.json` | FAB-DEEP and RW-FQA × none / dag / ancestors × r1–r3, 2026-09-17 21:51 → 2026-09-18 01:17 UTC; 17 completed, `RW-FQA-none-r1` timed out at 900.7 s | `research/08_context_inheritance/inherit_workloads.py` driving `research/common/profile_run.py --prewarm …` | Part A §3b (through `inherit_analyze.py`); the six `none` runs are six of F2's 15 baseline runs | published |
| `research/08_context_inheritance/data/inherit/inherit_tables.md` | Metric 1, stage 1 and stage 2 tables | `research/08_context_inheritance/inherit_analyze.py --stage1 … --stage2 … --md` | Part A §3, §3b; deck slide 6 | published. Re-running with `--coverage` appends an F1 section that this committed file does not contain; F1 was saved separately as `coverage_tables.md`. |
| `research/08_context_inheritance/data/inherit/inherit_rows.json` | `{"stage2": [...]}`: the 45 per-successor rows (15 per arm) behind the stage-2 tables | `inherit_analyze.py --json` | not cited by any note; the data behind Part A §3b | published (supporting data) |
| `research/08_context_inheritance/data/inherit/coverage.jsonl` | F1: 180 trials = 6 arms (k = 0…4, and k = 4 truncated) × 3 tasks (C1–C3) × 10; 177 answered, 3 stopped at the round cap (one each at k = 1, 2, 3) | `research/08_context_inheritance/coverage_sweep.py` | Part A §4c, through `coverage_tables.md` | published (raw F1 trials) |
| `research/08_context_inheritance/data/inherit/coverage_tables.md` | F1 tables: arms, step fit, convexity test, truncation contrast | `inherit_analyze.py --coverage … --md` (the F1 block alone) | Part A §4c (cited there as `traces/inherit/coverage_tables.md`) | published |
| `research/08_context_inheritance/data/inherit/budget_sweep.md`, `research/08_context_inheritance/data/inherit/budget_sweep.json` | F2: 6 orderings × 10 budgets + unlimited over 26 untreated successors from 15 baseline runs (the six `none` runs here and the nine team runs in `research/07_task_dag/data/dag_redundancy/`); the JSON holds `n`, `budgets` and the full `grid` | `research/08_context_inheritance/budget_sweep.py … --md --json` | Part A §4b (cited as `traces/inherit/budget_sweep.md`); deck slide 7 | published; both files regenerate byte-identically (re-run 2026-09-24) |
| `<label>.console.log` (not present) | `inherit_workloads.py` sends each run's driver output to `<label>.console.log` beside the trace; the file type is git-ignored (`*.log`) and none is in this checkout | `inherit_workloads.py` | — | not kept |

### `research/08_context_inheritance/data/realworld/` — Part B replay

| item | what it holds | written by | used in | status |
|---|---|---|---|---|
| `research/08_context_inheritance/data/realworld/open_swe/` | 1,200 trajectories of `nvidia/Open-SWE-Traces` v1.1 in canonical read-item form, one file per harness | `research/08_context_inheritance/traj_ingest.py --corpus open_swe --split <split> --limit 400` | Part B §2–§5 (every SWE-bench number) | input |
| `research/08_context_inheritance/data/realworld/open_swe/sweagent.jsonl` | 400 SWE-agent trajectories (median 76 rounds in `arms_open_swe.md`) | same | same | input |
| `research/08_context_inheritance/data/realworld/open_swe/openhands.jsonl` | 400 OpenHands trajectories (median 75 rounds) | same | same | input |
| `research/08_context_inheritance/data/realworld/open_swe/minisweagent.jsonl` | 400 mini-SWE-agent trajectories (median 51 rounds) | same | same | input |
| `research/08_context_inheritance/data/realworld/hal/` | 11 files, one per HAL run archive (table below); the 7 non-empty ones hold 410 tasks and 6,600 top-level model calls | `research/08_context_inheritance/hal_ingest.py --archive <zip>` from `agent-evals/hal_traces` | Part B §2–§5 (every τ-bench / GAIA number) | input; 4 files empty |
| `research/08_context_inheritance/data/realworld/arms_hal.md`, `research/08_context_inheritance/data/realworld/arms_open_swe.md` | E1-R within one trajectory: none / dag / ancestors over 1,207 (HAL) and 37,226 (SWE) successor rounds, plus a per-harness (per-model) table | `research/08_context_inheritance/replay_sweep.py --stage arms` | Part B §3 Metric 2 (within-trajectory rows), Metric 4 (per-model re-fetch rates), Metric 5 (prompt lengths, rounds per trajectory) | published; regenerate byte-identically (re-run 2026-09-24) |
| `research/08_context_inheritance/data/realworld/cross_hal.md`, `research/08_context_inheritance/data/realworld/cross_open_swe.md` | E1-R across tasks of one group: previous instance (`dag`) vs all earlier instances (`ancestors`); 407 HAL successors in 3 groups, 726 SWE successors (234 repositories, 732 successor trajectories) | `replay_sweep.py --stage cross` | Part B §3 "The seven metrics", Metric 1, Metric 2 (cross-task rows) | published. The tables regenerate identically, but the current script also appends a `_Validity_` line (477 HAL and 11 SWE predecessor slots dropped as the same benchmark task) that these files lack: they were written after the same-task fix and before that line was added, which is already in the first committed `replay_sweep.py` (aded0d0). |
| `research/08_context_inheritance/data/realworld/crossgrid_hal.md`, `research/08_context_inheritance/data/realworld/crossgrid_open_swe.md` | E2-R / E3-R: 16 policies × 8 / 16 / 32 / 64 KB / unlimited on the cross-task population — removable rounds, byte recall, precision, median KB handed over | `replay_sweep.py --stage crossgrid` | Part B §0 item 3, §3 Metric 3, §4 item 1; deck slide 7 | published; rewritten by the 2026-09-23 correction (0b65c46); regenerate byte-identically (re-run 2026-09-24) |
| `research/08_context_inheritance/data/realworld/grid_ancestors.md` | "Budget x policy grid — arm `ancestors`, 240 trajectories": a within-trajectory grid (whole-observation and line-level removable rounds, byte recall, precision, priced latency) | an early `replay_sweep.py --stage grid --arm ancestors` pass; committed in aded0d0 | cited nowhere | stale: computed on 240 trajectories (which ones is not recorded), not regenerated after the full ingest or the 2026-09-23 policy fix; its values (e.g. 1.9% at unlimited budget, whole observation) appear in no note |

The 11 files of `research/08_context_inheritance/data/realworld/hal/`:

| file | benchmark · agent (model) | tasks | model calls | read items |
|---|---|---|---|---|
| `corebench_hard_hal_generalist_agentdeepseekv30324_1755710007.jsonl` | CORE-bench hard · HAL Generalist Agent (DeepSeek-V3-0324) | 45 | 458 | 0 |
| `gaia_hal_generalist_agent_o320250416_1753903002slim.jsonl` | GAIA · HAL generalist agent (o3, by the file name) | 0 bytes | — | — |
| `gaia_hal_generalist_agent_o3mini20250131_high_1744670471.jsonl` | GAIA · HAL Generalist Agent (o3-mini-2025-01-31 high) | 121 | 687 | 308 |
| `scicode_scicode_zero_shot_agent_claude37sonnet20250219_1745345545.jsonl` | SciCode · zero-shot agent (claude-3.7-sonnet, by the file name) | 0 bytes | — | — |
| `scienceagentbench_sab_selfdebug_claudeopus41_1755385889.jsonl` | ScienceAgentBench · self-debug agent (claude-opus-4.1, by the file name) | 0 bytes | — | — |
| `swebench_verified_mini_my_agenttogether_aideepseekaideepseekr1_1745262492.jsonl` | SWE-bench Verified mini · a submitted agent (DeepSeek-R1 via Together AI, by the file name) | 0 bytes | — | — |
| `taubench_airline_taubench_toolcalling_claude37sonnet_1760379497.jsonl` | τ-bench airline · Taubench ToolCalling (claude-3.7-sonnet) | 49 | 1,018 | 272 |
| `taubench_airline_taubench_toolcalling_claudeopus41_1760380824.jsonl` | τ-bench airline · Taubench ToolCalling (claude-opus-4.1) | 50 | 1,025 | 256 |
| `taubench_airline_taubench_toolcalling_deepseekv3_1760385714.jsonl` | τ-bench airline · Taubench ToolCalling (deepseek-v3) | 49 | 1,011 | 240 |
| `taubench_airline_taubench_toolcalling_gemini20flash001_1760387340.jsonl` | τ-bench airline · Taubench ToolCalling (gemini-2.0-flash-001) | 49 | 1,320 | 226 |
| `taubench_airline_taubench_toolcalling_gpt4120250414_1760371184.jsonl` | τ-bench airline · Taubench ToolCalling (gpt-4.1-2025-04-14) | 47 | 1,081 | 253 |

Why four files are empty: `hal_ingest.py` opens the output file before it looks at the records, and `ingest()` drops every task whose conversation parses to no round (and yields nothing when an archive has no top-level call records). A zero-byte file is therefore an archive that was downloaded and ingested with no task surviving; it adds nothing to any table. Which cause applies to which archive is not determinable from the repo (the archives were not kept); Part B §5 says SciCode's agent is zero-shot, i.e. has no rounds. The second GAIA archive, ScienceAgentBench and SWE-bench Verified mini are not mentioned in any note.

### `research/08_context_inheritance/data/lmeval/` — Part B G1

| item | what it holds | written by | used in | status |
|---|---|---|---|---|
| `research/08_context_inheritance/data/lmeval/census.jsonl` | 15 rows = 5 lm-eval tasks × 3 documents (000–002), arm `none`, unlimited budget, all `tasks=0, edges=0, depth=0`; runs 2026-09-18 21:54–22:18 UTC | `research/08_context_inheritance/lmeval_workloads.py --census --limit 3` (prompts rendered by lm-eval 0.4.13 plus the answer contract from `lmeval_agent_model.py`) | Part B §3 G1; deck slides 9 and 13 | published |
| `research/08_context_inheritance/data/lmeval/gsm8k` | 4 traces — the 3 census runs and the duplicate `run_20260918T215844_735183Z_6506f373` — plus `.prompt.txt` / `.answer.json` for documents 000–002 | `lmeval_workloads.py` → `research/common/profile_run.py` | Part B G1 | published (+1 duplicate) |
| `research/08_context_inheritance/data/lmeval/longbench2_academic_multi` | 3 census traces; document 001's prompt is the 1.59 MB document that triggered compaction before the first round | same | Part B G1 | published |
| `research/08_context_inheritance/data/lmeval/longproc_path_traversal_2k` | 3 census traces | same | Part B G1 | published |
| `research/08_context_inheritance/data/lmeval/longproc_travel_planning_2k` | 4 traces — 3 census runs and the duplicate `run_20260918T215902_005223Z_81abc754` | same | Part B G1 | published (+1 duplicate) |
| `research/08_context_inheritance/data/lmeval/mmlu_pro_biology` | 4 traces — 3 census runs and the duplicate `run_20260918T215818_438503Z_4464c039` | same | Part B G1 | published (+1 duplicate) |
| the three duplicates: `research/08_context_inheritance/data/lmeval/gsm8k/run_20260918T215844_735183Z_6506f373`, `research/08_context_inheritance/data/lmeval/longproc_travel_planning_2k/run_20260918T215902_005223Z_81abc754`, `research/08_context_inheritance/data/lmeval/mmlu_pro_biology/run_20260918T215818_438503Z_4464c039` | Re-runs of the first three census cells, with the same labels (`mmlu_pro_biology-…-r1-001`, `gsm8k-…-r1-001`, `longproc_travel_planning_2k-…-r1-000`), made 21:58–22:01 UTC, between the first launch's three cells (21:54–21:56; why it stopped there is not recorded) and the rest of the census (22:01–22:18). The resumed census re-ran every completed cell and then discarded the result — the bug recorded as fixed by the "skip BEFORE spending the call" comment in `lmeval_workloads.py`'s main loop. `census.jsonl` keeps the first launch's rows, but the three `.answer.json` / `.prompt.txt` files of those labels were overwritten by the re-runs (their `wall_seconds` 16.21, 25.25 and 135.31 are the duplicates'). None of the three created a task either. | `lmeval_workloads.py` (resume before the fix) | not used | duplicate |

### `research/08_context_inheritance/data/lmeval_smoke/` — Part B §2

| item | what it holds | written by | used in | status |
|---|---|---|---|---|
| `research/08_context_inheritance/data/lmeval_smoke` | only `gsm8k/` | — | — | smoke |
| `research/08_context_inheritance/data/lmeval_smoke/gsm8k` | 3 runs, 2026-09-18 21:44–21:46 UTC, labels `gsm8k-none-lru-unlimited-r1-0000` … `-0002` (the four-digit label format of `lmeval_agent_model.py`), each a trace, `.inputs.jsonl` and `.answer.json`; completed in 19.3, 31.9 and 15.6 s; the answers end `#### 18`, `#### 3` and `#### 70000`, gsm8k's gold answers for its first three test questions | `lm_eval.simple_evaluate(model="s15-agent", tasks=["gsm8k"], limit=3)` through `research/08_context_inheritance/lmeval_agent_model.py` (Part C §5, "the lm-eval endpoint, end to end") | Part B §2 "Verified end to end: 3/3 on `gsm8k`"; deck slide 13 | smoke: the endpoint check, run about 10 minutes before the census; lm-eval's own results output was not kept |

### `research/08_context_inheritance/data/codebase/` — Part B G2, G2b, E1-L

Target: `lab-group-56`, an external ECE 4750 lab repository that is not in this repo. MODIFY runs edited a sandbox copy at `profiling_sandbox/codebase/`, wiped and re-seeded from the target before each run (`--sandbox-from`); it is not in the repo either.

| item | what it holds | written by | used in | status |
|---|---|---|---|---|
| `research/08_context_inheritance/data/codebase/runs.jsonl` | 20 rows: the census, 2026-09-19 01:19–03:04 UTC (MODIFY none r1–r3, EXPLAIN none r1–r3), and the arm sweeps, 2026-09-20 02:33–06:49 UTC (MODIFY dag and ancestors r1–r4 plus none r4 = 9 new cells; EXPLAIN ancestors r1–r4 plus none r4 = 5 new cells) | `research/08_context_inheritance/codebase_workloads.py --census`, then one `--arms … --reps 4` sweep per workload | Part B §3 G2 (census rows), G2b, E1-L (the 12 MODIFY cells), E1-L on EXPLAIN (the 8 EXPLAIN cells); deck slides 9 and 10 | published |
| `research/08_context_inheritance/data/codebase/EXPLAIN` | 9 traces (each with `.inputs.jsonl`, `.reads.jsonl`) and `.prompt.txt` / `.answer.json` / `.prewarm.json` for none and ancestors r1–r4; 8 traces are `runs.jsonl` rows, 1 is a pilot | `codebase_workloads.py` → `research/common/profile_run.py` | as above | published (+1 pilot) |
| `research/08_context_inheritance/data/codebase/MODIFY` | 15 traces (with sidecars) and `.prompt.txt` / `.answer.json` / `.prewarm.json` for none, dag and ancestors r1–r4, plus `MODIFY-none-lru-unlimited-r6.prompt.txt` / `.answer.json`; 12 traces are `runs.jsonl` rows, 3 are orphans | same | as above | published (+3 orphans) |
| `research/08_context_inheritance/data/codebase/EXPLAIN/run_20260919T004636_271811Z_68025e88` | Pilot, 2026-09-19 00:46 UTC, label `EXPLAIN-none-lru-unlimited-r1`, aimed at `agents` — this repository's `agents/` folder, the script's `--target` default — not lab-group-56, with an earlier item text ("Inventory every module under agents …"); timed out at 604.6 s with 2 of 4 tasks complete. Its `.prompt.txt` / `.answer.json` / `.prewarm.json` were later overwritten by the census run of the same label; the trace's `profile_meta` keeps the prompt. | `codebase_workloads.py` (earlier version) | not used; with the MODIFY pilot it is "the first two runs" of the script's deadline comment (a 600 s cap, 2 of 4 and 1 of 4 items), from which the 1,500 s / 2,100 s deadlines were set | pilot |
| `research/08_context_inheritance/data/codebase/MODIFY/run_20260919T005642_012386Z_5cbeea79` | Pilot, 00:56 UTC, label `MODIFY-none-lru-unlimited-r1`, `sandbox_from: agents`, with an earlier item text (a `MAX_LOOP_ROUNDS` cap on the agent loops under `agents`). The prompt named `agents` rather than the sandbox copy: 9 of 22 `edit_file` calls were denied (every attempt on `agents/*.py`) and 13 were applied in the sandbox; timed out at 670.8 s with 1 of 4 tasks complete. Its label files were likewise overwritten by the census run `MODIFY-none-lru-unlimited-r1`. The sandbox-path substitution in `build_prompt` ("If the prompt kept pointing at the original the agents would spend every round being refused") answers it. | same | not used; the nearest match to Part B §3 G2's "21 of 22" (flagged there) | pilot |
| `research/08_context_inheritance/data/codebase/MODIFY/run_20260919T010918_000833Z_9c8ee113`, `MODIFY-none-lru-unlimited-r6.prompt.txt`, `MODIFY-none-lru-unlimited-r6.answer.json` | 01:09 UTC, label `MODIFY-none-lru-unlimited-r6`, lab-group-56 through the sandbox; the prompt is the census MODIFY prompt apart from its label-derived nonce; completed in 571.2 s with 3 task completions; no `.prewarm.json` and no `runs.jsonl` row. It ran between the MODIFY pilot and the census; why it was labelled r6 and left unrecorded is not determinable from the repo. | `codebase_workloads.py`'s prompt builder (nonce = SHA-1 of the label) | not used | pilot |
| `research/08_context_inheritance/data/codebase/MODIFY/*.sandbox` (12, one per MODIFY cell of `runs.jsonl`) | Committed in aded0d0 as gitlinks (mode 160000, all pointing at commit 63d451ae…, no `.gitmodules`) — empty, broken submodule pointers; removed from git by the cleanup. The edited sandbox trees were never archived; only the traces record the edits. | — | — | removed |

Git-ignored local files: none belongs to this topic — `git status --ignored` lists nothing under `research/08_context_inheritance/`, and the Part A console logs above were never copied into this checkout. Outside the repo: `lab-group-56`, the sandbox, the Python environment `/home/yq335/rw_env` and HF cache of Part C §4, and the downloaded HAL archives.

## Source map

Every section of the three sources is kept whole; the new location is a section of `research/08_context_inheritance/README.md`.

| old file | old section | new location | status |
|---|---|---|---|
| `weekly_progress/092326/inheritance_depth.md` | (H1) How far back up the dependency graph is it worth reaching? — title and preamble | Part A — heading and preamble | kept |
| `weekly_progress/092326/inheritance_depth.md` | 1. The question | Part A §1 | kept |
| `weekly_progress/092326/inheritance_depth.md` | 2. Setup | Part A §2 | kept |
| `weekly_progress/092326/inheritance_depth.md` | 3. Results — the scripted stage (96 trials, 32 per arm) | Part A §3 | kept |
| `weekly_progress/092326/inheritance_depth.md` | §3 › Metric 2 — rounds reduced, and where the value sits | Part A §3 › Metric 2 — rounds reduced, and where the value sits | kept |
| `weekly_progress/092326/inheritance_depth.md` | §3 › Metric 3 — end-to-end latency and its breakdown | Part A §3 › Metric 3 — end-to-end latency and its breakdown | kept |
| `weekly_progress/092326/inheritance_depth.md` | §3 › Metric 4 — accuracy, split into the half each arm was handed | Part A §3 › Metric 4 — accuracy, split into the half each arm was handed | kept |
| `weekly_progress/092326/inheritance_depth.md` | §3 › Metric 5 — how much of the prompt is inherited content | Part A §3 › Metric 5 — how much of the prompt is inherited content | kept |
| `weekly_progress/092326/inheritance_depth.md` | §3 › Per task | Part A §3 › Per task | kept |
| `weekly_progress/092326/inheritance_depth.md` | 3b. Results — the real harness (18 runs, 15 successor tasks per arm) | Part A §3b | kept |
| `weekly_progress/092326/inheritance_depth.md` | §3b › Metric 1 — byte-level recall, measured on the untreated arm | Part A §3b › Metric 1 — byte-level recall, measured on the untreated arm | kept |
| `weekly_progress/092326/inheritance_depth.md` | §3b › Metrics 2 and 3 — rounds and latency: directionally right, statistically absent | Part A §3b › Metrics 2 and 3 — rounds and latency: directionally right, statistically absent | kept |
| `weekly_progress/092326/inheritance_depth.md` | §3b › Metric 4 — accuracy | Part A §3b › Metric 4 — accuracy | kept |
| `weekly_progress/092326/inheritance_depth.md` | §3b › Metric 5 — inherited share of the prompt | Part A §3b › Metric 5 — inherited share of the prompt | kept |
| `weekly_progress/092326/inheritance_depth.md` | 4. What it means | Part A §4 | kept |
| `weekly_progress/092326/inheritance_depth.md` | 4b. Follow-up F2 — given a byte budget, what should the engine keep? (no API calls) | Part A §4b | kept |
| `weekly_progress/092326/inheritance_depth.md` | 4c. Follow-up F1 — the coverage sweep: step or slope? (180 trials) | Part A §4c | kept |
| `weekly_progress/092326/inheritance_depth.md` | 4d. The rule the three results add up to | Part A §4d | kept |
| `weekly_progress/092326/inheritance_depth.md` | 5. Threats and what is not established | Part A §5 | kept |
| `weekly_progress/092326/inheritance_depth.md` | 6. Next steps | Part A §6 | kept |
| `weekly_progress/092326/realworld_inheritance.md` | (H1) Do the inheritance arms survive contact with real harnesses and real datasets? — title and preamble | Part B — heading and preamble | kept |
| `weekly_progress/092326/realworld_inheritance.md` | 0. In one page | Part B §0 | kept |
| `weekly_progress/092326/realworld_inheritance.md` | 1. The questions | Part B §1 | kept |
| `weekly_progress/092326/realworld_inheritance.md` | 2. Setup | Part B §2 | kept |
| `weekly_progress/092326/realworld_inheritance.md` | §2 › lm-evaluation-harness cannot host an agentic task, so it hosts the *grading* instead | Part B §2 › lm-evaluation-harness cannot host an agentic task, so it hosts the *grading* instead | kept |
| `weekly_progress/092326/realworld_inheritance.md` | §2 › Replay corpora | Part B §2 › Replay corpora | kept |
| `weekly_progress/092326/realworld_inheritance.md` | §2 › What a "read" is, defined once | Part B §2 › What a "read" is, defined once | kept |
| `weekly_progress/092326/realworld_inheritance.md` | §2 › Two populations, and they answer different questions | Part B §2 › Two populations, and they answer different questions | kept |
| `weekly_progress/092326/realworld_inheritance.md` | 3. Results | Part B §3 | kept |
| `weekly_progress/092326/realworld_inheritance.md` | §3 › The seven metrics, side by side | Part B §3 › The seven metrics, side by side | kept |
| `weekly_progress/092326/realworld_inheritance.md` | §3 › Metric 1 — what inheritance actually recovers (cross-task, unlimited budget) | Part B §3 › Metric 1 — what inheritance actually recovers (cross-task, unlimited budget) | kept |
| `weekly_progress/092326/realworld_inheritance.md` | §3 › Metric 2 — the retention unit is worth 2–3× on code and nothing on APIs | Part B §3 › Metric 2 — the retention unit is worth 2–3× on code and nothing on APIs | kept |
| `weekly_progress/092326/realworld_inheritance.md` | §3 › Metric 3 — budgets and eviction policies (E2-R / E3-R) | Part B §3 › Metric 3 — budgets and eviction policies (E2-R / E3-R) | kept |
| `weekly_progress/092326/realworld_inheritance.md` | §3 › Metric 4 — measured latency, and how much of the waste is the model's fault | Part B §3 › Metric 4 — measured latency, and how much of the waste is the model's fault | kept |
| `weekly_progress/092326/realworld_inheritance.md` | §3 › G1 — the live arms cannot be run on lm-eval tasks, and that is the finding | Part B §3 › G1 — the live arms cannot be run on lm-eval tasks, and that is the finding | kept |
| `weekly_progress/092326/realworld_inheritance.md` | §3 › Metric 5 — prompt length on real harnesses | Part B §3 › Metric 5 — prompt length on real harnesses | kept |
| `weekly_progress/092326/realworld_inheritance.md` | §3 › G2 — codebase work, where the lead does build a graph | Part B §3 › G2 — codebase work, where the lead does build a graph | kept |
| `weekly_progress/092326/realworld_inheritance.md` | §3 › G2b — mutation is not the mechanism; coverage is | Part B §3 › G2b — mutation is not the mechanism; coverage is | kept |
| `weekly_progress/092326/realworld_inheritance.md` | §3 › E1-L — the three arms, measured live on a real codebase | Part B §3 › E1-L — the three arms, measured live on a real codebase | kept |
| `weekly_progress/092326/realworld_inheritance.md` | §3 › E1-L on EXPLAIN — the high-headroom case is too noisy to read | Part B §3 › E1-L on EXPLAIN — the high-headroom case is too noisy to read | kept |
| `weekly_progress/092326/realworld_inheritance.md` | 4. What it means | Part B §4 | kept |
| `weekly_progress/092326/realworld_inheritance.md` | 5. Threats and what is not established | Part B §5 | kept |
| `weekly_progress/092326/realworld_inheritance.md` | 6. Housekeeping | Part B §6 | kept |
| `weekly_progress/092326/realworld_inheritance.md` | §6 › Next steps | Part B §6 › Next steps | kept |
| `s15_integrated_harness/realworld_inheritance_profile.md` | (H1) Real-world inheritance profiling — pipeline and reproduction — title and preamble | Part C — heading and preamble | kept |
| `s15_integrated_harness/realworld_inheritance_profile.md` | 1. Why a new pipeline | Part C §1 | kept |
| `s15_integrated_harness/realworld_inheritance_profile.md` | 2. Modules | Part C §2 | kept |
| `s15_integrated_harness/realworld_inheritance_profile.md` | 3. Design decisions that decide the numbers | Part C §3 | kept |
| `s15_integrated_harness/realworld_inheritance_profile.md` | 4. Environment | Part C §4 | kept |
| `s15_integrated_harness/realworld_inheritance_profile.md` | 5. Reproduce | Part C §5 | kept |
| `s15_integrated_harness/realworld_inheritance_profile.md` | 6. Known gaps | Part C §6 | kept |

## Cleanup notes

- **Part A §2 — flag.** "Facts were verified to be split": the script that verified it (`inherit_check.py`, named in the pre-cleanup `inherit_loop.py`) was never committed — `git log --all` finds no such file — so the claim cannot be re-checked from the repo. F1's equivalent gate is committed (`check()` in `research/08_context_inheritance/coverage_sweep.py`, which refuses to run a task set with clashing facts), so Part A §4c's "verified window-unique" is backed by code.
- **Part A §3b — cross-reference note.** The under-injection bug that Part B §6 lists (predecessors' `bash` reads recorded, only `read_file` ones injected; "~45% less than the recall denominator credited them with") applies to this stage, as the 092326 deck says on slide 6; the note points there. No number changed.
- **Part B §2 — flag (repeated briefly in §3 "The seven metrics" and §3 Metric 4).** The HAL figures "5,142 model calls", per-call "median 2.09 s, p90 19.4 s", and a τ-bench task's "101.5 s of which 101.1 s is model time" are not reproducible from the committed data, and no committed script or table computes them (`replay_sweep.py` has no latency stage). The files in `research/08_context_inheritance/data/realworld/hal/` give 410 tasks (matching) and 6,600 top-level model calls; per call, a median of 2.06 s and p90 of 17.3 s over all of them (1.85 s and 16.1 s over τ-bench + GAIA only); a mean per-task span of 81.8 s with 81.6 s of model time on τ-bench (104.4 s and 101.0 s over all HAL tasks). Row 7 of the seven-metrics table (~1.0 s, ~3.1 s) is priced with the 2.09 s figure. The 092326 deck quotes "410 tasks, 5,142 calls" on slide 8.
- **Part B §3 Metric 2 — flag.** In the coverage-threshold table only the 95% row matches a committed output (`research/08_context_inheritance/data/realworld/cross_open_swe.md`); the 80% and 50% rows and the 24,386-round count are in no committed output, and `replay_sweep.py` hard-codes `COVER_ITEM = 0.95`.
- **Part B §3 Metric 5 — flag.** "49–76 rounds (median)": the committed per-harness medians are 51, 75 and 76 (`research/08_context_inheritance/data/realworld/arms_open_swe.md`; 51.0, 75.0 and 75.5 recomputed from the ingested files), so the 49 is not reproduced. §3's "70 rounds (SWE-bench, median)" does match (69.5 over the 1,200 trajectories).
- **Part B §3 G2 — flag (requested check).** "verified: a run whose prompt still named the original path had 21 of 22 `edit_file` calls denied and the tree stayed byte-identical" — no trace matches. Counting `tool_start` / `tool_end` events with `tool == "edit_file"` over all 24 codebase traces gives 168 calls, 10 of them denied (9 in the pilot `research/08_context_inheritance/data/codebase/MODIFY/run_20260919T005642_012386Z_5cbeea79.jsonl`, 1 in `…/MODIFY/run_20260920T042019_685543Z_30f6c6b3.jsonl`); `write_file`: 71 calls, 21 denied; no `edit_file` or `write_file` succeeded outside `profiling_sandbox/codebase/`. The pilot is the only run whose prompt named an original path, and that path was this repository's `agents/` (`sandbox_from: agents`), not lab-group-56: its 22 `edit_file` calls split into 9 denied (all on `agents/*.py`, matching its `profile_end` `denials: {"edit_file-outside-root": 9}`) and 13 applied to the sandbox copy. No trace anywhere in the repo has more than 9 denied `edit_file` calls. "Byte-identical" is checkable only for `agents/`, which has no commit after 2026-08-26. The deck repeats the claim on slide 9 ("the original tree was verified byte-identical afterwards").
- **Part B §3 Metric 3 — verified, no edit (requested check).** The corrected table matches the committed grids cell for cell (14 rows × 6 columns): at 8 KB, τ-bench/GAIA GDSF 20.8%, LFU 19.2%, LRU 15.8% (`research/08_context_inheritance/data/realworld/crossgrid_hal.md`) and SWE-bench GDSF 2.9%, LFU 2.6%, LRU 0.3% (`research/08_context_inheritance/data/realworld/crossgrid_open_swe.md`). The pre-correction values quoted in the correction paragraph (LFU 21.9% / LRU 3.0%; 4.0% / 0.0%; keep-costliest 11.1% / 2.3%; graph-ordered 0.7% on SWE) are those of the aded0d0 versions of the same two files, and both files regenerate byte-identically with the current `replay_sweep.py`. Other folders should cite the corrected values from here: `research/09_efficiency/README.md` Part A (formerly `weekly_progress/efficiency_methodology.md`, line 141) and Part B (formerly `weekly_progress/efficiency_impl_report.md`, line 139) still quote "LFU 21.9% vs LRU 3.0%"; `weekly_progress/092326/conclusion.md` has meanwhile been given `[corrected 2026-09-23 …]` notes pointing here (uncommitted cleanup edits; the committed version carries none).
- **Regeneration checks (2026-09-24, offline, outputs discarded).** `budget_sweep.py` reproduces `budget_sweep.md` and `budget_sweep.json` byte for byte; `replay_sweep.py --stage arms|cross|crossgrid` on both corpora reproduces `arms_*` and `crossgrid_*` byte for byte, and `cross_*` except for the `_Validity_` line the script now appends. `inherit_analyze.py` was not re-run, because it imports `inherit_loop.py`, which the cleanup rules forbid running; by its code, `--coverage` appends the F1 block, which the committed `inherit_tables.md` lacks and `coverage_tables.md` holds.
- **Reproduction caveat.** `inherit_loop.py`'s `grep` tool, which `coverage_sweep.py` reuses, scans every `.md` / `.py` file directly in `s15_integrated_harness/`. The cleanup moves the profile reports out of that folder, so reruns of the scripted stage or F1 search a smaller corpus than the recorded trials did; tag `pre-cleanup-2026-09-23` has the original tree.
- **Removed from git:** the 12 `research/08_context_inheritance/data/codebase/MODIFY/*.sandbox` gitlinks (see Data inventory).
- **Paths.** Paths in the verbatim text were rewritten to the 2026-09-23 layout (scripts, data and merged notes point to `research/…`); bracketed notes, `_Formerly …_` lines and the Source map keep the old names on purpose.
- **Scope of the flags.** The two requested checks were made in full; the other flags come from cross-checks made while building the data inventory and are not an audit of every number.
- **Nothing was dropped.** All three sources are bases and appear whole; only their H1 lines gained the `Part X — ` prefix, and the merge check needed no exceptions.
