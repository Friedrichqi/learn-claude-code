# KV-threshold wait prompt: MiniMax-M2.7 probe (ef_kvwait.py)

Wait rate = share of samples whose reply calls `wait` (mean over items; 95 % session-cluster bootstrap). work = replies with tool calls after the turn's first call; kept = the recorded action still issued.

## screen (60 items)

| condition | work: wait [95 %] | start: wait | final: wait | wait-only | kept (work) | out tokens p50 | seconds p50 (n) |
|---|---|---|---|---|---|---|---|
| `B0` | 0 % [0 %, 0 %] (n 42) | 0 % | 0 % | 0.0 % | 61 % | 228 | — (0) |
| `C@0.62` | 1 % [0 %, 3 %] (n 42) | 0 % | 6 % | 1.2 % | 56 % | 236 | 2 (4) |
| `C@0.93` | 4 % [1 %, 8 %] (n 42) | 0 % | 11 % | 4.2 % | 60 % | 249 | 5 (11) |
| `R@0.62` | 0 % [0 %, 0 %] (n 42) | 0 % | 0 % | 0.0 % | 61 % | 286 | — (0) |
| `R@0.93` | 1 % [0 %, 3 %] (n 42) | 6 % | 0 % | 0.4 % | 58 % | 250 | 15 (4) |
| `RC@0.62` | 1 % [0 %, 2 %] (n 42) | 0 % | 0 % | 0.4 % | 51 % | 226 | 15 (1) |
| `RC@0.93` | 2 % [0 %, 6 %] (n 42) | 17 % | 6 % | 2.5 % | 57 % | 231 | 15 (12) |
| `T@0.62` | 0 % [0 %, 0 %] (n 42) | 0 % | 0 % | 0.0 % | 62 % | 234 | — (0) |
| `T@0.93` | 0 % [0 %, 0 %] (n 42) | 0 % | 0 % | 0.0 % | 60 % | 215 | — (0) |

**Winner:** `None`: the best rate at 0.93 is 0.04 < 0.3.

Wait rate on work items by the number of earlier lines at or above the rule (items, rate):

- `C@0.62`: 1-5: 7, 4 %; 6-20: 18, 1 %; >20: 17, 0 %
- `C@0.93`: 1-5: 7, 7 %; 6-20: 18, 7 %; >20: 17, 0 %
- `R@0.62`: 1-5: 7, 0 %; 6-20: 18, 0 %; >20: 17, 0 %
- `R@0.93`: 1-5: 7, 7 %; 6-20: 18, 0 %; >20: 17, 0 %
- `RC@0.62`: 1-5: 7, 4 %; 6-20: 18, 0 %; >20: 17, 0 %
- `RC@0.93`: 1-5: 7, 11 %; 6-20: 18, 1 %; >20: 17, 0 %
- `T@0.62`: 1-5: 7, 0 %; 6-20: 18, 0 %; >20: 17, 0 %
- `T@0.93`: 1-5: 7, 0 %; 6-20: 18, 0 %; >20: 17, 0 %

