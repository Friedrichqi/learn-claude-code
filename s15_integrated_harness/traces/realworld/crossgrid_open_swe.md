## E2-R / E3-R — budget x eviction policy on the cross-task population

234 groups, 732 successor tasks. Arm: all earlier tasks in the group, cut down to the budget by the policy.


### Removable rounds (share of the successor's reading rounds that disappear)

| policy | 8 KB | 16 KB | 32 KB | 64 KB | unlimited |
|---|---|---|---|---|---|
| LRU (least-recently-used) | 0.3% | 0.6% | 1.4% | 2.1% | 4.3% |
| FIFO / recency truncation | 0.3% | 0.5% | 1.1% | 1.8% | 4.3% |
| LFU (least-frequently-used) | 2.6% | 2.9% | 3.1% | 3.4% | 4.3% |
| GDSF (greedy-dual size-frequency) | 2.9% | 3.4% | 3.8% | 4.1% | 4.3% |
| sliding window (last N rounds, whole) | 0.3% | 0.6% | 1.4% | 2.1% | 4.3% |
| SIEVE | 2.0% | 2.7% | 3.0% | 3.3% | 4.3% |
| S3-FIFO | 2.0% | 2.1% | 2.2% | 2.3% | 4.3% |
| random eviction (control) | 0.3% | 0.6% | 1.3% | 1.8% | 4.3% |
| graph-ordered (preds → closure → rest) | 1.4% | 1.7% | 2.3% | 2.8% | 4.3% |
| largest spans first | 0.2% | 0.2% | 0.2% | 0.6% | 4.3% |
| smallest spans first | 3.5% | 3.7% | 3.9% | 4.0% | 4.3% |
| keep costliest-to-recover | 1.3% | 1.4% | 1.5% | 2.0% | 4.3% |
| oracle by byte density [CEILING] | 4.2% | 4.3% | 4.3% | 4.3% | 4.3% |
| Belady / MIN [CEILING] | 4.2% | 4.3% | 4.3% | 4.3% | 4.3% |
| cheapest whole rounds first [CEILING] | 0.7% | 0.9% | 1.4% | 1.9% | 4.3% |
| random selection (control) | 1.0% | 1.2% | 1.7% | 2.0% | 4.3% |

### Byte recall

| policy | 8 KB | 16 KB | 32 KB | 64 KB | unlimited |
|---|---|---|---|---|---|
| LRU (least-recently-used) | 0.0% | 0.1% | 0.2% | 0.5% | 1.2% |
| FIFO / recency truncation | 0.0% | 0.1% | 0.2% | 0.5% | 1.2% |
| LFU (least-frequently-used) | 0.1% | 0.2% | 0.3% | 0.6% | 1.2% |
| GDSF (greedy-dual size-frequency) | 0.1% | 0.2% | 0.4% | 0.6% | 1.2% |
| sliding window (last N rounds, whole) | 0.0% | 0.1% | 0.2% | 0.5% | 1.2% |
| SIEVE | 0.1% | 0.2% | 0.2% | 0.6% | 1.2% |
| S3-FIFO | 0.1% | 0.1% | 0.1% | 0.1% | 1.2% |
| random eviction (control) | 0.0% | 0.1% | 0.2% | 0.5% | 1.2% |
| graph-ordered (preds → closure → rest) | 0.1% | 0.1% | 0.3% | 0.6% | 1.2% |
| largest spans first | 0.0% | 0.1% | 0.2% | 0.4% | 1.2% |
| smallest spans first | 0.2% | 0.3% | 0.4% | 0.6% | 1.2% |
| keep costliest-to-recover | 0.1% | 0.2% | 0.3% | 0.5% | 1.2% |
| oracle by byte density [CEILING] | 0.7% | 1.0% | 1.2% | 1.2% | 1.2% |
| Belady / MIN [CEILING] | 0.7% | 1.0% | 1.2% | 1.2% | 1.2% |
| cheapest whole rounds first [CEILING] | 0.0% | 0.1% | 0.2% | 0.4% | 1.2% |
| random selection (control) | 0.1% | 0.1% | 0.2% | 0.4% | 1.2% |

### Precision (used / sent)

| policy | 8 KB | 16 KB | 32 KB | 64 KB | unlimited |
|---|---|---|---|---|---|
| LRU (least-recently-used) | 0.2% | 0.4% | 0.4% | 0.6% | 0.3% |
| FIFO / recency truncation | 0.2% | 0.3% | 0.4% | 0.6% | 0.3% |
| LFU (least-frequently-used) | 1.5% | 1.0% | 0.7% | 0.7% | 0.3% |
| GDSF (greedy-dual size-frequency) | 1.6% | 1.1% | 0.8% | 0.7% | 0.3% |
| sliding window (last N rounds, whole) | 0.2% | 0.4% | 0.4% | 0.6% | 0.3% |
| SIEVE | 1.0% | 0.8% | 0.6% | 0.7% | 0.3% |
| S3-FIFO | 16.0% | 7.3% | 3.0% | 1.4% | 0.3% |
| random eviction (control) | 0.3% | 0.4% | 0.5% | 0.6% | 0.3% |
| graph-ordered (preds → closure → rest) | 0.6% | 0.6% | 0.6% | 0.7% | 0.3% |
| largest spans first | 0.4% | 0.4% | 0.3% | 0.5% | 0.3% |
| smallest spans first | 1.8% | 1.3% | 0.9% | 0.6% | 0.3% |
| keep costliest-to-recover | 0.7% | 0.7% | 0.6% | 0.6% | 0.3% |
| oracle by byte density [CEILING] | 6.3% | 4.3% | 2.6% | 1.3% | 0.3% |
| Belady / MIN [CEILING] | 6.3% | 4.3% | 2.6% | 1.3% | 0.3% |
| cheapest whole rounds first [CEILING] | 0.4% | 0.3% | 0.4% | 0.4% | 0.3% |
| random selection (control) | 1.0% | 0.6% | 0.5% | 0.5% | 0.3% |

### Median KB handed over

| policy | 8 KB | 16 KB | 32 KB | 64 KB | unlimited |
|---|---|---|---|---|---|
| LRU (least-recently-used) | 7.0 | 14.7 | 30.2 | 61.0 | 158.6 |
| FIFO / recency truncation | 7.0 | 14.7 | 30.2 | 61.0 | 158.6 |
| LFU (least-frequently-used) | 7.0 | 14.7 | 30.3 | 60.9 | 158.6 |
| GDSF (greedy-dual size-frequency) | 7.0 | 14.6 | 30.0 | 61.4 | 158.6 |
| sliding window (last N rounds, whole) | 6.8 | 14.7 | 30.2 | 61.0 | 158.6 |
| SIEVE | 7.0 | 14.7 | 30.3 | 60.9 | 158.6 |
| S3-FIFO | 0.2 | 0.8 | 2.4 | 5.6 | 158.6 |
| random eviction (control) | 6.9 | 14.7 | 30.3 | 61.7 | 158.6 |
| graph-ordered (preds → closure → rest) | 8.0 | 16.0 | 32.0 | 64.0 | 158.6 |
| largest spans first | 8.0 | 16.0 | 32.0 | 64.0 | 158.6 |
| smallest spans first | 7.7 | 15.5 | 31.2 | 62.5 | 158.6 |
| keep costliest-to-recover | 8.0 | 16.0 | 32.0 | 64.0 | 158.6 |
| oracle by byte density [CEILING] | 8.0 | 16.0 | 32.0 | 64.0 | 158.6 |
| Belady / MIN [CEILING] | 8.0 | 16.0 | 32.0 | 64.0 | 158.6 |
| cheapest whole rounds first [CEILING] | 8.0 | 16.0 | 32.0 | 64.0 | 158.6 |
| random selection (control) | 8.0 | 16.0 | 32.0 | 64.0 | 158.6 |
