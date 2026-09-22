**Runs** (lead turns = lead activations; rounds = model calls of the agent loop; memory/compaction = auxiliary model calls; score: CODE tests passed, MATH answers correct, FQA keyword coverage)
| run | status | wall (s) | lead turns | lead rounds | teammates | teammate rounds | teammate tasks (closed) | memory calls | compaction calls | model errors (429 etc.) | tool calls (denied) | score |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| CODE-solo-r1 | completed | 361 | 1 | 18 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 19 (0) | 3/3 |
| CODE-solo-r2 | completed | 368 | 1 | 14 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 18 (0) | 3/3 |
| CODE-solo-r3 | completed | 430 | 1 | 12 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 20 (0) | 3/3 |
| FQA-solo-r1 | completed | 411 | 1 | 10 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 18 (0) | coverage 0.933 |
| FQA-solo-r2 | completed | 318 | 1 | 11 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 16 (0) | coverage 1.0 |
| FQA-solo-r3 | completed | 308 | 1 | 10 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 17 (0) | coverage 0.967 |
| MATH-solo-r1 | completed | 182 | 1 | 2 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 1 (0) | 3/3 |
| MATH-solo-r2 | completed | 216 | 1 | 3 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 2 (0) | 3/3 |
| MATH-solo-r3 | completed | 230 | 1 | 2 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 1 (1) | 3/3 |
| CODE-team-r1 | completed | 390 | 5 | 14 | 3 | 18 | 3 (3) | 5 | 0 | 0 | 32 (0) | 3/3 |
| CODE-team-r2 | completed | 440 | 5 | 13 | 3 | 20 | 3 (3) | 5 | 0 | 0 | 33 (0) | 3/3 |
| CODE-team-r3 | completed | 754 | 5 | 11 | 3 | 29 | 3 (3) | 5 | 0 | 0 | 40 (0) | 3/3 |
| CODE-team-r4 | completed | 381 | 5 | 13 | 3 | 18 | 3 (3) | 5 | 0 | 0 | 30 (0) | 3/3 |
| CODE-team-r5 | completed | 386 | 6 | 13 | 3 | 21 | 3 (3) | 6 | 0 | 0 | 37 (0) | 3/3 |
| FQA-team-r1 | completed | 417 | 4 | 10 | 3 | 12 | 3 (3) | 4 | 0 | 0 | 26 (0) | coverage 1.0 |
| FQA-team-r2 | completed | 506 | 4 | 11 | 3 | 14 | 3 (3) | 4 | 0 | 0 | 30 (0) | coverage 0.967 |
| FQA-team-r3 | completed | 396 | 4 | 11 | 3 | 14 | 3 (3) | 4 | 0 | 0 | 29 (0) | coverage 0.967 |
| FQA-team-r4 | completed | 398 | 5 | 14 | 3 | 11 | 3 (3) | 5 | 0 | 0 | 31 (0) | coverage 0.967 |
| FQA-team-r5 | completed | 385 | 4 | 11 | 3 | 12 | 3 (3) | 4 | 0 | 0 | 25 (0) | coverage 1.0 |
| MATH-team-r1 | completed | 318 | 7 | 16 | 3 | 14 | 3 (3) | 7 | 0 | 0 | 27 (1) | 3/3 |
| MATH-team-r2 | completed | 275 | 5 | 15 | 3 | 13 | 5 (5) | 5 | 0 | 0 | 26 (1) | 3/3 |
| MATH-team-r3 | completed | 395 | 5 | 16 | 3 | 10 | 4 (4) | 5 | 0 | 0 | 26 (1) | 3/3 |
| MATH-team-r4 | completed | 245 | 5 | 13 | 3 | 6 | 3 (3) | 5 | 0 | 0 | 18 (1) | 3/3 |
| MATH-team-r5 | completed | 297 | 3 | 11 | 3 | 15 | 5 (5) | 3 | 1 | 0 | 32 (1) | 3/3 |

**Latency breakdown of one agentic round** (time-weighted shares of the pooled round time; means per round in seconds)
| group | kind | rounds | final rounds | gross mean | gross median | gross p90 | prep | model | tools | other | prep share | model share (to first delivered block / streamed tail) | tools share | other share |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo | lead | 44 | 3 | 25.7 | 10.2 | 57.4 | 0.01 | 25.5 | 0.17 | 0.007 | 0.0% | 99.2% (5.1% / 94.1%) | 0.7% | 0.0% |
| FQA-solo | lead | 31 | 3 | 29.6 | 12.0 | 79.7 | 0.01 | 29.5 | 0.02 | 0.007 | 0.0% | 99.8% (3.8% / 96.0%) | 0.1% | 0.0% |
| MATH-solo | lead | 7 | 3 | 76.9 | 83.5 | 124.0 | 0.01 | 76.8 | 0.07 | 0.004 | 0.0% | 99.9% (0.8% / 99.1%) | 0.1% | 0.0% |
| CODE-team | lead | 64 | 26 | 11.8 | 8.6 | 23.9 | 0.01 | 11.8 | 0.02 | 0.004 | 0.1% | 99.6% (6.3% / 93.3%) | 0.2% | 0.0% |
| CODE-team | teammate | 106 | 15 | 27.6 | 5.3 | 85.5 | 0.01 | 27.5 | 0.12 | 0.006 | 0.0% | 99.5% (1.6% / 97.8%) | 0.4% | 0.0% |
| FQA-team | lead | 57 | 21 | 21.5 | 11.1 | 58.2 | 0.01 | 21.5 | 0.02 | 0.005 | 0.0% | 99.8% (4.8% / 95.0%) | 0.1% | 0.0% |
| FQA-team | teammate | 63 | 15 | 24.6 | 7.9 | 75.5 | 0.01 | 24.5 | 0.03 | 0.007 | 0.0% | 99.8% (3.9% / 95.9%) | 0.1% | 0.0% |
| MATH-team | lead | 71 | 25 | 14.5 | 7.6 | 32.5 | 0.74 | 13.7 | 0.03 | 0.005 | 5.1% | 94.6% (6.2% / 88.4%) | 0.2% | 0.0% |
| MATH-team | teammate | 58 | 20 | 27.0 | 23.8 | 60.2 | 0.01 | 27.0 | 0.05 | 0.004 | 0.0% | 99.7% (1.7% / 98.1%) | 0.2% | 0.0% |

**Context preparation, attributed** (lead rounds; seconds per round, pooled by group; `memory recall model` = model calls made by update_context; `post` = turn-end work after a final round: Stop hook + memory extraction + post-turn recall)
| group | rounds | prep mean | compaction pipeline (incl. any summary call) | of which summary-compaction model calls | summary calls / run | memory recall (model) | memory recall calls / round | prompt+tool assembly | inbox | unattributed | turns | post per turn | memory extract (model) per turn | memory calls per turn | post amortised per round | (prep + post) share of lead-side time |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo | 44 | 0.01 | 0.003 | 0.000 | 0.00 | 0.00 | 0.00 | 0.000 | 0.000 | 0.009 | 3 | 7.1 | 7.1 | 1.00 | 0.5 | 1.9% |
| FQA-solo | 31 | 0.01 | 0.002 | 0.000 | 0.00 | 0.00 | 0.00 | 0.000 | 0.000 | 0.008 | 3 | 38.4 | 38.4 | 1.00 | 3.7 | 11.2% |
| MATH-solo | 7 | 0.01 | 0.001 | 0.000 | 0.00 | 0.00 | 0.00 | 0.000 | 0.000 | 0.005 | 3 | 27.8 | 27.8 | 1.00 | 11.9 | 13.4% |
| CODE-team | 64 | 0.01 | 0.002 | 0.000 | 0.00 | 0.00 | 0.00 | 0.000 | 0.000 | 0.006 | 26 | 20.0 | 20.0 | 1.00 | 8.1 | 40.8% |
| FQA-team | 57 | 0.01 | 0.002 | 0.000 | 0.00 | 0.00 | 0.00 | 0.000 | 0.000 | 0.006 | 21 | 35.0 | 34.9 | 1.00 | 12.9 | 37.5% |
| MATH-team | 71 | 0.74 | 0.002 | 0.727 | 0.20 | 0.00 | 0.00 | 0.000 | 0.000 | 0.734 | 25 | 12.4 | 12.4 | 1.00 | 4.4 | 27.1% |

**Tokens and model-call timing per call** (agent calls only: purpose lead / teammate; prompt = input + cache_read (+cache_creation); uncached = tokens the provider computed: input_tokens plus cache_creation_input_tokens where reported (vLLM); first block = client time to the first delivered content block (NOT prefill alone: thinking/tool_use blocks arrive in bursts, see the delivery-pattern table); tail = first block to stream end; fit: first-block time = a + b x uncached tokens)
| group | kind | calls | prompt tok mean | prompt tok median | uncached mean | cache-read share | output tok mean | output tok median | thinking share of output chars | model call mean | first block mean | first block median | tail mean | tail tok/s (pooled) | first-block fit a (s) + b (ms/tok), r2 | first block is thinking |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| CODE-solo | lead | 44 | 9,445 | 8,464 | 7,093 | 24.9% | 633 | 216 | 55.1% | 25.5 | 1.3 | 1.1 | 24.2 | 26.1 | 0.12 s + 0.167 ms/tok, r2 1.00 | 100.0% |
| FQA-solo | lead | 31 | 8,380 | 7,321 | 6,028 | 28.1% | 739 | 296 | 62.0% | 29.5 | 1.1 | 0.9 | 28.4 | 26.1 | 0.14 s + 0.163 ms/tok, r2 1.00 | 100.0% |
| MATH-solo | lead | 7 | 4,986 | 5,394 | 2,634 | 47.2% | 2,001 | 2,186 | 61.4% | 76.8 | 0.6 | 0.6 | 76.2 | 26.3 | 0.31 s + 0.112 ms/tok, r2 0.94 | 100.0% |
| CODE-team | lead | 64 | 5,948 | 6,106 | 3,596 | 39.5% | 285 | 197 | 32.5% | 11.8 | 0.7 | 0.8 | 11.0 | 25.8 | 0.24 s + 0.141 ms/tok, r2 0.94 | 100.0% |
| CODE-team | teammate | 106 | 5,936 | 4,224 | 1,321 | 77.8% | 670 | 122 | 68.0% | 27.5 | 0.4 | 0.4 | 27.0 | 24.8 | 0.27 s + 0.131 ms/tok, r2 0.77 | 100.0% |
| FQA-team | lead | 57 | 7,265 | 6,394 | 4,913 | 32.4% | 521 | 213 | 49.3% | 21.5 | 1.0 | 0.9 | 20.4 | 25.5 | 0.29 s + 0.151 ms/tok, r2 0.55 | 100.0% |
| FQA-team | teammate | 63 | 8,830 | 9,324 | 3,292 | 62.7% | 544 | 150 | 64.9% | 24.5 | 1.0 | 0.6 | 23.6 | 23.1 | 0.33 s + 0.189 ms/tok, r2 0.82 | 100.0% |
| MATH-team | lead | 71 | 6,826 | 6,127 | 4,474 | 34.5% | 332 | 166 | 47.0% | 13.7 | 0.9 | 0.8 | 12.8 | 25.8 | 0.22 s + 0.154 ms/tok, r2 0.97 | 100.0% |
| MATH-team | teammate | 58 | 2,698 | 2,551 | 1,022 | 62.1% | 646 | 571 | 50.1% | 27.0 | 0.4 | 0.4 | 26.5 | 24.4 | 0.35 s + 0.097 ms/tok, r2 0.09 | 100.0% |
| pooled fit | lead | 274 | | | uncached 821-16,460 | | | | | | | | | 25.9 | first block vs uncached: 0.20 s + 0.158 ms/tok (r2 0.88); vs prompt: -0.18 s + 0.158 ms/tok (r2 0.88); tail vs output tok: 0.17 s + 38.3 ms/tok (r2 1.00) | |
| pooled fit | teammate | 227 | | | uncached 142-16,207 | | | | | | | | | 24.3 | first block vs uncached: 0.25 s + 0.186 ms/tok (r2 0.79); vs prompt: 0.38 s + 0.035 ms/tok (r2 0.11); tail vs output tok: 0.33 s + 40.7 ms/tok (r2 1.00) | |
| pooled fit | all | 501 | | | uncached 142-16,460 | | | | | | | | | 25.0 | first block vs uncached: 0.24 s + 0.159 ms/tok (r2 0.84); vs prompt: 0.27 s + 0.080 ms/tok (r2 0.35); tail vs output tok: 0.06 s + 39.8 ms/tok (r2 1.00) | |

**Prefill vs decode by regression** (agent calls only; model-call duration = a + b x uncached prompt tokens + c x output tokens, least squares; the per-group shares apply the pooled coefficients to each group's own token counts, so they add to ~100% of that group's call time)
| group | kind | calls | mean call (s) | fixed a | prefill b x uncached | decode c x output | residual |
|---|---|---:|---:|---:|---:|---:|---:|
| CODE-solo | lead | 44 | 25.5 | 1.7% | 3.2% | 98.9% | -3.8% |
| FQA-solo | lead | 31 | 29.5 | 1.5% | 2.4% | 99.9% | -3.8% |
| MATH-solo | lead | 7 | 76.8 | 0.6% | 0.4% | 103.9% | -4.9% |
| CODE-team | lead | 64 | 11.8 | 3.7% | 3.6% | 96.3% | -3.5% |
| CODE-team | teammate | 106 | 27.5 | 1.6% | 0.6% | 97.3% | 0.6% |
| FQA-team | lead | 57 | 21.5 | 2.0% | 2.7% | 96.8% | -1.5% |
| FQA-team | teammate | 63 | 24.5 | 1.8% | 1.6% | 88.5% | 8.2% |
| MATH-team | lead | 71 | 13.7 | 3.1% | 3.8% | 96.3% | -3.2% |
| MATH-team | teammate | 58 | 27.0 | 1.6% | 0.4% | 95.6% | 2.3% |

| fit over | calls | a fixed (s) | b prefill (ms per uncached tok) | c decode (ms per output tok) | r2 |
|---|---:|---:|---:|---:|---:|
| all agent calls | 501 | 0.43 | 0.116 | 39.88 | 1.00 |
| lead | 274 | 0.35 | 0.162 | 38.28 | 1.00 |
| teammate | 227 | 0.26 | 0.411 | 40.57 | 1.00 |
| responses ending in tool_use | 370 | 0.33 | 0.145 | 39.93 | 1.00 |
| responses ending in end_turn | 131 | 0.89 | 0.069 | 38.80 | 1.00 |

**Streaming delivery pattern** (why the first-block time is not prefill: the provider delivers thinking and tool_use blocks in bursts; 'first block' = client time to the first delivered content block, 'tail' = first block to end of stream; a tail under 0.1 s means the whole response arrived at once)
| kind | response ends with | calls | output tok mean | first block mean (s) | tail mean (s) | tail share of call | calls with tail < 0.1 s | first-block fit: a + c x output tok (r2) |
|---|---|---:|---:|---:|---:|---:|---:|---|
| lead | tool_use | 193 | 582 | 0.89 | 22.43 | 96.2% | 0.0% | 0.19 s + 0.00 ms/tok (0.98) |
| lead | end_turn | 81 | 296 | 1.18 | 11.51 | 90.7% | 0.0% | 0.24 s + -0.01 ms/tok (0.69) |
| teammate | tool_use | 177 | 716 | 0.65 | 29.50 | 97.8% | 0.0% | 0.26 s + 0.01 ms/tok (0.79) |
| teammate | end_turn | 50 | 324 | 0.37 | 13.33 | 97.3% | 0.0% | 0.29 s + -0.15 ms/tok (0.37) |

**Auxiliary model calls made by the harness itself** (context maintenance: memory recall inside update_context before a lead call, memory extraction after a lead turn, summary compaction; pooled by group)
| group | purpose | calls | calls / run | calls / lead round | mean duration | median | prompt tok mean | output tok mean | stop=max_tokens share | first block mean | tail mean | total per run (s) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo | memory_extract | 3 | 1.0 | 0.07 | 7.1 | 8.2 | 409 | 182 | 0.0% | 0.2 | 6.8 | 7.1 |
| FQA-solo | memory_extract | 3 | 1.0 | 0.10 | 38.4 | 38.4 | 2,144 | 1,000 | 100.0% | 0.4 | 37.9 | 38.4 |
| MATH-solo | memory_extract | 3 | 1.0 | 0.43 | 27.8 | 32.4 | 1,906 | 723 | 33.3% | 0.4 | 27.4 | 27.8 |
| CODE-team | memory_extract | 26 | 5.2 | 0.41 | 20.0 | 17.1 | 766 | 502 | 11.5% | 0.2 | 19.7 | 103.8 |
| FQA-team | memory_extract | 21 | 4.2 | 0.37 | 34.9 | 38.4 | 1,537 | 884 | 81.0% | 0.3 | 34.6 | 146.8 |
| MATH-team | compaction_summary | 1 | 0.2 | 0.01 | 51.6 | 51.6 | 3,002 | 1,260 | 0.0% | 0.6 | 51.0 | 10.3 |
| MATH-team | memory_extract | 25 | 5.0 | 0.35 | 12.4 | 9.7 | 1,426 | 306 | 4.0% | 0.3 | 12.1 | 61.9 |

**Teammate input redundancy** (a) re-sent = prompt tokens already sent in the same agent's previous call (append-only history), (b) provider cache-read share, (c) cross-teammate file bytes another teammate had fetched first (exact (file,line) provenance) and their share of teammate prompt tokens
| run | teammates | teammate calls | teammate prompt tok | re-sent share (a) | cache-read share (b) | file bytes fetched | cross-teammate redundancy, range (c) | same, whole-result SHA | resident copies | cross-redundant share of prompt tok | file content share of prompt tok | lead: prompt tok | lead re-sent share | lead cache-read |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo-r1 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 147,339 | 89.8% | 28.7% |
| CODE-solo-r2 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 148,042 | 88.9% | 22.2% |
| CODE-solo-r3 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 120,211 | 84.9% | 23.5% |
| FQA-solo-r1 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 83,545 | 77.5% | 28.2% |
| FQA-solo-r2 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 95,161 | 83.6% | 27.2% |
| FQA-solo-r3 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 81,069 | 80.4% | 29.0% |
| MATH-solo-r1 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 8,884 | 35.7% | 52.9% |
| MATH-solo-r2 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 17,451 | 58.4% | 40.4% |
| MATH-solo-r3 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 8,567 | 37.0% | 54.9% |
| CODE-team-r1 | 3 | 18 | 88,422 | 77.8% | 74.5% | 13,848 | 0.0% | 0.0% | 1.00x | 0.0% | 21.5% | 80,934 | 90.8% | 40.7% |
| CODE-team-r2 | 3 | 20 | 82,549 | 74.3% | 67.4% | 13,848 | 0.0% | 0.0% | 1.00x | 0.0% | 23.3% | 73,962 | 89.6% | 41.3% |
| CODE-team-r3 | 3 | 29 | 283,626 | 87.7% | 84.9% | 19,108 | 0.0% | 0.0% | 1.00x | 0.0% | 15.4% | 59,281 | 88.1% | 43.6% |
| CODE-team-r4 | 3 | 18 | 76,041 | 72.2% | 68.0% | 13,848 | 0.0% | 0.0% | 1.00x | 0.0% | 25.2% | 80,916 | 89.8% | 37.8% |
| CODE-team-r5 | 3 | 21 | 98,569 | 81.2% | 76.4% | 13,851 | 0.0% | 0.0% | 1.00x | 0.0% | 23.1% | 85,603 | 90.1% | 35.7% |
| FQA-team-r1 | 3 | 12 | 110,000 | 64.5% | 62.7% | 112,843 | 22.8% | 22.8% | 1.30x | 15.9% | 80.7% | 69,495 | 82.9% | 33.8% |
| FQA-team-r2 | 3 | 14 | 108,340 | 63.7% | 61.5% | 112,843 | 22.8% | 22.8% | 1.30x | 16.6% | 78.7% | 99,178 | 86.2% | 26.1% |
| FQA-team-r3 | 3 | 14 | 119,490 | 67.1% | 64.3% | 112,843 | 22.8% | 22.8% | 1.30x | 17.9% | 78.4% | 74,465 | 85.5% | 34.7% |
| FQA-team-r4 | 3 | 11 | 104,186 | 61.7% | 60.2% | 112,843 | 22.8% | 22.8% | 1.30x | 20.1% | 79.5% | 93,769 | 88.8% | 35.1% |
| FQA-team-r5 | 3 | 12 | 114,267 | 66.2% | 64.5% | 112,843 | 22.8% | 22.8% | 1.30x | 18.4% | 80.6% | 77,206 | 84.8% | 33.5% |
| MATH-team-r1 | 3 | 14 | 42,721 | 70.6% | 66.1% | 0 | - | - | - | 0.0% | 0.0% | 111,621 | 90.3% | 33.7% |
| MATH-team-r2 | 3 | 13 | 32,628 | 67.7% | 60.1% | 0 | - | - | - | 0.0% | 0.0% | 100,247 | 89.9% | 35.2% |
| MATH-team-r3 | 3 | 10 | 25,979 | 59.4% | 57.3% | 0 | - | - | - | 0.0% | 0.0% | 124,727 | 90.1% | 30.2% |
| MATH-team-r4 | 3 | 6 | 10,265 | 36.6% | 45.8% | 0 | - | - | - | 0.0% | 0.0% | 83,812 | 88.2% | 36.5% |
| MATH-team-r5 | 3 | 15 | 44,882 | 72.1% | 66.4% | 0 | - | - | - | 0.0% | 0.0% | 64,268 | 81.1% | 40.3% |

**Agent runs per task** (mean over runs of a group unless noted; a teammate task = rounds from assignment to its final reply; wall = driver wall time)
| group | runs | wall mean (s) | lead turns / run | lead rounds / run | lead rounds / turn | teammates / run | teammate rounds / task: mean | median | max | teammate task active time (s): mean | median | teammate tasks / run | tool calls / round (lead) | tool calls / round (teammate) | model calls / run (all) | denied tool calls / run |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo | 3 | 386 | 1.0 | 14.7 | 14.7 | 0.0 | - | - | 0 | - | - | 0.0 | 1.30 | 0.00 | 15.7 | 0.0 |
| FQA-solo | 3 | 346 | 1.0 | 10.3 | 10.3 | 0.0 | - | - | 0 | - | - | 0.0 | 1.65 | 0.00 | 11.3 | 0.0 |
| MATH-solo | 3 | 209 | 1.0 | 2.3 | 2.3 | 0.0 | - | - | 0 | - | - | 0.0 | 0.57 | 0.00 | 3.3 | 0.3 |
| CODE-team | 5 | 470 | 5.2 | 12.8 | 2.5 | 3.0 | 7.1 | 6 | 16 | 195 | 170 | 3.0 | 1.14 | 0.93 | 39.2 | 0.0 |
| FQA-team | 5 | 420 | 4.2 | 11.4 | 2.7 | 3.0 | 4.2 | 4 | 5 | 103 | 102 | 3.0 | 1.25 | 1.11 | 28.2 | 0.0 |
| MATH-team | 5 | 306 | 5.0 | 14.2 | 2.8 | 3.0 | 2.9 | 3 | 5 | 78 | 63 | 4.0 | 1.27 | 0.67 | 31.0 | 1.0 |

**Where the wall time of a run goes** (means over the runs of a group; lead active = lead turns incl. turn-end memory work; teammate active = union of teammate round windows; overlap = lead and teammates busy at the same time; idle tail = nobody busy: waits for team events, quiescence timer, shutdown handshake)
| group | runs | wall (s) | lead active (s) | share | of which lead agent calls (s) | of which turn-end memory (s) | teammate active (s) | share | overlap (s) | idle / tail (s) | share |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo | 3 | 386 | 384 | 99.5% | 374 | 7 | 0 | 0.0% | 0 | 2 | 0.5% |
| FQA-solo | 3 | 346 | 344 | 99.4% | 305 | 38 | 0 | 0.0% | 0 | 2 | 0.6% |
| MATH-solo | 3 | 209 | 207 | 99.0% | 179 | 28 | 0 | 0.0% | 0 | 2 | 1.0% |
| CODE-team | 5 | 470 | 256 | 54.4% | 151 | 104 | 325 | 69.1% | 113 | 3 | 0.5% |
| FQA-team | 5 | 420 | 392 | 93.3% | 245 | 147 | 123 | 29.3% | 98 | 3 | 0.7% |
| MATH-team | 5 | 306 | 268 | 87.8% | 195 | 62 | 156 | 51.0% | 122 | 3 | 0.9% |

**Tool execution time by tool** (tool_start -> tool_end spans, pooled over all runs)
| tool | calls | denied | error | mean (ms) | median (ms) | p90 (ms) | max (ms) | total (s) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| bash | 80 | 5 | 12 | 274 | 232 | 496 | 1,351 | 21.9 |
| spawn_teammate | 45 | 0 | 0 | 60 | 66 | 77 | 100 | 2.7 |
| read_file | 100 | 0 | 0 | 20 | 21 | 35 | 59 | 2.0 |
| complete_task | 39 | 0 | 0 | 36 | 36 | 40 | 49 | 1.4 |
| create_task | 45 | 0 | 0 | 17 | 17 | 21 | 22 | 0.8 |
| request_shutdown | 45 | 0 | 0 | 15 | 8 | 35 | 38 | 0.7 |
| write_file | 26 | 1 | 0 | 21 | 25 | 30 | 37 | 0.5 |
| edit_file | 14 | 0 | 0 | 23 | 25 | 32 | 34 | 0.3 |
| send_message | 27 | 0 | 0 | 11 | 11 | 15 | 15 | 0.3 |
| claim_task | 17 | 0 | 0 | 14 | 14 | 17 | 19 | 0.2 |
| todo_write | 97 | 0 | 0 | 1 | 1 | 1 | 1 | 0.1 |
| glob | 6 | 0 | 0 | 11 | 5 | 26 | 33 | 0.1 |
| list_tasks | 4 | 0 | 0 | 12 | 12 | 13 | 13 | 0.0 |
| get_task | 5 | 0 | 0 | 5 | 5 | 5 | 5 | 0.0 |
| list_teammates | 3 | 0 | 0 | 1 | 1 | 1 | 1 | 0.0 |
| compact | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0.0 |

**Per-run round breakdown** (lead and teammate rounds separately; seconds)
| run | kind | rounds | gross mean | prep mean | model mean | first block mean | tail mean | tools mean | other mean | prep share | model share | tools share | prompt tok mean | output tok mean |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo-r1 | lead | 18 | 19.7 | 0.01 | 19.6 | 1.1 | 18.5 | 0.13 | 0.008 | 0.1% | 99.2% | 0.6% | 8,186 | 484 |
| CODE-solo-r2 | lead | 14 | 25.5 | 0.01 | 25.3 | 1.5 | 23.8 | 0.14 | 0.006 | 0.1% | 99.3% | 0.6% | 10,574 | 622 |
| CODE-solo-r3 | lead | 12 | 35.0 | 0.01 | 34.7 | 1.4 | 33.3 | 0.26 | 0.007 | 0.0% | 99.1% | 0.7% | 10,018 | 868 |
| FQA-solo-r1 | lead | 10 | 37.0 | 0.01 | 37.0 | 1.1 | 35.9 | 0.02 | 0.007 | 0.0% | 99.9% | 0.0% | 8,354 | 932 |
| FQA-solo-r2 | lead | 11 | 25.2 | 0.01 | 25.2 | 1.2 | 24.0 | 0.01 | 0.007 | 0.0% | 99.8% | 0.1% | 8,651 | 628 |
| FQA-solo-r3 | lead | 10 | 26.8 | 0.01 | 26.7 | 1.1 | 25.7 | 0.02 | 0.007 | 0.0% | 99.8% | 0.1% | 8,107 | 669 |
| MATH-solo-r1 | lead | 2 | 70.7 | 0.01 | 70.6 | 0.5 | 70.0 | 0.10 | 0.004 | 0.0% | 99.8% | 0.1% | 4,442 | 1,842 |
| MATH-solo-r2 | lead | 3 | 67.0 | 0.01 | 66.9 | 0.7 | 66.2 | 0.09 | 0.005 | 0.0% | 99.8% | 0.1% | 5,817 | 1,737 |
| MATH-solo-r3 | lead | 2 | 98.0 | 0.01 | 97.9 | 0.5 | 97.4 | 0.00 | 0.004 | 0.0% | 100.0% | 0.0% | 4,284 | 2,558 |
| CODE-team-r1 | lead | 14 | 10.1 | 0.01 | 10.0 | 0.7 | 9.3 | 0.02 | 0.005 | 0.1% | 99.6% | 0.2% | 5,781 | 240 |
| CODE-team-r1 | teammate | 18 | 25.9 | 0.01 | 25.8 | 0.4 | 25.4 | 0.12 | 0.006 | 0.0% | 99.4% | 0.5% | 4,912 | 627 |
| CODE-team-r2 | lead | 13 | 10.9 | 0.01 | 10.8 | 0.7 | 10.1 | 0.03 | 0.005 | 0.1% | 99.5% | 0.2% | 5,689 | 262 |
| CODE-team-r2 | teammate | 20 | 27.1 | 0.01 | 27.0 | 0.4 | 26.5 | 0.09 | 0.006 | 0.0% | 99.6% | 0.3% | 4,127 | 658 |
| CODE-team-r3 | lead | 11 | 11.1 | 0.01 | 11.0 | 0.7 | 10.4 | 0.02 | 0.004 | 0.1% | 99.5% | 0.2% | 5,389 | 269 |
| CODE-team-r3 | teammate | 29 | 31.9 | 0.01 | 31.8 | 0.5 | 31.3 | 0.14 | 0.006 | 0.0% | 99.4% | 0.4% | 9,780 | 787 |
| CODE-team-r4 | lead | 13 | 13.4 | 0.01 | 13.3 | 0.8 | 12.5 | 0.02 | 0.004 | 0.1% | 99.7% | 0.2% | 6,224 | 322 |
| CODE-team-r4 | teammate | 18 | 30.5 | 0.01 | 30.4 | 0.5 | 30.0 | 0.09 | 0.005 | 0.0% | 99.6% | 0.3% | 4,224 | 735 |
| CODE-team-r5 | lead | 13 | 13.8 | 0.01 | 13.8 | 0.9 | 12.9 | 0.02 | 0.004 | 0.1% | 99.7% | 0.2% | 6,585 | 333 |
| CODE-team-r5 | teammate | 21 | 21.1 | 0.01 | 21.0 | 0.4 | 20.6 | 0.13 | 0.005 | 0.0% | 99.2% | 0.6% | 4,694 | 505 |
| FQA-team-r1 | lead | 10 | 23.8 | 0.01 | 23.8 | 0.9 | 22.9 | 0.03 | 0.005 | 0.0% | 99.8% | 0.1% | 6,950 | 586 |
| FQA-team-r1 | teammate | 12 | 26.1 | 0.01 | 26.1 | 1.0 | 25.1 | 0.03 | 0.006 | 0.0% | 99.8% | 0.1% | 9,167 | 584 |
| FQA-team-r2 | lead | 11 | 30.0 | 0.01 | 30.0 | 1.5 | 28.4 | 0.03 | 0.006 | 0.0% | 99.8% | 0.1% | 9,016 | 733 |
| FQA-team-r2 | teammate | 14 | 22.2 | 0.01 | 22.2 | 0.9 | 21.3 | 0.03 | 0.007 | 0.0% | 99.7% | 0.1% | 7,739 | 486 |
| FQA-team-r3 | lead | 11 | 20.6 | 0.01 | 20.5 | 0.9 | 19.6 | 0.02 | 0.005 | 0.0% | 99.8% | 0.1% | 6,770 | 500 |
| FQA-team-r3 | teammate | 14 | 19.6 | 0.01 | 19.5 | 0.9 | 18.6 | 0.03 | 0.007 | 0.0% | 99.7% | 0.1% | 8,535 | 430 |
| FQA-team-r4 | lead | 14 | 14.3 | 0.01 | 14.3 | 0.9 | 13.4 | 0.02 | 0.005 | 0.1% | 99.7% | 0.1% | 6,698 | 338 |
| FQA-team-r4 | teammate | 11 | 31.3 | 0.01 | 31.2 | 1.2 | 30.1 | 0.04 | 0.008 | 0.0% | 99.8% | 0.1% | 9,471 | 698 |
| FQA-team-r5 | lead | 11 | 20.9 | 0.01 | 20.9 | 1.0 | 19.9 | 0.03 | 0.005 | 0.0% | 99.7% | 0.1% | 7,019 | 505 |
| FQA-team-r5 | teammate | 12 | 25.5 | 0.01 | 25.5 | 0.8 | 24.6 | 0.03 | 0.007 | 0.0% | 99.8% | 0.1% | 9,522 | 566 |
| MATH-team-r1 | lead | 16 | 9.8 | 0.01 | 9.8 | 0.9 | 8.8 | 0.02 | 0.005 | 0.1% | 99.6% | 0.2% | 6,976 | 228 |
| MATH-team-r1 | teammate | 14 | 28.4 | 0.01 | 28.3 | 0.4 | 27.9 | 0.05 | 0.005 | 0.0% | 99.7% | 0.2% | 3,052 | 677 |
| MATH-team-r2 | lead | 15 | 12.0 | 0.01 | 12.0 | 0.9 | 11.1 | 0.02 | 0.005 | 0.1% | 99.6% | 0.2% | 6,683 | 286 |
| MATH-team-r2 | teammate | 13 | 21.6 | 0.01 | 21.6 | 0.5 | 21.1 | 0.04 | 0.004 | 0.0% | 99.7% | 0.2% | 2,510 | 511 |
| MATH-team-r3 | lead | 16 | 18.7 | 0.01 | 18.6 | 1.1 | 17.5 | 0.07 | 0.005 | 0.1% | 99.5% | 0.3% | 7,795 | 453 |
| MATH-team-r3 | teammate | 10 | 30.6 | 0.01 | 30.6 | 0.5 | 30.1 | 0.05 | 0.004 | 0.0% | 99.8% | 0.2% | 2,598 | 740 |
| MATH-team-r4 | lead | 13 | 12.2 | 0.01 | 12.2 | 0.8 | 11.4 | 0.02 | 0.004 | 0.1% | 99.6% | 0.2% | 6,447 | 296 |
| MATH-team-r4 | teammate | 6 | 35.3 | 0.00 | 35.3 | 0.4 | 34.9 | 0.07 | 0.003 | 0.0% | 99.8% | 0.2% | 1,711 | 854 |
| MATH-team-r5 | lead | 11 | 21.5 | 4.70 | 16.8 | 0.8 | 16.0 | 0.03 | 0.006 | 21.9% | 77.9% | 0.1% | 5,843 | 411 |
| MATH-team-r5 | teammate | 15 | 24.7 | 0.01 | 24.6 | 0.5 | 24.1 | 0.06 | 0.004 | 0.0% | 99.6% | 0.3% | 2,992 | 589 |

**Server-side timing from vLLM /metrics** (agent calls; deltas of the server's per-request histograms scraped right after each call; exact = exactly one request finished since the previous scrape, so the deltas belong to this call, otherwise they pool concurrent requests and are used only in the run totals; client gap = client model-call duration minus server e2e latency, i.e. HTTP/API-layer + SDK overhead; prefill tok/s = KV-computed tokens / prefill s; decode tok/s = (generation tokens - 1) / decode s; scrape = profiler overhead per call, kept out of the round buckets as `instrument`)
| group | kind | calls | exact | server prefill mean / median (s) | server decode mean / median (s) | server queue mean (s) | server e2e mean (s) | client call mean (s) | client gap mean / median (s) | client first block vs server TTFT mean (s) | prefill tok/s | decode tok/s | running at finish | KV usage at finish | finished=length | preemptions | scrape mean (ms) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo | lead | 44 | 100.0% | 1.16 / 0.97 | 24.2 / 8.3 | 0.000 | 25.5 | 25.5 | 0.06 / 0.06 | 1.30 vs 1.22 | 6,137 | 26 | 0.0 | 0.0% | 0.0% | 0 | 18.7 |
| FQA-solo | lead | 31 | 100.0% | 0.99 / 0.79 | 28.4 / 11.3 | 0.000 | 29.4 | 29.5 | 0.06 / 0.05 | 1.12 vs 1.05 | 6,110 | 26 | 0.0 | 0.0% | 0.0% | 0 | 18.7 |
| MATH-solo | lead | 7 | 100.0% | 0.47 / 0.51 | 76.2 / 83.1 | 0.000 | 76.8 | 76.8 | 0.05 / 0.04 | 0.60 vs 0.51 | 5,656 | 26 | 0.0 | 0.0% | 0.0% | 0 | 20.8 |
| CODE-team | lead | 64 | 100.0% | 0.61 / 0.63 | 11.1 / 7.8 | 0.000 | 11.7 | 11.8 | 0.06 / 0.06 | 0.75 vs 0.64 | 5,920 | 26 | 0.9 | 1.2% | 0.0% | 0 | 19.8 |
| CODE-team | teammate | 106 | 100.0% | 0.34 / 0.27 | 27.0 / 4.8 | 0.000 | 27.4 | 27.5 | 0.03 / 0.03 | 0.45 vs 0.43 | 3,937 | 25 | 1.4 | 2.0% | 0.0% | 0 | 21.2 |
| FQA-team | lead | 57 | 100.0% | 0.86 / 0.75 | 20.4 / 9.7 | 0.000 | 21.4 | 21.5 | 0.06 / 0.06 | 1.03 vs 1.11 | 5,693 | 25 | 1.0 | 2.8% | 0.0% | 0 | 19.1 |
| FQA-team | teammate | 63 | 100.0% | 0.79 / 0.42 | 23.6 / 7.4 | 0.000 | 24.5 | 24.5 | 0.03 / 0.03 | 0.95 vs 0.77 | 4,151 | 23 | 2.2 | 4.7% | 0.0% | 0 | 19.2 |
| MATH-team | lead | 71 | 100.0% | 0.76 / 0.69 | 12.9 / 6.7 | 0.000 | 13.7 | 13.7 | 0.06 / 0.06 | 0.91 vs 0.80 | 5,853 | 26 | 0.9 | 0.9% | 0.0% | 0 | 18.9 |
| MATH-team | teammate | 58 | 100.0% | 0.34 / 0.27 | 26.5 / 23.4 | 0.000 | 26.9 | 27.0 | 0.03 / 0.03 | 0.45 vs 0.48 | 2,966 | 24 | 2.0 | 2.3% | 0.0% | 0 | 19.0 |

| fit over exact calls | calls | server prefill (s) = a + b x computed tok, r2 -> tok/s | server decode (s) = a + c x generation tok, r2 -> ms/tok | client gap (s): mean / median / p90 |
|---|---:|---|---|---|
| lead | 274 | 0.075 + 0.1536 ms/tok (r2 0.95) -> 6,511 tok/s | 0.18 + 38.3 ms/tok (r2 1.00) | 0.06 / 0.06 / 0.07 |
| teammate | 227 | 0.150 + 0.1757 ms/tok (r2 0.80) -> 5,692 tok/s | 0.34 + 40.7 ms/tok (r2 1.00) | 0.03 / 0.03 / 0.04 |
| all | 501 | 0.132 + 0.1521 ms/tok (r2 0.88) -> 6,574 tok/s | 0.07 + 39.8 ms/tok (r2 1.00) | 0.05 / 0.04 / 0.07 |

| run | server prompt tok (all requests) | server cached tok | server cached share | usage cache-read share (agent calls) | prefix-cache hit rate (blocks) | generation tok | server prefill (s) | server decode (s) | server queue (s) | server e2e (s) | client model time, all calls (s) | finished by reason | preemptions |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| CODE-solo-r1 | 147,719 | 42,336 | 28.7% | 28.7% | 28.7% | 8,809 | 17.3 | 336.6 | 0.00 | 355.0 | 356.1 | stop 19 | 0 |
| CODE-solo-r2 | 148,463 | 32,928 | 22.2% | 22.2% | 22.2% | 8,946 | 18.9 | 342.5 | 0.00 | 362.4 | 363.3 | stop 15 | 0 |
| CODE-solo-r3 | 120,638 | 28,224 | 23.4% | 23.5% | 23.4% | 10,629 | 15.3 | 407.7 | 0.00 | 423.8 | 424.6 | stop 13 | 0 |
| FQA-solo-r1 | 85,798 | 23,520 | 27.4% | 28.2% | 27.4% | 10,325 | 10.3 | 396.8 | 0.00 | 407.7 | 408.2 | length 1, stop 10 | 0 |
| FQA-solo-r2 | 97,115 | 25,872 | 26.6% | 27.2% | 26.6% | 7,903 | 11.6 | 302.6 | 0.00 | 314.9 | 315.5 | length 1, stop 11 | 0 |
| FQA-solo-r3 | 83,294 | 23,520 | 28.2% | 29.0% | 28.2% | 7,691 | 9.8 | 294.8 | 0.00 | 305.2 | 305.8 | length 1, stop 10 | 0 |
| MATH-solo-r1 | 10,628 | 4,704 | 44.3% | 52.9% | 44.3% | 4,683 | 1.1 | 178.2 | 0.00 | 179.3 | 179.4 | length 1, stop 2 | 0 |
| MATH-solo-r2 | 19,302 | 7,056 | 36.6% | 40.4% | 36.6% | 5,536 | 2.1 | 210.9 | 0.00 | 213.1 | 213.2 | stop 4 | 0 |
| MATH-solo-r3 | 10,691 | 4,704 | 44.0% | 54.9% | 44.0% | 5,959 | 1.1 | 226.9 | 0.00 | 228.2 | 228.3 | stop 3 | 0 |
| CODE-team-r1 | 172,899 | 98,784 | 57.1% | 58.3% | 57.1% | 17,892 | 15.1 | 714.7 | 0.00 | 732.0 | 733.4 | length 1, stop 36 | 0 |
| CODE-team-r2 | 160,416 | 87,024 | 54.2% | 55.1% | 54.2% | 18,826 | 14.8 | 751.1 | 0.00 | 768.1 | 769.5 | stop 38 | 0 |
| CODE-team-r3 | 346,935 | 266,560 | 76.8% | 77.7% | 76.8% | 28,818 | 17.0 | 1,140.7 | 0.00 | 1,161.1 | 1,163.0 | length 1, stop 44 | 0 |
| CODE-team-r4 | 160,741 | 82,320 | 51.2% | 52.4% | 51.2% | 19,504 | 15.8 | 785.4 | 0.00 | 803.5 | 804.8 | stop 36 | 0 |
| CODE-team-r5 | 188,820 | 105,840 | 56.1% | 57.5% | 56.1% | 17,304 | 16.8 | 694.7 | 0.00 | 713.9 | 715.6 | length 1, stop 39 | 0 |
| FQA-team-r1 | 185,805 | 94,080 | 50.6% | 51.5% | 50.6% | 16,604 | 19.0 | 678.0 | 0.00 | 699.2 | 700.1 | length 3, stop 23 | 0 |
| FQA-team-r2 | 213,741 | 94,080 | 44.0% | 44.6% | 44.0% | 18,865 | 25.3 | 767.1 | 0.00 | 796.3 | 797.4 | length 4, stop 25 | 0 |
| FQA-team-r3 | 200,096 | 102,704 | 51.3% | 53.0% | 51.3% | 14,809 | 19.7 | 605.7 | 0.00 | 628.2 | 629.3 | length 3, stop 26 | 0 |
| FQA-team-r4 | 205,300 | 95,648 | 46.6% | 48.3% | 46.6% | 16,635 | 22.8 | 683.9 | 0.00 | 709.0 | 710.3 | length 4, stop 26 | 0 |
| FQA-team-r5 | 197,724 | 99,568 | 50.4% | 52.0% | 50.4% | 15,643 | 18.6 | 644.3 | 0.00 | 665.0 | 666.0 | length 3, stop 24 | 0 |
| MATH-team-r1 | 166,006 | 66,640 | 40.1% | 42.7% | 40.1% | 15,333 | 19.0 | 620.2 | 0.00 | 641.5 | 642.9 | stop 37 | 0 |
| MATH-team-r2 | 137,169 | 54,880 | 40.0% | 41.3% | 40.0% | 12,856 | 17.2 | 515.8 | 0.00 | 535.3 | 536.6 | length 1, stop 32 | 0 |
| MATH-team-r3 | 154,209 | 52,528 | 34.1% | 34.9% | 34.1% | 15,647 | 18.9 | 620.6 | 0.00 | 641.6 | 642.9 | stop 31 | 0 |
| MATH-team-r4 | 101,611 | 36,064 | 35.5% | 37.5% | 35.5% | 10,100 | 12.2 | 401.5 | 0.00 | 414.9 | 415.8 | stop 24 | 0 |
| MATH-team-r5 | 120,796 | 58,800 | 48.7% | 51.0% | 48.7% | 16,020 | 14.0 | 645.1 | 0.00 | 660.9 | 662.0 | stop 30 | 0 |

**Stability across repetitions** (per group: mean +- sd over the runs of the group, CV = sd / mean in parentheses; each run contributes its own mean; a teammate task = rounds from assignment to the final reply)
| group | runs | wall (s) | lead rounds / run | model calls / run | teammate rounds / task | lead gross / round (s) | teammate gross / round (s) | lead model call (s) | teammate model call (s) | prompt tok / agent call | output tok / agent call | score |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo | 3 | 386 +- 38 (10%) | 14.7 +- 3.1 (21%) | 15.7 +- 3.1 (20%) | - | 26.7 +- 7.7 (29%) | - | 26.5 +- 7.6 (29%) | - | 9,593 +- 1,250 (13%) | 658 +- 195 (30%) | 3.00 +- 0.00 (0%) |
| FQA-solo | 3 | 346 +- 57 (16%) | 10.3 +- 0.6 (6%) | 11.3 +- 0.6 (5%) | - | 29.7 +- 6.4 (22%) | - | 29.6 +- 6.4 (22%) | - | 8,371 +- 272 (3%) | 743 +- 165 (22%) | 0.97 +- 0.03 (3%) |
| MATH-solo | 3 | 209 +- 25 (12%) | 2.3 +- 0.6 (25%) | 3.3 +- 0.6 (17%) | - | 78.5 +- 16.9 (22%) | - | 78.5 +- 17.0 (22%) | - | 4,848 +- 843 (17%) | 2,045 +- 447 (22%) | 3.00 +- 0.00 (0%) |
| CODE-team | 5 | 470 +- 160 (34%) | 12.8 +- 1.1 (9%) | 39.2 +- 3.6 (9%) | 7.1 +- 1.5 (21%) | 11.8 +- 1.7 (14%) | 27.3 +- 4.2 (15%) | 11.8 +- 1.7 (14%) | 27.2 +- 4.2 (16%) | 5,818 +- 1,561 (27%) | 521 +- 84 (16%) | 3.00 +- 0.00 (0%) |
| FQA-team | 5 | 420 +- 49 (12%) | 11.4 +- 1.5 (13%) | 28.2 +- 1.6 (6%) | 4.2 +- 0.4 (11%) | 21.9 +- 5.7 (26%) | 25.0 +- 4.4 (18%) | 21.9 +- 5.7 (26%) | 24.9 +- 4.4 (18%) | 8,092 +- 247 (3%) | 535 +- 57 (11%) | 0.98 +- 0.02 (2%) |
| MATH-team | 5 | 306 +- 57 (19%) | 14.2 +- 2.2 (15%) | 31.0 +- 4.7 (15%) | 3.0 +- 1.0 (35%) | 14.8 +- 5.0 (34%) | 28.1 +- 5.3 (19%) | 13.9 +- 3.7 (27%) | 28.1 +- 5.3 (19%) | 4,967 +- 583 (12%) | 475 +- 67 (14%) | 3.00 +- 0.00 (0%) |

