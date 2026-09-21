## Metric 1 — byte-level recall, measured on the baseline arm

For every successor that actually read something, the share of ITS read bytes that each policy would already have held, and the share of its read-only rounds that become removable.

| policy | successors | byte recall | round recall | precision (used/sent) | sent KB median |
|---|---|---|---|---|---|
| direct predecessors (`dag` arm) | 15 | 24.2% | 15/63 (23.8%) | 29.9% | 23.8 |
| transitive closure (`ancestors` arm) | 15 | 39.2% | 18/63 (28.6%) | 30.8% | 53.6 |
| every earlier task (upper reference) | 15 | 48.2% | 28/63 (44.4%) | 27.1% | 60.1 |
| oracle (ceiling) | 15 | 100.0% | 61/63 (96.8%) | 100.0% | 35.6 |

## Stage 1 — scripted successor agent (one agent, real tools, no team)

| arm | n | rounds | censored | read_file | locate | wall s | model s | tool s | prompt tok | injected tok | **inherited share of opening prompt** | inherited share of all prompt tokens | accuracy | of which GRAND half | DIRECT half |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| none (baseline) | 32 | 8.00 (sd 2.27) | 1 | 3.31 | 8.12 | 68.2 | 68.0 | 0.17 | 25183 | 0 | **0.0%** | 0.0% | 88% | 84% | 91% |
| dag (direct preds) | 32 | 6.47 (sd 2.64) | 0 | 1.31 | 6.00 | 42.3 | 42.2 | 0.10 | 10522 | 349 | **73.1%** | 16.4% | 93% | 86% | 100% |
| ancestors (closure) | 32 | 1.50 (sd 2.14) | 0 | 0.06 | 0.47 | 12.5 | 12.5 | 0.01 | 828 | 728 | **85.0%** | 55.9% | 99% | 98% | 100% |

### Contrasts against the baseline arm

| arm | Δ rounds | 95% CI | Δ wall s | 95% CI | Δ accuracy | Δ GRAND half | Δ DIRECT half |
|---|---|---|---|---|---|---|---|
| dag (direct preds) | -1.53 | [-2.75, -0.34] | -25.9 | [-40.5, -14.2] | +5 pts | +2 pts | +9 pts |
| ancestors (closure) | -6.50 | [-7.50, -5.34] | -55.7 | [-70.0, -43.9] | +12 pts | +14 pts | +9 pts |

### The decisive contrast: ancestors vs dag (is reaching further worth it?)

| measure | dag | ancestors | difference | 95% CI |
|---|---|---|---|---|
| rounds | 6.47 | 1.50 | -4.97 | [-6.06, -3.72] |
| wall s | 42.3 | 12.5 | -29.73 | [-36.96, -21.76] |
| accuracy (all) | 93% | 99% | +6 pts | [+2, +10] |
| accuracy GRAND half | 86% | 98% | +12 pts | [+5, +20] |
| accuracy DIRECT half | 100% | 100% | +0 pts | [+0, +0] |

### Per task (rounds / accuracy)

| arm | T1 | T2 | T3 | T4 |
|---|---|---|---|---|
| none (baseline) | 8.0 / 94% | 7.9 / 84% | 8.1 / 72% | 8.0 / 100% |
| dag (direct preds) | 6.4 / 97% | 6.4 / 100% | 7.9 / 75% | 5.2 / 100% |
| ancestors (closure) | 1.0 / 100% | 1.0 / 100% | 2.9 / 97% | 1.1 / 100% |

Re-fetch rate despite being handed the content: ancestors (closure) 3%, dag (direct preds) 75%. A round the agent insists on spending is not one a cache can remove.

## Stage 2 — the real s15 team harness (lead + 3 teammates), successor tasks only

| arm | successors | cross-owner | rounds | reads | span s | model s | tool s | other s | prompt tok | injected tok | **inherited share of all prompt tokens** | completed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| none (baseline) | 15 | 14 | 7.87 | 9.47 | 149.0 | 139.4 | 0.11 | 9.5 | 27251 | 0 | **0.0%** | 100% |
| dag (direct preds) | 15 | 13 | 8.27 | 10.00 | 101.3 | 101.1 | 0.10 | 0.1 | 45218 | 5119 | **26.1%** | 100% |
| ancestors (closure) | 15 | 15 | 6.53 | 6.53 | 125.5 | 121.6 | 0.06 | 3.8 | 32914 | 5093 | **32.5%** | 93% |

### Contrasts against baseline, with the latency priced

| arm | Δ rounds | 95% CI | Δ span s | Δ model s | rounds×3.72 s | injected prefill s | net s |
|---|---|---|---|---|---|---|---|
| dag (direct preds) | +0.40 | [-3.00, +3.87] | -47.7 | -38.3 | -1.5 | 0.17 | -1.7 |
| ancestors (closure) | -1.33 | [-4.80, +2.40] | -23.6 | -17.8 | +5.0 | 0.17 | +4.8 |

### Accuracy of the graded successor report, and run wall

| arm | runs | gradeable | accuracy | edges | executed | run wall s | successor unfinished |
|---|---|---|---|---|---|---|---|
| none (baseline) | 6 | 6 | 100% | 3.0 | 3.0 | 626 | 0 |
| dag (direct preds) | 6 | 6 | 100% | 3.0 | 3.0 | 613 | 0 |
| ancestors (closure) | 6 | 5 | 100% | 3.0 | 3.0 | 561 | 1 |
