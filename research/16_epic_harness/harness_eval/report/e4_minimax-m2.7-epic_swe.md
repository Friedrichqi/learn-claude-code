### e4 on SWE-bench Verified: secondary tables

#### Decisions of the recompute arm (SWE-bench Verified)

| arm | decisions / run | mean JSD (= recompute probability) | recompute rate | recomputes / run | decision overhead tokens / run (share of computed) | decision time | noise floor (JSD, n) | failed decisions |
|---|---|---|---|---|---|---|---|---|
| shift+EPIC+recompute@JSD | 37.656 | 0.0531 | 5.55% | 2.089 | 15,642 (20.6%) | 2.68 s | 0.0354 (215) | 0 |

Descriptive, for the future TVD and EXP arms: the same decisions' other metrics (their mean is the recompute rate each would have produced on these calls).

| arm | jsd | tvd | exp | kl | jsd20 | tvd20 | exp20 | kl20 |
|---|---|---|---|---|---|---|---|---|
| shift+EPIC+recompute@JSD | 0.0531 | 0.0433 | 0.0115 | 0.0118 | 0.0528 | 0.0432 | 0.0114 | 0.0117 |

#### Decision metrics against the real error (132 probed decision calls)

Spearman ρ between each decision metric (original vs after-EPIC) and the shadow probe's first-token KL of the same call (fresh recompute vs after-EPIC), with an approximate 90 % interval (Fisher z).

| metric | n | ρ | 90% [lo, hi] |
|---|---|---|---|
| jsd | 132 | +0.529 | [+0.416, +0.625] |
| tvd | 132 | +0.530 | [+0.418, +0.626] |
| exp | 132 | +0.530 | [+0.418, +0.626] |
| kl | 132 | +0.530 | [+0.418, +0.626] |
| jsd20 | 132 | +0.527 | [+0.415, +0.624] |
| tvd20 | 132 | +0.528 | [+0.416, +0.624] |
| exp20 | 132 | +0.530 | [+0.418, +0.626] |
| kl20 | 132 | +0.530 | [+0.418, +0.626] |

| JSD quintile | n | mean JSD | mean first-token KL (fresh vs arm) |
|---|---|---|---|
| 1 | 26 | 0.0100 | 0.0052 |
| 2 | 26 | 0.0287 | 0.0242 |
| 3 | 27 | 0.0470 | 0.0413 |
| 4 | 26 | 0.0647 | 0.0247 |
| 5 | 27 | 0.1158 | 0.0583 |

#### Call accounting (SWE-bench Verified)

| arm | aborted records (P, C) | fallbacks | entry resets | lead calls without a record | records not joined | failed attempts | non-lead calls | runs with tool time clipped at 0 |
|---|---|---|---|---|---|---|---|---|
| baseline (full recomputation) | 0 (0, 0) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| prefix_aa | 0 (0, 0) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| shift | 0 (0, 0) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| shift+EPIC | 0 (0, 0) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| shift+EPICa1 | 0 (0, 0) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| shift+EPICa4 | 0 (0, 0) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| shift+EPIC+recompute@JSD | 0 (0, 0) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

#### Validity gates (SWE-bench Verified, epic/pilot_gates.py)

| replicate | trials | E-0 | preemptions | fallbacks+resets / post-snip | records | ranks | decisions (recompute rate) | failed decisions | pass |
|---|---|---|---|---|---|---|---|---|---|
| e4r0 | 210 | 99.4% | 0.0 | 0.00% | ok | ok | 930 (4.2%) | 0 | all |
| e4r1 | 210 | 99.5% | 0.0 | 0.00% | ok | ok | 1154 (5.5%) | 0 | all |
| e4r2 | 210 | 99.7% | 0.0 | 0.00% | ok | ok | 1305 (6.6%) | 0 | all |

