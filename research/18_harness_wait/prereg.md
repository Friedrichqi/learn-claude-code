# Pre-registration: research/18_harness_wait, main run (S3)

Frozen before the first main cell starts. The file's sha256 goes in `prereg.sha256`. Anything that changes
afterwards is listed, with the reason, under "Changes during the run" in README.md.

## Question

Many s15 sessions share one vLLM server past its turning point. Does waiting at the end of a round relieve the
server, and how much of the server-side session gate's gain does it recover? The wait is either decided by the
model from a one-line server status (`m_*`) or by a harness rule on the same status (`h_*`). Hypotheses:

- **H1 (primary):** at N=16 and N=32 a wait arm X raises work lead calls per hour over `none`.
- **H2 (decider):** the model arm and its harness twin differ on the same status.
- **H3 (information):** the share status and the stock status differ, within each decider.
- **H4 (no harm):** at N=8, below the turning point, the model arms cost no throughput.

## Fixed design

- **Model and server:**
  - Qwen/Qwen3-32B, bf16, thinking off, `--max-model-len 40960`.
  - vLLM 0.31.0, TP=2 on H100 80GB pairs {0,1} and {2,3}.
  - KV pool pinned to 16,137 blocks (the natural pool is 20,012).
  - The append-only Qwen3 template; prefix caching; FCFS; seed 0.
  - `--scheduler-cls wt_scheduler.WTScheduler`, `--middleware wt_server_ext.Middleware`, `MU_STRICT=1`.
  - All of it as written in `data/conditions.json` at freezing.
- **Workload:**
  - topic 11's 4-turn sessions, from `data/sessions.jsonl` (sha256 `8a20ee38...45fc99`) and the frozen seeds
    `data/seeds/` (`SEEDS.json` sha256 `d8d7cd20...6ebc6ce9`);
  - think time lognormal, median 30 s;
  - budgets of 100 lead calls per turn and 300 per session; memory off; snip off.
- **Cell:** N users in a closed loop. 20 min of warm-up (staggered starts, truncated warm-up fillers), then a
  90-min measurement window, then stop.
- **Arms** (client settings exactly as in `conditions.json`):
  - `none`.
  - `m_stock` / `m_share`:
    - the `wait` tool, with the description `WAIT_TEXTS["v2"]` (chosen by the rule written before P2b);
    - the status line every round, in the stock or the share view;
    - `S15_WAIT_MAX_S=300`.
  - `h_backoff`:
    - hold after tool rounds;
    - enter at KV ≥ 0.92 or a request waiting for capacity; leave at KV < 0.85 with nothing waiting for capacity;
    - steps of 2·2ᵏ·U(0.5, 1.5) s, at most 30 s;
    - cap 600 s per hold point.
  - `h_share`:
    - hold after tool rounds while the working sets ahead plus its own exceed 0.9 × pool;
    - re-check every 2·U(0.5, 1.5) s;
    - cap 600 s.
  - `sgate`: `MU_GATE=8`, `MU_GATE_IDLE_S=5`.
- **Code at freezing (sha256 of the files):** `s15_integrated_harness/code.py`, `hold_runtime.py`,
  `research/18_harness_wait/wt_scheduler.py`, `wt_server_ext.py`, `research/11_multiuser_kv/mu_scheduler.py`
  (listed in `prereg.sha256`).
- **Cells and order** (seed 20261024; each arm's N=16 and N=32 cells on different pairs):

| round | pair A (GPUs 0,1) | pair B (GPUs 2,3) |
|---|---|---|
| 1 | w16_none | w16_none_aa |
| 2 | w08_m_share | w16_m_share |
| 3 | w32_h_backoff | w32_h_share |
| 4 | w32_m_stock | w16_sgate |
| 5 | w08_none | w16_m_stock |
| 6 | w32_sgate | w08_m_stock |
| 7 | w32_m_share | w32_none |
| 8 | w16_h_share | w16_h_backoff |

## Metrics

- **Primary: work lead calls per hour** in the measurement window. These are lead calls with a reply received in
  the window whose reply is not only a `wait` call.
- **Uncertainty:** a block bootstrap over the window's nine 10-minute blocks, 4,000 draws, seed 18.
- **Secondary** (reported, not decided on):
  - turns per hour;
  - Kaplan–Meier turn latency p50 / p95, over turns started in the window and censored at its end;
  - call latency p50 / mean, and hold + wait per call;
  - hit rate (cached / prompt tokens);
  - GPU share by class, from the step model fit on the main cells;
  - preemptions; prompt tokens per call;
  - holds and waits (H1), decisions and oscillation (H2), prefix survival (H3), fairness (H4) and overhead (H6).

## Decision rules

- **The noise floor:** Δ_AA = |w16_none_aa − w16_none| in work lead calls/h.
- **Gain:** G_X = X − none at the same N. Its bootstrap draws resample X's and none's blocks independently.
- **R1 (recovery):** for X in {m_stock, m_share, h_backoff, h_share} at N in {16, 32},
  R_X = G_X / (sgate − none). The arm "recovers a meaningful share of the gate" if R_X ≥ 0.5 and the 2.5th
  percentile of G_X > Δ_AA.
  - It "hurts" if the 97.5th percentile of G_X < −Δ_AA.
  - R is reported only where sgate − none > Δ_AA. Otherwise the gains are reported alone.
- **R2 (decider):** D = m_X − h_X on the same view at the same N. "The decider matters" if |D| > Δ_AA and D's
  bootstrap 95 % interval excludes 0.
- **R3 (information):** S = X_share − X_stock within each decider at the same N, by the same criterion.
- **R4 (no harm at N=8):** each model arm m_X(8) satisfies m_X(8) − none(8) ≥ −Δ_AA, and its hit rate is within
  2 points of none(8)'s.
- **Exploratory, not decisions:**
  - every secondary metric;
  - the contingency cell x16_h_share_start (share holds also before a turn's first call), if the budget allows.

## Stopping and failures

- **GPU-hours:** a hard cap of 100, including smoke and pilot (14.2 at freezing). If the projection passes the cap,
  stop and ask the user.
- **A cell that fails** (a pipeline error, a stall abort, or a sweep exit ≠ 0) is rerun once from scratch after
  the scheduled rounds. A second failure is reported as missing.
- **GPUs:** 0–3 only, inside the user's sheet claims. Every server stops after its cell.
