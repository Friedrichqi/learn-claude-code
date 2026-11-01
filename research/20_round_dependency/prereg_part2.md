# Pre-registration, part 2 as frozen: the load tests (S5)

Written 2026-10-29 at about 02:40 UTC. The gates had been read on the main run `main`, and no load cell had run.
Part 2 was drafted in `prereg.md` and frozen there with part 1 at 2026-10-28 21:01 UTC, before the main run ended.
That draft applies unchanged, except for what is stated below. This file and the code and plan of the load tests are
frozen by `prereg_part2.sha256`.

## Gate outcomes (rd_analyze.py --run main; rd_verify.py agrees on every value)

- **G_merge:** the topic-19 chain groups remove 28.2 % of lead calls per turn (need ≥ 10 %). **The merge test
  runs.**
- **G_reorder:** 4.3 % of SWE-bench Pro and Terminal-Bench tool seconds run beside an independent next call (need
  ≥ 10 %). **The reorder test is not run.**
  - That also drops its regime pilot and its cells `r_rec`, `r_low` and `r_cpu`.
  - It also drops the exploratory `r_merge`, which the draft tied to the reorder test.
- **Main-run validity:**
  - V1: 4,415 / 4,415 control renders equal the recorded prompt tokens;
  - V2: ρ = 0.11 [0.08, 0.15] on 368 copied pairs.

  Both pass. V3 (A/A) ran in the pilot only, as planned.

## The merge test as it will run

- **Plan:** `data/load/plan_load19.json`, built by `rd_loadreplay.py prepare --run main --units t19 --out load19`.
  - It holds 60 units (topic 19's sampled sessions): 2,080 recorded rounds in 1,494 chain groups.
  - BETA is 12.4 reasoning tokens per folded call. It is fitted on the plan's recorded rounds with 1-6 tool calls.
- **CPU validity check (`check --plan load19`):** passed.
  - 5,654 prompts rendered.
  - Every `rec` prompt equals the recorded prompt length up to the run nonce. The offset is the same for every call
    of a unit (1-6 tokens).
  - No `merge` prompt is longer than the recorded one.
- **Cells:** `m16_rec`, `m16_merge`, `m32_rec`, `m32_merge`, in this order.
  - Each cell runs in its own `rd_serve.py` stage with the `load19` configuration, so a fresh server, the sheet claim
    checked and the GPU cap enforced.
  - Each has a 15-min warm-up and a 60-min window.
- **Metrics and the decision rule:** as drafted in `prereg.md`.
  - The primary metric is recorded rounds completed per hour inside the window.
  - Each `merge` cell is compared with the `rec` cell at the same N: "helps", "hurts" or "no clear difference", with
    F = 10 % and a block bootstrap over the six 10-min blocks.
- **Incomplete cells:**
  - A cell that does not finish its window (claim end, crash, cap) is invalid. It is moved aside and run again in
    full under a later claim; partial cells are never analyzed.
  - If both cells of an N cannot be run, that N is reported as not run.

## Deviations from the draft

1. **Hashes:** the draft put part 2's hashes into `prereg.sha256`. They go into `prereg_part2.sha256` instead, so
   `prereg.sha256` keeps verifying part 1 exactly as frozen.

## Notes recorded at the freeze (exploratory, not pre-registered, no effect on the decisions)

- **The next round after a tool step.** After G_reorder was read, the Pro + Terminal-Bench tool seconds were split
  by the label of the round that follows:
  - 36 % are followed by a dependent round;
  - 5 % by an independent one;
  - 58 % by an undetermined one (p_C < 2/S). These are mostly in Terminal-Bench, where the model's own control
    samples rarely reproduce the recorded next action.
- **A distribution-level version of the TAU rule.** It compares the pending samples with the control samples
  instead of with the recorded action.
  - The rule: independent iff pc ≥ TAU · cc, with cc ≥ 2/S. Here pc is how often a pending sample matches a control
    sample, and cc how often two different control samples match.
  - It agrees with the labels on determinate pairs. Σpc / Σcc is 0.11 on dependent pairs, 0.92 on independent ones
    and 0.12 on the V2 copied set.
  - **What it counts:**
    - 4.1 % of the tool seconds are independent;
    - it cannot read 52 %, because there the control samples disagree with each other;
    - if the unread rounds behave like the read ones, the share is 8.6 %.
  - A continuous version, min(1, pc / cc), gives 8.8 % on what it reads and about 13 % extrapolated.
- **Ceiling.** In the sampled trials, tools take 26.8 % of (model + tool) time (Pro 18.8 %, Terminal-Bench 31.5 %).
  - Even running 13 % of the tool seconds beside the next call shortens a trial by at most about 3.5 %, so it raises
    throughput by at most about 4 %.
  - That is below F = 10 %. Whichever reading is used, the reorder test could not reach "helps" under the decision
    rule.
