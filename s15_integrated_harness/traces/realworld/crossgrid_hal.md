## E2-R / E3-R — budget x eviction policy on the cross-task population

3 groups, 407 successor tasks. Arm: all earlier tasks in the group, cut down to the budget by the policy.


### Removable rounds (share of the successor's reading rounds that disappear)

| policy | 8 KB | 16 KB | 32 KB | 64 KB | unlimited |
|---|---|---|---|---|---|
| LRU (least-recently-used) | 15.8% | 19.4% | 22.8% | 26.1% | 38.2% |
| FIFO / recency truncation | 14.7% | 19.0% | 22.2% | 25.0% | 38.2% |
| LFU (least-frequently-used) | 19.2% | 20.7% | 23.1% | 28.4% | 38.2% |
| GDSF (greedy-dual size-frequency) | 20.8% | 24.4% | 26.3% | 28.3% | 38.2% |
| sliding window (last N rounds, whole) | 15.6% | 19.3% | 22.8% | 26.1% | 38.2% |
| SIEVE | 18.1% | 22.3% | 25.5% | 29.3% | 38.2% |
| S3-FIFO | 10.7% | 17.3% | 22.7% | 25.9% | 38.2% |
| random eviction (control) | 13.6% | 16.2% | 19.2% | 22.6% | 38.2% |
| graph-ordered (preds → closure → rest) | 21.9% | 23.4% | 25.8% | 26.1% | 38.2% |
| largest spans first | 6.0% | 4.1% | 3.1% | 4.9% | 38.2% |
| smallest spans first | 13.1% | 16.7% | 21.2% | 28.8% | 38.2% |
| keep costliest-to-recover | 10.0% | 12.0% | 14.7% | 20.5% | 38.2% |
| oracle by byte density [CEILING] | 38.0% | 38.2% | 38.2% | 38.2% | 38.2% |
| Belady / MIN [CEILING] | 38.0% | 38.2% | 38.2% | 38.2% | 38.2% |
| cheapest whole rounds first [CEILING] | 11.1% | 12.3% | 12.9% | 19.5% | 38.2% |
| random selection (control) | 9.6% | 10.4% | 13.6% | 19.6% | 38.2% |

### Byte recall

| policy | 8 KB | 16 KB | 32 KB | 64 KB | unlimited |
|---|---|---|---|---|---|
| LRU (least-recently-used) | 11.8% | 12.6% | 14.8% | 15.1% | 27.6% |
| FIFO / recency truncation | 11.6% | 12.8% | 15.0% | 15.0% | 27.6% |
| LFU (least-frequently-used) | 9.4% | 10.6% | 12.8% | 17.0% | 27.6% |
| GDSF (greedy-dual size-frequency) | 10.3% | 13.2% | 15.1% | 16.4% | 27.6% |
| sliding window (last N rounds, whole) | 11.5% | 12.6% | 14.8% | 15.1% | 27.6% |
| SIEVE | 10.4% | 11.8% | 14.6% | 17.5% | 27.6% |
| S3-FIFO | 1.9% | 6.1% | 10.1% | 13.4% | 27.6% |
| random eviction (control) | 9.9% | 11.3% | 13.0% | 14.6% | 27.6% |
| graph-ordered (preds → closure → rest) | 12.0% | 12.8% | 14.9% | 15.1% | 27.6% |
| largest spans first | 2.1% | 0.5% | 4.5% | 7.5% | 27.6% |
| smallest spans first | 1.8% | 3.7% | 7.0% | 12.9% | 27.6% |
| keep costliest-to-recover | 0.6% | 1.8% | 4.0% | 9.7% | 27.6% |
| oracle by byte density [CEILING] | 26.0% | 27.6% | 27.6% | 27.6% | 27.6% |
| Belady / MIN [CEILING] | 26.0% | 27.6% | 27.6% | 27.6% | 27.6% |
| cheapest whole rounds first [CEILING] | 1.3% | 1.5% | 2.0% | 6.8% | 27.6% |
| random selection (control) | 1.7% | 2.6% | 4.7% | 10.7% | 27.6% |

### Precision (used / sent)

| policy | 8 KB | 16 KB | 32 KB | 64 KB | unlimited |
|---|---|---|---|---|---|
| LRU (least-recently-used) | 6.4% | 3.2% | 2.0% | 1.0% | 0.5% |
| FIFO / recency truncation | 6.3% | 3.3% | 2.0% | 1.0% | 0.5% |
| LFU (least-frequently-used) | 5.1% | 2.7% | 1.7% | 1.1% | 0.5% |
| GDSF (greedy-dual size-frequency) | 5.6% | 3.4% | 2.0% | 1.1% | 0.5% |
| sliding window (last N rounds, whole) | 6.4% | 3.3% | 2.0% | 1.0% | 0.5% |
| SIEVE | 5.7% | 3.1% | 1.9% | 1.1% | 0.5% |
| S3-FIFO | 2.7% | 2.9% | 2.5% | 1.9% | 0.5% |
| random eviction (control) | 5.4% | 2.9% | 1.7% | 0.9% | 0.5% |
| graph-ordered (preds → closure → rest) | 5.7% | 3.1% | 1.8% | 0.9% | 0.5% |
| largest spans first | 1.0% | 0.1% | 0.5% | 0.5% | 0.5% |
| smallest spans first | 0.9% | 0.9% | 0.9% | 0.8% | 0.5% |
| keep costliest-to-recover | 0.3% | 0.4% | 0.5% | 0.6% | 0.5% |
| oracle by byte density [CEILING] | 12.5% | 6.6% | 3.3% | 1.7% | 0.5% |
| Belady / MIN [CEILING] | 12.5% | 6.6% | 3.3% | 1.7% | 0.5% |
| cheapest whole rounds first [CEILING] | 0.6% | 0.4% | 0.2% | 0.4% | 0.5% |
| random selection (control) | 0.8% | 0.6% | 0.6% | 0.7% | 0.5% |

### Median KB handed over

| policy | 8 KB | 16 KB | 32 KB | 64 KB | unlimited |
|---|---|---|---|---|---|
| LRU (least-recently-used) | 7.4 | 15.4 | 31.2 | 63.2 | 198.7 |
| FIFO / recency truncation | 7.4 | 15.3 | 31.2 | 63.2 | 198.7 |
| LFU (least-frequently-used) | 7.3 | 15.3 | 31.2 | 62.8 | 198.7 |
| GDSF (greedy-dual size-frequency) | 7.3 | 15.3 | 30.9 | 62.9 | 198.7 |
| sliding window (last N rounds, whole) | 7.4 | 15.3 | 31.2 | 63.2 | 198.7 |
| SIEVE | 7.4 | 15.3 | 31.2 | 63.1 | 198.7 |
| S3-FIFO | 2.5 | 8.3 | 12.8 | 28.2 | 198.7 |
| random eviction (control) | 7.4 | 15.3 | 31.3 | 62.8 | 198.7 |
| graph-ordered (preds → closure → rest) | 8.0 | 16.0 | 32.0 | 64.0 | 198.7 |
| largest spans first | 8.0 | 16.0 | 32.0 | 64.0 | 198.7 |
| smallest spans first | 7.6 | 15.5 | 31.5 | 63.4 | 198.7 |
| keep costliest-to-recover | 8.0 | 16.0 | 32.0 | 64.0 | 198.7 |
| oracle by byte density [CEILING] | 8.0 | 16.0 | 32.0 | 64.0 | 198.7 |
| Belady / MIN [CEILING] | 8.0 | 16.0 | 32.0 | 64.0 | 198.7 |
| cheapest whole rounds first [CEILING] | 7.9 | 16.0 | 32.0 | 64.0 | 198.7 |
| random selection (control) | 8.0 | 16.0 | 32.0 | 64.0 | 198.7 |
