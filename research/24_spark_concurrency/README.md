# Topic 24: Qwen3.8-27B on the DGX Spark at 1 to 8 concurrent agent users, in BF16, FP8 and NVFP4 (vLLM 0.32)

> **Status:** done 2026-11-12. 12 cells: BF16, FP8 and NVFP4 at 1, 2, 4 and 8 users; every data-quality gate
> passes. Plans: [PLAN.md](PLAN.md) (part 1, BF16) and [PLAN2.md](PLAN2.md) (part 2, three precisions). Commit
> 9f17514 holds a partial snapshot; everything after it (the 2-user cells, the final analysis, this writeup) is
> not committed. `data/` is on this machine only (not yet mirrored to the Drive, see `research/DATA.md`).
> vLLM 0.32.0, one NVIDIA GB10 (DGX Spark, 121.7 GiB unified memory), root hardware counters on.
> 16 tasks: 8 SWE-bench Lite (not in Verified) + 8 SWE-bench Verified, arm64 images.

## Question

1. Part 1: as the number of concurrent agent users N grows, what happens to throughput and latency, and how busy are
   the CPU, the GPU and the shared DRAM? Answered for BF16 at 8 users; 16 and 32 were dropped (2026-11-10): cache
   eviction between users already starts at 8.
2. Part 2: what changes when the weights are quantized? Same experiment and metrics for `Qwen/Qwen3.8-27B-FP8` and
   `unsloth/Qwen3.8-27B-NVFP4` at 1, 2, 4 and 8 users, with BF16 filled in at 1, 2 and 4.

## Results

Tables: `data/tables/sp.md` (every cell, every metric, and a "ratio to BF16" table per user count; numbers in
`sp.json`). Figures: `data/tables/figs/fig1` to `fig7` (users on the x-axis, one line or panel per model) and one
time-series page per cell (`ts_<cell>.svg`).

### Summary: 12 cells

"NVFP4" is the mixed checkpoint `unsloth/Qwen3.8-27B-NVFP4`: 4-bit MLPs in layers 0-55, FP8 elsewhere. Windows are
2.2 to 4.0 h; six cells ended by the stable rule, six at the 4 h limit.

| Users | Model | Output tok/s (steady) | TPOT p50 | TTFT mean | GPU reads, decode-only s | DRAM per output token | GPU W | kJ per 1k tokens | KV pool pinned |
|---|---|---|---|---|---|---|---|---|---|
| 1 | BF16 | 3.8 | 237 ms | 16.5 s | 226 GB/s | 57.0 GB | 47.6 | 11.9 | 5% |
| 1 | FP8 | 6.8 | 136 ms | 6.5 s | 211 GB/s | 30.4 GB | 41.6 | 6.0 | 3% |
| 1 | NVFP4 | 9.0 | 99 ms | 9.9 s | 213 GB/s | 22.9 GB | 45.0 | 4.8 | 3% |
| 2 | BF16 | 7.4 | 244 ms | 10.6 s | 224 GB/s | 29.5 GB | 44.4 | 5.8 | 7% |
| 2 | FP8 | 12.1 | 141 ms | 10.2 s | 214 GB/s | 17.0 GB | 45.2 | 3.6 | 7% |
| 2 | NVFP4 | 15.8 | 110 ms | 11.2 s | 211 GB/s | 13.0 GB | 46.5 | 2.9 | 6% |
| 4 | BF16 | 13.0 | 262 ms | 10.7 s | 222 GB/s | 16.5 GB | 48.4 | 3.6 | 15% |
| 4 | FP8 | 21.1 | 160 ms | 7.2 s | 212 GB/s | 9.5 GB | 49.2 | 2.2 | 9% |
| 4 | NVFP4 | 27.1 | 124 ms | 5.5 s | 210 GB/s | 7.6 GB | 49.4 | 1.8 | 9% |
| 8 | BF16 | 20.8 | 296 ms | 11.6 s | 219 GB/s | 9.8 GB | 59.5 | 2.6 | 28% |
| 8 | FP8 | 31.2 | 192 ms | 6.3 s | 211 GB/s | 6.2 GB | 57.0 | 1.7 | 16% |
| 8 | NVFP4 | 37.0 | 157 ms | 6.9 s | 210 GB/s | 5.4 GB | 57.1 | 1.5 | 17% |

Output against BF16 at the same number of users (weights read per step are 1.90 and 2.68 times smaller):

| Users | FP8 / BF16 | NVFP4 / BF16 |
|---|---|---|
| 1 | 1.76 | 2.35 |
| 2 | 1.64 | 2.14 |
| 4 | 1.62 | 2.09 |
| 8 | 1.50 | 1.78 |

What the 12 cells say:

1. **The DRAM is the limit in every cell.** In decode-only seconds the GPU reads 210 to 226 GB/s (77% to 83% of
   the 273 GB/s peak) for every model and every number of users, one user included. The tensor cores are under 8%
   active in those seconds. Memory latency on the probe core is 2.0 to 2.2 times idle throughout.
2. **A decode step costs the bytes it reads.** The byte model (weights + DeltaNet state + KV read per step) matches
   the measured GPU traffic in decode-only steps within 3% in all 12 cells (ratio 1.00 to 1.03).
3. **Quantization speeds decoding by the weight bytes it removes, less as users are added.** At one user FP8 gives
   1.76 and NVFP4 2.35 times BF16's output, near the weight ratios. At 8 users it is 1.50 and 1.78. Each user adds
   KV and state bytes that stay BF16 and FP32: in decode-only steps at 8 users the weights are 178 of 220 GB/s for
   BF16, 153 of 214 for FP8 and 128 of 214 for NVFP4.
4. **More users add tokens per step, not traffic.** From 1 to 8 users BF16's output grows 5.5 times, FP8's 4.6
   times and NVFP4's 4.1 times, while TPOT grows 25%, 41% and 59%.
5. **The freed memory removes the cache loss that starts at 8 users.** With BF16's 36 GiB pool, 3.6% of
   append-phase calls at 8 users lost cached prefix (0.5% at 4 users, none below). With 56 GiB (FP8) and 63 GiB
   (NVFP4) it is 0.3% and 0.9% at 8 users and none below. Running requests pin 28% of the small pool, 16% to 17%
   of the larger ones.
6. **Prefill gains less from NVFP4 than decode does.** After a trim a call recomputes 17,000 to 34,000 prompt
   tokens. Snip-phase TTFT p50 at 8 users is 20.0 s (BF16), 11.9 s (FP8) and 14.2 s (NVFP4); at one user 31.3,
   13.9 and 22.2 s. In prefill seconds the tensor cores are 40% to 59% active and GPU reads drop to 90 to 167 GB/s.
7. **Energy per token follows step time.** GPU power is 42 to 60 W in every cell, so energy per 1,000 output tokens
   falls from 11.9 kJ (BF16, one user) to 1.5 kJ (NVFP4, 8 users).
8. **The CPU stays idle.** 1.35 to 1.92 busy cores of 20, of which 1.2 to 1.4 are the measurement (the latency
   probe holds one core). vLLM uses 0.12 to 0.39 cores, harness and tools at most 0.02. All CPU cores together move 1.7
   to 2.8 GB/s of DRAM traffic.
9. **Tensor instructions cannot be compared across models.** Measured alone with the counters on, one tensor
   instruction does 4,034 FLOPs in the BF16 GEMM, 8,197 in the FP8 GEMMs and 16,394 in the FP4 GEMM
   (`data/runs/checks/qgemm_1112/qgemm.json`). The tables therefore give TFLOPS from the tokens computed: 4.0 to
   14.2 (BF16), 5.7 to 26.3 (FP8) and 10.7 to 31.1 (NVFP4), far below what the kernels reach alone (95, 176 and
   113 TFLOPS on an 8192-cubed multiply).

Limits of these numbers:

- Accuracy was not measured (verification was off). The quantized models may solve fewer or more tasks.
- The workload is not identical across models: the models write different replies. Output tokens per call range
  from 463 to 851 across cells, so whole-call latency and TTFT means carry that difference; per-token numbers
  (TPOT, tokens/s, GB and kJ per token) do not.
- Steady-period output by thirds is within 10% of its mean in 10 of 12 cells (not in BF16 at 4 users: 13.1 / 11.4 /
  14.5, and FP8 at 2 users: 13.0 / 10.8 / 12.5). GPU reads are within 10% in all 12. KV pool use keeps moving in
  11 of 12 cells: it follows which sessions happen to be long.
- At 1 and 2 users a window holds 2 to 9 sessions, so call-level numbers rest on few tasks.
- Two things differ between models besides the weights, both by design: the KV pool (36, 56, 63 GiB) and, for
  NVFP4, which layers are 4-bit. `nvfp4_n08`'s first minutes overlapped an analysis process (see Process).

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

### Three precisions at 4 users

Windows: BF16 4.0 h (hard limit), FP8 4.0 h (hard limit), NVFP4 3.01 h (stable stop). All gates pass.

| Metric | BF16 | FP8 | NVFP4 | FP8 / BF16 | NVFP4 / BF16 |
|---|---|---|---|---|---|
| Output tokens/s, all users, steady period | 13.0 | 21.1 | 27.1 | 1.62 | 2.09 |
| TPOT p50 | 262 ms | 160 ms | 124 ms | 0.61 | 0.47 |
| TTFT mean | 10.7 s | 7.2 s | 5.5 s | 0.67 | 0.52 |
| Call latency mean | 201 s | 142 s | 129 s | 0.71 | 0.64 |
| Output tokens per call | 624 | 613 | 687 | 0.98 | 1.10 |
| KV pool pinned by running requests | 15% | 9% | 9% | 0.59 | 0.58 |
| Append calls that lost cached prefix | 0.5% | 0% | 0% | - | - |
| GPU DRAM reads, decode-only seconds | 222 GB/s | 212 GB/s | 210 GB/s | 0.95 | 0.95 |
| DRAM traffic per output token | 16.5 GB | 9.5 GB | 7.6 GB | 0.58 | 0.46 |
| Measured / byte model, decode-only steps | 1.01 | 1.01 | 1.03 | - | - |
| GPU power | 48.4 W | 49.2 W | 49.4 W | 1.02 | 1.02 |
| GPU energy per 1,000 output tokens | 3.58 kJ | 2.21 kJ | 1.75 kJ | 0.62 | 0.49 |

- The speed-up sits between the 1-user and the 8-user values: 1.62 (FP8) and 2.09 (NVFP4) times BF16's output.
- BF16's output by thirds of the steady period is 13.1 / 11.4 / 14.5 tokens/s (not within 10%): with four users the
  mix of trimmed and fresh sessions swings more than with eight.

### Three precisions at 2 users

Windows: BF16 3.67 h (stable stop), FP8 3.84 h (stable stop), NVFP4 4.0 h (hard limit). All gates pass.

| Metric | BF16 | FP8 | NVFP4 | FP8 / BF16 | NVFP4 / BF16 |
|---|---|---|---|---|---|
| Output tokens/s, all users, steady period | 7.4 | 12.1 | 15.8 | 1.64 | 2.14 |
| TPOT p50 | 244 ms | 141 ms | 110 ms | 0.58 | 0.45 |
| Output tokens per call | 587 | 851 | 805 | 1.45 | 1.37 |
| Call latency mean | 176 s | 163 s | 162 s | 0.93 | 0.92 |
| GPU DRAM reads, decode-only seconds | 224 GB/s | 214 GB/s | 211 GB/s | 0.96 | 0.95 |
| DRAM traffic per output token | 29.5 GB | 17.0 GB | 13.0 GB | 0.58 | 0.44 |
| Measured / byte model, decode-only steps | 1.01 | 1.01 | 1.01 | - | - |
| GPU energy per 1,000 output tokens | 5.82 kJ | 3.61 kJ | 2.86 kJ | 0.62 | 0.49 |

- The quantized models wrote 1.4 times longer replies here, so their calls took almost as long as BF16's although
  each token came 1.7 to 2.2 times faster.

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
    2.84 h. `bf16_n04` (second try): 15:48 to 19:56, hard limit. `nvfp4_n04`: 20:05 to 23:12, stable stop after
    3.01 h.
  - `bf16_n02`: 2026-11-11 23:22 to 2026-11-12 03:11, stable stop after 3.67 h. `fp8_n02`: 03:22 to 07:20, stable
    stop after 3.84 h. `nvfp4_n02`: 07:31 to 11:38, hard limit. Chain done after 41.75 h.
- Q4 (2026-11-12): all 12 cells re-analysed with nothing running (`sp_analyze.py`), every gate passes; FLOPs per
  tensor instruction measured for the BF16, FP8 and FP4 GEMMs (`data/runs/checks/qgemm_1112/`); the root counter
  sampler stopped (`echo stop > data/hw/control`) after 49.3 h.

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

Everything is under `research/24_spark_concurrency/data/` on the DGX Spark (20 GB; git tracks only the `*.md` tables,
the `*.svg` figures and `tasks.json`).

| Path | What | Size |
|---|---|---|
| `tasks.json`, `taskdirs/`, `queue/` | The 16 drawn tasks, their Harbor task folders, the queue Harbor reads | 0.4 MB |
| `variants.json`, `model_bytes_{bf16,fp8,nvfp4}.json` | KV pool per model; byte model of each checkpoint | small |
| `runs/<cell>/` | One folder per cell: `cell.json` (flags, marks, stop reason, stable checks, kernel lines), `reqlog/` (per-request and per-step records of the vLLM plugin), `raw/` (10 Hz sampler, probe windows, server config), `extra.jsonl` (1 Hz process groups, PSI, energy), `logs/` | 2.3 GB |
| `jobs/<cell>/` | Harbor's job: one folder per trial with the s15 harness's `calls.jsonl`, `events.jsonl`, `result.json` | 0.5 GB |
| `hw/session_20261110_104702/` | Root counter sampler, 49.3 h: `perf_*.csv` (6 PMU events per core per second), `gpu_*.nsys-rep` and their `.sqlite` exports (GPU counters at 100 Hz), `segments.jsonl`, `hw.log` | 17 GB |
| `runs/checks/` | Server checks (`server_*`), counter check (`counters_1110_104724`), overhead check, container check, dry run, quantized GEMM calibration (`qgemm_1112`) | small |
| `runs/aborted/`, `runs/*_old*` | Set-aside runs: the first BF16 8-user start, both 32-user starts, the two failed starts of 2026-11-11 | small |
| `tables/sp.md`, `sp.json` | Final tables and numbers, all 12 cells | - |
| `tables/cells/<cell>.json`, `series_<cell>.json` | Per-cell results (the cache `--reuse` reads) and 30-second series | - |
| `tables/figs/` | `fig1_throughput` to `fig7_power`, `ts_<cell>` | - |
| `tables/sp_<cells>.md`, `stable_replay_n08.json` | Intermediate tables written during the run; the stop-rule replay | - |

Cells: `n08` (BF16, 8 users), `bf16_n01/02/04`, `fp8_n01/02/04/08`, `nvfp4_n01/02/04/08`; smoke cells `smoke`,
`smoke_fp8`, `smoke_nvfp4`.

To rebuild the tables and figures (nothing may be serving on the GPU; about 1 h, up to 10 GiB of RAM):

```
PY=~/dgxspark-profiling/exp01_um_profiling/.venv/bin/python
HF_HOME=/home/hf_data $PY research/24_spark_concurrency/sp_analyze.py      # add --reuse to use the cached cells
$PY research/24_spark_concurrency/sp_figs.py
```
