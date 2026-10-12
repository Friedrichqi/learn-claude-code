# E-C: time to first token per arm on one H100 (shape-equivalent requests)

600 lead calls; median of 3 timed runs each; CUDA graphs, GPU store.

| arm | tokens computed (mean) | TTFT median (ms) | TTFT mean (ms) | share of recompute (median) |
|---|---|---|---|---|
| recompute | 8,126 | 904 | 945 | 100% |
| prefix | 4,571 | 494 | 573 | 62% |
| shift | 590 | 65 | 119 | 9% |
| epic32 | 650 | 70 | 124 | 9% |
| epic64 | 708 | 77 | 130 | 10% |
| epic128 | 826 | 86 | 143 | 11% |

**TTFT model** (fit on 2/3 of the calls, validated on the rest):

| family | b0 (ms) | b1 (ms/token) | b2 (ms/token/1k ctx) | b3 (ms/loaded row) | held-out MdAPE |
|---|---|---|---|---|---|
| compute | 41.2 | 0.0875 | 0.00268 | 0.00000 | 1.8% |
| reuse | 31.7 | 0.0842 | 0.00353 | 0.00355 | 3.8% |

**TPOT** (ms per output token):

| context | 1 seq | 2 seqs |
|---|---|---|
| 2,000 | 26.3 | 26.5 |
| 6,000 | 26.6 | 27.2 |
| 10,000 | 27.1 | 28.1 |
| 16,000 | 27.6 | 29.0 |
| 24,000 | 28.4 | nan |
| 30,000 | 28.9 | nan |
