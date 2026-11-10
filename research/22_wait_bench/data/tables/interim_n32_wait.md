# Topic 22: the KV-threshold `wait` prompt with 4-32 concurrent MiniMax-M2.7 agents (wb_analyze.py)

Seconds unless noted. request = one model call (send -> full reply); entry = one task's agent phase (model calls, tools, waits); window = the full-N window (N agents at once until the queue is empty); 95 % bootstrap intervals (tasks resampled; throughput: 10-min blocks).

## Runs

| run | N | condition | tasks | window (min) | agents in window | run (min) | limit hits | call errors | pass rate |
|---|---|---|---|---|---|---|---|---|---|
| `n32_wait` | 32 | `wait` | 96 | 34.1 | 32.0 | 115.2 | 7 | 0 | 74 % |

## Request latency in the window (s)

| run | n | avg | p50 | p95 | p99 |
|---|---|---|---|---|---|
| `n32_wait` | 2384 | 13.5 [11.3, 16.0] | 7.6 [6.6, 8.8] | 42.5 [33.5, 52.9] | 90.4 [66.8, 129.2] |

## Entry end-to-end latency (s)

| run | n | avg | p50 | p95 | p99 |
|---|---|---|---|---|---|
| `n32_wait` | 96 | 947.3 [763.0, 1158.6] | 577.8 [436.0, 798.2] | 3186.5 [2153.7, 3600.0] | 3684.7 [3124.8, 5292.9] |

## Throughput in the window

| run | requests/h | tokens/s | generation tokens/s | prefix hit rate | preemptions/min | KV mean | time at KV >= 80 % |
|---|---|---|---|---|---|---|---|
| `n32_wait` | 4201 [2628, 5736] | 20593 [16740, 23741] | 353 [227, 453] | 0.300 | 0.4 | 0.848 | 71 % |

## Waits

| run | calls with wait | wait-only calls | wait calls per task | seconds waited per task | tasks that waited |
|---|---|---|---|---|---|
| `n32_wait` | 29.0 % | 16.9 % | 12.10 | 354.3 | 91 |
