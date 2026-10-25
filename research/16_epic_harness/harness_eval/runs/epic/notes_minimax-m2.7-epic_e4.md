### Setup and deviations (MiniMax-M2.7, phase e4: divergence-triggered full recomputation)

**Why.** After e3, the user asked when a full recomputation becomes urgent after shift+EPIC curates the KV cache.

**The signal.**
- At each curation, take the divergence between the original KV and the current after-EPIC KV. The original KV is
  every chunk's first-ever version, kept untouched across curations.
- Map it into [0, 1] and recompute fully with that probability.

**This round.**
- It adds the first such policy, `epic128rjsd` (shift+EPIC+recompute@JSD), next to the arms of e1–e3.
- It runs on all three benchmarks, with one table per benchmark in the user's columns.
- The user chose JSD only for now; TVD and EXP arms are future work. The probes still give their values at every
  decision, so they are logged.

**Plan:** `~/.claude/plans/whenever-we-do-shift-epic-misty-muffin.md`, approved 2026-10-15. Pre-registration:
`runs/epic/prereg_e4.md`.

**Decisions taken with the user (2026-10-15):**
- the divergence pair: the original cache against the after-EPIC cache, at the first generated token;
- EXP logged as 1 − exp(−KL);
- the profile's first 30 tasks per benchmark, × 3 replicates;
- GPUs 0–3, one server;
- a hard cap of 300 GPU-h with a pilot first.

My first estimate, 160–225 GPU-h, was corrected to 250–290 for 9 arms before the user approved. With JSD only
(7 arms), it is about 170–200.

**Code since e3.** Hashes are in `runs/epic/code_e4.json`; backups are in `run/state/*.before_e4`.
- **`epic/epic_plan.py`:**
  - the recompute arms (`<base>r{jsd,tvd,exp}`);
  - first-ever row versions (`Entry.orig_slot`/`orig_birth`, kept by run heads and overrides, redirected by
    `absorb_apc`);
  - h, the never-edited head;
  - `recompute_plan` and `same_as_original`.
  Other arms plan exactly as before: tested position by position.
- **`epic/epic_server_ext.py`:**
  - the decision (`_decide`, `_probe`, `partition_div`);
  - the recomputation path (a new cache salt; another one if it is aborted);
  - `held()` in every retain and release;
  - `/epic/end` under the session lock (a pre-existing double-release race);
  - `/epic/stats?check=1` (a reference-count audit);
  - partition metrics on shadow probes;
  - a per-probe counter in every probe's cache salt. The repeated-salt case never occurred in e1–e3: 0 repeated
    (spec, call) records in 103,807.
- **`epic/selftest_live.py`:** S5 (the recompute arm, live) and S6 (shadow partition metrics).
- **`epic/pilot_gates.py`:** decision gates, `ev:error` counts, `--out`.
- **`run/epic_round.py`:** admission helpers (session budget, pool guard).
- **`run/stage_e4.py`:** new.
- **Reports:** `report/e4_report.py` and `report/e4_verify.py` are new; `report/make_report.py` takes the e4 parts.
- **Tests:** `tests/test_epic_port.py` 36/36 (16 new), `tests/test_e4.py` 12/12.
- **Checks of the report code before any e4 data:**
  - On e3, `e4_report.py` reproduces all 81 published cells exactly. The one definitional difference is post-snip
    calls of the prefix arms: 7,824 and 7,807 instead of 0, because e3 counted the planner's flag, which only EPIC
    arms log.
  - On e2 the call-count identities hold, 20 of 20.
  - The independent verifier agrees on all 90 cells of an e3 table.

**Infrastructure** (torn down after e3, set up again on 2026-10-15):
- the eval image `s15-eval:15b24dda658e`; its build inputs differ from e3's only in `tests/`;
- Docker-in-Docker per `run/dind.md`;
- an oracle check of the 96 tasks (30 + 2 pilot tasks per benchmark), `jobs/oracle/<bench>/e4check`.

**Pilot e4p, first start (2026-10-15, 16:26–16:31 UTC, about 0.3 GPU-h): the selftests stopped it.**
- **Which checks failed.**
  - Three new S5 checks compare two separate forwards of the same cache. Their bounds were KL < 1e-3.
  - They measured:
    - E probe against the served call: 0.0021 and 0.0019;
    - E against epic128's served call: 0.0021;
    - O against shift's: 0.0048.
- **Why this is noise, not a bug.**
  - The decision's own floor, two concurrent probes of one plan, was 0.0021 at the same call.
  - S1 already allows 0.011 against prefix.
  - The forced recomputation matched a fresh prefill exactly (KL 0.0).
  - The other S1–S6 checks passed, including the reference-count audit and C_O = C_E − 128 run heads.
  So the server is not batch-invariant: two forwards of one cache differ by up to about 0.002–0.01 in first-token KL.
- **The change.**
  - Those three checks now use KL < 0.02 (wrong rows give far more than 0.1).
  - The O check also requires C_O = C_E − H.
  - The failed report is kept as `runs/epic/selftest_e4p_first_start_1629utc.json`.
- **A finding for the study.** The decision metric inherits this noise.
  - For the same history, O against E gave JSD 0.090 in one session (three probes sharing the batch) and 0.035 in
    another (two probes).
  - The floor, logged on 1 decision in 16, measures it in the run.

**The network fix for the verifiers, 16:36 UTC.**
- **The failures.** The oracle check's first results had 3 verifier failures with the same cause: the download of
  `uv` from GitHub stalled ("curl: (28) SSL connection timeout"), and the task scored 0. The tasks were:
  - SWE-bench Verified django__django-11951 and django__django-12193;
  - Terminal-Bench break-filter-js-from-html.
- **The cause.** Inside Docker-in-Docker, `--mtu 1460` sets only the default bridge.
  - Harbor's compose networks and their containers ran at MTU 1500, behind the dind container's eth0 at 1460 (GCP).
  - Nothing clamped TCP's MSS.
  - e1–e3's 2,347 verifier logs had no such failure. Unclamped, this would score infrastructure stalls as task
    failures.
- **The fix.** Inside our dind container only:
  `iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu`, now in `run/dind.md`.
  - The same download from a fresh 1500-MTU inner network then succeeded 3 of 3.
  - The 3 tasks are rerun in the oracle check (below).
  - Every pilot and main-run trial runs with the fix: the pilot's first trials started after it.

**A scoped infrastructure rule for e4, 16:40 UTC, before any e4 outcome.**
- `report/collect.py` now counts, for phases from e4 on, a verifier that failed to install its test runner as an
  infrastructure failure: its output has "failed to download https://github.com/astral-sh/uv". Such a trial is
  rerun under the same block key.
- **Why.** The verifier judged nothing, so a 0 would be scored on the network, not on the agent.
- **Scope.**
  - Earlier rounds keep their classification. Of 3,633 verifier logs before e4, one DeepSeek-V4-Flash profile trial
    has the line; it stays as published.
  - The e3 regression still reproduces its 81 published cells.
- **Checks.** `report/e4_verify.py` restates the rule independently, `tests/test_e4.py` checks the constants agree,
  and the synthetic round now holds such a trial and its rerun.

**Oracle check.**
Done 2026-10-15, 16:23–17:30 UTC, in `jobs/oracle/<bench>/e4check` with reruns in `e4check2` and `e4check3`.
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
  All 10 pass when rerun with it.

**Pilot e4p, second start** (process only; its outcomes were not read; `run/e4_pilot_summary.py`):
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
- **The dry run before it.** The stage was first run against the scripted fake model on the pilot tasks (`runs/epic/stage_e4p_dry.log`). It checked:
  - block names and seed keys (`e4pr0|<task>`);
  - the rotated launch order and the ×3 Terminal-Bench timeout;
  - the session-budget admission and the rerun naming (`b<i>x1`).
  Its "GPU-h" counter is wall time × 4; it used no GPU.

**Pre-registration frozen at 17:25 UTC:** `runs/epic/prereg_e4.md`, sha256 `b6094570863b…`.
- A first freeze at 17:24 (sha256 `13d8a0e1f2eb…`, kept as `runs/epic/prereg_e4_v1_unparsed.md`) wrapped two file hashes onto the line after their file names, and the stage's check could not read them.
- It was re-frozen with one file and its hash per line. Nothing else changed, and no e4 trial had started.

**Main run e4: 2026-10-15 17:25 UTC to 2026-10-17 03:57 UTC.**
- **Starting checks.** At the start the pre-registration hashes were checked (all 8 listed files) and S1–S6 passed
  (27 checks).
- **Replicates.** Queued by the budget rule:
  - e4r1 at 2026-10-16 04:54 UTC, projected total 100.0 GPU-h;
  - e4r2 at 16:06 UTC, projected 141.9.
- **Scale.** 270 blocks, 1,890 trials. Every block had a valid trial in every arm, so no rerun round ran anything.
- **GPU-hours.** 141.7 for e4 in all: the pilot 3.6 (two starts) plus the main run 138.1 (server uptime × 4,
  `report/gpu_hours.py`). That is under the estimate of 170–200 and the cap of 300. The study now totals 354.7 GPU-h.
- **Gates.** Every gate passed in every benchmark × replicate, except E-0 on Terminal-Bench:
  - Terminal-Bench E-0 was 96.6 %, 96.5 % and 95.7 %, flagged below 99 % but above the pre-registered 90 % stop;
  - no preemptions;
  - fallbacks + resets 0.00–0.09 % of post-snip calls;
  - one server record per call; ranks consistent; no sessions left;
  - a decision record on every curated call of the recompute arm and nowhere else;
  - 6,414 decisions, 0 failed.
- **Server end.** The server stopped at 03:57 UTC, and GPUs 0–3 are empty.
- **No deviation from the pre-registration** during the main run.

**Verification of the tables (the /goal).**
- **The tables.** `report/e4_report.py` wrote them on 2026-10-17 after the stage ended. It reads the log's first
  177,539,639 bytes, sha256 `33407b5cda6350f3…`.
- **Independent recomputation.** `report/e4_verify.py` recomputed every value from the raw files with its own code:
  396 values, 0 mismatches, 0 broken invariants. That is
  3 benchmarks × 7 arms × 18 columns, plus the recompute arm's decision summary.
- **Regressions.** The same report code on published data:
  - e3: 81 cells, 0 mismatches; the only differences are the 2 definitional
    post-snip counts of the prefix arms;
  - e2: 20 call identities, 0 mismatches.
- **Byte-identical.** Regenerating the markdown and JSON from scratch gives identical files.
- **Spot traces.** `runs/epic/e4_spot_traces.md` (`run/e4_spot_traces.py`) traces 2 runs per arm and benchmark, call
  by call from the raw lines to the per-run values.
