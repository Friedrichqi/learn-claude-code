# The agent's end-to-end bottleneck (2026-09-10 to 2026-09-12)

One session, one arc: from "are eviction-driven re-reads the s15 lead's main bottleneck?" to
"what would a tiered memory change, does any of it work, and does anyone already ship the engine-side
version?" Companion to `tiered_memory_conclusion.md` (which folds these findings into the wider
argument). Terminology as in the 2026-09-09 deck: a *turn* is one lead activation, a *round* is one
agent-loop iteration (one model call plus its tool calls). Model: `glm-5.3-flash` via the z.ai
Anthropic-compatible endpoint, which prefix-caches automatically. Workload X3 throughout: read all 17
chapter READMEs (162,526 chars, ~41k tokens) and compare them.

---

## Q1. Do evicted files force new tool rounds, and is that the main bottleneck?

**Question.** When the lead's context budget is smaller than the files it needs, `micro_compact`
replaces old tool results with a pointer. Does the lead then spend its time re-fetching what it lost,
and is that, rather than decoding, what makes long runs long?

**Experiment.** X3 run at `CONTEXT_LIMIT` 50k (x2), 100k (x3) and 200k chars (x2). Every model call,
tool call and context rewrite traced; every acquisition classified as first read, README re-read
(evicted or still live), spill-file read, or grep on an already-read README. Per-round autopsy of the
worst 50k run.

**Results.**

| budget | rounds | model time | rounds with a re-acquisition | uncached prompt tokens |
|---:|---:|---:|---:|---:|
| 50k (2 runs) | 99, 45 | 794 s, 463 s | 76%, 67% | 917k, 459k |
| 100k (3 runs) | 120, 30, 41 | 995 s, 466 s, 500 s | 92%, 0%, 73% | 2,260k, 536k, 744k |
| 200k (2 runs) | 6, 7 | 138 s, 149 s | 0, 0 | 115k, 129k |

- The 50k autopsy: rounds 2-13 read the corpus once, rounds 16-73 are two more full sequential
  passes one README per round (17-31 output tokens each), rounds 74-97 re-locate sentences with
  grep. Of the 656 s gap to the 200k run: re-read rounds 42%, extra small acquisition rounds 14%,
  re-locate greps 14%, slower answer production 24%.
- The miss rate is independent of budget: 100% of re-acquisitions at 100k and in 50k r2, and 77 of
  95 in 50k r1, targeted a README that was already a pointer. This is the LRU sequential-scan case:
  the access pattern is a pass over 17 files followed by revisits, eviction is oldest-first, so any
  capacity below the working set misses on every revisit. The lead's own notes (27.7k output tokens
  in the 100k run) also live in the budget and are never micro-compacted, so tool results are what
  gets pushed out.
- The budget meant to save tokens multiplied them 4-20x and output tokens 2.4-5.6x.

**Answer.** Yes. Below the working set every run serialises into 5-20x more rounds, whatever the
strategy (re-read, spill-file read, grep, or note-taking); above it, the corpus is read in 3 rounds
and 84% of model time is spent writing the answer. It is a round-count problem, and the budget
threshold that matters is "does the whole working set fit", not "how much of it fits".

---

## Q2. What does one round cost, and where does the time go?

**Questions.** What share of a round is prefill? Why did 100k not beat 50k? What is the timing
breakdown of an average read loop?

**Experiment.** Per-call durations from the traces, plus a direct streaming probe of the endpoint on
2026-09-10 (fresh prompts of 0.4k-36k tokens, `max_tokens=8`) to separate fixed latency from
per-token prefill.

**Results.**

| component of a cheap tool round (median 4.7 s, ~30 output tokens) | share |
|---|---:|
| fixed per-call latency (network + provider queue; TTFT floor 4.6 s even for 371 tokens) | ~80% |
| prefill of the 9-19k uncached tokens (0.052 ms/token, ~19k tok/s) | ~10% |
| decode (90-150 tok/s) | ~5% |
| tool execution + harness bookkeeping (`read_file` 1 ms, bash 7 ms, context pipeline 20-50 ms) | <1% |

- Consistent check: the 100k run's cheap rounds carried twice the uncached tokens (19.4k vs 9.4k) at
  the same ~4.5 s.
- 100k did not help because 100k holds ~60% of the corpus and the sequential scan still misses on
  every revisit (Q1); round counts below the threshold are strategy-dominated (30-120).
- Correction to the 2026-09-09 estimate: the pooled regression there (0.42 ms/token, ~2,400 tok/s)
  was confounded by long-output calls and overstated per-token prefill ~8x. The prefill-reuse
  headroom shrinks by the same factor (X2 run 2 teammates: ~12 s, not 95 s). The conclusion is
  unchanged and stronger: reuse headroom is small, round count is the lever.

- Confirmed on 2026-09-13 across three other task classes (`latency_breakdown.md`, 24 runs): a
  regression over 446 agent calls gives 3.7 s fixed + 0.033 ms per uncached token + 17.4 ms per
  output token (r2 0.96), and a dedicated size sweep gives 0.0384 ms per token (26,000 tok/s), so
  the 0.052 ms/token above holds to within a factor of 1.4. The prefill *share* scales with the
  uncached tokens a call carries: about 10% at the 9-19k of these runs, 0.4-1.6% at the 0.9-3.8k of
  the file-Q&A, coding and math workloads, and seconds only above roughly 50k per call.

**Answer.** A round is a fixed-cost event. Skipping the prefill of repeated content saves at most
~10% of a round; removing the round saves all of it. Decoding dominates only the answer rounds.

---

## Q3. What would a tiered memory change?

**Question.** Mapping the harness onto HBM / DRAM / flash (live prompt / provider prefix cache and
spill files / repository), where do the costs sit and what are the candidate fixes? Note that the
harnesses already run the live prompt at 90-97% of the window, so "more HBM" alone changes nothing:
the bottleneck is the harness tier's eviction policy and the round trip per miss.

**Candidate ladder (brainstormed, then items 1, 2, 4 implemented and 7 investigated).**

| # | change | tier boundary | expected effect |
|---|---|---|---|
| 1 | batch read tool (`read_files`) | repo -> live prompt | fill the resident set in one transfer instead of 17 rounds |
| 2 | outline demotion (evicted result -> structural outline with `read_file` offsets) | live prompt metadata | targeted re-reads instead of full re-reads |
| 3 | size the resident set above the working set | live prompt capacity | no misses at all (Q1) |
| 4 | stable prefix (no timestamp, fixed archive marker, rare batched eviction) | live prompt -> provider cache | make every re-send a cache hit |
| 5 | teammate task-boundary compaction, respawn on low overlap | teammate tier | bound the N resident copies (see `teammate_input_redundancy.md`) |
| 6 | shared, position-stable file block per team | provider cache | prefill a shared file once |
| 7 | in-engine execution / KV splice for whitelisted fetch tools | engine | remove the round trip per miss |
| 8 | publish the tier costs in the tool descriptions or the system prompt | model-side routing | let the model avoid the expensive tier by itself |

**Item 8 was tested on 2026-09-14 and does not work in a mixed pool**
(`s15_integrated_harness/tool_cost_profile.md`). Annotating `read_file` at anything from 0.05 to
60 s, in stated seconds, in hardware terms or as the word "slow", leaves it at 45% of first moves
against a 44% baseline over 160 trials (p = 1.000); the model mentions the cost in 0-3% of those
replies, because it routes on task fit and never consults the price. It is not insensitive: between
two tools described as interchangeable the same numbers decide 97-100% of choices against a 53%
baseline, but as a tie-break that ignores magnitude (a 5x gap and a 7500x gap are
indistinguishable). Adding one sentence of objective -- "prefer the cheapest tool that can still
answer the question correctly" -- is what moves a mixed pool, 31% to 9%. In the real harness neither
arm had room to act: a value-lookup workload was already answered with `bash` and grep in 12 runs of
12, and a comprehension workload read the whole file in 12 runs of 12 in every arm, correctly. So
item 8 belongs only alongside item 6 or 7, where a hot and a cold route to the same bytes make the
two tools genuine substitutes.

---

## Q4. Do the batch read, outline demotion and stable prefix work?

**Experiment.** Three flags on `scripts/profile_run.py` that monkeypatch the loaded harness module
(the committed harness is identical across arms): `--batch-read` (12 files, char cap = 25% of the
budget, entries past the cap are skipped with a note), `--skeleton-demote` (outline with rebased
0-based offsets per file), `--stable-prefix`, plus `--deny-bash` so every acquisition goes through
the read path. 34-check offline test suite. Two matrices: shell allowed (phase 1/2; the batch and
outline arms there ran on an unfixed driver and are quarantined), and shell denied with the fixed
driver (phase 3, run after the provider's 5-hour usage cap reset). Answer quality: blind judging by
three judges for the shell-allowed arms; the shell-denied answers were not judged (this session only),
so chapter coverage and completion are the proxy.

**Results, shell allowed @50k (n=3 each) and @100k (n=1).**

| arm | rounds | model time | cache hit | uncached prompt tok | chapters | compactions | judge score /20 |
|---|---|---|---:|---:|---|---:|---:|
| baseline @50k | 69 (27-153) | 429 s | 15% | 633k | 17/17 | 0 | 18 |
| stable prefix @50k | 57 (51-66) | 437 s | **60%** | **219k** | 13/17 | 0 | **14** |
| outline @50k (unfixed driver) | 129 | 982 s | 17% | 1,258k | 17/17 | **4** | **19** |
| baseline @100k | 134 | 964 s | 9% | 2,576k | 17/17 | 0 | |
| batch @100k (unfixed driver) | **56** | **600 s** | 10% | 966k | 17/17 (45/52 files delivered) | 0 | |

**Results, shell denied, fixed driver (phase 3).**

| arm | budget | rounds | model time | status | cache hit | uncached tok | reads | re-acq rounds | compactions | notes |
|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---|
| baseline r1 | 50k | 319 | 2,247 s | timeout | 19% | 2,804k | 409 | 295 | 4 | paged READMEs in 45-line windows, 87% of re-reads windowed |
| baseline r2 | 50k | 14 | 343 s | done | 14% | 121k | 17 | 7 | 0 | took notes, read spill files 24x |
| batch r1 | 50k | 20 | 188 s | done | 18% | 178k | 26 | 9 | 0 | 17 batch calls, 26/45 files delivered, 6 batches truncated by the cap |
| batch r2 | 50k | 20 | 348 s | done | 15% | 190k | 30 | 11 | 1 | 9 calls, 10/31 delivered, 9 truncated |
| outline r1 | 50k | 242 | 3,973 s | timeout | 21% | 1,706k | **809** | 171 | **37** | 241 outlines, 146k output tokens, answer unfinished |
| outline r2 | 50k | 87 | 1,174 s | timeout | 20% | 656k | 252 | 66 | 11 | outlines cost 16-23k of the 50k budget (33-45%) |
| stable prefix | 50k | 11 | 336 s | done | **39%** | **66k** | 17 | 6 | 1 | 17/17 chapters |
| baseline | 200k | 5 | 182 s | done | 4% | 117k | 17 | 0 | 0 | 17/17 |
| batch | 200k | 7 | 150 s | done | 5% | 162k | 17 | 0 | 0 | 4 calls, 17/24 delivered, zero re-acquisition |

All shell-denied arms except the two outline runs and baseline r1 covered 17/17 chapters.

**Answers.**

- **Stable prefix is a transfer-path fix.** Cache hit 15% -> 60% (39% with shell denied), uncached
  prompt tokens down 3-5x, round count unchanged, because rounds are set by residency, not by what
  each re-send costs. It is the only arm that lost answer quality (14/20 vs 18/20; coverage 13/17 in
  two of three runs), and which sub-change causes that is still to be attributed.
- **Batching is bounded by residency, not by the tool.** Where the batch fits (100k, 200k) it halves
  rounds and time with full coverage and zero re-acquisition. At 50k the 12.5k-char cap truncates
  6-9 of the batches and delivers 32-58% of what was asked; the lead still finishes in 20 rounds
  with 17/17 coverage in both runs, against 14 and 319 for the shell-denied baseline. Under a shell
  ban batching is the most consistent 50k arm, but it does not remove the misses.
- **Outline demotion is a representation cost, and with the shell denied it is the worst arm.**
  Each evicted slot costs 856-1,131 chars against 201-538 for the bare pointer, 33-45% of the
  budget; that forces 11-37 summary compactions, and the lead loops outline -> offset re-read ->
  eviction (809 reads, 720 of them re-reads of evicted READMEs) until it times out. The map crowds
  out the payload and manufactures the rounds it was meant to save. The one place it paid off was
  answer quality with the shell available (19/20), from targeted re-reads.
- **Sizing is still the only zero-cost fix.** 200k: 5-7 rounds, 150-182 s, no re-acquisition, with
  or without the shell, with or without batching.

---

## Q5. Item 7, "in-engine KV splice for fetch tools": what is it exactly?

**Question.** If the harness cannot avoid the miss, can the serving side make the miss free?

**Two variants.**

- **7a, in-engine execution (append-only).** A gateway or the engine itself executes a whitelisted,
  read-only tool (`read_file`, `read_files`, `glob`) the moment the model emits the call, tokenises
  the result, appends it to the *live* sequence and continues decoding in the same request. Fresh
  positions, no RoPE re-rotation, no fidelity question; what it removes is the client round trip and,
  above all, the provider queue in front of the next request.
- **7b, KV splice.** Instead of prefilling the fetched bytes, splice a precomputed KV block of the
  file into the sequence. The s15 splice study puts the error at KL 0.045 (Qwen2.5-1.5B) and 0.085
  (7B) against recompute, 1x -> 2x the eviction edit's own error with model size, and at ~19k tok/s
  prefill it saves ~0.1 s per fault. Negative expected value unless the block is shared by many agents.

**What it buys, from the measurements.** A fault round on the hosted endpoint costs ~4.7 s, ~80% of
it fixed queue and TTFT floor; on the repo's self-hosted Qwen/vLLM traces the smallest call was
1.35 s including ~49 output tokens under 28-thread contention. So most of the round-trip cost is the
hosted queue; self-hosting with a stable prefix captures most of it, 7a the remainder plus guaranteed
KV residency across the tool pause, 7b only the prefill share.

---

## Q6. Is the whitelisted server-side file read already shipped by Claude Code or Codex?

**Question.** The idea sounds naive enough to be product, not research. Is it?

**Findings (documentation and source analyses, 2026-09-12).**

- **Claude Code and Codex both execute file tools on the client.** Codex is deliberately stateless
  (zero data retention); Claude Code's file, grep and bash tools run in the CLI process and the
  result goes back as the next request. Neither keeps the sequence live across the tool pause.
- **Hosted analogues execute tools server-side but still one request per step.** Anthropic's
  server-side tools (web search, web fetch, code execution) and the MCP connector run during the
  request; Managed Agents run the whole loop plus file operations in a per-session container, which
  is item 7 fully hosted. OpenAI's Agents API does the same with hosted tools. Remote MCP is the
  protocol form of "the tool lives on the server".
- **vLLM `agentic-api`** (Rust gateway, stateful Responses API, gateway-owned tools, Codex and
  Claude Code launchers; Anthropic Messages API planned) removes the *client* round trip, but the
  engine still sees a follow-up request per tool call and relies on prefix caching.
- **Keeping the sequence live across the tool pause is research**: Continuum (KV TTL pinning across
  tool calls), ThunderAgent, Autellix, KVCOMM (cross-agent KV sharing), speculative tool calling.

**Answer.** No. The whitelisted server-side read exists only inside hosted sandboxes, at one request
per step; nobody does it in the engine with the sequence kept live. Given Q2, the gain would be the
per-round queue floor, which is a serving property the harness cannot reach; on the harness side the
resident-set size already removes the rounds.

---

## Conclusion

The agent's end-to-end bottleneck is the number of fixed-cost rounds the lead spends re-acquiring
content its own budget evicted: a resident set larger than the working set removes those rounds
outright (5-20x), a stable prefix makes the remaining ones 3-5x cheaper to re-send without removing
any, batching helps only where the batch fits, outline metadata that competes with payload makes it
worse, and moving the fetch into the engine would remove the ~4.6 s queue floor per round, which no
product does today.

## Open items from this arc

1. Blind-judge the shell-denied answers (`answers_final/NB-*.txt`) with the existing three-judge
   script so the phase-3 table has a quality column.
2. Attribute the stable-prefix quality loss to its sub-changes (snip, archive marker, timestamp).
3. Re-run the outline arm with a budgeted outline (cap total outline chars at ~10% of the budget)
   to test whether the representation is wrong or only its size.
4. Batch arm at 50k with the cap raised to 50% of the budget, to separate the tool from the cap.

## Artifacts

- `s15_integrated_harness/file_read_reuse_profile.md` section 3.8: X3 budget sweep, autopsy, cost
  decomposition and the prefill correction.
- `s15_integrated_harness/context_intervention_profile.md`: intervention design, results, blind
  judging, compaction ladder; shell-denied matrix added 2026-09-12.
- `s15_integrated_harness/scripts/profile_run.py` (flags), `scripts/ix_analyze.py` (analyzer),
  `tests/test_context_interventions.py`.
- Traces: `s15_integrated_harness/traces/reuse_profiling/` (X3 sweep),
  `traces/context_interventions/` (valid runs at top level; `unfixed_driver/`, `quota_killed/`).
- `weekly_progress/090926/teammate_overlap.md` section 4: the original latency-headroom estimate and
  its correction.
- `s15_integrated_harness/tool_cost_profile.md` and `weekly_progress/091626/tool_cost_exposure.md`:
  ladder item 8 tested over three stages (893 single-shot trials, 80 multi-round runs, 24 s15
  sessions); `scripts/tool_cost_{probe,loop,harness,analyze}.py`, `profile_run.py --tool-cost`,
  traces in `traces/tool_cost/`.
