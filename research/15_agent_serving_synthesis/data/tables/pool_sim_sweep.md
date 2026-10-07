# Dead-first vs vLLM's LRU across pool sizes (11's n04 / n08 traces, offline)

Generated inline with `pool_sim.simulate` (LRU = vLLM 0.29's free queue: tail-first freeing, FIFO for hashed blocks; dead-first = the same plus a session's dead blocks moved to the front when its next request arrives). Recorded timing, no feedback. A smaller pool on the same trace stands in for more load per block.

| trace | pool fraction | pool (tokens) | LRU: hit % / eviction recompute, % of computed | dead-first: hit % / eviction recompute | dead-first removes, % of LRU's eviction recompute | total prefill saved, % |
|---|---:|---:|---|---|---:|---:|
| n08 | 1.00 | 258k | 99.5 / 3.4 | 100.0 / 0.0 | 99 | 3.4 |
| n08 | 0.90 | 232k | 99.3 / 4.8 | 100.0 / 0.1 | 97 | 4.6 |
| n08 | 0.80 | 206k | 99.0 / 6.3 | 99.9 / 0.8 | 87 | 5.5 |
| n08 | 0.75 | 193k | 98.7 / 7.9 | 99.6 / 2.6 | 69 | 5.5 |
| n08 | 0.70 | 180k | 97.7 / 13.4 | 98.6 / 8.6 | 39 | 5.2 |
| n08 | 0.65 | 167k | 95.5 / 23.3 | 96.2 / 20.2 | 17 | 3.9 |
| n08 | 0.60 | 154k | 92.6 / 33.3 | 93.2 / 31.5 | 8 | 2.6 |
| n04 | 1.00 | 258k | 99.9 / 0.6 | 100.0 / 0.0 | 98 | 0.6 |
| n04 | 0.60 | 154k | 99.4 / 2.7 | 100.0 / 0.0 | 100 | 2.7 |
| n04 | 0.50 | 129k | 99.1 / 3.7 | 100.0 / 0.0 | 100 | 3.7 |
| n04 | 0.45 | 116k | 99.0 / 4.2 | 100.0 / 0.1 | 98 | 4.1 |
| n04 | 0.40 | 103k | 98.7 / 5.3 | 99.8 / 0.7 | 87 | 4.6 |
| n04 | 0.35 | 90k | 97.6 / 9.6 | 98.7 / 5.3 | 47 | 4.5 |

