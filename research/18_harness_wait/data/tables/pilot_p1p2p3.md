# research/18_harness_wait tables (p08_none, p16_none, p16_m_stock, p16_m_share, p16_h_share, p16_h_backoff)

Step model (H100 TP=2, 206572 steps): fixed 0.01323, mixed -0.00017, pf_tok 0.05381, pf_attn 0.00182, dec_seq 0.00014, dec_ctx 0.04989; R² 1.000, median error 3.0%

## H5 throughput and latency (measurement window)

| cell | arm | N | work lead calls/h [10-min blocks 95%] | wait-only calls | turns/h | call p50 / mean (s) | hold + wait per call (s) | turn p50 / p95 KM (s) | censored | hit rate | prompt tok/call | GPU: evicted / preempted / fresh / decode / idle | preemptions |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| p08_none | none | 8 | 6,242 [4,970, 7,011] | 0 | 142.0 | 1.7 / 3.8 | 0.00 | 93 / 410 | 10.7% | 89.3% | 23,688 | 1.7% / 0.0% / 38.7% / 59.6% / 0.0% | 0 |
| p16_none | none | 16 | 2,138 [1,807, 2,644] | 0 | 58.0 | 18.0 / 22.0 | 0.00 | 1,516 / – | 53.3% | 13.8% | 22,636 | 74.2% / 0.0% / 17.2% / 8.5% / 0.0% | 4 |
| p16_m_stock | m_stock | 16 | 1,072 [808, 1,498] | 525 | 34.0 | 22.1 / 23.1 | 4.19 | – / – | 70.6% | 15.8% | 23,529 | 83.5% / 0.0% / 9.3% / 7.3% / 0.0% | 0 |
| p16_m_share | m_share | 16 | 2,010 [1,375, 2,880] | 502 | 48.0 | 15.1 / 17.6 | 2.98 | 1,246 / – | 56.5% | 34.2% | 20,763 | 77.3% / 0.0% / 12.8% / 9.9% / 0.0% | 1 |
| p16_h_share | h_share | 16 | 5,760 [5,371, 6,363] | 0 | 158.0 | 2.3 / 5.4 | 3.38 | 291 / 967 | 17.5% | 83.7% | 24,411 | 18.9% / 0.0% / 37.1% / 44.0% / 0.0% | 1 |
| p16_h_backoff | h_backoff | 16 | 4,422 [3,447, 5,784] | 0 | 96.0 | 4.2 / 7.4 | 3.97 | 454 / – | 31.2% | 71.3% | 24,080 | 45.7% / 0.0% / 24.2% / 30.1% / 0.0% | 2 |

## H1 holds and waits

| cell | arm | hold checks | held | hold p50 / p95 / max (s) | why | model waits | per 100 work calls | requested p50 / p95 (s) | slept (s) | status lines |
|---|---|---|---|---|---|---|---|---|---|---|
| p08_none | none | 0 | – | – / – / – | – | 0 | 0.0 | – / – | 0 | 0 |
| p16_none | none | 0 | – | – / – / – | – | 0 | 0.0 | – / – | 0 | 0 |
| p16_m_stock | m_stock | 0 | – | – / – / – | – | 491 | 91.6 | 2.0 / 15.0 | 2,245 | 1060 |
| p16_m_share | m_share | 0 | – | – / – / – | – | 503 | 50.0 | 1.0 / 30.0 | 2,996 | 1508 |
| p16_h_share | h_share | 2821 | 5.3% | 51.0 / 202.4 / 234.3 | {'fits': 149} | 0 | 0.0 | – / – | 0 | 0 |
| p16_h_backoff | h_backoff | 2183 | 28.7% | 2.8 / 71.0 / 269.7 | {'clear': 623, None: 3} | 0 | 0.0 | – / – | 0 | 0 |

## H1b wait loops (model arms; exploratory, added after the freeze)

A run is a stretch of consecutive wait-only rounds within one turn. "Crowded" means the status line the model read met its harness twin's rule (share: ahead + own > 0.9 × pool; stock: a request waiting for KV memory, or KV in use ≥ 92%). GPU time is a share of the window's wall time, split like the H5 columns; other calls are compaction summaries and one-shot helper calls.

| cell | arm | wait-only calls | runs | sessions with a run ≥ 10 | run length p50 / p90 / max | wait-only calls in runs ≥ 10 | runs ended: resumed / budget / last | crowded: at run start / wait-only calls / work calls | GPU: wait-only calls (evicted recompute) / work calls / other calls |
|---|---|---|---|---|---|---|---|---|---|
| p16_m_stock | m_stock | 525 | 45 | 8 | 1 / 64 / 97 | 93.0% | 33 / 2 / 10 | 91.1% / 83.4% / 78.7% | 54.0% (50.6%) / 42.8% / 3.2% |
| p16_m_share | m_share | 502 | 47 | 8 | 1 / 53 / 97 | 92.0% | 38 / 3 / 6 | 27.7% / 25.1% / 23.8% | 29.1% (27.0%) / 67.9% / 3.0% |

## H2 decisions and oscillation

| cell | arm | crowded at check | bunched releases (< 2 s) | waiting queue mean | autocorrelation 10 / 30 / 60 s |
|---|---|---|---|---|---|
| p08_none | none | – | – | 0.0 | -0.01 / -0.01 / -0.01 |
| p16_none | none | – | – | 3.2 | 0.66 / 0.55 / 0.48 |
| p16_m_stock | m_stock | – | – | 3.6 | 0.39 / 0.51 / 0.35 |
| p16_m_share | m_share | – | – | 2.6 | 0.47 / 0.42 / 0.35 |
| p16_h_share | h_share | 5.2% | 49.7% | 0.1 | 0.07 / 0.07 / -0.03 |
| p16_h_backoff | h_backoff | 28.8% | 86.3% | 0.5 | 0.08 / 0.00 / 0.04 |

## H3 prefix survival

| cell | arm | holds / waits with a share reading | prefix kept p50 / mean | work calls: hit / ideal |
|---|---|---|---|---|
| p08_none | none | 0 | – / – | 99.0% |
| p16_none | none | 0 | – / – | 19.3% |
| p16_m_stock | m_stock | 0 | – / – | 25.5% |
| p16_m_share | m_share | 503 | 100.0% / 57.8% | 45.5% |
| p16_h_share | h_share | 149 | 4.8% / 27.8% | 91.6% |
| p16_h_backoff | h_backoff | 0 | – / – | 78.4% |

## H4 fairness and H6 overhead

| cell | arm | sessions | Jain index (work calls) | max hold + wait per session (s) | status requests/s | status age p50 / p99 (ms) |
|---|---|---|---|---|---|---|
| p08_none | none | 25 | 0.602 | 0 | 0.00 | – / – |
| p16_none | none | 22 | 0.641 | 0 | 0.00 | – / – |
| p16_m_stock | m_stock | 17 | 0.488 | 750 | 1.14 | 250 / 496 |
| p16_m_share | m_share | 19 | 0.526 | 1,290 | 1.59 | 338 / 733 |
| p16_h_share | h_share | 32 | 0.480 | 673 | 4.20 | 304 / 719 |
| p16_h_backoff | h_backoff | 26 | 0.597 | 889 | 1.94 | 248 / 496 |

## Pilot gates

- none16_thrashes: PASS
- none8_does_not: PASS
- p16_m_stock_waits: PASS
- p16_m_share_waits: PASS
- p16_h_backoff_holds: PASS
- p16_h_backoff_no_deadlock: PASS
- p16_h_share_holds: PASS
- p16_h_share_no_deadlock: PASS
