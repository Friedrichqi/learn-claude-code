# Census: how much tool work could a front-load line move earlier? (pw_census.py; Stage 0, CPU)

Unit: the 58 SWE-bench Pro + Terminal-Bench trials of topic 20's main replay (1,557 rounds), the only trials with chain labels; SWE-bench Verified beside them. Heavy round: >= 1 s of tools, j > 0. Decidable earlier: the round joins an earlier chain group, so the group's first call could have issued it.

## A. Topic 20's exploratory numbers, reproduced

| | Pro + TB | SWE Verified |
|---|---|---|
| heavy rounds (tool hours) | 259 (3.92 h) | 82 (0.09 h) |
| decidable earlier (joins an earlier group), share of heavy tool time | 44.1 % | 3.2 % |
| own label: independent, share of heavy tool time | 43.8 % | 3.2 % |
| own label: dependent, share of heavy tool time | 36.3 % | 59.0 % |
| own label: undetermined, share of heavy tool time | 19.9 % | 37.9 % |
| order-free (already sampled one call earlier), share of heavy tool time | 6.7 % | 2.3 % |
| next round independent, share of heavy tool time | 4.6 % | 27.9 % |
| next round dependent, share of heavy tool time | 36.2 % | 41.4 % |
| next round undetermined, share of heavy tool time | 58.8 % | 29.4 % |
| repeats an earlier command, share of heavy tool time | 18.3 % | 0.7 % |
| a joining round could start earlier by | 1.48 calls, 28.8 s | 1.17 calls, 13.4 s |
| trial time: recorded model / tools | 10.97 h / 4.02 h | 3.42 h / 0.14 h |
| ceiling: launch every decidable heavy command at its group's first call | 5.2 % of trial time | 0.6 % |
| ceiling: speculate on repeated heavy commands (50 rounds) | 0.3 % | 0.0 % |

The 'starts earlier by' seconds are recorded model time of calls k+1..h, on topic 16's loaded server.

## B. Folds (Pro + TB)

| fold | calls removed | share of the 1,557 calls |
|---|---|---|
| each group up to its last heavy (>= 1 s) joining round | 141 | 9.1 % |
| each group up to its last >= 10 s joining round | 75 | 4.8 % |
| every join (topic 20's merged world) | 407 | 26.1 % |

- Joining heavy rounds: 97; adjacent to their group start (h = k+1): 76; bash only: 92.
- Only 11 of 97 commute with the group's earlier rounds (static eff_deps), so front-loading must fold rounds k..h into one response (merge-prefix); a background launch of the heavy command alone is valid only for those.

## C. Concentration (Pro + TB)

- Heavy tool time 3.92 h; command timeouts (>= 1,790 s): 4 rounds, 2.00 h = 51.1 % of it; 2 of them are decidable earlier.
- Decidable heavy tool time 1.73 h: the two largest rounds hold 58 %, the three largest trials 71 %; 31 trials have any.
- Decidable rounds of >= 10 s: 51, in 20 trials; position in the trial (j over the last j) median 0.59, IQR 0.41-0.80.

## D. Compliance-probe items (Pro + TB)

- Eligible (j, h) pairs: 144 (76 with h >= 10 s), 141 distinct requests L_j, 94 group starts.
- Controls: 162 heavy rounds start a group; by their own label dependent 61, independent 2, undetermined 99.

## E. Tool share of trial time (every MiniMax trial topic 20 loaded)

| job | benchmark | trials | model h | tool h | tool share |
|---|---|---|---|---|---|
| minimax-m2.7 | swe | 90 | 7.0 | 0.9 | 11.3 % |
| minimax-m2.7 | swepro | 90 | 4.1 | 4.4 | 51.9 % |
| minimax-m2.7 | tbench | 110 | 11.4 | 5.1 | 30.7 % |
| minimax-m2.7-epic+prefix | swe | 491 | 69.5 | 5.4 | 7.3 % |
| minimax-m2.7-epic+prefix | swepro | 122 | 17.6 | 5.8 | 25.0 % |
| minimax-m2.7-epic+prefix | tbench | 122 | 39.9 | 14.4 | 26.5 % |
| minimax-m2.7-epic+prefix_aa | swe | 491 | 74.8 | 5.9 | 7.3 % |
| minimax-m2.7-epic+prefix_aa | swepro | 122 | 18.1 | 5.8 | 24.5 % |
| minimax-m2.7-epic+prefix_aa | tbench | 122 | 42.2 | 13.4 | 24.1 % |

Model time is the recorded call time (t1 - t0) on topic 16's servers, which ran other trials concurrently, so tool shares depend on that load.
