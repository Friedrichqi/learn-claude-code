# Topic 23: can harness-called efficiency tools help Qwen3.8-27B serve 32 benchmark agents on one H100?

> **Status:** done (2026-11-03 16:05 UTC). All 8 main runs are valid; 35.69 GPU-h on GPUs 4-5 (`data/gpu_hours.jsonl`).
> Results below. Docker-in-docker and this topic's two images are removed. Plan: [PLAN.md](PLAN.md) (approved
> 2026-11-02).
> **Model:** Qwen3.8-27B-FP8 (hybrid: 48 Gated DeltaNet + 16 attention layers), vLLM 0.31.0, TP=1, GPUs 4-5.
> **Benchmark:** topic 22's 96 tasks (32 SWE-bench Verified + 32 SWE-bench Pro + 32 Terminal-Bench 2.1, seed 2211),
> 32 agents at once. Nothing committed.

## Question

The user (2026-11-02): run topic 22's idea again with Qwen3.8-27B: the harness decides by itself whether to call
external tools for efficiency.
- **The setups:** a vanilla harness; fixed session gates (8 and 16 sessions); an adaptive gate, where the model sets
  the session limit from the hardware metrics; the final `wait` tool; and LMCache's KV offload to DRAM as a tool the
  model calls.
- **First, the prompts:** engineer the tool descriptions so the model understands and uses them.

The user's reading of topic 22 is the reason for the offload setup. The GPU's KV cache can't hold that many
sessions, so a session's cache is evicted while it waits, and the recompute remains. Offloading the KV to host memory
should let a paused session's cache survive.

## Setups (8 runs; each: the 96 tasks, 32 agents at once, one H100)

| setup | what is on | who decides |
|---|---|---|
| `none` | vanilla s15: no pacing tools, no status line | — |
| `gate8`, `gate16` | session gate (et_proxy.py), K fixed at 8 / 16 | fixed |
| `agate` | session gate; once a minute a controller model call sees the hardware metrics and sets K (`set_session_limit`; prompt `c3`, low effort); starts at 32 | the model |
| `wait` | topic 22's final prompt: wait text v5, paragraph `wait_kv`, the `kv_cue` line at 80 % | the model |
| `lmc` | `wait` plus `save_context` (description s1; the `[server]` line's cue names it); LMCache stores only what `save_context` sends | the model |
| `lmc_all` | LMCache stores every request; no pacing tools (offload without decisions) | — |
| `none_r2` | `none` again, on the other GPU (an A/A check of the noise) | — |

Schedule, two at a time: P1 `none` | `lmc_all`, P2 `wait` | `lmc`, P3 `gate8` | `agate`, P4 `gate16` | `none_r2`.

## Results

**All 8 runs are valid** (`et_analyze.py valid`):
- every task reached an end state, with no infrastructure errors;
- the proxy's answered calls, plus `agate`'s controller calls, equal vLLM's request counter in every run;
- the monitor wrote 1 record a second;
- the GPU was empty after every run.

**How to read the tables:**
- **Units:** seconds unless noted.
- **LLM call latency** (`et.md`'s request latency): a model call's time as the agent sees it, gate wait included.
  **Task latency** (`et.md`'s entry e2e): a task's agent run, from start to end.
- **Window:** request latency and throughput are over the full-N window, from the moment all 32 agents run to the last
  task's start (81-170 minutes per run).
- **Differences:** `x` − `none`, paired by task, with 95 % bootstrap intervals. Throughput ratios come from 10-minute
  blocks of both runs, 8-17 blocks per run. "Clear" means the interval leaves out 0 (or 1 for a ratio).
- **The agents' work:** throughput counts vLLM's counters minus the harness's own calls (`lmc`'s save re-sends,
  `agate`'s controller calls).
- **Tokens/s** counts prompt tokens, computed or found in a cache, plus generated tokens. Prompt tokens are 96 % of it,
  so a token found in a cache counts like one computed. Generated tokens/s and requests/h are closer to the agents'
  progress.
- **One run per setup.** `none_r2`, the same setup run again on the other GPU in another pair, is the yardstick
  (`none_r2` − `none`).
- **Tables:** `data/tables/et.md` and `et.json` (pre-registered).

**Each setup against `none`:**

| setup | requests/h × | tokens/s × | generated tok/s × | LLM call avg | LLM call p50 | task avg | task p50 | pass (points) |
|---|---|---|---|---|---|---|---|---|
| `none_r2` (A/A) | 0.94 [0.71, 1.29] | 0.98 [0.88, 1.11] | 0.93 [0.67, 1.29] | +3.7 [−1.0, 8.7] | **+4.5 [1.6, 7.5]** | +164 [−42, 370] | +302 [−223, 590] | +2.1 [−5.2, 9.4] |
| `gate8` | **0.66 [0.52, 0.84]** | **0.78 [0.69, 0.87]** | **0.74 [0.58, 0.92]** | **+36.4 [23.2, 51.5]** | **−17.6 [−20.1, −14.9]** | **+1,548 [1,004, 2,091]** | **+1,670 [1,019, 2,065]** | +7.3 [0.0, 14.6] |
| `gate16` | 0.95 [0.76, 1.23] | 1.07 [0.98, 1.19] | 0.92 [0.71, 1.17] | +6.2 [−3.1, 17.5] | **−13.7 [−16.2, −10.6]** | **+608 [276, 922]** | **+925 [322, 1,289]** | +5.2 [−3.1, 14.6] |
| `agate` | 0.98 [0.74, 1.31] | 1.04 [0.93, 1.18] | 0.92 [0.70, 1.23] | +4.4 [−0.3, 9.1] | **−5.3 [−7.7, −2.6]** | **+621 [364, 887]** | **+611 [143, 1,093]** | +4.2 [−4.2, 13.5] |
| `wait` | 1.02 [0.76, 1.40] | 1.05 [0.93, 1.19] | 1.05 [0.77, 1.41] | **−7.9 [−14.1, −1.6]** | **−7.3 [−10.2, −4.6]** | **−240 [−468, −16]** | −89 [−472, 125] | +5.2 [−2.1, 13.5] |
| `lmc` | 0.94 [0.70, 1.26] | 1.04 [0.92, 1.17] | 1.07 [0.81, 1.47] | **−20.3 [−24.8, −15.9]** | **−13.3 [−16.1, −10.5]** | −154 [−384, 77] | −89 [−557, 76] | +2.1 [−5.2, 9.4] |
| `lmc_all` | 1.09 [0.86, 1.46] | **1.21 [1.09, 1.39]** | 1.18 [0.89, 1.59] | −4.5 [−9.1, 0.4] | +2.1 [−0.9, 5.1] | −6 [−228, 232] | +192 [−215, 474] | +4.2 [−2.1, 10.4] |

Bold: clear. The A/A pair differs clearly at the median LLM call, so a median shift of a few seconds can be run-to-run
noise. The p95 and p99 of both latencies are in `et.md` and in the next table.

**LLM call and task latency per run.** The avg, p50, p95 and p99 are pre-registered (`et.md`). The split of the
average is post hoc (`et_latency.py` → `data/tables/latency.md`). It uses the proxy's timestamps for calls and the
gate's log for tasks. The proxy also logs the calls cut at a task's time limit (5-15 per run in the window), so its
two parts add up to within 2 s of the agent-seen average.

| setup | LLM call avg | p50 | p95 | p99 | gate wait (avg) | server (avg) | task avg | p50 | p95 | p99 | active (avg) | at the gate (avg) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `none` | 54.8 | 26.3 | 192.4 | 534.4 | 0 | 55.7 | 2,480 | 1,890 | 5,836 | 6,005 | 2,480 | 0 |
| `none_r2` | 58.7 | 30.6 | 200.8 | 511.2 | 0 | 60.1 | 2,644 | 2,192 | 6,004 | 6,006 | 2,644 | 0 |
| `gate8` | 92.8 | 8.7 | 645.0 | 1,514.0 | 70.9 | 22.7 | 4,028 | 3,560 | 10,671 | 11,940 | 1,265 | 2,763 |
| `gate16` | 62.0 | 12.6 | 394.5 | 730.6 | 31.8 | 30.9 | 3,088 | 2,815 | 6,447 | 8,474 | 1,771 | 1,317 |
| `agate` | 60.3 | 21.1 | 256.9 | 436.3 | 14.9 | 45.2 | 3,101 | 2,501 | 6,912 | 8,461 | 2,419 | 682 |
| `wait` | 48.3 | 19.1 | 167.1 | 452.5 | 0 | 48.0 | 2,240 | 1,801 | 5,206 | 6,004 | 2,240 | 0 |
| `lmc` | 35.5 | 13.0 | 134.1 | 380.9 | 0 | 50.4 | 2,326 | 1,801 | 5,991 | 6,003 | 2,326 | 0 |
| `lmc_all` | 51.8 | 28.4 | 158.8 | 458.9 | 0 | 53.2 | 2,474 | 2,082 | 6,003 | 6,004 | 2,474 | 0 |

What the split shows (post hoc):
- **A gate makes the server faster per call, and the wait moves to the gate.**
  - **`gate8`:** a call's server time averaged 22.7 s against `none`'s 55.7 s, with a batch of 7.6 requests against
    27.6 and an inter-token latency of 26 ms against 69 ms (vLLM's histograms). So the median call fell to 8.7 s.
  - But 8 % of `gate8`'s calls waited at the gate (p95 of the wait: 614 s), which raised the average call to 92.8 s.
    Per task it is the same trade.
- **`lmc`'s calls are as fast as `wait`'s once the save re-sends are left out:** 48.6 s avg and 21.0 s p50, against
  48.3 and 19.1. Its 35.5 / 13.0 includes the 1-token re-sends.
- **`lmc_all`:** requests waited longer in vLLM's queue before they started (12.2 s against 6.0 s on average) but
  decoded faster (51 ms against 69 ms a token). The server time came out about the same (53.2 s against 55.7 s).

**Per run:**

| setup | time with KV ≥ 80 % | prompt tokens: GPU hit / reloaded from DRAM | recomputed prompt tokens per task | requests running | gate-held s per task | requests/h | generated tok/s | pass | at the time limit | makespan (min) |
|---|---|---|---|---|---|---|---|---|---|---|
| `none` | 94 % | 11 % / – | 788,584 | 27.6 | – | 1,868 | 387 | 71 % | 25 | 180 |
| `none_r2` | 93 % | 11 % / – | 830,502 | 26.5 | – | 1,762 | 361 | 73 % | 31 | 197 |
| `gate8` | 0 % | 40 % / – | 651,961 | 7.6 | 2,763 | 1,230 | 287 | 78 % | 11 | 236 |
| `gate16` | 0 % | 31 % / – | 791,827 | 15.4 | 1,317 | 1,774 | 355 | 76 % | 14 | 230 |
| `agate` | 58 % | 16 % / – | 937,534 | 23.3 | 682 | 1,832 | 356 | 75 % | 21 | 193 |
| `wait` | 79 % | 18 % / – | 665,762 | 25.6 | – | 1,898 | 407 | 76 % | 21 | 159 |
| `lmc` | 83 % | 8 % / 37 % | 696,698 | 24.6 | – | 1,754 | 416 | 73 % | 24 | 170 |
| `lmc_all` | 93 % | 5 % / 38 % | 646,076 | 23.6 | – | 2,045 | 458 | 75 % | 24 | 165 |

**Reading 1, offload alone (`lmc_all` − `none`):**
- **Clear:** tokens/s, 1.21× [1.09, 1.39], against 0.98× in the A/A pair. LMCache reloaded 38 % of the prompt tokens from
  DRAM, and recomputed prompt tokens per task fell by 18 %.
- **Not clear:** requests/h (1.09×), generated tokens/s (1.18×), entry p50 (+192 s) and p95 (+167 s [−1,089, 803]).
- So offload clearly let the server get through more prompt tokens a second. Whether the agents got more done is
  not clear in one run.

**Reading 2, offload with `wait` (`lmc` − `wait`; the same pair):**
- **The paused session's cache survives:** the lead call after a pause found 44 % of its prompt cached after a save in
  `lmc`, against 7 % after a wait in `wait`.
- **Recompute per task is not lower:** 696,698 against 665,762 (+5 %; the two `none` runs differ by +5 % too).
  - Post hoc split: the agents' own calls recomputed 532,444 per task, 20 % less than in `wait` (39 % of their prompt
    cached, against 16 %).
  - The 1,311 save re-sends added 164,254 per task: when a save ran, only 45 % of its prompt was found cached (on the GPU or in DRAM: vLLM's cached count
    includes LMCache's hits, `vllm/v1/metrics/stats.py:288`). They
    were 29 % of vLLM's requests and 30 % of its tokens in the window.
- **Throughput and tasks: no clear difference.** Requests/h 0.92× [0.68, 1.26], tokens/s 0.99× [0.87, 1.09], entry avg
  +86 s [−113, 276], pass −3.1 points [−11.5, 5.2].
- **Request latency is the call mix:** avg −12.8 s [−18.2, −8.2], but the save re-sends are 1-token calls. Over working
  calls only (no re-sends, no wait-only calls; post hoc), avg +0.1 s [−5.8, 5.1] and p50 +2.0 s [0.4, 3.9].
- Both followed the 80 % rule: 13.3 waits per task in `wait`, and 13.8 waits and 13.7 saves in `lmc`. 54-56 % of the
  lead calls whose `[server]` line read 80 % or more waited, against 7-9 % below.

**Reading 3, the model-set K (`agate` − `gate16`; `gate16` is the better fixed gate at 1,774 requests/h against
`gate8`'s 1,230):**
- **No clear difference in throughput or tasks:** requests/h 1.03× [0.84, 1.28], tokens/s 0.97× [0.89, 1.05], entry avg
  +13 s [−345, 384], entry p50 −314 s [−813, 499], pass −1.0 points [−9.4, 8.3].
- **Request latency:** a shorter tail, p95 −138 s [−183, −75] and p99 −306 s [−384, −195], and a longer median, p50
  +8.5 s [6.6, 10.0]. For scale, the A/A p50 difference is +4.5 s [1.6, 7.5].
- **The controller:** K averaged 23.7 in the window and changed 61 times between 6 and 32. It cut K when the hit rate
  collapsed and raised it once the active contexts fit, mostly on data from before its previous change (Process,
  S4 P3).
- The two runs were in different pairs (P3 on GPU 5, P4 on GPU 4).

**The other setups against `none`:**
- **The gates trade active time for gate time.** A task's time is its active time plus its time at the gate; `none`'s
  tasks averaged 2,480 s, all active.
  - **`gate8`:** with 8 sessions the cache was 32 % used and the batch 7.6 requests, so requests/h fell to 0.66×. A
    task's active time halved (1,265 s), but it waited 2,763 s at the gate: 26 minutes longer in all.
  - **`gate16`:** throughput as `none` (0.95×, 1.07×). Active time fell by 29 % (1,771 s), but 1,317 s at the gate
    made tasks 10 minutes longer.
  - **`agate`:** throughput as `none` (0.98×, 1.04×). Active time about as `none`'s (2,419 s), plus 682 s at the gate:
    10 minutes longer.
  - `agate`'s high recompute per task comes from more calls per task (52.6 against 43.1). Per call it recomputed about
    as much as `none` (17,833 against 18,277 tokens; post hoc).
- **`wait`:** no clear throughput change.
  - Calls 7.9 s faster on average (7.4 s over working calls only).
  - Tasks 240 s faster on average [−468, −16]: clear, but about as large as the A/A difference (+164 s, the other way).
- **`lmc`:** no clear throughput or task change. Request avg −20.3 s, but −7.3 s [−12.9, −1.9] over working calls
  only (post hoc), as for `wait`.
- **Pass rates:** 71-78 %, no difference clear. The gated setups had fewer tasks at the time limit (11-21 against 25 and
  31), since time at the gate does not count toward it.

**Answer:** at 32 agents, Qwen3.8-27B's KV cache was at least 80 % full for 94 % of the time, and 11 % of the prompt
tokens hit the GPU cache.
- **No setup where the model decides clearly raised the agents' throughput:** not `wait`, not `wait` with
  `save_context`, and not the controller's K. Neither did a fixed K.
- **The one clear throughput gain** came from offload with no decision, `lmc_all`. It got through 1.21× the tokens/s,
  as reloads replaced recompute. Requests/h and task times did not change clearly.
- **The user's mechanism holds, but it doesn't pay:** a save keeps the paused session's cache (44 % against 7 % hits
  after a pause). But the save's re-send has to recompute what was already evicted: 164,254 tokens per task, against
  the 133,318 the agents' own calls saved. Recompute per task did not fall (+5 %, about the A/A difference).

**Caveats:**
- **One run per setup.** The intervals resample tasks within a run, not runs. The A/A pair shows the spread between
  runs: makespan 180 against 197 minutes, and a clear LLM call p50 difference (+4.5 s).
- **Pairs:** cross-pair contrasts include the time of day and the partner run on the same CPUs. Reading 1's and
  reading 2's runs shared a pair; reading 3's did not.
- **Gate time is outside the time limit,** so the gated setups gave their tasks more wall time. That moves their pass
  rates and entry times.
- **`agate`'s controller ran on the server it measured.** Its calls took 75 s at the median (68 of 122 over 60 s), so
  a round came every 75 s, not every 60 s. After a call over 60 s the next round starts at once, and its snapshot
  shows the minute before the change. A faster controller (a smaller model, no thinking) was not tested.
- **`lmc`'s `[server]` line differs from `wait`'s** (it names `save_context`; S2), so reading 2 changes the line as well
  as the tool.
- **CPU placement in P1:** something on the host changed both servers' CPU affinity 8-10 times in minutes 38-77, at the
  same moments on both. Each change was reset within 2 s (`pin.jsonl`). The other runs saw it only at start-up.
- **Throughput intervals are wide** (about ±30 % for requests/h), with 8-17 ten-minute blocks per run.

## Process

### S0, build and feasibility (2026-11-02)

- **Topic venv** `/data/fritz/venv23` (20:13 UTC): /data/fritz/venv's 200 packages at the same versions (uv, from its
  cache), then LMCache 0.5.6.
  - LMCache brings numpy 2.2.6, protobuf 6, prometheus-client 0.24.1 and opentelemetry 1.40 down, and tokenizers
    0.23.2 up to 0.23.3.
  - /data/fritz/venv is untouched. Every run uses venv23, so the software is the same in all setups.
  - LMCache's CUDA ops load against torch 2.13.0+cu130, and its own multiprocess connector imports against vLLM 0.31.
- **LMCache on Qwen3.8 works** (20:20-20:28 UTC, GPU 5, 0.12 GPU-h; `et_feas.py` → `data/s0/feas_lmcache.json`):
  - **Server:** `lmcache server --chunk-size 784 --separate-object-groups --l1-size-gb 200 --eviction-policy LRU`,
    plus vLLM with `--enable-prefix-caching --mamba-cache-mode align` and `kv_connector_module_path`
    `lmcache.integration.vllm.lmcache_mp_connector`. vLLM 0.31's bundled LMCache connectors refuse this hybrid model
    (topic 09 hit the same with LMCache 0.3.6); LMCache's own connector declares hybrid support.
  - **GPU pool:** 784-token blocks; 42.8 GiB of KV, 673,438 tokens.
  - **Reload:** a 20,384-token context, after `/reset_prefix_cache` cleared the GPU cache, came back as 20,384 external
    hit tokens, and the greedy reply was identical.
  - **Per-request switch:** `kv_transfer_params: {"lmcache.max_offload_tokens": 0}` stores nothing. LMCache's cache
    key uses only `lmcache.tag.*` (lmcache/utils.py `CacheEngineKey`), so later requests still find what others stored.
  - **Store by re-sending:** re-sending a request whose prefix is still on the GPU, without the switch and with
    `max_tokens` 1, stored all 13,328 tokens. After the next GPU reset they were hits. That is how `save_context` works.
  - **Speed and size:** reloading 14,896 tokens took 0.18 s against 0.96 s to recompute. Host memory was 76-88 KB per
    stored token.
  - So `lmc` and `lmc_all` use LMCache; the native-offload fallback the user approved is not needed.
- **Docker-in-docker** (20:21 UTC): `s15-dind` on socket 1's CPUs 72-103,176-207 and NUMA node 1, no memory cap, with
  the MSS clamp. The eval image `s15-eval:b4e57657ba6d` was rebuilt (the same inputs as topic 22), and topic 16's
  `CURRENT_TAG` was restored.
- **Pilot tasks:** 48 = 16 per benchmark, seed 2212, from topic 22's reserves (`et_tasks.py pilot`).
- **Oracle** (`et_tasks.py oracle`, 20:28-21:13 UTC, 2,660 s, `-n 16`): 141 of the 144 main and pilot tasks passed,
  and pulling them filled the new docker-in-docker.
  - All 96 main tasks pass.
  - Three pilot tasks don't: `build-cython-ext` and `psf__requests-1921` (reward 0), and `torch-pipeline-parallelism`
    (no reward). They are replaced from the reserves after the pilots (see Changes).
- **The harness's `save_context`** (opt-in: `S15_SAVE_TOOL`, `S15_SAVE_TEXT` s1-s4; lead only; deferred and run before
  the round's `wait`):
  - It re-sends the lead's last request with `max_tokens` 1 and the header `X-KV-Store: 1`.
  - The harness tests (18), topic 18/19/22's end-to-end tests (12) and `test_et.py` (19) pass.

### S1, pilot (2026-11-02, 20:50 UTC on)

- **Runs:** `pilot_none` (GPU 4; the gate at K = 32, then the sweep 16 → 8 → 4) and `pilot_wait` (GPU 5), 32 agents
  each, on the 48 pilot tasks with `-k 2`; `et_chain.py pilot`.
  - Both servers were ready in 115 s; the clients started at 20:52:46 UTC.
  - `pilot_wait` ended at its time limit at 21:58 UTC (1.12 GPU-h).
- **The decision point: KV pressure is far above the bar** (`et_analyze.py pilot`, minutes 10-60; `data/tables/pilot.json`):

  | measure | `pilot_none` | `pilot_wait` |
  |---|---|---|
  | time with KV ≥ 80 % | 99.9 % | 98.6 % |
  | KV use, mean | 96 % | 95 % |
  | requests running / queued at vLLM | 22.3 / 8.4 | 22.2 / 4.0 |
  | GPU prefix-cache hit rate | 3.3 % | 5.3 % |
  | preemptions per minute | 10.3 | 7.4 |
  | requests/h | 1,330 | 1,285 |
  | generated tokens/s | 284 | 303 |
  | output tokens per call (thinking included) | 749 | 708 |

  - The bar was KV ≥ 80 % in at least 30 % of the time and a hit rate at most 80 %. The design stays at 32 agents.
  - For comparison, topic 22's MiniMax at N=32 had a 9 % hit rate and about 1 preemption per minute.
- **Qwen follows the final `wait` prompt live:** 51.6 % of the lead calls whose newest `[server]` line showed ≥ 80 %
  added `wait` (1,284 calls), against 0.2 % below 80 % (446 calls).
- **The active-time limit works:** stops at exactly the task's timeout × 2 (`et_limit.json`, for example 1,800.8 s of
  1,800). Harbor records them as `AgentTimeoutError`, whose message names its own 7,200 s backstop, so the analysis
  reads the real limit from `et_limit.json`.
- **Context overflows:** as in topic 16 they come back as HTTP 400 and the harness compacts.
- **Rough run time:** about 2.6 h for a main run.

### S2, prompt screens (2026-11-02, 22:02 UTC on)

- **Agent screen:** 60 items from `pilot_wait` (42 work, 9 first, 9 final; 41 sessions), drawn and frozen
  (`screen_prereg.sha256`) at 22:02 UTC. Ran 22:06-22:42 UTC on GPU 5, 0.60 GPU-h, 2,640 replies
  (`data/tables/screen.md`). Work replies, item means with 95 % session-cluster intervals:

  | condition | wait at 93 % | wait at 62 % | save with wait at 93 % | save at 62 % | wait-only at 93 % | kept at 93 % |
  |---|---|---|---|---|---|---|
  | `B0` (as recorded) | — | — | — | — | — | 92 % |
  | `W` (wait v5) | 63 % [49, 76] | 5 % [2, 9] | — | — | 2 % | 86 % |
  | `S1` (save with every wait) | 61 % | 3 % | 18 % [9, 28] | 1 % | 5 % | 84 % |
  | `S2` (its own KV rule) | 64 % | 3 % | 0 % | 0 % | 4 % | 86 % |
  | `S3` (KV rule, when pausing) | 61 % | 4 % | 6 % | 0 % | 3 % | 86 % |
  | `S4` (S2 plus an example) | 60 % | 4 % | 3 % | 0 % | 4 % | 89 % |

  - **The final `wait` prompt passes on Qwen.** Live it was 52 % against 0.2 %.
  - **No `save_context` variant passed** (bar: 30 % save-with-wait at 93 %). By the rule I stopped and asked.
- **Revised save screen** (the user's choice, 22:51 UTC; same 60 items; frozen first):
  - **Variants:** `S5` = s1's description with the `[server]` line's cue naming the tool (`kv_cue_save`: "At 80% or
    more: add a wait call and a save_context call to your response."); `S6` = s1 plus a worked example.
  - **Run:** 22:53-23:03 UTC, GPU 5, 0.22 GPU-h, 960 replies.

  | condition | wait at 93 % | wait at 62 % | save with wait at 93 % | save at 62 % | wait-only at 93 % | kept at 93 % |
  |---|---|---|---|---|---|---|
  | `S5` | 77 % [71, 84] | 4 % | **73 % [65, 81]** | 0 % | 7 % | 87 % |
  | `S6` | 58 % | 3 % | 17 % [8, 27] | 0 % | 2 % | 87 % |

  - **`S5` passes and goes into `lmc`:** `S15_SAVE_TEXT=s1`, `S15_STATUS_FORMAT=kv_cue_save`.
  - As with `wait` in topic 19, the line's literal cue moves the model; the tool's own description barely does.
  - So `lmc` differs from `wait` in the cue line as well as the tool. The cue alone also raised waits in replay, from
    63 % to 77 %.
- **Controller screen** (22:52 UTC on, GPU 4): 112 one-minute snapshots of `pilot_none`. By the reference rule, 59
  call for lower, 5 for keep and 48 for raise.
  - At K = 32 the active sessions' contexts (about 699k tokens) overfill the 684k-token pool.
  - The default (xhigh) effort takes 2,200-5,100 output tokens and 38-86 s per call even on an idle server, and some
    calls hit the 8,192-token cap before calling the tool. Low effort takes 880-1,500 tokens and 15-26 s.
  - **Run:** 22:51 UTC-00:45 UTC (its 2 h stage limit, 2,406 replies), resumed 00:46-00:57 UTC for the last 282; 2.10
    GPU-h, 2,688 replies (8 conditions × 112 snapshots × 3 samples).

  | variant | effort | valid calls | agree | when the rule says lower | raise | keep |
  |---|---|---|---|---|---|---|
  | `c1` (the job and the metrics) | default | 91 % | 97 % | 97 % | 100 % | 62 % |
  | `c1` | low | 100 % | 96 % | 98 % | 99 % | 53 % |
  | `c2` (`c1` + capacity arithmetic) | default | 85 % | 97 % | 99 % | 98 % | 50 % |
  | `c2` | low | 100 % | 94 % | 100 % | 93 % | 40 % |
  | `c3` (`c2` + decision cues) | default | 94.6 % | 98 % | 100 % | 100 % | 67 % |
  | `c3` | low | 100 % | 98 % | 100 % | 99 % | 60 % |
  | `c4` (`c3` + the last 10 decisions) | default | **98 %** | **98.5 %** | 99 % | 99 % | 77 % |
  | `c4` | low | 100 % | 97.6 % | 100 % | 99 % | 60 % |

  - **The rule picks `c4` at the default effort** (passing: ≥ 95 % valid and ≥ 70 % agreement; the highest agreement
    wins). The default effort's invalid calls ran out of the 8,192 tokens before calling the tool.
  - Every condition follows the reference direction when it says lower or raise. "Keep" is where they differ, and it
    has only 15 calls per condition (5 snapshots).
  - **Cost of a decision:** the screen's calls ran at about 59 tok/s each (16 in flight). At the default effort a
    call writes 2,242 tokens at the median (p90 5,250), at low effort 1,078 (p90 1,588). The smoke runs decode at
    22-27 tok/s per sequence with 25-30 running, which puts a default-effort decision at about 85-100 s at the
    median and 195-240 s at p90, against the 60-s interval. `agate`'s smoke run measures it live.
- **The pilot's K sweep, for context** (sequential 15-min phases, so confounded with time and task mix):

  | K | requests/min | gen tok/s | GPU hit rate | preemptions/min | KV mean |
  |---|---|---|---|---|---|
  | 32 | 22.2 | 284 | 3 % | 10.3 | 96 % |
  | 16 | 24.8 | 305 | 11 % | 2.8 | 76 % |
  | 8 | 19.4 | 245 | 21 % | 0 | 40 % |
  | 4 | 16.3 | 203 | 46 % | 0 | 12 % |

  Sessions admitted from the gate had lost their cache during the earlier phases, so even at small K the hit rate
  stays low.
- **Oracle for the pilot replacements** (22:52-22:55 UTC): `psf__requests-1921` → `psf__requests-2317` (failed too)
  → `astropy__astropy-13398`; `torch-pipeline-parallelism` → `large-scale-text-editing`; `build-cython-ext` →
  `break-filter-js-from-html`. All 48 pilot tasks and all 96 main tasks pass.

### S3, smoke runs (2026-11-02 23:06 UTC to 2026-11-03 01:57 UTC)

- **The runs:** each setup for a 0.45 h stage (about 21 minutes of agents) on the pilot tasks.
  - On GPU 5, one at a time while the controller screen held GPU 4 (a plumbing check, so the main runs' pairing
    isn't needed): `lmc`, `lmc_all`, `gate8`, `wait`, `none`, `gate16`, `none_r2`.
  - On GPU 4 after the controller screen: `agate` twice, first with the screen rule's pick (`smoke_agate`), then with
    its replacement (`smoke_agate_low`, Changes 01:26 UTC).
  - 3.55 GPU-h over 10 stages, counting the first `lmc` start that failed on the port (Changes, 23:17 UTC). The topic
    stands at 9.75 GPU-h before the main runs.
  - The numbers in `data/tables/et_smoke.md` (`et_analyze.py smoke`) are not comparable between setups: each run is
    cut at 21 minutes, in a different stretch of its tasks.
- **Plumbing, in every run:**
  - every request carried its session (the trial's name), and every call was answered (HTTP 200);
  - the monitor wrote 1 record a second with no gap over 1.5 s, NVML GPM on every record, and LMCache's metrics on
    every record of the offload runs;
  - the proxy's answered calls equal vLLM's request counter inside the monitor's span (`et_analyze.py valid`). The 5-6
    calls that end in the 10-13 s after the monitor stops are why the span matters;
  - the analysis runs end to end.
- **The fixed gates:** at most 8 (`gate8`) and 16 (`gate16`) sessions had a request at vLLM at any moment.
- **`wait`:** 3.2 waits per task. 67 % of the lead calls whose newest line read 80 % or more waited, against 1 % below.
- **`lmc`:**
  - 3.4 saves per task, 223 store-flagged re-sends, 6.5M prompt tokens reloaded from LMCache.
  - The lead call after a save found 71 % of its prompt cached, against 9 % after a wait alone and 60 % after
    other tools.
  - The re-sends were 22 % of vLLM's requests and 28 % of its prompt tokens. Only 64 % of their tokens were cache
    hits: by the time of the save, part of the prefix had already been evicted.
  - So throughput is counted net of the harness's own calls (Changes, prereg).
- **`lmc_all`:** 7.9M prompt tokens reloaded, with no flagged requests.
- **`agate`:**
  - **c4 at the default effort** (`smoke_agate`): 6 of 8 calls valid; 2 timed out at 300 s. One decision lowered K
    from 32 to 16, and 6.5 minutes later 26 of the 32 sessions still held slots. Both led to changes (Changes, 01:26
    UTC).
  - **c3 at low effort** (`smoke_agate_low`): 14 of 14 calls valid, 17-181 s each, rounds a median of about 62 s
    apart.
    - K moved 8 times: 32 → 16 → 8 → 16 → 24 → 28 → 16 → 8 → 16. It lowered K when the GPU hit rate fell to 3-7 %
      with 8-22 preemptions/min, and raised it once the active contexts fit in 55-72 % of the cache.
    - After each cut, the sessions holding a slot fell to the new K within 30-60 s. 42 slots were given up at a
      request's end and 43 after 5 s idle.
- **Not checked by the smoke runs:** pass rates and times, since every run is cut at 21 minutes.

### S4, main runs (2026-11-03, from 01:56 UTC)

Frozen at 01:56:46 UTC (`prereg.md`, `prereg.sha256`); `et_chain.py main` runs the pairs.

- **P1, `none` (GPU 4) | `lmc_all` (GPU 5):** both started at 01:56:53 UTC.
  - `lmc_all` ended at 04:45 UTC (2.80 GPU-h), `none` at 05:08 UTC (3.19 GPU-h). The topic stands at 15.73 GPU-h.
  - Both are valid (`et_analyze.py valid`):

    | run | ended normally | at the time limit | infrastructure errors | answered calls = vLLM's counter | monitor | GPU empty after |
    |---|---|---|---|---|---|---|
    | `none` | 71 | 25 | 0 | 4,157 = 4,157 | 1.00 records/s, max gap 1.0 s | yes |
    | `lmc_all` | 72 | 24 | 0 | 4,648 = 4,648 | 1.00 records/s, max gap 1.2 s | yes |

  - A quarter of the tasks reach the time limit (the task's agent timeout × 2 of active time). Harbor still runs the
    verifier, so they count in the pass rate.
  - The analysis ran on P1 as a check of the code on full runs. It found the NaN samples in Changes, 05:12 UTC. The
    results wait for all four pairs.
- **P2, `wait` (GPU 4) | `lmc` (GPU 5):** both started at 05:08:23 UTC.
  - `wait` ended at 07:50 UTC (2.69 GPU-h), `lmc` at 08:01 UTC (2.87 GPU-h). The topic stands at 21.29 GPU-h.
  - Both are valid:

    | run | ended normally | at the time limit | infrastructure errors | answered calls = vLLM's counter | monitor | GPU empty after |
    |---|---|---|---|---|---|---|
    | `wait` | 75 | 21 | 0 | 3,640 = 3,640 | 1.00 records/s, max gap 1.0 s | yes |
    | `lmc` | 72 | 24 | 0 | 5,054 = 5,054 | 1.00 records/s, max gap 1.0 s | yes |

  - `lmc`'s 5,054 calls include 1,311 `save_context` re-sends.
  - In all four runs so far, every trial's call records line up with its agent's log, so no call loses its labels
    (save, wait, the `[server]` line it saw). The proxy answered 15-21 calls more per run than the agents recorded.
    Each one is the last call of a task that reached its time limit (one per such task): the agent was stopped
    while the call ran.
- **P3, `gate8` (GPU 4) | `agate` (GPU 5):** both started at 08:01:23 UTC, past the freeze check.
  - `agate` ended at 11:16 UTC (3.25 GPU-h), `gate8` at 12:00 UTC (3.97 GPU-h). The topic stands at 28.51 GPU-h.
  - Both are valid:

    | run | ended normally | at the time limit | infrastructure errors | answered calls = vLLM's counter | monitor | GPU empty after |
    |---|---|---|---|---|---|---|
    | `gate8` | 85 | 11 | 0 | 4,444 = 4,444 | 1.00 records/s, max gap 1.0 s | yes |
    | `agate` | 75 | 21 | 0 | 5,066 + 122 controller calls = 5,188 | 1.00 records/s, max gap 1.0 s | yes |

  - `gate8` never had more than 8 sessions holding a slot.
  - **`agate`'s controller:**
    - 122 rounds, all answered, none at the 300-s limit. A call took 75 s at the median, 178 s at p90 and 237 s at
      most, so rounds came 75 s apart at the median.
    - K changed 61 times between 6 and 32 (26 cuts, 35 raises), all in the first 144 minutes; it then stayed at 20 to
      the end.
    - After a cut, the sessions over K gave up their slots as their requests ended. In 4.6 % of the monitor's records
      more sessions held a slot than K allowed. `gate8` and `gate16` never did.
    - **Cuts (26):** the snapshot's prefix-cache hit rate read 3.5 % at the median (8 % or less in 22, at most
      23 %). The active contexts filled 84.5 % of the cache at the median, and 14 snapshots showed preemptions.
    - **Raises (35):** the active contexts filled 58 % at the median (at most 77 %), with 10 sessions waiting at the
      gate. The hit rate still read 19 % at the median: after a cut, the sessions over K give up their slots within a
      minute, so the active contexts shrink before the hit rate recovers.
    - **The decisions lag the changes.** A round's snapshot covers the minute before the round starts, and K changes
      when the call returns, 75 s later at the median. A call longer than the 60-s interval is followed at once, so
      the next snapshot shows the minute before the change. Of the 15 peaks (a raise followed by a cut), 10 were cut
      on a snapshot taken within 0.3 minutes of the raise, before it could show. The other 5 were cut 1.7-3.2 minutes
      after the raise, with the cache 90-100 % full at most and hits at 0-5 %.
- **P4, `gate16` (GPU 4) | `none_r2` (GPU 5):** both started at 11:59:53 UTC.
  - `none_r2` ended at 15:19 UTC (3.32 GPU-h), `gate16` at 15:52 UTC (3.87 GPU-h).
  - Both are valid:

    | run | ended normally | at the time limit | infrastructure errors | answered calls = vLLM's counter | monitor | GPU empty after |
    |---|---|---|---|---|---|---|
    | `gate16` | 82 | 14 | 0 | 4,920 = 4,920 | 1.00 records/s, max gap 1.0 s | yes |
    | `none_r2` | 65 | 31 | 0 | 4,375 = 4,375 | 1.00 records/s, max gap 1.0 s | yes |

  - `gate16` never had more than 16 sessions holding a slot.
- **The main runs:** 25.95 GPU-h, and no stage needed its resume. The topic stands at 35.69 GPU-h.

### S5, analysis and teardown (2026-11-03, 15:53-16:05 UTC)

- **`et_analyze.py valid`:** all 8 runs valid. **`et_analyze.py main`** → `data/tables/et.{md,json}` (3 minutes).
- **`et_cell.py check` after the runs:** every frozen file unchanged (with the 05:12 UTC amendment). `test_et.py`: 24
  tests pass.
- **Post hoc checks** (scratchpad scripts on the run files, not pre-registered; numbers in Results):
  - `lmc`'s recompute, split between the agents' calls and the save re-sends;
  - request latency over working calls only, from `et.json`'s `request_working`;
  - `agate`'s controller: its K changes, the snapshots behind them, and their lag (S4, P3);
  - recompute per call and calls per task in every run;
  - where an LLM call's and a task's time goes: `et_latency.py` → `data/tables/latency.{md,json}` (added after the
    user asked whether the average and median call and task latencies were recorded; they are in `et.md`).
- **Teardown** (per harness_eval `run/dind.md`, as topic 22 did):
  - `s15-dind` stopped and removed (16:01 UTC; no inner containers were left).
  - Its data root `/data/fritz/docker` (150 task images, 159 GB) emptied through a container that mounted only that
    folder: the dind image's own shell, so nothing was pulled. Then both folders were removed (16:05 UTC).
  - The two images this topic pulled or built were removed: `docker:29.4.3-dind` (tagged 2026-11-02 20:21 UTC) and
    `s15-eval:b4e57657ba6d` (20:22 UTC).
  - Not touched: the untagged `alpine` image (tagged 2026-10-12, before this topic) and other users' containers and
    images.
  - Kept: the topic venv `/data/fritz/venv23`, which the analysis and the tests run in.

## Changes

- 2026-11-02 21:15 UTC, `et_tasks.py oracle`: it loaded `tasks_pilot.json` when it started and wrote it back when it
  ended.
  - That undid a `replace` run in between: `build-cython-ext` → `break-filter-js-from-html`, at 20:50, before the
    pilots.
  - The pilot queue (`data/queue/pilot`) kept the replacement, so the pilots run it.
  - Fixed: the oracle reads the file again before it writes. The replacement is applied again after the pilots, with
    the same reserve order, so the file and the queue agree.
- 2026-11-02 21:58 UTC, `et_chain.py pilot`: the running chain had the main runs' rule, which runs a stage stopped at
  its time limit once more, so it started `pilot_wait` again.
  - It was stopped at 22:02 UTC (SIGINT, a clean stop) before its server finished starting: 0.06 GPU-h, logged.
  - The chain process was stopped too. `pilot_none` keeps running on its own to the end of its sweep.
  - Fixed: the pilot is never resumed (`pair(..., resume=False)`).
- 2026-11-02 22:06 UTC, the agent screen: the earlier `[server]` lines of a replayed call now stay as recorded. The
  freeze had capped them at 0.75 (topic 19's `cap`).
  - Topic 19 capped them because MiniMax's histories showed the rule ignored. Qwen followed the rule live, so its
    histories hold waits next to lines at 80 % or more.
  - Capping made those read "the line said 75 % and I waited", which teaches waiting below the threshold. The first
    item waited in 4 of 4 samples at a newest line of 0.62; live, Qwen waited in 0.2 % of the calls below 80 %.
  - The 89 samples so far are set aside (`data/screen/agent/records_cap_superseded.jsonl`), and the screen restarted.
  - The prereg text and its hashes are amended, with the earlier hashes kept in `screen_prereg.sha256`.
- 2026-11-02 23:17 UTC, the `lmc` smoke run: LMCache's server exited at 23:06:44, ten seconds after it started. Its HTTP
  port 50552 was "already in use".
  - The topic's fixed ports (50431-50561) lay inside the kernel's ephemeral range (32768-60999). There a short-lived
    client socket can hold a port; the revised screen had just made 960 connections to that slot's server.
  - vLLM then waited for the missing LMCache server. The stage was stopped at 23:20 UTC (0.19 GPU-h).
  - **Fixed:** all fixed ports are now 31431-31561, below the range.
  - **Fixed:** `et_vllm.sh` now fails at once if LMCache's server exits or never answers its health check, instead of
    starting vLLM.
  - The docstring examples in `et_hw.py` and `et_proxy.py` (hashed in `screen_prereg.sha256`) changed only in those
    ports.
- 2026-11-03 01:00 UTC, `et_controller.py`: the loop slept 60 s *after* each round, so a round came every 60 s plus
  the call's own time. The screen's calls take 15-26 s at low effort and 40-90 s at the default.
  - **Fixed:** the 60 s count from the previous round's start, so a round longer than 60 s is followed at once. That is
    the user's "a controller call every 60 s".
  - A new test covers it (`test_controller_rounds_start_every_interval`; `test_et.py` has 21 tests, all passing). No
    run used the controller before the fix: the screen sends its own requests, and `agate`'s smoke runs after it.
- 2026-11-03 01:26 UTC, `agate`'s controller: **c3 at low effort instead of the screen rule's c4 at the default
  effort.**
  - The rule picked c4 at the default effort: 98 % valid calls and 98.5 % agreement on the replayed snapshots, ahead
    of c3 and c4 at low effort, both 100 % and 97.6 %.
  - It failed live in the `agate` smoke run (`smoke_agate`, 01:00-01:23 UTC, GPU 4). Once the KV cache filled, the
    server decoded about 12 tok/s per sequence, against about 59 in the screen.
    - A default-effort decision writes 2,242 tokens at the median and 5,250 at p90.
    - So 2 of 8 rounds timed out at the 300-s call limit, and the one that lowered K (32 → 16) took 186 s.
    - That is 75 % valid calls, against the screen's 95 % bar, and a decision every 3-5 minutes against the 60 s
      the user chose.
  - **The fallback follows the same rule** with that condition out. c3 low and c4 low tie at 41/42, and the tie goes to
    the lower variant number.
    - c3 low writes 1,155 tokens at the median, 2,530 at p99 and 3,344 at most. That is about 100-165 s per call at
      the loaded server's speed, with rare timeouts, and faster once K is lower.
  - It is checked live in `smoke_agate_low` (GPU 4, from 01:26 UTC). `agate` runs in pair P3, after P1 and P2.
- 2026-11-03 01:26 UTC, the session gate: **when K is lowered, a session over the limit now gives its slot up as soon
  as its request ends.** Before, every session kept its slot until it was 5 s idle.
  - In `smoke_agate`, 6.5 minutes after K fell from 32 to 16, 26 of the 32 sessions still held slots and 6 more waited.
    Under this load an agent sends its next request within seconds of the last, so a busy session is never 5 s idle,
    and a lower K took effect only as tasks ended.
  - This keeps both of the plan's rules for K: "K changes apply at once", and "lowering K never evicts an active
    session" (no request is cut and no cache is dropped).
  - The sessions that give up their slot queue behind the waiting ones (first come, first served). Their gate time
    is left out of their time limit, as for any session.
  - The fixed gates (`gate8`, `gate16`) never hold more sessions than K, so this changes nothing for them. A new
    test covers it (`test_gate_lowered_k_takes_slots_back_at_request_ends`; 23 tests pass).
  - The analysis now reports sessions holding a slot next to K (`session_profile`).
- 2026-11-03 05:12 UTC, `et_analyze.py`: **the GPU utilization means leave out samples that NVML returned as NaN.**
  - The analysis, run on P1 as a check, showed `none`'s HBM-bandwidth utilization as NaN. 2 of `none`'s 11,406
    monitor records and 1 of `lmc_all`'s 9,977 hold a NaN for it; no other field holds one. A single NaN made the whole
    mean NaN.
  - **Fixed:** each mean is over the window's finite samples (`none` 49 %, `lmc_all` 55 %). A record without the field
    is now left out instead of counted as 0; no P1 record lacks one.
  - A new test covers it (`test_gpm_means_skip_nan_samples`; 24 tests pass).
  - The runs don't import the analysis, so P1 and the running P2 are unaffected. `prereg.sha256` is amended for
    `et_analyze.py` and `test_et.py`, with their earlier hashes kept at its end. `et_cell.py check`: all unchanged, so
    P3 and P4 start as planned.

## Data inventory

Everything is under `data/` (7.0 GB, not in git).

| run | what | analyzed |
|---|---|---|
| `none`, `none_r2`, `gate8`, `gate16`, `agate`, `wait`, `lmc`, `lmc_all` | the 8 main runs | `et.md`, `et.json`; post hoc `latency.md`, `latency.json` |
| `smoke_*` (`lmc`, `lmc_all`, `gate8`, `wait`, `none`, `gate16`, `none_r2`, `agate`, `agate_low`) | the smoke runs, about 21 minutes each on pilot tasks | `et_smoke.md`, `et_smoke.json` |
| `pilot_none`, `pilot_wait` | the pilots: 48 pilot tasks with `-k 2`; `pilot_none` with its K sweep | `pilot.json` |
| `screen_agent`, `screen_agent2`, `screen_controller` | the prompt screens' server stages (the replies are in `data/screen/`) | `screen.md`, `screen.json` |

**Files:**
- **Per run:**
  - `data/jobs/<run>/`: Harbor's job. Per trial: `result.json`, `trial.log`, the verifier's output, and under
    `agent/s15/`: `calls.jsonl` (every model call with its request), `events.jsonl`, `history.json`, `child.log`, and
    `et_limit.json` (the active-time limit). Not in git or the file mirror: on Drive as one `.tar.zst` per run
    ([research/DATA.md](../DATA.md)).
  - `data/runs/<run>/`:
    - `cell.jsonl`: the times, the setup, the GPU and the server flags;
    - `harbor.txt`: Harbor's console;
    - `hw_*.jsonl`: the monitor, once a second: NVML and GPM, vLLM's `/metrics`, LMCache's metrics, the gate's state;
    - `proxy_requests.jsonl`: every request through the proxy (session, gate times, status, tokens, the store flag);
    - `gate_events.jsonl`: every slot taken and given up, and every K change;
    - `controller.jsonl` (`agate` and its smoke runs): every controller round, with its snapshot and reply;
    - `lmcache_*.log` (`lmc`, `lmc_all`): LMCache's server;
    - `wt_ext.jsonl`, `pin.jsonl` (CPU resets), `proxy.log`, `hw.log`.
  - `data/runs/<run>.stage.log` and `data/servers/<run>_<UTC start>/`: the stage's and vLLM's logs.
- **The setups:** `data/env/<setup>.env`, the `S15_*` settings the agents get.
- **The screens:** `data/screen/`: `items.json` (the 60 agent items), `snapshots.json` (the 112 controller snapshots),
  `winners.json`, and each screen's replies under `agent/`, `agent2/` and `controller/`. The superseded capped agent
  screen is `agent/records_cap_superseded.jsonl`.
- **S0:** `data/s0/` (LMCache's feasibility check, `feas_lmcache.json`, with its logs).
- **Tasks:** `data/tasks_pilot.json` (the pilot draw and its replacements), `data/queue/`, `data/dataset/`, and the
  oracle logs `data/oracle_*.txt` and `oracle_run*.log` (job in `data/jobs/oracle/`). The main tasks are topic 22's
  (`22_wait_bench/data/tasks.json`, queue `22_wait_bench/data/queue/main`).
- **The chain:** `data/runs/chain.log`, `chain.out`, `chain_pilot.out`.
- **GPU-hours:** `data/gpu_hours.jsonl` (27 stages, 35.69 in all).
- **Freezes:** `prereg.sha256` (the main runs; amended 05:12 UTC with the earlier hashes at its end) and
  `screen_prereg.sha256` (the screens).
- **Code:** `et_*.py` and `et_*.sh` in this folder (`et_latency.py` is post hoc and not frozen); the pinned harness in `vendor/s15/` (`SNAPSHOT.json`); the patched
  template `qwen3_8_append_only.jinja`. `home/` is the eval container's home folder.
- **Software:** `/data/fritz/venv23` (vLLM 0.31.0 and LMCache 0.5.6). Run the tests with
  `/data/fritz/venv23/bin/python -m pytest -q test_et.py`.
