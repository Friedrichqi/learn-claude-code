## Profile (250 lead calls; qualification q3 + profiling q4+; first 30 tasks per benchmark, first 3 valid trials per task)

| model | bench | tasks | trials | pass@1 [95% CI] | tasks with 3 trials | pass@3 | pass^3 | consistent | lead calls | out tok | compactions/run | wall min | s15 status | exceptions | infra (excluded) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| deepseek-v4-flash | swe | 30 | 90 | 0.900 [0.822, 0.978] | 30 | 0.967 | 0.800 | 0.83 | 53.2 | 15624 | 32.2 | 5.3 | budget:2 completed:88 | 3 | 0 |
| deepseek-v4-flash | swepro | 30 | 90 | 0.944 [0.889, 0.989] | 30 | 1.000 | 0.867 | 0.87 | 56.4 | 23077 | 36.6 | 9.9 | budget:3 completed:83 timeout:4 | 4 | 0 |
| deepseek-v4-flash | tbench | 30 | 90 | 0.589 [0.456, 0.733] | 30 | 0.833 | 0.367 | 0.53 | 26.6 | 28608 | 9.1 | 11.0 | completed:72 timeout:18 | 18 | 6 |
| minimax-m2.7 | swe | 15 | 41 | 0.578 [0.356, 0.800] | 13 | 0.769 | 0.538 | 0.77 | 64.5 | 12200 | 43.0 | 5.5 | budget:1 completed:40 | 0 | 0 |
| minimax-m2.7 | swepro | 6 | 16 | 0.778 [0.444, 1.000] | 5 | 0.800 | 0.600 | 0.80 | 56.4 | 10203 | 36.8 | 8.1 | budget:1 completed:15 | 0 | 2 |
| minimax-m2.7 | tbench | 16 | 43 | 0.531 [0.333, 0.708] | 12 | 0.667 | 0.167 | 0.50 | 35.4 | 18348 | 18.8 | 8.9 | budget:2 completed:34 timeout:7 | 7 | 0 |
| qwen3-32b | tbench | 14 | 37 | 0.024 [0.000, 0.071] | 9 | 0.111 | 0.000 | 0.89 | 194.5 | 8130 | 174.0 | 6.1 | budget:28 completed:7 error:1 timeout:1 | 1 | 2 |
| qwen3.8-27b | tbench | 8 | 8 | 0.625 [0.250, 1.000] | 0 | nan | nan | nan | 62.0 | 64990 | 44.2 | 20.0 | completed:4 timeout:4 | 4 | 0 |

## Pilot (150 lead calls, as in X2)

| model | bench | tasks | trials | pass@1 [95% CI] | tasks with 3 trials | pass@3 | pass^3 | consistent | lead calls | out tok | compactions/run | wall min | s15 status | exceptions | infra (excluded) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| qwen3-32b | swe | 29 | 29 | 0.069 [0.000, 0.172] | 0 | nan | nan | nan | 114.1 | 5816 | 99.6 | 7.0 | budget:21 completed:7 timeout:1 | 2 | 1 |

## Earlier 150-call trials, not used in the profile (smoke p2, first qualification p3/p4)

| model | bench | tasks | trials | pass@1 [95% CI] | tasks with 3 trials | pass@3 | pass^3 | consistent | lead calls | out tok | compactions/run | wall min | s15 status | exceptions | infra (excluded) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| qwen3-32b | tbench | 8 | 9 | 0.000 [0.000, 0.000] | 0 | nan | nan | nan | 76.2 | 5126 | 59.6 | 8.4 | budget:4 completed:3 timeout:2 | 2 | 3 |
| qwen3.8-27b | tbench | 3 | 3 | 0.333 [0.000, 1.000] | 0 | nan | nan | nan | 70.7 | 66841 | 50.3 | 21.2 | budget:1 completed:1 timeout:1 | 1 | 1 |

