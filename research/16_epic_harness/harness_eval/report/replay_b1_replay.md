### E-A replay `b1_replay`: 10 trajectories, arms epic128, epic128a1, prefix, prefix_aa, shift

Rendered prompts equal to the recorded prompt length: 522 of 522 calls.

First-token distribution against `prefix` on the identical prompt (top-20 KL; top-1 = same argmax):

| arm | calls | KL mean | KL p90 | KL max | top-1 agree | KL since snip 0-0 | KL since snip 1-5 | KL since snip 6-20 | KL since snip 21-+ |
|---|---|---|---|---|---|---|---|---|---|
| epic128 | 522 | 0.0166 | 0.0281 | 1.603 | 92.9% | 0.0073 (n=216) | 0.0135 (n=48) | 0.0176 (n=124) | 0.0317 (n=134) |
| epic128a1 | 522 | 0.0073 | 0.0160 | 0.123 | 94.6% | 0.0066 (n=216) | 0.0103 (n=48) | 0.0077 (n=124) | 0.0070 (n=134) |
| prefix_aa | 522 | 0.0059 | 0.0134 | 0.134 | 95.0% | 0.0062 (n=216) | 0.0056 (n=48) | 0.0061 (n=124) | 0.0054 (n=134) |
| shift | 522 | 0.0175 | 0.0321 | 1.707 | 92.7% | 0.0073 (n=216) | 0.0133 (n=48) | 0.0179 (n=124) | 0.0353 (n=134) |

Server side (shadow probes of the replay sessions):

| arm | calls | computed / prompt | probes | first-token KL | reply KL | reply top-1 |
|---|---|---|---|---|---|---|
| epic128 | 522 | 6.1% | 38 | +0.0141 | invalid | invalid |
| epic128a1 | 522 | 35.0% | 47 | +0.0095 | invalid | invalid |
| prefix | 522 | 66.5% | 0 | — | invalid | invalid |
| prefix_aa | 522 | 66.5% | 0 | — | invalid | invalid |
| shift | 522 | 5.7% | 36 | +0.0147 | invalid | invalid |

Reply KL and top-1 are not shown: three of the four instances (@g0-@g2) ran a middleware whose teacher-forced probe misread the prompt logprobs when the hybrid model's loads are refused (fixed during the stage); the valid @g3 subset is in the notes below

