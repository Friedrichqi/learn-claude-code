## EPIC round: minimax-m2.7-epic on swe (phase e3)

Arms: epic128, epic128a1, epic128a4, prefix, prefix_aa. Control: mean of prefix and prefix_aa per block. Blocks: 300. Infrastructure failures are excluded and rerun under the same block key.

### Exploratory: resolve rate, arm minus control (t-interval over task means; intervals only, no verdicts)

| arm | tasks | blocks | difference | 90% two-sided [lo, hi] | non-inferior |
|---|---|---|---|---|---|
| epic128 | 100 | 300 | -0.010 | [-0.043, +0.023] | — |
| epic128a1 | 100 | 300 | -0.000 | [-0.033, +0.033] | — |
| epic128a4 | 100 | 300 | +0.017 | [-0.014, +0.047] | — |
| prefix_aa vs prefix (A/A) | 100 | 300 | +0.020 | [-0.017, +0.057] | noise floor |

### Per arm

| arm | trials | pass@1 | pass^3 | consistent | at budget | agent timeout | lead calls | out tok |
|---|---|---|---|---|---|---|---|---|
| epic128 | 300 | 0.730 | 0.590 | 0.74 | 1.0% | 0 | 46.3 | 10,292 |
| epic128a1 | 300 | 0.740 | 0.630 | 0.79 | 1.0% | 0 | 47.7 | 10,618 |
| epic128a4 | 300 | 0.757 | 0.620 | 0.74 | 1.0% | 0 | 44.0 | 9,915 |
| prefix | 300 | 0.730 | 0.580 | 0.74 | 1.7% | 0 | 46.7 | 10,621 |
| prefix_aa | 300 | 0.750 | 0.620 | 0.77 | 1.3% | 0 | 46.7 | 10,494 |

### Server side (EPIC_LOG)

| arm | lead calls logged | computed / prompt | post-snip calls | fallbacks | resets | errors | probes | first-token KL | reply KL | reply top-1 | D^K reused by age (0,1,2-3,4-7,8-15,16+) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| epic128 | 13892 | 4.3% | 7686 | 0 | 5 | 0 | 363 | 0.0314 | 0.0165 | 97.71% | 0.022 / 0.098 / 0.104 / 0.113 / 0.154 / 0.199 |
| epic128a1 | 14298 | 24.6% | 8058 | 0 | 1 | 0 | 344 | 0.0139 | 0.0085 | 98.06% | 0.027 / 0.089 / — / — / — / — |
| epic128a4 | 13190 | 11.0% | 7029 | 0 | 11 | 0 | 323 | 0.0220 | 0.0116 | 97.95% | 0.021 / 0.112 / 0.104 / 0.129 / — / — |
| prefix | 14012 | 47.7% | 0 | 0 | 0 | 0 | 0 | — | — | — | — / — / — / — / — / — |
| prefix_aa | 14001 | 46.1% | 0 | 0 | 0 | 0 | 0 | — | — | — | — / — / — / — / — / — |

### First divergence from `prefix` in the same block (lead-call index; −1 = identical run)

| arm | blocks | identical | median first divergent call |
|---|---|---|---|
| epic128 | 300 | 0 | 0.0 |
| epic128a1 | 300 | 0 | 0.0 |
| epic128a4 | 300 | 0 | 0.0 |
| prefix_aa | 300 | 0 | 0.0 |

### Setup and deviations (MiniMax-M2.7, phase e3: the pre-registered test on new SWE-bench Verified tasks)

**Why.** After e1 and e2, the user asked whether EPIC's KV-cache curation causes a distinguishable accuracy drop.
The existing data could not settle it:
- epic128 showed no drop, but its interval still reached a 5-point loss;
- epic128a1 dropped about 7 points, but only in exploratory data.

The user approved about 86 GPU-hours for a pre-registered test of both.

**Pre-registration.** `runs/epic/prereg_e3.md`, written before any e3 trial; its sha256 is in
`runs/epic/prereg_e3.sha256`. Before starting the server, `run/stage_e3.py` checks that hash and the hashes of the
task list and the analysis script.

**Same as e1:**
- the s15 harness and adapter, with the 250 lead-call cap;
- the e1 server configuration: `gpu_util` 0.93, `--watermark 0.08`, the append-only template with the last-split
  fix;
- paired blocks with common random numbers; control = mean of prefix and prefix_aa.

**Changed from e1, all fixed before any e3 result:**
- 100 new tasks (a stratified seeded draw, `run/sample_e3.py`), 3 replicates.
- 5 arms: `shift` dropped, `epic128a4` added.
- Agent timeout ×2 for every arm, as in e2.
- Per-block scheduling, 4 blocks at once; the arms' launch order rotates with the task's list index.
- Host pool 960K rows; shadow probes on every 32nd eligible call.

**Code since e2.** Hashes are in `runs/epic/code_e3.json`.
- Unchanged: the adapter, the EPIC server code (`epic/*.py` except the gates script) and the template.
- Changed:
  - `serve/models.yaml`: the pool size and the probe rate;
  - `run/batch.py`: `list:` task specs and `--rotate-offset`;
  - `run/epic_round.py`: per-block runs, distinct rerun names, block indices from the task list;
  - `epic/pilot_gates.py`: also counts per-block and rerun jobs;
  - report scripts: `--out` options and t-quantiles up to df 120.
- The eval image was rebuilt as `s15-eval:bb96a0e133b7` after the e2 teardown. Its build inputs differ from e2's
  only in `tests/`.
- Dry run before the GPU run: `run/epic_round.py --per-block` against the scripted fake model, 2 tasks × 5 arms plus
  a rerun round. It confirmed:
  - block and rerun names (`<phase>r0b00`, `<phase>r0b00x1`);
  - one seed key per replicate;
  - the rotated launch order;
  - the ×2 timeout in every trial.

**Oracle check before the GPU run.** Harbor's oracle agent applied the gold patch to all 100 tasks (01:03–01:29 UTC,
`jobs/oracle/swe/e3check`). Every task scored 1.0, so no spare was used. The check also pulled every task image
into the Docker-in-Docker daemon.

**Pre-registration fixed at 01:29 UTC:**
- `runs/epic/prereg_e3.md`: sha256 `80ab2942e9a6…`;
- task list: `48123bc3855c…`;
- analysis script: `a7e7f285843d…`.

**Deviation 1: a false stop at the interim check, then a resume (2026-10-14, 02:01 to about 02:30 UTC).**
- **What happened.** At 02:01:22 UTC the interim check ran after the first 8 complete blocks.
  - Every gate passed except "one server record per call". It counted 1 lead call without its server record, so the
    stage wrote the stop file as pre-registered.
  - The other figures were clean: E-0 99.84 % of 621 pairs, no preemptions, no fallbacks or resets in 1,027
    post-snip calls, ranks consistent.
- **Cause: a read-order race in the check itself, not in the emulation.**
  - `epic/pilot_gates.py` read the server's call log (44 MB) first and the trials' own call logs afterwards.
  - While trials run, a call that finishes between the two reads appears in the agent's log but not in the server-log
    snapshot already taken.
  - The server writes each record before the reply reaches the agent (`epic_server_ext.py`: `_log(rec)` comes before
    the final `yield`), so read in the other order the count is exact.
  - Recounted that way at about 02:04 UTC: all 2,224 tagged lead calls of e3r0 had exactly one record, none missing
    and none duplicated.
  - e1 and e2 never hit this because their gates ran after the trials had finished.
- **Fix.**
  - `pilot_gates.py` now reads the agents' logs first and the server log last (backup:
    `run/state/pilot_gates.py.before_e3fix`).
  - On e1r0 the old and the new script give identical reports.
  - A new test, `test_gates_read_the_server_log_after_the_agents_logs`, fails on the old order and passes on the new.
- **Resume.**
  - The 4 blocks in flight at the stop (b07, b08, b10, b11) finished as normal. No new block had started.
  - The stage was then terminated (stopping the server) and restarted with `--first-replicate 0 --base-gpu-h <the
    first start's GPU-h>`, so the budget rule counts everything.
  - The restart re-checks the pre-registration hashes, reruns the selftests, and runs the interim check again at
    once, with the fixed script.
  - The interim report that failed is kept as `runs/epic/gates_minimax-m2.7-epic_e3r0_first8_0201utc.json`.
- **As it happened:**
  - The first start was terminated at 02:24 UTC, after the 4 blocks finished. It had used 3.55 GPU-h (server ready
    01:31:05 to 02:24:19, × 4 GPUs).
  - The restart at 02:25 passed the hash check. Its server was ready at 02:27:00 and all 12 selftests passed.
  - Its interim check at 02:28:18 UTC (12 complete blocks, 80 trials, 20 sessions live) passed every stop condition:
    - E-0 99.60 % of 758 pairs;
    - no preemptions;
    - no fallbacks or resets in 1,803 post-snip calls;
    - 0 missing and 0 duplicate records;
    - ranks consistent.
  - e3r0 went on with its other 88 blocks.
- **What did not change:** the arms, tasks, seeds, blocks, the analysis and every threshold.
- **No outcome was looked at.**

**End of run (2026-10-14 19:34 UTC).**
- Replicates:
  - e3r0 ended 07:41 UTC, 24.5 GPU-h including the first start;
  - e3r1 ended 13:47, 24.4 GPU-h;
  - e3r2 ended 19:32, 23.0 GPU-h.
- Total 72.0 GPU-h of server time, plus about 0.2 GPU-h for the two server loads. The budget rule never bound:
  73.3 GPU-h were projected before e3r2, against the 95 cap.
- 1,500 trials, all valid, so no rerun round ran anything. No agent timeouts.
- 17 session resets, all decode overflows (an EPIC-arm reply ran past the 4,096-token reserve). 12 came from
  sympy__sympy-23413 and 5 from four other tasks. No fallbacks.
- Every gate passed in every replicate (`runs/epic/gates_minimax-m2.7-epic_e3r{0,1,2}.json`).
- The server stopped at 19:34 UTC and GPUs 0–3 are empty.

**Moved on 2026-10-15.** At the user's request the study moved from `/data/fritz/harness_eval` into the
learn-claude-code repo, as `research/16_epic_harness/harness_eval`.
- The scripts' hard-coded root path was rewritten. `report/e3_analysis.py` therefore no longer matches its
  pre-registered sha256 (`a7e7f285…`): it differs only in its `ROOT` line.
- The script exactly as pre-registered and as run is kept byte-identical as `runs/epic/prereg_e3_analysis.py`.
- Re-run from the new location, the analysis reproduces `report/e3_minimax-m2.7-epic_swe.md` byte for byte.

