# e3 pre-registration: does EPIC's KV reuse cost accuracy? (MiniMax-M2.7, SWE-bench Verified)

Written on 2026-10-14, before any e3 trial and before any e3 outcome existed. The file's sha256 is recorded in
`runs/epic/prereg_e3.sha256`, and `run/stage_e3.py` checks it, and the two hashes below, before it starts the
server. Anything changed after that point is reported as a deviation in the e3 part of `REPORT.md` §7, with its
time and reason.

## Questions

1. **H1 (primary).** Does `epic128` lose at most 5 points of SWE-bench Verified success against normal serving
   (full re-prefill after each snip)?
2. **H2 (key secondary).** Is `epic128a1` worse than normal serving?
3. **Exploratory.** Does a looser age cap (`epic128a4`) avoid what `epic128a1` loses?

## Why these questions

- e1 (SWE-bench Verified, 30 tasks × 3 replicates) and e2 (SWE-bench Pro, 30 tasks × 1 replicate), MiniMax-M2.7.
  Pooled, exploratory, differences against the control in success rate, 90 % intervals:
  - `epic128`: −0.002 [−0.054, +0.050]. No detectable drop, but the interval still reaches a 5-point loss.
  - `epic128a1`: −0.072 [−0.120, −0.024]. A drop of about 7 points on both benchmarks.
- e1's own pre-registered sequence stopped at its first test (epic128a1 against a 10-point margin), so epic128 was
  never formally tested.
- e3 tests both on 100 tasks that neither e1 nor the baseline profile used.

## Design

### Server

- `minimax-m2.7-epic` in `serve/models.yaml` on GPUs 0–3 (tensor parallel 4): vLLM 0.31 with the EPIC connector,
  the same configuration as e1 and e2 (gpu_util 0.93, `--watermark 0.08`, at most 48 sequences, the append-only
  chat template with the last-split fix). Two changes, both from e1/e2's lessons and neither touching what a call
  computes:
  - `EPIC_POOL_ROWS` 960,000 (was 640,000). e2 exhausted the host pool, and sessions then fell back to prefix.
  - `EPIC_SHADOW_EVERY` 32 (was 8). Fidelity probes now run on about 1 in 32 eligible calls of the EPIC arms and
    1 in 64 calls of the prefix arms, which slows the EPIC arms less. A probe never changes a response.

### Tasks

- The list is `run/state/e3_tasks.json`, sha256 `48123bc3855ce115bf1398cede1ad947bfd527a4e6cbd410bffd0f7791562777`, made by `run/sample_e3.py` (seed 20261014, drawn
  2026-10-14 01:02 UTC).
- Pool: the 500 tasks of `datasets/swe-bench-verified-prebuilt`, minus e1's 30 tasks and minus the 3 tasks labelled
  ">4 hours" (always unresolved in the baseline, and the most expensive).
- Draw, stratified by the dataset's difficulty label:
  - 35 of the 182 "<15 min fix" tasks;
  - 50 of the 249 "15 min - 1 hour" tasks;
  - 15 of the 37 "1-4 hours" tasks.
  Within each label the tasks were shuffled with the seed. The first n form the sample and the next 10 are spares,
  in order.
- Oracle check (Harbor's oracle agent applies the gold patch, before any GPU run): all 100 tasks passed (reward 1.0) on 2026-10-14 between 01:03 and 01:29 UTC (job `jobs/oracle/swe/e3check`, 8 at once), so no spare was used. The check also pulled every task image into the Docker-in-Docker daemon, so trials do not wait on downloads.
- Run order: a seeded shuffle of the 100 tasks. A task's block index is its position in the list.

### Arms

| arm | what the server does after a snip edits the history |
|---|---|
| `prefix` | recomputes everything after the edit (normal prefix caching) |
| `prefix_aa` | identical to `prefix`; the A/A copy, so the gap between the two is pure noise |
| `epic128` | reuses the stored KV of the kept text, recomputing the first 128 tokens after each edit |
| `epic128a1` | as `epic128`, and also recomputes every reused row that would have crossed more than 1 edit |
| `epic128a4` | as `epic128`, with an age cap of 4 edits instead of 1 |

**Control** = the mean of `prefix` and `prefix_aa` in the same block.

### Blocks, replicates and scheduling

- A **block** is one task in one replicate. All 5 arms start it within seconds of each other, on the same server,
  with common random numbers: each lead call's sampling seed is sha256(block key | call number), and the block key
  is the replicate's phase plus the task. The arms' launch order rotates from block to block.
- **3 replicates** (e3r0, e3r1, e3r2), run in order. Each covers all 100 tasks in list order, so there are 300 blocks
  and 1,500 trials. Blocks run per block, 4 at once (20 agent sessions, e1's load).
- Unchanged since e1/e2:
  - the s15 harness and its Harbor adapter, with the 250 lead-call cap;
  - the SWE verifier timeout ×0.6 (1,800 s, as in the official harness).
- **Agent timeout ×2 for every arm.** On the emulating server the EPIC arms' calls are slower, so the normal timeout
  would cut them off sooner (e1's lesson).
- **Infrastructure failures** are not outcomes: lost model connections, container or daemon errors, and the other
  classes in `report/collect.py` (`INFRA_EXC`, `CONN`). They are rerun under the same block key, for up to 2 rerun
  rounds. An agent timeout is an outcome, not an infrastructure failure.

### Outcome

A trial succeeds if the SWE-bench Verified verifier reports reward ≥ 1, i.e. all FAIL_TO_PASS and PASS_TO_PASS tests
pass.

## Analysis

The analysis is fixed in `report/e3_analysis.py`, sha256 `a7e7f285843df4440299daa685528e6c65021d6c5a85105124e3b08e43e69d2c`, together with the functions it uses from
`report/epic_report.py` and `report/collect.py`.

- **Unit.** Per arm and block, the first valid (finished, not infrastructure) trial by start time.
- **Statistic.**
  - d = arm − control in each block where the arm and both control arms are valid;
  - a task's mean d over its blocks;
  - the estimate is the mean of the task means, with a 90 % two-sided t-interval over the task means (df = tasks − 1;
    t = 1.660 at df 99). That is, one-sided 95 % bounds.
- **H1, epic128 − control.** Each test is at one-sided α = 0.05; H1 and H2 ask separate questions and get no
  multiplicity adjustment.
  - "**non-inferior**" if the lower bound is above −0.05;
  - "**inferior beyond the margin**" if the whole interval is below −0.05;
  - "**not shown**" otherwise.
- **H2, epic128a1 − control.**
  - "**worse than the control**" if the upper bound is below 0;
  - "**not shown**" otherwise.
- **A/A check, prefix_aa − prefix.** Its interval should cover 0. If it does not, the hypotheses are still reported,
  with the run flagged, as Terminal-Bench was in e2.
- **Partial runs.** All valid blocks are analysed, including those of a replicate cut short by a stop. The run order
  is random, so a cut-short replicate covers a random subset of tasks.

### Power

Power comes from e1's variance components:
- the variance of the per-block difference within a task, from block to block;
- the variance of the true task-level differences between tasks.

| comparison | within | between | SE, 100 × 3 | power |
|---|---|---|---|---|
| epic128 | 0.100 | 0.0037 | 0.019 | 83 % if the true difference is 0; 47 % if −0.02 |
| epic128a1 | 0.094 | 0.017 | 0.022 | 94 % for a true −0.07; 73 % for −0.05 |

With only 2 complete replicates, H1's power falls to about 69 %. That will be stated if it happens.

### Exploratory (reported without verdicts)

- epic128a4 against the control and against epic128a1; epic128 against epic128a1.
- By difficulty stratum.
- Blocks whose control snipped.
- Per arm: pass@1, lead calls, share of runs at the 250-call budget (looping), agent timeouts.
- Server side (`report/epic_report.py`, `report/latency_model.py`): prefill per call, reuse, probe fidelity, modeled
  latency.
- Descriptive pooling with e1 and e2.

## Validity gates and stop conditions

- **Gates per replicate, as in e1 (`epic/pilot_gates.py`):**
  - E-0 ≥ 99 %;
  - no preemptions;
  - fallbacks + resets below 5 % of post-snip calls;
  - exactly one server record per tagged lead call;
  - the same override counts on every tensor-parallel rank;
  - no session left on the server.
  The gates are reported with the results. A failed gate flags the result; it does not change the analysis.
- **Stop conditions** (`run/stage_e3.py`), checked after the first 8 complete blocks and at the end of every
  replicate:
  - a preemption;
  - fallbacks + resets at 5 % or more;
  - a missing or duplicate server record;
  - ranks that disagree;
  - E-0 below 97 % (the interim sample is small: e1's interim E-0 was 98.8 % and its final 99.2–99.4 %).
  Any of these means the emulation is not working as designed. On a stop, the blocks in flight finish, no new block
  starts, and the server stops. Resuming after a stop is a deviation.

## Stopping and budget

- **No interim look at outcomes.** While e3 runs, the operator watches only infrastructure signals: errors, gates,
  the health watcher, GPU-hours. No success rate is computed before the run ends.
- No early stop for efficacy or futility.
- **Budget.** About 86 GPU-h are expected (e1's 0.052 GPU-h per trial plus 10 %).
  - Replicate k+1 starts only if e3's GPU-hours so far plus replicate k's cost stay within 95 GPU-h (GPU-hours =
    server uptime × 4 GPUs).
  - At 95 GPU-h the run stops; the blocks in flight finish.

## Task list

| # | task | difficulty label |
|---|---|---|
| 0 | scikit-learn__scikit-learn-26194 | 15 min - 1 hour |
| 1 | sympy__sympy-12481 | <15 min fix |
| 2 | django__django-13128 | 1-4 hours |
| 3 | sphinx-doc__sphinx-9258 | <15 min fix |
| 4 | django__django-16100 | <15 min fix |
| 5 | scikit-learn__scikit-learn-12585 | <15 min fix |
| 6 | sympy__sympy-23824 | 15 min - 1 hour |
| 7 | django__django-16560 | 1-4 hours |
| 8 | django__django-10554 | 1-4 hours |
| 9 | astropy__astropy-7671 | 15 min - 1 hour |
| 10 | sympy__sympy-22080 | 15 min - 1 hour |
| 11 | sphinx-doc__sphinx-9658 | 15 min - 1 hour |
| 12 | sympy__sympy-13852 | 1-4 hours |
| 13 | django__django-16082 | 15 min - 1 hour |
| 14 | matplotlib__matplotlib-23314 | 15 min - 1 hour |
| 15 | django__django-16938 | 15 min - 1 hour |
| 16 | django__django-15380 | <15 min fix |
| 17 | django__django-11276 | 15 min - 1 hour |
| 18 | django__django-15572 | <15 min fix |
| 19 | sphinx-doc__sphinx-7440 | <15 min fix |
| 20 | django__django-13315 | 15 min - 1 hour |
| 21 | django__django-13028 | 15 min - 1 hour |
| 22 | sympy__sympy-24539 | <15 min fix |
| 23 | django__django-12406 | 15 min - 1 hour |
| 24 | django__django-13964 | 15 min - 1 hour |
| 25 | django__django-14315 | <15 min fix |
| 26 | matplotlib__matplotlib-25775 | 15 min - 1 hour |
| 27 | django__django-11119 | <15 min fix |
| 28 | matplotlib__matplotlib-24637 | 15 min - 1 hour |
| 29 | sympy__sympy-19783 | 15 min - 1 hour |
| 30 | django__django-11149 | 15 min - 1 hour |
| 31 | pytest-dev__pytest-5631 | 15 min - 1 hour |
| 32 | astropy__astropy-13033 | 15 min - 1 hour |
| 33 | sympy__sympy-15017 | <15 min fix |
| 34 | django__django-16569 | <15 min fix |
| 35 | sphinx-doc__sphinx-10323 | <15 min fix |
| 36 | django__django-16116 | <15 min fix |
| 37 | matplotlib__matplotlib-26113 | <15 min fix |
| 38 | django__django-15467 | <15 min fix |
| 39 | sympy__sympy-23262 | 15 min - 1 hour |
| 40 | scikit-learn__scikit-learn-10908 | 15 min - 1 hour |
| 41 | sympy__sympy-19346 | 15 min - 1 hour |
| 42 | django__django-13212 | 1-4 hours |
| 43 | sympy__sympy-23413 | 15 min - 1 hour |
| 44 | sympy__sympy-13091 | 15 min - 1 hour |
| 45 | django__django-15128 | 1-4 hours |
| 46 | django__django-13109 | <15 min fix |
| 47 | django__django-15957 | 1-4 hours |
| 48 | sphinx-doc__sphinx-8721 | <15 min fix |
| 49 | mwaskom__seaborn-3069 | 15 min - 1 hour |
| 50 | astropy__astropy-12907 | 15 min - 1 hour |
| 51 | sympy__sympy-16792 | 15 min - 1 hour |
| 52 | django__django-13449 | 1-4 hours |
| 53 | sympy__sympy-16450 | <15 min fix |
| 54 | matplotlib__matplotlib-20488 | 15 min - 1 hour |
| 55 | django__django-11433 | <15 min fix |
| 56 | matplotlib__matplotlib-25960 | 15 min - 1 hour |
| 57 | django__django-13568 | 15 min - 1 hour |
| 58 | pytest-dev__pytest-8399 | 15 min - 1 hour |
| 59 | sympy__sympy-18189 | <15 min fix |
| 60 | pytest-dev__pytest-7205 | <15 min fix |
| 61 | astropy__astropy-13579 | 1-4 hours |
| 62 | sympy__sympy-14248 | 1-4 hours |
| 63 | sympy__sympy-20916 | <15 min fix |
| 64 | django__django-10880 | <15 min fix |
| 65 | pytest-dev__pytest-6197 | 1-4 hours |
| 66 | django__django-13551 | <15 min fix |
| 67 | django__django-11206 | 15 min - 1 hour |
| 68 | sympy__sympy-24213 | 15 min - 1 hour |
| 69 | sympy__sympy-23950 | 15 min - 1 hour |
| 70 | matplotlib__matplotlib-22871 | 15 min - 1 hour |
| 71 | sympy__sympy-16597 | 1-4 hours |
| 72 | django__django-11292 | 15 min - 1 hour |
| 73 | django__django-15973 | 15 min - 1 hour |
| 74 | django__django-14855 | <15 min fix |
| 75 | django__django-13670 | 15 min - 1 hour |
| 76 | matplotlib__matplotlib-20676 | <15 min fix |
| 77 | sympy__sympy-13647 | 15 min - 1 hour |
| 78 | pydata__xarray-3993 | 1-4 hours |
| 79 | django__django-11299 | <15 min fix |
| 80 | sympy__sympy-21847 | <15 min fix |
| 81 | django__django-13512 | <15 min fix |
| 82 | django__django-11885 | 1-4 hours |
| 83 | django__django-16255 | <15 min fix |
| 84 | django__django-13121 | 15 min - 1 hour |
| 85 | scikit-learn__scikit-learn-11578 | 15 min - 1 hour |
| 86 | django__django-14559 | 15 min - 1 hour |
| 87 | django__django-15732 | 15 min - 1 hour |
| 88 | django__django-15277 | <15 min fix |
| 89 | django__django-13343 | 15 min - 1 hour |
| 90 | django__django-14122 | 15 min - 1 hour |
| 91 | django__django-10914 | <15 min fix |
| 92 | sympy__sympy-21379 | 15 min - 1 hour |
| 93 | sympy__sympy-11618 | 15 min - 1 hour |
| 94 | django__django-12125 | <15 min fix |
| 95 | sphinx-doc__sphinx-8551 | 15 min - 1 hour |
| 96 | sphinx-doc__sphinx-9230 | <15 min fix |
| 97 | django__django-14011 | 1-4 hours |
| 98 | pydata__xarray-3677 | 15 min - 1 hour |
| 99 | sphinx-doc__sphinx-7985 | 15 min - 1 hour |
