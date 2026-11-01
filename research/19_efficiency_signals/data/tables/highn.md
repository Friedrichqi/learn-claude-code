# High-N follow-up: does MiniMax-M2.7 wait more when far more users compete? (ef_highn.py)

Pooled N ≤ 32 baseline (w16_m_wait + w32_m_wait): 3 of 8,468 lead calls carry `wait` = 0.04 % [0.01 %, 0.10 %].

| cell | N | lead calls | with `wait` | rate [95 %] | verdict | wait-only | reasoning mentions | ahead / pool p50 (p90) | requests waiting p50 (p90) | crowded share | hit rate | call p50 / p95 (s) | queue p50 / p95 (s) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| w16_m_wait | 16 | 4,415 | 0 | 0.00 % [0.00 %, 0.08 %] | still ignores it | 0 | 3 | 0.65 (1.20) | 3 (5) | 30.60 % | 17.28 % | 11.2 / 47.3 | 3.9 / 9.1 |
| w32_m_wait | 32 | 4,053 | 3 | 0.07 % [0.02 %, 0.22 %] | still ignores it | 3 | 6 | 1.46 (2.60) | 20 (21) | 67.04 % | 1.35 % | 33.9 / 74.7 | 25.5 / 34.4 |
| w16_none | 16 | 4,524 | 0 | — | — | 0 | 2 | — (—) | — (—) | — | 13.89 % | 11.1 / 47.7 | 3.9 / 8.9 |
| w32_none | 32 | 4,294 | 0 | — | — | 0 | 2 | — (—) | — (—) | — | 1.36 % | 31.6 / 69.1 | 23.8 / 32.7 |
| x64_m_wait | 64 | 1,778 | 1 | 0.06 % [0.00 %, 0.31 %] | still ignores it | 1 | 2 | 3.08 (5.34) | 51 (53) | 84.76 % | 1.39 % | 78.2 / 118.7 | 68.2 / 81.7 |
| x128_m_wait | 128 | 1,849 | 0 | 0.00 % [0.00 %, 0.20 %] | still ignores it | 0 | 0 | 5.67 (10.21) | 113 (116) | 91.62 % | 1.50 % | 160.2 / 207.1 | 151.6 / 182.6 |

Conditional `none` cell: not triggered.

