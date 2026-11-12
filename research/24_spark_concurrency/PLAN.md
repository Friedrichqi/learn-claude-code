# Topic 24: Qwen3.8-27B (BF16, vLLM 0.32) on the DGX Spark with 1 to 32 concurrent agent users — plan

Approved 2026-11-09. Changes after approval are logged at the end of the README, not here.

## Context

- **What exists.**
  - `~/dgxspark-profiling/exp02_vllm_um` measured this Spark under synthetic prompts (random token ids, prefix caching off), without hardware counters.
  - Topics 22 and 23 measured real agent sessions, but on an H100 with the FP8 checkpoint.
  - Nothing yet measures real agent sessions on the Spark.
- **Question.** As the number of concurrent agent users N grows from 1 to 32 on this one box, what happens to throughput and latency, and how busy are the CPU, the GPU and the shared DRAM?
- **Decisions made with you.**
  - Live on the Spark: the model, the agents and the agents' tools all run here.
  - Task pool: 16 tasks in all, 8 from SWE-bench Lite (not in Verified) and 8 from SWE-bench Verified, only tasks with a native arm64 image. At N = 32 each task runs as two independent sessions. SWE-bench Pro and Terminal-Bench are dropped (x86-only images).
  - Sessions run up to 40 calls, so they include the snip phase described below.
  - sudo may be used to turn on hardware counters.
  - About 2 days of machine time. Pass rate does not matter; a cell stops once its numbers are established, not when tasks finish.
- **Facts that shape the design** (each checked on this machine or in the source).
  - **Hardware counters need sudo, and cover the GPU and the CPU cores, not the memory controller.**
    - With sudo, two sets open up. The GPU's own counters (L2 traffic, tensor-core and SM activity) can be sampled continuously by Nsight Systems; it lists this GPU as supported but refuses without root (`RmProfilingAdminOnly=1`). Each CPU core's counters (cycles, instructions, memory stalls, cache misses) can be read by `perf`, which `perf_event_paranoid=4` blocks for normal users.
    - Even with sudo there is no counter at the memory controller. The firmware describes no uncore counter unit (no APMT or MPAM ACPI table, no DSU, CMN or DMC device), and this GPU has no `dram__` counters. Total DRAM traffic is therefore the sum of what the GPU and the cores report. exp02's calibrated latency probe stays as an independent check.
    - NVML reports GPU power only; module and memory power return "not supported".
  - **The harness does not stream**, so the client cannot see TTFT. vLLM 0.32.0 loads stat-logger plugins, which gives exact per-request timings on the server. Tested in scratch: the plugin loads without an install and its fields exist.
  - **Decode is slow here:** about 4.5 tokens/s per user and about 660 tokens per call, so about 2.5 minutes per call.
  - **A session has two phases** (`s15_integrated_harness/code.py:2564`, `snip_compact`).
    - *Append phase*, about the first 25 calls: the conversation only grows, and each call reuses the cached context.
    - *Snip phase*, from the 51st message on: every call archives the middle of the conversation, which changes the prompt near its start. Each call then recomputes most of its prompt.
    - On this box the snip phase is the expensive one: prefill is not shared between users the way decode is.

## Choices I made that you may want to change

1. **N = 1, 2, 4, 8, 16, 32**, then a repeat of 8, then 24 and 12, as time allows.
2. **Thinking stays at the model's default (xhigh)**, as in topics 16, 22 and 23. Medium would be faster but not comparable.
3. **Prefix caching is on and the KV pool is 36 GiB.** exp02's main runs had prefix caching off and 24 GiB.
4. **A cell stops after one full session per user.** No early stop: the last calls of a session are the expensive ones.
5. **Counters run for the whole chain:** GPU at 100 samples/s, CPU once per second per core. Their overhead is measured before the main cells.

## Definitions

- **User**: one live s15 agent session on one task: model call, tool run in the task's container, next call. When a session ends, the same user starts the next task, so N sessions are always active.
- **Cell**: one run at one N, with a fresh vLLM server.
- **Window**: from the moment the N users start until the last of their first sessions ends. All numbers are over the window, and also per phase.
- **Phase**: a call is in the append or the snip phase by whether its session has been trimmed yet. For numbers over time (tokens/s, DRAM, CPU, GPU) the cell has an *append period*, before any first session is trimmed, and a *snip period*, after all of them are and until the first one ends. The time between the two counts only in the whole-window number.
- **Step**: one pass of the vLLM engine. A decode step produces one token for every running request. A step can also carry a chunk of some request's prompt (prefill), and it then takes 1.4–2.2 s, not 0.2–0.5 s.

## What is measured

Each metric names its source by letter. All sources stamp CLOCK_MONOTONIC, or wall time paired with it, so they join without fitting.

| Letter | Source | Rate | sudo |
|---|---|---|---|
| **R** | `requests.jsonl`: one line per finished request, written inside vLLM by a small plugin | per request | no |
| **I** | `steps.jsonl`: one line per engine step, same plugin (requests and tokens in the step, step time, KV pinned, token gaps) | per step | no |
| **M** | vLLM `/metrics` | 1/s | no |
| **H** | the harness's `calls.jsonl` and `events.jsonl`: one line per model call and per trim | per call | no |
| **G** | GPU counters: Nsight Systems GPU-metrics sampling | 100/s | yes |
| **C** | CPU counters: `perf stat`, every core | 1/s | yes |
| **S** | exp02's `sampler.py`, unchanged: per-core busy, memory, NVML | 10/s | no |
| **P** | exp02's latency probe (umbench) on core 8 | 100/s | no |
| **X** | a new sampler: CPU time by group, pressure (PSI), containers, NVML energy, temperatures, disk | 1/s | no |

Metrics in bold form the headline tables.

### 1. Latency of one model call

| Metric | Unit | What it tells you | How it is computed | Source |
|---|---|---|---|---|
| **Call latency** | s | What the agent waits for one reply | send to full reply, as the harness sees it | H |
| **TTFT**, time to first token | s | How long before the model starts answering. Server-side: the non-streaming client never sees it | first token − arrival of the tokenized prompt at vLLM's engine: queue + prefill | R |
| Queue time | s | Whether the server is over capacity | first admission − queued | R |
| Prefill time | s | Cost of the prompt part that was not cached | first token − admission | R |
| **TPOT**, time per output token | ms | The speed a user sees while the answer is written | decode time / (output tokens − 1), per request | R |
| ITL, inter-token latency | ms | Stalls inside an answer | every gap between two tokens, all requests pooled: p50, p99, max | I |
| Front-end overhead | s | HTTP, prompt rendering, tokenization, reply parsing | call latency − vLLM's end-to-end time | H, R |
| Time per agent step | s | Where an agent's time goes | queue + prefill + decode + tool, per call | R, H |

- Percentiles follow the sample size: mean and p50 always; p90 from 80 calls; p95 from 160; p99 from 500. N = 1 has 40 calls.
- TTFT has two modes, cached and recomputed, so it is given as the mean plus the p50 of each phase.

### 2. Throughput

| Metric | Unit | What it tells you | How it is computed | Source |
|---|---|---|---|---|
| **Output tokens/s** | tok/s | What the server produces in total | generated tokens in window / window | R, M |
| **Requests/h** | 1/h | Model calls served, by purpose (lead, sub-agent, other) | finished requests / window | R, H |
| Computed prompt tokens/s | tok/s | Prefill work actually done | (prompt − cached) tokens / window | R |
| Per-user decode speed | tok/s | Speed each user gets | 1000 / TPOT p50 | R |
| Calls per user-hour | 1/h | Agent progress per user | lead calls / (N × window) | H |
| Speedup, scaling efficiency | ×, % | How well concurrency pays | tok/s(N) / tok/s(1); speedup / N | derived |
| Per-user slowdown | × | What each user gives up | call latency(N) / call latency(1) | derived |

### 3. Cache and scheduler

| Metric | Unit | What it tells you | How it is computed | Source |
|---|---|---|---|---|
| **Recomputed prompt tokens per call** | tok | Prefill work per call. Small in the append phase, most of the prompt in the snip phase | prompt − cached | R |
| **Prefix-cache hit rate** | % | How much of each prompt was reused | cached / prompt tokens | R |
| **Cache loss** | % of calls, tok | How often a paused session's context was evicted, and what that cost | append-phase calls whose cached tokens fall short of the boundary they should resume at; the shortfall | R, H |
| **KV pool pinned** | % | Share of the pool held by running requests. Cached contexts of paused sessions are not counted (checked in exp02's data) | `kv_cache_usage_perc`: mean, p95, share of time ≥ 95% | M |
| Preemptions, and their recompute | 1/min, tok | Running requests thrown out and redone | preemption count; tokens recomputed after it | M, I |
| Running, waiting | requests | Batch size and queue length | gauges: mean, max | M |
| In-flight share | % | Share of a user's time spent waiting on the model, not on tools | mean(running + waiting) / N | M |
| Steps carrying prefill | % | How often decoders are held up by someone's prompt | steps with a prefill chunk / all steps | I |
| Request shape | tok, s | Context for all of the above | prompt and output tokens per call; tool time between calls | R, H |

### 4. Memory traffic (one shared LPDDR5x for CPU and GPU; peak 273 GB/s)

| Metric | Unit | What it tells you | How it is computed | Source |
|---|---|---|---|---|
| **DRAM traffic, total** | GB/s, % of 273 | How busy the shared memory is | GPU traffic + CPU traffic, below | G, C |
| **GPU DRAM traffic** (vLLM's bandwidth) | GB/s | The bytes the GPU moves to and from memory. vLLM is the only real GPU user | reads: L2 read misses × 32 B; writes: L2 write sectors × 32 B (an upper bound; L2 is write-back). Of which non-SM clients such as the copy engines: L2 sectors from the hub | G |
| vLLM traffic by purpose | GB/s | Which data those bytes are: weights, DeltaNet state, KV cache | exp02's byte model (`model_bytes.py`) on decode-only steps; its total is compared with the measured GPU reads | I, R, G |
| **CPU DRAM traffic** | GB/s, by core group | The bytes CPU cores move to and from memory | reads: last-level-cache read misses × 64 B per core; writes: L2 write-backs × 64 B (an upper bound) | C |
| **Memory latency** | ns, × idle | How much slower memory got for a CPU program | exp02's pointer-chase probe on core 8: mean, p50, p99; idle is 151 ns | P |
| DRAM per output token | GB/tok | Memory cost of one token | total traffic / output tokens | G, C, R |
| exp02's gauge | GB/s | Continuity with exp02 | probe latency mapped through exp02's calibration to the GPU read stream that slows memory equally | P |
| CPU bandwidth left | GB/s | What a CPU job still gets next to this load | exp02's 4-core and 12-core stream readers, before and after the window | exp02 meters |

- **Worked example**, expected at 16 decoding users from exp02's numbers. A 246-ms step streams 51.25 GB of weights, reads and writes 2.5 GB of DeltaNet state, and reads 0.6 GB of KV. The GPU counters should show about 219 GB/s of reads and 10 GB/s of writes: 84% of the peak. The probe should read 2.3× its idle latency.
- A pure GPU read stream tops out at 255 GB/s (93% of the peak), so 85–90% means the memory is full.
- The two GPU numbers answer different questions: the counters say how many bytes moved; the byte model says what they were.

### 5. GPU compute (48 SMs)

| Metric | Unit | What it tells you | How it is computed | Source |
|---|---|---|---|---|
| **Tensor-core activity** | % | Share of cycles the tensor cores, the GPU's matrix engines, are busy: the direct measure of compute use | `sm__pipe_tensor_cycles_active`, % of peak | G |
| **Achieved TFLOPS** | TFLOPS | Matrix math done per second | BF16 tensor-op counter, scaled by a GEMM of known size (S1a) | G |
| SM issue | % | Share of the maximum instruction rate used, all kinds of work | `sm__inst_executed`, % of peak | G |
| SMs active | % | Share of time SMs had any work resident, including work waiting on memory | `sm__cycles_active` | G |
| Warp occupancy | % | How full the SMs are with threads | compute warps in flight / maximum | G |
| FP32 pipe activity | % | The DeltaNet state update runs in fp32, outside the tensor cores | `sm__pipe_fma_cycles_active` | G |
| L2 hit rate | % | How much of the GPU's memory reads its 24 MB cache served: low when streaming weights, higher in prefill matrix math | 1 − read misses / read sectors | G |
| GPU time split | % | What the GPU spends its time on | step time of decode-only steps, of steps with prefill, and time with no step | I |
| **Power; energy per 1,000 output tokens** | W, kJ | Electrical cost, GPU rail only | NVML power and energy counter | S, X |
| GPU utilization (NVML) | % | What `nvidia-smi` shows: share of time any kernel ran. Reads about 96% whenever decoding, so it only separates busy from idle | `utilization.gpu` | S |
| SM clock, temperature, throttle reasons | MHz, °C, flags | Whether the GPU was slowed | NVML; GPC clock from G | S, X, G |
| Device memory | GiB | What vLLM holds on the GPU side | NVML per-process memory; meminfo residual | S |

- Every G metric is also split by step type (decode-only vs prefill), by placing the 10-ms samples on the plugin's step times. This shows the phase change directly: more prefill, more tensor activity, less DRAM traffic.

### 6. CPU (20 cores: 10 X925, 10 A725)

| Metric | Unit | What it tells you | How it is computed | Source |
|---|---|---|---|---|
| **Busy cores, CPU utilization** | cores, % | Total CPU load | sum of per-core busy; / 20; also by core type | S |
| **Cores by group** | cores | Who uses the CPU | CPU-seconds per second of: vLLM API server, vLLM engine, agent harness, tools (task containers), Docker, measurement, other users | S, X |
| **Memory-stall share** | % of busy cycles | How much of the CPU's working time waits on memory. This is where GPU traffic slows the tools | memory stall cycles / cycles, by core group | C |
| IPC | instructions/cycle | How efficiently busy cores run | instructions / cycles | C |
| Effective clock | GHz | Whether cores ran at full speed | cycles / busy time | C |
| Per-core busy | % | Which cores work | `/proc/stat` deltas, each core | S |
| Hottest vLLM thread | % | Whether vLLM's host side saturates one core (it spins during prefill) | busiest thread of the engine process | X |
| CPU pressure | % | Whether work waited for a core | PSI `some` | X |
| CPU cost | s | CPU per unit of work | vLLM CPU-s per 1k output tokens; tool CPU-s per call | derived |

### 7. Memory capacity, thermal, disk

| Metric | Unit | What it tells you | Source |
|---|---|---|---|
| **MemAvailable (minimum)**, swap activity | GiB, MB/s | Whether the box ran short of memory | S, X |
| Page cache, anonymous, shared; vLLM host RSS; containers' memory | GiB | Who holds host memory | S, X |
| Memory pressure | % | Whether work stalled on memory (PSI) | X |
| SoC temperature (hottest zone) | °C | Thermal headroom | X |
| Disk | MB/s | Background I/O | X |

Logged but tabulated only if something looks wrong: context switches, run queue, network, NVML `utilization.memory` (always 0 on this GPU).

### How each hardware number is checked

Before the first cell (S1a), every counter is compared with a load whose size is known: umbench counts the bytes it moves itself, and a GEMM's FLOPs are known.

| Number | Known load | Pass if |
|---|---|---|
| GPU DRAM reads | umbench GPU read stream at 50 and 150 GB/s, and unpaced | within 5% of umbench's own count |
| GPU DRAM writes | umbench GPU write stream | within 10% |
| CPU DRAM reads | umbench 4-core read stream, on X925 cores and on A725 cores | within 10%; else `l3d_cache_refill` replaces the read-miss event |
| CPU DRAM writes | umbench 4-core write stream | between 95% and 130% (an upper bound) |
| Achieved TFLOPS | a BF16 GEMM of 8192³, timed | sets the counter's scale; then within 5% |
| Counter overhead | the GPU read stream with sampling on and off; vLLM decode at 2 users on and off (S1b) | slowdown under 1% and 2% |
| Probe | idle reading | 151 ns within 3%, else exp02's calibration is rerun |

A counter that fails is reported as a relative trend only, and the tables say so.

### What you get

- **Headline tables**, one row per N, each number for the whole window and per phase: serving (bold metrics of 1–3) and hardware (bold metrics of 4–7).
- **Supporting tables**: latency percentiles; CPU by group and per core; GPU metrics by step type; memory; request shape; stability; the counter check.
- **Figures**, all with N on the x-axis:
  1. output tokens/s and per-user decode speed;
  2. call latency split into queue, prefill, decode and tool;
  3. DRAM traffic stacked by source (GPU reads, GPU writes, CPU), with the byte model's split of the GPU reads;
  4. tensor-core activity, SM issue and achieved TFLOPS, decode steps and prefill steps;
  5. CPU cores by group and memory-stall share, plus a per-core heat map;
  6. recomputed tokens per call, hit rate and cache loss;
  7. power and energy per 1,000 tokens.
- One time-series page per cell: throughput, KV pinned, DRAM traffic, tensor activity, busy cores, with the phase change marked.
- **Intervals.**
  - Per-call metrics: 95% bootstrap over sessions. For N ≤ 4, the range across sessions and n.
  - Rates: block bootstrap within each phase.
  - The repeat of N = 8 gives a run-to-run band per metric. A difference between two N smaller than that band is reported as not resolved.

## Setup

### Server (one fresh start per cell)

```
taskset -c 9,17,18,19 ~/.venv/bin/vllm serve Qwen/Qwen3.8-27B --served-model-name Qwen/Qwen3.8-27B \
  --host 127.0.0.1 --port 8000 --seed 0 --dtype bfloat16 --language-model-only \
  --max-model-len 131072 --max-num-seqs 64 \
  --kv-cache-memory-bytes 36G --gpu-memory-utilization 0.85 --enable-prefix-caching \
  --enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser qwen3 \
  --enable-prompt-tokens-details --chat-template research/23_efficiency_tools/qwen3_8_append_only.jinja \
  --enable-logging-iteration-details --custom-histogram-buckets '<finer TTFT, ITL and latency buckets>'
```

- Environment as exp02: `HF_HUB_OFFLINE=1`, `CUDA_CACHE_MAXSIZE=4G`, the `CPATH` fix for Triton. Plus `PYTHONPATH` and an output folder for the plugin.
- No telemetry leaves the box: `VLLM_NO_USAGE_STATS=1` and `DO_NOT_TRACK=1` for vLLM, `HARBOR_TELEMETRY=0` for Harbor (Harbor 0.24.0 reports jobs to an outside analytics service by default).
- Why each choice:
  - **BF16**: as asked; it is the checkpoint's own precision.
  - **Prefix caching on**: an agent re-sends its whole conversation every call. exp02 had it off because its prompts were random.
  - **36 GiB KV pool, fixed**: `--kv-cache-memory-bytes` makes the size exact: 752 blocks of 784 tokens. exp02 ran this size with prefix caching on this box.
  - **Per-step details**: give the GPU time split, the step types for the counters, and the inputs of the byte model.
  - **Parsers and template**: the ones topic 23 used, so the harness behaves the same.
  - **Finer histogram buckets**: vLLM's defaults have one bucket for 160–640 s of TTFT. The histograms are only a backup for the plugin's exact records.
  - Not used: `--enable-mfu-metrics`. Its estimator bills all 64 layers as attention and leaves out this model's 48 DeltaNet layers. The tensor counters measure the real thing.

### What the server's cache will do (read from vLLM 0.32.0's source)

- **In the append phase a call recomputes little.** A session's next call resumes at the last 784-token boundary of its previous prompt. It computes up to 784 old tokens, the previous reply (about 660) and the new tool result. Cached tokens are always a multiple of 784.
- **In the snip phase a call resumes near the start of its prompt,** so it computes roughly 10–18k tokens at about 1,000 tokens/s, on steps that also hold up every decoder.
- **The pool holds about 26 paused sessions of 20k tokens.** Such a session keeps 25 attention blocks plus 3 for its state checkpoint; a running one reserves about 32. So 16 users should fit and 32 should not; N = 24 is there to find the edge.
- **Losing the cache is silent.** vLLM evicts idle cached blocks without a log line or a counter. It shows per call, as the "cache loss" metric.

### Hardware counters (the sudo part)

- **One root process for the whole chain,** `sp_hw.py`, started once with sudo. It runs two samplers, pinned to cores 0, 11 and 13:
  - `perf stat`, every core, once per second, rotated hourly. Six events per core: cycles, instructions, memory stall cycles (`stall_backend_mem`), last-level-cache read misses (`ll_cache_miss_rd`), L3 refills (`l3d_cache_refill`), L2 write-backs (`l2d_cache_wb`). Both core types support all six.
  - `nsys profile` with GPU-metrics sampling only (no CUDA trace, nothing attached to vLLM), 100 samples per second, in 30-minute segments.
- Schematically:

  ```
  perf stat -a -A -x, -I 1000 -o perf_<hour>.csv \
    -e armv8_pmuv3_{0,1}/{cpu_cycles,inst_retired,stall_backend_mem,ll_cache_miss_rd,l3d_cache_refill,l2d_cache_wb}/
  nsys profile --trace=none --sample=none --cpuctxsw=none --gpu-metrics-devices=0 \
    --gpu-metrics-set=file:sp24.config --gpu-metrics-frequency=100 -o gpu_<n> sleep 1800
  ```

- **The GPU metric set** is Nsight's own set for this GPU (`gb20b`: SMs active, SM issue, tensor active, warps in flight, clocks) plus six sampleable counters this GPU offers: L2 read sectors and read misses, L2 write sectors, L2 sectors from the hub, BF16 tensor ops, FP32 pipe activity.
  - If Nsight rejects the additions, the built-in set runs alone. GPU memory traffic then comes from the byte model and the probe, as in exp02.
- **Control:** the unprivileged chain talks to it through a control file (`run`, `pause`, `stop`) and reads its heartbeat. It writes only new files in its own folder and hands each finished one to your user.
- **Data:** a few GB for the whole chain. The analysis reduces each Nsight segment to per-second and per-step values.
- **If it dies,** the chain carries on with the other sources and marks the cells without counters. They are repeated at the end if time allows.
- **Nothing persistent changes:** no `sysctl`, no driver reload, no sudoers entry. The Docker CPU pinning below lasts until the next reboot.

### Cores

| Role | Cores |
|---|---|
| vLLM (API server and engine) | 9, 17, 18, 19 |
| Latency probe | 8 |
| Samplers, counter samplers, cell runner, the probe's own thread | 0, 11, 13 |
| Agents: Harbor, s15 sessions, task containers, Docker daemons | 1–7, 10, 12, 14, 15, 16 (12 cores) |

- Same map as exp02, with the agents on the cores exp02 gave its CPU tenants.
- The Docker daemons are pinned with sudo (`systemctl set-property --runtime`), so their work counts on the agents' cores instead of landing on vLLM's.

### Memory (121.7 GiB)

| Holder | GiB |
|---|---|
| Weights | 50.1 |
| KV pool | 36 |
| vLLM runtime on the GPU side, and its host RSS | 1.7 + 4.7 |
| Desktop sessions of another user, and the OS | about 5 |
| Probe buffer, samplers, Nsight | about 2.5 |
| Harness (up to 32 sessions) | about 6 (estimate) |
| Task containers and their tools | up to about 10 (estimate) |
| Margin | about 5 |

- N = 32 is tight, and the two estimates decide it. S1b and S2 measure them.
- **Rule, fixed now:** the pool is 36 GiB if the projected MemAvailable at N = 32 stays above 8 GiB, otherwise 32 GiB (668 blocks). One size for every cell.
- Each task container is limited to 6 GiB.
- A cell is void if it swaps, and stops early if MemAvailable falls below 4 GiB.

### Workload

- **Tasks.** A seeded shuffle of SWE-bench Lite minus Verified ids (207 tasks), and one of Verified (500). From each, the first 8 tasks whose arm64 image exists and passes a sanity check, at most 2 per repository. The list interleaves the two sets and is frozen in `data/tasks.json`.
  - Checked against the registry: 175 of the 207 Lite-only tasks and 174 of 200 sampled Verified tasks have an arm64 image (`ghcr.io/epoch-research/swe-bench.eval.arm64.<id>`). The missing ones are mostly matplotlib, scikit-learn and xarray.
- **Users.** The i-th session to start takes the i-th entry of the list, which repeats (Harbor's `-k 8`: eight passes over the 16 tasks). At N = 32 every task therefore has two independent sessions. N = 1 works on the first task only, so its row carries that task's request shape.
- **Session limits are in calls and tokens, never in time,** so the work is the same at every N:
  - at most 40 lead calls per session: about 25 in the append phase and 15 in the snip phase. Topic 23's runs averaged 43 calls per task; full SWE-bench Verified sessions in topic 16 averaged 68;
  - at most 8,000 output tokens per call (the harness default). A longer reply is kept and continued. Unpatched, s15 would throw it away and ask again with a 16,000 limit;
  - window 131,072 tokens; no time limit on a session; 4 h timeout on a call, as topic 23.
- **Harness.** Today's s15, pinned as a snapshot, pacing off. Sampling and thinking are the model's defaults (temperature 1.0, thinking effort xhigh), as in topics 16, 22 and 23. The verifier is off.
- **Containers.** One compose override for every task container: the agents' cores, no image pulls, the 6 GiB limit, and the image's test environment on `PATH` (Harbor runs commands with `bash -c`, which would otherwise find the wrong Python).

## Runs

### Stages

1. **S0, build (no GPU).** Prerequisites, tasks, images, code, CPU tests against a fake server.
2. **S1a, counter check (about 20 min, no vLLM).** With `sp_hw.py` running (prerequisite 3), run the known loads in "How each hardware number is checked", plus 60 s idle for the background level (the desktop's display). Fix the CPU read event and the tensor-op scale.
3. **S1b, server check (about 30 min).**
   - Start the server with the final flags. Check its log: 752 blocks, 784-token blocks, cache mode `align`.
   - Send a scripted three-call session. Check the plugin's records, and that call 2 reports cached tokens equal to the last 784-token boundary of call 1's prompt.
   - Counter overhead on vLLM: exp02's synthetic decode load at 2 users, 2 minutes with GPU sampling and 2 minutes paused.
   - Start all 32 task containers once, idle, and read their memory.
4. **S2, smoke cell (N = 2, about 40 min).** The whole pipeline, with sessions cut at 6 calls so that users finish and start a next task. Gates in Verification. It also fixes the pool size by the memory rule.
5. **S3, main cells.** N = 8, 32, 16, 4, 2, 1.
6. **S4, as time allows, in this order.** A repeat of N = 8 (the run-to-run band), N = 24, N = 12.
7. **S5, analysis and writeup.**

### One cell

1. Preflight: no other GPU compute job, memory and disk available, no containers left over, the counter sampler's heartbeat fresh.
2. Start the samplers and the probe. Evict the checkpoint from the page cache, start the server, evict again. Send one throwaway request.
3. Idle baseline, 90 s: probe, then the two CPU stream readers.
4. Start N users. The window starts here; no call is discarded.
5. The window ends by the stop rule below.
6. Coda, 40 s, users still running: the two CPU stream readers. Skipped if MemAvailable is below 8 GiB.
7. Stop Harbor (SIGTERM), then leftover sessions and containers, the server and the samplers. The counter sampler keeps running.

A cell is marked failed and the chain moves on if the server dies or stalls, or Harbor exits early.

### Stop rule

- A cell stops when every one of the N first sessions has ended: at its natural end or at the 40-call limit.
- It also stops at the hard limit in the table below.

Why not earlier: the snip phase comes last and costs the most, so an early stop would report only the cheap part of a session.

What "stable" then means, and how it is shown:

- The snip phase has a bounded conversation, so it is the steady state of a long session. Its numbers are checked for stability: the snip period is cut into three equal parts, and a metric counts as stable when each part is within 10% of their mean. The tables mark every metric that is not.
- The append phase is a ramp: context grows from call to call. It is reported as an average over the ramp, with the time series beside it.
- Latency statistics use the first sessions only, so every call of a session counts once and none is cut off at the end. A supporting table has all calls.

One limit to keep in mind: all N users start together, so they reach the snip phase together. Queue and TTFT therefore read lower in the append phase and higher in the snip phase than they would for users who started at different times. Work metrics (tokens/s, recomputed tokens, DRAM bytes and energy per token) are not affected.

### Time

| Cell | Expected window (h) | Hard limit (h) |
|---|---|---|
| N = 1 | 1.9 | 2.5 |
| N = 2 | 2.0 | 2.5 |
| N = 4 | 2.3 | 3 |
| N = 8 | 3.0 | 4 |
| N = 16 | 4.3 | 6 |
| N = 32 | 8.5 | 12 |
| repeat of 8 | 3.0 | 4 |
| N = 24 | 5.6 | 8 |
| N = 12 | 3.6 | 5 |

- Each cell adds about 20 minutes: server start, baseline, coda, teardown. S1a and S1b add about 1 h.
- S1 to S3: about 26 h expected. With all of S4: about 40 h.
- The windows above N = 4 are estimates from exp02's prefill speed, not measurements. After the first cell (N = 8) the chain rescales the later cells from the seconds per call it measured in each phase.
- The chain holds a total of 46 h. It skips an S4 cell that no longer fits.

## Code

New folder `research/24_spark_concurrency/`, script prefix `sp_`.

| File | Purpose | Reuses |
|---|---|---|
| `sp_tasks.py` | Draw the 16 tasks, check images, write `data/tasks.json` and the task folders (`task.toml` with the image, `instruction.md`, an empty `environment/`) | HF datasets-server for the task lists; GHCR for the images |
| `sp_agent.py` | The Harbor agent: fixed to SWE tasks, keeps the 64 MiB tool-output cap | `22_wait_bench/wb_agent.py` (`WBAgent`), `harness_eval/adapter/harbor_agent.py` |
| `sp_child.py` | The s15 child: a request id on every call, send and receive stamps in `calls.jsonl`, the kept-not-retried long reply | `harness_eval/adapter/s15_child.py`; header patch as `23_efficiency_tools/et_child.py` |
| `sp_vendor.sh` | Pin today's s15 as a snapshot | `23_efficiency_tools/et_vendor.sh`, with the repo path derived |
| `sp_compose.yaml` | The override for task containers | — |
| `sp_reqlog.py` + entry-point metadata | vLLM stat-logger plugin: `requests.jsonl`, `steps.jsonl` | vLLM's `StatLoggerBase` |
| `sp_hw.py` | The root counter sampler: `perf` and Nsight GPU metrics, the control file, the heartbeat; runs with the system Python in isolated mode (`/usr/bin/python3 -I`), standard library only | — |
| `sp24.config` | The GPU metric set | Nsight's `GpuMetrics/gb20b.config` |
| `sp_check.py` | The counter check: known loads, sampling on and off, a pass/fail table | exp02 `run_study.py` (`Umbench`, `gpu_cfg`); torch for the GEMM |
| `sp_extra.py` | 1/s sampler for what exp02's sampler lacks | `12_hw_utilization/hw_sampler.py`: `TreeTotals`, `parse_pressure`, `children_of`, `net_bytes`, `Writer` |
| `sp_cell.py` | One cell | exp02 `run_study.py`: `Server`, `Sampler`, `Umbench`, `probe_loop`, `preflight`, `evict_model_pages`, `parse_server_log`, `smoke` |
| `sp_chain.py` | The cells in order, resumable, with the time budget | pattern of `22_wait_bench/wb_chain.py` |
| `sp_analyze.py` | Tables and figures; reads the Nsight segments through `nsys export --type sqlite` | exp02 `model_bytes.step_bytes`, `analyze.Inverse`; `22_wait_bench/wb_analyze.py` `pct`, `summary`, `ci` |
| `test_sp.py` | CPU tests | `22_wait_bench/wb_fake.py` |
| `PLAN.md`, `README.md` | Plan and writeup, in the repo's format | topic 23's headings |

- exp02's code is imported from `~/dgxspark-profiling` at commit 35b3781, not copied; `run_study.py` is safe to import. The cell repoints its `RAW`, `LOGS` and `BASE` and records the commit.
- Harbor 0.24.0 and anthropic 1.12.1 go in a new venv, `~/.venv-harbor`. `~/.venv` stays untouched: its anthropic 2.3.0 is too new for s15. No eval container, no docker-in-docker.
- The analysis runs in exp01's venv, which has pandas and matplotlib. Load the dataviz skill before writing the plotting code.
- `.gitignore`: keep `data/tasks.json`; ignore the generated task folders and the counter data.
- A row in `research/README.md`. Nothing is committed unless you ask.

## Not in this plan

- Pass rates: the verifier is off.
- SWE-bench Pro and Terminal-Bench: their images are x86-only.
- Counters that exist but answer other questions: Arm SPE (per-load latency samples) and the SMMU's counters (traffic on the coherent path). Both are available with sudo.
- Memory-controller counters and whole-module power: not available on this box, even with sudo.
- Other server configurations: MTP speculative decoding, quantized checkpoints, a CPU KV tier, an SM cap.
- exp02's synthetic CPU tenants (vector search, lookup service). The agents' own tools are the CPU load here.
- Kernel traces of vLLM (nsys CUDA trace, Nsight Compute). exp02's scripts can add them at one or two N later.
- A cell with staggered starts, which would measure the limit named under "Stop rule". The recorded calls make it possible later.

## Prerequisites

**You, with sudo:**
1. `sudo usermod -aG docker friedrichqi`, then a fresh login. A running tmux server keeps the old groups, so start the work from a new one.
2. `sudo systemctl set-property --runtime docker.service AllowedCPUs=1-7,10,12,14-16`, and the same for `containerd.service`. Undone by a reboot.
3. Before S1a, once per chain, from the repository root: `sudo -b /usr/bin/python3 -I research/24_spark_concurrency/sp_hw.py run --out research/24_spark_concurrency/data/hw --owner friedrichqi`. It stops when the chain writes `stop` to its control file, or after 60 hours. After a reboot it must be started again.

**Downloads:** Harbor and its dependencies (about 80 MB); the 16 task images (0.7–1.0 GB each compressed, about 14 GB in all); the two task lists (3 MB).

## Verification

- **Tests.** `test_sp.py`: the plugin on synthetic stats; the analysis on synthetic runs, including synthetic `perf` and Nsight tables; the cell runner against the fake server; the control-file protocol of `sp_hw.py` without root.
- **Counter check (S1a, S1b).** The table under "How each hardware number is checked". Its results go into the README.
- **Gates after the smoke cell.**
  - Every request appears once in `requests.jsonl`; their count and token sums match vLLM's counters and the harness's `calls.jsonl`.
  - Queue + prefill + decode never exceeds end-to-end.
  - TPOT at N = 2 is within 10% of exp02's 222 ms step.
  - Append-phase calls report cached tokens at the expected boundary.
  - On decode-only steps the measured GPU reads are 0.95–1.15× the byte model's.
  - Samplers: S at least 9.5 records/s, X, M and C at least 0.95/s, G at least 95 samples/s; no gap over 5 s except between Nsight segments (under 10 s). `perf` never time-shares counters (its running share is 100%).
  - Containers and the Docker daemons run only on the agents' cores; vLLM only on its four. The repository's package imports inside a container.
  - MemAvailable stays above 8 GiB; no swap.
- **Per cell.** The same checks, plus: no throttle reason set, no image pull inside the window, total DRAM traffic below 273 GB/s.
- **Analysis.** Every number in the README traces to `data/tables/sp.json`.
