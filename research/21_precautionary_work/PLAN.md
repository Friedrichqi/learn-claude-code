<!-- Topic 21's plan as approved on 2026-10-30 16:44 UTC (copy of ~/.claude/plans/i-desire-to-run-moonlit-panda.md). -->

# Topic 21 · Precautionary CPU work under congestion: can a harness that foresees KV pressure turn waiting into tool work? (MiniMax-M2.7, SWE-bench Pro + Terminal-Bench)

## Context

**The user's idea (10-29):**
- When many users race for scarce HBM, requests fall into mutual KV-cache eviction.
- The harness should foresee the congestion and KV-consumption trend.
- It should then prompt the agent to do CPU-heavy tool work instead of sitting evicted and idle.

On 10-29 I proposed this design, which the user now asks to plan as topic 21:
1. **Forecast:** the harness forecasts congestion with topic 18's share rule.
2. **Targeted line:** only sessions about to be held get one line in their next call.
3. **Hold during tools:** the harness holds the next call while those tools run.
4. **Eviction order (optional):** the server evicts the cache of sessions in a tool phase first.

**What earlier topics settle:**
- **The model ignores passive signals,** so the harness or server must decide.
  - Topic 19: MiniMax made 3 `wait` calls in 21,081, and 1 of 3,627 at N=64/128.
  - Topic 18: Qwen3-32B over-waited.
- **Harness-decided holds and the server gate work.**
  - Topic 18: 1.8-3.8×.
  - Topic 19 (gate): 1.42-1.44×, with the hit rate going from 1-14 % to 82 %.
- **Only SWE-bench Pro and Terminal-Bench have real CPU work.** Tools take 41.6 % and 46.4 % of trial time there, against 0.01-0.14 % in topic 19's code sessions.

**What the exploration found** (checked on topic 20's 58 labeled Pro+TB trials, 1,557 rounds):
- **At saturation, overlap alone cannot help.**
  - In a closed loop, completed tasks/h = server capacity ÷ GPU work per task.
  - Overlapping tools with holds changes neither, so sessions just hold longer.
  - Gains need one of three things:
    - (a) fewer calls (front-loading as a merge);
    - (b) a shorter critical path below saturation or with open arrivals;
    - (c) better KV use (tool-phase sessions don't count toward the share and are evicted first).
- **The ceiling is small.**
  - **Calls that folding can remove, even if the model always complies:**
    - folding each chain group up to its heavy round (tools ≥ 1 s) removes at most 141 of 1,557 calls (9.1 %);
    - for heavy rounds of ≥ 10 s, 75 calls (4.8 %).
  - **The movable tool time is concentrated.**
    - 44 % of heavy tool time is decidable earlier, by 1.48 calls.
    - Two of those rounds are 1,800-s command timeouts, and together they hold 58 % of it.
    - 51 such rounds are ≥ 10 s, in 20 trials. They sit late in each trial (median position 0.59).
  - **Only merges are possible:** 11 of the 97 movable rounds commute with the rounds before them, so front-loading must fold rounds k..h into one response.
- **Trials and holds rarely line up.**
  - Pro/TB trials are single-turn.
  - The long turn-start holds come before the first call, where there is nothing to front-load.
  - Mid-turn holds under the robust rule are short: 5.6 s p50 and 77 s p95 in topic 18's `x16`.
- **The simulator's share rule is not the deployed one.**
  - `kvsim._share_holds` (`research/18_harness_wait/followups/sim/kvsim.py:226-239`) counts a session for its whole turn.
  - The real server drops it 10 s after a tool-calling reply (`WT_MID_EXPIRE_S`, `wt_scheduler.py:297`; `WT_HOLD_TTL_S`; `wt_server_ext.py:217-223`).
  - kvsim also uses Qwen's step model. Client rules were 1.2-1.6× optimistic in topic 18's calibration.
- **Early planning signals** (rough kvsim runs, MiniMax step model, Pro/TB scripts, 3 seeds; not results):

  | condition | N=16 | N=32 |
  |---|---|---|
  | `none`: calls/h, hit rate | 3,516, 48 % | 2,896, 4 % |
  | gate K=8 | 1.04× | 1.29× |
  | share rule with the deployed semantics, τ 0.7 / 0.9 | 1.02× / 1.08× | 1.24× / 1.29× |
  | + leaving the share during ≥ 10 s tools | +1-2 % | +1-2 % |
  | folding every movable heavy round at 100 % compliance (trials/h) | +8 % | +10 % |
  | tool-phase-first eviction | ±0.5 % | ±0.5 % |

- **Correction to my 10-29 message:** "holding after long tools reaches 0.94× the gate" came from synthetic long tools added to topic 18's Qwen code sessions (`tables.py:117-122`), not from Pro or Terminal-Bench.

**Intended outcome:** a clear, quantified answer to four questions:
1. How much can precautionary front-loading buy on CPU-heavy agent work under KV pressure, and in which load regime?
2. Does MiniMax follow a targeted, directive line, which topic 19 never tested?
3. Does the forecast add anything over an always-on line?
4. Do the model-free variants (tool-phase accounting and eviction) help?

The most likely outcome is that Stage 1 (CPU) ends the topic with "small, below the decision floor". GPU time is spent only behind gates.

**User decisions (10-30):**
- **The compliance probe always runs** (about 3 GPU-h). The load replay is gated.
- **Cap:** 40 GPU-h on GPUs 0-3.
- **Scope:** prompt-driven front-loading, with the model-free controls (tool-phase accounting and eviction, the
  gate, the share rule) as references. No other levers are in this topic.

## Hypotheses and outcomes

| | hypothesis | where | passes if |
|---|---|---|---|
| H1 | compliance: the targeted line makes MiniMax issue the movable heavy command in the group's first response | probe | targeted − neutral line ≥ 15 points (valid early issue), with the lower bound > 5 |
| H2 | forecast: "next call held ≥ 10 s" is predictable at send time | simulator | precision ≥ 0.6 at hold-seconds-weighted recall ≥ 0.5, and better than persistence |
| H3 | closed loop: front-loading on top of the best no-line policy raises throughput | simulator, then replay | ≥ 10 % recorded rounds/h at N*, and calls per round fall |
| H4 | open arrivals: front-loading cuts trial latency | simulator | mean and p95 trial time fall ≥ 15 % at λ = 0.8 and 0.95 of capacity |
| H5 | model-free: tool-phase accounting and eviction beat the share rule or gate | simulator | ≥ 10 % rounds/h |
| H0 | null check: overlap without the line at 2× the knee | simulator | rounds/h within the floor; hold-s rises by about the overlapped tool-s |

- **Primary outcome:** recorded rounds per hour in the window, counted when a round's tool step ends, as in `rd_loadreplay`.
  - Merges leave it unchanged (rounds are conserved) and censoring affects it little.
  - A GPU-cost-weighted version is the robustness check.
- **Secondary:**
  - trials/h;
  - Kaplan-Meier trial time (mean, p50, p95; via `research/18_harness_wait/wt_latency.py`'s KM, quantile, rmst and bootstrap);
  - calls per round, GPU-s per round, hit rate;
  - idle vs overlapped hold-s per trial;
  - front-load lines shown, eligible and applied.

## Stage 0 · Setup and census (CPU)

1. **Folder and housekeeping:**
   - Create `research/21_precautionary_work/`, with modules prefixed `pw_` so names stay unique on `_paths.py`'s sys.path.
   - Copy this plan to its `PLAN.md`.
   - This plan file has now been overwritten twice, so preserve the earlier plans:
     - restore topic 20's original plan from this session's transcript (the Write at line 441) into `research/20_round_dependency/PLAN.md`, and repoint its README:11;
     - save the 10-29 completion-time/high-N plan, from the same transcript, into `research/19_efficiency_signals/followups/plan_1029.md`.
2. **`pw_census.py` (`--check` asserts the values):**
   - Reproduce topic 20's ad-hoc numbers, which today live only in the transcript: 259 / 97 / 44 % / 1.48 calls / 6.7 % / 5.2 %, and the 28.8 s definition.
   - Add the new ceilings: 9.1 % / 4.8 % / 26.1 % of calls; the timeouts' share; positions in the trial; the 11-of-97 commute count; per-benchmark tool share.
   - It imports `rd_analyze.load_run("main")`, `rd_static` and `rd_rounds`.
   - Output: `data/tables/census.{md,json}`.

## Stage 1 · Simulation (CPU, the main deliverable)

### Scripts: `pw_scripts.py`

**Pro/TB:**
- **Source:** topic 20's `load16` plan, built with `rd_loadreplay.prepare("main", "t16", …, {"swepro", "tbench"})` and `LOAD` redirected to topic 21's `data/load`. That gives 58 units and 1,557 rounds, the same units Stage 2b replays.
- **Per call:**
  - `np` is the rendered token length;
  - `out` is the recorded output;
  - `delay` is the previous round's tool gap (capped at 600 s, as the replay does);
  - `ideal` is the token LCP of consecutive rendered prompts (replay semantics; `append_ok` holds in only 66 % of pairs).
- **Fold table:** for every eligible (j, h_max), the merge-prefix world via `rd_loadreplay.fold_group`, with `out_extra`, `gap_sum`, `np_delta` and `ideal_after`.

**Code sessions (negative control and calibration):**
- Topic 19's cells, through a copy of topic 18's `extract.py` loop. That file is a script with a hard-coded root.
- Regression: running it on topic 18's cells must equal topic 18's `scripts.pkl`.

### Simulator: `pw_sim.py`

`PWSim(kvsim.Sim)` and `PSess(Sess)`. kvsim is imported unchanged, so FOLLOWUPS.md stays reproducible.

**Server:**
- MiniMax step model from `research/19_efficiency_signals/data/tables/main.json:/step_model/coef`;
- pool of 16,136 blocks;
- `max-num-seqs` 64;
- slots started U(0, 300) s apart and U(5, 30) s between trials, matching `rd_loadreplay`.

**Registry semantics:**
- `reg`: kvsim's;
- `wt`: the deployed rule, with the 10 s expiry, the 10 s hold TTL and "behind everyone" at the first check after a long tool;
- `park`: `wt` plus the session leaves the share at the start of a predicted-long tool and keeps its rank when it returns.

**Forecasts:** logged against the truth "held ≥ 10 s after this call":
- persistence;
- nowcast at τ_f ∈ {0.6, 0.7, 0.8};
- trend (the 60-s slope of `ahead` × horizon);
- oracle.

**Front-load (`fl`):**
- **The decision:** made at send time; the line is shown targeted (by a forecast), untargeted, or by the oracle.
- **Compliance:** draws are paired common random numbers at p ∈ {0.07, 0.25, 0.5, 1} (later p̂ from the probe).
- **The fold:** copy-on-write, since scripts are shared and recycled. It costs output-token inflation of 0 or +5 % and wasted heavy tool time on line-shown rounds with no target (rate from the probe).

**Other mechanisms:**
- `tpf`: tool-phase-first eviction, a key in `_victims`;
- gate K with its 5 s idle release;
- an open-arrival mode with Poisson trial arrivals at λ = 0.8 and 0.95 of the measured capacity.

**Report:** recorded rounds/h, trials/h, KM trial time, hit rate, GPU shares, holds split into idle and overlapped, and forecast precision/recall.

### Calibration gate G0: `pw_calibrate.py`

Simulate topic 19's measured cells and compare:
- **Cells:**
  - `w08_none`, `w16_none` and `w32_none` (sim `none`);
  - `w16_sgate` and `w32_sgate` (gate K=8);
  - `x64_m_wait` and `x128_m_wait` (sim `none`, 1,200 + 2,400 s).
- **Tolerances:**
  - throughput within 15 %;
  - hit rate within 0.05;
  - turn p50 within 25 %;
  - the x cells' call p50 within 30 %;
  - the plateau of about 2,700-2,900 calls/h.
- **If the simulator misses:** report the ratios, shrink the LRU buffer until held prefixes stop surviving gaps over about 4 s, and decide only on paired contrasts.

### Grid and tables: `pw_tables.py`

**What it runs:** a pool of 48 workers, 3-10 s per run, about 500 runs, a few minutes.
- **Workloads:** Pro/TB as the primary; code sessions as a negative control (expect no front-load gain).
- **Load:** N ∈ {8, 16, 24, 32, 48, 64}.
  - N_knee is the smallest N where `none` reaches ≥ 90 % of its maximum rounds/h.
  - The tests run at N_knee and 2·N_knee, plus the open-arrival rates.
- **Factorial:** line (none / targeted / untargeted / oracle) × hold timing (after tools / during tools) × accounting (`reg` / `wt` / `park`, ± `tpf`).
- **References:** `none`; gate K ∈ {6, 8, 10, 12}; share τ ∈ {0.7, 0.9}.
- **Repetition:** 6 paired slot orders.
- **Timeout sensitivity:** cap at 600 s (primary), 1,800 s, or excluded.

**Outputs:** `data/tables/{calib,sim}.{md,json}`, regenerated byte-identically.

### Gate G1: does front-loading have room to matter?

G1 no longer gates the probe, which always runs. It feeds G2. At full compliance, front-loading (targeted or
untargeted) must meet either:
- a closed-loop gain of ≥ 10 % rounds/h over the best no-line policy at some N ∈ {16, 24, 32}, in ≥ 5 of 6 orders, with the same sign when timeouts are excluded; or
- an open-loop cut of ≥ 15 % in mean trial time.

H5's result is reported either way. If G1 fails, the topic closes after Stage 1 and the probe, with the write-up
below.

## Stage 2a · Compliance probe (GPU, about 2-3 GPU-h; runs regardless of G1, by the user's choice of 10-30)

It can run as soon as the probe code and `prereg_2a` are frozen, in parallel with the simulator work. It needs a
1-2 h sheet claim.

`pw_probe.py` subclasses `rd_cf.Runner` and reuses its `variant`, `keys_of`, `matches` and sampling settings (S=8).

**Items:**
- the 144 eligible (j, h) pairs (141 distinct requests at L_j);
- the 51 rounds of ≥ 10 s are primary;
- controls:
  - 63 dependent heavy rounds probed at L_{h−1}, to measure premature launches;
  - 100 calls with no heavy round in the next 3, to measure invented long commands.

**Conditions** (paired, on the same request; the line is appended to the newest user message, as `hold_runtime.append_status_line` does):
- no line: topic 20's stored C samples, plus 40 fresh ones for drift;
- the targeted line, verbatim: "Capacity is short and your next call will wait. Put any long-running commands that are ready (tests, builds, installs, downloads, searches) in this response.";
- a neutral line of the same length.

**Scoring:**
- the heavy command issued early, at target and loose level;
- valid order: the intermediate calls are present or they commute;
- the recorded first action kept;
- output tokens;
- premature or invented long commands;
- `wait` or ending the turn early.

**Statistics:** a trial-cluster bootstrap (2,000 draws), split by benchmark and by adjacency.

**Gate G2 (go to the replay):**
- H1 passes;
- output tokens +≤ 5 %;
- premature and invented long commands +≤ 5 points each;
- the simulator, rerun at the measured p̂ with the measured costs, still meets G1's closed-loop criterion.

## Stage 2b · Closed-loop load replay (GPU, ≤ about 30 GPU-h; only if G2 passes)

**Code:** `pw_loadreplay.py` (`PWCell(rd_loadreplay.Cell)`).
- **Hold:** an in-process registry runs the share rule with `wt`/`park` semantics. All N sessions are in one process, so no `/wt/status` is needed. The hold overlaps the tool sleep.
- **Front-load:** a targeted line plus a seeded fold at p̂. Every earlier line is re-inserted in later prompts so the prefix cache still hits.
- **Logs:** `holds.jsonl`, `copies.jsonl` (trial start and end, for KM) and 1 Hz registry snapshots.

**Server:** `pw_serve.py`.
- It copies `rd_serve.run_stage`, with topic 21's own GPU-hour log and cap, and the claim check.
- Config: topic 20's `load16` without priority scheduling.
- Optional: `pw_scheduler.PWMixin(MUMixin)` for `tpf`, only if the simulator shows ≥ 3 %.

**Cells:**
- one fresh server per cell;
- paired slot orders;
- 900 + 3,600 s, about 1.42 h × 4 GPUs = 5.7 GPU-h each;
- N* chosen by a frozen rule: the N ∈ {16, 24, 32} with the largest predicted gain where share ≥ 1.1× `rec`.

| cell | what |
|---|---|
| smoke (N=8, 300 + 900 s) | `rec`, and front-loading at p=1: checks re-inserted lines, hits after folds, and logs |
| `rec` | no policy (the regime check) |
| `share` | the best no-line policy from the simulator (R0) |
| `share_aa` | an A/A repeat of R0, run first |
| `fl_p` | R0 + targeted front-load at p̂ (R1) |
| `fl_1` | R0 + front-load at p=1 (the ceiling), only if p̂ < 0.8 |

**Pre-registration (`prereg_2b.md` + sha256),** frozen before the smoke. It fixes:
- the line text, the forecast and τ_f, τ, the hold cap and the registry semantics;
- N*, the cells, windows, seeds and p̂;
- timeout handling;
- the simulator's predicted effects with intervals;
- the analysis code hashes.

**Decision:** R1 vs R0 recorded rounds/h counts only if both hold:
- the block-bootstrap gain ≥ max(10 %, 2× the A/A difference);
- the interval excludes 0.

The measured gain is also compared with the simulator's prediction, which checks the simulator's validity.

**Stage 3 (live A/B with real Pro/TB containers): out of scope.** It would need, all on a separate approval:
- harness flags (`S15_FRONTLOAD`, `S15_PARK`);
- a vendored s15 with `hold_runtime.py`;
- the dind and image rebuild (about 88 GB of pulls);
- a multi-user Harbor launcher.

## Files (all new, in `research/21_precautionary_work/`)

| file | role | reuses |
|---|---|---|
| `README.md`, `PLAN.md` | topic document | — |
| `pw_rules.py` | shared logic, pure functions: line texts, `append_line`, `Registry` (`reg`/`wt`/`park`), forecasts, `fold_targets`, long-command classifier, common random numbers | rule as `hold_runtime.py:543-548` |
| `pw_census.py` | Stage 0 numbers | `rd_analyze`, `rd_static`, `rd_rounds` |
| `pw_scripts.py` | simulator scripts and fold tables | imports `rd_loadreplay.prepare/build_calls/fold_group/with_nonce`, `rd_cf.render_ids`; copies `extract.py`'s loop |
| `pw_sim.py` | `PWSim(kvsim.Sim)` | imports `kvsim` (unchanged) |
| `pw_calibrate.py`, `pw_tables.py` | G0 and the grid | topic 19's `main.json`, `highn.json`, `latency.json`; `wt_latency` KM |
| `pw_probe.py` | Stage 2a | subclasses `rd_cf.Runner` |
| `pw_serve.py`, `pw_fake_vllm.py` | GPU lifecycle and dry runs | `rd_serve` helpers (claims, gpus_empty, free_port, stop_group) |
| `pw_loadreplay.py`, `pw_scheduler.py` | Stage 2b | `rd_loadreplay.Cell`, `mu_scheduler.MUMixin` |
| `test_pw.py` | script runner, as in `test_rd.py` | — |

**Untouched:**
- topic 18's sim;
- topic 19's frozen harness files: its `prereg.sha256` hashes `code.py` and `hold_runtime.py`;
- topic 20's frozen files: `rd_*` and `plan_load19.json`. Topic 20's S5 merge test is still pending.

`pw_serve` verifies all these hash files before each GPU stage.

## Verification

- **`test_pw.py`:**
  - `PWSim` with kvsim's policy strings equals kvsim exactly (none, gate:8, share:0.9:mid at N=16/32), and equals stored `sim_tables.md` rows;
  - determinism, and scripts unchanged after a fold run;
  - two-session scenarios:
    - `park` excludes a session that `reg` keeps;
    - `wt` ranks a session behind everyone after a 12 s tool;
    - a fold removes h − k calls and conserves rounds;
  - `tpf` victim order;
  - the `mns` cap;
  - forecasts on synthetic series;
  - KM and trials/h on synthetic data;
  - the extractor regression against topic 18's `scripts.pkl`;
  - probe requests are append-only, and scoring against a fake server that answers c_h (p=1) or c_j (p=0) gives 1 and 0;
  - replay against a fake server at `time_scale` 0.02 for each variant: rounds complete once, folds remove calls, holds are logged, and later prompts keep the earlier prefix;
  - a `pw_serve` dry run against `pw_fake_vllm.py`.
- **Reproduction:**
  - `pw_census.py --check` must pass;
  - tables regenerate byte-identically under two `PYTHONHASHSEED`s;
  - G0's calibration report must be in hand before any GPU stage.
- **GPU stages:**
  - the lab-sheet claim on GPUs 0-3, entered by the user as Yijiahao, is verified through the CSV export;
  - the hash checks pass;
  - vLLM is stopped and the GPUs confirmed empty after every stage;
  - the GPU-hour log is kept against the cap.

## Budget, rules, write-up

- **GPU need, if every gate passes:**

  | item | GPU-h |
  |---|---|
  | probe | about 3 |
  | smoke | about 4 |
  | 4-5 replay cells | about 23-28 |
  | **total** | **about 30-35** |

  - **Cap: 40 GPU-h** (the user's choice, 10-30). It is separate from topic 20's 90 GPU-h. `pw_serve` refuses a
    stage that would cross it.
  - Stage 1 is CPU only. The probe always runs; the replay runs only if G2 passes.
- **Rules:**
  - GPUs 0-3 only;
  - no commits or Drive pushes unless asked;
  - never call a condition an "arm";
  - every setup name gets a short gloss;
  - no time conclusions from single-session timing.
- **Write-up:**
  - the README Short answer, covering H0-H5 with the numbers, the regime map, and what the user should pursue next;
  - the `research/README.md` row;
  - memory updates.
  - If G1 fails, the next candidates are listed there: the gate/share rule on Pro/TB, with slot release at long-tool start and pre-queueing; topic 20's pending merge test; and context slimming.
