# 20 · Round dependency: does each agentic round need the one before? (MiniMax-M2.7, s15 harness)

> **Question.** Does a strong dependency run through all agentic rounds of MiniMax-M2.7 in the s15 harness?
> - If it does not, independent requests could be reordered by what they consume.
> - When the server is busy, GPU-heavy model requests could wait while independent CPU work (the tools) runs.
> - Requests that lead to CPU-heavy work could go first.
>
> The study profiles two properties of every round and tests both under load: whether it could be **merged** into
> the previous response, and whether it could be **reordered**, i.e. sent while the previous round's tools still run.
>
> **Plan:** `PLAN.md` (approved 2026-10-28; recovered on 2026-10-30 from the session transcript, since the plan file
> `~/.claude/plans/i-desire-to-run-moonlit-panda.md` now holds topic 21's plan). Pre-registration: `prereg.md` and
> `prereg.sha256`.
>
> **Budget:** GPUs 0-3 only (four H100 80GB, MiniMax-M2.7 at TP=4, one server at a time), hard cap 90 GPU-h.
>
> **Status:** profile done (S0-S4). The merge load test (S5) is not run: its runner was stopped on 2026-10-29 before
> any GPU claim arrived. The reorder load test is not run, because its gate failed.
> - **Dates (UTC):**
>   - 2026-10-28: S0, the CPU build and census; smoke 20:18-20:24; pilot 20:24-21:00; pre-registration part 1
>     frozen 21:01; main replay 21:01 until 02:21 on 10-29.
>   - 2026-10-29: analysis and gates 02:25; pre-registration part 2 frozen 02:44.
> - **GPU time:** 24.03 GPU-h so far (see the GPU-hour log).
> - **Commits:** none. Everything here is uncommitted.

**Contents** — Short answer · Why · Definitions · Data · What was built · S0 static census · S1 smoke · S2 pilot ·
S3 main replay · S4 analysis · S5 load tests · GPU-hour log · Reproduction · Data inventory · Changes

## Short answer

**No strong dependency runs through all rounds. But the rounds that carry the CPU time are chained.**

- **About half of the rounds do not need the previous round's results.**
  - The pending test hides round j-1's tool results behind s15's background placeholder.
  - In about half of the rounds whose next action can be reproduced at all, MiniMax still takes the recorded next
    action.

  | source | reproducible pairs | independent of the previous round | pre-registered wording |
  |---|---|---|---|
  | topic 19 (4-turn code sessions) | 77 % of 1,621 | 49 % [43 %, 55 %] | "weak dependency: many rounds do not need the previous results" |
  | topic 16 (SWE-bench Verified and Pro, Terminal-Bench) | 64 % of 2,183 | 43 % [40 %, 47 %] | the same, at the edge: the interval reaches the 20-40 % band, "mostly chained, with a minority of independent rounds" |

- **Independence follows the kind of step.**
  - A planned series of edits does not wait for each result: edit → edit is 97 % independent, edit → shell 95 %,
    todo → edit 97 %.
  - Reading something and then acting on it does wait: read → edit is 9 % independent, shell → edit 4 %, shell → read
    17 %, shell → shell 24 %.
- **Chains are short.** A chain group is a run of rounds that each could be produced while the group's earlier
  results are pending.
  - Groups hold 1.33-1.39 rounds on average, and 78-80 % are single rounds.
  - Folding each group into one call removes 28 % of topic 19's lead calls and 25 % of topic 16's.
- **The CPU time sits in the chained rounds.**
  - Rounds with at least 1 s of tools (346 of them, median 6.9 s) are followed by an independent round in only 19 %
    of determinate cases.
  - Only 4.3 % of SWE-bench Pro and Terminal-Bench tool seconds run while the next call could already be sent. The
    model waits for the output of long commands (tests, builds, downloads, installs) before it acts.
- **So merging may help a crowded server. Overlapping GPU calls with CPU tool time has little to work with in these
  workloads.**
  - G_merge passed (28 % ≥ 10 %), so the merge load test runs (S5).
  - G_reorder failed (4.3 % < 10 %), so the reorder load test is not run.
  - Even the most generous exploratory reading (13 % of tool seconds) caps the throughput gain of reordering at about
    4-6 %, below the 10 % decision floor.
- **Copying is a poor proxy for dependence.** "The arguments copy a string from the previous results" predicts
  dependence with 92 % precision but only 20 % recall: most dependent rounds copy nothing.

The merge load test is pending (S5).

## Why

- **Topics 11, 18 and 19 found the bottleneck of a crowded agent server.** It is KV residency: under contention, a
  session's prefix is evicted between its calls, and every call re-prefills it.
  - Topic 19's MiniMax-M2.7 runs about 10-11 lead calls per turn. At N=16 a third of the GPU goes to that
    recomputation, and 60-90 % to decoding.
- **Every lead call is a full model request, and in s15 each round waits for the one before.** The harness runs a
  response's tool calls one after another (`s15_integrated_harness/code.py:3814-3890`). Its system prompt already
  asks the model to batch independent calls (`code.py:951-955`).
- **So two kinds of slack would matter:**
  - rounds that need nothing from the previous results could be folded into the previous response (fewer calls);
  - or they could be sent while the previous tools still run (overlap).
- **Nothing in the repository measured this.** Trace events carry `depends_on_event_ids: []`, and topic 17's
  critical path assumes a chain.

## Definitions

- **Round j:** lead call L_j answers a_j (thinking + tool calls c_j). The harness runs c_j, which gives the results
  O_j.
- **Pending test P_j:** L_j's recorded prompt with every result of round j-1 replaced by s15's own background
  placeholder, "[Background task bg_NNNN started] Result will arrive as a task_notification."
- **Independent of the previous round:** the model still produces c_j under P_j. The rule is p_P ≥ TAU · p_C,
  with p_C ≥ 2/S.
- **Chain group (merged world):** a maximal run of rounds k..m, each produced while all earlier results of the
  group are pending.
  - **Mergeable:** a round inside a group after its first.
  - **Reorderable:** mergeable, and its tool effects commute with the group's.
- **Order-free:** c_j already appears among the samples of L_{j-1}.

## Data

- **Topic 19:** `none` + `sgate` cells: 1,050 sessions, 39,924 rounds. These are topic 11's 4-turn code sessions,
  EXPLAIN E1-E3 and MODIFY M1-M3, on 10 codebases.
- **Topic 16:** the MiniMax-M2.7 profile plus the EPIC `prefix` / `prefix_aa` trials: 1,760 trials, 77,445 rounds,
  on SWE-bench Verified, SWE-bench Pro and Terminal-Bench.
- **Tool share of (tool + model) time:**

  | workload | tool share |
  |---|---|
  | topic 19 | 0.01-0.14 % |
  | SWE-bench Verified | 9.9 % |
  | SWE-bench Pro | 41.6 % |
  | Terminal-Bench | 46.4 % |

  So reordering can only pay off on Pro and Terminal-Bench, which have CPU work to overlap.

## What was built (S0, CPU)

| file | what it does |
|---|---|
| `rd_rounds.py` | the rounds table of both sources, plus flags: final, append-only, recent-tail kept, where results came from |
| `rd_static.py` | the static census: argument provenance, read/write effects, static DAG and merge groups, resources |
| `rd_cf.py` | the counterfactual replay: sample, control / pending / merged-world variants, parser, action keys, chain |
| `rd_serve.py` | MiniMax server lifecycle on GPUs 0-3: sheet claims, cap, GPU-hour log, stage window |
| `rd_analyze.py` | tables, gates and decisions of a replay run |
| `rd_verify.py` | an independent recomputation of the headline values from the raw records |
| `rd_explore.py` | exploratory breakdowns: transitions, position, group lengths, CPU-heavy rounds |
| `rd_loadreplay.py` | the S5 load tests: closed-loop replay of recorded, merged and reordered sessions |
| `test_rd.py` | CPU tests, including an end-to-end replay against a fake server |

## S0 static census

`data/tables/census.md`: every round of both sources, on CPU. A round "copies the previous results" when a salient
string of its arguments first appeared there (paths, line anchors, grep patterns, code identifiers, an edit's first
lines).

| | topic 19 | topic 16 |
|---|---|---|
| rounds (turns) | 39,924 (3,887) | 77,445 (1,760) |
| responses with several tool calls | 41 % | 9 % |
| arguments copy the previous round's results | 13 % | 7 % |
| arguments copy nothing from any result of the turn | 58 % | 39 % |
| effects conflict with the previous round's | 56 % | 76 % |
| static merge groups / recorded calls | 0.32 | 0.14 |
| tool seconds per round | 0.01 | 2.85 |

- **Copying is a poor proxy for dependence.** The pilot's replay labels show that "copies the previous results"
  predicts "dependent" with 95 % precision but only 17 % recall: most dependent rounds copy nothing.
- **So the static merge estimate (0.32 and 0.14 groups per call) is far too optimistic.** The census serves as
  context, and the counterfactual replay gives the answer.

## S1 smoke

20:18-20:24 UTC, 0.31 GPU-h. One topic-19 session and three topic-16 trials, the first 6 rounds of each turn,
S = 8: 38 rounds, 73 sampling batches.
- **Server:** ready in 115 s; KV pool 277,072 tokens; prefix-cache hit rate 92-98 %.
- **V1:** 38/38 control renders equal the recorded prompt tokens.
- **Warm-then-sample works:** the sampled request hit about 99.9 % of its prompt.
- **V2 (positive control):** ρ = 0.11 on 6 copied pairs.
- **The placeholder works as intended:**
  - when a round needs the pending result, MiniMax waits ("Let me wait for the background task to complete"),
    re-runs the pending call, or calls list_tasks;
  - when it does not, it goes ahead: in one SWE trial, 5 of 8 samples read the file named in the pending glob.
- **Fixed after the smoke:** s15's read-only classifier fails closed on any '>', so `grep … 2>/dev/null` counted
  as a write, and `2>/dev/null` as a path. rd_static now strips /dev/null redirections before classifying.

## S2 pilot

20:24-21:00 UTC, 2.40 GPU-h. 375 rounds of units disjoint from the main sample: 4 topic-19 pilot-cell sessions
and 9 e4r1 trials. Variants C, P, M and A (a second control with its own seeds).

| gate | value | rule | verdict |
|---|---|---|---|
| V1 renders equal the recorded tokens | 375/375 | ≥ 95 % | pass |
| V2 positive control (25 copied pairs) | ρ = 0.06 [0.01, 0.11] | ≤ 0.30 | pass |
| V3 A/A (325 pairs) | ρ = 1.03 [1.00, 1.05] | 0.85-1.15 | pass |

- **TAU = 0.5:** 4 % of determinate A/A rounds are called dependent at 0.5.
- **S = 8:** projected 19.2 GPU-h for the main sample's ~9,360 batches, against 32.3 left of the 35 for S1-S3.
- **Sample lengths:** a sample's median reasoning is 186 characters (p99 1,441), with no truncation at 8,192 tokens.
- **Client concurrency was the bottleneck** (8 batches in flight; 26-54 sequences running), so the main run sends
  16.
- **The pilot's estimates are calibration only and are not reported as results.** The main sample is a different,
  larger set of units.

## S3 main replay

2026-10-28 21:01 until 2026-10-29 02:21 UTC, 21.32 GPU-h. The pilot projected 19.2, so the projection was 11 % low.
- **Sample:** 4,415 rounds in 318 turns.
  - Topic 19: 60 sessions, 240 turns, 2,080 rounds.
  - Topic 16: 78 trials (20 SWE-bench Verified, 30 Pro, 28 Terminal-Bench), 2,335 rounds.
- **Sampling batches:** 4,415 control, 3,804 pending and 1,081 merged-world, each with S = 8 samples. There was one
  retry and no failure.
- **Validity:**
  - V1: 4,415 / 4,415 control renders equal the recorded prompt tokens.
  - V2: ρ = 0.11 [0.08, 0.15] on 368 copied pairs.

  Both pass. V3, the A/A check, ran in the pilot only, as planned.
- **Reproducible pairs.** A pair is determinate when the recorded action comes back in at least 2 of 8 control
  samples. 69 % of pairs are determinate: topic 19 77 %, SWE-bench Verified 66 %, Pro 74 %, Terminal-Bench 50 %.
- **Checks:**
  - `rd_verify.py` recomputes every headline value from the raw records and agrees.
  - The tables regenerate byte for byte.

## S4 analysis

`data/tables/cf_main.md` holds the pre-registered outcomes, and `data/tables/explore_main.md` the exploratory
breakdowns. Intervals are 95 % percentile bootstraps over sessions or trials.

**Columns:**
- **independent:** the share of determinate pairs where the pending samples still take the recorded action
  (p_P ≥ 0.5 · p_C).
- **ρ:** Σp_P / Σp_C over all pairs.
- **mergeable:** the round joins its predecessor's chain group.
- **reorderable:** mergeable, and its effects commute with the group's.
- **order-free:** the round's action already appears among the previous call's samples.

| subset | pairs | determinate | independent | ρ | mergeable | reorderable | order-free |
|---|---|---|---|---|---|---|---|
| all | 3,804 | 69 % | 46 % [42, 50] | 0.50 | 31 % | 12 % | 21 % |
| **topic 19** | 1,621 | 77 % | **49 % [43, 55]** | 0.53 | 36 % | 15 % | 27 % |
| E1 inventory | 110 | 81 % | 38 % [25, 50] | 0.36 | 28 % | 26 % | 27 % |
| E2 trace | 346 | 77 % | 40 % [31, 49] | 0.40 | 29 % | 22 % | 50 % |
| E3 orientation | 96 | 86 % | 33 % [18, 47] | 0.32 | 22 % | 19 % | 37 % |
| M1 build tag + changelog | 402 | 69 % | 32 % [27, 37] | 0.35 | 23 % | 16 % | 13 % |
| M2 `_v2` rename | 164 | 78 % | 41 % [34, 49] | 0.45 | 32 % | 7 % | 10 % |
| M3 docstrings | 503 | 80 % | 76 % [69, 82] | 0.81 | 58 % | 9 % | 24 % |
| **topic 16** | 2,183 | 64 % | **43 % [40, 47]** | 0.48 | 26 % | 10 % | 16 % |
| SWE-bench Verified | 738 | 66 % | 36 % [31, 41] | 0.42 | 23 % | 9 % | 16 % |
| SWE-bench Pro | 793 | 74 % | 51 % [45, 56] | 0.54 | 35 % | 14 % | 16 % |
| Terminal-Bench | 652 | 50 % | 40 % [33, 45] | 0.46 | 19 % | 5 % | 15 % |

**By the tool class of round j** (both sources):

| class | pairs | determinate | independent | mergeable | reorderable |
|---|---|---|---|---|---|
| read | 1,339 | 80 % | 40 % | 31 % | 22 % |
| edit | 912 | 88 % | 54 % | 46 % | 11 % |
| shell | 1,272 | 42 % | 49 % | 20 % | 2 % |
| todo | 183 | 89 % | 43 % | 34 % | 23 % |
| mixed | 98 | 83 % | 32 % | 23 % | 4 % |

**Merge and overlap (Q2, Q5):**

| subset | turns | calls | calls a merge removes | output tokens of removed calls | tool s beside an independent next call | rounds with ≥ 1 s tools | of these followed by an independent round |
|---|---|---|---|---|---|---|---|
| topic 19 | 240 | 2,080 | 28 % | 18 % | 15 % | 0 | — |
| topic 16 | 78 | 2,335 | 25 % | 17 % | 5 % | 346 | 11 % |
| SWE-bench Verified | 20 | 778 | 22 % | 15 % | 26 % | 82 | 21 % |
| SWE-bench Pro | 30 | 853 | 33 % | 33 % | 13 % | 124 | 10 % |
| Terminal-Bench | 28 | 704 | 18 % | 9 % | 1 % | 140 | 6 % |

A merge does not save all of the removed calls' output: the folded tool calls are still generated. The load test
counts their tokens plus 12.4 reasoning tokens per folded call.

**Exploratory breakdowns** (`rd_explore.py`, not pre-registered):
- **By transition (round j-1 → round j), share independent:**

  | transition | pairs | determinate | independent |
  |---|---|---|---|
  | edit → edit | 376 | 349 | 97 % |
  | todo → edit | 75 | 65 | 97 % |
  | edit → shell | 238 | 154 | 95 % |
  | edit → todo | 53 | 51 | 92 % |
  | edit → read | 190 | 139 | 70 % |
  | read → read | 826 | 683 | 39 % |
  | shell → shell | 752 | 271 | 24 % |
  | read → shell | 207 | 67 | 19 % |
  | shell → read | 242 | 174 | 17 % |
  | read → edit | 311 | 265 | 9 % |
  | shell → edit | 144 | 114 | 4 % |

  - After an edit the next step is usually already planned: the next edit of a series, or the test command.
  - After a read or a command the model needs what it saw.
- **By position:** pairs in the middle half of a turn are more often independent (52 % and 49 %) than in the first
  and last quarters (40 % and 39 %).
- **When a round depends on its predecessor,** the pending samples call another tool (64 %), re-issue the pending
  call (30 %), or still take the recorded action (6 %). The model rarely acts as if it had seen the hidden result.
- **Chain group lengths:**
  - topic 19: 80 % single rounds, 14 % pairs, mean 1.39;
  - topic 16: 78 % single rounds, 17 % pairs, mean 1.33.
- **CPU-heavy rounds** (≥ 1 s of tools; 346, all in topic 16, median 6.9 s): the next round is independent of their
  results in 37 of 198 determinate cases (19 %).

**Static census against the replay** (2,640 determinate pairs):
- "Copies the previous results" predicts "dependent" with 92 % precision and 20 % recall. 1,143 dependent rounds copy
  nothing from the previous results.
- Static merge groups agree with the replay's chain on 41 % of rounds. The census cannot stand in for the replay.

**Load-test gates:**

| gate | value | rule | decision |
|---|---|---|---|
| G_merge: topic-19 lead calls a merge removes | 28.2 % | ≥ 10 % | merge test runs |
| G_reorder: Pro + Terminal-Bench tool seconds beside an independent next call | 4.3 % | ≥ 10 % | reorder test not run |

**Is the reorder verdict robust?** These readings are exploratory, from `prereg_part2.md`.
- **The tool seconds by the round that follows:** 36 % dependent, 5 % independent, 58 % undetermined. The
  undetermined part is mostly Terminal-Bench, where the model's own control samples rarely reproduce the recorded
  next action.
- **A distribution-level version of the rule** compares pending samples with control samples instead of with the
  recorded action.
  - It agrees with the labels on determinate pairs: Σpc / Σcc is 0.11 on dependent pairs, 0.92 on independent ones
    and 0.12 on the V2 set.
  - It reads 4.1 % of the tool seconds as independent, or 8.6 % if the unread half behaves like the rest.
  - A continuous version reads about 13 % extrapolated.
- **What the long tool steps are.** The CPU-heavy calls are tests (pytest, npm test, Django's runtests), builds
  (go build, make, tsc), downloads, installs and training runs. The next step reads their output.
- **Ceiling.** Tools take 27 % of (model + tool) time in these trials: Pro 18.8 %, Terminal-Bench 31.5 %.
  - The Data section's 41.6 % and 46.4 % come from the wider planning sample, which includes trials with more than
    100 calls.
  - Even at 46 %, running 13 % of the tool seconds beside the next call shortens a trial by at most 6 %. That is a
    throughput gain of at most 6.4 %, below the pre-registered floor F = 10 %, so the test could not have shown
    "helps".

## S5 load tests

**Merge test (topic 19, server `load19`).**
- **Server:** topic 19's configuration: pool pinned at 16,137 blocks, 64 sequences, a 65,536-token window, topic 11's
  scheduler log, FCFS.
- **Cells:** `m16_rec` and `m16_merge`, then `m32_rec` and `m32_merge`.
  - `rec` replays the recorded calls in order.
  - `merge` folds each chain group into one call.
  - Each cell is a closed loop of N sessions (16 or 32), with a 15-min warm-up and a 60-min window.
- **Plan:** `data/load/plan_load19.json`: 60 sessions, 2,080 rounds in 1,494 chain groups.
  - Its CPU check passed: every `rec` prompt equals the recorded length up to the run nonce, and no `merge` prompt
    is longer than recorded.
- **Status: not run.** A runner waited for a GPU sheet claim from 02:45 to 20:26 UTC on 2026-10-29. No claim
  arrived, and it was stopped at the user's request with no GPU time used.
  - The plan, its CPU check and part 2 of the pre-registration stay ready.
  - The test runs with the `chain` command under Reproduction once GPUs 0-3 are claimed for about 6 h.

**Reorder test: not run.** G_reorder failed (S4).

**Pre-registration part 2:** frozen 2026-10-29 02:44 UTC in `prereg_part2.md` and `prereg_part2.sha256`.

## GPU-hour log

| stage | GPU-h | tally |
|---|---|---|
| S1 smoke | 0.31 | 0.31 |
| S2 pilot | 2.40 | 2.71 |
| S3 main replay | 21.32 | 24.03 |

## Reproduction

```bash
PY=/data/fritz/venv/bin/python
$PY research/20_round_dependency/rd_rounds.py build && $PY research/20_round_dependency/rd_static.py build
$PY research/20_round_dependency/rd_static.py tables
$PY research/20_round_dependency/test_rd.py
$PY research/20_round_dependency/rd_cf.py sample --pilot && $PY research/20_round_dependency/rd_cf.py sample
$PY research/20_round_dependency/rd_cf.py check --sample pilot
$PY research/20_round_dependency/rd_serve.py run --stage s2_pilot --hours 1 -- \
    $PY research/20_round_dependency/rd_cf.py run --url {url} --sample pilot --out pilot --aa
$PY research/20_round_dependency/rd_analyze.py --run pilot --pilot
# S3 main replay, S4 analysis
$PY research/20_round_dependency/rd_serve.py run --stage s3_main --hours 6.8 -- \
    $PY research/20_round_dependency/rd_cf.py run --url {url} --sample main --out main --S 8 --tau 0.5 \
    --inflight 16 --turns 64 --renderers 32
$PY research/20_round_dependency/rd_analyze.py --run main
$PY research/20_round_dependency/rd_verify.py --run main
$PY research/20_round_dependency/rd_explore.py --run main
# S5 merge test
$PY research/20_round_dependency/rd_loadreplay.py prepare --run main --units t19 --out load19
$PY research/20_round_dependency/rd_loadreplay.py check --plan load19
$PY research/20_round_dependency/rd_loadreplay.py chain m16_rec:load19:rec:16 m16_merge:load19:merge:16 \
    m32_rec:load19:rec:32 m32_merge:load19:merge:32
$PY research/20_round_dependency/rd_loadreplay.py show --cells m16_rec,m16_merge --ref m16_rec
```

## Data inventory

Everything is under `data/` (gitignored).

| path | contents |
|---|---|
| `rounds/{sessions,rounds}_{t19,t16}.jsonl.xz` | the rounds tables |
| `static/static_{t19,t16}.jsonl.xz` | the static census per round |
| `cf/sample_{pilot,main}.json` | the replay samples |
| `cf/{smoke,pilot,main}/` | `run.json`, `records.jsonl` (per batch: samples' keys, p values; chain records), `texts.jsonl` (sample texts) |
| `tables/` | `census.md`, `cf_<run>.{md,json}`, `explore_<run>.{md,json}` |
| `load/plan_load19.json` | the merge test's plan |
| `load/cells/<cell>/` | `cell.json`, `calls.jsonl`, `rounds.jsonl`, `WINDOW_END` |
| `servers/<stage>_<stamp>/` | vLLM logs and, for load cells, the scheduler log |
| `gpu_hours.jsonl` | one line per GPU stage |
| `jobs/` | stage logs |

## Changes

- **2026-10-28, before the freeze:**
  - the S rule projects per sampling batch and leaves out the pilot-only A/A variant;
  - S = 4 was dropped for resolution.
- **2026-10-29, part 2:** its hashes went into `prereg_part2.sha256` rather than `prereg.sha256`, so the part-1
  hash file still verifies part 1 as frozen.
