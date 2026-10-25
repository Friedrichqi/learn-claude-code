### e4 on Terminal-Bench: secondary tables

#### Decisions of the recompute arm (Terminal-Bench)

| arm | decisions / run | mean JSD (= recompute probability) | recompute rate | recomputes / run | decision overhead tokens / run (share of computed) | decision time | noise floor (JSD, n) | failed decisions |
|---|---|---|---|---|---|---|---|---|
| shift+EPIC+recompute@JSD | 22.044 | 0.0733 | 6.96% | 1.533 | 9,111 (15.0%) | 9.30 s | 0.0527 (129) | 0 |

Descriptive, for the future TVD and EXP arms: the same decisions' other metrics (their mean is the recompute rate each would have produced on these calls).

| arm | jsd | tvd | exp | kl | jsd20 | tvd20 | exp20 | kl20 |
|---|---|---|---|---|---|---|---|---|
| shift+EPIC+recompute@JSD | 0.0733 | 0.0614 | 0.0214 | 0.0222 | 0.0724 | 0.0609 | 0.0208 | 0.0216 |

#### Decision metrics against the real error (99 probed decision calls)

Spearman ρ between each decision metric (original vs after-EPIC) and the shadow probe's first-token KL of the same call (fresh recompute vs after-EPIC), with an approximate 90 % interval (Fisher z).

| metric | n | ρ | 90% [lo, hi] |
|---|---|---|---|
| jsd | 99 | +0.442 | [+0.298, +0.567] |
| tvd | 99 | +0.462 | [+0.320, +0.583] |
| exp | 99 | +0.435 | [+0.289, +0.561] |
| kl | 99 | +0.435 | [+0.289, +0.561] |
| jsd20 | 99 | +0.436 | [+0.291, +0.561] |
| tvd20 | 99 | +0.459 | [+0.317, +0.581] |
| exp20 | 99 | +0.432 | [+0.286, +0.558] |
| kl20 | 99 | +0.432 | [+0.286, +0.558] |

| JSD quintile | n | mean JSD | mean first-token KL (fresh vs arm) |
|---|---|---|---|
| 1 | 19 | 0.0197 | 0.0223 |
| 2 | 20 | 0.0433 | 0.0554 |
| 3 | 20 | 0.0584 | 0.0622 |
| 4 | 20 | 0.0827 | 0.0827 |
| 5 | 20 | 0.1351 | 0.0837 |

#### Sensitivity (Terminal-Bench): resolve difference without blocks where any arm hit the agent timeout (31 blocks left out)

| arm | tasks | blocks | difference | 90% CI |
|---|---|---|---|---|
| prefix_aa (vs baseline, A/A) | 22 | 59 | -0.023 | [-0.094, +0.049] |
| shift | 22 | 59 | +0.027 | [-0.044, +0.097] |
| shift+EPIC | 22 | 59 | +0.057 | [-0.051, +0.165] |
| shift+EPICa1 | 22 | 59 | -0.087 | [-0.213, +0.039] |
| shift+EPICa4 | 22 | 59 | -0.004 | [-0.106, +0.099] |
| shift+EPIC+recompute@JSD | 22 | 59 | -0.049 | [-0.160, +0.062] |

#### Call accounting (Terminal-Bench)

| arm | aborted records (P, C) | fallbacks | entry resets | lead calls without a record | records not joined | failed attempts | non-lead calls | runs with tool time clipped at 0 |
|---|---|---|---|---|---|---|---|---|
| baseline (full recomputation) | 8 (323,761, 255,745) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| prefix_aa | 9 (359,725, 266,429) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| shift | 8 (230,711, 1,610) | 0 | 9 | 0 | 0 | 0 | 2 | 0 |
| shift+EPIC | 8 (265,013, 4,633) | 0 | 8 | 0 | 0 | 1 | 1 | 0 |
| shift+EPICa1 | 7 (199,731, 90,807) | 0 | 5 | 0 | 0 | 0 | 0 | 0 |
| shift+EPICa4 | 9 (285,663, 59,255) | 0 | 2 | 0 | 0 | 0 | 0 | 0 |
| shift+EPIC+recompute@JSD | 12 (440,576, 4,378) | 0 | 6 | 0 | 0 | 0 | 0 | 0 |

#### Validity gates (Terminal-Bench, epic/pilot_gates.py)

| replicate | trials | E-0 | preemptions | fallbacks+resets / post-snip | records | ranks | decisions (recompute rate) | failed decisions | pass |
|---|---|---|---|---|---|---|---|---|---|
| e4r0 | 210 | 96.6% | 0.0 | 0.09% | ok | ok | 596 (6.7%) | 0 | E0_append_only fail |
| e4r1 | 210 | 96.5% | 0.0 | 0.06% | ok | ok | 808 (6.9%) | 0 | E0_append_only fail |
| e4r2 | 210 | 95.7% | 0.0 | 0.02% | ok | ok | 580 (7.2%) | 0 | E0_append_only fail |

