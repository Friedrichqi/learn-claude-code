# S0 · Tool-duration predictor for B1 (seconds a session is away after a reply)

Written by `tm_predict.py` to `tool_durations.json`. Keys with ≥ 20 samples.

## What the server sees: window after a reply, by its dominant tool (137,544 windows, 11/12 live cells)

| what the reply called | windows | p50 | p90 | p99 | mean | cells |
|---|---|---|---|---|---|---|
| (turn end) | 3,005 | 2.01 | 83.01 | 1370.5 | 75.77 | n01a, n04, n08, n12, n16, n32, o1, p1a, p1b, p2a, p2b, p2c, t2 |
| read_file | 56,884 | 0.01 | 0.07 | 6.4 | 0.84 | n01a, n04, n08, n12, n16, n32, o1, p1a, p1b, p2a, p2b, p2c, t2 |
| (no tool) | 452 | 2.01 | 56.26 | 231.1 | 19.89 | n01a, n04, n08, n12, n16, n32, o1, p1a, p2a, p2b, p2c, t2 |
| bash:pytest/suite | 207 | 18.57 | 20.25 | 21.9 | 18.79 | p1a, p1b, p2b, p2c |
| bash:find | 18,641 | 0.03 | 0.05 | 2.0 | 0.18 | n04, n08, n12, n16, n32, p1b, p2c |
| bash:find/wide | 203 | 5.44 | 36.32 | 58.7 | 15.98 | n04, n32, p1b, p2b, p2c |
| edit_file | 20,633 | 0.01 | 0.01 | 0.0 | 0.12 | n01a, n04, n08, n12, n16, n32, o1, p1a, p1b, p2a, p2b, p2c, t2 |
| bash:pytest | 1,881 | 0.79 | 0.89 | 1.5 | 0.84 | p1a, p1b, p2b, p2c |
| write_file | 2,200 | 0.01 | 0.01 | 0.6 | 0.69 | n01a, n04, n08, n12, n16, n32, o1, p1a, p2a, p2b, p2c, t2 |
| todo_write | 17,023 | 0.01 | 0.01 | 2.0 | 0.07 | n01a, n04, n08, n12, n16, n32, o1, p1a, p1b, p2a, p2b, p2c, t2 |
| bash:grep | 8,615 | 0.01 | 0.02 | 0.1 | 0.07 | n01a, n04, n08, n12, n16, n32, p1b, p2a, p2c, t2 |
| glob | 5,525 | 0.01 | 0.01 | 0.1 | 0.10 | n01a, n04, n08, n12, n16, n32, o1, p1a, p1b, p2a, p2b, p2c, t2 |
| bash:grep/wide | 135 | 0.01 | 0.02 | 91.3 | 2.44 | p1a, p2b, p2c |
| bash:view | 309 | 0.01 | 0.13 | 0.5 | 0.11 | n04, n32, p2c |
| bash:other | 1,250 | 0.02 | 0.02 | 0.0 | 0.02 | n01a, n04, n32, p2c |
| search | 75 | 0.01 | 0.01 | 0.5 | 0.04 | p2c |
| list_tasks | 200 | 0.01 | 0.01 | 0.0 | 0.01 | p2c |
| complete_task | 70 | 0.01 | 0.01 | 0.0 | 0.01 | p2c |
| grep | 58 | 0.01 | 0.01 | 0.0 | 0.01 | n04, n08, n16, n32, p1b, p2a, p2b, p2c |
| filter | 33 | 0.01 | 0.01 | 0.0 | 0.01 | p2c |
| list_teammates | 24 | 0.01 | 0.01 | 0.0 | 0.01 | p2c |
| update_task | 25 | 0.00 | 0.01 | 0.0 | 0.00 | p2c |
| create_task | 21 | 0.00 | 0.01 | 0.0 | 0.00 | p2c |

## Fallback: the tool's own time (291,660 calls: 06 census on this cluster + 12's traces)

Median window minus tool time over shared keys: **0.01 s** (harness and turn handling around the tool).

| tool (bash by command class) | calls | p50 | p90 | p99 | mean |
|---|---|---|---|---|---|
| bash:pytest | 2,918 | 0.806 | 1.232 | 19.34 | 2.140 |
| bash:find | 31,096 | 0.021 | 0.048 | 0.72 | 0.171 |
| bash:grep | 15,371 | 0.005 | 0.009 | 0.03 | 0.087 |
| task | 71 | 8.469 | 20.697 | 203.88 | 18.191 |
| read_file | 148,269 | 0.001 | 0.004 | 0.02 | 0.002 |
| glob | 11,084 | 0.001 | 0.004 | 0.02 | 0.007 |
| bash:python-test | 76 | 0.399 | 0.494 | 0.54 | 0.401 |
| edit_file | 39,892 | 0.001 | 0.001 | 0.00 | 0.001 |
| bash:other | 1,815 | 0.011 | 0.014 | 0.03 | 0.012 |
| bash:python | 87 | 0.180 | 0.231 | 0.25 | 0.160 |
| list_tasks | 1,245 | 0.010 | 0.014 | 0.09 | 0.010 |
| spawn_teammate | 120 | 0.052 | 0.078 | 0.11 | 0.056 |
| todo_write | 33,433 | 0.000 | 0.000 | 0.00 | 0.000 |
| complete_task | 407 | 0.000 | 0.037 | 0.06 | 0.010 |
| write_file | 4,546 | 0.001 | 0.001 | 0.01 | 0.001 |
| create_task | 171 | 0.015 | 0.021 | 0.03 | 0.014 |
| bash:view | 320 | 0.004 | 0.008 | 0.05 | 0.007 |
| claim_task | 122 | 0.012 | 0.029 | 0.06 | 0.015 |
| request_shutdown | 96 | 0.009 | 0.032 | 0.04 | 0.014 |
| send_message | 123 | 0.009 | 0.013 | 0.03 | 0.010 |

*Key.* **window**: the client-side time between a reply's arrival and the session's next call (harness + tool + think time); 11's sessions draw a think time (lognormal, median 30 s) before each follow-up turn, 12's run closed loop. `/suite`: pytest over a whole tests/ directory; `/wide`: find, a recursive grep or another walk of a huge tree (site-packages, or a path at most two levels below /). `bash:grep/wide` is bimodal (p50 0.01 s, p99 ~90 s): whether the tree is in the page cache decides, which no static feature predicts. Rows sorted by total time.
