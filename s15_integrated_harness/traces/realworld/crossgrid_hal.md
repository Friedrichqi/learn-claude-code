## E2-R / E3-R — budget x eviction policy on the cross-task population

3 groups, 407 successor tasks. Arm: all earlier tasks in the group, cut down to the budget by the policy.


### Removable rounds (share of the successor's reading rounds that disappear)

| policy | 8 KB | 16 KB | 32 KB | 64 KB | unlimited |
|---|---|---|---|---|---|
| LRU (least-recently-used) | 3.0% | 3.7% | 4.8% | 7.9% | 38.2% |
| FIFO / recency truncation | 3.0% | 3.7% | 4.8% | 7.9% | 38.2% |
| LFU (least-frequently-used) | 21.9% | 28.0% | 35.6% | 36.8% | 38.2% |
| GDSF (greedy-dual size-frequency) | 17.2% | 22.7% | 29.5% | 35.1% | 38.2% |
| sliding window (last N rounds, whole) | 2.4% | 3.7% | 4.4% | 7.4% | 38.2% |
| SIEVE | 7.0% | 7.9% | 9.7% | 13.4% | 38.2% |
| S3-FIFO | 15.8% | 18.3% | 25.4% | 34.4% | 38.2% |
| random eviction (control) | 2.5% | 3.5% | 5.7% | 7.9% | 38.2% |
| graph-ordered (preds → closure → rest) | 7.8% | 9.1% | 11.3% | 13.3% | 38.2% |
| largest spans first | 6.0% | 4.1% | 3.1% | 4.9% | 38.2% |
| smallest spans first | 13.1% | 16.7% | 21.2% | 28.8% | 38.2% |
| keep costliest-to-recover | 11.1% | 13.3% | 17.1% | 23.6% | 38.2% |
| oracle by byte density [CEILING] | 38.0% | 38.2% | 38.2% | 38.2% | 38.2% |
| Belady / MIN [CEILING] | 38.0% | 38.2% | 38.2% | 38.2% | 38.2% |
| cheapest whole rounds first [CEILING] | 11.1% | 12.3% | 12.9% | 19.5% | 38.2% |
| random selection (control) | 9.6% | 10.4% | 13.6% | 19.6% | 38.2% |

### Byte recall

| policy | 8 KB | 16 KB | 32 KB | 64 KB | unlimited |
|---|---|---|---|---|---|
| LRU (least-recently-used) | 2.8% | 3.3% | 4.0% | 8.4% | 27.6% |
| FIFO / recency truncation | 2.8% | 3.3% | 4.0% | 8.4% | 27.6% |
| LFU (least-frequently-used) | 9.2% | 14.1% | 22.4% | 25.9% | 27.6% |
| GDSF (greedy-dual size-frequency) | 4.8% | 9.1% | 15.7% | 20.3% | 27.6% |
| sliding window (last N rounds, whole) | 1.9% | 3.3% | 3.7% | 7.6% | 27.6% |
| SIEVE | 3.6% | 4.2% | 6.6% | 11.4% | 27.6% |
| S3-FIFO | 5.1% | 7.7% | 14.1% | 24.6% | 27.6% |
| random eviction (control) | 2.4% | 3.0% | 4.3% | 6.7% | 27.6% |
| graph-ordered (preds → closure → rest) | 3.0% | 3.4% | 4.2% | 8.6% | 27.6% |
| largest spans first | 2.1% | 0.5% | 4.5% | 7.5% | 27.6% |
| smallest spans first | 1.8% | 3.7% | 7.0% | 12.9% | 27.6% |
| keep costliest-to-recover | 0.9% | 2.6% | 5.7% | 11.5% | 27.6% |
| oracle by byte density [CEILING] | 26.0% | 27.6% | 27.6% | 27.6% | 27.6% |
| Belady / MIN [CEILING] | 26.0% | 27.6% | 27.6% | 27.6% | 27.6% |
| cheapest whole rounds first [CEILING] | 1.3% | 1.5% | 2.0% | 6.8% | 27.6% |
| random selection (control) | 1.7% | 2.6% | 4.7% | 10.7% | 27.6% |

### Precision (used / sent)

| policy | 8 KB | 16 KB | 32 KB | 64 KB | unlimited |
|---|---|---|---|---|---|
| LRU (least-recently-used) | 1.6% | 0.9% | 0.5% | 0.5% | 0.5% |
| FIFO / recency truncation | 1.6% | 0.9% | 0.5% | 0.5% | 0.5% |
| LFU (least-frequently-used) | 4.7% | 3.5% | 2.9% | 1.6% | 0.5% |
| GDSF (greedy-dual size-frequency) | 2.5% | 2.3% | 2.0% | 1.3% | 0.5% |
| sliding window (last N rounds, whole) | 2.1% | 1.2% | 0.6% | 0.5% | 0.5% |
| SIEVE | 2.2% | 1.1% | 0.9% | 0.7% | 0.5% |
| S3-FIFO | 4.9% | 3.1% | 2.7% | 2.3% | 0.5% |
| random eviction (control) | 1.3% | 0.8% | 0.5% | 0.4% | 0.5% |
| graph-ordered (preds → closure → rest) | 1.4% | 0.8% | 0.5% | 0.5% | 0.5% |
| largest spans first | 1.0% | 0.1% | 0.5% | 0.5% | 0.5% |
| smallest spans first | 0.9% | 0.9% | 0.9% | 0.8% | 0.5% |
| keep costliest-to-recover | 0.4% | 0.6% | 0.7% | 0.7% | 0.5% |
| oracle by byte density [CEILING] | 12.5% | 6.6% | 3.3% | 1.7% | 0.5% |
| Belady / MIN [CEILING] | 12.5% | 6.6% | 3.3% | 1.7% | 0.5% |
| cheapest whole rounds first [CEILING] | 0.6% | 0.4% | 0.2% | 0.4% | 0.5% |
| random selection (control) | 0.8% | 0.6% | 0.6% | 0.7% | 0.5% |

### Median KB handed over

| policy | 8 KB | 16 KB | 32 KB | 64 KB | unlimited |
|---|---|---|---|---|---|
| LRU (least-recently-used) | 7.0 | 14.6 | 29.5 | 62.1 | 198.7 |
| FIFO / recency truncation | 7.0 | 14.6 | 29.5 | 62.1 | 198.7 |
| LFU (least-frequently-used) | 7.9 | 15.9 | 31.8 | 63.6 | 198.7 |
| GDSF (greedy-dual size-frequency) | 7.3 | 15.2 | 30.9 | 62.0 | 198.7 |
| sliding window (last N rounds, whole) | 2.5 | 11.2 | 24.7 | 58.3 | 198.7 |
| SIEVE | 6.7 | 15.2 | 29.4 | 62.5 | 198.7 |
| S3-FIFO | 1.8 | 11.7 | 25.8 | 58.0 | 198.7 |
| random eviction (control) | 7.2 | 15.0 | 30.5 | 62.7 | 198.7 |
| graph-ordered (preds → closure → rest) | 8.0 | 16.0 | 32.0 | 63.9 | 198.7 |
| largest spans first | 8.0 | 16.0 | 32.0 | 64.0 | 198.7 |
| smallest spans first | 7.6 | 15.5 | 31.5 | 63.4 | 198.7 |
| keep costliest-to-recover | 8.0 | 16.0 | 32.0 | 63.9 | 198.7 |
| oracle by byte density [CEILING] | 8.0 | 16.0 | 32.0 | 64.0 | 198.7 |
| Belady / MIN [CEILING] | 8.0 | 16.0 | 32.0 | 64.0 | 198.7 |
| cheapest whole rounds first [CEILING] | 7.9 | 16.0 | 32.0 | 64.0 | 198.7 |
| random selection (control) | 8.0 | 16.0 | 32.0 | 64.0 | 198.7 |
