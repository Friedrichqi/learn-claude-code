## Stage 1. Scripted successor agent (micro-loop)

| arm | n | rounds | censored | read_file calls | locate calls | correct | injected tok | prompt tok | output tok | wall s |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 32 | 7.22 (sd 2.94) | 3 | 2.31 | 8.19 | 91% | 0 | 12998 | 1067 | 62.37 |
| oracle | 32 | 2.50 (sd 2.18) | 0 | 0.59 | 1.22 | 100% | 644 | 2037 | 316 | 18.44 |
| pollution | 32 | 7.53 (sd 2.63) | 0 | 2.47 | 6.84 | 100% | 644 | 14172 | 772 | 52.35 |
| summary | 32 | 6.41 (sd 2.82) | 1 | 2.22 | 6.88 | 97% | 45 | 13320 | 1027 | 57.07 |

4 of 128 trials hit the 12-round cap (all of them are kept, right-censored, so every round difference below is a LOWER bound).

### Stage 1 contrasts against baseline (unpaired bootstrap)

| arm | delta rounds | 95% CI | delta read_file | delta correct | delta wall s |
|---|---|---|---|---|---|
| oracle | -4.72 | [-5.97, -3.47] | -1.72 | +9 pts | -43.9 |
| pollution | +0.31 | [-1.03, +1.66] | +0.16 | +9 pts | -10.0 |
| summary | -0.81 | [-2.22, +0.59] | -0.09 | +6 pts | -5.3 |

### Stage 1 by task (rounds)

| arm | T1 | T2 | T3 | T4 |
|---|---|---|---|---|
| baseline | 9.0 | 7.6 | 8.2 | 4.0 |
| oracle | 1.9 | 1.5 | 5.4 | 1.2 |
| pollution | 7.8 | 7.2 | 8.2 | 6.9 |
| summary | 6.2 | 6.6 | 8.9 | 3.9 |

Re-read rate under oracle injection: 25% of trials still issued a read_file (32 trials). A high rate means the handoff fails behaviourally -- no serving-side mechanism can remove a round the agent insists on spending.

## Stage 2. Real s15 team harness, successor tasks only

| arm | successors | cross-owner | rounds | reads | active s | prompt tok | out tok | injected tok | files cited | completed |
|---|---|---|---|---|---|---|---|---|---|---|
| none | 6 | 6 | 5.17 | 4.50 | 146.9 | 14754 | 6451 | 0 | 5.5 | 100% |
| dag | 6 | 6 | 3.00 | 3.33 | 113.0 | 30009 | 4507 | 11114 | 3.7 | 100% |
| pollute | 5 | 5 | 5.00 | 4.60 | 142.3 | 37696 | 5971 | 11075 | 2.4 | 60% |
| summary | 5 | 5 | 5.80 | 5.20 | 161.5 | 35669 | 6267 | 480 | 3.6 | 100% |

### Stage 2 contrasts against baseline

| arm | delta rounds | 95% CI | delta reads | delta active s | saved s (rounds x fixed) | prefill paid s | net s |
|---|---|---|---|---|---|---|---|
| dag | -2.17 | [-3.50, -0.83] | -1.17 | -33.8 | +55.2 | 0.37 | +54.9 |
| pollute | -0.17 | [-1.60, +1.27] | +0.10 | -4.6 | +4.2 | 0.37 | +3.9 |
| summary | +0.63 | [-1.07, +2.57] | +0.70 | +14.6 | -16.1 | 0.02 | -16.2 |
