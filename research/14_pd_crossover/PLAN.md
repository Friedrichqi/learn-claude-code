# research/14_pd_crossover — where does prefill/decode disaggregation stop paying? (2 and 4 H200s)

## Context

- **Where this comes from.** 13's S0 found that decodes sharing an engine step with a prefill chunk wait for the
  whole chunk (~1.2 s on Qwen3-32B). That is 21-45 % of 32B call time at N = 4-8 agents per GPU, the largest
  ceiling 13 measured.
- **The trade-off.** P/D disaggregation removes that wait, but it costs three things:
  - all prefill lands on fewer GPUs;
  - KV has to move between GPUs;
  - P's prefix cache and D's working set duplicate memory.
- **What 13 planned.** B3 was only 4 arms at N = 4, 8, 16 on two GPUs.
- **What the user wants now.** A comprehensive experiment that measures the turning point(s) on both 2 and 4 GPUs.
  Every detail is fixed here, with no open choices left.
- **Outcome:**
  - For every layout and metric, the crossover N (agents) with a 90 % CI, and the regime where each layout wins.
  - The mechanism behind each crossover: which resource binds.
  - All of it on the real harness (live) plus paired trace replay of the same live sessions.
- **Decisions (user, 2026-10-05):**
  - scale "Core";
  - instrument "Live + paired replay";
  - extra dimensions: HEAVY tool mix and think-time sessions (2 GPUs).
  - Budget ≈ 164 H200 GPU-hours.
- **Standing rules:**
  - New folder `research/14_pd_crossover/`; 11/12/13 are only imported, subclassed or run, never edited.
  - Only multi-GPU cells (every cell here is DP, TP or P/D > 1), so only H200 2- and 4-GPU jobs.
  - `--qos=standard --export=NONE`.
  - Real harness only; microbenchmarks only calibrate.
  - `data/` goes to Google Drive, gitignored except `*.md` / `*.svg`.
  - No commits unless asked.
  - After each watched job ends, list my GPU jobs and ask before `scancel`.

## 1. Definitions (fixed before any run)

- **Model / server.**
  - Qwen3-32B bf16, thinking off, vLLM 0.29.0.
  - Flags: 11's `VLLM_COMMON` (max-model-len 40960, prefix caching, prompt-token details, hermes tools, qwen3
    reasoning parser), with `--scheduler-cls pd_scheduler.PDScheduler`.
  - Defaults kept: `--gpu-memory-utilization` 0.92, `--max-num-batched-tokens` 8192, async scheduling.
  - KV is 256 KiB per token. The TP=1 pool holds 258,192 tokens, about 10 sessions of ~25k tokens.
- **Load.** N harness agents in closed loop (11's mu_sweep → mu_session → s15).
  - Think time 0 (12's fleet setup), except the think cell.
  - Inter-session gap U(5,30) s (mu_sweep's).
  - Sessions from 12's frozen list: LIGHT pool (14, 526); HEAVY pool (526, 1038).
- **Metrics.** Each level has a window W0 = level start + warm-up, W1 = W0 + window.
  - **Primary throughput:** lead calls/h, counting lead calls with t_recv ∈ [W0, W1].
  - **Primary latency:** lead call time p50 and p95, over calls with t_send ≥ W0 and t_recv ≤ W1.
  - **Component metrics:**
    - TTFT: wall time of the decode-side `tok1` − t_send.
    - TPOT per call: (fin − tok1)/(no − 1).
    - ITL p50/p99 from per-token times. Token 1 is at `tok1`; token k is at `u0` of the request's (k−1)-th
      `dcr` step (verified on 12's logs).
- **Advantage** of a P/D layout X over a baseline B at N: a(N) = ln(M_X/M_B), sign-flipped for lower-is-better
  metrics, so a > 0 means X is better.
  - Baselines are each colocated arm separately, plus the envelope "best colocated" (the best colocated arm at that
    N for that metric).
- **Turning points.**
  - Every sign change of a(N) between adjacent grid points, located by linear interpolation:
    N* = N_lo + (N_hi − N_lo)·a_lo/(a_lo − a_hi).
  - We expect two: **N*_low**, where P/D starts winning once stalls outweigh the transfer and hop overhead, and
    **N*_high**, where it stops winning because P compute, P cache or D capacity binds.
  - They are also reported per GPU (N*/G).

## 2. Hypotheses, pre-registered (numbers from 12/13 data; `pd_predict.py` recomputes them after S1 calibration and freezes them before the sweeps)

- **H1:** at ≤ 1 agent per GPU, P/D ≤ DP: there is no interference to remove, and P/D pays the transfer, the
  extra hop and a second render/tokenise on D.
- **H2:** 1p1d beats dp2 at 2-4 agents per GPU, driven by the removed decode stall (S0: 1.1-2.8 s per call).
- **H3:** P/D's TPOT p99 beats colocated at every N ≥ 2, until D's pool binds.
- **H4:** 1p1d's N*_high against dp2 is at ≈ 8-12 agents (2 GPUs).
  - P prefill demand ≈ 0.6 GPU-s per call, so P caps 1p1d at ≈ 5.7k lead calls/h.
  - P's cache holds about 10 sessions.
- **H5:** tp2 beats 1p1d on throughput at every N ≥ 8. 12's TP=2 measured 6,994 lead calls/h at N = 8, above
  1p1d's P cap. So the P/D window against the best colocated arm may be empty on 2 GPUs.
- **H6:** on 4 GPUs 3p1d is bound by D capacity: ~10 concurrent decodes on one TP=1 pool, so ≲ 9.5k calls/h, while
  dp4 peaks around 16k. 2p2d is P-bound at ≈ 11.4k; its N*_high against dp4 is ≈ 16-24.
- **H7:** 1p1d-tp2, whose pool per side is 776k tokens, has the widest P/D window on 4 GPUs.
- **H8:** dp2c (2k-token chunks) recovers part of P/D's latency gain at no throughput cost, so 1p1d's window
  against dp2c is narrower than against dp2.

## 3. Layouts (arms)

| arm | GPUs | instances (role, TP, GPUs) | router |
|---|---|---|---|
| dp2 | 2 | C tp1 g0; C tp1 g1 | dp |
| dp2c | 2 | as dp2, both with `--max-num-batched-tokens 2048` | dp |
| tp2 | 2 | C tp2 g0g1 | pass |
| 1p1d | 2 | P tp1 g0; D tp1 g1 | pd |
| dp4 | 4 | 4 × C tp1 | dp |
| tp2dp2 | 4 | C tp2 g0g1; C tp2 g2g3 | dp |
| 2p2d | 4 | P g0, P g1; D g2, D g3 (all tp1) | pd |
| 3p1d | 4 | P g0, g1, g2; D g3 | pd |
| 1p1d-tp2 | 4 | P tp2 g0g1; D tp2 g2g3 | pd |
| 1p3d *(secondary)* | 4 | P g0; D g1, g2, g3 | pd |
| tp4 *(secondary)* | 4 | C tp4 | pass |
| 2p1d-dtp2 *(secondary)* | 4 | P g0, P g1 (tp1); D tp2 g2g3 (mixed TP, which NIXL supports) | pd |

C = colocated, P = prefill, D = decode.

- **P/D instances** use NIXL in its default pull mode:
  - P: `--kv-transfer-config {"kv_connector":"NixlConnector","kv_role":"kv_producer"}`.
  - D: `{"kv_connector":"NixlConnector","kv_role":"kv_consumer","kv_load_failure_policy":"recompute"}`.
  - Each instance gets a unique `VLLM_NIXL_SIDE_CHANNEL_PORT`, and `VLLM_NIXL_SIDE_CHANNEL_HOST=127.0.0.1`.
- **What vLLM 0.29 does here (verified in the source):**
  - P computes only its uncached suffix and returns all prompt blocks.
  - D pulls N_prompt − floor_block(min(local hit, N−1)) tokens, i.e. only what it lacks, and recomputes the last
    prompt token.
  - P pins its blocks until D's read notification; the lease is 30 s, renewed by D's heartbeats.
- **NIXL install.** `pip install --no-deps --target research/14_pd_crossover/.deps nixl==1.5.0 nixl-cu13==1.5.0`.
  - vLLM imports `nixl._api`; the meta package picks `nixl_cu13`, which matches torch cu130 and bundles UCX with its
    CUDA transports.
  - `.deps` is gitignored and goes on PYTHONPATH for 14's servers only; `~/.venv` is untouched.
- **GPU visibility:**
  - Variant A: each instance sees its own GPUs first, then the job's other GPUs (13's approach).
  - Variant B: own GPUs only.
  - S1 picks one by rule: B if its NVLink byte share is ≥ 90 % and its transfer time is within 10 % of A's;
    otherwise A.
- **CPU cores** (sorted affinity list):
  - 4 cores per TP rank: TP1 4, TP2 8, TP4 16.
  - Then 1 core for the router, 1 for the sampler and scraper, and the rest for agents.
  - Jobs: 2-GPU `--cpus-per-task=32 --mem=192G`; 4-GPU `--cpus-per-task=48 --mem=320G`; `--constraint=h200`;
    `--time` = estimate + 1.5 h (≤ 12 h).

## 4. Router `pd_router.py` (new; aiohttp; reuses 13's `pd_proxy.FWD`, `P_PARAMS` by import)

- **Modes.**
  - `dp`: K colocated backends.
  - `pd`: lists of P and D backends.
  - `pass`: one backend.
- **Session placement.**
  - A session (`X-Session-ID`) is placed at its first call and stays there.
  - The backend is the one with the fewest active sessions; ties go to the lowest index.
  - In pd mode, P and D are chosen this way independently.
  - A session leaves the active set on `POST /tm/hint {"ev":"session_end"}`, or when it has had no call in flight and
    no activity for `--idle-s` (60 s for LIGHT think-0 and replay, 120 s for HEAVY, 300 s for think). This only
    changes the counts; a session never moves.
  - Live clients send the hint through 13's `bin/tmpython` with `TM_CLIENT_HINTS=1`. Replay sends it from
    `PDReplay`.
- **pd call.**
  - P gets the body plus `max_tokens:1, stream:false, kv_transfer_params:P_PARAMS`, with the same `X-Request-Id`.
  - D then gets the original body plus the `kv_transfer_params` P returned.
  - If P returns no params, D computes locally; this is counted.
  - A P error is passed through.
  - Both `/v1/messages` (live) and `/v1/completions` (replay) are handled.
- **Other endpoints.**
  - `GET /health`: 200 only if every backend is healthy (cached 5 s).
  - `GET /metrics`: the sum, over backends, of `vllm:generation_tokens_total`, `num_requests_running` and
    `num_requests_waiting`. mu_sweep's watchdog reads these.
  - `GET /router/stats`.
- **Log.** One JSON line per call: session, rid, path, backend(s), wall and monotonic times at router-in, P sent,
  P done, D sent, D done and router-out, statuses, number of P blocks, errors.

## 5. Grid, order and windows

| cell (= one job) | GPUs | arms × N | levels | job h | GPU-h |
|---|---|---|---|---|---|
| s1 smoke + calibration | 2 | §7 | — | 2.5 | 5 |
| l2a live LIGHT | 2 | {dp2, tp2, 1p1d} × N {1, 2, 4, 8}; dp2c × {4, 8}; + dup of 1p1d N=8 | 15 | 6.7 | 13.4 |
| l2b live LIGHT | 2 | {dp2, tp2, 1p1d} × {12, 16, 24}; dp2c × {12, 16}; + dup of dp2 N=12 | 12 | 5.7 | 11.4 |
| x2 live extras | 2 | HEAVY {dp2, tp2, 1p1d} × {4, 8, 16}; think {dp2, tp2, 1p1d} × {8, 16, 24} (think-scale 1, 11's lognormal draws); + dup of 1p1d think N=16 | 19 | 9.0 | 18.0 |
| s3 smoke | 4 | §7 | — | 2.0 | 8 |
| l4a live LIGHT | 4 | {dp4, tp2dp2, 2p2d, 3p1d, 1p1d-tp2} × {4, 8, 16}; {1p3d, tp4, 2p1d-dtp2} × {8, 16}; + dup of 2p2d N=8 | 22 | 10.6 | 42.2 |
| l4b live LIGHT | 4 | core 5 × {24, 32, 48}; secondary 3 × {32}; + dup of 3p1d N=32 | 19 | 9.2 | 36.7 |
| r2a replay | 2 | {dp2, dp2c, tp2, 1p1d} × {2, 3, 4, 5, 6}; + dup of 1p1d N=4 | 21 | 7.8 | 15.6 |
| r2b replay | 2 | same 4 arms × {8, 10, 12, 16}; + dup of dp2 N=10 | 17 | 6.7 | 13.3 |
| **total** | | | 125 + smoke | | **≈ 164** |

- **Order inside a cell: N-major, with the arms rotated.**
  - For the k-th N, the arm list is rotated left by k.
  - A partially finished job therefore still has complete comparisons at every finished N, and time drift does not
    line up with any one arm.
  - Servers restart between levels (~2-4 min; 12's 32B starts in ~100 s).
  - Each job's last level repeats one of its levels (the "dup"). This gives the run-to-run noise σ_run.
- **Live level timing:**
  - Prefix caches are reset on every instance (`/reset_prefix_cache?reset_external=true`).
  - Warm-up = mu_sweep stagger = min(300, 60N) s, then a 1,200-s window, ending in a hard stop.
- **Replay level timing:** the same stagger rule, a 900-s window (`--window-s` = warm-up + 900).
- **Resumability:** levels are resumable (`WINDOW_DONE`), cells too (`COMPLETE`). Raw logs are written node-local,
  then xz'd onto NFS. This is 13's tm2 pattern, generalised.

## 6. Replay (paired; 11's method, extended to any N)

- **Pool.** Completed LIGHT think-0 sessions recorded in l2a/l2b. Keep one recording per spec index (the
  earliest), ordered by spec index.
  - Need ≥ 48 sessions (≥ 3 per slot at N = 16).
  - If fewer, wait for l4a and add its recordings.
  - Rendered offline with `mu_replay.prepare_session` (exact prompt ids via 10's renderer, recorded output lengths,
    recorded gaps); CPU job `pd_cpu.sbatch`.
- **Per-N plan** (`pd_replay.py mkplan --n N`).
  - Slot i gets pool[i], pool[i+N], …
  - Synthetic t0/t1: start stagger i·S/N + U(0,5) s with S = min(300, 60N) (mu_sweep's rule), inter-session gap
    U(5,30) s, seeded by (slot, k).
  - mu_replay's slot loop reproduces these exactly, so every arm replays identical calls in identical order.
- **Run.**
  - `pd_replay.py run` = `PDReplay(mu_replay.Replay)`: it only adds the `session_end` hint after each session.
  - It is registered as `r_pd_…` conditions through `MU_EXTRA_CONDITIONS`.
  - Calls are `/v1/completions` with prompt ids, `max_tokens` = recorded, `ignore_eos`, temperature 0, through the
    router.
- **Fidelity check (gate G7).** Replay dp2 N=8 against live dp2 N=8, on engine-side measures:
  - arrival→finish p50/p90 within 10 %;
  - prefix-hit share within 3 points;
  - prefill GPU share within 5 points.
- **Known difference.** Replay skips chat render and tokenise, so live P/D's second front end on D is missing.
  - The analysis reports each replay crossover twice: raw, and with the live front-end difference Δfe(N) added.
  - Δfe(N) = live P/D minus live dp front end per call, interpolated over the live N grid.

## 7. Stages and gates

- **S0 (CPU, login node + `pd_cpu.sbatch`).**
  - Code and tests (§8); NIXL install.
  - Fake end-to-end for every layout (2- and 4-GPU, live and replay) against a fake vLLM.
  - `pd_predict.py` v0 → `data/tables/S0_predictions.md`.
  - **G0:** `test_pd.py` passes; 11/12/13 suites unchanged; every fake level ends `WINDOW_DONE`.
- **S1 (2-GPU, ~2.5 h, `pd2.sbatch s1`).**
  - (a) `nvidia-smi topo`; NIXL import (`nixl._api`, `nixl._bindings`) on the node.
  - (b) `pd_calib.py` on 1p1d:
    - KV transfer at {1k, 2k, 4k, 8k, 16k, 24k, 32k} tokens × 5 unique prompts: D load wait, NIXL xfer time and
      bytes, NVLink and PCIe counters at 0.1 s.
    - Delta pulls: a 16k base plus {16, 128, 1k, 4k} tokens.
    - Correctness: 24 rendered prompts from 12's p2a, greedy 64 tokens, through P→D vs served locally on D.
    - Then the visibility variant B at 8k and 24k.
  - (c) 13's `tm_vllm_probe.py` on a plain TP1 instance, a P-configured TP1 instance (does the KV layout change
    speed?) and a TP2 instance.
  - (d) Router overhead: 50 calls, `pass` vs direct.
  - (e) Reset check.
  - (f) 5-min smoke levels at N=4: dp2, dp2c, tp2, 1p1d (LIGHT); 1p1d HEAVY and think.
  - (g) A 3-min replay smoke, using a pool rendered from 12's p2a.
  - `pd_predict.py` v1 with the calibrated values → `S1_predictions.md`, frozen.
  - **Gates:**
    - **G1 NVLink path:** NIXL bytes / P-GPU NVLink TX ∈ [0.8, 1.25], and PCIe bytes during transfers < 10 % of
      them. If it fails, retry with `UCX_TLS=cuda_ipc,cuda_copy,sm,self,tcp` and the other visibility; if that fails
      too, **stop and report** (a custom connector needs the user's approval).
    - **G2 correctness:** first token identical 24/24, full 64 tokens ≥ 22/24, else **stop**.
    - **G3:** delta pulls ≤ delta + 16 tokens in ≥ 95 % of cases. If not, note it and continue.
    - **G4:** reset succeeds within 90 s on P and D. If not, the pipeline restarts servers before every level.
    - **G5 stability:** every smoke level has 0 router errors, 0 `nixl_num_failed_transfers`, 0
      `nixl_num_kv_expired_reqs` (if > 0: set `kv_lease_duration` = 120 s for all P/D instances and recheck), and
      no STALL.
  - If all gates pass: submit l2a, l2b, x2 and s3 at once, and report S1 to the user.
- **S3 (4-GPU, ~2 h).**
  - 5-min smoke at N=8 for all 8 four-GPU arms: xPyD routing balance, NIXL handshakes for every P↔D pair, per-GPU
    memory of foreign CUDA contexts.
  - **G6:** 2p1d-dtp2 correctness on 8 prompts; if it fails, drop that arm and report. Plus G5 for every arm.
  - If they pass: submit l4a and l4b.
- **Replay.** When l2a and l2b are done: prepare the pool, then submit r2a and r2b.
  - **G7** is evaluated on r2b's dp2 N=8 level. If it fails, replay is reported as engine-level only, and the 2-GPU
    crossovers fall back to live.
- **Watching jobs.** Session-only CronCreate checks every 2 h, because background Bash dies at 30 min. When a job
  ends, report it and ask about `scancel`.

## 8. Code (all new under `research/14_pd_crossover/`; 11/12/13 untouched)

Module names are unique across folders; every script carries the `_paths` two-line header.

| file | role | reuses |
|---|---|---|
| `pd_workloads.py` | layouts, arms, cells, levels (§5 order rule), server lines per instance, the conditions JSON for `MU_EXTRA_CONDITIONS`; CLI `build/cell/level/server/instances` | 12's `hw_workloads.level`, `LPOOL`/`HPOOL`; 11's `VLLM_COMMON` |
| `pd_scheduler.py` | `PDScheduler(HWScheduler)`: a `pdhdr` record (role, arm, idx, TP, GPUs, connector); every 2-s pool walk adds `pdh` (on P: finished requests still holding blocks for D's read, and their blocks; on D: requests waiting for KV); fail-open | 12's HWScheduler → 11's MUScheduler (already logs D's KV waits as `adm.load` / `adm.he`) |
| `pd_launch.py` | starts a layout's instances as children of one process (the sampler's single server root): `CUDA_VISIBLE_DEVICES`, cores, ports, side-channel ports, `.deps` path, `MU_SCHED_LOG` per instance; staggered 20 s; writes `instances.json`, then `READY` or `FAILED`; SIGTERM kills every process group and leftover GPU processes | 13's `tm2_pipeline.start_srv` logic |
| `pd_router.py` | §4 | 13's `pd_proxy` constants |
| `pd_scrape.py` | every 5 s, each instance's `/metrics` → `scrape.<i>.jsonl` (nixl_*, prompt_tokens_by_source, external_prefix_cache_*, request gauges, token counters) | 11's `mu_scrape.parse_metrics` |
| `pd_pipeline.sh`, `pd2.sbatch`, `pd4.sbatch`, `pd_cpu.sbatch` | job pipeline for G GPUs: per level, launch or reuse the layout, router, sampler (12's `hw_sampler.py` over all GPUs, server root = launcher), scraper, cache reset, then mu_sweep (live, `--python` 13's `bin/tmpython`, `TM_CLIENT_HINTS=1`, think-scale from the level) or `pd_replay.py run`; then store and stop; resumable | 13's `tm2_pipeline.sh` (copied and generalised) |
| `pd_calib.py` | S1 transfer, delta, correctness and router-overhead tests | 13's `tm_vllm_probe.py` (run as is) |
| `pd_replay.py` | `prepare` / `mkplan` / `run` (§6) | 11's `mu_replay` (prepare_session, Replay) |
| `pd_predict.py` | bottleneck bounds per layout: X_P = n_P / E[prefill GPU-s per call] (step model on the workload's (context, new tokens)); X_D = n_D·b_max / (E[L]·t_step(b_max)), with b_max = D pool / mean session tokens; cache-capacity N per pool; closed-loop saturation N_sat = X_max·(Z + R0) → which resource binds first, and at what N | 12/13 step-model coefficients, S1 calibration, 12's p2a workload stats |
| `pd_analyze.py` | joins and metrics (§9) → `data/tables/*.md` | 11's `mu_analyze` (Server, fit_step_model, attribute_steps), 12's `hw_analyze.gpu_busy_intervals`, 13's `tm_ceilings.pure_decode_cost` |
| `pd_figures.py` | SVG figures (load the dataviz skill before writing it) | — |
| `pd_fake_vllm.py`, `test_pd.py` | a fake server with kv_transfer_params, `/v1/completions`, `/metrics` and `/reset_prefix_cache`; tests | 13's `tm_fake_vllm` behaviour |
| `PLAN.md`, `README.md` | this plan; then the writeup (status, short answer, results, data inventory, source map, cleanup notes) | — |

Also: one index row in `research/README.md`, and a project memory entry once execution starts.

## 9. Analysis (`pd_analyze.py`)

- **Join per call.** Client capture (or the replay's `replay_calls.jsonl`) ↔ router record ↔ scheduler logs, by
  (instance role, id).
  - P and D share the same `X-Request-Id`, so the join is per role.
  - All instances share CLOCK_MONOTONIC on the node; wall time comes from `hdr`/`clk`.
- **Per-call time decomposition** (mean s per lead call; the components sum to the call time, and the residual is
  reported):
  - front end in;
  - queue (P or C: arrival → first admission);
  - prefill (admission → prefill done);
  - P→D hand-off (P fin → D arrival);
  - KV wait (D `adm.load`);
  - D queue;
  - pure decode;
  - decode stall: the step's GPU time minus the step model's pure-decode cost, charged to each decoding request.
    13's S0 method, with the step model fitted per instance type over all of the cell's logs.
  - front end out.
- **Per level, per arm:**
  - throughput (lead calls/h, all calls/h, output tok/s);
  - latency (call p50/p95, TTFT p50/p95, TPOT p50/p99, ITL p99);
  - stall share;
  - per GPU: busy %, prefill share of busy time, GPM SM/tensor/DRAM, NVLink and PCIe GB/s mean and p99;
  - KV per instance: live % mean/max, prompt tokens by source (local compute / local hit / transferred), recompute
    after eviction, P held blocks, D KV-wait count, preemptions, NIXL failures and expiries;
  - transfer per call: tokens, GiB, wait;
  - workload comparability: prompt tokens, new prefill tokens and output tokens per call; share of calls with a
    history edit (prompt diverges > 64 tokens before the previous prompt's end); sessions completed.
- **Uncertainty.**
  - Live: σ_run = pooled sd of ln(metric) over the dup pairs (floor 0.03; if fewer than 4 pairs completed, use
    max(estimate, 0.07) from 12's five N=8 levels). Var a(N) = 2σ².
  - Replay throughput: σ_rep from replay dups (floor 0.01).
  - Replay latency: paired per-call log ratios on the calls completed in both arms, block bootstrap over slots
    (2,000 resamples).
- **Crossover CI.**
  - Monte Carlo, 10,000 draws of a(N) ~ Normal(â, σ) at every grid N; the crossing is recomputed within the
    bracketing interval.
  - Report the 5-95 % range and P(no crossing).
  - A crossing is **resolved** if both bracketing points have |â|/σ ≥ 1.645.
  - Ties in the regime map: the best two arms within 1.645·σ·√2.
- **Tables (`data/tables/`):**
  - T0 calibration and S1 gates;
  - T1 predictions vs measured;
  - T2 levels for 2 GPUs (live), T3 for 4 GPUs, T4 replay, T5 extras;
  - T6 decomposition;
  - **T7 turning points**: per family (2-GPU replay, 2-GPU live, 4-GPU live, HEAVY, think) × P/D arm × baseline
    (each colocated arm plus the envelope) × metric (lead calls/h, call p50, call p95, TPOT p99, TTFT p95). Columns:
    N*_low and N*_high, 90 % CI, resolved, per-GPU N*, binding resource at N*_high.
  - T8 regime map;
  - T9 mechanism vs N: P busy, P held blocks, P recompute, D pool live, D waits, transfer;
  - T10 scaling checks: 2p2d at 2N vs 1p1d at N per GPU, dp4 vs dp2, replay fidelity.
- **Figures:**
  - lead calls/h vs N (2-GPU live, 4-GPU live, 2-GPU replay);
  - call p50/p95 vs N;
  - TPOT p99 and TTFT p95 vs N;
  - a(N) with 90 % bands and N* markers;
  - decomposition stacked bars;
  - regime-map heatmaps;
  - mechanism lines;
  - extras against LIGHT.

## 10. Verification

- `pytest research/14_pd_crossover/test_pd.py`:
  - router: least-active placement and its release, xPyD with 2P×2D fakes (P gets `max_tokens` 1 and the params; D
    gets P's params with the same rid), completions path, health and metrics aggregation, error pass-through, no
    params → local;
  - workloads: every level's GPU and core count matches its cell, kv-transfer-config per role, unique side-channel
    ports, deterministic rotation, conditions load through 11's `MU_EXTRA_CONDITIONS`;
  - PDScheduler imports into vLLM 0.29's classes;
  - replay: slot assignment, seeded gaps, request bodies;
  - analysis: P/D join, decomposition sums, crossover estimator recovers a known crossing (N = 7) on synthetic
    curves with correct CI coverage;
  - slow (`PD_SLOW=1`): the pipeline end-to-end on `pd_fake_vllm` for dp2, tp2, 1p1d, dp4, 2p2d, 3p1d (live and
    replay).
- 11/12/13 test suites pass unchanged. `git status` shows changes only under `research/14_pd_crossover/` plus the
  index row.
- GPU: gates G1-G7 as above. Each analysis is cross-checked against an independent source:
  - NIXL bytes vs NVML NVLink bytes;
  - prompt_tokens_by_source vs the scheduler's `adm.he` / hit;
  - client vs server call time;
  - dp2 at N=2k per GPU vs 12's p2a at N=k (H200 TP1).

## 11. Risks and mitigations

- **4-GPU H200 jobs may queue for a long time.** Mitigations: ≤ 12-h resumable jobs, N-major order (partial jobs
  are still usable), 2-GPU cells run meanwhile.
- **Live 4-GPU crossovers are noisier** (no 4-GPU replay, the user's "Core" choice). They are reported with σ_run
  and resolved flags, never as more precise than they are.
- **NIXL lease expiry under a D backlog is silent** (stale blocks get read). Watch `nixl_num_kv_expired_reqs`; any
  level with a value > 0 is flagged (G5 rule).
- **Foreign CUDA contexts under visibility A could eat HBM.** Check per-GPU memory by process in S1/S3.
- **NIXL's KV layout could make P/D instances' kernels slower.** Measured in S1(c) and reported.
- **The replay pool may be too small.** Fall back to adding l4a's recordings (§6).

## 12. Deliberately excluded

- **NIXL `bidirectional_kv_xfer`.** It loads D's previous-turn blocks by count without checking tokens. Qwen3's
  re-rendered history and the harness's in-place compaction edits would load wrong KV.
- **Push mode:** same capability, more constraints.
- **vLLM's internal DP balancer:** it has no session affinity.
- **dp4 with 2k chunks, Qwen3-8B, 4-GPU replay:** outside the chosen scale.
