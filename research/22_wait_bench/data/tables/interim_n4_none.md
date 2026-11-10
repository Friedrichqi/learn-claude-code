# Topic 22: the KV-threshold `wait` prompt with 4-32 concurrent MiniMax-M2.7 agents (wb_analyze.py)

Seconds unless noted. request = one model call (send -> full reply); entry = one task's agent phase (model calls, tools, waits); window = the full-N window (N agents at once until the queue is empty); 95 % bootstrap intervals (tasks resampled; throughput: 10-min blocks).

## Runs

| run | N | condition | tasks | window (min) | agents in window | run (min) | limit hits | call errors | pass rate |
|---|---|---|---|---|---|---|---|---|---|
| `n4_none` | 4 | `none` | 96 | 127.3 | 4.0 | 143.9 | 4 | 0 | 80 % |

## Request latency in the window (s)

| run | n | avg | p50 | p95 | p99 |
|---|---|---|---|---|---|
| `n4_none` | 3855 | 4.2 [3.4, 5.0] | 1.9 [1.7, 2.3] | 13.8 [10.6, 16.4] | 31.8 [23.2, 40.0] |

## Entry end-to-end latency (s)

| run | n | avg | p50 | p95 | p99 |
|---|---|---|---|---|---|
| `n4_none` | 96 | 330.9 [222.5, 456.6] | 130.8 [108.5, 180.0] | 1676.8 [651.5, 2317.5] | 3600.0 [1648.4, 3600.0] |

## Throughput in the window

| run | requests/h | tokens/s | generation tokens/s | prefix hit rate | preemptions/min | KV mean | time at KV >= 80 % |
|---|---|---|---|---|---|---|---|
| `n4_none` | 1815 [1483, 2157] | 10060 [8014, 11914] | 193 [171, 216] | 0.517 | 0.0 | 0.186 | 0 % |

## Waits

| run | calls with wait | wait-only calls | wait calls per task | seconds waited per task | tasks that waited |
|---|---|---|---|---|---|
| `n4_none` | 0.0 % | 0.0 % | 0.00 | 0.0 | 0 |
