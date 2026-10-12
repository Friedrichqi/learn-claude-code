# Job completion time (modeled on one H100)

Models: `/home/fritz/learn-claude-code/research/16_epic_harness/data/live/main/latency/models.json`. Prefill = the fitted TTFT model on each call's geometry; decode = output tokens x TPOT(context); planning = the live planner's time; tool = the recorded gaps between lead calls.

**As run** (each live run on its own trajectory; means per run):

| arm | runs | lead calls | prefill (s) | decode (s) | planning (s) | tool + harness (s) | JCT (s) | prefill per call (ms) |
|---|---|---|---|---|---|---|---|---|
| prefix | 48 | 83 | 44.5 | 248.6 | 0.00 | 4.7 | 297.8 | 536 |
| prefix_aa | 24 | 84 | 56.8 | 199.6 | 0.00 | 1.4 | 257.8 | 678 |
| shift | 24 | 98 | 15.6 | 248.7 | 0.45 | 0.6 | 265.3 | 160 |
| epic32 | 48 | 89 | 12.5 | 223.9 | 0.38 | 1.2 | 238.0 | 140 |
| epic64 | 48 | 91 | 12.2 | 238.6 | 0.39 | 0.9 | 252.1 | 134 |
| epic128 | 48 | 92 | 15.9 | 240.7 | 0.43 | 1.9 | 259.0 | 172 |

**As run, paired by block** (arm minus prefix, seconds per run; cluster bootstrap over cells; trajectories differ between arms, so decode differences are mostly trajectory noise):

| arm | blocks | prefill [95% CI] | decode [95% CI] | JCT [95% CI] | JCT ratio (paired sums) |
|---|---|---|---|---|---|
| prefix_aa | 24 | +6.7 [-17.6, +31.6] | -73.2 [-206.7, +39.3] | -73.8 [-233.9, +56.3] | 0.777 |
| shift | 24 | -23.3 [-36.3, -12.1] | +24.3 [-74.1, +119.7] | +1.3 [-108.0, +104.1] | 1.005 |
| epic32 | 48 | -32.0 [-45.7, -18.6] | -24.7 [-80.7, +39.7] | -59.8 [-128.9, +14.6] | 0.799 |
| epic64 | 48 | -32.3 [-45.6, -19.2] | -10.0 [-84.2, +62.7] | -45.7 [-126.2, +36.0] | 0.846 |
| epic128 | 48 | -28.5 [-40.5, -16.8] | -7.9 [-80.1, +61.0] | -38.8 [-110.5, +36.2] | 0.870 |

**Paired, on identical trajectories** (E-A replays: 30 recorded prefix runs, 2290 lead calls; the same decode 4164 s and tool time 239 s in every arm):

| arm | prefill per call (ms) | prefill vs prefix | prefill vs recompute | JCT (s, sum) | JCT vs prefix |
|---|---|---|---|---|---|
| recompute | 1005 | 192% | 100% | 6,706 | 1.197 |
| prefix | 523 | 100% | 52% | 5,600 | 1.000 |
| shift | 143 | 27% | 14% | 4,730 | 0.845 |
| epic32 | 147 | 28% | 15% | 4,741 | 0.847 |
| epic64 | 151 | 29% | 15% | 4,749 | 0.848 |
| epic128 | 157 | 30% | 16% | 4,764 | 0.851 |
