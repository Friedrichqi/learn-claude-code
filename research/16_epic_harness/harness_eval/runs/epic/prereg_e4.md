# e4 pre-registration: divergence-triggered full recomputation (MiniMax-M2.7, SWE-bench Verified, Pro, Terminal-Bench)

Written on 2026-10-15, after the pilot e4p (its tasks are not e4's) and before any e4 trial. The file's sha256 is in
`runs/epic/prereg_e4.sha256`. `run/stage_e4.py` checks it, and every sha256 listed below, before it starts the server.
Anything changed after that point is reported as a deviation in the e4 notes (`runs/epic/notes_minimax-m2.7-epic_e4.md`),
with its time and reason. Plan: `~/.claude/plans/whenever-we-do-shift-epic-misty-muffin.md` (approved 2026-10-15).

## Question

The user's question is how urgent a full recomputation is after shift+EPIC curates the KV cache. The candidate signal
is a divergence between the *original* KV (every row in its first-ever version, kept untouched across curations) and
the *after-EPIC* KV, mapped into [0, 1] and used as the probability of a full recomputation. e4 runs the first such
policy, JSD, next to the arms of e1–e3. It answers with one table per benchmark (columns in §Analysis) and, as a
secondary result, the relationship between the decision metric and the real error. TVD and EXP arms are future
work; their values are logged at every JSD decision.

All comparisons are exploratory: intervals, no verdicts, no multiplicity adjustment.

## Design

### Server

`minimax-m2.7-epic` in `serve/models.yaml`, GPUs 0–3 (tensor parallel 4): vLLM 0.31 with the EPIC connector and the e3
configuration (gpu_util 0.93, `--watermark 0.08`, at most 48 sequences, the append-only template with the last-split
fix, shadow probes on 1 in 32 eligible calls plus every shift of at least 8,000 tokens). Changes for e4:
- `--max-logprobs 128`, `EPIC_DEC_K` 128: the decision probes' top-K.
- `EPIC_DEC_FLOOR_EVERY` 16: a duplicate after-EPIC probe on 1 decision in 16, the decision's noise floor.
- `EPIC_DEC_CONCURRENCY` 0 (no limit; the pilot had no preemptions at 21 sessions): decisions probing at once (0 = no limit).
- `EPIC_POOL_ROWS` 1,280,000 (was 960,000): the originals the recompute arm keeps and Terminal-Bench's long histories.
- `EPIC_DECODE_RESERVE` 12,288 (was 4,096): Terminal-Bench replies overflowed 4,096 in e2 (23 resets).

### Arms

| arm | label | what the server does after a snip edits the history |
|---|---|---|
| `prefix` | baseline (full recomputation) | recomputes everything after the edit (normal prefix caching) |
| `prefix_aa` | prefix_aa | identical to `prefix`; the A/A copy |
| `shift` | shift | reuses every surviving row, re-rotated to its new position |
| `epic128` | shift+EPIC | as `shift`, and recomputes the first 128 tokens after each edit |
| `epic128a1` | shift+EPICa1 | as `epic128`, and recomputes every reused row that would cross more than 1 edit |
| `epic128a4` | shift+EPICa4 | as `epic128`, with an age cap of 4 edits |
| `epic128rjsd` | shift+EPIC+recompute@JSD | as `epic128`, plus the decision below at every curated call |

**The recompute arm.**
- *Curated call:* a lead call whose plan is `edited` (the prompt is not an append-only extension of the session's
  previous sequence).
- *Two distributions, before the call is served:*
  - O = the first-token distribution with the original cache (every reused row in its first-ever version, rotated to
    its current position; only new tokens computed);
  - E = the first-token distribution with the after-EPIC cache this call would use (its epic128 plan).
  - Both come from 1-token forwards (temperature 0, top-128 logprobs, no rows saved, never-used cache salts), run
    concurrently. If every prompt row of E is its original version, the divergence is 0 and nothing is probed.
- *Divergences* on the partition {tokens in both top-128 lists} + one rest bucket (exact for the partition, a lower
  bound of the full-vocabulary value): KL = KL(O‖E) in nats, JSD = √JS₂(O,E), TVD = ½Σ|O−E|, EXP = 1 − e^(−KL).
- *Decision:* u = SHA256(seed key | recompute | call) / 2³² (seed key = the replicate phase | the task, so u is shared
  by any recompute arms of a block and independent of probe sampling); recompute iff u < JSD.
- *Full recomputation:* keep [0, h), where h is the running minimum of p0 over the edited calls since the last full
  recomputation (rows never edited, bit-exact), and recompute [h, P) under a new cache salt. The entry is then all
  fresh, and its originals are reset to these rows.
- *Accounting:* charged computed tokens = served C + C_O (the original-cache forward's own tokens) + C_E on a
  recomputation (the arm's prefill done before deciding). The E probe is free on a real server.
- *Failures:* a failed decision serves the epic128 plan and is logged as `dec.error`; never as a fallback or reset.

### Tasks

The baseline profile's first 30 tasks of each benchmark, the lists of e1 (SWE-bench Verified) and e2 (Pro and
Terminal-Bench, with e2's in-place replacements):
- `run/state/e4_tasks_swe.json`, sha256 `a5874c6d76dcf073ba2caef1d59d4bb1ff1b8bf0eb499c0a2053adf94202766e`;
- `run/state/e4_tasks_swepro.json`, sha256 `7d59abd94136118b4df2e85fc8f8f02f3e263409141e0b6a9e6a6204ba5de185`;
- `run/state/e4_tasks_tbench.json`, sha256 `f0dd97c3d54a79e6e7ec8614d5cec35f313d66572a4e9e1f3300e7249f066352`.

A task's block index is its index in its list. Oracle check (Harbor's oracle agent, before any GPU run): Done 2026-10-15, 16:23–17:30 UTC, in `jobs/oracle/<bench>/e4check` with reruns in `e4check2` and `e4check3`.
- **Results:**

  | benchmark | main tasks (30) | pilot tasks (2) |
  |---|---|---|
  | SWE-bench Verified | all pass | both pass |
  | SWE-bench Pro | all pass | both pass |
  | Terminal-Bench | all pass | neither passes |

- **Terminal-Bench's pilot tasks** affect only the pilot:
  - qemu-startup: the verifier's apt sources return 404;
  - mcmc-sampling-stan: the reference solution's RStan setup fails.
- **Before the network fix,** 10 verifiers had failed on stalled downloads (6 SWE-bench Verified, 4 Terminal-Bench).
  All 10 pass when rerun with it..
No task is replaced.

### Blocks, replicates and scheduling

- A **block** is one task in one replicate; its 7 arms start within seconds of each other on the same server, with
  common random numbers (each lead call's sampling seed is SHA256(block key | call)), in an order rotated by the block
  index.
- **3 replicates** (e4r0, e4r1, e4r2), 270 blocks and 1,890 trials.
- **Scheduling** (`run/stage_e4.py`): one queue over the three benchmarks, replicate after replicate; within a
  replicate the longest blocks first (Terminal-Bench, Pro, SWE-bench Verified). A block starts while the agent sessions
  in flight plus its 7 stay within 22 and the host pool is below 60 % full (at most 6 blocks at once).
- **Agent timeout** ×2 (SWE-bench Verified, Pro) and ×3 (Terminal-Bench, which was time-bound at ×2 in e2). The SWE
  verifier timeout ×0.6, the 250 lead-call cap and the s15 harness are unchanged.
- **Infrastructure failures** (`report/collect.py`'s classes) are not outcomes. From e4 on these include a verifier
  that failed to install its test runner (its output has "failed to download https://github.com/astral-sh/uv"). They
  are rerun under the same block key for up to 2 rounds after the replicates. An agent timeout and a verifier timeout
  are outcomes.
- **Task containers** run in Docker-in-Docker (`run/dind.md`), with TCP MSS clamped to the path MTU on forwarded
  connections. Without the clamp, 10 oracle verifiers stalled on downloads on 2026-10-15; with it, all 10 pass.

### Outcome

A trial succeeds if the benchmark's verifier reports reward ≥ 1.

## Analysis

Fixed in these files, each with its sha256:
- `report/e4_report.py`, sha256 `ebc0229999022d20797996ac1154bcacdf8f61b4656c9facb3140027692a1c92`: the tables;
- `report/epic_report.py`, sha256 `fcaa960a804886cc289b4fca71d6de02b38b80f57264a72c399ffce4a01c88d7`: the functions e4_report uses (first-valid trials, the paired statistic);
- `report/collect.py`, sha256 `7c64870f414bf1072bd7bb0719b6edebfd8a7de22d2fdd92e7fada506309a824`: the trials and their infrastructure classes;
- `report/latency_model.py`, sha256 `c7787a1f15ee533885b27a69fa0cfffd0b8223bd1b1ba1bc10e87bbf5366c1e6`: the time model's inputs and price;
- `report/e4_verify.py`, sha256 `f55d52f703c8db76da55a7c9921d7f609052287fa95d29d0b3085b9970852333`: recomputes every cell independently; a mismatch is a bug to fix before reporting, not a deviation.

**One trial set for every column:** per arm and block, the first valid trial by start time. Its calls are the
completed lead calls, joined on (trial, call index) to the last non-aborted server record.

**The table, one per benchmark, rows = the 7 arms, columns in this order:**
1. arm; runs; calls;
2. computed tok / call and / run (charged tokens: `ideal_C` for the prefix arms, the plan's C for EPIC arms,
   `C_charged` for the recompute arm, P − apc_hit for fallbacks);
3. model LLM time / run (the per-benchmark time model fitted on the baseline profile's lead calls, all of them, priced
   on the charged tokens); tool+harness / run (agent wall time minus the joined calls' measured times); model job time;
4. computed / prompt; post-snip calls (calls whose prompt is not an append-only extension of the previous sequence);
5. resolve difference (arm − control, the control being the mean of baseline and prefix_aa in the block; per task the
   mean over its blocks; the estimate is the mean over tasks); pass@1; pass^3 (tasks whose 3 replicate trials all
   succeed, over the tasks with all 3 valid); 90 % CI (two-sided t-interval over the task means, exact t quantile,
   df = tasks − 1). The prefix_aa row is prefix_aa − baseline (A/A); the baseline row has none.
6. first-token KL, reply KL, reply top-1 (the e1–e3 shadow-probe estimators); D^K reused by age (0, 1, 2–3, 4–7, 8–15,
   16+).

**Secondary, per benchmark, reported without verdicts:**
- the recompute arm's decisions (per run, mean JSD, recompute rate, recomputes per run, overhead tokens, time, noise
  floor), and the means of the other logged metrics (KL, TVD, EXP and the top-20 versions);
- Spearman ρ between each decision metric and the shadow probe's first-token KL of the same call (fresh recompute vs
  after-EPIC), with Fisher-z 90 % intervals, and the means by JSD quintile;
- sensitivity: the differences without blocks where any arm hit the agent timeout, and without blocks where any call
  fell back to prefix caching;
- call accounting and the validity gates per replicate.

## Validity gates and stop conditions

- **Gates per benchmark and replicate** (`epic/pilot_gates.py`): E-0 ≥ 99 %; no preemptions; fallbacks + resets below
  5 % of post-snip calls; exactly one server record per tagged lead call; the same override counts on every rank; no
  session left; one decision record on every curated call of the recompute arm and none elsewhere; no failed
  decisions. A failed gate flags the result; it does not change the analysis.
- **Stop conditions** (`run/stage_e4.py`), checked after the first 8 complete blocks of each benchmark in e4r0 and
  when each benchmark × replicate is complete:
  - a preemption;
  - fallbacks + resets at 5 % or more;
  - a missing or duplicate server record;
  - ranks that disagree;
  - E-0 below 97 % (SWE-bench Verified, Pro) or 90 % (Terminal-Bench: its long reasoning hits the template's known
    `\n\n\n` limit, 94.15 % in e2, for every arm alike);
  - a curated call of the recompute arm without its decision record, or a decision record anywhere else;
  - failed decisions at 1 % of decisions.
  On a stop, the blocks in flight finish, no new block starts, and the server stops. Resuming is a deviation.

## Stopping and budget

- **No interim look at outcomes.** While e4 runs, only infrastructure signals are watched: errors, gates, the health
  watcher, GPU-hours. `report/e4_report.py` refuses outcome columns until the stage log says the stage is done.
- No early stop for efficacy or futility.
- **Budget:** a hard cap of 300 GPU-h (server uptime × 4) for e4 including the pilot (3.63 GPU-h, passed as
  `--base-gpu-h`). The estimate is 170–200 GPU-h. A replicate is queued only if the projected total (this run's
  GPU-hours per agent session-hour times the session-hours left) stays within the cap; at the cap the stop file ends
  the run.

## Pilot e4p (process only; its outcomes were not read)

- **When and cost.** 2026-10-15, 16:26–17:22 UTC, two server starts, 3.63 GPU-h.
- **What ran.** 6 blocks (profile indices 30–31 per benchmark) × 7 arms = 42 trials; all valid, no reruns.
- **First start: the selftests stopped it.**
  - Three new checks compare two separate forwards of the same cache. At a bound of KL < 1e-3 they measured
    0.0019–0.0048: the server's numerical noise.
  - The bounds were set to 0.02, with a structural check (C_O = C_E − run heads); see the e4 notes.
  - Second start: S1–S6 all pass.
- **Gates, on all three benchmarks:**
  - E-0 99.06–99.30 %;
  - no preemptions, fallbacks or resets;
  - one server record per call; ranks consistent;
  - every curated call of the recompute arm had its decision record; no failed decisions.
- **Decisions.** 44: 7 on SWE-bench Verified and 37 on Pro. The two Terminal-Bench tasks never snipped.
  - 2 recomputations.
  - JSD: mean 0.028–0.036, median 0.020–0.030, p90 0.061–0.083.
  - **The decision's noise floor** (a duplicate after-EPIC probe): JSD 0.037 and 0.047 (n = 1 each), of the order of
    the decision JSD.
  - Top-128 mass 1.00 on both sides.
  - A decision took 1.1–1.2 s on average (p90 1.4–1.8 s).
  - C_O averaged 410–560 tokens and C_E 540–690.
- **Server:** KV use at most 69 % at 21 sessions; host pool at most 277,285 of 1,280,000 rows; no server errors.
- **Kept for the main run:** 22 sessions, pool guard 60 %, at most 6 blocks, `EPIC_DEC_CONCURRENCY` 0.
