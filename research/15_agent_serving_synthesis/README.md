# 15 · Serving an agent harness vs vanilla multi-user serving: what 01-14 add up to, and what tiered memory can do

> **Status:** complete (2026-10-05). This is a synthesis, not an experiment: no new runs. Every number comes from the
> READMEs, tables and notes of 01-14 and `related_work/`, plus one external figure (ShareGPT request lengths, from the
> vLLM paper). Derived and ad-hoc numbers are marked in the text and listed in
> [Numbers that are derived, ad hoc or external](#numbers-that-are-derived-ad-hoc-or-external).
>
> **Question (user, 2026-10-05):**
> 1. Go over every profiling experiment under `research/`: proposals, quantitative results, observations, claims,
>    conclusions.
> 2. Summarise each experiment that ran: proposal, observations, final conclusions.
> 3. Brainstorm: given the results, what is the major difference between serving an agent system and a vanilla
>    multi-user serving system?
> 4. Given the tiered memory architecture, what can memory management do about that bottleneck?
>
> "Vanilla" is taken in both senses the user picked: chat-style serving (Table A) and solo agent users versus agent
> teams (Table B).
>
> **Commits:** none. **Presented:** —

**Files**

| file | what it is |
|---|---|
| `README.md` | this document |
| `kv_lifecycle.py` → `data/tables/kv_lifecycle.md`, `kv_lifecycle_12.md` | Part 4: edits, garbage, reuse distance, KV bandwidth and pool composition, from 11's and 12's scheduler logs (offline, ~30 s for all of 11) |
| `content_reuse.py` → `data/tables/content_reuse.md` | Part 4: tool-result content re-read by the same session or fetched before by another session on the same codebase, from 11's request captures (offline, ~2 min) |
| `edit_anatomy.py` → `data/tables/edit_anatomy.md` | §4.6: what a harness edit discards, message by message (removed / surviving at shifted positions / previous reply), from 11's captures (offline, ~2 min) |
| `pool_sim.py` → `data/tables/pool_sim.md`, `pool_sim_dram.md`, `pool_sim_pin.md` | Part 4, I15 and I22: trace-driven pool simulation. GPU arms: LRU vs dead-first eviction (~4 min for five cells × three pool sizes). `--dram 0,64,128,256`: a write-through DRAM tier with and without dead-version invalidation (~3 min for n16 + n32). `simulate(..., pin_arrival=True)`: pin at arrival on n12/n16/n32 (run inline; table in `pool_sim_pin.md`). A pool-size sweep of LRU vs dead-first on the n04/n08 traces (run inline; `pool_sim_sweep.md`, §4.6) |

All three read `research/11_multiuser_kv/data/` (and 12's `o1` and `p1a` cells) read-only, and run from the
repository root with `~/.venv/bin/python`.

**Contents**
- Short answer
- Part 1: the experiments, one row each
- Part 2: what changes when the served application is an agent
- Part 3: tiered-memory management, a ranked brainstorm
- Part 4: deeper brainstorm from the raw logs (findings, new ideas I15-I24, theses, §4.6 dead-first vs vLLM's policy)
- Numbers that are derived, ad hoc or external
- Sources

**Terms**
- **Round:** one agent-loop iteration (one model call plus its tools).
- **Turn:** one user request or lead activation.
- **Session:** one harness process, i.e. one user.
- **N:** the number of concurrent sessions on one server.
- **Turning point:** the N at which the KV pool stops holding every session's working set (8-12 on one H200 with Qwen3-32B, 11).
- **Reducible rounds:** what 07/08 call "removable rounds" or "round recall".
- **Citations:** `(11 §3.3)` = `research/11_multiuser_kv/README.md` §3.3; `(13 S0)` = `research/13_tiered_memory/RESULTS.md` §1 and
  `data/tables/S0_ceilings.md`.

## Short answer

- **The major difference.**
  - A vanilla multi-user server treats the KV cache as per-request scratch space. Requests are independent and
    histories short, so a lost cache entry costs a small re-prefill. The server is limited by compute and batching.
  - An agent session is a chain of dependent calls: a median 30 per task (p10-p90 4-325). Its ~24k-token context comes
    back every few hundred milliseconds. So the server has to keep each session's working set resident: about a context
    window, ~5-7 GiB of KV on Qwen3-32B.
  - **Memory residency, not compute, is the binding resource.** The typical failure is a **recompute storm**, not a
    queue. Past 8-12 users on one H200:
    - the prefix-hit rate falls from 86 % to 1-2 %;
    - 74 % of GPU time goes to re-prefilling evicted histories;
    - throughput drops to a third (11).
- **The bottleneck has three faces, all of them recompute:**
  - **Capacity**, above the turning point: eviction recompute takes 65-74 % of GPU time.
  - **Addressing**, below the turning point:
    - the harness edits its own prompt, so 60-80 % of the tokens prefilled at N ≤ 8 are re-prefill after its edits;
    - a chat-template re-render throws away the previous reply's KV on 99.9 % of calls.
  - **Interference:** decodes wait behind those re-prefills for 21-45 % of a 32B call at N = 4-8 (13 S0).
  - Teams make all three worse:
    - 1 + k sessions per user;
    - teammate contexts that never shrink;
    - the same files held 2-7× at different offsets.
- **What memory management can do, in order of expected value:**
  1. **Working-set admission plus a DRAM tier sized to the fleet.**
     - A session gate sized to HBM gave 3.5-3.8× (measured).
     - A DRAM tier gave 2.8-3× while it held the working set (measured).
     - Each H200 has ~250 GB of mostly idle host DRAM behind it.
  2. **Edit-aware, position-shifted KV:** patch the cache at the harness's edit point instead of re-prefilling the
     suffix. This skips 79.6 % of post-edit prefill with no change at agent level (10), and it also shrinks the decode
     stalls.
  3. **Compressed lower tiers:** FP8 KV fits about 2× the users before the turning point (inferred).
  4. **For teams:**
     - aligned shared blocks or content-addressed segments;
     - a task-boundary release of teammate history;
     - session-level prefetch driven by the task board.
- **Not the bottleneck:** tools, the CPU, host DRAM and the links (06, 12). The lower memory tiers are idle capacity.
- **Added 2026-10-06 (Part 4).** Two offline analyzers re-read 11's and 12's logs and captures:
  - Below the turning point, only about 5 % of the tokens the server prefills is content it never computed before. The rest
    is old content at a new position, after an edit:
    - about half is surviving context re-prefilled after a harness edit;
    - about 40 % is tool results, which the same session had already fetched 66-76 % of the time, and another
      session on the same codebase 6-25 % of the time.
  - At least 19-24 % of the KV pool at the operating point holds dead blocks: superseded versions and re-rendered
    replies. A trace simulation shows that evicting them first on the GPU removes most eviction recompute while the
    pool has slack, but does not move the turning point. In a write-through DRAM tier, though, invalidating them makes an
    undersized tier act about twice as large: at N = 32, 128 GiB goes from 54 % to 86 % of the ideal prefix served.
    Checked against 11's real offload replays, the simulator matches N = 16 (98.0 vs 98.5 % served) but is 22 points
    too optimistic at N = 32 (62 vs 40 % on the same trace). By its own decomposition, garbage accounts for about
    45 % of the real tier's shortfall at N = 32, capacity for ~18 %, and an unmodelled effect for ~36 %. Garbage is
    one cause of offload's weak 1.1× there, not the whole story.
  - KV reads take 38-49 % of every decode step at N ≥ 8, so KV size costs decode bandwidth as well as capacity.
  - New ideas I15-I24 follow from this, notably:
    - an edit-aware garbage collector;
    - position-free paged KV;
    - a content-addressed session and repository KV store;
    - "context ballooning", which treats the harness window and the GPU pool as one working-set problem.

## Part 1 — The experiments, one row each

| # | experiment · status · setup | proposal / question | key observations | final conclusion |
|---|---|---|---|---|
| 1 | **[01 Tracing](../01_tracing/)** · done 09-01→05 · Qwen3.8-27B (vLLM), GLM · §2-§5 | Trace every model, tool, agent and context event of s15/s16, then read the first traces | Interactive turns ran 4-5 model calls of 2.1-19.1 s; tools 0.03-146 ms. 28-teammate stress run (3 h 12 min): 57 tasks with 96 edges, 18 lead compactions, none for teammates. Teammate contexts reached 1.07M chars and six teammates were rejected at the 262k-token limit. memory_extract stopped at its 1,000-token cap in 3 of 4 runs (~17 s each). 105 of 109 bash calls were denied | Tracing works. It set the agenda: unbounded teammate contexts, costly memory extraction, lead-only compaction |
| 2 | **[02A File-read reuse](../02_input_redundancy/)** · done 09-08/11 · GLM, X0-X5 + the Qwen trace · Part A §3 | How much file input repeats (same agent, other agent, re-sent), and why? | 59 % of lead reads and 94 % of teammate reads reopen a file, but exact repeats are 6 % and 0 %. 82 % of lead re-reads follow a compaction. Raising the limit from 50k to 200k chars: 100 calls / 808 s → 7 calls / 168 s. Teammates re-send each byte 12.6× at 90 % cache hit; the lead hits 17 %. 35 % of teammate prompt tokens are finished-task history. X3 sweep: 30-120 rounds below the working set, 6-7 above it | The repetition is structural: the lead forgets (its budget), teammates are isolated and never forget. Fix it in the harness: a budget ≥ the working set, a stable prefix, task-boundary resets. Rounds cost time, prefill does not |
| 3 | **[02B Byte-level redundancy](../02_input_redundancy/)** · done 09-10 · GLM, 4 workloads × 2 + 512k rerun · Part B §2, §6 | Cross-teammate redundancy measured on bytes, by workload shape | Shared corpus 64-69 %, partial overlap 53 %, small shared file 67-80 %, disjoint 0 % (SHA-256 finds 0-3 %). In prefill-heavy runs duplicates are 45-54 % of teammate prompt tokens, in 2-5 resident copies. Duplicates arrive < 60 s apart within a wave and 6-16 min apart across waves, past the ~5-min cache TTL. The 512k lead limit fixed the lead and left teammates at (N−1)/N | Redundancy = corpus overlap × team size. It costs KV memory and context growth, not latency on a hosted API. It needs a shared, position-stable block |
| 4 | **[03 Context interventions](../03_context_interventions/)** · ARCHIVED 09-11 · GLM, X3 at 50k · Results, Conclusions | Do batch reads, outline demotion or a stable prefix cut eviction-driven rounds? | Stable prefix: cache hit 15→60 %, uncached tokens −65 %, but the worst answers (14/20 vs 18). Batching halves rounds at 100k (134→56) and fails at 50k (10-61 % delivered). Outlines take 34 % of the budget, cost more rounds (129 vs 69) and give the best answers (19/20). A cheap round is ~80 % fixed provider latency | Rounds follow residency: size the resident set above the working set, the only change with no quality cost. Compaction ladder: edit when the cache is cold, batch the deletions |
| 5 | **[04 KV splice](../04_kv_splice/)** · ARCHIVED 09-11/13 · Qwen2.5-1.5B/7B, CPU replay · §3-§4 | Keep the KV across a micro-compaction edit by splicing and re-rotating RoPE | −61 % prefill tokens. KL 0.045 vs the edit's own 0.046 (1.5B); 0.085 vs 0.043 (7B). 96-97 % of argmax decisions kept. Up to 1 nat without the RoPE fix. The stale suffix keeps no trace of the evicted file (recall at the prior; key cosine 0.987). Saves 1.3-2.6 % of wall time | Saves FLOPs, not rounds, and the rotation is mandatory. The evicted content lives in its own KV: keep it in a lower tier and re-attach it |
| 6 | **[05A Latency breakdown, GLM](../05_latency_breakdown/)** · done 09-12/16 · 24 runs FQA/CODE/MATH · Part A | What does one agentic round cost, by bucket? | Rounds: 7-10 s (lead), 9-17 s (teammate). The model call is 93-99.9 % of a round: 3.7 s fixed + 0.033 ms per uncached token + 17.4 ms per output token (r² 0.96). Prefill < 2 %, context prep ~10 ms, tools 0.1-0.5 %. Turn-end memory extraction: 13-21 s per turn, 35 % of all wall time. Solo runs 1.3-1.8× faster than teams | Latency = rounds × (fixed cost + decode). At 2-25k-token prompts KV reuse buys capacity, not latency. Make memory extraction async |
| 7 | **[05B Qwen3.8-27B on vLLM](../05_latency_breakdown/)** · done 09-21/23 · RTX PRO 6000 · Part B | The same matrix with the model under our control | The fixed cost is gone (queue 0, HTTP+SDK 27-61 ms). Decode is 90-99 % of a call (39-43 ms per token), prefill 1-6 %. Rounds are 1.5-2.8× longer and runs 1.8-3.4×, at the same quality. Rounds per task do not depend on the model. Hybrid model: 784-token cache blocks, 0 % mid-prompt reuse | On self-hosted serving the cost is output volume × ms per token. Hybrid attention constrains KV sharing |
| 8 | **[06A Tool price](../06_tool_cost/)** · done 09-15 · Part A | What does the harness charge for each tool? | 0.2-10 ms per call, except bash, read_file, glob and create_worktree (607 ms). Tools are 0.15 % of run time | Tools are not a latency lever |
| 9 | **[06B Cost exposure](../06_tool_cost/)** · done 09-14 · 893 + 80 + 24 runs · Part B | Does advertising a tool's latency change which tool the model calls? | Mixed pool: 45 % vs 44 % (p = 1.0). Between interchangeable tools the cheaper one wins 97-100 % of the time: a tie-break, with no dose-response. One "prefer the cheapest" sentence: 31 → 9 % | Cost-aware routing stays in the harness. Exposing costs matters only for hot and cold routes to the same bytes |
| 10 | **[06C/D Tool share vs Sutradhara](../06_tool_cost/)** · done 09-29→10-01 · census + RTX cells · Parts C, D | Does "tools < 1 %" hold against the paper's 30-85 %? | 173,380 calls in 1,801 runs: mean 12.4 ms, 0.09 % of 1,042 h. FTR share ≤ 1.8 % in all but 2 requests. Heavy tools take 15-45 % on Qwen3.8-27B; a fast Qwen3-8B with 1-6 s tools takes 35-89 %. Share ≈ t/(t+L+o): our L = 3.8-48 s per tool call, the paper's 0.7-1.6 s. The paper's figure rests on synthetic requests with rescaled latencies and has not been replicated | The tool share depends on (tool latency, model speed). It is negligible for this harness's file and shell work |
| 11 | **[07A DAG as a reuse signal](../07_task_dag/)** · done 09-16/17 · GLM · Part A §5-§6 | Does an edge predict what a successor reads? Is handing it over worth a round? | Aligned boards: +46 pts of byte coverage; placebo board −1.0. Keeping every earlier task's reads: 85.6 % byte recall, vs 36 % from direct predecessors. Handoff: 7.22 → 2.50 rounds in the micro-loop, 5.17 → 3.00 in the real harness (CI [−3.50, −0.83]). A summary does nothing, irrelevant bulk hurts (60 % completion), a fragmented handoff is re-read 88 % of the time | Reuse saves rounds. The DAG is a shadow of the task text. Retention is about capacity; precision is nearly free |
| 12 | **[07B DAG census](../07_task_dag/)** · done 09-17 · GLM + DeepSeek, 44 runs · Part B §4 | Does the lead keep a dependency graph? | Staged work: 45/45 edges, 0 false. Flat work: 0 edges (p = 5e-13). The whole board is created in one wave, with 0 order violations. Earlier empty boards came from the word "independent" in the prompts | The board is an early, exact scheduling signal, not a retention signal |
| 13 | **[08A Inheritance depth](../08_context_inheritance/)** · done 09-17/18 · GLM · Part A §3-§4d | Hand a successor nothing, its direct predecessors' reads, or all its ancestors'? | Scripted: 8.00 → 6.47 → 1.50 rounds. Harness: CIs straddle 0, and the direct-predecessor arm costs +17,968 tokens. Coverage sweep: a step at complete coverage (a/b = 69×); 3 of 4 documents gain nothing, and accuracy on the missing facts falls 99 → 83 %. Spans trimmed by half cost +0.3 rounds. Graph order beats LRU at 1 of 11 budgets, by 1 point | A round disappears only when all its reads are held: cover completely or not at all, buy whole rounds, trim rather than drop |
| 14 | **[08B Real-world inheritance](../08_context_inheritance/)** · done 09-18/20 · 1,200 Open-SWE + HAL τ-bench/GAIA + live codebase · Part B §0, §3 | Do the inheritance arms survive real trajectories? | Cross-task reuse: SWE 0.8-1.2 % of bytes (2.1-4.3 % of reading rounds) vs τ-bench/GAIA 11.2-38.2 %. Range keys instead of files: 36 → 91 % reducible rounds. With foresight, 8 KB reaches the ceiling. At 8 KB on τ-bench/GAIA, LFU/GDSF 19-21 % vs LRU 16 %. A pool of 8 → 32 tasks: 13 → 36 %. Cross-model reuse works as well as same-model. Only 42 % of rounds are pure reads | Inheritance pays for tool-calling agents and little for SWE work. Key on content ranges, rank by frequency, widen the pool |
| 15 | **[09 Efficiency method + optimized arm](../09_efficiency/)** · done 09-22 · Qwen3.8-27B, RTX · Parts A, B | Cost model Wall = R × (F + P + D) + H; attack the terms in order | 2.3× geometric-mean speedup (−12 to −83 % wall per group) at the same quality: thinking off on FQA/CODE (output −53-82 %), async memory (H −93-99 %), a batching hint (rounds −77 % on FQA-solo). LMCache cannot serve the hybrid model | The leverage sits in the harness (D volume, H, R). Prefill and KV work are capacity engineering |
| 16 | **[10A RoPE-shift reuse](../10_rope_shift/)** · done 09-25 · 500 live Qwen3-32B runs, 683 events, 3 models · Part A §4-§5 | Reuse the surviving KV across a compaction edit, re-rotate its RoPE, prefill only the new text | Skips 79.6 % of post-edit prefill. ~30 % of next rounds change (noise floor 4-6 %), but no more than undoing the compaction changes them, and tool calls stay as valid. Two costs: long verbatim copies −8.9 pts (32B); the 8B stops admitting evicted content is gone. Without the rotation 71-76 % change | Safe at the next-round level on GQA models; the rotation is mandatory |
| 17 | **[10B Selective recompute](../10_rope_shift/)** · done 09-25/27 · same events + H200 latency · Part B | Can CacheBlend or EPIC repair the two costs? | EPIC (the first 32-128 tokens of every reused run, 4-11 % of reused tokens) repairs both on GQA, with next-round KL −33-58 %. CacheBlend repairs part, a random 15 % repairs nothing, and the MLA model is not repaired. Qwen3-32B TTFT: recompute 1,097 ms, prefix caching 765, shift 90, epic128 121 | Shift + EPIC is the cheap default on GQA, at 10-13 % of recompute's TTFT |
| 18 | **[11 Multi-user KV](../11_multiuser_kv/)** · done 09-26/29 · Qwen3-32B, 1 H200, 502 sessions · §3-§4 | Is cross-user KV contention the bottleneck? Does letting ongoing sessions finish help? | **N ≤ 8** is compute-bound: calls 1.7-2.4× slower, 3,077 lead calls/h. **N ≥ 12** thrashes: hit 86 → 1-2 %, 84-85 % of prompt tokens re-prefilled, 74 % of GPU time on recompute, 976-990 calls/h, 10-42× latency, no preemption. Halving the pool at N = 8 reproduces it. Session gate 3.5-3.8×; CPU offload 2.8× / 1.1× (N = 16 / 32); TTL pins 1.2×; smaller chunks 1.0×. The per-second timestamp alone → 2 % hit | The bottleneck is KV capacity against the concurrent working set. Admit by session, sized to the pool, and stop the harness from busting its own cache |
| 19 | **[12 Hardware utilization](../12_hw_utilization/)** · done 10-02/04 · H200, 32B/8B, TP1/TP2, 1,532 sessions · §3-§4 | Is the CPU (or DRAM, or a link) the bottleneck, as recent papers claim? | The GPU saturates first. The busiest host used 1.8 of its 12 cores, and taking cores away leaves throughput flat. Front end ~3.5 ms per 1k prompt tokens. Batch-1 decode: tensor 5 %, HBM bandwidth 70 %. One 32B agent uses ~70 of the 130 GiB vLLM holds; 56 GiB is idle prefix cache, and at N = 16 the pool is 91 % live. PCIe ≤ 0.05 GB/s, NVLink < 5 %. CPU offload 3× at N = 16; TP=2 triples the KV | No CPU bottleneck. HBM binds: its bandwidth in decode, its capacity past the turning point. DRAM, PCIe and NVLink are idle tiers |
| 20 | **[13 Tiered memory S0-S2](../13_tiered_memory/)** · done 10-03/04 · S0 offline, RTX smoke · RESULTS | Can HBM, a peer's HBM, DRAM and flash remove 12's bottlenecks? (B1 tiered KV + hints, B2 peer HBM, B3 P/D, D1 session tokens) | S0 ceilings: decode stalls behind prefill take 21-45 % of call time (N = 4-8); 74-89 % of evictions happen while the call is queued; a DRAM tier leaves 1.8 % of GPU on recompute; D1 ≤ 9 %; B2 ≤ 3 %. RTX: a DRAM restore takes 0.31 s against 6.08 s to recompute. D1 live is exact (794/794), front end −77 %, reply re-prefill −90 %. B1 live: the DRAM tier gives 1.65×, hints are neutral | A plain DRAM tier is what matters. Hints are second-order while DRAM holds the working set. P/D has the largest ceiling, hence 14 |
| 21 | **[14 P/D crossover](../14_pd_crossover/)** · in progress (S0 only) · 2 and 4 H200 · README, PLAN | At what load does P/D (KV over NVLink) stop beating colocated layouts, and which resource binds? | Pre-registered bounds: 1p1d is prefill-bound at ~6.5k lead calls/h, below dp2 (7.9k) and tp2 (8.7k). 4 GPUs: 2p2d 13.1k, 1p1d-tp2 12.1k (prefill-bound), 3p1d 10k (decode-bound), dp4 15.8k, tp2dp2 17.3k. A 10-job chain is queued (~164 GPU-h) | Expected: P/D wins only at low-to-moderate N. Pending |

Not an experiment: `related_work/` holds reading notes on serving-side memory, Continuum, compaction and KV re-read,
teammate memory, and the KV-reuse deck. It is cited below where it supports a claim.

### What the 21 rows add up to

1. **The dominant cost changed with the serving regime.**
   - Hosted single session: the provider's fixed per-call cost (3.7 s) × rounds (03, 05A).
   - Self-hosted single session: decode volume (05B, 09).
   - Many sessions on one GPU: KV residency and prefill interference (11-13).
   - Single-session conclusions do not carry over to multi-user load (§2.5).
2. **Recompute is the common currency of waste:**
   - re-prefill after harness edits: 60-80 % of the tokens prefilled below the turning point (11 §3.4);
   - eviction recompute: 74 % of GPU time above the turning point (11 §3.3);
   - the previous reply re-prefilled on every call: 86-119 tokens (13 S0);
   - rounds spent re-reading what compaction evicted (02A §3.8).
3. **Capacity first, prediction second, at both levels.**
   - Harness level: keeping every earlier task's reads beats graph-selected sets (85.6 vs 36 %, 07A §5.5), and graph
     order is no better than LRU (08A §4b).
   - Engine level: a plain LRU DRAM tier removes almost all eviction recompute (18.3 → 0 %, 13 §4.2), and hints add
     nothing while it holds the working set.
   - Prediction helps only once capacity binds, and then modestly: LFU/GDSF 19-21 % vs LRU 16 % at 8 KB (08B), hints
     ≤ ~5 % over DRAM (13 S0).
4. **Reuse is all-or-nothing on coarse units.**
   - A round disappears only when every read it would issue is held (08A §4d).
   - A prefix cache reuses nothing after the first changed token (05A §2.5.1, 05B §3B).
   - Finer, position-independent units turn partial residency into partial savings: byte ranges instead of whole files
     raise reducible rounds 36 → 91 % (08B G2b), and shifting the surviving KV saves 80 % of post-edit prefill (10A §4.2).
5. **The host is idle; HBM is the scarce resource (12).**

## Part 2 — What changes when the served application is an agent

### 2.1 Chat-style serving vs agent sessions (Table A)

**The chat column** states the design assumptions of chat-style serving: independent requests, or conversations
paced by a human. This is the regime that vLLM/SGLang defaults and ShareGPT-style benchmarks target. The repository
has no first-party chat baseline; E0 (§3.9) would add one.

**The agent column** is solo harness sessions under multi-user load (11/12/13), plus the single-session studies.
Published agent traces look the same, as summarised in the related-work notes:
- Continuum's SWE-bench traces: 10.9 turns and 70k tokens per program (`related_work/continuum_paper_reading.md` §1);
- production agent traffic: > 80 % KV reuse vs 54-62 % for chat (`related_work/serving_memory_balancing.md` §6.3).

| property | vanilla multi-user (chat) serving | agent harness, measured | consequence |
|---|---|---|---|
| unit of work | an independent request, or a human-paced turn | a session of dependent lead calls: a median 30 per task, p10-p90 4-325 (10 §4.1); up to 100 per turn in 11 (the call cap, often reached) | admit and schedule by session; the latency target is per turn, the sum of 5-100 calls |
| reuse distance | a human's think time: tens of seconds to minutes | after `read_file` / `edit_file`, a median 0.01 s before the next call; closed-loop turn end 2 s; full test suite p50 18.6 s; human think time median 30 s (13 S0, 11 §2.1) | the prefix comes back before LRU considers it cold. Idle windows are bimodal, and partly predictable from the tool |
| request shape | inputs and outputs of the same order: ShareGPT means 161 input / 338 output tokens per request (vLLM paper, Fig. 11) | prompts of 23.9k tokens, of which 3.4k are computed; ~95 output tokens per call (12's p2a workload, 14 README). File content is 73-86 % of a file-heavy teammate's prompt (02A §3.1, 05A §2.7) | ~5-7 GiB of KV per session on Qwen3-32B (256 KiB per token). KV demand scales with live sessions, not in-flight requests: ~10 running requests × 25k tokens fill the 258k-token pool (11 §3.4) |
| cost of a miss | re-prefilling a short history costs a few % of the answer's decode (§2.2, illustrative) | re-prefilling a 23.5k-token context takes 6.08 s on the RTX PRO 6000 (13 §3) and ~3.4 s on an H200 (derived, §2.2), against 2.0-4.5 s for the call's own ~95-token decode | a miss costs **1.4-1.7×** the call's own decode. Past the turning point this compounds into thrashing: hit 86 → 1-2 %, 74 % of GPU time on recompute, throughput ÷ 3, latency × 10-42 (11 §3.2-3.4) |
| eviction | LRU over unreferenced prefix blocks is adequate | LRU with optimistic admission evicts what returns next: 74-89 % of evicted tokens are lost **while the returning call waits in the queue**, not during the pause (13 S0). At N ≥ 12 even sub-second tool pauses lose part of the prefix (11 §3.4) | needs working-set admission (session gate: 3.5-3.8×) and cheaper misses (DRAM tier: 2.8-3×). TTL pins alone give 1.2× (11 §3.7) |
| client behaviour | appends; re-sends are short | the client rewrites its own prompt. Harness edits are 60-80 % of the tokens prefilled below the turning point, ~90 % of them from compaction at the window (11 §3.4, T7). The per-second timestamp alone gives 2 % hit at N = 8 (11 §3.6). The template re-render discards the previous reply's KV on 99.9 % of calls (13 S0). Re-sending and re-tokenising the whole history costs ~3.5 ms per 1k tokens (12 §3.2) | prefix-keyed caching misses even when memory is ample. It needs edit-aware, position-independent reuse and session-resident token state |
| prefill vs decode | interference comes from long user prompts | prefill is mostly re-prefill, interleaved with tiny decodes. Decodes stall behind co-scheduled chunks for 21-45 % of call time at N = 4-8 (13 S0). Batch-1 decode uses 5 % of the tensor cores and 70 % of HBM bandwidth (12 §3.2) | removing the recompute shrinks the stall at its source. P/D moves it to another GPU (14 measures that trade) |
| sharing between requests | system prompts | teammates hold the same files at different offsets: 53-80 % of teammate file bytes duplicated, 2-7.4 resident copies (02B §2.1, §2.6). Cross-task reuse: 1 % on SWE, 11-38 % on τ-bench (08B). One user spawns 1 + k sessions | address segments by content, not by prefix. KV demand multiplies per user (Table B) |
| background work | none | harness-internal model calls: turn-end memory extraction is 20-35 % of wall (05B §4, 09A); a compaction summary takes ~80 s on a thinking 27B (06 §C.4.4) | needs a priority class below the user's own calls. 09 made extraction async (H −93-99 %) |
| host | the CPU keeps the GPU fed | 1.8 of 12 cores used; 50-60 MB of DRAM per agent; PCIe ≤ 0.05 GB/s; NVLink < 5 % (12) | the lower tiers sit idle: ~250 GB of DRAM per GPU, 55 GB/s PCIe, 478 GB/s NVLink, NVMe at 4 GB/s read |

### 2.2 The cost of a miss, worked out (derived)

Agent session on Qwen3-32B:
- **Re-prefill:** 23.5k tokens. On an H200 the 32B prefills 8,192-token chunks in ~1.2 s (12 §3.3), so ~3.4 s. On the
  RTX PRO 6000, 6.08 s (13 §3).
- **The call's own work:** ~95 output tokens. At batch 1 that is 95 × 20.5 ms = 2.0 s on the H200 (12 §3.2), or
  95 × 47 ms = 4.5 s on the RTX (13 §3).
- **Ratio:** a miss costs 1.4-1.7× the call it delays, and above the turning point it comes back on almost every call
  (84-85 % of prompt tokens evicted, 11 §3.4).

A chat request, for comparison (illustrative):
- 161 prompt tokens and 338 output tokens (ShareGPT means), with, say, a 1-2k-token history.
- Re-prefill at ~7k tokens/s: 0.15-0.3 s. Decode: 338 × 20.5 ms = 6.9 s.
- So a miss costs 2-4 % of the call.

A ~50× larger miss penalty, paid at 10-1,000× shorter reuse distances: this is why LRU plus recompute-on-miss, which
works for chat, collapses for agents.

### 2.3 What agent teams add on top of solo agent users (Table B)

These are measured on single team runs, mostly on the hosted GLM endpoint, plus one self-hosted 28-teammate stress
trace. None ran under multi-team load on our vLLM; that is the gap E5 (§3.9) fills.

| property | solo agent user (11's setup) | agent team (lead + k teammates), measured | consequence for memory |
|---|---|---|---|
| sessions per user request | 1 | 1 + k concurrent sessions: lead + 3 in every 02/05 team run; up to 28 teammates, and 6 spawns in one response, in the stress run (01 §3.2) | KV demand per user × (1 + k). 11's turning point of 8-12 solo users becomes ~2-3 teams of four (inferred) |
| context growth | the harness compacts at the window | teammates never compact and keep their finished tasks. One teammate's task-start prompts grew 1.2k → 51.6k → 102k → 136k tokens; 35 % of teammate prompt tokens are finished-task history (02A §3.7). Contexts reach 82k tokens (02A §3.4) and the 262k limit, where six teammates died (01) | working sets grow without bound. The cheapest capacity win is releasing teammate history at task boundaries |
| shared content | own files only | the same files at different offsets: 53-80 % of teammate bytes duplicated, 2-5 resident copies (7.4 in the stress run), 45-54 % of teammate prompt tokens. SHA-256 sees only 0-3 % because paging windows differ (02B §2) | prefix caching cannot deduplicate this. It needs aligned placement (exact) or position-independent segments (approximate) |
| arrival pattern | one stream of calls | bursts: the whole board is created in one wave (07B §4.3); 6 spawns in one response (01); teammates open the shared file within ~30 s of each other (02B §2.4) | correlated prefill bursts → spikes in capacity demand and in decode stalls |
| activity pattern | active through a turn, idle during think time | the lead is busy 87-98 % of wall time; teammates overlap only partly, then sit idle on the message bus with their contexts held (05A §2.8, 02A §3.7). Successors wait on predecessors along an exact DAG, built before work starts (45/45 edges, 07B §4.1) | idle teammates can be demoted. The board predicts **when** a session runs next, though not **which bytes** it reads (07A §5.4, 08A §4b) |
| content handoff | — | handing a successor its predecessors' bytes: 5.17 → 3.00 rounds (07A §5.7). Only complete coverage pays (a/b = 69×, 08A §4c). On real traces: 12.7-38 % of reading rounds on τ-bench vs 2-4 % on SWE (08B) | the KV form of inheritance is segment reuse across agents |
| orchestration | — | 10-11 lead rounds per run; teams are slower than solo on small tasks (1.3-1.8× on GLM, solo/team 0.68-0.82 on Qwen). The lead hits the cache ~17 % of the time, teammates ~90 %. Every team-event turn triggers a memory extraction (05A §2.8, 05B §3G, 02A §3.1) | the lead is a mutating, latency-critical session; teammates are append-only and cache-friendly. Treat them differently |

### 2.4 Where the GPU's time goes

Qwen3-32B on one H200, 11's sessions:

| regime | GPU time | binding memory problem |
|---|---|---|
| N ≤ 8, below the turning point | decode 56 %, prefill 41 % (60-80 % of it re-prefill after harness edits), eviction recompute 2 % (11 §3.3-3.4). Decodes stall 21-45 % of call time (13 S0) | **addressing:** prefix-keyed reuse breaks on edits and re-renders |
| N ≥ 12, above the turning point | eviction recompute 65-74 %, fresh prefill 14-19 %, decode 10-16 % (11 §3.3) | **capacity:** the concurrent working set exceeds the HBM pool |

### 2.5 What single-session profiling said, and what multi-user load showed

| single-session conclusion | under multi-user load | why it changed |
|---|---|---|
| "KV and prefix reuse buy capacity, not latency": prefill is 1-6 % of a call (05B §4, 09A) | losing reuse costs two thirds of throughput and 10-42× latency (11 §3.2) | at N = 1 the GPU has spare time; from N = 4 it is busy 94-100 % (12 §3.3), so every re-prefill takes someone's step |
| "P/D disaggregation does not pay: the prefill share is too small" (09A) | decodes stall behind *other* sessions' prefill for 21-45 % of call time, the largest of 13's four ceilings (13 S0) | a single session never shares a step with someone else's prefill |
| "Tools are < 1 % of latency" (06) | still true for latency. But at N ≥ 12 even sub-second tool pauses lose part of the prefix (11 §3.4) | under memory pressure a pause is an eviction window, not a latency term |
| "The harness's context preparation is free": ~10 ms per round (05A §2.3) | its edits cost the server: 60-80 % of prefill below the turning point comes from them, and the per-second timestamp alone drops throughput to 935 lead calls/h at N = 8 (11 §3.6) | the cost lands on the server's prefix cache, not on the harness's CPU |

### 2.6 The bottleneck, named

**The bottleneck is KV-cache residency of the sessions' working sets.** It shows up three ways:
- as recompute: eviction recompute above the turning point, edit and re-render recompute below it;
- as the decode stalls that recompute causes;
- as smaller decode batches, since HBM capacity bounds how many sessions can decode together (tensor activity is 5 %
  at batch 1 and 24 % at batch 11, 12 §3.2).

Tools, the CPU, host DRAM and the links are not the bottleneck (06, 12). Teams make it worse in three ways (Table B):
- more concurrent sessions per user;
- unbounded teammate contexts;
- shared content that prefix caching cannot deduplicate.

So the turning point arrives at fewer users (inferred; E5 measures it).

## Part 3 — Tiered-memory management: a ranked brainstorm

### 3.1 The tiers and what they cost

Qwen3-32B, 256 KiB of KV per token; a 23.5k-token context is ~5.7 GiB.

| tier | capacity | time to bring back a 23.5k-token context | source |
|---|---|---|---|
| HBM, prefix hit | H200 TP=1 pool 258k tokens (~10 sessions); RTX 89k (~3.8) | 0.15 s | 11 header, 13 §3 |
| a peer GPU's HBM (NVLink) | whatever a lender has free | ~22 ms at the measured 281.6 GB/s peer copy (derived) | 12 §3.1 |
| host DRAM (PCIe Gen5) | ~250 GB per GPU (2 TB per 8-GPU node), ≈ 950k tokens ≈ 40 sessions | 0.31 s idle (37 GB/s with 32 KiB pages); ~0.6 s per load under load (20.5k tokens at 8.9 GB/s) | 12 §3.1, 13 §3, 13 S0 |
| local NVMe | 2 × 960 GB on the RTX nodes | ~1.5-2 s to read (3-4 GB/s); writes run at only 1-1.5 GB/s | 13 §3 |
| recompute | — | 6.08 s on the RTX; ~3.4 s on an H200 (derived) | 13 §3, §2.2 |

### 3.2 The rule: pick the tier by reuse distance

| reuse distance | where the KV should sit |
|---|---|
| tool gaps (milliseconds) | HBM |
| closed-loop turn ends and long tools (2-40 s) | DRAM, restored in 0.3-0.6 s |
| human think time and idle sessions (30 s to hours) | DRAM or NVMe |

Never recompute what any tier still holds, and keep each restore shorter than the reuse distance it has to beat.
Today vLLM holds 56 GiB of valid prefixes at N = 1 that it reports as free (12 §3.6), and throws them away at N ≥ 12.

### 3.3 Failure class → mechanism

| failure | measured size | memory-management mechanism |
|---|---|---|
| capacity misses (thrashing) | 65-74 % of GPU time above the turning point (11) | working-set admission + DRAM/NVMe tiers + compressed KV |
| addressing misses | 60-80 % of prefill below the turning point (edits, 11); 86-119 reply tokens per call (13); 45-54 % of teammate prompts duplicated on a shared corpus (02B) | session-resident tokens + edit-aware, position-independent KV + content-addressed segments |
| interference | decodes stalled 21-45 % of call time at N = 4-8 (13 S0) | shrink the recompute (the two rows above); P/D as a memory layout |
| over-residency | tool output is 73-86 % of a file-heavy prompt (02A, 05A); 56 GiB of idle prefixes at N = 1 (12) | demote cold segments; sparse residency (exploratory) |

### 3.4 Ranked ideas

★ marks an idea that is new beyond what 11, 13 and 14 have built or planned.

| # | idea | evidence and ceiling | status | first test | risk |
|---|---|---|---|---|---|
| I1 | **Working-set admission across tiers.** Size a session gate to HBM: K = usable HBM blocks ÷ the median session's blocks. A returning request whose prefix is cached at arrival pins those blocks until it is admitted. Waiting sessions keep their KV in DRAM, so re-admission is a 0.3-0.6 s restore, not a 3-6 s recompute | gate 3.5-3.8× live, turn p95 5.5-6.3× lower (11 §3.7). 74-89 % of evictions happen while the call is queued (13 S0) | gate done (11); ★ tier-aware gate + pin at arrival (pin listed in 13 §6, not run) | gate × DRAM size × N = 16 / 32 by replay (E1) | low. Prior work: Continuum (TTL), ThunderAgent, Autellix; our replay has TTL pins at 1.2× |
| I2 | **A DRAM tier sized to the fleet's working set, plus NVMe write-back for idle sessions** | offload gave 2.8-3× at N = 16 (11, 12) but 1.1× at N = 32, where the working set (640-800k tokens) outgrew the 128 GiB tier (~520k tokens, 11 §3.7). Restores are 15-20× cheaper than recompute (13 §3). Write-through to NVMe would lag the 32B's ~1.3 GB/s peak KV output, so write back only for idle sessions | partly measured | N = 32 with 256-512 GiB; RTX 32B with 16 GiB + NVMe (13's arms A2/A4) | low. Prior: Mooncake, HiCache, Dynamo KVBM, LMCache (not on hybrid models, 09) |
| I3 | ★ **Edit-aware KV: patch, don't re-prefill.** 13's D1 client already sends `messages[k:]` with base k, so the server knows the first edited message. Then: drop the replaced span's KV (to DRAM, I5); prefill the new text; re-rotate the surviving suffix by the position shift; recompute the first 32-128 tokens of each reused run (EPIC); prefill the tail | edits are 60-80 % of prefill below the turning point (11 §3.4). Shift prefills 20 % of what prefix caching re-prefills after the edit, at agent-level parity; TTFT 121 ms vs 765 ms for prefix caching (10A §4.2, 10B §B3.6). Ceiling: ≈ 50-63 % of all prefill below the turning point, ≈ 20 % of GPU time at N = 8 (derived), plus a proportional cut in decode stalls | new. The pieces exist: 10's `splice_connector.py` (offline), 13's D1 protocol (live) | port into the live path; check exactness against 10's emulation; A/B below the turning point (E2) | medium. GQA only: MLA is not repaired by EPIC, and hybrid models cannot shift recurrent state. Staleness compounds weakly (04 §3.2), so re-anchor at each summary. Prior: Leyline (related_work/compaction §4) |
| I4 | ★ **Shared segments across agents.** (a) Exact: the harness puts shared file content at the same position in every teammate's prompt (a "pinboard" with aligned windows), so plain prefix caching deduplicates it. (b) Approximate: a content-addressed segment cache with range keys, reused at any offset via shift + repair | team duplication is 45-54 % of teammate prompts, in 2-7.4 copies (02B). Cross-task reuse 1-38 % (08B). Range keys beat whole-file keys 2.5× in reducible rounds (08B G2b) | new; the pinboard was proposed in 02 and never built | E5: the pinboard arm first, then the segment cache | (a) low; (b) medium-high (CacheBlend-type deviation across different prefixes). Prior: KVCOMM, DroidSpeak, ReCache |
| I5 | ★ **Re-attach evicted spans on re-read.** Keep what compaction drops in DRAM. When the harness re-reads the same bytes, splice the old KV back in (shifted + repaired) instead of prefilling it | 28-47 % of 32B rounds re-fetch content the session already had, 94-95 % of them after compaction evicted it (ad hoc). The content lives in the evicted block's own KV, not downstream (04 §3.4) | new | count re-read hits in a DRAM segment pool on 11/12's captures, offline (E4) | medium. Saves the re-read's prefill, not its round, unless the harness re-attaches proactively |
| I6 | ★ **Compressed KV per tier:** FP8 in HBM; FP8 or INT4 for the DRAM and NVMe copies | FP8 halves bytes per token: the H200 pool goes from 258k to ~516k tokens. 11's half-pool test shows capacity sets the turning point, so it should move from 8-12 to ~16-24 sessions (inferred). Compressed copies also restore 2-4× faster | new | an FP8-KV turning-point sweep plus 10's next-round fidelity metrics (E3) | low-medium (quality) |
| I7 | **Turn-boundary hibernation + prefetch.** At a turn end or a long tool, move the session DRAM → NVMe; prefetch it on the predicted return (13's tool-duration predictor) | think time median 30 s. Hints add ≤ ~5 % while DRAM holds the working set (13 S0, §4.2), so this pays only once sessions ≫ DRAM | planned in 13 (A2/A4), not run | think-time cells with constrained DRAM | low |
| I8 | **P/D as a memory layout, coupled with recompute removal** | B3 ceiling 21-45 % (13 S0). 1p1d is bound by its one prefill GPU (14 S0). The prefill that stalls decodes is mostly recompute, which I1-I3 remove at the source: that would shrink both the stall and P/D's prefill bottleneck | 14 running | after 14: a 2×2 of P/D × I3 | medium |
| I9 | ★ **Sparse residency of cold tool-output spans.** Keep the hot KV in HBM; fetch the top-k blocks of old tool results from DRAM at attention time | tool output is 73-86 % of a file-heavy prompt. Evicted content is not carried downstream (04 §3.4, 10A §4.5), which suggests old spans draw little attention, but this is unmeasured | new, exploratory | attention mass on old tool-result spans in captured prompts, offline (E4) | high (approximate attention changes outputs). Prior: Quest, InfiniGen, ShadowKV |
| I10 | ★ **Speculative decoding from the prompt** (n-gram or suffix speculation) | 62-78 % of reply 4-grams already appear in the prompt, and 87-97 % of tool arguments (ad hoc). Decode is HBM-bound: tensor 5 % at batch 1 (12 §3.2) | new | replay with vLLM's n-gram speculator | low. Prior: prompt-lookup decoding, SuffixDecoding |
| I11 | **Session-resident token state (13's D1)** as the protocol under I1-I5 | front end −77 %, reply re-prefill −90 %, exact on 794/794 live calls (13 §4.1) | done (smoke) | 13's S3 repeats | low |
| I12 | A peer GPU's HBM as the spill tier (13's B2) | ≤ 3 % of call time on this workload (13 S0) | planned in 13 | only if restores become frequent | low value |
| I13 | ★ **Team-aware admission + session-level prefetch from the task board.** Count the gate in KV bytes across a user's agents. Demote teammates idle on the bus. When a predecessor completes, prefetch the successor's session and its predecessors' segments | the board is exact and complete before work starts (07B §4.1-4.3); teammates hold their contexts while idle (02A §3.7); the board predicts when a session runs, not which bytes it reads (07A, 08A) | new | E5 | medium |
| I14 | **Release teammate history at task boundaries:** a harness reset, a respawn on low overlap, or server-side demotion of finished-task history | 35 % of teammate prompt tokens are finished-task history; task-start prompts reach 136k tokens; six teammates died at 262k (01, 02A §3.7) | proposed in 02 and related_work, never measured | E5, with and without the release | low |

### 3.5 Why this order

- I1 and I2 recover the largest measured loss (about ×3) and are nearly built.
- I3 is the largest new lever below the turning point and the most novel, though it is GQA-only.
- I6 is the cheapest capacity lever.
- I4, I13 and I14 matter once teams share a server.
- I9 is the high-risk, high-reward bet.
- I10 is cheap, and it stacks with any of the others.

**The exact mechanisms carry over to every architecture:** I1, I2, I7, I11, I4a, I14, and I6 if its quality holds.
**The approximate ones need GQA attention:** I3, I4b, I5.

### 3.6 One design: an agent-aware KV manager

- **Session table** (from D1): exact token ids, message offsets, which tier holds each segment, and state (in a call /
  in a tool with a predicted end / thinking / ended).
- **Segment index:** content hash → locations, for I3-I5.
- **Admission:** an HBM-sized active set, counted in KV bytes across all of a user's agents. Pin at arrival. Swap at
  turn and task boundaries. Prefetch successors from the task board (I13).
- **Placement:**
  - write-through to DRAM (vLLM's native offload already does this) and write-back to NVMe for idle sessions;
  - replace by predicted reuse distance, keeping sessions about to return;
  - LFU for segments shared across sessions.
- **Edit handler:** I3.
- **Compression** for the lower tiers (I6).
- **Optional:** P/D (I8), sparse residency (I9), speculation (I10).

### 3.7 What it should buy

Upper bounds from the measured ceilings:

| regime | expected gain | basis |
|---|---|---|
| above the turning point | ≈ 3-3.8× throughput, turn p95 5-6× lower | I1, I2; each measured alone |
| below the turning point | ≈ −50 % prefill tokens, ≈ +20-25 % throughput if GPU-bound, fewer decode stalls | I3; estimate |
| where the turning point sits | ~2× the users before the turning point | I6; inferred |
| per call | front end −77 %, previous-reply re-prefill −90 % | I11; measured |
| teams | shared content held once instead of 2-5 times; teammate working sets bounded by the task, not the session | I4a, I14; E5 measures the turning-point shift |

### 3.8 Measured negatives to avoid

- TTL pins alone (1.2×), session priority alone (1.3-1.4×), smaller prefill chunks (1.0×) (11 §3.7).
- Demoting at turn ends without protecting the sessions about to return: −23 % (13 §4.2).
- DAG-directed retention (07A, 08A).
- Splicing to save rounds: it saves FLOPs only (04).
- A summary handoff instead of the bytes (07A).
- LMCache on hybrid models: the engine dies (09B).
- Cost labels for the model (06B).

### 3.9 First experiments

Each experiment goes in a new folder (the standing rule). Use a 1-GPU RTX PRO 6000 unless the cell needs two GPUs, and
QoS standard. All reuse existing machinery: 11's mu_sweep / mu_replay, 12's sampler, 13's `tm_*` hooks, 10's splice
connector and 14's router.

| # | experiment | tests | GPU |
|---|---|---|---|
| E0 | **Vanilla baseline.** Replay a ShareGPT-style multi-turn chat workload and 11's agent sessions on the same server at matched N. Measure the turning point, hit rate and miss penalty: Table A becomes first-party | Part 2 | 1 RTX |
| E1 | gate × DRAM size × N, by replay of recorded sessions | I1, I2 | 1 RTX (RTX baselines only) |
| E2 | wire 10's splice connector into D1's delta path; verify exactness against 10's emulation; live A/B below the turning point | I3 | 1 RTX |
| E3 | FP8-KV turning-point sweep plus next-round fidelity | I6 | 1 RTX |
| E4 | offline, on existing captures: re-derive the ad-hoc numbers; count re-read hits; measure attention mass on old tool spans; n-gram acceptance | I5, I9, I10 | CPU (+1 GPU for attention) |
| E5 | **multi-team load test.** N teams (lead + 3) on shared and on disjoint corpora against one server. Measure the turning point in teams and in agents, duplicated KV and burst stalls. Arms: baseline, task-boundary release (I14), pinboard (I4a), team-aware gate (I13) | Table B, I4, I13, I14 | 1 RTX |

### 3.10 Open questions and threats to validity

- **One model under load.** Every multi-user number comes from one dense GQA model (Qwen3-32B, 256 KiB per token), a
  40,960-token window, thinking off, vLLM 0.29. The turning point moves with KV per token, pool size and context length (11 §5).
  - Hybrid models (05B: 784-token blocks, no LMCache) and MLA models (10B: EPIC does not repair) keep only the exact
    mechanisms.
- **Teams under load are untested.** Table B rests on single team runs; E5 fills the gap.
- **Synthetic conditions.** Think time is synthetic (lognormal, median 30 s), and the tools are local and light.
  Slower tools would expose prefixes to eviction below the turning point (11 §5).
- **Unpublished numbers.** Three figures come from an ad-hoc analysis not yet in any README (listed below): the
  re-fetch share, the copy-heavy replies, and cross-session tool results. E4 re-derives them.
- **The chat column** rests on design assumptions and one published dataset until E0 runs.
- **I3's ceiling** assumes recomputed tokens cost the same per token wherever they sit. Suffix tokens late in a long
  context cost more (attention), so the true share may be higher.

## Part 4 — Deeper brainstorm: what the raw logs add (2026-10-06)

Parts 1-3 rest on the topic READMEs. This part goes back to the raw data. Two offline, read-only analyzers in this
folder re-read the scheduler logs and request captures of 11's twelve live cells and of 12's p1a and o1 cells:
- `kv_lifecycle.py` → `data/tables/kv_lifecycle.md` and `kv_lifecycle_12.md`;
- `content_reuse.py` → `data/tables/content_reuse.md`.

Both run in seconds on the login node. All numbers are Qwen3-32B on one H200 unless a cell says otherwise.

### 4.1 Six findings the READMEs did not have

| # | finding | numbers (cells) |
|---|---|---|
| F1 | **Edits are frequent, early and large.** A harness edit (compaction at the window, or a memory or timestamp section) rewrites the session's prompt well before its end | 15-23 % of calls edit. The edit point sits a median 23-38 % into the previous prompt. Each edit kills 15-21k cached tokens of the old version and re-prefills 12-16k tokens after the edit point. Below the turning point, edit calls produce 61-75 % of all computed tokens; the per-second timestamp makes 99.8 % of calls edits (n08ts) |
| F2 | **Below the turning point, most of the eviction stream is garbage, and garbage holds a fifth of the pool.** | 73-96 % of evicted blocks are dead: superseded versions and re-rendered replies (n01-n08, gates, p1a). Dead blocks are produced at 14-74 % of the pool per minute (111 % with the timestamp). At N = 8, or under the gate, at least 19-24 % of the whole pool is dead at any moment, 63-69 % of the cached-free space (pool walks every ~2 s). **But evicting garbage first does not move the turning point** (`pool_sim.py`, see I15) |
| F3 | **Above the turning point, prefixes are lost by sessions that are waiting, not pausing.** | 68-81 % of live evictions hit a chain with a request already in flight (n12-n32, n08kvh). After a 0-1 s gap, 89-95 % of the ideal prefix is still cached when the request arrives, but only 5-58 % when it is admitted |
| F4 | **KV is a decode-bandwidth cost, not only a capacity cost.** With 11's step model, each context token costs 77.3 ns per decode step per sequence: 256 KiB at ~3.4 TB/s, matching 12's measured 70 % HBM bandwidth | Reading KV takes 9 % of decode-only step time at N = 1, 21 % at N = 4, 38 % at N = 8, 41-42 % under the gate, and 47-49 % at N ≥ 12 (~10 sequences, ~220k context tokens per step) |
| F5 | **Most tool-result content that enters a prompt has been fetched before.** | Lead calls' tool results: 66-76 % are byte-identical to something the same session had already fetched (74-83 % at line level). Another 6-25 % were fetched earlier by another session on the same codebase (5-22 % at line level), rising with N: 5-7 % at N = 1, 22-25 % at N = 32. Below the turning point, tool results are ~40 % of all computed tokens (38-42 %) |
| F6 | **With a DRAM tier, edits are again the largest prefill cost.** | o1 (N = 16, 128 GiB CPU tier): evicted recompute falls to 5.4 % of computed tokens, edit calls produce 65.6 %, and ~59 % is shift-reusable (estimate). The tier also holds garbage: o1 produced dead blocks at 68 % of the GPU pool per minute, all of it written through to DRAM |

### 4.2 Where the prefill goes at N = 8, and what that says about agents

This decomposition combines kv_lifecycle (n08) with content_reuse (n08) and 10's per-event saving. The categories
overlap slightly, so treat the shares as approximate.

| share of all computed (prefilled) tokens | what it is | does the server already have its KV somewhere? |
|---|---|---|
| ~5.5 % | eviction recompute (the prefix was cached, then evicted) | yes, until it was evicted |
| ~50 % (52 % from 10's 79.6 % saving × the 65.5 % edit share; 61 % by the suffix estimate) | surviving context re-prefilled after a harness edit, at a shifted position | yes: the previous version's blocks, now dead |
| ~41 % | tool results entering the prompt: 68 % byte-identical re-reads by the same session, 19 % fetched by another session on the same codebase, **13 % new** (line level: 4.6 % new) | mostly yes, at another position or in another session |
| ~3 % | the previous reply re-rendered (86-119 tokens per call, 13 S0) | yes: generated by this session |

**About 95 % of what the server prefills for these agent sessions is content whose KV it had already computed**
(~5 % new at whole-result level, ~2 % at line level). The content is the same; only its position or preceding
context has changed.

In chat serving, prefill is mostly new user text. That is the deepest form of the agent-vs-vanilla difference this
repository can measure. Below the turning point the agent server's prefill problem is not compute and not capacity: it is
**addressing**. The KV exists, but prefix caching cannot find it, because it is keyed by exact position and exact
prefix.

**Caveats:**
- These are 11's settings: a 40,960-token window with compaction at the window, the 32B, and EXPLAIN/MODIFY tasks
  on ten codebases.
- A larger window means fewer edits and fewer re-reads: 02A's limit of 50k → 200k chars took one task from 53
  re-reads to 0.
- Tool-result tokens are estimated at 4.61 chars per token.

### 4.3 New ideas (I15-I24)

★ = new beyond Parts 1-3.

| # | idea | mechanism | evidence and ceiling | first test | risk |
|---|---|---|---|---|---|
| I15 | ★ **Edit-aware garbage collection** | When a session's next request arrives, every cached block of its previous version past the common prefix, plus the old reply blocks, can never be hit again. Move those blocks to the front of the eviction queue (13's demote mechanism already does this for other reasons), and invalidate their DRAM copies | F2: ≥ 19-24 % of the pool is dead at the operating point, and 73-96 % of evictions are dead. **Trace simulation (E6, done, `data/tables/pool_sim.md`):** the LRU arm reproduces the logged eviction recompute roughly (n08: 3.4 % vs 5.5 % logged; the simulation has no feedback, so it understates thrash). Dead-first then removes: 99 % of eviction recompute at n08 (3.4 → 0.0 % of computed); 69 % on a 75 % pool (7.9 → 2.6 %); but only 8 % on a 60 % pool, 14 % at n12 and 0-6 % in the gated cells. **So it smooths the approach to the turning point and does not move it**: past the turning point, idle prefixes are pushed out by other sessions' live data, not by garbage. **In the DRAM tier it matters a lot** (`data/tables/pool_sim_dram.md`). Write-through fills the tier with dead versions. On the N = 32 trace, a 128 GiB tier serves 54 % of the ideal prefix and leaves 0.62× of GPU-only prefill, the same weak gain 11's replay measured (1.1×). Invalidating dead versions raises that to 86 % served and 0.32× prefill, close to what a 256 GiB tier gives (99-100 %, 0.18×). At N = 16, a 64 GiB tier goes from 91 % to 96 %. Edit-aware GC makes an undersized DRAM tier act about twice as large. **Calibration** (`pool_sim_dram.md`): on the real offload replays' own traces the simulator matches N = 16 (98.0 vs 98.5 % served) but overstates N = 32 (61.6 vs 39.6 %). Garbage is ≈ 45 % of the real shortfall at N = 32, capacity ≈ 18 %, and ≈ 36 % comes from something the simulator does not model | E6 done (offline, GPU and DRAM arms); next, a live arm via TMScheduler and `tm_policy` | low: exact, any architecture |
| I16 | ★ **Position-free paged KV** | Store keys before RoPE and rotate them inside the attention kernel at their current position, as StreamingLLM does and as FlashInfer's RoPE-in-kernel mode allows. An edit becomes a block-table renumbering: no re-rotation pass, no new block versions, no garbage. Identical content at different offsets shares physical blocks; contextual drift is repaired by EPIC head recompute | Makes I3 (edits, ~50 % of prefill below the turning point) and I17 (re-reads and cross-session reads, ~35 % of prefill) cheap. The extra rotation is a few ALU ops per element in a memory-bound decode kernel (F4: decode is bandwidth-limited) | E7: a decode microbenchmark of RoPE-in-kernel vs pre-rotated keys; then a live shift + EPIC arm | medium: kernel work; GQA (MLA already keeps a small, separate RoPE part) |
| I17 | ★ **Content-addressed session and repository KV store** ("reads hit KV") | Key tool-result segments by content hash, at line or range granularity. When a session re-reads a file, or another session reads it, splice the stored KV in (shift + EPIC) instead of prefilling. Keep the store in DRAM/NVMe; keep a repository's hot files warm for every session working on it | F5: re-reads are 66-76 % of tool-result content (≈ 28-32 % of all prefill at N = 8); cross-session reads are 6-25 % and grow with N (≈ 2-8 % of prefill below the turning point and under the gate). Range keys beat whole-file keys 2.5× on reducible rounds (08B) | E8: replay 10-style next-round fidelity when a re-read file's KV comes from its earlier copy | medium-high (contextual drift across different prefixes; HijackKV-type poisoning across sessions) |
| I18 | ★ **Bandwidth-aware context** | Treat every resident context token as a per-step decode cost: FP8 KV, dropping dead spans from attention (old tool outputs, finished-task history, old thinking) via cheap edits (I3/I16), and sparse residency (I9) for cold spans | F4: KV reads are 38-49 % of decode step time at N ≥ 8. FP8 alone ≈ −19-24 % decode step time at the turning point (half the KV term; estimate) on top of ~2× capacity (I6). Each 1k tokens trimmed per sequence saves 77 µs per step | E3 extended: FP8 turning-point sweep with TPOT | low-medium |
| I19 | ★ **Context ballooning (two-level working-set control)** | The harness's context window and the server's KV pool are two levels of one hierarchy, and both thrash when their working set does not fit (Denning). The server inflates or deflates each session's context budget with load, as a hypervisor's balloon driver does for VM memory. I3/I16 make the shrink edits cheap. Rule: never deflate a session below its task's working set; queue it at the gate instead | Both thrashes appear in the same runs: context level, 66-76 % of tool content re-fetched (F5) under a 40,960-token window (02A §3.8: 30-120 rounds below the working set vs 6-7 above); KV level, 84-85 % re-prefill above the turning point (11). The throughput-optimal budget per load is unknown | E9: 11's workload at limits {20k, 40k, 128k} × N; measure rounds, re-read share, hit rate, throughput | medium (quality: 03 lost answer quality with aggressive snips) |
| I20 | ★ **Fork-spawned teammates** (copy-on-write prefix) | Spawn a teammate by forking the lead's context (its exact prefix plus the task), so vLLM shares the lead's blocks physically and the teammate inherits the lead's reads with no prefill | Handing successors their predecessors' bytes cuts rounds 5.17 → 3.00 (07A). In Claude Code "a fork inherits the caller's cache" (03's compaction ladder). It removes 2-7× duplication of lead-read files (02B). Cost: longer teammate contexts → more decode KV reads (F4) | E5 arm | low-medium (teammates see the lead's history: a behaviour change) |
| I21 | ★ **Policy for reasoning traces** | Keep old thinking blocks only in DRAM, or drop them with a cheap edit (I3), instead of choosing between preserving them (a larger KV) and dropping them (a prefix break every turn) | 05B attributes Qwen's 8-51 % larger prompts, in characters, to the template re-rendering every earlier thinking block (`preserve_thinking`); thinking is 33-68 % of output (05B, 09) | a thinking-on variant of E2 | medium |
| I22 | ★ **Pin at arrival + continuation through short pauses** | Reference a returning request's cached prefix the moment it arrives, until it is admitted. Optionally let a session keep its slot through sub-second tool pauses, so it is not re-queued | F3: 89-95 % of the prefix is cached at arrival and 5-58 % at admission. 96-98 % of evicted tokens follow sub-second tools (13 S0). TTL pins on pauses gave only 1.2× (11) because they protect the pause, not the queue. **Simulated** (`data/tables/pool_sim_pin.md`): at n12, pinning from arrival to admission halves eviction recompute (44.9 → 22.2 % of computed; hits 88.9 → 96.1 %). At N = 16-32 most pins cannot be honoured (1.0-4.4M allocations find every block held): pinning converts eviction into admission pressure, so it works only together with the gate | in E1 | low (must sit with the gate to avoid deadlock) |
| I23 | ★ **An edit protocol: the harness declares edits, the server applies them** | Extend D1's delta (base k) with operations ("replace message i with placeholder P", "drop thinking of turn t"). The server applies them to the KV: drops spans, prefills the placeholders, shifts or renumbers the rest, repairs heads, and keeps dropped spans in DRAM under an ID for addressable recall | Makes I3, I15, I16 and I21 exact about what changed, with no prefix diffing. D1 already found the harness edits messages in place (13 §5, item 10), so a protocol beats inference | spec + unit tests on 13's recorded sessions | low |
| I24 | ★ **Server-owned context (virtual memory for agents)** | Push I23 to the end: the harness appends only, and the server keeps the attended window within the model's budget by masking or demoting old spans (page table: span → attended / DRAM / dropped). Re-attach on an explicit recall, or when attention-guided retrieval (I9) shows a span is needed | Unifies I3, I9, I15-I17, I19 and I23. 04 §4 and 10A §4.5: the evicted content lives in its own KV, and re-attaching that KV restores recall (oracle rows) where downstream KV does not. ARC/LCM/VISTA show addressable recall works at the text level (related_work/compaction §3 F6); nobody does it at the KV level (§4) | design study after E6-E8 | high (scope) |

### 4.4 Three research theses

**T1 — Agent sessions are working-set programs.**
- Claim: session-level working-set admission (I1, I22) and cheap lower tiers (I2, I7) keep the 86 % hit rate at 2-4×
  today's turning point. Edit-aware GC (I15) supports this as hygiene.
- Why credible: each piece is measured or exact:
  - gate 3.5-3.8×;
  - DRAM 2.8-3× while it fits;
  - dead-first eviction removes 69-99 % of eviction recompute while the pool has slack (simulated);
  - invalidating dead versions makes an undersized DRAM tier act about twice as large: at N = 32, 128 GiB serves
    54 → 86 % of the ideal prefix (simulated; the simulator is calibrated at N = 16 and optimistic at N = 32).
- Evaluate on: 11's mu_sweep and mu_replay, 12's sampler, 13's tm hooks.

**T2 — Edit-tolerant, position-free KV for self-editing prompts.**
- Claim: position-free paged KV (I16) plus edit operations (I23), EPIC repair and a content-addressed store (I17)
  remove most of the ~95 % of prefill that is known content at a new position.
- Why credible: 10 already measured agent-level parity for single edits on GQA models.
- Open: compounding over many edits, cross-session drift, MLA and hybrid models.

**T3 — Context ballooning.**
- Claim: co-managing the model's window and the GPU's KV pool (I19, with I14 and I20 for teams) finds a
  throughput-optimal context budget per load, without the quality loss of blind snipping (03).
- Why credible: both thrashes are visible in the same runs (F5 and 11).

### 4.5 Revised top five, and experiments to add

**Top five, combining Parts 3 and 4:**
1. **I1 + I2 + I15 in DRAM:** admission, a DRAM tier sized to the fleet, and dead-version invalidation in that tier.
   - Simulated on the N = 32 trace: 128 GiB serves 54 % of the ideal prefix, 86 % with invalidation, and 256 GiB
     serves 99-100 %. The simulator is exact at N = 16 but 22 points optimistic at N = 32 (calibration in
     `pool_sim_dram.md`).
   - The gate alone gave 3.5-3.8× live.
2. **I3 / I16:** edit-aware, position-free KV. Targets ~50 % of prefill below the turning point, and ~59 % even with a DRAM
   tier.
3. **I17:** content-addressed session and repository store. Re-reads ≈ 28-32 % of prefill; cross-session reads grow
   with N.
4. **I6 / I18:** FP8 KV. ~2× capacity and ≈ −20 % decode step time at the turning point.
5. **I15 on the GPU:** exact and nearly free, but it only removes eviction recompute while the pool has slack. It does
   not move the turning point (simulated).

I4, I13, I14 and I20 matter as soon as teams share a server (E5).

**Experiments to add:**

| # | experiment | tests | GPU |
|---|---|---|---|
| E6 | **done for I15** (`pool_sim.py`): GPU arms, LRU vs dead-first at 100 / 75 / 60 % of the pool (`pool_sim.md`); DRAM arms, write-through tiers of 0-256 GiB with and without dead-version invalidation on n16/n32 (`pool_sim_dram.md`). Next: pin at arrival (I22), feedback (a step-time model), restore bandwidth; then a live arm via TMScheduler and `tm_policy` | I15, I22, I2 | CPU, then 1 RTX |
| E7 | decode microbenchmark of RoPE-in-kernel vs pre-rotated keys at 4-32k contexts and batch 1-16; then a live shift + EPIC arm | I16, I3 | 1 RTX |
| E8 | splice a re-read file's KV from its earlier copy (shift + EPIC) on recorded re-reads; score next-round fidelity with 10's metrics | I17 | 1 RTX |
| E9 | 11's workload at harness limits {20k, 40k, 128k} tokens × N | I19 | 1 RTX |

### 4.6 Dead-first eviction (I15) against vLLM 0.29's own policy

**What vLLM 0.29 does.** Read from the installed source: `v1/core/block_pool.py`, `single_type_kv_cache_manager.py`,
`kv_cache_utils.py` and `v1/kv_offload/cpu/policies/`.

- **GPU prefix cache:** one free queue.
  - Allocation takes blocks from the head (`popleft_n`); a popped block loses its cached hash.
  - A finished request's blocks are freed tail first (`free_blocks(reversed(...))`).
  - A freed block with no hash (it can never be matched) goes to the front.
  - A hashed block goes to the back, so hashed blocks leave in the order they were freed: LRU by release time.
  - A prefix hit takes blocks out of the queue (`touch`).
- **CPU offload tier:** LRU by default (ARC is built in; an out-of-tree policy loads through
  `cache_policy_module_path`).
  - A block counts as used when it is stored, when a load completes, and on a touch.
  - Blocks are stored as they are computed: write-through.

**What dead-first adds: one fact vLLM cannot know.** That fact is which hashed blocks a session will never hit again.
- When a session's next request arrives, its block hashes are compared with the session's previous prompt (their common
  prefix, as 11's scheduler already logs).
- The previous version's blocks past that prefix, and the previous reply's blocks, are dead. They move to the front of
  the free queue (`remove` + `prepend_n`) and are dropped from the CPU tier.
- **The idea is vLLM's own:** freed blocks with no hash already go first, as "never match". Dead-first extends that
  rule to hashed blocks proven dead by the session's history.
- **Within one request, vLLM already approximates it:** tail-first freeing puts a session's dead suffix ahead of its
  live head.
- **What vLLM cannot do is order across sessions:** session A's dead version, freed a second ago, stays behind
  session B's live prefix freed 30 s ago while B was thinking, so LRU evicts B first.
- **Where the dead blocks come from:** 95-96 % are superseded versions after harness edits, 3.6-4.5 % are re-rendered
  replies and 0.1 % is the template tail (n08, s3gate16, n16).

**Results** (trace simulation, recorded timing, no feedback; `data/tables/pool_sim_sweep.md`, `pool_sim.md`,
`pool_sim_dram.md`):

| setting | vLLM LRU (simulated) | dead-first (simulated) |
|---|---|---|
| n08 trace, full pool | eviction recompute 3.4 % of computed | 0.0 % |
| n08, 80 / 75 / 70 / 65 / 60 % of the pool | 6.3 / 7.9 / 13.4 / 23.3 / 33.3 % | 0.8 / 2.6 / 8.6 / 20.2 / 31.5 % |
| n04 trace, 50 / 40 / 35 % of the pool | 3.7 / 5.3 / 9.6 % | 0.0 / 0.7 / 5.3 % |
| pool fraction at which eviction recompute reaches 5 % of computed | n08: 89 %; n04: 41 % | n08: 73 %; n04: 35 % (≈ 15-18 % less memory) |
| same at 10 % | n08: 73 % | n08: 69 % (≈ 5 % less) |
| gated cells (s3gate16 / 32), full pool | 11.3 / 9.4 % | 11.2 / 9.4 % |
| CPU tier, N = 32 trace, 128 GiB | 54 % of the ideal prefix served | 86 % (the simulator is 22 points optimistic at N = 32, exact at N = 16) |
| CPU tier, N = 16 trace, 64 GiB | 91 % served | 96 % |

**Reading:**
- On the GPU, dead-first never did worse than LRU in any simulated setting.
- It keeps eviction recompute near zero down to a ~15-18 % smaller pool.
- But its absolute saving peaks at ~5.5 % of all prefill (n08 at 75-80 % of the pool), and it barely moves the cliff:
  past the turning point, live working sets fill the pool.
- In a write-through CPU tier it matters more, because every edit stores a 12-16k-token new version that becomes
  garbage at the next edit.

**Why the two policies so often give the same result: a survival-time view.** A paused session keeps its cached
prefix only if it returns before the eviction front reaches its blocks.
- **Under vLLM's LRU** every block in the free queue ages at the same rate, so survival ≈ free-queue blocks ÷
  evictions per second.
- **Under dead-first** garbage goes first, so live blocks are evicted only for the demand garbage cannot cover:
  survival ≈ free-queue blocks ÷ (evictions − garbage produced) per second.

With 11's measured rates (`kv_lifecycle.md` §2, §5; time away = gap + wait before admission, for returns after a
pause ≥ 15 s):

| cell | free queue (blocks) | evicted / garbage produced per min | survival, LRU | survival, dead-first | time away, p50 / p90 | outcome |
|---|---:|---|---:|---:|---|---|
| n08 | 5,696 | 10,768 / 9,379 | ~32 s | ~4 min | 30 / 83 s | dead-first keeps the think-pause prefixes LRU loses (simulated 3.4 → 0 %) |
| s3gate16 | 4,760 | 13,003 / 10,422 | ~22 s | ~110 s | 215 / 407 s | parked at the gate longer than either survival time: same (11.3 vs 11.2 %) |
| n16 | 1,259 | 23,609 / 3,299 | ~3 s | ~4 s | 67 / 181 s | thrashing: no room to protect anything: same |

With no memory pressure (N ≤ 4) nothing is evicted, and the order does not matter either. Dead-first pays only where
it lifts survival past the time sessions are away. That points to the DRAM tier as its complement: the tier gives the
minutes of survival that sessions parked at the gate need. The estimates are first-order: steady state, FIFO aging,
and all garbage evicted before any live block under dead-first.

**A worked example** (12-block pool, 16-token blocks, all blocks empty at start). "No-hash" means a partly filled last
block: it has fewer than 16 tokens, so vLLM never hashed it and no request can ever match it.

```
B's call: prompt B0 B1 B2 + 4 tokens; its 20-token reply fills that block (Br, hashed) and leaves b· (8 tokens, no hash)
  B finishes (blocks freed tail first: b· to the front, the hashed ones to the back):
  queue  b· e6 e7 e8 e9 e10 e11 e12 | Br B2 B1 B0
A's call: prompt A0-A3 + 7 tokens; reply → Ar, Ar2 (hashed), a· (no hash); A takes b· e6..e11
  A finishes:  a· e12 | Br B2 B1 B0 | Ar2 Ar A3 A2 A1 A0       (B was freed first, so B is evicted first)
B's user thinks for 30 s. A's next call arrives after a compaction edit at block 1: the new prompt is
  A0 A1' A2' A3' + tail, so A1-A3 and Ar, Ar2 are dead and A0 is a hit. A needs 4 new blocks.
  vLLM LRU:    takes a· e12 Br B2  -> B's live block B2 is gone, and A's next calls take B1, B0
  dead-first:  moves Ar2 Ar A3 A2 A1 to the front, takes Ar2 Ar A3 A2 -> B0 B1 B2 survive; B returns to a hit
If B had finished after A, or A were the only session, LRU would already evict A's dead blocks first: same result.
```

**Cost and safety:**
- It needs the session id (`X-Session-ID` → `Request.session_id`, which 11 already uses) and the previous prompt's
  block hashes per session.
- The work is O(dead blocks) per arrival: ~6 blocks for an append, ~1,000 for an edit. That is milliseconds against
  20-1,200 ms engine steps.
- It is exact: it only reorders eviction and never frees a referenced block, so outputs cannot change. A wrong "dead"
  mark costs at most a recompute.
- Two guards:
  - skip blocks another session's latest prompt still contains (the system-prompt root, a forked teammate);
  - derive deadness from the actual next prompt, not from assumptions. With 13's D1 the reply is reused, so it is
    no longer dead.
- **What "dead" means.** vLLM itself keeps every computed block as a branch of a prefix tree: blocks are keyed by
  chained hashes, and an old branch stays cached until LRU reaches it. "Dead" is dead-first's label for the **old**
  branch of one session: its previous prompt past the divergence point, plus its previous reply. The new prompt's
  unmatched part is new work: it is prefilled and cached as a new branch.
- **Is the label right for this harness?** In 11's logs a follow-up call almost never matches anything beyond the
  common prefix with its own previous prompt:
  - 0.34-0.56 % of follow-up calls, about 2 blocks each (n01a, n08, s3gate16, n16);
  - 2.7 % with memory on (n08mem), < 1 % of computed tokens.

  The harness does not go back to an old version of its history. In other workloads it can (a chat "regenerate", an
  edited earlier message, parallel samples, retries), which is why vLLM, with no notion of a session, cannot call any
  block dead.
- **Why not free them outright?** In vLLM, freeing a cached block means dropping its hash and putting it at the front
  of the free queue: the same placement dead-first gives. Keeping the hash until the block is reused is weakly
  better, because 0.3-0.6 % of calls do match an older branch. Either way, freeing returns no reserved memory:
  - vLLM pre-allocates the whole KV pool (63 GiB on the H200 for Qwen3-32B);
  - cached-but-idle blocks are already counted as free (12 §3.6), and any new request can take them at once.

  What freeing changes is which data survives. The dead share (≥ 19-24 % of the pool at the operating point, ≈ 12-15
  GiB) becomes room for other sessions' live prefixes. The only sense in which it saves memory is the same hit rate
  on a ~15-18 % smaller pool, below the turning point.
- **Better than freeing: reuse half of it** (`edit_anatomy.py` → `data/tables/edit_anatomy.md`; message-level diff of
  11's captures). Of what an edit discards under prefix caching, the old suffix plus the reply:
  - 39-46 % is truly gone: the tool results compaction replaced with placeholders, ~6.2-7.7k tokens per edit;
  - 54-60 % is content the new prompt still contains at shifted positions, ~8.4-9.6k tokens per edit;
  - ~1 % is the previous reply.

  Freeing the surviving part means recomputing it. The best policy is to free the replaced span and the old reply
  first, and to keep and shift the surviving blocks (I3, I16), which 10 measured as skipping ~80 % of the post-edit
  prefill at agent-level parity.

## Numbers that are derived, ad hoc or external

**Derived here (arithmetic on cited numbers):**

| number | how it was derived |
|---|---|
| ~3.4 s to re-prefill 23.5k tokens on an H200 | 8,192-token chunks at ~1.2 s each (12 §3.3) |
| the 1.4-1.7× miss penalty, and the 2-4 % chat comparison | §2.2 |
| ~5-7 GiB of KV per session | 20-30k tokens × 256 KiB |
| ~950k tokens / ~40 sessions in ~250 GB of DRAM | ~250 GB ÷ 256 KiB per token |
| ~22 ms for a peer-HBM restore | 5.7 GiB at 281.6 GB/s |
| I3's ≈ 50-63 % of prefill and ≈ 20 % of GPU time at N = 8 | 11 §3.4: harness edits are 8 of 13 computed points at N = 8 and 11 of 14 at N = 1, times 0.8 (10A §4.2); prefill is 41 % of GPU at N = 8 (11 §3.3) |
| I6's ~2× turning point | half the bytes per token; 11 §3.5 |
| "~2-3 teams of four" at the turning point | 8-12 solo sessions ÷ 4 |

**Ad hoc:** a 2026-10-03 analysis of 12's request captures, recorded in project notes, not in a README.
- 28 % (N = 1) and 47 % (N = 8) of 32B rounds only re-fetch content the session already had; 94-95 % of them after
  compaction evicted the original.
- 62-78 % of reply 4-grams already appear in the prompt (tool arguments 87-97 %, free text 29-53 %).
- 6-9 % of recomputed tool results were first seen in another session.

The related figure "~70 % of the 32B's recomputed tokens come from micro-compaction" is published, in 13's
`PROPOSAL.md` §1 (424 of 2,260 calls at N = 8). It agrees with 11 §3.4, where harness edits are 8 of the 13 computed
points at N = 8.

**External:**
- ShareGPT request means, 161.31 input and 337.99 output tokens: Kwon et al., *Efficient Memory Management for Large
  Language Model Serving with PagedAttention*, SOSP 2023, arXiv:2309.06180, Fig. 11. Checked 2026-10-05.
- The agent-traffic figures in §2.1 (KV reuse > 80 % vs 54-62 % for chat; Continuum's 10.9 turns and 70k tokens per
  program) are quoted from `related_work/` and were not re-checked here.

## Sources

| part | read from |
|---|---|
| Part 1, rows 1-21 | each row's folder README, in the sections named in the row; `13_tiered_memory/RESULTS.md` and `data/tables/S0_ceilings.md`; `14_pd_crossover/README.md` and `PLAN.md` |
| Table A | 10 §4.1; 11 §2.1, §3.2-§3.7; 12 §3.2, §3.3, §3.6; 13 RESULTS §1, §3, §5; 02A §3.1; 05A §2.7; 05B §3-§4; 06 §C.4.4; 09A; vLLM paper Fig. 11; `related_work/continuum_paper_reading.md` §1 and `serving_memory_balancing.md` §6.3 |
| Table B | 01 §3.2, §4.2-§5 and cleanup notes; 02A §3.1, §3.4, §3.7; 02B §2.1-§2.6; 05A §2.8; 05B §3G; 07A §5.4-§5.7; 07B §4.1-§4.3; 08A §4c; 08B §3 |
| §2.4-§2.6 | 11 §3.3-§3.6; 12 §3.2-§3.3; 13 S0; 05A §2.3; 05B §4; 09A |
| Part 3 | 10A §4.2, §4.5, §6; 10B §B3.6, §B4; 11 §3.7, §5; 12 §3.1, §3.5-§3.6; 13 PROPOSAL §1-§2, RESULTS §1-§6; 14 README; 04 §3.2-§4; 08B §3; `related_work/compaction_kv_reread_related_work.md` §3-§4 and `serving_memory_balancing.md` §6 |
| cross-cutting lessons | 07A §5.5; 08A §4b-§4d; 08B §3 (G2b, Metric 3); 13 §4.2; 05A §2.5.1; 05B §3B; 10A §4.2 |

The weekly syntheses (`weekly_progress/091626/tiered_memory_conclusion.md`, `weekly_progress/092326/conclusion.md`)
were read for context. Where they disagree with a topic README, the README's corrected numbers are used: LFU vs LRU,
graph-ordered retention, and the cause of the preemptions.
