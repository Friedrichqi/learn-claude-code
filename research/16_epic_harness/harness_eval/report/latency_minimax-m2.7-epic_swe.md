### Time model (minimax-m2.7-epic, swe, phase e1)

Fitted on 6214 baseline lead calls of `minimax-m2.7`: ms = 323 + 52.938·C/1e3 + 0.0000·C·P/1e6 + 19.5·out; validation MdAPE 29%. Reused rows cost nothing in the model (EPIC's time is a lower bound).

| arm | runs | calls | computed tok / call | computed tok / run | model LLM time / run | tool+harness / run | model job time / run | job time vs prefix |
|---|---|---|---|---|---|---|---|---|
| epic128 | 90 | 4303 | 702 | 33,562 | 211 s | 38 s | 250 s | 0.94x |
| epic128a1 | 90 | 4699 | 3,835 | 200,232 | 236 s | 36 s | 272 s | 1.03x |
| prefix | 90 | 4445 | 7,552 | 372,961 | 235 s | 30 s | 265 s | 1.00x |
| prefix_aa | 90 | 4802 | 7,379 | 393,719 | 240 s | 31 s | 271 s | 1.02x |
| shift | 90 | 4565 | 605 | 30,706 | 226 s | 45 s | 271 s | 1.02x |

