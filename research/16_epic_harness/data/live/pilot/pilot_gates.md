# Pilot gates (plan §5)

| regime | arm | runs | statuses | lead calls (median) | edit calls/run (median) | score (mean) | pass | wall s (median) |
|---|---|---|---|---|---|---|---|---|
| R1 | epic128 | 12 | {'ok': 5, 'budget': 7} | 150.0 | 127.5 | 0.17 | 1/12 | 462 |
| R1 | prefix | 12 | {'budget': 6, 'ok': 6} | 103.0 | 74.5 | 0.11 | 1/12 | 335 |
| R1 | prefix_aa | 12 | {'budget': 8, 'ok': 4} | 150.0 | 127.5 | 0.17 | 2/12 | 361 |
| R1 | shift | 12 | {'budget': 6, 'ok': 6} | 119.0 | 96.5 | 0.11 | 0/12 | 499 |
| R2 | epic128 | 8 | {'budget': 4, 'terminated': 2, 'ok': 2} | 7.0 | 0.5 | 0.04 | 0/8 | 341 |
| R2 | prefix | 8 | {'budget': 2, 'ok': 3, 'terminated': 3} | 5.0 | 0.0 | 0.00 | 0/8 | 542 |

- **G2 CRN**: A/A identical through the first edit in 8/12 blocks (67%; gate >= 80%)
- **G3 edits**: R1 runs with an edit 83% (>= 75%), median edit calls 127.0 (>= 10); R2 runs with a summary/reactive compaction 31% (>= 50%), fatal 0% (< 10%)
- **G4 base rates** (prefix + prefix_aa pass rate per template): E1 0.29 (n=7), E2 0.00 (n=5), E3 0.00 (n=5), M1 0.00 (n=6), M2 0.00 (n=5), M3 0.25 (n=4)
- **G6**: emulated / prefix median wall 1.23x (<= 1.5x); cap hits 52% (<= 25%)
