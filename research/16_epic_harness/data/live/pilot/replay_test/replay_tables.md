# E-A: compounded replay on recorded prefix trajectories

1 runs, 40 lead calls, 39 after an edit. Teacher-forced over each arm's KV against a fresh recompute (top-20 KL, nats/token).

| arm | recomputed share of reused | KL mean | first-token KL | top-1 | dNLL | greedy identical |
|---|---|---|---|---|---|---|
| shift | 0.0% | 0.0402 | 0.0041 | 97.49% | 0.0302 | 0.0% |
| epic32 | 2.3% | 0.0335 | 0.0036 | 97.77% | 0.0250 | 28.6% |
| epic64 | 3.6% | 0.0429 | 0.0024 | 97.20% | 0.0334 | 14.3% |
| epic128 | 6.2% | 0.0344 | 0.0016 | 97.77% | 0.0294 | 42.9% |
| shift1 | 0.0% | 0.0139 | 0.0011 | 98.78% | 0.0119 | 57.1% |
| epic128_1 | 6.2% | 0.0111 | 0.0011 | 98.90% | 0.0079 | 57.1% |
