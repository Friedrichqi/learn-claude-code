# E-C validation against real serving

503 lead calls of 18 E-0 runs, configuration C (vanilla vLLM 0.31, prefix cache, CUDA graphs, append-only template, two sessions at a time, non-streamed). Measured: the client's send-to-receive time. Model: TTFT_compute(P - cached, P) + n_out x TPOT(context), as `jct` prices a prefix call; the second model uses the two-sequence TPOT for the share of the call another request was in flight.

| calls | n | measured median (ms) | MdAPE, JCT model (1 seq) | median signed error | MdAPE, with concurrency | median signed error |
|---|---|---|---|---|---|---|
| all calls | 503 | 1,807 | 3.4% | -2.8% | 4.1% | -0.8% |
| <= 50 output tokens | 399 | 1,531 | 5.7% | -4.7% | 4.6% | -3.5% |
| 51-300 output tokens | 68 | 2,640 | 1.6% | -0.5% | 3.3% | +2.9% |
| > 300 output tokens | 36 | 23,955 | 2.1% | +0.3% | 4.1% | +3.4% |
| computed >= 4,000 tokens | 130 | 1,999 | 5.6% | -5.6% | 4.0% | -3.3% |

Per run (sum of its lead calls' time, 18 runs): MdAPE 4.3% (signed median -4.1%) with the JCT model, 4.5% (-2.3%) with concurrency. Mean share of a call with another request in flight: 89%.
