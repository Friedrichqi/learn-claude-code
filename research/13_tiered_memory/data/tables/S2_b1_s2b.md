# S2 · B1 smoke: tiered KV with agent hints (cell s2b, Qwen/Qwen3-32B, RTX PRO 6000)

| level | arm | N | lead calls per h (window) | calls | prefilled tokens per call | evicted, share of prompt tokens | loaded from DRAM, share of prompt tokens | load wait per loading call, s | engine TTFT s (p50 / p95) | call s (p50 / p95) |
|---|---|---|---|---|---|---|---|---|---|---|
| tm_s2b_a1 | A1 + DRAM 64 GiB | 4 | 1,296 | 339 | 5,242 | 0.0 % | 27.7 % | 0.572 | 3.09 / 9.28 | 7.30 / 18.97 |
| tm_s2b_a3 | A3 + hints | 4 | 996 | 303 | 6,449 | 3.5 % | 7.7 % | 1.646 | 3.62 / 11.61 | 8.29 / 26.97 |
| tm_s2b_a0 | A0 GPU only | 4 | 786 | 245 | 9,687 | 18.3 % | 0.0 % | — | 3.33 / 13.67 | 9.98 / 30.86 |

*Key.* **evicted**: tokens inside the arrival ideal hit that the call did not hit (recomputed because evicted). **loaded from DRAM**: tokens restored from the CPU tier at admission. **engine TTFT**: scheduler arrival → first token.

**tm_s2b_a1 events**: hints {'tool_start': 336, 'turn_end': 3}; demotions 0 ({}, 0 blocks moved, 0 shared kept); returns 305 (0 after a demotion); predicted vs actual away time |error| p50 0.19 s, p90 covers 3 %; prefetch shadow requests 0 ({}, p50 — ms); gate changes 0.
  - returns kept: 304 calls; prompt tokens from GPU cache 49.3 %, from DRAM 29.8 %, recomputed after eviction 0.0 %

**tm_s2b_a3 events**: hints {'tool_start': 296, 'turn_end': 5, 'session_end': 1}; demotions 5 ({'predicted': 5}, 6,868 blocks moved, 0 shared kept); returns 263 ({'n': 4, 'gpu_hit_share': 0.25345330122924853, 'dram_share': 0.3972458074599755, 'recompute_share': 0.054070037595573016} after a demotion); predicted vs actual away time |error| p50 0.22 s, p90 covers 3 %; prefetch shadow requests 0 ({}, p50 — ms); gate changes 0.
  - returns demoted: 4 calls; prompt tokens from GPU cache 25.3 %, from DRAM 39.7 %, recomputed after eviction 5.4 %
  - returns kept: 257 calls; prompt tokens from GPU cache 64.0 %, from DRAM 8.2 %, recomputed after eviction 4.0 %

**tm_s2b_a0 events**: hints {'tool_start': 242, 'turn_end': 2}; demotions 0 ({}, 0 blocks moved, 0 shared kept); returns 209 (0 after a demotion); predicted vs actual away time |error| p50 1.12 s, p90 covers 5 %; prefetch shadow requests 0 ({}, p50 — ms); gate changes 0.
  - returns kept: 208 calls; prompt tokens from GPU cache 55.3 %, from DRAM 0.0 %, recomputed after eviction 20.4 %

