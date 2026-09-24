# Latency breakdown: glm-5.3-flash vs Qwen/Qwen3.8-27B

A = `glm-5.3-flash` (/mnt/home/yqi10/learn-claude-code/s15_integrated_harness/traces/latency_profiling), B = `Qwen/Qwen3.8-27B` (/mnt/home/yqi10/learn-claude-code/s15_integrated_harness/traces/latency_profiling_qwen_opt). Both analysed with `scripts/latency_breakdown.py` (same round reconstruction, `uncached` = tokens the provider computed = input_tokens + cache_creation_input_tokens). Means are over pooled rounds or calls unless a row says otherwise; B/A is the ratio of the two means.

## T0. Setup

| item | glm-5.3-flash | Qwen/Qwen3.8-27B |
|---|---:|---:|
| model | glm-5.3-flash | Qwen/Qwen3.8-27B |
| endpoint | https://api.z.ai/api/anthropic | http://127.0.0.1:50081 |
| runs | CODE-solo x3, FQA-solo x3, MATH-solo x3, CODE-team x5, FQA-team x5, MATH-team x5 | CODE-solo x3, FQA-solo x3, MATH-solo x3, CODE-team x5, FQA-team x5, MATH-team x5 |
| dates | 2026-09-13 | 2026-09-22 |
| harness commit | 64d1b11bc9 | 0cbddf885d |
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
| CODE-solo | 3/3 | 3/3 | 9/9 | 9/9 | 161 +- 53 | 178 +- 28 | 1.10x | 0% | 0% | 0 | 0 | 0 | 0 | 0 | 0 |
| FQA-solo | 3/3 | 3/3 | coverage 0.97 +- 0.00 | coverage 0.96 +- 0.02 | 101 +- 28 | 58 +- 7 | 0.57x | 0% | 0% | 0 | 0 | 0 | 0 | 0 | 0 |
| MATH-solo | 3/3 | 3/3 | 9/9 | 9/9 | 118 +- 3 | 147 +- 27 | 1.25x | 0% | 0% | 0 | 0 | 0 | 0 | 4 | 0 |
| CODE-team | 5/5 | 5/5 | 15/15 | 15/15 | 215 +- 32 | 182 +- 42 | 0.84x | 0% | 0% | 0 | 0 | 0 | 0 | 5 | 0 |
| FQA-team | 5/5 | 5/5 | coverage 0.99 +- 0.01 | coverage 0.98 +- 0.02 | 180 +- 21 | 137 +- 11 | 0.76x | 0% | 0% | 0 | 0 | 0 | 0 | 4 | 0 |
| MATH-team | 5/5 | 5/5 | 15/15 | 15/15 | 165 +- 12 | 268 +- 34 | 1.63x | 0% | 0% | 0 | 0 | 0 | 0 | 11 | 5 |


## T2. One agentic round (prep / model / tools / other add up to the round; instrument = profiler's vLLM metrics scrape, kept out of the buckets)

Lead rounds (team and solo):

| metric | CODE-solo lead glm-5.3-flash | CODE-solo lead Qwen/Qwen3.8-27B | FQA-solo lead glm-5.3-flash | FQA-solo lead Qwen/Qwen3.8-27B | MATH-solo lead glm-5.3-flash | MATH-solo lead Qwen/Qwen3.8-27B | CODE-team lead glm-5.3-flash | CODE-team lead Qwen/Qwen3.8-27B | FQA-team lead glm-5.3-flash | FQA-team lead Qwen/Qwen3.8-27B | MATH-team lead glm-5.3-flash | MATH-team lead Qwen/Qwen3.8-27B |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| rounds | 47 | 58 | 11 | 7 | 7 | 7 | 56 | 60 | 51 | 63 | 55 | 56 |
| gross per round, mean (s) | 9.5 | 9.1 | 21.0 | 24.1 | 41.5 | 62.2 | 7.7 | 7.2 | 9.7 | 9.0 | 7.6 | 12.9 |
| gross median (s) | 6.1 | 3.9 | 9.0 | 8.2 | 42.5 | 52.4 | 6.9 | 4.7 | 8.3 | 5.1 | 6.6 | 7.2 |
| gross p90 (s) | 20.2 | 23.3 | 57.6 | 49.8 | 69.7 | 98.4 | 12.6 | 18.0 | 14.2 | 24.9 | 11.7 | 32.1 |
| context preparation share | 0.1% | 0.1% | 0.0% | 0.0% | 0.0% | 0.0% | 7.0% | 0.1% | 0.1% | 2.7% | 0.1% | 0.1% |
| agent call share | 99.4% | 98.2% | 99.8% | 99.7% | 99.9% | 99.8% | 92.9% | 99.4% | 99.8% | 96.9% | 99.8% | 99.6% |
| tool execution share | 0.5% | 1.5% | 0.1% | 0.2% | 0.1% | 0.2% | 0.1% | 0.3% | 0.1% | 0.2% | 0.1% | 0.2% |
| other share | 0.0% | 0.1% | 0.0% | 0.1% | 0.0% | 0.0% | 0.0% | 0.1% | 0.0% | 0.0% | 0.0% | 0.0% |
| instrument share | 0.0% | 0.2% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.2% | 0.0% | 0.1% | 0.0% | 0.1% |
| prep mean (ms) | 6.4 | 10.5 | 8.4 | 11.0 | 4.6 | 6.3 | 542.1 | 7.2 | 7.3 | 239.9 | 6.5 | 8.2 |
| tools mean (ms) | 45.3 | 133.3 | 23.7 | 39.6 | 22.8 | 109.3 | 8.5 | 23.6 | 9.1 | 20.5 | 7.8 | 25.5 |
| tool calls per round | 1.09 | 1.09 | 1.73 | 1.71 | 0.86 | 0.57 | 1.20 | 1.15 | 1.37 | 1.03 | 1.24 | 1.23 |
| client first block mean (s) | 5.2 | 0.5 | 6.0 | 1.5 | 5.7 | 0.5 | 5.5 | 0.5 | 6.1 | 0.5 | 5.9 | 0.4 |
| client streamed tail mean (s) | 4.3 | 8.4 | 15.0 | 22.5 | 35.8 | 61.6 | 1.6 | 6.7 | 3.6 | 8.2 | 1.6 | 12.5 |
| turn-end work per lead turn (s) | 10.1 | 0.0 | 22.2 | 0.0 | 19.1 | 0.0 | 19.1 | 0.0 | 20.6 | -0.5 | 13.1 | 0.1 |
| turn-end work amortised per lead round (s) | 0.6 | 0.0 | 6.1 | 0.0 | 8.2 | 0.0 | 9.2 | 0.0 | 7.7 | -0.2 | 5.5 | 0.1 |
| (prep + turn-end) share of lead-side time | 6.4% | 0.1% | 22.4% | 0.1% | 16.5% | 0.0% | 57.6% | 0.3% | 44.2% | 0.1% | 42.1% | 0.5% |

Teammate rounds (team runs):

| metric | CODE teammate glm-5.3-flash | CODE teammate Qwen/Qwen3.8-27B | FQA teammate glm-5.3-flash | FQA teammate Qwen/Qwen3.8-27B | MATH teammate glm-5.3-flash | MATH teammate Qwen/Qwen3.8-27B |
|---|---:|---:|---:|---:|---:|---:|
| rounds | 112 | 94 | 64 | 68 | 43 | 42 |
| gross per round, mean (s) | 10.0 | 11.4 | 8.9 | 8.8 | 17.2 | 41.5 |
| gross median (s) | 5.5 | 4.0 | 6.2 | 4.8 | 12.1 | 33.0 |
| gross p90 (s) | 15.0 | 26.3 | 17.0 | 21.4 | 34.7 | 79.5 |
| context preparation share | 0.0% | 22.0% | 0.0% | 27.8% | 0.0% | 24.3% |
| agent call share | 99.6% | 76.9% | 99.8% | 71.0% | 99.9% | 75.5% |
| tool execution share | 0.4% | 0.9% | 0.2% | 0.4% | 0.1% | 0.1% |
| other share | 0.0% | 0.0% | 0.0% | 0.1% | 0.0% | 0.0% |
| instrument share | 0.0% | 0.2% | 0.0% | 0.7% | 0.0% | 0.0% |
| prep mean (ms) | 2.8 | 2,505.5 | 4.2 | 2,438.8 | 1.9 | 10,101.0 |
| tools mean (ms) | 36.6 | 98.9 | 14.2 | 30.9 | 11.9 | 44.9 |
| tool calls per round | 1.04 | 1.03 | 1.12 | 1.12 | 0.79 | 0.64 |
| client first block mean (s) | 5.6 | 0.5 | 5.5 | 0.9 | 6.1 | 0.3 |
| client streamed tail mean (s) | 4.4 | 8.2 | 3.3 | 5.3 | 11.0 | 31.0 |


## T3. Inside the agent call

Three-parameter regression over agent calls: model-call duration = a + b x computed prompt tokens + c x output tokens (least squares).

| fit over | calls glm-5.3-flash | calls Qwen/Qwen3.8-27B | a fixed s glm-5.3-flash | a fixed s Qwen/Qwen3.8-27B | b ms/computed tok glm-5.3-flash | b ms/computed tok Qwen/Qwen3.8-27B | implied prefill tok/s glm-5.3-flash | implied prefill tok/s Qwen/Qwen3.8-27B | c ms/output tok glm-5.3-flash | c ms/output tok Qwen/Qwen3.8-27B | r2 glm-5.3-flash | r2 Qwen/Qwen3.8-27B |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| all agent calls | 446 | 455 | 3.72 | 0.23 | 0.0327 | 0.2260 | 30,593 | 4,425 | 17.44 | 38.86 | 0.96 | 1.00 |
| lead | 227 | 251 | 3.16 | 0.32 | 0.0856 | 0.1860 | 11,689 | 5,375 | 17.82 | 38.01 | 0.95 | 1.00 |
| teammate | 219 | 204 | 4.10 | 0.22 | -0.0024 | 0.2503 | - | 3,995 | 17.13 | 39.54 | 0.97 | 1.00 |
| responses ending in tool_use | 322 | 324 | 3.76 | 0.16 | -0.0037 | 0.2558 | - | 3,909 | 17.61 | 38.92 | 0.96 | 1.00 |
| responses ending in end_turn | 124 | 131 | 3.57 | 0.34 | 0.1766 | 0.1647 | 5,663 | 6,072 | 16.22 | 38.70 | 0.96 | 1.00 |

Share of call time by the pooled coefficients, per group and kind:

| metric | CODE-solo lead glm-5.3-flash | CODE-solo lead Qwen/Qwen3.8-27B | FQA-solo lead glm-5.3-flash | FQA-solo lead Qwen/Qwen3.8-27B | MATH-solo lead glm-5.3-flash | MATH-solo lead Qwen/Qwen3.8-27B | CODE-team lead glm-5.3-flash | CODE-team lead Qwen/Qwen3.8-27B | CODE-team teammate glm-5.3-flash | CODE-team teammate Qwen/Qwen3.8-27B | FQA-team lead glm-5.3-flash | FQA-team lead Qwen/Qwen3.8-27B | FQA-team teammate glm-5.3-flash | FQA-team teammate Qwen/Qwen3.8-27B | MATH-team lead glm-5.3-flash | MATH-team lead Qwen/Qwen3.8-27B | MATH-team teammate glm-5.3-flash | MATH-team teammate Qwen/Qwen3.8-27B |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| calls | 47 | 58 | 11 | 7 | 7 | 7 | 56 | 60 | 112 | 94 | 51 | 63 | 64 | 68 | 55 | 56 | 43 | 42 |
| fixed (network, queue, start-up) | 39.2% | 2.6% | 17.7% | 1.0% | 9.0% | 0.4% | 51.8% | 3.2% | 37.2% | 2.6% | 38.3% | 2.6% | 41.8% | 3.7% | 49.2% | 1.8% | 21.7% | 0.7% |
| prefill of computed tokens | 1.8% | 1.9% | 1.9% | 6.7% | 0.2% | 0.6% | 1.4% | 2.1% | 0.3% | 2.2% | 1.3% | 1.9% | 1.0% | 8.4% | 1.5% | 1.5% | 0.2% | 0.7% |
| decode | 61.2% | 98.7% | 78.4% | 94.6% | 88.5% | 101.4% | 56.2% | 96.9% | 58.7% | 94.8% | 61.7% | 94.7% | 58.0% | 82.4% | 49.7% | 98.3% | 77.3% | 97.0% |
| residual | -2.2% | -3.2% | 2.0% | -2.2% | 2.3% | -2.4% | -9.4% | -2.2% | 3.8% | 0.5% | -1.3% | 0.7% | -0.8% | 5.6% | -0.4% | -1.6% | 0.9% | 1.5% |

Server-side measurement from vLLM /metrics (only for the side served by vLLM; exact-attribution calls; gap = client duration - server e2e = HTTP/SDK overhead):

| metric | CODE-solo lead glm-5.3-flash | CODE-solo lead Qwen/Qwen3.8-27B | FQA-solo lead glm-5.3-flash | FQA-solo lead Qwen/Qwen3.8-27B | MATH-solo lead glm-5.3-flash | MATH-solo lead Qwen/Qwen3.8-27B | CODE-team lead glm-5.3-flash | CODE-team lead Qwen/Qwen3.8-27B | CODE-team teammate glm-5.3-flash | CODE-team teammate Qwen/Qwen3.8-27B | FQA-team lead glm-5.3-flash | FQA-team lead Qwen/Qwen3.8-27B | FQA-team teammate glm-5.3-flash | FQA-team teammate Qwen/Qwen3.8-27B | MATH-team lead glm-5.3-flash | MATH-team lead Qwen/Qwen3.8-27B | MATH-team teammate glm-5.3-flash | MATH-team teammate Qwen/Qwen3.8-27B |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| calls with server metrics | - | 58 | - | 7 | - | 7 | - | 60 | - | 94 | - | 63 | - | 68 | - | 56 | - | 42 |
| exactly attributable share | - | 100.0% | - | 100.0% | - | 100.0% | - | 100.0% | - | 100.0% | - | 98.4% | - | 95.6% | - | 98.2% | - | 100.0% |
| server prefill mean (s) | - | 0.194 | - | 1.217 | - | 0.329 | - | 0.177 | - | 0.217 | - | 0.215 | - | 0.451 | - | 0.221 | - | 0.251 |
| server decode mean (s) | - | 8.6 | - | 22.7 | - | 61.6 | - | 6.8 | - | 8.5 | - | 8.4 | - | 5.8 | - | 12.6 | - | 31.0 |
| server queue mean (s) | - | 0.000 | - | 0.000 | - | 0.000 | - | 0.000 | - | 0.000 | - | 0.000 | - | 0.000 | - | 0.000 | - | 0.000 |
| server e2e mean (s) | - | 8.9 | - | 24.0 | - | 62.0 | - | 7.1 | - | 8.7 | - | 8.8 | - | 6.3 | - | 12.9 | - | 31.3 |
| client gap mean (s) | - | 0.059 | - | 0.044 | - | 0.039 | - | 0.059 | - | 0.043 | - | 0.091 | - | 0.076 | - | 0.055 | - | 0.025 |
| client gap median (s) | - | 0.060 | - | 0.044 | - | 0.041 | - | 0.059 | - | 0.043 | - | 0.058 | - | 0.038 | - | 0.056 | - | 0.024 |
| server prefill share of client call | - | 2.2% | - | 5.1% | - | 0.5% | - | 2.5% | - | 2.5% | - | 2.4% | - | 7.1% | - | 1.7% | - | 0.8% |
| server decode share of client call | - | 96.6% | - | 94.4% | - | 99.3% | - | 96.0% | - | 96.4% | - | 94.7% | - | 92.0% | - | 97.4% | - | 99.0% |
| server queue share of client call | - | 0.0% | - | 0.0% | - | 0.0% | - | 0.0% | - | 0.0% | - | 0.0% | - | 0.0% | - | 0.0% | - | 0.0% |
| gap share of client call | - | 0.7% | - | 0.2% | - | 0.1% | - | 0.8% | - | 0.5% | - | 1.0% | - | 1.2% | - | 0.4% | - | 0.1% |
| prefill tok/s (computed tokens / prefill s) | - | 3,927 | - | 5,851 | - | 5,029 | - | 3,738 | - | 3,852 | - | 3,466 | - | 5,079 | - | 4,018 | - | 4,137 |
| decode ms per token | - | 38.2 | - | 38.9 | - | 38.1 | - | 38.7 | - | 39.7 | - | 38.9 | - | 43.8 | - | 38.6 | - | 39.7 |
| finished by length (max_tokens) share | - | 0.0% | - | 0.0% | - | 0.0% | - | 0.0% | - | 0.0% | - | 0.0% | - | 0.0% | - | 0.0% | - | 0.0% |
| requests running at finish, mean | - | 0.0 | - | 0.0 | - | 0.0 | - | 0.6 | - | 0.9 | - | 0.7 | - | 1.5 | - | 0.7 | - | 0.9 |
| KV cache usage at finish, mean | - | 0.0% | - | 0.0% | - | 0.0% | - | 0.8% | - | 1.2% | - | 1.3% | - | 2.5% | - | 0.7% | - | 1.0% |
| metrics scrape per call (ms) | - | 18.4 | - | 18.9 | - | 19.2 | - | 18.5 | - | 29.6 | - | 19.2 | - | 67.5 | - | 36.9 | - | 28.3 |


## T4. Tokens per agent call

Lead calls (team and solo):

| metric | CODE-solo lead glm-5.3-flash | CODE-solo lead Qwen/Qwen3.8-27B | FQA-solo lead glm-5.3-flash | FQA-solo lead Qwen/Qwen3.8-27B | MATH-solo lead glm-5.3-flash | MATH-solo lead Qwen/Qwen3.8-27B | CODE-team lead glm-5.3-flash | CODE-team lead Qwen/Qwen3.8-27B | FQA-team lead glm-5.3-flash | FQA-team lead Qwen/Qwen3.8-27B | MATH-team lead glm-5.3-flash | MATH-team lead Qwen/Qwen3.8-27B |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| calls | 47 | 58 | 11 | 7 | 7 | 7 | 56 | 60 | 51 | 63 | 55 | 56 |
| prompt tokens, mean | 7,136 | 7,670 | 13,801 | 15,071 | 4,533 | 4,676 | 4,796 | 4,987 | 5,547 | 5,690 | 5,267 | 6,508 |
| prompt tokens, median | 6,686 | 8,052 | 18,183 | 18,665 | 5,223 | 5,247 | 4,937 | 5,146 | 5,490 | 5,731 | 5,267 | 6,424 |
| prompt chars, mean (tokenizer-independent) | 31,518 | 29,523 | 57,396 | 58,656 | 15,678 | 14,109 | 21,414 | 18,742 | 24,594 | 21,710 | 21,326 | 23,448 |
| chars per prompt token | 4.42 | 3.85 | 4.16 | 3.89 | 3.46 | 3.02 | 4.46 | 3.76 | 4.43 | 3.82 | 4.05 | 3.60 |
| output chars, mean (tokenizer-independent) | 1,325 | 758 | 3,726 | 2,067 | 4,958 | 3,209 | 904 | 538 | 1,397 | 706 | 765 | 1,004 |
| chars per output token | 3.99 | 3.34 | 3.95 | 3.54 | 2.35 | 1.98 | 3.91 | 3.03 | 4.07 | 3.31 | 3.56 | 3.09 |
| prompt tokens, max | 12,912 | 12,177 | 25,504 | 25,715 | 6,790 | 6,449 | 6,459 | 6,527 | 8,437 | 8,401 | 8,387 | 10,608 |
| computed (uncached) tokens, mean | 5,249 | 762 | 12,073 | 7,119 | 2,722 | 1,652 | 3,099 | 662 | 3,766 | 750 | 3,577 | 880 |
| provider cache-read share | 26.4% | 90.1% | 12.5% | 52.8% | 39.9% | 64.7% | 35.4% | 86.7% | 32.1% | 86.8% | 32.1% | 86.5% |
| cache-creation share (vLLM only) | 0.0% | 5.3% | 0.0% | 44.6% | 0.0% | 31.1% | 0.0% | 5.5% | 0.0% | 7.0% | 0.0% | 8.4% |
| output tokens, mean | 332 | 227 | 943 | 584 | 2,107 | 1,618 | 231 | 178 | 343 | 213 | 215 | 325 |
| output tokens, median | 116 | 92 | 302 | 196 | 2,387 | 1,369 | 143 | 102 | 189 | 111 | 152 | 176 |
| output tokens, max | 2,121 | 1,570 | 4,035 | 1,327 | 4,025 | 3,073 | 771 | 709 | 1,297 | 843 | 690 | 1,486 |
| thinking share of output chars | 37.8% | 0.0% | 49.3% | 0.0% | 68.0% | 56.5% | 22.1% | 0.0% | 36.8% | 0.0% | 25.2% | 44.9% |
| first block is thinking | 46.8% | 0.0% | 100.0% | 0.0% | 100.0% | 100.0% | 71.4% | 0.0% | 72.5% | 0.0% | 67.3% | 100.0% |
| prompt growth per round, median (tok) | 285 | 304 | 6,954 | 18,947 | 2,688 | 2,216 | 242 | 179 | 580 | 331 | 360 | 580 |
| prompt growth per round, mean (tok) | 496 | 433 | 8,321 | 16,852 | 2,412 | 2,010 | 320 | 261 | 565 | 392 | 461 | 584 |
| stop = tool_use share | 93.6% | 94.8% | 72.7% | 57.1% | 57.1% | 57.1% | 51.8% | 58.3% | 62.7% | 57.1% | 58.2% | 55.4% |
| stop = max_tokens share | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| model call mean (s) | 9.5 | 8.9 | 21.0 | 24.0 | 41.5 | 62.0 | 7.2 | 7.1 | 9.7 | 8.8 | 7.6 | 12.9 |
| streamed tail tok/s (pooled) | 77 | 27 | 63 | 26 | 59 | 26 | 142 | 27 | 94 | 26 | 132 | 26 |

Teammate calls (team runs):

| metric | CODE teammate glm-5.3-flash | CODE teammate Qwen/Qwen3.8-27B | FQA teammate glm-5.3-flash | FQA teammate Qwen/Qwen3.8-27B | MATH teammate glm-5.3-flash | MATH teammate Qwen/Qwen3.8-27B |
|---|---:|---:|---:|---:|---:|---:|
| calls | 112 | 94 | 64 | 68 | 43 | 42 |
| prompt tokens, mean | 3,717 | 3,614 | 5,373 | 6,948 | 1,921 | 2,233 |
| prompt tokens, median | 3,407 | 3,317 | 3,348 | 7,101 | 1,582 | 2,019 |
| prompt chars, mean (tokenizer-independent) | 16,250 | 13,333 | 22,922 | 27,279 | 6,527 | 6,202 |
| chars per prompt token | 4.37 | 3.69 | 4.27 | 3.93 | 3.40 | 2.78 |
| output chars, mean (tokenizer-independent) | 1,336 | 753 | 1,175 | 420 | 1,905 | 1,711 |
| chars per output token | 3.97 | 3.52 | 3.97 | 3.19 | 2.51 | 2.19 |
| prompt tokens, max | 8,223 | 8,205 | 17,305 | 17,216 | 4,855 | 5,052 |
| computed (uncached) tokens, mean | 905 | 837 | 2,586 | 2,313 | 983 | 1,038 |
| provider cache-read share | 75.7% | 76.9% | 51.9% | 66.7% | 48.8% | 53.5% |
| cache-creation share (vLLM only) | 0.0% | 14.5% | 0.0% | 26.4% | 0.0% | 29.3% |
| output tokens, mean | 337 | 214 | 296 | 132 | 760 | 782 |
| output tokens, median | 68 | 68 | 95 | 67 | 518 | 596 |
| output tokens, max | 5,200 | 1,562 | 1,585 | 505 | 3,173 | 3,456 |
| thinking share of output chars | 51.0% | 0.0% | 51.1% | 0.0% | 56.1% | 61.3% |
| first block is thinking | 57.1% | 0.0% | 75.0% | 0.0% | 88.4% | 100.0% |
| prompt growth per round, median (tok) | 276 | 391 | 847 | 399 | 546 | 653 |
| prompt growth per round, mean (tok) | 616 | 589 | 2,959 | 2,357 | 845 | 895 |
| stop = tool_use share | 86.6% | 84.0% | 75.0% | 77.9% | 65.1% | 64.3% |
| stop = max_tokens share | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| model call mean (s) | 10.0 | 8.8 | 8.9 | 6.2 | 17.1 | 31.3 |
| streamed tail tok/s (pooled) | 77 | 26 | 89 | 25 | 69 | 25 |


## T5. Teammate input redundancy (ranges over the runs of a group)

| metric | CODE glm-5.3-flash | CODE Qwen/Qwen3.8-27B | FQA glm-5.3-flash | FQA Qwen/Qwen3.8-27B | MATH glm-5.3-flash | MATH Qwen/Qwen3.8-27B |
|---|---:|---:|---:|---:|---:|---:|
| re-sent: prompt tokens already sent in the same teammate's previous call | 76-86% | 75-84% | 33-58% | 67-73% | 45-63% | 35-59% |
| served by the provider cache (usage cache-read share) | 68-79% | 72-79% | 27-58% | 65-70% | 44-52% | 44-58% |
| server prefix-cache hit rate, whole run (vLLM only) | - | 75-81% | - | 67-75% | - | 70-78% |
| server cached share of all prompt tokens, whole run (vLLM only) | - | 75-81% | - | 67-75% | - | 70-78% |
| file bytes another teammate fetched first (exact (file,line)) | 0% | 0% | 12-23% | 12-23% | - | - |
| cross-redundant share of teammate prompt tokens | 0% | 0% | 8-27% | 11-20% | 0% | 0% |
| file content share of teammate prompt tokens | 21-30% | 28-47% | 76-86% | 80-88% | 0% | 0% |
| lead: re-sent share | 87-91% | 89-90% | 84-88% | 87-90% | 85-89% | 84-89% |
| lead: cache-read share | 28-42% | 81-88% | 20-38% | 79-89% | 29-36% | 84-90% |


## T6. Agent runs per task and end-to-end time

| metric | CODE-solo glm-5.3-flash | CODE-solo Qwen/Qwen3.8-27B | B/A | FQA-solo glm-5.3-flash | FQA-solo Qwen/Qwen3.8-27B | B/A | MATH-solo glm-5.3-flash | MATH-solo Qwen/Qwen3.8-27B | B/A | CODE-team glm-5.3-flash | CODE-team Qwen/Qwen3.8-27B | B/A | FQA-team glm-5.3-flash | FQA-team Qwen/Qwen3.8-27B | B/A | MATH-team glm-5.3-flash | MATH-team Qwen/Qwen3.8-27B | B/A |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| runs | 3 | 3 | 1.00x | 3 | 3 | 1.00x | 3 | 3 | 1.00x | 5 | 5 | 1.00x | 5 | 5 | 1.00x | 5 | 5 | 1.00x |
| teammate rounds per task, mean | - | - | - | - | - | - | - | - | - | 7.5 | 6.3 | 0.84x | 4.0 | 4.5 | 1.13x | 2.9 | 2.8 | 0.98x |
| teammate rounds per task, median | - | - | - | - | - | - | - | - | - | 6 | 5 | 0.83x | 4 | 4 | 1.00x | 3 | 3 | 1.00x |
| teammate rounds per task, max | - | - | - | - | - | - | - | - | - | 15 | 11 | 0.73x | 6 | 6 | 1.00x | 5 | 4 | 0.80x |
| teammate active time per task (s) | - | - | - | - | - | - | - | - | - | 75 | 71 | 0.95x | 36 | 40 | 1.11x | 49 | 116 | 2.36x |
| teammates per run | 0.0 | 0.0 | - | 0.0 | 0.0 | - | 0.0 | 0.0 | - | 3.0 | 3.0 | 1.00x | 3.0 | 3.0 | 1.00x | 3.0 | 3.0 | 1.00x |
| teammate tasks per run | 0.0 | 0.0 | - | 0.0 | 0.0 | - | 0.0 | 0.0 | - | 3.0 | 3.0 | 1.00x | 3.2 | 3.0 | 0.94x | 3.0 | 3.0 | 1.00x |
| lead rounds per run | 15.7 | 19.3 | 1.23x | 3.7 | 2.3 | 0.64x | 2.3 | 2.3 | 1.00x | 11.2 | 12.0 | 1.07x | 10.2 | 12.6 | 1.24x | 11.0 | 11.2 | 1.02x |
| lead turns per run | 1.0 | 1.0 | 1.00x | 1.0 | 1.0 | 1.00x | 1.0 | 1.0 | 1.00x | 5.4 | 5.0 | 0.93x | 3.8 | 5.4 | 1.42x | 4.6 | 5.0 | 1.09x |
| model calls per run (incl. memory) | 16.7 | 20.3 | 1.22x | 4.7 | 3.3 | 0.71x | 3.3 | 3.3 | 1.00x | 41.4 | 35.8 | 0.86x | 26.8 | 32.0 | 1.19x | 24.2 | 24.8 | 1.02x |
| wall per run (s) | 161 | 178 | 1.10x | 101 | 58 | 0.57x | 118 | 147 | 1.25x | 215 | 182 | 0.84x | 180 | 137 | 0.76x | 165 | 268 | 1.63x |
| wall per task (s) | 54 | 59 | 1.10x | 34 | 19 | 0.57x | 39 | 49 | 1.25x | 72 | 61 | 0.84x | 60 | 46 | 0.76x | 55 | 89 | 1.63x |
| lead busy share of wall | 98.7% | 98.9% | 1.00x | 98.0% | 96.5% | 0.98x | 98.3% | 98.7% | 1.00x | 88.2% | 47.4% | 0.54x | 98.3% | 81.0% | 0.82x | 87.0% | 54.1% | 0.62x |
| of which lead agent calls (s) | 148 | 173 | 1.16x | 77 | 56 | 0.73x | 97 | 145 | 1.49x | 80 | 85 | 1.06x | 99 | 110 | 1.12x | 83 | 144 | 1.73x |
| of which turn-end memory work (s) | 10 | 0 | 0.00x | 22 | 0 | 0.00x | 19 | 0 | 0.00x | 103 | 0 | 0.00x | 78 | -3 | -0.04x | 60 | 1 | 0.01x |
| teammate active share of wall | 0.0% | 0.0% | - | 0.0% | 0.0% | - | 0.0% | 0.0% | - | 51.7% | 62.4% | 1.21x | 26.4% | 34.1% | 1.29x | 50.2% | 58.8% | 1.17x |
| lead/teammate overlap (s) | 0 | 0 | - | 0 | 0 | - | 0 | 0 | - | 89 | 20 | 0.22x | 47 | 23 | 0.49x | 64 | 37 | 0.58x |
| idle share of wall | 1.3% | 1.1% | 0.89x | 2.0% | 3.5% | 1.78x | 1.7% | 1.3% | 0.80x | 1.3% | 1.0% | 0.76x | 1.4% | 1.8% | 1.26x | 1.8% | 0.9% | 0.50x |
| denied tool calls per run | 0.0 | 0.0 | - | 0.0 | 0.0 | - | 1.3 | 0.0 | 0.00x | 1.0 | 0.0 | 0.00x | 0.8 | 0.0 | 0.00x | 2.2 | 1.0 | 0.45x |


## T7. Stability across repetitions (mean +- sd over the runs of a group)

| group | runs A / B | wall s glm-5.3-flash | wall s Qwen/Qwen3.8-27B | lead rounds/run glm-5.3-flash | lead rounds/run Qwen/Qwen3.8-27B | model calls/run glm-5.3-flash | model calls/run Qwen/Qwen3.8-27B | lead gross/round s glm-5.3-flash | lead gross/round s Qwen/Qwen3.8-27B | teammate gross/round s glm-5.3-flash | teammate gross/round s Qwen/Qwen3.8-27B | prompt tok/call glm-5.3-flash | prompt tok/call Qwen/Qwen3.8-27B | output tok/call glm-5.3-flash | output tok/call Qwen/Qwen3.8-27B | score glm-5.3-flash | score Qwen/Qwen3.8-27B |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo | 3 / 3 | 161 +- 53 | 178 +- 28 | 15.7 +- 3.8 | 19.3 +- 3.8 | 16.7 +- 3.8 | 20.3 +- 3.8 | 9.5 +- 2.5 | 9.1 +- 0.4 | - | - | 6,949 +- 1,471 | 7,594 +- 589 | 332 +- 115 | 228 +- 10 | 3.00 +- 0.00 | 3.00 +- 0.00 |
| FQA-solo | 3 / 3 | 101 +- 28 | 58 +- 7 | 3.7 +- 1.5 | 2.3 +- 0.6 | 4.7 +- 1.5 | 3.3 +- 0.6 | 23.7 +- 10.6 | 24.5 +- 3.2 | - | - | 13,666 +- 1,173 | 14,937 +- 810 | 1,060 +- 489 | 597 +- 98 | 0.97 +- 0.00 | 0.96 +- 0.02 |
| MATH-solo | 3 / 3 | 118 +- 3 | 147 +- 27 | 2.3 +- 0.6 | 2.3 +- 0.6 | 3.3 +- 0.6 | 3.3 +- 0.6 | 42.8 +- 8.9 | 64.2 +- 18.2 | - | - | 4,497 +- 446 | 4,641 +- 368 | 2,210 +- 647 | 1,672 +- 477 | 3.00 +- 0.00 | 3.00 +- 0.00 |
| CODE-team | 5 / 5 | 215 +- 32 | 182 +- 42 | 11.2 +- 1.6 | 12.0 +- 0.7 | 41.4 +- 5.4 | 35.8 +- 2.5 | 7.9 +- 1.4 | 7.2 +- 0.7 | 10.3 +- 1.9 | 11.4 +- 1.4 | 4,076 +- 247 | 4,133 +- 294 | 307 +- 72 | 199 +- 27 | 3.00 +- 0.00 | 3.00 +- 0.00 |
| FQA-team | 5 / 5 | 180 +- 21 | 137 +- 11 | 10.2 +- 1.3 | 12.6 +- 1.3 | 26.8 +- 4.1 | 32.0 +- 1.0 | 9.8 +- 1.3 | 9.0 +- 0.8 | 8.9 +- 0.8 | 8.8 +- 1.2 | 5,471 +- 584 | 6,359 +- 888 | 321 +- 58 | 171 +- 14 | 0.99 +- 0.01 | 0.98 +- 0.02 |
| MATH-team | 5 / 5 | 165 +- 12 | 268 +- 34 | 11.0 +- 1.6 | 11.2 +- 1.6 | 24.2 +- 2.6 | 24.8 +- 1.6 | 7.6 +- 0.5 | 12.9 +- 3.5 | 17.3 +- 1.6 | 41.9 +- 5.5 | 3,780 +- 280 | 4,683 +- 562 | 457 +- 61 | 523 +- 81 | 3.00 +- 0.00 | 3.00 +- 0.00 |


## T8. Auxiliary model calls made by the harness (memory recall / extraction, summary compaction)

| group | purpose | calls/run glm-5.3-flash | calls/run Qwen/Qwen3.8-27B | mean s glm-5.3-flash | mean s Qwen/Qwen3.8-27B | B/A | prompt tok glm-5.3-flash | prompt tok Qwen/Qwen3.8-27B | output tok glm-5.3-flash | output tok Qwen/Qwen3.8-27B | stop=max_tokens glm-5.3-flash | stop=max_tokens Qwen/Qwen3.8-27B | total per run s glm-5.3-flash | total per run s Qwen/Qwen3.8-27B |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo | memory_extract | 1.0 | 1.0 | 10.1 | 0.2 | 0.02x | 303 | 291 | 416 | 2 | 0% | 0% | 10.1 | 0.2 |
| FQA-solo | memory_extract | 1.0 | 1.0 | 22.2 | 0.4 | 0.02x | 2,119 | 1,774 | 1,000 | 4 | 100% | 0% | 22.2 | 0.4 |
| MATH-solo | memory_extract | 1.0 | 1.0 | 19.1 | 0.4 | 0.02x | 1,756 | 1,730 | 761 | 2 | 0% | 0% | 19.1 | 0.4 |
| CODE-team | memory_extract | 5.4 | 5.0 | 18.1 | 0.2 | 0.01x | 774 | 571 | 770 | 2 | 56% | 0% | 97.6 | 1.1 |
| CODE-team | memory_recall | 2.4 | - | 4.9 | - | - | 332 | - | 190 | - | 75% | - | 11.7 | - |
| FQA-team | memory_extract | 3.8 | 5.4 | 20.6 | 1.8 | 0.09x | 1,458 | 1,192 | 899 | 51 | 74% | 0% | 78.2 | 9.6 |
| FQA-team | memory_recall | - | 0.4 | - | 0.4 | - | - | 747 | - | 7 | - | 0% | - | 0.2 |
| MATH-team | memory_extract | 4.6 | 5.0 | 13.1 | 0.7 | 0.05x | 1,443 | 1,482 | 585 | 10 | 35% | 0% | 60.3 | 3.5 |
| MATH-team | memory_recall | - | 0.2 | - | 0.3 | - | - | 1,007 | - | 2 | - | 0% | - | 0.1 |


## T9. Streaming delivery pattern (first block = client time to the first content block; tail = first block to end of stream; tail < 0.1 s = whole response in one burst)

| kind | response ends with | calls glm-5.3-flash | calls Qwen/Qwen3.8-27B | output tok glm-5.3-flash | output tok Qwen/Qwen3.8-27B | first block s glm-5.3-flash | first block s Qwen/Qwen3.8-27B | tail s glm-5.3-flash | tail s Qwen/Qwen3.8-27B | tail share glm-5.3-flash | tail share Qwen/Qwen3.8-27B | tail<0.1s glm-5.3-flash | tail<0.1s Qwen/Qwen3.8-27B | tail tok/s glm-5.3-flash | tail tok/s Qwen/Qwen3.8-27B |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| lead | tool_use | 149 | 165 | 396 | 336 | 5.73 | 0.56 | 4.64 | 12.65 | 45% | 96% | 29% | 0% | 85 | 27 |
| lead | end_turn | 78 | 86 | 308 | 180 | 5.66 | 0.40 | 3.75 | 7.02 | 40% | 95% | 50% | 0% | 82 | 26 |
| teammate | tool_use | 173 | 159 | 396 | 314 | 5.60 | 0.71 | 5.43 | 12.37 | 49% | 95% | 59% | 0% | 73 | 25 |
| teammate | end_turn | 46 | 45 | 451 | 265 | 5.99 | 0.28 | 5.24 | 10.49 | 47% | 97% | 33% | 0% | 86 | 25 |


## T10. Direct provider probes (scripts/provider_probe.py: prompt-size sweep, cached resends, decode rate, cache semantics)

| probe | glm-5.3-flash | Qwen/Qwen3.8-27B |
|---|---:|---:|
| probe calls | - | 27 |
| first block = a + b x uncached tokens (fresh calls) | - | -0.01 s + 0.1729 ms/tok (r2 1.00) -> 5,784 tok/s |
| decode tok/s median (min-max) | - | 26 (25-26) |
| responses delivered in one burst (tail < 0.1 s) | - | 0% |
| identical resend 1 | - | 36,064 cached: 6.42s -> 0.38s = 0.1676 ms saved/tok |
| identical resend 2 | - | 12,544 cached: 2.07s -> 0.22s = 0.1475 ms saved/tok |
| cache case 1 first send, unique prefix | - | 0.0% cached, first block 5.14 s |
| cache case 2 identical resend | - | 99.7% cached, first block 0.28 s |
| cache case 3 new prefix, same body | - | 0.0% cached, first block 5.09 s |
| cache case 4 unique block inserted mid-prompt | - | 0.0% cached, first block 5.11 s |
| cache case 5 document never sent before | - | 0.0% cached, first block 11.59 s |
| cache case 6 that document again, new prefix | - | 0.0% cached, first block 11.58 s |
