# Completion time per task, topic 19 (wt_latency.py; exploratory, added 2026-10-29)

A task entry is one turn (one task prompt). Completion time = turn_start to turn_end, including queueing, model calls, tools, model waits, harness holds and gate waits. Kaplan-Meier over turns of counted sessions started in the window, censored at its end; seconds. Mean: area under the curve up to the longest follow-up (≥ = the curve does not reach 0, so a lower bound; * = under 1 % left). "> t" = not reached by the longest follow-up t. † = fewer than 5 turns expected beyond the quantile. [95 %]: spec-cluster bootstrap, 2,000 draws; it shows sampling noise only (see the A/A row in topic 18). Ratios: against `none` at the same N.

## L1 · Completion time per task

| cell | turns (specs) | ended normally | capped | cut | refused (left out) | mean [95 %] | p50 [95 %] | p95 [95 %] | p99 | mean ÷ none | p50 ÷ none | p95 ÷ none |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| w08_none | 478 (124) | 470 | 3 | 5 | 0 | 56 [51, 61] | 38 [32, 43] | 192 [142, 233] | 306† [258, 330] | — | — | — |
| w08_m_tools | 452 (117) | 444 | 1 | 7 | 0 | 62 [57, 68] | 40 [37, 43] | 207 [166, 256] | 380† [290, 429] | 1.10 [0.98, 1.23] | 1.05 [0.90, 1.22] | 1.08 [0.78, 1.56] |
| w16_none | 410 (112) | 399 | 0 | 11 | 0 | 175 [159, 192] | 110 [99, 121] | 559 [422, 783] | 973† [894, 1,020] | — | — | — |
| w16_sgate | 599 (128) | 585 | 0 | 14 | 0 | 111 [106, 116] | 93 [89, 98] | 253 [215, 283] | 399 [330, 481] | 0.70 [0.64, 0.76] | 0.84 [0.75, 0.96] | 0.45 [0.31, 0.63] |
| w16_m_wait | 431 (120) | 416 | 1 | 14 | 0 | 165 [150, 180] | 105 [95, 118] | 562 [427, 687] | 833† [741, 1,093] | 0.94 [0.83, 1.07] | 0.95 [0.83, 1.13] | 1.00 [0.63, 1.50] |
| w16_m_tools | 383 (106) | 368 | 0 | 15 | 0 | 192* [170, 215] | 119 [106, 133] | 649 [496, 819] | 1,114† [927, ∞] | 1.10 [0.94, 1.26] | 1.08 [0.92, 1.28] | 1.16 [0.76, 1.76] |
| w32_none | 383 (117) | 354 | 0 | 29 | 0 | 438 [389, 490] | 283 [246, 314] | 1,690 [1,123, 1,964] | 2,956† [1,964, ∞] | — | — | — |
| w32_sgate | 544 (128) | 515 | 3 | 26 | 0 | 280 [273, 288] | 265 [260, 273] | 451 [403, 485] | 725 [560, 830] | 0.78 [0.73, 0.85] | 0.94 [0.84, 1.08] | 0.27 [0.23, 0.40] |
| w32_m_wait | 368 (112) | 339 | 0 | 29 | 0 | 460* [400, 523] | 267 [238, 300] | 1,780 [1,302, 2,131] | 3,168† [2,284, ∞] | 1.04 [0.87, 1.22] | 0.94 [0.82, 1.12] | 1.05 [0.76, 1.57] |
| w32_m_tools | 378 (115) | 349 | 0 | 29 | 0 | 439* [387, 499] | 271 [248, 307] | 1,424 [1,069, 1,789] | 2,969† [1,820, ∞] | 0.98 [0.83, 1.16] | 0.96 [0.83, 1.14] | 0.84 [0.61, 1.31] |
| x64_m_wait | 200 (88) | 140 | 0 | 60 | 0 | ≥ 821 | 593 [506, 766] | > 2,259 | > 2,259 | — | — | — |
| x128_m_wait | 263 (121) | 144 | 0 | 119 | 0 | ≥ 1,180 | 1,116 [1,002, 1,206] | > 2,351 | > 2,351 | — | — | — |

Capped: the turn ended at its 100-call cap, the session's 300-call cap, or a prompt overflow. Refused: the session's budget was already spent, so the turn ended at once without a call.

## L2 · Cross-checks

| cell | KM mean | Little's law W (departures; from s) | naive mean of finished turns | stored KM p50 / p95 (reproduced) | normal-only p50 | all-sessions p50 | p50, first / second half of the window |
|---|---|---|---|---|---|---|---|
| w08_none | 56 | 56 (477; 0) | 55 | 38 / 192 | 38 | 38 | 38 / 38 |
| w08_m_tools | 62 | 61 (450; 0) | 59 | 40 / 207 | 40 | 40 | 40 / 39 |
| w16_none | 175 | 174 (415; 0) | 169 | 110 / 559 | 110 | 110 | 115 / 107 |
| w16_sgate | 111 | 111 (596; 0) | 109 | 93 / 253 | 93 | 93 | 92 / 95 |
| w16_m_wait | 165 | 165 (432; 0) | 157 | 105 / 562 | 105 | 105 | 106 / 104 |
| w16_m_tools | 192* | 192 (383; 0) | 180 | 119 / 649 | 119 | 119 | 116 / 119 |
| w32_none | 438 | 422 (366; 176) | 378 | 281 / 1,690 | 283 | 283 | 278 / 283 |
| w32_sgate | 280 | 281 (548; 0) | 279 | 265 / 451 | 265 | 265 | 255 / 280 |
| w32_m_wait | 460* | 441 (355; 124) | 384 | 267 / 1,780 | 267 | 267 | 266 / 267 |
| w32_m_tools | 439* | 428 (361; 177) | 382 | 270 / 1,424 | 271 | 271 | 273 / 261 |
| x64_m_wait | ≥ 821 | 791 (171; 200) | 577 | 593 / — | 593 | 593 | 587 / 593 |
| x128_m_wait | ≥ 1,180 | 1,263 (207; 294) | 855 | 1,116 / — | 1,116 | 1,116 | 1,021 / not reached |

## L3 · By task family

| cell | EXPLAIN turns | EXPLAIN mean / p50 / p95 | MODIFY turns | MODIFY mean / p50 / p95 | standardized mean / p50 / p95 |
|---|---|---|---|---|---|
| w08_none | 239 | 38 / 31 / 84 | 239 | 74 / 53 / 250 | 56 / 38 / 192 |
| w08_m_tools | 223 | 40 / 34 / 83 | 229 | 83 / 56 / 289 | 62 / 39 / 190 |
| w16_none | 206 | 112 / 92 / 267 | 204 | 239 / 174 / 894 | 176 / 111 / 559 |
| w16_sgate | 299 | 89 / 86 / 146 | 300 | 133 / 107 / 301 | 111 / 93 / 253 |
| w16_m_wait | 214 | 107 / 88 / 262 | 217 | 221 / 149 / 731 | 164 / 105 / 562 |
| w16_m_tools | 190 | 128 / 104 / 287 | 193 | 256* / 165 / 927 | 179 / 119 / 649 |
| w32_none | 184 | 263 / 223 / 577 | 199 | 595 / 362 / 1,964 | 429 / 281 / 1,621 |
| w32_sgate | 268 | 257 / 256 / 350 | 276 | 303 / 278 / 539 | 280 / 265 / 451 |
| w32_m_wait | 173 | 268 / 210 / 653 | 195 | ≥ 634 / 345 / 2,284 | 357 / 265 / 1,749 |
| w32_m_tools | 187 | 284 / 228 / 710 | 191 | 593* / 364 / 1,820 | 405 / 271 / 1,424 |
| x64_m_wait | 87 | 560 / 421 / 1,533† | 113 | ≥ 1,021 / 796 / > 2,259 | 738 / 579 / not reached |
| x128_m_wait | 131 | ≥ 1,022 / 858 / > 2,351 | 132 | ≥ 1,350 / 1,238 / > 2,285 | 1,184 / 1,125 / not reached |

## L4 · Lead calls (end-to-end latency of one model call, s)

| cell | work calls received in the window | mean | p50 | p95 | p99 | queue p50 / p95 | prefill p50 | decode p50 | wait-only calls (p50) | in flight when their session was stopped (median age) | sent in the window: KM p50 / p95 / p99 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| w08_none | 5,419 | 4.8 | 2.3 | 17.7 | 37.2 | 0.0 / 0.0 | 0.1 | 2.1 | 0 (—) | 3 (4) | 2 / 18 / 37 |
| w08_m_tools | 4,801 | 5.6 | 2.7 | 21.3 | 41.6 | 0.0 / 0.0 | 0.1 | 2.4 | 0 (—) | 7 (4) | 3 / 21 / 42 |
| w16_none | 4,524 | 15.8 | 11.1 | 47.6 | 89.4 | 3.9 / 8.9 | 0.8 | 5.1 | 0 (—) | 8 (9) | 11 / 48 / 90 |
| w16_sgate | 6,265 | 10.4 | 3.4 | 48.9 | 76.3 | 0.0 / 39.7 | 0.1 | 2.6 | 0 (—) | 12 (48) | 3 / 49 / 77 |
| w16_m_wait | 4,415 | 15.9 | 11.2 | 47.2 | 88.7 | 3.9 / 9.1 | 0.8 | 5.3 | 0 (—) | 13 (7) | 11 / 48 / 89 |
| w16_m_tools | 3,917 | 18.3 | 12.6 | 52.9 | 93.5 | 4.9 / 10.8 | 0.9 | 5.6 | 0 (—) | 13 (12) | 13 / 53 / 94 |
| w32_none | 4,298 | 36.9 | 31.6 | 69.1 | 111.0 | 23.7 / 32.6 | 0.9 | 5.4 | 0 (—) | 28 (21) | 32 / 69 / 111 |
| w32_sgate | 6,154 | 24.9 | 3.7 | 207.4 | 263.4 | 0.0 / 202.9 | 0.1 | 2.9 | 0 (—) | 24 (70) | 4 / 206 / 263 |
| w32_m_wait | 4,053 | 38.9 | 33.9 | 74.0 | 114.2 | 25.5 / 34.4 | 0.9 | 5.5 | 3 (80.0) | 29 (19) | 34 / 74 / 114 |
| w32_m_tools | 3,892 | 40.7 | 35.6 | 75.7 | 113.5 | 26.8 / 36.3 | 0.9 | 5.8 | 0 (—) | 31 (19) | 36 / 76 / 115 |
| x64_m_wait | 1,793 | 81.3 | 78.0 | 118.6 | 157.0 | 68.0 / 81.7 | 0.8 | 5.3 | 1 (59.4) | 68 (33) | 79 / 119 / 159 |
| x128_m_wait | 1,867 | 156.8 | 160.0 | 207.0 | 244.9 | 151.4 / 182.6 | 0.8 | 4.8 | 0 (—) | 139 (76) | 161 / 209 / 250 |

## L5 · What the stored turns/h counts

| cell | stored-definition turns/h | without killed warm-up turns | turn ends in the window by kind |
|---|---|---|---|
| w08_none | 318.0 | 318.0 | normal 474, turn cap 3 |
| w08_m_tools | 300.0 | 300.0 | normal 449, turn cap 1 |
| w16_none | 276.7 | 276.7 | normal 415 |
| w16_sgate | 397.3 | 397.3 | normal 596 |
| w16_m_wait | 288.0 | 288.0 | normal 431, turn cap 1 |
| w16_m_tools | 255.3 | 255.3 | normal 383 |
| w32_none | 256.7 | 256.0 | filler killed 1, filler normal 1, normal 383 |
| w32_sgate | 365.3 | 365.3 | normal 545, turn cap 3 |
| w32_m_wait | 244.7 | 244.0 | filler killed 1, normal 366 |
| w32_m_tools | 250.7 | 248.0 | filler killed 4, normal 372 |
| x64_m_wait | 300.0 | 286.5 | filler killed 9, filler normal 1, normal 190 |
| x128_m_wait | 393.0 | 364.5 | filler killed 19, normal 243 |

## L6 · Sessions (4 turns and 3 think pauses; counted sessions whose first turn started in the window)

| cell | sessions | finished | mean | p50 | p95 |
|---|---|---|---|---|---|
| w08_none | 120 | 113 | 343 | 320 | 556 |
| w08_m_tools | 110 | 103 | 365 | 337 | 578 |
| w16_none | 104 | 88 | 839 | 744 | 1,458 |
| w16_sgate | 151 | 135 | 555 | 532 | 812 |
| w16_m_wait | 108 | 93 | 774 | 708 | 1,353 |
| w16_m_tools | 96 | 80 | ≥ 895 | 733 | 1,734† |
| w32_none | 90 | 60 | 1,950 | 1,499 | 4,657† |
| w32_sgate | 134 | 103 | 1,226 | 1,182 | 1,669 |
| w32_m_wait | 84 | 53 | ≥ 2,016 | 1,492 | > 4,909 |
| w32_m_tools | 93 | 61 | ≥ 1,845 | 1,573 | 4,202† |
| x64_m_wait | 37 | 3 | ≥ 2,221 | > 2,390 | > 2,390 |
| x128_m_wait | 34 | 0 | ≥ 2,393 | > 2,393 | > 2,393 |

## Reproduction check

The 'old' sample (wt_analyze.turn_latencies on the traces) against the stored H5 values (KM p50, p95, turns started, censored share), the stored call p50 and mean, and the stored turns/h: all match.
