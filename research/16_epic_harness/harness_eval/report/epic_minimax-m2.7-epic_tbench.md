## EPIC round: minimax-m2.7-epic on tbench (phase e2)

Arms: epic128, epic128a1, prefix, prefix_aa, shift. Control: mean of prefix and prefix_aa per block. Blocks: 30. Infrastructure failures are excluded and rerun under the same block key.

### Exploratory: resolve rate, arm minus control (t-interval over task means; intervals only, no verdicts)

| arm | tasks | blocks | difference | 90% two-sided [lo, hi] | non-inferior |
|---|---|---|---|---|---|
| epic128a1 | 30 | 30 | +0.050 | [-0.104, +0.204] | — |
| epic128 | 30 | 30 | +0.150 | [+0.002, +0.298] | — |
| shift | 30 | 30 | +0.017 | [-0.127, +0.161] | — |
| prefix_aa vs prefix (A/A) | 30 | 30 | -0.300 | [-0.485, -0.115] | noise floor |

Sensitivity: the same, without the 5 blocks in which an EPIC arm's call fell back to prefix (host pool exhausted):

| arm | tasks | blocks | difference | 90% two-sided [lo, hi] |
|---|---|---|---|---|
| epic128a1 | 25 | 25 | +0.040 | [-0.131, +0.211] |
| epic128 | 25 | 25 | +0.120 | [-0.046, +0.286] |
| shift | 25 | 25 | +0.040 | [-0.116, +0.196] |

Sensitivity: the same, without the 15 blocks where any arm hit the agent timeout (the timeout binds earlier for the EPIC arms, whose calls are slower on the emulating server):

| arm | tasks | blocks | difference | 90% two-sided [lo, hi] |
|---|---|---|---|---|
| epic128a1 | 15 | 15 | -0.167 | [-0.388, +0.054] |
| epic128 | 15 | 15 | +0.033 | [-0.200, +0.267] |
| shift | 15 | 15 | -0.100 | [-0.295, +0.095] |

### Secondary: tasks whose baseline runs snipped (17 tasks)

| arm | tasks | blocks | difference | 90% [lo, hi] |
|---|---|---|---|---|
| epic128a1 | 17 | 17 | +0.206 | [+0.021, +0.391] |
| epic128 | 17 | 17 | +0.206 | [+0.021, +0.391] |
| shift | 17 | 17 | +0.088 | [-0.114, +0.290] |

### Per arm

| arm | trials | pass@1 | pass^3 | consistent | at budget | agent timeout | lead calls | out tok |
|---|---|---|---|---|---|---|---|---|
| epic128 | 30 | 0.633 | None | None | 3.3% | 6 | 31.2 | 14,594 |
| epic128a1 | 30 | 0.533 | None | None | 3.3% | 5 | 29.7 | 16,797 |
| prefix | 30 | 0.633 | None | None | 0.0% | 6 | 29.8 | 15,030 |
| prefix_aa | 30 | 0.333 | None | None | 3.3% | 13 | 34.7 | 19,739 |
| shift | 30 | 0.500 | None | None | 3.3% | 6 | 32.8 | 21,472 |

### Against the baseline profile (`minimax-m2.7`, same tasks; another server, template and day)

| run | trials | pass@1 | minus baseline, 90% [lo, hi] (task-paired) | lead calls | at budget | agent timeout |
|---|---|---|---|---|---|---|
| baseline | 90 | 0.478 | — | 38.1 | 5.6% | 18 |
| epic128 | 30 | 0.633 | +0.156 [+0.038, +0.273] | 31.2 | 3.3% | 6 |
| epic128a1 | 30 | 0.533 | +0.056 [-0.057, +0.168] | 29.7 | 3.3% | 5 |
| prefix | 30 | 0.633 | +0.156 [+0.013, +0.299] | 29.8 | 0.0% | 6 |
| prefix_aa | 30 | 0.333 | -0.144 [-0.313, +0.024] | 34.7 | 3.3% | 13 |
| shift | 30 | 0.500 | +0.022 [-0.105, +0.149] | 32.8 | 3.3% | 6 |

### Server side (EPIC_LOG)

| arm | lead calls logged | computed / prompt | post-snip calls | fallbacks | resets | errors | probes | first-token KL | reply KL | reply top-1 | D^K reused by age (0,1,2-3,4-7,8-15,16+) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| epic128 | 937 | 7.7% | 449 | 10 | 7 | 13 | 78 | 0.0504 | 0.0215 | 97.45% | 0.026 / 0.082 / 0.105 / 0.099 / 0.125 / 0.211 |
| epic128a1 | 895 | 27.5% | 388 | 19 | 21 | 19 | 53 | 0.0195 | 0.0131 | 97.64% | 0.013 / 0.074 / — / — / — / — |
| prefix | 897 | 44.4% | 0 | 0 | 0 | 2 | 0 | — | — | — | — / — / — / — / — / — |
| prefix_aa | 1048 | 50.2% | 0 | 0 | 0 | 3 | 0 | — | — | — | — / — / — / — / — / — |
| shift | 988 | 10.2% | 487 | 11 | 27 | 17 | 58 | 0.1037 | 0.0448 | 96.52% | 0.022 / 0.053 / 0.071 / 0.089 / 0.111 / 0.218 |

### First divergence from `prefix` in the same block (lead-call index; −1 = identical run)

| arm | blocks | identical | median first divergent call |
|---|---|---|---|
| epic128 | 30 | 0 | 0.0 |
| epic128a1 | 30 | 0 | 0.0 |
| prefix_aa | 30 | 0 | 0.0 |
| shift | 30 | 0 | 0.0 |

(Setup and deviations of phase e2: see the e2 notes in the other benchmark's part.)

