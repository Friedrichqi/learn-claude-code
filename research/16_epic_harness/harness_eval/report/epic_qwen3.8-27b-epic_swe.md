## EPIC round: qwen3.8-27b-epic on swe (phase b1a,b1b,b1c,b1d)

Arms: epic128, epic128a1, prefix, shift. Control: mean of prefix per block. Blocks: 5. Infrastructure failures are excluded and rerun under the same block key.

### Exploratory: resolve rate, arm minus control (t-interval over task means; intervals only, no verdicts)

| arm | tasks | blocks | difference | 90% two-sided [lo, hi] | non-inferior |
|---|---|---|---|---|---|
| epic128a1 | 5 | 5 | -0.200 | [-0.671, +0.271] | — |
| epic128 | 5 | 5 | -0.200 | [-0.671, +0.271] | — |
| shift | 5 | 5 | -0.400 | [-0.976, +0.176] | — |

Sensitivity: the same, without the 1 blocks where any arm hit the agent timeout (the timeout binds earlier for the EPIC arms, whose calls are slower on the emulating server):

| arm | tasks | blocks | difference | 90% two-sided [lo, hi] |
|---|---|---|---|---|
| epic128a1 | 4 | 4 | -0.250 | [-0.838, +0.338] |
| epic128 | 4 | 4 | -0.250 | [-0.838, +0.338] |
| shift | 4 | 4 | -0.250 | [-0.838, +0.338] |

### Secondary: tasks whose baseline runs snipped (30 tasks)

| arm | tasks | blocks | difference | 90% [lo, hi] |
|---|---|---|---|---|
| epic128a1 | 5 | 5 | -0.200 | [-0.671, +0.271] |
| epic128 | 5 | 5 | -0.200 | [-0.671, +0.271] |
| shift | 5 | 5 | -0.400 | [-0.976, +0.176] |

### Per arm

| arm | trials | pass@1 | pass^3 | consistent | at budget | agent timeout | lead calls | out tok |
|---|---|---|---|---|---|---|---|---|
| epic128 | 5 | 0.800 | None | None | 0.0% | 1 | 117.6 | 61,881 |
| epic128a1 | 5 | 0.800 | None | None | 20.0% | 1 | 124.4 | 66,059 |
| prefix | 5 | 1.000 | None | None | 0.0% | 1 | 78.2 | 46,568 |
| shift | 5 | 0.600 | None | None | 20.0% | 1 | 127.6 | 63,987 |

### Server side (EPIC_LOG)

| arm | lead calls logged | computed / prompt | post-snip calls | fallbacks | resets | errors | probes | first-token KL | reply KL | reply top-1 | D^K reused by age (0,1,2-3,4-7,8-15,16+) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| epic128 | 589 | 2.9% | 481 | 0 | 0 | 0 | 63 | 0.0322 | invalid | invalid | 0.005 / 0.056 / 0.056 / 0.087 / 0.121 / 0.234 |
| epic128a1 | 623 | 38.5% | 511 | 0 | 2 | 0 | 72 | 0.0107 | invalid | invalid | 0.003 / 0.045 / — / — / — / — |
| prefix | 392 | 69.0% | 0 | 0 | 0 | 0 | 0 | — | invalid | invalid | — / — / — / — / — / — |
| shift | 639 | 2.4% | 532 | 0 | 0 | 0 | 68 | 0.0315 | invalid | invalid | 0.007 / 0.075 / 0.073 / 0.060 / 0.108 / 0.207 |

Reply KL and top-1 are not shown: three of the four instances (@g0-@g2) ran a middleware whose teacher-forced probe misread the prompt logprobs when the hybrid model's loads are refused (fixed during the stage); the valid @g3 subset is in the notes below

### First divergence from `prefix` in the same block (lead-call index; −1 = identical run)

| arm | blocks | identical | median first divergent call |
|---|---|---|---|
| epic128 | 5 | 0 | 0 |
| epic128a1 | 5 | 0 | 0 |
| shift | 5 | 0 | 0 |

### Setup, selftests and deviations (Qwen3.8-27B, stage B, exploratory)

**Server.** Four instances, one per GPU (`qwen3.8-27b-epic@g0..g3`), each with:
- `EpicConnectorHMA`: hybrid model, so vLLM's hybrid memory allocator stays on;
- overrides in the 16 attention layers only;
- the vendor chat template, which keeps reasoning for every turn and is append-only;
- `EPIC_NO_LOAD`;
- a pool of 600K rows;
- a decode reserve of 8,192 tokens, because 1.6 % of baseline replies exceed 4,096.

The connector registered views of shape [863 blocks, 4 KV heads, 784 tokens, 512] with 16 hooks and a 64-dim RoPE.

**Selftests on @g0: all passed.**
- At delta 0, shift, epicinf and prefix agree to KL 0.0.
- S2 snip: 1,733–1,861 rows reused across a 13,046-token shift, with one probe on the single rank.
- The K deviation of reused rows stays bounded: at most 0.35 for shift and 0.24 for epic128.
- Hooks fire in the 16 attention layers; the pool is empty after the sessions end.

**Shadow probes on a hybrid model.**
- The probes' teacher-forced and counterfactual passes ask the connector to load the fresh recompute's rows for
  `[0, base)`. On a hybrid model the connector refuses, so those positions are computed. The scheduler logs
  "loads planned on a hybrid model are computed instead".
- A fresh computation gives exactly what the loaded rows held, so the passes keep their meaning.
- But the teacher-forced pass read the returned prompt logprobs at an offset of `base`, which assumes the prefix
  was loaded. With the refused loads, every position came back, so it compared distributions `base` positions
  apart.
- Effect in stage B (all servers started before the fix): **`tf_kl`, `tf_top1` and `tf_dnll` (reply-level KL)
  are invalid for Qwen3.8**. The S2 selftest showed reply KL around 13.4 against first-token KL around 0.
- Still valid:
  - first-token KL;
  - the L1 K deviation by age;
  - the counterfactual decodes;
  - the replay's first-token KL against `prefix` on identical prompts, which the driver computes from the returned
    logprobs.
- MiniMax is unaffected, because its loads are accepted. The offset is now read from the returned length
  (`epic/epic_server_ext.py`, after stage B started).
- **Which instances have valid reply metrics.**
  - The fix was written at 09:34:03. @g3's process launched at 09:34:01 and imported the middleware a few seconds
    later, so it runs the fixed code.
  - The data confirm it: the median shadow `tf_kl` is 0.0047 on @g3, against 10.4–11.2 on @g0–@g2.
  - So @g3's 35 probes are a valid subset for reply-level metrics: replay trajectories 3 and 7, and the pilot task
    django-12155. Reply KL, replay / pilot:

    | arm | reply KL, replay / pilot | probes, replay / pilot |
    |---|---|---|
    | prefix | 0.0039 / 0.0032 | 3 / 2 |
    | prefix_aa | 0.0038 | 7 |
    | epic128a1 | 0.0063 / 0.0042 | 7 / 2 |
    | epic128 | 0.0110 / 0.0039 | 3 / 1 |
    | shift | 0.0095 / 0.0085 | 8 / 2 |

    Reply top-1 agreement is 97–100 % for every arm.
- **Resets.** Two planned decode-overflow resets in the pilot, both epic128a1 replies of 8.6K and 9.2K tokens, past
  the 8,192-token reserve.

