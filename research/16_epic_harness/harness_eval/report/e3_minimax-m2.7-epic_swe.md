## e3: pre-registered test of EPIC's accuracy cost (MiniMax-M2.7, SWE-bench Verified, new tasks)

Pre-registration: `runs/epic/prereg_e3.md` (sha256 in `runs/epic/prereg_e3.sha256`). Tasks: 100 new SWE-bench Verified tasks (seed 20261014; strata <15 min fix 35, 15 min - 1 hour 50, 1-4 hours 15; 0 replaced after the oracle check). Arms prefix, prefix_aa, epic128, epic128a1, epic128a4; control = mean of prefix and prefix_aa per block. Differences are in success rate (0.05 = 5 points); intervals are 90 % two-sided t-intervals over task means, i.e. one-sided 95 % bounds.

### Hypotheses

| test | tasks | blocks | difference | 90% [lo, hi] | result |
|---|---|---|---|---|---|
| H1 (primary): epic128 − control, non-inferior at margin 0.05 | 100 | 300 | -0.010 | [-0.043, +0.023] | **non-inferior** (lower bound above −0.05) |
| H2: epic128a1 − control, worse than the control | 100 | 300 | -0.000 | [-0.033, +0.033] | not shown (the interval reaches 0) |
| check: prefix_aa − prefix (A/A) | 100 | 300 | +0.020 | [-0.017, +0.057] | covers 0 |

### Validity gates per replicate (epic/pilot_gates.py)

| replicate | trials | E-0 | preemptions | fallbacks+resets / post-snip | records | ranks | sessions left |
|---|---|---|---|---|---|---|---|
| e3r0 | 500 | 99.4% pass | 0.0 pass | 0.07% pass | pass | pass | pass |
| e3r1 | 500 | 99.6% pass | 0.0 pass | 0.01% pass | pass | pass | pass |
| e3r2 | 500 | 99.4% pass | 0.0 pass | 0.01% pass | pass | pass | pass |

### Exploratory (no verdicts)

| comparison | tasks | blocks | difference | 90% [lo, hi] |
|---|---|---|---|---|
| epic128a4 − control | 100 | 300 | +0.017 | [-0.014, +0.047] |
| epic128a4 − epic128a1 | 100 | 300 | +0.017 | [-0.025, +0.059] |
| epic128 − epic128a1 | 100 | 300 | -0.010 | [-0.053, +0.033] |
| epic128 − control, stratum “<15 min fix” | 35 | 105 | -0.033 | [-0.093, +0.026] |
| epic128a1 − control, stratum “<15 min fix” | 35 | 105 | -0.024 | [-0.059, +0.011] |
| epic128a4 − control, stratum “<15 min fix” | 35 | 105 | +0.014 | [-0.017, +0.046] |
| epic128 − control, stratum “15 min - 1 hour” | 50 | 150 | +0.000 | [-0.042, +0.042] |
| epic128a1 − control, stratum “15 min - 1 hour” | 50 | 150 | +0.027 | [-0.028, +0.082] |
| epic128a4 − control, stratum “15 min - 1 hour” | 50 | 150 | -0.007 | [-0.056, +0.043] |
| epic128 − control, stratum “1-4 hours” | 15 | 45 | +0.011 | [-0.105, +0.127] |
| epic128a1 − control, stratum “1-4 hours” | 15 | 45 | -0.033 | [-0.133, +0.066] |
| epic128a4 − control, stratum “1-4 hours” | 15 | 45 | +0.100 | [+0.002, +0.198] |
| epic128 − control, blocks whose control snipped | 92 | 253 | -0.006 | [-0.043, +0.030] |
| epic128a1 − control, blocks whose control snipped | 92 | 253 | -0.008 | [-0.048, +0.032] |
| epic128a4 − control, blocks whose control snipped | 92 | 253 | +0.021 | [-0.014, +0.056] |

| arm | trials | pass@1 | lead calls | at budget | agent timeout |
|---|---|---|---|---|---|
| prefix | 300 | 0.730 | 46.7 | 1.7% | 0 |
| prefix_aa | 300 | 0.750 | 46.7 | 1.3% | 0 |
| epic128 | 300 | 0.730 | 46.3 | 1.0% | 0 |
| epic128a1 | 300 | 0.740 | 47.7 | 1.0% | 0 |
| epic128a4 | 300 | 0.757 | 44.0 | 1.0% | 0 |

