# Weekly conclusion, 2026-09-16: where the tiered-memory argument stands

Covers 2026-09-10 to 2026-09-14. The week's thread is one question seen from eight angles: where
does the s15 harness waste memory and time as its agents' contexts grow, and what would a tiered
design (files on disk, harness context, provider prefix cache, GPU KV) change? Terminology as in the
2026-09-09 deck: a *turn* is one lead activation, a *round* is one agent-loop iteration.

## 1. Documents produced this week

| document | question it answers | status |
|---|---|---|
| `research/02_input_redundancy/README.md` Part B (was `weekly_progress/091626/teammate_input_redundancy.md`) | how many of the bytes teammates load are copies another teammate already holds, by workload; rerun at the 512k lead budget | committed (`d67ab7f`) |
| `research/03_context_interventions/README.md` (was `s15_integrated_harness/context_intervention_profile.md`) | three lead-side fixes for eviction-driven round inflation: batch reads, outline demotion, stable prefix; plus the compaction ladder section | committed (`64d1b11`, `bdc4788`) |
| `research/04_kv_splice/README.md` (was `s15_integrated_harness/kv_splice_profile.md`) | can the lead keep the KV cache across a micro-compaction edit by splicing and re-rotating RoPE, and does the stale suffix remember the evicted file | committed (`bdc4788`, with the 7B confirmation) |
| `research/related_work/compaction_kv_reread_related_work.md` | has anyone named, measured or solved the two post-compaction costs, re-prefill and re-read | committed (`bdc4788`) |
| `research/related_work/teammate_memory_management.md` | how a Claude Code teammate carries context across task-board tasks; the menu of compaction choices; where s15 sits | committed (`bdc4788`) |
| `research/related_work/serving_memory_balancing.md` | how engines, clusters and providers ration HBM between concurrent requests, and what the harnesses can do about it | committed (`bdc4788`) |
| `research/05_latency_breakdown/README.md` Part A (was `weekly_progress/091626/latency_breakdown.md` and `s15_integrated_harness/latency_profile.md`) | what one agentic round costs end to end on three task classes (file Q&A, coding bench, math), team against solo: context preparation, prefill, decode, tool time, tokens, redundancy, rounds per task | committed (`bdc4788`); sections 4-5 added since |
| `research/06_tool_cost/README.md` Part B (was `weekly_progress/091626/tool_cost_exposure.md` and `s15_integrated_harness/tool_cost_profile.md`) | if the harness publishes its measured costs in the tool descriptions or the system prompt, does the model route around the expensive tools | untracked (2026-09-14) |

Slides for this week are not built; last week's `weekly_progress/090926/slides.html` is the template. [cleanup note 2026-09-23: the deck was built afterwards, `weekly_progress/091626/slides.html`.]
The latency results also exist as a page: https://claude.ai/code/artifact/3ce0a0d4-dc93-410d-bd45-13192db44281
The tool-cost results likewise: https://claude.ai/code/artifact/6de7f01b-27ae-4151-911f-42293a286e98

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
7. **A round is a fixed-cost provider event, and the only harness-side cost that shows is the
   harness calling the model itself.** Over 24 runs on three task classes (file Q&A, a coding bench
   with unit tests, competition math; five team runs and three solo runs each, every task solved)
   the agent call is 93-99.9% of a round: lead rounds 7.6-9.7 s in team mode, teammate rounds
   8.9-17.2 s, and a solo lead 9.5-41.5 s because it decodes the answers itself. Context preparation
   proper is about 10 ms (compaction pipeline 2 ms, prompt and tool assembly under 1 ms) and tool
   execution 10-50 ms, together 0.1-0.5% of a round; over all runs tools took 9.4 s against 4,704 s
   of agent calls. What does cost is context maintenance done by calling the model: memory recall
   4.9 s per call once records exist, and the turn-end `remember_after_turn` extraction 13-21 s per
   lead turn, which is 42-58% of the lead's busy time in team mode and 35% of all wall time
   (1,364 s of 3,946 s), serial with the next turn because it holds the agent lock. Inside the call,
   a regression over 446 calls gives 3.7 s fixed + 0.033 ms per uncached token + 17.4 ms per output
   token (r2 0.96), confirmed by a direct size sweep (0.0384 ms/token, 26,000 tok/s, r2 0.88) and by
   cache-served resends (0.034-0.042 ms/token): prefill is under 2% of call time everywhere, the
   fixed part 9-52% and decode 50-89%. Rounds per task are 2.9 (math), 4.0 (file Q&A) and 7.5
   (coding) for a teammate, plus 10-11 lead orchestration rounds per run; for tasks this small the
   solo lead was 1.4-1.8x faster end to end at equal quality.
8. **Redundancy is paid in tokens, KV memory and round count, never in preparation time.** The same
   runs still repeat as much input as the earlier ones: teammates re-send 33-86% of their prompt
   tokens, file content is 76-86% of a file-Q&A teammate's prompt, and the provider cache serves
   28-79% of it. Cross-teammate duplication equals each workload's design ceiling exactly (file Q&A
   22.8% measured against a 22.8% ceiling, coding 0% with disjoint directories, math no file reads,
   against PF-S's 61% under a 66.7% ceiling), so it measures how the tasks were cut, not the
   harness. None of it converts to latency: a re-sent 5k-token history costs 0.19 s. The regime
   where redundancy hurt was context pressure, not sharing: at the 50k-character limit the same
   harness spent 33-65 rounds and 363-807 s on one task with 18-45 shrink events, 26-35 placeholders
   resident and up to 46% of the lead's file bytes re-read, while preparation per round was still
   0.65 s of which 0.62 s was a single summary-compaction call and 25 ms the compaction code. The
   provider's cache is strictly prefix-based (identical resend 99.9% cached, same body behind a new
   prefix 0%, a unique block inserted mid-prompt 0%), which constrains any shared-block design.

9. **The measured costs cannot be delegated to the model by writing them into the prompt.** Tool
   descriptions say what a tool does and never what it costs, so the obvious cheap fix is to publish
   the price list. It does not work in a realistic pool: annotating `read_file` at 0.05-60 s, in
   stated seconds, in hardware terms (a 128 GiB working set staged across PCIe versus resident in
   HBM3) or as the words fast and slow, leaves its share of first moves at 45% against a 44%
   baseline over 160 trials (p = 1.000), and the model mentions the cost in 0-3% of those replies:
   it never raises the subject, because it picks on task fit and stops. The same model is not
   insensitive -- between two tools described as interchangeable the advertised cost decides
   97-100% of choices against a 53% baseline (p = 7e-06), overriding a 91% name prior, in every
   framing and from either position in the prompt -- but it is applying a tie-break, not optimising:
   a 5x advertised gap and a 7500x gap are indistinguishable and a genuine tie returns it to chance.
   What closes the gap is an objective, not information: adding "Tool calls are not free. Prefer the
   cheapest tool that can still answer the question correctly" to the same table takes `read_file`
   from 31% to 9% (p = 0.0098), while the table alone takes it from 31% to 32%. In the real harness
   neither arm had anywhere to act -- on value-lookup questions the lead already answered with
   `bash` and grep in 12 runs of 12 and never called `read_file`, and on "explain what these entries
   say" it read the whole file in 12 runs of 12 in every arm, correctly, since grep matches cannot
   answer that. Cost-aware routing therefore stays in the harness. The exception is the
   tiered-memory case itself: where a pool offers a hot and a cold route to the same bytes, nine
   tokens of description move the model from 53% to 100%.

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
- **The boundary costs are now priced.** Crossing the harness-to-provider boundary costs 0.038 ms
  per uncached token, so at the 2-25k-token prompts these harnesses actually run it is expensive in
  tokens and KV memory and nearly free in time; it turns into seconds only above roughly 50k tokens
  per call. What is expensive in time is crossing the boundary more often, which is round count,
  and the harness's own model calls for memory (findings 7, 8). This sharpens the thesis rather than
  changing it: a tiered design has to be argued on capacity, resident copies and round count, and on
  latency only above that prompt size.
- **The routing stays on the harness side.** Having priced the boundaries, the cheapest imaginable
  fix was to publish the price list to the model and let it schedule itself. It does not work: in a
  pool of tools that do different things, an advertised cost of 0.05 to 60 s leaves `read_file` at
  45% of first moves against a 44% baseline, and the model mentions the cost in 0-3% of those
  replies -- it routes on task fit and never consults the price (finding 9). The sensitivity is
  real but narrow: between two tools it is told are interchangeable the same numbers decide
  97-100% of choices, in any wording and from either position in the prompt, as a tie-break that
  ignores magnitude entirely. So the levers stay where findings 2, 7 and 8 put them -- residency,
  round count and the harness's own model calls -- and the one place model-side exposure belongs is
  the tiered pool itself, where a hot and a cold route to the same bytes are exactly the
  substitutable pair the effect needs, and where the objective has to be stated alongside the
  numbers or nothing moves.
- **The cheapest fix is still sizing.** Setting the resident set correctly was the only change with
  no quality cost (finding 2); Claude Code and Codex both hold their live prompt at 90-97% of the
  window and clear tool results before summarising (finding 5).

Caveats carried over from the individual reports: the interventions and the splice were measured on
one workload (X3) at one or two budgets; splice fidelity comes from Qwen2.5 1.5B and 7B replaying
GLM transcripts, not from the serving model; answer quality rests on three judges over 45
judgements; Claude Code internals come from analyses of the leaked v2.1.88 source, not from
Anthropic documentation; provider prices and limits were fetched on 2026-09-12 and drift; the
latency numbers come from one provider and model on one evening, whose fixed per-call latency itself
varied between 1.5 and 7 s across probes; and the tool-cost null rests on the same single model, on
two real workloads that turned out to floor and ceiling respectively, against a measured noise floor
of 12-15 percentage points.

## 4. Next steps

1. **Take the memory extraction off the lead's critical path.** The breakdown is done
   (`research/05_latency_breakdown/README.md` Part A): `remember_after_turn` runs after every lead turn, including the short
   turns that team events wake, at 13-21 s each and 35% of all wall time, and 35-74% of those calls
   hit the 1,000-token thinking cap. Run it once per user request, or asynchronously outside the
   agent lock, or with a smaller budget, and re-measure the three workloads in team mode.
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
9. **Re-run the latency breakdown where prefill matters.** Everything this week sat at 2-25k-token
   prompts, where prefill is under 2% of a call. Repeat file Q&A and the coding bench with a
   resident set above 50k tokens per call, where 0.038 ms per token becomes seconds, to find the
   crossover at which cache and KV reuse start buying latency instead of capacity.
10. **Give the tiered prototype two advertised routes.** Finding 9 says model-side cost exposure
    only bites between substitutable tools, which is exactly what a hot/cold pair of routes to the
    same working set is. When the shared file block or the KV re-attach prototype exists, expose
    both routes with ordered (not necessarily accurate) costs and a stated objective, and check the
    97-100% discrimination survives outside a synthetic pool. Re-running the 15-minute stage-1
    probe against a second model comes first, since finding 9's null is the part that could be
    model-specific.

11. **Housekeeping.** The week's notes, the latency tooling and traces and the 7B splice section are
   committed (`bdc4788`); the 2026-09-13 report edits, `provider_probe.py`, and the whole 2026-09-14
   tool-cost set (`tool_cost_profile.md`, `tool_cost_exposure.md`, four `scripts/tool_cost_*.py`,
   the additive `profile_run.py --tool-cost` diff and 2.1 MB of `traces/tool_cost/`) are not. Beyond
   committing those, what is left is to decide whether section 7 of
   `teammate_input_redundancy.md` (the compaction ladder, discarded by
   the 2026-09-11 reset) should be restored from `teammate_memory_management.md`; rerun the
   shell-denied intervention matrix after the provider's 5-hour cap resets; build this week's deck.
   [cleanup note 2026-09-23: the tool-cost set was committed later (`e0e522b`) and now lives in
   `research/06_tool_cost/`; the compaction ladder is kept once, in
   `research/03_context_interventions/README.md`.]
