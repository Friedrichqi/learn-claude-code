# 14 · Where does prefill/decode disaggregation stop paying? Turning points on 2 and 4 H200s

> **Status:** S0 (code, tests, NIXL install, pre-registered predictions) done 2026-10-05; GPU stages S1 → sweeps
> next. The full plan, with every definition, grid point, gate and budget, is [PLAN.md](PLAN.md).
>
> **Question (user, 2026-10-05):** 13's S0 found that decodes sharing an engine step with a prefill chunk wait for
> the whole chunk (21-45 % of Qwen3-32B call time at 4-8 agents per GPU). Splitting prefill (P) and decode (D) onto
> different GPUs removes that wait, but puts all prefill on fewer GPUs, moves KV between GPUs and duplicates the
> sessions' KV. "Come up with a comprehensive profiling experiment to measure the turning point of PD
> disaggregation ... across both 2 GPUs and 4 GPUs."
>
> **User decisions (2026-10-05):** scale "Core" (live sweep on 2 and 4 GPUs including secondary 4-GPU layouts;
> paired replay on 2 GPUs only); instrument "live + paired replay"; extra 2-GPU workloads: HEAVY tool mix and
> think-time sessions. Budget ≈ 164 H200 GPU-hours.
>
> **Setup:** Qwen3-32B bf16, thinking off, vLLM 0.29.0, NIXL 1.5.0 (pull mode, UCX, installed in `.deps/` only);
> the real s15 harness through 11's mu_sweep / mu_session, closed loop; H200 pairs and quads from one node
> (NVSwitch). Every arm goes through `pd_router.py`.
>
> **Commits:** none yet.

## Arms

| GPUs | colocated baselines | P/D layouts |
|---|---|---|
| 2 | dp2 (two TP=1), dp2c (dp2 with 2,048-token prefill chunks), tp2 | 1p1d |
| 4 | dp4, tp2dp2 (two TP=2), tp4 *(secondary)* | 2p2d, 3p1d, 1p1d-tp2 (P TP=2 + D TP=2), 1p3d *(secondary)*, 2p1d-dtp2 (two TP=1 P + one TP=2 D, *secondary*) |

## Pre-registered bounds (S0)

[`data/tables/S0_predictions.md`](data/tables/S0_predictions.md), from 12's p2a workload (3,793 calls; mean prompt
23.9k tokens, 3.4k computed, 95 output) and 11's step model. They are recomputed with S1's measured transfer rate
and router hop and frozen as `S1_predictions.md` before the sweeps.

- **2 GPUs:** one prefill GPU caps 1p1d at about 6,500 lead calls/h, below dp2 (7,900) and tp2 (8,700). The
  measured 2 x 12's p2a N = 8 (8,140) sits about 3 % above dp2's approximate bound.
- **4 GPUs:** 2p2d and 1p1d-tp2 are prefill-bound (13,100 / 12,100); 3p1d is bound by its one decode pool (about
  10 concurrent decodes, 10,000); dp4 and tp2dp2 reach 15,800 / 17,300.
- So P/D can only win where the colocated arms are far from their bound, at low-to-moderate N; the sweeps measure
  where that ends.

## Implementation notes (where the code refines PLAN.md)

- **Gates run themselves; the whole chain is queued at once.** H200 queues are days long, so every job is submitted
  with Slurm dependencies.
  - Each smoke cell (s1, s3) writes `data/PASS_<cell>` only when its gates pass:
    - s1: G1-G4 in `pd_calib.py decide`;
    - both: G5 on every smoke level, plus an analysis sanity check on the real logs. It needs ≥ 20 window lead
      calls, ≥ 50 % of them joined to the scheduler logs, a mean decomposition residual under 0.05 s, and KV
      actually pulled in P/D levels.
  - Sweep jobs check the marker first and run nothing without it.
  - s1 freezes `S1_predictions.md` before any sweep can start.
- **G2 tolerates numeric near-ties.** On the P→D path, D computes the last prompt token in a 1-token step; locally
  that token is computed inside a prefill chunk. A greedy token can therefore flip where the top two logits are
  nearly tied.
  - G2 passes on the plan's strict rule (first token 24/24, all 64 tokens ≥ 22/24).
  - It also passes when every divergence is a near-tie: the P/D token within 0.5 nats of the local top-1, from
    top-5 logprobs, with the first token identical in ≥ 22/24.
  - Wrong KV yields tokens the local model would not choose, so it still fails.
- **Secondary 4-GPU arms are dropped, not blocking.** If 1p3d, tp4 or 2p1d-dtp2 fails its s3 smoke level, or G6, it
  is dropped (`data/dropped_arms.json`) and reported. A failing core arm still blocks l4a / l4b.
- **Different sessions per N (LIGHT only).** LIGHT levels at N start at their own offset in 12's 512-session LIGHT
  pool (`pd_workloads.L_OFFSET`).
  - Every arm at one N, and both GPU counts at one N, run the same sequence.
  - The live sweep then records ~125 distinct sessions for the replay pool, instead of the ~40-50 it would record if
    every level started at the pool head.
  - HEAVY and think-time levels start at the pool head, as in 12.

## Files

| file | what it is |
|---|---|
| `PLAN.md` | the approved plan (definitions, hypotheses H1-H8, grid, gates G0-G7, budget, analysis) |
| `pd_workloads.py` | arms, levels, cells (N-major order, arms rotated, one duplicate level per job), vLLM flags per instance; `data/conditions.json` for 11's `MU_EXTRA_CONDITIONS` hook |
| `pd_scheduler.py` | `PDScheduler(HWScheduler)`: adds `pdhdr` and per-pool-walk `pdh` (P blocks held for D's read, D requests waiting for KV) |
| `pd_launch.py` | starts an arm's vLLM instances as children of one process (the sampler's server root), 20 s apart, with GPUs / cores / ports / NIXL side channels |
| `pd_router.py` | dp / pd / pass router: least-active sticky placement, P→D with kv_transfer_params, aggregated /health and /metrics, per-call hop log |
| `pd_scrape.py` | per-instance /metrics every 5 s (NIXL, prompt tokens by source, queues) |
| `pd_pipeline.sh`, `pd2.sbatch`, `pd4.sbatch`, `pd_cpu.sbatch` | one job = cells of levels; calibration for s1 / s3; resumable |
| `pd_calib.py` | S1 / S3: KV transfer sweep, delta pulls, P/D correctness, router hop, gate decision (`data/decisions.json`) |
| `pd_replay.py` | paired replay: pool from live recordings, per-N plans, `PDReplay(mu_replay.Replay)` |
| `pd_predict.py` | bottleneck bounds per arm |
| `pd_analyze.py` | levels → `data/levels.json`; tables T2-T10 incl. turning points (T7) and regime map (T8); gates (G5); calibration table (T0) |
| `pd_fake_vllm.py`, `test_pd.py` | fake vLLM (kv_transfer_params, completions, scheduler log); tests (`PD_SLOW=1` runs the real harness end to end) |

`data/` is gitignored except `*.md` / `*.svg`; raw streams go to Google Drive with
`python3 research/common/drive_data.py push 14`.

## Reproducing

```
pytest research/14_pd_crossover/test_pd.py                    # + PD_SLOW=1 for the end-to-end run
sbatch --export=NONE research/14_pd_crossover/pd_cpu.sbatch pd_predict.py
sbatch --export=NONE --time=04:00:00 research/14_pd_crossover/pd2.sbatch s1
sbatch --export=NONE --time=08:30:00 research/14_pd_crossover/pd2.sbatch l2a     # l2b, x2 alike
sbatch --export=NONE --time=03:30:00 research/14_pd_crossover/pd4.sbatch s3      # then l4a, l4b
sbatch --export=NONE research/14_pd_crossover/pd_cpu.sbatch pd_replay.py prepare --levels <l2a/l2b LIGHT live level dirs> --pool l2
python research/14_pd_crossover/pd_replay.py mkplan --pool l2 --n N   # N = 2 3 4 5 6 8 10 12 16
sbatch --export=NONE research/14_pd_crossover/pd2.sbatch r2a          # r2b alike
sbatch --export=NONE research/14_pd_crossover/pd_cpu.sbatch pd_analyze.py levels
python research/14_pd_crossover/pd_analyze.py tables
```
