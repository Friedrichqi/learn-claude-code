### Time model (minimax-m2.7-epic, swepro, phase e2)

Fitted on 3368 baseline lead calls of `minimax-m2.7`: ms = 0 + 5.516·C/1e3 + 1.7325·C·P/1e6 + 17.8·out; validation MdAPE 38%. Reused rows cost nothing in the model (EPIC's time is a lower bound).

| arm | runs | calls | computed tok / call | computed tok / run | model LLM time / run | tool+harness / run | model job time / run | job time vs prefix |
|---|---|---|---|---|---|---|---|---|
| epic128 | 30 | 1035 | 964 | 33,241 | 165 s | 126 s | 290 s | 0.91x |
| epic128a1 | 30 | 952 | 3,615 | 114,705 | 157 s | 163 s | 321 s | 1.01x |
| prefix | 30 | 958 | 7,519 | 240,105 | 157 s | 162 s | 319 s | 1.00x |
| prefix_aa | 30 | 860 | 6,107 | 175,074 | 149 s | 147 s | 295 s | 0.93x |
| shift | 30 | 1019 | 884 | 30,018 | 147 s | 199 s | 346 s | 1.09x |

