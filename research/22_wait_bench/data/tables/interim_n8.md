# Topic 22: the KV-threshold `wait` prompt with 4-32 concurrent MiniMax-M2.7 agents (wb_analyze.py)

Seconds unless noted. request = one model call (send -> full reply); entry = one task's agent phase (model calls, tools, waits); window = the full-N window (N agents at once until the queue is empty); 95 % bootstrap intervals (tasks resampled; throughput: 10-min blocks).

## Runs

| run | N | condition | tasks | window (min) | agents in window | run (min) | limit hits | call errors | pass rate |
|---|---|---|---|---|---|---|---|---|---|
| `n8_none` | 8 | `none` | 96 | 54.5 | 8.0 | 111.2 | 5 | 3 | 76 % |
| `n8_wait` | 8 | `wait` | 96 | 60.0 | 8.0 | 115.3 | 4 | 0 | 73 % |

## Request latency in the window (s)

| run | n | avg | p50 | p95 | p99 |
|---|---|---|---|---|---|
| `n8_none` | 2784 | 5.4 [4.1, 6.8] | 2.3 [2.0, 2.8] | 19.7 [13.9, 23.6] | 36.3 [29.9, 42.6] |
| `n8_wait` | 2840 | 7.0 [5.2, 9.4] | 3.0 [2.7, 3.6] | 27.8 [17.0, 39.0] | 57.3 [33.9, 87.3] |

## Entry end-to-end latency (s)

| run | n | avg | p50 | p95 | p99 |
|---|---|---|---|---|---|
| `n8_none` | 96 | 329.1 [219.3, 461.6] | 140.2 [123.7, 173.0] | 1645.4 [576.2, 2250.0] | 3600.0 [1604.2, 3600.0] |
| `n8_wait` | 96 | 363.8 [250.3, 507.2] | 158.5 [125.8, 221.4] | 1198.9 [612.2, 3542.9] | 3600.0 [1096.7, 3600.0] |

## Throughput in the window

| run | requests/h | tokens/s | generation tokens/s | prefix hit rate | preemptions/min | KV mean | time at KV >= 80 % |
|---|---|---|---|---|---|---|---|
| `n8_none` | 3061 [2768, 3323] | 17198 [16094, 18869] | 327 [298, 350] | 0.650 | 0.0 | 0.370 | 1 % |
| `n8_wait` | 2836 [2411, 3412] | 15971 [13502, 18377] | 338 [317, 361] | 0.576 | 0.0 | 0.524 | 4 % |

## Waits

| run | calls with wait | wait-only calls | wait calls per task | seconds waited per task | tasks that waited |
|---|---|---|---|---|---|
| `n8_none` | 0.0 % | 0.0 % | 0.00 | 0.0 | 0 |
| `n8_wait` | 1.0 % | 0.5 % | 0.34 | 4.0 | 18 |

## wait − none at each N (paired by task)

| N | request avg | request p50 | request p95 | request p99 | entry avg | entry p50 | entry p95 | entry p99 | requests/h ratio | tokens/s ratio | pass rate (points) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 8 | 1.7 [0.5, 3.3] | 0.7 [0.4, 1.1] | 8.0 [0.8, 18.0] | 21.1 [2.3, 49.2] | 34.7 [-26.6, 105.6] | 18.3 [-7.4, 65.2] | -446.5 [-868.2, 1742.8] | -0.0 [-475.6, 1800.0] | 0.926 [0.777, 1.150] | 0.929 [0.768, 1.073] | -3.1 [-10.4, 4.2] |
