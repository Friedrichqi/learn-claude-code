# Topic 22: the KV-threshold `wait` prompt with 4-32 concurrent MiniMax-M2.7 agents (wb_analyze.py)

Seconds unless noted. request = one model call (send -> full reply); entry = one task's agent phase (model calls, tools, waits); window = the full-N window (N agents at once until the queue is empty); 95 % bootstrap intervals (tasks resampled; throughput: 10-min blocks).

## Runs

| run | N | condition | tasks | window (min) | agents in window | run (min) | limit hits | call errors | pass rate |
|---|---|---|---|---|---|---|---|---|---|
| `smoke_wait` | 4 | `wait` | 12 | 4.8 | 4.0 | 14.5 | 0 | 0 | 58 % |

## Request latency in the window (s)

| run | n | avg | p50 | p95 | p99 |
|---|---|---|---|---|---|
| `smoke_wait` | 318 | 2.6 [2.1, 3.0] | 1.4 [1.2, 1.6] | 8.4 [5.4, 11.2] | 16.2 [10.6, 24.5] |

## Entry end-to-end latency (s)

| run | n | avg | p50 | p95 | p99 |
|---|---|---|---|---|---|
| `smoke_wait` | 12 | 206.7 [95.4, 344.3] | 121.7 [63.9, 238.0] | 652.0 [175.5, 741.5] | 723.6 [183.6, 741.5] |

## Throughput in the window

| run | requests/h | tokens/s | generation tokens/s | prefix hit rate | preemptions/min | KV mean | time at KV >= 80 % |
|---|---|---|---|---|---|---|---|
| `smoke_wait` | 3923 | 15076 | 264 | 0.613 | 0.0 | 0.145 | 0 % |

## Waits

| run | calls with wait | wait-only calls | wait calls per task | seconds waited per task | tasks that waited |
|---|---|---|---|---|---|
| `smoke_wait` | 0.0 % | 0.0 % | 0.00 | 0.0 | 0 |
