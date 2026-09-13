
## KV-base-50k-r1.qwen1.5b.replay.jsonl  (Qwen/Qwen2.5-1.5B-Instruct)
steps=48 edit_steps=28 placeholder_steps=25 evicted_items=55 evicted_tokens=74752 ctx_median=12020.0 ctx_max=14758 edit_kinds={'placeholder': 50, 'snip': 6, 'summary': 1}
real trajectory: {'tool_calls': 95, 'steps_reacquiring_evicted': 33, 'tools': {'glob': 2, 'bash': 38, 'read_file': 46, 'todo_write': 9}}

### compute (tokens the model ran)
| policy | total tokens computed | on edit steps | share of recompute |
|---|---:|---:|---:|
| recompute | 218,803 | 181,005 | 100.0% |
| shift | 99,318 | 61,520 | 45.4% |
| gap | 99,318 | 61,520 | 45.4% |
| shift1 | 61,520 | 61,520 | 28.1% |
| oracle | 50,876 | 50,876 | 23.3% |
tail tokens (unavoidable new input) = 88,587; tokens after first change on edit steps: median 6417.0, sum 181,005

### fidelity on edit steps (teacher-forced over the real response)
| policy | n | KL from recompute (mean / median / p90 / max) | top-1 agree w/ recompute | KL from oracle | top-1 agree w/ oracle | NLL of real response | first-token KL from recompute (median) | first-token top1 = recompute |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| recompute | 28 | 0.000 / 0.000 / 0.000 / 0.000 | 1.000 | 0.030 | 0.974 | 0.479 | 0.000 | 1.00 |
| shift | 28 | 0.056 / 0.036 / 0.111 / 0.290 | 0.960 | 0.052 | 0.958 | 0.474 | 0.178 | 0.75 |
| gap | 28 | 0.072 / 0.048 / 0.162 / 0.318 | 0.952 | 0.062 | 0.959 | 0.476 | 0.213 | 0.75 |
| shift1 | 28 | 0.018 / 0.013 / 0.036 / 0.068 | 0.976 | 0.018 | 0.980 | 0.471 | 0.053 | 0.93 |
| oracle | 28 | 0.028 / 0.015 / 0.071 / 0.097 | 0.974 | 0.000 | 1.000 | 0.474 | 0.078 | 0.89 |

### fidelity on ALL steps
| policy | n | KL from recompute (mean) | NLL of real response |
|---|---:|---:|---:|
| recompute | 48 | 0.000 | 0.383 |
| shift | 48 | 0.042 | 0.378 |
| gap | 48 | 0.053 | 0.381 |
| shift1 | 28 | 0.018 | 0.471 |
| oracle | 28 | 0.028 | 0.474 |

### behaviour: greedy next action on edit steps
| policy | n | tool-call rate | re-fetches content evicted this step | re-fetches any evicted content | same tool as recompute | identical text to recompute | tools |
|---|---:|---:|---:|---:|---:|---:|---|
| recompute | 28 | 0.79 | 0.04 | 0.32 | 1.00 | 1.00 | {'read_file': 5, 'todo_write': 8, 'bash': 7, 'glob': 1, 'task': 1} |
| shift | 28 | 0.68 | 0.04 | 0.32 | 0.61 | 0.36 | {'read_file': 8, 'todo_write': 5, 'bash': 5, 'glob': 1} |
| gap | 28 | 0.71 | 0.04 | 0.29 | 0.71 | 0.36 | {'read_file': 6, 'todo_write': 7, 'bash': 6, 'glob': 1} |
| shift1 | 28 | 0.79 | 0.04 | 0.43 | 0.82 | 0.39 | {'read_file': 7, 'todo_write': 7, 'bash': 7, 'glob': 1} |
| oracle | 28 | 0.82 | 0.04 | 0.39 | 0.82 | 0.46 | {'read_file': 5, 'todo_write': 8, 'bash': 9, 'glob': 1} |

### probes about the evicted content (asked as the next user turn; NLL per token of the true answer)
| metric | recompute | shift | gap | oracle | n |
|---|---:|---:|---:|---:|---:|
| headings:nll | 3.591 | 3.687 | 3.702 | 0.605 | 23 |
| headings:nll_minus_recompute | 0.000 | 0.096 | 0.111 | -2.986 | 23 |
| headings:nll_minus_oracle | 2.986 | 3.082 | 3.097 | 0.000 | 23 |
| headings:argmax_agree | 0.404 | 0.398 | 0.398 | 0.895 | 23 |
| cloze:nll | 4.438 | 4.468 | 4.500 | 0.501 | 23 |
| cloze:nll_minus_recompute | 0.000 | 0.030 | 0.063 | -3.937 | 23 |
| cloze:nll_minus_oracle | 3.937 | 3.967 | 3.999 | 0.000 | 23 |
| cloze:argmax_agree | 0.287 | 0.305 | 0.307 | 0.873 | 23 |
| cloze:greedy_lcs | 0.149 | 0.153 | 0.173 | 0.631 | 23 |
| presence_yes:nll | 0.489 | 0.338 | 0.266 | 0.293 | 23 |
| presence_yes:nll_minus_recompute | 0.000 | -0.151 | -0.223 | -0.196 | 23 |
| presence_yes:nll_minus_oracle | 0.196 | 0.045 | -0.027 | 0.000 | 23 |
| presence_yes:argmax_agree | 0.783 | 0.870 | 1.000 | 1.000 | 23 |
| presence_yes:forced_choice_correct | 0.783 | 0.870 | 1.000 | 1.000 | 23 |
| presence_yes:margin_yes | 0.766 | 1.279 | 1.503 | 1.620 | 23 |
| presence_no:nll | 0.983 | 1.227 | 1.353 | 1.112 | 23 |
| presence_no:nll_minus_recompute | 0.000 | 0.244 | 0.370 | 0.129 | 23 |
| presence_no:nll_minus_oracle | -0.129 | 0.115 | 0.241 | 0.000 | 23 |
| presence_no:argmax_agree | 0.435 | 0.174 | 0.130 | 0.304 | 23 |
| presence_no:forced_choice_correct | 0.435 | 0.174 | 0.130 | 0.304 | 23 |
| presence_no:margin_yes | 0.315 | 0.740 | 0.924 | 0.485 | 23 |
| copy:nll | 3.681 | 3.683 | 3.718 | 0.422 | 23 |
| copy:nll_minus_recompute | 0.000 | 0.002 | 0.037 | -3.259 | 23 |
| copy:nll_minus_oracle | 3.259 | 3.261 | 3.296 | 0.000 | 23 |
| copy:argmax_agree | 0.396 | 0.403 | 0.403 | 0.941 | 23 |

### compounding: KL from recompute by number of edit steps already spliced in the run (mean / median)
| edits so far | n | shift | gap | shift1 | oracle |
|---|---:|---|---|---|---|
| 1-3 | 3 | 0.060 / 0.068 | 0.057 / 0.056 | 0.034 / 0.025 | 0.052 / 0.047 |
| 4-10 | 7 | 0.033 / 0.015 | 0.055 / 0.044 | 0.017 / 0.005 | 0.019 / 0.013 |
| 11-20 | 10 | 0.029 / 0.020 | 0.035 / 0.027 | 0.015 / 0.013 | 0.027 / 0.018 |
| 21+ | 8 | 0.110 / 0.091 | 0.139 / 0.135 | 0.016 / 0.015 | 0.026 / 0.019 |

### edit steps split by whether the real (glm) response re-fetched evicted content
| subset | n | recompute: real-response NLL / KL from recompute | shift: real-response NLL / KL from recompute | gap: real-response NLL / KL from recompute | shift1: real-response NLL / KL from recompute | oracle: real-response NLL / KL from recompute |
|---|---:|---|---|---|---|---|
| real_refetch | 20 | 0.450 / 0.000 | 0.441 / 0.065 | 0.445 / 0.083 | 0.444 / 0.018 | 0.441 / 0.029 |
| real_no_refetch | 8 | 0.549 / 0.000 | 0.555 / 0.035 | 0.555 / 0.044 | 0.539 / 0.018 | 0.557 / 0.024 |

### stale vs recomputed KV on reused blocks after the edit
| policy | key cos | value cos | frac tokens key cos < 0.9 |
|---|---:|---:|---:|
| shift | 0.987 | 0.964 | 0.018 |
| shift1 | 0.992 | 0.976 | 0.009 |
| gap | 0.984 | 0.958 | 0.022 |
shift key cos by layer: [1.0, 1.0, 1.0, 0.999, 0.998, 0.997, 0.998, 0.998, 0.996, 0.995, 0.99, 0.988, 0.986, 0.986, 0.98, 0.985, 0.963, 0.972, 0.981, 0.98, 0.969, 0.975, 0.981, 0.979, 0.985, 0.987, 0.983, 0.977]
gap-mode max RoPE position reached: 51171; replay wall time 3384.2 s

## KV-base-50k-r2.qwen1.5b.replay.jsonl  (Qwen/Qwen2.5-1.5B-Instruct)
steps=36 edit_steps=17 placeholder_steps=14 evicted_items=43 evicted_tokens=44854 ctx_median=12383.0 ctx_max=15179 edit_kinds={'placeholder': 29, 'summary': 1, 'snip': 6}
real trajectory: {'tool_calls': 75, 'steps_reacquiring_evicted': 26, 'tools': {'glob': 2, 'bash': 5, 'read_file': 61, 'todo_write': 7}}

### compute (tokens the model ran)
| policy | total tokens computed | on edit steps | share of recompute |
|---|---:|---:|---:|
| recompute | 146,131 | 121,536 | 100.0% |
| shift | 60,283 | 35,688 | 41.3% |
| gap | 60,283 | 35,688 | 41.3% |
| shift1 | 35,688 | 35,688 | 24.4% |
| oracle | 28,522 | 28,522 | 19.5% |
tail tokens (unavoidable new input) = 52,005; tokens after first change on edit steps: median 6912, sum 121,536

### fidelity on edit steps (teacher-forced over the real response)
| policy | n | KL from recompute (mean / median / p90 / max) | top-1 agree w/ recompute | KL from oracle | top-1 agree w/ oracle | NLL of real response | first-token KL from recompute (median) | first-token top1 = recompute |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| recompute | 17 | 0.000 / 0.000 / 0.000 / 0.000 | 1.000 | 0.289 | 0.953 | 0.380 | 0.000 | 1.00 |
| shift | 17 | 0.029 / 0.024 / 0.047 / 0.060 | 0.978 | 0.305 | 0.950 | 0.380 | 0.409 | 0.88 |
| gap | 17 | 0.034 / 0.035 / 0.055 / 0.099 | 0.973 | 0.315 | 0.947 | 0.385 | 0.344 | 0.82 |
| shift1 | 17 | 0.011 / 0.012 / 0.016 / 0.043 | 0.987 | 0.281 | 0.954 | 0.379 | 0.034 | 0.77 |
| oracle | 17 | 0.093 / 0.018 / 0.104 / 0.750 | 0.956 | 0.000 | 1.000 | 0.437 | 0.082 | 0.82 |

### fidelity on ALL steps
| policy | n | KL from recompute (mean) | NLL of real response |
|---|---:|---:|---:|
| recompute | 36 | 0.000 | 0.339 |
| shift | 36 | 0.016 | 0.340 |
| gap | 36 | 0.018 | 0.344 |
| shift1 | 17 | 0.011 | 0.379 |
| oracle | 17 | 0.093 | 0.437 |

### behaviour: greedy next action on edit steps
| policy | n | tool-call rate | re-fetches content evicted this step | re-fetches any evicted content | same tool as recompute | identical text to recompute | tools |
|---|---:|---:|---:|---:|---:|---:|---|
| recompute | 17 | 0.47 | 0.06 | 0.18 | 1.00 | 1.00 | {'todo_write': 3, 'read_file': 5} |
| shift | 17 | 0.59 | 0.06 | 0.18 | 0.82 | 0.59 | {'todo_write': 3, 'bash': 1, 'read_file': 6} |
| gap | 17 | 0.53 | 0.06 | 0.18 | 0.77 | 0.53 | {'todo_write': 3, 'bash': 1, 'read_file': 5} |
| shift1 | 17 | 0.65 | 0.06 | 0.18 | 0.77 | 0.53 | {'todo_write': 3, 'bash': 1, 'read_file': 7} |
| oracle | 17 | 0.53 | 0.06 | 0.18 | 0.82 | 0.59 | {'todo_write': 3, 'read_file': 6} |

### probes about the evicted content (asked as the next user turn; NLL per token of the true answer)
| metric | recompute | shift | gap | oracle | n |
|---|---:|---:|---:|---:|---:|
| headings:nll | 3.977 | 4.026 | 4.017 | 1.027 | 15 |
| headings:nll_minus_recompute | 0.000 | 0.050 | 0.041 | -2.950 | 15 |
| headings:nll_minus_oracle | 2.950 | 3.000 | 2.990 | 0.000 | 15 |
| headings:argmax_agree | 0.375 | 0.376 | 0.368 | 0.832 | 15 |
| cloze:nll | 4.169 | 4.241 | 4.248 | 0.857 | 15 |
| cloze:nll_minus_recompute | 0.000 | 0.072 | 0.079 | -3.312 | 15 |
| cloze:nll_minus_oracle | 3.312 | 3.384 | 3.391 | 0.000 | 15 |
| cloze:argmax_agree | 0.367 | 0.382 | 0.374 | 0.849 | 15 |
| cloze:greedy_lcs | 0.139 | 0.132 | 0.159 | 0.587 | 15 |
| presence_yes:nll | 0.486 | 0.406 | 0.385 | 0.422 | 15 |
| presence_yes:nll_minus_recompute | 0.000 | -0.080 | -0.102 | -0.065 | 15 |
| presence_yes:nll_minus_oracle | 0.065 | -0.016 | -0.037 | 0.000 | 15 |
| presence_yes:argmax_agree | 0.867 | 0.867 | 0.867 | 0.933 | 15 |
| presence_yes:forced_choice_correct | 0.867 | 0.867 | 0.867 | 0.933 | 15 |
| presence_yes:margin_yes | 1.067 | 1.308 | 1.385 | 1.369 | 15 |
| presence_no:nll | 1.232 | 1.423 | 1.452 | 1.149 | 15 |
| presence_no:nll_minus_recompute | 0.000 | 0.191 | 0.221 | -0.083 | 15 |
| presence_no:nll_minus_oracle | 0.083 | 0.274 | 0.304 | 0.000 | 15 |
| presence_no:argmax_agree | 0.400 | 0.200 | 0.200 | 0.333 | 15 |
| presence_no:forced_choice_correct | 0.400 | 0.200 | 0.200 | 0.333 | 15 |
| presence_no:margin_yes | 0.662 | 0.953 | 0.988 | 0.543 | 15 |
| copy:nll | 3.229 | 3.270 | 3.278 | 0.551 | 14 |
| copy:nll_minus_recompute | 0.000 | 0.042 | 0.050 | -2.677 | 14 |
| copy:nll_minus_oracle | 2.677 | 2.719 | 2.727 | 0.000 | 14 |
| copy:argmax_agree | 0.441 | 0.438 | 0.441 | 0.911 | 14 |

### compounding: KL from recompute by number of edit steps already spliced in the run (mean / median)
| edits so far | n | shift | gap | shift1 | oracle |
|---|---:|---|---|---|---|
| 1-3 | 3 | 0.041 / 0.041 | 0.045 / 0.047 | 0.023 / 0.014 | 0.420 / 0.488 |
| 4-10 | 7 | 0.014 / 0.014 | 0.015 / 0.014 | 0.009 / 0.011 | 0.013 / 0.017 |
| 11-20 | 7 | 0.038 / 0.037 | 0.048 / 0.041 | 0.009 / 0.010 | 0.031 / 0.013 |

### edit steps split by whether the real (glm) response re-fetched evicted content
| subset | n | recompute: real-response NLL / KL from recompute | shift: real-response NLL / KL from recompute | gap: real-response NLL / KL from recompute | shift1: real-response NLL / KL from recompute | oracle: real-response NLL / KL from recompute |
|---|---:|---|---|---|---|---|
| real_refetch | 14 | 0.182 / 0.000 | 0.177 / 0.026 | 0.181 / 0.026 | 0.176 / 0.010 | 0.208 / 0.046 |
| real_no_refetch | 3 | 1.302 / 0.000 | 1.327 / 0.042 | 1.340 / 0.072 | 1.325 / 0.021 | 1.506 / 0.309 |

### stale vs recomputed KV on reused blocks after the edit
| policy | key cos | value cos | frac tokens key cos < 0.9 |
|---|---:|---:|---:|
| shift | 0.987 | 0.962 | 0.013 |
| shift1 | 0.993 | 0.978 | 0.006 |
| gap | 0.986 | 0.959 | 0.014 |
shift key cos by layer: [1.0, 1.0, 0.999, 0.998, 0.998, 0.997, 0.997, 0.998, 0.995, 0.994, 0.99, 0.987, 0.985, 0.985, 0.979, 0.985, 0.964, 0.973, 0.981, 0.98, 0.97, 0.976, 0.982, 0.981, 0.987, 0.988, 0.985, 0.98]
gap-mode max RoPE position reached: 35739; replay wall time 3967.3 s

## KV-base-50k-r3.qwen1.5b.replay.jsonl  (Qwen/Qwen2.5-1.5B-Instruct)
steps=33 edit_steps=24 placeholder_steps=17 evicted_items=37 evicted_tokens=54151 ctx_median=12326 ctx_max=14376 edit_kinds={'placeholder': 36, 'snip': 12}
real trajectory: {'tool_calls': 51, 'steps_reacquiring_evicted': 16, 'tools': {'glob': 1, 'bash': 12, 'read_file': 32, 'todo_write': 6}}

### compute (tokens the model ran)
| policy | total tokens computed | on edit steps | share of recompute |
|---|---:|---:|---:|
| recompute | 210,736 | 196,124 | 100.0% |
| shift | 65,964 | 51,352 | 31.3% |
| gap | 65,964 | 51,352 | 31.3% |
| shift1 | 51,352 | 51,352 | 24.4% |
| oracle | 44,096 | 44,096 | 20.9% |
tail tokens (unavoidable new input) = 58,708; tokens after first change on edit steps: median 8310.0, sum 196,124

### fidelity on edit steps (teacher-forced over the real response)
| policy | n | KL from recompute (mean / median / p90 / max) | top-1 agree w/ recompute | KL from oracle | top-1 agree w/ oracle | NLL of real response | first-token KL from recompute (median) | first-token top1 = recompute |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| recompute | 24 | 0.000 / 0.000 / 0.000 / 0.000 | 1.000 | 0.032 | 0.974 | 0.506 | 0.000 | 1.00 |
| shift | 24 | 0.043 / 0.033 / 0.079 / 0.186 | 0.969 | 0.053 | 0.969 | 0.533 | 0.099 | 1.00 |
| gap | 24 | 0.147 / 0.060 / 0.246 / 1.015 | 0.946 | 0.152 | 0.945 | 0.633 | 0.149 | 0.96 |
| shift1 | 24 | 0.012 / 0.004 / 0.032 / 0.046 | 0.982 | 0.026 | 0.980 | 0.501 | 0.004 | 1.00 |
| oracle | 24 | 0.033 / 0.010 / 0.052 / 0.186 | 0.974 | 0.000 | 1.000 | 0.505 | 0.009 | 1.00 |

### fidelity on ALL steps
| policy | n | KL from recompute (mean) | NLL of real response |
|---|---:|---:|---:|
| recompute | 33 | 0.000 | 0.445 |
| shift | 33 | 0.036 | 0.469 |
| gap | 33 | 0.114 | 0.545 |
| shift1 | 24 | 0.012 | 0.501 |
| oracle | 24 | 0.033 | 0.505 |

### behaviour: greedy next action on edit steps
| policy | n | tool-call rate | re-fetches content evicted this step | re-fetches any evicted content | same tool as recompute | identical text to recompute | tools |
|---|---:|---:|---:|---:|---:|---:|---|
| recompute | 24 | 1.00 | 0.00 | 0.33 | 1.00 | 1.00 | {'todo_write': 7, 'bash': 11, 'read_file': 6} |
| shift | 24 | 1.00 | 0.00 | 0.54 | 0.75 | 0.29 | {'todo_write': 4, 'bash': 9, 'read_file': 11} |
| gap | 24 | 0.96 | 0.04 | 0.42 | 0.79 | 0.21 | {'todo_write': 4, 'bash': 10, 'read_file': 9} |
| shift1 | 24 | 1.00 | 0.08 | 0.46 | 0.96 | 0.58 | {'todo_write': 7, 'bash': 10, 'read_file': 7} |
| oracle | 24 | 1.00 | 0.04 | 0.42 | 0.92 | 0.54 | {'todo_write': 7, 'bash': 9, 'read_file': 8} |

### probes about the evicted content (asked as the next user turn; NLL per token of the true answer)
| metric | recompute | shift | gap | oracle | n |
|---|---:|---:|---:|---:|---:|
| headings:nll | 3.151 | 3.309 | 3.343 | 0.715 | 17 |
| headings:nll_minus_recompute | 0.000 | 0.158 | 0.193 | -2.435 | 17 |
| headings:nll_minus_oracle | 2.435 | 2.594 | 2.628 | 0.000 | 17 |
| headings:argmax_agree | 0.469 | 0.461 | 0.453 | 0.862 | 17 |
| cloze:nll | 5.084 | 5.142 | 5.136 | 1.104 | 17 |
| cloze:nll_minus_recompute | 0.000 | 0.058 | 0.052 | -3.981 | 17 |
| cloze:nll_minus_oracle | 3.981 | 4.039 | 4.033 | 0.000 | 17 |
| cloze:argmax_agree | 0.250 | 0.242 | 0.243 | 0.800 | 17 |
| cloze:greedy_lcs | 0.124 | 0.129 | 0.116 | 0.473 | 17 |
| presence_yes:nll | 0.456 | 0.285 | 0.278 | 0.332 | 17 |
| presence_yes:nll_minus_recompute | 0.000 | -0.171 | -0.178 | -0.124 | 17 |
| presence_yes:nll_minus_oracle | 0.124 | -0.047 | -0.055 | 0.000 | 17 |
| presence_yes:argmax_agree | 0.824 | 0.882 | 0.941 | 0.941 | 17 |
| presence_yes:forced_choice_correct | 0.824 | 0.882 | 0.941 | 0.941 | 17 |
| presence_yes:margin_yes | 1.033 | 1.621 | 1.787 | 1.555 | 17 |
| presence_no:nll | 1.394 | 1.878 | 1.908 | 1.270 | 17 |
| presence_no:nll_minus_recompute | 0.000 | 0.483 | 0.513 | -0.125 | 17 |
| presence_no:nll_minus_oracle | 0.125 | 0.608 | 0.638 | 0.000 | 17 |
| presence_no:argmax_agree | 0.118 | 0.059 | 0.118 | 0.235 | 17 |
| presence_no:forced_choice_correct | 0.118 | 0.059 | 0.118 | 0.235 | 17 |
| presence_no:margin_yes | 0.919 | 1.607 | 1.632 | 0.736 | 17 |
| copy:nll | 3.477 | 3.502 | 3.537 | 0.380 | 17 |
| copy:nll_minus_recompute | 0.000 | 0.025 | 0.060 | -3.097 | 17 |
| copy:nll_minus_oracle | 3.097 | 3.122 | 3.157 | 0.000 | 17 |
| copy:argmax_agree | 0.388 | 0.380 | 0.381 | 0.950 | 17 |

### compounding: KL from recompute by number of edit steps already spliced in the run (mean / median)
| edits so far | n | shift | gap | shift1 | oracle |
|---|---:|---|---|---|---|
| 1-3 | 3 | 0.031 / 0.031 | 0.034 / 0.032 | 0.023 / 0.021 | 0.024 / 0.014 |
| 4-10 | 7 | 0.052 / 0.056 | 0.323 / 0.122 | 0.010 / 0.006 | 0.038 / 0.014 |
| 11-20 | 10 | 0.044 / 0.023 | 0.099 / 0.065 | 0.008 / 0.004 | 0.025 / 0.005 |
| 21+ | 4 | 0.035 / 0.035 | 0.047 / 0.045 | 0.016 / 0.015 | 0.052 / 0.025 |

### edit steps split by whether the real (glm) response re-fetched evicted content
| subset | n | recompute: real-response NLL / KL from recompute | shift: real-response NLL / KL from recompute | gap: real-response NLL / KL from recompute | shift1: real-response NLL / KL from recompute | oracle: real-response NLL / KL from recompute |
|---|---:|---|---|---|---|---|
| real_refetch | 14 | 0.577 / 0.000 | 0.607 / 0.051 | 0.622 / 0.065 | 0.563 / 0.012 | 0.585 / 0.034 |
| real_no_refetch | 10 | 0.406 / 0.000 | 0.430 / 0.032 | 0.648 / 0.263 | 0.414 / 0.013 | 0.394 / 0.033 |

### stale vs recomputed KV on reused blocks after the edit
| policy | key cos | value cos | frac tokens key cos < 0.9 |
|---|---:|---:|---:|
| shift | 0.987 | 0.963 | 0.016 |
| shift1 | 0.994 | 0.983 | 0.005 |
| gap | 0.983 | 0.952 | 0.020 |
shift key cos by layer: [1.0, 1.0, 0.999, 0.998, 0.998, 0.997, 0.998, 0.998, 0.996, 0.995, 0.991, 0.988, 0.986, 0.986, 0.979, 0.985, 0.963, 0.972, 0.981, 0.979, 0.97, 0.976, 0.981, 0.98, 0.986, 0.988, 0.984, 0.978]
gap-mode max RoPE position reached: 64497; replay wall time 2910.1 s

## KV-base-50k-r3.qwen7b.replay.jsonl  (Qwen/Qwen2.5-7B-Instruct)
steps=32 edit_steps=23 placeholder_steps=16 evicted_items=33 evicted_tokens=51569 ctx_median=12338.0 ctx_max=14376 edit_kinds={'placeholder': 32, 'snip': 11}
real trajectory: {'tool_calls': 51, 'steps_reacquiring_evicted': 16, 'tools': {'glob': 1, 'bash': 12, 'read_file': 32, 'todo_write': 6}}

### compute (tokens the model ran)
| policy | total tokens computed | on edit steps | share of recompute |
|---|---:|---:|---:|
| recompute | 203,599 | 188,987 | 100.0% |
| shift | 65,380 | 50,768 | 32.1% |
| gap | 65,380 | 50,768 | 32.1% |
| oracle | 44,078 | 44,078 | 21.6% |
tail tokens (unavoidable new input) = 58,690; tokens after first change on edit steps: median 8482, sum 188,987

### fidelity on edit steps (teacher-forced over the real response)
| policy | n | KL from recompute (mean / median / p90 / max) | top-1 agree w/ recompute | KL from oracle | top-1 agree w/ oracle | NLL of real response | first-token KL from recompute (median) | first-token top1 = recompute |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| recompute | 23 | 0.000 / 0.000 / 0.000 / 0.000 | 1.000 | 0.035 | 0.977 | 0.400 | 0.000 | 1.00 |
| shift | 23 | 0.085 / 0.066 / 0.133 / 0.275 | 0.963 | 0.088 | 0.960 | 0.451 | 0.011 | 0.87 |
| gap | 23 | 0.168 / 0.112 / 0.354 / 0.776 | 0.945 | 0.163 | 0.946 | 0.533 | 0.030 | 0.78 |
| oracle | 23 | 0.043 / 0.022 / 0.063 / 0.282 | 0.977 | 0.000 | 1.000 | 0.426 | 0.003 | 1.00 |

### fidelity on ALL steps
| policy | n | KL from recompute (mean) | NLL of real response |
|---|---:|---:|---:|
| recompute | 32 | 0.000 | 0.378 |
| shift | 32 | 0.067 | 0.420 |
| gap | 32 | 0.128 | 0.477 |
| oracle | 23 | 0.043 | 0.426 |

### behaviour: greedy next action on edit steps
| policy | n | tool-call rate | re-fetches content evicted this step | re-fetches any evicted content | same tool as recompute | identical text to recompute | tools |
|---|---:|---:|---:|---:|---:|---:|---|
| recompute | 23 | 0.96 | 0.09 | 0.13 | 1.00 | 1.00 | {'todo_write': 16, 'create_task': 2, 'read_file': 3, 'bash': 1} |
| shift | 23 | 0.87 | 0.00 | 0.04 | 0.78 | 0.26 | {'todo_write': 18, 'create_task': 1, 'read_file': 1} |
| gap | 23 | 0.78 | 0.00 | 0.00 | 0.70 | 0.22 | {'todo_write': 17, 'create_task': 1} |
| oracle | 23 | 0.96 | 0.00 | 0.00 | 0.83 | 0.61 | {'todo_write': 20, 'create_task': 1, 'bash': 1} |

### probes about the evicted content (asked as the next user turn; NLL per token of the true answer)
| metric | recompute | shift | gap | oracle | n |
|---|---:|---:|---:|---:|---:|
| headings:nll | 2.715 | 2.993 | 2.947 | 0.330 | 16 |
| headings:nll_minus_recompute | 0.000 | 0.278 | 0.232 | -2.385 | 16 |
| headings:nll_minus_oracle | 2.385 | 2.663 | 2.617 | 0.000 | 16 |
| headings:argmax_agree | 0.525 | 0.490 | 0.499 | 0.936 | 16 |
| cloze:nll | 5.125 | 5.527 | 5.236 | 0.429 | 16 |
| cloze:nll_minus_recompute | 0.000 | 0.401 | 0.111 | -4.696 | 16 |
| cloze:nll_minus_oracle | 4.696 | 5.097 | 4.807 | 0.000 | 16 |
| cloze:argmax_agree | 0.312 | 0.302 | 0.311 | 0.883 | 16 |
| cloze:greedy_lcs | 0.136 | 0.142 | 0.136 | 0.741 | 16 |
| presence_yes:nll | 8.775 | 8.237 | 8.299 | 4.539 | 16 |
| presence_yes:nll_minus_recompute | 0.000 | -0.538 | -0.476 | -4.236 | 16 |
| presence_yes:nll_minus_oracle | 4.236 | 3.698 | 3.760 | 0.000 | 16 |
| presence_yes:argmax_agree | 0.000 | 0.000 | 0.000 | 0.125 | 16 |
| presence_yes:forced_choice_correct | 0.000 | 0.000 | 0.000 | 0.125 | 16 |
| presence_yes:margin_yes | -8.567 | -7.926 | -8.245 | -3.824 | 16 |
| presence_no:nll | 0.055 | 0.083 | 0.011 | 0.063 | 16 |
| presence_no:nll_minus_recompute | 0.000 | 0.028 | -0.044 | 0.008 | 16 |
| presence_no:nll_minus_oracle | -0.008 | 0.019 | -0.052 | 0.000 | 16 |
| presence_no:argmax_agree | 1.000 | 1.000 | 1.000 | 1.000 | 16 |
| presence_no:forced_choice_correct | 1.000 | 1.000 | 1.000 | 1.000 | 16 |
| presence_no:margin_yes | -10.323 | -9.793 | -10.454 | -10.517 | 16 |
| copy:nll | 3.481 | 3.653 | 3.550 | 0.276 | 16 |
| copy:nll_minus_recompute | 0.000 | 0.172 | 0.070 | -3.205 | 16 |
| copy:nll_minus_oracle | 3.205 | 3.377 | 3.275 | 0.000 | 16 |
| copy:argmax_agree | 0.402 | 0.403 | 0.405 | 0.964 | 16 |

### compounding: KL from recompute by number of edit steps already spliced in the run (mean / median)
| edits so far | n | shift | gap | oracle |
|---|---:|---|---|---|
| 1-3 | 3 | 0.057 / 0.053 | 0.070 / 0.072 | 0.043 / 0.031 |
| 4-10 | 7 | 0.065 / 0.063 | 0.231 / 0.112 | 0.060 / 0.022 |
| 11-20 | 10 | 0.103 / 0.076 | 0.171 / 0.139 | 0.031 / 0.009 |
| 21+ | 3 | 0.095 / 0.117 | 0.106 / 0.119 | 0.041 / 0.060 |

### edit steps split by whether the real (glm) response re-fetched evicted content
| subset | n | recompute: real-response NLL / KL from recompute | shift: real-response NLL / KL from recompute | gap: real-response NLL / KL from recompute | oracle: real-response NLL / KL from recompute |
|---|---:|---|---|---|---|
| real_refetch | 14 | 0.553 / 0.000 | 0.636 / 0.098 | 0.665 / 0.137 | 0.587 / 0.045 |
| real_no_refetch | 9 | 0.162 / 0.000 | 0.164 / 0.063 | 0.327 / 0.215 | 0.175 / 0.039 |

### stale vs recomputed KV on reused blocks after the edit
| policy | key cos | value cos | frac tokens key cos < 0.9 |
|---|---:|---:|---:|
| shift | 0.970 | 0.914 | 0.073 |
| gap | 0.963 | 0.897 | 0.090 |
shift key cos by layer: [1.0, 1.0, 0.999, 0.999, 0.997, 0.994, 0.995, 0.995, 0.991, 0.984, 0.984, 0.974, 0.961, 0.974, 0.936, 0.951, 0.931, 0.922, 0.936, 0.939, 0.917, 0.95, 0.956, 0.967, 0.973, 0.977, 0.971, 0.979]
gap-mode max RoPE position reached: 64393; replay wall time 9150.5 s

## POOLED[3 runs]  (Qwen/Qwen2.5-1.5B-Instruct)
steps=117 edit_steps=69 placeholder_steps=56 evicted_items=135 evicted_tokens=173757 ctx_median=12116 ctx_max=15179 edit_kinds={'placeholder': 115, 'snip': 24, 'summary': 2}
real trajectory: {'tool_calls': 221, 'steps_reacquiring_evicted': 75, 'tools': {'glob': 5, 'bash': 55, 'read_file': 139, 'todo_write': 22}}

### compute (tokens the model ran)
| policy | total tokens computed | on edit steps | share of recompute |
|---|---:|---:|---:|
| recompute | 575,670 | 498,665 | 100.0% |
| shift | 225,565 | 148,560 | 39.2% |
| gap | 225,565 | 148,560 | 39.2% |
| shift1 | 148,560 | 148,560 | 25.8% |
| oracle | 123,494 | 123,494 | 21.5% |
tail tokens (unavoidable new input) = 199,300; tokens after first change on edit steps: median 7137, sum 498,665

### fidelity on edit steps (teacher-forced over the real response)
| policy | n | KL from recompute (mean / median / p90 / max) | top-1 agree w/ recompute | KL from oracle | top-1 agree w/ oracle | NLL of real response | first-token KL from recompute (median) | first-token top1 = recompute |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| recompute | 69 | 0.000 / 0.000 / 0.000 / 0.000 | 1.000 | 0.092 | 0.969 | 0.464 | 0.000 | 1.00 |
| shift | 69 | 0.045 / 0.032 / 0.092 / 0.290 | 0.968 | 0.113 | 0.960 | 0.471 | 0.165 | 0.87 |
| gap | 69 | 0.089 / 0.046 / 0.179 / 1.015 | 0.955 | 0.155 | 0.951 | 0.508 | 0.208 | 0.84 |
| shift1 | 69 | 0.014 / 0.010 / 0.033 / 0.068 | 0.981 | 0.084 | 0.974 | 0.459 | 0.021 | 0.91 |
| oracle | 69 | 0.046 / 0.014 / 0.093 / 0.750 | 0.970 | 0.000 | 1.000 | 0.476 | 0.049 | 0.91 |

### fidelity on ALL steps
| policy | n | KL from recompute (mean) | NLL of real response |
|---|---:|---:|---:|
| recompute | 117 | 0.000 | 0.387 |
| shift | 117 | 0.032 | 0.392 |
| gap | 117 | 0.060 | 0.416 |
| shift1 | 69 | 0.014 | 0.459 |
| oracle | 69 | 0.046 | 0.476 |

### behaviour: greedy next action on edit steps
| policy | n | tool-call rate | re-fetches content evicted this step | re-fetches any evicted content | same tool as recompute | identical text to recompute | tools |
|---|---:|---:|---:|---:|---:|---:|---|
| recompute | 69 | 0.78 | 0.03 | 0.29 | 1.00 | 1.00 | {'read_file': 16, 'todo_write': 18, 'bash': 18, 'glob': 1, 'task': 1} |
| shift | 69 | 0.77 | 0.03 | 0.36 | 0.71 | 0.39 | {'read_file': 25, 'todo_write': 12, 'bash': 15, 'glob': 1} |
| gap | 69 | 0.75 | 0.04 | 0.30 | 0.75 | 0.35 | {'read_file': 20, 'todo_write': 14, 'bash': 17, 'glob': 1} |
| shift1 | 69 | 0.83 | 0.06 | 0.38 | 0.85 | 0.49 | {'read_file': 21, 'todo_write': 17, 'bash': 18, 'glob': 1} |
| oracle | 69 | 0.81 | 0.04 | 0.35 | 0.85 | 0.52 | {'read_file': 19, 'todo_write': 18, 'bash': 18, 'glob': 1} |

### probes about the evicted content (asked as the next user turn; NLL per token of the true answer)
| metric | recompute | shift | gap | oracle | n |
|---|---:|---:|---:|---:|---:|
| headings:nll | 3.560 | 3.663 | 3.677 | 0.754 | 55 |
| headings:nll_minus_recompute | 0.000 | 0.103 | 0.117 | -2.806 | 55 |
| headings:nll_minus_oracle | 2.806 | 2.908 | 2.923 | 0.000 | 55 |
| headings:argmax_agree | 0.416 | 0.412 | 0.407 | 0.868 | 55 |
| cloze:nll | 4.564 | 4.614 | 4.628 | 0.784 | 55 |
| cloze:nll_minus_recompute | 0.000 | 0.050 | 0.064 | -3.780 | 55 |
| cloze:nll_minus_oracle | 3.780 | 3.830 | 3.844 | 0.000 | 55 |
| cloze:argmax_agree | 0.298 | 0.306 | 0.306 | 0.844 | 55 |
| cloze:greedy_lcs | 0.139 | 0.140 | 0.151 | 0.570 | 55 |
| presence_yes:nll | 0.478 | 0.340 | 0.302 | 0.340 | 55 |
| presence_yes:nll_minus_recompute | 0.000 | -0.138 | -0.176 | -0.138 | 55 |
| presence_yes:nll_minus_oracle | 0.138 | 0.000 | -0.038 | 0.000 | 55 |
| presence_yes:argmax_agree | 0.818 | 0.873 | 0.945 | 0.964 | 55 |
| presence_yes:forced_choice_correct | 0.818 | 0.873 | 0.945 | 0.964 | 55 |
| presence_yes:margin_yes | 0.931 | 1.393 | 1.558 | 1.531 | 55 |
| presence_no:nll | 1.178 | 1.482 | 1.551 | 1.171 | 55 |
| presence_no:nll_minus_recompute | 0.000 | 0.304 | 0.373 | -0.007 | 55 |
| presence_no:nll_minus_oracle | 0.007 | 0.311 | 0.381 | 0.000 | 55 |
| presence_no:argmax_agree | 0.327 | 0.145 | 0.145 | 0.291 | 55 |
| presence_no:forced_choice_correct | 0.327 | 0.145 | 0.145 | 0.291 | 55 |
| presence_no:margin_yes | 0.596 | 1.066 | 1.160 | 0.578 | 55 |
| copy:nll | 3.500 | 3.519 | 3.547 | 0.442 | 54 |
| copy:nll_minus_recompute | 0.000 | 0.019 | 0.047 | -3.057 | 54 |
| copy:nll_minus_oracle | 3.057 | 3.077 | 3.104 | 0.000 | 54 |
| copy:argmax_agree | 0.405 | 0.405 | 0.406 | 0.936 | 54 |

### compounding: KL from recompute by number of edit steps already spliced in the run (mean / median)
| edits so far | n | shift | gap | shift1 | oracle |
|---|---:|---|---|---|---|
| 1-3 | 9 | 0.044 / 0.041 | 0.045 / 0.046 | 0.027 / 0.021 | 0.165 / 0.045 |
| 4-10 | 21 | 0.033 / 0.020 | 0.131 / 0.035 | 0.012 / 0.009 | 0.023 / 0.014 |
| 11-20 | 27 | 0.037 / 0.027 | 0.062 / 0.037 | 0.011 / 0.004 | 0.028 / 0.007 |
| 21+ | 12 | 0.085 / 0.045 | 0.108 / 0.070 | 0.016 / 0.015 | 0.035 / 0.019 |

### edit steps split by whether the real (glm) response re-fetched evicted content
| subset | n | recompute: real-response NLL / KL from recompute | shift: real-response NLL / KL from recompute | gap: real-response NLL / KL from recompute | shift1: real-response NLL / KL from recompute | oracle: real-response NLL / KL from recompute |
|---|---:|---|---|---|---|---|
| real_refetch | 48 | 0.409 / 0.000 | 0.412 / 0.050 | 0.419 / 0.061 | 0.401 / 0.014 | 0.415 / 0.035 |
| real_no_refetch | 21 | 0.588 / 0.000 | 0.606 / 0.035 | 0.712 / 0.152 | 0.592 / 0.016 | 0.615 / 0.069 |

### stale vs recomputed KV on reused blocks after the edit
| policy | key cos | value cos | frac tokens key cos < 0.9 |
|---|---:|---:|---:|
| shift | 0.987 | 0.963 | 0.016 |
| shift1 | 0.993 | 0.979 | 0.007 |
| gap | 0.984 | 0.956 | 0.019 |
shift key cos by layer: [1.0, 1.0, 0.999, 0.998, 0.998, 0.997, 0.998, 0.998, 0.996, 0.995, 0.99, 0.988, 0.986, 0.985, 0.979, 0.985, 0.963, 0.972, 0.981, 0.98, 0.97, 0.975, 0.981, 0.98, 0.986, 0.988, 0.984, 0.978]
gap-mode max RoPE position reached: 64497; replay wall time 2910.1 s
