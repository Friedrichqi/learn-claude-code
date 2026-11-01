# Counterfactual replay `smoke` (rd_analyze.py)

S = 8 samples per variant; TAU = 0.5; target key level. Tested rounds: 38 (28 with a predecessor in the turn and not a final answer).

## Validity gates

- V1 control render = recorded prompt tokens: 100.0% (need >= 95%): pass
- V2 positive control (rounds whose arguments copy the previous results, n=6): rho = 0.11 [0.00, 0.18] (need <= 0.3): pass
- V3 A/A (n=0): rho = — (need 0.85-1.15): —

## Q1-Q4 by subset

| subset | pairs | determinate | independent of prev | rho P/C | rho exact | mean p_C | pending outcomes (same/reissue/other/none/trunc) | mergeable | reorderable | order-free |
|---|---|---|---|---|---|---|---|---|---|---|
| all | 28 | 86% | 46% [33%, 62%] | 0.36 [0.31, 0.51] | 0.36 [0.27, 0.52] | 0.69 | 25%/16%/58%/0%/0% | 36% [25%, 40%] | 21% [16%, 35%] | 21% [6%, 25%] |
| t19 | 13 | 92% | 42% | 0.33 | 0.41 | 0.72 | 24%/13%/62%/1%/0% | 38% | 15% | 25% |
| t19 E1 | 2 | 100% | 50% | 0.25 | 0.43 | 1.00 | 25%/31%/44%/0%/0% | 50% | 50% | 50% |
| t19 E3 | 4 | 100% | 25% | 0.18 | 0.00 | 0.88 | 16%/19%/66%/0%/0% | 25% | 0% | 25% |
| t19 M2 | 2 | 100% | 50% | 0.67 | 0.67 | 0.75 | 50%/0%/50%/0%/0% | 50% | 0% | 0% |
| t19 M3 | 5 | 80% | 50% | 0.42 | — | 0.47 | 20%/8%/70%/2%/0% | 40% | 20% | 25% |
| t16 | 15 | 80% | 50% [25%, 75%] | 0.39 [0.29, 0.62] | 0.35 [0.23, 0.67] | 0.66 | 26%/18%/56%/0%/0% | 33% [20%, 40%] | 27% [20%, 40%] | 17% [0%, 25%] |
| t16 swe | 5 | 80% | 50% | 0.33 | 0.33 | 0.68 | 22%/30%/48%/0%/0% | 40% | 40% | 25% |
| t16 swepro | 5 | 80% | 25% | 0.29 | 0.23 | 0.78 | 22%/10%/68%/0%/0% | 20% | 20% | 0% |
| t16 tbench | 5 | 80% | 75% | 0.62 | 0.67 | 0.53 | 32%/15%/52%/0%/0% | 40% | 20% | 25% |
| class read | 14 | 93% | 38% [0%, 67%] | 0.33 [0.12, 0.45] | 0.38 [0.12, 0.50] | 0.79 | 26%/12%/62%/1%/0% | 36% [0%, 67%] | 29% [0%, 67%] | 15% [0%, 33%] |
| class edit | 5 | 100% | 40% [0%, 100%] | 0.38 [0.00, 1.00] | 0.31 [0.00, 0.83] | 0.72 | 28%/18%/55%/0%/0% | 40% [0%, 100%] | 20% [0%, 100%] | 20% [0%, 33%] |
| class shell | 2 | 50% | 100% | 1.00 | — | 0.12 | 12%/0%/88%/0%/0% | 50% | 0% | 0% |
| class todo | 1 | 100% | 100% | 0.50 | — | 0.75 | 38%/25%/38%/0%/0% | 100% | 100% | 0% |
| class mixed | 6 | 67% | 50% [0%, 100%] | 0.39 [0.00, 0.85] | 0.36 [0.00, 1.14] | 0.58 | 23%/29%/48%/0%/0% | 17% [0%, 60%] | 0% [0%, 0%] | 50% [0%, 100%] |

## Q2 merge and Q5 overlap by source

| subset | turns | calls | calls a merge removes | output tokens removed | prompt tokens of removed calls | tool s beside an independent next call | rounds with >= 1 s tools | of these followed by an independent round |
|---|---|---|---|---|---|---|---|---|
| all | 7 | 38 | 26% | 16% | 33% | 41% | 1 | 100% |
| t19 | 4 | 20 | 25% | 13% | 28% | 44% | 0 | — |
| t19 E1 | 1 | 4 | 25% | 13% | 24% | 41% | 0 | — |
| t19 E3 | 1 | 6 | 17% | 7% | 23% | 16% | 0 | — |
| t19 M2 | 1 | 4 | 25% | 9% | 25% | 21% | 0 | — |
| t19 M3 | 1 | 6 | 33% | 16% | 34% | 63% | 0 | — |
| t16 | 3 | 18 | 28% | 21% | 41% | 41% | 1 | 100% |
| t16 swe | 1 | 6 | 33% | 10% | 29% | 34% | 0 | — |
| t16 swepro | 1 | 6 | 17% | 25% | 26% | 0% | 0 | — |
| t16 tbench | 1 | 6 | 33% | 24% | 48% | 61% | 1 | 100% |

## Static census against the replay

Determinate pairs n=24: 'copies prev' predicts 'dependent' with precision 83% and recall 38%; 8 dependent rounds copy nothing from the previous results. Static merge groups agree with the replay's chain on 50% of rounds.

## Load-test gates

- G_merge (topic 19: calls a merge removes >= 10 %): 25% -> run the merge test
- G_reorder (Pro + Terminal-Bench: tool seconds beside an independent next call >= 10 %): 43% -> run the reorder test

