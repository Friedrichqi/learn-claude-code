# Topic 22: the KV-threshold `wait` prompt with 4-32 concurrent MiniMax-M2.7 agents (wb_analyze.py)

Seconds unless noted. request = one model call (send -> full reply); entry = one task's agent phase (model calls, tools, waits); window = the full-N window (N agents at once until the queue is empty); 95 % bootstrap intervals (tasks resampled; throughput: 10-min blocks).

## Runs

| run | N | condition | tasks | window (min) | agents in window | run (min) | limit hits | call errors | pass rate |
|---|---|---|---|---|---|---|---|---|---|
| `dry_none` | 2 | `none` | 12 | 0.9 | 0.5 | 1.0 | 0 | 0 | 0 % |
| `dry_wait` | 2 | `wait` | 12 | 1.1 | 0.9 | 1.2 | 0 | 0 | 0 % |

## Request latency in the window (s)

| run | n | avg | p50 | p95 | p99 |
|---|---|---|---|---|---|
| `dry_none` | 10 | 0.2 [0.2, 0.2] | 0.2 [0.2, 0.2] | 0.2 [0.2, 0.2] | 0.2 [0.2, 0.2] |
| `dry_wait` | 21 | 0.2 [0.2, 0.2] | 0.2 [0.2, 0.2] | 0.2 [0.2, 0.2] | 0.2 [0.2, 0.2] |

## Entry end-to-end latency (s)

| run | n | avg | p50 | p95 | p99 |
|---|---|---|---|---|---|
| `dry_none` | 12 | 2.6 [1.9, 3.2] | 2.2 [1.4, 4.0] | 4.2 [3.1, 4.3] | 4.2 [3.9, 4.3] |
| `dry_wait` | 12 | 5.3 [4.5, 6.2] | 4.7 [3.9, 6.6] | 7.4 [6.5, 8.1] | 8.0 [6.5, 8.1] |

## Throughput in the window

| run | requests/h | tokens/s | generation tokens/s | prefix hit rate | preemptions/min | KV mean | time at KV >= 80 % |
|---|---|---|---|---|---|---|---|
| `dry_none` | 655 | 101 | 4 | — | 0.0 | 0.930 | 100 % |
| `dry_wait` | 1181 | 226 | 7 | — | 0.0 | 0.930 | 100 % |

## Waits

| run | calls with wait | wait-only calls | wait calls per task | seconds waited per task | tasks that waited |
|---|---|---|---|---|---|
| `dry_none` | 0.0 % | 0.0 % | 0.00 | 0.0 | 0 |
| `dry_wait` | 50.0 % | 0.0 % | 1.00 | 2.0 | 12 |
