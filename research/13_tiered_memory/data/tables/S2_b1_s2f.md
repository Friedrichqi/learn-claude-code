# S2 · B1 smoke: tiered KV with agent hints (cell s2f, Qwen/Qwen3-32B, RTX PRO 6000)

| level | arm | N | lead calls per h (window) | calls | prefilled tokens per call | evicted, share of prompt tokens | loaded from DRAM, share of prompt tokens | load wait per loading call, s | engine TTFT s (p50 / p95) | call s (p50 / p95) |
|---|---|---|---|---|---|---|---|---|---|---|
| tm_s2f_a1 | A1 + DRAM 64 GiB | 4 | 1,311 | 344 | 4,281 | 0.0 % | 14.5 % | 0.442 | 1.91 / 7.25 | 5.51 / 27.53 |
| tm_s2f_a3b | A3b + hints (fixed) | 4 | 1,302 | 333 | 4,889 | 0.0 % | 0.5 % | 0.677 | 1.29 / 6.00 | 5.48 / 16.76 |

*Key.* **evicted**: tokens inside the arrival ideal hit that the call did not hit (recomputed because evicted). **loaded from DRAM**: tokens restored from the CPU tier at admission. **engine TTFT**: scheduler arrival → first token.

**tm_s2f_a1 events**: hints {'tool_start': 335, 'turn_end': 7}; demotions 0 ({}, 0 blocks moved, 0 shared kept); returns 311 (0 after a demotion); predicted vs actual away time |error| p50 0.29 s, p90 covers 3 %; prefetch shadow requests 0 ({}, p50 — ms); gate changes 0.
  - returns kept: 310 calls; prompt tokens from GPU cache 65.9 %, from DRAM 15.5 %, recomputed after eviction 0.0 %

**tm_s2f_a3b events**: hints {'tool_start': 328, 'turn_end': 5, 'session_end': 2}; demotions 6 ({'ended': 2, 'timeout': 3, 'predicted': 1}, 1,151 blocks moved, 0 shared kept); returns 302 ({'n': 4, 'gpu_hit_share': 0.08392892269359387, 'dram_share': 0.8739099075470461, 'recompute_share': 0.0} after a demotion); predicted vs actual away time |error| p50 0.33 s, p90 covers 0 %; prefetch shadow requests 1 ({'done': 1}, p50 7,344 ms); gate changes 0.
  - returns demoted: 4 calls; prompt tokens from GPU cache 8.4 %, from DRAM 87.4 %, recomputed after eviction 0.0 %
  - returns kept: 298 calls; prompt tokens from GPU cache 79.9 %, from DRAM 0.5 %, recomputed after eviction 0.0 %

