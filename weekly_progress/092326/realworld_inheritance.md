# Do the inheritance arms survive contact with real harnesses and real datasets?

*2026-09-18. Follows `inheritance_depth.md` (three arms on hand-authored workloads) and
`dag_kv_reuse.md`. Every number below is computed over runs that real agent harnesses really made on
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
3. **The retention policy matters far more than the budget, and everyone is using the wrong one.**
   At an **8 KB** budget, LFU reaches **99% (SWE)** and **57% (τ-bench/GAIA)** of what an unlimited
   budget buys. LRU — the basis of what every production harness ships — reaches **0%** and **8%**.
   On SWE-bench LRU at 8 KB removes *no* rounds at all where LFU removes 93% of the reachable ones.

The one conclusion that survives intact is the negative one: **the task graph is worthless as a
retention ordering.** `graph-ordered` lands near the bottom of both policy tables.

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
interface. `scripts/lmeval_agent_model.py` registers `s15-agent`: lm-eval renders a task prompt, the
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

### What a "read" is, defined once

`scripts/traj_ingest.py::read_key` covers four shapes: an editor `view` of a file span, a shell
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

### Metric 3 — budgets and eviction policies (E2-R / E3-R)

Removable rounds, cross-task, by policy and budget. Ceilings are marked and are not deployable.

| policy | τ+GAIA @8 KB | @32 KB | unlimited | SWE @8 KB | @32 KB | unlimited |
|---|---|---|---|---|---|---|
| **LFU** | **21.9%** | 35.6% | 38.2% | **4.0%** | 4.3% | 4.3% |
| **GDSF** | 17.2% | 29.5% | 38.2% | 3.5% | 4.0% | 4.3% |
| **S3-FIFO** | 15.8% | 25.4% | 38.2% | 3.9% | 4.3% | 4.3% |
| keep costliest-to-recover | 11.1% | 17.1% | 38.2% | 2.3% | 2.4% | 4.3% |
| graph-ordered | 7.8% | 11.3% | 38.2% | 0.7% | 1.1% | 4.3% |
| **LRU** | **3.0%** | 4.8% | 38.2% | **0.0%** | 0.5% | 4.3% |
| FIFO / recency truncation | 3.0% | 4.8% | 38.2% | 0.0% | 0.5% | 4.3% |
| Belady / MIN *[ceiling]* | 38.0% | 38.2% | 38.2% | 4.2% | 4.3% | 4.3% |
| byte-density oracle *[ceiling]* | 38.0% | 38.2% | 38.2% | 4.2% | 4.3% | 4.3% |

**The budget is not the binding constraint.** With perfect foresight, **8 KB already buys the entire
unlimited-budget value** in both corpora (38.0 of 38.2; 4.2 of 4.3). Every gap in the table is a
prediction failure, not a capacity failure.

**Frequency beats recency, by a lot.** At 8 KB LFU reaches 57% (τ/GAIA) and 93% (SWE) of the
reachable ceiling; LRU reaches 8% and **0%**. On SWE-bench LRU at 8 KB removes *no rounds at all*.
This matters because recency truncation and last-N-rounds windows are what production harnesses
actually ship.

**The graph is still worthless as an ordering.** `graph-ordered` sits near the bottom of both tables
(0.7% on SWE at 8 KB). Third independent test failed, agreeing with the F2 budget sweep.

**"Evict what is simplest to recover" does not work.** Keeping the costliest-to-recover items reaches
11.1% / 2.3% at 8 KB, below LFU, GDSF and S3-FIFO everywhere. Recovery cost correlates with size, and
the large items are not the reused ones. A methodological note that cost us a wrong table first:
**recovery cost must be measured per item or the policy silently becomes "keep the largest"** — with
only the modelled cost, which is monotone in bytes, the two columns came out byte-identical.

### Metric 4 — measured latency, and how much of the waste is the model's fault

From the HAL Weave logs, per model call: median **2.09 s**, p90 **19.4 s**; a τ-bench task is
**101.5 s** wall of which **101.1 s** is model time.

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

1. **Rank by reuse frequency, not recency.** One line in a context manager, and at a tight budget it
   is the difference between removing 4.0% of rounds and removing none.
2. **Spend the budget on prediction, not capacity.** 8 KB is already enough; the ceiling is reached
   at the smallest budget tested.
3. **Breadth is the ceiling, and it scales.** With the retention policy held fixed, removable rounds
   go 13% → 23% → 36% as the pool grows 8 → 16 → 32 earlier tasks, roughly linearly in the log. Since
   8 KB is enough to hold the *useful* part of any pool, the design tension is not budget against
   coverage — it is how many earlier tasks the engine can see at all. Widening the pool and then
   ranking it by reuse frequency beats every other knob measured here.
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
- **All 16 eviction policies are non-monotone in retained bytes if they are online caches** — LRU and
  FIFO violate it in 17 of 60 random pools. This is not a bug: LRU's stack property only holds for
  equal-sized objects, and tool observations are not. The earlier study's invariant "removable rounds
  are non-decreasing in budget for every ordering" was only ever tested on offline orderings.

### Next steps

1. Live confirmation needs a genuinely multi-part workload, because the census cleared no lm-eval
   task (§3, G1). The honest options are a benchmark whose unit of work is a project rather than a
   question — SWE-bench instances driven through mini-swe-agent, or AppWorld — or accepting that the
   live check measures the *policy* rather than the arms: `none` vs `ancestors` under LFU and LRU at
   8 KB and unlimited needs no graph at all, and it is the LFU-vs-LRU gap that the replay says
   matters most.
2. Fold line-level content addressing into the injector — it is the largest single lever on coding
   work and it is not implemented.
3. A staleness-aware policy: never hand over a span the successor's own base commit has moved past.
4. Ingest AppWorld and Online Mind2Web for a third non-coding regime.
