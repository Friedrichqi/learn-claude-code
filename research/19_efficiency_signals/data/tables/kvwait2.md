# KV-threshold wait prompt, revised screen: the replayed history and a worked example (ef_kvwait2.py)

Wait rate = share of samples whose reply calls `wait` (mean over items; 95 % session-cluster bootstrap). work = replies with tool calls after the turn's first call; kept = the recorded action still issued; cap = every earlier [server] line shows at most 75 %; mentions = samples whose thinking mentions the line, the rule or a pause.

## screen (60 items)

| condition | work: wait [95 %] | start: wait | final: wait | wait-only | kept (work) | mentions | out tokens p50 | seconds p50 (n) |
|---|---|---|---|---|---|---|---|---|
| `B0` | 0 % [0 %, 0 %] (n 42) | 0 % | 0 % | 0.0 % | 60 % | 0.0 % | 220 | — (0) |
| `RC@0.62` | 0 % [0 %, 0 %] (n 42) | 0 % | 0 % | 0.0 % | 58 % | 5.0 % | 224 | — (0) |
| `RC@0.93` | 3 % [0 %, 7 %] (n 42) | 25 % | 8 % | 1.2 % | 55 % | 2.9 % | 211 | 15 (17) |
| `RCcap@0.62` | 0 % [0 %, 0 %] (n 42) | 0 % | 0 % | 0.0 % | 56 % | 0.4 % | 219 | — (0) |
| `RCcap@0.93` | 58 % [49 %, 66 %] (n 42) | 64 % | 86 % | 30.0 % | 46 % | 57.5 % | 210 | 15 (151) |
| `RX@0.62` | 1 % [0 %, 2 %] (n 42) | 0 % | 3 % | 0.4 % | 54 % | 4.2 % | 227 | 15 (2) |
| `RX@0.93` | 4 % [0 %, 8 %] (n 42) | 19 % | 6 % | 1.2 % | 54 % | 3.8 % | 211 | 15 (15) |
| `RXcap@0.62` | 0 % [0 %, 0 %] (n 42) | 0 % | 0 % | 0.0 % | 54 % | 0.0 % | 241 | — (0) |
| `RXcap@0.93` | 57 % [48 %, 65 %] (n 42) | 72 % | 69 % | 26.7 % | 39 % | 55.8 % | 182 | 15 (146) |

Paired differences in the work wait rate at 0.93 (points; 95 % session-cluster bootstrap; history and example are the means of their two pairs):

- RCcap - RC: +54.8 [+45.9, +63.6]
- RXcap - RX: +53.0 [+45.0, +60.9]
- history: +53.9 [+46.2, +61.0]
- RX - RC: +0.6 [-1.7, +3.6]
- RXcap - RCcap: -1.2 [-9.4, +6.5]
- example: -0.3 [-4.5, +4.0]

**Readings** (followups/kvwait2_prereg.md): usable at 0.93 (eligible and >= 30 %): none; the replayed history: holds it back; the worked example: not shown to help.

Wait rate on work items by the number of earlier lines at or above the rule as recorded (items, rate); under cap those lines show at most 75 %:

- `RC@0.62`: 1-5: 8, 0 %; 6-20: 20, 0 %; >20: 14, 0 %
- `RC@0.93`: 1-5: 8, 16 %; 6-20: 20, 0 %; >20: 14, 0 %
- `RCcap@0.62`: 1-5: 8, 0 %; 6-20: 20, 0 %; >20: 14, 0 %
- `RCcap@0.93`: 1-5: 8, 69 %; 6-20: 20, 49 %; >20: 14, 64 %
- `RX@0.62`: 1-5: 8, 3 %; 6-20: 20, 0 %; >20: 14, 0 %
- `RX@0.93`: 1-5: 8, 19 %; 6-20: 20, 0 %; >20: 14, 0 %
- `RXcap@0.62`: 1-5: 8, 0 %; 6-20: 20, 0 %; >20: 14, 0 %
- `RXcap@0.93`: 1-5: 8, 59 %; 6-20: 20, 50 %; >20: 14, 64 %

