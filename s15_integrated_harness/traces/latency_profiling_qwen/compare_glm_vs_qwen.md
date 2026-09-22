# Latency breakdown: glm-5.3-flash vs Qwen/Qwen3.8-27B

A = `glm-5.3-flash` (s15_integrated_harness/traces/latency_profiling), B = `Qwen/Qwen3.8-27B` (s15_integrated_harness/traces/latency_profiling_qwen). Both analysed with `scripts/latency_breakdown.py` (same round reconstruction, `uncached` = tokens the provider computed = input_tokens + cache_creation_input_tokens). Means are over pooled rounds or calls unless a row says otherwise; B/A is the ratio of the two means.

## T0. Setup

| item | glm-5.3-flash | Qwen/Qwen3.8-27B |
|---|---:|---:|
| model | glm-5.3-flash | Qwen/Qwen3.8-27B |
| endpoint | https://api.z.ai/api/anthropic | http://127.0.0.1:40727 |
| runs | CODE-solo x3, FQA-solo x3, MATH-solo x3, CODE-team x5, FQA-team x5, MATH-team x5 | CODE-solo x3, FQA-solo x3, MATH-solo x3, CODE-team x5, FQA-team x5, MATH-team x5 |
| dates | 2026-09-13 | 2026-09-22 |
| harness commit | 64d1b11bc9 | aded0d0e7f |
| CONTEXT_LIMIT (chars) | 512000 | 512000 |
| streaming | True | True |
| server | - | {"version": "0.29.0"} |
| GPU | - | NVIDIA RTX PRO 6000 Blackwell Server Edition 97887 MiB |
| vLLM flags | - | max-model-len 262144, gpu-util 0.9, max-num-seqs 32, max-num-batched-tokens 16384, extra: - |
| software | - | vllm 0.29.0, torch 2.13.0+cu130, anthropic 1.7.0 |
| run timeout (s) | - | 3600 |
| server metrics per call | no | yes |


## T1. Runs and quality

| group | completed glm-5.3-flash | completed Qwen/Qwen3.8-27B | score glm-5.3-flash | score Qwen/Qwen3.8-27B | wall/run s glm-5.3-flash | wall/run s Qwen/Qwen3.8-27B | wall B/A | stop=max_tokens glm-5.3-flash | stop=max_tokens Qwen/Qwen3.8-27B | model errors glm-5.3-flash | model errors Qwen/Qwen3.8-27B | retried calls glm-5.3-flash | retried calls Qwen/Qwen3.8-27B | denied tools glm-5.3-flash | denied tools Qwen/Qwen3.8-27B |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo | 3/3 | 3/3 | 9/9 | 9/9 | 161 +- 53 | 386 +- 38 | 2.39x | 0% | 0% | 0 | 0 | 0 | 0 | 0 | 0 |
| FQA-solo | 3/3 | 3/3 | coverage 0.97 +- 0.00 | coverage 0.97 +- 0.03 | 101 +- 28 | 346 +- 57 | 3.41x | 0% | 0% | 0 | 0 | 0 | 0 | 0 | 0 |
| MATH-solo | 3/3 | 3/3 | 9/9 | 9/9 | 118 +- 3 | 209 +- 25 | 1.77x | 0% | 0% | 0 | 0 | 0 | 0 | 4 | 1 |
| CODE-team | 5/5 | 5/5 | 15/15 | 15/15 | 215 +- 32 | 470 +- 160 | 2.18x | 0% | 0% | 0 | 0 | 0 | 0 | 5 | 0 |
| FQA-team | 5/5 | 5/5 | coverage 0.99 +- 0.01 | coverage 0.98 +- 0.02 | 180 +- 21 | 420 +- 49 | 2.33x | 0% | 0% | 0 | 0 | 0 | 0 | 4 | 0 |
| MATH-team | 5/5 | 5/5 | 15/15 | 15/15 | 165 +- 12 | 306 +- 57 | 1.85x | 0% | 0% | 0 | 0 | 0 | 0 | 11 | 5 |


## T2. One agentic round (prep / model / tools / other add up to the round; instrument = profiler's vLLM metrics scrape, kept out of the buckets)

Lead rounds (team and solo):

| metric | CODE-solo lead glm-5.3-flash | CODE-solo lead Qwen/Qwen3.8-27B | FQA-solo lead glm-5.3-flash | FQA-solo lead Qwen/Qwen3.8-27B | MATH-solo lead glm-5.3-flash | MATH-solo lead Qwen/Qwen3.8-27B | CODE-team lead glm-5.3-flash | CODE-team lead Qwen/Qwen3.8-27B | FQA-team lead glm-5.3-flash | FQA-team lead Qwen/Qwen3.8-27B | MATH-team lead glm-5.3-flash | MATH-team lead Qwen/Qwen3.8-27B |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| rounds | 47 | 44 | 11 | 31 | 7 | 7 | 56 | 64 | 51 | 57 | 55 | 71 |
| gross per round, mean (s) | 9.5 | 25.7 | 21.0 | 29.6 | 41.5 | 76.9 | 7.7 | 11.8 | 9.7 | 21.5 | 7.6 | 14.5 |
| gross median (s) | 6.1 | 10.2 | 9.0 | 12.0 | 42.5 | 83.5 | 6.9 | 8.6 | 8.3 | 11.1 | 6.6 | 7.6 |
| gross p90 (s) | 20.2 | 57.4 | 57.6 | 79.7 | 69.7 | 124.0 | 12.6 | 23.9 | 14.2 | 58.2 | 11.7 | 32.5 |
| context preparation share | 0.1% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 7.0% | 0.1% | 0.1% | 0.0% | 0.1% | 5.1% |
| agent call share | 99.4% | 99.2% | 99.8% | 99.8% | 99.9% | 99.9% | 92.9% | 99.6% | 99.8% | 99.8% | 99.8% | 94.6% |
| tool execution share | 0.5% | 0.7% | 0.1% | 0.1% | 0.1% | 0.1% | 0.1% | 0.2% | 0.1% | 0.1% | 0.1% | 0.2% |
| other share | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| instrument share | 0.0% | 0.1% | 0.0% | 0.1% | 0.0% | 0.0% | 0.0% | 0.1% | 0.0% | 0.1% | 0.0% | 0.1% |
| prep mean (ms) | 6.4 | 12.3 | 8.4 | 11.1 | 4.6 | 6.7 | 542.1 | 8.3 | 7.3 | 9.2 | 6.5 | 736.3 |
| tools mean (ms) | 45.3 | 169.1 | 23.7 | 17.0 | 22.8 | 67.8 | 8.5 | 23.0 | 9.1 | 24.6 | 7.8 | 31.8 |
| tool calls per round | 1.09 | 1.30 | 1.73 | 1.65 | 0.86 | 0.57 | 1.20 | 1.14 | 1.37 | 1.25 | 1.24 | 1.27 |
| client first block mean (s) | 5.2 | 1.3 | 6.0 | 1.1 | 5.7 | 0.6 | 5.5 | 0.7 | 6.1 | 1.0 | 5.9 | 0.9 |
| client streamed tail mean (s) | 4.3 | 24.2 | 15.0 | 28.4 | 35.8 | 76.2 | 1.6 | 11.0 | 3.6 | 20.4 | 1.6 | 12.8 |
| turn-end work per lead turn (s) | 10.1 | 7.1 | 22.2 | 38.4 | 19.1 | 27.8 | 19.1 | 20.0 | 20.6 | 35.0 | 13.1 | 12.4 |
| turn-end work amortised per lead round (s) | 0.6 | 0.5 | 6.1 | 3.7 | 8.2 | 11.9 | 9.2 | 8.1 | 7.7 | 12.9 | 5.5 | 4.4 |
| (prep + turn-end) share of lead-side time | 6.4% | 1.9% | 22.4% | 11.2% | 16.5% | 13.4% | 57.6% | 40.8% | 44.2% | 37.5% | 42.1% | 27.1% |

Teammate rounds (team runs):

| metric | CODE teammate glm-5.3-flash | CODE teammate Qwen/Qwen3.8-27B | FQA teammate glm-5.3-flash | FQA teammate Qwen/Qwen3.8-27B | MATH teammate glm-5.3-flash | MATH teammate Qwen/Qwen3.8-27B |
|---|---:|---:|---:|---:|---:|---:|
| rounds | 112 | 106 | 64 | 63 | 43 | 58 |
| gross per round, mean (s) | 10.0 | 27.6 | 8.9 | 24.6 | 17.2 | 27.0 |
| gross median (s) | 5.5 | 5.3 | 6.2 | 7.9 | 12.1 | 23.8 |
| gross p90 (s) | 15.0 | 85.5 | 17.0 | 75.5 | 34.7 | 60.2 |
| context preparation share | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| agent call share | 99.6% | 99.5% | 99.8% | 99.8% | 99.9% | 99.7% |
| tool execution share | 0.4% | 0.4% | 0.2% | 0.1% | 0.1% | 0.2% |
| other share | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| instrument share | 0.0% | 0.1% | 0.0% | 0.1% | 0.0% | 0.0% |
| prep mean (ms) | 2.8 | 6.8 | 4.2 | 7.9 | 1.9 | 6.4 |
| tools mean (ms) | 36.6 | 118.0 | 14.2 | 30.6 | 11.9 | 53.7 |
| tool calls per round | 1.04 | 0.93 | 1.12 | 1.11 | 0.79 | 0.67 |
| client first block mean (s) | 5.6 | 0.4 | 5.5 | 1.0 | 6.1 | 0.4 |
| client streamed tail mean (s) | 4.4 | 27.0 | 3.3 | 23.6 | 11.0 | 26.5 |


## T3. Inside the agent call

Three-parameter regression over agent calls: model-call duration = a + b x computed prompt tokens + c x output tokens (least squares).

| fit over | calls glm-5.3-flash | calls Qwen/Qwen3.8-27B | a fixed s glm-5.3-flash | a fixed s Qwen/Qwen3.8-27B | b ms/computed tok glm-5.3-flash | b ms/computed tok Qwen/Qwen3.8-27B | implied prefill tok/s glm-5.3-flash | implied prefill tok/s Qwen/Qwen3.8-27B | c ms/output tok glm-5.3-flash | c ms/output tok Qwen/Qwen3.8-27B | r2 glm-5.3-flash | r2 Qwen/Qwen3.8-27B |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| all agent calls | 446 | 501 | 3.72 | 0.43 | 0.0327 | 0.1165 | 30,593 | 8,584 | 17.44 | 39.88 | 0.96 | 1.00 |
| lead | 227 | 274 | 3.16 | 0.35 | 0.0856 | 0.1616 | 11,689 | 6,189 | 17.82 | 38.28 | 0.95 | 1.00 |
| teammate | 219 | 227 | 4.10 | 0.26 | -0.0024 | 0.4107 | - | 2,435 | 17.13 | 40.57 | 0.97 | 1.00 |
| responses ending in tool_use | 322 | 370 | 3.76 | 0.33 | -0.0037 | 0.1451 | - | 6,893 | 17.61 | 39.93 | 0.96 | 1.00 |
| responses ending in end_turn | 124 | 131 | 3.57 | 0.89 | 0.1766 | 0.0690 | 5,663 | 14,495 | 16.22 | 38.80 | 0.96 | 1.00 |

Share of call time by the pooled coefficients, per group and kind:

| metric | CODE-solo lead glm-5.3-flash | CODE-solo lead Qwen/Qwen3.8-27B | FQA-solo lead glm-5.3-flash | FQA-solo lead Qwen/Qwen3.8-27B | MATH-solo lead glm-5.3-flash | MATH-solo lead Qwen/Qwen3.8-27B | CODE-team lead glm-5.3-flash | CODE-team lead Qwen/Qwen3.8-27B | CODE-team teammate glm-5.3-flash | CODE-team teammate Qwen/Qwen3.8-27B | FQA-team lead glm-5.3-flash | FQA-team lead Qwen/Qwen3.8-27B | FQA-team teammate glm-5.3-flash | FQA-team teammate Qwen/Qwen3.8-27B | MATH-team lead glm-5.3-flash | MATH-team lead Qwen/Qwen3.8-27B | MATH-team teammate glm-5.3-flash | MATH-team teammate Qwen/Qwen3.8-27B |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| calls | 47 | 44 | 11 | 31 | 7 | 7 | 56 | 64 | 112 | 106 | 51 | 57 | 64 | 63 | 55 | 71 | 43 | 58 |
| fixed (network, queue, start-up) | 39.2% | 1.7% | 17.7% | 1.5% | 9.0% | 0.6% | 51.8% | 3.7% | 37.2% | 1.6% | 38.3% | 2.0% | 41.8% | 1.8% | 49.2% | 3.1% | 21.7% | 1.6% |
| prefill of computed tokens | 1.8% | 3.2% | 1.9% | 2.4% | 0.2% | 0.4% | 1.4% | 3.6% | 0.3% | 0.6% | 1.3% | 2.7% | 1.0% | 1.6% | 1.5% | 3.8% | 0.2% | 0.4% |
| decode | 61.2% | 98.9% | 78.4% | 99.9% | 88.5% | 103.9% | 56.2% | 96.3% | 58.7% | 97.3% | 61.7% | 96.8% | 58.0% | 88.5% | 49.7% | 96.3% | 77.3% | 95.6% |
| residual | -2.2% | -3.8% | 2.0% | -3.8% | 2.3% | -4.9% | -9.4% | -3.5% | 3.8% | 0.6% | -1.3% | -1.5% | -0.8% | 8.2% | -0.4% | -3.2% | 0.9% | 2.3% |

Server-side measurement from vLLM /metrics (only for the side served by vLLM; exact-attribution calls; gap = client duration - server e2e = HTTP/SDK overhead):

| metric | CODE-solo lead glm-5.3-flash | CODE-solo lead Qwen/Qwen3.8-27B | FQA-solo lead glm-5.3-flash | FQA-solo lead Qwen/Qwen3.8-27B | MATH-solo lead glm-5.3-flash | MATH-solo lead Qwen/Qwen3.8-27B | CODE-team lead glm-5.3-flash | CODE-team lead Qwen/Qwen3.8-27B | CODE-team teammate glm-5.3-flash | CODE-team teammate Qwen/Qwen3.8-27B | FQA-team lead glm-5.3-flash | FQA-team lead Qwen/Qwen3.8-27B | FQA-team teammate glm-5.3-flash | FQA-team teammate Qwen/Qwen3.8-27B | MATH-team lead glm-5.3-flash | MATH-team lead Qwen/Qwen3.8-27B | MATH-team teammate glm-5.3-flash | MATH-team teammate Qwen/Qwen3.8-27B |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| calls with server metrics | - | 44 | - | 31 | - | 7 | - | 64 | - | 106 | - | 57 | - | 63 | - | 71 | - | 58 |
| exactly attributable share | - | 100.0% | - | 100.0% | - | 100.0% | - | 100.0% | - | 100.0% | - | 100.0% | - | 100.0% | - | 100.0% | - | 100.0% |
| server prefill mean (s) | - | 1.156 | - | 0.987 | - | 0.466 | - | 0.608 | - | 0.335 | - | 0.863 | - | 0.793 | - | 0.765 | - | 0.345 |
| server decode mean (s) | - | 24.2 | - | 28.4 | - | 76.2 | - | 11.1 | - | 27.0 | - | 20.4 | - | 23.6 | - | 12.9 | - | 26.5 |
| server queue mean (s) | - | 0.000 | - | 0.000 | - | 0.000 | - | 0.000 | - | 0.000 | - | 0.000 | - | 0.000 | - | 0.000 | - | 0.000 |
| server e2e mean (s) | - | 25.5 | - | 29.4 | - | 76.8 | - | 11.7 | - | 27.4 | - | 21.4 | - | 24.5 | - | 13.7 | - | 26.9 |
| client gap mean (s) | - | 0.060 | - | 0.055 | - | 0.046 | - | 0.059 | - | 0.034 | - | 0.058 | - | 0.029 | - | 0.061 | - | 0.027 |
| client gap median (s) | - | 0.061 | - | 0.052 | - | 0.044 | - | 0.061 | - | 0.032 | - | 0.059 | - | 0.029 | - | 0.061 | - | 0.026 |
| server prefill share of client call | - | 4.5% | - | 3.3% | - | 0.6% | - | 5.2% | - | 1.2% | - | 4.0% | - | 3.2% | - | 5.6% | - | 1.3% |
| server decode share of client call | - | 95.0% | - | 96.3% | - | 99.3% | - | 93.8% | - | 98.4% | - | 95.3% | - | 96.2% | - | 93.5% | - | 98.4% |
| server queue share of client call | - | 0.0% | - | 0.0% | - | 0.0% | - | 0.0% | - | 0.0% | - | 0.0% | - | 0.0% | - | 0.0% | - | 0.0% |
| gap share of client call | - | 0.2% | - | 0.2% | - | 0.1% | - | 0.5% | - | 0.1% | - | 0.3% | - | 0.1% | - | 0.4% | - | 0.1% |
| prefill tok/s (computed tokens / prefill s) | - | 6,137 | - | 6,110 | - | 5,656 | - | 5,920 | - | 3,937 | - | 5,693 | - | 4,151 | - | 5,853 | - | 2,966 |
| decode ms per token | - | 38.4 | - | 38.5 | - | 38.1 | - | 39.0 | - | 40.4 | - | 39.3 | - | 43.4 | - | 38.9 | - | 41.1 |
| finished by length (max_tokens) share | - | 0.0% | - | 0.0% | - | 0.0% | - | 0.0% | - | 0.0% | - | 0.0% | - | 0.0% | - | 0.0% | - | 0.0% |
| requests running at finish, mean | - | 0.0 | - | 0.0 | - | 0.0 | - | 0.9 | - | 1.4 | - | 1.0 | - | 2.2 | - | 0.9 | - | 2.0 |
| KV cache usage at finish, mean | - | 0.0% | - | 0.0% | - | 0.0% | - | 1.2% | - | 2.0% | - | 2.8% | - | 4.7% | - | 0.9% | - | 2.3% |
| metrics scrape per call (ms) | - | 18.7 | - | 18.7 | - | 20.8 | - | 19.8 | - | 21.2 | - | 19.1 | - | 19.2 | - | 18.9 | - | 19.0 |


## T4. Tokens per agent call

Lead calls (team and solo):

| metric | CODE-solo lead glm-5.3-flash | CODE-solo lead Qwen/Qwen3.8-27B | FQA-solo lead glm-5.3-flash | FQA-solo lead Qwen/Qwen3.8-27B | MATH-solo lead glm-5.3-flash | MATH-solo lead Qwen/Qwen3.8-27B | CODE-team lead glm-5.3-flash | CODE-team lead Qwen/Qwen3.8-27B | FQA-team lead glm-5.3-flash | FQA-team lead Qwen/Qwen3.8-27B | MATH-team lead glm-5.3-flash | MATH-team lead Qwen/Qwen3.8-27B |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| calls | 47 | 44 | 11 | 31 | 7 | 7 | 56 | 64 | 51 | 57 | 55 | 71 |
| prompt tokens, mean | 7,136 | 9,445 | 13,801 | 8,380 | 4,533 | 4,986 | 4,796 | 5,948 | 5,547 | 7,265 | 5,267 | 6,826 |
| prompt tokens, median | 6,686 | 8,464 | 18,183 | 7,321 | 5,223 | 5,394 | 4,937 | 6,106 | 5,490 | 6,394 | 5,267 | 6,127 |
| prompt chars, mean (tokenizer-independent) | 31,518 | 36,433 | 57,396 | 31,600 | 15,678 | 14,562 | 21,414 | 23,035 | 24,594 | 28,414 | 21,326 | 24,085 |
| chars per prompt token | 4.42 | 3.86 | 4.16 | 3.77 | 3.46 | 2.92 | 4.46 | 3.87 | 4.43 | 3.91 | 4.05 | 3.53 |
| output chars, mean (tokenizer-independent) | 1,325 | 2,199 | 3,726 | 2,610 | 4,958 | 3,914 | 904 | 946 | 1,397 | 1,887 | 765 | 971 |
| chars per output token | 3.99 | 3.48 | 3.95 | 3.53 | 2.35 | 1.96 | 3.91 | 3.32 | 4.07 | 3.62 | 3.56 | 2.93 |
| prompt tokens, max | 12,912 | 18,151 | 25,504 | 18,812 | 6,790 | 7,267 | 6,459 | 8,504 | 8,437 | 13,724 | 8,387 | 12,337 |
| computed (uncached) tokens, mean | 5,249 | 7,093 | 12,073 | 6,028 | 2,722 | 2,634 | 3,099 | 3,596 | 3,766 | 4,913 | 3,577 | 4,474 |
| provider cache-read share | 26.4% | 24.9% | 12.5% | 28.1% | 39.9% | 47.2% | 35.4% | 39.5% | 32.1% | 32.4% | 32.1% | 34.5% |
| cache-creation share (vLLM only) | 0.0% | 70.9% | 0.0% | 67.3% | 0.0% | 47.2% | 0.0% | 53.7% | 0.0% | 62.9% | 0.0% | 60.0% |
| output tokens, mean | 332 | 633 | 943 | 739 | 2,107 | 2,001 | 231 | 285 | 343 | 521 | 215 | 332 |
| output tokens, median | 116 | 216 | 302 | 296 | 2,387 | 2,186 | 143 | 197 | 189 | 213 | 152 | 166 |
| output tokens, max | 2,121 | 5,281 | 4,035 | 5,276 | 4,025 | 3,702 | 771 | 1,232 | 1,297 | 2,558 | 690 | 1,516 |
| thinking share of output chars | 37.8% | 55.1% | 49.3% | 62.0% | 68.0% | 61.4% | 22.1% | 32.5% | 36.8% | 49.3% | 25.2% | 47.0% |
| first block is thinking | 46.8% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 71.4% | 100.0% | 72.5% | 100.0% | 67.3% | 100.0% |
| prompt growth per round, median (tok) | 285 | 478 | 6,954 | 1,312 | 2,688 | 2,380 | 242 | 283 | 580 | 652 | 360 | 520 |
| prompt growth per round, mean (tok) | 496 | 970 | 8,321 | 1,450 | 2,412 | 2,213 | 320 | 381 | 565 | 811 | 461 | 577 |
| stop = tool_use share | 93.6% | 93.2% | 72.7% | 90.3% | 57.1% | 57.1% | 51.8% | 59.4% | 62.7% | 63.2% | 58.2% | 64.8% |
| stop = max_tokens share | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| model call mean (s) | 9.5 | 25.5 | 21.0 | 29.5 | 41.5 | 76.8 | 7.2 | 11.8 | 9.7 | 21.5 | 7.6 | 13.7 |
| streamed tail tok/s (pooled) | 77 | 26 | 63 | 26 | 59 | 26 | 142 | 26 | 94 | 26 | 132 | 26 |

Teammate calls (team runs):

| metric | CODE teammate glm-5.3-flash | CODE teammate Qwen/Qwen3.8-27B | FQA teammate glm-5.3-flash | FQA teammate Qwen/Qwen3.8-27B | MATH teammate glm-5.3-flash | MATH teammate Qwen/Qwen3.8-27B |
|---|---:|---:|---:|---:|---:|---:|
| calls | 112 | 106 | 64 | 63 | 43 | 58 |
| prompt tokens, mean | 3,717 | 5,936 | 5,373 | 8,830 | 1,921 | 2,698 |
| prompt tokens, median | 3,407 | 4,224 | 3,348 | 9,324 | 1,582 | 2,551 |
| prompt chars, mean (tokenizer-independent) | 16,250 | 22,527 | 22,922 | 34,705 | 6,527 | 7,452 |
| chars per prompt token | 4.37 | 3.80 | 4.27 | 3.93 | 3.40 | 2.76 |
| output chars, mean (tokenizer-independent) | 1,336 | 2,351 | 1,175 | 2,081 | 1,905 | 1,420 |
| chars per output token | 3.97 | 3.51 | 3.97 | 3.82 | 2.51 | 2.20 |
| prompt tokens, max | 8,223 | 21,743 | 17,305 | 19,319 | 4,855 | 5,897 |
| computed (uncached) tokens, mean | 905 | 1,321 | 2,586 | 3,292 | 983 | 1,022 |
| provider cache-read share | 75.7% | 77.8% | 51.9% | 62.7% | 48.8% | 62.1% |
| cache-creation share (vLLM only) | 0.0% | 15.6% | 0.0% | 32.1% | 0.0% | 22.5% |
| output tokens, mean | 337 | 670 | 296 | 544 | 760 | 646 |
| output tokens, median | 68 | 122 | 95 | 150 | 518 | 571 |
| output tokens, max | 5,200 | 6,767 | 1,585 | 2,437 | 3,173 | 3,294 |
| thinking share of output chars | 51.0% | 68.0% | 51.1% | 64.9% | 56.1% | 50.1% |
| first block is thinking | 57.1% | 100.0% | 75.0% | 100.0% | 88.4% | 100.0% |
| prompt growth per round, median (tok) | 276 | 485 | 847 | 1,625 | 546 | 624 |
| prompt growth per round, mean (tok) | 616 | 1,056 | 2,959 | 3,657 | 845 | 788 |
| stop = tool_use share | 86.6% | 85.8% | 75.0% | 76.2% | 65.1% | 65.5% |
| stop = max_tokens share | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| model call mean (s) | 10.0 | 27.5 | 8.9 | 24.5 | 17.1 | 27.0 |
| streamed tail tok/s (pooled) | 77 | 25 | 89 | 23 | 69 | 24 |


## T5. Teammate input redundancy (ranges over the runs of a group)

| metric | CODE glm-5.3-flash | CODE Qwen/Qwen3.8-27B | FQA glm-5.3-flash | FQA Qwen/Qwen3.8-27B | MATH glm-5.3-flash | MATH Qwen/Qwen3.8-27B |
|---|---:|---:|---:|---:|---:|---:|
| re-sent: prompt tokens already sent in the same teammate's previous call | 76-86% | 72-88% | 33-58% | 62-67% | 45-63% | 37-72% |
| served by the provider cache (usage cache-read share) | 68-79% | 67-85% | 27-58% | 60-64% | 44-52% | 46-66% |
| server prefix-cache hit rate, whole run (vLLM only) | - | 51-77% | - | 44-51% | - | 34-49% |
| server cached share of all prompt tokens, whole run (vLLM only) | - | 51-77% | - | 44-51% | - | 34-49% |
| file bytes another teammate fetched first (exact (file,line)) | 0% | 0% | 12-23% | 23% | - | - |
| cross-redundant share of teammate prompt tokens | 0% | 0% | 8-27% | 16-20% | 0% | 0% |
| file content share of teammate prompt tokens | 21-30% | 15-25% | 76-86% | 78-81% | 0% | 0% |
| lead: re-sent share | 87-91% | 88-91% | 84-88% | 83-89% | 85-89% | 81-90% |
| lead: cache-read share | 28-42% | 36-44% | 20-38% | 26-35% | 29-36% | 30-40% |


## T6. Agent runs per task and end-to-end time

| metric | CODE-solo glm-5.3-flash | CODE-solo Qwen/Qwen3.8-27B | B/A | FQA-solo glm-5.3-flash | FQA-solo Qwen/Qwen3.8-27B | B/A | MATH-solo glm-5.3-flash | MATH-solo Qwen/Qwen3.8-27B | B/A | CODE-team glm-5.3-flash | CODE-team Qwen/Qwen3.8-27B | B/A | FQA-team glm-5.3-flash | FQA-team Qwen/Qwen3.8-27B | B/A | MATH-team glm-5.3-flash | MATH-team Qwen/Qwen3.8-27B | B/A |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| runs | 3 | 3 | 1.00x | 3 | 3 | 1.00x | 3 | 3 | 1.00x | 5 | 5 | 1.00x | 5 | 5 | 1.00x | 5 | 5 | 1.00x |
| teammate rounds per task, mean | - | - | - | - | - | - | - | - | - | 7.5 | 7.1 | 0.95x | 4.0 | 4.2 | 1.05x | 2.9 | 2.9 | 1.01x |
| teammate rounds per task, median | - | - | - | - | - | - | - | - | - | 6 | 6 | 1.00x | 4 | 4 | 1.00x | 3 | 3 | 1.00x |
| teammate rounds per task, max | - | - | - | - | - | - | - | - | - | 15 | 16 | 1.07x | 6 | 5 | 0.83x | 5 | 5 | 1.00x |
| teammate active time per task (s) | - | - | - | - | - | - | - | - | - | 75 | 195 | 2.60x | 36 | 103 | 2.90x | 49 | 78 | 1.59x |
| teammates per run | 0.0 | 0.0 | - | 0.0 | 0.0 | - | 0.0 | 0.0 | - | 3.0 | 3.0 | 1.00x | 3.0 | 3.0 | 1.00x | 3.0 | 3.0 | 1.00x |
| teammate tasks per run | 0.0 | 0.0 | - | 0.0 | 0.0 | - | 0.0 | 0.0 | - | 3.0 | 3.0 | 1.00x | 3.2 | 3.0 | 0.94x | 3.0 | 4.0 | 1.33x |
| lead rounds per run | 15.7 | 14.7 | 0.94x | 3.7 | 10.3 | 2.82x | 2.3 | 2.3 | 1.00x | 11.2 | 12.8 | 1.14x | 10.2 | 11.4 | 1.12x | 11.0 | 14.2 | 1.29x |
| lead turns per run | 1.0 | 1.0 | 1.00x | 1.0 | 1.0 | 1.00x | 1.0 | 1.0 | 1.00x | 5.4 | 5.2 | 0.96x | 3.8 | 4.2 | 1.11x | 4.6 | 5.0 | 1.09x |
| model calls per run (incl. memory) | 16.7 | 15.7 | 0.94x | 4.7 | 11.3 | 2.43x | 3.3 | 3.3 | 1.00x | 41.4 | 39.2 | 0.95x | 26.8 | 28.2 | 1.05x | 24.2 | 31.0 | 1.28x |
| wall per run (s) | 161 | 386 | 2.39x | 101 | 346 | 3.41x | 118 | 209 | 1.77x | 215 | 470 | 2.18x | 180 | 420 | 2.33x | 165 | 306 | 1.85x |
| wall per task (s) | 54 | 129 | 2.39x | 34 | 115 | 3.41x | 39 | 70 | 1.77x | 72 | 157 | 2.18x | 60 | 140 | 2.33x | 55 | 102 | 1.85x |
| lead busy share of wall | 98.7% | 99.5% | 1.01x | 98.0% | 99.4% | 1.01x | 98.3% | 99.0% | 1.01x | 88.2% | 54.4% | 0.62x | 98.3% | 93.3% | 0.95x | 87.0% | 87.8% | 1.01x |
| of which lead agent calls (s) | 148 | 374 | 2.52x | 77 | 305 | 3.96x | 97 | 179 | 1.85x | 80 | 151 | 1.88x | 99 | 245 | 2.47x | 83 | 195 | 2.35x |
| of which turn-end memory work (s) | 10 | 7 | 0.70x | 22 | 38 | 1.73x | 19 | 28 | 1.46x | 103 | 104 | 1.01x | 78 | 147 | 1.88x | 60 | 62 | 1.03x |
| teammate active share of wall | 0.0% | 0.0% | - | 0.0% | 0.0% | - | 0.0% | 0.0% | - | 51.7% | 69.1% | 1.34x | 26.4% | 29.3% | 1.11x | 50.2% | 51.0% | 1.02x |
| lead/teammate overlap (s) | 0 | 0 | - | 0 | 0 | - | 0 | 0 | - | 89 | 113 | 1.27x | 47 | 98 | 2.08x | 64 | 122 | 1.89x |
| idle share of wall | 1.3% | 0.5% | 0.42x | 2.0% | 0.6% | 0.30x | 1.7% | 1.0% | 0.57x | 1.3% | 0.5% | 0.40x | 1.4% | 0.7% | 0.48x | 1.8% | 0.9% | 0.52x |
| denied tool calls per run | 0.0 | 0.0 | - | 0.0 | 0.0 | - | 1.3 | 0.3 | 0.25x | 1.0 | 0.0 | 0.00x | 0.8 | 0.0 | 0.00x | 2.2 | 1.0 | 0.45x |


## T7. Stability across repetitions (mean +- sd over the runs of a group)

| group | runs A / B | wall s glm-5.3-flash | wall s Qwen/Qwen3.8-27B | lead rounds/run glm-5.3-flash | lead rounds/run Qwen/Qwen3.8-27B | model calls/run glm-5.3-flash | model calls/run Qwen/Qwen3.8-27B | lead gross/round s glm-5.3-flash | lead gross/round s Qwen/Qwen3.8-27B | teammate gross/round s glm-5.3-flash | teammate gross/round s Qwen/Qwen3.8-27B | prompt tok/call glm-5.3-flash | prompt tok/call Qwen/Qwen3.8-27B | output tok/call glm-5.3-flash | output tok/call Qwen/Qwen3.8-27B | score glm-5.3-flash | score Qwen/Qwen3.8-27B |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo | 3 / 3 | 161 +- 53 | 386 +- 38 | 15.7 +- 3.8 | 14.7 +- 3.1 | 16.7 +- 3.8 | 15.7 +- 3.1 | 9.5 +- 2.5 | 26.7 +- 7.7 | - | - | 6,949 +- 1,471 | 9,593 +- 1,250 | 332 +- 115 | 658 +- 195 | 3.00 +- 0.00 | 3.00 +- 0.00 |
| FQA-solo | 3 / 3 | 101 +- 28 | 346 +- 57 | 3.7 +- 1.5 | 10.3 +- 0.6 | 4.7 +- 1.5 | 11.3 +- 0.6 | 23.7 +- 10.6 | 29.7 +- 6.4 | - | - | 13,666 +- 1,173 | 8,371 +- 272 | 1,060 +- 489 | 743 +- 165 | 0.97 +- 0.00 | 0.97 +- 0.03 |
| MATH-solo | 3 / 3 | 118 +- 3 | 209 +- 25 | 2.3 +- 0.6 | 2.3 +- 0.6 | 3.3 +- 0.6 | 3.3 +- 0.6 | 42.8 +- 8.9 | 78.5 +- 16.9 | - | - | 4,497 +- 446 | 4,848 +- 843 | 2,210 +- 647 | 2,045 +- 447 | 3.00 +- 0.00 | 3.00 +- 0.00 |
| CODE-team | 5 / 5 | 215 +- 32 | 470 +- 160 | 11.2 +- 1.6 | 12.8 +- 1.1 | 41.4 +- 5.4 | 39.2 +- 3.6 | 7.9 +- 1.4 | 11.8 +- 1.7 | 10.3 +- 1.9 | 27.3 +- 4.2 | 4,076 +- 247 | 5,818 +- 1,561 | 307 +- 72 | 521 +- 84 | 3.00 +- 0.00 | 3.00 +- 0.00 |
| FQA-team | 5 / 5 | 180 +- 21 | 420 +- 49 | 10.2 +- 1.3 | 11.4 +- 1.5 | 26.8 +- 4.1 | 28.2 +- 1.6 | 9.8 +- 1.3 | 21.9 +- 5.7 | 8.9 +- 0.8 | 25.0 +- 4.4 | 5,471 +- 584 | 8,092 +- 247 | 321 +- 58 | 535 +- 57 | 0.99 +- 0.01 | 0.98 +- 0.02 |
| MATH-team | 5 / 5 | 165 +- 12 | 306 +- 57 | 11.0 +- 1.6 | 14.2 +- 2.2 | 24.2 +- 2.6 | 31.0 +- 4.7 | 7.6 +- 0.5 | 14.8 +- 5.0 | 17.3 +- 1.6 | 28.1 +- 5.3 | 3,780 +- 280 | 4,967 +- 583 | 457 +- 61 | 475 +- 67 | 3.00 +- 0.00 | 3.00 +- 0.00 |


## T8. Auxiliary model calls made by the harness (memory recall / extraction, summary compaction)

| group | purpose | calls/run glm-5.3-flash | calls/run Qwen/Qwen3.8-27B | mean s glm-5.3-flash | mean s Qwen/Qwen3.8-27B | B/A | prompt tok glm-5.3-flash | prompt tok Qwen/Qwen3.8-27B | output tok glm-5.3-flash | output tok Qwen/Qwen3.8-27B | stop=max_tokens glm-5.3-flash | stop=max_tokens Qwen/Qwen3.8-27B | total per run s glm-5.3-flash | total per run s Qwen/Qwen3.8-27B |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo | memory_extract | 1.0 | 1.0 | 10.1 | 7.1 | 0.70x | 303 | 409 | 416 | 182 | 0% | 0% | 10.1 | 7.1 |
| FQA-solo | memory_extract | 1.0 | 1.0 | 22.2 | 38.4 | 1.73x | 2,119 | 2,144 | 1,000 | 1,000 | 100% | 100% | 22.2 | 38.4 |
| MATH-solo | memory_extract | 1.0 | 1.0 | 19.1 | 27.8 | 1.46x | 1,756 | 1,906 | 761 | 723 | 0% | 33% | 19.1 | 27.8 |
| CODE-team | memory_extract | 5.4 | 5.2 | 18.1 | 20.0 | 1.10x | 774 | 766 | 770 | 502 | 56% | 12% | 97.6 | 103.8 |
| CODE-team | memory_recall | 2.4 | - | 4.9 | - | - | 332 | - | 190 | - | 75% | - | 11.7 | - |
| FQA-team | memory_extract | 3.8 | 4.2 | 20.6 | 34.9 | 1.70x | 1,458 | 1,537 | 899 | 884 | 74% | 81% | 78.2 | 146.8 |
| MATH-team | compaction_summary | - | 0.2 | - | 51.6 | - | - | 3,002 | - | 1,260 | - | 0% | - | 10.3 |
| MATH-team | memory_extract | 4.6 | 5.0 | 13.1 | 12.4 | 0.94x | 1,443 | 1,426 | 585 | 306 | 35% | 4% | 60.3 | 61.9 |


## T9. Streaming delivery pattern (first block = client time to the first content block; tail = first block to end of stream; tail < 0.1 s = whole response in one burst)

| kind | response ends with | calls glm-5.3-flash | calls Qwen/Qwen3.8-27B | output tok glm-5.3-flash | output tok Qwen/Qwen3.8-27B | first block s glm-5.3-flash | first block s Qwen/Qwen3.8-27B | tail s glm-5.3-flash | tail s Qwen/Qwen3.8-27B | tail share glm-5.3-flash | tail share Qwen/Qwen3.8-27B | tail<0.1s glm-5.3-flash | tail<0.1s Qwen/Qwen3.8-27B | tail tok/s glm-5.3-flash | tail tok/s Qwen/Qwen3.8-27B |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| lead | tool_use | 149 | 193 | 396 | 582 | 5.73 | 0.89 | 4.64 | 22.43 | 45% | 96% | 29% | 0% | 85 | 26 |
| lead | end_turn | 78 | 81 | 308 | 296 | 5.66 | 1.18 | 3.75 | 11.51 | 40% | 91% | 50% | 0% | 82 | 26 |
| teammate | tool_use | 173 | 177 | 396 | 716 | 5.60 | 0.65 | 5.43 | 29.50 | 49% | 98% | 59% | 0% | 73 | 24 |
| teammate | end_turn | 46 | 50 | 451 | 324 | 5.99 | 0.37 | 5.24 | 13.33 | 47% | 97% | 33% | 0% | 86 | 24 |


## T10. Direct provider probes (scripts/provider_probe.py: prompt-size sweep, cached resends, decode rate, cache semantics)

| probe | glm-5.3-flash | Qwen/Qwen3.8-27B |
|---|---:|---:|
| probe calls | - | 27 |
| first block = a + b x uncached tokens (fresh calls) | - | 0.00 s + 0.1746 ms/tok (r2 1.00) -> 5,729 tok/s |
| decode tok/s median (min-max) | - | 26 (25-26) |
| responses delivered in one burst (tail < 0.1 s) | - | 0% |
| identical resend 1 | - | 35,280 cached: 6.31s -> 0.42s = 0.1668 ms saved/tok |
| identical resend 2 | - | 12,544 cached: 2.11s -> 0.27s = 0.1471 ms saved/tok |
| cache case 1 first send, unique prefix | - | 0.0% cached, first block 5.21 s |
| cache case 2 identical resend | - | 99.4% cached, first block 0.37 s |
| cache case 3 new prefix, same body | - | 0.0% cached, first block 5.21 s |
| cache case 4 unique block inserted mid-prompt | - | 0.0% cached, first block 5.21 s |
| cache case 5 document never sent before | - | 0.0% cached, first block 11.73 s |
| cache case 6 that document again, new prefix | - | 0.0% cached, first block 11.72 s |
