# S2 · D1 smoke: session-resident token state (cell s2g, Qwen/Qwen3-8B, RTX PRO 6000)

| level | arm | N | calls (lead) | client modes | SDK+HTTP+parse ms (p50) | template+tokenise+hand-off ms (p50) | front end ms per call (mean) | front end, share of call time | prefilled tokens per call (mean) | call s (p50 / p95) | engine TTFT s (p50) | lead calls per h (window) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| tm_s2g_d1v | D1 verify | 2 | 802 (800) | init:8 verify:794 | 242 | 17 | 267 | 17.3 % | 407 | 1.30 / 2.89 | 0.043 | 3,974 |
| tm_s2g_d1 | D1 | 4 | 1635 (1631) | delta:1618 init:16 | 18 | 25 | 55 | 3.4 % | 939 | 1.39 / 2.78 | 0.058 | 7,383 |
| tm_s2g_base | baseline | 4 | 1198 (1194) | full | 122 | 122 | 244 | 12.5 % | 927 | 1.51 / 3.76 | 0.055 | 5,733 |

| level | append-only calls | previous reply, tokens (mean) | previous reply re-prefilled, tokens per call (mean) |
|---|---|---|---|
| tm_s2g_d1v | 773 | 82.2 | 8.7 |
| tm_s2g_d1 | 1,534 | 68.7 | 8.3 |
| tm_s2g_base | 1,137 | 80.3 | 84.3 |

*Key.* **front end**: client send → scheduler arrival; its first part is the SDK, HTTP and request parsing (send → vLLM starts rendering), the second the chat template, tokenisation and hand-off (with D1: the stored ids plus a render of only the new messages). **prefilled**: prompt tokens the engine computed (prompt − cache hit). **engine TTFT**: scheduler arrival → first token.

**D1 exactness (tm_s2g_d1v)**: 794 calls checked against vLLM's own full render: 794 identical outside the assistant turns and at every turn boundary (0 differing blocks counted over all calls). Assistant turns: 49398 of 74351 differ as expected (D1 keeps each reply as generated: 5,512,040 tokens vs 5,314,448 re-rendered).

**tm_s2g_d1**: client diff p50 2.12 ms; server delta render p50 4.6 ms; resyncs 0; engine prompt ≠ D1 ids 0.

