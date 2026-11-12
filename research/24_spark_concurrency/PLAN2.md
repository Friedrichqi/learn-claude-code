# Topic 24, part 2: the same profiling for FP8 and NVFP4, at 1, 2, 4 and 8 users

## Context

Part 1 measured `Qwen/Qwen3.8-27B` in BF16 at 8 concurrent agent users on the DGX Spark: 22.3 output tokens/s,
TPOT p50 296 ms, 219 GB/s of DRAM traffic (80% of the 273 GB/s peak), decoding bound by memory traffic and not by
compute (tensor cores 4.5% active in decode-only seconds). You stopped the scaling at 8 users: cache eviction between
users already starts there, so 16 and 32 add nothing.

The question now: **what changes when the weights are quantized?** If decoding is bound by the bytes of weights read
per step, smaller weights should give faster steps, and the freed memory can hold a larger KV cache. This part runs the
same experiment, with the same serving metrics and the same hardware counters, on:

- `Qwen/Qwen3.8-27B-FP8` (30.9 GB on disk)
- `unsloth/Qwen3.8-27B-NVFP4` (23.4 GB on disk)

and fills in BF16 at 1, 2 and 4 users, so every user count has all three precisions side by side.

Decisions you made: user counts 1, 2, 4, 8 for both quantized models; BF16 added at 1, 2, 4; a cell stops once its
numbers are stable; quantized models get a larger KV pool from the freed memory.

Current state: nothing is running, the GPU is free, the root counter sampler is still up (no new sudo needed). The
32-user cell is abandoned; its 1-minute-old folder goes to `data/runs/aborted/`.

## What the two checkpoints are (checked on Hugging Face and in the installed vLLM 0.32.0)

| | BF16 (done) | FP8 | NVFP4 |
|---|---|---|---|
| Repo | `Qwen/Qwen3.8-27B` | `Qwen/Qwen3.8-27B-FP8` | `unsloth/Qwen3.8-27B-NVFP4` |
| Size on disk | 55.6 GB | 30.9 GB | 23.4 GB |
| What is quantized | nothing | every linear layer of the text model: FP8 weights with 128x128 block scales, FP8 activations (W8A8) | **mixed**: MLPs of layers 0-55 are NVFP4 (4-bit weights and activations); attention and DeltaNet projections, MLPs of layers 56-63 and the output head are FP8 |
| Stays BF16 | - | output head, embeddings, norms, small DeltaNet parts, vision tower | embeddings, small DeltaNet parts, vision tower |
| Kernel vLLM picks on this GPU (SM 12.1) | - | CUTLASS block-FP8 GEMM, precompiled | FlashInfer CUTLASS FP4 GEMM, compiled on first start; FP8 layers use the CUTLASS FP8 GEMM |
| Weights read per decode step (estimate) | 51.25 GB | about 27 GB | about 19 GB |

- Neither repo is gated. Both keep the vision tower, which `--language-model-only` drops as for BF16.
- "NVFP4" is therefore not a pure 4-bit model. The report will call it "NVFP4 (mixed 4-bit / FP8)".
- Both run their matrix multiplies natively in FP8 / FP4. Nothing is converted back to BF16.
- Unsloth ships a different chat template. All three models are served with the same template file as in part 1
  (`research/23_efficiency_tools/qwen3_8_append_only.jinja`), so prompts are tokenized identically.

## What stays the same, and what changes

**Same for all three:** vLLM 0.32.0, the server flags of part 1 (`--dtype bfloat16`, `--language-model-only`,
`--max-model-len 131072`, `--max-num-seqs 64`, prefix caching, same parsers and template), the 16 tasks, 40-call
sessions through Harbor and the s15 harness, the core layout, the samplers, the hardware counters and their
calibration, the analysis.

**Changed on purpose:**

1. **The model repo.** No `--quantization` flag is needed; vLLM detects the format.
2. **KV cache stays BF16**, pinned with `--kv-cache-dtype bfloat16`. A KV token costs 65,536 bytes in all three
   models, so a pool size means the same number of tokens everywhere. (The unsloth checkpoint carries FP8 KV scales;
   they are not used.)
3. **KV pool size** (your choice): each model gets 36 GiB plus the GPU memory its weights free compared with BF16,
   rounded down to a whole GiB. Total GPU memory then equals BF16's, and so does the free-memory margin (21 GiB at
   8 users). Expected: about 58 GiB for FP8 and about 66 GiB for NVFP4, against 36 GiB (752 blocks of 784 tokens)
   for BF16. The real numbers come from loading each model once (stage Q1).
4. **Stop rule** (your choice), see below.

**One thing I cannot hold equal:** a quantized model may write different replies (other lengths, other tool calls),
so the workload itself can differ. Each table therefore shows output tokens per call, prompt tokens per call and how
the sessions ended, per model. Per-token numbers (TPOT, tokens/s, GB per token) stay directly comparable.

## Metrics

Everything in part 1's metric dictionary (PLAN.md), unchanged: throughput, TTFT, TPOT, call latency split, cache
reuse and cache loss, KV pool use, DRAM traffic by source (GPU reads and writes, CPU reads and writes), memory latency
on the probe core, tensor-core activity, SM issue rate, GPU power and energy per token, CPU cores by group and per
core, memory-stall share, free memory, temperature.

Additions and one change:

| Metric | Why |
|---|---|
| Ratio to BF16 at the same user count, for every headline metric | The comparison this part is for |
| Weights read per step, per model (byte model) against measured GPU reads | Tests "step time follows weight bytes" across precisions |
| GB of DRAM traffic per output token | Should fall with the weight size |
| KV pool size, blocks, share pinned, cache loss | Shows what the larger pool buys |
| Tensor-pipe instructions per second (raw counter) | See below |
| GPU memory of the loaded model, load time | Capacity |

**Tensor work (changed).** The working counter counts tensor-pipe instructions. One instruction does about 4,096
multiply-adds' worth of FLOPs in BF16, but twice that in FP8 and four times in FP4 (wider instruction shapes), and a
quantized model mixes all three. One scale therefore no longer turns the counter into TFLOPS. Reported instead:

- tensor cores active (%), and tensor instructions per second, as measured;
- TFLOPS from the counter for BF16 only;
- for all three, TFLOPS from the workload: tokens computed per second x FLOPs per token of the linear layers (the
  same formula for every precision);
- if the FP8 and FP4 kernels can be called alone (30 minutes allowed in Q1), their FLOPs per instruction, measured
  like the BF16 one.

## Cells

Eleven new cells, plus the finished BF16 8-user cell (`n08`, kept as is):

| Order | Cell | Model | Users |
|---|---|---|---|
| 1, 2 | `fp8_n08`, `nvfp4_n08` | FP8, NVFP4 | 8 |
| 3-5 | `bf16_n01`, `fp8_n01`, `nvfp4_n01` | all | 1 |
| 6-8 | `bf16_n04`, `fp8_n04`, `nvfp4_n04` | all | 4 |
| 9-11 | `bf16_n02`, `fp8_n02`, `nvfp4_n02` | all | 2 |

The 8-user pair runs first because BF16 at 8 users exists already: the first full comparison is ready after two
cells. One user comes next: it is the cleanest view of pure decode speed.

**Stop rule.** A cell's window ends at the first of:

- **stable**: checked every 10 minutes. The steady period (from the second when half of the active sessions have
  been trimmed) is at least 90 minutes long, and the running means over it of output tokens/s, prompt tokens
  computed/s, KV pool pinned and engine step time each moved by at most 5% in the last 30 minutes;
- **hard limit**: 4 hours, as for the BF16 8-user cell.

Before the chain starts, the rule is replayed on the recorded BF16 8-user cell: where it would have stopped, and
how far the numbers at that point are from the full 4-hour numbers. If any headline number is off by more than 5%,
the tolerance is tightened until it is not. GPU traffic cannot be read live (counter files close every 30 minutes),
so its stability is checked after the cell; in part 1 it was the steadiest number (201 / 204 / 206 GB/s by thirds).

**Time.** About 3 hours of window per cell plus 20 minutes of start and teardown: 11 cells take about 37 hours;
48 hours if every cell runs to its hard limit. Preparation (downloads, checks, smoke cells) takes about 2.5 hours.
The root sampler stops by itself 60 hours after its start, on 2026-11-12 at 22:47. The expected end is 2026-11-12
around 09:00; in the worst case it is close to the sampler's limit, and I will tell you before then if one more
restart is needed.

## Stages

- **Q0, preparation (no GPU, about 1 hour, downloads in parallel).**
  - Code changes below; `test_sp.py` extended and passing.
  - Download both repos into `/home/hf_data` (54 GB, public, no token; only requests to huggingface.co).
  - Byte model for all three checkpoints from their safetensors headers. Gate: the BF16 result equals exp02's
    51,249,200,128 bytes exactly.
  - Replay of the stop rule on the BF16 8-user cell.
- **Q1, server check per model (GPU, about 20 minutes each; NVFP4's first start also compiles the FlashInfer FP4
  kernel, up to 40 minutes, once).** Loads with a 36 GiB pool and checks:
  - which kernel vLLM reports for each quantized layer type (must not be the emulation or Marlin fallback);
  - `kv_cache_dtype` is BF16; block size 784 tokens; cached tokens land on the 784-token boundary;
  - request ids join, tool calls parse, a 3-call scripted session works;
  - TPOT at one user; GPU memory of the process, which fixes the model's pool size.
  - If the FlashInfer kernel does not build or work: `--linear-backend cutlass` (vLLM's own precompiled FP4
    kernel), recorded as a change.
- **Q2, smoke cell per model (2 users, 6 calls per session, about 15 minutes each)** with the final pool size. Gates:
  all data-quality gates of part 1; free memory never below 8 GiB (else the pool shrinks by 4 GiB and the smoke
  cell is repeated).
- **Q3, the chain of 11 cells** in the order above, unattended, counters on, resumable.
- **Q4, analysis and writeup**: tables, figures, README. I analyse each cell as it ends and report the first
  comparison (8 users, three precisions) as soon as cells 1 and 2 are done.

## Code changes (all in `research/24_spark_concurrency/`)

| File | Change |
|---|---|
| `sp_common.py` | `VARIANTS` table (`bf16`, `fp8`, `nvfp4`: repo, pool, extra flags, label) and `use_variant()`, which sets `MODEL`, the served name and the pool, and in `exp02()` also `rs.MODEL` and `rs.HF_HUB` (today only the BF16 cache folder is evicted and served). `--kv-cache-dtype bfloat16` added to the flags. |
| `sp_cell.py` | `--variant`; `cell.json` records variant, repo, snapshot, pool, block count and the kernel lines of the server log; the stable stop (runs `sp_stable.py` every 10 minutes after minute 60) and stop reason `stable`. |
| `sp_stable.py` (new) | The stop rule on a live or finished cell, reusing `Cell`, `grid`, `step_table`, `per_second` from `sp_analyze.py`; `--replay` for the check on `n08`. |
| `sp_modelbytes.py` (new) | Byte model of any checkpoint from safetensors headers (exp02's `model_bytes.py` hardcodes the BF16 folder and file pattern); writes `data/model_bytes_<variant>.json`. |
| `sp_chain.py` | Cells become (name, variant, users, hard limit); the old 16/32/24/12-user cells and the rescaling from `n08` are removed; `n08` is listed as BF16 at 8 users. |
| `sp_check.py` | `server --variant` (kernel lines, KV dtype, process memory, pool size), and the optional FP8 / FP4 GEMM loads for FLOPs per instruction. |
| `sp_analyze.py` | Variant per cell (byte model file, pool), tensor instructions/s, workload TFLOPS, a "ratio to BF16" table, model column in every table. |
| `sp_figs.py` | Users on the x-axis, one line per model in a fixed colour (BF16, FP8, NVFP4); stacked charts become three small panels, one per model. |
| `test_sp.py` | Tests for the byte model on synthetic headers, the stop rule on synthetic series, the variant plumbing. |
| `README.md`, `PLAN.md` | Title and question widened to three precisions; this plan appended as part 2; results. |

Nothing outside the topic folder changes (exp02 stays untouched). Nothing is committed unless you ask.

## Verification

- `test_sp.py` passes (existing 6 tests plus the new ones).
- Byte model: BF16 reproduces exp02's number exactly; FP8 and NVFP4 totals match the files' sizes minus embeddings,
  vision tower and MTP head.
- Q1 per model: native FP8 / FP4 kernels named in the server log; KV cache BF16; cached-token boundary at 784.
- Q2 per model: every data-quality gate passes (calls joined to server records, token counts equal on both sides,
  plugin totals equal vLLM's counters, sampler rates, probe coverage, no preemptions, counters present).
- Stop rule replay on `n08`: numbers at the stop point within 5% of the full-window numbers.
- Per cell after the run: gates pass; measured GPU traffic in decode-only steps against the byte model (BF16 gave
  1.02); steady period stable by thirds, including GPU reads.
- Cross-check of the comparison: at one user, step time x measured GB/s should equal the weights read per step for
  each model, as it does for BF16.
