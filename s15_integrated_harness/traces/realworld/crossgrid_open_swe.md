## E2-R / E3-R — budget x eviction policy on the cross-task population

234 groups, 732 successor tasks. Arm: all earlier tasks in the group, cut down to the budget by the policy.


### Removable rounds (share of the successor's reading rounds that disappear)

| policy | 8 KB | 16 KB | 32 KB | 64 KB | unlimited |
|---|---|---|---|---|---|
| LRU (least-recently-used) | 0.0% | 0.2% | 0.5% | 1.1% | 4.3% |
| FIFO / recency truncation | 0.0% | 0.2% | 0.5% | 1.1% | 4.3% |
| LFU (least-frequently-used) | 4.0% | 4.2% | 4.3% | 4.3% | 4.3% |
| GDSF (greedy-dual size-frequency) | 3.5% | 3.8% | 4.0% | 4.1% | 4.3% |
| sliding window (last N rounds, whole) | 0.0% | 0.1% | 0.3% | 0.8% | 4.3% |
| SIEVE | 2.9% | 3.6% | 3.9% | 4.3% | 4.3% |
| S3-FIFO | 3.9% | 4.1% | 4.3% | 4.3% | 4.3% |
| random eviction (control) | 0.2% | 0.3% | 0.5% | 1.1% | 4.3% |
| graph-ordered (preds → closure → rest) | 0.7% | 0.8% | 1.1% | 1.7% | 4.3% |
| largest spans first | 0.2% | 0.2% | 0.2% | 0.6% | 4.3% |
| smallest spans first | 3.5% | 3.7% | 3.9% | 4.0% | 4.3% |
| keep costliest-to-recover | 2.3% | 2.2% | 2.4% | 2.8% | 4.3% |
| oracle by byte density [CEILING] | 4.2% | 4.3% | 4.3% | 4.3% | 4.3% |
| Belady / MIN [CEILING] | 4.2% | 4.3% | 4.3% | 4.3% | 4.3% |
| cheapest whole rounds first [CEILING] | 0.7% | 0.9% | 1.4% | 1.9% | 4.3% |
| random selection (control) | 1.0% | 1.2% | 1.7% | 2.0% | 4.3% |

### Byte recall

| policy | 8 KB | 16 KB | 32 KB | 64 KB | unlimited |
|---|---|---|---|---|---|
| LRU (least-recently-used) | 0.0% | 0.1% | 0.1% | 0.4% | 1.2% |
| FIFO / recency truncation | 0.0% | 0.1% | 0.1% | 0.4% | 1.2% |
| LFU (least-frequently-used) | 0.7% | 0.9% | 1.2% | 1.2% | 1.2% |
| GDSF (greedy-dual size-frequency) | 0.2% | 0.3% | 0.5% | 0.8% | 1.2% |
| sliding window (last N rounds, whole) | 0.0% | 0.0% | 0.1% | 0.3% | 1.2% |
| SIEVE | 0.5% | 0.7% | 1.1% | 1.2% | 1.2% |
| S3-FIFO | 0.6% | 0.9% | 1.2% | 1.2% | 1.2% |
| random eviction (control) | 0.0% | 0.0% | 0.2% | 0.5% | 1.2% |
| graph-ordered (preds → closure → rest) | 0.0% | 0.1% | 0.2% | 0.5% | 1.2% |
| largest spans first | 0.0% | 0.1% | 0.2% | 0.4% | 1.2% |
| smallest spans first | 0.2% | 0.3% | 0.4% | 0.6% | 1.2% |
| keep costliest-to-recover | 0.2% | 0.3% | 0.5% | 0.7% | 1.2% |
| oracle by byte density [CEILING] | 0.7% | 1.0% | 1.2% | 1.2% | 1.2% |
| Belady / MIN [CEILING] | 0.7% | 1.0% | 1.2% | 1.2% | 1.2% |
| cheapest whole rounds first [CEILING] | 0.0% | 0.1% | 0.2% | 0.4% | 1.2% |
| random selection (control) | 0.1% | 0.1% | 0.2% | 0.4% | 1.2% |

### Precision (used / sent)

| policy | 8 KB | 16 KB | 32 KB | 64 KB | unlimited |
|---|---|---|---|---|---|
| LRU (least-recently-used) | 0.1% | 0.3% | 0.3% | 0.5% | 0.3% |
| FIFO / recency truncation | 0.1% | 0.3% | 0.3% | 0.5% | 0.3% |
| LFU (least-frequently-used) | 6.7% | 4.3% | 2.8% | 1.4% | 0.3% |
| GDSF (greedy-dual size-frequency) | 2.3% | 1.6% | 1.2% | 0.9% | 0.3% |
| sliding window (last N rounds, whole) | 0.0% | 0.2% | 0.3% | 0.4% | 0.3% |
| SIEVE | 4.7% | 3.5% | 2.5% | 1.4% | 0.3% |
| S3-FIFO | 14.2% | 12.8% | 11.0% | 7.5% | 0.3% |
| random eviction (control) | 0.2% | 0.2% | 0.4% | 0.6% | 0.3% |
| graph-ordered (preds → closure → rest) | 0.4% | 0.4% | 0.4% | 0.6% | 0.3% |
| largest spans first | 0.4% | 0.4% | 0.3% | 0.5% | 0.3% |
| smallest spans first | 1.8% | 1.3% | 0.9% | 0.6% | 0.3% |
| keep costliest-to-recover | 1.3% | 1.1% | 1.0% | 0.8% | 0.3% |
| oracle by byte density [CEILING] | 6.3% | 4.3% | 2.6% | 1.3% | 0.3% |
| Belady / MIN [CEILING] | 6.3% | 4.3% | 2.6% | 1.3% | 0.3% |
| cheapest whole rounds first [CEILING] | 0.4% | 0.3% | 0.4% | 0.4% | 0.3% |
| random selection (control) | 1.0% | 0.6% | 0.5% | 0.5% | 0.3% |

### Median KB handed over

| policy | 8 KB | 16 KB | 32 KB | 64 KB | unlimited |
|---|---|---|---|---|---|
| LRU (least-recently-used) | 7.0 | 14.7 | 30.2 | 61.1 | 158.6 |
| FIFO / recency truncation | 7.0 | 14.7 | 30.2 | 61.1 | 158.6 |
| LFU (least-frequently-used) | 7.2 | 14.8 | 30.4 | 60.9 | 158.6 |
| GDSF (greedy-dual size-frequency) | 7.1 | 14.8 | 30.3 | 61.5 | 158.6 |
| sliding window (last N rounds, whole) | 1.1 | 9.5 | 23.6 | 51.2 | 158.6 |
| SIEVE | 7.0 | 14.8 | 30.3 | 60.9 | 158.6 |
| S3-FIFO | 2.2 | 2.8 | 4.3 | 7.4 | 158.6 |
| random eviction (control) | 7.0 | 14.8 | 30.3 | 61.8 | 158.6 |
| graph-ordered (preds → closure → rest) | 8.0 | 16.0 | 32.0 | 64.0 | 158.6 |
| largest spans first | 8.0 | 16.0 | 32.0 | 64.0 | 158.6 |
| smallest spans first | 7.7 | 15.5 | 31.2 | 62.5 | 158.6 |
| keep costliest-to-recover | 8.0 | 16.0 | 32.0 | 64.0 | 158.6 |
| oracle by byte density [CEILING] | 8.0 | 16.0 | 32.0 | 64.0 | 158.6 |
| Belady / MIN [CEILING] | 8.0 | 16.0 | 32.0 | 64.0 | 158.6 |
| cheapest whole rounds first [CEILING] | 8.0 | 16.0 | 32.0 | 64.0 | 158.6 |
| random selection (control) | 8.0 | 16.0 | 32.0 | 64.0 | 158.6 |
