# The task DAG as a prefetch signal, and what handing a successor the bytes actually buys

Weekly progress, 2026-09-23. Full report: `s15_integrated_harness/dag_kv_reuse_profile.md`.
Tooling: `s15_integrated_harness/scripts/dag_workloads.py` (four dependency-shaped workloads and the
DAG-execution gate), `scripts/dag_redundancy.py` (task attribution, matched-pair contrast, prefetch
policies), `scripts/prewarm_smoke.py` (provider gate), `scripts/prewarm_loop.py` and
`scripts/prewarm_harness.py` (the two stages of the handoff experiment),
`scripts/prewarm_analyze.py` (tables), and additive `--prewarm*` flags in `scripts/profile_run.py`.
Traces: `s15_integrated_harness/traces/dag_redundancy/` and `traces/prewarm/`.
Provider: z.ai `glm-5.3-flash`. Companion notes: `../091626/tiered_memory_conclusion.md` next steps
5, 7 and 13.

Terminology as in the 2026-09-09 deck: a *turn* is one lead activation, a *round* is one agent-loop
iteration.

## 0. In one page

**The idea being tested.** When the lead splits work into tasks, some tasks depend on others. If a
successor is likely to open the same files its predecessor opened, a serving system could hand the
successor that content (its KV) instead of making it spend agent-loop rounds opening the files again.
Two questions follow: *does the dependency graph tell you which content to keep* (experiment 1), and
*is handing it over actually worth anything* (experiment 2).

**What the five workloads are.** Each is one lead prompt that makes the lead build a task board of a
particular shape. They differ only in shape, so that the shape is the variable:

| id | shape | why it exists |
|----|-------|---------------|
| **W1 CHAIN** | two 2-step chains over two different files, plus two unattached audits | the normal case: A must finish before B |
| **W2 FANIN** | three audits of *one* file, then a synthesis that waits for all three | the join case |
| **W5 FANIN-DISJOINT** | three inventories of *three different* files, then a synthesis | the only shape where the successor is genuinely missing something |
| **W3 PIPELINE** | each step reads the previous step's *output file*, not its input | **control**: dependencies that by construction share no source files |
| **W4 PLACEBO** | W1's text word for word, but the board edges wired to the **wrong** branch | **the decisive test**: does overlap follow the graph, or the words? |

W3 and W4 are built to show zero. They are there so that a positive result in W1/W2/W5 can be
attributed to the dependency rather than to "these tasks were about similar things".

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

**The three results, in plain terms.**

1. **The dependency edge is a good predictor -- but only because it repeats what the task text
   already says.** In aligned boards an edge predicts +46 points more content overlap than an
   unrelated earlier task. In W4, where the text says one thing and the board says another, the board
   edges predict **nothing at all** (-1.0, both runs): the agent read what its *description* pointed
   at. So a serving system that is shown only the graph is reading a shadow.
2. **The crudest policy wins anyway.** "Keep everything every earlier task read" recalls 85.6% of
   what a successor needs; "keep what its direct predecessors read" recalls 36.0%. Sending three
   quarters more than needed costs 3.9 s of prefill against 63 s of rounds removed, so there is no
   precision argument to set against that. **The engine-tier question is capacity, not prediction.**
3. **Handing over the content is worth a lot.** A successor given the bytes runs in 2.50 rounds
   instead of 7.22 (scripted, n=128) and 3.00 instead of 5.17 (real harness, n=6/arm), with
   correctness no worse. Two controls say the bytes are what does it: an equal-size **irrelevant**
   file changes nothing and actually cost 2 of 5 successors their task, and a **summary** of the
   predecessor's findings is worth nothing measurable in either stage.

**The one operational thing to fix.** `spawn_teammate` claims a task before starting the agent, and a
claim on a blocked task is refused -- so a lead that assigns a successor at spawn time loses it
silently. In the first probe the synthesis item was never claimed by anyone.

## 1. Questions

The 2026-09-16 deck closes on a proposal — emit a retention directive to the engine from the task
board, because "the task board is a dependency graph the engine cannot see". That proposal assumes
something nobody had measured: that the graph says anything about which bytes are wanted next. Two
experiments, in the order a serving system would need them answered:

1. **When to reuse.** For a predecessor/successor pair on the board, is the successor more likely to
   read what the predecessor read than an *unrelated task in the same run* is? And is the signal in
   the graph, or merely in the task text that a cheaper mechanism could read?
2. **How to reuse, and whether it pays.** If the successor is handed the predecessor's file content
   instead of opening the file itself, how many agent-loop rounds disappear — and does the effect
   survive a negative control that injects an equal-size irrelevant file?

## 2. Experiments

**Setup.** z.ai `glm-5.3-flash` through the real s15 harness, driven non-interactively by
`profile_run.py` with full tool output and the content sidecar. Nine team runs over five
dependency-shaped workloads, plus a 128-trial scripted micro-loop and a second set of harness runs
for the handoff arms. Constants carried in from `../091626/latency_breakdown.md`: a model call costs
3.72 s + 0.033 ms per uncached prompt token + 17.44 ms per output token.

**Three things had to be fixed before anything could be measured**, and all three were found by
running rather than by reading:

1. The existing corpus cannot answer question 1. `blockedBy` is empty in all 31 team runs that carry
   byte-level read data; every workload written for this series so far was deliberately flat.
2. The lead does not emit a dependency edge unless the prompt names the mechanism
   (`update_task(addBlockedBy=...)`). The harness system prompt already says this and it is not
   enough.
3. Creating an edge and executing one are different things. `spawn_teammate` claims a task before
   starting the agent, and a claim on a blocked task is refused, so `spawn_teammate(task_id=<blocked>)`
   fails silently and that item is never picked up. Successors reach an agent only through an idle
   teammate's auto-claim. After rewriting the workloads around that, executed-edge yield was 1.0 in
   seven of nine runs.

**Design.** Raw overlap rates are not evidence here -- every earlier workload in this series produced
exactly the redundancy its task cut implied. The headline is a matched contrast: each edge (p, s)
against a non-adjacent control (p', s) with the *same successor*, which cancels the successor's read
volume, its corpus and its position in the run. Significance is a run-clustered sign-flip
randomisation with a cluster bootstrap; a validity block (within-run variance, base rate, executed
yield, same-owner share, provenance, confound correlations) is printed above every number.

For experiment 2 the KV transfer is emulated by handing the successor the same *tokens*: the exact
assistant `tool_use` / user `tool_result` pair a real `read_file` would have produced, prepended to
its history and rebuilt byte-identically on every call. A provider gate confirmed first that z.ai
accepts a fabricated tool_use and answers from it without calling a tool (five shapes, all accepted).

## 3. Findings

**1. The dependency edge predicts well -- but only because it echoes the task text.** Matched on the
successor, an edge in an *aligned* board (W1, W2, W5) covers **+46 points** more of the successor's
read bytes than a non-adjacent control that finished before the same successor started (95% CI
[+0.27, +0.68], 5/5 runs positive) and +37 points of removable rounds. In the **W4 placebo** -- the
same workload text word for word, with the `addBlockedBy` wiring crossed between branches -- the same
edges cover **0.0%** of their successors' bytes while their matched controls cover everything: a
delta of exactly -1.0 in both reps. The successors kept reading the file their *description* pointed
at, not the one their *predecessor* had read.

The graph is a shadow of the task text, not the carrier of the signal. A serving system shown only
the board -- the proposal the 2026-09-16 deck ends on -- gets the W4 case: a structure that looks
predictive and is not. (Pooling all five workloads gives a flat +0.105, p = 0.68; that number is an
artefact of averaging two designed-zero controls with three aligned workloads and should not be
quoted.)

**2. Broadcast beats the graph, and it is not close.** Over the 11 successor tasks that read
anything: direct predecessors recall 36.0% of the successor's bytes and 50.0% of its removable
rounds; **all earlier tasks recall 85.6% and 77.3%**. The gap holds for cross-owner successors alone
(45.1% / 55.0% against 85.6% / 77.3%). This was the pre-registered decision rule, and it fires
against the hypothesis: a retention directive derived from the task board would keep less, and serve
worse, than one that keeps whatever fits.

**3. Precision is nearly free to give up; recall is the whole game.** Sending the union of every
earlier task's reads is 119k tokens, of which three quarters go unused -- and that costs 3.9 s of
prefill against 63 s of rounds removed. The oracle sends 34k tokens and saves 78 s. At these prompt
sizes the binding constraint on a retention policy is memory capacity, not prediction quality, so the
engine-tier question is "how much can I hold?", not "what should I hold?" -- which is a different
proposal from the one the 2026-09-16 deck ends on.

**4. Intra-agent reuse is real but partial, and the transferable case is common.** Six of thirteen
claimed successors landed on an agent that had already done one of their predecessors; those read 3.7
files on average and got 44.7% of their bytes from the predecessors. The seven that landed on a
fresh agent read *more* (5.7) and got *less* from their predecessors (29.9%). Only 2 of 13 read
nothing at all. An earlier single-run observation that a same-agent successor reads nothing did not
survive the larger sample.

**5. Handing a successor the bytes removes two thirds of its rounds, and the content is what does
it.** 128 scripted trials, four arms, order shuffled: baseline 7.22 rounds and 62.4 s; with the
predecessor's windows injected as a synthetic `read_file` pair, **2.50 rounds and 18.4 s**
(-4.72 rounds, 95% CI [-5.97, -3.47]; -70% wall). Correctness went **up**, 91% to 100% -- the
baseline sometimes gave up searching before it found the answer. Four trials hit the 12-round cap,
all in the baseline and summary arms, so these are lower bounds.

**6. The negative control is clean and the cheap alternative fails.** An equal-size window of an
*unrelated* file moves rounds by +0.31 (CI [-1.03, +1.66]) -- so the effect is the content, not the
presence of a bulky early tool result. And a condensed note summarising the predecessor's findings,
which a harness can already produce today with no cache at all, is worth -0.81 rounds (CI [-2.22,
+0.59]). **A summary does not substitute for the bytes.** That is the comparison that decides whether
any of this needs a cache, and it says yes.

**7. The cost side of a handoff is negative, and the ceiling is behavioural.** The baseline spends
12,998 prompt tokens reaching its answer, mostly tool results from 8.19 locate calls; the injected
arm spends 2,037 including the 644 it was handed. The injection costs 0.02 s of prefill and saves
about 0.36 s of it -- so in the micro-benchmark a handoff is cheaper in tokens than the search it
replaces (finding 8 shows this does not carry into the harness),
which is the same reason precision did not matter in finding 3. The limit is elsewhere: 25% of injected trials went and issued a
`read_file` anyway despite already holding the content -- and that rate is not spread evenly. Three
of the four tasks were handed one contiguous window and re-read 0%, 0% and 12% of the time; the
fourth was handed **two non-contiguous slices of the same file** and re-read **88%** of the time,
which is the whole of the pooled 25% and which cut its own round saving from about 6 to 2.9.
**A fragmented handoff is distrusted; a contiguous one is not.** Hand over whole spans, not a
stitched selection, even when the stitched version is smaller.

**8. The effect survives contact with the real harness.** Sixteen W5 team runs, four arms
interleaved inside each repetition, every successor cross-owner. The deployable arm -- predecessors'
reads captured live in the same run and handed over -- ran the successor in **3.00 rounds against the
baseline's 5.17** (95% CI [-3.50, -0.83]) and cut its active model time from 146.9 s to 113.0 s. Both
controls behaved as they did in the micro-benchmark: the equal-size irrelevant file moved nothing
(-0.17, straddling zero) and the summary moved nothing (+0.63, straddling zero).

The harness adds two things the scripted loop could not show. **The pollution arm is not neutral but
harmful**: only 3 of its 5 successors completed at all (60% against 100% everywhere else) and its
reports cite the fewest files. Filling a successor's context with an equal volume of irrelevant
content costs it the task -- the cleanest demonstration that content, not bulk, is what the working
arms deliver. And **the token argument does not carry over**: the injected arms spend more prompt
tokens than the baseline (30,009 against 14,754), because the policy hands over all three
predecessors' read sets. Latency still improves -- 11.1k tokens is 0.37 s of prefill against 34 s of
active time removed -- but the free-lunch framing belongs to the micro-benchmark, not to the policy.

## 4. Where this leaves the argument

The 2026-09-16 deck ends on a proposal -- emit a per-session retention directive to the engine from
the task board. These experiments say its second half is right and its first half is not.

**Reuse is worth a lot, and it is worth it in rounds.** 4.72 rounds and 44 s per successor task in
the micro-benchmark, 2.17 rounds and 34 s of active time in the harness, at no cost to correctness. That is exactly the shape finding 7 of last
week's synthesis predicted: prefill is under 2% of a call, so what a cache buys is round count.

**But the dependency graph is the wrong key.** It predicts only where it echoes the task text, and
even at its best it is beaten on recall by the crudest possible policy -- keep every earlier task's
reads. Transferring three quarters more than needed costs 3.9 s of prefill against 63 s of rounds
removed, so there is no precision argument to set against that recall gap.

**The engine-tier question is therefore capacity, not prediction.** Hold as much of the session's
read history as fits, evict on capacity alone, and skip the part that works out from the board which
block the next agent will want. If something must be prioritised, the task description is a better
and far cheaper key than the graph.

## 5. Next steps

1. **Re-run where prefill matters.** Every prompt here is 2-14k tokens, where 0.033 ms/token is
   noise. Above ~50k tokens per call the precision argument may come back. This is next step 9 of
   `../091626/tiered_memory_conclusion.md`, still open, and it is now the main threat to finding 3.
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

## 6. Housekeeping

Untracked and ready to commit: `scripts/dag_workloads.py`, `scripts/dag_redundancy.py`,
`scripts/prewarm_smoke.py`, `scripts/prewarm_loop.py`, `scripts/prewarm_harness.py`,
`scripts/prewarm_analyze.py`, the additive `--prewarm*` diff in `scripts/profile_run.py`, a one-line
`call_id` addition and a `collect()` sidecar-filter fix in `scripts/input_redundancy.py`,
`s15_integrated_harness/dag_kv_reuse_profile.md`, this note, and the traces under
`traces/dag_redundancy/`, `traces/dag_redundancy_probe/` and `traces/prewarm/`.

One correction to an older document: `file_read_reuse_profile.md:44` says teammates are always denied
bash. Since commit `968a33c` read-only commands are auto-allowed off the main thread
(`code.py:2016-2019`), so redundancy measurements must count bash reads, which this analyzer does.
