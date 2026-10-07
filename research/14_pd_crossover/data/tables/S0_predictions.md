# S0 predictions (12's coefficients, assumed transfer rate)

Workload: 3793 calls of 12's p2a N = 4 and 8; mean prompt 23,902 tokens, new (computed) 3,437, output 95; client time Z = 0.37 s. Prefill GPU-s per call: TP1 0.550, TP2 0.297, TP4 0.160 (extrapolated). KV pool tokens: TP1 258,192, TP2 776,720, TP4 1,782,784. KV transfer 200 GB/s, router hop 2.0 ms.

| arm | GPUs | prefill bound (calls/h) | decode bound | colocated bound | arm bound | binds | uncontended call s | N_sat | KV capacity (sessions) | first to bind |
|---|---|---|---|---|---|---|---|---|---|---|
| dp2 | 2 | — | — | 7,915 | 7,915 | GPU compute | 2.50 | 6.3 | 20 | throughput at N ~ 6  |
| dp2c | 2 | — | — | 7,915 | 7,915 | GPU compute | 2.50 | 6.3 | 20 | throughput at N ~ 6  |
| tp2 | 2 | — | — | 8,662 | 8,662 | GPU compute | 1.42 | 4.3 | 32 | throughput at N ~ 4  |
| 1p1d | 2 | 6,545 | 10,010 | — | 6,545 | P compute | 2.51 | 5.2 | P caches 10, D concurrent 10 | throughput at N ~ 5  |
| dp4 | 4 | — | — | 15,829 | 15,829 | GPU compute | 2.50 | 12.6 | 40 | throughput at N ~ 13  |
| tp2dp2 | 4 | — | — | 17,323 | 17,323 | GPU compute | 1.42 | 8.6 | 64 | throughput at N ~ 9  |
| 2p2d | 4 | 13,090 | 20,020 | — | 13,090 | P compute | 2.51 | 10.5 | P caches 20, D concurrent 20 | throughput at N ~ 10  |
| 3p1d | 4 | 19,634 | 10,010 | — | 10,010 | D capacity x speed | 2.51 | 8.0 | P caches 30, D concurrent 10 | throughput at N ~ 8  |
| 1p1d-tp2 | 4 | 12,141 | 30,220 | — | 12,141 | P compute | 1.42 | 6.0 | P caches 32, D concurrent 32 | throughput at N ~ 6  |
| 1p3d | 4 | 6,545 | 30,030 | — | 6,545 | P compute | 2.51 | 5.2 | P caches 10, D concurrent 30 | throughput at N ~ 5  |
| tp4 | 4 | — | — | 15,962 | 15,962 | GPU compute | 0.82 | 5.3 | 74 | throughput at N ~ 5 TP=4 extrapolated |
| 2p1d-dtp2 | 4 | 13,090 | 30,220 | — | 13,090 | P compute | 1.68 | 7.4 | P caches 20, D concurrent 32 | throughput at N ~ 7  |

*Key.* Bounds are approximate upper limits (minimum GPU-seconds per call from 11's step model, decode steps at the largest batch the KV pool allows) on calls per hour from one resource each (PLAN.md §2): the prefill side's compute, the decode side's concurrent-decode capacity times its step speed, or a colocated instance's compute. **N_sat**: agents at which the closed loop reaches the arm bound (Little's law). **KV capacity**: sessions whose prefix fits (pd: in the prefill caches; the decode side holds at most the listed concurrent decodes). The lowest N among them is the predicted mechanism of N*_high.
