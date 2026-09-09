# Teammate file-read overlap in the s15 harness

Weekly progress, 2026-09-09. Full report: `s15_integrated_harness/file_read_reuse_profile.md`.
Tooling: `s15_integrated_harness/scripts/profile_run.py` (driver), `scripts/file_read_reuse.py` (analyzer).
Traces: `s15_integrated_harness/traces/reuse_profiling/`.

## 1. Questions

1. In a long team run, how much of the file input reaching the models is **repeated** (same file read again by the same agent, read again by another agent, or re-sent to the provider), and which harness mechanisms cause it?
2. Do the lead and the teammates open the **same files**, and do they load only **fractions** of them?
3. After a teammate finishes a task, is its **context / KV cache freed**?
4. Why are **byte-identical** duplicate reads rare while the same file is read many times? How do slices overlap?
5. Do teammates have to read **whole files**, do they page incrementally, and do they ever stop midway?
6. Where does the lead's **50k-char budget** come from, and could a PagedAttention-like **window scheme** remove the repetition?
7. If the prefill of all repeated file content could be skipped, what is the **upper bound on latency saved**, and how much of end-to-end latency does the repetition cost today?

## 2. Experiments

Setup: s15 integrated harness (lead loop + persistent teammate threads + one-shot subagents), model `glm-5.3-flash` via z.ai's Anthropic-compatible API (reports `cache_read_input_tokens`; automatic prefix caching confirmed by probe). Plus the committed 28-teammate `Qwen3.8-27B`/vLLM stress trace as an observational sample.

| Id | Shape | Task (abridged) | Runs |
|---|---|---|---|
| X1 | lead only, tool-heavy | inspect `code.py` + `trace_runtime.py`, cite every model-call and tool-dispatch boundary | 2, plus 1 with the per-second timestamp removed |
| X2 | lead + 3 teammates, **same** file | three task-board items about `code.py`, one teammate each, synthesize | 2 (run 2 spawned a second wave of 3) |
| X3 | lead only, long context | read all 17 chapter READMEs and compare | 1, plus 1 with `CONTEXT_LIMIT` 50k -> 200k chars |
| X4 | lead + one-shot | one-shot summarises a file, lead verifies by reading it | 1 |
| X5 | lead + 3 teammates, **disjoint** files | summarise s09 / s10 / s13 `code.py` | 1 |
| Q | observational | 28-teammate Qwen stress run | 1 |

Instrumentation: the harness's own JSONL trace (every read carries path, offset/limit, size, SHA-256 of the result) plus a per-model-call sidecar (which tool results, how many chars, compaction placeholders, cache usage). Extra probes: identical-request cache probe, growing-conversation probes, cache-lifetime probe (idle 60/180/300/600 s), and a byte-level replay of consecutive lead requests under timestamp / no-timestamp / compaction variants.

## 3. Qualitative results

**Repetition is high at the path level, rare at the byte level, and structural.**

| agent kind | reads | re-open a file the same agent already opened | exact same-agent repeats | reads of a file another agent already read | bytes already fetched earlier | re-send per fetched byte | provider cache hit |
|---|---:|---:|---:|---:|---:|---:|---:|
| lead | 197 | 59% | 6% | 1% | 19% | 3.4x | 17% |
| teammates | 251 | 94% | 0% | 95% | 44% | 12.6x | 90% |

- **The lead repeats because the harness forgets.** `CONTEXT_LIMIT = 50000` chars (~12k tokens, `code.py:99`, harness-side, not an API limit) evicts a read after a median of 5 calls; 82% of lead re-reads follow a compaction shrink. Raising it to 200k turned X3 from 100 calls / 70 reads / 53 re-reads / 808 s into 7 calls / 17 reads / 0 re-reads / 168 s, same answer quality.
- **Teammates repeat because the harness isolates them.** No shared history, and shell is denied off the main thread, so a teammate cannot search: it pages through the whole file. Every X2 teammate reached 100% line coverage of `code.py` (lead with `grep`: 32-44% in X1). Over 47 teammate-file pairs, 81% reached >=90% coverage; the partial cases were all peripheral files (5 "page from the top then stop", 4 targeted lookups). When tasks touch disjoint files (X5) duplication is exactly zero.
- **Slices are nearly disjoint within one teammate, duplicated across teammates.** 174 reads of one file used 167 distinct windows; identical reads are 3% by count but 66% by bytes (whole-file reads). Line-level: each teammate holds 1.0-1.3 copies, the team 4.3x (run 1) and 6.8x (run 2); the file was transmitted to the model ~44 and ~103 times. Self re-fetches (1-24%) are re-zooms for line numbers (`read_file` returns none) and second-pass region reads.
- **Every window stays.** Windows are appended verbatim and never removed for teammates; the whole file ends up in context whether read at once or in pages (paging just costs one model call per page). The provider cache saves compute, not context.
- **A teammate's context is never released between tasks.** The message list persists across auto-claimed tasks (`code.py:1647/1788`); in the Qwen run task starts grew 1.2k -> 51.6k -> 102k -> 136k tokens, 35% of all teammate prompt tokens were finished-task history, five teammates died at the 262k-token provider limit, and only 16% of later-task reads even touched files already in context. Provider cache entries expire after ~5 min idle (99% hit at 300 s, 0% at 600 s) and never transfer between agents.
- **Why the lead's cache misses.** Consecutive lead requests share only 7,966 identical chars (tools + system up to `Current time`); removing the timestamp gives 100% prefix identity until compaction, but `micro_compact` (37-45%), `snip_compact` (~51%) and summary compaction (16%) break it again. Teammates' append-only prompts hit 84-94%.
- **Window/KV scheme.** KV states depend on position and prefix, so no hosted API can reuse a file window across contexts; a harness-level version can: fixed-size blocks with headers, a pointer instead of bytes for blocks already in context, a shared file "pinboard" in the prefix so one prefill serves all teammates, and volatile text moved after it.

## 4. Follow-up experiment: latency headroom from reusing repeated file content

**Question.** Teammates re-send the same file content many times; if every repeated window could be served from cache (however implemented), how much end-to-end latency could be saved at most?

**Method.** Latency model fitted on all 663 recorded model calls (R^2 = 0.90):
`duration = 2.6 s + 0.42 ms x uncached prompt token + 0.10 ms x cached prompt token + 18.4 ms x output token`
(uncached prefill ~2,400 tok/s, decode ~54 tok/s, a cached token costs 24% of an uncached one). "Repeated" file content = newly appended `read_file` windows whose lines any agent had fetched before (the most generous definition), converted at 3.94 chars/token.

**Where teammate time goes** (X2 run 2: 6 teammates, 3,227 s summed model time, 1,519 s wall):

| component | seconds | share |
|---|---:|---:|
| decode (output + thinking tokens) | 2,008 | 62% |
| cached-context cost (3.66M cached tokens) | 368 | 11% |
| fixed per-call overhead (95 calls) | 250 | 8% |
| uncached prefill (352k tokens) | 149 | 5% |

**Prefill of repeated file content that could still be skipped:**

| run | repeated content appended | prefill cost | share of that kind's model time | share of wall (ceiling) |
|---|---:|---:|---:|---:|
| X2 run 2, teammates | ~226k tok | 95 s | 3.0% | <= 6.3% (realistically 1-2%, teammates overlap) |
| X2 run 1, teammates | ~86k tok | 36 s | 1.4% | <= 2.2% |
| X3, lead (post-compaction re-reads) | ~80k tok | 34 s | 4.2% | 4.2% |
| X1 run 2, lead | ~10k tok | 4 s | 0.5% | 0.5% |
| X5, teammates (disjoint files) | 0 | 0 s | 0% | 0% |

Outer bound if repeated windows were also removed from the context entirely (pointer instead of bytes): roughly +240 s in X2 run 2, i.e. ~10% of teammate model time.

**Preview takeaways.**
- The provider's prefix cache already skips the prefill of the re-sent history: 3.66M of 4.0M teammate prompt tokens were cache hits in X2 run 2, worth ~1,175 s of prefill (~642 s in run 1); without it teammate model time would be 25-36% higher. Only newly appended duplicates still pay prefill.
- Prefill is cheap next to decode for this model (2,400 tok/s in vs 54 tok/s out): a whole 143k-char file costs ~15 s of prefill once, a 2,000-token answer ~37 s of decode every time.
- The real latency levers are elsewhere: fewer model calls (X3: 100 -> 7 calls cut wall time 808 s -> 168 s, -79%), a cacheable lead prefix (X3 lead spends 48% of its model time on uncached prefill; teammate-like hit rates would save ~265 s vs 34 s from skipping re-read content), and less coordination chatter (X2 runs spent 2,000-2,400 s of teammate time on decode and hit the 1,500 s ceiling).
- Caveats: one pooled linear fit for a hosted model with network included; the cached-token coefficient bundles KV loading with long-context decode slowdown; line-level "repeated" is the most generous count, so the savings are upper bounds.

## 5. Conclusion

File-read repetition in s15 is structural: the lead re-reads because a 50k-char harness budget evicts what it read, each teammate re-loads and permanently keeps the whole shared file because it has no search tool and no context reset, and the provider's prefix cache hides most of the teammates' cost (90% hits) but almost none of the lead's (17%), so the remedies are harness-side (a token-based budget, a stable cacheable prefix, task-boundary resets, and a locator tool or shared file pinboard), not model- or API-side, and skipping the prefill of repeated file content would recover only 1-3% of teammate model time because the provider cache already absorbs most of it and latency is dominated by decode and call count.
