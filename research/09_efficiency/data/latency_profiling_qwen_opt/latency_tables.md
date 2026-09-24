**Runs** (lead turns = lead activations; rounds = model calls of the agent loop; memory/compaction = auxiliary model calls; score: CODE tests passed, MATH answers correct, FQA keyword coverage)
| run | status | wall (s) | lead turns | lead rounds | teammates | teammate rounds | teammate tasks (closed) | memory calls | compaction calls | model errors (429 etc.) | tool calls (denied) | score |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| CODE-solo-r1 | completed | 196 | 1 | 22 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 23 (0) | 3/3 |
| CODE-solo-r2 | completed | 193 | 1 | 21 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 22 (0) | 3/3 |
| CODE-solo-r3 | completed | 145 | 1 | 15 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 18 (0) | 3/3 |
| FQA-solo-r1 | completed | 52 | 1 | 2 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 4 (0) | coverage 0.933 |
| FQA-solo-r2 | completed | 66 | 1 | 3 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 4 (0) | coverage 0.967 |
| FQA-solo-r3 | completed | 57 | 1 | 2 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 4 (0) | coverage 0.967 |
| MATH-solo-r1 | completed | 118 | 1 | 2 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 1 (0) | 3/3 |
| MATH-solo-r2 | completed | 152 | 1 | 3 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 2 (0) | 3/3 |
| MATH-solo-r3 | completed | 171 | 1 | 2 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 1 (0) | 3/3 |
| CODE-team-r1 | completed | 149 | 5 | 12 | 3 | 15 | 3 (3) | 5 | 0 | 0 | 28 (0) | 3/3 |
| CODE-team-r2 | completed | 202 | 5 | 12 | 3 | 21 | 3 (3) | 5 | 0 | 0 | 37 (0) | 3/3 |
| CODE-team-r3 | completed | 176 | 5 | 11 | 3 | 22 | 3 (3) | 5 | 0 | 0 | 35 (0) | 3/3 |
| CODE-team-r4 | completed | 140 | 5 | 12 | 3 | 18 | 3 (3) | 5 | 0 | 0 | 32 (0) | 3/3 |
| CODE-team-r5 | completed | 242 | 5 | 13 | 3 | 18 | 3 (3) | 5 | 0 | 0 | 34 (0) | 3/3 |
| FQA-team-r1 | completed | 132 | 6 | 12 | 3 | 15 | 3 (3) | 6 | 0 | 0 | 29 (0) | coverage 0.967 |
| FQA-team-r2 | completed | 130 | 6 | 11 | 3 | 14 | 3 (3) | 8 | 0 | 0 | 25 (0) | coverage 0.967 |
| FQA-team-r3 | completed | 154 | 5 | 14 | 3 | 12 | 3 (3) | 5 | 0 | 0 | 27 (0) | coverage 0.967 |
| FQA-team-r4 | completed | 128 | 5 | 12 | 3 | 15 | 3 (3) | 5 | 0 | 0 | 30 (0) | coverage 1.0 |
| FQA-team-r5 | completed | 140 | 5 | 14 | 3 | 12 | 3 (3) | 5 | 0 | 0 | 30 (0) | coverage 1.0 |
| MATH-team-r1 | completed | 258 | 5 | 13 | 3 | 8 | 3 (3) | 5 | 0 | 0 | 20 (1) | 3/3 |
| MATH-team-r2 | completed | 283 | 5 | 12 | 3 | 9 | 3 (3) | 6 | 0 | 0 | 21 (1) | 3/3 |
| MATH-team-r3 | completed | 218 | 5 | 10 | 3 | 9 | 3 (3) | 5 | 0 | 0 | 18 (1) | 3/3 |
| MATH-team-r4 | completed | 274 | 5 | 12 | 3 | 6 | 3 (3) | 5 | 0 | 0 | 18 (1) | 3/3 |
| MATH-team-r5 | completed | 309 | 5 | 9 | 3 | 10 | 3 (3) | 5 | 0 | 0 | 19 (1) | 3/3 |

**Latency breakdown of one agentic round** (time-weighted shares of the pooled round time; means per round in seconds)
| group | kind | rounds | final rounds | gross mean | gross median | gross p90 | prep | model | tools | other | prep share | model share (to first delivered block / streamed tail) | tools share | other share |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo | lead | 58 | 3 | 9.1 | 3.9 | 23.3 | 0.01 | 8.9 | 0.13 | 0.007 | 0.1% | 98.2% (6.0% / 92.1%) | 1.5% | 0.1% |
| FQA-solo | lead | 7 | 3 | 24.1 | 8.2 | 49.8 | 0.01 | 24.0 | 0.04 | 0.016 | 0.0% | 99.7% (6.2% / 93.4%) | 0.2% | 0.1% |
| MATH-solo | lead | 7 | 3 | 62.2 | 52.4 | 98.4 | 0.01 | 62.0 | 0.11 | 0.013 | 0.0% | 99.8% (0.7% / 99.0%) | 0.2% | 0.0% |
| CODE-team | lead | 60 | 25 | 7.2 | 4.7 | 18.0 | 0.01 | 7.1 | 0.02 | 0.005 | 0.1% | 99.4% (6.5% / 92.8%) | 0.3% | 0.1% |
| CODE-team | teammate | 94 | 15 | 11.4 | 4.0 | 26.3 | 2.51 | 8.8 | 0.10 | 0.006 | 22.0% | 76.9% (4.8% / 72.1%) | 0.9% | 0.0% |
| FQA-team | lead | 63 | 29 | 9.0 | 5.1 | 24.9 | 0.24 | 8.8 | 0.02 | 0.004 | 2.7% | 96.9% (5.8% / 91.1%) | 0.2% | 0.0% |
| FQA-team | teammate | 68 | 15 | 8.8 | 4.8 | 21.4 | 2.44 | 6.2 | 0.03 | 0.008 | 27.8% | 71.0% (10.1% / 60.9%) | 0.4% | 0.1% |
| MATH-team | lead | 56 | 25 | 12.9 | 7.2 | 32.1 | 0.01 | 12.9 | 0.03 | 0.005 | 0.1% | 99.6% (2.7% / 96.9%) | 0.2% | 0.0% |
| MATH-team | teammate | 42 | 15 | 41.5 | 33.0 | 79.5 | 10.10 | 31.3 | 0.04 | 0.004 | 24.3% | 75.5% (0.8% / 74.7%) | 0.1% | 0.0% |

**Context preparation, attributed** (lead rounds; seconds per round, pooled by group; `memory recall model` = model calls made by update_context; `post` = turn-end work after a final round: Stop hook + memory extraction + post-turn recall)
| group | rounds | prep mean | compaction pipeline (incl. any summary call) | of which summary-compaction model calls | summary calls / run | memory recall (model) | memory recall calls / round | prompt+tool assembly | inbox | unattributed | turns | post per turn | memory extract (model) per turn | memory calls per turn | post amortised per round | (prep + post) share of lead-side time |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo | 58 | 0.01 | 0.002 | 0.000 | 0.00 | 0.00 | 0.00 | 0.000 | 0.000 | 0.008 | 3 | 0.0 | 0.0 | 0.00 | 0.0 | 0.1% |
| FQA-solo | 7 | 0.01 | 0.001 | 0.000 | 0.00 | 0.00 | 0.00 | 0.000 | 0.000 | 0.009 | 3 | 0.0 | 0.0 | 0.00 | 0.0 | 0.1% |
| MATH-solo | 7 | 0.01 | 0.001 | 0.000 | 0.00 | 0.00 | 0.00 | 0.000 | 0.000 | 0.005 | 3 | 0.0 | 0.0 | 0.00 | 0.0 | 0.0% |
| CODE-team | 60 | 0.01 | 0.001 | 0.000 | 0.00 | 0.00 | 0.00 | 0.000 | 0.000 | 0.005 | 25 | 0.0 | 0.0 | 0.00 | 0.0 | 0.3% |
| FQA-team | 63 | 0.24 | 0.001 | 0.000 | 0.00 | 0.00 | 0.02 | 0.000 | 0.000 | 0.232 | 27 | -0.5 | 0.0 | 0.07 | -0.2 | 0.1% |
| MATH-team | 56 | 0.01 | 0.002 | 0.000 | 0.00 | 0.00 | 0.00 | 0.000 | 0.000 | 0.005 | 25 | 0.1 | 0.0 | 0.08 | 0.1 | 0.5% |

**Tokens and model-call timing per call** (agent calls only: purpose lead / teammate; prompt = input + cache_read (+cache_creation); uncached = tokens the provider computed: input_tokens plus cache_creation_input_tokens where reported (vLLM); first block = client time to the first delivered content block (NOT prefill alone: thinking/tool_use blocks arrive in bursts, see the delivery-pattern table); tail = first block to stream end; fit: first-block time = a + b x uncached tokens)
| group | kind | calls | prompt tok mean | prompt tok median | uncached mean | cache-read share | output tok mean | output tok median | thinking share of output chars | model call mean | first block mean | first block median | tail mean | tail tok/s (pooled) | first-block fit a (s) + b (ms/tok), r2 | first block is thinking |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| CODE-solo | lead | 58 | 7,670 | 8,052 | 762 | 90.1% | 227 | 92 | 0.0% | 8.9 | 0.5 | 0.6 | 8.4 | 27.1 | 0.47 s + 0.102 ms/tok, r2 0.23 | 0.0% |
| FQA-solo | lead | 7 | 15,071 | 18,665 | 7,119 | 52.8% | 584 | 196 | 0.0% | 24.0 | 1.5 | 0.9 | 22.5 | 26.0 | 0.38 s + 0.157 ms/tok, r2 1.00 | 0.0% |
| MATH-solo | lead | 7 | 4,676 | 5,247 | 1,652 | 64.7% | 1,618 | 1,369 | 56.5% | 62.0 | 0.5 | 0.5 | 61.6 | 26.3 | 0.22 s + 0.140 ms/tok, r2 0.95 | 100.0% |
| CODE-team | lead | 60 | 4,987 | 5,146 | 662 | 86.7% | 178 | 102 | 0.0% | 7.1 | 0.5 | 0.5 | 6.7 | 26.7 | 0.32 s + 0.219 ms/tok, r2 0.37 | 0.0% |
| CODE-team | teammate | 94 | 3,614 | 3,317 | 837 | 76.9% | 214 | 68 | 0.0% | 8.8 | 0.5 | 0.6 | 8.2 | 26.0 | 0.42 s + 0.153 ms/tok, r2 0.30 | 0.0% |
| FQA-team | lead | 63 | 5,690 | 5,731 | 750 | 86.8% | 213 | 111 | 0.0% | 8.8 | 0.5 | 0.5 | 8.2 | 26.0 | 0.44 s + 0.113 ms/tok, r2 0.09 | 0.0% |
| FQA-team | teammate | 68 | 6,948 | 7,101 | 2,313 | 66.7% | 132 | 67 | 0.0% | 6.2 | 0.9 | 0.7 | 5.3 | 24.7 | 0.54 s + 0.152 ms/tok, r2 0.75 | 0.0% |
| MATH-team | lead | 56 | 6,508 | 6,424 | 880 | 86.5% | 325 | 176 | 44.9% | 12.9 | 0.4 | 0.3 | 12.5 | 26.0 | 0.22 s + 0.152 ms/tok, r2 0.85 | 100.0% |
| MATH-team | teammate | 42 | 2,233 | 2,019 | 1,038 | 53.5% | 782 | 596 | 61.3% | 31.3 | 0.3 | 0.3 | 31.0 | 25.2 | 0.19 s + 0.129 ms/tok, r2 0.81 | 100.0% |
| pooled fit | lead | 251 | | | uncached 53-22,567 | | | | | | | | | 26.3 | first block vs uncached: 0.35 s + 0.154 ms/tok (r2 0.72); vs prompt: 0.21 s + 0.046 ms/tok (r2 0.18); tail vs output tok: -0.03 s + 38.1 ms/tok (r2 1.00) | |
| pooled fit | teammate | 204 | | | uncached 70-15,933 | | | | | | | | | 25.4 | first block vs uncached: 0.39 s + 0.161 ms/tok (r2 0.69); vs prompt: 0.43 s + 0.042 ms/tok (r2 0.12); tail vs output tok: -0.10 s + 39.7 ms/tok (r2 1.00) | |
| pooled fit | all | 455 | | | uncached 53-22,567 | | | | | | | | | 25.9 | first block vs uncached: 0.37 s + 0.159 ms/tok (r2 0.70); vs prompt: 0.35 s + 0.036 ms/tok (r2 0.10); tail vs output tok: -0.11 s + 39.0 ms/tok (r2 1.00) | |

**Prefill vs decode by regression** (agent calls only; model-call duration = a + b x uncached prompt tokens + c x output tokens, least squares; the per-group shares apply the pooled coefficients to each group's own token counts, so they add to ~100% of that group's call time)
| group | kind | calls | mean call (s) | fixed a | prefill b x uncached | decode c x output | residual |
|---|---|---:|---:|---:|---:|---:|---:|
| CODE-solo | lead | 58 | 8.9 | 2.6% | 1.9% | 98.7% | -3.2% |
| FQA-solo | lead | 7 | 24.0 | 1.0% | 6.7% | 94.6% | -2.2% |
| MATH-solo | lead | 7 | 62.0 | 0.4% | 0.6% | 101.4% | -2.4% |
| CODE-team | lead | 60 | 7.1 | 3.2% | 2.1% | 96.9% | -2.2% |
| CODE-team | teammate | 94 | 8.8 | 2.6% | 2.2% | 94.8% | 0.5% |
| FQA-team | lead | 63 | 8.8 | 2.6% | 1.9% | 94.7% | 0.7% |
| FQA-team | teammate | 68 | 6.2 | 3.7% | 8.4% | 82.4% | 5.6% |
| MATH-team | lead | 56 | 12.9 | 1.8% | 1.5% | 98.3% | -1.6% |
| MATH-team | teammate | 42 | 31.3 | 0.7% | 0.7% | 97.0% | 1.5% |

| fit over | calls | a fixed (s) | b prefill (ms per uncached tok) | c decode (ms per output tok) | r2 |
|---|---:|---:|---:|---:|---:|
| all agent calls | 455 | 0.23 | 0.226 | 38.86 | 1.00 |
| lead | 251 | 0.32 | 0.186 | 38.01 | 1.00 |
| teammate | 204 | 0.22 | 0.250 | 39.54 | 1.00 |
| responses ending in tool_use | 324 | 0.16 | 0.256 | 38.92 | 1.00 |
| responses ending in end_turn | 131 | 0.34 | 0.165 | 38.70 | 1.00 |

**Streaming delivery pattern** (why the first-block time is not prefill: the provider delivers thinking and tool_use blocks in bursts; 'first block' = client time to the first delivered content block, 'tail' = first block to end of stream; a tail under 0.1 s means the whole response arrived at once)
| kind | response ends with | calls | output tok mean | first block mean (s) | tail mean (s) | tail share of call | calls with tail < 0.1 s | first-block fit: a + c x output tok (r2) |
|---|---|---:|---:|---:|---:|---:|---:|---|
| lead | tool_use | 165 | 336 | 0.56 | 12.65 | 95.7% | 0.0% | 0.46 s + -0.11 ms/tok (0.69) |
| lead | end_turn | 86 | 180 | 0.40 | 7.02 | 94.6% | 0.0% | 0.24 s + -0.02 ms/tok (0.84) |
| teammate | tool_use | 159 | 314 | 0.71 | 12.37 | 94.6% | 0.0% | 0.51 s + -0.15 ms/tok (0.73) |
| teammate | end_turn | 45 | 265 | 0.28 | 10.49 | 97.4% | 0.0% | 0.22 s + -0.07 ms/tok (0.72) |

**Auxiliary model calls made by the harness itself** (context maintenance: memory recall inside update_context before a lead call, memory extraction after a lead turn, summary compaction; pooled by group)
| group | purpose | calls | calls / run | calls / lead round | mean duration | median | prompt tok mean | output tok mean | stop=max_tokens share | first block mean | tail mean | total per run (s) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo | memory_extract | 3 | 1.0 | 0.05 | 0.2 | 0.2 | 291 | 2 | 0.0% | 0.1 | 0.0 | 0.2 |
| FQA-solo | memory_extract | 3 | 1.0 | 0.43 | 0.4 | 0.4 | 1,774 | 4 | 0.0% | 0.3 | 0.1 | 0.4 |
| MATH-solo | memory_extract | 3 | 1.0 | 0.43 | 0.4 | 0.4 | 1,730 | 2 | 0.0% | 0.3 | 0.0 | 0.4 |
| CODE-team | memory_extract | 25 | 5.0 | 0.42 | 0.2 | 0.2 | 571 | 2 | 0.0% | 0.2 | 0.0 | 1.1 |
| FQA-team | memory_extract | 27 | 5.4 | 0.43 | 1.8 | 0.3 | 1,192 | 51 | 0.0% | 0.3 | 2.0 | 9.6 |
| FQA-team | memory_recall | 2 | 0.4 | 0.03 | 0.4 | 0.4 | 747 | 7 | 0.0% | 0.2 | 0.2 | 0.2 |
| MATH-team | memory_extract | 25 | 5.0 | 0.45 | 0.7 | 0.4 | 1,482 | 10 | 0.0% | 0.3 | 0.4 | 3.5 |
| MATH-team | memory_recall | 1 | 0.2 | 0.02 | 0.3 | 0.3 | 1,007 | 2 | 0.0% | 0.2 | 0.0 | 0.1 |

**Teammate input redundancy** (a) re-sent = prompt tokens already sent in the same agent's previous call (append-only history), (b) provider cache-read share, (c) cross-teammate file bytes another teammate had fetched first (exact (file,line) provenance) and their share of teammate prompt tokens
| run | teammates | teammate calls | teammate prompt tok | re-sent share (a) | cache-read share (b) | file bytes fetched | cross-teammate redundancy, range (c) | same, whole-result SHA | resident copies | cross-redundant share of prompt tok | file content share of prompt tok | lead: prompt tok | lead re-sent share | lead cache-read |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo-r1 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 174,187 | 93.1% | 90.9% |
| CODE-solo-r2 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 166,938 | 92.7% | 90.2% |
| CODE-solo-r3 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 103,706 | 91.0% | 88.5% |
| FQA-solo-r1 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 28,940 | 11.2% | 10.8% |
| FQA-solo-r2 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 47,617 | 46.0% | 51.0% |
| FQA-solo-r3 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 28,940 | 11.2% | 97.5% |
| MATH-solo-r1 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 8,436 | 37.8% | 37.2% |
| MATH-solo-r2 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 14,661 | 59.7% | 80.2% |
| MATH-solo-r3 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 9,638 | 33.1% | 65.1% |
| CODE-team-r1 | 3 | 15 | 43,859 | 75.1% | 71.5% | 13,961 | 0.0% | 0.0% | 1.00x | 0.0% | 46.9% | 58,688 | 89.8% | 81.5% |
| CODE-team-r2 | 3 | 21 | 77,465 | 83.6% | 78.9% | 13,848 | 0.0% | 0.0% | 1.00x | 0.0% | 33.7% | 62,420 | 89.5% | 87.9% |
| CODE-team-r3 | 3 | 22 | 93,218 | 82.9% | 79.1% | 13,848 | 0.0% | 0.0% | 1.00x | 0.0% | 27.8% | 53,218 | 89.0% | 88.4% |
| CODE-team-r4 | 3 | 18 | 55,685 | 79.3% | 74.6% | 13,848 | 0.0% | 0.0% | 1.00x | 0.0% | 45.2% | 59,624 | 90.0% | 88.1% |
| CODE-team-r5 | 3 | 18 | 69,477 | 79.8% | 76.7% | 13,848 | 0.0% | 0.0% | 1.00x | 0.0% | 31.5% | 65,270 | 90.4% | 87.7% |
| FQA-team-r1 | 3 | 15 | 81,034 | 73.1% | 69.7% | 64,177 | 22.6% | 20.0% | 1.31x | 15.3% | 80.4% | 66,063 | 88.8% | 89.0% |
| FQA-team-r2 | 3 | 14 | 102,590 | 68.7% | 65.7% | 103,301 | 12.5% | 12.5% | 1.15x | 10.6% | 87.6% | 61,214 | 87.3% | 79.4% |
| FQA-team-r3 | 3 | 12 | 105,264 | 67.4% | 65.5% | 112,843 | 22.8% | 22.8% | 1.30x | 20.2% | 88.4% | 83,713 | 90.0% | 89.0% |
| FQA-team-r4 | 3 | 15 | 78,133 | 73.0% | 68.2% | 61,921 | 23.5% | 20.8% | 1.32x | 16.2% | 81.8% | 68,007 | 88.9% | 87.6% |
| FQA-team-r5 | 3 | 12 | 105,427 | 67.4% | 65.4% | 112,843 | 22.8% | 22.8% | 1.30x | 20.1% | 88.1% | 79,487 | 90.0% | 87.8% |
| MATH-team-r1 | 3 | 8 | 16,866 | 52.9% | 46.5% | 0 | - | - | - | 0.0% | 0.0% | 86,424 | 88.5% | 84.4% |
| MATH-team-r2 | 3 | 9 | 21,775 | 56.6% | 57.6% | 0 | - | - | - | 0.0% | 0.0% | 73,054 | 88.7% | 90.1% |
| MATH-team-r3 | 3 | 9 | 18,461 | 57.5% | 55.2% | 0 | - | - | - | 0.0% | 0.0% | 58,049 | 86.7% | 85.1% |
| MATH-team-r4 | 3 | 6 | 10,800 | 34.6% | 43.6% | 0 | - | - | - | 0.0% | 0.0% | 88,455 | 88.0% | 86.9% |
| MATH-team-r5 | 3 | 10 | 25,875 | 58.9% | 57.6% | 0 | - | - | - | 0.0% | 0.0% | 58,484 | 84.0% | 85.8% |

**Agent runs per task** (mean over runs of a group unless noted; a teammate task = rounds from assignment to its final reply; wall = driver wall time)
| group | runs | wall mean (s) | lead turns / run | lead rounds / run | lead rounds / turn | teammates / run | teammate rounds / task: mean | median | max | teammate task active time (s): mean | median | teammate tasks / run | tool calls / round (lead) | tool calls / round (teammate) | model calls / run (all) | denied tool calls / run |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo | 3 | 178 | 1.0 | 19.3 | 19.3 | 0.0 | - | - | 0 | - | - | 0.0 | 1.09 | 0.00 | 20.3 | 0.0 |
| FQA-solo | 3 | 58 | 1.0 | 2.3 | 2.3 | 0.0 | - | - | 0 | - | - | 0.0 | 1.71 | 0.00 | 3.3 | 0.0 |
| MATH-solo | 3 | 147 | 1.0 | 2.3 | 2.3 | 0.0 | - | - | 0 | - | - | 0.0 | 0.57 | 0.00 | 3.3 | 0.0 |
| CODE-team | 5 | 182 | 5.0 | 12.0 | 2.4 | 3.0 | 6.3 | 5 | 11 | 71 | 63 | 3.0 | 1.15 | 1.03 | 35.8 | 0.0 |
| FQA-team | 5 | 137 | 5.4 | 12.6 | 2.3 | 3.0 | 4.5 | 4 | 6 | 40 | 38 | 3.0 | 1.03 | 1.12 | 32.0 | 0.0 |
| MATH-team | 5 | 268 | 5.0 | 11.2 | 2.2 | 3.0 | 2.8 | 3 | 4 | 116 | 118 | 3.0 | 1.23 | 0.64 | 24.8 | 1.0 |

**Where the wall time of a run goes** (means over the runs of a group; lead active = lead turns incl. turn-end memory work; teammate active = union of teammate round windows; overlap = lead and teammates busy at the same time; idle tail = nobody busy: waits for team events, quiescence timer, shutdown handshake)
| group | runs | wall (s) | lead active (s) | share | of which lead agent calls (s) | of which turn-end memory (s) | teammate active (s) | share | overlap (s) | idle / tail (s) | share |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo | 3 | 178 | 176 | 98.9% | 173 | 0 | 0 | 0.0% | 0 | 2 | 1.1% |
| FQA-solo | 3 | 58 | 56 | 96.5% | 56 | 0 | 0 | 0.0% | 0 | 2 | 3.5% |
| MATH-solo | 3 | 147 | 145 | 98.7% | 145 | 0 | 0 | 0.0% | 0 | 2 | 1.3% |
| CODE-team | 5 | 182 | 86 | 47.4% | 85 | 0 | 113 | 62.4% | 20 | 2 | 1.0% |
| FQA-team | 5 | 137 | 111 | 81.0% | 110 | -3 | 47 | 34.1% | 23 | 2 | 1.8% |
| MATH-team | 5 | 268 | 145 | 54.1% | 144 | 1 | 158 | 58.8% | 37 | 2 | 0.9% |

**Tool execution time by tool** (tool_start -> tool_end spans, pooled over all runs)
| tool | calls | denied | error | mean (ms) | median (ms) | p90 (ms) | max (ms) | total (s) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| bash | 64 | 5 | 9 | 267 | 331 | 404 | 451 | 17.1 |
| spawn_teammate | 45 | 0 | 0 | 59 | 54 | 77 | 145 | 2.7 |
| read_file | 83 | 0 | 0 | 25 | 23 | 40 | 73 | 2.1 |
| complete_task | 41 | 0 | 0 | 36 | 36 | 39 | 47 | 1.5 |
| create_task | 45 | 0 | 0 | 18 | 16 | 25 | 38 | 0.8 |
| request_shutdown | 42 | 0 | 0 | 14 | 9 | 25 | 37 | 0.6 |
| write_file | 27 | 0 | 0 | 22 | 25 | 29 | 39 | 0.6 |
| edit_file | 22 | 0 | 0 | 18 | 19 | 25 | 28 | 0.4 |
| claim_task | 15 | 0 | 0 | 13 | 12 | 14 | 14 | 0.2 |
| send_message | 15 | 0 | 0 | 11 | 11 | 14 | 15 | 0.2 |
| todo_write | 81 | 0 | 0 | 1 | 1 | 1 | 1 | 0.1 |
| glob | 2 | 0 | 0 | 23 | 23 | 30 | 31 | 0.0 |

**Per-run round breakdown** (lead and teammate rounds separately; seconds)
| run | kind | rounds | gross mean | prep mean | model mean | first block mean | tail mean | tools mean | other mean | prep share | model share | tools share | prompt tok mean | output tok mean |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo-r1 | lead | 22 | 8.8 | 0.01 | 8.6 | 0.5 | 8.1 | 0.16 | 0.007 | 0.1% | 97.8% | 1.8% | 7,918 | 219 |
| CODE-solo-r2 | lead | 21 | 9.1 | 0.01 | 8.9 | 0.5 | 8.4 | 0.10 | 0.006 | 0.1% | 98.5% | 1.1% | 7,949 | 227 |
| CODE-solo-r3 | lead | 15 | 9.5 | 0.01 | 9.4 | 0.6 | 8.8 | 0.15 | 0.007 | 0.1% | 98.1% | 1.5% | 6,914 | 239 |
| FQA-solo-r1 | lead | 2 | 24.8 | 0.01 | 24.7 | 2.4 | 22.4 | 0.05 | 0.018 | 0.0% | 99.7% | 0.2% | 14,470 | 580 |
| FQA-solo-r2 | lead | 3 | 21.2 | 0.01 | 21.2 | 1.6 | 19.5 | 0.03 | 0.014 | 0.1% | 99.7% | 0.1% | 15,872 | 508 |
| FQA-solo-r3 | lead | 2 | 27.6 | 0.01 | 27.5 | 0.4 | 27.1 | 0.04 | 0.017 | 0.0% | 99.7% | 0.2% | 14,470 | 702 |
| MATH-solo-r1 | lead | 2 | 58.0 | 0.01 | 57.9 | 0.6 | 57.3 | 0.11 | 0.029 | 0.0% | 99.7% | 0.2% | 4,218 | 1,508 |
| MATH-solo-r2 | lead | 3 | 49.9 | 0.01 | 49.7 | 0.4 | 49.4 | 0.13 | 0.006 | 0.0% | 99.7% | 0.3% | 4,887 | 1,299 |
| MATH-solo-r3 | lead | 2 | 84.6 | 0.01 | 84.5 | 0.5 | 84.1 | 0.08 | 0.007 | 0.0% | 99.9% | 0.1% | 4,819 | 2,209 |
| CODE-team-r1 | lead | 12 | 6.5 | 0.01 | 6.5 | 0.5 | 6.0 | 0.02 | 0.004 | 0.1% | 99.3% | 0.3% | 4,891 | 161 |
| CODE-team-r1 | teammate | 15 | 10.9 | 2.59 | 8.2 | 0.6 | 7.6 | 0.09 | 0.005 | 23.7% | 75.1% | 0.8% | 2,924 | 198 |
| CODE-team-r2 | lead | 12 | 8.4 | 0.01 | 8.3 | 0.5 | 7.8 | 0.02 | 0.005 | 0.1% | 99.4% | 0.3% | 5,202 | 208 |
| CODE-team-r2 | teammate | 21 | 10.4 | 2.03 | 8.3 | 0.5 | 7.8 | 0.09 | 0.006 | 19.5% | 79.4% | 0.9% | 3,689 | 203 |
| CODE-team-r3 | lead | 11 | 6.9 | 0.01 | 6.8 | 0.4 | 6.4 | 0.02 | 0.004 | 0.1% | 99.4% | 0.3% | 4,838 | 171 |
| CODE-team-r3 | teammate | 22 | 11.0 | 2.79 | 8.1 | 0.6 | 7.5 | 0.10 | 0.005 | 25.3% | 73.5% | 0.9% | 4,237 | 196 |
| CODE-team-r4 | lead | 12 | 6.8 | 0.01 | 6.8 | 0.5 | 6.3 | 0.03 | 0.006 | 0.1% | 99.3% | 0.4% | 4,969 | 169 |
| CODE-team-r4 | teammate | 18 | 10.8 | 3.05 | 7.6 | 0.6 | 7.1 | 0.10 | 0.005 | 28.2% | 70.6% | 1.0% | 3,094 | 182 |
| CODE-team-r5 | lead | 13 | 7.2 | 0.01 | 7.1 | 0.5 | 6.7 | 0.02 | 0.005 | 0.1% | 99.4% | 0.3% | 5,021 | 178 |
| CODE-team-r5 | teammate | 18 | 14.0 | 2.10 | 11.7 | 0.5 | 11.2 | 0.10 | 0.006 | 15.0% | 84.0% | 0.7% | 3,860 | 292 |
| FQA-team-r1 | lead | 12 | 8.0 | 0.01 | 8.0 | 0.4 | 7.6 | 0.02 | 0.006 | 0.1% | 99.4% | 0.3% | 5,505 | 198 |
| FQA-team-r1 | teammate | 15 | 7.7 | 1.96 | 5.6 | 0.8 | 4.8 | 0.03 | 0.008 | 25.5% | 72.7% | 0.4% | 5,402 | 123 |
| FQA-team-r2 | lead | 11 | 9.0 | 0.81 | 8.1 | 0.5 | 7.5 | 0.02 | 0.003 | 9.1% | 90.6% | 0.2% | 5,565 | 195 |
| FQA-team-r2 | teammate | 14 | 9.9 | 2.66 | 7.2 | 0.9 | 6.3 | 0.03 | 0.007 | 26.8% | 72.5% | 0.3% | 7,328 | 153 |
| FQA-team-r3 | lead | 14 | 10.3 | 0.42 | 9.9 | 0.5 | 9.3 | 0.02 | 0.005 | 4.1% | 95.6% | 0.2% | 5,980 | 238 |
| FQA-team-r3 | teammate | 12 | 9.3 | 2.69 | 6.6 | 1.0 | 5.5 | 0.03 | 0.007 | 28.9% | 70.4% | 0.3% | 8,772 | 134 |
| FQA-team-r4 | lead | 12 | 8.8 | 0.01 | 8.8 | 0.6 | 8.2 | 0.02 | 0.004 | 0.1% | 99.5% | 0.2% | 5,667 | 217 |
| FQA-team-r4 | teammate | 15 | 7.5 | 2.06 | 5.3 | 0.8 | 4.5 | 0.03 | 0.009 | 27.6% | 70.2% | 0.4% | 5,209 | 114 |
| FQA-team-r5 | lead | 14 | 8.9 | 0.01 | 8.8 | 0.6 | 8.2 | 0.02 | 0.005 | 0.1% | 99.5% | 0.2% | 5,678 | 213 |
| FQA-team-r5 | teammate | 12 | 9.8 | 2.99 | 6.8 | 1.0 | 5.7 | 0.04 | 0.008 | 30.5% | 68.8% | 0.4% | 8,786 | 138 |
| MATH-team-r1 | lead | 13 | 13.0 | 0.01 | 13.0 | 0.4 | 12.6 | 0.02 | 0.006 | 0.1% | 99.7% | 0.2% | 6,648 | 327 |
| MATH-team-r1 | teammate | 8 | 45.2 | 12.79 | 32.3 | 0.4 | 31.9 | 0.05 | 0.004 | 28.3% | 71.5% | 0.1% | 2,108 | 799 |
| MATH-team-r2 | lead | 12 | 10.5 | 0.01 | 10.5 | 0.3 | 10.2 | 0.02 | 0.005 | 0.1% | 99.6% | 0.2% | 6,088 | 264 |
| MATH-team-r2 | teammate | 9 | 38.3 | 7.51 | 30.7 | 0.3 | 30.4 | 0.05 | 0.004 | 19.6% | 80.2% | 0.1% | 2,419 | 773 |
| MATH-team-r3 | lead | 10 | 8.4 | 0.01 | 8.4 | 0.3 | 8.1 | 0.03 | 0.004 | 0.1% | 99.4% | 0.4% | 5,805 | 211 |
| MATH-team-r3 | teammate | 9 | 33.9 | 7.53 | 26.3 | 0.3 | 26.0 | 0.05 | 0.004 | 22.2% | 77.6% | 0.1% | 2,051 | 658 |
| MATH-team-r4 | lead | 12 | 16.4 | 0.01 | 16.3 | 0.4 | 16.0 | 0.02 | 0.005 | 0.1% | 99.7% | 0.1% | 7,371 | 415 |
| MATH-team-r4 | teammate | 6 | 46.7 | 11.57 | 35.1 | 0.3 | 34.7 | 0.04 | 0.003 | 24.8% | 75.1% | 0.1% | 1,800 | 870 |
| MATH-team-r5 | lead | 9 | 16.2 | 0.01 | 16.2 | 0.4 | 15.8 | 0.03 | 0.005 | 0.0% | 99.7% | 0.2% | 6,498 | 412 |
| MATH-team-r5 | teammate | 10 | 45.3 | 11.72 | 33.5 | 0.3 | 33.2 | 0.04 | 0.005 | 25.9% | 74.0% | 0.1% | 2,588 | 837 |

**Server-side timing from vLLM /metrics** (agent calls; deltas of the server's per-request histograms scraped right after each call; exact = exactly one request finished since the previous scrape, so the deltas belong to this call, otherwise they pool concurrent requests and are used only in the run totals; client gap = client model-call duration minus server e2e latency, i.e. HTTP/API-layer + SDK overhead; prefill tok/s = KV-computed tokens / prefill s; decode tok/s = (generation tokens - 1) / decode s; scrape = profiler overhead per call, kept out of the round buckets as `instrument`)
| group | kind | calls | exact | server prefill mean / median (s) | server decode mean / median (s) | server queue mean (s) | server e2e mean (s) | client call mean (s) | client gap mean / median (s) | client first block vs server TTFT mean (s) | prefill tok/s | decode tok/s | running at finish | KV usage at finish | finished=length | preemptions | scrape mean (ms) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo | lead | 58 | 100.0% | 0.19 / 0.16 | 8.6 / 3.5 | 0.000 | 8.9 | 8.9 | 0.06 / 0.06 | 0.55 vs 0.25 | 3,927 | 26 | 0.0 | 0.0% | 0.0% | 0 | 18.4 |
| FQA-solo | lead | 7 | 100.0% | 1.22 / 0.55 | 22.7 / 7.4 | 0.000 | 24.0 | 24.0 | 0.04 / 0.04 | 1.50 vs 1.29 | 5,851 | 26 | 0.0 | 0.0% | 0.0% | 0 | 18.9 |
| MATH-solo | lead | 7 | 100.0% | 0.33 / 0.37 | 61.6 / 52.2 | 0.000 | 62.0 | 62.0 | 0.04 / 0.04 | 0.45 vs 0.37 | 5,029 | 26 | 0.0 | 0.0% | 0.0% | 0 | 19.2 |
| CODE-team | lead | 60 | 100.0% | 0.18 / 0.15 | 6.8 / 4.3 | 0.000 | 7.1 | 7.1 | 0.06 / 0.06 | 0.47 vs 0.23 | 3,738 | 26 | 0.6 | 0.8% | 0.0% | 0 | 18.5 |
| CODE-team | teammate | 94 | 100.0% | 0.22 / 0.22 | 8.5 / 2.7 | 0.000 | 8.7 | 8.8 | 0.04 / 0.04 | 0.54 vs 0.27 | 3,852 | 25 | 0.9 | 1.2% | 0.0% | 0 | 29.6 |
| FQA-team | lead | 63 | 98.4% | 0.21 / 0.16 | 8.4 / 4.5 | 0.000 | 8.8 | 8.8 | 0.09 / 0.06 | 0.51 vs 0.31 | 3,466 | 26 | 0.7 | 1.3% | 0.0% | 0 | 19.2 |
| FQA-team | teammate | 68 | 95.6% | 0.45 / 0.24 | 5.8 / 3.0 | 0.000 | 6.3 | 6.2 | 0.08 / 0.04 | 0.88 vs 0.49 | 5,079 | 23 | 1.5 | 2.5% | 0.0% | 0 | 67.5 |
| MATH-team | lead | 56 | 98.2% | 0.22 / 0.23 | 12.6 / 6.8 | 0.000 | 12.9 | 12.9 | 0.06 / 0.06 | 0.35 vs 0.33 | 4,018 | 26 | 0.7 | 0.7% | 0.0% | 0 | 36.9 |
| MATH-team | teammate | 42 | 100.0% | 0.25 / 0.23 | 31.0 / 23.7 | 0.000 | 31.3 | 31.3 | 0.03 / 0.02 | 0.33 vs 0.23 | 4,137 | 25 | 0.9 | 1.0% | 0.0% | 0 | 28.3 |

| fit over exact calls | calls | server prefill (s) = a + b x computed tok, r2 -> tok/s | server decode (s) = a + c x generation tok, r2 -> ms/tok | client gap (s): mean / median / p90 |
|---|---:|---|---|---|
| lead | 249 | 0.082 + 0.1574 ms/tok (r2 0.96) -> 6,351 tok/s | 0.09 + 38.1 ms/tok (r2 1.00) | 0.07 / 0.06 / 0.07 |
| teammate | 201 | 0.095 + 0.1522 ms/tok (r2 0.98) -> 6,569 tok/s | 0.19 + 39.6 ms/tok (r2 1.00) | 0.05 / 0.04 / 0.06 |
| all | 450 | 0.087 + 0.1547 ms/tok (r2 0.97) -> 6,466 tok/s | 0.09 + 38.9 ms/tok (r2 1.00) | 0.06 / 0.05 / 0.07 |

| run | server prompt tok (all requests) | server cached tok | server cached share | usage cache-read share (agent calls) | prefix-cache hit rate (blocks) | generation tok | server prefill (s) | server decode (s) | server queue (s) | server e2e (s) | client model time, all calls (s) | finished by reason | preemptions |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| CODE-solo-r1 | 174,470 | 158,368 | 90.8% | 90.9% | 90.8% | 4,811 | 4.4 | 183.0 | 0.00 | 188.6 | 189.9 | stop 23 | 0 |
| CODE-solo-r2 | 167,227 | 150,528 | 90.0% | 90.2% | 90.0% | 4,768 | 4.2 | 181.3 | 0.00 | 186.7 | 187.9 | stop 22 | 0 |
| CODE-solo-r3 | 104,008 | 91,728 | 88.2% | 88.5% | 88.2% | 3,581 | 3.0 | 136.0 | 0.00 | 139.8 | 140.7 | stop 16 | 0 |
| FQA-solo-r1 | 30,513 | 3,136 | 10.3% | 10.8% | 10.3% | 1,163 | 4.6 | 45.0 | 0.00 | 49.8 | 49.8 | stop 3 | 0 |
| FQA-solo-r2 | 49,552 | 24,304 | 49.0% | 51.0% | 49.0% | 1,525 | 4.3 | 59.2 | 0.00 | 63.8 | 63.9 | stop 4 | 0 |
| FQA-solo-r3 | 30,755 | 28,224 | 91.8% | 97.5% | 91.8% | 1,410 | 0.6 | 54.7 | 0.00 | 55.5 | 55.6 | stop 3 | 0 |
| MATH-solo-r1 | 9,961 | 3,136 | 31.5% | 37.2% | 31.5% | 3,017 | 1.2 | 114.7 | 0.00 | 116.0 | 116.1 | stop 3 | 0 |
| MATH-solo-r2 | 16,551 | 11,760 | 71.1% | 80.2% | 71.1% | 3,898 | 1.0 | 148.3 | 0.00 | 149.5 | 149.6 | stop 4 | 0 |
| MATH-solo-r3 | 11,414 | 6,272 | 55.0% | 65.1% | 55.0% | 4,420 | 1.0 | 168.3 | 0.00 | 169.4 | 169.5 | stop 3 | 0 |
| CODE-team-r1 | 105,536 | 79,184 | 75.0% | 77.2% | 75.0% | 4,917 | 6.6 | 192.4 | 0.00 | 200.5 | 201.8 | stop 32 | 0 |
| CODE-team-r2 | 143,036 | 116,032 | 81.1% | 82.9% | 81.1% | 6,763 | 7.2 | 264.4 | 0.00 | 273.4 | 275.1 | stop 38 | 0 |
| CODE-team-r3 | 149,607 | 120,736 | 80.7% | 82.4% | 80.7% | 6,206 | 7.5 | 243.7 | 0.00 | 253.0 | 254.7 | stop 38 | 0 |
| CODE-team-r4 | 117,799 | 94,080 | 79.9% | 81.6% | 79.9% | 5,319 | 6.4 | 210.4 | 0.00 | 218.5 | 220.2 | stop 35 | 0 |
| CODE-team-r5 | 137,231 | 110,544 | 80.6% | 82.0% | 80.6% | 7,582 | 6.9 | 294.5 | 0.00 | 303.2 | 304.8 | stop 36 | 0 |
| FQA-team-r1 | 153,882 | 116,032 | 75.4% | 78.3% | 75.4% | 4,244 | 8.5 | 169.9 | 0.00 | 180.2 | 181.5 | stop 33 | 0 |
| FQA-team-r2 | 173,720 | 116,032 | 66.8% | 70.8% | 66.8% | 4,935 | 11.9 | 202.5 | 0.00 | 216.4 | 204.4 | stop 33 | 0 |
| FQA-team-r3 | 193,371 | 143,472 | 74.2% | 75.9% | 74.2% | 5,283 | 9.9 | 203.4 | 0.00 | 215.1 | 245.9 | stop 29 | 0 |
| FQA-team-r4 | 151,493 | 112,896 | 74.5% | 77.3% | 74.5% | 4,324 | 10.3 | 171.3 | 0.00 | 184.3 | 185.7 | stop 32 | 0 |
| FQA-team-r5 | 190,561 | 139,552 | 73.2% | 75.0% | 73.2% | 4,650 | 11.9 | 190.2 | 0.00 | 204.6 | 205.9 | stop 31 | 0 |
| MATH-team-r1 | 110,083 | 81,536 | 74.1% | 78.2% | 74.1% | 10,662 | 6.6 | 420.1 | 0.00 | 428.0 | 429.0 | stop 26 | 0 |
| MATH-team-r2 | 101,317 | 78,400 | 77.4% | 82.7% | 77.4% | 10,329 | 5.8 | 403.6 | 0.00 | 410.6 | 411.7 | stop 27 | 0 |
| MATH-team-r3 | 84,361 | 59,584 | 70.6% | 77.9% | 70.6% | 8,044 | 5.6 | 314.8 | 0.00 | 321.6 | 322.4 | stop 24 | 0 |
| MATH-team-r4 | 104,987 | 81,536 | 77.7% | 82.1% | 77.7% | 10,207 | 5.5 | 400.3 | 0.00 | 407.1 | 408.3 | stop 22 | 0 |
| MATH-team-r5 | 93,417 | 65,072 | 69.7% | 77.1% | 69.7% | 12,089 | 6.2 | 474.6 | 0.00 | 482.0 | 482.8 | stop 24 | 0 |

**Stability across repetitions** (per group: mean +- sd over the runs of the group, CV = sd / mean in parentheses; each run contributes its own mean; a teammate task = rounds from assignment to the final reply)
| group | runs | wall (s) | lead rounds / run | model calls / run | teammate rounds / task | lead gross / round (s) | teammate gross / round (s) | lead model call (s) | teammate model call (s) | prompt tok / agent call | output tok / agent call | score |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo | 3 | 178 +- 28 (16%) | 19.3 +- 3.8 (20%) | 20.3 +- 3.8 (19%) | - | 9.1 +- 0.4 (4%) | - | 9.0 +- 0.4 (4%) | - | 7,594 +- 589 (8%) | 228 +- 10 (4%) | 3.00 +- 0.00 (0%) |
| FQA-solo | 3 | 58 +- 7 (12%) | 2.3 +- 0.6 (25%) | 3.3 +- 0.6 (17%) | - | 24.5 +- 3.2 (13%) | - | 24.5 +- 3.2 (13%) | - | 14,937 +- 810 (5%) | 597 +- 98 (16%) | 0.96 +- 0.02 (2%) |
| MATH-solo | 3 | 147 +- 27 (18%) | 2.3 +- 0.6 (25%) | 3.3 +- 0.6 (17%) | - | 64.2 +- 18.2 (28%) | - | 64.1 +- 18.2 (28%) | - | 4,641 +- 368 (8%) | 1,672 +- 477 (29%) | 3.00 +- 0.00 (0%) |
| CODE-team | 5 | 182 +- 42 (23%) | 12.0 +- 0.7 (6%) | 35.8 +- 2.5 (7%) | 6.3 +- 0.9 (15%) | 7.2 +- 0.7 (10%) | 11.4 +- 1.4 (13%) | 7.1 +- 0.7 (10%) | 8.8 +- 1.7 (19%) | 4,133 +- 294 (7%) | 199 +- 27 (14%) | 3.00 +- 0.00 (0%) |
| FQA-team | 5 | 137 +- 11 (8%) | 12.6 +- 1.3 (11%) | 32.0 +- 1.0 (3%) | 4.5 +- 0.5 (11%) | 9.0 +- 0.8 (9%) | 8.8 +- 1.2 (13%) | 8.7 +- 0.7 (8%) | 6.3 +- 0.8 (13%) | 6,359 +- 888 (14%) | 171 +- 14 (8%) | 0.98 +- 0.02 (2%) |
| MATH-team | 5 | 268 +- 34 (13%) | 11.2 +- 1.6 (15%) | 24.8 +- 1.6 (7%) | 2.8 +- 0.5 (18%) | 12.9 +- 3.5 (27%) | 41.9 +- 5.5 (13%) | 12.9 +- 3.5 (27%) | 31.6 +- 3.4 (11%) | 4,683 +- 562 (12%) | 523 +- 81 (16%) | 3.00 +- 0.00 (0%) |

