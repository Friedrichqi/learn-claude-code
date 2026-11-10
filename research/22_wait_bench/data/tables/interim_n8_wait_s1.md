# Topic 22: the KV-threshold `wait` prompt with 4-32 concurrent MiniMax-M2.7 agents (wb_analyze.py)

Seconds unless noted. request = one model call (send -> full reply); entry = one task's agent phase (model calls, tools, waits); window = the full-N window (N agents at once until the queue is empty); 95 % bootstrap intervals (tasks resampled; throughput: 10-min blocks).

## Runs

| run | N | condition | tasks | window (min) | agents in window | run (min) | limit hits | call errors | pass rate |
|---|---|---|---|---|---|---|---|---|---|
| `n8_wait` | 8 | `wait` | 96 | 77.3 | 8.0 | 165.7 | 8 | 0 | 67 % |

## Request latency in the window (s)

| run | n | avg | p50 | p95 | p99 |
|---|---|---|---|---|---|
| `n8_wait` | 3195 | 6.7 [5.1, 8.3] | 2.7 [2.2, 3.3] | 25.6 [17.7, 32.7] | 52.5 [39.8, 69.2] |

## Entry end-to-end latency (s)

| run | n | avg | p50 | p95 | p99 |
|---|---|---|---|---|---|
| `n8_wait` | 96 | 496.0 [333.2, 702.3] | 165.3 [135.2, 218.6] | 1866.7 [1602.7, 3600.0] | 3720.2 [1926.8, 6002.4] |

## Throughput in the window

| run | requests/h | tokens/s | generation tokens/s | prefix hit rate | preemptions/min | KV mean | time at KV >= 80 % |
|---|---|---|---|---|---|---|---|
| `n8_wait` | 2476 [1988, 2925] | 14386 [12143, 16228] | 307 [279, 334] | 0.544 | 0.0 | 0.437 | 4 % |

## Waits

| run | calls with wait | wait-only calls | wait calls per task | seconds waited per task | tasks that waited |
|---|---|---|---|---|---|
| `n8_wait` | 1.5 % | 1.0 % | 0.58 | 64.0 | 11 |
