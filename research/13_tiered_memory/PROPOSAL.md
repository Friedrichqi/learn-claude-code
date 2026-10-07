# 13 · Tiered memory for the harness — proposal for B1, B2, B3 and D1

> **Status:** proposal (2026-10-03/04); stage S0 done 2026-10-04. No code beyond the offline S0 analysis, and
> no GPU job yet. §2 holds the S0 ceilings, computed from 11's and 12's existing data, and what they change.
>
> **Question (user, 2026-10-03):** 12 measured how busy the hardware is on live harness runs. Which parts
> of the harness system are the bottleneck now? With a tiered memory hierarchy (HBM, several GPUs' HBM,
> host DRAM, flash), how can the tiers remove those bottlenecks? From the brainstormed ideas the user
> picked four: **B1, B2, B3 and D1**. "Write a proposal first."
>
> **Constraints (user, 2026-10-03):**
> - Everything new lives in this folder. No existing research directory is replaced or edited; code from
>   11 and 12 is imported, subclassed or wrapped at runtime.
> - A job asks for more than one GPU only when the configuration needs it (TP, DP or P/D > 1).
> - QoS `standard`.
> - Experiments use the real harness on real tasks; microbenchmarks only calibrate.
>
> **Models / hardware:** Qwen3-32B and Qwen3-8B (bf16, thinking off) on vLLM 0.29.0, as in 11/12.
> - 1-GPU work (B1, D1) runs on an RTX PRO 6000 Blackwell: 96 GB, PCIe only. Slurm sends every 1-GPU job
>   to that pool.
> - 2-GPU work (B2, B3) runs on an H200 pair: 141 GB each, NVLink through NVSwitch.

**Contents** — §1 Bottlenecks · §2 What each idea can win at most · §3 Shared design · §4 D1 · §5 B1 ·
§6 B2 · §7 B3 · §8 GPUs and budget · §9 Stages and gates · §10 Verification · §11 Risks

## 1. The bottlenecks this proposal starts from

12 found **no CPU bottleneck**: the GPU always saturates first. Ranked by cost, the bottlenecks on the
live harness (12, plus an analysis of its request captures) are:

| # | bottleneck | evidence | addressed by |
|---|---|---|---|
| 1 | **Re-prefill on the GPU.** About 70 % of the 32B's recomputed tokens come from the harness's micro-compaction: it replaces an old tool result in place, which breaks the prefix, so the ~11.6k-token suffix is prefilled again. Most of the rest comes from eviction. | 12 request captures: 424 of 2,260 calls at 32B N = 8 | none of the four fixes compaction itself. B1/B2 cut the eviction part; B3 moves prefill off the decode GPU |
| 2 | **KV capacity.** On one H200 the 32B's cache thrashes past 8-12 users, and 74 % of GPU time goes to recompute. | 11 §3.4; 12 T4 | B1, B2 |
| 3 | **Decode is memory-bound and stalls behind prefill.** At batch 1, tensor cores are 5 % busy and HBM bandwidth 70 %. A 1.2-s 32B re-prefill step holds up every running decode. | 12 T7, T8, T9 | B3 |
| 4 | **CPU front end.** Each call re-sends, parses, templates and tokenizes the whole conversation: ~3.5 ms per 1k prompt tokens on the critical path. That is 3 % of a 32B call and 11 % of an 8B call. It adds latency, not a throughput limit. | 12 §3.2 | D1 |
| 5 | Heavy tools wait on NFS metadata (repository greps at 0.04 cores). | 12 §3.5 | — |

The four ideas, in one line each:
- **B1. Agent-aware tiered KV.** GPU HBM → host DRAM → local flash, driven by hints from the harness:
  - demote a session's KV when a long tool starts;
  - prefetch it back before the tool ends;
  - size the session gate to HBM.
- **B2. A peer GPU's HBM as a KV tier.** Reached over NVLink.
- **B3. Prefill/decode disaggregation across two GPUs.** KV moves over NVLink.
- **D1. Session-resident token state.** The server keeps each session's token ids in host DRAM, so the
  harness sends a session handle plus the new messages only. Generated tokens are kept exactly as
  generated.

## 2. What each idea can win at most (S0)

`tm_ceilings.py` computed these offline. It read 13 scheduler logs from 11 and 12: 24 levels, about 160k model
calls, all on H200. Its docstring holds the definitions, and `data/tables/S0_ceilings.md` the full tables.

Each number is an upper bound: it assumes the idea removes its target completely and changes nothing else.

| idea | target | ceiling (S0) | where it is large |
|---|---|---|---|
| D1 | the front end that grows with the context; the re-prefill of the previous reply | **Front end:** 9.0 % of 8B call time at N = 1; 4.8-6.7 % at N = 4; 1.7-3.2 % at N = 16-32. For the 32B, 2.7 % at N = 1 and ≤ 1.8 % above. **Reply:** 86-119 tokens per call (never reused today); 10-19 % of the 8B's prefill tokens, but ≤ 2.5 % of GPU time | small, fast models at low load |
| B1 | recompute after eviction | **32B, no CPU tier:** 0-5.5 % of prefill tokens up to N = 8; 71-85 % at N = 12-16 (64-73 % of GPU time). **With the hint-free CPU tier** (12's o1, N = 16): 4.4 % of prefill tokens (1.8 % of GPU), plus 3.2 % of call time waiting for loads | past the turning point. Over a plain DRAM tier, hints can add ~5 % at most while DRAM holds the working set. 11's offload gave 2.8× at N = 16, but only 1.1× at N = 32, where DRAM was too small |
| B2 | how fast the spill tier restores | o1: 83 % of calls load from DRAM, 20.5k tokens each. Each load waits 0.60 s, which is 3.2 % of call time. The rate is 34k tokens/s = 8.9 GB/s, about 1/6 of PCIe Gen5 | ≤ 3 % of call time at 32B N = 16. The copy path, not the link, may set the speed |
| B3 | decodes stalled behind co-scheduled prefill | **32B:** 21-24 % of call time at N = 4, 35-45 % at N = 8. At N = 16, 29 % with the CPU tier and 52-61 % without. **TP=2:** 35 % at N = 8, 61 % at N = 24. **8B:** 3 % at N = 4, 13-23 % at N = 16-32 | the largest of the four, from N = 4 up |

**What S0 changes:**
- **B3 has by far the largest ceiling, much larger than expected.** A decode that shares an engine step with
  a prefill chunk waits for the whole chunk, about 1.2 s on the 32B. The cheap competitor, smaller prefill
  chunks on DP=2, becomes a B3 arm (§7).
- **Most of B1's eviction losses happen in the queue, not during the pause.**
  - At N ≥ 12, 74-89 % of the evicted tokens were still cached when the call arrived and were evicted
    while it waited for admission.
  - 96-98 % come after sub-second tool calls.
  - Demoting at tool start only matters for the long windows: a full test suite (p50 18.6 s), find over
    site-packages (p50 5.4 s, p90 36 s), and think time (median 30 s in 11).
  - So B1's value over a plain DRAM tier lies in three places:
    1. not evicting the prefixes of queued sessions (the gate; 11's pins, which last until admission);
    2. hiding the 3.2 % load wait with prefetch;
    3. working sets larger than DRAM (arms A2 / A4).
- **D1** is worth 5-9 % of 8B call time at low load and little elsewhere. Its reply-reuse part is small,
  because replies are 50-120 tokens.
- **B2** is capped at about 3 % of call time on this workload, unless restores grow more frequent or PCIe
  busier. The S4 calibration must check whether the copy path runs any faster over NVLink.
- **The tool-duration predictor** (`tool_durations.json`, table in `data/tables/S0_tool_durations.md`):
  - built from 137,544 windows (11/12, client-side) between a reply and the session's next call, keyed by
    the reply's dominant tool, with 06's census as the fallback;
  - `bash:grep/wide` is bimodal (p50 0.01 s, p99 about 90 s) because page-cache state decides, so B1 also
    demotes a session once it has been away longer than θ, whatever the prediction said.

## 3. Shared design

The harness calls vLLM's Anthropic endpoint (`/v1/messages`). 11's `mu_session.py` patches the SDK's
`Messages.create` and tags every call with `X-Session-ID`. vLLM stores that header as
`Request.session_id`, and 11's scheduler reads it. Everything below is new code in this folder; the
hooks it uses already exist.

| new file | role | existing hook it uses (unchanged) |
|---|---|---|
| `tm_client.py`, `bin/tmpython` | client side: D1 delta requests; `session_end` and actual `tool_end` hints | `bin/tmpython` is passed as mu_sweep's `--python` (mu_sweep.py:213, :400). It wraps `Messages.create` *before* mu_session's capture does (mu_session.py:123-126 wraps whatever create it finds), so 11's tags and request capture still see every call |
| `tm_server_ext.py` | API server: D1 session store (token ids and per-message offsets, host DRAM), render hook, server-inferred hints, prefetch shadow requests, `/tm/hint` and `/tm/stats` | vLLM `--middleware tm_server_ext.Middleware` (serve/middleware/register.py:65). Wraps `OpenAIServingChat.render_chat_request` (openai/chat_completion/serving.py:219), which the Anthropic path calls (anthropic/serving.py:98, :610) |
| `tm_scheduler.py` | `TMScheduler(HWScheduler)`: a hint socket (ZMQ PULL, drained in each `schedule()`), per-session state, demote / protect, an HBM-sized gate, `tm` records | `--scheduler-cls`. Builds on 12's HWScheduler, which builds on 11's MUScheduler (gate, TTL pins, pool walk); free-queue `remove` / `prepend_n` (core/kv_cache_utils.py:351, :394) |
| `tm_policy.py` | session-aware eviction in the DRAM tier | out-of-tree `CachePolicy` via `cache_policy_module_path` (kv_offload/tiering/spec.py) |
| `tm_peer_spec.py` | `PeerHBMOffloadingSpec`: the offload buffer lives in the peer GPU's HBM | out-of-tree spec via `spec_name` + `spec_module_path` (kv_offload/factory.py:31-50). Reuses the CPU spec's block-copy handler (kv_offload/cpu/gpu_worker.py, `swap_blocks_batch` on UVA pointers) once peer access is on |
| `pd_proxy.py` | Anthropic proxy: session-affine routing (DP=2), the two P/D steps, passthrough | `kv_transfer_params` is already in vLLM's Anthropic request and response (anthropic/protocol.py:154, :249) |
| `tm_predict.py` | tool-duration predictor, saved as a frozen JSON | 06's census (173k calls) and 11/12 traces |
| `tm_calib.py` | restore microbenchmark: peer HBM, pinned DRAM, local NVMe, recompute | 12's `hw_sampler.py` link counters show which link moved the bytes |
| `tm_ceilings.py` | S0: the §2 ceilings from existing data | 11/12 scheduler logs, request captures, harness traces |
| `tm_workloads.py`, `tm_pipeline.sh`, `tm1.sbatch` (1 GPU), `tm2.sbatch` (2 GPUs), `tm_cpu.sbatch` | cells as JSON for 11's existing `MU_EXTRA_CONDITIONS` hook; a pipeline modelled on 12's `hw_pipeline.sh` (a copy, not an edit) | 11's `mu_sweep.py` / `mu_session.py` |
| `tm_analyze.py`, `tm_figures.py`, `test_tm.py` | tables, SVG figures, tests | 12's `call_breakdown`, `gpu_busy_intervals`, phases T7-T9 and `fake_vllm.py` |

- **Layout:** the usual research layout (depth-2 scripts, `_paths` header). data/ goes on Google Drive and
  is gitignored, except tables, figures and frozen inputs.
- **Server hooks fail open:** an exception writes an `err` record and turns that hook off, as in
  HWScheduler.
- **vLLM version:** the private APIs above are pinned to 0.29.0.

## 4. D1 — session-resident token state (1 GPU, RTX PRO 6000)

**Hypothesis.** Suppose the client sends only the new messages and the server keeps the session's token
ids in host DRAM:
- the per-call front end becomes O(new tokens) instead of O(context);
- keeping generated tokens exactly as generated ends the re-prefill of the previous reply.

**Why the reply is re-prefilled today.** With thinking off, the generation prompt ends in an empty
`<think>\n\n</think>\n\n`. Qwen3's template drops that block when it renders the same assistant turn as
history. The next prompt therefore differs four tokens before the end of the previous prompt, and every
block of the reply is prefilled again. 12's scheduler log confirms it: in 99.9 % of append-only calls the
reusable prefix ends exactly there.

**Mechanism:**
- **Client.** Per session, keep the last-sent message list.
  - Let k be its longest common prefix with the new list (identity check first, then equality).
  - Send `messages[k:]` with `X-TM-Base: k`. System prompt and tools go only with the first call
    (`X-TM-Init`).
  - A micro-compaction edit just gives a smaller k.
  - If the server lacks the session or k, it answers 409 and the client resends in full (counted).
- **Server.**
  - The prompt ids are the stored ids up to message k, plus a delta render of `messages[k:]` and the
    generation prompt. vLLM's normal path then runs (tool parser, Anthropic response).
  - At completion the server appends the generated ids exactly, empty think block included.
- **Delta renderer.** It covers the message shapes the harness sends: user text, tool results, and an
  edited assistant turn sent again. Anything else falls back to a full render (counted). A test checks it
  is token-exact against the full template (§10).

**Cells.** LIGHT closed loop with think time 0 (12's fleet setup); baseline vs D1:
- Qwen3-8B at N = 1, 4, 16;
- Qwen3-32B at N = 1 (exactness and quality on the large model).

**Metrics:**
- front end per call vs prompt length, from 12's `call_breakdown` (client send → scheduler arrival;
  HTTP + parse vs render + tokenize), plus client SDK time;
- API-server CPU, prefix-hit tokens per call, time to first token, session time, lead calls per hour.

**Guards.** The model now sees empty think blocks in its history, so we track task success (06's
`score_run`), tool-call parse errors, resyncs, fallback renders and loop rate.

**Expected (S0 ceilings):**
- The front end stays roughly flat as context grows.
- 8B call time drops up to 9 % at N = 1, 5-7 % at N = 4 and about 3 % at N = 16; 32B under 3 %.
- The reply re-prefill (86-119 tokens per call) drops to zero; that is ≤ 2.5 % of GPU time.

D1's session store is also what B1's prefetch needs.

## 5. B1 — agent-aware tiered KV: GPU → DRAM → flash (1 GPU, RTX PRO 6000)

**Hypothesis.** With harness hints, a GPU → DRAM → flash hierarchy keeps a returning session's KV closer
than vLLM's LRU offload does: fewer recomputes, and loads hidden behind the tool. The gap should be
largest when DRAM is constrained.

**Hints:**
- **Server-inferred, no harness change** (`tm_server_ext`):
  - a reply that ends in a tool call sends `tool_start`, with the tool, its command class and a predicted
    p50/p90 duration;
  - a reply that ends the turn sends `turn_end`.
- **From the client:** `session_end`, and the actual `tool_end` (for analysis).
- **Teammate waits and the next agent from the task DAG:** the protocol accepts a `wait` hint, but our task
  specs run one agent plus memory calls, so this is not exercised here.

**Policy:**
- **Write-through.** vLLM's BLOCK_LEVEL offload already copies every computed block to DRAM, so demoting
  costs no copy.
- **Demote.** On `tool_start` with predicted p50 > θ (θ = restore time × margin), the session's cached-free
  GPU blocks that are already in DRAM move to the front of the eviction queue.
- **Protect.** Shorter predicted waits get 11's TTL pin for the predicted p90. The pin lasts until the next
  call is admitted, which matters because S0 finds most evictions happen while a call is queued.
- **Demote after θ.** A session away longer than θ is demoted whatever was predicted; bimodal tools such as
  a recursive grep over site-packages are then still caught.
- **Gate.** 11's session gate, with K recomputed at each pool walk as usable HBM blocks divided by the
  median active session's blocks.
- **DRAM eviction order:** ended sessions → turn-ended sessions idle longest → in-tool sessions with the
  longest predicted remaining time → LRU.
- **Prefetch.**
  - At the predicted end minus a lead time (session bytes / measured PCIe rate + 50 ms), the API server
    submits an internal 1-token shadow request with the session's exact token ids (D1's store).
  - It runs at lower priority; every B1 arm uses `--scheduling-policy priority`, so arms stay comparable.
  - This pulls the blocks into the GPU prefix cache. The shadow request is cancelled if the real call
    arrives first.

**Workload.** The HEAVY mix — HTEST : HSEARCH : LIGHT = 1 : 1 : 2 with 06's prompts — plus 11's think time
between turns. Idle windows come from real 17-43-s test-suite and grep runs; LIGHT sessions' own tools
return in 20-80 ms (12's scheduler log), too short to matter.

**Capacity.** On the RTX the 32B leaves ~20 GiB of KV (~80k tokens, about three full sessions), so its turning point
is near N = 3. The 8B leaves ~65 GiB (~470k tokens).

| arm | tiers | policy | 32B N | 8B N |
|---|---|---|---|---|
| A0 | GPU only | vLLM LRU | 2, 4, 8 | 16, 32 |
| A1 | GPU + DRAM 64 GiB | native CPU offload, LRU | 2, 4, 8 | 16, 32 |
| A3 | as A1 | all hints + prefetch | 2, 4, 8 | 16, 32 |
| A3−p | as A1 | hints, no prefetch | 4 | — |
| A2 | GPU + DRAM 16 GiB + local flash | TieringOffloadingSpec with the fs tier, LRU | 4, 8 | — |
| A4 | as A2 | all hints + prefetch | 4, 8 | — |

**Metrics:**
- lead calls/h, sessions/h, time to first token p50/p95;
- recomputed prefill tokens per call (vLLM's prefix-hit counters, and 11's ideal-vs-actual hit);
- loads and bytes per tier (`vllm:kv_offload_tiering_*`), PCIe GB/s, flash bytes;
- demote / protect counts, prediction error, prefetch hits, late prefetches, wasted bytes.

**Expected (S0):**
- A1 is far ahead of A0 above the turning point, as o1 was. o1's CPU tier left 4.4 % of prefill tokens recomputed
  and 3.2 % of call time waiting for loads.
- A3 over A1 is therefore capped at about 5 % while DRAM holds the working set.
- A4 vs A2 is where hints should pay: the working set outgrows DRAM, as at 11's N = 32, where offload gave
  only 1.1×.

**Kill criterion:** if neither A3 nor A4 beats its no-hint arm by ≥ 5 % in throughput or p95 time to first
token at any N, report that hints do not pay at this scale.

## 6. B2 — a peer GPU's HBM as a KV tier over NVLink (2×H200, DP=2)

**Hypothesis.** For an overloaded GPU, a lightly loaded neighbour's HBM is a faster spill tier than host
DRAM (NVLink vs PCIe).

**Mechanism:**
- Two TP=1 vLLM instances. Each process sees both GPUs (`CUDA_VISIBLE_DEVICES=own,peer`); vLLM runs on
  cuda:0, its own GPU.
- `PeerHBMOffloadingSpec` puts the borrower's offload buffer on cuda:1 (the peer) and turns on peer access,
  so block copies go over NVLink.
- Total HBM stays fixed through `--kv-cache-memory-bytes`: the lender's own KV shrinks by the lent amount L.
- Lending only helps when load is uneven; under balanced load the best lending is none. So the split is
  static and one-way, lender B → borrower A.
- Dynamic lending, where B lends its free blocks on demand, follows only if the static split wins.

**Calibration first** (same job). `tm_calib.py` measures restore latency and bandwidth vs size for peer
HBM, pinned DRAM, local NVMe (O_DIRECT) and recompute. 12's NVML link counters confirm which link was
used.

**Cells.** 32B, LIGHT closed loop, sessions pinned to an instance by `pd_proxy`, uneven load,
L = 24 GiB. K is an instance's normal KV pool.

| arm | A (heavy: 18 or 24 sessions) | B (light: 6 or 8 sessions) |
|---|---|---|
| D-L | K + DRAM L | K + DRAM L |
| P-L | K + peer L (on B's GPU) | K − L |
| D-64 | K + DRAM 64 GiB | K + DRAM 64 GiB |
| P+D | K + peer L + DRAM 64 GiB (MultiConnector, peer first) | K − L + DRAM 64 GiB |
| TP2 | one TP=2 instance + DRAM 128 GiB, all sessions: pooling by sharding | |

- D-L vs P-L is the speed test at equal capacity, run at 18 + 6.
- D-64 vs P+D is the realistic pair, run at 18 + 6 and at 24 + 8.
- TP2 runs with 24 sessions.

**Metrics:** fleet and per-instance lead calls/h; time to first token p50/p95; restores per tier (count,
bytes, ms); NVLink and PCIe GB/s; recompute tokens; what lending costs B.

**Expected:** a small gain.
- S0 puts the whole DRAM load wait at 3.2 % of call time in o1, at 8.9 GB/s.
- B1's prefetch can hide part of that wait.
- Peer HBM pays only when returns are unpredictable, contexts are large, or the copy path runs faster than
  8.9 GB/s over NVLink. The S4 calibration settles the last point.

**Kill criterion:** if calibration shows peer restores at most 50 ms faster than DRAM for typical contexts,
run only the 18 + 6 D-64 / P+D pair. (S0 found restores take 3.2 % of call time, above the 2 % bound.)

## 7. B3 — prefill/decode disaggregation, KV over NVLink (2×H200)

**Hypothesis.** Running prefill and decode on different GPUs, with KV moved over NVLink, removes the decode
stalls behind long agent re-prefills.

The catch for multi-turn agents is capacity. The prefill GPU must cache every session's prefix, and the
decode GPU holds every session too. Distinct-KV capacity therefore halves compared with DP=2.

**Mechanism:**
- vLLM's NixlConnector, with prefill (P) on GPU0 and decode (D) on GPU1. Each process sees both GPUs, its
  own first.
- `pd_proxy` sends each call to P with `max_tokens=1` and `kv_transfer_params {do_remote_decode: true}`,
  then to D with the params P returns. D pulls only the blocks it lacks.
- **NIXL is not installed.**
  - Install it with `pip install --no-deps --target research/13_tiered_memory/.deps nixl-cu13==1.5.0`.
    The 59 MB PyPI wheel needs only torch and numpy.
  - The `.deps` directory is gitignored and goes on PYTHONPATH only for 13's servers; `~/.venv` is not
    touched.

**Cells.** 32B, LIGHT closed loop, total N = 4, 8, 16. Every arm goes through `pd_proxy`, so all pay the
same extra hop.

| arm | layout | N |
|---|---|---|
| DP2 | two TP=1 instances, sessions pinned, N/2 each | 4, 8, 16 |
| DP2-c2k | DP2 with smaller prefill chunks (`--max-num-batched-tokens 2048`): the cheap way to shorten decode stalls | 4, 8 |
| 1P1D | NIXL over NVLink (UCX cuda_ipc) | 4, 8, 16 |
| TP2 | one TP=2 instance; 12's T2 cell (N = 8, 24) as a cross-reference | 8 |

**Metrics:**
- lead calls/h; time to first token; time between decode tokens p50/p99; decode time per call; critical
  path per call;
- KV bytes per call over NVLink (NIXL stats and NVML counters); busy split per GPU (12's T7 method);
- evictions on each side; recompute of reply tokens on P.

**Expected.** S0 finds decodes stalled behind prefill for 21-45 % of 32B call time at N = 4-8.
- 1P1D should remove most of that stall at N = 4-8, at the price of KV transfers and of prefill queueing
  on one GPU.
- It loses at N = 16, where both GPUs must hold every session. The crossover N is the result.
- DP2-c2k tells whether shorter chunks already capture the gain. 11 found they did nothing for thrashing
  throughput, but that measured recompute, not decode stalls.
If 1P1D wins, the follow-up is for D to use P's cache as its NVLink tier (B2 × B3).

## 8. GPUs and budget

| work | GPUs per job | node | cells | GPU-hours |
|---|---|---|---|---|
| S2 probe + smoke tests | 1 | RTX PRO 6000 | — | ~1 |
| D1 | 1 | RTX PRO 6000 | 8 | ~6 |
| B1 | 1 | RTX PRO 6000 | 20 | ~15 |
| S4 calibration + peer / NIXL smoke tests | 2 | H200 pair | — | ~2 |
| B2 | 2 | H200 pair | 7 | ~12 |
| B3 | 2 | H200 pair | 9 | ~14 |

- Total: about 22 RTX and 28 H200 GPU-hours.
- Every job runs with `--qos=standard --export=NONE` and is resumable (COMPLETE marker plus lock, as in
  11/12).
- A 2-GPU `--constraint=h200` job appears only where the cell needs both GPUs.
- RTX results are compared only with RTX baselines. H200 results are compared with 11/12 only as
  cross-checks.

## 9. Stages and gates

1. **S0** (no GPU): this proposal, the §2 ceilings, and the tool-duration predictor. **Stop and report.**
2. **S1** (CPU): the code and `test_tm.py`, plus an end-to-end run against a fake server: mu_sweep with
   `--python bin/tmpython` and 12's `fake_vllm.py`.
3. **S2** (1 RTX GPU, ~1 h), then **report**:
   - probe: lscpu; lsblk / df (Slurm reports no TmpDisk, so whether there is local flash is unknown);
     O_DIRECT flash bandwidth; PCIe H2D / D2H; DRAM;
   - vLLM's KV capacity for the 32B and the 8B on the RTX;
   - live D1 token exactness;
   - B1 offload, hint and prefetch events.
4. **S3:** D1 cells, then B1 cells.
5. **S4** (2×H200, ~1 h): `tm_calib`; the peer-spec copy check; the NIXL check (NVLink bytes move, PCIe
   bytes don't). **Report.**
6. **S5:** B2, then B3.

## 10. Verification

- **`test_tm.py`:**
  - the D1 delta render is token-exact against the full Qwen3 template on every recorded request in 11's
    and 12's captures, apart from the intended reply spans;
  - the delta protocol: append; an edit at k (compaction); an unknown session or k gives 409 and a full
    resend;
  - a hint-bus round trip; the demotion order on a synthetic free queue; the DRAM eviction order;
  - `pd_proxy`'s P/D and passthrough flows against fake servers.
- **No edits elsewhere.** 11's and 12's test suites pass unchanged, and `git status` shows changes only
  under `research/13_tiered_memory/` plus the index row in `research/README.md`.
- **GPU smoke tests:** S2 and S4.
- **Cross-checks in `tm_analyze.py`:**
  - prefix-hit tokens (vLLM counters) vs 11's ideal / actual hit;
  - tier bytes (tiering metrics, NIXL stats) vs NVML / GPM link bytes;
  - client-side vs server-side front-end time.

## 11. Risks

- **No local flash on the RTX nodes.** A2 / A4 then keep the 16 GiB DRAM limit without the flash tier, and
  the report says so.
- **The 32B in bf16 may not fit `--max-model-len 40960` with CUDA graphs on 96 GB.** Cap the length at the
  harness's real maximum, or make the 8B B1's main model.
- **D1 changes what the model sees:** its history keeps the empty think block. The guards watch for this;
  any quality drop is reported as a cost of D1.
- **Private vLLM APIs:** pinned to 0.29.0, and every hook fails open.
- **NIXL / UCX may stage transfers through host memory.**
  - Both processes see both GPUs to avoid that.
  - If NVLink stays idle anyway, B3 is reported with the path NIXL used.
  - A custom NVLink connector needs the user's approval first.
- **MultiConnector with two OffloadingConnectors (P+D) is untested.** If it fails, that arm is dropped.
- **Wrong tool-duration predictions** waste PCIe bandwidth and shadow-request slots. This is measured.
- **Queue waits:** there are only 4 RTX nodes, and the H200s are often full.
- **Data:**
  - raw streams are written node-local, then compressed with xz onto NFS;
  - data/ is pushed to Google Drive and gitignored;
  - nothing is committed unless the user asks.
