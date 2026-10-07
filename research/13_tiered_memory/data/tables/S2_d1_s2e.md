# S2 · D1 smoke: session-resident token state (cell s2e, Qwen/Qwen3-8B, RTX PRO 6000)

| level | arm | N | calls (lead) | client modes | SDK+HTTP+parse ms (p50) | template+tokenise+hand-off ms (p50) | front end ms per call (mean) | front end, share of call time | prefilled tokens per call (mean) | call s (p50 / p95) | engine TTFT s (p50) | lead calls per h (window) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| tm_s2e_d1v | D1 verify | 2 | 795 (786) | init:22 verify:765 | 244 | 17 | 271 | 18.9 % | 588 | 1.23 / 2.21 | 0.048 | 4,119 |
| tm_s2e_d1 | D1 | 4 | 1337 (1319) | delta:1273 delta+resync:2 init:44 | 19 | 27 | 55 | 2.7 % | 827 | 1.34 / 7.80 | 0.065 | 5,231 |
| tm_s2e_base | baseline | 4 | 1112 (1101) | full | 126 | 119 | 242 | 10.3 % | 450 | 1.27 / 2.98 | 0.055 | 4,970 |

| level | append-only calls | previous reply, tokens (mean) | previous reply re-prefilled, tokens per call (mean) |
|---|---|---|---|
| tm_s2e_d1v | 766 | 66.6 | 8.7 |
| tm_s2e_d1 | 1,276 | 84.6 | 8.3 |
| tm_s2e_base | 1,080 | 87.8 | 91.8 |

*Key.* **front end**: client send → scheduler arrival; its first part is the SDK, HTTP and request parsing (send → vLLM starts rendering), the second the chat template, tokenisation and hand-off (with D1: the stored ids plus a render of only the new messages). **prefilled**: prompt tokens the engine computed (prompt − cache hit). **engine TTFT**: scheduler arrival → first token.

**D1 exactness (tm_s2e_d1v)**: 765 calls checked against vLLM's own full render: 713 identical outside the assistant turns and at every turn boundary (3437 differing blocks counted over all calls). Assistant turns: 69827 of 71003 differ as expected (D1 keeps each reply as generated: 5,124,456 tokens vs 4,845,101 re-rendered).

First non-assistant difference: `{"block": 3, "role": "user", "at": 5, "d1": "<|im_start|>user\n<tool_response>\nprofiling_sandbox/typer/__init__.py\nprofiling_sandbox/typer/__main__.py\n", "full": "<|im_start|>user\n<tool_response>\n[Earlier tool result saved at /tmp/yqi10/tm/117827/lanes"}`

**tm_s2e_d1**: client diff p50 0.63 ms; server delta render p50 5.7 ms; resyncs 2; engine prompt ≠ D1 ids 0.

