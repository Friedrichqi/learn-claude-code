# Topic 24: Qwen3.8-27B on the DGX Spark at 1 to 8 concurrent agent users, in BF16, FP8 and NVFP4 (vLLM 0.32)

> **Status:** part 2 chain running since 2026-11-10 18:05 (`sp_chain.py status`): 8 users done for all three
> models; 1, 4 and 2 users to go.
> Plans: [PLAN.md](PLAN.md) (part 1, BF16) and [PLAN2.md](PLAN2.md) (part 2, three precisions). Nothing committed.
> vLLM 0.32.0, one NVIDIA GB10 (DGX Spark, 121.7 GiB unified memory), root hardware counters on.
> 16 tasks: 8 SWE-bench Lite (not in Verified) + 8 SWE-bench Verified, arm64 images.

## Question

1. Part 1: as the number of concurrent agent users N grows, what happens to throughput and latency, and how busy are
   the CPU, the GPU and the shared DRAM? Answered for BF16 at 8 users; 16 and 32 were dropped (2026-11-10): cache
   eviction between users already starts at 8.
2. Part 2: what changes when the weights are quantized? Same experiment and metrics for `Qwen/Qwen3.8-27B-FP8` and
   `unsloth/Qwen3.8-27B-NVFP4` at 1, 2, 4 and 8 users, with BF16 filled in at 1, 2 and 4.

## Results

Partial (2026-11-11 15:50): all three models are done at 8 users and at 1 user; FP8 at 4 users is done; the
rest is running. Tables:
`data/tables/sp.md` (all finished cells, with a "ratio to BF16" table); figures: `data/tables/figs/`.

### Three precisions at 8 users

Windows: BF16 4.0 h (hard limit), FP8 2.17 h (stable stop), NVFP4 4.0 h (hard limit; its running means still moved
4% to 6% per half hour). All data-quality gates pass for all three. "NVFP4" is the mixed checkpoint: 4-bit MLPs in
layers 0-55, FP8 elsewhere.

| Metric | BF16 | FP8 | NVFP4 | FP8 / BF16 | NVFP4 / BF16 |
|---|---|---|---|---|---|
| Weights read per decode step | 51.25 GB | 26.93 GB | 19.10 GB | 0.53 | 0.37 |
| KV pool | 36 GiB | 56 GiB | 63 GiB | 1.56 | 1.75 |
| Output tokens/s, all users, steady period | 20.8 | 31.2 | 37.0 | 1.50 | 1.78 |
| TPOT p50, all calls | 296 ms | 192 ms | 157 ms | 0.65 | 0.53 |
| TPOT p50, append-phase calls | 282 ms | 170 ms | 138 ms | 0.60 | 0.49 |
| TTFT mean | 11.6 s | 6.3 s | 6.9 s | 0.55 | 0.59 |
| Call latency mean | 235 s | 117 s | 112 s | 0.50 | 0.48 |
| Output tokens per call | 646 | 463 | 596 | 0.72 | 0.92 |
| KV pool pinned by running requests | 28% | 16% | 17% | 0.56 | 0.61 |
| Append calls that lost cached prefix | 3.6% | 0.3% | 0.9% | 0.08 | 0.24 |
| GPU DRAM reads, decode-only seconds | 219 GB/s | 211 GB/s | 210 GB/s | 0.96 | 0.96 |
| DRAM traffic, all sources | 219 GB/s | 208 GB/s | 209 GB/s | 0.95 | 0.96 |
| DRAM traffic per output token | 9.8 GB | 6.2 GB | 5.4 GB | 0.63 | 0.55 |
| Measured / byte model, decode-only steps | 1.02 | 1.02 | 1.03 | - | - |
| Tensor cores active, whole window | 16.6% | 18.7% | 18.3% | 1.13 | 1.11 |
| Tensor cores active, decode-only seconds | 4.5% | 6.9% | 7.5% | - | - |
| GPU power | 59.5 W | 57.0 W | 57.1 W | 0.96 | 0.96 |
| GPU energy per 1,000 output tokens | 2.64 kJ | 1.67 kJ | 1.45 kJ | 0.63 | 0.55 |
| MemAvailable min | 21.1 GiB | 25.8 GiB | 17.4 GiB | - | - |

- **Quantization buys speed in proportion to the bytes a step reads, and the DRAM stays the limit.** In decode-only
  seconds the GPU reads 210 to 219 GB/s with every model (77% to 80% of the peak), and the tensor cores stay under
  8% active. The byte model matches the measured traffic within 3% for all three.
- **The gain is smaller than the weight ratio, because the per-user bytes do not shrink.** Output at 8 users rises
  1.50 times (FP8) and 1.78 times (NVFP4), while the weights shrink 1.90 and 2.68 times. In decode-only steps the
  weights are 178 of 220 GB/s for BF16, 153 of 214 for FP8 and 128 of 214 for NVFP4; KV reads and DeltaNet state
  take the rest (42, 61 and 86 GB/s), and they are BF16 and FP32 in all three models.
- **The larger pools remove most cache loss.** 3.6% of append-phase calls lost cached prefix with BF16 (36 GiB),
  0.3% with FP8 (56 GiB) and 0.9% with NVFP4 (63 GiB); running requests pin 16% to 17% of the larger pools, 28% of
  the small one.
- **After a trim, prefill costs about the same in FP8 and NVFP4.** Snip-phase TTFT p50 is 20.0 s (BF16), 11.9 s (FP8)
  and 14.2 s (NVFP4, which also recomputed more tokens per call: 22,992 against 16,815).
- **Energy per token falls with the step time**: GPU power is 57 to 60 W for all three, so 2.64, 1.67 and 1.45 kJ
  per 1,000 output tokens.
- The workloads are close but not equal: first sessions ended 4 at the 40-call budget and 4 (BF16: 3, plus one cut
  off at the hard limit) by themselves in every model; output tokens per call are 646, 463 and 596.
- NVFP4's lowest MemAvailable (17.4 GiB) is from the analysis processes that ran in its first minutes (see
  Process), not from the model.

### Three precisions at 1 user

Windows: BF16 3.67 h (stable stop), FP8 4.0 h (hard limit), NVFP4 2.84 h (stable stop). With one user the window
holds two to five whole sessions, so whole-call numbers rest on few tasks; per-token numbers are solid.

| Metric | BF16 | FP8 | NVFP4 | FP8 / BF16 | NVFP4 / BF16 |
|---|---|---|---|---|---|
| Output tokens/s, steady period | 3.8 | 6.8 | 9.0 | 1.76 | 2.35 |
| TPOT p50 | 237 ms | 136 ms | 99 ms | 0.57 | 0.42 |
| TTFT p50, append-phase calls | 1.96 s | 0.73 s | 0.61 s | 0.37 | 0.31 |
| TTFT p50, snip-phase calls | 31.3 s | 13.9 s | 22.2 s | 0.44 | 0.71 |
| Call latency mean | 219 s | 121 s | 100 s | 0.55 | 0.46 |
| GPU DRAM reads, decode-only seconds | 226 GB/s | 211 GB/s | 213 GB/s | 0.93 | 0.94 |
| DRAM traffic per output token | 57.0 GB | 30.4 GB | 22.9 GB | 0.53 | 0.40 |
| Measured / byte model, decode-only steps | 1.00 | 1.01 | 1.01 | - | - |
| Tensor cores active | 4.7% | 8.1% | 11.2% | 1.73 | 2.38 |
| GPU power | 47.6 W | 41.6 W | 45.0 W | 0.87 | 0.95 |
| GPU energy per 1,000 output tokens | 11.9 kJ | 6.0 kJ | 4.8 kJ | 0.50 | 0.41 |

- **At one user the speed-up is close to the weight ratio**: 1.76 times for FP8 (weights 1.90 times smaller) and
  2.35 times for NVFP4 (2.68 times smaller). One user's KV and state bytes are small (10 to 20 GB/s of about
  210), so nearly every byte read is a weight byte, and DRAM traffic per output token falls to 0.53 and 0.40 of
  BF16's.
- **One user already fills the memory bus**: 211 to 226 GB/s in decode-only seconds, the same as at 8 users. More
  users add tokens per step, not traffic.
- **NVFP4's prefill is slower than FP8's**: for the same 25,000 recomputed tokens after a trim, TTFT p50 is 22.2 s
  against 13.9 s (and 14.2 s against 11.9 s at 8 users). Its decode is faster (99 against 136 ms per token).
- One gate is flagged for `nvfp4_n01` in `sp.md` (calls cut at teardown: 3, limit 2): the three are the last
  records of sessions that ended at their call budget, not cut calls. The gate is corrected for later analyses.

### BF16 at 8 users

**N = 8** (4.0 h window, ended at its hard limit with 7 of 8 first sessions finished; all data-quality gates pass):

| Metric | Whole window | Append period (first 44 min) | Steady period (last 3.1 h) |
|---|---|---|---|
| Output tokens/s, all users | 22.3 | 27.2 | 20.8 |
| Prompt tokens computed/s | 254 | 108 | 298 |
| Call latency, mean | 235 s | 209 s | 299 s |
| TTFT, mean | 11.6 s | 3.0 s | 21.7 s |
| TPOT, p50 | 296 ms | 282 ms | 342 ms |
| Prompt tokens recomputed per call | 7,454 | 2,010 | 10,029 |
| KV pool pinned by running requests | 28% | 19% | 31% |
| GPU DRAM reads | 206 GB/s | 213 GB/s | 204 GB/s |
| DRAM traffic, all sources (% of 273 GB/s) | 219 GB/s (80%) | 223 GB/s | 217 GB/s |
| Tensor cores active | 16.6% | 9.2% | 18.8% |
| Tensor work | 18.9 TFLOPS | 10.6 TFLOPS | 21.5 TFLOPS |
| GPU power | 59.5 W | 53.3 W | 60.8 W |

- The steady period is stable: output tokens/s by thirds 19.4 / 21.5 / 21.6, GPU reads 201 / 204 / 206 GB/s.
- Decode is bound by memory traffic, not by compute. In decode-only seconds the GPU reads 219 GB/s (80% of the
  peak) with tensor cores 4.5% active (5.3 TFLOPS). In prefill seconds it reads 167 GB/s, writes 27 GB/s, and the
  tensor cores are 52% active (59.5 TFLOPS).
- The byte model (weights + DeltaNet state + KV read per step) gives 220 GB/s for decode-only steps; measured 224 GB/s
  (ratio 1.02). Weights are 178 GB/s of it, KV reads 34 GB/s.
- After a session's first trim, each call recomputes 21,202 prompt tokens on average (hit rate 10%; 84% before), so
  TTFT p50 goes from 1.7 s to 20.0 s. Prefill steps take 24% of the engine's time over the window.
- The CPU side is small: 2.2 GB/s of DRAM traffic from all 20 cores, 1.63 busy cores of which 1.37 are the
  measurement itself (the latency probe holds one core); vLLM uses 0.29 cores, harness and tools 0.02.
- Memory latency on the probe core is 2.19x idle. GPU energy is 2.64 kJ per 1,000 output tokens. MemAvailable
  never below 21.1 GiB. SoC temperature peaks at 90.7 C with no GPU throttling recorded.

## Process

- S0, build (2026-11-09, done):
  - 16 tasks drawn (seed 2424; `data/tasks.json`), all images pulled and checked (aarch64, base commit, imports).
  - Server check passed (`data/runs/checks/server_1109_231451/check.json`): 752 blocks of 784 tokens, `align` mode,
    request ids joined, cached tokens exactly at the 784-token boundary, TPOT 225 ms at one user.
  - No-GPU dry run through Harbor passed (`data/runs/checks/dryrun_1109_233519/`): tools run in the container on the
    agents' cores, 6 GiB limit, the test environment's Python.
  - `test_sp.py`: 6/6 pass.
- S1b (2026-11-09/10, done): 32 idle task containers cost 0.16 GiB (`data/runs/checks/containers_*/`). Docker and
  containerd are pinned to the agents' cores (`AllowedCPUs=1-7,10,12,14-16`, runtime only, gone after a reboot).
- S2, smoke cell (2026-11-10, done; N = 2, 6 calls per session, no counters; `data/tables/sp_smoke.md`):
  - All serving gates pass: every call joined to its server record, token counts equal on both sides, plugin totals
    equal vLLM's own counters, samplers at 10.0 Hz and 1.0 Hz, probe covers 100% of the window.
  - TPOT p50 233 ms, TTFT mean 1.24 s, call latency mean 30.1 s, 8.1 output tokens/s, cache loss 0%.
  - Memory latency on the probe core 2.31x idle; byte model for decode-only steps 223 GB/s; GPU 29.3 W.
  - MemAvailable never below 22.8 GiB with the 36 GiB pool, so the pool stays at 36 GiB.
  - Early calls are short (about 120 output tokens, 25-30 s), so sessions are likely shorter than the plan's
    estimates. The chain rescales the later cells from the N = 8 window.
- S1a, counter check (2026-11-10, passed; `data/runs/checks/counters_1110_104724/compare.json`). Known `umbench`
  streams against the counters, 20 s each:

  | Load | Known | Counter | Ratio |
  |---|---|---|---|
  | GPU reads, paced 50 / 150 GB/s, unpaced | 50.0 / 150.0 / 234.9 GB/s | L2 read misses x 32 B | 1.001 / 1.000 / 1.000 |
  | GPU writes, unpaced | 194.1 GB/s | L2 write sectors x 32 B | 1.000 |
  | CPU reads, X925 cores | 104.9 GB/s | L2 refills x 64 B | 1.005 |
  | CPU reads, A725 cores | 53.0 GB/s | L2 refills x 64 B | 1.006 |
  | CPU writes, X925 cores | 80.8 GB/s | bus transactions x 32 B | 1.018 |
  | BF16 matrix multiply (8192 cubed) | 96.2 TFLOPS | tensor-pipe instructions | 4,033 FLOPs per instruction; tensor active 88.8% |

  - Demand last-level-cache read misses x 64 B give only 0.16 (X925) and 0.42 (A725) of a prefetched read stream:
    kept as a lower bound.
  - Bus transactions are 2.04 to 2.08 per 64-byte line, for reads and writes alike.
  - Counter sampling slows an unpaced GPU read stream by 0.01% and 0.7% in two tries (noise level).
- S1b, overhead on vLLM (2026-11-10, passed; `data/runs/checks/overhead_*/overhead.json`): exp02's synthetic decode
  load at 2 users, 120 s windows: 8.63 tok/s with counter sampling, 8.57 without, 8.63 with. No slowdown.
- S3, BF16 cells (2026-11-10):
  - N = 8: done 15:16 at the 4 h hard limit (7 of 8 first sessions ended; the eighth was at call 36 of 40).
  - N = 32: started twice, both set aside in `data/runs/aborted/`. First run: 98 of 128 trials failed at container
    start ("all predefined address pools have been fully subnetted": Docker holds about 30 networks, and each trial
    made one). Fixed with one shared network (`sp24net`, `sp_compose.yaml`). Then dropped with N = 16.
- Part 2 (PLAN2.md), Q0, preparation (2026-11-10, done):
  - Both checkpoints downloaded (30.9 GB and 23.4 GB). `test_sp.py`: 9/9 pass.
  - Weights read per decode step, from the safetensors headers (`sp_modelbytes.py`, `data/model_bytes_*.json`):
    BF16 51.25 GB (equal to exp02's number), FP8 26.93 GB, NVFP4 19.10 GB.
  - The NVFP4 checkpoint is mixed: of its 19.1 GB, 7.5 GB are packed 4-bit MLP weights (layers 0-55), the rest FP8
    (attention and DeltaNet projections, MLPs of layers 56-63, output head) and scales.
  - Stop rule replayed on the BF16 8-user cell (`data/tables/stable_replay_n08.json`): see Changes.
- Q1, server checks (2026-11-10):
  - FP8 (`data/runs/checks/server_fp8_*/check.json`, passed): vLLM picks `CutlassFp8BlockScaledMMKernel`; KV cache
    BF16; 784-token blocks; cached-token boundary exact; tool call parsed; loaded model 27.55 GiB (BF16 50.13);
    TPOT at one user 132 ms (BF16 225 ms); GPU process 66.6 GiB with a 36 GiB pool, so its pool is 56 GiB.
  - NVFP4 (`data/runs/checks/server_nvfp4_*/check.json`, passed): vLLM picks `FlashInferCutlassNvFp4LinearKernel`
    for the 4-bit layers (built once on this first start: ready after 396 s, 254 s for FP8) and
    `CutlassFP8ScaledMMLinearKernel` for the FP8 layers; same cache checks pass; loaded model 20.35 GiB; TPOT at
    one user 92 ms; GPU process 59.5 GiB with a 36 GiB pool, so its pool is 63 GiB.
  - Pool sizes are stored in `data/variants.json` (56G, 63G); BF16 stays at 36G.
- Q2, smoke cells (2026-11-10, 2 users, 6 calls per session; `data/tables/sp_smoke_smoke_fp8_smoke_nvfp4.md`):
  every gate passes for both quantized models.

  | | BF16 | FP8 | NVFP4 |
  |---|---|---|---|
  | TPOT p50 | 233 ms | 132 ms | 96 ms |
  | Weights read per step | 51.25 GB | 26.93 GB | 19.10 GB |
  | GPU reads in decode-only seconds | no counters | 211 GB/s | 207 GB/s |
  | Measured / byte model, decode-only steps | - | 1.01 | 1.01 |
  | KV pool, tokens | 36 GiB, 566,472 | 56 GiB, 881,346 | 63 GiB, 991,326 |
  | MemAvailable min | 22.8 GiB | 25.1 GiB | 25.2 GiB |

  - The GPU still reads about 210 GB/s with either quantized model: a step is shorter because it has fewer bytes to
    read, not because the memory is less busy.
  - The sessions differ between models even in six calls (output tokens per call 121, 454, 197), so whole-call
    numbers are not comparable from the smoke cells.
- Q3, the chain (started 2026-11-10 18:05): `fp8_n08`, `nvfp4_n08`, then all three models at 1, 4 and 2 users.
  - `fp8_n08`: done 20:23, stable stop after 2.17 h (steady period 95 min; drift 0.8% to 1.8%).
  - `nvfp4_n08`: 20:23 to 00:29, hard limit (4.0 h). Note: `sp_analyze.py` ran on the aux cores (up to 9 GiB of
    RAM) during its minutes 0 to 9 and about 15 to 17.
  - `bf16_n01`: 00:30 to 04:19, stable stop after 3.67 h. `fp8_n01`: 04:19 to 08:25, hard limit (4.0 h).
  - `nvfp4_n01` and `bf16_n04` failed at start (08:29, 08:30), both because the analysis of `fp8_n01` was still
    running and held about 10 GiB:
    - `nvfp4_n01`: umbench could not create its CUDA context for the baseline meters ("out of memory").
    - `bf16_n04`: vLLM exited: "Free memory on device (94.26/121.69 GiB) on startup is less than desired GPU memory
      utilization (0.85, 103.44 GiB)".
    - Both are re-run in their place in the list. The chain now analyses each cell itself and waits for that
      before starting the next one.
  - `fp8_n04`: 08:30 to 12:36, hard limit (4.0 h). `nvfp4_n01` (second try): 12:43 to 15:41, stable stop after
    2.84 h. `bf16_n04` (second try): started 15:48.

## Changes

- 2026-11-09, analysis: the append and snip periods are the seconds when at least 80% of the active sessions are in
  that phase. The plan's definition (first trim to first end) is empty whenever a session finishes before the others trim.
- 2026-11-09, vLLM: the per-step `elapsed_ms` is ~0.1 ms under async scheduling, so step time is the gap between
  consecutive step records.

- 2026-11-10, counters (found by the counter check, S1a):
  - The GPU metric file is `sp24.config`: Nsight wants the file named after the set's alias.
  - Nsight silently drops the `sm__ops_path_tensor_*` counters. Tensor work is counted with
    `sm__inst_executed_pipe_tensor_subpipe_hmma_realtime` (tensor-pipe instructions) and scaled to FLOPs with the
    BF16 matrix-multiply load.
  - CPU events are now cycles, instructions, memory-stall cycles, demand LLC read misses, bus transactions and L2
    refills (`sp_hw_events.txt`, read by `sp_hw.py` each time perf starts). The plan's L3 refills equal the demand
    misses, and L2 write-backs miss streaming writes.
  - CPU traffic: reads = L2 refills x 64 B (an upper bound when data is served from L3), total = bus transactions x
    32 B, writes = total - reads; demand misses x 64 B are reported as the lower bound on reads.
  - Each cell ends by pausing and resuming the sampler, which closes its files, so the cell can be analysed at once.
    Seconds without counter samples (file changes, about 2 s per 30 min) are left out of means, not counted as zero.
  - `sp_hw.py`: one copy per output folder (lock file), the folder is handed to `--owner`, a failing GPU sampler is
    retried with a growing delay.
- 2026-11-10, chain: it can start without the root sampler; it runs the counter and overhead checks the first time it
  sees the sampler between cells, and repeats counter-less cells at the end.
- 2026-11-10, analysis: data-quality gates per cell (`gates` in `sp.json`, last table of `sp.md`). vLLM's token counters
  are compared with the step records, since both include requests cut off at teardown; request records do not.

- 2026-11-10, analysis: a third period, steady = from the second when half of the active sessions have been trimmed.
  The plan's snip period (80% trimmed) never occurs because finished sessions are replaced by fresh ones. Stability
  is judged on the steady period. Step time is spread over the seconds a step spans.
- 2026-11-10, cells: a cell that ends at its hard limit counts as done (it was labelled failed).

- 2026-11-10, part 2 stop rule (PLAN2.md said: steady period of 90 min and running means within 5% over 30 min):
  - Replayed on the BF16 8-user cell, that rule stops at minute 150 with prompt tokens/s 19% above the whole
    window and output tokens/s 6% below. The users move through the phases together at first: output falls to
    18.7 tokens/s around minute 105, when nearly all sessions are trimmed, and returns to about 22 once fresh
    sessions have replaced finished ones.
  - The rule is now: steady period of 90 min, at least half of the first sessions ended, and running means within
    3% over 30 min. On the same cell it stops at minute 220 of 240 with every number within 3% of the whole window.
  - Cells therefore run about 3.5 h, not 3 h. If time runs short, the 2-user cells (last in the order) are cut.
- 2026-11-10, part 2 cells: under the stable rule a window no longer ends when all first sessions have ended; refill
  sessions keep the number of users at N until the rule or the 4 h limit ends it.
- 2026-11-10, part 2 server flags: the quantized models get `--kv-cache-dtype bfloat16`; BF16 keeps exactly the
  flags of part 1. The server's PATH starts with the vLLM venv (FlashInfer builds its FP4 kernel with the venv's
  ninja on the first NVFP4 start).

- 2026-11-10, analysis: finished cells are cached in `data/tables/cells/` (`sp_analyze.py --reuse`), and without
  `--cells` only ended cells are analysed, so no analysis runs inside a measured window. The swap gate counts only
  swap-out during the window (swap used while the weights load may come back in slowly).

- 2026-11-11, chain: the per-cell analysis is a step of the chain, between cells (it was a separate watcher that
  started when a cell ended and overlapped the next server's start: two cells failed, see Process). A failed cell
  is run again when its turn in the list comes, not at the end. `sp_after.sh` only waits and prints.

## Data inventory

None yet.
