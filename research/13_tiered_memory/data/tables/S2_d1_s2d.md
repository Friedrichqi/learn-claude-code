# S2 · D1 smoke: session-resident token state (cell s2d, Qwen/Qwen3-8B, RTX PRO 6000)

| level | arm | N | calls (lead) | client modes | SDK+HTTP+parse ms (p50) | template+tokenise+hand-off ms (p50) | front end ms per call (mean) | front end, share of call time | prefilled tokens per call (mean) | call s (p50 / p95) | engine TTFT s (p50) | lead calls per h (window) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| tm_s2d_base | baseline | 4 | 1137 (1126) | full | 81 | 89 | 172 | 7.5 % | 608 | 1.21 / 7.87 | 0.054 | 5,141 |
| tm_s2d_d1v | D1 verify | 2 | 886 (877) | init:20 verify:855 | 194 | 17 | 217 | 15.2 % | 616 | 1.27 / 1.89 | 0.051 | 4,753 |
| tm_s2d_d1 | D1 | 4 | 1389 (1376) | delta:1339 delta+resync:1 init:33 | 16 | 25 | 47 | 2.6 % | 606 | 1.18 / 2.81 | 0.064 | 5,809 |

| level | append-only calls | previous reply, tokens (mean) | previous reply re-prefilled, tokens per call (mean) |
|---|---|---|---|
| tm_s2d_base | 1,091 | 86.8 | 90.8 |
| tm_s2d_d1v | 854 | 62.7 | 8.8 |
| tm_s2d_d1 | 1,342 | 71.2 | 8.4 |

*Key.* **front end**: client send → scheduler arrival; its first part is the SDK, HTTP and request parsing (send → vLLM starts rendering), the second the chat template, tokenisation and hand-off (with D1: the stored ids plus a render of only the new messages). **prefilled**: prompt tokens the engine computed (prompt − cache hit). **engine TTFT**: scheduler arrival → first token.

**D1 exactness (tm_s2d_d1v)**: 855 calls checked against vLLM's own full render: 748 identical outside the assistant turns and at every turn boundary (4448 differing blocks counted over all calls). Assistant turns: 69187 of 71242 differ as expected (D1 keeps each reply as generated: 4,633,968 tokens vs 4,357,643 re-rendered).

First non-assistant difference: `{"block": 2, "role": "user", "at": 14, "d1": "Update your todos.</reminder><|im_end|>\n\n", "full": "Update your todos.</reminder><|im_end|>\n"}`

**tm_s2d_d1**: client diff p50 0.60 ms; server delta render p50 5.0 ms; resyncs 1; engine prompt ≠ D1 ids 0.

