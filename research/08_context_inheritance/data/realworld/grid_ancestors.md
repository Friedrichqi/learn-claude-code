## Budget x policy grid — arm `ancestors`, 240 trajectories


### Removable rounds, whole-observation identity (the deployable unit)

| policy | 8 KB | 16 KB | 32 KB | 64 KB | unlimited |
|---|---|---|---|---|---|
| LRU (least-recently-used) | 1.4% | 1.7% | 1.8% | 1.9% | 1.9% |
| FIFO / recency truncation | 1.4% | 1.7% | 1.8% | 1.9% | 1.9% |
| LFU (least-frequently-used) | 1.4% | 1.7% | 1.8% | 1.9% | 1.9% |
| GDSF (greedy-dual size-frequency) | 1.7% | 1.8% | 1.9% | 1.9% | 1.9% |
| sliding window (last N rounds, whole) | 1.4% | 1.7% | 1.8% | 1.9% | 1.9% |
| SIEVE | 1.4% | 1.7% | 1.8% | 1.9% | 1.9% |
| S3-FIFO | 0.6% | 0.9% | 1.1% | 1.4% | 1.9% |
| random eviction (control) | 1.4% | 1.7% | 1.8% | 1.9% | 1.9% |
| graph-ordered (preds → closure → rest) | 1.7% | 1.8% | 1.9% | 1.9% | 1.9% |
| largest spans first | 0.8% | 1.0% | 1.4% | 1.8% | 1.9% |
| smallest spans first | 1.7% | 1.8% | 1.9% | 1.9% | 1.9% |
| keep costliest-to-recover | 1.3% | 1.4% | 1.7% | 1.9% | 1.9% |
| oracle by byte density [CEILING] | 1.9% | 1.9% | 1.9% | 1.9% | 1.9% |
| Belady / MIN [CEILING] | 1.9% | 1.9% | 1.9% | 1.9% | 1.9% |
| cheapest whole rounds first [CEILING] | 1.7% | 1.8% | 1.9% | 1.9% | 1.9% |
| random selection (control) | 1.4% | 1.6% | 1.8% | 1.9% | 1.9% |

### Removable rounds, line-level content addressing (the ceiling a finer unit buys)

| policy | 8 KB | 16 KB | 32 KB | 64 KB | unlimited |
|---|---|---|---|---|---|
| LRU (least-recently-used) | 4.6% | 6.7% | 8.4% | 9.1% | 9.1% |
| FIFO / recency truncation | 4.6% | 6.7% | 8.4% | 9.1% | 9.1% |
| LFU (least-frequently-used) | 4.6% | 6.7% | 8.4% | 9.1% | 9.1% |
| GDSF (greedy-dual size-frequency) | 4.5% | 6.8% | 8.6% | 9.1% | 9.1% |
| sliding window (last N rounds, whole) | 4.4% | 6.7% | 8.4% | 9.1% | 9.1% |
| SIEVE | 4.6% | 6.7% | 8.4% | 9.1% | 9.1% |
| S3-FIFO | 0.8% | 1.4% | 2.4% | 3.9% | 9.1% |
| random eviction (control) | 4.3% | 6.6% | 8.4% | 9.1% | 9.1% |
| graph-ordered (preds → closure → rest) | 5.1% | 7.1% | 8.6% | 9.1% | 9.1% |
| largest spans first | 3.9% | 4.8% | 7.3% | 8.9% | 9.1% |
| smallest spans first | 4.1% | 6.7% | 8.5% | 9.1% | 9.1% |
| keep costliest-to-recover | 4.5% | 5.4% | 7.7% | 9.0% | 9.1% |
| oracle by byte density [CEILING] | 4.8% | 5.7% | 7.7% | 8.9% | 9.1% |
| Belady / MIN [CEILING] | 4.8% | 5.8% | 7.7% | 8.9% | 9.1% |
| cheapest whole rounds first [CEILING] | 4.1% | 6.7% | 8.5% | 9.1% | 9.1% |
| random selection (control) | 4.2% | 6.2% | 8.3% | 9.1% | 9.1% |

### Byte recall

| policy | 8 KB | 16 KB | 32 KB | 64 KB | unlimited |
|---|---|---|---|---|---|
| LRU (least-recently-used) | 0.5% | 0.8% | 0.9% | 0.9% | 0.9% |
| FIFO / recency truncation | 0.5% | 0.8% | 0.9% | 0.9% | 0.9% |
| LFU (least-frequently-used) | 0.5% | 0.8% | 0.9% | 0.9% | 0.9% |
| GDSF (greedy-dual size-frequency) | 0.6% | 0.8% | 0.9% | 0.9% | 0.9% |
| sliding window (last N rounds, whole) | 0.5% | 0.8% | 0.9% | 0.9% | 0.9% |
| SIEVE | 0.5% | 0.8% | 0.9% | 0.9% | 0.9% |
| S3-FIFO | 0.0% | 0.2% | 0.3% | 0.5% | 0.9% |
| random eviction (control) | 0.4% | 0.8% | 0.9% | 0.9% | 0.9% |
| graph-ordered (preds → closure → rest) | 0.6% | 0.8% | 0.9% | 0.9% | 0.9% |
| largest spans first | 0.3% | 0.5% | 0.7% | 0.9% | 0.9% |
| smallest spans first | 0.5% | 0.8% | 0.9% | 0.9% | 0.9% |
| keep costliest-to-recover | 0.4% | 0.6% | 0.8% | 0.9% | 0.9% |
| oracle by byte density [CEILING] | 0.8% | 0.9% | 0.9% | 0.9% | 0.9% |
| Belady / MIN [CEILING] | 0.8% | 0.9% | 0.9% | 0.9% | 0.9% |
| cheapest whole rounds first [CEILING] | 0.5% | 0.8% | 0.9% | 0.9% | 0.9% |
| random selection (control) | 0.5% | 0.7% | 0.8% | 0.9% | 0.9% |

### Precision (used / sent)

| policy | 8 KB | 16 KB | 32 KB | 64 KB | unlimited |
|---|---|---|---|---|---|
| LRU (least-recently-used) | 0.2% | 0.1% | 0.1% | 0.1% | 0.1% |
| FIFO / recency truncation | 0.2% | 0.1% | 0.1% | 0.1% | 0.1% |
| LFU (least-frequently-used) | 0.2% | 0.1% | 0.1% | 0.1% | 0.1% |
| GDSF (greedy-dual size-frequency) | 0.2% | 0.1% | 0.1% | 0.1% | 0.1% |
| sliding window (last N rounds, whole) | 0.2% | 0.1% | 0.1% | 0.1% | 0.1% |
| SIEVE | 0.2% | 0.1% | 0.1% | 0.1% | 0.1% |
| S3-FIFO | 0.3% | 0.4% | 0.3% | 0.2% | 0.1% |
| random eviction (control) | 0.1% | 0.1% | 0.1% | 0.1% | 0.1% |
| graph-ordered (preds → closure → rest) | 0.1% | 0.1% | 0.1% | 0.1% | 0.1% |
| largest spans first | 0.1% | 0.1% | 0.1% | 0.1% | 0.1% |
| smallest spans first | 0.1% | 0.1% | 0.1% | 0.1% | 0.1% |
| keep costliest-to-recover | 0.1% | 0.1% | 0.1% | 0.1% | 0.1% |
| oracle by byte density [CEILING] | 0.2% | 0.1% | 0.1% | 0.1% | 0.1% |
| Belady / MIN [CEILING] | 0.2% | 0.1% | 0.1% | 0.1% | 0.1% |
| cheapest whole rounds first [CEILING] | 0.1% | 0.1% | 0.1% | 0.1% | 0.1% |
| random selection (control) | 0.1% | 0.1% | 0.1% | 0.1% | 0.1% |

### Latency saved per successor round (s, priced)

| policy | 8 KB | 16 KB | 32 KB | 64 KB | unlimited |
|---|---|---|---|---|---|
| LRU (least-recently-used) | 0.08 | 0.10 | 0.11 | 0.12 | 0.12 |
| FIFO / recency truncation | 0.08 | 0.10 | 0.11 | 0.12 | 0.12 |
| LFU (least-frequently-used) | 0.08 | 0.10 | 0.11 | 0.12 | 0.12 |
| GDSF (greedy-dual size-frequency) | 0.11 | 0.11 | 0.12 | 0.12 | 0.12 |
| sliding window (last N rounds, whole) | 0.08 | 0.10 | 0.11 | 0.12 | 0.12 |
| SIEVE | 0.08 | 0.10 | 0.11 | 0.12 | 0.12 |
| S3-FIFO | 0.03 | 0.05 | 0.06 | 0.08 | 0.12 |
| random eviction (control) | 0.09 | 0.10 | 0.11 | 0.12 | 0.12 |
| graph-ordered (preds → closure → rest) | 0.10 | 0.11 | 0.11 | 0.12 | 0.12 |
| largest spans first | 0.05 | 0.06 | 0.08 | 0.11 | 0.12 |
| smallest spans first | 0.11 | 0.11 | 0.12 | 0.12 | 0.12 |
| keep costliest-to-recover | 0.09 | 0.09 | 0.11 | 0.12 | 0.12 |
| oracle by byte density [CEILING] | 0.12 | 0.12 | 0.12 | 0.12 | 0.12 |
| Belady / MIN [CEILING] | 0.12 | 0.12 | 0.12 | 0.12 | 0.12 |
| cheapest whole rounds first [CEILING] | 0.11 | 0.11 | 0.12 | 0.12 | 0.12 |
| random selection (control) | 0.09 | 0.10 | 0.11 | 0.12 | 0.12 |
