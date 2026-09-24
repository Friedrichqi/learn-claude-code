# 09 · Efficiency methodology and the optimized Qwen arm

> **Status:** Part A is the plan, Part B implements it. The optimized 24-run matrix on Qwen / vLLM is complete and published (2.3× geometric-mean speedup against the Qwen baseline of `research/05_latency_breakdown/` at unchanged quality); the Step 3 inheritance arms are an inconclusive pilot (all 8 cells hit the 900 s cap); the LMCache connector was rejected by the hybrid model and the server fell back to native prefix caching.
>
> **Dates:** both notes written 2026-09-22; z.ai probe 2026-09-22; Slurm job 96392 (optimized arm, alphagpu51) 2026-09-22 16:22–18:02 EDT with the 24 runs at 16:47–18:01; Slurm job 96562 (inheritance arms, alphagpu52) 18:23–20:44 EDT with the 8 cells at 18:27–20:43.
>
> **Models / providers:** `Qwen/Qwen3.8-27B` (bf16) on vLLM 0.29.0, one RTX PRO 6000 Blackwell, the baseline's server flags; `glm-5.3-flash` via z.ai for Step 5's F table; the baseline is the Qwen run set of `research/05_latency_breakdown/` (its Part B).
>
> **Commits:** runs at HEAD `0cbddf8` with the harness edits not yet committed; notes, scripts, harness edits (`s15_integrated_harness/code.py`, `s09_memory/code.py`, `research/common/profile_run.py`) and data committed in `0b65c46` (2026-09-23).
>
> **Presented:** not cited by any deck (091626, 092326).

**Contents**

- Part A — the cost model `Wall = R × [F + P + D] + H`, the bottleneck verdict per provider, where the data differs from the literature, the methodology step by step (Steps 0–7), priority order, threats, sources
- Part B — what was built per step, verification before GPU time, the baseline scoreboard, Step 5's F table (z.ai probe), the A/B on vLLM (per-group scoreboard, per-step attribution, Step 3 inheritance arms), what shipped, bottom line
- Data inventory · Source map · Cleanup notes

**Files in this folder** (`research/09_efficiency/`)

| path | purpose |
|---|---|
| `README.md` | this document |
| `term_scoreboard.py` | Step 0 scoreboard: per-run R / F / P / D / H, the fit, `--compare` baseline against optimized (`scoreboard_ab.json`); offline |
| `effort_probe.py` | Step 2 lever probe (one prompt, five configurations) → `pipeline/effort_mode.json`; calls the model |
| `inherit_trace_stats.py` | per-cell teammates, rounds, reads, re-read warnings and injections of the inheritance arms; falls back to the cell's own folder when the absolute trace path stored in `*.cell.json` is missing; offline |
| `qwen_vllm_opt.sbatch` | Slurm job of the optimized arm (job 96392): the pipeline with `OPT=1`, then the inheritance arms, which failed there because line 31 calls the system `python3` without the venv |
| `qwen_inherit.sbatch` | Slurm rerun of the inheritance arms under `~/.venv/bin/python` (job 96562) |
| `data/latency_profiling_qwen_opt/` | the optimized run set, its A/B scoreboard and the inheritance arms |
| `data/provider_probe_zai_20260922.json` | the z.ai probe behind Part B's F table |
| `research/05_latency_breakdown/qwen_vllm_pipeline.sh` (outside) | the pipeline; `OPT=1` tries LMCache, probes and applies the effort policy, adds inheritance, a stable prefix and the teammate cap |
| `research/08_context_inheritance/inherit_workloads.py` (outside) | runs the RW-FQA / FAB-DEEP inheritance cells |
| `research/common/profile_run.py` (outside) | driver flags `--effort-policy`, `--effort-mode`, `--max-teammate-concurrent`, `--prewarm*`, `--no-timestamp` |

---

# Part A — Agentic system efficiency: bottleneck verdict and a step-by-step improvement methodology

_Formerly `weekly_progress/efficiency_methodology.md` (written 2026-09-22)._

Synthesises every profiling experiment in this repo into a cost model, a bottleneck
verdict per provider, and an ordered intervention plan. Every predicted gain below is
derived from our own measurements, and each step names the current paper line it
extends or contradicts. Written 2026-09-22.

Inputs: `research/05_latency_breakdown/README.md` Part A (+ Appendix C, Qwen/vLLM rerun),
`research/06_tool_cost/README.md` Part A, `research/06_tool_cost/README.md` Part B, `research/08_context_inheritance/README.md` Part C,
`research/07_task_dag/README.md` Part B, `research/07_task_dag/README.md` Part A, `research/02_input_redundancy/README.md` Part B,
`research/04_kv_splice/README.md`, `research/03_context_interventions/README.md`, and the weekly notes in
`091626/` and `092326/`. Underlying regressions: 446 GLM calls (z.ai API, 2026-09-12)
and 501 Qwen3.8-27B calls (local vLLM 0.29.0, 2026-09-21), r² = 0.96 / 1.00.

## The question

Where does an agentic task's wall time actually go on each provider — the commercial
API or the vLLM endpoint — and, given that, in what order should we attack it? And
where do current agentic/MAS papers (context management, memory, serving schedulers)
point in the same direction as our data, and where do they not?

## The answer, in one line

**The bottleneck is not where the serving literature is looking.** Prefill — the
target of the KV-reuse and PD-disaggregation literature — is 1–6% of call time in our
traces on both providers. On the API the unique cost is a **3.72 s fixed per-call
component** (9–52% of a call, 2–3 s of it outside even the server's own
`x-process-time`); on local vLLM the endpoint is already healthy (queue ≈ 0, HTTP+SDK
27–61 ms) and **decode is 94–98% of server time**. Provider-independent, the dominant
multiplier is **round count**, and the one structural harness cost is **synchronous
memory extraction at 20–35% of wall**. So: cut rounds and output tokens first, make
memory ops async, consolidate calls on the API — and treat prefill/KV work as
capacity engineering, not latency engineering.

## The cost model

Both latency studies converged on the same linear model over all traced calls:

```
Wall(task) = R × [ F + P + D ] + H        (tools ≈ 0)
```

| term | meaning | GLM-5.3-flash (z.ai API) | Qwen3.8-27B (local vLLM) |
|---|---|---|---|
| **R** | rounds per task | 7.5/4.0/2.9 teammate rounds (CODE/FQA/MATH) | 7.1/4.2/2.9 — **model-independent** |
| **F** | fixed per-call cost | **3.72 s** (9–52% of a call); 2–3 s outside `x-process-time`; TTFT flat 5.6–6.1 s; 29–59% of responses arrive in one burst | ≈ 0 (queue 0.000 s; HTTP+SDK 27–61 ms) |
| **P** | prefill of uncached tokens | 0.033–0.052 ms/tok = 0.2–1.9% of a call | 0.117 ms/tok (≈8.6k tok/s) = 1.2–5.6% of server time |
| **D** | decode | 17.4 ms/tok = 50–89% of a call (solo math lead 89%) | **39.9 ms/tok = 94–98% of server time**; Qwen emits 1.2–2× more tokens (thinking = 33–68% of output at `xhigh`) |
| **H** | harness sync costs | turn-end memory extraction 13–21 s/lead turn = **20–35% of wall**; memory recall 4.9 s/call; context prep ~10 ms/round | same shape; extraction 12–35 s/turn, worse with thinking models (4–81% of 1,000-token extraction calls hit `max_tokens`) |

Closed by measurement (not worth attacking): tool execution 0.1–0.5% of a round
(every tool 0.2–10 ms except four argument-scaling ones, max `create_worktree`
607 ms — still 1/6 of one API fixed component); harness context preparation ~0.1%
(compaction 2 ms, prompt/tool assembly <1 ms).

## Bottleneck verdict per provider

**API (z.ai / GLM):** bottleneck = **F + D, multiplied by R**. On short tool-rounds F
is up to half the round; the client-side flat TTFT (5.6–6.1 s regardless of prompt
size, with a third to half of responses arriving in a single burst) shows the fixed
cost is provider queue/delivery, not our prompt shape. The lever is *fewer, larger
calls* — and provider selection measured as F, not just $/token.

**vLLM (local Qwen):** the endpoint is *not* the bottleneck — queue ≈ 0 and prefill
≤ 5.6% mean the serving stack is already efficient for this workload. The bottleneck
is **decode volume: N_out × ms/token**, inflated 1.2–2× by thinking tokens, at 2.3×
the per-token cost of the API. The levers are output-token control (thinking
budgets) and decode throughput (batch width, speculative decoding) — plus R.

**Both:** R is the multiplier, and it is set by the harness side (context limits,
what context a successor inherits), not by the model or the endpoint. One X3 run went
808 s → 168 s (−79%) from a 50k → 200k context limit alone; limit-starved runs
serialize into 5–20× more rounds. And H (sync memory extraction) is a 20–35% tax
that no serving-side fix can touch.

## Where our data differs from the literature (the complementary contribution)

| literature line | what it optimises | what our data says |
|---|---|---|
| KV reuse: CacheBlend, LMCache, Prompt Cache, vLLM APC, semantic-reuse RFC | prefill avoidance | Prefill is 1–6% of call time at agentic prompt sizes; a 5k-token re-send costs ~0.85 s = 3–6% of a round. **KV reuse buys capacity, not latency.** Cross-request reuse on our hybrid Gated-DeltaNet model only lands at 784-token-aligned checkpoints — a real deviation from the standard-attention assumption these papers make. |
| PD disaggregation: DistServe / Mooncake / Splitwise | separating prefill from decode | Prefill share too small to pay for disaggregation on our single-GPU setup; decode-dominated workloads need decode capacity, not a second pool. |
| Agent-memory: MemOS, Mem0, A-MEM, 2026 unified-taxonomy surveys | memory as capability | None price the serving cost of memory ops. Our **sync turn-end extraction = 20–35% of wall** makes memory a first-class schedulable workload — a number the surveys do not have. |
| Graph-exploiting agent serving: Parrot semantic variables, DAG schedulers | task/request DAG structure | Our task DAG failed three independent tests as a *retention* signal (placebo delta −1.0; graph-ordered retention 1.4% [corrected 2026-09-23: earlier efficiency_methodology.md said 0.7%, the first cut of the replay (SWE-bench, 8 KB); the corrected grid research/08_context_inheritance/data/realworld/crossgrid_open_swe.md gives 1.4%, graph-ordered now leads τ-bench/GAIA at 8 KB with 21.9% (crossgrid_hal.md), and research/08_context_inheritance/README.md Part B, Metric 3, withdraws the “third independent test failed” reading: the replay “says nothing about the graph”]; census: edges are a shadow of task text). The DAG is fine for dataflow/scheduling; worthless for predicting what bytes to keep. |
| MAS efficiency: shared-context and team papers (e.g. Decentralized MAS with Shared Context, arXiv 2026-06) | teams + shared context as a win | **Solo beat teams 1.4–1.8×** on our small tasks; cross-teammate prompt duplication reached the workload's design ceiling (8–27% on FQA); teammates sharing the GPU cut prefill throughput 5.7k → 3–4.2k tok/s. Teams must pay for themselves in parallelism, not assumed. |
| Tool-use cost work | making tools cheaper / exposing costs | Tools are 0.1–0.5% of a round, and advertising per-tool costs to the model moves nothing (p=1.0; tie-breaker only). The expensive unit is the *round*, never the tool. |
| AgentFold / Context Folding (ICML'26) | fold context via model-generated summaries | Our injection experiment: a **summary** handoff had no effect (CI straddles 0); **bytes** cut successor rounds 5.17 → 3.00 in the real harness and wall −70% in the micro-loop (7.22 → 2.50 rounds) [corrected 2026-09-23: earlier efficiency_methodology.md said “bytes cut rounds 3.00 → 5.17 → −70% wall in the micro-loop”; source research/07_task_dag/README.md Part A §5.6 (scripted micro-loop, 128 trials, 62.4 → 18.4 s) and §5.7 (real harness, 95% CI [-3.50, -0.83])]. Compress the format, never summarise the span away. |

## The methodology, step by step

Every intervention must pass the same gate (Step 0) and name the term it attacks.
Predicted gains are derived from the table above, not from papers.

### Step 0 — Lock the attribution scoreboard (the method itself)

- Keep `Wall = R×(F+P+D) + H` as the canonical scoreboard. Each intervention states:
  term attacked, predicted delta from current regression constants, measured per-term
  delta, quality gate result.
- Accept/reject on replay corpora first — 1,200 SWE + 407 τ-bench/GAIA successors and
  the n=128 scripted micro-loop give intervals that exclude zero; live 16-run arms do
  not (every live inheritance CI straddled 0). Reserve the live 24-run matrix for
  final validation only.
- Report negative results at the same rank as positive ones (the DAG-retention and
  KV-splice negative results are among this repo's most citable outputs).

### Step 1 — H: make memory extraction and recall asynchronous (highest ROI, pure engineering)

Move turn-end extraction off the lead's critical path (background thread + merge
before next recall), batch extractions per turn, cap extraction output tokens (think
models blow the 1,000-token cap 4–81% of the time, doubling the cost of the call that
already blocks the lead).

- Predicted: **−28–48% team wall** (research/05_latency_breakdown/README.md Part A's own amortisation bound).
- Validate: no `model_request` span with `purpose=memory` between two lead rounds on
  the critical path; wall delta on the 24-run matrix; extraction recall quality
  unchanged on scored runs.
- Literature tie-in: gives 2026 agent-memory surveys the *serving-cost* dimension
  they lack — memory ops as preemptible background load, not invisible tax.

### Step 2 — D: control output volume — per-round thinking budgets

Decode is 94–98% of vLLM server time, so output tokens × ms/token is *the* serving
lever. Ship a policy table: task/round type → reasoning effort. Trivial tool-ack
rounds and CODE edit rounds get minimal thinking; MATH keeps full budget. Qwen at
`xhigh` spends 33–68% of output on thinking and writes 1.2–2× GLM's tokens.

- Predicted: a 30–50% thinking-token cut ⇒ **~15–35% wall on vLLM**; on the API it
  also cuts billed output tokens.
- Escalations if ms/token itself must fall: speculative decoding (EAGLE-3/MTP),
  `max-num-seqs` sweep (decode ran 38–43 ms/tok at width 32), W8A8 quantisation —
  each gated on quality.
- Literature tie-in: SelfBudgeter (self-allocated budgets), Budget Guidance
  (decoding-level steering), Plan-and-Budget (2026), adaptive-reasoning surveys. Our
  addition: budgets should be **per round type, set by the harness**, since the
  harness knows the round's purpose — the papers budget per query.

### Step 3 — R: ship complete-coverage context inheritance (the round lever)

Deploy the deployable rules from the inheritance study as one mechanism: ancestors-
closure handoff, keyed on **content + byte-range** (range granularity lifts removable
rounds 36.4% → 90.9%), ranked by **reuse frequency not recency** (LFU 19.2% vs LRU
15.8% removable at 8 KB [corrected 2026-09-23: earlier efficiency_methodology.md said LFU 21.9% vs LRU 3.0%; corrected replay, τ-bench/GAIA, research/08_context_inheritance/data/realworld/crossgrid_hal.md; on SWE-bench the corrected pair is 2.6% vs 0.3% (crossgrid_open_swe.md); research/08_context_inheritance/README.md Part B, Metric 3]), shared pool across agents (model-mismatch costs ~2 points),
pool widened 8 → 16 → 32 tasks (13% → 23% → 36% removable).

- The iron rule from the coverage sweep (step ratio a/b = 69×): **complete coverage
  or nothing**. Supplying 1–3 of 4 needed documents bought zero rounds (8.00 → 8.00 →
  8.53 vs 8.00) and partial coverage was mildly *harmful* (99% → 83% accuracy on
  missing-doc facts). Trimming a retained span to half costs only +0.30 rounds —
  compress the format, never drop the span, never summarise it away.
- Expectation ceiling: reads are 42.4% of a successor's rounds (44.7% read nothing),
  so the live −25% mean rounds was already 58% of structural ceiling; whole-observation
  reuse on real SWE data is ~1%, not the synthetic 24–39%. Market this honestly.
- Validate: scripted micro-loop (n=128) for power, then replay corpora, then live.
- Literature tie-in: this *is* the missing policy for shared-context MAS (e.g.
  Decentralized MAS with Shared Context, 2026) — and a falsifiable prediction against
  AgentFold / Context Folding: byte-injection should beat summary-folding for
  handoff. Run that head-to-head; it is a paper-sized comparison and we hold the
  measurement infrastructure for both arms.

### Step 4 — R: context-window guardrails (cheap tail wins)

Keep the effective context limit high and detect starvation: if >50% of recent rounds
contain a re-acquisition, warn/refuse rather than serialize (limit-starved runs did
5–20× more rounds; one run −79% from the limit fix alone). Keep prefixes stable
(cache hits 15% → 60%, 3–5× fewer uncached tokens — a capacity and API-billing win;
on the API it also shrinks the P term). Batch reads only when the batch fits —
outline demotion backfired (809 reads, timeouts).

### Step 5 — F: API-side call consolidation and provider routing

Consolidate rounds: parallel/batched tool calls per round; fuse harness-initiated
calls (recall, compaction previews) into main calls where quality allows — on the API
each eliminated short round refunds up to 3.72 s.

- Treat **F as a procurement metric**: our regression-intercept + flat-TTFT method
  measures it per provider in one run (z.ai 3.72 s vs local ~0). Route
  short-round-heavy workloads (tool-heavy CODE) to low-F endpoints and long-decode
  workloads (MATH, thinking models) to cheap decode.
- The 2–3 s of F outside `x-process-time` is a provider queue/delivery artifact —
  worth escalating with our traces as evidence; 29–59% single-burst responses also
  inflate perceived TTFT for streaming clients.
- Literature tie-in: Tempo (SLO-aware scheduling) and Autellix schedule *within* a
  provider; our data says that *across* providers, F is the scheduling unit that
  matters for short rounds — a routing policy the intra-provider schedulers cannot
  express.

### Step 6 — Scheduling policies on the vLLM endpoint (pays at concurrency)

- Autellix-style program-level priority/preemption (arXiv:2502.13961), using our
  trace `purpose` tags as the program context: lead-critical short calls (compaction,
  inbox, memory if any remain synchronous) preempt teammate long decodes. Our traces
  already carry the labels Autellix has to infer.
- Parrot-style semantic variables (OSDI'24) to expose lead/teammate and
  predecessor/successor relations to the engine for cross-request reuse decisions —
  but *not* for KV retention (DAG verdict above).
- Streaming prefill overlap for multi-agent (MLSys 2026) targets exactly our measured
  co-scheduling prefill degradation (5.7k → 3–4.2k tok/s with teammates active).
- Keep prefix caching for capacity (34–77% of prompt tokens reused, zero eviction
  pressure at 1–5% KV occupancy); document the 784-token hybrid-model alignment
  constraint as a deviation from CacheBlend/LMCache assumptions. Deprioritise PD
  disaggregation and KV-splice (KL 0.045–0.085 at 1.5B/7B, ~0.1 s/fault — negative
  value unless the spliced KV is shared across requests); track vLLM's semantic
  KV-reuse RFC rather than building our own.
- Topology policy from our own numbers: **solo for small tasks** (1.4–1.8× faster),
  team only when the task genuinely decomposes into parallel subtasks.

### Step 7 — Evaluation and paper positioning

- Replay-first pipeline (extend `replay_sweep.py` / `budget_sweep.py` /
  `evict_policies.py` to the candidate policies above), lm-eval-rendered tasks for
  quality where boards exist, live matrix as the final gate.
- Per-intervention report format: term attacked → predicted (from model constants) →
  measured per-term delta → quality gate → CI method. This per-term attribution is
  itself the methodological contribution: agent-efficiency papers report e2e only,
  which is why the field keeps optimising the 1–6% term.
- The five gaps our numbers fill: prefill share at agentic prompt sizes on API vs
  vLLM; serving cost of agent memory ops; DAG-retention negative results (three
  tests) [cleanup note: the replay test in this count was withdrawn on 2026-09-23, see the corrected “Graph-exploiting agent serving” row of the literature table]; hybrid-attention KV block alignment; tool-cost behavioural inertness.

## Priority order by expected value

| # | intervention | term | predicted gain (basis) | risk |
|---|---|---|---|---|
| 1 | async memory extraction/recall | H | −28–48% team wall (measured amortisation) | none to quality |
| 2 | per-round thinking budgets | D | −15–35% vLLM wall (thinking = 33–68% of output) | quality — gate it |
| 3 | complete-coverage inheritance | R | −25% live rounds / −70% micro-loop wall (measured) | cost: +42k prompt tokens/inheritance; partial coverage harms |
| 4 | context-limit guardrails | R | up to −79% on starved runs (observed) | none |
| 5 | API call consolidation + F-based routing | F | F = 9–52% of short rounds (measured) | none |
| 6 | agent-aware scheduling + solo/team policy | D/R at concurrency | pays at concurrency; prefill co-scheduling −25–35% (measured) | engineering depth |

**Explicit non-goals (measured closed):** tool execution latency and harness context
prep (<0.5% combined); prefill/KV-*latency* engineering (1–6%, capacity-only value);
DAG-directed retention (failed three tests) [cleanup note: the replay test in this count was withdrawn on 2026-09-23, see the literature table]; KV-splice (negative standalone value);
lm-eval as a source of agentic tasks (0 boards in 15 runs).

## Threats and what is not established

- Step 1's −28–48% is an *amortisation bound* computed from current traces, not a
  built system; the async merge design may force partial synchronisation.
- Step 2's projection assumes thinking-token cuts preserve quality — the budget
  papers say yes for easy tasks and we must verify per workload (MATH is the risk).
- Step 3's live arms remain underpowered; the −25% live figure has CIs straddling
  zero and rests on the replay + micro-loop evidence for direction.
- One codebase for live arms; two providers total; replay is counterfactual by
  construction.

## Sources

- Autellix: An Efficient Serving Engine for LLM Agents as General Programs —
  [arXiv:2502.13961](https://arxiv.org/abs/2502.13961)
- Parrot: Efficient Serving of LLM-based Applications with Semantic Variables
  (OSDI'24) — [microsoft/ParrotServe](https://github.com/microsoft/ParrotServe)
- Tempo: SLO-aware LLM request scheduler (arXiv, Apr 2025)
- AgentFold: Long-Horizon Web Agents with Proactive Context Management
  (R. Ye et al., arXiv, Oct 2025)
- Scaling Long-Horizon LLM Agent via Context Folding (ICML 2026, OpenReview)
- Decentralized Multi-Agent Systems with Shared Context (arXiv, Jun 2026)
- Accelerating Multi-Agent LLM Systems via Streaming Prefill Overlap (MLSys 2026)
- SelfBudgeter: Adaptive Token Allocation for Efficient LLM Reasoning (OpenReview)
- Steering LLM Thinking with Budget Guidance (ACL Anthology); Plan and Budget
  (alphaXiv, Mar 2026); adaptive/controllable test-time compute surveys (2025–2026)
- LMCache: KV cache layer + PD disaggregation ([blog](https://blog.lmcache.ai),
  arXiv Dec 2025); CacheBlend (non-prefix KV reuse);
  [vLLM semantic KV-reuse RFC #44223](https://github.com/vllm-project/vllm/issues/44223);
  [vLLM APC docs](https://docs.vllm.ai)
- LLM agent memory surveys 2026 (unified taxonomy, preprints.org; mechanisms survey,
  arXiv Mar 2026); MemOS (MemTensor/SJTU, 2025); Mem0 (2025); A-MEM (2025)

# Part B — Implementing the efficiency methodology: step-by-step results

_Formerly `weekly_progress/efficiency_impl_report.md` (written 2026-09-22)._

Implementation of Part A on vLLM (+LMCache attempt),
A/B against the untouched baseline in `research/05_latency_breakdown/data/latency_profiling_qwen` (24 runs, same GPU
type, same server flags). Written 2026-09-22.

## What was built, per step

| step | intervention | term | implementation |
|---|---|---|---|
| 0 | term-attribution scoreboard | all | new `research/09_efficiency/term_scoreboard.py`: per-run R/F/P/D/H table reusing the validated trace parser; multivariate fit `duration = F + P·uncached + D·output`; `--compare` prints term deltas. Runs on login node, no GPU needed. |
| 1 | async memory extraction + per-turn recall | H | `code.py`: `remember_after_turn` moved to a daemon worker (`remember_after_turn_async`), snapshot + trace-context carried, `flush_memory()` joins at shutdown (`profile_run.py` and REPL both). `s09_memory`: recall selection memoized per turn (key = catalog fingerprint + reminder-stripped query) — kills the per-round 200-token selection call; extraction `max_tokens` 1000 → 1500. |
| 2 | per-round reasoning budgets | D | `profile_run.py` `--effort-policy {off,memory,main-low,main-medium}` + `--effort-mode {output_config,nothink}`: purpose→effort map applied centrally in `profiled_create` (memory/compaction always low; main rounds per arm). vLLM levers verified in source: `output_config.effort`, `chat_template_kwargs` (the Anthropic `thinking` param is NOT supported by vLLM's /v1/messages). New `research/09_efficiency/effort_probe.py` fires one prompt five ways and picks the lever that actually reduces output — the pipeline refuses to guess. |
| 3 | complete-coverage inheritance | R | the prewarm injector already had range-keyed residency, frequency counts, byte budgets; what was missing is wiring: pipeline runs arms with `--prewarm ancestors --prewarm-evict lfu --prewarm-budget 32k` (LFU beat LRU 19.2% vs 15.8% [corrected 2026-09-23: earlier efficiency_impl_report.md said 21.9% vs 3.0%; τ-bench/GAIA at 8 KB, research/08_context_inheritance/data/realworld/crossgrid_hal.md] in the sweep), and `inherit_workloads.py` gained `--driver-arg` passthrough. Matrix teams are independent by design (no DAG edges) so Step 3 is measured on the dedicated dependency-shaped workload: none vs ancestors, 2 reps, same optimized harness. |
| 4 | re-acquisition guardrails | R | `run_read` (single funnel for lead/teammate/subagent reads) now tracks resident bytes per file (mtime-invalidated) and emits `reacquire_warn` trace events on full re-reads of resident content — advisory, never blocking; the events are the "injected but re-read anyway" behavioural signal for Step 3. Stable system prefix via `--no-timestamp` on all matrix runs. |
| 5 | call consolidation + F table | F / R | system-prompt hint added: batch independent tool calls into one response (adoption measured as multi-tool-round share). F re-measured on z.ai today (below). |
| 6 | vLLM + LMCache + client priority | P / D | `lmcache 0.3.6` installed into `~/.venv`; pipeline gains `--lmcache`-style switch: `--kv-transfer-config {"kv_connector":"LMCacheConnectorV1","kv_role":"kv_both"}` with health-check and **automatic fallback to native prefix caching** (status recorded in `pipeline/lmcache_status.json`). Client-side priority: `--max-teammate-concurrent N` semaphore in `profiled_create` caps in-flight teammate decodes so lead/harness calls schedule immediately (Autellix-style ordering where we control it: the client). Core-scheduler priority = documented follow-up. |
| 7 | A/B | — | `qwen_vllm_opt.sbatch`: OPT=1 → optimized 24-run matrix (`--stream --vllm-metrics`, scored, smoke-gated) + inherit arms, then `term_scoreboard --compare`. |

## Verification before spending GPU time

- z.ai smoke run of the full optimized harness (`SMOKE-opt`, 57 s) [cleanup note: this run was never committed; on 2026-09-24 its trace (z.ai `glm-5.3-flash`, 2026-09-22 19:46 UTC, label SMOKE-opt, one `reacquire_warn`) was recovered from /tmp/opt_smoke/ on the host into `research/09_efficiency/data/latency_profiling_qwen_opt/smoke_opt/`]: `reacquire_warn` fired on
  the second read of the same file (44,301 chars resident); memory extraction launched at
  exactly the last lead response (t=17.6 s → 57.3 s, entirely off the critical path —
  quiescence reached at 20 s while extraction still ran).
- Recall-cache unit check: 4 `load_memories` calls (repeat + reminder-perturbed + new
  request) → 2 selector calls; `<reminder>` texts correctly deduped.
- Effort-policy unit check: purpose→effort mapping and `extra_body` shapes verified.
- Scoreboard reproduces the published constants from the baseline traces on its first run:
  **D = 39.9 ms/out tok (published: 39.9), r² = 0.998 over the same 501 calls.**

## Baseline scoreboard (existing traces, no re-run needed)

| group | n | wall_s | R | out_tok | think | H_s | H_ovl | cache | multi% | qual |
|---|---|---|---|---|---|---|---|---|---|---|
| CODE-solo | 3 | 386 | 14.7 | 9,280 | 0.55 | 7.1 | 0.00 | 6% | 35 | 3.00 |
| CODE-team | 5 | 470 | 34.0 | 17,859 | 0.60 | 103.8 | 0.61 | 9% | 18 | 3.00 |
| FQA-solo | 3 | 346 | 10.3 | 7,640 | 0.61 | 38.4 | 0.00 | 6% | 61 | 0.97 |
| FQA-team | 5 | 420 | 24.0 | 12,800 | 0.58 | 146.8 | 0.44 | 10% | 41 | 0.98 |
| MATH-solo | 3 | 209 | 2.3 | 4,670 | 0.61 | 27.8 | 0.00 | 11% | 0 | 3.00 |
| MATH-team | 5 | 306 | 25.8 | 12,207 | 0.48 | 72.2 | 0.52 | 5% | 26 | 3.00 |

fit(all main calls): **F = 1,018 ms/call, P = 0.069 ms/uncached tok, D = 39.9 ms/out tok** (r²=0.998, n=501). [cleanup note: term_scoreboard.py counts uncached = input_tokens − cache_read_input_tokens, which is negative for all 48 runs in scoreboard_ab.json (uncached_tokens_run), so F and P here are not comparable with research/05_latency_breakdown/README.md Part B §3 B, which fits the same 501 calls on computed tokens (0.43 s, 0.117 ms per token); D agrees (39.9 ms)]
Reading: baseline thinking is 48–61% of output everywhere (the D-term target); memory work
averages 7–147 s per run (the H-term target); H-overlap 0.44–0.61 in team runs shows
extraction already colliding with teammate activity it used to wait for.

## Step 5's F table (provider probe, 2026-09-22)

z.ai, today: **first block = 3.26 s fixed + 0.057 ms/uncached token** (r²=0.82; matches the
published 3.72 s within jitter), decode median 78 tok/s (55–129), cache strictly
prefix-based (99.9% hit on identical resend, 0% after a mid-prompt insertion). The fixed
component remains the API's dominant cost for short rounds and the reason call
consolidation is on the list.

## A/B results (optimized matrix vs baseline, same GPU type, same server flags)

Job `qwen-opt` (Slurm 96392): server healthy at 16:37, effprobe verdict **nothink**, LMCache
rejected → native prefix caching (status recorded), smoke gate **passed** (FQA coverage 0.967
under the full optimized policy), matrix 24/24 scored, tables + compare written. Clean-A/B
check: the optimized server's own probe (prefill 0.173 ms/uncached tok, decode 26 tok/s,
99.7% identical-resend hits) matches the baseline server within noise, so every delta below
is harness/policy, not serving stack.

### Per-group scoreboard (baseline → optimized)

| group | wall_s | R | out_tok | think | H_s | H_ovl | cache | quality |
|---|---|---|---|---|---|---|---|---|
| CODE-solo | 386→178 (**−54%**) | 14.7→19.3 (+32%) | 9,280→4,385 (−53%) | 0.55→**0.00** | 7.1→0.2 (−97%) | 0→0 | 6→19% | 3/3→3/3 |
| CODE-team | 470→182 (**−61%**) | 34.0→30.8 (−9%) | 17,859→6,147 (−66%) | 0.60→**0.00** | 103.8→1.1 (−99%) | 0.61→0.60 | 9→10% | 3/3→3/3 |
| FQA-solo | 346→58 (**−83%**) | 10.3→**2.3** (−77%) | 7,640→1,362 (−82%) | 0.61→**0.00** | 38.4→0.4 (−99%) | 0→0 | 6→21% | 0.97→0.96 |
| FQA-team | 420→137 (**−67%**) | 24.0→26.2 (+9%) | 12,800→4,483 (−65%) | 0.58→**0.00** | 146.8→9.7 (−93%) | 0.44→0.60 | 10→12% | 0.980→0.980 |
| MATH-solo | 209→147 (**−30%**) | 2.3→2.3 (0%) | 4,670→3,776 (−19%) | 0.61→0.56 (kept) | 27.8→0.4 (−99%) | 0→0 | 11→18% | 3/3→3/3 |
| MATH-team | 306→268 (**−12%**) | 25.8→19.6 (−24%) | 12,207→10,214 (−16%) | 0.48→0.54 (kept) | 72.2→3.5 (−95%) | 0.52→0.57 | 5→11% | 3/3→3/3 |

Fits: F = 1,018→727 ms/call, P = 0.069→0.061 ms/uncached tok, D = 39.9→38.9 ms/out tok
(r² = 0.998 both sides; n = 501 / 455 main calls). **Geometric mean wall ratio 0.43 ≈ 2.3×
end-to-end speedup across the six groups, with zero quality regression** (FQA-team coverage
bit-identical at 0.9802; worst movement FQA-solo −1.1 pp; all CODE/MATH unittest gates 3/3).

### Per-step attribution (each step leaves a distinct trace signature)

- **Step 2 — per-round thinking budgets (the dominant win).** On FQA/CODE (arms where the
  policy applies to main rounds) thinking share went 0.55–0.61 → **0.00** and output tokens
  fell 53–82%; wall fell 54–83%. MATH was deliberately left thinking (policy `memory`
  only) and moved least (−12%/−30%) — the clean internal control that attributes the
  FQA/CODE gains to the thinking budget rather than to noise or the other steps.
  Effort-probe finding recorded for the paper: **vLLM 0.29's `output_config.effort` is
  silently ignored by the Qwen3.8 template; only `chat_template_kwargs.enable_thinking`
  moves output volume** — the probe gate saved the matrix from a no-op arm.
- **Step 1 — async memory + per-turn recall.** H collapsed 93–99% in every group
  (FQA-team 146.8 s → 9.7 s per run; CODE-solo 7.1 s → 0.2 s). Team H-overlap stays ~0.6
  (extraction still overlaps teammate activity — but now the lead stops waiting for it),
  and in solo runs H was pure critical path (overlap 0.00 → wall gains there are H removal
  plus cache: MATH-solo −30% with R unchanged and thinking kept).
- **Step 5 — batching hint.** FQA-solo rounds collapsed 10.3 → 2.3 (−77%) with multi-tool
  rounds at 100% (was 61%): the lead now reads everything in one or two rounds — the
  largest single-group win (−83% wall). Note the honest counter-case: CODE-solo rounds
  *rose* +32% (15→19) because without thinking the model iterates in more, much cheaper
  rounds — output tokens −53% and wall −54% anyway. R is a lever, but D can pay for R.
- **Steps 4+6 — stable prefix & client cap.** Cache hits rose in every group
  (solo 6→19–21%, MATH-team 5.2→10.8%, +108%) from `--no-timestamp` alone; the teammate
  concurrency cap (2) held CODE-team together while F built — CODE-team R fell 9% and its
  wall fell 61%. `reacquire_warn` events fired 7 times across the matrix (baseline
  tr definitionally 0) — the guard is observing real re-acquisitions for future
  round-attribution.
- **Step 6 — LMCache: rejected by the hybrid model (documented finding).** vLLM 0.29 +
  LMCacheConnectorV1 (lmcache 0.3.6) fails at EngineCore init on Qwen3.8-27B: the connector
  does not support hybrid-memory-attention (HMA), which hybrid SSM models require — vLLM
  logs "Turning off hybrid kv cache manager…" and the engine dies; the pipeline fell back
  to native prefix caching automatically (`pipeline/lmcache_status.json`). This is the
  standard-attention assumption our methodology predicted, now with a concrete error trail.
  The serving side therefore ran identical to baseline — which is what makes the A/B clean.

### Step 3 — complete-coverage inheritance (dependency-shaped workload)

Matrix teams are independent by design (no DAG edges to inherit along), so Step 3 ran on the
dedicated dependency-shaped workloads (RW-FQA, FAB-DEEP): none vs ancestors(**LFU**,
**32k**), 2 reps, identical optimized harness in both arms (Slurm 96562; first attempt
failed only environmentally — the arms must run under the venv python, as the pipeline
always does).

What the runs establish:

- **The mechanism shipped and worked mechanically.** Every ancestors cell injected real
  handoffs (1–2 injections per run, 5,410–20,197 chars, LFU-ranked at the 32 KB budget,
  byte-range-keyed, once per (agent, task), byte-stable for the prefix cache); the none arm
  injected zero. `reacquire_warn` events fired in both arms (0–2 per run) — the guard is
  observing genuine injected-but-re-read cases, the behavioural signal the coverage study
  predicted (25% of injected successors re-read anyway).
- **Wall: neutral, not negative.** Every cell hit the 900 s cap in BOTH arms (mean 957 s,
  range 908–1,028) — at Qwen-with-thinking speed these deep workloads do not complete in
  the budget, and inheritance neither rescued nor slowed them. Injection cost nothing.
- **Effect size: unresolvable at this scale — and that is itself the methodology's
  prediction.** Rounds-within-cap (21–518) are dominated by timeout dynamics, and
  accuracy 0/5 in every cell of both arms [cleanup note: slurm-inherit-96562.out and the `*.cell.json` files score the RW-FQA cells 0/5 and the FAB-DEEP cells 0/4] is truncation, not arm treatment. This is the
  live-arm underpowering problem Step 0 was written for: n=2×2 with a wall cap cannot
  resolve a step-function coverage effect. Per the gate, the accept/reject for Step 3
  stays with the replay corpora (13–38% removable rounds, LFU 19.2% vs LRU 15.8% [corrected 2026-09-23: earlier efficiency_impl_report.md said LFU 21.9% vs LRU 3.0%; research/08_context_inheritance/data/realworld/crossgrid_hal.md], range
  keying 36→91%) and the mechanism now ships behind flags ready for a properly powered
  trial (`--max-seconds 1800+`, effort policy on, ≥4 reps).

## What shipped, and where it lives

- Harness (production path, always on): async memory extraction worker + shutdown flush
  (`code.py`), per-turn recall cache (`s09_memory/code.py`), re-acquisition guard
  (`run_read`), batching hint (`PROMPT_SECTIONS`), extraction `max_tokens` 1500.
- Measurement: `research/09_efficiency/term_scoreboard.py` (Step 0), `research/09_efficiency/effort_probe.py`,
  `research/09_efficiency/inherit_trace_stats.py`.
- Policies: `profile_run.py --effort-policy/--effort-mode/--max-teammate-concurrent`,
  prewarm flags (`--prewarm ancestors --prewarm-evict lfu --prewarm-budget 32k`),
  `--no-timestamp`; pipeline switches `OPT/LMCACHE/EFFORT/INHERIT/PREFIX_STABLE/TEAMMATE_CAP`
  in `qwen_vllm_pipeline.sh` (+ `qwen_vllm_opt.sbatch`, `qwen_inherit.sbatch`).
- Artifacts: `research/09_efficiency/data/latency_profiling_qwen_opt/` (24 scored runs, scoreboard_ab.json,
  provider probe, effort_mode.json, lmcache_status.json, inherit/ with 8 cells + dumps),
  `research/09_efficiency/data/provider_probe_zai_20260922.json` (F table).

## Bottom line

**2.3× geometric-mean end-to-end speedup (wall −12% to −83% per group) with zero quality
regression**, from three measured terms: thinking-budget removal on FQA/CODE (−53 to −82%
output tokens; MATH kept thinking as the internal control and moved least), async memory +
per-turn recall (H −93 to −99%), and round batching on read-heavy work (FQA-solo rounds
−77%). The serving-side item (LMCache) produced a documented negative result: vLLM 0.29 +
LMCache 0.3.6 cannot serve this hybrid-attention model (no HMA support) — the pipeline
auto-fell back to native prefix caching, and the clean A/B that resulted proves the wins
are harness-side, exactly where the methodology said the leverage was.

---

## Data inventory

All paths are under `research/09_efficiency/data/`. Run labels, sidecars (`.inputs.jsonl`, `.reads.jsonl`), score files and sandboxes follow `research/05_latency_breakdown/`. Git-tracked unless marked local.

| path | what it holds | written by | used in | status |
|---|---|---|---|---|
| `latency_profiling_qwen_opt/run_20260922T2047…T2158*.jsonl` + sidecars (72 files) | 24 optimized runs, 2026-09-22 20:47:15–22:01:25 UTC (16:47–18:01 EDT), the baseline's labels and prompts; `Qwen/Qwen3.8-27B` via vLLM at `127.0.0.1:50081` (native prefix caching); HEAD `0cbddf8` plus the uncommitted harness edits; driver flags `--effort-policy main-low` (FQA, CODE) or `memory` (MATH), `--effort-mode nothink`, `--prewarm ancestors --prewarm-evict lfu --prewarm-budget 32k`, `--no-timestamp`, `--max-teammate-concurrent 2`, `--vllm-metrics`, `--client-max-retries 0`; all `completed`, wall 51.7–308.9 s | `research/05_latency_breakdown/qwen_vllm_pipeline.sh` with `OPT=1` (`qwen_vllm_opt.sbatch`, Slurm job 96392 on alphagpu51) → `latency_workloads.py` → `profile_run.py` | Part B, A/B results | published |
| `latency_profiling_qwen_opt/<label>.score.json` (24), `CODE-*.sandbox/` (8) | per-run scores and archived coding sandboxes | `research/05_latency_breakdown/latency_workloads.py` | Part B per-group scoreboard (quality column) | published |
| `latency_profiling_qwen_opt/scoreboard_ab.json` | Step 0 scoreboard of the baseline (`research/05_latency_breakdown/data/latency_profiling_qwen`) and of this set: fit constants and per-run R, wall, output tokens, thinking share, H, H overlap, cache hit, tool and multi-tool rounds, `reacquire_warn`, quality | `term_scoreboard.py <baseline> --compare <optimized> --json`, run by hand (no script in the repo calls it) | Part B baseline scoreboard, per-group scoreboard and fits | published; regenerates identically apart from the two directory names (checked 2026-09-24); its `uncached_tokens_run` values are all negative (see the cleanup note in Part B's baseline scoreboard) |
| `latency_profiling_qwen_opt/latency_tables.md` / `.json` | every table of this set, same analyzer as `research/05_latency_breakdown/` | pipeline `tables` step, 2026-09-22 18:01 EDT | Part B names the folder only | published; the .md regenerates byte-identically, the .json differs only in stored absolute trace paths (checked 2026-09-24) |
| `latency_profiling_qwen_opt/compare_glm_vs_qwen.md` / `.json` | T0–T10 side by side of GLM (A) against this optimized Qwen arm (B): not baseline against optimized, which is `scoreboard_ab.json` | pipeline `compare` step (`latency_compare.py`), 2026-09-22 18:01 EDT | not cited | published; regenerates identically except for the path strings: the committed files print the absolute old paths `/mnt/home/yqi10/learn-claude-code/s15_integrated_harness/traces/…` (header line and JSON `dir` fields) and `scripts/…` script names |
| `latency_profiling_qwen_opt/provider_probe.json` | 27 probe calls against the optimized server, 16:37–16:44 EDT: first block 0.1729 ms per computed token (r2 1.00), decode 26 tok/s, identical resend 99.7% cached | pipeline `probe` step (`provider_probe.py --all --json`, computed-token version) | Part B, clean-A/B check | published |
| `latency_profiling_qwen_opt/smoke/` | `FQA-team-smoke`, 2026-09-22 20:44:33 UTC (16:44 EDT), under the full optimized policy: 118.8 s, coverage 0.967, 29 model calls, thinking share 0%; trace, sidecars, `.score.json` | pipeline `smoke` step + `smoke_check.py` (`PASS` in `slurm-96392.out`, marker `pipeline/smoke.ok`) | Part B ("smoke gate passed") | smoke (passed) |
| `latency_profiling_qwen_opt/smoke_opt/` | `SMOKE-opt`: one z.ai `glm-5.3-flash` session of the full optimized harness, 2026-09-22 19:46 UTC, 57 s, one `reacquire_warn`; trace, `.inputs.jsonl`, `.reads.jsonl` | `research/common/profile_run.py`, run by hand | Part B "Verification before spending GPU time" | smoke; recovered from `/tmp/opt_smoke/` on 2026-09-24 (never committed before) |
| `latency_profiling_qwen_opt/pipeline/` | `effort_mode.json` (verdict `nothink` and the five probe results, 16:35–16:37 EDT), `lmcache_status.json` (`rejected`, fallback native prefix caching, 16:29:05), `meta.json`, `server_info.json`, `server_startup.txt` (two server starts: 16:35:19 in job 96392, 18:27:20 in job 96562), `port` (50081 in both jobs), `READY`, `smoke.ok` | `qwen_vllm_pipeline.sh` (`step_env`, `ensure_vllm`, `step_effprobe`, `step_smoke`); `effort_mode.json` via `effort_probe.py`; `READY` touched by hand | Part B (effort verdict, LMCache fallback); `latency_compare.py` reads `meta.json` and `server_info.json` | published (metadata and control files); `meta.json` and `server_info.json` describe job 96562, not the matrix (see below) |
| `latency_profiling_qwen_opt/inherit/` | 8 cells, RW-FQA and FAB-DEEP × none / ancestors × r1, r2, 2026-09-22 22:27 UTC – 2026-09-23 00:43 UTC (18:27–20:43 EDT); per cell a trace with sidecars, `<cell>.cell.json` (tasks, edges, executed edges, status, wall, accuracy, missed facts; the trace path under the old absolute location) and `<cell>.prewarm.json` (per-task reads and handoffs, the "dumps") | `research/08_context_inheritance/inherit_workloads.py --arms none,ancestors --reps 2` (default `--max-seconds 900`) with driver arguments `--prewarm-evict=lfu --prewarm-budget=32k --no-timestamp --vllm-metrics`, via `qwen_inherit.sbatch` (Slurm job 96562 on alphagpu52); no `--effort-policy` or teammate cap was passed and streaming was off | Part B, "Step 3 — complete-coverage inheritance" | pilot, inconclusive: all 8 cells `timeout`, wall 908.1–1,028.0 s (mean 957 s), accuracy 0/5 (RW-FQA) and 0/4 (FAB-DEEP); `inherit_trace_stats.py` reproduces rounds 21–518, 0–2 `reacquire_warn` per cell, 1–2 injections of 5,410–20,197 chars per ancestors cell and none in the none arm |
| `latency_profiling_qwen_opt/slurm-96392.out` | stdout of job 96392: LMCache server failing at engine initialisation and the fallback to native prefix caching, effort probe, lane, probe, smoke gate, 24 runs, tables, compare, the first inheritance pass (8 × "no trace" within a second at 18:01:36, system `python3` without the venv) and stop | `qwen_vllm_opt.sbatch` | provenance of Part B | published (log); its first 29,784 bytes equal the local `pipeline/pipeline_20260922T162159.log` |
| `latency_profiling_qwen_opt/slurm-inherit-96562.out` | stdout of job 96562: serve (healthy after 170 s), the 8 inheritance cells with status, accuracy and wall, the summary table, stop | `qwen_inherit.sbatch` | Part B Step 3 | published (log) |
| `provider_probe_zai_20260922.json` | 27 probe calls against z.ai `glm-5.3-flash` (18 fresh prefill-sweep calls, 2 cached resends, 1 long decode, 6 cache-semantics cases): first block 3.26 s + 0.0569 ms per uncached token (r2 0.82), decode median 78.3 tok/s (55.4–129.0), identical resend 27,200 of 27,233 tokens cached, mid-prompt insertion 0% | `research/05_latency_breakdown/provider_probe.py --json` against z.ai on 2026-09-22 (date from the file name; the records carry no timestamps) | Part B, "Step 5's F table" | published; Part B's figures recomputed from it on 2026-09-24 |

Local only (git-ignored `*.log`, not in the repository):

- `latency_profiling_qwen_opt/<label>.console.log` (24), `latency_profiling_qwen_opt/smoke/FQA-team-smoke.console.log`, `latency_profiling_qwen_opt/inherit/<cell>.console.log` (8): the driver's console output of each run.
- `latency_profiling_qwen_opt/vllm_server_lmcache_20260922T162322.log`: the LMCache attempt, and the only place that keeps its root cause ("Turning off hybrid kv cache manager because `--kv-transfer-config` selects a KV connector that does not support it", then `ValueError: Failed to promote local KV cache specs to one unified type.`); `slurm-96392.out` keeps only the final `RuntimeError`.
- `latency_profiling_qwen_opt/vllm_server_base_20260922T162905.log` (the matrix server, job 96392) and `latency_profiling_qwen_opt/vllm_server_base_20260922T182426.log` (the inheritance-arm server, job 96562).
- `latency_profiling_qwen_opt/vllm_server.log`: symlink to the second server log (`vllm_server_base_20260922T182426.log`); it pointed at the old absolute path under `s15_integrated_harness/traces/latency_profiling_qwen_opt/` and was re-pointed as a relative link during the cleanup.
- `latency_profiling_qwen_opt/provider_probe.log`: the console output of the probe step.
- `latency_profiling_qwen_opt/pipeline/pipeline_20260922T162159.log` (job 96392, main run), `pipeline_20260922T180137.log` (job 96392, `stop`), `pipeline_20260922T182332.log` (job 96562, `serve`), `pipeline_20260922T204344.log` (job 96562, `stop`) and `restarts.log` (5 lines: two start requests at 16:23:22 and 18:24:26, the LMCache rejection at 16:29:05, healthy base servers at 16:35:20 and 18:27:20).

Previously uncited items:

- `latency_profiling_qwen_opt/inherit/`: Step 3 needs dependency-shaped work that the matrix (independent tasks) cannot express, so RW-FQA and FAB-DEEP ran none against ancestors (LFU, 32 KB) on the optimized server. The first pass inside job 96392 failed at once (system `python3` without the venv); the rerun in job 96562 produced these 8 cells, all at the 900 s cap, so Part B leaves the accept/reject with the replay corpora.
- `latency_profiling_qwen_opt/pipeline/`: the pipeline's control and metadata folder (port, `READY`, `smoke.ok`, effort verdict, LMCache status, job metadata, server info, startup lines). `effort_mode.json` and `lmcache_status.json` are the evidence for Part B's effort verdict and LMCache fallback; `meta.json` and `server_info.json` were overwritten by job 96562.
- `latency_profiling_qwen_opt/smoke/`: the smoke gate of the optimized arm under the full policy, run before the matrix; it passed (coverage 0.967, thinking share 0%, 29 calls) and the matrix started at 16:47 EDT.
- `latency_profiling_qwen_opt/compare_glm_vs_qwen.json`: the pipeline's `compare` step always renders GLM against its output folder, so this compares GLM with the optimized arm (the baseline-against-optimized comparison is `scoreboard_ab.json`); committed with the data, never cited; regenerates identically apart from the paths.
- `latency_profiling_qwen_opt/pipeline/meta.json`: job metadata written by the pipeline's `env` step at every invocation; last rewritten by the `stop` call of job 96562 at 2026-09-22 20:43:44 EDT, so it records job 96562 on alphagpu52 rather than the matrix job 96392 on alphagpu51, whose metadata survives as the first line of `slurm-96392.out`. The compare T0 table is unaffected: both jobs used the same GPU type, server flags, versions and harness commit.
- `latency_profiling_qwen_opt/pipeline/server_info.json`: `/version` and `/v1/models`, rewritten at every healthy server start; the last write was job 96562's `serve` step at 18:27:20 EDT, so it describes the inheritance-arm server. The matrix server's `/v1/models` answer is kept in every matrix trace's `profile_meta.server_info`.
## Source map

One row per `##` / `###` section of both sources; neither is a digest, so nothing was dropped. Headings are unchanged, so `new_location` names the same heading in this file.

| old file · old section | new location | status |
|---|---|---|
| `weekly_progress/efficiency_methodology.md` · (title and opening) | Part A (opening) | kept (the two-line H1 joined into the Part A heading) |
| `weekly_progress/efficiency_methodology.md` · The question | Part A “The question” | kept |
| `weekly_progress/efficiency_methodology.md` · The answer, in one line | Part A “The answer, in one line” | kept |
| `weekly_progress/efficiency_methodology.md` · The cost model | Part A “The cost model” | kept |
| `weekly_progress/efficiency_methodology.md` · Bottleneck verdict per provider | Part A “Bottleneck verdict per provider” | kept |
| `weekly_progress/efficiency_methodology.md` · Where our data differs from the literature (the complementary contribution) | Part A “Where our data differs from the literature (the complementary contribution)” | kept (row 'Graph-exploiting agent serving' corrected (graph-ordered retention); row 'AgentFold / Context Folding' corrected (5.17 → 3.00 harness rounds, −70% micro-loop wall)) |
| `weekly_progress/efficiency_methodology.md` · The methodology, step by step | Part A “The methodology, step by step” | kept |
| `weekly_progress/efficiency_methodology.md` · Step 0 — Lock the attribution scoreboard (the method itself) | Part A “Step 0 — Lock the attribution scoreboard (the method itself)” | kept |
| `weekly_progress/efficiency_methodology.md` · Step 1 — H: make memory extraction and recall asynchronous (highest ROI, pure engineering) | Part A “Step 1 — H: make memory extraction and recall asynchronous (highest ROI, pure engineering)” | kept |
| `weekly_progress/efficiency_methodology.md` · Step 2 — D: control output volume — per-round thinking budgets | Part A “Step 2 — D: control output volume — per-round thinking budgets” | kept |
| `weekly_progress/efficiency_methodology.md` · Step 3 — R: ship complete-coverage context inheritance (the round lever) | Part A “Step 3 — R: ship complete-coverage context inheritance (the round lever)” | kept (LFU vs LRU corrected) |
| `weekly_progress/efficiency_methodology.md` · Step 4 — R: context-window guardrails (cheap tail wins) | Part A “Step 4 — R: context-window guardrails (cheap tail wins)” | kept |
| `weekly_progress/efficiency_methodology.md` · Step 5 — F: API-side call consolidation and provider routing | Part A “Step 5 — F: API-side call consolidation and provider routing” | kept |
| `weekly_progress/efficiency_methodology.md` · Step 6 — Scheduling policies on the vLLM endpoint (pays at concurrency) | Part A “Step 6 — Scheduling policies on the vLLM endpoint (pays at concurrency)” | kept |
| `weekly_progress/efficiency_methodology.md` · Step 7 — Evaluation and paper positioning | Part A “Step 7 — Evaluation and paper positioning” | kept (flag on 'three tests') |
| `weekly_progress/efficiency_methodology.md` · Priority order by expected value | Part A “Priority order by expected value” | kept (flag on 'failed three tests') |
| `weekly_progress/efficiency_methodology.md` · Threats and what is not established | Part A “Threats and what is not established” | kept |
| `weekly_progress/efficiency_methodology.md` · Sources | Part A “Sources” | kept |
| `weekly_progress/efficiency_impl_report.md` · (title and opening) | Part B (opening) | kept |
| `weekly_progress/efficiency_impl_report.md` · What was built, per step | Part B “What was built, per step” | kept (row 3: LFU vs LRU corrected) |
| `weekly_progress/efficiency_impl_report.md` · Verification before spending GPU time | Part B “Verification before spending GPU time” | kept (SMOKE-opt trace recovered into smoke_opt/) |
| `weekly_progress/efficiency_impl_report.md` · Baseline scoreboard (existing traces, no re-run needed) | Part B “Baseline scoreboard (existing traces, no re-run needed)” | kept (flag on the fit's F and P) |
| `weekly_progress/efficiency_impl_report.md` · Step 5's F table (provider probe, 2026-09-22) | Part B “Step 5's F table (provider probe, 2026-09-22)” | kept |
| `weekly_progress/efficiency_impl_report.md` · A/B results (optimized matrix vs baseline, same GPU type, same server flags) | Part B “A/B results (optimized matrix vs baseline, same GPU type, same server flags)” | kept |
| `weekly_progress/efficiency_impl_report.md` · Per-group scoreboard (baseline → optimized) | Part B “Per-group scoreboard (baseline → optimized)” | kept |
| `weekly_progress/efficiency_impl_report.md` · Per-step attribution (each step leaves a distinct trace signature) | Part B “Per-step attribution (each step leaves a distinct trace signature)” | kept |
| `weekly_progress/efficiency_impl_report.md` · Step 3 — complete-coverage inheritance (dependency-shaped workload) | Part B “Step 3 — complete-coverage inheritance (dependency-shaped workload)” | kept (flag on 'accuracy 0/5 in every cell'; LFU vs LRU corrected) |
| `weekly_progress/efficiency_impl_report.md` · What shipped, and where it lives | Part B “What shipped, and where it lives” | kept |
| `weekly_progress/efficiency_impl_report.md` · Bottom line | Part B “Bottom line” | kept |

## Cleanup notes

- Part A heading: the source's H1 was wrapped over two lines ("… a step-by-step" / "improvement methodology"); both are joined into the single Part A heading.
- Part A, literature table, row "Graph-exploiting agent serving": graph-ordered retention corrected from 0.7% to 1.4% (SWE-bench, 8 KB, corrected replay of 2026-09-23), with the note that the replay no longer counts as a failed graph test.
- Part A, literature table, row "AgentFold / Context Folding": "3.00 → 5.17 → −70%" corrected to 5.17 → 3.00 successor rounds (real harness) and −70% wall (scripted micro-loop, 7.22 → 2.50 rounds).
- Part A, Step 3: LFU against LRU at 8 KB corrected from 21.9% vs 3.0% to 19.2% vs 15.8% (τ-bench/GAIA; SWE-bench 2.6% vs 0.3%).
- Part A, Step 7 ("DAG-retention negative results (three tests)") and the explicit non-goals ("failed three tests"): flagged, because the replay test in that count was withdrawn on 2026-09-23.
- Part B, "What was built", row 3: LFU against LRU corrected from 21.9% vs 3.0% to 19.2% vs 15.8%.
- Part B, "Verification before spending GPU time": the `SMOKE-opt` trace was never committed; it existed only in `/tmp/opt_smoke/` and was copied into `latency_profiling_qwen_opt/smoke_opt/` on 2026-09-24 (trace + `.inputs.jsonl` + `.reads.jsonl`).
- Part B, "Baseline scoreboard": the fit's F and P flagged — `term_scoreboard.py`'s uncached-token count is negative on these traces, so those two constants are not comparable with the computed-token fit of `research/05_latency_breakdown/` Part B; D agrees.
- Part B, "Step 3 — complete-coverage inheritance": "accuracy 0/5 in every cell" flagged (FAB-DEEP cells are 0/4), and LFU against LRU corrected from 21.9% vs 3.0% to 19.2% vs 15.8%.
- No other numbers of either note were changed.
