# How far back up the dependency graph is it worth reaching?

Weekly progress, 2026-09-23. Full tables: `s15_integrated_harness/traces/inherit/inherit_tables.md`.
Tooling: `s15_integrated_harness/scripts/inherit_workloads.py` (the two team workloads and the
grader), `scripts/inherit_loop.py` (the scripted stage), `scripts/inherit_analyze.py` (all five
metrics), and a third injection arm `--prewarm ancestors` added to `scripts/profile_run.py`.
Traces: `s15_integrated_harness/traces/inherit/`. Provider: z.ai `glm-5.3-flash`.
Companion notes: `dag_kv_reuse.md` (which established that handing a successor its predecessor's
bytes removes rounds) and `dag_census.md` (which established that the Lead builds the graph itself
on genuinely staged work).

Terminology as in the 2026-09-09 deck: a *turn* is one lead activation, a *round* is one agent-loop
iteration.

## 1. The question

`dag_kv_reuse.md` showed that handing a successor the content its **direct** predecessors read
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
these coverage rates is to keep everything that fits. That agrees with `dag_kv_reuse.md`, which found
the same ordering from the other direction: every-earlier-task recalls far more than the graph-derived
sets, and precision is nearly free to give up.

**Accuracy was never the constraint.** 100% in all three harness arms, and in the scripted stage the
fuller handoff was the *most* accurate (99% against 88%), gaining exactly where it was given content
the others lacked. Nothing here trades correctness for speed.

## 4b. Follow-up F2 — given a byte budget, what should the engine keep? (no API calls)

Every policy above is defined by graph position. A real engine is constrained by **bytes**. This
recomputes over the 26 baseline successors already on disk (`traces/inherit/` +
`traces/dag_redundancy/`) and asks the deployable question: at equal budget, does a graph-aware
ordering beat plain recency? The unit of retention is one whole read item, because an engine holds or
drops whole spans. Tables: `traces/inherit/budget_sweep.md`; tool: `scripts/budget_sweep.py`.

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
is never confounded with "which one". Tool: `scripts/coverage_sweep.py`; tables:
`traces/inherit/coverage_tables.md`.

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
`dag_kv_reuse.md`), reaching further along it does not convert into rounds (metric 1), and as a
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
