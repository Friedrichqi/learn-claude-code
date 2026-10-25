### Time model (minimax-m2.7-epic, swe, phase e3)

Fitted on 6214 baseline lead calls of `minimax-m2.7`: ms = 323 + 52.938·C/1e3 + 0.0000·C·P/1e6 + 19.5·out; validation MdAPE 29%. Reused rows cost nothing in the model (EPIC's time is a lower bound).

| arm | runs | calls | computed tok / call | computed tok / run | model LLM time / run | tool+harness / run | model job time / run | job time vs prefix |
|---|---|---|---|---|---|---|---|---|
| epic128 | 300 | 13892 | 634 | 29,368 | 217 s | 69 s | 286 s | 0.99x |
| epic128a1 | 300 | 14298 | 3,552 | 169,299 | 231 s | 58 s | 289 s | 1.00x |
| epic128a4 | 300 | 13190 | 1,572 | 69,121 | 210 s | 52 s | 262 s | 0.90x |
| prefix | 300 | 14012 | 6,937 | 324,021 | 239 s | 51 s | 290 s | 1.00x |
| prefix_aa | 300 | 14001 | 6,649 | 310,332 | 235 s | 53 s | 288 s | 1.00x |

