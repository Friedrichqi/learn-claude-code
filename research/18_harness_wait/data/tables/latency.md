# Completion time per task, topic 18 (wt_latency.py; exploratory, added 2026-10-29)

A task entry is one turn (one task prompt). Completion time = turn_start to turn_end, including queueing, model calls, tools, model waits, harness holds and gate waits. Kaplan-Meier over turns of counted sessions started in the window, censored at its end; seconds. Mean: area under the curve up to the longest follow-up (≥ = the curve does not reach 0, so a lower bound; * = under 1 % left). "> t" = not reached by the longest follow-up t. † = fewer than 5 turns expected beyond the quantile. [95 %]: spec-cluster bootstrap, 2,000 draws; it shows sampling noise only (see the A/A row in topic 18). Ratios: against `none` at the same N.

## L1 · Completion time per task

| cell | turns (specs) | ended normally | capped | cut | refused (left out) | mean [95 %] | p50 [95 %] | p95 [95 %] | p99 | mean ÷ none | p50 ÷ none | p95 ÷ none |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| w08_none | 201 (60) | 108 | 85 | 8 | 13 | 184 [142, 233] | 118 [83, 143] | 562 [391, 770] | 2,349† [664, 2,626] | — | — | — |
| w08_m_stock | 166 (52) | 66 | 93 | 7 | 14 | 229 [189, 275] | 172 [134, 204] | 664 [559, 1,018] | 1,253† [686, 1,721] | 1.31 [1.00, 1.75] | 1.45 [1.09, 2.27] | 1.18 [0.78, 2.37] |
| w08_m_share | 158 (49) | 71 | 79 | 8 | 14 | 244* [192, 305] | 167 [147, 208] | 915 [545, 1,146] | 1,204† [819, ∞] | 1.38 [1.00, 1.90] | 1.42 [1.12, 2.30] | 1.63 [0.85, 2.78] |
| w16_none | 56 (22) | 31 | 12 | 13 | 0 | ≥ 1,498 | 1,094 [506, 2,176] | > 4,977 | n/a | — | — | — |
| w16_none_aa | 55 (24) | 28 | 12 | 15 | 0 | ≥ 1,644 | 1,984 [763, 2,255] | 3,584† [2,569, ∞] | n/a | 1.15 [0.81, 1.65] | 1.81 [0.59, 3.84] | — |
| w16_m_stock | 37 (17) | 5 | 19 | 13 | 1 | 2,171 [1,825, 2,635] | 2,029 [1,900, 2,442] | 4,151† [2,890, 4,465] | n/a | 1.49 [1.06, 2.14] | 1.85 [0.97, 4.15] | — |
| w16_m_share | 48 (19) | 14 | 18 | 16 | 0 | ≥ 1,844 | 2,164 [959, 2,435] | 2,999† [2,565, ∞] | n/a | 1.31 [0.90, 1.90] | 1.98 [0.95, 4.19] | — |
| w16_h_backoff | 123 (42) | 62 | 46 | 15 | 2 | 726 [581, 880] | 641 [245, 760] | 2,644 [1,763, 3,077] | 3,077† [2,644, 3,077] | 0.53 [0.37, 0.77] | 0.59 [0.20, 1.29] | — |
| w16_h_share | 159 (52) | 90 | 53 | 16 | 8 | 531 [444, 630] | 378 [319, 409] | 1,738 [1,033, 2,641] | 2,680† [2,549, ∞] | 0.41 [0.29, 0.57] | 0.34 [0.17, 0.71] | — |
| w16_sgate | 195 (60) | 102 | 78 | 15 | 4 | 422* [372, 479] | 337 [296, 379] | 1,032 [731, 1,285] | 2,128† [1,174, ∞] | 0.36 [0.26, 0.48] | 0.31 [0.16, 0.65] | — |
| x16_h_share_start | 188 (61) | 99 | 76 | 13 | 6 | 443 [379, 523] | 322 [274, 369] | 1,057 [841, 2,129] | 2,839† [1,251, ∞] | 0.33 [0.24, 0.47] | 0.29 [0.15, 0.62] | — |
| w32_none | 97 (41) | 60 | 5 | 32 | 0 | ≥ 1,875 | 776 [598, 1,580] | > 5,008 | n/a | — | — | — |
| w32_m_stock | 42 (30) | 8 | 5 | 29 | 0 | ≥ 3,985 | 5,229 [4,831, ∞] | > 5,292 | n/a | 2.05 [1.47, 2.87] | — | — |
| w32_m_share | 46 (32) | 15 | 2 | 29 | 0 | ≥ 3,530 | 5,227 [1,954, ∞] | > 5,274 | n/a | 1.81 [1.27, 2.58] | — | — |
| w32_h_backoff | 107 (42) | 53 | 23 | 31 | 0 | 1,710 [1,374, 2,106] | 1,339 [653, 2,703] | 4,329 [3,505, 5,051] | 5,051† [3,927, 5,051] | 0.91 [0.65, 1.27] | 1.73 [0.61, 3.83] | — |
| w32_h_share | 116 (42) | 51 | 34 | 31 | 1 | 1,460 [1,325, 1,600] | 1,362 [1,271, 1,655] | 2,819 [2,312, 3,272] | 3,272† [2,802, 3,272] | 1.02 [0.81, 1.27] | 1.75 [0.88, 2.50] | — |
| w32_sgate | 208 (70) | 118 | 61 | 29 | 3 | 788 [737, 845] | 727 [680, 776] | 1,274 [1,093, 1,942] | 2,306† [1,459, 3,436] | 0.53 [0.43, 0.66] | 0.94 [0.48, 1.26] | — |

Capped: the turn ended at its 100-call cap, the session's 300-call cap, or a prompt overflow. Refused: the session's budget was already spent, so the turn ended at once without a call.

## L2 · Cross-checks

| cell | KM mean | Little's law W (departures; from s) | naive mean of finished turns | stored KM p50 / p95 (reproduced) | normal-only p50 | all-sessions p50 | p50, first / second half of the window |
|---|---|---|---|---|---|---|---|
| w08_none | 184 | 180 (200; 0) | 176 | 100 / 429 | 221 | 118 | 138 / 88 |
| w08_m_stock | 229 | 227 (165; 0) | 223 | 161 / 644 | 686 | 172 | 165 / 182 |
| w08_m_share | 244* | 239 (157; 0) | 224 | 157 / 819 | 500 | 167 | 183 / 149 |
| w16_none | ≥ 1,498 | 1,613 (50; 260) | 1,014 | 1,094 / — | 1,223 | 1,094 | 895 / 1,872 |
| w16_none_aa | ≥ 1,644 | 1,612 (49; 371) | 1,341 | 1,984 / 3,584 | 2,255 | 1,984 | 1,011 / 1,984 |
| w16_m_stock | 2,171 | 2,384 (33; 403) | 2,089 | 2,029 / 4,151 | > 4,465 | 2,029 | 2,125 / 1,657 |
| w16_m_share | ≥ 1,844 | 2,037 (40; 210) | 1,570 | 2,164 / 2,999 | > 3,663 | 2,164 | 2,150 / not reached |
| w16_h_backoff | 726 | 693 (114; 210) | 619 | 613 / 2,644 | 1,094 | 654 | 737 / 605 |
| w16_h_share | 531 | 529 (146; 234) | 463 | 350 / 1,418 | 457 | 378 | 444 / 321 |
| w16_sgate | 422* | 418 (175; 474) | 397 | 335 / 1,032 | 421 | 337 | 341 / 335 |
| x16_h_share_start | 443 | 427 (170; 465) | 401 | 314 / 1,057 | 421 | 322 | 340 / 274 |
| w32_none | ≥ 1,875 | 2,485 (53; 1,224) | 831 | 776 / — | 776 | 776 | 702 / 776 |
| w32_m_stock | ≥ 3,985 | 8,923 (15; 1,204) | 1,817 | 5,012 / — | > 5,292 | 5,229 | 5,012 / not reached |
| w32_m_share | ≥ 3,530 | 5,987 (23; 1,065) | 1,216 | 5,227 / — | > 5,274 | 5,227 | 5,227 / not reached |
| w32_h_backoff | 1,710 | 1,714 (77; 1,186) | 1,271 | 1,097 / 4,329 | 1,339 | 1,176 | 1,059 / 1,531 |
| w32_h_share | 1,460 | 1,907 (69; 1,211) | 1,245 | 1,278 / 2,819 | 2,312 | 1,345 | 1,262 / 2,237 |
| w32_sgate | 788 | 838 (159; 1,073) | 767 | 698 / 1,246 | 832 | 726 | 612 / 828 |

## L3 · By task family

| cell | EXPLAIN turns | EXPLAIN mean / p50 / p95 | MODIFY turns | MODIFY mean / p50 / p95 | standardized mean / p50 / p95 |
|---|---|---|---|---|---|
| w08_none | 106 | 128 / 119 / 301 | 95 | 253 / 114 / 1,031† | 190 / 118 / 599 |
| w08_m_stock | 92 | 215 / 192 / 603† | 74 | 242 / 148 / 800† | 229 / 168 / 664 |
| w08_m_share | 84 | ≥ 242 / 176 / 1,067† | 74 | 245 / 158 / 819† | 239 / 167 / 819 |
| w16_none | 29 | 1,332 / 1,260 / 2,924† | 27 | ≥ 1,735 / 1,094 / > 4,977 | 1,356 / 1,094 / not reached |
| w16_none_aa | 25 | 1,445 / 1,983 / 3,584† | 30 | ≥ 1,844 / 2,255 / > 4,091 | 1,616 / 1,984 / not reached |
| w16_m_stock | 21 | 2,303 / 2,125 / 4,465† | 16 | 1,866 / 1,750 / 2,890† | 2,084 / 2,008 / 4,151 |
| w16_m_share | 27 | 1,774 / 2,159 / 2,617† | 21 | ≥ 2,066 / 2,305 / > 3,663 | 1,865 / 2,217 / not reached |
| w16_h_backoff | 64 | ≥ 565 / 641 / 1,399† | 59 | 891 / 656 / 2,794† | 666 / 641 / 2,644 |
| w16_h_share | 80 | 431 / 319 / 1,072† | 79 | 634 / 456 / 2,641† | 533 / 378 / 1,418 |
| w16_sgate | 99 | 347 / 331 / 617† | 96 | ≥ 497 / 351 / 1,285† | 397 / 337 / 1,032 |
| x16_h_share_start | 97 | 369 / 321 / 786† | 91 | 525 / 326 / 2,129† | 447 / 322 / 1,057 |
| w32_none | 58 | ≥ 1,777 / 702 / > 5,008 | 39 | ≥ 1,980 / 1,098 / > 4,983 | 1,876 / 834 / not reached |
| w32_m_stock | 36 | ≥ 4,215 / 5,229 / > 5,292 | 6 | ≥ 2,243 / 1,095† / > 4,313 | 2,898 / 5,012 / not reached |
| w32_m_share | 36 | ≥ 4,028 / 5,227 / > 5,274 | 10 | ≥ 1,715 / 728 / > 4,382 | 2,562 / 2,547 / not reached |
| w32_h_backoff | 62 | 1,653 / 1,097 / 3,927† | 45 | 1,777 / 1,364 / 4,329† | 1,715 / 1,176 / 4,329 |
| w32_h_share | 65 | 1,153 / 1,260 / 2,114† | 51 | 1,895 / 2,058 / 3,272† | 1,524 / 1,414 / 2,886 |
| w32_sgate | 113 | 684 / 679 / 976 | 95 | 908 / 827 / 1,942† | 796 / 744 / 1,274 |

## L4 · Lead calls (end-to-end latency of one model call, s)

| cell | work calls received in the window | mean | p50 | p95 | p99 | queue p50 / p95 | prefill p50 | decode p50 | wait-only calls (p50) | in flight when their session was stopped (median age) | sent in the window: KM p50 / p95 / p99 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| w08_none | 10,070 | 3.4 | 1.5 | 5.6 | 32.1 | 0.0 / 0.0 | 0.0 | 1.0 | 0 (—) | 5 (70) | 1 / 6 / 32 |
| w08_m_stock | 9,846 | 3.0 | 1.7 | 6.4 | 20.0 | 0.0 / 0.0 | 0.1 | 1.2 | 848 (1.0) | 2 (22) | 2 / 6 / 19 |
| w08_m_share | 6,892 | 3.3 | 1.7 | 5.9 | 28.3 | 0.0 / 0.0 | 0.1 | 1.1 | 2179 (0.9) | 4 (85) | 1 / 5 / 21 |
| w16_none | 2,576 | 30.3 | 22.6 | 50.6 | 178.2 | 10.4 / 14.9 | 2.5 | 9.0 | 0 (—) | 14 (13) | 23 / 51 / 183 |
| w16_none_aa | 2,608 | 31.5 | 22.8 | 51.8 | 226.8 | 10.6 / 14.4 | 2.5 | 9.2 | 0 (—) | 15 (20) | 23 / 53 / 228 |
| w16_m_stock | 2,357 | 23.5 | 21.1 | 41.9 | 151.9 | 5.4 / 11.5 | 2.4 | 11.6 | 990 (20.0) | 14 (9) | 21 / 34 / 102 |
| w16_m_share | 2,287 | 27.6 | 24.0 | 38.2 | 132.7 | 7.6 / 13.4 | 2.6 | 12.8 | 748 (20.5) | 20 (14) | 23 / 36 / 82 |
| w16_h_backoff | 6,236 | 8.5 | 3.7 | 13.9 | 77.0 | 0.0 / 2.7 | 0.3 | 2.3 | 0 (—) | 10 (47) | 4 / 14 / 78 |
| w16_h_share | 7,255 | 6.8 | 2.6 | 15.9 | 61.3 | 0.0 / 1.7 | 0.1 | 1.8 | 0 (—) | 9 (19) | 3 / 16 / 65 |
| w16_sgate | 9,808 | 8.0 | 1.9 | 9.0 | 220.7 | 0.0 / 0.0 | 0.1 | 1.3 | 0 (—) | 15 (31) | 2 / 9 / 226 |
| x16_h_share_start | 9,144 | 5.1 | 2.4 | 10.1 | 42.2 | 0.0 / 0.0 | 0.1 | 1.7 | 0 (—) | 7 (286) | 2 / 10 / 43 |
| w32_none | 2,711 | 59.5 | 50.9 | 70.3 | 224.4 | 40.6 / 48.5 | 2.3 | 7.0 | 0 (—) | 47 (27) | 51 / 71 / 251 |
| w32_m_stock | 1,899 | 55.3 | 50.1 | 66.0 | 124.4 | 33.3 / 44.5 | 2.6 | 13.5 | 924 (50.8) | 48 (26) | 51 / 64 / 82 |
| w32_m_share | 1,978 | 56.8 | 51.1 | 61.5 | 232.2 | 35.6 / 44.6 | 2.6 | 12.2 | 759 (47.4) | 43 (25) | 50 / 60 / 105 |
| w32_h_backoff | 4,760 | 12.0 | 8.8 | 21.8 | 77.0 | 0.0 / 7.1 | 1.4 | 5.4 | 0 (—) | 22 (2) | 9 / 22 / 78 |
| w32_h_share | 5,901 | 9.2 | 3.7 | 19.9 | 88.9 | 0.0 / 7.0 | 0.2 | 2.6 | 0 (—) | 14 (76) | 4 / 20 / 105 |
| w32_sgate | 8,755 | 18.2 | 1.9 | 16.1 | 573.1 | 0.0 / 0.0 | 0.1 | 1.2 | 0 (—) | 39 (178) | 2 / 17 / 639 |

## L5 · What the stored turns/h counts

| cell | stored-definition turns/h | without killed warm-up turns | turn ends in the window by kind |
|---|---|---|---|
| w08_none | 142.0 | 142.0 | normal 112, refused 13, session cap 17, turn cap 71 |
| w08_m_stock | 119.3 | 119.3 | normal 66, refused 14, session cap 27, too long 1, turn cap 71 |
| w08_m_share | 114.0 | 114.0 | normal 73, refused 14, session cap 18, too long 5, turn cap 61 |
| w16_none | 37.3 | 35.3 | filler killed 3, normal 35, too long 2, turn cap 16 |
| w16_none_aa | 37.3 | 36.0 | filler killed 2, normal 35, turn cap 19 |
| w16_m_stock | 26.7 | 24.0 | filler killed 4, normal 6, refused 1, session cap 2, too long 1, turn cap 26 |
| w16_m_share | 32.0 | 27.3 | filler killed 7, normal 15, turn cap 26 |
| w16_h_backoff | 84.0 | 82.0 | filler killed 3, filler normal 1, normal 67, refused 2, session cap 7, turn cap 46 |
| w16_h_share | 110.0 | 109.3 | filler killed 1, normal 96, refused 8, session cap 9, too long 4, turn cap 47 |
| w16_sgate | 132.0 | 129.3 | filler killed 4, filler normal 1, normal 103, refused 4, session cap 12, too long 3, turn cap 71 |
| x16_h_share_start | 129.3 | 128.0 | filler killed 2, normal 105, refused 6, session cap 18, too long 1, turn cap 62 |
| w32_none | 64.0 | 51.3 | filler killed 19, normal 66, too long 3, turn cap 8 |
| w32_m_stock | 30.0 | 15.3 | filler killed 22, filler normal 2, filler turn cap 1, normal 9, too long 3, turn cap 8 |
| w32_m_share | 31.3 | 20.0 | filler killed 17, filler normal 1, normal 18, turn cap 11 |
| w32_h_backoff | 73.3 | 63.3 | filler killed 15, filler normal 5, normal 57, too long 1, turn cap 32 |
| w32_h_share | 88.7 | 76.0 | filler killed 19, filler normal 7, filler turn cap 11, normal 54, refused 1, session cap 2, turn cap 39 |
| w32_sgate | 153.3 | 144.0 | filler killed 14, filler normal 13, filler refused 1, filler session cap 1, filler turn cap 5, normal 125, refused 3, session cap 6, too long 5, turn cap 57 |

## L6 · Sessions (4 turns and 3 think pauses; counted sessions whose first turn started in the window)

| cell | sessions | finished | mean | p50 | p95 |
|---|---|---|---|---|---|
| w08_none | 53 | 45 | 745 | 735 | 1,456† |
| w08_m_stock | 44 | 36 | 934 | 815 | 2,075† |
| w08_m_share | 42 | 34 | 1,033 | 886 | 2,094† |
| w16_none | 11 | 4 | ≥ 3,738 | > 5,287 | > 5,287 |
| w16_none_aa | 10 | 2 | ≥ 4,413 | 4,080 | > 5,246 |
| w16_m_stock | 5 | 0 | ≥ 5,280 | > 5,280 | > 5,280 |
| w16_m_share | 10 | 2 | ≥ 4,688 | > 5,377 | > 5,377 |
| w16_h_backoff | 30 | 14 | ≥ 2,989 | 2,568 | > 5,159 |
| w16_h_share | 38 | 22 | ≥ 2,106 | 1,706 | > 4,104 |
| w16_sgate | 49 | 33 | ≥ 1,778 | 1,551 | 3,446† |
| x16_h_share_start | 49 | 33 | ≥ 1,694 | 1,666 | 2,957† |
| w32_none | 28 | 7 | ≥ 4,645 | > 5,374 | > 5,374 |
| w32_m_stock | 24 | 1 | ≥ 5,255 | > 5,368 | > 5,368 |
| w32_m_share | 20 | 1 | ≥ 5,182 | > 5,367 | > 5,367 |
| w32_h_backoff | 28 | 5 | ≥ 4,733 | > 5,395 | > 5,395 |
| w32_h_share | 31 | 5 | ≥ 4,780 | > 5,079 | > 5,079 |
| w32_sgate | 56 | 26 | 3,194 | 3,009 | 4,426† |

## Reproduction check

The 'old' sample (wt_analyze.turn_latencies on the traces) against the stored H5 values (KM p50, p95, turns started, censored share), the stored call p50 and mean, and the stored turns/h: all match.
