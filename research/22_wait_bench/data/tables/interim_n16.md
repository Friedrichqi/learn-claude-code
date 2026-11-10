# Topic 22: the KV-threshold `wait` prompt with 4-32 concurrent MiniMax-M2.7 agents (wb_analyze.py)

Seconds unless noted. request = one model call (send -> full reply); entry = one task's agent phase (model calls, tools, waits); window = the full-N window (N agents at once until the queue is empty); 95 % bootstrap intervals (tasks resampled; throughput: 10-min blocks).

## Runs

| run | N | condition | tasks | window (min) | agents in window | run (min) | limit hits | call errors | pass rate |
|---|---|---|---|---|---|---|---|---|---|
| `n16_none` | 16 | `none` | 96 | 51.4 | 16.0 | 86.7 | 6 | 1 | 69 % |
| `n16_wait` | 16 | `wait` | 96 | 45.5 | 16.0 | 89.1 | 6 | 0 | 69 % |

## Request latency in the window (s)

| run | n | avg | p50 | p95 | p99 |
|---|---|---|---|---|---|
| `n16_none` | 3075 | 13.8 [11.4, 16.8] | 7.7 [6.0, 9.6] | 44.7 [34.1, 55.7] | 91.6 [68.3, 122.9] |
| `n16_wait` | 2723 | 8.8 [7.2, 10.8] | 3.8 [3.4, 4.4] | 31.5 [24.0, 44.9] | 62.2 [49.9, 83.9] |

## Entry end-to-end latency (s)

| run | n | avg | p50 | p95 | p99 |
|---|---|---|---|---|---|
| `n16_none` | 96 | 636.2 [490.8, 797.3] | 304.5 [249.7, 381.9] | 2066.3 [1800.0, 3193.0] | 3478.3 [2043.2, 3600.0] |
| `n16_wait` | 96 | 561.2 [411.8, 728.3] | 241.1 [186.5, 280.3] | 2147.8 [1751.5, 3229.4] | 3600.0 [2106.3, 3600.0] |

## Throughput in the window

| run | requests/h | tokens/s | generation tokens/s | prefix hit rate | preemptions/min | KV mean | time at KV >= 80 % |
|---|---|---|---|---|---|---|---|
| `n16_none` | 3573 [2616, 4660] | 19239 [16203, 23105] | 378 [311, 456] | 0.356 | 0.4 | 0.852 | 71 % |
| `n16_wait` | 3580 [2445, 4869] | 19252 [16093, 22813] | 371 [283, 469] | 0.488 | 0.1 | 0.731 | 35 % |

## Waits

| run | calls with wait | wait-only calls | wait calls per task | seconds waited per task | tasks that waited |
|---|---|---|---|---|---|
| `n16_none` | 0.0 % | 0.0 % | 0.00 | 0.0 | 0 |
| `n16_wait` | 13.4 % | 8.2 % | 4.96 | 118.6 | 75 |

## wait − none at each N (paired by task)

| N | request avg | request p50 | request p95 | request p99 | entry avg | entry p50 | entry p95 | entry p99 | requests/h ratio | tokens/s ratio | pass rate (points) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 16 | -5.0 [-7.3, -3.0] | -3.9 [-5.5, -2.3] | -13.2 [-22.5, -2.2] | -29.4 [-56.5, -4.7] | -75.0 [-201.7, 47.2] | -63.5 [-143.6, -10.8] | 81.6 [-1046.3, 1099.4] | 121.7 [-1347.3, 1311.3] | 1.002 [0.619, 1.546] | 1.001 [0.775, 1.276] | 0.0 [-8.3, 8.3] |
