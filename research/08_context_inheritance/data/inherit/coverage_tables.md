## F1 — rounds against number of documents supplied (N=4)

| arm | n | rounds | sd | read_file | locate | wall s | injected tok | accuracy | facts whose doc WAS supplied | facts whose doc was MISSING |
|---|---|---|---|---|---|---|---|---|---|---|
| k=0 (nothing) | 30 | 8.00 | 2.41 | 3.13 | 10.17 | 74.3 | 0 | 99% | - | 99% |
| k=1 | 30 | 8.00 | 2.70 | 1.97 | 10.03 | 58.8 | 604 | 97% | 97% | 97% |
| k=2 | 30 | 8.53 | 3.03 | 1.90 | 8.73 | 54.3 | 1232 | 96% | 97% | 95% |
| k=3 | 30 | 7.53 | 3.47 | 2.10 | 5.63 | 46.5 | 1798 | 93% | 97% | 83% |
| k=4 (complete) | 30 | 1.83 | 1.78 | 0.10 | 0.90 | 13.1 | 2340 | 100% | 100% | - |
| k=4 but each truncated ~50% | 30 | 2.13 | 2.87 | 0.13 | 1.23 | 16.0 | 1270 | 100% | 100% | - |

### Step or slope?

Fit `rounds = c + a x 1[anything missing] + b x (documents missing)` over the breadth arms:

| c (floor) | a (cost of anything missing) | b (cost per missing document) | r2 |
|---|---|---|---|
| 1.83 | **5.97** | **0.09** | 0.45 |

Ratio a/b = **68.8**. Reading: a STEP — partial coverage buys almost nothing, so a retention budget should be concentrated on covering some successors completely.

### Pre-registered convexity test

- drop from k=3 to k=4 (the last document): **5.70 rounds**
- mean drop per document over k=0 to k=3: **0.16 rounds**
- ratio: **36.6x**
- k4 vs k3 difference -5.70 rounds, 95% CI [-7.07, -4.30]

The prediction was that the last document is worth more than the average of the earlier ones. A ratio near 1 falsifies the step story and flips the advice to spreading the budget.

### Is a partially present document present?

| k=4 complete | k=4 truncated ~50% | k=3 (one document absent) |
|---|---|---|
| 1.83 | **2.13** | 7.53 |

Truncated-but-contiguous lands nearer **complete**. If it behaves like absent, the step is over completeness of each span, not merely over document count, and an engine must keep whole spans rather than trim them.
