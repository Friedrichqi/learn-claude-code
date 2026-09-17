## Table A. Runs

| run | model | tasks | edges | executed | owners | reads | attributed | t-rounds | wall s | status |
|---|---|---|---|---|---|---|---|---|---|---|
| W1-L2-r1 | glm-5.3-flash | 6 | 2 | 0 | 3 | 9 | 9 | 39 | 607.1 | timeout |
| W2-L2-r1 | glm-5.3-flash | 5 | 3 | 3 | 3 | 34 | 34 | 55 | 601.3 | timeout |
| W3-L2-r1 | glm-5.3-flash | 4 | 2 | 2 | 2 | 4 | 4 | 17 | 548.8 | completed |
| W4-L2-r1 | glm-5.3-flash | 6 | 2 | 2 | 3 | 21 | 21 | 39 | 735.6 | completed |
| W5-L2-r1 | glm-5.3-flash | 5 | 3 | 3 | 3 | 35 | 35 | 74 | 701.9 | completed |
| W2-L2-r2 | glm-5.3-flash | 5 | 3 | 3 | 3 | 25 | 25 | 58 | 622.1 | completed |
| W5-L2-r2 | glm-5.3-flash | 5 | 3 | 3 | 3 | 33 | 33 | 55 | 671.8 | completed |
| W1-L2-r2 | glm-5.3-flash | 6 | 2 | 1 | 3 | 75 | 75 | 71 | 1004.4 | timeout |
| W4-L2-r2 | glm-5.3-flash | 6 | 2 | 2 | 3 | 33 | 33 | 65 | 807.7 | completed |

## Table V. Validity (read this before any p-value)

| run | non-adj sd | non-adj base | exec yield | same-owner edges | resolvable bytes | unattributed | rho(edge,desc) | rho(edge,gap) | edges w/ control |
|---|---|---|---|---|---|---|---|---|---|
| W1-L2-r1 | 0.4714 | 0.333 | 0.0 | None | 0.99 | 0.0 | 0.0 | 0.0 | 0 |
| W2-L2-r1 | 0.1759 | 0.928 | 1.0 | 0.33 | 1.0 | 0.0 | -0.57 | 0.72 | 3 |
| W3-L2-r1 | 0.4714 | 0.333 | 1.0 | 0.0 | 1.0 | 0.0 | 0.41 | -0.41 | 2 |
| W4-L2-r1 | 0.4781 | 0.398 | 1.0 | 0.0 | 0.986 | 0.0 | -0.23 | 0.32 | 2 |
| W5-L2-r1 | 0.3499 | 0.143 | 1.0 | 0.33 | 0.99 | 0.0 | -0.11 | 0.65 | 3 |
| W2-L2-r2 | 0.461 | 0.469 | 1.0 | 0.33 | 1.0 | 0.0 | -0.65 | 0.65 | 3 |
| W5-L2-r2 | 0.1397 | 0.057 | 1.0 | 0.33 | 0.952 | 0.0 | 0.04 | 0.8 | 3 |
| W1-L2-r2 | 0.4714 | 0.333 | 0.5 | 0.0 | 1.0 | 0.0 | 0.29 | 0.06 | 1 |
| W4-L2-r2 | 0.4513 | 0.326 | 1.0 | 0.5 | 1.0 | 0.0 | -0.23 | 0.05 | 2 |

V1 sd = 0 means every non-adjacent pair in the run has the identical value, so the workload -- not the graph -- fixed the answer and the run carries no information. V2 outside [0.05, 0.95] is a ceiling/floor. V4 near 1.0 means the successors were the same agent, which already holds the bytes for free. V5 below 0.70 means bash provenance is too thin for `range` and `cdc256` should carry the headline.

## Table S. Successor tasks: what an agent that inherits a dependency does

| successors | claimed | same agent as a predecessor | reads issued | read nothing at all |
|---|---|---|---|---|
| 14 | 13 | 6 | 62 | 2 |

| group | n | mean reads | mean bytes read | mean predecessor bytes | byte coverage | removable rounds |
|---|---|---|---|---|---|---|
| same agent as predecessor | 6 | 3.7 | 9,477 | 34,018 | 44.7% | 5/10 (50.0%) |
| different agent | 7 | 5.7 | 11,491 | 21,984 | 29.9% | 6/12 (50.0%) |

A successor that reads nothing has already got what it needed -- it is the same agent, and its own context still holds the predecessor's bytes. Those rows are the free case; only the 'different agent' rows are what a KV transfer would have to buy.

## Table B. Task pairs by relation (pooled)

| relation | pairs | same owner | any shared path | path Jaccard | byte coverage | removable rounds | desc names pred file |
|---|---|---|---|---|---|---|---|
| edge | 19 | 26.3% | 57.9% | 0.47 | 31.8% | 12/38 (31.6%) | 36.8% |
| ancestor | 1 | 100.0% | 0.0% | 0.00 | 0.0% | 0/1 (0.0%) | 0.0% |
| sibling | 0 | - | - | - | - | - | - |
| unrelated | 72 | 23.6% | 51.4% | 0.47 | 32.9% | 78/249 (31.3%) | 51.4% |

## Table C. Matched contrast: each edge against a control with the SAME successor

| group | measure | edges matched | mean delta | 95% CI | sign-flip p | runs positive |
|---|---|---|---|---|---|---|
| aligned boards (W1, W2, W5) | byte coverage | 13 | +0.461 | [+0.273, +0.676] | 0.0614 | 5/5 |
| aligned boards (W1, W2, W5) | removable-round share | 13 | +0.372 | [+0.122, +0.682] | 0.1240 | 4/5 |
| board contradicts the text (W4 placebo) | byte coverage | 4 | -1.000 | [-1.000, -1.000] | 0.4969 | 0/2 |
| board contradicts the text (W4 placebo) | removable-round share | 4 | -1.000 | [-1.000, -1.000] | 0.4969 | 0/2 |
| edges span disjoint corpora (W3 control) | byte coverage | 2 | +0.000 | [+0.000, +0.000] | 1.0000 | 0/1 |
| edges span disjoint corpora (W3 control) | removable-round share | 2 | +0.000 | [+0.000, +0.000] | 1.0000 | 0/1 |
| all workloads pooled | byte coverage | 19 | +0.105 | [-0.366, +0.471] | 0.6753 | 5/8 |
| all workloads pooled | removable-round share | 19 | +0.044 | [-0.412, +0.421] | 0.9084 | 4/8 |

### Table C2. Per-workload edge vs unrelated (raw, not matched)

| workload | edges | edge byte coverage | unrelated byte coverage | edge removable rounds | unrelated removable rounds |
|---|---|---|---|---|---|
| W1 | 1 | 100.0% | 31.8% | 100.0% | 27.5% |
| W2 | 6 | 77.1% | 69.7% | 55.6% | 66.7% |
| W3 | 2 | 0.0% | 44.6% | 0.0% | 33.3% |
| W4 | 4 | 0.0% | 26.7% | 0.0% | 32.1% |
| W5 | 6 | 26.9% | 6.2% | 6.7% | 5.1% |

A control is a non-adjacent, non-ancestor task that finished before the same successor started. Holding the successor fixed removes its read volume, its corpus and its position in the run from the comparison.

## Table D. Prefetch policies, over tasks that HAVE a predecessor

| policy | tasks | byte recall | round recall | precision (used/sent) | sent KB median | tokens per round removed | gap s median |
|---|---|---|---|---|---|---|---|
| direct_pred | 11 | 36.0% | 11/22 (50.0%) | 16.3% | 26.2 | 6,911 | 2 |
| text_pred | 11 | 46.1% | 9/22 (40.9%) | 34.5% | 12.6 | 5,096 | 1 |
| ancestors | 11 | 36.0% | 11/22 (50.0%) | 15.6% | 26.2 | 7,204 | 2 |
| all_earlier | 11 | 85.6% | 17/22 (77.3%) | 24.7% | 49.3 | 7,009 | -15 |
| same_owner | 11 | 14.6% | 3/22 (13.6%) | 9.6% | 20.0 | 17,301 | 65 |
| lead | 11 | 0.0% | 0/22 (0.0%) | - | 0.0 | - | - |
| random | 11 | 26.8% | 3/22 (13.6%) | 21.4% | 12.6 | 14,342 | 65 |
| oracle | 11 | 100.0% | 21/22 (95.5%) | 100.0% | 8.1 | 1,635 | -135 |

The same policies over EVERY reading task, predecessor or not -- the view a retention policy that does not look at the board would see:

| policy | tasks | byte recall | round recall | precision | sent KB median | tokens per round removed | gap s median |
|---|---|---|---|---|---|---|---|
| direct_pred | 45 | 6.4% | 11/152 (7.2%) | 16.3% | 0.0 | 6,911 | 2 |
| text_pred | 45 | 26.1% | 36/152 (23.7%) | 33.5% | 12.6 | 4,511 | -46 |
| ancestors | 45 | 6.4% | 11/152 (7.2%) | 15.6% | 0.0 | 7,204 | 2 |
| all_earlier | 45 | 47.6% | 69/152 (45.4%) | 30.2% | 25.3 | 4,558 | -109 |
| same_owner | 45 | 3.6% | 8/152 (5.3%) | 8.2% | 0.0 | 10,693 | 47 |
| lead | 45 | 0.0% | 0/152 (0.0%) | - | 0.0 | - | - |
| random | 45 | 21.3% | 26/152 (17.1%) | 29.2% | 12.6 | 6,006 | -49 |
| oracle | 45 | 100.0% | 139/152 (91.4%) | 100.0% | 21.6 | 1,392 | -118 |

Cross-owner successors only (the transferable case):

| policy | tasks | byte recall | round recall | precision | sent KB median | tokens per round removed | gap s median |
|---|---|---|---|---|---|---|---|
| direct_pred | 10 | 45.1% | 11/20 (55.0%) | 17.6% | 26.7 | 6,383 | 2 |
| text_pred | 9 | 46.6% | 8/20 (40.0%) | 40.1% | 12.6 | 4,515 | 1 |
| ancestors | 10 | 45.1% | 11/20 (55.0%) | 16.8% | 26.7 | 6,676 | 2 |
| all_earlier | 11 | 85.6% | 17/22 (77.3%) | 24.7% | 49.3 | 7,009 | -15 |
| random | 6 | 49.2% | 3/14 (21.4%) | 26.9% | 24.3 | 11,200 | -15 |

`direct_pred` sending nothing means the predecessor itself read nothing -- which happens exactly when the predecessor was the same agent and had the bytes already.

## Table E. What a removable round is worth

One round = 3.72 s fixed + prefill + decode; sending N bytes costs N/4 tokens x 0.033 ms of prefill.

| policy | removable rounds | saved s (rounds x fixed) | sent tokens | prefill s | net s |
|---|---|---|---|---|---|
| direct_pred | 11 | 40.9 | 76,025 | 2.51 | +38.4 |
| text_pred | 9 | 33.5 | 45,862 | 1.51 | +32.0 |
| ancestors | 11 | 40.9 | 79,242 | 2.62 | +38.3 |
| all_earlier | 17 | 63.2 | 119,151 | 3.93 | +59.3 |
| same_owner | 3 | 11.2 | 51,904 | 1.71 | +9.4 |
| lead | 0 | 0.0 | 0 | 0.00 | +0.0 |
| random | 3 | 11.2 | 43,027 | 1.42 | +9.7 |
| oracle | 21 | 78.1 | 34,326 | 1.13 | +77.0 |
