# E-C: time to first token per arm on one H100 (shape-equivalent requests)

24 lead calls; median of 3 timed runs each; CUDA graphs, GPU store.

| arm | tokens computed (mean) | TTFT median (ms) | TTFT mean (ms) | share of recompute (median) |
|---|---|---|---|---|
| recompute | 8,279 | 1,044 | 972 | 100% |
| prefix | 4,369 | 411 | 549 | 58% |
| shift | 941 | 58 | 162 | 8% |
| epic32 | 1,003 | 59 | 167 | 9% |
| epic64 | 1,065 | 61 | 173 | 9% |
| epic128 | 1,187 | 72 | 187 | 11% |

**TTFT model** (fit on 2/3 of the calls, validated on the rest):

| family | b0 (ms) | b1 (ms/token) | b2 (ms/token/1k ctx) | b3 (ms/loaded row) | held-out MdAPE |
|---|---|---|---|---|---|
| compute | 33.2 | 0.0927 | 0.00229 | 0.00000 | 2.4% |
| reuse | 33.5 | 0.0643 | 0.00553 | 0.00252 | 4.9% |

**TPOT** (ms per output token):

| context | 1 seq | 2 seqs |
|---|---|---|
| 2,000 | 27.8 | 26.7 |
| 6,000 | 26.8 | 27.6 |
| 10,000 | 27.3 | 28.5 |
| 16,000 | 27.9 | 29.3 |
| 24,000 | 28.7 | 92.4 |
| 30,000 | 29.2 | 123.1 |
