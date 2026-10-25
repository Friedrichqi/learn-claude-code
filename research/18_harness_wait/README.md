# 18 · Harness-side waiting: can a crowded server be relieved by the agent pausing itself?

> **Status: complete.** The smoke (4 cells), the pilot (8 cells), the 16 pre-registered main cells and one
> exploratory cell all ran on 2026-10-24. The answer is in the Short answer and in S3.
>
> **Dates (2026-10-24, UTC):**
> - Code and CPU tests before 03:00.
> - Smoke 03:00-03:31; pilot 03:32-06:46.
> - Pre-registration frozen at 06:47 (`prereg.md`, `prereg.sha256`).
> - Main run 06:47-21:56; exploratory cell 21:56-23:48.
>
> **GPU time:** 77.3 of the 100 GPU-h cap. That is smoke 1.8, pilot 12.4, main 59.4 and the exploratory cell 3.7,
> all on GPUs 0-3 of this host and inside the user's lab GPU sheet claims (GPU-hour log below).
>
> **Commits:** none. Everything here is uncommitted.
>
> **Question (user, 2026-10-24):**
> - Expose the live vLLM server status to the harness.
> - Add a `wait(seconds)` tool, a specialization of bash `sleep`. It runs at the end of an agentic round and
>   holds the session's next request while the server is crowded, so the server can drain instead of evicting KV
>   and recomputing it.
> - This is a gate in another form: the harness side decides, not the server.
>
> **User decisions (2026-10-24):**
> - The **model** decides when and how long to wait. It sees a one-line server status after every round.
> - Two model arms differ only in the status they see: stock `/metrics` fields, or the session's share of the KV
>   pool. Each has a harness-decided, timing-only twin on the same status.
> - The references are topic 11's server-side session gate and no wait.
> - The testbed is topic 11's workload: Qwen3-32B, TP=2 on two H100s, with the pool pinned to topic 11's
>   16,137 blocks.
> - **Budget:** GPUs 0-3 and at most 100 GPU-hours. The user enters the lab GPU sheet claims. No commits unless
>   asked.
>
> **Model / server:**
> - `Qwen/Qwen3-32B`, bf16, thinking off, `--max-model-len 40960`.
> - vLLM 0.31.0 (`/data/fritz/venv`), TP=2 on H100 80GB pairs {0,1} and {2,3}.
> - Prefix caching on, the append-only Qwen3 template of topic 16, FCFS, recompute preemption.
> - **KV pool:** 16,137 blocks of 16 tokens (`--num-gpu-blocks-override`). The natural TP=2 pool is 20,012
>   blocks (S1).
>
> **Plan:** `~/.claude/plans/now-i-want-to-jiggly-waffle.md` (approved 2026-10-24).
>
> **Follow-ups:** [`FOLLOWUPS.md`](FOLLOWUPS.md), a brainstorm after the run (2026-10-25, CPU only).
> - Offline analyses of these cells.
> - A prototype simulator, under `followups/`.
> - A ranked list of next experiments.

**Contents** — Short answer · What was built · Arms · Cells and budget · Metrics and rules · S1 smoke · S2 pilot ·
S3 main run · Exploratory: turn-start holds · Conclusions · Caveats · GPU-hour log · Reproduction · Data
inventory · Changes during the run

## Short answer

- **When the model decides, waiting does not relieve the server.** Qwen3-32B saw the server status every round
  and had a `wait` tool. At N=16 it did no better than not waiting: −146 and −193 work lead calls/h, with
  intervals that include 0. At N=32 it did worse: 0.70× and 0.73× of `none`, both "hurts" by the pre-registered
  rule. At N=8, where the server is not crowded, the share-view arm lost a third of the throughput.
- **The same status, read by a fixed harness rule, recovers half or more of the server gate's gain.** No model
  is in that loop.
  - The share rule holds a session while the sessions ahead of it plus its own need more than 0.9 of the pool.
    It reached 2.82× `none` at N=16 (recovery ratio R = 0.65) and 2.18× at N=32 (R = 0.53).
  - Backoff on the stock `/metrics` fields reached 2.42× (R = 0.51) and 1.76× (R = 0.34).
  - Topic 11's server-side session gate stays ahead, at 3.81× and 3.23×.
- **Why the model fails: it waits in loops of short waits, and every wait is itself a model call.**
  - 81-99 % of its wait-only rounds come in runs of 10 or more within one turn. The longest runs go on for 93-100
    rounds, and a turn's budget is 100 calls.
  - On a crowded server each of those calls first re-prefills the session's evicted context. At N ≥ 16, wait-only
    calls took 22-32 % of the GPU's wall time, nearly all of it evicted recompute.
  - The waits barely track the signal. At N=8 only 0-2 % of them followed a crowded status line. At N ≥ 16 they
    followed one 0-9 points more often than the work calls did.
  - The harness rule decides without a model call.
- **Which status the decider saw made no measurable difference**, for either decider. Pool share against stock
  `/metrics`: the share rule was ahead of backoff by 679 and 761 calls/h, but with intervals that include 0.
- **How the rules work:**
  - A session held by the share rule comes back to 2-4 % of its cached prefix at the median. The gain is that the
    other sessions' prefixes survive.
  - Backoff releases in herds: 85-94 % of its releases come within 2 s of another.
  - At N=32 a quarter of the share holds ran into the 600-s cap, and throughput swung between 1.6k and 7.4k
    calls/h per 10-min block. The worst blocks were the ones with the most cap releases (exploratory).
- **Exploratory: holding at turn starts as well nearly closes the gap to the gate.**
  - In one cell at N=16, the share rule that also held before a turn's first call reached 3.55× `none`
    (R = 0.91 [0.70, 1.16]). Holding only after tool rounds reached 2.82×.
  - A turn-start hold costs the session little, because its context was already evicted during the think pause.
  - The model cannot hold there, because it needs a call to decide.
- **Caveats:**
  - one model and one tool text;
  - one 90-min cell per arm and N, with a block bootstrap that sees only the variation inside a cell;
  - the pool pinned to 16,137 blocks.

## What was built

**The harness: `s15_integrated_harness/`.** Everything is default-off and byte-identical when unset. The
repository's tests still pass, apart from two failures that already exist on git HEAD.
- **`hold_runtime.py`, a new sibling of `trace_runtime.py`.** It provides:
  - the `wait` tool;
  - the server-status client, which fails open: a timeout of 0.5 s, a circuit breaker, it never raises, and it
    never puts "429" or "529" in error text;
  - the status line, in a stock or a share view;
  - two hold rules:
    - **backoff:** hysteresis (enter when a request waits for KV or KV in use ≥ 0.92; leave when nothing waits for
      KV and KV < 0.85) plus jittered exponential steps;
    - **share:** hold while the working sets of the sessions ahead plus the session's own exceed τ × pool, with
      sessions ranked first come, first served by the start of their current turn.
- **`code.py` wiring:**
  - `wait` is advertised when `S15_WAIT_TOOL=1`. A `wait` call is deferred to the end of the round, after the
    round's other tools. That holds in the lead, the subagent and the teammate loops.
  - The status line is appended to the newest user message once per round (append-only). A re-issued call never
    gets a second one.
  - A harness hold (`S15_HOLD_POLICY`) runs at the top of the lead loop after a tool round (`mid`) or before a
    turn's first call (`start`).
  - The lead reports its turn start with every status request.
- **Tests:** `tests/test_s15_server_hold.py` (12 tests).

**The server: this folder.**
- **`wt_scheduler.py`, `WTScheduler(MUScheduler)`.** It is topic 11's observer plus:
  - per-session resident blocks (incremental: +1 on commit, −1 on evict);
  - each session's cached prefix (binary search over its last prompt + reply hashes);
  - its working set, mid-turn flag and turn start;
  - global running / waiting / KV, and the 10 s windows the status needs;
  - a JSON snapshot written to `/dev/shm` every 0.2 s by a writer thread;
  - a `wt` record in the sched log every second.
- **`wt_server_ext.py`, an ASGI `--middleware` in vLLM's API process.** It serves `GET /wt/status`:
  - `view=stock`: only what stock `/metrics` exposes, read from the process's own Prometheus registry every 0.5 s;
  - `view=share`: the stock fields plus the snapshot and the caller's place in line;
  - a registry of callers that are holding or waiting, which keeps them mid-turn;
  - an audit log.
- **`research/11_multiuser_kv/mu_scheduler.py`, ported to vLLM 0.31.0** (the 0.29 paths are unchanged):
  - a version allowlist;
  - the 0.31 waiting-queue view;
  - the `defer` and `stale` queue-head reasons;
  - async and sync classes;
  - commit / evict callbacks;
  - `MU_STRICT=1` to fail closed. Before the port, the 0.29 assert made it serve stock silently on 0.31, so a gate
    arm would have run as no gate.

  Its CPU test (`test_mu_scheduler.py`, 10 tests incl. 4 new) passes on 0.31.
- **`mu_session.py`** exports `S15_SESSION_ID`, which equals its `X-Session-ID`. **`mu_scrape.py`** takes a GPU
  list.

**The drivers.**
- **`wt_workloads.py`.** Topic 11's 160 session specs rebuilt from frozen seeds: `data/seeds/` holds nine
  site-packages codebases plus the s15 harness at git 59a24c1.
  - Codebases, templates, think draws and nonces are identical to topic 11 (160/160).
  - 12 specs have other M2/M3 targets, because typer 0.27.3 and the harness changed.
  - The file also holds the condition table (smoke, pilot, main, contingency).
- **`wt_sweep.py`.** Topic 11's closed loop with a fixed clock: warm-up, then a measurement window, then stop.
  - Lanes are 16 MB (harness_eval is excluded).
  - The arm's `S15_*` settings are set once per cell.
  - A watchdog notes idle-server spans.
- **`wt_pipeline.py`.** It runs one cell per GPU pair (no Slurm):
  1. Checks: empty GPUs, an empty cell folder, and the GPU-hour cap.
  2. Starts vLLM, pinned to socket-0 cores.
  3. Sends a 1-token probe, then checks the scheduler header and `/wt/status`, and fails fast on a mismatch.
  4. Runs the scrape, the KV-event counter and the sweep.
  5. Stops everything, verifies the GPUs are empty, and adds the cell to the tally.
- **`wt_analyze.py`.** Window-clipped tables:
  - H1 holds and waits, H2 decisions and oscillation, H3 prefix survival, H4 fairness, H5 throughput and latency
    (primary), H6 overhead;
  - the recovery ratio, with a 10-min block bootstrap;
  - the smoke and pilot gates.

**Tests** (all pass, CPU only):
- `test_wt_scheduler.py`: the real vLLM 0.31 scheduler in the loop, 4 tests;
- `test_wt_server_ext.py`: 7 tests;
- `test_wt_e2e.py`: real s15 sessions in one window cell per arm against a fake server;
- topic 11's `test_multiuser.py` still passes.

## Arms

| arm | `wait` tool | status the model sees each round | who holds, and when |
|---|---|---|---|
| `none` | — | — | nobody |
| `m_stock` | advertised | `[server] running 12, waiting 5 (4 for KV memory); KV memory in use 97%; prompt-cache hits 4% and preemptions 0 in the last 10 s` | the model, after its round's other tools |
| `m_share` | advertised | `[server] KV pool 258,176 tokens; sessions ahead of you hold 231,040; your context 24,512, 18,304 still cached; 5 requests waiting` | the model, after its round's other tools |
| `h_backoff` | — | — (timing-only twin of `m_stock`) | the harness, after tool rounds: the backoff rule |
| `h_share` | — | — (timing-only twin of `m_share`) | the harness, after tool rounds: the share rule |
| `sgate` | — | — | the server: topic 11's gate, K=8, 5 s idle |

**Why the twins hold only after tool rounds:** the model can never hold a turn's first call, because it needs a
call to decide. The exploratory contingency cell `x16_h_share_start` adds turn-start holds.

## Cells and budget

| stage | cells | content |
|---|---|---|
| S1 smoke (~4 GPU-h) | `s_nat` | natural pool, 1 user |
| | `s_small`, `s_gate2`, `s_mstock`, `s_hshare` | a 1,100-block pool, 16k window, 4 users, KV events on; checks the observer against the KV events, the gate at K=2, and the two arms end to end |
| S2 pilot (~12-16 GPU-h; 15 min warm-up + 30 min window) | `p08_none` ∥ `p16_none` | does the regime reproduce on H100 TP=2? |
| | `p16_m_stock` ∥ `p16_m_share` | do the model arms wait, and with sane seconds? |
| | `p16_h_share` ∥ `p16_h_backoff` | do the rules trigger without deadlock? |
| | `p16_h_share_t75` | τ, only if still open |
| prereg | — | freeze the parameters, the tool text, the cell order and the rules |
| S3 main (64 GPU-h; 20 min warm-up + 90 min window) | N=16 | all 6 arms + the A/A `w16_none_aa` |
| | N=32 | all 6 arms |
| | N=8 | `none`, `m_stock`, `m_share` |
| contingency | reruns; `x16_h_share_start` | |

## Metrics and rules (frozen in `prereg.md` at 06:47 UTC, before the first main cell)

- **Primary metric: work lead calls per hour in the window.** A lead call whose reply is only `wait` is not work.
- **Uncertainty:** a block bootstrap over the window's nine 10-minute blocks (4,000 draws, seed 18), with the cells
  resampled independently.
- **Secondary metrics** (reported, not decided on):
  - turns/h;
  - Kaplan–Meier turn p50/p95 (turns started in the window, censored at its end);
  - call latency, plus hold and wait per call;
  - hit rate;
  - GPU share on evicted and on preempted recompute;
  - prompt tokens per call.
- **The noise floor:** Δ_AA = |w16_none_aa − w16_none|.
- **R1, recovery:** R_X = (X − none) / (sgate − none) at N=16 and N=32.
  - X "recovers" if R ≥ 0.5 and the 2.5th percentile of its gain X − none is above Δ_AA.
  - X "hurts" if the 97.5th percentile of the gain is below −Δ_AA.
- **R2, decider:** D = m_X − h_X on the same view. The decider matters if |D| > Δ_AA and D's 95 % interval
  excludes 0.
- **R3, information:** S = share − stock within each decider, by the same criterion.
- **R4, no harm at N=8:** m_X(8) − none(8) ≥ −Δ_AA, and the hit rate within 2 points of none(8).

## S1 smoke (2026-10-24, 03:00-03:31 UTC; 1.8 GPU-h)

**Setup.** Four cells, each with 4 users on a forced-contention pool: 1,100 blocks (17,600 tokens) and a 16k
window. A 2-min warm-up, then a 10-min window. vLLM was ready 70-115 s after start.

| check | s_small (none) | s_gate2 (gate K=2) | s_mstock (model, stock line) | s_hshare (harness, share rule) |
|---|---|---|---|---|
| lead calls joined client ↔ scheduler log | 375/375 | 414/414 | 480/480 | 588/588 |
| admission hit = `cache_read_input_tokens` | 100 % | 100 % | 100 % | 100 % |
| evictions: observer / vLLM KV events (removed hashes) | 86,449 / 86,623 | 46,612 / 46,612 | 70,555 / 70,555 | 48,404 / 48,585 |
| preemptions: observer / vLLM counter | 8 / 8 | 4 / 4 | 10 / 10 | 4 / 4 |
| /wt/status age p50 / p99 (ms) | 309 / 408 | 111 / 111 | 240 / 494 | 245 / 494 |
| spans in the traces | — | — | 32 `wait` | 582 hold checks, 18 holds |
| gate: peak active sessions | — | 2 | — | — |

- **The natural pool at TP=2** on these H100s is 20,012 blocks (320,192 tokens), from vLLM's "Overriding
  num_gpu_blocks" line. The `s_nat` cell was therefore not run.
- **The observer's eviction count matches vLLM's KV-event stream in two of four cells.** In the other two, vLLM
  reports 174 and 181 more removed hashes (0.2-0.4 %).
  - The observer is internally exact: commits − evictions = hashed blocks, checked on CPU with vLLM's real
    scheduler. On `s_small`, commits − evictions = 1,098 of the 1,099 usable blocks.
  - So the extra events come from vLLM's event accounting, not from missed evictions. The cause was not
    pinned down.
  - Admission hits, which every token class uses, match the client's usage on 100 % of calls.
- **The stock view against /metrics** (s_mstock, 555 status reads):
  - KV in use differs by 0.007 at the median.
  - It differs by up to 0.40 at p95. On a 1,100-block pool one prefill moves usage by tens of points between two
    reads up to 1 s apart.
  - Running requests agree within ±1 on 99 % of reads.
- **The model does use the tool unprompted.** Qwen3-32B called `wait` when the status line showed KV memory at
  82-96 %:
  - it waited 1-30 s, with reasons such as "KV memory in use 92%" and "Let model server catch up with KV memory.";
  - that was 3-5 waits per 37-170 lead calls.

## S2 pilot (2026-10-24, from 03:32 UTC)

**P1 (`p08_none` ∥ `p16_none`, 15-min warm-up + 30-min window): the regime reproduces.**

| cell | work lead calls/h [10-min blocks] | hit rate | GPU on evicted recompute | call p50 / mean | turn p50 (KM) | censored turns |
|---|---|---|---|---|---|---|
| p08_none | 6,242 [4,970, 7,011] | 89.3 % | 1.7 % | 1.7 / 3.8 s | 93 s | 10.7 % |
| p16_none | 2,138 [1,807, 2,644] | 13.8 % | 74.3 % | 18.0 / 22.0 s | 1,516 s | 53.3 % |

- **Doubling the users cuts the work done by two thirds** (ratio 0.34). Topic 11 measured 0.32 on one H200
  (3,077 → 976 lead calls/h).
- **The mechanism is the same:** at N=16, 74 % of GPU time goes to re-prefilling evicted prefixes (topic 11: 74 %).
- **The two gates pass:** `none16` thrashes and `none8` does not.
- **This hardware is about twice as fast.** H100 TP=2 runs the same pool about 2× faster than topic 11's H200 TP=1
  (6,242 vs 3,077 lead calls/h at N=8).
- **Step model fit on these cells:** R² 1.000, median error 2.0 %.

**P2 (`p16_m_stock` ∥ `p16_m_share`, tool description v1): the model waits, but in loops.**

| cell | work lead calls/h | wait-only lead calls | waits per 100 work calls | requested p50 / p95 (s) | hit rate | GPU on evicted recompute |
|---|---|---|---|---|---|---|
| p16_none (P1) | 2,138 [1,807, 2,644] | 0 | — | — | 13.8 % | 74.3 % |
| p16_m_stock | 1,072 [808, 1,498] | 525 | 91.6 | 2 / 15 | 15.8 % | 83.6 % |
| p16_m_share | 2,010 [1,375, 2,880] | 502 | 50.0 | 1 / 30 | 34.2 % | 77.3 % |

- **About half the sessions chain wait-only rounds.** Examples: 86 waits in 89 lead calls, and 75 in 80, often
  of 1 s, with reasons such as "KV memory pressure" and "Let server catch up.".
- **Each such round is a model call on the crowded server.** The session's prefix was probably evicted while it
  waited, so the call often re-prefills the whole context only to decide to wait again. In the stock arm the
  evicted-recompute share rose (74 → 84 %) and the work done halved.
- **The share line led to fewer and longer waits, and a better hit rate** (34 % vs 14 %). The work done stayed
  level with `none`.
- **Response, made before the pre-registration:** a v2 tool description (`S15_WAIT_TEXT=v2`). It adds one factual
  sentence: a wait-only response still costs one model step on the server, so prefer one longer wait to many short
  ones, and keep working when the server is not crowded. P2b tests v2 on both model arms. The main run uses the
  version that loops less.

**P3 (`p16_h_share` ∥ `p16_h_backoff`, τ = 0.9): the harness-decided holds work.**

| cell | work lead calls/h | turns/h | hit rate | GPU on evicted recompute | call p50 | turn p50 (KM) | holds | hold p50 / p95 (s) | bunched releases |
|---|---|---|---|---|---|---|---|---|---|
| p16_none (P1) | 2,138 | 58 | 13.8 % | 74.2 % | 18.0 s | 1,516 s | — | — | — |
| p16_h_share | **5,760 [5,371, 6,363]** | 158 | 83.7 % | 18.9 % | 2.3 s | 291 s | 5.3 % of 2,821 checks | 51 / 202 | 50 % |
| p16_h_backoff | 4,422 [3,447, 5,784] | 96 | 71.3 % | 45.7 % | 4.2 s | 454 s | 28.7 % of 2,183 checks | 3 / 71 | 86 % |

- **The share rule nearly restores N=8's throughput at N=16:** 5,760 vs 6,242 lead calls/h. It holds rarely but long.
- **A held session loses most of its own prefix** (5 % kept at the median), as the plan expected. The work calls
  overall still hit 92 % of their ideal prefix, against 19 % without any wait.
- **Backoff gains 2.1×** while every session reacts to the same signal: 86 % of its releases come within 2 s of
  another.
- **Fairness falls:** Jain's index over sessions' work calls is 0.48 for share vs 0.64 for none.
- **The pilot gates all pass.** τ = 0.9 holds without deadlock (max 673 s per session), so the τ = 0.75 cell is
  not needed.

- **The selection rule, written 2026-10-24 05:15 UTC before P2b ran:**
  - Take the wait-only lead calls per 100 work lead calls in each model arm.
  - Average them over the two arms.
  - The main run uses the version with the lower average. A tie (within 10 %) goes to v2.
  - Throughput does not enter the choice, so the choice cannot select on the outcome.

**P2b (`p16_m_stock_v2` ∥ `p16_m_share_v2`): v2 loops less, and is chosen.**

| cell | work lead calls/h | wait-only lead calls | per 100 work calls | hit rate | GPU on evicted recompute |
|---|---|---|---|---|---|
| p16_m_stock (v1) | 1,072 | 525 | 97.9 | 15.8 % | 83.6 % |
| p16_m_stock_v2 | 1,320 [1,052, 1,696] | 473 | 71.7 | 19.2 % | 85.0 % |
| p16_m_share (v1) | 2,010 | 502 | 50.0 | 34.2 % | 77.4 % |
| p16_m_share_v2 | 2,096 [1,422, 2,476] | 351 | 33.5 | 26.8 % | 77.2 % |

- **The rule picks v2:** a mean of 52.6 wait-only calls per 100 work calls, against 73.9 for v1. The main run's
  model arms use v2 (`wt_workloads.MAIN_WAIT_TEXT`).
- **Neither version lifts the model arms above `none`** (2,138) at N=16 in the pilot.
- **The pilot cost 12.4 GPU-h** (14.2 with the smoke).

## S3 main run (2026-10-24, 06:47-21:56 UTC; 59.4 GPU-h)

**What ran.**
- The 16 cells of `prereg.md`, in its order: 8 rounds of two cells, one per GPU pair.
- Each cell had a 20-min warm-up and a 90-min window, and cost 3.71 GPU-h.
- Every cell exited 0, so none was rerun.
- Tables: `data/tables/main.md` (`wt_analyze.py`; step model refit on the 16 cells: R² 1.000, median error 2.8 %).

**Throughput: work lead calls/h [95 % block bootstrap], and × `none` at the same N.**

| arm | N=8 | N=16 | N=32 |
|---|---|---|---|
| `none` | 6,713 [4,760, 8,332] | 1,717 [1,603, 2,256]; A/A 1,739 [1,637, 1,896] | 1,807 [1,662, 2,082] |
| `m_stock` (model, stock line) | 6,564 [5,378, 7,814] · 0.98× | 1,571 [1,154, 2,521] · 0.91× | 1,266 [992, 1,664] · 0.70× |
| `m_share` (model, share line) | 4,595 [2,981, 5,693] · 0.68× | 1,525 [1,254, 2,156] · 0.89× | 1,319 [1,214, 1,484] · 0.73× |
| `h_backoff` (harness, stock signals) | — | 4,157 [3,451, 5,159] · 2.42× | 3,173 [2,971, 3,440] · 1.76× |
| `h_share` (harness, share signals) | — | 4,837 [3,596, 6,314] · 2.82× | 3,934 [1,664, 7,192] · 2.18× |
| `sgate` (server gate) | — | 6,539 [4,702, 7,991] · 3.81× | 5,837 [4,214, 7,014] · 3.23× |

- **The regime is topic 11's.**
  - Going from 8 to 16 users cuts `none` to a quarter (0.26×), and N=32 stays there.
  - At N ≥ 16, 76-78 % of GPU time is re-prefill of evicted prefixes, and the hit rate is 1-5 %.
  - The server gate gains 3.8× and 3.2× (topic 11 on one H200: 3.5-3.8×).
- **The A/A pair agrees within 21 calls/h (1.2 %)**, well inside either cell's interval (widths 653 and 259
  calls/h).

**Secondary metrics** (H5 and H4 of `main.md`; KM = Kaplan–Meier over turns started in the window):

| cell | hit rate | GPU on evicted recompute | turns/h | turn p50 / p95, KM (s) | censored turns | call p50 (s) | hold + wait per call (s) | Jain index |
|---|---|---|---|---|---|---|---|---|
| w08_none | 89.6 % | 2.8 % | 142.0 | 100 / 429 | 3.7 % | 1.5 | 0 | 0.689 |
| w08_m_stock | 89.2 % | 4.7 % | 119.3 | 161 / 644 | 3.9 % | 1.7 | 0.45 | 0.760 |
| w08_m_share | 87.3 % | 3.4 % | 114.0 | 157 / 819 | 4.7 % | 1.7 | 1.35 | 0.655 |
| w16_none | 4.6 % | 78.3 % | 37.3 | 1,094 / – | 23.2 % | 22.6 | 0 | 0.585 |
| w16_none_aa | 1.4 % | 78.3 % | 37.3 | 1,984 / 3,584 | 27.3 % | 22.8 | 0 | 0.658 |
| w16_m_stock | 16.8 % | 84.0 % | 26.7 | 2,029 / 4,151 | 34.2 % | 21.1 | 3.28 | 0.622 |
| w16_m_share | 7.0 % | 85.4 % | 32.0 | 2,164 / 2,999 | 33.3 % | 24.0 | 1.28 | 0.539 |
| w16_h_backoff | 70.6 % | 40.4 % | 84.0 | 613 / 2,644 | 11.8 % | 3.7 | 4.03 | 0.603 |
| w16_h_share | 78.4 % | 27.3 % | 110.0 | 350 / 1,418 | 9.6 % | 2.6 | 3.06 | 0.637 |
| w16_sgate | 88.0 % | 4.5 % | 132.0 | 335 / 1,032 | 7.5 % | 1.9 | 0 | 0.659 |
| w32_none | 1.4 % | 75.9 % | 64.0 | 776 / – | 33.0 % | 50.9 | 0 | 0.574 |
| w32_m_stock | 1.4 % | 79.9 % | 30.0 | 5,012 / – | 65.9 % | 50.1 | 7.28 | 0.480 |
| w32_m_share | 1.4 % | 81.8 % | 31.3 | 5,227 / – | 61.7 % | 51.1 | 4.88 | 0.521 |
| w32_h_backoff | 49.8 % | 62.7 % | 73.3 | 1,097 / 4,329 | 28.2 % | 8.8 | 22.39 | 0.620 |
| w32_h_share | 67.7 % | 45.3 % | 88.7 | 1,278 / 2,819 | 23.0 % | 3.7 | 17.17 | 0.508 |
| w32_sgate | 86.8 % | 5.1 % | 153.3 | 698 / 1,246 | 12.7 % | 1.9 | 0 | 0.557 |

A dash means that more than 5 % of the turns were still running when the window ended, so the curve never
reached p95.

- **At N=16 the twins shorten the turns.** Their median turn is 350 s and 613 s, against 1,094 s with no wait
  and 335 s with the server gate.
- **At N=32 they trade the median for the tail.**
  - Their KM medians (1,097 s and 1,278 s) are above `none`'s 776 s.
  - But their p95 is finite, where `none`'s is not reached: `none`'s censored turns had already run 2,664 s at
    the median when the window closed.
  - They also finish more turns per hour (73 and 89, against 64).
- **The model arms lengthen the turns at N=8 and N=32.** At N=32 the median goes from 776 s to 5,012-5,227 s.
- **At N=16 the turn quantiles are too noisy to compare.** The model arms' medians (2,029 s and 2,164 s) lie near the
  A/A cell's 1,984 s, and the two `none` cells' medians differ by 890 s.

**Pre-registered decisions.** Δ_AA = |1,739 − 1,717| = 21 work lead calls/h.

| R1 recovery | N=16: gain [95 %] | R [95 %] | verdict | N=32: gain [95 %] | R [95 %] | verdict |
|---|---|---|---|---|---|---|
| m_stock | −146 [−460, 223] | −0.03 [−0.10, 0.05] | neither | −541 [−718, −347] | −0.13 [−0.19, −0.08] | **hurts** |
| m_share | −193 [−463, 73] | −0.04 [−0.10, 0.02] | neither | −489 [−619, −372] | −0.12 [−0.17, −0.09] | **hurts** |
| h_backoff | 2,440 [2,057, 2,817] | 0.51 [0.41, 0.62] | **recovers** | 1,366 [1,213, 1,511] | 0.34 [0.29, 0.41] | neither |
| h_share | 3,119 [2,425, 3,837] | 0.65 [0.49, 0.83] | **recovers** | 2,127 [869, 3,489] | 0.53 [0.22, 0.89] | **recovers** |
| sgate (the denominator) | 4,821 [4,107, 5,484] | — | beats noise | 4,029 [3,381, 4,628] | — | beats noise |

- **R2, decider: the decider matters in all four contrasts, always in the harness's favor.**
  - m_stock − h_backoff: −2,586 [−3,033, −2,087] at N=16 and −1,907 [−2,084, −1,717] at N=32.
  - m_share − h_share: −3,312 [−4,039, −2,580] and −2,615 [−3,981, −1,358].
- **R3, information: no contrast differs.**
  - m_share − m_stock: −47 [−433, 300] at N=16 and 53 [−123, 214] at N=32.
  - h_share − h_backoff: 679 [−101, 1,463] and 761 [−493, 2,113].
- **R4, no harm at N=8: neither model arm passes.**
  - m_stock: −149 calls/h, hit rate −0.4 points.
  - m_share: −2,119 calls/h, hit rate −2.3 points.
  - Two intervals computed for this README (not pre-registered quantities):
    - m_stock's −2 % has a block-bootstrap interval of [−1,028, 764], which includes 0. The rule fails it against
      a floor of 21, and the A/A difference that sets that floor has an interval of its own of [−169, 145].
    - m_share's loss is clear: [−3,034, −1,170].
- **h_backoff's "recovers" at N=16 is marginal.** R = 0.51 against the 0.5 threshold, with an R interval of
  0.41-0.62.

**Why the model's waits do not help** (H1 and the exploratory H1b of `main.md`):

| cell | wait-only calls | per 100 work calls | requested p50 / p95 (s) | in runs ≥ 10 | sessions with such a run | longest run | crowded: at wait-only / at work calls | GPU: wait-only calls (evicted recompute) / work calls |
|---|---|---|---|---|---|---|---|---|
| w08_m_stock | 848 | 8.6 | 5 / 10 | 80.9 % | 5 | 100 | 1.8 % / 0.2 % | 3.3 % (0.9 %) / 90.9 % |
| w08_m_share | 2,179 | 31.6 | 1 / 10 | 96.7 % | 12 | 100 | 0.0 % / 0.0 % | 7.4 % (1.5 %) / 85.2 % |
| w16_m_stock | 990 | 41.9 | 5 / 20 | 87.6 % | 7 | 98 | 88.2 % / 83.2 % | 30.7 % (28.2 %) / 67.7 % |
| w16_m_share | 748 | 32.7 | 2 / 10 | 99.3 % | 7 | 100 | 36.4 % / 29.8 % | 21.9 % (20.7 %) / 76.3 % |
| w32_m_stock | 924 | 48.6 | 10 / 60 | 90.3 % | 16 | 93 | 100 % / 100 % | 31.8 % (29.2 %) / 66.3 % |
| w32_m_share | 759 | 38.3 | 10 / 30 | 94.6 % | 15 | 97 | 76.4 % / 67.2 % | 23.1 % (21.7 %) / 73.6 % |

Column notes:
- "Per 100 work calls" counts the traced `wait` spans (H1).
- A run is a stretch of consecutive wait-only rounds within one turn. The longest runs (93-100) take up nearly the
  whole of a turn's budget of 100 lead calls.
- "Crowded" means the status line the model had just read met its harness twin's rule.
- GPU time is a share of the window's wall time.

- **The model waits often and briefly, in loops.**
  - It makes 9-49 waits per 100 work calls, asking for 1-10 s at the median.
  - 81-99 % of its wait-only rounds belong to runs of 10 or more.
- **Each wait-only round is a model call on the server it means to relieve.**
  - While the session slept its context was evicted, so the call first re-prefills it.
  - At N ≥ 16 the wait-only calls took 22-32 % of the GPU's wall time, 21-29 points of it evicted recompute.
  - The work calls got 66-76 %.
- **The waits barely follow the signal.**
  - At N=8 almost none of them followed a crowded line.
  - At N ≥ 16 the share of crowded lines before a wait was only 0-9 points above the share before a work call.
  - The stock line is crowded before 83-100 % of the work calls at N ≥ 16, so it says little there. Topic 11's
    logs had requests waiting in 98-99 % of steps.
- **The waits do not keep anyone's cache.** At N ≥ 16 the model arms' work calls got 2-24 % of their ideal prefix
  hit (H3), against 2-7 % for `none`.
- **The status lines are not the cost.** Prompt tokens per call are 22.5k-24.2k in every model cell, about what
  `none` sends (22.4k-24.7k).

**What the harness twins do** (H1-H3 of `main.md`, and `wt_blocks.py`):
- **Backoff holds often and briefly, and in herds.**
  - It held at 31 % (N=16) and 49 % (N=32) of its checks, for p50 2.6 s and 6.8 s (p95 71 s and 226 s).
  - 85 % and 94 % of its releases came within 2 s of another.
  - It keeps the waiting queue short (a mean of 0.4 and 1.2 requests, against 4.9 and 20.3 for `none`) and the
    throughput steady: 2,964-3,474 calls/h in every 10-min block at N=32.
- **Share holds rarely, but long.**
  - It held at 5 % and 7 % of its checks, for p50 18.5 s and 56 s (p95 231 s and 600 s).
  - At N=16 every hold ended because the session fit (longest 412 s), with 2-6 sessions holding at a time.
  - At N=32 about 20 of the 32 sessions were holding at any time. 102 of its 398 releases in the window came from
    the 600-s cap; the other 296 came because the session fit.
- **At N=32 the share rule's throughput swings with the cap releases** (`w32_h_share`, one cell, an association in
  time):

| 10-min block | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|---|
| work lead calls/h | 6,486 | 7,368 | 3,834 | 2,004 | 1,794 | 5,664 | 3,210 | 3,414 | 1,632 |
| holds released by the cap | 1 | 0 | 9 | 13 | 17 | 13 | 19 | 12 | 18 |
| holds released because the session fit | 54 | 67 | 51 | 32 | 10 | 30 | 24 | 20 | 8 |

  - **A likely loop:**
    1. A cap release admits a session whether or not it fits, so the pool is overcommitted again and prefixes
       thrash.
    2. Turns slow down, the sessions ahead stay mid-turn longer, and more holds reach the cap.
  - The data cannot separate cause from effect, and block 5 is an exception (13 cap releases, 5,664 calls/h).
- **A held session loses its own prefix.**
  - Share holders kept 4.0 % (N=16) and 1.9 % (N=32) of their cached prefix at the median by their release.
  - The gain is the others' prefixes surviving: the twins' work calls got 55-87 % of their ideal prefix, against
    2-7 % for `none` and 97-98 % for the server gate.
- **Fairness is about `none`'s.** At N ≥ 16, Jain's index over the sessions' work calls is 0.51-0.64 for the
  twins and 0.57-0.66 for `none`.
- **Not counted as releases:** holds cut short when a warm-up filler session was stopped (SIGTERM). There were 11 of
  them in `w32_h_share`'s window, 8 in `w32_h_backoff`'s and 2 in `w16_h_backoff`'s.

**Integrity (all 16 cells):**
- Every cell is `COMPLETE`, with a full 90.0-min window after a 20.0-min warm-up.
- 0 server errors.
- 100 % of the lead calls joined client to scheduler log (3,390-13,120 per cell).
- The admission hit equals `cache_read_input_tokens` on 100 % of the calls.
- The observer's preemptions equal vLLM's counter in every cell (0-14).
- Every `hold_start` has its `hold_end`.
- The gate's peak was 8 active sessions in both `sgate` cells.
- `/wt/status` age p99 was 495-498 ms for the stock view and 673-756 ms for the share view (see Caveats).
- `wt_verify.py` recomputed the primary metric, the wait-only calls, turns/h and the hit rate from the raw captures
  and traces: 16/16 cells agree.
- `wt_analyze.py` regenerates `main.md` and `main.json` byte for byte.
- The GPUs were empty after every cell.

## Exploratory: turn-start holds (`x16_h_share_start`, 2026-10-24, 21:56-23:48 UTC; 3.7 GPU-h)

Not pre-registered. This is one cell, run on the budget left after the main run, as the plan's contingency
allowed. The numbers come from `data/tables/contingency.md` and `wt_blocks.py`. `contingency.md` refits the step
model on all 17 cells; that moves the main cells' GPU shares by at most 0.1 points and changes nothing else.

**What ran.**
- `w16_h_share`'s share rule at N=16: τ = 0.9, a re-check every 2 s with jitter, the 600-s cap.
- It also held before a turn's first call (`S15_HOLD_PHASES=start,mid`).
- Everything else was as in the main run: pair A, a 20-min warm-up and a 90-min window.
- The model cannot hold there, because it needs a call to decide. So the cell asks how much of the gap to the
  server gate comes from where the harness holds.

**N=16, side by side** (H5, R and H4 of `contingency.md`):

| | `none` | `h_share` (after tool rounds) | `x16_h_share_start` (turn starts too) | `sgate` |
|---|---|---|---|---|
| work lead calls/h [95 %] | 1,717 [1,603, 2,256] | 4,837 [3,596, 6,314] | **6,096 [4,250, 8,429]** | 6,539 [4,702, 7,991] |
| × `none` | 1 | 2.82× | **3.55×** | 3.81× |
| R [95 %] | — | 0.65 [0.49, 0.84] | **0.91 [0.70, 1.16]** | — |
| hit rate | 4.6 % | 78.4 % | 85.0 % | 88.0 % |
| GPU on evicted recompute | 78.3 % | 27.3 % | 11.0 % | 4.5 % |
| turns/h | 37.3 | 110.0 | 129.3 | 132.0 |
| turn p50 / p95, KM (s) | 1,094 / – | 350 / 1,418 | 314 / 1,057 | 335 / 1,032 |
| censored turns | 23.2 % | 9.6 % | 6.7 % | 7.5 % |
| hold per work call (s) | 0 | 3.06 | 3.12 | 0 |
| Jain index | 0.585 | 0.637 | 0.635 | 0.659 |

- **Holding at turn starts as well took the share rule from 2.82× to 3.55× `none`, near the gate's 3.81×.**
  - Exploratory contrasts, with the decisions' bootstrap:
    - against `w16_h_share`: +1,259 calls/h [147, 2,461]. Its lower end is about the size of the A/A pair's own
      interval, [−169, 145];
    - against `w16_sgate`: −443 [−1,584, 737], an interval that includes 0.
  - Every secondary metric moved toward the gate: the hit rate, the evicted recompute, turns/h and the turn tail.
- **No hold reached the cap.**
  - All 416 releases in the window came because the session fit: 189 at turn starts and 227 after tool rounds.
  - On average 3.9-6.8 sessions were holding in each 10-min block, against 2.3-6.4 without turn-start holds.
  - The blocks ran at 4,128-8,454 work calls/h.
- **Where a session is held decides what the hold costs it** (`wt_blocks.py`, by hold point):

| cell: hold point | releases | held p50 / p95 (s) | context still cached when the hold began, p50 | prefix kept at release, p50 / mean |
|---|---|---|---|---|
| `x16_h_share_start`: turn start | 189 | 107 / 342 | 1.3 % | 100 % / 84 % |
| `x16_h_share_start`: after a tool round | 227 | 5.6 / 77 | 99.8 % | 94 % / 56 % |
| `w16_h_share`: after a tool round | 374 | 18.5 / 231 | 99.8 % | 4.0 % / 28 % |

  - **A turn-start hold costs its session almost nothing.** By then the context had already been evicted during
    the think pause: when such a hold began, 1.3 % of it was still cached at the median. The turn's first call
    re-prefills either way.
  - **Holds after tool rounds got shorter and kept more of their prefix:** 94 % at the median (mean 56 %),
    against 4 % (mean 28 %) in the cell without turn-start holds.
- **Integrity:**
  - The cell is `COMPLETE` with a 90.0-min window, and there were 0 server errors.
  - 11,411 of 11,411 lead calls joined. The admission hit equals `cache_read_input_tokens` on 100 % of the calls.
  - The observer's preemptions equal vLLM's counter (5).
  - All 517 `hold_start`s have their `hold_end`.
  - Status age p99 was 732 ms (share view).
  - `wt_verify.py` agrees, and GPUs 0-1 were empty afterwards.
- **Limits:** this is one cell, not pre-registered, with the main run's τ and cap, at N=16 only. Whether
  turn-start holds also fix the share rule's cap releases at N=32 was not tested.

## Conclusions

1. **The harness can relieve a crowded server by pausing the agent, if the harness decides.**
   - A fixed rule on the live status gave 2.4-2.8× the work at N=16 and 1.8-2.2× at N=32.
   - The same model given the same status and a `wait` tool gave 0.7-0.9×.
   - Backoff needs only fields that stock `/metrics` exposes, so a client can do it against an unmodified vLLM.
2. **A model-called wait is a poor gate here, for two measured reasons.**
   - **Deciding to wait costs a model call on the crowded server**, and that call re-prefills the evicted context.
     Backoff's holds are as short as the model's waits (p50 2.6-6.8 s), but backoff re-checks with a status poll,
     not a model call.
   - **The model's waits barely follow the status.** It loops on wait(1)-wait(10) even when the line shows nothing
     waiting.

   This is one model with one tool text. The pilot's v1 → v2 change, which added the cost sentence, reduced the
   loops but did not remove them.
3. **The harness rules leave a third to two thirds of the server gate's gain on the table.**
   - The gate sees every admission and keeps a stable set of 8 active sessions.
   - The rules act only at round ends, on a status sampled every 0.2-0.5 s.
   - They also herd (backoff) or overcommit through the cap at N=32 (share).
   - In one exploratory cell at N=16, holding at turn starts as well closed most of the gap (R = 0.91
     [0.70, 1.16]). A turn start is where a session has the least cache left to lose.
4. **Which signal the rule reads mattered less than holding sessions out at all.**
   - The stock signals are global, the same for every session, yet backoff on them gained 1.8-2.4×.
   - The share rule ranks the sessions and knows their working sets. Its lead of about 700 calls/h is within the
     noise of these single cells.
5. **Under contention, waiting does not protect the waiter's own cache.**
   - At the median, share holders came back to 2-4 % of their prefix, and the model's waiters at N=32 to 2 %.
   - The gain is collective: fewer sessions compete for the pool, so the active ones keep theirs.
   - Where turn starts were held too (exploratory), evicted recompute fell to 11 % of GPU time, and holds after
     tool rounds kept 94 % of their prefix at the median.

## Caveats

- **One model and one tool text.** The model is Qwen3-32B with thinking off, and the text is v2, chosen in the
  pilot by a loop-count rule. Another model, a policy sentence (topic 06: models act on cost information only with
  one) or a status line that states how long to wait might use the tool better. None of these were tested.
- **One cell per arm and N, and one 90-min window each.**
  - The block bootstrap resamples 10-min blocks within a cell. It sees the variation inside a cell, not that
    between runs.
  - Blocks are autocorrelated: `w32_h_share` swings over tens of minutes. So nine blocks may understate the
    uncertainty.
- **The noise floor is one A/A pair's point difference** (21 calls/h), and that pair's own interval is
  [−169, 145].
  - No R1-R3 verdict would change with a floor as wide as that interval.
  - R4 for `m_stock` would: its −149 would pass.
  - Separately, `h_backoff`'s "recovers" at N=16 rests on R = 0.51 against the 0.5 threshold.
- **The pool is pinned** to 16,137 blocks, topic 11's H200 pool, on hardware whose natural pool is 20,012. The
  turning point moves with the pool.
- **The twins' parameters were set in the N=16 pilot:** τ = 0.9, the 600-s cap and the backoff thresholds. At N=32
  the cap bit often, and a longer cap or another τ was not tested.
- **The twins hold only after tool rounds**, which is all the model can do. Turn-start holds ran in one
  exploratory cell, at N=16 only (see "Exploratory: turn-start holds").
- **Status age.** The share view's p99 age (673-756 ms) was above the smoke's 500 ms target; the stock view's was
  495-498 ms.
  - The share view includes the engine snapshot, which is taken after an engine step at most every 0.2 s, so long
    prefill steps add to its age.
  - The harness discards a status older than 5 s.
- **Turn latency at N ≥ 16 is heavily censored** (10-66 % of the turns), so the KM quantiles there are rough.
- **The sessions are topic 11's workload.** These are 4-turn coding sessions on 10 local codebases with 30-s think
  pauses. Other workloads may differ.

## GPU-hour log

| stage | cells | GPU-h |
|---|---|---|
| S1 smoke | 4 (`s_small`, `s_gate2`, `s_mstock`, `s_hshare`) | 1.80 |
| S2 pilot | 8 (P1, P2, P3, P2b) | 12.39 |
| S3 main | 16 | 59.42 |
| exploratory | 1 (`x16_h_share_start`) | 3.71 |
| **total** | 29 | **77.33 of 100** |

From `data/gpu_hours.jsonl` (`wt_pipeline.py hours`). Each cell is charged 2 GPUs × its wall time, from server
start to the GPUs being empty.

## Reproduction

```bash
PY=/data/fritz/venv/bin/python
# CPU tests
research/16_epic_harness/harness_eval/.venv/bin/python -m pytest tests -q          # harness (incl. test_s15_server_hold.py)
CUDA_VISIBLE_DEVICES= $PY research/11_multiuser_kv/test_mu_scheduler.py           # the 0.31 port
CUDA_VISIBLE_DEVICES= $PY research/18_harness_wait/test_wt_scheduler.py
$PY research/18_harness_wait/test_wt_server_ext.py
$PY research/18_harness_wait/test_wt_e2e.py
$PY research/18_harness_wait/test_wt_analyze.py                                    # the decision rules, KM, wait runs
# workload and conditions
$PY research/18_harness_wait/wt_workloads.py build
$PY research/18_harness_wait/wt_workloads.py conditions
# GPU (after the sheet claim): one cell per pair, two at a time; wt_chain.sh runs rounds back to back
$PY research/18_harness_wait/wt_pipeline.py round s_small:A s_gate2:B
research/18_harness_wait/wt_chain.sh "w16_none:A w16_none_aa:B" "w08_m_share:A w16_m_share:B" \
  "w32_h_backoff:A w32_h_share:B" "w32_m_stock:A w16_sgate:B" "w08_none:A w16_m_stock:B" \
  "w32_sgate:A w08_m_stock:B" "w32_m_share:A w32_none:B" "w16_h_share:A w16_h_backoff:B" \
  > research/18_harness_wait/data/jobs/chain_main.log 2>&1                         # S3, prereg.md's order
research/18_harness_wait/wt_chain.sh "x16_h_share_start:A" > research/18_harness_wait/data/jobs/chain_contingency.log 2>&1
$PY research/18_harness_wait/wt_pipeline.py hours
# tables
MAIN=w16_none,w16_none_aa,w08_m_share,w16_m_share,w32_h_backoff,w32_h_share,w32_m_stock,w16_sgate,w08_none,w16_m_stock,w32_sgate,w08_m_stock,w32_m_share,w32_none,w16_h_share,w16_h_backoff
$PY research/18_harness_wait/wt_analyze.py --smoke s_small,s_gate2,s_mstock,s_hshare
$PY research/18_harness_wait/wt_analyze.py --pilot p08_none,p16_none,p16_m_stock,p16_m_share --out research/18_harness_wait/data/tables/pilot_p1p2.md
$PY research/18_harness_wait/wt_analyze.py --pilot p08_none,p16_none,p16_m_stock,p16_m_share,p16_h_share,p16_h_backoff --out research/18_harness_wait/data/tables/pilot_p1p2p3.md
$PY research/18_harness_wait/wt_analyze.py --cells p16_none,p16_m_stock,p16_m_share,p16_m_stock_v2,p16_m_share_v2 --out research/18_harness_wait/data/tables/pilot_p2b.md
$PY research/18_harness_wait/wt_analyze.py --cells $MAIN --out research/18_harness_wait/data/tables/main.md
$PY research/18_harness_wait/wt_verify.py --json research/18_harness_wait/data/tables/main.json
$PY research/18_harness_wait/wt_analyze.py --smoke $MAIN                          # integrity of the main cells
$PY research/18_harness_wait/wt_analyze.py --cells $MAIN,x16_h_share_start --out research/18_harness_wait/data/tables/contingency.md
$PY research/18_harness_wait/wt_blocks.py --cells w16_h_share,w32_h_share,w16_h_backoff,w32_h_backoff,x16_h_share_start
```

## Data inventory

| path | what |
|---|---|
| `prereg.md`, `prereg.sha256` | the pre-registration and the sha256 of it and of the code at freezing |
| `data/seeds/` (+ `SEEDS.json`) | the frozen codebases (sha256 per tree) |
| `data/sessions.jsonl`, `data/sessions_meta.json` | the manifest and its comparison with topic 11 |
| `data/conditions.json` | every cell's server and client settings (`MU_EXTRA_CONDITIONS`) |
| `data/cells/<cond>/` | one folder per cell, 29 in all: `s_*` smoke, `p*` pilot, `w*` main, `x16_h_share_start` exploratory. Each holds `window.json`, `cell_log.jsonl`, `sweep.log`, `COMPLETE`, `runs/<label>/` (capture, trace, meta per session) and `server/<pair>_<UTC>/` (`vllm.log`, `server_info.json`, `sched.jsonl.xz`, `wt_ext.jsonl`, `scrape.jsonl`, `metrics_final.prom`, `kvevents.json`) |
| `data/tables/main.{md,json}` | the S3 tables and decisions (16 cells) |
| `data/tables/contingency.{md,json}` | the same plus `x16_h_share_start` and its exploratory contrasts (step model refit on 17 cells) |
| `data/tables/pilot_{p1p2,p1p2p3,p2b}.{md,json}` | the pilot tables |
| `data/gpu_hours.jsonl` | the GPU-hour tally, one line per cell |
| `data/jobs/` | one pipeline log per cell (`<UTC>_<cond>_<pair>.log`) and the chain logs (`chain_pilot.log`, `chain_main.log`, `chain_contingency.log`) |

`data/` is gitignored apart from its `*.md` tables, and goes to Google Drive (`research/DATA.md`).

## Changes during the run

- **2026-10-24:** `wt_server_ext.py`'s audit log is line-buffered. The first version flushed every 20 records,
  and the pipeline test found it empty after a stop.
- **2026-10-24, pilot:** the tool description v2 (`S15_WAIT_TEXT`). This was before the freeze; see S2 above.
- **2026-10-24, after the freeze (06:47 UTC), analysis code only.** Neither file is in `prereg.sha256`, and neither
  changes the run.
  - `wt_analyze.decisions()` implements prereg.md's R1-R4 as written. It is tested on synthetic cells in
    `test_wt_analyze.py`.
  - `wt_verify.py` recomputes the primary metric, wait-only calls, turns/h and hit rate from the raw captures and
    traces. On the six pilot cells it agrees with `wt_analyze` exactly (6/6).
- **2026-10-24, after main round 2 (10:40 UTC), analysis code only:** an exploratory table **H1b wait loops** for
  the model arms (`wt_analyze.h1b_row`, `wait_runs` and `twin_would_hold`, tested in `test_wt_analyze.py`).
  - **Why it was added:** `w08_m_share` made 2,179 wait-only calls although no status line it showed was crowded.
    Its messages show the model calling `wait(1)` for "server congestion" on a line that said 0 requests waiting,
    then repeating it until the turn's call budget ran out.
  - **What it reports:** runs of consecutive wait-only rounds within a turn, how each run ended, and how often the
    status the model read met its harness twin's rule.
  - **Added at 20:40 UTC, after round 7:** the GPU time of wait-only calls, work calls and other calls
    (`gpu_by_reply`). It uses the same per-step accounting as the H5 GPU columns, and its parts sum to the busy
    share on every model cell.
  - Nothing else in the output changes, and the decisions do not use it.
- **2026-10-24, after the main run (22:30 UTC), analysis code only:** `wt_blocks.py`, exploratory.
  - For each 10-min block of a twin cell, it lists the work calls/h, the hold releases by reason and the mean
    number of sessions holding.
  - It reads the raw captures and traces, like `wt_verify.py`, and changes no other output.
  - **Why it was added:** `w32_h_share`'s blocks ranged from 1.6k to 7.4k calls/h, and 102 of its holds reached
    the cap.
- **2026-10-25, after the exploratory cell (00:00-00:15 UTC), analysis code only:**
  - **Fix: H3 matched holds across sessions.**
    - `h3_row` paired each `hold_end` with a `hold_start` by span id alone. Span ids count up per session, so they
      repeat across sessions: `x16_h_share_start`'s 517 holds had 319 distinct ids. An end was therefore often
      paired with another session's start.
    - It now pairs within the session, tested in `test_wt_analyze.py`. The bug showed up as a mean prefix kept of
      172 % in `x16_h_share_start`.
    - Only H3's "prefix kept" for the share-hold cells changed (p50 / mean):
      - `w16_h_share`: 3.7 % / 32.1 % → 4.0 % / 28.4 %;
      - `w32_h_share`: 2.3 % / 42.1 % → 1.9 % / 24.8 %;
      - `p16_h_share` (pilot): 5.1 % / 30.5 % → 4.8 % / 27.8 %;
      - `x16_h_share_start`: 100 % / 172 % → 100 % / 67 %.
    - The model arms' waits are measured within one event and did not change. No other table, R1-R4 or
      `wt_verify.py` changed either.
    - All tables were regenerated: `main` (twice, byte-identical), `contingency` and the three pilot tables, and
      the README's figures were updated.
    - The pilot tables also gain the sections added since they were made: H1b and, in the JSON, an empty
      `decisions`. `pilot_p2b` is made with `--cells`, as it was originally.
  - **`exploratory_contrasts`** in `wt_analyze.py`: each exploratory cell minus its main-run twin and the server
    gate, with the decisions' bootstrap.
    - It prints only when an exploratory cell is analyzed, so `main.md` does not change.
    - Tested in `test_wt_analyze.py`.
  - **`wt_blocks.py` by hold point:** a second table per cell with each hold point's releases and hold lengths,
    the share of the context cached when the hold began, and the prefix kept.
  - **Why:** to describe `x16_h_share_start`, whose turn-start holds are the point of the cell.
