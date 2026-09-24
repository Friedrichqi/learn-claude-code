# Week of 2026-09-16 — conclusion: inheritance removes the rounds it can reach,
# and reading is under half of agentic work

Synthesises the four notes in this folder: `dag_census.md`, `dag_kv_reuse.md`,
`inheritance_depth.md`, `realworld_inheritance.md`. Written 2026-09-21. [cleanup note 2026-09-23: the
four notes now live in `research/07_task_dag/README.md` (Part A dag_kv_reuse, Part B dag_census) and
`research/08_context_inheritance/README.md` (Part A inheritance_depth, Part B realworld_inheritance).]

## The question

If a successor task is about to read what its predecessor already read, hand it the bytes instead of
letting it spend a round fetching them. Does that remove agentic rounds and end-to-end latency, and
how far up the dependency graph is it worth reaching?

## The answer, in one line

**Handing over the bytes removes read-rounds efficiently — and read-rounds are a minority of what an
agent does, so total rounds move far less than the byte numbers suggest.** In the live sweep the
closure cut reads per successor 60% and mean rounds 25% (13.0 → 9.8), which is **58% of the
structural ceiling**: only 42% of a successor's rounds are pure reads, 45% contain no read at all,
and 13% mix a read with work that has to happen anyway. No round or latency interval in any real
harness excludes zero at the sample sizes reached; the only effect that does is a **token cost**.

## The evidence, and why it points one way

| population | what it measured | rounds removed |
|---|---|---|
| scripted single agent (n=128, 096) | 100% coverage by construction | 8.00 → 1.50, **−81%** |
| s15 team harness, synthetic (n=18) | lead-built boards, 24–39% coverage | every CI straddles 0 |
| replay, SWE-bench cross-task (n=726) | real agents, no shared context | 2.1% (dag), 4.3% (ancestors) |
| replay, τ-bench + GAIA cross-task (n=407) | real agents, shared database | 12.7% (dag), 38.2% (ancestors) |
| live, MODIFY on `lab-group-56` (n=18 successors) | lead-built depth-3 boards | 13.0 → 12.5 → 9.8 mean (−25%), CIs straddle 0 |

The scripted stage is the outlier, and it is the one whose coverage was guaranteed by construction.
Everywhere coverage had to be earned, rounds barely moved.

**The mechanism is quantisation, bounded by round composition.** A round disappears only when
*everything* it would have fetched is already held — so a round issuing two reads needs both, and
30.6% of baseline successor rounds issue two or more. Those fall to 6.8% under the closure, which is
the clearest sign the mechanism is real: complete coverage is exactly what kills a multi-read round.
But the ceiling is set by composition, not coverage: 44.7% of a successor's rounds read nothing at
all and 12.9% mix a read with an edit or a test run that still has to happen. Of the 5.5 rounds that
were structurally removable, the closure removed 3.2. The coverage sweep found the same shape from
the other side: supplying one, two or three of four needed documents was indistinguishable from
supplying none, and the fourth was worth 5.70 rounds.

## What changed when the workloads became real

Four beliefs from the synthetic study did not survive.

1. **Whole-observation reuse is ~1%, not 24–39%.** On real SWE work a successor finds 0.8–1.2% of its
   bytes already fetched. The synthetic 24–39% came from agents re-reading four small documents.
2. **The direct-predecessor arm is not "the worst of the three".** On τ-bench/GAIA it buys 12.7% of
   rounds for **497 tokens** where the closure buys 38.2% for **50,858** — the extra 25 points cost
   102× the tokens. The earlier verdict was an artefact of workloads built so the needed fact sat two
   hops back.
3. **Mutation is a contributor, not the mechanism.** The EXPLAIN/MODIFY pair isolates it: same
   codebase, same harness, same model, only writing differs. Stale spans are **0.0% and 0.9%**. What
   separates the two workloads is **coverage** — 62.6% of MODIFY's reads are files no predecessor
   opened, against 45.7% for EXPLAIN.
4. **The retention unit is worth more than the retention policy's reach.** At file granularity 36.4%
   of a successor's rounds are removable; at byte-range granularity, 90.9%. Line granularity adds
   nothing beyond range.

## What is deployable

1. **Rank by reuse frequency, not recency.** At an 8 KB budget LFU removes 21.9% of rounds on
   τ-bench/GAIA and 4.0% on SWE-bench; LRU removes 3.0% and **0.0%**. [corrected 2026-09-23: the
   corrected replay gives LFU 19.2% against LRU 15.8% on τ-bench/GAIA and 2.6% against 0.3% on
   SWE-bench; see research/08_context_inheritance/README.md Part B, metric 3.] Recency truncation and
   last-N-round windows — what production harnesses actually ship — are the worst deployable family
   tested.
2. **Spend the budget on prediction, not capacity.** With perfect foresight 8 KB already buys the
   entire unlimited-budget value (38.0 of 38.2; 4.2 of 4.3). Every gap is a prediction failure.
3. **Key on content and on ranges, never on paths.** An exact-observation key finds almost nothing
   (agents re-issue a byte-identical read 0.4–2.6% of the time) while 86% of the bytes sit in files a
   predecessor already opened.
4. **Widen the pool before tuning the policy.** Removable rounds scale 13% → 23% → 36% as the pool
   grows 8 → 16 → 32 earlier tasks, roughly linear in the log.
5. **Do not partition the pool per agent.** Size-matched, inheriting from a different model's runs is
   as good as inheriting from your own, within ~2 points at every pool size.

## What is retired

- **DAG-directed retention.** The task graph failed a third independent test: as a priority ordering
  under budget it sits near the bottom of both policy tables (0.7% on SWE at 8 KB). [corrected
  2026-09-23: the first replay ran `graph-ordered` as plain recency; in the corrected replay
  graph-ordered (previous task first) removes 21.9% on τ-bench/GAIA and 1.4% on SWE-bench at 8 KB —
  research/08_context_inheritance/README.md Part B, metric 3.] It does not
  predict which bytes are wanted, reaching further does not convert to rounds, and it is useless as
  an ordering. The artefact worth shipping to an engine is a per-item reuse count, not the board.
- **"Evict what is simplest to recover."** Keeping the costliest-to-recover items reaches 11.1% and
  2.3% at 8 KB, below LFU, GDSF and S3-FIFO everywhere. [corrected 2026-09-23: 10.0% and 1.3% in the
  corrected replay, research/08_context_inheritance/README.md Part B, metric 3.] Recovery cost correlates with size and the
  large items are not the reused ones. It must also be **measured per item**: with a modelled cost,
  which is monotone in bytes, the policy silently becomes "keep the largest".
- **lm-evaluation-harness as a source of agentic tasks.** It has none and cannot express one. Wrapped
  as a model endpoint it grades fine (3/3 on gsm8k through full agentic sessions), but its tasks
  produced **0 task boards in 15 runs** — a benchmark question is one deliverable, and one
  deliverable cannot be decomposed.

## What real project work does that benchmarks do not

Codebase tasks produce boards every time: **20 of 20 runs, 11 of 20 at depth ≥ 3**, against 0 of 15
on lm-eval. The two shapes differ — **comprehension work fans in** (inventory and trace are
independent, everything after needs both), **modification work chains** (each edit builds on the
last) — and only the chaining shape reaches the depth where `dag` and `ancestors` can differ at all.

## Threats and what is not established

- **The live arms are underpowered and the EXPLAIN cell is unreadable.** Its three successors took
  1, 36 and 2 rounds; the median says rounds fell and the mean says they rose. Coverage as the lever
  is untested, not refuted — it needs roughly 4× these runs.
- **Rounds removed are counterfactual on the replay side**, computed rather than observed.
- **One provider** (z.ai `glm-5.3-flash`) for everything live; one codebase for the live arms.
- **HAL cross-task has only 3 groups**, and its τ-bench runs share one 50-task set.
- **No accuracy effect was measured** on the replay side, though the synthetic study found partial
  coverage *hurts* accuracy (99% → 83%).

## Next steps

1. Power the EXPLAIN arm properly — it is the only cell with the headroom to move rounds, and it is
   the study's open question.
2. Implement range-granular content addressing in the injector. It is the largest measured lever
   (36.4% → 90.9% of rounds) and it is not built.
3. Test the deployable rule end to end: widen the pool, rank by reuse frequency, cap at 8 KB.
4. A staleness-aware policy for long trajectories, where mutation does bite (7.6–14.3% of a SWE
   successor's bytes) even though it does not in short team runs.

## Corrections made to earlier numbers in this folder

- `inheritance_depth.md`'s harness stage was handed ~45% less than its recall denominator credited:
  `profile_run.py` recorded predecessors' `bash` reads but injected only `read_file` ones. Fixed.
- Its "dag 75% re-fetch" line counted *every* read, not re-reads of injected paths. Fixed.
- The first τ-bench cross-task numbers in `realworld_inheritance.md` were inflated by pairing a task
  with itself solved by another model (23.2%/30.5% → 11.2%/12.7%). Fixed and now reported.
- `board_of` read the wrong key and reported every run as edgeless. Fixed; reports now re-derive
  boards from traces so a parser fix repairs old rows.
