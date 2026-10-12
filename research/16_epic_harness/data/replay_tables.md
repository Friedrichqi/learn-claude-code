# E-A: compounded replay on recorded prefix trajectories

Sources: live/main:replay, live/main:replay_b, live/screen, live/pilot:replay, live/pilot:replay_b, live/pilot:replay_c, live/pilot:replay_d, live/pilot:replay_e. 30 runs, 2290 lead calls, 2260 after an edit (the calls below). Each arm's KV evolves under its own policy over the recorded prompts; the one-step arms (`*_1`) rebuild from fresh rows every call. KL: top-20, teacher-forced over the recorded reply, against a fresh recompute (nats/token); 95% intervals resample runs.

| arm | recomputed share of reused | reply KL [95% CI] | first-token KL | top-1 | dNLL | greedy next round identical |
|---|---|---|---|---|---|---|
| shift | 0.0% | 0.0127 [0.0083, 0.0174] | 0.0062 | 99.29% | 0.0111 | 77.9% |
| epic32 | 1.5% | 0.0113 [0.0075, 0.0154] | 0.0053 | 99.35% | 0.0097 | 79.6% |
| epic64 | 2.5% | 0.0098 [0.0063, 0.0137] | 0.0051 | 99.38% | 0.0088 | 80.0% |
| epic128 | 4.4% | 0.0091 [0.0057, 0.0131] | 0.0041 | 99.40% | 0.0083 | 81.0% |
| shift1 | 0.0% | 0.0043 [0.0026, 0.0061] | 0.0014 | 99.68% | 0.0036 | 89.8% |
| epic128_1 | 4.4% | 0.0031 [0.0019, 0.0045] | 0.0012 | 99.73% | 0.0026 | 91.0% |

**KV restoration** on the same calls: rho = 1 - D(arm) / D(shift), D = sum |K - K*|^2 / sum |K*|^2 over the reused-stale rows, pooled over calls (95% CI over runs):

| arm | all layers | layers 0-15 | 16-31 | 32-47 | 48-63 |
|---|---|---|---|---|---|
| epic32 | 11% [8%, 14%] | 27% [20%, 32%] | 22% [17%, 27%] | 13% [9%, 16%] | 7% [5%, 9%] |
| epic64 | 22% [17%, 28%] | 43% [34%, 51%] | 40% [31%, 47%] | 26% [20%, 32%] | 15% [11%, 19%] |
| epic128 | 30% [24%, 37%] | 55% [46%, 62%] | 50% [42%, 58%] | 35% [28%, 42%] | 21% [16%, 27%] |
| epic128_1 | 67% [64%, 71%] | 86% [84%, 87%] | 85% [82%, 88%] | 75% [71%, 78%] | 58% [54%, 62%] |
| shift1 | 64% [60%, 67%] | 78% [76%, 79%] | 79% [76%, 81%] | 70% [67%, 74%] | 55% [52%, 59%] |

**Paired against shift** (same calls): mean reply KL and first-token KL of the arm / of shift (compounded arms against compounded shift, one-step against one-step):

| arm | reply KL ratio [95% CI] | first-token KL ratio [95% CI] |
|---|---|---|
| epic32 / shift | 0.89 [0.86, 0.92] | 0.86 [0.73, 1.05] |
| epic64 / shift | 0.77 [0.66, 0.90] | 0.82 [0.68, 1.08] |
| epic128 / shift | 0.72 [0.59, 0.85] | 0.66 [0.48, 0.95] |
| epic128_1 / shift1 | 0.73 [0.67, 0.80] | 0.88 [0.76, 1.00] |

**Compounding** (same calls): reply KL of the compounded arm / its one-step twin:

- shift / shift1: 2.96× [2.56, 3.50]
- epic128 / epic128_1: 2.91× [2.47, 3.52]

**Reply KL by template** (edited calls):

| template | calls | shift | epic32 | epic64 | epic128 | shift1 | epic128_1 |
|---|---|---|---|---|---|---|---|
| E1 | 79 | 0.0349 | 0.0321 | 0.0273 | 0.0268 | 0.0155 | 0.0121 |
| E2 | 395 | 0.0116 | 0.0104 | 0.0104 | 0.0091 | 0.0032 | 0.0024 |
| E3 | 316 | 0.0035 | 0.0033 | 0.0026 | 0.0024 | 0.0014 | 0.0012 |
| M1 | 459 | 0.0035 | 0.0031 | 0.0029 | 0.0025 | 0.0007 | 0.0006 |
| M2 | 853 | 0.0182 | 0.0161 | 0.0128 | 0.0116 | 0.0064 | 0.0043 |
| M3 | 158 | 0.0200 | 0.0167 | 0.0183 | 0.0202 | 0.0065 | 0.0057 |
