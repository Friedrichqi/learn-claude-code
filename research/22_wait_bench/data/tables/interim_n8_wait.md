# Topic 22: the KV-threshold `wait` prompt with 4-32 concurrent MiniMax-M2.7 agents (wb_analyze.py)

Seconds unless noted. request = one model call (send -> full reply); entry = one task's agent phase (model calls, tools, waits); window = the full-N window (N agents at once until the queue is empty); 95 % bootstrap intervals (tasks resampled; throughput: 10-min blocks).

## Runs

| run | N | condition | tasks | window (min) | agents in window | run (min) | limit hits | call errors | pass rate |
|---|---|---|---|---|---|---|---|---|---|
| `n8_wait_s1` | 8 | `wait` | 96 | 77.3 | 8.0 | 165.7 | 8 | 0 | 67 % |
| `n8_wait` | 8 | `wait` | 96 | 60.0 | 8.0 | 115.3 | 4 | 0 | 73 % |

## Request latency in the window (s)

| run | n | avg | p50 | p95 | p99 |
|---|---|---|---|---|---|
| `n8_wait_s1` | 3195 | 6.7 [5.1, 8.4] | 2.7 [2.2, 3.3] | 25.6 [17.9, 32.7] | 52.5 [40.2, 69.2] |
| `n8_wait` | 2840 | 7.0 [5.2, 9.4] | 3.0 [2.7, 3.6] | 27.8 [17.0, 39.0] | 57.3 [33.9, 87.3] |

## Entry end-to-end latency (s)

| run | n | avg | p50 | p95 | p99 |
|---|---|---|---|---|---|
| `n8_wait_s1` | 96 | 496.0 [324.2, 693.6] | 165.3 [133.2, 219.6] | 1866.7 [1300.0, 3600.0] | 3720.2 [1926.8, 6002.4] |
| `n8_wait` | 96 | 363.8 [250.3, 507.2] | 158.5 [125.8, 221.4] | 1198.9 [612.2, 3542.9] | 3600.0 [1096.7, 3600.0] |

## Throughput in the window

| run | requests/h | tokens/s | generation tokens/s | prefix hit rate | preemptions/min | KV mean | time at KV >= 80 % |
|---|---|---|---|---|---|---|---|
| `n8_wait_s1` | 2476 [1957, 2931] | 14386 [11953, 16228] | 307 [278, 333] | 0.544 | 0.0 | 0.437 | 4 % |
| `n8_wait` | 2836 [2411, 3412] | 15971 [13502, 18377] | 338 [317, 361] | 0.576 | 0.0 | 0.524 | 4 % |

## Waits

| run | calls with wait | wait-only calls | wait calls per task | seconds waited per task | tasks that waited |
|---|---|---|---|---|---|
| `n8_wait_s1` | 1.5 % | 1.0 % | 0.58 | 64.0 | 11 |
| `n8_wait` | 1.0 % | 0.5 % | 0.34 | 4.0 | 18 |
