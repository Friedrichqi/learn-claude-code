# Agentic system efficiency: bottleneck verdict and a step-by-step
# improvement methodology

Synthesises every profiling experiment in this repo into a cost model, a bottleneck
verdict per provider, and an ordered intervention plan. Every predicted gain below is
derived from our own measurements, and each step names the current paper line it
extends or contradicts. Written 2026-09-22.

Inputs: `s15_integrated_harness/latency_profile.md` (+ Appendix C, Qwen/vLLM rerun),
`tool_latency_profile.md`, `tool_cost_profile.md`, `realworld_inheritance_profile.md`,
`dag_census_profile.md`, `dag_kv_reuse_profile.md`, `input_redundancy_profile.md`,
`kv_splice_profile.md`, `context_intervention_profile.md`, and the weekly notes in
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
| Graph-exploiting agent serving: Parrot semantic variables, DAG schedulers | task/request DAG structure | Our task DAG failed three independent tests as a *retention* signal (placebo delta −1.0; graph-ordered retention 0.7%; census: edges are a shadow of task text). The DAG is fine for dataflow/scheduling; worthless for predicting what bytes to keep. |
| MAS efficiency: shared-context and team papers (e.g. Decentralized MAS with Shared Context, arXiv 2026-06) | teams + shared context as a win | **Solo beat teams 1.4–1.8×** on our small tasks; cross-teammate prompt duplication reached the workload's design ceiling (8–27% on FQA); teammates sharing the GPU cut prefill throughput 5.7k → 3–4.2k tok/s. Teams must pay for themselves in parallelism, not assumed. |
| Tool-use cost work | making tools cheaper / exposing costs | Tools are 0.1–0.5% of a round, and advertising per-tool costs to the model moves nothing (p=1.0; tie-breaker only). The expensive unit is the *round*, never the tool. |
| AgentFold / Context Folding (ICML'26) | fold context via model-generated summaries | Our injection experiment: a **summary** handoff had no effect (CI straddles 0); **bytes** cut rounds 3.00 → 5.17 → −70% wall in the micro-loop. Compress the format, never summarise the span away. |

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

- Predicted: **−28–48% team wall** (latency_profile.md's own amortisation bound).
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
rounds 36.4% → 90.9%), ranked by **reuse frequency not recency** (LFU 21.9% vs LRU
3.0% removable at 8 KB), shared pool across agents (model-mismatch costs ~2 points),
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
  tests); hybrid-attention KV block alignment; tool-cost behavioural inertness.

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
DAG-directed retention (failed three tests); KV-splice (negative standalone value);
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
