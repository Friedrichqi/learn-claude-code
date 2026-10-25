# research/18_harness_wait tables (p16_none, p16_m_stock, p16_m_share, p16_m_stock_v2, p16_m_share_v2)

Step model (H100 TP=2, 75770 steps): fixed 0.01363, mixed -0.00113, pf_tok 0.05401, pf_attn 0.00181, dec_seq -0.00005, dec_ctx 0.05854; R² 1.000, median error 1.5%

## H5 throughput and latency (measurement window)

| cell | arm | N | work lead calls/h [10-min blocks 95%] | wait-only calls | turns/h | call p50 / mean (s) | hold + wait per call (s) | turn p50 / p95 KM (s) | censored | hit rate | prompt tok/call | GPU: evicted / preempted / fresh / decode / idle | preemptions |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| p16_none | none | 16 | 2,138 [1,807, 2,644] | 0 | 58.0 | 18.0 / 22.0 | 0.00 | 1,516 / – | 53.3% | 13.8% | 22,636 | 74.3% / 0.0% / 17.2% / 8.4% / 0.0% | 4 |
| p16_m_stock | m_stock | 16 | 1,072 [808, 1,498] | 525 | 34.0 | 22.1 / 23.1 | 4.19 | – / – | 70.6% | 15.8% | 23,529 | 83.6% / 0.0% / 9.3% / 7.2% / 0.0% | 0 |
| p16_m_share | m_share | 16 | 2,010 [1,375, 2,880] | 502 | 48.0 | 15.1 / 17.6 | 2.98 | 1,246 / – | 56.5% | 34.2% | 20,763 | 77.4% / 0.0% / 12.9% / 9.8% / 0.0% | 1 |
| p16_m_stock_v2 | m_stock | 16 | 1,320 [1,052, 1,696] | 473 | 26.0 | 23.2 / 24.2 | 4.27 | – / – | 72.7% | 19.2% | 23,216 | 85.0% / 0.0% / 8.9% / 6.2% / 0.0% | 0 |
| p16_m_share_v2 | m_share | 16 | 2,096 [1,422, 2,476] | 351 | 44.0 | 16.8 / 18.4 | 1.90 | 1,053 / – | 54.5% | 26.8% | 20,426 | 77.2% / 0.0% / 14.7% / 8.1% / 0.0% | 1 |

## H1 holds and waits

| cell | arm | hold checks | held | hold p50 / p95 / max (s) | why | model waits | per 100 work calls | requested p50 / p95 (s) | slept (s) | status lines |
|---|---|---|---|---|---|---|---|---|---|---|
| p16_none | none | 0 | – | – / – / – | – | 0 | 0.0 | – / – | 0 | 0 |
| p16_m_stock | m_stock | 0 | – | – / – / – | – | 491 | 91.6 | 2.0 / 15.0 | 2,245 | 1060 |
| p16_m_share | m_share | 0 | – | – / – / – | – | 503 | 50.0 | 1.0 / 30.0 | 2,996 | 1508 |
| p16_m_stock_v2 | m_stock | 0 | – | – / – / – | – | 458 | 69.4 | 2.0 / 30.0 | 2,819 | 1129 |
| p16_m_share_v2 | m_share | 0 | – | – / – / – | – | 350 | 33.4 | 5.0 / 10.0 | 1,992 | 1400 |

## H1b wait loops (model arms; exploratory, added after the freeze)

A run is a stretch of consecutive wait-only rounds within one turn. "Crowded" means the status line the model read met its harness twin's rule (share: ahead + own > 0.9 × pool; stock: a request waiting for KV memory, or KV in use ≥ 92%). GPU time is a share of the window's wall time, split like the H5 columns; other calls are compaction summaries and one-shot helper calls.

| cell | arm | wait-only calls | runs | sessions with a run ≥ 10 | run length p50 / p90 / max | wait-only calls in runs ≥ 10 | runs ended: resumed / budget / last | crowded: at run start / wait-only calls / work calls | GPU: wait-only calls (evicted recompute) / work calls / other calls |
|---|---|---|---|---|---|---|---|---|---|
| p16_m_stock | m_stock | 525 | 45 | 8 | 1 / 64 / 97 | 93.0% | 33 / 2 / 10 | 91.1% / 83.4% / 78.7% | 54.1% (50.7%) / 42.8% / 3.1% |
| p16_m_share | m_share | 502 | 47 | 8 | 1 / 53 / 97 | 92.0% | 38 / 3 / 6 | 27.7% / 25.1% / 23.8% | 29.1% (27.0%) / 67.9% / 3.0% |
| p16_m_stock_v2 | m_stock | 473 | 30 | 9 | 1 / 63 / 98 | 95.6% | 21 / 0 / 9 | 90.0% / 85.0% / 81.5% | 51.1% (47.5%) / 47.1% / 1.8% |
| p16_m_share_v2 | m_share | 351 | 9 | 7 | 41 / 77 / 91 | 100.0% | 1 / 1 / 7 | 55.6% / 8.5% / 29.1% | 27.3% (25.7%) / 69.6% / 3.1% |

## H2 decisions and oscillation

| cell | arm | crowded at check | bunched releases (< 2 s) | waiting queue mean | autocorrelation 10 / 30 / 60 s |
|---|---|---|---|---|---|
| p16_none | none | – | – | 3.2 | 0.66 / 0.55 / 0.48 |
| p16_m_stock | m_stock | – | – | 3.6 | 0.39 / 0.51 / 0.35 |
| p16_m_share | m_share | – | – | 2.6 | 0.47 / 0.42 / 0.35 |
| p16_m_stock_v2 | m_stock | – | – | 2.9 | 0.23 / 0.05 / 0.27 |
| p16_m_share_v2 | m_share | – | – | 2.9 | 0.35 / 0.35 / 0.18 |

## H3 prefix survival

| cell | arm | holds / waits with a share reading | prefix kept p50 / mean | work calls: hit / ideal |
|---|---|---|---|---|
| p16_none | none | 0 | – / – | 19.3% |
| p16_m_stock | m_stock | 0 | – / – | 25.5% |
| p16_m_share | m_share | 503 | 100.0% / 57.8% | 45.5% |
| p16_m_stock_v2 | m_stock | 0 | – / – | 27.2% |
| p16_m_share_v2 | m_share | 350 | 3.8% / 31.2% | 38.8% |

## H4 fairness and H6 overhead

| cell | arm | sessions | Jain index (work calls) | max hold + wait per session (s) | status requests/s | status age p50 / p99 (ms) |
|---|---|---|---|---|---|---|
| p16_none | none | 22 | 0.641 | 0 | 0.00 | – / – |
| p16_m_stock | m_stock | 17 | 0.488 | 750 | 1.14 | 250 / 496 |
| p16_m_share | m_share | 19 | 0.526 | 1,290 | 1.59 | 338 / 733 |
| p16_m_stock_v2 | m_stock | 19 | 0.558 | 900 | 1.14 | 253 / 496 |
| p16_m_share_v2 | m_share | 24 | 0.510 | 720 | 1.24 | 300 / 719 |
