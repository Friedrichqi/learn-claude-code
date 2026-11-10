# Topic 23: can harness-called efficiency tools help Qwen3.8-27B serve 32 benchmark agents on one H100?

## Context

**The ask (2026-11-02):**
- Rerun topic 22's idea with Qwen3.8-27B: the harness decides by itself whether to call external efficiency
  tools.
- Setups: vanilla; fixed session gates (8 and 16 sessions); an adaptive gate, where the LLM picks the session
  limit from hardware metrics; the final `wait` tool; and LMCache KV offload to DRAM as an LLM-called tool.
- First, engineer the tool descriptions so the model understands and uses them.
- Benchmark: topic 22's 96 tasks.

**Why:** topic 22's post hoc check showed why `wait` (MiniMax, 4×H100) didn't raise throughput:
- the KV cache held about 15 contexts for 32 agents;
- a sleeping agent's cache was evicted anyway: 9–14 % hits after a wait, against 32–50 % after other tools;
- wait-only calls recomputed what the waits saved;
- the batch shrank as much as the requests sped up.

The user's hypothesis: offloading the KV to DRAM lets a paused session's cache survive.

**User decisions (2026-11-02):**
- **Runs:** 7 setups, effects separated, including an LMCache "save every request" reference.
- **Adaptive gate:** a controller call every 60 s.
- **If LMCache can't serve the model:** fall back to vLLM's native CPU offload.
- **Resources:** unlimited DRAM; two runs at once on **GPUs 4 and 5**, with CPUs on the same socket (socket 1:
  52-103, 156-207, NUMA node 1, confirmed by `nvidia-smi topo`).

**Facts found while planning:**
- **Model:** `/data/fritz/hf/hub/models--Qwen--Qwen3.8-27B-FP8` (29 GB). It is hybrid: 48 Gated DeltaNet layers plus 16
  attention layers (4 KV heads × 256). That makes 64 KiB/token of KV, plus about 3.2 MB of DeltaNet state per layer.
- **Topic 16 served it** on vLLM 0.31.0, TP=1, through the same Harbor harness:
  - 784-token cache blocks and 678,711 tokens of KV on one GPU;
  - prompts append-only 99 %, pass@1 0.79 / 0.97 / 0.50 (SWE / Pro / TB subsets);
  - at about 10 agents at once: median trial 13-23 min, 21-60 calls, 18-33k output tokens, final prompts 21-31k
    tokens.
  - Flags (`harness_eval/serve/models.yaml:21-32`): `--tool-call-parser qwen3_coder --reasoning-parser qwen3
    --language-model-only --enable-prefix-caching --enable-mamba-shared-prefix-checkpoint`.
  - s15 keeps thinking blocks, and the vendor template keeps them (preserve_thinking).
- **The template has a known defect:** it joins text parts with no separator (`chat_template.jinja:30`), so the
  `[server]` line would run into the preceding text. The fix is a patched template, as topic 19 did for MiniMax.
- **LMCache:**
  - It is not installed. vLLM 0.31's bundled LMCache connectors refuse hybrid models (`lmcache_mp_connector.py:77-81`).
    Topic 09 hit the same with LMCache 0.3.6.
  - LMCache's own multiprocess connector lists Qwen3.8-27B as validated on one GPU (docs.lmcache.ai recipes). Launch:
    `lmcache server --chunk-size 784 --separate-object-groups --l1-size-gb N --eviction-policy LRU`; vLLM with
    `--mamba-cache-mode align` and `kv_connector_module_path` set to LMCache's connector.
  - It has no per-request store switch or pin.
- **vLLM's native offload** (`--kv-offloading-backend native --kv-offloading-size N`) supports hybrid models. It also has
  per-request `kv_transfer_params` switches: `max_offload_tokens: 0` skips storing, `max_load_tokens: 0` skips loading
  (`offloading/scheduler.py:388-416`). vLLM's Anthropic endpoint, which s15 uses, accepts `kv_transfer_params`
  (`anthropic/protocol.py:195`).
- **Topic 11, Qwen3-32B on one H200:**
  - a session gate at 8 gave 2.8–3.1× throughput;
  - native CPU offload gave 2.8× at N=16, but only 1.1× at N=32, once the CPU tier no longer held the working set.
- **Hardware counters:** NVML GPM is readable without root on these H100s: SM utilization, SM occupancy,
  tensor-core utilization, HBM bandwidth utilization, PCIe TX/RX. vLLM `/metrics` and LMCache `/metrics` add KV,
  queue, hit-rate and offload numbers.
- **The existing session gate** (topic 11's `MU_GATE`, inside the vLLM scheduler) reads K once at start, and s15
  never sends `X-Session-ID`. An adaptive K needs a new path.

## Setups: 8 runs, each the 96 tasks with 32 agents at once on one H100

| setup | what is on | who decides |
|---|---|---|
| `none` | vanilla s15: no pacing tools, no status line | — |
| `gate8`, `gate16` | session gate, K fixed at 8 / 16 | fixed |
| `agate` | session gate. Every 60 s a controller call shows the model the hardware metrics and the gate's state; the model calls `set_session_limit(k)` (1–32; starts at 32) | the model |
| `wait` | topic 22's final prompt (`S15_WAIT_TOOL=1 S15_WAIT_TEXT=v5 S15_SERVER_SECTION=wait_kv S15_STATUS_LINE=1 S15_STATUS_FORMAT=kv_cue S15_WAIT_KV=0.8`) | the model |
| `lmc` | `wait` plus a `save_context` tool. KV offload stores only on that tool's request; loads are always on | the model |
| `lmc_all` | KV offload stores every request; no pacing tools (reference: offload without decisions) | — |
| `none_r2` | a second `none` run, filling the schedule's 8th slot (an A/A check of run-to-run noise) | — |

**Session gate:**
- All 32 agents run; at most K sessions may have requests at the server. A session becomes active first-come
  first-served and frees its slot after 5 s idle with nothing in flight, as in topic 11.
- Time blocked at the gate doesn't count toward a task's time limit.
- `agate`'s controller calls bypass the gate.

**`save_context`:**
- When called, the harness re-sends the session's last lead request (same system, tools and messages; a prefix of
  its next request) with `max_tokens` 1 and the store flag. That copies its KV to DRAM, and the next request reloads
  it on a GPU miss.
- It runs before the round's `wait`.
- Its text comes from the screen (S2).

**Offload backend:** LMCache for both `lmc` and `lmc_all`, if S0 shows that (a) it serves this model correctly and
(b) a store-only-when-flagged wrapper works. Otherwise vLLM native offload for both, labeled as such.

## Design

- **Tasks:** `22_wait_bench/data/tasks.json` `main` (96 = 32 SWE-bench Verified + 32 SWE-bench Pro + 32 Terminal-Bench
  2.1, seed 2211), in the same queue order (topic 22's `data/queue/main` links; read only).
  - Pilot and screens use a fresh draw: 48 tasks (16 per benchmark, seed 2212, none of the main 96 or smoke 12),
    oracle-checked.
- **Agents:** Harbor 0.24 via topic 22's pattern.
  - `--n-concurrent-agents 32 -n 48`; per-task limit = task.toml agent timeout × 2 of active time. Harbor's own
    multiplier is raised as a backstop.
  - At the limit `et_agent.py` cancels the child and raises `asyncio.TimeoutError`, extending its deadline by the
    session's gate-blocked seconds from the proxy. Harbor turns that into the same `AgentTimeoutError` as its own
    timeout and still runs the verifier (Harbor 0.24 `trial/trial.py:659-662`, `trial/single_step.py:83-86`, read
    from the uv cache).
  - The 64 MiB exec-output cap (`wb_cap.py`) stays.
  - **The per-call cap moves to the proxy.** The child's `call_timeout` (`s15_child.py:168`, 1,800 s) would count
    gate waits. So in every setup it is raised to 4 h, and the proxy cuts the time a call spends at vLLM at 1,800 s,
    keeping topic 22's cap on server time.
  - Topic 16's Qwen agent settings: 250 lead calls, window 131,072, `max_tokens` 32,000 → 64,000, the model's
    sampling defaults (temperature 1.0, top_p 0.95, top_k 20), thinking on at the default effort.
  - If the pilot projects a run over 5 h, I'll come back with a medium-effort option before the screens.
- **Server, identical in every setup except the offload connector:** vLLM 0.31.0, TP=1.
  - Flags: `--served-model-name qwen3.8-27b --gpu-memory-utilization 0.92 --max-model-len 131072 --seed 0
    --enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser qwen3 --language-model-only
    --enable-prefix-caching --mamba-cache-mode align --enable-mamba-shared-prefix-checkpoint
    --enable-prompt-tokens-details --max-num-seqs 64 --chat-template <patched template>
    --middleware wt_server_ext.Middleware`.
  - No MTP, KV in bf16, one fresh server per run.
  - If LMCache works, all 8 runs use the topic venv with LMCache installed, so the software is identical.
- **Gate proxy (`et_proxy.py`, new, in every setup so every run has the same hop):** an asyncio reverse proxy
  between the agents and vLLM.
  - Sessions come from the `X-Session-ID` header that `et_child.py` adds.
  - The gate holds a session's requests until it has a slot. It keeps per-session blocked seconds
    (`GET /gate/session/<id>`) and takes runtime K (`POST /gate/k`).
  - For the offload setups it injects the store policy into `kv_transfer_params`: all, or only flagged requests.
  - It logs every request and gate event as JSONL.
  - Pass-through when the gate is off.
- **Controller (`et_controller.py`, `agate` only):** every 60 s it sends one OpenAI-style request with the tool
  `set_session_limit(k, reason)`. `tool_choice` is required, or auto with a parse check if required interferes
  with thinking; the screen decides. Without a valid call, K stays.
  - It shows the last 60 s and the previous 5 minutes, plus the last 10 decisions and their outcomes.
  - The prompt and reasoning effort come from the S2 screen.
  - K changes apply at once. Lowering K never evicts an active session; it leaves when idle or done.
- **Hardware monitor (`et_hw.py`, every run, 1 Hz):**
  - NVML/GPM: SM util, SM occupancy, tensor util, HBM-bandwidth util, PCIe TX/RX, power, clocks, HBM used.
  - vLLM `/metrics`: KV use, running/waiting (by reason), prefix-cache and external hit tokens, computed vs cached
    prompt tokens, preemptions, gen tok/s, ITL/TTFT/queue time, tokens per step, offload stores/loads.
  - LMCache `/metrics`: L1 use, lookup hits, stores, retrieves, evictions, GPU↔CPU GB/s.
  - Gate state: K, active/waiting sessions, the active sessions' context tokens against KV capacity.
  - It writes JSONL for the analysis and serves `/hw/summary?window=60` to the controller.
- **The session header:** `et_child.py` wraps the client call and adds `X-Session-ID` (the trial name). This is how
  `s15_child.py:184-186` adds `X-Request-Id`, so the header needs no harness change.
- **Harness change:** opt-in, default off, in `s15_integrated_harness/`, then pinned in `23_*/vendor/s15/`
  (`wb_vendor.sh` pattern).
  - `save_context`: schema, `S15_SAVE_TOOL` / `S15_SAVE_TEXT`, handler, `pacing_handlers` entry, deferred before
    `wait`. It follows the `wait` pattern in `hold_runtime.py` (`PACING_TOOLS`, `DEFERRED_TOOLS`, `run_*`) and
    `code.py:1257-1278`.
  - `et_child.py` adds the new tool to the allowed list, as `wb_child.py:26-38` did for `wait`.
- **Placement** (socket 1 only; `numactl --membind=1`; `wb_cell.pin_tree` re-pins every 2 s against numad):
  - vLLM + LMCache on GPU 4: CPUs 52-57,156-161. On GPU 5: 58-63,162-167.
  - Proxies, controllers, monitors and the two Harbor eval containers (64 s15 children): 64-71,168-175.
  - Docker-in-docker task containers: 72-103,176-207, `--cpuset-mems 1`, no memory cap. Topic 22's N=32 runs averaged
    a host load of 8-14 (p95 19-25). That leaves room for two runs on 64 logical CPUs except at peaks.
  - S1 records the CPU load of each set and moves cores between them if one set saturates.
  - The two offload runs never overlap: GPUs 4 and 5 sit on adjacent PCIe buses (84:00, 85:00).
- **Schedule (pre-registered):** 4 pairs, two runs at once. Each key contrast runs in the same pair, so both runs
  share the time and the CPU load. The two offload runs stay in different pairs.
  - P1 `none` (GPU 4) | `lmc_all` (GPU 5): does offload by itself help?
  - P2 `wait` (4) | `lmc` (5): does offload fix `wait`'s eviction problem?
  - P3 `gate8` (4) | `agate` (5): does the LLM-set K beat a fixed one?
  - P4 `gate16` (4) | `none_r2` (5): `none_r2` lands on the other GPU, so `none_r2` − `none` measures run, time and
    GPU noise together.

## Stages

**S0, build and feasibility** (CPU; about 3 GPU-h on GPUs 4-5):
1. **Folder:** `research/23_efficiency_tools/`. Its scripts reuse topic 22 by import where possible: `wb_analyze`
   helpers, `wb_cap`, `wb_agent`, `pin_tree`.
   - `rd_serve.run_stage` runs as two separate processes, one per GPU. Each overrides its module globals in
     `configure()`: `GPUS` [4] or [5], `SERVER_ENV` `CUDA_VISIBLE_DEVICES`, CPU sets, VLLM, `CONFIGS`, `MODEL`, no
     cap. The GPU-empty check and the GPU-hour log then cover only that GPU.
   - `et_cell.py` starts the LMCache server before `run_stage`. Its client command starts the proxy, the monitor and
     the Harbor job.
2. **Topic venv `/data/fritz/venv23`** (uv 0.12; /data has 15 TB free): /data/fritz/venv's packages plus LMCache.
   - LMCache comes from a CUDA 13 / torch 2.13 wheel if one exists. Otherwise it is built with
     `--no-build-isolation` against the venv's torch, using the venv's own `nvidia/cu13/bin/nvcc`; the host has no
     system CUDA toolkit.
   - /data/fritz/venv stays untouched.
   - Note the existing transformers 5.19 vs vLLM's `<5.18` pin.
3. **LMCache check on GPU 5:**
   - Serve plus the LMCache server.
   - A 3-turn conversation with `/reset_prefix_cache` between turns: external hit tokens must appear, with a
     score-level output check.
   - The store-only-when-flagged wrapper: unflagged requests store nothing.
   - A flagged re-send of a request whose prefix is still cached on the GPU stores that whole prefix.
     `save_context` depends on this. vLLM's native connector does it in its request-level policy
     (`offloading/scheduler.py:1181-1184`); LMCache has to be tested.
   - DRAM bytes per token, GPU↔CPU GB/s, reload against recompute time.
   - Chunk size: 784, or a multiple if the DeltaNet state per chunk makes DRAM per token too high.
   - Start-up time with a large L1.
   - If any of these fails: the same checks on native offload, and native for both offload setups.
4. **Serving check on GPU 4:**
   - The patched template (`qwen3_8_append_only.jinja`: text parts joined by a newline) must be append-only on
     topic 16's recorded Qwen requests (CPU render test).
   - 784-token blocks, `max-num-seqs 64` accepted, the qwen3_coder tool calls parse through s15.
5. **Code and tests:** `et_proxy.py`, `et_controller.py`, `et_hw.py`, `et_agent.py` / `et_child.py` (session header,
   active-time limit), `et_cell.py` / `et_chain.py` / `et_vllm.sh` / `et_lmcache.sh`, harness tools.
   - `test_et.py` with a fake vLLM (as `wb_fake.py`). It covers: the gate never exceeds K; idle release; runtime K;
     blocked-time accounting; the store flag reaching `kv_transfer_params`; controller parsing and bounds; the
     summary math; the analysis on synthetic runs.
6. **Docker-in-docker on socket 1:** rebuild with `docker:29.4.3-dind` and the MSS clamp, build `s15-eval` with
   `docker/build.sh` (restore topic 16's `CURRENT_TAG` with git checkout), then pull images through a fresh oracle
   folder for the 96 + 48 tasks (~25 min).

**S1, pilot** (~2 h). Each pilot job runs the 48 pilot tasks with `-k 2`, so 32 agents stay busy, and is stopped
at its time.
- **GPU 4:** `none` at N=32 for 60 min. It gives the vanilla call, token and timeout stats and the run-time
  projection. Then gate K = 16, 8 and 4 for 15 min each, which gives metric snapshots for the controller screen and
  tests the proxy under load.
- **GPU 5:** `wait` at N=32 for 60 min. It records Qwen histories that carry real `[server]` lines, the items for the
  screens. It is also a first live look at whether Qwen follows the rule.
- **Decision point: is there KV pressure at N=32?**
  - Topic 16's Qwen trials, at about 10 agents, ended at 21-31k-token prompts. Thirty-two such contexts are about
    0.7-1.4× the 678k-token pool, so the pressure may be mild.
  - Gates and offload can only help where contexts get evicted. If the `none` pilot has the KV at ≥ 80 % for less
    than 30 % of the time, or a prefix hit rate above 80 %, I stop and come back with options: more agents (48 or
    64), or a smaller KV pool (a lower `--gpu-memory-utilization`, as topic 11 did).

**S2, prompt engineering** (GPUs 4-5, ~2-3 h): topic 19's replay method (`ef_kvwait2.py` + `rd_cf.Runner`), ported to
Qwen (renderer, parser for the `<tool_call><function=…>` XML, model guard).
- **Items:** about 60 lead calls from the GPU-5 pilot (work / start / final strata, at most 2 per session), 4 samples
  each.
- **Levels:** the newest line at KV 0.62 and 0.93; earlier lines capped at 0.75, as in topic 19's `RCcap`.
- **Wait-only replies don't count against a variant:** the user ruled them acceptable on 11-01.
- **`wait` v5 on Qwen:**
  - Rule: ≥ 30 % of work replies wait at 0.93, ≤ 10 % at 0.62, and the recorded action is kept within 10 points of
    the as-recorded request.
  - If v5 misses, I report and ask before changing the user's final prompt.
- **`save_context`:**
  - 4 description variants; vary the name, and whether it is tied to the wait rule.
  - Same levels. Pass: ≥ 30 % save-with-wait at 0.93, ≤ 10 % at 0.62, no rise in wait-only replies against v5
    alone.
  - Pick the highest-rate passer; ties go to the shorter text.
- **Controller:**
  - 4 prompt variants: plain; plus capacity arithmetic; plus decision cues; plus K→outcome history. Each at the
    default and low effort.
  - Scored on pilot/sweep snapshots against a reference that topics 11 and 18 support: lower K when the active
    contexts exceed 0.9 × KV capacity, or the hit rate is under 50 % with preemptions; raise it when the active
    contexts plus the next waiting session fit in 0.8 × capacity and sessions are waiting; else keep.
  - Pass: ≥ 95 % valid calls and ≥ 70 % direction agreement.
- **Selection:** the rules are fixed in `screen_prereg.md` before the screen runs. If no variant passes, I stop and
  report.

**S3, smoke** (~1.5 h): every setup for 15 min on pilot tasks, two at a time. Checks:
- headers and sessions;
- the gate holds K and applies runtime K;
- controller decisions are applied;
- `save_context` stores and a later request reloads (external hit tokens > 0);
- waits run and the monitor covers the run;
- the analysis runs end to end.

**Then** `prereg.md` + `prereg.sha256` freeze the tasks, schedule, prompts, code and measures.

**S4, main** (4 pairs × ~3-4 h; about 28 GPU-h): `et_chain.py` runs P1-P4 with `--hours 5` per run, resumable. After
each run:
- all 96 tasks reach an end state, with infrastructure errors under 2 %;
- call counts match vLLM's counter within 1 %;
- the monitor covers the run;
- GPUs 4-5 are empty afterwards.

**S5:** analysis, README (results, caveats, Process, Changes, data inventory), the `research/README.md` row, memory.
Then teardown: stop docker-in-docker, and remove only the images and folders this topic pulled or created.

## Measures (`et_analyze.py` → `data/tables/et.{md,json}`; built on `wb_analyze` helpers)

- **As topic 22, over the full-N window:**
  - request latency avg/p50/p95/p99, as the agent sees it, gate wait included; plus vLLM's server-side time;
  - entry end-to-end avg/p50/p95/p99 over the 96 tasks; plus active time and gate-blocked time;
  - vLLM requests/h, tok/s, gen tok/s.
- **New:**
  - working requests/h: no wait-only, store or controller calls;
  - tasks/h and makespan;
  - pass rate.
- **Mechanism:**
  - KV use; GPU prefix hits; offload hit tokens; recomputed prompt tokens per task; preemptions;
  - the batch, and time in the batch;
  - SM / tensor / HBM-bandwidth utilization;
  - DRAM-cache use and evictions.
- **Decisions:**
  - K over time;
  - waits and `save_context` calls, on the rule and off it;
  - the hit rate of the call after a wait or a save.
- **Contrasts:** each setup against `none`, tasks paired, 2,000-draw bootstrap, throughput in 10-min blocks.
  `none_r2` − `none` is the noise yardstick.
- **Limit of the design:** one run per setup. Pairs share CPUs, so cross-pair comparisons include partner effects.

## Resources and rules

- **GPUs and claims:** GPUs 4-5 only. As in topic 22, you enter the claims (Yijiahao), and stages skip the sheet
  check unless you ask for it.
- **Budget:** no GPU-hour cap. The estimate is about 40 GPU-h, and I'll report if the projection passes 60.
- **CPU and memory:** only socket 1 (52-103, 156-207) and NUMA node 1. No sudo, and no changes to numad.
- **Rules:**
  - no commits unless asked;
  - never call a setup an "arm";
  - every setup name glossed;
  - no edits to other topics' frozen files, and /data/fritz/venv untouched;
  - remove only what this topic pulled or built, never prune;
  - no conclusions about time from single-session timing.

## Verification

- `test_et.py`, `22_wait_bench/test_wb.py`, topic 19's tests and the harness tests all pass. Every S0 feasibility
  check is recorded in the README.
- The smoke checks hold for all 8 setups.
- The analysis reproduces from files, and one run's percentiles are recomputed by hand.
