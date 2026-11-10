# Topic 22: the KV-threshold `wait` prompt with 4-32 concurrent MiniMax-M2.7 agents (wb_analyze.py)

Seconds unless noted. request = one model call (send -> full reply); entry = one task's agent phase (model calls, tools, waits); window = the full-N window (N agents at once until the queue is empty); 95 % bootstrap intervals (tasks resampled; throughput: 10-min blocks).

## Runs

| run | N | condition | tasks | window (min) | agents in window | run (min) | limit hits | call errors | pass rate |
|---|---|---|---|---|---|---|---|---|---|
| `n32_none` | 32 | `none` | 96 | 35.7 | 32.0 | 70.9 | 8 | 0 | 70 % |
| `n32_wait` | 32 | `wait` | 96 | 34.1 | 32.0 | 115.2 | 7 | 0 | 74 % |

## Request latency in the window (s)

| run | n | avg | p50 | p95 | p99 |
|---|---|---|---|---|---|
| `n32_none` | 2256 | 24.9 [22.9, 27.5] | 19.1 [18.2, 20.7] | 56.3 [46.9, 69.3] | 115.4 [85.0, 175.2] |
| `n32_wait` | 2384 | 13.5 [11.3, 16.0] | 7.6 [6.6, 8.8] | 42.5 [33.5, 52.9] | 90.4 [66.8, 129.2] |

## Entry end-to-end latency (s)

| run | n | avg | p50 | p95 | p99 |
|---|---|---|---|---|---|
| `n32_none` | 96 | 951.5 [805.2, 1113.7] | 660.1 [561.5, 873.5] | 2297.4 [1800.0, 3600.0] | 3600.0 [2321.4, 3600.0] |
| `n32_wait` | 96 | 947.3 [763.0, 1158.6] | 577.8 [436.0, 798.2] | 3186.5 [2153.7, 3600.0] | 3684.7 [3124.8, 5292.9] |

## Throughput in the window

| run | requests/h | tokens/s | generation tokens/s | prefix hit rate | preemptions/min | KV mean | time at KV >= 80 % |
|---|---|---|---|---|---|---|---|
| `n32_none` | 3770 [3492, 4482] | 19256 [18658, 20866] | 343 [334, 388] | 0.087 | 1.0 | 0.918 | 93 % |
| `n32_wait` | 4201 [2628, 5736] | 20593 [16740, 23741] | 353 [227, 453] | 0.300 | 0.4 | 0.848 | 71 % |

## Waits

| run | calls with wait | wait-only calls | wait calls per task | seconds waited per task | tasks that waited |
|---|---|---|---|---|---|
| `n32_none` | 0.0 % | 0.0 % | 0.00 | 0.0 | 0 |
| `n32_wait` | 29.0 % | 16.9 % | 12.10 | 354.3 | 91 |

## wait − none at each N (paired by task)

| N | request avg | request p50 | request p95 | request p99 | entry avg | entry p50 | entry p95 | entry p99 | requests/h ratio | tokens/s ratio | pass rate (points) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 32 | -11.5 [-13.4, -9.6] | -11.5 [-12.9, -10.5] | -13.7 [-23.0, -3.0] | -24.8 [-63.2, 10.6] | -4.2 [-172.8, 167.0] | -82.3 [-266.8, 136.6] | 889.2 [-413.5, 1531.1] | 84.6 [-145.4, 2602.6] | 1.114 [0.669, 1.491] | 1.069 [0.863, 1.232] | 4.2 [-3.1, 11.5] |
