**Runs** (lead turns = lead activations; rounds = model calls of the agent loop; memory/compaction = auxiliary model calls; score: CODE tests passed, MATH answers correct, FQA keyword coverage)
| run | status | wall (s) | lead turns | lead rounds | teammates | teammate rounds | teammate tasks (closed) | memory calls | compaction calls | model errors (429 etc.) | tool calls (denied) | score |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| CODE-solo-r1 | completed | 106 | 1 | 14 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 15 (0) | 3/3 |
| CODE-solo-r2 | completed | 165 | 1 | 13 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 14 (0) | 3/3 |
| CODE-solo-r3 | completed | 213 | 1 | 20 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 22 (0) | 3/3 |
| FQA-solo-r1 | completed | 133 | 1 | 4 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 6 (0) | coverage 0.967 |
| FQA-solo-r2 | completed | 89 | 1 | 2 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 4 (0) | coverage 0.967 |
| FQA-solo-r3 | completed | 82 | 1 | 5 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 9 (0) | coverage 0.967 |
| MATH-solo-r1 | completed | 115 | 1 | 2 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 1 (1) | 3/3 |
| MATH-solo-r2 | completed | 118 | 1 | 3 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 4 (2) | 3/3 |
| MATH-solo-r3 | completed | 121 | 1 | 2 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 1 (1) | 3/3 |
| CODE-team-r1 | completed | 211 | 5 | 10 | 3 | 24 | 3 (3) | 8 | 0 | 0 | 38 (1) | 3/3 |
| CODE-team-r2 | completed | 206 | 5 | 11 | 3 | 27 | 3 (3) | 5 | 0 | 0 | 40 (4) | 3/3 |
| CODE-team-r3 | completed | 249 | 5 | 11 | 3 | 23 | 3 (3) | 14 | 0 | 0 | 36 (0) | 3/3 |
| CODE-team-r4 | completed | 242 | 5 | 10 | 3 | 18 | 3 (3) | 5 | 0 | 0 | 32 (0) | 3/3 |
| CODE-team-r5 | completed | 168 | 7 | 14 | 3 | 20 | 3 (3) | 7 | 0 | 0 | 37 (0) | 3/3 |
| FQA-team-r1 | completed | 178 | 3 | 9 | 3 | 11 | 3 (3) | 3 | 0 | 0 | 27 (1) | coverage 1.0 |
| FQA-team-r2 | completed | 162 | 4 | 11 | 3 | 13 | 3 (3) | 4 | 0 | 0 | 29 (1) | coverage 1.0 |
| FQA-team-r3 | completed | 214 | 5 | 12 | 3 | 14 | 4 (4) | 5 | 0 | 0 | 31 (1) | coverage 1.0 |
| FQA-team-r4 | completed | 185 | 4 | 10 | 3 | 16 | 3 (3) | 4 | 0 | 0 | 30 (0) | coverage 1.0 |
| FQA-team-r5 | completed | 163 | 3 | 9 | 3 | 10 | 3 (3) | 3 | 0 | 0 | 25 (1) | coverage 0.967 |
| MATH-team-r1 | completed | 174 | 5 | 12 | 3 | 8 | 3 (3) | 5 | 0 | 0 | 19 (2) | 3/3 |
| MATH-team-r2 | completed | 157 | 4 | 11 | 3 | 8 | 3 (3) | 4 | 0 | 0 | 20 (2) | 3/3 |
| MATH-team-r3 | completed | 169 | 5 | 13 | 3 | 10 | 3 (3) | 5 | 0 | 0 | 24 (3) | 3/3 |
| MATH-team-r4 | completed | 176 | 4 | 9 | 3 | 8 | 3 (3) | 4 | 0 | 0 | 19 (1) | 3/3 |
| MATH-team-r5 | completed | 148 | 5 | 10 | 3 | 9 | 3 (3) | 5 | 0 | 0 | 20 (3) | 3/3 |

**Latency breakdown of one agentic round** (time-weighted shares of the pooled round time; means per round in seconds)
| group | kind | rounds | final rounds | gross mean | gross median | gross p90 | prep | model | tools | other | prep share | model share (to first delivered block / streamed tail) | tools share | other share |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo | lead | 47 | 3 | 9.5 | 6.1 | 20.2 | 0.01 | 9.5 | 0.05 | 0.002 | 0.1% | 99.4% (54.3% / 45.2%) | 0.5% | 0.0% |
| FQA-solo | lead | 11 | 3 | 21.0 | 9.0 | 57.6 | 0.01 | 21.0 | 0.02 | 0.008 | 0.0% | 99.8% (28.6% / 71.2%) | 0.1% | 0.0% |
| MATH-solo | lead | 7 | 3 | 41.5 | 42.5 | 69.7 | 0.00 | 41.5 | 0.02 | 0.001 | 0.0% | 99.9% (13.7% / 86.3%) | 0.1% | 0.0% |
| CODE-team | lead | 56 | 27 | 7.7 | 6.9 | 12.6 | 0.54 | 7.2 | 0.01 | 0.001 | 7.0% | 92.9% (71.8% / 21.1%) | 0.1% | 0.0% |
| CODE-team | teammate | 112 | 15 | 10.0 | 5.5 | 15.0 | 0.00 | 10.0 | 0.04 | 0.001 | 0.0% | 99.6% (55.8% / 43.8%) | 0.4% | 0.0% |
| FQA-team | lead | 51 | 19 | 9.7 | 8.3 | 14.2 | 0.01 | 9.7 | 0.01 | 0.002 | 0.1% | 99.8% (62.4% / 37.5%) | 0.1% | 0.0% |
| FQA-team | teammate | 64 | 16 | 8.9 | 6.2 | 17.0 | 0.00 | 8.9 | 0.01 | 0.003 | 0.0% | 99.8% (62.3% / 37.5%) | 0.2% | 0.0% |
| MATH-team | lead | 55 | 23 | 7.6 | 6.6 | 11.7 | 0.01 | 7.6 | 0.01 | 0.001 | 0.1% | 99.8% (78.2% / 21.6%) | 0.1% | 0.0% |
| MATH-team | teammate | 43 | 15 | 17.2 | 12.1 | 34.7 | 0.00 | 17.1 | 0.01 | 0.001 | 0.0% | 99.9% (35.5% / 64.4%) | 0.1% | 0.0% |

**Context preparation, attributed** (lead rounds; seconds per round, pooled by group; `memory recall model` = model calls made by update_context; `post` = turn-end work after a final round: Stop hook + memory extraction + post-turn recall)
| group | rounds | prep mean | compaction pipeline | memory recall (model) | memory recall calls / round | prompt+tool assembly | inbox | unattributed | turns | post per turn | memory extract (model) per turn | memory calls per turn | post amortised per round | (prep + post) share of lead-side time |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo | 47 | 0.01 | 0.002 | 0.00 | 0.00 | 0.000 | 0.000 | 0.004 | 3 | 10.1 | 10.1 | 1.00 | 0.6 | 6.4% |
| FQA-solo | 11 | 0.01 | 0.002 | 0.00 | 0.00 | 0.000 | 0.000 | 0.006 | 3 | 22.2 | 22.2 | 1.00 | 6.1 | 22.4% |
| MATH-solo | 7 | 0.00 | 0.001 | 0.00 | 0.00 | 0.000 | 0.000 | 0.003 | 3 | 19.1 | 19.1 | 1.00 | 8.2 | 16.5% |
| CODE-team | 56 | 0.54 | 0.002 | 0.53 | 0.11 | 0.000 | 0.000 | 0.004 | 27 | 19.1 | 19.1 | 1.22 | 9.2 | 57.6% |
| FQA-team | 51 | 0.01 | 0.002 | 0.00 | 0.00 | 0.000 | 0.000 | 0.004 | 19 | 20.6 | 20.6 | 1.00 | 7.7 | 44.2% |
| MATH-team | 55 | 0.01 | 0.002 | 0.00 | 0.00 | 0.000 | 0.000 | 0.004 | 23 | 13.1 | 13.1 | 1.00 | 5.5 | 42.1% |

**Tokens and model-call timing per call** (agent calls only: purpose lead / teammate; prompt = input + cache_read (+cache_creation); uncached = input_tokens as reported; first block = client time to the first delivered content block (NOT prefill alone: thinking/tool_use blocks arrive in bursts, see the delivery-pattern table); tail = first block to stream end; fit: first-block time = a + b x uncached tokens)
| group | kind | calls | prompt tok mean | prompt tok median | uncached mean | cache-read share | output tok mean | output tok median | thinking share of output chars | model call mean | first block mean | first block median | tail mean | tail tok/s (pooled) | first-block fit a (s) + b (ms/tok), r2 | first block is thinking |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| CODE-solo | lead | 47 | 7,136 | 6,686 | 5,249 | 26.4% | 332 | 116 | 37.8% | 9.5 | 5.2 | 5.2 | 4.3 | 77.3 | 5.45 s + -0.052 ms/tok, r2 0.00 | 46.8% |
| FQA-solo | lead | 11 | 13,801 | 18,183 | 12,073 | 12.5% | 943 | 302 | 49.3% | 21.0 | 6.0 | 6.1 | 15.0 | 63.0 | 6.26 s + -0.020 ms/tok, r2 0.10 | 100.0% |
| MATH-solo | lead | 7 | 4,533 | 5,223 | 2,722 | 39.9% | 2,107 | 2,387 | 68.0% | 41.5 | 5.7 | 6.1 | 35.8 | 58.8 | 5.92 s + -0.087 ms/tok, r2 0.01 | 100.0% |
| CODE-team | lead | 56 | 4,796 | 4,937 | 3,099 | 35.4% | 231 | 143 | 22.1% | 7.2 | 5.5 | 5.9 | 1.6 | 142.2 | 5.38 s + 0.054 ms/tok, r2 0.00 | 71.4% |
| CODE-team | teammate | 112 | 3,717 | 3,407 | 905 | 75.7% | 337 | 68 | 51.0% | 10.0 | 5.6 | 5.4 | 4.4 | 76.6 | 5.66 s + -0.061 ms/tok, r2 0.00 | 57.1% |
| FQA-team | lead | 51 | 5,547 | 5,490 | 3,766 | 32.1% | 343 | 189 | 36.8% | 9.7 | 6.1 | 6.2 | 3.6 | 94.3 | 5.09 s + 0.257 ms/tok, r2 0.08 | 72.5% |
| FQA-team | teammate | 64 | 5,373 | 3,348 | 2,586 | 51.9% | 296 | 95 | 51.1% | 8.9 | 5.5 | 5.7 | 3.3 | 88.5 | 5.41 s + 0.055 ms/tok, r2 0.04 | 75.0% |
| MATH-team | lead | 55 | 5,267 | 5,267 | 3,577 | 32.1% | 215 | 152 | 25.2% | 7.6 | 5.9 | 5.9 | 1.6 | 131.9 | 6.38 s + -0.128 ms/tok, r2 0.02 | 67.3% |
| MATH-team | teammate | 43 | 1,921 | 1,582 | 983 | 48.8% | 760 | 518 | 56.1% | 17.1 | 6.1 | 6.1 | 11.0 | 68.8 | 5.79 s + 0.307 ms/tok, r2 0.04 | 88.4% |
| pooled fit | lead | 227 | | | uncached 629-24,254 | | | | | | | | | 84.4 | first block vs uncached: 5.73 s + -0.007 ms/tok (r2 0.00); vs prompt: 5.81 s + -0.018 ms/tok (r2 0.00); tail vs output tok: -2.15 s + 17.7 ms/tok (r2 0.97) | |
| pooled fit | teammate | 219 | | | uncached 48-16,374 | | | | | | | | | 75.6 | first block vs uncached: 5.64 s + 0.033 ms/tok (r2 0.01); vs prompt: 5.63 s + 0.014 ms/tok (r2 0.00); tail vs output tok: -1.41 s + 16.7 ms/tok (r2 0.98) | |
| pooled fit | all | 446 | | | uncached 48-24,254 | | | | | | | | | 79.6 | first block vs uncached: 5.68 s + 0.006 ms/tok (r2 0.00); vs prompt: 5.70 s + -0.002 ms/tok (r2 0.00); tail vs output tok: -1.76 s + 17.1 ms/tok (r2 0.97) | |

**Prefill vs decode by regression** (agent calls only; model-call duration = a + b x uncached prompt tokens + c x output tokens, least squares; the per-group shares apply the pooled coefficients to each group's own token counts, so they add to ~100% of that group's call time)
| group | kind | calls | mean call (s) | fixed a | prefill b x uncached | decode c x output | residual |
|---|---|---:|---:|---:|---:|---:|---:|
| CODE-solo | lead | 47 | 9.5 | 39.2% | 1.8% | 61.2% | -2.2% |
| FQA-solo | lead | 11 | 21.0 | 17.7% | 1.9% | 78.4% | 2.0% |
| MATH-solo | lead | 7 | 41.5 | 9.0% | 0.2% | 88.5% | 2.3% |
| CODE-team | lead | 56 | 7.2 | 51.8% | 1.4% | 56.2% | -9.4% |
| CODE-team | teammate | 112 | 10.0 | 37.2% | 0.3% | 58.7% | 3.8% |
| FQA-team | lead | 51 | 9.7 | 38.3% | 1.3% | 61.7% | -1.3% |
| FQA-team | teammate | 64 | 8.9 | 41.8% | 1.0% | 58.0% | -0.8% |
| MATH-team | lead | 55 | 7.6 | 49.2% | 1.5% | 49.7% | -0.4% |
| MATH-team | teammate | 43 | 17.1 | 21.7% | 0.2% | 77.3% | 0.9% |

| fit over | calls | a fixed (s) | b prefill (ms per uncached tok) | c decode (ms per output tok) | r2 |
|---|---:|---:|---:|---:|---:|
| all agent calls | 446 | 3.72 | 0.033 | 17.44 | 0.96 |
| lead | 227 | 3.16 | 0.086 | 17.82 | 0.95 |
| teammate | 219 | 4.10 | -0.002 | 17.13 | 0.97 |
| responses ending in tool_use | 322 | 3.76 | -0.004 | 17.61 | 0.96 |
| responses ending in end_turn | 124 | 3.57 | 0.177 | 16.22 | 0.96 |

**Streaming delivery pattern** (why the first-block time is not prefill: the provider delivers thinking and tool_use blocks in bursts; 'first block' = client time to the first delivered content block, 'tail' = first block to end of stream; a tail under 0.1 s means the whole response arrived at once)
| kind | response ends with | calls | output tok mean | first block mean (s) | tail mean (s) | tail share of call | calls with tail < 0.1 s | first-block fit: a + c x output tok (r2) |
|---|---|---:|---:|---:|---:|---:|---:|---|
| lead | tool_use | 149 | 396 | 5.73 | 4.64 | 44.8% | 28.9% | 5.78 s + 0.23 ms/tok (0.01) |
| lead | end_turn | 78 | 308 | 5.66 | 3.75 | 39.9% | 50.0% | 5.43 s + -0.13 ms/tok (0.01) |
| teammate | tool_use | 173 | 396 | 5.60 | 5.43 | 49.2% | 59.0% | 5.37 s + 0.51 ms/tok (0.12) |
| teammate | end_turn | 46 | 451 | 5.99 | 5.24 | 46.7% | 32.6% | 6.21 s + -0.62 ms/tok (0.06) |

**Auxiliary model calls made by the harness itself** (context maintenance: memory recall inside update_context before a lead call, memory extraction after a lead turn, summary compaction; pooled by group)
| group | purpose | calls | calls / run | calls / lead round | mean duration | median | prompt tok mean | output tok mean | stop=max_tokens share | first block mean | tail mean | total per run (s) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo | memory_extract | 3 | 1.0 | 0.06 | 10.1 | 10.2 | 303 | 416 | 0.0% | 3.3 | 6.8 | 10.1 |
| FQA-solo | memory_extract | 3 | 1.0 | 0.27 | 22.2 | 22.3 | 2,119 | 1,000 | 100.0% | 2.9 | 19.3 | 22.2 |
| MATH-solo | memory_extract | 3 | 1.0 | 0.43 | 19.1 | 16.0 | 1,756 | 761 | 0.0% | 3.3 | 15.8 | 19.1 |
| CODE-team | memory_extract | 27 | 5.4 | 0.48 | 18.1 | 20.5 | 774 | 770 | 55.6% | 3.6 | 14.5 | 97.6 |
| CODE-team | memory_recall | 12 | 2.4 | 0.21 | 4.9 | 4.9 | 332 | 190 | 75.0% | 3.2 | 1.7 | 11.7 |
| FQA-team | memory_extract | 19 | 3.8 | 0.37 | 20.6 | 21.1 | 1,458 | 899 | 73.7% | 3.4 | 17.2 | 78.2 |
| MATH-team | memory_extract | 23 | 4.6 | 0.42 | 13.1 | 11.5 | 1,443 | 585 | 34.8% | 3.2 | 9.9 | 60.3 |

**Teammate input redundancy** (a) re-sent = prompt tokens already sent in the same agent's previous call (append-only history), (b) provider cache-read share, (c) cross-teammate file bytes another teammate had fetched first (exact (file,line) provenance) and their share of teammate prompt tokens
| run | teammates | teammate calls | teammate prompt tok | re-sent share (a) | cache-read share (b) | file bytes fetched | cross-teammate redundancy, range (c) | same, whole-result SHA | resident copies | cross-redundant share of prompt tok | file content share of prompt tok | lead: prompt tok | lead re-sent share | lead cache-read |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo-r1 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 76,596 | 90.3% | 33.1% |
| CODE-solo-r2 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 90,509 | 89.0% | 25.7% |
| CODE-solo-r3 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 168,279 | 92.3% | 23.8% |
| FQA-solo-r1 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 50,249 | 49.2% | 12.6% |
| FQA-solo-r2 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 27,076 | 10.4% | 7.8% |
| FQA-solo-r3 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 74,484 | 66.1% | 14.2% |
| MATH-solo-r1 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 7,964 | 34.4% | 26.5% |
| MATH-solo-r2 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 14,234 | 58.8% | 44.5% |
| MATH-solo-r3 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 9,531 | 28.8% | 44.3% |
| CODE-team-r1 | 3 | 24 | 90,618 | 83.4% | 78.9% | 14,359 | 0.0% | 0.0% | 1.00x | 0.0% | 24.6% | 46,844 | 87.3% | 31.6% |
| CODE-team-r2 | 3 | 27 | 104,279 | 85.6% | 78.5% | 13,871 | 0.0% | 0.0% | 1.00x | 0.0% | 24.7% | 50,554 | 88.4% | 41.8% |
| CODE-team-r3 | 3 | 23 | 74,775 | 81.1% | 73.8% | 13,848 | 0.0% | 0.0% | 1.00x | 0.0% | 30.3% | 53,038 | 88.2% | 27.9% |
| CODE-team-r4 | 3 | 18 | 65,743 | 76.5% | 68.4% | 10,802 | 0.0% | 0.0% | 1.00x | 0.0% | 21.3% | 47,660 | 87.3% | 35.5% |
| CODE-team-r5 | 3 | 20 | 80,869 | 80.4% | 76.0% | 13,848 | 0.0% | 0.0% | 1.00x | 0.0% | 25.1% | 70,500 | 90.8% | 38.9% |
| FQA-team-r1 | 3 | 11 | 72,432 | 52.9% | 52.6% | 112,843 | 22.8% | 22.8% | 1.30x | 18.5% | 81.3% | 53,267 | 84.4% | 19.8% |
| FQA-team-r2 | 3 | 13 | 76,130 | 58.4% | 58.2% | 112,843 | 22.8% | 22.8% | 1.30x | 26.8% | 86.3% | 60,136 | 87.7% | 31.6% |
| FQA-team-r3 | 3 | 14 | 78,579 | 56.2% | 55.9% | 112,843 | 22.8% | 22.8% | 1.30x | 25.0% | 76.1% | 65,997 | 87.2% | 38.4% |
| FQA-team-r4 | 3 | 16 | 73,394 | 57.6% | 54.8% | 103,310 | 15.6% | 12.5% | 1.18x | 11.8% | 77.8% | 50,956 | 84.8% | 37.3% |
| FQA-team-r5 | 3 | 10 | 43,356 | 32.9% | 27.5% | 103,492 | 12.4% | 12.4% | 1.15x | 8.1% | 83.4% | 52,545 | 84.1% | 32.2% |
| MATH-team-r1 | 3 | 8 | 15,589 | 49.2% | 48.4% | 0 | - | - | - | 0.0% | 0.0% | 58,361 | 89.1% | 29.0% |
| MATH-team-r2 | 3 | 8 | 15,298 | 49.3% | 48.1% | 0 | - | - | - | 0.0% | 0.0% | 58,149 | 87.0% | 36.3% |
| MATH-team-r3 | 3 | 10 | 24,021 | 62.6% | 50.4% | 0 | - | - | - | 0.0% | 0.0% | 72,727 | 88.5% | 34.8% |
| MATH-team-r4 | 3 | 8 | 13,788 | 44.9% | 44.1% | 0 | - | - | - | 0.0% | 0.0% | 48,731 | 84.6% | 30.3% |
| MATH-team-r5 | 3 | 9 | 13,891 | 53.4% | 52.1% | 0 | - | - | - | 0.0% | 0.0% | 51,718 | 86.3% | 28.6% |

**Agent runs per task** (mean over runs of a group unless noted; a teammate task = rounds from assignment to its final reply; wall = driver wall time)
| group | runs | wall mean (s) | lead turns / run | lead rounds / run | lead rounds / turn | teammates / run | teammate rounds / task: mean | median | max | teammate task active time (s): mean | median | teammate tasks / run | tool calls / round (lead) | tool calls / round (teammate) | model calls / run (all) | denied tool calls / run |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo | 3 | 161 | 1.0 | 15.7 | 15.7 | 0.0 | - | - | 0 | - | - | 0.0 | 1.09 | 0.00 | 16.7 | 0.0 |
| FQA-solo | 3 | 101 | 1.0 | 3.7 | 3.7 | 0.0 | - | - | 0 | - | - | 0.0 | 1.73 | 0.00 | 4.7 | 0.0 |
| MATH-solo | 3 | 118 | 1.0 | 2.3 | 2.3 | 0.0 | - | - | 0 | - | - | 0.0 | 0.86 | 0.00 | 3.3 | 1.3 |
| CODE-team | 5 | 215 | 5.4 | 11.2 | 2.1 | 3.0 | 7.5 | 6 | 15 | 75 | 70 | 3.0 | 1.20 | 1.04 | 41.4 | 1.0 |
| FQA-team | 5 | 180 | 3.8 | 10.2 | 2.7 | 3.0 | 4.0 | 4 | 6 | 36 | 35 | 3.2 | 1.37 | 1.12 | 26.8 | 0.8 |
| MATH-team | 5 | 165 | 4.6 | 11.0 | 2.4 | 3.0 | 2.9 | 3 | 5 | 49 | 33 | 3.0 | 1.24 | 0.79 | 24.2 | 2.2 |

**Where the wall time of a run goes** (means over the runs of a group; lead active = lead turns incl. turn-end memory work; teammate active = union of teammate round windows; overlap = lead and teammates busy at the same time; idle tail = nobody busy: waits for team events, quiescence timer, shutdown handshake)
| group | runs | wall (s) | lead active (s) | share | of which lead agent calls (s) | of which turn-end memory (s) | teammate active (s) | share | overlap (s) | idle / tail (s) | share |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo | 3 | 161 | 159 | 98.7% | 148 | 10 | 0 | 0.0% | 0 | 2 | 1.3% |
| FQA-solo | 3 | 101 | 99 | 98.0% | 77 | 22 | 0 | 0.0% | 0 | 2 | 2.0% |
| MATH-solo | 3 | 118 | 116 | 98.3% | 97 | 19 | 0 | 0.0% | 0 | 2 | 1.7% |
| CODE-team | 5 | 215 | 190 | 88.2% | 80 | 103 | 111 | 51.7% | 89 | 3 | 1.3% |
| FQA-team | 5 | 180 | 177 | 98.3% | 99 | 78 | 48 | 26.4% | 47 | 3 | 1.4% |
| MATH-team | 5 | 165 | 144 | 87.0% | 83 | 60 | 83 | 50.2% | 64 | 3 | 1.8% |

**Tool execution time by tool** (tool_start -> tool_end spans, pooled over all runs)
| tool | calls | denied | error | mean (ms) | median (ms) | p90 (ms) | max (ms) | total (s) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| bash | 81 | 23 | 9 | 79 | 66 | 187 | 220 | 6.4 |
| read_file | 78 | 0 | 0 | 13 | 9 | 24 | 47 | 1.0 |
| spawn_teammate | 45 | 0 | 0 | 21 | 24 | 40 | 61 | 0.9 |
| request_shutdown | 45 | 0 | 0 | 5 | 3 | 11 | 15 | 0.2 |
| glob | 21 | 0 | 0 | 9 | 10 | 12 | 19 | 0.2 |
| complete_task | 33 | 0 | 0 | 5 | 5 | 5 | 5 | 0.2 |
| write_file | 24 | 0 | 0 | 7 | 5 | 6 | 30 | 0.2 |
| create_task | 45 | 0 | 0 | 2 | 2 | 3 | 5 | 0.1 |
| todo_write | 82 | 0 | 0 | 1 | 1 | 1 | 2 | 0.1 |
| edit_file | 16 | 1 | 1 | 4 | 4 | 5 | 5 | 0.1 |
| claim_task | 22 | 0 | 0 | 2 | 2 | 2 | 2 | 0.0 |
| send_message | 6 | 0 | 0 | 4 | 3 | 7 | 8 | 0.0 |
| list_tasks | 5 | 0 | 0 | 3 | 3 | 3 | 3 | 0.0 |

**Per-run round breakdown** (lead and teammate rounds separately; seconds)
| run | kind | rounds | gross mean | prep mean | model mean | first block mean | tail mean | tools mean | other mean | prep share | model share | tools share | prompt tok mean | output tok mean |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo-r1 | lead | 14 | 6.7 | 0.01 | 6.7 | 4.9 | 1.8 | 0.04 | 0.002 | 0.1% | 99.3% | 0.7% | 5,471 | 210 |
| CODE-solo-r2 | lead | 13 | 11.7 | 0.01 | 11.7 | 5.0 | 6.7 | 0.05 | 0.002 | 0.1% | 99.5% | 0.4% | 6,962 | 437 |
| CODE-solo-r3 | lead | 20 | 10.0 | 0.01 | 10.0 | 5.5 | 4.5 | 0.05 | 0.002 | 0.1% | 99.5% | 0.4% | 8,414 | 350 |
| FQA-solo-r1 | lead | 4 | 26.9 | 0.01 | 26.8 | 6.5 | 20.4 | 0.03 | 0.008 | 0.0% | 99.8% | 0.1% | 12,562 | 1,255 |
| FQA-solo-r2 | lead | 2 | 32.3 | 0.00 | 32.2 | 5.5 | 26.8 | 0.02 | 0.009 | 0.0% | 99.9% | 0.1% | 13,538 | 1,421 |
| FQA-solo-r3 | lead | 5 | 11.9 | 0.01 | 11.8 | 5.9 | 5.9 | 0.02 | 0.007 | 0.1% | 99.7% | 0.2% | 14,897 | 503 |
| MATH-solo-r1 | lead | 2 | 43.5 | 0.00 | 43.5 | 5.0 | 38.6 | 0.00 | 0.001 | 0.0% | 100.0% | 0.0% | 3,982 | 2,422 |
| MATH-solo-r2 | lead | 3 | 33.6 | 0.01 | 33.5 | 6.3 | 27.3 | 0.05 | 0.002 | 0.0% | 99.8% | 0.2% | 4,745 | 1,484 |
| MATH-solo-r3 | lead | 2 | 51.4 | 0.00 | 51.4 | 5.5 | 45.9 | 0.00 | 0.001 | 0.0% | 100.0% | 0.0% | 4,766 | 2,725 |
| CODE-team-r1 | lead | 10 | 8.6 | 0.49 | 8.1 | 6.2 | 1.9 | 0.01 | 0.001 | 5.7% | 94.2% | 0.1% | 4,684 | 248 |
| CODE-team-r1 | teammate | 24 | 9.5 | 0.00 | 9.5 | 5.4 | 4.0 | 0.03 | 0.002 | 0.0% | 99.6% | 0.3% | 3,776 | 324 |
| CODE-team-r2 | lead | 11 | 7.7 | 0.01 | 7.7 | 6.2 | 1.4 | 0.01 | 0.001 | 0.1% | 99.8% | 0.1% | 4,596 | 223 |
| CODE-team-r2 | teammate | 27 | 8.3 | 0.00 | 8.2 | 5.7 | 2.6 | 0.03 | 0.002 | 0.0% | 99.5% | 0.4% | 3,862 | 237 |
| CODE-team-r3 | lead | 11 | 9.4 | 2.29 | 7.1 | 5.5 | 1.7 | 0.01 | 0.001 | 24.3% | 75.6% | 0.1% | 4,822 | 246 |
| CODE-team-r3 | teammate | 23 | 9.7 | 0.00 | 9.6 | 5.9 | 3.7 | 0.04 | 0.001 | 0.0% | 99.5% | 0.4% | 3,251 | 280 |
| CODE-team-r4 | lead | 10 | 7.9 | 0.01 | 7.9 | 5.9 | 2.1 | 0.01 | 0.001 | 0.1% | 99.8% | 0.1% | 4,766 | 264 |
| CODE-team-r4 | teammate | 18 | 13.2 | 0.00 | 13.2 | 5.5 | 7.7 | 0.03 | 0.001 | 0.0% | 99.7% | 0.3% | 3,652 | 512 |
| CODE-team-r5 | lead | 14 | 5.6 | 0.01 | 5.6 | 4.4 | 1.2 | 0.01 | 0.001 | 0.1% | 99.7% | 0.2% | 5,036 | 192 |
| CODE-team-r5 | teammate | 20 | 10.6 | 0.00 | 10.6 | 5.5 | 5.1 | 0.04 | 0.001 | 0.0% | 99.6% | 0.4% | 4,043 | 394 |
| FQA-team-r1 | lead | 9 | 11.1 | 0.01 | 11.1 | 6.9 | 4.2 | 0.01 | 0.002 | 0.1% | 99.8% | 0.1% | 5,919 | 425 |
| FQA-team-r1 | teammate | 11 | 9.9 | 0.00 | 9.9 | 5.8 | 4.1 | 0.01 | 0.004 | 0.0% | 99.8% | 0.1% | 6,585 | 377 |
| FQA-team-r2 | lead | 11 | 8.0 | 0.01 | 7.9 | 5.6 | 2.3 | 0.01 | 0.002 | 0.1% | 99.8% | 0.1% | 5,467 | 259 |
| FQA-team-r2 | teammate | 13 | 7.9 | 0.00 | 7.8 | 5.5 | 2.3 | 0.02 | 0.003 | 0.1% | 99.7% | 0.2% | 5,856 | 224 |
| FQA-team-r3 | lead | 12 | 9.5 | 0.01 | 9.5 | 6.3 | 3.2 | 0.01 | 0.001 | 0.1% | 99.8% | 0.1% | 5,500 | 307 |
| FQA-team-r3 | teammate | 14 | 9.5 | 0.00 | 9.5 | 5.9 | 3.6 | 0.01 | 0.003 | 0.0% | 99.8% | 0.2% | 5,613 | 323 |
| FQA-team-r4 | lead | 10 | 9.5 | 0.01 | 9.5 | 5.8 | 3.7 | 0.01 | 0.001 | 0.1% | 99.8% | 0.1% | 5,096 | 331 |
| FQA-team-r4 | teammate | 16 | 8.5 | 0.00 | 8.5 | 5.3 | 3.3 | 0.01 | 0.003 | 0.1% | 99.8% | 0.1% | 4,587 | 290 |
| FQA-team-r5 | lead | 9 | 11.0 | 0.01 | 10.9 | 5.8 | 5.2 | 0.01 | 0.002 | 0.1% | 99.8% | 0.1% | 5,838 | 427 |
| FQA-team-r5 | teammate | 10 | 8.9 | 0.00 | 8.9 | 5.4 | 3.5 | 0.02 | 0.004 | 0.0% | 99.7% | 0.2% | 4,336 | 269 |
| MATH-team-r1 | lead | 12 | 7.1 | 0.01 | 7.1 | 6.0 | 1.1 | 0.01 | 0.001 | 0.1% | 99.8% | 0.1% | 4,863 | 172 |
| MATH-team-r1 | teammate | 8 | 18.9 | 0.00 | 18.9 | 6.7 | 12.2 | 0.02 | 0.001 | 0.0% | 99.9% | 0.1% | 1,949 | 749 |
| MATH-team-r2 | lead | 11 | 7.8 | 0.01 | 7.8 | 6.0 | 1.8 | 0.01 | 0.001 | 0.1% | 99.8% | 0.1% | 5,286 | 234 |
| MATH-team-r2 | teammate | 8 | 18.7 | 0.00 | 18.7 | 6.1 | 12.5 | 0.01 | 0.001 | 0.0% | 99.9% | 0.1% | 1,912 | 837 |
| MATH-team-r3 | lead | 13 | 7.1 | 0.01 | 7.1 | 5.8 | 1.3 | 0.01 | 0.001 | 0.1% | 99.8% | 0.1% | 5,594 | 193 |
| MATH-team-r3 | teammate | 10 | 15.7 | 0.00 | 15.7 | 5.9 | 9.8 | 0.01 | 0.001 | 0.0% | 99.9% | 0.1% | 2,402 | 744 |
| MATH-team-r4 | lead | 9 | 7.7 | 0.01 | 7.6 | 5.2 | 2.5 | 0.01 | 0.001 | 0.1% | 99.8% | 0.1% | 5,415 | 268 |
| MATH-team-r4 | teammate | 8 | 17.4 | 0.00 | 17.3 | 5.6 | 11.8 | 0.02 | 0.001 | 0.0% | 99.9% | 0.1% | 1,724 | 866 |
| MATH-team-r5 | lead | 10 | 8.4 | 0.01 | 8.4 | 6.5 | 1.9 | 0.01 | 0.001 | 0.1% | 99.8% | 0.1% | 5,172 | 228 |
| MATH-team-r5 | teammate | 9 | 15.6 | 0.00 | 15.6 | 6.2 | 9.4 | 0.00 | 0.001 | 0.0% | 100.0% | 0.0% | 1,543 | 623 |

