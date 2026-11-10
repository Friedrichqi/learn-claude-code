# Topic 22: numad's moves of each run's vLLM processes (wb_numad.py, post hoc)

moves: numad rebinding a `VLLM::` process (its CPUs and memory) between the run's client start and end. share off node 0: the part of the run each process's memory was on another node (EngineCore, then the 4 workers).

| run | moves | to another node | minutes with a move | share off node 0 per process |
|---|---|---|---|---|
| `n32_wait` | 2 | 0 | 2 | 0 % 0 % 0 % 2 % 0 % |
| `n32_none` | 3 | 0 | 3 | 0 % 1 % 0 % 4 % 0 % |
| `n16_none` | 2 | 0 | 2 | 0 % 2 % 3 % 0 % 0 % |
| `n16_wait` | 2 | 0 | 2 | 0 % 0 % 3 % 0 % 0 % |
| `n8_wait_s1` | 1 | 0 | 1 | 0 % 0 % 0 % 0 % 1 % |
| `n8_none_s1_partial` | 3 | 0 | 2 | 0 % 0 % 2 % 1 % 0 % |
| `n8_wait_split_partial` | 2 | 0 | 1 | 0 % 0 % 50 % 0 % 0 % |
| `n8_wait_ht_partial` | 3 | 0 | 2 | 0 % 0 % 0 % 27 % 30 % |
| `n8_wait` | 136 | 64 | 19 | 1 % 6 % 3 % 4 % 6 % |
| `n8_none_oom_partial` | 148 | 70 | 24 | 6 % 20 % 13 % 12 % 14 % |
| `n8_none` | 331 | 166 | 42 | 23 % 21 % 21 % 19 % 15 % |
| `n4_none` | 17 | 6 | 4 | 1 % 0 % 0 % 0 % 0 % |
| `n4_wait` | 71 | 30 | 15 | 0 % 3 % 1 % 1 % 3 % |
