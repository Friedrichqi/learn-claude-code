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
- **Pilot gates, per instance.**
  - Taken live before each instance was stopped. Stage B's own end-of-stage run found @g1–@g3 already stopped, so
    those versions are kept as `gates_*.stopped.json`.
  - E-0 per instance: @g0 98.4 % (126 pairs), @g1 100 % (61), @g2 98.4 % (63), @g3 100 % (63). Pooled: 99.0 % of
    313 pairs, with a mean share of 0.999.
  - Every miss is a non-canonical tokenization. The model sampled a token split that is not how the tokenizer would
    encode the same text, so re-tokenizing the rendered history differs inside the previous reply even though the
    text is identical. Only token-level history could avoid this; the template has no say.
  - All other gates passed on every instance: no preemptions; no fallbacks; resets were 2 of 1,802 post-snip calls,
    the two decode overflows above; one server record per call; no sessions left.
- **Stopping servers early.** @g1 and @g3 were stopped as soon as their pilot and the replay were done (about 10:51),
  and @g2 at 11:16. Stage B would otherwise have kept them up until @g0 finished.
