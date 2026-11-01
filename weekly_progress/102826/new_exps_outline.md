# New experiments since Oct 21: slide outline

Built 2026-10-27; revised 2026-10-28 (old slide 6 removed). Deck: `new_exps_slides.html` in this folder (14 slides).

**Sources:**
- `research/18_harness_wait/` (README.md, FOLLOWUPS.md);
- `research/19_efficiency_signals/` (README.md, prereg.md; the main run is complete);
- the private repo `Friedrichqi/dgxspark-profiling` at 35b3781 (exp01 microbenchmarks and follow-ups, exp02 vLLM).

**Rules used:**
- 1–2 bullets per slide, at most 20 words each;
- every slide carries a diagram or chart;
- every setup name gets a few words saying what it means;
- no "arm" wording;
- no comparisons between models run on different GPU counts.

## Slide titles

1. Three new studies since October 21
2. Can an agent relieve a crowded server by pausing itself?
3. Pausing helps only when the harness decides
4. The model waits in loops, and each wait is another model call
5. Prompts lose their cache while queued; only capping active sessions fixes it
6. Does a much stronger model use the server's signals?
7. The stronger model ignores the signals; only the gate helps
8. On the Spark, CPU and GPU share one memory, through two GPU paths
9. GPU traffic slows the CPU long before memory saturates
10. A decoding vLLM server fills the memory from its first user
11. On the Spark, give spare memory to the GPU's KV pool, not a CPU tier
12. Capping vLLM's GPU cores protects CPU services that share the memory
13. Takeaways
14. Next steps

## Slides

**1. Three new studies since October 21**
- Topics 18–19: when many agents share one GPU server, who should decide to pause them?
- DGX Spark: how its CPU and GPU share one memory, in microbenchmarks and under vLLM.
- *Visual:* three study cards (model, hardware, scale) over an Oct 21 → 27 date ribbon.

**2. Can an agent relieve a crowded server by pausing itself?** (topic 18)
- Many agent sessions share one server; once KV memory fills, evicted prompts are recomputed.
- Three deciders tried: the model with a wait tool, a fixed harness rule, and the server's gate.
- *Visual:* system diagram (harness loop → vLLM queue → full KV pool), with the three pause points numbered. Below it,
  the two status lines with a real example of each:
  - **stock status:** vLLM's standard load numbers, the same for every session;
  - **pool-share status:** this session's place in line for the KV pool.

**3. Pausing helps only when the harness decides**
- With a live status and a wait tool, the model reached 0.7–0.9× the no-wait throughput.
- A fixed harness rule on the same status reached 1.8–2.8×; the server's gate 3.2–3.8×.
- *Visual:* bars of work calls/h × no wait at 16 and 32 users, each setup with a one-line gloss:
  - **model · stock status / pool-share status:** the model reads that status and may call wait;
  - **harness · backoff rule:** holds while KV is ≥ 92% full, re-checking after 2 → 30 s;
  - **harness · share rule:** holds while the sessions ahead plus itself need > 90% of the pool;
  - **+ turn-start holds:** also holds before a turn's first call (1 run);
  - **gate:** the server admits at most 8 sessions at once.

**4. The model waits in loops, and each wait is another model call**
- 81–99% of its wait-only rounds came in runs of ten or more, barely tracking the status.
- Each wait call first re-prefills the evicted context: 22–32% of GPU time at 16+ users.
- *Visual:*
  - Two turn strips: the model's waits are each a model call; the harness's hold makes none.
  - GPU-time bars for the four model-decided runs, each with the median wait the model asked for: 5 s and 2 s at
    16 users, 10 s and 10 s at 32.

**5. Prompts lose their cache while queued; only capping active sessions fixes it** (follow-ups)
- Calls arrive with 87–94% of their reusable prefix cached, but keep only 1–5% after queueing.
- Simulated: reordering requests gives 0.9–1.7×; capping active sessions, as the gate does, gives 3.7×.
- *Visual:*
  - Left: cached prefix at arrival vs admission (prefix = the start of the prompt the server already computed).
    Below, "what survives the queue": the share of the cached prefix left at admission by time queued, 0.98 / 0.39 /
    0.11 / 0.02; 91% of calls waited ≥ 4 s.
  - Right: simulated server policies, each with what it does, against the session gate (3.65 / 3.68).

**6. Does a much stronger model use the server's signals?** (topic 19)
- MiniMax-M2.7 resolves 64% of SWE-bench Verified in our harness, against Qwen3-32B's 9%.
- Two setups: a crowding line plus wait, or a cost receipt plus three pacing tools.
- *Visual:*
  - pass@1 bars on three benchmarks.
  - Two example prompt tails: Setup 1, with `[server]` and `wait`; Setup 2, with `[cost]` and `wait` /
    `wait_for_capacity` / `server_status`. Each tool has a gloss.

**7. The stronger model ignores the signals; only the gate helps** (main run; MiniMax only)
- Main run: 3 pacing calls in 21,081 lead calls; its reasoning mentions the signals only twice.
- Only the server's gate helped: 1.42–1.44× completed turns per hour; the two model setups reached 0.92–1.04×.
- *Visual:*
  - Turns/h × no waiting for Setup 1, Setup 2 and the gate, with a one-line key.
  - Pre-registered decisions (rules frozen before the run), stated as questions:
    - does a model setup win back half the gate's gain? No: −18% to +9% of it;
    - does the toolbox beat plain wait? No clear difference;
    - does it hurt at 8 users? No: 0.94×.
  - Tool-use tiles: 3 / 0 / 0.
  - What the gate changed:
    - cache hit rate 13.9 → 81.7% and 1.4 → 82.3%;
    - evicted recompute 37.0 → 4.8% and 40.8 → 4.4%;
    - slowest 5% of turns 559 → 253 s and 1,690 → 451 s.

**8. On the Spark, CPU and GPU share one memory, through two GPU paths**
- One 128 GB memory at 273 GB/s peak serves 20 Arm cores and a Blackwell GPU.
- GPU-allocated memory streams at ~255 GB/s; CPU-allocated memory caps near 165–170 GB/s, shared with the CPU.
- *Visual:* architecture diagram. CPU clusters and CPU-allocated GPU traffic share a coherent path; GPU-allocated
  memory takes a direct path. It notes the ~8 GiB page-table reach.

**9. GPU traffic slows the CPU long before memory saturates**
- Each GB/s of GPU traffic costs the CPU 0.29% of its bandwidth; p99.9 latency rises 4–5× at 100 GB/s.
- At saturation the trade is concave, not constant-sum; a three-parameter model predicts it with 1–4% mean error.
- *Visual:*
  - Left: CPU bandwidth kept by a streaming job on 1 or 19 cores, as the GPU's traffic rises.
  - Right: GPU vs CPU bandwidth with the GPU flat out, against a constant-sum line (defined in the footer).

**10. A decoding vLLM server fills the memory from its first user**
- Each decode step streams all 51 GB of weights: 233 GB/s, 85% of peak, from one user on.
- CPU readers beside it keep 27–43% of their bandwidth; a 12-core CPU writer cuts decode to 37–43%.
- *Visual:*
  - Stacked DRAM-traffic columns by users.
  - "Who keeps what" range bars. Each workload has a gloss: stream readers on 4 and 12 cores, vector search,
    text prep, arithmetic only, 12 cores reading flat out, 12 cores writing flat out.

**11. On the Spark, give spare memory to the GPU's KV pool, not a CPU tier**
- On the Spark, CPU and GPU memory are the same chips, so a CPU KV tier copies DRAM to DRAM.
- Given to the GPU's KV pool instead, the same 12 GiB ran 63 requests, not 51, and ended preemptions.
- *Visual:*
  - Two memory maps: A is 50.1 GiB of weights + a 24 GiB KV pool + a 12 GiB CPU tier; B is 50.1 GiB + a 36 GiB
    pool.
  - Bars comparing A and B: running 51 vs 63, preemptions/min 3.7 vs 0, median TTFT 453 vs 281 s. Chat load, 96
    users, prefix caching on in both.

**12. Capping vLLM's GPU cores protects CPU services that share the memory**
- While vLLM decodes, CPU memory accesses wait behind GPU traffic; a CPU lookup service's p99 rises 45×.
- Capping vLLM at 14 of 48 SMs cuts its memory pressure: that p99 falls 6–24×; decode slows 8–18%.
- *Visual:*
  - Mechanism diagram: all 48 SMs push 223–232 GB/s into the shared memory controllers, so the CPU's request waits
    in a long queue. 14 SMs push 182–204 GB/s, giving a shorter queue.
  - The lookup service's p99 on a log scale: 89 → 16, 244 → 27, 778 → 32 µs at 1, 16 and 64 users.
  - The price: decode 18 / 8 / 14% slower; prefill first token 2.8 → 6.9 s.

**13. Takeaways**
- Pausing agents relieves a crowded server only when the harness or server decides; neither model used the signals
  well.
- On the Spark, decoding saturates the shared memory; CPU work pays in bandwidth and tail latency.
- *Visual:* a ✓ / ✗ board. Each model sits in its own group, labeled with its hardware, and each × is against its
  own no-wait run.

**14. Next steps** (25–27)
- Test turn-start holds at 32 users and pointers for repeated file reads: six pre-registered runs, 22.3 GPU-h.
- Spark: test grouping GPU lookups by memory region in real embedding and paged-KV kernels; write an allocator guide.
- *Visual:* a numbered roadmap with a status chip per step.
