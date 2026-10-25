# bs_step: offline step/idle/K/hold analyses (topic 18 brainstorm)

## d1/d2 per cell (window only)

| cell | no-step gap share | gaps >50 ms | requests running (time-wtd) | decode seqs per decode-only step | step time decode-only / mixed |
|---|---|---|---|---|---|
| w08_none | 0.00 % | 2 | 6.3 | 6.17 | 55 % / 45 % |
| w08_m_stock | 0.02 % | 14 | 5.7 | 5.44 | 48 % / 52 % |
| w08_m_share | 0.12 % | 9 | 4.8 | 4.66 | 50 % / 50 % |
| w16_none | 0.00 % | 0 | 10.5 | 10.57 | 7 % / 93 % |
| w16_none_aa | 0.00 % | 0 | 10.3 | 10.41 | 6 % / 94 % |
| w16_m_stock | 0.00 % | 0 | 10.6 | 10.57 | 5 % / 95 % |
| w16_m_share | 0.00 % | 0 | 10.5 | 10.82 | 4 % / 96 % |
| w16_h_backoff | 0.00 % | 0 | 9.7 | 9.32 | 29 % / 71 % |
| w16_h_share | 0.00 % | 0 | 9.7 | 9.25 | 36 % / 64 % |
| x16_h_share_start | 0.00 % | 0 | 9.0 | 8.67 | 43 % / 57 % |
| w16_sgate | 0.00 % | 0 | 7.5 | 7.45 | 50 % / 50 % |
| w32_none | 0.00 % | 0 | 10.8 | 10.77 | 10 % / 90 % |
| w32_m_stock | 0.00 % | 0 | 11.0 | 11.53 | 3 % / 97 % |
| w32_m_share | 0.00 % | 0 | 10.9 | 11.08 | 4 % / 96 % |
| w32_h_backoff | 0.00 % | 0 | 9.9 | 9.41 | 14 % / 86 % |
| w32_h_share | 0.00 % | 0 | 9.8 | 9.32 | 25 % / 75 % |
| w32_sgate | 0.00 % | 0 | 7.5 | 7.48 | 51 % / 49 % |

## d3 step-model components (share of predicted step time)

| cell | fixed cost of decode-only steps | fixed of mixed | prefill | decode per-seq+ctx |
|---|---|---|---|---|
| w08_none | 34.2 % | 2.8 % | 40.6 % | 22.5 % |
| w16_sgate | 28.1 % | 2.7 % | 45.0 % | 24.3 % |
| w32_sgate | 28.4 % | 2.5 % | 45.1 % | 24.0 % |
| x16_h_share_start | 22.3 % | 2.6 % | 52.4 % | 22.7 % |
| w16_h_share | 18.1 % | 2.4 % | 59.9 % | 19.7 % |

## d3 pool constraint: summed lead context of the K sessions that would be active / 258,176-token pool (1-s grid; K>8 adds the next sessions in the gate queue, FCFS)

| cell | K | p50 | p90 | p99 | P(>0.9) | P(>1.0) | iid-resampled P(>0.9) | decode-amortization gain if no extra eviction |
|---|---|---|---|---|---|---|---|---|
| w16_sgate | 8 | 0.71 | 0.80 | 0.84 | 0.0 % | 0.0 % | 0.0 % | +0.0 % |
| w16_sgate | 9 | 0.79 | 0.89 | 0.94 | 7.1 % | 0.0 % | 8.8 % | +3.2 % |
| w16_sgate | 10 | 0.86 | 0.97 | 1.04 | 35.0 % | 4.8 % | 44.9 % | +6.0 % |
| w16_sgate | 11 | 0.92 | 1.06 | 1.13 | 57.2 % | 27.6 % | 79.6 % | +8.3 % |
| w16_sgate | 12 | 1.00 | 1.14 | 1.23 | 71.5 % | 50.4 % | 94.9 % | +10.3 % |
| w16_sgate | gate queue mean 6.8; active ws p50 24490, mean 22820, p90 29746 tokens | | | | | | | |
| w32_sgate | 8 | 0.71 | 0.80 | 0.86 | 0.0 % | 0.0 % | 0.1 % | +0.0 % |
| w32_sgate | 9 | 0.77 | 0.88 | 0.95 | 5.5 % | 0.0 % | 8.9 % | +3.3 % |
| w32_sgate | 10 | 0.85 | 0.96 | 1.02 | 27.3 % | 3.5 % | 41.5 % | +6.0 % |
| w32_sgate | 11 | 0.91 | 1.04 | 1.12 | 55.0 % | 19.2 % | 75.2 % | +8.4 % |
| w32_sgate | 12 | 0.98 | 1.12 | 1.20 | 76.1 % | 44.7 % | 92.8 % | +10.5 % |
| w32_sgate | gate queue mean 21.1; active ws p50 24613, mean 22601, p90 29840 tokens | | | | | | | |

## g1 holds vs server room (hold-seconds inside the window)

| cell | hold-s | mean sessions holding | share with waiting=0 and KV usage<0.85 | share with waiting=0 | mean KV usage while held | share with no step running |
|---|---|---|---|---|---|---|
| w16_h_backoff | 25,485 | 4.7 | 38 % | 76 % | 0.83 | 0.0 % |
| w32_h_backoff | 106,918 | 19.8 | 21 % | 47 % | 0.85 | 0.0 % |
| w16_h_share | 23,907 | 4.4 | 48 % | 83 % | 0.84 | 0.0 % |
| w32_h_share | 106,454 | 19.7 | 27 % | 49 % | 0.86 | 0.0 % |
| x16_h_share_start | 28,675 | 5.3 | 71 % | 96 % | 0.79 | 0.0 % |

x16 by hold point: start 24,743 hold-s (room 71 %), mid 3,932 hold-s (room 68 %).
