## E1-R — the three arms on real trajectories, unlimited budget

One row per arm, pooled over successor rounds. `byte recall` is the share of what the round went on to read that was already held; `round recall` the share of rounds that could have disappeared entirely.

| arm | successor rounds | sent KB (median) | byte recall | line recall | round recall | precision (used/sent) | stale |
|---|---|---|---|---|---|---|---|
| none (hold nothing) | 1207 | 0.0 | 0.0% | 0.0% | 0/1207 = 0.0% | 0.0% | -- | 0.0% |
| dag (direct predecessor) | 1207 | 0.7 | 0.9% | 26.1% | 75/1207 = 6.2% | 7.9% | 1.1% | 0.1% |
| ancestors (all earlier rounds) | 1207 | 2.0 | 4.1% | 28.8% | 131/1207 = 10.9% | 12.6% | 1.3% | 0.5% |

### By harness — the tool table decides how much redundancy there is

| harness | trajectories | rounds/traj (median) | prompt chars, median successor round | byte recall (ancestors) | round recall (ancestors) | stale |
|---|---|---|---|---|---|---|
| HAL Generalist Agent (o3-mini-2025-01-31 high) | 121 | 3 | 17,423 | 0.0% | 0.5% | 0.0% |
| Taubench ToolCalling (claude-3.7-sonnet) | 49 | 8 | 10,237 | 12.6% | 13.9% | 1.0% |
| Taubench ToolCalling (claude-opus-4.1) | 50 | 6 | 9,976 | 0.0% | 10.7% | 0.7% |
| Taubench ToolCalling (deepseek-v3) | 49 | 5 | 10,762 | 2.4% | 5.2% | 0.7% |
| Taubench ToolCalling (gemini-2.0-flash-001) | 49 | 6 | 9,874 | 12.2% | 20.8% | 1.3% |
| Taubench ToolCalling (gpt-4.1-2025-04-14) | 47 | 7 | 10,109 | 1.6% | 14.6% | 0.0% |
