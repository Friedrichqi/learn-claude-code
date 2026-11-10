# Topic 22: the KV-threshold `wait` prompt with 4-32 concurrent MiniMax-M2.7 agents (wb_analyze.py)

Seconds unless noted. request = one model call (send -> full reply); entry = one task's agent phase (model calls, tools, waits); window = the full-N window (N agents at once until the queue is empty); 95 % bootstrap intervals (tasks resampled; throughput: 10-min blocks).

## Runs

| run | N | condition | tasks | window (min) | agents in window | run (min) | limit hits | call errors | pass rate |
|---|---|---|---|---|---|---|---|---|---|
| `n16_none` | 16 | `none` | 96 | 51.4 | 16.0 | 86.7 | 6 | 1 | 69 % |

## Request latency in the window (s)

| run | n | avg | p50 | p95 | p99 |
|---|---|---|---|---|---|
| `n16_none` | 3075 | 13.8 [11.4, 16.8] | 7.7 [6.0, 9.6] | 44.7 [34.1, 55.7] | 91.6 [68.3, 122.9] |

## Entry end-to-end latency (s)

| run | n | avg | p50 | p95 | p99 |
|---|---|---|---|---|---|
| `n16_none` | 96 | 636.2 [490.8, 797.3] | 304.5 [249.7, 381.9] | 2066.3 [1800.0, 3193.0] | 3478.3 [2043.2, 3600.0] |

## Throughput in the window

| run | requests/h | tokens/s | generation tokens/s | prefix hit rate | preemptions/min | KV mean | time at KV >= 80 % |
|---|---|---|---|---|---|---|---|
| `n16_none` | 3573 [2616, 4660] | 19239 [16203, 23105] | 378 [311, 456] | 0.356 | 0.4 | 0.852 | 71 % |

## Waits

| run | calls with wait | wait-only calls | wait calls per task | seconds waited per task | tasks that waited |
|---|---|---|---|---|---|
| `n16_none` | 0.0 % | 0.0 % | 0.00 | 0.0 | 0 |
