# Paper reading: Continuum — Efficient and Robust Multi-Turn LLM Agent Scheduling with KV Cache Time-to-Live

**Li, He, Mang (equal), Q. Zhang, Mao, Chen, Zhou, H. Zhang, Cheung, Gonzalez, Stoica** — UC Berkeley /
Stanford / Tsinghua. arXiv:2511.02230v7 [cs.OS], 8 Sep 2026; submitted to PVLDB Vol. 20 No. 1 (2027).
Code: https://github.com/Hanchenli/vllm-continuum
(The PDF body spells the system "Continnum"; the title, repo, and abstract use "Continuum".)

Read 2026-09-16. Related notes in this repo: `compaction_kv_reread_related_work.md` (§F4 already lists
this paper), `latency_breakdown.md`, `tool_execution_cost.md`, `agent_e2e_bottleneck.md`.

---

## 1. The major problems it encounters

The paper's frame: **an inference engine's eviction policy assumes "decoding finished" == "request
done". For a ReAct agent, "decoding finished" means "about to call a tool", and the same 70–90k-token
context comes back in one or two seconds.** They call the current behaviour *end-of-turn eviction* and
name four problems it creates.

### P1. Turn-based eviction → repeated prefill of a long prefix

vLLM and SGLang free a request's KV blocks as soon as decoding ends if anything is waiting. When the
tool returns, the engine must redo the full prefill, or reload from DRAM when offloading is on. Their
trace study (100 mini-swe-agent traces on SWE-Bench + 100 BFCL v4 Web Search traces, GPT-5 as the
base model):

| Dataset | Turns/program (μ, σ) | Tool time ms (μ, σ) | Tokens/program (μ, σ) |
|---|---|---|---|
| SWE-Bench | 10.9, 2.1 | 925, 3,550 | 70,126, 19,732 |
| BFCL v4 | 6.3, 2.3 | 1,923, 2,133 | 93,256, 68,687 |

So the cache is discarded ~10 times per program and re-materialised after a pause shorter than a human
would take to type a reply. Their illustrative SWE-agent trace (Fig. 2) has LLM steps of 1.3–5.2 s
separated by tool calls of 8 ms (`sed`), 10 ms, 137 ms (`grep`) — and one 14 s `pytest`.

### P2. Per-turn queueing delay — the paper's actual novel problem

This is the contribution that distinguishes it, and it is worth stating precisely:

> Even when reload is free, an evicted program pays a scheduling bubble every turn, because its next
> request must re-enter the waiting queue and wait for GPU *memory* to be released by other requests'
> in-flight prefill/decode.

Three properties make it nasty:
- **It survives fast offloading.** With non-blocking LMCache offload, `Prefill-Reload` shrinks toward
  zero, but the returning request still waits for memory. Bandwidth cannot fix a scheduling problem.
- **It accumulates linearly in turns.** Paid once per turn × 10.9 turns (SWE-Bench) or more.
  Fig. 14 scales the trace to 1×–5× turns (10.9 → 50.6 turns): baselines degrade monotonically,
  Continuum stays flat.
- **It is invisible to offline profiling.** It is a function of live scheduler and memory state, which
  is exactly why every prior cost model leaves it out. The paper says this explicitly: "since this
  delay is not measurable by offline profiling, we need to design a new model to include its impact."

It also destroys program ordering: a program that arrived first gets scheduled behind programs that
arrived later, and raising the returning request's priority does not help, because it is blocked by
computation already resident on the GPU, not by queue position.

Fig. 4 is the money plot: per-program total waiting time under CPU offloading. InferCept's bubbles are
**comparable to vanilla vLLM** despite InferCept's genuine reload savings.

### P3. Tool durations are long-tailed, so a static "preserve" is unsafe

- Slowest 10% of BFCL `fetch_url` calls = **52.5%** of total delay.
- Slowest 10% of SWE-Bench `cd` calls = **94.1%** of total delay.

Pinning "until the next request arrives" (InferCept's preserve) therefore lets one hung or slow tool
hold GPU memory indefinitely; in the limit every block is pinned by a program whose tool has not
returned and the scheduling loop **deadlocks**.

### P4. The TTL itself is a two-sided trap (Fig. 6)

Too long: pinned KV blocks other requests and costs throughput. Too short: the cache is evicted just
before the tool returns, so you paid the memory occupancy **and** still pay prefill plus the bubble —
strictly worse than not pinning. This is what forces a real cost model rather than a constant.

### Enabling observation (Fig. 3)

Expected remaining tokens *decrease* as a program advances, on both workloads. So "prioritise programs
that arrived earlier, or have executed more turns" approximates clairvoyant SRTF. This is what licenses
program-level FCFS — and, later, the `η` term.

---

## 2. Why other papers don't solve it

Their Table 1 reduces the field to three axes: **retains KV across the tool pause / models per-turn
queueing delay / bounds retention time**. Only Continuum ticks all three.

| Method | Retains KV | Per-turn queueing | Bounded retention |
|---|---|---|---|
| vLLM | ✗ | ✗ | ✗ |
| Autellix | ✗ | ✗ | ✗ |
| Pie | ✓ | ✗ | ✗ |
| InferCept | ✓ | ✗ | ✗ |
| Continuum | ✓ | ✓ | ✓ |

**InferCept (ICML'24) — the closest work, and the one they argue against hardest.** It has exactly the
right mechanism (a `preserve` op that pins KV between tool calls) and the wrong policy, for two
reasons:
1. Its preserve decision compares *reload cost* against GPU occupancy cost **for the immediate next
   turn only**. With modern non-blocking offload (LMCache) reload is cheap, so the condition is
   **rarely triggered** — the preserve op mostly does not fire, the program gets evicted, and it eats
   the queueing bubble that InferCept never modelled (Fig. 4).
2. Its preserve is **static and unbounded** — pin until the next request arrives. Under the long tail
   of P3 that is memory waste and potential deadlock. The paper's verdict: "impractical for real-world
   deployment."

**Autellix (PLAS).** Gets program-level scheduling right — prioritise by cumulative program service —
but **keeps end-of-turn eviction entirely**, so it never addresses KV retention. Its heuristic (longer
attained service ⇒ longer remaining) is also empirically wrong on SWE-Bench, where it loses to plain
vLLM even though it wins on BFCL. And its gains *shrink* once CPU offloading is enabled.

**Pie (SOSP'25).** Mechanism without policy: programmable fine-grained handlers let a developer
express custom tool handling, but the developer must hand-design scheduling per agent, and Pie offers
nothing that adapts to dynamic tool latency or multi-turn dependency.

**Parrot (OSDI'24), Alto, Teola/Ayo (ASPLOS'25).** All assume a **static or deterministically defined
DAG**. A ReAct agent's dependency graph only exists at runtime — the next tool depends on the last
tool's output. Structurally inapplicable.

**Per-request schedulers: Apt-Serve, AlignedServe, Tempo, Wadlom et al.** Optimise individual requests,
prefix-aware batching, or mixed-SLO request classes. None model tool-call duration variance or
request-specific agent state spanning a pause.

**Cache-Craft.** Chunk-cache reuse for RAG *documents*, not request-specific agent state across turns.

**The whole offloading line (LMCache, AttentionStore, CacheGen, CacheBlend, HiCache).** Orthogonal, and
the paper proves it rather than asserting it: gains hold on top of DRAM offload (Fig. 10) and on top of
SSD offload at 400 GB and 800 GB (Fig. 15). Cheaper reload cannot remove a memory-contention bubble.

**Classical TTL work (DNS, CDN, memcached, Twitter cache study).** TTL there bounds *staleness* over
independent entries with semantic correctness constraints. Here entries are coupled through GPU memory
pressure, prefill cost, and scheduling fairness. Their novelty claim: first system to regulate KV cache
with a TTL set as a function of predicted tool-call duration, scheduling-side delay propagation, and
workload pattern.

---

## 3. Its major methodology

### (a) A utility model denominated in time

Everything is converted to seconds of job-completion latency, so cost and benefit are comparable.

**Cost of pinning** request `r` for TTL `τ`:

    Cost(τ, r) = (MemUsage(r) / M) · τ

`M` = average GPU memory footprint of active requests, so `MemUsage(r)/M` is *how many average-sized
requests get blocked*. Stated assumption: the waiting queue is always backlogged enough for that
blocking to be real.

**Benefit of pinning** (realised if the next request arrives within `τ`):

    Benefit(r) = CacheMissCost(r) + OutofOrderCost(r)

    CacheMissCost(r)   = (MemUsage(r) / M) · Prefill-Reload(r)
    OutofOrderCost(r)  = (T / M) · MemUsage(r) · η

- `Prefill-Reload(r)`: from a <10-minute offline profile per (model, hardware) pair — GPU↔CPU offload
  bandwidth, plus a quadratic fit of prefill time vs context length over chunk sizes {1000, 2000,
  4000, …}. They approximate with *full* prefill, acknowledging that a few pages may survive in GPU.
- `T`: sliding-window average queueing delay actually experienced by evicted requests — **measured
  online**, which is the only way to capture P2.
- `η`: the **memoryfulness factor**, and the prettiest idea in the paper.

      η = −Corr(k, N − k)

  over (requests already served `k`, requests remaining `N − k`) per program.
  - Fixed turn count per program ⇒ `Corr(k, −k) = −1` ⇒ **η = 1**: preserving order genuinely
    approximates Shortest-Job-First, so the queueing saving is real.
  - Geometric turn count (memoryless) ⇒ **η = 0**: expected remaining work is constant regardless of
    progress, so keeping order buys nothing and the queueing term vanishes.
  - **η < 0** (anti-memoryful, extreme long tail): progress reveals *more* remaining work, so you
    should switch between programs frequently. Designed for, not observed in their traces.

  In one scalar: *the value of preserving program continuity is exactly how predictable the remaining
  work is.*

**Choosing the TTL:**

    τ* = argmax_τ  P(τ, f) · Benefit(r) − Cost(τ, r)

and because `MemUsage(r)/M` is a shared factor it cancels, leaving the form they actually implement:

    τ* = argmax_τ  P(τ, f) · ( T·η + Prefill-Reload(r) ) − τ

so online you only need `T` and `P`. `P(τ, f)` = empirical CDF of tool `f`'s historical durations
`S[f]`; solved by enumerating the observed durations (plus `τ = 0`, i.e. "don't pin") as candidates.

**Cold start,** three tiers with threshold `K = 100`, `T` initialised to 0: per-tool CDF → global CDF
over all tools → a fixed `T_default` derived from the same cost model under `ToolCallDuration ~ Exp(1)`
and `η = 1`. They note the stats can also be harvested during post-training, since agents are trained
against their tools.

### (b) TTL-aware scheduling priority

A multi-key tuple, in order: **(1)** preempted requests first (unchanged from vLLM), **(2)** requests
pinned within their TTL window, **(3)** program-level arrival time — FCFS over *programs*, not
requests. (3) is what restores the continuity that eviction destroys.

### (c) System: ~1k lines of Python on vLLM

Deliberately modular; the core scheduler loop barely changes. Clients attach a `program_id` to every
request. A **Tool-Call Handler** is invoked on request arrival and completion, with three hooks —
`func_call_finish(tool, timestamp)`, `update_tool_call_time(program_id, timestamp)`,
`set_up_ttl(request, tool)`.

- **Tool identification**: parses the OpenAI function-call schema from the LLM output; for SWE-Bench,
  takes the first word inside the bash block as the tool name. Extensible per model schema.
- **Duration measurement**: purely server-side, as the inter-request interval
  `t_arrive^{p,i+1} − t_finish^{p,i}` within a program. No client instrumentation needed. *(Note: this
  therefore folds in client, network, and harness time, not just tool execution — see §5.)*
- **Pin**: record `(request, now + τ*)` in `pinned_requests` and skip freeing the KV blocks.
- **Unpin**: scanned at the head of every scheduling step, but only for programs whose follow-up
  request is *not* already in the waiting queue — avoids evicting a cache whose consumer has arrived
  but hasn't been scheduled. Program termination proactively unpins.
- **Deadlock avoidance**: when the head of the queue cannot be allocated, iteratively evict pinned
  victims **with the latest program arrival time** until it fits.

### (d) Evaluation

Trace replay with Poisson program arrivals. Models Llama-3.1-8B, Llama-3.1-70B, Gemma-3-12B; hardware
A100-SXM (RunPod), H100 (AWS and "Company A"), B200 (on-prem). Offload: vLLM 0.10.2 + LMCache 0.3.7,
100 GB DRAM on A100, 200 GB/GPU on B200/H100. Workloads SWE-Bench (mini-swe-agent), BFCL v4 Web Search
(scaled 0.4× to fit 100 requests in Llama's 128k window), OpenHands (multi-SWE-bench, Go). Baselines:
vLLM, PLAS-from-Autellix reimplemented on vLLM (+LMCache = "Autellix+"), InferCept reimplemented with
its cost model updated for non-blocking offload, and for the real-agent run SGLang 0.5.5 with
cache-aware routing and NVIDIA Dynamo 0.7.0 at 1P1D.

Results:
- **Trace replay: 1.12–3.66× delay reduction, 1.10–3.22× throughput** (the intro's honest range); up to
  ~2× average response time vs vanilla vLLM at Llama-8B. Better P90/P95 (Fig. 11).
- **Robustness**: stable across max batch size 16–256 and chunk size 256–4096 (Fig. 13, 1.3–2.0×);
  holds with SSD offload at 400/800 GB; gains *grow* with turn count while baselines degrade (Fig. 14,
  up to 3.7× at 50.6 turns).
- **Overhead**: scheduling latency 0.96 ms without offload / 2.30 ms with, vs vLLM's 0.95 / 2.33 ms.
- **Ablation** (Fig. 16): program-level FCFS alone < + static cold-start TTL < full cost model. Each
  piece contributes; neither alone gets there.
- **Real distributed SWE-agent**: 500 SWE-Bench-Verified tasks on Company A's H100 testbed with
  session-aware routing → **up to 8.18×** lower delay *and a higher pass rate* than SGLang/Dynamo. The
  pass-rate gain is an artifact worth knowing: SWE-Bench kills Docker environments at 15 minutes, so
  baseline slowness converts directly into scored failures.
- **RL rollout micro-benchmark**: OpenHands + GLM-4.5-fp8 on 8×H100 → 144.9 steps/min vs ThunderAgent
  114.8 and vLLM 93.4.

Scope they declare (§7): the design assumes the sequential `reason → tool → reason` rhythm (parallel
tool calls are fine). Speculative branching, asynchronous multi-agent coordination, and context folding
violate it and are left to future work.

---

## 4. TL;DR — one sentence

**Continuum observes that an inference engine evicting an agent's KV cache the moment it emits a tool
call costs not just a re-prefill but a fresh queueing bubble on every one of ~11 turns — a cost that
fast DRAM offloading cannot remove and that no prior policy models — and fixes it by pinning the cache
with a per-tool, cost-modelled time-to-live (balancing occupancy against `P(tool finishes in τ) ×
(re-prefill + queueing delay × how predictable the program's remaining work is)`) plus program-level
FCFS, buying 1.1–3.7× lower job completion time in trace replay and up to 8.18× on a real distributed
SWE-agent deployment for ~1k lines on top of vLLM and ~1 ms of scheduling overhead.**

---

## 5. Caveats and reading-against-the-grain notes

Things to keep straight before citing this paper:

1. **Two very different headline numbers.** The abstract's "over 8×" and the intro's "1.12× to 3.66×"
   are not the same experiment. 8.18× is the distributed real-agent run against SGLang/Dynamo, where
   part of the gap is the 15-minute Docker timeout converting latency into failures. Cite 1.12–3.66×
   for the controlled comparison.
2. **Model-coverage mismatch.** The abstract advertises "Llama-3.1 8B/70B, Gemma-3 12B, and GLM-4.5
   355B"; GLM-4.5 appears only in the RL rollout micro-benchmark (Table 5), never in the end-to-end
   delay/throughput curves.
3. **`Prefill-Reload` is approximated as full prefill**, ignoring pages that survive in GPU. They
   argue the residue is small under contention. Acknowledged, not measured.
4. **`η` is one workload-global scalar** estimated from history, applied to every request. The
   interesting `η < 0` regime is designed for but never exercised.
5. **The cost model assumes a backlogged queue.** Under light load, `Cost(τ, r)` overstates the real
   opportunity cost, so TTLs come out too short — precisely the regime where pinning is free.
6. **Deadlock victim selection inverts the priority rule**: FCFS favours early-arriving programs, but
   when memory runs out the victims are the *latest*-arriving pinned programs. Self-consistent, but it
   means tail programs absorb all the memory pressure; no tail-fairness analysis is given.
7. **Tool duration is inferred from the server-side inter-request interval**, so it silently includes
   client, network, and *harness* time — not tool execution. That matters a lot for us (below).
8. **This is the append-only case.** Continuum pins KV across a tool pause on the assumption that the
   context grows by appending the tool result. It says nothing about **compaction**, which rewrites
   the prefix and destroys the value of the pin. That is the gap `compaction_kv_reread_related_work.md`
   names, and Continuum does not close it.

## 6. Why this matters for our own measurements

- **Continuum's P2 is the systems-side explanation of the biggest term in our numbers.**
  `latency_breakdown.md` regresses round latency to **3.7 s fixed + 0.033 ms/uncached-token +
  17.4 ms/output-token**, and `in-engine-tool-execution-landscape` measured that ~80% of a hosted
  fault round is fixed provider latency (queue + a 4.6 s TTFT floor even for 371 tokens). "Per-turn
  queueing delay" is the published name for that fixed term. This is the paper to cite when we claim
  the floor is scheduling, not compute.
- **Our tool times are 20–90× shorter than theirs.** We measure tools at **10–50 ms** against their
  925 ms / 1.9 s means. Shorter pauses push `P(τ, f)` toward 1 at tiny `τ`, so on our workload the TTL
  decision should be nearly unconditional — retention is almost free. Continuum's mechanism is *more*
  favourable for a Claude-Code-shaped harness than for the agents they evaluated.
- **But the harness, not the tool, sets the pause length.** Our `memory_extract` step costs **13–21 s
  per turn** — an order of magnitude longer than any tool call in their traces, and it lands inside
  exactly the inter-request interval Continuum measures as "tool duration" (caveat 7). Pinning 70–90k
  tokens of KV for 13–21 s inverts their cost/benefit: `Cost(τ, r) = (MemUsage/M)·τ` grows linearly in
  that interval while `Benefit` is fixed. **Harness-side post-turn work is a first-class input to a TTL
  policy, and no paper models it.** That is a defensible gap for us: their `η` captures workload turn
  structure, ours would need a term for harness pause structure.
- **Positioning.** Continuum is the strongest prior art for "keep the KV resident across the pause"
  (§F4 of the related-work report). It does not touch the two costs we measure after *compaction*
  (re-prefill of a rewritten prefix, re-acquisition of evicted files), and it explicitly excludes
  async multi-agent coordination — which is where our teammate-context work lives.
