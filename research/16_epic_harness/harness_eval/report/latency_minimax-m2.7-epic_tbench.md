### Time model (minimax-m2.7-epic, tbench, phase e2)

Fitted on 3867 baseline lead calls of `minimax-m2.7`: ms = 0 + 0.000·C/1e3 + 0.2595·C·P/1e6 + 19.8·out; validation MdAPE 33%. Reused rows cost nothing in the model (EPIC's time is a lower bound).

| arm | runs | calls | computed tok / call | computed tok / run | model LLM time / run | tool+harness / run | model job time / run | job time vs prefix |
|---|---|---|---|---|---|---|---|---|
| epic128 | 30 | 925 | 752 | 23,199 | 285 s | 425 s | 710 s | 1.03x |
| epic128a1 | 30 | 872 | 4,410 | 128,185 | 322 s | 428 s | 749 s | 1.08x |
| prefix | 30 | 894 | 7,952 | 236,982 | 300 s | 393 s | 693 s | 1.00x |
| prefix_aa | 30 | 1042 | 9,678 | 336,161 | 395 s | 530 s | 925 s | 1.33x |
| shift | 30 | 974 | 1,508 | 48,966 | 422 s | 468 s | 890 s | 1.28x |

