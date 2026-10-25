# research/18_harness_wait tables (w16_none, w16_none_aa, w08_m_share, w16_m_share, w32_h_backoff, w32_h_share, w32_m_stock, w16_sgate, w08_none, w16_m_stock, w32_sgate, w08_m_stock, w32_m_share, w32_none, w16_h_share, w16_h_backoff, x16_h_share_start)

Step model (H100 TP=2, 1480554 steps): fixed 0.01288, mixed -0.00014, pf_tok 0.05367, pf_attn 0.00183, dec_seq 0.00016, dec_ctx 0.05090; R² 1.000, median error 2.9%

## H5 throughput and latency (measurement window)

| cell | arm | N | work lead calls/h [10-min blocks 95%] | wait-only calls | turns/h | call p50 / mean (s) | hold + wait per call (s) | turn p50 / p95 KM (s) | censored | hit rate | prompt tok/call | GPU: evicted / preempted / fresh / decode / idle | preemptions |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| w16_none | none | 16 | 1,717 [1,603, 2,256] | 0 | 37.3 | 22.6 / 30.3 | 0.00 | 1,094 / – | 23.2% | 4.6% | 24,665 | 78.3% / 0.0% / 10.7% / 11.1% / 0.0% | 4 |
| w16_none_aa | none | 16 | 1,739 [1,637, 1,896] | 0 | 37.3 | 22.8 / 31.5 | 0.00 | 1,984 / 3,584 | 27.3% | 1.4% | 24,276 | 78.3% / 0.0% / 12.0% / 9.7% / 0.0% | 5 |
| w08_m_share | m_share | 8 | 4,595 [2,981, 5,693] | 2179 | 114.0 | 1.7 / 3.3 | 1.35 | 157 / 819 | 4.7% | 87.3% | 23,143 | 3.4% / 0.0% / 43.3% / 53.2% / 0.1% | 0 |
| w16_m_share | m_share | 16 | 1,525 [1,254, 2,156] | 748 | 32.0 | 24.0 / 27.6 | 1.28 | 2,164 / 2,999 | 33.3% | 7.0% | 22,560 | 85.4% / 0.0% / 7.2% / 7.5% / 0.0% | 6 |
| w32_h_backoff | h_backoff | 32 | 3,173 [2,971, 3,440] | 0 | 73.3 | 8.8 / 12.0 | 22.39 | 1,097 / 4,329 | 28.2% | 49.8% | 23,427 | 62.7% / 0.0% / 19.2% / 18.1% / 0.0% | 1 |
| w32_h_share | h_share | 32 | 3,934 [1,664, 7,192] | 0 | 88.7 | 3.7 / 9.2 | 17.17 | 1,278 / 2,819 | 23.0% | 67.7% | 23,677 | 45.3% / 0.0% / 25.4% / 29.3% / 0.0% | 4 |
| w32_m_stock | m_stock | 32 | 1,266 [992, 1,664] | 924 | 30.0 | 50.1 / 55.3 | 7.28 | 5,012 / – | 65.9% | 1.4% | 23,099 | 79.9% / 0.0% / 13.9% / 6.2% / 0.0% | 1 |
| w16_sgate | sgate | 16 | 6,539 [4,702, 7,991] | 0 | 132.0 | 1.9 / 8.0 | 0.00 | 335 / 1,032 | 7.5% | 88.0% | 23,257 | 4.5% / 0.0% / 40.9% / 54.6% / 0.0% | 0 |
| w08_none | none | 8 | 6,713 [4,760, 8,332] | 0 | 142.0 | 1.5 / 3.4 | 0.00 | 100 / 429 | 3.7% | 89.6% | 23,324 | 2.8% / 0.0% / 38.4% / 58.8% / 0.0% | 0 |
| w16_m_stock | m_stock | 16 | 1,571 [1,154, 2,521] | 990 | 26.7 | 21.1 / 23.5 | 3.28 | 2,029 / 4,151 | 34.2% | 16.8% | 22,525 | 84.0% / 0.0% / 7.6% / 8.4% / 0.0% | 3 |
| w32_sgate | sgate | 32 | 5,837 [4,214, 7,014] | 0 | 153.3 | 1.9 / 18.2 | 0.00 | 698 / 1,246 | 12.7% | 86.8% | 23,808 | 5.1% / 0.0% / 40.5% / 54.5% / 0.0% | 0 |
| w08_m_stock | m_stock | 8 | 6,564 [5,378, 7,814] | 848 | 119.3 | 1.7 / 3.0 | 0.45 | 161 / 644 | 3.9% | 89.2% | 24,151 | 4.7% / 0.0% / 43.6% / 51.7% / 0.0% | 0 |
| w32_m_share | m_share | 32 | 1,319 [1,214, 1,484] | 759 | 31.3 | 51.1 / 56.8 | 4.88 | 5,227 / – | 61.7% | 1.4% | 23,314 | 81.8% / 0.0% / 10.8% / 7.4% / 0.0% | 4 |
| w32_none | none | 32 | 1,807 [1,662, 2,082] | 0 | 64.0 | 50.9 / 59.5 | 0.00 | 776 / – | 33.0% | 1.4% | 22,362 | 75.9% / 0.0% / 10.7% / 13.5% / 0.0% | 11 |
| w16_h_share | h_share | 16 | 4,837 [3,596, 6,314] | 0 | 110.0 | 2.6 / 6.8 | 3.06 | 350 / 1,418 | 9.6% | 78.4% | 23,628 | 27.3% / 0.0% / 32.6% / 40.1% / 0.0% | 3 |
| w16_h_backoff | h_backoff | 16 | 4,157 [3,451, 5,159] | 0 | 84.0 | 3.7 / 8.5 | 4.03 | 613 / 2,644 | 11.8% | 70.6% | 23,870 | 40.4% / 0.0% / 26.8% / 32.8% / 0.0% | 4 |
| x16_h_share_start | h_share_start | 16 | 6,096 [4,250, 8,429] | 0 | 129.3 | 2.4 / 5.1 | 3.12 | 314 / 1,057 | 6.7% | 85.0% | 23,524 | 11.0% / 0.0% / 41.7% / 47.3% / 0.0% | 3 |

## Recovery ratio R = (X − none) / (sgate − none), work lead calls/h

| arm @ N | R | 95% (block bootstrap) |
|---|---|---|
| m_share@16 | -0.04 | [-0.10, 0.02] |
| m_stock@16 | -0.03 | [-0.10, 0.04] |
| h_share@16 | 0.65 | [0.49, 0.84] |
| h_backoff@16 | 0.51 | [0.41, 0.63] |
| h_share_start@16 | 0.91 | [0.70, 1.16] |
| h_backoff@32 | 0.34 | [0.29, 0.41] |
| h_share@32 | 0.53 | [0.22, 0.89] |
| m_stock@32 | -0.13 | [-0.19, -0.08] |
| m_share@32 | -0.12 | [-0.17, -0.09] |

A/A: w16_none_aa − w16_none = 21 calls/h (1.2%).

## H1 holds and waits

| cell | arm | hold checks | held | hold p50 / p95 / max (s) | why | model waits | per 100 work calls | requested p50 / p95 (s) | slept (s) | status lines |
|---|---|---|---|---|---|---|---|---|---|---|
| w16_none | none | 0 | – | – / – / – | – | 0 | 0.0 | – / – | 0 | 0 |
| w16_none_aa | none | 0 | – | – / – / – | – | 0 | 0.0 | – / – | 0 | 0 |
| w08_m_share | m_share | 0 | – | – / – / – | – | 2177 | 31.6 | 1.0 / 10.0 | 9,328 | 9258 |
| w16_m_share | m_share | 0 | – | – / – / – | – | 747 | 32.7 | 2.0 / 10.0 | 2,929 | 3070 |
| w32_h_backoff | h_backoff | 4693 | 49.4% | 6.8 / 225.6 / 600.0 | {'clear': 2307, None: 8, 'cap': 4} | 0 | 0.0 | – / – | 0 | 0 |
| w32_h_share | h_share | 5832 | 7.0% | 56.3 / 600.0 / 600.0 | {'fits': 296, None: 11, 'cap': 102} | 0 | 0.0 | – / – | 0 | 0 |
| w32_m_stock | m_stock | 0 | – | – / – / – | – | 923 | 48.6 | 10.0 / 60.0 | 13,833 | 2826 |
| w16_sgate | sgate | 0 | – | – / – / – | – | 0 | 0.0 | – / – | 0 | 0 |
| w08_none | none | 0 | – | – / – / – | – | 0 | 0.0 | – / – | 0 | 0 |
| w16_m_stock | m_stock | 0 | – | – / – / – | – | 988 | 41.9 | 5.0 / 20.0 | 7,738 | 3380 |
| w32_sgate | sgate | 0 | – | – / – / – | – | 0 | 0.0 | – / – | 0 | 0 |
| w08_m_stock | m_stock | 0 | – | – / – / – | – | 850 | 8.6 | 5.0 / 10.0 | 4,419 | 10856 |
| w32_m_share | m_share | 0 | – | – / – / – | – | 757 | 38.3 | 10.0 / 30.0 | 9,658 | 2737 |
| w32_none | none | 0 | – | – / – / – | – | 0 | 0.0 | – / – | 0 | 0 |
| w16_h_share | h_share | 7147 | 5.2% | 18.5 / 231.4 / 412.1 | {'fits': 374} | 0 | 0.0 | – / – | 0 | 0 |
| w16_h_backoff | h_backoff | 6150 | 30.9% | 2.6 / 70.7 / 386.6 | {'clear': 1896, None: 2} | 0 | 0.0 | – / – | 0 | 0 |
| x16_h_share_start | h_share_start | 9213 | 4.5% | 26.1 / 255.6 / 491.9 | {'fits': 416} | 0 | 0.0 | – / – | 0 | 0 |

## H1b wait loops (model arms; exploratory, added after the freeze)

A run is a stretch of consecutive wait-only rounds within one turn. "Crowded" means the status line the model read met its harness twin's rule (share: ahead + own > 0.9 × pool; stock: a request waiting for KV memory, or KV in use ≥ 92%). GPU time is a share of the window's wall time, split like the H5 columns; other calls are compaction summaries and one-shot helper calls.

| cell | arm | wait-only calls | runs | sessions with a run ≥ 10 | run length p50 / p90 / max | wait-only calls in runs ≥ 10 | runs ended: resumed / budget / last | crowded: at run start / wait-only calls / work calls | GPU: wait-only calls (evicted recompute) / work calls / other calls |
|---|---|---|---|---|---|---|---|---|---|
| w08_m_share | m_share | 2179 | 88 | 12 | 1 / 98 / 100 | 96.7% | 64 / 18 / 6 | 0.0% / 0.0% / 0.0% | 7.4% (1.5%) / 85.2% / 7.2% |
| w16_m_share | m_share | 748 | 19 | 7 | 43 / 97 / 100 | 99.3% | 7 / 6 / 6 | 68.4% / 36.4% / 29.8% | 21.9% (20.7%) / 76.3% / 1.8% |
| w32_m_stock | m_stock | 924 | 106 | 16 | 1 / 49 / 93 | 90.3% | 89 / 0 / 17 | 99.1% / 100.0% / 100.0% | 31.8% (29.2%) / 66.3% / 1.9% |
| w16_m_stock | m_stock | 990 | 111 | 7 | 1 / 30 / 98 | 87.6% | 98 / 7 / 6 | 96.4% / 88.2% / 83.2% | 30.7% (28.2%) / 67.7% / 1.6% |
| w08_m_stock | m_stock | 848 | 167 | 5 | 1 / 1 / 100 | 80.9% | 155 / 7 / 5 | 0.6% / 1.8% / 0.2% | 3.3% (0.9%) / 90.9% / 5.8% |
| w32_m_share | m_share | 759 | 48 | 15 | 1 / 75 / 97 | 94.6% | 29 / 2 / 17 | 83.3% / 76.4% / 67.2% | 23.1% (21.7%) / 73.6% / 3.3% |

## H2 decisions and oscillation

| cell | arm | crowded at check | bunched releases (< 2 s) | waiting queue mean | autocorrelation 10 / 30 / 60 s |
|---|---|---|---|---|---|
| w16_none | none | – | – | 4.9 | 0.64 / 0.50 / 0.39 |
| w16_none_aa | none | – | – | 5.1 | 0.47 / 0.42 / 0.36 |
| w08_m_share | m_share | – | – | 0.0 | 0.05 / -0.00 / -0.00 |
| w16_m_share | m_share | – | – | 4.3 | 0.52 / 0.54 / 0.46 |
| w32_h_backoff | h_backoff | 49.3% | 93.9% | 1.2 | -0.06 / 0.04 / 0.01 |
| w32_h_share | h_share | 7.1% | 44.5% | 1.3 | 0.78 / 0.67 / 0.59 |
| w32_m_stock | m_stock | – | – | 17.9 | 0.70 / 0.53 / 0.59 |
| w16_sgate | sgate | – | – | 7.0 | 0.67 / 0.32 / 0.10 |
| w08_none | none | – | – | 0.0 | -0.01 / 0.02 / 0.02 |
| w16_m_stock | m_stock | – | – | 3.4 | 0.42 / 0.41 / 0.26 |
| w32_sgate | sgate | – | – | 22.8 | 0.72 / 0.39 / 0.20 |
| w08_m_stock | m_stock | – | – | 0.0 | 0.01 / 0.00 / 0.01 |
| w32_m_share | m_share | – | – | 18.6 | 0.56 / 0.45 / 0.52 |
| w32_none | none | – | – | 20.3 | 0.64 / 0.50 / 0.50 |
| w16_h_share | h_share | 5.3% | 46.0% | 0.4 | 0.54 / 0.45 / 0.40 |
| w16_h_backoff | h_backoff | 30.9% | 85.4% | 0.4 | 0.07 / 0.01 / -0.01 |
| x16_h_share_start | h_share_start | 4.5% | 44.2% | 0.1 | 0.48 / 0.38 / 0.25 |

## H3 prefix survival

| cell | arm | holds / waits with a share reading | prefix kept p50 / mean | work calls: hit / ideal |
|---|---|---|---|---|
| w16_none | none | 0 | – / – | 6.5% |
| w16_none_aa | none | 0 | – / – | 2.2% |
| w08_m_share | m_share | 2177 | 100.1% / 97.6% | 98.6% |
| w16_m_share | m_share | 747 | 70.7% / 56.0% | 10.1% |
| w32_h_backoff | h_backoff | 0 | – / – | 55.5% |
| w32_h_share | h_share | 398 | 1.9% / 24.8% | 74.4% |
| w32_m_stock | m_stock | 0 | – / – | 2.7% |
| w16_sgate | sgate | 0 | – / – | 97.9% |
| w08_none | none | 0 | – / – | 99.0% |
| w16_m_stock | m_stock | 0 | – / – | 23.9% |
| w32_sgate | sgate | 0 | – / – | 97.4% |
| w08_m_stock | m_stock | 0 | – / – | 98.5% |
| w32_m_share | m_share | 757 | 1.7% / 15.7% | 2.3% |
| w32_none | none | 0 | – / – | 2.6% |
| w16_h_share | h_share | 374 | 4.0% / 28.4% | 86.8% |
| w16_h_backoff | h_backoff | 0 | – / – | 78.9% |
| x16_h_share_start | h_share_start | 370 | 100.0% / 67.0% | 95.2% |

## H4 fairness and H6 overhead

| cell | arm | sessions | Jain index (work calls) | max hold + wait per session (s) | status requests/s | status age p50 / p99 (ms) |
|---|---|---|---|---|---|---|
| w16_none | none | 26 | 0.585 | 0 | 0.00 | – / – |
| w16_none_aa | none | 25 | 0.658 | 0 | 0.00 | – / – |
| w08_m_share | m_share | 49 | 0.655 | 2,930 | 2.67 | 278 / 678 |
| w16_m_share | m_share | 23 | 0.539 | 1,440 | 0.88 | 303 / 709 |
| w32_h_backoff | h_backoff | 57 | 0.620 | 4,294 | 2.37 | 250 / 496 |
| w32_h_share | h_share | 60 | 0.508 | 3,730 | 10.95 | 356 / 752 |
| w32_m_stock | m_stock | 50 | 0.480 | 3,000 | 0.87 | 249 / 496 |
| w16_sgate | sgate | 62 | 0.659 | 0 | 0.00 | – / – |
| w08_none | none | 61 | 0.689 | 0 | 0.00 | – / – |
| w16_m_stock | m_stock | 19 | 0.622 | 2,160 | 0.99 | 253 / 498 |
| w32_sgate | sgate | 78 | 0.557 | 0 | 0.00 | – / – |
| w08_m_stock | m_stock | 52 | 0.760 | 1,170 | 2.32 | 256 / 496 |
| w32_m_share | m_share | 45 | 0.521 | 2,070 | 1.03 | 336 / 710 |
| w32_none | none | 58 | 0.574 | 0 | 0.00 | – / – |
| w16_h_share | h_share | 54 | 0.637 | 786 | 3.54 | 304 / 743 |
| w16_h_backoff | h_backoff | 46 | 0.603 | 1,820 | 1.83 | 247 / 495 |
| x16_h_share_start | h_share_start | 63 | 0.635 | 1,052 | 4.37 | 299 / 737 |

## Pre-registered decisions (prereg.md)

Noise floor Δ_AA = |w16_none_aa − w16_none| = 21 work lead calls/h.

**R1 recovery** (gain = X − none; R = gain / (sgate − none))

| cell | gain | gain 95% | R | R 95% | recovers | hurts |
|---|---|---|---|---|---|---|
| w16_m_stock | -146 | [-460, 223] | -0.03 | [-0.10, 0.05] | no | no |
| w16_m_share | -193 | [-463, 73] | -0.04 | [-0.10, 0.02] | no | no |
| w16_h_backoff | 2,440 | [2,057, 2,817] | 0.51 | [0.41, 0.62] | YES | no |
| w16_h_share | 3,119 | [2,425, 3,837] | 0.65 | [0.49, 0.83] | YES | no |
| w16_sgate (gate gain) | 4,821 | [4,107, 5,484] | – | – | gate beats noise: yes | – |
| w32_m_stock | -541 | [-718, -347] | -0.13 | [-0.19, -0.08] | no | YES |
| w32_m_share | -489 | [-619, -372] | -0.12 | [-0.17, -0.09] | no | YES |
| w32_h_backoff | 1,366 | [1,213, 1,511] | 0.34 | [0.29, 0.41] | no | no |
| w32_h_share | 2,127 | [869, 3,489] | 0.53 | [0.22, 0.89] | YES | no |
| w32_sgate (gate gain) | 4,029 | [3,381, 4,628] | – | – | gate beats noise: yes | – |

**R2 decider (model − harness, same view)**

| contrast | D | 95% | differs |
|---|---|---|---|
| w16_m_stock - w16_h_backoff | -2,586 | [-3,033, -2,087] | YES |
| w16_m_share - w16_h_share | -3,312 | [-4,039, -2,580] | YES |
| w32_m_stock - w32_h_backoff | -1,907 | [-2,084, -1,717] | YES |
| w32_m_share - w32_h_share | -2,615 | [-3,981, -1,358] | YES |

**R3 information (share − stock, same decider)**

| contrast | S | 95% | differs |
|---|---|---|---|
| w16_m_share - w16_m_stock | -47 | [-433, 300] | no |
| w16_h_share - w16_h_backoff | 679 | [-101, 1,463] | no |
| w32_m_share - w32_m_stock | 53 | [-123, 214] | no |
| w32_h_share - w32_h_backoff | 761 | [-493, 2,113] | no |

**R4 no harm at N=8**

| cell | difference vs none | hit-rate difference (points) | no harm |
|---|---|---|---|
| w08_m_stock | -149 | -0.4 | no |
| w08_m_share | -2,119 | -2.3 | no |

**Exploratory contrasts (not pre-registered; work lead calls/h, the decisions' bootstrap)**

| contrast | D | 95% |
|---|---|---|
| x16_h_share_start - w16_h_share | 1,259 | [147, 2,461] |
| x16_h_share_start - w16_sgate | -443 | [-1,584, 737] |
