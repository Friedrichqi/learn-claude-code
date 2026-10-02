# 11_multiuser_kv replay tables

Each replay drives the recorded sessions of one live cell closed-loop (same N, same slot order, exact prompt ids, recorded output lengths and gaps) against a fresh server with one policy, and is measured over its window after a 10-minute warm-up.

## Replay of n16 (N=16)

| policy | calls | calls / h | turns / h | turns vs default | call p50 s | call p95 s | turn p50 s | turn p95 s | evicted / prompt | queue p50 s | preemptions | errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| default | 1554 | 1036.00 | 48.00 | 1.00 | 33.55 | 95.87 | 740.62 | 3143.27 | 0.81 | 16.47 | 6 | 0 |
| default2 | 1591 | 1060.67 | 48.67 | 1.01 | 32.66 | 92.34 | 752.02 | 3250.38 | 0.81 | 16.03 | 6 | 0 |
| prio | 2076 | 1384.00 | 59.33 | 1.24 | 15.13 | 113.59 | 626.41 | 3284.48 | 0.44 | 2.60 | 11 | 0 |
| pin10t | 1929 | 1286.00 | 60.00 | 1.25 | 32.65 | 71.00 | 450.38 | 3590.36 | 0.46 | 24.01 | 0 | 0 |
| gate | 4456 | 2970.67 | 105.33 | 2.19 | 2.87 | 22.03 | 114.77 | 1063.46 | 0.02 | 0.00 | 0 | 0 |
| mnbt2k | 1580 | 1053.33 | 46.00 | 0.96 | 36.15 | 83.24 | 773.46 | 3474.01 | 0.81 | 22.40 | 1 | 0 |
| sgate8 | 4405 | 2936.67 | 103.33 | 2.15 | 2.92 | 70.19 | 414.00 | 1341.92 | 0.02 | 0.00 | 0 | 0 |
| off128 | 4357 | 2904.67 | 108.67 | 2.26 | 9.86 | 36.08 | 279.87 | 1387.45 | 0.01 | 5.13 | 168 | 0 |

## Replay of n32 (N=32)

| policy | calls | calls / h | turns / h | turns vs default | call p50 s | call p95 s | turn p50 s | turn p95 s | evicted / prompt | queue p50 s | preemptions | errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| default | 1712 | 1141.33 | 76.67 | 1.00 | 85.62 | 127.11 | 1061.55 | 4777.61 | 0.79 | 62.57 | 0 | 0 |
| prio | 2462 | 1641.33 | 92.00 | 1.20 | 17.20 | 172.79 | 586.61 | 4021.08 | 0.43 | 2.53 | 8 | 8 |
| gate | 1791 | 1194.00 | 62.67 | 0.82 | 37.78 | 65.44 | 622.65 | 3794.29 | 0.72 | 13.29 | 3 | 0 |
| sgate8 | 5268 | 3512.00 | 138.00 | 1.80 | 3.84 | 68.75 | 768.89 | 1280.44 | 0.02 | 0.00 | 0 | 0 |
| sgate6 | 5096 | 3397.33 | 131.33 | 1.71 | 2.92 | 49.79 | 814.31 | 1268.10 | 0.02 | 0.00 | 0 | 0 |
| off128 | 1879 | 1252.67 | 70.67 | 0.92 | 87.05 | 124.87 | 840.93 | 5334.29 | 0.64 | 65.98 | 50 | 0 |

## Replay of pilot8 (N=8)

| policy | calls | calls / h | turns / h | turns vs default | call p50 s | call p95 s | turn p50 s | turn p95 s | evicted / prompt | queue p50 s | preemptions | errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| default | 2308 | 3048.68 | 146.62 | 1.00 | 1.84 | 21.15 | 65.40 | 692.20 | 0.01 | 0.00 | 0 | 0 |
| default2 | 2308 | 3047.61 | 146.57 | 1.00 | 1.82 | 20.77 | 65.18 | 691.93 | 0.01 | 0.00 | 0 | 0 |
| stock | 2308 | 3050.61 | 146.71 | 1.00 | 1.84 | 20.80 | 65.32 | 692.41 | – | – | 0 | 0 |

