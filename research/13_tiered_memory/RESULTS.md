# 13 · Results of stages S0, S1 and S2 (tiered memory for the harness: B1, B2, B3, D1)

> **Scope:** the user's goal (2026-10-04): "Complete all S0, S1, and S2 experiments, and come up with quantitative
> results back to me. Remember to conclude all the phenomena and results you observed in a Markdown file."
> This file collects them. The plan is in [PROPOSAL.md](PROPOSAL.md).
>
> **Stages:**
> - **S0:** offline ceilings from 11/12's data, no GPU.
> - **S1:** all code plus tests (CPU).
> - **S2:** one RTX PRO 6000 (the cluster puts every 1-GPU job there):
>   - S2a: node probe, tier calibration, vLLM capacity and rates;
>   - S2b: live D1 and B1 smoke runs on the real harness.
>
> **Jobs:**
> - S2a: 117716, alphagpu51, 10:08-10:20 EDT.
> - S2b: 117737 (alphagpu51, 10:18-12:02), 117827 / 117864 / 117867 (alphagpu54, 11:06-12:38). All 2026-10-04,
>   all 1-GPU RTX PRO 6000.
> - CPU jobs: 117680-117721 (S0), 117762 / 117828 (S1 replay).
>
> No 2-GPU job ran. **Commits:** none (uncommitted).

**Contents** — Summary · §1 S0 ceilings · §2 S1 implementation and verification · §3 S2a node and tiers · §4 S2b
live smoke runs · §5 Phenomena observed · §6 What changes for S3-S5 · §7 Data and reproduction

## Summary

| stage | result |
|---|---|
| **S0** ceilings (11/12 data) | **B3** has by far the largest ceiling: decodes stalled behind co-scheduled prefill take 21-45 % of 32B call time at N = 4-8. **B1:** a plain DRAM tier already removes most eviction recompute (64-73 % → 1.8 % of GPU at N = 16); 74-89 % of evictions happen while a call is queued, not during the tool pause. **D1:** ≤ 9 % of 8B call time. **B2:** ≤ ~3 %. |
| **S1** code + tests | All four ideas implemented in 13 only, plus 1- and 2-GPU orchestration. 10 unit tests pass; 11 / 12 suites unchanged (26 passed). D1 replayed on 24 recorded sessions: 2,345 D1 calls (297 after compaction edits), all identical to the full re-render; −33 % per-call prefill on the 8B. Fake end-to-end runs: 1 GPU, with the real harness; 2 GPUs: dp2, P/D and tp2 through the proxy. |
| **S2a** RTX PRO 6000 | PCIe Gen5 56.6 GB/s; vLLM's block copy 37-56 GB/s by page size; local NVMe 4 GB/s read, 1-1.5 GB/s write. 32B: 88,848-token KV pool, prefill 3.5-5.3k tok/s, decode 47 ms/token; a 23.5k-token context restores from DRAM in 0.31 s vs 6.08 s to recompute (19.5×). 8B: 498k-token pool, 7.4×. |
| **S2b** D1 live | **Exact:** 794 / 794 verify calls identical to vLLM's full render, after fixing two bugs the harness exposed. **Front end** −75-77 % (244 → 55 ms per call, 12.5 % → 3.4 % of call time). **Reply re-prefill** −90 % (84 → 8 tokens per call). **Lead calls/h** +5 to +29 % (noisy). |
| **S2b** B1 live | **DRAM tier:** 1.65× lead calls per hour vs GPU only (32B, N = 4); recompute after eviction 18.3 % → 0 %. **Hints:** the first version lost 23 %: it demoted sessions at turn ends that came straight back, and its DRAM order evicted the same sessions' copies, so 255k tokens were recomputed. Fixed, hints are neutral on throughput, as S0 predicted with ample DRAM. [corrected 2026-10-05: an earlier version blamed priority-scheduling preemptions, which the fixed arm had too] |


## 1. S0 — what each idea can win at most (offline, 11/12 data)

`tm_ceilings.py` read 13 scheduler logs: 24 levels and about 160k calls, all on H200. Full tables:
[`data/tables/S0_ceilings.md`](data/tables/S0_ceilings.md). Every number is an upper bound: it assumes the idea
removes its target completely.

| idea | ceiling | where |
|---|---|---|
| **B3** prefill/decode split | Decodes that share an engine step with a prefill chunk wait for the whole chunk. **32B: 21-24 % of call time at N = 4, 35-45 % at N = 8**; 29 % at N = 16 with the CPU tier, 52-61 % without. TP=2: 35 % (N = 8), 61 % (N = 24). 8B: 3 % (N = 4), 13-23 % (N = 16-32) | the largest of the four, from N = 4 |
| **B1** tiered KV + hints | Without a CPU tier, recompute after eviction is 64-73 % of GPU time at N ≥ 12 (71-85 % of prefill tokens). A plain DRAM tier (12's o1, N = 16) leaves 4.4 % of prefill tokens (1.8 % of GPU), plus 3.2 % of call time waiting for loads | hints add ≤ ~5 % over a DRAM tier unless the working set outgrows DRAM |
| **D1** session-resident tokens | The front end that grows with the context: **9.0 % of 8B call time at N = 1**, 4.8-6.7 % at N = 4, 1.7-3.2 % at N = 16-32; 32B 2.7 % at N = 1, ≤ 1.8 % above. Previous-reply re-prefill: 86-119 tokens per call, never reused today; 10-19 % of the 8B's prefill tokens but ≤ 2.5 % of GPU | small, fast models at low load |
| **B2** peer HBM | o1: 83 % of calls load from DRAM, 20.5k tokens each, 0.60 s per load = **3.2 % of call time**, at 34k tokens/s = 8.9 GB/s | ≤ ~3 % of call time on this workload |

**Phenomena found in S0:**
- **Most eviction losses happen in the queue, not during the pause.**
  - At N ≥ 12, 74-89 % of evicted tokens were still cached when the call arrived (`hit_now`) and were evicted
    while it waited for admission. Only 10-26 % were lost during the pause.
  - 96-98 % follow sub-second tool calls.
  - So "demote at tool start" can only act on the few long windows. Queue-time protection (a gate, pins that last
    until admission) and a DRAM tier matter more.
- **LIGHT sessions' own tools return in 10-80 ms** (median window 0.01 s after read_file / edit_file). Only heavy
  tools and think time leave long idle windows:

  | dominant call of the reply | windows | p50 | p90 |
  |---|---|---|---|
  | `bash:pytest/suite` (full test suite) | 207 | 18.6 s | 20.3 s |
  | `bash:find/wide` (find over site-packages) | 203 | 5.4 s | 36.3 s |
  | `bash:pytest` (sandbox tests) | 1,881 | 0.79 s | 0.89 s |
  | turn end | 3,005 | 2.0 s (closed loop) | 83 s (11's think time) |

- **`bash:grep/wide` is bimodal** (p50 0.01 s, p99 91 s): page-cache state decides, and no static feature of the
  command predicts it. So B1 also demotes once a session has been away longer than θ.
- **The previous reply's KV is never reused today.** In 99.9 % of append-only 32B calls the reusable prefix ends
  exactly 4 tokens before the previous prompt's end. Qwen3's template drops the generation prompt's empty think
  block when it re-renders history.
- **At load, the measured front end mostly waits for the running engine step.**
  - The front end (client send → scheduler arrival) grows from 70-90 ms at N = 1 to 650-870 ms at 32B N ≥ 12.
    The SDK + HTTP + parse part stays at ~45 ms; the growth is the wait for the running engine step (up to
    ~1.2 s on the 32B), which D1 cannot remove.
  - The clean per-token front-end cost comes from N = 1 cells only: 4.07 ms per 1k tokens (32B), 4.14 ms (8B).
- **Two data traps.**
  - 06's census marks failing pytest runs and no-match greps as `error`. Filtering on `ok` silently dropped every
    test-suite run.
  - Decode-step interference must be charged to every request decoding in the step, because each one waits for
    the whole step.

## 2. S1 — implementation and verification (CPU)

All code is new in this folder. Code from 11 and 12 is imported or wrapped at runtime, and no other research
directory changed.

| file | what it is | how it hooks in |
|---|---|---|
| `tm_server_ext.py` | D1 server side and B1's API-server half: per-chain token store, delta render, verify mode, server-inferred hints, prefetch shadow requests, `/tm/hint`, `/tm/stats` | vLLM `--middleware`; patches `AnthropicServingMessages.create_messages`, `_reused_prompt_token_ids` (vLLM's decode-side token-reuse path, which skips templating and tokenisation) and `AsyncLLM.generate` (a tee) |
| `tm_client.py`, `tm_boot.py`, `bin/tmpython` | D1 client: common prefix with what the server holds; send `messages[k:]`; 409 → resend in full; `session_end` hints | mu_sweep `--python`; wraps `Messages.create` inside 11's capture layer |
| `tm_scheduler.py` | B1 scheduler: hint socket (ZMQ), demote / protect / timeout demotion, auto-sized gate, `tm*` records | `--scheduler-cls`, on top of 12's HWScheduler and 11's MUScheduler |
| `tm_policy.py` | `SessionAwareLRU` for the DRAM tier (ended → away → LRU) | `cache_policy_module_path` |
| `tm_peer_spec.py` | B2 `PeerHBMOffloadingSpec`: the offload tier on the peer GPU | `spec_name` + `spec_module_path` |
| `pd_proxy.py` | B2 / B3 proxy: session-affine (weighted), 1P1D with kv_transfer_params, passthrough | in front of every 2-GPU arm |
| `tm_calib.py`, `tm_vllm_probe.py` | tier calibration (PCIe, vLLM's block-copy kernel, flash, peer); a live server's capacity, prefill, decode and restore rates | S2a / S4 |
| `tm_workloads.py`, `tm_pipeline.sh` / `tm1.sbatch`, `tm2_pipeline.sh` / `tm2.sbatch`, `tm_probe.sbatch`, `tm_cpu.sbatch` | cells (S2 smoke; S4 calibration; S5 B2 / B3 arms), 1- and 2-GPU orchestration | 11's `MU_EXTRA_CONDITIONS` hook, 12's sampler |
| `tm_ceilings.py`, `tm_predict.py` → `tool_durations.json`, `tm_analyze.py`, `tm_common.py`, `tm_fake_vllm.py`, `test_tm.py` | S0, the predictor, S2 analysis, shared helpers, a D1-aware fake server, tests | — |

**Verification:**
- **`pytest research/13_tiered_memory/test_tm.py`:** 10 tests pass; the slow pipeline test is opt-in (11 in all). They cover:
  - tool keys and predictor lookup; hermes tool-call parsing;
  - the client's prefix logic with SDK objects vs dicts;
  - demotion order on vLLM's real free-block queue (tail first, shared prefix blocks kept);
  - `SessionAwareLRU` eviction order;
  - pd_proxy affine (3:1) and P/D flows against fake backends;
  - the peer spec loading through vLLM's factory;
  - **D1 on recorded sessions:** 3 sessions of 12's p1b replayed through the server's own delta render and
    bookkeeping, each call compared with a full re-render; no non-assistant block differs;
  - **regression for the S2 bug:** an edit right after a user message.
- **D1 replay at scale** (`tm_analyze.py d1replay`, CPU jobs 117762 / 117828; table
  [`data/tables/S1_d1_replay.md`](data/tables/S1_d1_replay.md)):
  - 24 recorded sessions (8 each from 12's p1a and p1b and 11's n01a): 2,383 lead calls and 2,345 D1 calls, 297 of
    them after a history edit (truncation at k);
  - **all 2,345 identical to the full re-render** outside assistant turns and at every turn boundary
    (boundary-strict check, after the S2 fix below);
  - new prefill per call with a warm cache: 8B **216 → 145 tokens (−33 %)**; 32B 3,751 → 3,685 (−1.8 %) and
    3,939 → 3,888 (−1.3 %).
- **1-GPU fake end-to-end** (`tm_pipeline.sh tt` against `tm_fake_vllm.py`; real harness, 11's mu_sweep /
  mu_session, `bin/tmpython`):
  - all 3 levels completed;
  - verify level: 37 verify calls, **0 mismatches** between the fake's held history and the harness's full message
    list, so the client's k is right with the real harness;
  - D1 level: 16 delta calls sending 1 message out of 14-18; no tools or system resent; no 409s.
- **2-GPU fake end-to-end** (`tm2_pipeline.sh tt2`: dp2, pd and tp2 layouts through pd_proxy):
  - dp2 (affine 1:1): 36 calls split 18 / 18 over two servers that each saw both GPUs, own first;
  - pd: 45 calls, each a prefill call (`do_remote_decode`, `max_tokens` 1) then a decode call carrying the returned
    kv_transfer_params;
  - tp2: 45 calls through one server;
  - every level completed (WINDOW_DONE).
- **The real vLLM patches import and install** in vLLM 0.29.0's API-server classes (login node), and the policy
  loads through vLLM's `CachePolicyFactory`.
- **11's and 12's test suites pass unchanged** (26 passed).

**Bugs the fake runs caught before any GPU time:**
- conditions read before they were built;
- a cell with no readable levels marked COMPLETE;
- `build` ignoring the test's data root;
- transformers' `apply_chat_template(tokenize=True)` now returning a dict.

## 3. S2a — the RTX PRO 6000 node and what moving KV costs there (job 117716)

Full tables: [`data/tables/S2_probe.md`](data/tables/S2_probe.md).

**The node (alphagpu51):**
- **GPU:** RTX PRO 6000 Blackwell Server Edition, 97,887 MiB, PCIe Gen5 x16, 600 W.
- **Host:** Intel Xeon 6960P, 144 CPUs, 1 TiB DRAM; `/dev/shm` 504 GiB.
- **Flash:** two local Micron 7450 NVMe drives (960 GB). `/` and `/tmp` are xfs on nvme0n1.

| path | measured |
|---|---|
| PCIe, one large pinned copy | **56.6 GB/s** H2D, 57.4 GB/s D2H; pageable ~10.5-11.8 GB/s |
| vLLM's block-copy kernel (`swap_blocks_batch`) | 32 KiB pages 36.6 / 38.2 GB/s · 64 KiB 43 · 256 KiB 51 · 4 MiB 56. A 23k-token 32B context (5.6 GiB in 32 KiB pages) takes **135 ms** |
| local NVMe, O_DIRECT, 4 MiB I/Os | sequential write 1.05-1.47 GB/s, read 4.0-4.2 GB/s; one file per 4 MiB block (fs-tier-like): write 0.92-1.01, read 2.9 GB/s |

| | Qwen3-32B | Qwen3-8B |
|---|---|---|
| weights | 61.0 GiB | 15.3 GiB |
| KV pool (0.92 utilisation) | 21.7 GiB = **88,848 tokens** (2.2 × the 40,960-token window) | 68.4 GiB = **498,208 tokens** (12.2×) |
| prefill (recompute) rate | 5.1-5.3k tok/s at 1-4k tokens → 3.9k at 23.5k → 3.5k at 32k | 13.7-18.5k tok/s → 15.1k at 23.5k → 13.4k at 32k |
| decode, batch 1 | **47.0 ms/token** | 11.8 ms/token |
| 23.5k-token context: recompute vs GPU hit vs restore from DRAM | 6.08 s vs 0.146 s vs **0.312 s** (19.5× faster than recompute) | 1.58 s vs 0.119 s vs **0.212 s** (7.4×) |
| DRAM restore above a GPU hit | 0.047 s (4k), 0.118 s (16k), 0.167 s (23.5k tokens) = 23-37 GB/s | 0.027 / 0.078 / 0.093 s = 22-37 GB/s |

**What S2a shows:**
- **On this GPU the 32B is capacity-starved.** Its KV pool holds about 3.8 harness sessions of 23k tokens. For
  B1 that is the point.
- **A DRAM restore is 15-20× cheaper than recomputing a 16-23k-token 32B context.** vLLM's offload path reaches
  ~37 GB/s, which is the 32 KiB-page rate of its own copy kernel: 65 % of the link. Larger pages would approach
  56 GB/s.
- **Local NVMe could hold a third tier.**
  - It reads at ~3-4 GB/s, 1.5-2 s for a 23k-token 32B context. That is still 3-4× faster than recomputing it
    (6.1 s).
  - Writes are slow (~1-1.5 GB/s): a write-through flash tier would lag the 32B's peak prefill of ~1.3 GB/s of
    KV.
- **12's o1 restored at 8.9 GB/s under load on H200**, a quarter of what the idle RTX path reaches. Contention
  and batching, not the link, set the in-run restore speed. B2 should measure under load.

## 4. S2b — live smoke runs on the real harness (jobs 117737, 117827, 117864, 117867)

Real harness, real tasks: 12's frozen session list, 11's mu_sweep / mu_session, closed loop with think time 0.
Each level ran a 10-12-minute window after a warm-up. Every server carried TMScheduler (observing unless an arm
switched a policy on) and the tm_server_ext middleware.

**Jobs:**
- 117737: cells s2d (D1) and s2b (B1).
- 117827: s2e, D1 after the first fix.
- 117864: s2f, the paired B1 rerun after its fixes.
- 117867: s2g, D1 after the second fix.
- All 1-GPU jobs on RTX PRO 6000s (alphagpu51 / 54), 2026-10-04 10:18-12:38 EDT.

**Tables:**
- [`data/tables/S2_d1_s2g.md`](data/tables/S2_d1_s2g.md) (final D1), plus `S2_d1_s2d.md` and `S2_d1_s2e.md`;
- [`data/tables/S2_b1_s2b.md`](data/tables/S2_b1_s2b.md) and [`S2_b1_s2f.md`](data/tables/S2_b1_s2f.md).

### 4.1 D1 on Qwen3-8B (KV holds every session, so this is a pure front-end comparison), N = 4 LIGHT sessions

| run | arm | calls | front end per call, mean (p50: SDK+HTTP+parse / template+tokenise+hand-off) | front end, share of call time | previous reply re-prefilled per call | call s, p50 / p95 | lead calls per h |
|---|---|---|---|---|---|---|---|
| s2d | baseline | 1,137 | 172 ms (81 / 89) | 7.5 % | 90.8 tok | 1.21 / 7.87 | 5,141 |
| s2d | D1 | 1,389 | **47 ms** (16 / 25) | 2.6 % | **8.4 tok** | 1.18 / 2.81 | 5,809 |
| s2e | baseline | 1,112 | 242 ms (126 / 119) | 10.3 % | 91.8 tok | 1.27 / 2.98 | 4,970 |
| s2e | D1 | 1,337 | **55 ms** (19 / 27) | 2.7 % | **8.3 tok** | 1.34 / 7.80 | 5,231 |
| **s2g (final)** | baseline | 1,198 | 244 ms (122 / 122) | 12.5 % | 84.3 tok | 1.51 / 3.76 | 5,733 |
| **s2g (final)** | D1 | 1,635 | **55 ms** (18 / 25) | **3.4 %** | **8.3 tok** | 1.39 / 2.78 | **7,383** |

**D1 exactness (verify mode).** The server served D1's ids and also rendered each full request the normal way,
then compared them block by block.

| run | client and server code | verify calls | identical outside assistant turns and at every turn boundary | what differed |
|---|---|---|---|---|
| s2d | first version | 855 | 748 (87.5 %) | the duplicated `\n` separator after an edit right after a user message (107 calls) |
| s2e | cut fixed | 765 | 713 (93.2 %) | stale tool results: the harness edits messages in place (52 calls) |
| **s2g** | **both fixed** | **794** | **794 (100 %)** | — |

**In s2g:**
- the engine's prompt always equalled D1's ids;
- 0 resyncs;
- client-side diff p50 2.1 ms (fingerprints), server delta render p50 4.6 ms;
- 16 of 1,634 D1-arm calls were full (init) requests, at each chain's first call.

**What D1 does, live:**
- **Front end:** it removes 75-77 % of the per-call front end (−117 to −189 ms; 2.6-3.4 % of call time is left).
- **Re-prefill:** it removes 90 % of the previous reply's re-prefill (84-92 → 8.3-8.4 tokens per call: only the
  last partial block remains).
- **Throughput:** lead calls/h rose in all three paired runs (+13 %, +5 %, +29 %). Call-time p50 / p95 move both
  ways between runs, so the throughput figure needs S3's repeats.
- **S0 ceiling:** S0 bounded D1 at 4.8-6.7 % of 8B call time at N = 4. These runs show 4.9-9.1 points of front-end
  share removed, the upper end because the RTX node's front end is slower (172-244 ms vs 70-100 ms on the H200 node).

### 4.2 B1 on Qwen3-32B (KV pool 88,848 tokens ≈ 3.8 sessions), N = 4 HEAVY mix (test suites, site-packages search, typical sessions)

| run | arm | lead calls per h | prompt tokens recomputed after eviction | loaded from DRAM | load wait per loading call | engine TTFT p50 / p95 | call p50 / p95 | preemptions |
|---|---|---|---|---|---|---|---|---|
| s2b | A0 GPU only | 786 | **18.3 %** (1.05M tokens = 43 % of all prefill) | — | — | 3.33 / 13.67 s | 9.98 / 30.86 s | 0 |
| s2b | A1 + DRAM 64 GiB (LRU) | **1,296** (1.65×) | **0.0 %** | 27.7 % | 0.57 s | 3.09 / 9.28 s | 7.30 / 18.97 s | 0 |
| s2b | A3 + hints (first version) | 996 | 3.5 % | 7.7 % | 1.65 s | 3.62 / 11.61 s | 8.29 / 26.97 s | 4 (96k tokens; 31 recomputed) |
| s2f | A1 + DRAM 64 GiB (LRU), paired | 1,311 | 0.0 % | 14.5 % | 0.44 s | 1.91 / 7.25 s | 5.51 / 27.53 s | 0 |
| s2f | **A3b + hints (fixed)** | 1,302 | 0.0 % | 0.5 % | 0.68 s | 1.29 / 6.00 s | 5.48 / 16.76 s | 4 (96k tokens; 33 recomputed) |

*Key.* **preemptions**: running requests that vLLM pushed off the GPU to make room. Given as how many, the KV
tokens they had computed, and how many of those had to be recomputed when they resumed; the rest came back from the
caches.

**The mechanisms fired** (A3 / A3b):
- **Hints:** 296-328 `tool_start`, 5 `turn_end` and 1-2 `session_end` per level, all received by the scheduler.
- **Demotions:**
  - 5 in A3, each right after a closed-loop turn end, because the predictor's turn-end value (2.01 s) is above
    the 2-s threshold. They pushed 6,868 GPU blocks (110k tokens) to the eviction front, for sessions that came
    straight back.
  - 6 in A3b: 2 at session end, 3 after the 10-s timeout, 1 predicted.
  - A3b moved 1,151 GPU blocks to the eviction front, never a block shared with another session's prompt.
  - Returns after a demotion found 87 % of their prompt in DRAM and recomputed 0 %.
- **Prefetch:** 1 shadow request. In A3b only 8 of 675 replies predicted a pause ≥ 2 s within 12 minutes; the
  32B on RTX reached few full-suite runs.

**What B1's smoke shows:**
- **The DRAM tier is what matters at this load:** 1.65× the throughput of GPU only, and recompute after eviction
  falls from 18 % to 0 % of prompt tokens.
- **Hints, done wrong, cost 23 %.**
  - The first A3 demoted sessions at turn ends that came straight back. Its DRAM order then evicted those same
    sessions' DRAM copies first, so they lost their KV in both tiers: 255k tokens recomputed, against 2.5k in A3b.
  - A3b fixed both at once and also dropped priority scheduling, so the smoke cannot say which bug mattered more.
  - [corrected 2026-10-05: an earlier version blamed priority-scheduling preemptions; A3b, without priority
    scheduling, preempted just as often]
- **Preemptions are an open question.**
  - Both hint arms had 4, about 96k computed tokens each: A3 under priority scheduling, A3b under first come,
    first served.
  - A1 and A0 had none.
  - Each cost almost nothing: the preempted requests got their KV back from the caches, with 31-33 tokens
    recomputed per run.
- **Hints, done right, are neutral on throughput** (1,302 vs 1,311 lead calls/h). That is S0's prediction: a
  DRAM tier that holds the working set leaves little for hints.
- **TTFT and call-time tails looked better with hints**, but two identical A1 runs (s2b, s2f) already differed
  that much (DRAM-load share 27.7 % vs 14.5 %). The smoke cannot resolve effects of this size; S3 needs repeats,
  longer windows and constrained DRAM.


## 5. Phenomena observed (all stages)

Ordered by how much they matter for the four ideas.

1. **Decodes stall behind co-scheduled prefill** (S0, all H200 levels with N ≥ 4).
   - A decode request in an engine step that also runs a prefill chunk waits for the whole chunk, up to ~1.2 s on
     the 32B (8,192-token chunks).
   - That is 21-45 % of 32B call time at N = 4-8, and 29 % at N = 16 even with a DRAM tier. This is B3's case, and
     the largest ceiling found.
2. **Eviction losses happen mostly while a call waits in the queue, not during the tool pause** (S0).
   - At N ≥ 12, 74-89 % of evicted tokens were still cached when the call arrived, and 96-98 % followed
     sub-second tool calls.
   - Demoting at tool start can only help the few long windows. A DRAM tier, or not evicting queued sessions'
     prefixes, helps everything.
3. **A plain DRAM tier already removes nearly all eviction recompute when it holds the working set.**
   - S2b, 32B on RTX, N = 4: recompute after eviction 18.3 % of prompt tokens (GPU only) → 0 % (DRAM tier), and
     1.65× the lead calls per hour.
   - S0, 12's o1 on H200, N = 16: 64-73 % of GPU time → 1.8 %.
4. **Write-through fills a DRAM tier in minutes.**
   - Each DRAM-tier arm prefilled 1.5-2.0M tokens in its 16 minutes of warm-up and window, against a 64 GiB tier
     holding 262k 32B tokens. So the tier evicts all the time, and its eviction order decides what survives.
   - LRU kept the active sessions (0 % recompute).
   - The first "session-aware" order evicted *away* sessions first, i.e. the ones about to return. Together with
     A3's demotions at turn ends, that cost 3.5 % recompute (§4.2).
5. **Preemptions appeared in both hint arms and in no other arm; their cause is open.**
   - A3 ran under priority scheduling, A3b under first come, first served. Each had 4 preemptions, about 96k
     computed tokens per run. A1 and A0 had none.
   - They cost little: the preempted requests got their KV back from the caches, with 31-33 tokens recomputed
     per run.
   - [corrected 2026-10-05: this item first blamed vLLM's priority scheduling, under which mu_session's
     `X-Vllm-Priority` tag (each session's arrival index) picks the victims. A3b rules that out as the cause.]
6. **Tools in typical sessions return in tens of milliseconds** (median window 0.01 s after read_file /
   edit_file).
   - Long windows come only from full test suites (p50 18.6 s), finds over site-packages (5.4 s, p90 36 s), and
     user think time (11: median 30 s).
   - A recursive grep over site-packages is bimodal (p50 0.01 s, p99 91 s): page-cache state decides.
7. **The duration predictor is biased by ~0.2 s** at the scheduler (S2b). It was trained on client-side windows,
   while the scheduler's gap also holds the front end and engine waits, so its p90 covered only 3-5 % of actual
   gaps. That is irrelevant for 2-s demotion thresholds, but it must be calibrated on server gaps.
8. **The previous reply's KV is never reused today; D1 makes it reusable.**
   - Qwen3's template drops the generation prompt's empty think block when it re-renders history, so the next
     prompt diverges 4 tokens before the end of the previous one.
   - Live (8B, N = 4): re-prefill of the previous reply goes from 90.8-91.8 tokens per call (baseline) to 8.3-8.4
     (D1): only the last partial block.
9. **The per-call front end is half SDK/HTTP/parse and half template/tokenise.** D1 removes most of both: live 8B,
   N = 4, 172 → 47 and 242 → 55 ms per call (−73 to −77 %), from 7.5-10.3 % of call time to 2.6-2.7 %. At load,
   the measured front end also waits for the running engine step, which D1 does not touch.
10. **Three ways the live harness broke D1 that offline replays could not show**, all fixed and covered by tests:
    - **Edits right after a user message.** The harness sends consecutive user messages (todo reminders every 3
      rounds). A history edit then left the template's `\n` separator duplicated: 107 of 855 live verify calls.
    - **In-place edits.** Micro-compaction replaces old tool results *inside the same dicts*, so an identity check
      sees no change: 52 of 767 verify calls reused stale tool results. The client now fingerprints messages at
      send time.
    - **Prompts over the window.** 7-10 calls per verify level exceeded 40,960 tokens. The verify render rejects
      them and they go back to vLLM's normal error path, which the harness answers with compaction.
11. **vLLM's KV offload copies 32 KiB pages at ~37 GB/s on PCIe Gen5** (65 % of the link). Restoring a
    16-23k-token 32B context costs 0.12-0.17 s over a GPU hit, against 3.8-6.1 s to recompute it (S2a). Under load
    on H200, 12's o1 restored at only 8.9 GB/s.
12. **Local NVMe reads at 3-4 GB/s but writes at 1-1.5 GB/s.** A flash tier fed by write-through would fall behind
    the 32B's peak KV production on RTX (~1.3 GB/s).
13. **The RTX PRO 6000 32B is capacity-starved by design**: a 88,848-token KV pool, about 3.8 sessions of 23k
    tokens. Decode costs 47 ms per token, against ~20-25 ms on H200.


## 6. What changes for S3-S5

- **B3 first.** S0 says decode stalls are the biggest addressable cost from N = 4 up.
  - Run S4 (H200 calibration, the NIXL check, peer-copy rates), then S5's B3 arms.
  - Keep DP2-c2k (smaller prefill chunks) as the cheap competitor.
- **B1: rebuild the policy around what S0 and S2 showed.**
  - Protect sessions that are away (done: the order is now ended → LRU → away).
  - Never demote on a closed-loop turn end (`TM_THINK_S=0`).
  - Keep first-come-first-served scheduling. Priority scheduling was there only for prefetch requests, and A3
    sent none.
  - Find out why the hint arms preempted while A1 and A0 did not (pool state at each preemption).
  - Add queue-time protection, i.e. pins that last until admission plus the gate.
  - Test where DRAM is constrained: arms A2 / A4 with 16 GiB plus the local NVMe tier, and longer windows so the
    heavy tools' long windows actually occur.
  - The paired rerun s2f is the first check (§4).
- **D1** is worth its complexity for small, fast models at low-to-moderate load: a −75 % front end and −90 % reply
  re-prefill. Keep verify mode as a regression guard; s2g is the clean live exactness run after both fixes (§4).
- **B2 is capped at ~3 %** on this workload. Measure the copy path's NVLink rate in S4 before spending S5 time on
  it.
- **Calibrate the predictor on server-side gaps**, adding the ~0.2 s front end and engine wait, before S3.


## 7. Data and reproduction

- **S0:**
  - `sbatch --export=NONE research/13_tiered_memory/tm_cpu.sbatch tm_ceilings.py --jobs 15`, then `... tm_predict.py`.
  - Outputs: `data/s0/{ceilings.json, pauses.jsonl.xz}`, `data/tables/S0_*.md`, `tool_durations.json`.
- **S1:**
  - `pytest research/13_tiered_memory/test_tm.py`.
  - `TM_SLOW=1 pytest ... -k pipeline`.
  - `sbatch ... tm_cpu.sbatch tm_analyze.py d1replay` → `data/tables/S1_d1_replay.md`.
- **S2a:** `sbatch --export=NONE research/13_tiered_memory/tm_probe.sbatch` → `data/probe/<job>/`, then
  `tm_analyze.py probe`.
- **S2b:** `sbatch --export=NONE research/13_tiered_memory/tm1.sbatch s2d,s2b` → `data/cells/{s2d,s2b}/`, then
  `tm_analyze.py d1` / `b1`.
- **Storage:** `data/` is gitignored except `*.md` tables (research-data convention). Raw streams are xz on NFS,
  to be pushed to Drive with `research/common/drive_data.py push 13`.
