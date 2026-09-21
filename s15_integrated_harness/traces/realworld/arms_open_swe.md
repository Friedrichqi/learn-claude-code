## E1-R — the three arms on real trajectories, unlimited budget

One row per arm, pooled over successor rounds. `byte recall` is the share of what the round went on to read that was already held; `round recall` the share of rounds that could have disappeared entirely.

| arm | successor rounds | sent KB (median) | byte recall | line recall | round recall | precision (used/sent) | stale |
|---|---|---|---|---|---|---|---|
| none (hold nothing) | 37226 | 0.0 | 0.0% | 0.0% | 0/37226 = 0.0% | 0.0% | -- | 0.0% |
| dag (direct predecessor) | 37226 | 0.9 | 0.2% | 7.1% | 561/37226 = 1.5% | 2.8% | 0.2% | 3.5% |
| ancestors (all earlier rounds) | 37226 | 34.2 | 0.9% | 23.5% | 1870/37226 = 5.0% | 16.2% | 0.1% | 8.2% |

### By harness — the tool table decides how much redundancy there is

| harness | trajectories | rounds/traj (median) | prompt chars, median successor round | byte recall (ancestors) | round recall (ancestors) | stale |
|---|---|---|---|---|---|---|
| minisweagent | 400 | 51 | 43,040 | 0.8% | 1.9% | 22.9% |
| openhands | 400 | 75 | 87,750 | 0.8% | 4.7% | 3.2% |
| sweagent | 400 | 76 | 63,382 | 1.0% | 8.5% | 3.9% |
