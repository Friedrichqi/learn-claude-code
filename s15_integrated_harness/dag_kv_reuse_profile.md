# Does the task DAG tell a serving system which bytes to keep, and is handing them over worth a round?

2026-09-16/17. Two experiments, one question each, both aimed at the two next steps the
2026-09-16 deck ends on (eviction order at the harness tier, DAG-aware retention at the engine tier).

Tooling: `scripts/dag_workloads.py` (five dependency-shaped workloads + the DAG-execution gate),
`scripts/dag_redundancy.py` (task attribution, matched-pair contrast, prefetch-policy table),
`scripts/prewarm_smoke.py` (does the provider accept a fabricated tool_use?),
`scripts/prewarm_loop.py` (stage 1 micro-loop), `scripts/prewarm_harness.py` (stage 2 in the real
harness), `scripts/prewarm_analyze.py` (tables), and additive `--prewarm*` flags in
`scripts/profile_run.py`. Traces: `traces/dag_redundancy/` and `traces/prewarm/`.
Provider: z.ai `glm-5.3-flash`, the same model as every earlier experiment in this series.

Terminology as in the 2026-09-09 deck: a *turn* is one lead activation, a *round* is one agent-loop
iteration. Constants carried in from `latency_profile.md`: a model call costs
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
carry byte-level `.reads.jsonl` data (`traces/latency_profiling/` 15, `traces/redundancy_profiling/`
8, `traces/redundancy_profiling_ctx512k/` 8). Exactly one run in the whole tree has a lead-authored
DAG — `traces/run_20260902T004901_584576Z_dc6a9685.jsonl`, 57 tasks and 96 edges — and it was
recorded in `summary` output mode against another machine's paths. Every workload written for this
series so far was deliberately flat and parallel, which is why the question had never come up.

**2.2 The lead does not emit edges unless the prompt names the mechanism.** Three instruction
strengths were probed (`scripts/dag_workloads.py` `LEVELS`): L0 narrative order only, L1 an explicit
ordering constraint, L2 the ordering constraint plus "create all task nodes first, then call
update_task with addBlockedBy". Only L2 produced `task_update` events with a non-empty
`blocked_by_task_ids`. The harness's own system prompt already carries that sentence
(`code.py:893-897`); it is not enough on its own.

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
corrects `file_read_reuse_profile.md:44`).

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

`scripts/prewarm_smoke.py` checked first that the endpoint accepts this at all — five shapes, one
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
(`traces/prewarm/smoke.json`), and in every one the model answered from the injected content without
calling a tool: a provider-shaped `call_<24 hex>` id, a distinctive `prewarm_0001` id, a text block
before the tool_use, a tool name absent from the `tools` array, and a plain-user-message control. The
distinctive id is used from here on, so injected bytes remain separable in both sidecars. Injected
prompts were 915-947 tokens against a 2,928-character injection, and the five calls took 4.7-6.1 s.

**Does the lead emit dependency edges?** Only at instruction level L2. The probe at L2 produced a
board with the intended chain on the first attempt (`traces/dag_redundancy_probe/`, 3 tasks, 2 edges,
successor claimed with `blocked_by_task_ids` recorded).

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
which 13 were claimed. Full tables in `traces/dag_redundancy/dag_tables.md`.

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
(`traces/prewarm/stage1.jsonl`, tables in `traces/prewarm/prewarm_tables.md`).

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

## 7. Reproduction

```bash
cd /home/yq335/learn-claude-code

# gates, in the order they have to pass
python3 s15_integrated_harness/scripts/prewarm_smoke.py                 # does the provider accept a fabricated tool_use?
python3 s15_integrated_harness/scripts/dag_workloads.py --probe --level L2   # does the lead emit edges, and do they execute?

# experiment 1: nine team runs over five dependency-shaped workloads
python3 s15_integrated_harness/scripts/dag_workloads.py --rep r1 --level L2 \
        --only W1,W2,W3,W4,W5 --max-seconds 900
python3 s15_integrated_harness/scripts/dag_workloads.py --rep r2 --level L2 \
        --only W2,W5,W1,W4 --max-seconds 900
python3 s15_integrated_harness/scripts/dag_redundancy.py \
        s15_integrated_harness/traces/dag_redundancy --tables --perms 20000 \
        --json s15_integrated_harness/traces/dag_redundancy/dag_tables.json

# the zero-edge base rate, from the existing flat corpus (no API calls)
python3 s15_integrated_harness/scripts/dag_redundancy.py \
        s15_integrated_harness/traces/redundancy_profiling_ctx512k --tables

# experiment 2, stage 1 (128 trials) and stage 2 (8 harness runs)
python3 s15_integrated_harness/scripts/prewarm_loop.py --reps 8 --concurrency 3
python3 s15_integrated_harness/scripts/prewarm_harness.py --reps 2 --only W5 \
        --arms baseline,dag,pollute,summary --max-seconds 900
python3 s15_integrated_harness/scripts/prewarm_analyze.py \
        --stage1 s15_integrated_harness/traces/prewarm/stage1.jsonl \
        --stage2 s15_integrated_harness/traces/prewarm/harness
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
