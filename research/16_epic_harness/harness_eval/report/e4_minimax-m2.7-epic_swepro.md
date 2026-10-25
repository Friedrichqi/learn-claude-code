### e4 on SWE-bench Pro: secondary tables

#### Decisions of the recompute arm (SWE-bench Pro)

| arm | decisions / run | mean JSD (= recompute probability) | recompute rate | recomputes / run | decision overhead tokens / run (share of computed) | decision time | noise floor (JSD, n) | failed decisions |
|---|---|---|---|---|---|---|---|---|
| shift+EPIC+recompute@JSD | 11.567 | 0.0461 | 5.28% | 0.611 | 7,806 (15.9%) | 5.19 s | 0.0306 (66) | 0 |

Descriptive, for the future TVD and EXP arms: the same decisions' other metrics (their mean is the recompute rate each would have produced on these calls).

| arm | jsd | tvd | exp | kl | jsd20 | tvd20 | exp20 | kl20 |
|---|---|---|---|---|---|---|---|---|
| shift+EPIC+recompute@JSD | 0.0461 | 0.0370 | 0.0091 | 0.0093 | 0.0459 | 0.0370 | 0.0090 | 0.0092 |

#### Decision metrics against the real error (61 probed decision calls)

Spearman ρ between each decision metric (original vs after-EPIC) and the shadow probe's first-token KL of the same call (fresh recompute vs after-EPIC), with an approximate 90 % interval (Fisher z).

| metric | n | ρ | 90% [lo, hi] |
|---|---|---|---|
| jsd | 61 | +0.599 | [+0.443, +0.720] |
| tvd | 61 | +0.601 | [+0.445, +0.721] |
| exp | 61 | +0.587 | [+0.428, +0.711] |
| kl | 61 | +0.587 | [+0.428, +0.711] |
| jsd20 | 61 | +0.595 | [+0.437, +0.717] |
| tvd20 | 61 | +0.601 | [+0.445, +0.721] |
| exp20 | 61 | +0.594 | [+0.437, +0.716] |
| kl20 | 61 | +0.594 | [+0.437, +0.716] |

| JSD quintile | n | mean JSD | mean first-token KL (fresh vs arm) |
|---|---|---|---|
| 1 | 12 | 0.0056 | 0.1067 |
| 2 | 12 | 0.0228 | 0.0052 |
| 3 | 12 | 0.0396 | 0.0249 |
| 4 | 12 | 0.0524 | 0.0420 |
| 5 | 13 | 0.0896 | 0.0541 |

#### Sensitivity (SWE-bench Pro): resolve difference without blocks where any arm hit the agent timeout (2 blocks left out)

| arm | tasks | blocks | difference | 90% CI |
|---|---|---|---|---|
| prefix_aa (vs baseline, A/A) | 30 | 88 | -0.011 | [-0.062, +0.040] |
| shift | 30 | 88 | +0.028 | [-0.021, +0.077] |
| shift+EPIC | 30 | 88 | +0.106 | [+0.025, +0.186] |
| shift+EPICa1 | 30 | 88 | +0.056 | [-0.031, +0.142] |
| shift+EPICa4 | 30 | 88 | +0.017 | [-0.020, +0.053] |
| shift+EPIC+recompute@JSD | 30 | 88 | +0.011 | [-0.050, +0.072] |

#### Call accounting (SWE-bench Pro)

| arm | aborted records (P, C) | fallbacks | entry resets | lead calls without a record | records not joined | failed attempts | non-lead calls | runs with tool time clipped at 0 |
|---|---|---|---|---|---|---|---|---|
| baseline (full recomputation) | 0 (0, 0) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| prefix_aa | 0 (0, 0) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| shift | 0 (0, 0) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| shift+EPIC | 0 (0, 0) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| shift+EPICa1 | 1 (15,098, 6,521) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| shift+EPICa4 | 0 (0, 0) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| shift+EPIC+recompute@JSD | 0 (0, 0) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

#### Validity gates (SWE-bench Pro, epic/pilot_gates.py)

| replicate | trials | E-0 | preemptions | fallbacks+resets / post-snip | records | ranks | decisions (recompute rate) | failed decisions | pass |
|---|---|---|---|---|---|---|---|---|---|
| e4r0 | 210 | 99.2% | 0.0 | 0.00% | ok | ok | 377 (5.0%) | 0 | all |
| e4r1 | 210 | 99.6% | 0.0 | 0.00% | ok | ok | 353 (5.7%) | 0 | all |
| e4r2 | 210 | 99.4% | 0.0 | 0.00% | ok | ok | 311 (5.1%) | 0 | all |

