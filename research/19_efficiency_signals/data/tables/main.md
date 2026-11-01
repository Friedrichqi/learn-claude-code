## T: throughput and efficiency (measurement window)

| cell | turns/h [95 %] | work lead calls/h | turn p50 / p95 (KM, s) | GPU-s per turn (busy x TP) | hit rate | GPU evicted / preempted | out tokens p50 / p95 | escalations |
|---|---|---|---|---|---|---|---|---|
| w16_sgate | 397.3 [378, 424] | 4,177 | 93 / 253 | 36.2 | 81.7 % | 4.8 % / 0.0 % | 160 / 1,431 | 0 |
| w16_none | 276.7 [190, 356] | 3,016 | 110 / 559 | 52.0 | 13.9 % | 37.0 % / 0.0 % | 158 / 1,345 | 0 |
| w32_none | 256.7 [199, 320] | 2,865 | 281 / 1,690 | 56.1 | 1.4 % | 40.8 % / 0.0 % | 158 / 1,341 | 0 |
| w32_sgate | 365.3 [313, 421] | 4,103 | 265 / 451 | 39.4 | 82.3 % | 4.4 % / 0.0 % | 173 / 1,472 | 0 |
| w16_m_tools | 255.3 [214, 298] | 2,611 | 119 / 649 | 56.4 | 6.6 % | 35.9 % / 0.0 % | 180 / 1,523 | 0 |
| w16_m_wait | 288.0 [240, 365] | 2,943 | 105 / 562 | 50.0 | 17.3 % | 34.5 % / 0.0 % | 174 / 1,463 | 0 |
| w32_m_wait | 244.7 [198, 294] | 2,702 | 267 / 1,780 | 58.9 | 1.3 % | 39.0 % / 0.0 % | 168 / 1,490 | 0 |
| w32_m_tools | 250.7 [205, 290] | 2,595 | 270 / 1,424 | 57.4 | 1.3 % | 37.7 % / 0.0 % | 178 / 1,510 | 0 |
| w08_none | 318.0 [266, 361] | 3,613 | 38 / 192 | 45.2 | 86.1 % | 1.5 % / 0.0 % | 162 / 1,348 | 0 |
| w08_m_tools | 300.0 [242, 347] | 3,201 | 40 / 207 | 47.9 | 84.9 % | 1.7 % / 0.0 % | 183 / 1,588 | 1 |

## R: decisions (completed turns / h; floor F = 10 % of none)

| contrast | gain [95 %] | R [95 %] | verdict | R on work calls |
|---|---|---|---|---|
| R1 m_wait@16 | 11.3 [-36.0, 62.0] | 0.09 [-0.41, 0.42] | neither | -0.06 |
| R1 m_tools@16 | -21.3 [-60.7, 22.0] | -0.18 [-0.71, 0.14] | neither | -0.35 |
| R1 m_wait@32 | -12.0 [-44.7, 20.7] | -0.11 [-0.52, 0.16] | neither | -0.13 |
| R1 m_tools@32 | -6.0 [-35.3, 23.3] | -0.06 [-0.41, 0.19] | neither | -0.22 |
| R2 m_tools − m_wait @16 | -32.7 [-67.3, 0.7] | — | no clear difference | — |
| R2 m_tools − m_wait @32 | 6.0 [-21.3, 35.3] | — | no clear difference | — |
| R3 m_tools − none @8 | -18.0 (floor 31.8; hit −1.1 points) | — | no harm | — |

## Q: quality guard

| cell | turns ended | ended normally | M turns (complete sessions) | M edits done |
|---|---|---|---|---|
| w16_sgate | 596 | 100.0 % | 300 | 99.0 % |
| w16_none | 415 | 100.0 % | 208 | 98.1 % |
| w32_none | 385 | 99.7 % | 180 | 96.7 % |
| w32_sgate | 548 | 99.5 % | 264 | 100.0 % |
| w16_m_tools | 383 | 100.0 % | 192 | 95.3 % |
| w16_m_wait | 432 | 99.8 % | 214 | 96.7 % |
| w32_m_wait | 367 | 99.7 % | 164 | 93.9 % |
| w32_m_tools | 376 | 98.9 % | 176 | 96.6 % |
| w08_none | 477 | 99.4 % | 230 | 97.8 % |
| w08_m_tools | 450 | 99.8 % | 220 | 98.2 % |

## B: behaviour (model arms)

| cell | tool calls per response (0/1/2/3/4+) | step-backs with work | overhead per 100 work | GPU on overhead | P(step-back) crowded / calm | crowded share | status reads per 100 work | step-backs after a read | reads after costly / cheap step | waits, yields (p50 / p95 s) |
|---|---|---|---|---|---|---|---|---|---|---|
| w16_m_tools | 383/2134/474/230/696 | — | 0.0 | 0.0 % | 0.0 % / 0.0 % | 34.8 % | 0.0 | — | 0.0 % / 0.0 % | 0, 0 (— / —) |
| w16_m_wait | 431/2409/502/255/818 | — | 0.0 | 0.0 % | 0.0 % / 0.0 % | 30.6 % | 0.0 | — | — / — | 0, 0 (— / —) |
| w32_m_wait | 366/2303/469/229/689 | 0.0 % | 0.1 | 0.2 % | 0.0 % / 0.1 % | 67.0 % | 0.0 | 0.0 % | — / — | 3, 0 (— / —) |
| w32_m_tools | 372/2165/452/237/666 | — | 0.0 | 0.0 % | 0.0 % / 0.0 % | 67.2 % | 0.0 | — | 0.0 % / 0.0 % | 0, 0 (— / —) |
| w08_m_tools | 450/2559/602/252/938 | — | 0.0 | 0.0 % | — / 0.0 % | 0.0 % | 0.0 | — | 0.0 % / 0.0 % | 0, 0 (— / —) |

| cell | tools per work response (crowded / calm) | output tokens per work call (crowded / calm) | calls per turn | exact re-reads per 100 reads |
|---|---|---|---|---|
| w16_m_tools | 3.04 / 2.23 | 188 / 184 | 10.1 | 0.7 |
| w16_m_wait | 2.88 / 2.22 | 172 / 176 | 10.2 | 0.7 |
| w32_m_wait | 2.54 / 1.76 | 170 / 165 | 10.1 | 0.7 |
| w32_m_tools | 2.75 / 1.96 | 187 / 180 | 9.5 | 0.4 |
| w08_m_tools | — / 2.58 | — / 188 | 10.3 | 0.8 |
