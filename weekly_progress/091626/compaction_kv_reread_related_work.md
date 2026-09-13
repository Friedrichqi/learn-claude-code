# Related work: what a harness pays after compaction — prefix-cache re-prefill and re-reading evicted files

Background research, 2026-09-12. Question: when an agent harness hits its context limit and compacts
(micro-compact = old tool results -> placeholders; summary/"semantic" compact = history -> model-written
summary), (a) the serving engine has to re-prefill everything after the edit because the cached prefix no
longer matches, and (b) when the agent later needs the detailed content again it spends extra rounds
re-reading the file. Has anyone named this problem, measured it, or solved it?

Method: web search over arXiv + venue sites + harness docs/issues; abstracts read for ~60 items; full text
skimmed for Leyline, "Models Take Notes at Prefill" and TokenPilot. Numbers below are as reported in the
papers' abstracts unless noted. arXiv IDs give the month (YYMM); the four-digit ID is the canonical handle.

Short answer: yes. Both costs are now named explicitly in the literature (mostly 2026), both show up as
production bug reports, and there are seven identifiable solution families — but nobody measures the two
costs jointly on real coding-agent traces across repeated compactions, which is exactly the gap the
rate–distortion paper (2607.08032) calls out.

---

## 1. The problem in the literature's own words

Two coupled costs:

- **Cost A — cache.** Any in-place edit of the message list changes the token sequence from the edit
  point on. Exact-prefix caches (vLLM automatic prefix caching, SGLang RadixAttention, the Anthropic
  prompt cache) miss from there and the engine re-prefills every later token. Manus reports a ~100:1
  input:output token ratio in production, so prefill is the cost that matters; *Agentic AI Workload
  Characteristics* (2605.26297) finds that with effective caching "most input tokens are reused across
  turns, making execution decode-dominated" — a compaction event flips one call back to prefill-bound.
- **Cost B — information.** The evicted or summarized content is gone. Re-acquiring it costs a tool round
  (round-trip latency + a fresh prefill of the file) and grows the context toward the next compaction
  (the cascade).

Papers that state the problem directly:

| Work | What it says |
|---|---|
| **Leyline: KV Cache Directives for Agentic Inference** (2606.01065) | "production agentic harnesses fall back to re-prefill on every edit, paying full prefix-recomputation cost." Names two agent-specific cache failures: *position invalidation* (identical content shifts position between turns) and *content removal* (policies need to drop cached spans without re-prefilling what follows). |
| **TokenPilot: Cache-Efficient Context Management for LLM Agents** (2606.17016) | Text pruning and dynamic memory eviction have "unconstrained sequence mutations [that] alter layouts, introducing prefix mismatches and cache invalidation." Fig. 7: without utility gates "each subsequent task inherits a cold window, forcing it to rediscover the document architecture via redundant full file reads." |
| **What to Keep, What to Forget: A Rate–Distortion View of Memory Compaction** (2607.08032) | Every layer (KV cache, prompt, agent memory) decides retention by attention magnitude or recency, discarding "before the query is known and with no way to undo it." "The repeated compaction that agents actually perform is almost never measured." |
| **CORVUS** (2607.22711) | Stale file snapshots force re-reads, "each re-read appending duplicate content to the trajectory"; defines a *duplicate file reads* metric (read_file/sync_file calls on a file already read in an earlier cycle). |
| **Parallel Context Compaction for Long-Horizon LLM Agent Serving** (2605.23296) | LLM summarization is lossy, its blocking call "stalls agent inference for tens of seconds", and both the summary volume and what it retains "fluctuate substantially from run to run." |
| **Toward Reliable Context Compression… Execution Instability** (2608.06503) | After compression: weakened influence of recent interactions, more blocked actions, and *repeated exploration across runs*. |
| **What Context Does a Coding Agent Actually Need to Act?** (2607.09691) | Natural-language summaries of code answer almost none of the behavioural questions the source answers (4/45 vs 27/45, held-out repos). Implication: for code, a summary cannot stand in for the file, so post-summary re-reads are structural, not a prompting bug. |
| **The Complexity Trap** (2508.21433) | Observation masking (placeholder for old tool outputs, actions kept) halves cost vs. the raw agent and matches LLM summarization on SWE-bench Verified across five model configs; a hybrid is 7–11% cheaper still. |
| **Reducing Cost of LLM Agents with Trajectory Reduction / AgentDiet** (2509.23586, FSE 2026) | "Useless, redundant, and expired information is widespread across trajectories"; removing it cuts input tokens 39.9–59.7% at equal performance. |
| **Governance Decay** (2606.22528) | The same information loss hits safety: constraints visible in context give 0% violations; after compaction 30% on average, up to 59%; constraint pinning restores 0%. |

## 2. Evidence from production harnesses

| Harness | Compaction mechanism | What happens to the two costs | Sources |
|---|---|---|---|
| **Codex CLI** | Server-side compaction (Responses API returns an encrypted state blob); all tool outputs dropped. | Re-reads up to 5 recently edited files (~50K-token budget) after compaction — this re-injection itself caused *cascading compactions* (v0.118 doubled compaction frequency). Issue **openai/codex#16839** (Apr 2026, open): a 610 KB OpenAPI spec was re-read 53× in one session, a 93 KB doc 24×, a 58 KB plan 17×; "multiplies token consumption by 10–20×". Proposes tracking re-reads across compaction cycles and a `context_pinned_files` option. | [deep-dive](https://codex.danielvaughan.com/2026/04/14/context-compaction-deep-dive-codex-cli-claude-code-opencode/), [#16839](https://github.com/openai/codex/issues/16839) |
| **Claude Code** | Three tiers: microcompact (clears old FileRead/Bash/Grep/Glob/WebSearch/WebFetch/Edit/Write results, keeps recent N), session-memory compact, full LLM summary. | Post-compaction re-injection of ≤5 recently read files (50K tokens total, 5K/file) plus skills (25K) so the model "doesn't immediately need to re-read them". Cache handling: time-based microcompact fires only when the server cache has already expired (zero marginal invalidation); a *cached path* (Anthropic-internal) issues `cache_edits` that remove tool results from the server-side cache **without invalidating the prefix**; session-memory compact keeps the kept messages as the prefix ("from" direction) to preserve cache; full compact invalidates. Issue **anthropics/claude-code#32099** (Mar 2026, closed not-planned): compaction dropped a subagent's result and the agent re-ran the whole subagent. | [source analysis ch.11](https://openedclaude.github.io/claude-reviews-claude/chapters/11-compact-system), [decodeclaude](https://decodeclaude.com/compaction-deep-dive/), [#32099](https://github.com/anthropics/claude-code/issues/32099) — third-party readings of the leaked source, not first-party docs |
| **Anthropic API** | Context editing `clear_tool_uses_20250919`; server-side compaction `compact_20260112` (default trigger 150K input tokens). | Docs: "Clearing invalidates cached prompt prefixes. To account for this, clear enough tokens to make the cache invalidation worthwhile" (`clear_at_least`). Compaction appends a `compaction` block and the API drops everything before it; guidance is to put a cache breakpoint after the system prompt so only the summary is a new cache write; compaction blocks are themselves cacheable; pre-compaction thinking blocks are not carried forward. | [context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing), [compaction](https://platform.claude.com/docs/en/build-with-claude/compaction) |
| **OpenCode** | Model-driven Compress tool + selective pruning. | Prunes only when >20K tokens can be freed; protected zone = most recent 40K tokens + skill outputs; overlapping compressions nest summaries. | deep-dive above |
| **Manus** | Append-only context; "restorable compression". | "KV-cache hit rate is the single most important metric for a production-stage AI agent." Never modify previous actions/observations; drop web page content but keep the URL, drop document content but keep the path — i.e. the re-read is explicitly accepted as the price of restorability. | [Manus blog](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus) |
| **OPENDEV** (2603.05344) | Adaptive compaction that progressively reduces older observations. | Tool outputs >8,000 chars go to a scratch file, replaced by a 500-char preview + path; the agent uses read_file to recover (itself subject to the threshold). | arXiv |
| **This repo (s08 / s15)** | `micro_compact` + summary compaction. | `micro_compact` writes the full result to disk before replacing it, so every placeholder carries a recovery path (same pattern as Manus/OPENDEV); s15 adds a stable-prefix flag. | `s08_context_compact/README.md` |

Takeaway: every major harness has converged on the same two mitigations — *restorable placeholders* for
Cost B and *append-only / prefix-stable layouts* for Cost A — and the two known production fixes for the
re-read (Codex's and Claude Code's 5-file re-injection) trade Cost B for more context pressure, which in
Codex's case produced the compaction cascade.

## 3. Solution families

Each family is tagged with the cost it addresses (A = cache re-prefill, B = re-acquisition rounds).

### F1. Cache-preserving text-level compaction  [A; no engine changes]

- **Append-only summary placement.** Anthropic's `compaction` block is appended and the *server* drops
  earlier blocks; Claude Code's session-memory compact keeps retained messages as the prefix; Manus keeps
  the context append-only. *Models Take Notes at Prefill* (2606.17107) shows the extreme case: an
  append-only "erratum" line instead of editing an earlier field keeps 98.5% vLLM APC hit-rate vs 1.0%
  for the in-prefix edit, 53–398× lower p90 TTFT.
- **Ingestion-time canonicalization.** TokenPilot compacts tool results *as they enter* the context
  (canonicalize volatile fields such as timestamps/paths/session IDs, truncate at 50k chars global / 30k
  bash / 10k grep, hash-dedup repeated results, move tool definitions downstream to avoid position
  jitter) so later edits are unnecessary; prefix stabilization alone lifted cache hit rate from 38.7% to
  79.2% on PinchBench; total cost $2.79 vs $7.24 vanilla and $4.06–4.21 for LLMLingua-2 /
  SelectiveContext / LCM. (This is the same idea as s15's stable-prefix flag and the per-second
  "Current time:" normalisation you needed in the replay.)
- **Edit only when the cache is already cold.** Claude Code's time-based microcompact runs when the 5-min
  server cache has expired anyway, so the edit's invalidation is free. Anthropic's `clear_at_least` and
  OpenCode's ">20K freed" rule are the batch version: amortise one invalidation over a large deletion.
- **Hide the summarizer stall.** *Parallel Context Compaction* (2605.23296) summarizes blocks
  concurrently; **Slipstream** (2605.08580) runs the compactor asynchronously while the agent continues
  on the original context and validates the summary against the agent's own next steps (+8.8 pp task
  accuracy, −39.7% end-to-end latency on SWE-bench Verified / BrowseComp).

### F2. Edit the KV cache instead of the text  [A at the engine; closest to the s15 KV-splice work]

- **CachedAttention / AttentionStore** (2403.19708, USENIX ATC 2024). The earliest systems paper on
  exactly this: when a conversation overflows the window and the oldest tokens are truncated, the
  positions of the remaining tokens change and the saved KV becomes invalid. Fix: save KV *before*
  positional encoding (RPE models) and re-embed positions on load. Handles FIFO truncation, not
  mid-sequence deletion.
- **Leyline** (2606.01065). A declarative directive D = (s_start, s_end, R, m) with m ∈ {amortize,
  forget}: `amortize` splices the replacement stub R in place, rotates the RoPE part of downstream keys by
  Δ and reindexes them, and leaves downstream K_nope / V **stale**; `forget` does prefix-trimmed
  re-prefill. ~200-LOC SGLang RadixCache patch. Results: +11.2 pp cache-hit, up to 241 ms latency
  saved; a ten-line `truncate_older_than(n=2)` policy routed through the interface lifts debug-gym
  solve rate +14.3 pp. Fidelity is strongly model-dependent: 128-token common-prefix agreement 128/128
  on DeepSeek-V2-Lite (bit-equivalent, MLA), 42/128 on JoyAI-Flash, 6/128 on GLM-4.7-Flash; over a
  50-step trajectory first-token agreement 100% / ≥80% / ~56% (DSv2-Lite / GLM / Moonlight). Their
  evaluation contract is "track full-context behaviour", not "match re-prefill". Cites LMCache's
  Pin/Clear/Move/Compress as operating below the semantic-edit level and Irminsul (Ma et al. 2026,
  content-addressed reuse + δ-rotation for MLA) as the reuse half.
- **Models Take Notes at Prefill: KV Cache Can Be Editable and Composable** (2606.17107). Re-rotates
  cached keys (values are position-free) to splice pre-compiled "skill" caches at new positions with a
  few seam-repair tokens; logit cosine 0.90–0.999 across twelve models (Qwen3 1.7B–32B, Llama-3.1
  8B–70B, Gemma, Mistral, DeepSeek-R1-Distill), O(L) vs O(L²), 13.9× at 32k. Explicitly does **not**
  support deleting a span: argues downstream tokens have "memoized conclusions" about earlier content,
  so it patches by appending, never by removing.
- **Claude Code `cache_edits`** (Anthropic-internal path, per source analyses): server-side removal of
  tool results from the cached prefix without invalidation — an existence proof that the vendor
  implemented KV-level deletion for micro-compact.

### F3. Compact the KV directly so nothing is re-prefilled  [A, and B if the compressed KV keeps the content]

- **KVzip** (2505.23416, NeurIPS 2025 oral): query-agnostic eviction scored by the model's ability to
  reconstruct the context from the cache; 3–4× KV reduction, ~2× decode latency, reusable across
  later queries; Fast KVzip (Jan 2026) removes the compression overhead; in NVIDIA KVpress.
- **Practical Online KV Cache Compaction for LLM Agents** (2608.00902): the agent setting — compaction
  must happen before future relevance is known, so proxy queries are needed. Findings: immediate
  compaction hurts; delaying until the agent's own future queries exist recovers most of the gap; token
  eviction is more robust than attention matching under imperfect proxies; 80% KV reduction with most
  accuracy kept.
- **Still** (2606.07878): a per-layer Perceiver trained once against a frozen model produces compact
  K/V in a single forward pass and can be applied iteratively — "a long-horizon regime unavailable to
  per-context methods"; 8–200×, +8–22 points over the best baseline on RULER.
- **SideQuest** (2602.22603): the reasoning model itself judges token utility as an auxiliary task run
  in parallel; up to 65% peak-token reduction from 215 training samples.
- **MemDecay** (2607.10582): region-aware eviction for agent traces (system prompt, tool interactions,
  scratchpad get different base priority/decay; system tokens live ~14× longer); pinning keeps 24/24
  system facts vs ≤13/24 for uniform policies.
- Related: **CONF-KV** (2605.24786), **EVICPRESS** (2512.14946), **Tangram** (2606.06302; a vLLM
  substrate for non-uniform per-head KV budgets in multi-turn serving, 2.6× throughput), **ChunkKV**
  (2502.00299).
- Learned soft compression into activations: **Activation Beacon** (2401.03462, ICLR 2025), gist
  tokens / ICAE / AutoCompressors / Compressed Context Memory; **Breadcrumbs Reasoning**
  (2510.13797); *A Silver Bullet or a Compromise for Full Attention?* (2412.17483) documents gist-token
  failure modes.
- **Rate–distortion view** (2607.08032) gives a seven-axis taxonomy across all of these layers and a
  layer-agnostic lower bound.

### F4. Keep the KV resident or persisted across the tool pause  [A for the no-edit case; across sessions]

- **Continuum** (2511.02230): pin the KV in GPU memory with a time-to-live during tool execution
  (reload cost + queueing delay decide the TTL); up to 8.18× latency/throughput on multi-turn agents.
- Workflow-aware eviction/prefetch for multi-agent programs: **KVFlow** (2507.07400; agent step graph,
  steps-to-execution eviction, 1.83–2.19× over SGLang hierarchical radix cache), **TokenCake**
  (2510.18586; offload idle KV during function calls, predictive upload, −47% latency vs vLLM),
  **PBKV** (2605.06472; prediction-based, 1.85× over LRU, 1.26× over KVFlow), **CacheScout**
  (2608.14624; learns agent execution transitions online, +10–18 pp hit rate, +57% peak throughput),
  **Autellix** (2502.13965; programs as first-class scheduling units), **ForkKV** (2604.06370).
- **Agent Memory Below the Prompt** (2603.04428): persist each agent's KV to disk in 4-bit and reload
  into attention instead of re-prefilling (15.7 s per 4K-context agent on an M4 Pro); 22–136× faster
  restoration than prefill.
- **Can I Buy Your KV Cache?** (2606.13361): "every agent re-runs prefill … over identical text"; a
  market for publisher-precomputed, token-exact KV; 9–50× cheaper than prefill on Qwen3-4B.

### F5. Position-independent KV for content that is re-read  [makes the re-read's prefill nearly free]

- General position-independent caching (PIC): **Prompt Cache** (2311.04934, MLSys 2024),
  **CacheBlend** (2405.16444, EuroSys 2025; selective recompute of high-deviation tokens), **EPIC**
  (2410.15332; static first-k tokens per chunk), **KVLink** (2502.16002; trained link tokens),
  **Block-Attention** (2409.15355, ICLR 2025), **KV Packet** (2604.13226; immutable packets with
  soft-token adapters, no recompute, F1 comparable to full recompute), *Fine-Tuning a
  Concatenation-Aware Model or Recomputing KV Caches? Why Not Both?* (2609.09768; RULER at 124k
  +9.7 points vs recompute-only, TTFT −80%), **RedKnot** (2606.06256), **SparseX** (2606.01751),
  **KVShareArena** (2609.10266; benchmark for reuse across contexts and checkpoints).
- Agent-specific PIC: **ReCache** (2608.19662) — *resource-wise attention* removes attention between
  distinct resource blocks and gives each block resource-local positions, so tool/skill/schema KV is
  composition-invariant and cached once (3.655× TTFT, −92% KV memory, Inv-F1 82.3 vs 82.4);
  **AgentKVShift** (2607.21604) — probe-guided residual correction per memory unit, refresh only 10–30%
  of the cache for near-full quality, 2–3.5× prefill speedup; **KVCOMM** (2510.12872) and
  **DroidSpeak** (2411.02820) for cross-agent KV sharing. **HijackKV** (2607.19957) is the security
  caveat: PIC is "most practical for moderately shared content such as … code files, or agent skill
  files", which is also where poisoning lands.
- Fit: a re-read of a repository file after eviction is exactly "the same chunk at a new position after
  a different prefix". PIC would let the harness re-inject a file's KV instead of re-prefilling it —
  but at PIC fidelity cost, which in every paper above grows with how much the chunk interacts with the
  prefix.

### F6. Make evicted content recoverable without re-running the tool  [B; removes the tool round, not the prefill]

- Restorable placeholders: Manus (URL/path), OPENDEV (scratch file + preview), this repo's
  `micro_compact` recovery path.
- **Addressable Recall Compaction / ARC** (2607.25066): tool observations go to an append-only,
  ID-addressable archive; the active context keeps only citation IDs; the agent recalls by ID instead of
  re-executing the tool or trusting similarity retrieval. NIAH exact-answer 99.40% vs 88.12% best
  baseline; LongBench-v2 Hard 29.97 vs 28.25; lower serving time and memory traffic.
- **LCM: Lossless Context Management** (2605.04050): hierarchical summary DAG with lossless pointers to
  every original and automatic restoration of full content on request; the Volt agent (Opus 4.6) beats
  Claude Code on OOLONG at every length 32K–1M.
- **VISTA** (2606.30005): working memory as typed addressable blocks plus a runtime dashboard (token
  use, recency, archive status, remaining budget); archived blocks are recoverable full-fidelity
  payloads. LOCA-Bench 22.7% → 50.7% for Gemini-3-Flash; the dashboard matters beyond archive/recover.
- **TokenPilot**'s recovery tool (content-hash artifact registry): removing it drops accuracy 80.9 → 77.1.
- **ACM: Agentic Context Management** (2607.23809): agent-invoked context-editing tools; discarded
  content goes to external memory and is retrieved on demand; post-training pipeline; gains on agentic
  search and coding.
- **CORVUS** (2607.22711): the coding-agent-specific fix — a synchronized registry decouples file reads
  from their observations and injects the *current* file content at each cycle, so nothing is stale and
  nothing is duplicated: 9–50% fewer input tokens per task, 15–32% shorter final prompts, up to 37%
  fewer cycles on SWE-PolyBench_Verified / SWE-Bench Pro, pass rates maintained. Open question it does
  not address: content injected fresh each cycle is not append-only, so its interaction with prefix
  caching depends on where the registry sits in the prompt.
- Harness re-injection: Claude Code (≤5 files, 50K/5K), Codex (5 edited files, ~50K → cascade), the
  Codex issue's frequency-based pinning proposal.
- Trajectory folding: **Context-Folding** (2510.11967), **AgentFold** (2510.24699); classic paging:
  **MemGPT** (2310.08560).
- Every method here still pays one model round for the recall and a prefill of the recalled content.
  No paper found combines addressable recall (F6) with position-independent KV of the archived block
  (F5), which would be "recall without prefill".

### F7. Decide better what to keep  [reduces how often B happens]

- Masking beats summarizing on cost, for coding at least: *Complexity Trap* (above); *Evaluating
  Memory Condensation Strategies for Coding Agents* (2605.18854; 8 condensers × 60 DiscoveryBench
  tasks, GPT-4o: LLM condensers raise token cost 24–94%, masking gives 8.6% net savings, none changes
  hypothesis quality); *Less Context, Better Agents* (2606.10209; enterprise tool agent with GPT-5:
  last-5 pruning 79% vs full history 71%, pruning + summary 91.6%, at ~1/3 the tokens).
- Learned or optimized compaction: **ACON** (2510.00615; natural-language guideline optimization from
  success/failure pairs, 26–54% peak-token reduction), **TRACE** (2608.06503; paired closed-loop
  continuations from the same state to score a compression event, boundary-local evaluation),
  **Slipstream** (above), **AdaCoM** (2605.30785; an external RL-trained manager for a frozen agent;
  finds a fidelity–reliability trade-off: strong agents want fidelity, weak agents want aggressive
  compression), **CompactionRL** (2607.05378; joint task + summary RL, SWE-bench Verified +7.0 for
  GLM-4.5-Air, used to train GLM-5.2), **ZipRL** (2605.28069), **ContextBudget / BACM-RL**
  (2604.01664), **Self-Compacting Agents** (2606.23525; the model decides when to compact, 30–70%
  lower cost), **Agent-Omit** (2602.04284), **MEM1** (2506.15841), **ReSum** (2509.13313).
- Code-aware pruning of the observations themselves: **SWE-Pruner** (2601.16746; goal-guided 0.6B
  skimmer, 23–54% token reduction with higher success on SWE-bench Verified), **LaMR** (2605.15315),
  **ContextWeaver** (2604.23069; dependency-structured memory).
- **Governance Decay**'s constraint pinning: isolate must-keep content from the lossy path.

### F8. Avoid the accumulation by architecture  [sidesteps both]

- **Recursive Language Models** (2512.24601): the prompt lives in a REPL variable; the model inspects
  slices and recurses — inputs 100× beyond the window at comparable cost.
- **LCM / Volt** (F6) and *Coding Agents are Effective Long-Context Processors* (2603.20432; agents
  with a file system beat semantic search and window scaling by ~17% on long-context tasks).
- Sub-agent isolation (Anthropic's context-engineering guidance; s15 lead/teammate split).

## 4. Where the s15 work sits

- **Nearest prior work to the KV-splice experiment is Leyline** (2606.01065): the same mechanism
  (in-place stub, RoPE re-rotation of downstream keys, stale K_nope/V, SGLang patch). Differences worth
  stating in the write-up: (i) Leyline evaluates against *full-context* behaviour, s15 evaluates against
  *recompute after the edit* (the behaviour the harness actually wants); (ii) Leyline's fidelity swings
  from bit-exact (DSv2-Lite, MLA) to 6/128 agreement (GLM-4.7-Flash) — the s15 finding that splice error
  grows from 1.0× to 2.0× the eviction-edit error between 1.5B and 7B is the same "do not generalise
  across models" warning with a different metric; (iii) *Models Take Notes at Prefill* refuses to
  delete spans because downstream tokens carry "memoized conclusions" — the s15 retention probes found
  stale keys at cosine 0.987 to recomputed ones (the downstream KV barely encoded the evicted file).
  Those two observations are about different content (a decision-relevant field vs. a bulk file dump)
  and should be reconciled explicitly rather than treated as a contradiction.
- **The joint measurement is open.** TokenPilot measures cache hit rate (from API metadata) for
  text-level compaction; Leyline measures cache hit and solve rate but not re-read rounds; CORVUS and
  the Codex issue measure duplicate reads but not cache; 2607.08032 says outright that repeated
  compaction is "almost never measured". s15's traces (re-acquisition rounds 30–58%, cache hit 16–19%,
  1.3–2.6% wall-time saving from splice) are that measurement.
- **The cascade is documented in production, not in papers.** Codex's 5-file re-injection doubling
  compaction frequency is the only concrete report; TokenPilot's lifecycle gates and the Codex issue's
  "pin after 2+ re-reads" are the proposed controls. A budget-aware pinning policy evaluated on traces
  would be new.
- **Unexplored combination:** F6 + F5 — addressable recall of an archived block *as KV* (ReCache-style
  resource-local positions, or KV Packet adapters) so a recall costs neither a tool execution nor a
  prefill. Related but not identical: Leyline's `amortize`, AgentKVShift's memory-unit correction.

## 5. Reading list (by relevance)

| # | Work | ID / venue | Cost | Why it matters here |
|---|---|---|---|---|
| 1 | Leyline: KV Cache Directives for Agentic Inference | 2606.01065 | A | In-place KV splice with RoPE correction for harness edits; names the re-prefill-on-every-edit problem |
| 2 | TokenPilot: Cache-Efficient Context Management for LLM Agents | 2606.17016 | A+B | States the mutation-vs-cache tension; ingestion-time canonicalization; recovery tool; measured hit rates |
| 3 | openai/codex#16839 | GitHub, Apr 2026 | B | 53 re-reads of one 610 KB file; 10–20× tokens; pinned-files proposal |
| 4 | Claude Code compaction (source analyses) | openedclaude ch.11, decodeclaude | A+B | 5-file re-injection; cold-cache-only microcompact; internal `cache_edits` |
| 5 | What to Keep, What to Forget (rate–distortion) | 2607.08032 | A+B | Unifying framing; "repeated compaction is almost never measured" |
| 6 | CORVUS | 2607.22711 | B | Duplicate-file-read metric; registry that removes re-reads |
| 7 | Practical Online KV Cache Compaction for LLM Agents | 2608.00902 | A | Online KV compaction with proxy queries; delay compaction |
| 8 | Addressable Recall Compaction (ARC) | 2607.25066 | B | Recall by ID without re-executing tools |
| 9 | CachedAttention / AttentionStore | 2403.19708, ATC 2024 | A | Truncation invalidates KV via positions; decoupled positional encoding |
| 10 | The Complexity Trap | 2508.21433 | B | Masking ≈ summarization at half the cost (SWE-agent) |
| 11 | What Context Does a Coding Agent Actually Need to Act? | 2607.09691 | B | Summaries cannot replace code (4/45 vs 27/45) |
| 12 | ReCache | 2608.19662 | A/B | Composition-invariant KV blocks for tool resources |
| 13 | Models Take Notes at Prefill | 2606.17107 | A | Append-only erratum keeps 98.5% APC hit; RoPE-repositioned skill splices; no deletion |
| 14 | Slipstream | 2605.08580 | B | Async compaction validated against the agent's next steps |
| 15 | Parallel Context Compaction | 2605.23296 | A | Summarizer stall and volume unpredictability |
| 16 | Continuum | 2511.02230 | A | KV TTL across tool calls |
| 17 | LCM: Lossless Context Management | 2605.04050 | B | Lossless pointers + auto-restore; beats Claude Code on OOLONG |
| 18 | VISTA (LLM Agents Are Latent Context Managers) | 2606.30005 | B | Addressable blocks + budget dashboard |
| 19 | KVzip | 2505.23416, NeurIPS 2025 oral | A | Query-agnostic KV eviction reusable across queries |
| 20 | Still | 2606.07878 | A | Iterative learned KV compaction for long horizons |
| 21 | Agentic AI Workload Characteristics | 2605.26297 | A | Decode-dominated only while the cache holds |
| 22 | Toward Reliable Context Compression (TRACE) | 2608.06503 | B | Repeated exploration after compression; boundary-local eval |
| 23 | AgentDiet | 2509.23586, FSE 2026 | B | Redundant/expired information taxonomy |
| 24 | Governance Decay | 2606.22528 | B | Compaction erases constraints; pinning |
| 25 | KV Packet; Why Not Both?; KVShareArena | 2604.13226; 2609.09768; 2609.10266 | A | State of position-independent KV reuse, Sept 2026 |
| 26 | AgentKVShift | 2607.21604 | A | Cheap correction for re-encoded memory units |
| 27 | Evaluating Memory Condensation Strategies for Coding Agents | 2605.18854 | B | LLM condensers cost +24–94%; masking saves 8.6% |
| 28 | ACON; CompactionRL; AdaCoM; Self-Compacting Agents | 2510.00615; 2607.05378; 2605.30785; 2606.23525 | B | Learned/optimized summaries |
| 29 | Manus: Context Engineering for AI Agents | blog, Jul 2025 | A+B | KV hit rate as the metric; restorable compression |
| 30 | Anthropic docs: context editing, compaction | platform.claude.com | A | Vendor statement that clearing invalidates the prefix |
| 31 | Recursive Language Models | 2512.24601 | — | Architecture that avoids accumulation |
| 32 | Inside the Scaffold: source-code taxonomy of 13 coding agents | 2604.03515 | — | Seven distinct compaction strategies; compaction is an unconverged design axis |

Surveys for framing: *A Survey of Context Engineering for LLMs* (2507.13334); *Externalization in LLM
Agents: memory, skills, protocols, harness engineering* (2604.08224); *A Survey of Agent Memory in the
Second Half* (2602.06052, TMLR).

## 6. Caveats

- Abstract-level reading for most items; only Leyline, *Models Take Notes at Prefill* and TokenPilot
  were checked in the full text. Verify numbers before quoting them in a paper.
- Claude Code internals come from third-party analyses of leaked source, not Anthropic documentation.
- Several 2026 preprints have no venue yet; treat them as claims, not results.
