# Weekly conclusion, 2026-09-16: where the tiered-memory argument stands

Covers 2026-09-10 to 2026-09-12. The week's thread is one question seen from six angles: where does
the s15 harness waste memory and time as its agents' contexts grow, and what would a tiered design
(files on disk, harness context, provider prefix cache, GPU KV) change? Terminology as in the
2026-09-09 deck: a *turn* is one lead activation, a *round* is one agent-loop iteration.

## 1. Documents produced this week

| document | question it answers | status |
|---|---|---|
| `weekly_progress/091626/teammate_input_redundancy.md` | how many of the bytes teammates load are copies another teammate already holds, by workload; rerun at the 512k lead budget | committed (`e7351e6`, `d67ab7f`) |
| `s15_integrated_harness/context_intervention_profile.md` | three lead-side fixes for eviction-driven round inflation: batch reads, outline demotion, stable prefix; plus the compaction ladder section | committed (`64d1b11`) |
| `s15_integrated_harness/kv_splice_profile.md` | can the lead keep the KV cache across a micro-compaction edit by splicing and re-rotating RoPE, and does the stale suffix remember the evicted file | modified, uncommitted (7B confirmation added) |
| `weekly_progress/091626/compaction_kv_reread_related_work.md` | has anyone named, measured or solved the two post-compaction costs, re-prefill and re-read | untracked |
| `weekly_progress/091626/teammate_memory_management.md` | how a Claude Code teammate carries context across task-board tasks; the menu of compaction choices; where s15 sits | untracked |
| `weekly_progress/091626/serving_memory_balancing.md` | how engines, clusters and providers ration HBM between concurrent requests, and what the harnesses can do about it | untracked |
| `scripts/latency_workloads.py`, `scripts/latency_bench/coding/` | end-to-end latency breakdown on three task classes (file Q&A, coding bench, math), team against solo | in progress, no write-up yet |

Slides for this week are not built; last week's `weekly_progress/090926/slides.html` is the template.

## 2. Findings

1. **Cross-teammate redundancy is arithmetic, and no budget touches it.** When teammates share a
   file, 52-80% of the bytes they load are copies another teammate already holds; the shared
   file's redundant share is (N-1)/N for N teammates, diluted by private files; disjoint corpora
   give 0%. Whole-result SHA-256 misses it (0-3%) because paging windows differ. In prefill-heavy
   runs the duplicates are 45-54% of all teammate prompt tokens; in decode-heavy runs the rate is
   as high but the volume small. Teammates never compact, so the team ends a run holding 2-5
   resident copies of the same file. Duplicates arrive within 60 s in a single wave and 6-16 min
   apart across waves, past the provider's roughly 5-minute cache lifetime. Raising the lead's
   budget from 50k to 512k chars fixed the lead (no compactions, no lost reports, no second waves,
   1.7-2.6x shorter wall time) and left the teammate numbers unchanged.
2. **The three lead interventions separate along different axes, and two trade against each
   other.** A stable prefix is a transfer-path property: cache hit 15% to 60%, uncached prompt
   tokens 633k to 219k, rewrite events 60 to 15, round count unchanged, and the worst answer
   quality of any arm (median 14/20 against 18). Batching is an upper-tier capacity property: with
   headroom at 100k it halves rounds and time (134 to 56 rounds, 964 to 600 s, 17/17 chapters);
   at 50k it delivers 10-61% of what was requested and coverage collapses to 2/17. An outline is a
   representation property: 856 chars per evicted slot against 248 for the bare pointer took 34%
   of a 50k budget, forced 4 summary compactions and 129 rounds against 69, yet produced the best
   answers (19/20) and shifted re-reads to targeted windows. The only change so far that improved
   latency without a quality cost is sizing the resident set (50k to 200k, 5x fewer rounds).
3. **The KV splice is a FLOP saver, not a round saver, and the information is elsewhere.** At the
   50k budget the splice removes 61% of prefill tokens and keeps 96-97% of argmax decisions; its
   error equals the compaction edit's own on Qwen2.5-1.5B (0.045 against 0.046 nats/token) and
   twice it on Qwen2.5-7B (0.085 against 0.043), so the ratio must be re-checked on a serving-size
   model before any rollout claim. The RoPE correction is mandatory: without it positions reach 64k
   with 12k resident tokens and the divergence tail hits 1.0 nats/token. It removes none of the
   re-acquisition rounds (39, 21, 10 per run at about 5 s each) and saves 1.3-2.6% of wall time,
   because the stale suffix carries no usable trace of the evicted file (stale keys at cosine 0.987
   to recomputed ones; recall probes at the prior). The oracle rows show where the information is:
   in the evicted block's own KV entries. The design that removes zoom-in rounds is keeping that
   block's KV in a lower tier and re-attaching it, with the splice as the mechanism that makes the
   re-attach affordable.
4. **Both post-compaction costs are named in the 2026 literature; their joint measurement is not.**
   Leyline is the nearest prior work to the splice (same in-place stub and RoPE re-rotation, patched
   into SGLang), evaluated against full-context behaviour where s15 evaluates against recompute,
   with fidelity from bit-exact on an MLA model to 6/128 agreement on GLM-4.7-Flash, the same
   "do not generalise across models" warning as finding 3. TokenPilot states the mutation-versus-
   cache tension and raised cache hit 38.7% to 79.2% by stabilising the prefix at ingestion; CORVUS
   defines a duplicate-read metric; Codex issue #16839 read one 610 KB file 53 times and Codex's
   5-file re-injection doubled compaction frequency. The rate-distortion paper says repeated
   compaction is "almost never measured"; s15's traces (re-acquisition rounds 30-58%, cache hit
   16-19%) are that measurement. Unexplored: addressable recall of an archived block as
   position-independent KV, so a recall costs neither a tool call nor a prefill.
5. **Claude Code's teammate is an append-only session with the standard compaction cascade
   inside it.** A new task is appended as the next user turn; nothing is reset per task; idle
   costs no API calls; the in-process 50-entry cap is UI-only; exited teammates resume with full
   history. Old tasks' tool results leave only through the cascade (persist large results,
   microcompact, full auto-compact at about 967k on 1M models), and the teammate cache TTL is five
   minutes by default. Anthropic's own coordinator prompt tells the lead to respawn a worker when
   task overlap is low. s15 mirrors the append-only half and has no cascade for teammates, which is
   the mechanism behind finding 1's resident copies. The compaction ladder (persist, cached and
   time-based microcompact, API context editing, session-memory, full, server-side, structural
   avoidance) gives a decision rule: keep content out of the prefix, edit when the cache is cold or
   in batches, summarise last with a shared prefix and a restored working set, budget placeholders
   like content, and for teammates compact at task boundaries and respawn on low overlap.
6. **On the serving side, nothing caps one request's share of memory; everything prices or
   places it.** Batching never shortens a context; a 1M-token Llama-3.1-70B context is 328 GB of
   KV, five H100s before weights, and costs the batch as much per-step bandwidth as a thousand
   1k-token requests. Engines make an oversized prompt wait until it fits, evict others' cached
   prefixes LRU to admit it, chunk its prefill behind the decodes, and on exhaustion recompute the
   newest request (vLLM) or retract short-output long-input ones (SGLang). Clusters route to the
   prefix, migrate, or reject by predicted SLO. Agents break the LRU assumption: 18.5% of a KV pool
   idle on tool calls, 40-60% of session time paused, and the fixes are all ways of telling the
   engine how long to hold (TTL pinning, offload at call start, session KV outside eviction,
   retention directives). Providers ration by uncached input tokens per minute, cache TTLs as
   memory leases, and price steps at the size where sharding begins; Anthropic now bills 1M
   context at flat rates. The harnesses can only shrink requests, cap concurrency and back off.

## 3. The argument as it stands

The tiered-memory thesis was that the harness's context is one tier in a hierarchy and that its
costs sit at the tier boundaries. This week's results fill in what each boundary costs and who pays.

- **Harness to provider cache.** Every in-place edit the harness makes is a re-prefill on the
  server; stabilising the prefix made re-sending 4x cheaper without changing the round count,
  because rounds are set by residency (finding 2). The literature confirms the mechanism and its
  production symptoms (finding 4). The engine-side alternative, editing the KV in place, is a valid
  FLOP saver whose error grows with model size (finding 3).
- **Provider cache to HBM.** The provider's TTL is a lease on memory, and idle teammates outlive it:
  second waves 6-16 min later are cold-cache restarts under s15's provider and under Claude Code's
  5-minute teammate TTL alike (findings 1, 5, 6). The engines' answer is emerging retention hints
  rather than fairness, so the lifetime knowledge the harness has (task boundaries, idle gaps) is
  exactly what the engine lacks.
- **What the lower tier should hold.** The splice retention probes show that the evicted content's
  meaning lives in its own KV block, not downstream (finding 3); across teammates the same file is
  held N times (finding 1). Both point at the same object: a shared, position-stable block of file
  content that is prefilled once and re-attached, which at the harness level is a pinboard or a
  shared file prefix and at the engine level is a lower-tier KV block with a stable position.
- **Representation costs budget.** Outlines bought accuracy with latency by crowding out payload
  (finding 2). Whatever metadata a tier keeps about evicted content must be budgeted like content.
- **The cheapest fix is still sizing.** Setting the resident set correctly was the only change with
  no quality cost (finding 2); Claude Code and Codex both hold their live prompt at 90-97% of the
  window and clear tool results before summarising (finding 5).

Caveats carried over from the individual reports: the interventions and the splice were measured on
one workload (X3) at one or two budgets; splice fidelity comes from Qwen2.5 1.5B and 7B replaying
GLM transcripts, not from the serving model; answer quality rests on three judges over 45
judgements; Claude Code internals come from analyses of the leaked v2.1.88 source, not from
Anthropic documentation; provider prices and limits were fetched on 2026-09-12 and drift.

## 4. Next steps

1. **Finish the latency breakdown.** Run the FQA, CODE and MATH workloads in team and solo modes
   through `scripts/latency_workloads.py`, then write up time-to-first-token, prefill, decode and
   tool time per round; this is the end-to-end number the memory argument has lacked.
2. **Give teammates a task-boundary compaction and the lead a respawn rule.** Time-based
   microcompact when the idle gap exceeded the provider's cache lifetime, respawn when the new
   task's file overlap is low; measure resident copies and uncached prefill tokens per minute
   against the PF-S and DC-S runs.
3. **Attribute the stable-prefix quality loss.** Run the low-water-mark snip alone, then the fixed
   archive marker, then the timestamp removal, and find which sub-change discards what the final
   answer needed.
4. **Make `compact_history` cache-sharing and restore the working set afterwards**, as Claude Code
   does with the same system prompt plus an appended instruction and up to five re-read files.
5. **Prototype the shared file block** (one position-stable prefix per team, prefilled once) and
   measure it against finding 1's (N-1)/N.
6. **Prototype KV re-attach** on the local Qwen replay: keep the evicted block's KV in a CPU tier and
   splice it back on demand; compare recall probes and re-acquisition rounds against the oracle.
7. **Measure the self-hosted vLLM directly**: KV blocks held by idle versus active teammates over a
   run, the effect of `--max-num-seqs` small enough to force preemption, and hit rate with
   session-sticky routing; then emit a retention-hint header from the harness as a prototype.
8. **Re-check the splice error ratio on a production-size model** before the FLOP-saving claim is
   used outside the replay.
9. **Housekeeping.** Commit the three untracked notes and the 7B section of the splice report;
   decide whether section 7 of `teammate_input_redundancy.md` (the compaction ladder, discarded by
   the 2026-09-11 reset) should be restored from `teammate_memory_management.md`; rerun the
   shell-denied intervention matrix after the provider's 5-hour cap resets; build this week's deck.
