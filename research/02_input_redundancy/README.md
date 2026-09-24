# 02 · Input redundancy across teammates

> **Status:** complete. **Dates:** Part A runs 2026-09-08, with an X3 budget sweep 2026-09-10/11; Part B runs 2026-09-10 at two lead budgets (50k and 512k characters); weekly digests dated 2026-09-09 and 2026-09-16. **Models:** `glm-5.3-flash` via z.ai for every controlled run; the `Qwen/Qwen3.8-27B` (vLLM) stress trace of topic 01 re-analysed as an observational sample. **Commits:** f48d5b8 (Part A runs, driver, analyzer and report, 09-08), ef352ae (090926 digest, 09-09), e7351e6 (Part B runs, analyzer and report; `CONTEXT_LIMIT` 50k → 512k, 09-10), d67ab7f (Part B §6 rerun at 512k, Part A §3.8 sweep, the 090926 digest's correction and the 091626 digest, 09-10). **Presented:** 090926 slides 4, 10 and 11 (teammate_overlap §1, §4, §5) and slide 5 (the f48d5b8 runs), with slides 6–9 showing Part A results without a source footer; 091626 slide 9 (teammate_input_redundancy §3 A–E, §5–6), also listed on slide 4.

**Contents**

- Part A — File-read reuse in the s15 integrated harness: lead vs. teammates — path-level reuse; runs X0–X5, the observational Qwen run Q, the §3.8 X3 context-budget sweep (formerly `s15_integrated_harness/file_read_reuse_profile.md`)
  - A.W Additions from `weekly_progress/090926/teammate_overlap.md` — the week's questions, extra teammate findings, and the latency-headroom estimate (§4) with its 2026-09-10 correction
- Part B — Byte-level redundancy of file input across s15 teammates, by workload — PF-S / PF-P / DC-S / DC-P at a 50k lead budget, rerun at 512k in §6 (formerly `s15_integrated_harness/input_redundancy_profile.md`)
  - B.W Additions from `weekly_progress/091626/teammate_input_redundancy.md`
- Data inventory — every run directory, including the two aborted first attempts
- Source map
- Cleanup notes

**Files in this folder**

| Path | Purpose |
|---|---|
| `README.md` | this document |
| `file_read_reuse.py` | Part A analyzer: re-reads, cross-agent duplicates, compaction-driven re-reads, re-send multiplier and cache statistics from traces and `.inputs.jsonl` sidecars |
| `redundancy_workloads.py` | Part B workload prompts (PF-S, PF-P, DC-S, DC-P) and sequential runner; it launches `research/common/profile_run.py`, i.e. real provider sessions |
| `data/reuse_profiling/` | Part A traces: X0–X5 (2026-09-08) and the X3 budget sweep (2026-09-10/11); `aborted/` holds two interrupted first attempts |
| `data/redundancy_profiling/` | Part B at the 50k lead budget: 8 runs |
| `data/redundancy_profiling_ctx512k/` | Part B §6 at the 512k lead budget: 8 runs (labels `*-c512-r1/r2`), published side by side with the 50k arm |
| `data/input_redundancy_runs.md` | per-run reports of `research/common/input_redundancy.py`; frozen — `research/05_latency_breakdown/provider_probe.py` also reads it as filler text, so never edit it |

Shared, in `research/common/`: `profile_run.py` (non-interactive session driver that writes the `.inputs.jsonl` and `.reads.jsonl` sidecars) and `input_redundancy.py` (Part B's byte-level analyzer). The observational run Q, `s15_integrated_harness/traces/run_20260902T004901_584576Z_dc6a9685.jsonl`, stays in `s15_integrated_harness/` (topic 01, `research/01_tracing/README.md`).

---

# Part A — File-read reuse in the s15 integrated harness: lead vs. teammates
_Formerly `s15_integrated_harness/file_read_reuse_profile.md` (2026-09-08, f48d5b8; §3.8 added and §4 item 5 rewritten 2026-09-10, d67ab7f)._

Profiling question: in a long team run, how much of the file input that reaches the models is
*repeated* -- the same file read again by the same agent, read again by a different agent, or
re-sent to the provider on every call -- and which harness mechanisms cause it?

Short answer:

- **Repetition is high but mostly structural, not byte-identical waste.** Over the controlled
  runs, 59% of the lead's `read_file` calls and 94% of teammates' calls re-open a file that the
  same agent had already opened, yet only 6% (lead) and 0% (teammates) are exact repeats by the
  same agent. Teammates page through big files with `offset`/`limit` (in both X2 runs about 95%
  of teammate reads were another slice of the same 143 KB `code.py`), and the lead re-reads
  because compaction evicted the content.
- **The lead's re-reading is a compaction artefact.** 82% of all lead re-reads across runs
  happened after the context pipeline had shrunk the history (100% in the long-context run).
  Raising `CONTEXT_LIMIT` from 50k to 200k chars turned the same 17-README task from 100 model
  calls / 70 reads / 53 re-reads / 808 s into 7 calls / 17 reads / 0 re-reads / 168 s, with an
  answer of the same quality.
- **Cross-agent duplication is by design and is where the identical bytes are.** Children never
  see the lead's reads: 95% of teammate reads were of a file another teammate had already read
  (6% of a file the lead had read). Byte-identical duplicates are rare in count (lead 17%,
  teammates 3% of reads) but large in volume because they are whole-file reads: 19% of the bytes
  the lead fetched and 44% of the bytes teammates fetched had already been fetched earlier in the
  same run (X2 run 2: six teammates read all of `code.py` seven times). When tasks touch disjoint
  files (X5) the duplication is exactly zero.
- **At the token level the picture inverts.** Teammates re-send everything (no compaction): each
  byte they fetched was re-sent 12.6x on average (13.7x in X2 run 2, 15.7x in the Qwen run) and
  file content made up 73% of their request bytes -- but the provider's prefix cache absorbed 90%
  of teammate prompt tokens. The lead re-sends each fetched byte only ~3.4x (file content is ~29%
  of its requests) but gets only 17% cache hits, because the harness breaks the lead's prompt
  prefix on every call (per-second timestamp, rolling `micro_compact` rewrites, a fresh
  `snip_compact` archive marker, and summary compaction).

Everything below is reproducible with `research/common/profile_run.py` (runs one non-interactive session
and records a per-call input sidecar) and `research/02_input_redundancy/file_read_reuse.py` (computes the metrics from
the harness's own JSONL traces). Traces of the new runs are in `research/02_input_redundancy/data/reuse_profiling/`.

## 1. Where file input enters a model, and why it repeats (code facts)

| Mechanism | Lead | Teammate (thread) | One-shot (`task`) |
|---|---|---|---|
| `read_file` handler | `run_agent_read` -> `run_read`: whole file or an `offset/limit` window; no cache | `_run_read`, bound to the claimed task's cwd; refused until a task is claimed | `run_read` |
| `bash` as a reader (`grep`, `sed -n`, `head`) | allowed after an interactive `[y/N]` prompt on the main thread | **always denied**: `permission_hook` refuses shell approval off the main thread, so teammates can only read via `read_file`/`glob` | allowed only inside a foreground user turn |
| System prompt | rebuilt every call by `assemble_system_prompt`: 1,794 chars of fixed sections, then `Current time: <ISO seconds>`, skills catalog, memory catalog/records, MCP list (2,929 chars with no memories) | fixed string, no timestamp, no memory | fixed string |
| Tool schemas sent per call | 26 tools, 6,103 chars | 10 tools | 5 tools |
| Memory recall before each call | `update_context` -> s09 `load_memories`: once memory records exist, one extra model call per lead call selects records and their files are re-read and re-injected | none | none |
| Context pipeline before each call | `tool_result_budget` (200k) -> `snip_compact` (>50 messages: archive the middle, insert `[N messages archived at <new transcript path>]`) -> `micro_compact` when history > `CONTEXT_LIMIT` = 50,000 chars: every consumed tool result except the 3 newest becomes `[Earlier tool result saved at ...]` -> `fit_tool_results` -> `compact_history` (model-written summary replaces everything) | **none**: append-only until the provider rejects the prompt | none; 30-round cap |
| What a child returns | -- | final text via `MessageBus` (`result` then `idle_notification`) | final text as one `tool_result` |
| Shared state | task board (`.tasks/`), mailboxes, workspace, model client | same | same |

Consequences that drive repetition:

1. **Children never see the lead's reads.** A teammate starts from its assignment prompt only, so
   every file its task touches is opened again inside that thread. Three teammates on one file
   means three private copies of it in three contexts, plus the lead's own copy.
2. **The lead forgets files quickly.** 50,000 chars is roughly 12k tokens. As soon as the history
   is over that budget, `micro_compact` replaces every consumed tool result but the 3 newest with a
   path placeholder. Measured lifetime of a `read_file` result in the lead's context: median 5
   calls in the long-context run (max 11), 1-3 calls in the code-inspection run. To use the file
   again the lead re-reads it, re-greps it, or reads the harness's own spill file under
   `.task_outputs/tool-results/`.
3. **Every model call re-sends the whole history.** The only saving is provider-side prefix
   caching of a byte-identical prefix. The provider configured here (`glm-5.3-flash` via z.ai)
   caches automatically in 64-token blocks with roughly a 1k-token minimum; a probe with two
   identical requests reported 3,904 of 3,944 tokens as cache reads. Teammates' requests are
   append-only, so they hit. The lead's requests are rewritten between calls (section 3.5).
   [cleanup note: no committed script or output records this probe; the same holds for the lead-request replay in §3.5 and the cache-lifetime probe in §3.7. `research/05_latency_breakdown/provider_probe.py` is a later, separate probe (added 2026-09-15).]
4. **Teammates paginate large files** (`offset`/`limit`), which shows up as many `read_file`
   calls on the same path with different content. That is not redundancy, so every table below
   separates *same path* from *byte-identical content* (SHA-256 of the returned text, recorded by
   the trace).

## 2. Method

- **Traces.** Each run writes the harness's JSONL trace (`trace_runtime.py`). Every `tool_end`
  carries tool, arguments (path/offset/limit or command), status, and the result's character
  count and SHA-256, so repeated content is detectable without storing it.
- **Driver.** `research/common/profile_run.py` runs one session non-interactively from the repository
  root: it auto-approves shell prompts but denies mutating commands and `write_file`/`edit_file`
  (read-only policy; denials are counted), wipes `.memory/.tasks/.mailboxes/.transcripts/
  .task_outputs` per run, waits until teammates are idle, shuts them down, and writes a sidecar
  `<trace>.inputs.jsonl` with, for every model call, the characters of `read_file`/`bash`/other
  tool results present in the request, the number of compaction placeholders, and provider usage.
  It also retries provider 429s for every agent (the harness retries only lead calls, 3x with
  sub-second delays; a teammate dies on the first 429). Two interventions are flags:
  `--no-timestamp` and `--context-limit`.
- **Analyzer.** `research/02_input_redundancy/file_read_reuse.py <trace|dir> [--calls] [--exclude REGEX]` pairs tool
  spans, normalises paths to the run cwd, and reports per agent kind: reads, distinct files,
  same-agent re-reads (identical vs other range), cross-agent duplicates (same path / identical
  content), lead re-reads that follow a history shrink, spill-file reads, bash-as-reader
  repetition, prompt-token and cache statistics, and the sidecar-based re-send multiplier.
- **Provider.** `glm-5.3-flash` through z.ai's Anthropic-compatible endpoint (the configured
  `.env`). `input_tokens` from this provider excludes cached tokens, so prompt size is reported as
  `input_tokens + cache_read_input_tokens`. The committed traces used `Qwen/Qwen3.8-27B` on a
  local vLLM, which reports no cache fields.

Experiments (fresh process each; prompts forbid file modification; WORKDIR = repo root):

| Id | Shape | Prompt (abridged) | Runs |
|---|---|---|---|
| X0 | no tools | "reply with exactly: baseline complete" | 1 |
| X1 | lead only, tool-heavy | inspect `code.py` + `trace_runtime.py`, cite every model-call and tool-dispatch boundary, no delegation | 2, plus 1 with `--no-timestamp` |
| X2 | lead + 3 teammates on the **same** file | three task-board items about `code.py` (model calls / tool dispatch / compaction), one teammate each, synthesize | 2 (run 2 spawned a second wave of 3) |
| X3 | lead only, long context | read all 17 chapter READMEs and compare context, delegation, stopping | 1, plus 1 with `--context-limit 200000` |
| X4 | lead + one-shot subagent | one-shot summarises `trace_runtime.py`, lead verifies by reading the same file | 1 |
| X5 | lead + 3 teammates on **disjoint** files | summarise s09 / s10 / s13 `code.py`, one teammate each | 1 |
| Q | observational | committed 28-teammate Qwen stress run (`s15_integrated_harness/traces/run_20260902T004901_..._dc6a9685.jsonl`), analysed with `--exclude 'traces/'` to drop its self-observation loop | 1 |

Caveats: model choice is stochastic, so single-run numbers are indicative; the read-only denial
policy fired 3 times in total (one was a false positive on a `>` inside a grep pattern); z.ai
returned 429s whenever more than ~5 sessions ran concurrently, so runs were paced and the first
X2 attempt (a teammate killed by a 429) was discarded; both X2 runs stopped at the driver's 1,500 s
ceiling because the lead kept re-engaging (run 1) or re-spawning (run 2) teammates, so their
lead numbers include that coordination chatter.

## 3. Results

### 3.1 Cross-run summary

Same-agent re-reads are split into *identical* (same path and same returned bytes) and *other
range/content* (pagination or changed file). Cross-agent columns count reads of a path another
agent had already read, and reads whose bytes another agent had already fetched. "Re-send x" is
the number of times each byte fetched by `read_file` was sent to the model (sidecar; for the Qwen
run computed from the trace, valid because teammates never compact). "Cache hit" is
`cache_read / (input + cache_read)` as reported by the provider.

<!-- RESULTS_TABLE -->
| run | status / wall | kind | agents | model calls | reads | distinct files | same-agent re-read: identical / other range | cross-agent: same file / identical | re-reads after compaction shrink | spill-file reads | read_file share of request chars | re-send x | cache hit |
|---|---|---|---:|---:|---:|---:|---|---|---|---:|---:|---:|---:|
| X1-lead-inspect-r1 | completed / 426s | lead | 1 | 27 | 3 | 2 | 0 / 1 | 0 / 0 | 100% | 0 | 7% | 2.0x | 17% |
| X3-long-context-r1 | completed / 808s | lead | 1 | 100 | 70 | 17 | 11 / 42 | 0 / 0 | 100% | 0 | 49% | 4.5x | 17% |
| X2-team-shared-file-r1 | timeout / 1627s | lead | 1 | 148 | 28 | 5 | 0 / 23 | 1 / 0 | 96% | 11 | 15% | 2.4x | 19% |
| X2-team-shared-file-r1 | timeout / 1627s | teammate | 3 | 59 | 70 | 1 | 0 / 67 | 2 / 1 | - | 0 | 64% | 12.9x | 90% |
| X1-lead-inspect-r2 | completed / 911s | lead | 1 | 71 | 44 | 18 | 0 / 26 | 0 / 13 | 50% | 17 | 29% | 3.0x | 15% |
| X4-one-shot-r1 | completed / 260s | lead | 1 | 8 | 1 | 1 | 0 / 0 | 1 / 1 | - | 0 | 34% | 3.0x | 19% |
| X4-one-shot-r1 | completed / 260s | one_shot | 1 | 2 | 1 | 1 | 0 / 0 | 0 / 0 | - | 0 | 88% | 1.0x | 0% |
| X1-lead-inspect-notime-r1 | completed / 786s | lead | 1 | 74 | 34 | 20 | 0 / 14 | 0 / 8 | 50% | 18 | 20% | 3.0x | 19% |
| X5-team-disjoint-files-r1 | completed / 237s | lead | 1 | 15 | 0 | 0 | 0 / 0 | 0 / 0 | - | 0 | 0% | - | 28% |
| X5-team-disjoint-files-r1 | completed / 237s | teammate | 3 | 12 | 3 | 3 | 0 / 0 | 0 / 0 | - | 0 | 80% | 2.0x | 51% |
| X3-long-context-bigctx-r1 | completed / 168s | lead | 1 | 7 | 17 | 17 | 0 / 0 | 0 / 0 | - | 0 | 83% | 2.7x | 5% |
| X2-team-shared-file-r2 | timeout / 1519s | lead | 1 | 43 | 0 | 0 | 0 / 0 | 0 / 0 | - | 0 | 0% | - | 8% |
| X2-team-shared-file-r2 | timeout / 1519s | teammate | 6 | 95 | 178 | 3 | 0 / 168 | 7 / 6 | - | 0 | 78% | 13.7x | 91% |
| Q: Qwen 28-teammate stress run | None / 11538s | lead | 1 | 152 | 40 | 29 | 7 / 4 | 7 / 9 | 82% | 7 | - | - | n/r |
| Q: Qwen 28-teammate stress run | None / 11538s | teammate | 28 | 525 | 373 | 30 | 3 / 235 | 105 / 73 | - | 0 | - | 15.7x | n/r |
| Q: Qwen 28-teammate stress run | None / 11538s | one_shot | 1 | 24 | 28 | 1 | 0 / 27 | 1 / 0 | - | 0 | - | 14.8x | n/r |
<!-- /RESULTS_TABLE -->

Aggregate over the controlled runs (X1-X5 and variants, glm-5.3-flash):

<!-- AGGREGATE_TABLE -->
| kind | reads | same-agent identical re-reads | same-agent other-range re-reads | cross-agent same path | cross-agent identical | chars fetched | identical-content chars |
|---|---:|---:|---:|---:|---:|---:|---:|
| lead | 197 | 11 (5.6%) | 106 (53.8%) | 2 (1.0%) | 22 (11.2%) | 1,355,707 | 261,149 (19.3%) |
| teammate | 251 | 0 (0.0%) | 235 (93.6%) | 9 (3.6%) | 7 (2.8%) | 1,684,032 | 732,021 (43.5%) |
| one_shot | 1 | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) | 21,821 | 0 (0.0%) |
teammate reads of a file the lead had already read: 15/251 (6.0%); of a file another teammate had already read: 238/251 (94.8%)
lead re-reads that followed a compaction shrink: 96/117 (82.1%)
| kind | model calls | prompt tokens (uncached + cached) | prefix-repeat est. | provider cache_read (hit rate) |
|---|---:|---:|---:|---:|
| lead | 495 | 4,200,035 | 2,230,838 (53.1%) | 705,216 (16.8%) |
| teammate | 166 | 6,310,890 | 5,697,842 (90.3%) | 5,689,920 (90.2%) |
| one_shot | 2 | 6,165 | 607 (9.8%) | 0 (0.0%) |
| kind | read_file chars in requests | request chars | share | bytes fetched | re-send multiplier |
|---|---:|---:|---:|---:|---:|
| lead | 4,620,678 | 15,963,011 | 28.9% | 1,355,707 | 3.4x |
| teammate | 21,269,464 | 28,959,192 | 73.4% | 1,684,032 | 12.6x |
| one_shot | 21,821 | 24,886 | 87.7% | 21,821 | 1.0x |
<!-- /AGGREGATE_TABLE -->

### 3.2 Lead-only inspection (X1) -- compaction turns reads into re-greps

The lead reads mostly through bash: 37-49 `grep`/`sed -n` commands per run versus 3-44
`read_file` calls, and 88-92% of those bash reads targeted a file it had already read. Per-call
composition from the sidecar (run 1) shows the mechanism: the 21,821-char `trace_runtime.py`
read enters the request at call 3 and is gone by call 7 (placeholder count 0 -> 29 over 18 calls);
bash output then dominates the request until a summary compaction at call 19 (53k chars ->
16k-token summary), after which the lead re-derives its findings with fresh greps ("Line numbers
shifted slightly from my earlier notes -- let me re-verify") before answering. Run 2 read
`code.py` 23 times in different line ranges and read back 17 of its own spilled tool outputs.
The final reports were accurate in both runs; the repetition cost tokens and time, not quality.

### 3.3 Long context (X3) -- the 50k limit makes the lead read each README 3-8 times

Default limit: 100 model calls, 70 successful reads of 17 files (s13 README 8x, s08 6x), 53
same-agent re-reads, every one of them after a history shrink (88 shrink events, 0 summary
compactions). From call 8 the message count is pinned at 50-51 by `snip_compact` and the request
oscillates between 36k and 53k chars; a read survives a median of 5 calls. File content was 49%
of all request bytes and each fetched byte was re-sent 4.5x. With `--context-limit 200000` the
same prompt took 7 calls, 17 reads (one per README), 0 re-reads, 168 s instead of 808 s, and
produced a comparable comparison table.

### 3.4 Teams -- shared file (X2) vs. disjoint files (X5), one-shot (X4)

X2 run 1: the three teammates read `code.py` 70 times in total (67 same-agent re-reads, all
different slices; 99% of teammate reads were of the file another teammate had already read, 21% of
the file the lead had read), and the lead read the same file 28 times (23 re-reads, 22 after
shrinks) plus 11 spill files. Teammates have no compaction: their prompts reached 82k tokens,
file content was 64% of their request bytes, and each fetched byte was re-sent 12.9x -- but 90%
of their prompt tokens were cache hits. The lead: 148 model calls (71 lead turns, 68 memory
recall selections costing 388 s, 7 memory extractions, 2 summary compactions), 19% cache hits.
X2 run 2: the lead delegated everything (0 reads of its own), shut the first three teammates down
cleanly through the shutdown protocol after their results arrived (about 17 min), then re-created
the same three tasks and spawned a second wave of three teammates. Six teammates read `code.py`
168 times, including seven whole-file reads of which six were byte-identical to an earlier one
(66% of the run's fetched bytes); each fetched byte was re-sent 13.7x, file content was 78% of
teammate request bytes (peak prompt 76k tokens), and 91% of teammate prompt tokens were cache
hits against 8% for the lead. Both X2 runs ran until the driver's 1,500 s ceiling.
X5 (disjoint files): 3 reads total, zero duplication, 237 s, clean shutdown by the lead. X4: the
lead re-read the exact 21,821-char file its one-shot had just summarised (100% of the child's
input duplicated), because the child returns only text.

### 3.5 Why the lead's prompt cache misses (replay evidence)

Consecutive lead requests were replayed with a fake client and compared byte-for-byte:

| Variant | Identical prefix between consecutive requests | Divergence point |
|---|---|---|
| default harness | 7,966 chars = 14-87% of the previous request (constant) | `Current time: <second>` in the system prompt |
| `--no-timestamp`, history under 50k chars | 100% (perfectly append-only) | -- |
| `--no-timestamp`, after summary compaction | 16% | `compact_history` replaced the history |
| `--no-timestamp`, `CONTEXT_LIMIT` 20k (micro_compact active) | 37-45% | the oldest not-yet-replaced tool result is rewritten to a placeholder each call |
| `--no-timestamp`, >50 messages (snip_compact active) | ~51% | `[N messages archived at <new transcript path>]` marker changes every call |

7,966 chars is the 26-tool schema (6,103) plus the system prompt up to the timestamp (1,794),
which is exactly the flat 2,112-2,176 cached tokens seen on nearly every lead call in every run.
Removing the timestamp alone (X1 no-timestamp run) raised the hit rate only from 17% to 19%,
because after a few calls `micro_compact` and `snip_compact` rewrite the prefix anyway; teammates,
whose history is never rewritten, reach 84-90%.
[cleanup note: no committed script or output records this replay. The teammate range is not reproduced exactly by the committed traces either: per-teammate cache hit rates in the X2 runs span 79-94% (run 1: 89-90%; run 2: 79-94%), and the A.W addition (was §3) gives 84-94%.]

### 3.6 Observational: the committed 28-teammate Qwen run

Excluding the run's self-observation of its own trace file: 441 reads over 52 files; teammates
made 373 reads of 30 files (235 pagination re-reads, 105 cross-agent same-file, 70 byte-identical
to an earlier read; 91% of teammate reads were of files another teammate had already read); the
lead made 40 reads with 11 re-reads, 9 of them after a compaction shrink (82%). Teammates re-sent
each fetched byte 15.7x and accumulated 34.6M prompt tokens against the lead's 1.4M; five teammate
contexts grew past 250k tokens and the provider rejected them (`maximum context length is 262144
tokens`), which is the failure mode of "no compaction for children". 105 of 109 bash calls were
denied (all teammate calls), forcing `read_file` pagination.
[cleanup note: the committed trace has six teammate calls rejected at the 262,144-token limit — trace-analyst, trace-cli, trace-view-dev, stats-reviewer, usage-doc and report-reviewer (topic 01, note under its §5); 090926 slide 8 already says "The report counts five 262k deaths (error notifications the lead received); the trace shows six rejected teammates." The same "five" recurs in §3.7 and in the A.W addition (was §3).]

### 3.7 What each agent actually holds, and for how long

Line coverage of `code.py` (3,710 lines) reconstructed from the recorded `read_file` ranges and
`sed -n` windows: the lead extracts fractions, teammates load the whole file.

| run | agent | slices read | lines covered |
|---|---|---:|---:|
| X1 run 1 | lead | 19 | 32% |
| X1 run 2 | lead | 23 | 44% |
| X2 run 1 | lead | 17 | 90% |
| X2 run 1 | each of 3 teammates | 17-28 | 100% |
| X2 run 2 | each of 3 first-wave teammates | 51-61 | 100% (plus whole-file single reads) |

The lead's slices are transient (median lifetime 5 calls before a compaction placeholder replaces
them). A teammate's history is never released between tasks: the message list is created once per
thread (`code.py:1647`), survives the work loop (`code.py:1717`), and when the idle teammate
auto-claims the next task the new assignment is appended to it (`code.py:1788`). It is freed only
when the thread exits (lead `request_shutdown`, error, or process exit). In the Qwen run, prompt
size at the first call of each successive task:

| teammate | task 1 | task 2 | task 3 | task 4 |
|---|---:|---:|---:|---:|
| image-auditor | 1,193 tok | 51,643 | 101,985 | 135,572 |
| trace-analyst | 1,358 | 1,358 (re-claim) | 207,516 | |
| link-auditor | 1,439 | 108,479 | 149,393 | |
| test-reviewer | 3,330 | 152,089 | 182,840 | |

Across that run, 11.9M of the 34.6M teammate prompt tokens (35%; 47% for the nine multi-task
teammates) were history of tasks already completed, re-sent on every call of the next task, and
five teammate contexts eventually exceeded the provider's 262k-token limit. One-shot subagents are
the exception: their list is local to `spawn_subagent` (`code.py:2108`) and is dropped when only
the final text returns (`code.py:2161`). Provider-side prompt/KV caches are outside the harness:
entries for an idle or shut-down teammate simply expire there (see the lifetime probe below), and
nothing transfers to the lead or to another teammate because their prefixes differ.
[cleanup note: six in the committed trace; see the note under §3.6.]

Provider cache lifetime probe (one 4,574-token prompt, re-sent unchanged after idle gaps):

| idle gap since previous call | cache_read | hit |
|---|---:|---:|
| t=0 fresh | 0 | 0% |
| after 60s idle | 4544 | 99% |
| after 180s idle | 4544 | 99% |
| after 300s idle | 4544 | 99% |
| after 600s idle | 0 | 0% |

So an idle or finished teammate's cached prefix survives about five minutes of idleness at the
provider (still 99% after 300 s, gone after 600 s; consistent with a ~5-minute TTL refreshed on
use). It is neither freed nor reused by the harness, and only that teammate's own later calls
(same prefix) can hit it; a teammate that idles longer than the TTL re-pays the full prompt on its
next task.
[cleanup note: no committed script or output records this probe.]

### 3.8 X3 budget sweep: the small budget costs rounds, not decode (five runs)

The X3 prompt (read all 17 chapter READMEs, 162,526 chars ~ 41k tokens, and compare them) was run
at three `CONTEXT_LIMIT` values: 50k twice, 100k three times, 200k twice. Wall time includes the profiling
driver's 429 back-off (34-65 s in three runs), so model time (sum of successful lead calls) is the
fairer column. "Re-acquisition" = any way the lead fetched content it had already fetched once:
re-reading the README, reading the harness's own spill file under `.task_outputs/tool-results/`
(the path the compaction placeholder points to), or grepping a README it had already read.

| budget (chars) | run | lead rounds | model time | wall | README re-reads | spill-file reads | greps on already-read READMEs | rounds containing a re-acquisition | uncached prompt tokens | output tokens | live READMEs (median / max) |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 50,000 | r1 | 99 | 794 s | 808 s | 53 | 0 | 34 | 75 (76%) | 916,664 | 13,263 | 3 / 7 |
| 50,000 | r2 | 45 | 463 s | 546 s | 0 | 13 | 31 | 30 (67%) | 458,956 | 13,716 | 0 / 6 |
| 100,000 | r1 | 120 | 995 s | 1,114 s | 0 | 24 | 111 | 110 (92%) | 2,259,806 | 27,655 | 0 / 12 |
| 100,000 | r2 | 30 | 466 s | 492 s | 0 | 0 | 0 | 0 (0%) | 535,552 | 16,677 | 0 / 10 |
| 100,000 | r3 | 41 | 500 s | 546 s | 0 | 15 | 34 | 30 (73%) | 743,891 | 15,173 | 0 / 9 |
| 200,000 | r1 | 6 | 138 s | 168 s | 0 | 0 | 0 | 0 | 115,411 | 4,931 | 9 / 17 |
| 200,000 | r2 | 7 | 149 s | 180 s | 0 | 0 | 0 | 0 | 128,731 | 5,591 | 8 / 17 |

Per-round autopsy of the 50k r1 run (99 rounds): rounds 2-13 read the corpus once (1-2 files per
round); rounds 16-45 and 47-73 are two more full sequential passes s01 -> s17, one README per
round, 17-31 output tokens per round; rounds 74-97 are grep/sed rounds that re-locate sentences
for the "quote one sentence" requirement; the answer is written in rounds 92-99. For 35 of the 53
re-reads the file's result had already been replaced by a placeholder or a 1,000-char preview at
the time of the request; the other 18 fetched the second half of a README whose first half had
been fetched one round earlier. Of the 656 s model-time gap to the 200k r1 run: re-read rounds
277 s (42%), extra small acquisition rounds 93 s (14%), re-locate greps 89 s (14%), slower answer
production 160 s (24%), other 36 s. The 78 cheap tool rounds cost a median of 4.7 s each with a
median of 30 output tokens. A direct streaming probe of the endpoint (2026-09-10, fresh prompts of
0.4k-36k tokens, `max_tokens=8`) puts time-to-first-token at 4.6 s + 0.052 ms per uncached token
(prefill ~19k tok/s) and decode at ~90-150 tok/s, so a cheap round is ~80% fixed per-call latency
(network + provider queue), ~10% prefill of the 9-19k-token uncached context, ~5% decode, and
under 1% tool execution and harness bookkeeping (read_file 1 ms, bash 7 ms, context pipeline and
bookkeeping 20-50 ms per round). Consistently, the 100k run's cheap rounds carried twice the
uncached tokens (19.4k vs 9.4k) at the same ~4.5 s. The pooled regression used in the earlier
latency-headroom estimate (0.42 ms/token, ~2,400 tok/s) was confounded by long-output calls and
overstates per-token prefill about 8x; the prefill-reuse headroom quoted there shrinks accordingly.
The 200k runs read the corpus in 3 rounds (6+6+5 files) and spend 84% of their model time
decoding the answer.
[cleanup note: the 2026-09-10 streaming probe has no committed script or output; `research/05_latency_breakdown/provider_probe.py` (added 2026-09-15) is a later probe of the same endpoint.]

Takeaways:

- **Below the working set every run serialises; above it none does.** With 50k or 100k the lead
  needed 30-120 rounds and 463-995 s of model time (3.1-7.2x the 200k runs); with 200k it needed
  6-7 rounds and 138-149 s, with zero re-acquisitions, in both replications. The threshold that
  matters is whether the whole working set fits: the 100k arm (holds ~60% of the corpus) was not
  consistently better than 50k (~25%): 120 / 30 / 41 rounds against 99 / 45. Even the best
  sub-working-set run (100k r2, zero re-acquisitions) took 30 rounds and 3.2x the time, because with
  the files evicted (median live READMEs 0) it worked chapter by chapter through its own notes
  instead of seeing everything once and writing the answer in two rounds.
- **Why a bigger-but-insufficient budget does not help.** `micro_compact` evicts consumed tool
  results oldest-first (keeping the 3 newest) whenever the history exceeds the budget, and the
  model's access pattern is a sequential pass over the 17 files followed by revisits. That is the
  LRU sequential-scan case: with a capacity below the working set every revisit misses whether the
  capacity is 25% or 60% of it. Measured: 100% of the 155 re-acquisitions in the 100k run and of
  the 44 in 50k r2 targeted a README that was already a placeholder; in 50k r1, 77 of 95 (the
  other 18 were second windows of a file just re-read). After the first pass the median number of
  live READMEs was 3 (50k r1), 0 (50k r2) and 0 (100k), because the model's own drafts and notes
  (27.7k output tokens in the 100k run) also occupy the budget and are never micro-compacted, so
  the tool results are what gets pushed out.
- **The modality varies, the cost does not.** One run re-read the READMEs (53 times), three read
  spill files (13-24) and grepped (31-111), one took notes instead and never re-acquired. All are
  extra rounds: ~4.5-5 s each when they are tool calls, more when they carry note-taking output.
- **It is a round-count problem, not a decoding or prefill problem.** The extra rounds emit 17-90
  tokens and their cost is ~80% fixed per-call latency; prefill of the 9-19k-token uncached context
  is ~10% and tool execution is negligible. Decoding dominates only the answer rounds (~120 s).
- **The budget meant to save tokens multiplied them.** 459k-2.26M uncached prompt tokens below the
  working set versus 115-129k above it (4-20x), and 2.4-5.6x more output tokens.
- Variance below the threshold is large (30-120 rounds, strategy-dependent); above it tight
  (6-7 rounds). HEAD has since raised the default to 512,000 chars (128k tokens x 4), above this
  working set.

## 4. Qualitative conclusions

1. **Path-level repetition is the norm, byte-level repetition is rare in count.** Roughly six
   tenths of lead reads and nine tenths of teammate reads re-open an already-opened file, but
   exact repeats by the same agent are under 6%, and byte-identical reads overall are 17% (lead)
   and 3% (teammates) of reads. Most "re-reads" are slices of large files or post-compaction
   refreshes; the identical ones are whole-file reads by a second agent.
2. **The lead and the teammates repeat for opposite reasons.** The lead repeats because the
   harness *forgets* (50k-char compaction budget, rolling placeholder rewrites, summary compaction,
   spill files that it then reads back). Teammates repeat because the harness *isolates* (no shared
   history, no shell, so each one pages through the same file) and never forget, so the cost shows
   up as ever-growing prompts instead of re-reads, and the history of a finished task is carried
   into the next one.
3. **Reuse the provider can exploit is where the harness spends the least effort.** Teammates'
   append-only prompts get ~90% cache hits with zero harness support; the lead's carefully
   compacted prompt gets ~17% because every compaction step also invalidates the cache prefix.
4. **Duplication scales with task overlap, not with team size.** Three teammates on one file
   tripled the file's presence (plus the lead's copy); three teammates on three files duplicated
   nothing. The one-shot pattern duplicates whenever the parent verifies the child's reading.
5. **For this workload the default limit is counter-productive.** Across five X3 runs, budgets
   below the 162k-char working set needed 45-120 rounds (67-92% of them re-acquiring evicted
   content) and 463-995 s of model time; budgets above it needed 6-7 rounds and 138-149 s, with no
   loss in answer quality (section 3.8).
   [cleanup note: the §3.8 table has seven X3 runs; the five below the working set needed 30-120 rounds (0-92% of them re-acquiring) and 463-995 s of model time. "45-120 rounds (67-92%)" leaves out 100k r2 (30 rounds, 0%) and 100k r3 (41 rounds).]

## 5. What would reduce the repetition

- **Raise or tokenise `CONTEXT_LIMIT`.** 50,000 chars (~12k tokens) is a tenth of the models'
  windows. A limit near the model's real budget removes almost all lead re-reads (X3: 53 -> 0).
- **Keep the lead's prefix stable.** Move `Current time` into the user turn or round it to the
  hour; make `snip_compact` reuse the previous archive marker instead of writing a new transcript
  path each call; let `micro_compact` replace old results in one batch when it triggers rather
  than one more result per call; on the Anthropic API add `cache_control` breakpoints after the
  tools/system block and before the newest messages.
- **Release or compact a teammate's history at task boundaries.** A teammate that claims a second
  task carries the whole first task in every call (35% of all teammate prompt tokens in the Qwen
  run). Resetting the list to the assignment prompt plus a short summary of the finished task, or
  compacting in one large step at the boundary, keeps the prefix cache effective and avoids the
  provider context-limit deaths seen in that run.
- **Let teammates read selectively.** Off-main-thread shell is always denied, so a teammate can
  only page through whole files. A `grep`-style search tool, or approving read-only shell
  commands for teammates, would cut the 70 whole-file reads of X2 dramatically.
- **Pass what the lead already learned.** Task descriptions could carry the relevant line ranges
  or excerpts; a one-shot could return the ranges it inspected so the parent verifies a slice
  rather than re-reading the whole file.
- **Cheaper memory recall.** Once memory records exist, every lead call pays a selection model
  call (68 calls, 388 s in X2 run 1); a keyword pre-filter or a per-turn selection would remove
  most of them.

## Appendix: reproduce

```sh
# one profiling session (from the repository root); trace + <trace>.inputs.jsonl land in --trace-dir
python3 research/common/profile_run.py --label X3 \
  --prompt "Read all chapter README.md files from s01_agent_loop through s17_goal_loop ... do not create or modify any files."
python3 research/common/profile_run.py --label X3-big --context-limit 200000 --prompt "..."
python3 research/common/profile_run.py --label X1-notime --no-timestamp --prompt "..."

# metrics for one trace (with per-call request composition) or a directory of traces
python3 research/02_input_redundancy/file_read_reuse.py research/02_input_redundancy/data/reuse_profiling --calls
python3 research/02_input_redundancy/file_read_reuse.py s15_integrated_harness/traces/run_20260902T004901_584576Z_dc6a9685.jsonl --exclude 'traces/'
```

## A.W Additions from `weekly_progress/090926/teammate_overlap.md` (2026-09-09)

Blocks of the weekly digest that the report above does not contain, verbatim and in the digest's order. Everything else in the digest repeats Part A (see the source map). Section references inside these blocks are to the digest's own sections.

_(was §1 "Questions")_

1. In a long team run, how much of the file input reaching the models is **repeated** (same file read again by the same agent, read again by another agent, or re-sent to the provider), and which harness mechanisms cause it?
2. Do the lead and the teammates open the **same files**, and do they load only **fractions** of them?
3. After a teammate finishes a task, is its **context / KV cache freed**?
4. Why are **byte-identical** duplicate reads rare while the same file is read many times? How do slices overlap?
5. Do teammates have to read **whole files**, do they page incrementally, and do they ever stop midway?
6. Where does the lead's **50k-char budget** come from, and could a PagedAttention-like **window scheme** remove the repetition?
7. If the prefill of all repeated file content could be skipped, what is the **upper bound on latency saved**, and how much of end-to-end latency does the repetition cost today?

_(was §2 "Experiments", instrumentation paragraph)_

Instrumentation: the harness's own JSONL trace (every read carries path, offset/limit, size, SHA-256 of the result) plus a per-model-call sidecar (which tool results, how many chars, compaction placeholders, cache usage). Extra probes: identical-request cache probe, growing-conversation probes, cache-lifetime probe (idle 60/180/300/600 s), and a byte-level replay of consecutive lead requests under timestamp / no-timestamp / compaction variants.

_(was §3 "Qualitative results", the seven bullets)_

- **The lead repeats because the harness forgets.** `CONTEXT_LIMIT = 50000` chars (~12k tokens, `code.py:99`, harness-side, not an API limit) evicts a read after a median of 5 calls; 82% of lead re-reads follow a compaction shrink. Raising it to 200k turned X3 from 100 calls / 70 reads / 53 re-reads / 808 s into 7 calls / 17 reads / 0 re-reads / 168 s, same answer quality.
- **Teammates repeat because the harness isolates them.** No shared history, and shell is denied off the main thread, so a teammate cannot search: it pages through the whole file. Every X2 teammate reached 100% line coverage of `code.py` (lead with `grep`: 32-44% in X1). Over 47 teammate-file pairs, 81% reached >=90% coverage; the partial cases were all peripheral files (5 "page from the top then stop", 4 targeted lookups). When tasks touch disjoint files (X5) duplication is exactly zero.
- **Slices are nearly disjoint within one teammate, duplicated across teammates.** 174 reads of one file used 167 distinct windows; identical reads are 3% by count but 66% by bytes (whole-file reads). Line-level: each teammate holds 1.0-1.3 copies, the team 4.3x (run 1) and 6.8x (run 2); the file was transmitted to the model ~44 and ~103 times. Self re-fetches (1-24%) are re-zooms for line numbers (`read_file` returns none) and second-pass region reads.
- **Every window stays.** Windows are appended verbatim and never removed for teammates; the whole file ends up in context whether read at once or in pages (paging just costs one model call per page). The provider cache saves compute, not context.
- **A teammate's context is never released between tasks.** The message list persists across auto-claimed tasks (`code.py:1647/1788`); in the Qwen run task starts grew 1.2k -> 51.6k -> 102k -> 136k tokens, 35% of all teammate prompt tokens were finished-task history, five teammates died at the 262k-token provider limit, and only 16% of later-task reads even touched files already in context. Provider cache entries expire after ~5 min idle (99% hit at 300 s, 0% at 600 s) and never transfer between agents.
  [cleanup note: six in the committed trace; see the note under Part A §3.6.]
- **Why the lead's cache misses.** Consecutive lead requests share only 7,966 identical chars (tools + system up to `Current time`); removing the timestamp gives 100% prefix identity until compaction, but `micro_compact` (37-45%), `snip_compact` (~51%) and summary compaction (16%) break it again. Teammates' append-only prompts hit 84-94%.
  [cleanup note: Part A §3.5 gives 84-90% for the same quantity; see the note there.]
- **Window/KV scheme.** KV states depend on position and prefix, so no hosted API can reuse a file window across contexts; a harness-level version can: fixed-size blocks with headers, a pointer instead of bytes for blocks already in context, a shared file "pinboard" in the prefix so one prefill serves all teammates, and volatile text moved after it.

_(was §4 "Follow-up experiment: latency headroom from reusing repeated file content")_

**Question.** Teammates re-send the same file content many times; if every repeated window could be served from cache (however implemented), how much end-to-end latency could be saved at most?

**Method.** Latency model fitted on all 663 recorded model calls (R^2 = 0.90):
`duration = 2.6 s + 0.42 ms x uncached prompt token + 0.10 ms x cached prompt token + 18.4 ms x output token`
(uncached prefill ~2,400 tok/s, decode ~54 tok/s, a cached token costs 24% of an uncached one). "Repeated" file content = newly appended `read_file` windows whose lines any agent had fetched before (the most generous definition), converted at 3.94 chars/token.
[corrected 2026-09-10: a direct streaming probe of the same endpoint gives 4.6 s fixed + 0.052 ms per uncached token (prefill ~19k tok/s) and decode ~90-150 tok/s (Correction bullet below; Part A §3.8). Both tables below rest on this pooled fit; per the Correction bullet the repeated-content prefill headroom is ~8x smaller than stated. Cleanup note: no committed script fits this model; 090926 slide 10 says an OLS refit on the committed per-call sidecars (`research/02_input_redundancy/data/reuse_profiling/*.inputs.jsonl`) reproduces the coefficients exactly.]

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

[corrected 2026-09-10: ~8x smaller with the probe-based fit — X2 run 2 ~12 s, not 95 s (Correction bullet below).]

Outer bound if repeated windows were also removed from the context entirely (pointer instead of bytes): roughly +240 s in X2 run 2, i.e. ~10% of teammate model time.

**Preview takeaways.**
- The provider's prefix cache already skips the prefill of the re-sent history: 3.66M of 4.0M teammate prompt tokens were cache hits in X2 run 2, worth ~1,175 s of prefill (~642 s in run 1); without it teammate model time would be 25-36% higher. Only newly appended duplicates still pay prefill.
- Prefill is cheap next to decode for this model (2,400 tok/s in vs 54 tok/s out): a whole 143k-char file costs ~15 s of prefill once, a 2,000-token answer ~37 s of decode every time.
- The real latency levers are elsewhere: fewer model calls (X3: 100 -> 7 calls cut wall time 808 s -> 168 s, -79%), a cacheable lead prefix (X3 lead spends 48% of its model time on uncached prefill; teammate-like hit rates would save ~265 s vs 34 s from skipping re-read content), and less coordination chatter (X2 runs spent 2,000-2,400 s of teammate time on decode and hit the 1,500 s ceiling).
- **Correction (2026-09-10 probe).** A direct streaming time-to-first-token probe of the same endpoint gives 4.6 s fixed + 0.052 ms per uncached token (prefill ~19k tok/s, decode ~90-150 tok/s). The pooled regression above (0.42 ms/token) was confounded by long-output calls and overstates per-token prefill ~8x; the repeated-content prefill headroom is therefore ~8x smaller than the table says (X2 run 2: ~12 s, not 95 s), and per-call fixed latency, not prefill, is what each extra round costs. The conclusion (reuse headroom is small; round count is the lever) is unchanged and stronger.
- Caveats: one pooled linear fit for a hosted model with network included; the cached-token coefficient bundles KV loading with long-context decode slowdown; line-level "repeated" is the most generous count, so the savings are upper bounds.

_(was §5 "Conclusion")_

File-read repetition in s15 is structural: the lead re-reads because a 50k-char harness budget evicts what it read, each teammate re-loads and permanently keeps the whole shared file because it has no search tool and no context reset, and the provider's prefix cache hides most of the teammates' cost (90% hits) but almost none of the lead's (17%), so the remedies are harness-side (a token-based budget, a stable cacheable prefix, task-boundary resets, and a locator tool or shared file pinboard), not model- or API-side, and skipping the prefill of repeated file content would recover only 1-3% of teammate model time because the provider cache already absorbs most of it and latency is dominated by decode and call count.
[corrected 2026-09-10: the "1-3%" rests on the pooled fit; with the probe-based fit the repeated-content prefill headroom is about 8x smaller (Correction bullet in the "was §4" block above), which that bullet says leaves the conclusion unchanged.]

# Part B — Byte-level redundancy of file input across s15 teammates, by workload
_Formerly `s15_integrated_harness/input_redundancy_profile.md` (2026-09-10, e7351e6; §6 and the paragraph pointing to it added the same day, d67ab7f)._

Profiling question (2026-09-10): when several teammates work on one team task, how much of the
file content they pull into their model inputs is *the same bytes* another teammate already pulled
in -- measured on the bytes, not on whole-result hashes -- and how does that depend on whether the
workload is prefill-heavy (read a lot, write little) or decode-heavy (read little, write a lot)?

Short answer (two repetitions per workload, lead + 3 teammates, `glm-5.3-flash`):

- **Cross-teammate redundancy is set by how much the tasks' files overlap, not by the
  prefill/decode balance.** Of the bytes teammates pulled into their inputs, the share another
  teammate already held was 64-69% when three auditors read the same two source files (PF-S),
  53% when each compared a private chapter file against the shared s15 file (PF-P), 67-80% when
  three writers started from the same 13 KB glossary (DC-S), and 0% when each wrote from its own
  chapter (DC-P). The earlier X2 / X5 / Qwen runs re-measured on bytes fit the same pattern
  (60-77% / 0% / 77%).
- **The workload axis decides what the redundancy costs.** Prefill-heavy teams carry it in
  every call: the duplicated file content is 45-54% of all teammate prompt tokens in PF-S and
  46-50% in PF-P (40-49% of the *uncached* tokens), with 2-4 resident copies of a 146 KB file.
  Decode-heavy teams duplicate a small input: 14-15% of prompt tokens in DC-S (7-9% of uncached),
  0% in DC-P, while output tokens are 5-8x larger relative to the prompt.
- **Whole-result hashing (SHA-256) misses almost all of it.** It reported 0-3% in all four
  prefill-heavy shared-corpus runs (and 2.7% in X2 run 1) against 53-69% on the bytes, because
  teammates page the same file in 300-, 500-, 700-, 800- and 1,000-line windows starting at line
  0 or 1; it only agrees with the byte-level measures when every teammate happens to read a file
  in one call (DC-S, X2 run 2). Exact (file, line) provenance and ~350-byte content-defined
  chunks agree within 1-4 points on every run.
- **Duplicates are fresh within a wave.** In single-wave runs 94-100% of the duplicated bytes
  were fetched within 60 s of the first copy; second waves (PF-S run 1, DC-S run 2, X2 run 2, the
  Qwen run) re-fetched them 6-16 minutes or hours later, past the provider cache's idle lifetime.
- **Latency headroom is small on this provider.** Decode is 61-92% of teammate model time
  (the model thinks for 77-91% of its output on the audits) and the provider's prefix cache
  serves 62-91% of the re-sent prompt tokens; skipping the redundant prefill would save only the
  first send of each duplicate window: 74-112k uncached tokens (~30-47 s summed over the team)
  in PF-S, ~64k in PF-P, 9-15k in DC-S. The larger effects are context growth and KV memory.
  [cleanup note: the decode shares and prefill seconds here — and in §2.3, §3 item 5 and the §6 workload-shape table — come from the pooled latency fit (2.6 s + 0.42 ms per uncached prompt token + 0.10 ms per cached token + 18.4 ms per output token), which the 2026-09-10 streaming probe superseded (4.6 s + 0.052 ms per uncached token, decode ~90-150 tok/s; Part A §3.8 and A.W). They were not recomputed; by Part A §3.8 the per-token prefill cost is overstated about 8x, so the prefill seconds shrink accordingly.]

Per-run reports for every run (workload runs, X2, X5, Qwen) are in `research/02_input_redundancy/data/input_redundancy_runs.md`.
Section 6 repeats all eight runs after the lead's context budget was raised from 50k to 512k
characters (commit `e7351e6`): the lead stops compacting and every run completes, while the
teammates' byte-level redundancy stays where it was.

## 1. Method

### 1.1 What is measured

An *item* is one tool result that carried file content into an agent's model input: every
successful `read_file` result, plus shell results whose command is a file reader (`cat`, `head`,
`tail`, `grep`, `sed -n`, ...; denied commands and errors are excluded). Items are ordered by the
time they entered a model request and split into *units* at five granularities; every unit is
weighted by its size in UTF-8 bytes and classified chronologically:

- **new** -- never fetched before by any agent;
- **intra** -- fetched before by the same agent (self re-read, re-paging);
- **cross** -- not fetched before by this agent, but fetched earlier by another agent.

The *cross-agent redundancy rate* of a run is cross bytes / total bytes fetched. The
teammates-only view drops the lead's reads from the state, so "cross" means "another teammate
had it first". Additional views: total redundancy (1 - unique bytes / fetched bytes), pairwise
overlap of each teammate's unique byte set, resident copies (sum of per-teammate unique bytes /
union), the share of prompt tokens the redundant bytes occupy once re-sending is counted, and
the time gap between the first fetch and each cross-agent re-fetch.

| granularity | unit | what it catches / misses |
|---|---|---|
| `whole` | SHA-256 of the entire tool result | only byte-identical results; lines 1-700 vs 0-800 of the same file share nothing (the previous report's measure) |
| `range` | (file, line number), exact provenance | any overlap of paging windows of the same file; misses the same text in other files |
| `line` | exact text of a line (>= 8 non-blank chars; shorter lines never deduplicate) | copied code across files; over-counts common lines like `return None` |
| `cdc256` | gear-hash content-defined chunk, 64-2048 B (measured mean ~360 B on this code, ~90 tokens) | alignment-insensitive duplicate stretches anywhere; roughly a 64-token block in a content-addressed KV cache |
| `cdc1k` | content-defined chunk, 256-8192 B (measured mean ~1.7 KB) | only long identical stretches |

Validation on synthetic data: two agents paging the same 3,000-line text with different windows
(1,000/2,000 vs 1,500/1,500 lines) give a second-agent cross redundancy of 0% under `whole`, 100%
under `range` and `line`, 98% under `cdc256`, 93% under `cdc1k`; a copy shifted by one line
shares 99.9% of its bytes under `cdc256`.

### 1.2 Instrumentation

- `research/common/profile_run.py` (extended): besides the trace and the per-call `.inputs.jsonl`, it now
  writes `<trace>.reads.jsonl` with the **full text of every tool_result block the first time it
  appears in an agent's request**, with the tool name and arguments resolved from the matching
  `tool_use` block, and records the list of tool_result ids present in every call plus the
  output's text/thinking character split. This is exactly what the model saw, per agent.
- The trace's own `HARNESS_TRACE_OUTPUT=full` mode was not sufficient: `emit()` re-summarises
  every event payload with `argument_chars=2048`, so any result longer than 2,048 characters is
  stored as `{characters, sha256, preview}` even in full mode (`trace_runtime.py:311`). For
  the earlier runs (no sidecar) the analyzer **reconstructs** `read_file` results from the git
  blob of the run's commit and accepts a reconstruction only if its SHA-256 equals the one the
  trace recorded: 178/178 results of X2 run 2 and all X2 run 1 / X5 results reconstruct
  byte-exactly at `f48d5b8`; the Qwen stress run reconstructs 367 of 434 non-trace reads at
  `6218dd6` (the rest are files its own teammates were editing, and are dropped).
- `research/common/input_redundancy.py`: the analyzer (standard library only; ~1 s per run).
- `research/02_input_redundancy/redundancy_workloads.py`: the workload prompts and sequential runner.

### 1.3 Workloads

All runs: lead + 3 teammates, `glm-5.3-flash` via z.ai, repository read-only (writes denied,
mutating shell denied), fresh state per run, 1,200 s ceiling, HEAD `ef352ae` (teammates may run
display-only shell commands since `968a33c`). Two repetitions of each workload.

| id | axis | shape | teammate tasks (abridged) |
|---|---|---|---|
| PF-S | prefill-heavy, shared corpus | all three read `code.py` (146 KB) + `trace_runtime.py` (22 KB) completely, answer in <= 15 lines | swallowed exceptions / unlocked shared state / paths that drop tool results |
| PF-P | prefill-heavy, partial overlap | each reads its own chapter `code.py` (s08 24 KB, s10 20 KB, s13 71 KB) plus the shared s15 `code.py`, answer in <= 15 lines | which chapter functions survived into s15 unchanged / changed / dropped |
| DC-S | decode-heavy, shared small input | all three start from `GLOSSARY.md` (13 KB), deliver >= 1,200-word documents | tutorial / 40-case test plan / design critique |
| DC-P | decode-heavy, disjoint inputs | each reads one chapter README (s06 4.6 KB, s10 9.4 KB, s13 20 KB), delivers a >= 1,200-word tutorial | one tutorial per chapter |

Earlier runs re-analysed with the same tool: X2 (3 teammates analysing `code.py`, twice; run 2
spawned a second wave of 3), X5 (3 teammates summarising disjoint chapter files), and the
committed 28-teammate Qwen stress run.

## 2. Results

### 2.1 Cross-teammate redundancy (byte-weighted, exact `range` provenance)

| run | status / wall | teammates | teammate bytes fetched | union unique | cross-teammate redundancy | intra-teammate repeat | total redundancy (1-union/total) | resident copies | cross-redundant share of teammate prompt tok |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| PF-S-r1 | timeout / 1324s | 4 | 732,718 | 168,078 | **68.8%** | 7.6% | 77.1% | 4.01x | 45.2% |
| PF-S-r2 | completed / 467s | 3 | 524,522 | 168,640 | **64.1%** | 2.5% | 67.9% | 3.00x | 54.1% |
| PF-P-r1 | completed / 258s | 3 | 553,690 | 261,133 | **52.8%** | 0.0% | 52.8% | 2.12x | 50.3% |
| PF-P-r2 | completed / 338s | 3 | 553,669 | 261,112 | **52.8%** | 0.0% | 52.9% | 2.12x | 45.8% |
| DC-S-r1 | timeout / 1334s | 3 | 38,505 | 12,835 | **66.7%** | 0.0% | 66.7% | 3.00x | 15.1% |
| DC-S-r2 | timeout / 1365s | 5 | 64,175 | 12,835 | **80.0%** | 0.0% | 80.0% | 5.00x | 13.9% |
| DC-P-r1 | completed / 293s | 3 | 67,229 | 67,229 | **0.0%** | 0.0% | 0.0% | 1.00x | 0.0% |
| DC-P-r2 | completed / 335s | 3 | 71,954 | 71,954 | **0.0%** | 0.0% | 0.1% | 1.00x | 0.0% |

"Teammates" counts every teammate the lead spawned (PF-S run 1 and DC-S run 2 got a second
wave). "Resident copies" is the sum of each teammate's unique bytes over the union: how many
times the same bytes sit in teammate contexts at the end of the run, since teammates never
compact. The last column counts every re-send of a duplicated window over the run.

### 2.2 The same quantity under each identification method

Share of teammate bytes that another teammate had fetched earlier:

| run | `whole` (SHA-256 of result) | `range` (file, line) | `line` (text, >= 8 chars) | `cdc256` (~350 B chunks) | `cdc1k` (~1.6 KB chunks) |
|---|---:|---:|---:|---:|---:|
| PF-S-r1 | 3.0% | 68.8% | 59.3% | 66.9% | 58.9% |
| PF-S-r2 | 0.0% | 64.1% | 55.2% | 61.6% | 49.2% |
| PF-P-r1 | 0.0% | 52.8% | 46.9% | 52.4% | 46.6% |
| PF-P-r2 | 0.0% | 52.8% | 46.9% | 50.5% | 37.3% |
| DC-S-r1 | 66.7% | 66.7% | 66.3% | 66.7% | 66.7% |
| DC-S-r2 | 80.0% | 80.0% | 79.6% | 80.0% | 80.0% |
| DC-P-r1 | 0.0% | 0.0% | 11.5% | 2.3% | 1.4% |
| DC-P-r2 | 0.0% | 0.0% | 4.0% | 0.1% | 0.0% |

`line` reads lower than `range` on the shared-corpus runs because line hashes of `grep -n`
output (prefixed with the line number) do not match the raw lines, and higher on disjoint
inputs because common statements repeat across unrelated files; `cdc1k` needs ~1.6 KB
identical stretches and so loses the short windows. `range` and `cdc256` are the two measures
to trust, and they agree within 1-4 points.

### 2.3 Workload shape of the teammates

| run | teammate calls | prompt tok | cache hit | output tok | thinking share of output | output / prompt tok | file bytes per output tok | est. decode share of model time | file content share of prompt tok | cross-redundant first sends (tok) | share of uncached tok |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| PF-S-r1 | 69 | 2,929,726 | 90.6% | 121,040 | 91.1% | 0.041 | 6.1 | 79.9% | 70.9% | 111,652 | 40.4% |
| PF-S-r2 | 46 | 1,354,872 | 88.7% | 28,046 | 80.5% | 0.021 | 18.7 | 62.9% | 83.5% | 74,298 | 48.5% |
| PF-P-r1 | 17 | 369,583 | 63.4% | 10,664 | 81.2% | 0.029 | 51.9 | 61.2% | 89.3% | 64,365 | 47.6% |
| PF-P-r2 | 35 | 836,835 | 82.6% | 18,962 | 77.0% | 0.023 | 29.2 | 61.2% | 86.5% | 63,964 | 43.8% |
| DC-S-r1 | 24 | 379,303 | 65.8% | 92,780 | 51.8% | 0.245 | 0.4 | 92.3% | 15.1% | 9,027 | 7.0% |
| DC-S-r2 | 47 | 815,452 | 80.6% | 165,188 | 43.7% | 0.203 | 0.4 | 92.3% | 13.9% | 14,529 | 9.2% |
| DC-P-r1 | 13 | 58,649 | 61.7% | 11,410 | 22.6% | 0.195 | 5.9 | 81.8% | 76.1% | 0 | 0.0% |
| DC-P-r2 | 14 | 71,102 | 65.7% | 13,045 | 29.7% | 0.183 | 5.5 | 82.4% | 75.0% | 0 | 0.0% |

The prefill/decode split uses the latency fit from the previous report (2.6 s + 0.42 ms per
uncached prompt token + 0.10 ms per cached token + 18.4 ms per output token). "File content
share" counts every re-send of every window; "first sends" is the prefill that a shared cache
across teammates could remove at most (each duplicate window paid once per teammate).
[corrected 2026-09-10: this is the pooled fit that the streaming probe superseded; see the note under the short answer. The "est. decode share" columns here and in §6 were not recomputed.]

### 2.4 Age of the duplicates

Time between the first fetch of a byte range by one teammate and its re-fetch by another
(byte-weighted, `range`):

| run | cross bytes | p50 | p90 | max | within 60 s | 60-300 s | 300-600 s | over 600 s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| PF-S-r1 | 504,054 | 5s | 970s | 975s | 67% | 0% | 0% | 33% |
| PF-S-r2 | 336,081 | 12s | 29s | 93s | 94% | 6% | 0% | 0% |
| PF-P-r1 | 292,557 | 21s | 26s | 27s | 100% | 0% | 0% | 0% |
| PF-P-r2 | 292,557 | 11s | 31s | 36s | 100% | 0% | 0% | 0% |
| DC-S-r1 | 25,670 | 23s | 33s | 33s | 100% | 0% | 0% | 0% |
| DC-S-r2 | 51,340 | 3s | 362s | 362s | 50% | 0% | 50% | 0% |

The late tails are second waves: the fourth teammate of PF-S run 1 (spawned 16 minutes in) and
the two extra teammates of DC-S run 2 (spawned after provider rate limits stalled the first
wave). Within a wave the three teammates open the shared file within half a minute of each
other, so the duplicated prefill happens concurrently rather than sequentially.

### 2.5 What happened in each workload

**PF-S (prefill-heavy, shared corpus).** Run 1: the three auditors each paged `code.py` in
different windows within the first 35 s (700-, 800- and 1,000-line pages starting at line 0 or 1),
then read `trace_runtime.py`; every teammate reached 100% coverage of both files. Because the
windows differ, only 4 of the 77 file-input results were byte-identical to an earlier one
(`whole` 3.0% of the bytes), while 68.8% of the fetched bytes were lines another teammate already
held (`range`). Teammates then switched to `grep -n` for line numbers; twelve of those calls were
denied -- six because a double-quoted regex alternation (`"except|raise"`) is not recognised as
read-only (single quotes are), six because `cd dir && grep ...` is a compound -- so the teammates
re-read windows instead. The lead lost one teammate's final report
to its own compaction, asked for it again, spawned a fourth teammate for an item that was already
done, and the run hit the 1,200 s ceiling. Seven teammate calls stopped at `max_tokens` (8,000)
because thinking plus report exceeded the limit. Run 2 finished in 467 s with exactly three
teammates: they paged `code.py` in 700-line windows from line 0, 800 then 700-line windows from
line 801, and 500-line windows from line 1, so no two results were byte-identical (`whole`
0.0%) while 64.1% of the bytes were duplicates; 10 shell searches were denied (6 `cd dir &&
grep`, 2 double-quoted alternations, 2 other), 94% of the duplicated bytes arrived within 60 s,
and the duplicated content made up 54% of all teammate prompt tokens.

**PF-P (prefill-heavy, partial overlap).** Run 1 finished cleanly in 258 s with exactly three
teammates and 17 teammate calls. Each teammate read its private chapter file once and the shared
s15 `code.py` completely (one teammate in a single 146 KB read, the other two in pages); the
shared file is 52.8% of all bytes fetched and is exactly the cross-teammate redundancy under
`range`. Under `line` the total redundancy is 65.7% because 18.9% of the bytes are *intra*-agent
repeats: lines of the chapter file that reappear verbatim in s15's `code.py` (the functions that
were carried over) plus common short statements; under `cdc256`, which needs ~350-byte identical
stretches, that intra share is 4.5%. All duplicates were fetched within 27 s of the first copy. Run 2 repeated the figure exactly
(52.8%, 338 s, 35 teammate calls) with different paging: 300-line windows for one teammate,
400/600-line windows for another, 800-line windows for the third, and again zero byte-identical
results.
[cleanup note: per `research/02_input_redundancy/data/input_redundancy_runs.md` (PF-P-r1, "Files" table) the s15 `code.py` accounts for 438,978 of the 553,829 teammate bytes fetched (79%); the 52.8% is its second and third copies (292,557 cross bytes), as the B.W addition (was §4 item 1) puts it.]

**DC-S (decode-heavy, shared small input).** Run 1: each teammate read `GLOSSARY.md` (12.8 KB)
exactly once and wrote its document (tutorial 21.7k, test plan 44.6k, critique 26.5k output
tokens; four calls truncated at `max_tokens`, one document 32,000 characters long). Redundancy is
the same 66.7% under every granularity -- three identical whole-file reads -- but the absolute
volume is 38.5 KB against 734 KB in PF-S, file content is 15% of teammate prompt tokens against
71%, and output tokens are 0.245 of prompt tokens against 0.041. The lead re-engaged the
teammates twice with "assignment corrections", so the run also hit the ceiling. Run 2 met the
provider's rate limit (20 retried 429s), the lead spawned a second wave of two teammates while
the first wave was stalled, and five contexts ended up holding the glossary (80.0% redundancy,
5.00 resident copies); eight calls stopped at `max_tokens` and the largest delivered document
was 34,000 characters.

**DC-P (decode-heavy, disjoint inputs).** Run 1 finished in 293 s with three teammates and 13
teammate calls. Each teammate read its chapter README once, two of them also the chapter's
`code.py`; 67 KB fetched in total, no file shared, so `whole` and `range` report 0%. `cdc256`
still finds 2.3% (1.6 KB of identical import/tool-schema boilerplate between the s06 and s10
code) and `line` 11.5% (common statements). Output tokens are 0.195 of prompt tokens and, unlike
the audits, the model spent only 23% of its output on thinking. Run 2 (335 s) was the same
shape; one teammate read 36% of the s13 `code.py` in three targeted windows, and the
content-chunk measure found 0.1% shared bytes.

### 2.6 The earlier team runs, re-measured on bytes

| run | teammates | bytes fetched | `whole` cross | `range` intra / cross | `line` cross | `cdc256` cross | `cdc1k` cross | resident copies | cross-redundant share of teammate prompt tok |
|---|---:|---:|---:|---|---:|---:|---:|---:|---:|
| X2 run 1 (3 analysts on `code.py`) | 3 | 482,977 | 2.7% | 10.7% / 59.5% | 51.3% | 54.7% | 38.0% | 3.00x | 41.2% |
| X2 run 2 (two waves of 3) | 6 | 1,077,918 | 66.5% | 11.7% / 70.4% | 60.7% | 69.5% | 68.1% | 4.94x | 64.3% |
| X5 (3 summarisers, disjoint files) | 3 | 118,309 | 0.0% | 0.0% / 0.0% | 12.6% | 1.4% | 0.8% | 1.00x | 0.0% |
| Qwen 28-teammate stress run (367 reconstructed reads) | 28 | 3,233,355 | 39.9% | 10.8% / 77.1% | 67.4% | 74.9% | 67.9% | 7.40x | 27.9% |

The two X2 runs had the same task and nearly the same byte-level redundancy (60% and 70% under
`range`), yet the whole-result measure reported 2.7% and 66.5%: in run 1 every teammate paged the
file, in run 2 five of six read it in one call. Whole-result hashing measures paging habits, not
redundancy. X5's 12.6% under `line` with 0% under `range` and 1.4% under `cdc256` is the
over-count of common statements across three unrelated chapter files (short lines under 8
characters are already excluded), which is why `line` is reported as an upper bound only.
In the Qwen run 28 teammates fetched 3.2 MB of which the union is 390 KB (7.4 resident copies);
83% of the cross-duplicated bytes were re-fetched more than ten minutes after the first copy,
because tasks were claimed in waves over three hours -- a provider prefix cache with a ~5-minute
idle lifetime could not serve them even if prefixes matched, which they do not.

## 3. Interpretation

1. **Cross-teammate redundancy is a property of the corpus overlap, not of the workload's
   prefill/decode balance.** Shared-corpus workloads land at two thirds regardless of whether
   the teammates read 500-700 KB (PF-S, 64-69%) or 13 KB (DC-S, 67-80%); partial overlap gives
   half (PF-P, 53% twice); disjoint inputs give zero (X5, DC-P). What the workload axis changes is the *volume* and the *share of
   the prompt* that the redundancy occupies.
2. **Prefill-heavy teams carry the redundancy in every call.** Teammates never compact, so a
   redundant window stays resident for the rest of the teammate's life and is re-sent 6-14 times.
   In PF-S the cross-redundant file content is 45-54% of all teammate prompt tokens (1.3M of
   2.9M in run 1) and 40-49% of the *uncached* tokens; in PF-P 46-50% and 44-48%; in DC-S 14-15%
   and 7-9%; in DC-P zero.
3. **Whole-result hashing (SHA-256) is the wrong instrument for this question.** It found 0-3%
   cross redundancy in the four prefill-heavy shared-corpus runs (and in X2 run 1) where the
   byte-level measures found 53-69%, and it jumps to 66-80% as soon as teammates happen to read
   a file in one call (DC-S, X2 run 2). Exact
   line provenance (`range`) and content-defined chunks (`cdc256`) agree within a few points on
   every run; the chunk measure additionally sees copied code across files and would be the
   natural unit for a content-addressed KV cache.
4. **The duplicates are fresh in a single-wave team and stale in a multi-wave one.** In PF-P,
   PF-S run 2, DC-S run 1 and DC-P 94-100% of the duplicated bytes were fetched within 60 s of
   the original; second waves re-fetched them 6 minutes (DC-S run 2), 16 minutes (PF-S run 1) or
   hours (X2 run 2, Qwen) later, after the original's provider-cache lifetime had passed.
5. **Latency headroom stays small on this provider.** Even the prefill-heavy runs are 61-80%
   decode time by the earlier latency fit (glm-5.3-flash thinks for 77-91% of its output tokens
   on the audits), and the provider's prefix cache already serves 63-91% of the teammates'
   re-sent prompt tokens. Removing the cross-redundant prefill would save at most the first send
   of each duplicate window: 74-112k uncached tokens in PF-S (~30-47 s of prefill summed over the
   team), ~64k in PF-P, 9-15k in DC-S. The larger effects of redundancy are context growth (peak
   teammate prompts of 46-92k tokens in PF-S versus 18-48k in DC-S) and the KV memory of 2-5
   resident copies of the same file.
   [corrected 2026-09-10: the decode shares and the ~30-47 s rest on the superseded pooled fit; see the note under the short answer.]

## 4. Harness observations made along the way

- `HARNESS_TRACE_OUTPUT=full` does not store results longer than 2,048 characters verbatim
  (`emit()` re-applies `argument_chars`), so the trace alone cannot support byte-level analysis;
  the `.reads.jsonl` sidecar or git reconstruction is required.
- `_is_read_only_command` strips only single-quoted text, so `grep -E "a|b" file` is treated as a
  pipeline of unknown commands and denied for teammates, while `grep -E 'a|b' file` is allowed;
  `cd dir && grep ...` and `sed -n` are always denied. Denied searches turn into whole-window
  re-reads.
- Teammates hit `max_tokens=8000` when thinking plus a long deliverable exceed it (7 calls in
  PF-S run 1, 4 and 8 in the DC-S runs); the truncated text is still delivered as the result.
- Provider rate limits (429) hit DC-S run 2 twenty times with only four concurrent sessions; the
  driver's retries kept the run alive but the stalled first wave led the lead to spawn a second
  one, which is where the fifth copy of the glossary came from.
- The lead's coordination after results arrive (ownership reminders, duplicate task creation,
  re-requests for reports lost to its own compaction) is what pushed PF-S and DC-S to the 1,200 s
  ceiling; PF-P, whose lead simply summarised and shut the team down, took 258 s.

## 5. What would remove the redundancy

- **A shared, position-stable file block in the prefix.** Because every teammate of a shared-corpus
  task holds the same 146 KB file (3.0-4.9 resident copies), a team-level "pinboard" of file
  windows placed at the start of every teammate prompt would let one prefill serve all teammates
  on the Anthropic-style API (identical prefix) and would remove the duplicates from the KV
  memory of a self-hosted server. It needs the windows to be identical bytes at identical
  positions -- exactly what `range`/`cdc` show to be true and what paging with different
  offsets currently destroys.
- **Normalise paging.** Teammates chose 700-, 800- and 1,000-line windows starting at line 0 or
  1. Fixed-size, aligned windows (e.g. 500 lines from line 0) would make whole-window hashes
  match across teammates (turning the 3% `whole` figure into the 69% `range` figure) and make
  provider prefix caching work across agents when the pinboard above is in place.
- **Pass line ranges instead of re-reading.** In PF-P each teammate loaded the whole 146 KB s15
  file to compare one chapter against it; a locator tool or an excerpt in the task description
  would cut the shared read to the relevant functions.
- **Let teammates search.** Accepting double-quoted patterns and `sed -n` in the read-only
  classifier removes the re-read fallbacks observed in PF-S.


## 6. Rerun with the lead's context budget at 512,000 characters

Commit `e7351e6` set `CONTEXT_LIMIT = CONTEXT_TOKEN_LIMIT (128,000) * CHARS_PER_TOKEN (4)`, ten
times the earlier 50,000. The budget governs only the lead's compaction pipeline (placeholder
rewrites, `fit_tool_results`, summary compaction); teammates never compact, and `snip_compact`
still archives the middle of the lead's history above 50 messages. Same prompts, two repetitions,
traces in `research/02_input_redundancy/data/redundancy_profiling_ctx512k/` (labels `*-c512-r1/r2`).

**Side by side (50k r1 / r2 -> 512k r1 / r2)**

| workload | cross-teammate redundancy (`range`) | teammates spawned | status / wall | lead calls | lead summary compactions + shrinks | lead cache hit | teammate prompt tok | redundant share of teammate prompt tok |
|---|---|---|---|---|---|---|---|---|
| PF-S | 68.8 / 64.1 -> **61.3 / 61.5%** | 4 / 3 -> 3 / 3 | timeout 1324 s / 467 s -> 788 s / 447 s | 49 / 18 -> 36 / 17 | 2+6 / 0 -> 0+7 / 0 | 25 / 26 -> 9 / 18% | 2.93M / 1.35M -> 2.08M / 2.08M | 45 / 54 -> 47 / 53% |
| PF-P | 52.8 / 52.8 -> **52.8 / 52.8%** | 3 / 3 -> 3 / 3 | 258 s / 338 s -> 317 s / 275 s | 14 / 16 -> 15 / 15 | 0 / 0 -> 0 / 0 | 33 / 28 -> 21 / 23% | 370k / 837k -> 1.01M / 426k | 50 / 46 -> 48 / 47% |
| DC-S | 66.7 / 80.0 -> **66.7 / 59.9%** | 3 / 5 -> 3 / 3 | timeout 1334 s / timeout 1365 s -> 673 s / 518 s | 52 / 55 -> 56 / 26 | 4+6 / 4+4 -> 0 / 0 | 17 / 15 -> 6 / 10% | 379k / 815k -> 140k / 90k | 15 / 14 -> 24 / 27% |
| DC-P | 0.0 / 0.0 -> **0.0 / 0.0%** | 3 / 3 -> 3 / 3 | 293 s / 335 s -> 783 s / 238 s | 14 / 17 -> 18 / 20 | 1+1 / 1+1 -> 0 / 0 | 21 / 24 -> 4 / 22% | 59k / 71k -> 825k / 66k | 0 / 0 -> 0 / 0% |

**Granularities, 512k runs** (cross-teammate share of teammate bytes)

| run | `whole` | `range` | `line` | `cdc256` | `cdc1k` | teammate bytes | resident copies |
|---|---:|---:|---:|---:|---:|---:|---:|
| PF-S-c512 r1 / r2 | 4.0 / 9.4% | 61.3 / 61.5% | 52.8 / 53.0% | 59.7 / 59.5% | 52.9 / 51.1% | 549 / 547 KB | 3.0x / 3.0x |
| PF-P-c512 r1 / r2 | 1.3 / 26.4% | 52.8 / 52.8% | 46.9 / 46.8% | 50.6 / 52.5% | 32.9 / 48.8% | 554 / 555 KB | 2.1x / 2.1x |
| DC-S-c512 r1 / r2 | 66.7 / 59.9% | 66.7 / 59.9% | 66.3 / 59.6% | 66.7 / 59.9% | 66.7 / 59.9% | 39 / 43 KB | 3.0x / 3.0x |
| DC-P-c512 r1 / r2 | 0.0 / 0.0% | 0.0 / 0.0% | 10.5 / 0.9% | 2.0 / 0.0% | 1.1 / 0.0% | 83 / 49 KB | 1.0x / 1.0x |

**Workload shape, 512k runs (teammates)**

| run | calls | prompt tok | cache hit | output tok | thinking share | output / prompt | est. decode share | file content share of prompt | redundant first sends (tok) | share of uncached | duplicates within 60 s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| PF-S-c512-r1 | 63 | 2,076,999 | 90.9% | 58,837 | 88% | 0.028 | 72% | 75% | 73,595 | 39% | 50% (all < 170 s) |
| PF-S-c512-r2 | 60 | 2,078,948 | 92.3% | 32,051 | 82% | 0.015 | 59% | 82% | 72,704 | 46% | 100% |
| PF-P-c512-r1 | 45 | 1,010,037 | 86.4% | 12,500 | 78% | 0.012 | 47% | 88% | 64,169 | 47% | 79% (all < 75 s) |
| PF-P-c512-r2 | 15 | 425,862 | 67.2% | 13,464 | 78% | 0.032 | 66% | 89% | 63,585 | 46% | 100% |
| DC-S-c512-r1 | 15 | 139,914 | 52.9% | 52,054 | 49% | 0.372 | 93% | 24% | 9,052 | 14% | 100% |
| DC-S-c512-r2 | 13 | 89,911 | 27.7% | 40,347 | 55% | 0.449 | 92% | 31% | 10,455 | 16% | 100% |
| DC-P-c512-r1 | 46 | 824,936 | 91.6% | 46,220 | 49% | 0.056 | 79% | 32% | 0 | 0% | - |
| DC-P-c512-r2 | 15 | 65,809 | 68.1% | 13,980 | 37% | 0.212 | 83% | 69% | 0 | 0% | - |

What changed and what did not:

- **Teammate-side redundancy is unchanged.** PF-P is 52.8% in all four runs; PF-S is 61% twice
  (a few points under the 50k runs' 64-69% because no fourth teammate appeared and the auditors
  took more of their input through shell searches); DC-S is 66.7% and 59.9% (one writer also
  pulled 4.4 KB of `find` output); DC-P stays at 0%. With three teammates the shared bytes cannot
  exceed (N-1)/N = 66.7% redundancy, and every shared-corpus run sits at or just under it.
- **The lead-side pathologies disappeared.** Zero summary compactions and zero placeholder
  rewrites in all eight runs (five of the eight 50k runs had 1-4 summary compactions), so no
  report was lost to compaction, the lead never re-requested a result, created a duplicate task or
  spawned a second wave, and every run finished before the ceiling: PF-S 788 / 447 s (was a
  timeout and 467 s), DC-S 673 / 518 s (was two timeouts). `snip_compact` still shrank the PF-S
  run-1 lead history seven times once it passed 50 messages (52-58k chars).
- **The lead's cache hit rate fell** from 15-33% to 4-23%: its history is no longer shortened, so
  the ~2.1k always-cached tokens (tools + system prompt before the per-second timestamp) are a
  smaller share of each prompt, and the timestamp still breaks the prefix on every call.
- **Decode-heavy teammates wrote less** (40-52k output tokens versus 93-165k) because nobody
  re-engaged them, so the three glossary copies are a larger share of their prompts (24-27%
  versus 14-15%) although the redundancy rate is the same.
- **Shell denials still cost time.** DC-P run 1 took 783 s because one writer ran about thirty
  `echo "..." | wc -w` word counts on its own prose, seventeen of them denied (double quotes plus
  a pipe); another writer noted that shell approval was unavailable and delivered its tutorial
  anyway. Whole-result hashing stayed erratic (PF-P 1.3% and 26.4% for identical `range` results).

Conclusion of the rerun: a larger lead budget fixes the lead (no compaction, no lost reports, no
second waves, no timeouts, 1.7-2.6x shorter wall time where runs had hit the ceiling) and leaves
the teammates' byte-level redundancy exactly where corpus overlap and team size put it. The
remedies in section 5 stand; the lead's budget should stay large, and the remaining lead-side item
is a cacheable prefix.

## Appendix: reproduce

```sh
# four workloads, one repetition (traces + sidecars + console logs in research/02_input_redundancy/data/redundancy_profiling/)
python3 research/02_input_redundancy/redundancy_workloads.py --rep r1

# the same four workloads under another harness configuration (here: after raising CONTEXT_LIMIT), kept apart by directory
python3 research/02_input_redundancy/redundancy_workloads.py --rep c512-r1 --trace-dir research/02_input_redundancy/data/redundancy_profiling_ctx512k

# byte-level report for every run in a directory (sidecar mode), or for old traces via git reconstruction;
# --tables prints the four summary tables, --lead-table the lead/run-shape view
python3 research/common/input_redundancy.py research/02_input_redundancy/data/redundancy_profiling
python3 research/common/input_redundancy.py research/02_input_redundancy/data/redundancy_profiling_ctx512k --tables --lead-table --sort label
python3 research/common/input_redundancy.py research/02_input_redundancy/data/reuse_profiling/run_20260908T150237_103264Z_67505088.jsonl --git-rev f48d5b8
python3 research/common/input_redundancy.py s15_integrated_harness/traces/run_20260902T004901_584576Z_dc6a9685.jsonl --git-rev 6218dd6 --exclude 'traces/|\.task_outputs'
```

## B.W Additions from `weekly_progress/091626/teammate_input_redundancy.md` (2026-09-16)

The digest (committed 2026-09-10 in d67ab7f, dated 2026-09-16) was reported to be a strict subset of the report above; checked block by block it is not. The blocks below add numbers or statements the report lacks and are kept verbatim, in the digest's order; all other blocks repeat Part B (see the source map).

_(was §3 "Results", per-workload observations)_

Per-workload observations: PF-S teammates page the shared file in 500/700/800/1,000-line windows
starting at line 0 or 1 (only 4 of 77 results byte-identical), then fall back to `grep -n`, of
which 10-12 calls per run were denied (double-quoted alternations and `cd dir && grep` are not
classified read-only); the lead lost a report to its own compaction and spawned a duplicate
teammate (run 1 hit the ceiling; run 2 finished in 467 s). PF-P finished cleanly both times
(258 s, 338 s) with every teammate loading the whole 146 KB shared file to compare one chapter.
DC-S teammates read the glossary once and wrote 22-47k output tokens each (4-8 calls truncated
at `max_tokens=8000`); the lead's "assignment corrections" kept both runs at the ceiling. DC-P
finished in ~300 s with nothing shared.

_(was §4 "Conclusions", item 1)_

1. **When teammates share files, 52-80% of the bytes they load are copies of bytes another
   teammate already holds; when they do not, 0%.** The number is essentially arithmetic: with N
   teammates loading one shared file the redundant share of the shared bytes is (N-1)/N (66.7%
   for 3, 80% for 5), diluted by private files (PF-P: 2/3 of the shared file's 79% share =
   52.8%). It is set by corpus overlap, not by the prefill/decode balance.

_(was §4 "Conclusions", item 3)_

3. **The workload decides the cost.** In the prefill-heavy runs file content is 70-89% of teammate
   prompt tokens and the redundant copies are 45-54% of all prompt tokens (40-49% of the
   uncached ones); the other file half is each teammate's own first copy plus private files,
   which no cross-teammate scheme removes. In the decode-heavy shared run the redundancy rate is
   just as high but the volume is small: 14-15% of prompt tokens, 7-9% of uncached, and output
   dominates.

_(was §5 "Rerun with the lead's context budget raised from 50k to 512k characters", introduction)_

Change (commit `e7351e6`): `CONTEXT_LIMIT = CONTEXT_TOKEN_LIMIT (128,000) * CHARS_PER_TOKEN (4)` =
512,000 characters, up from 50,000. This is the **lead's** compaction budget (micro-compaction
placeholders, `fit_tool_results`, summary compaction); teammates never compact, and `snip_compact`
still archives the middle of the history whenever the lead exceeds 50 messages. Same four
workloads, same prompts, two repetitions, traces in
`research/02_input_redundancy/data/redundancy_profiling_ctx512k/` (labels `*-c512-r1/r2`); the lead
table below comes from `input_redundancy.py --lead-table`.
[cleanup note: "the lead table below" is the side-by-side table now in Part B §6; the note on shrinks that follows belongs under that table.]

_(was §5, note under the side-by-side table)_

(shrinks = `context_prepared` events that reduced the history: micro/fit compaction at 50k,
`snip_compact` at both limits.)

_(was §6 "Conclusions after the rerun")_

1. The 50k -> 512k change fixes the **lead**: no compaction, no lost reports, no second waves, no
   timeouts, 1.7-2.6x shorter wall time where runs had hit the ceiling. It does not touch the
   **teammates'** input redundancy, which stayed at 52-67% for shared corpora and 0% for disjoint
   ones, because that redundancy is set by corpus overlap and team size ((N-1)/N), not by any
   context budget: teammates never compact regardless of the limit.
2. The cost picture for prefill-heavy teams is unchanged: duplicated file content is still
   45-53% of teammate prompt tokens (39-47% of the uncached ones), with 2-3 resident copies of the
   146 KB file. For decode-heavy shared teams the rate is the same but the volume is small
   (24-31% of a much smaller prompt).
   [cleanup note: the side-by-side table of Part B §6 gives 47-53% for the 512k PF-S/PF-P runs (45-54% across both budgets), so the 45-53% here matches neither.]
3. The remedies for teammate redundancy therefore remain harness-side sharing (a position-stable
   shared file block, normalised paging windows, line ranges in task descriptions) and a
   read-only shell classifier that accepts double-quoted patterns and pipes into `wc`/`head`; the
   lead's budget should stay large, and the remaining lead-side item is a cacheable prefix
   (timestamp out of the system prompt, stable `snip_compact` markers).

---

## Data inventory

Every run directory holds, per run, `<run id>.jsonl` (the harness trace; its `profile_meta` record carries the label and driver arguments, its `profile_end` record the status, wall time, denials and 429 retries) and `<run id>.inputs.jsonl` (per-model-call input composition); runs made with the extended driver from 2026-09-10 on also have `<run id>.reads.jsonl` (full text of every tool result the first time it entered an agent's request). Every controlled run used `glm-5.3-flash` via `https://api.z.ai/api/anthropic` and the driver now at `research/common/profile_run.py` (then `s15_integrated_harness/scripts/profile_run.py`). Times are UTC; walls are `profile_end.wall_seconds`.

### `data/reuse_profiling/` — Part A (15 runs)

Written by `profile_run.py` run directly (the 2026-09-08 runs committed in f48d5b8, the X3 sweep in d67ab7f); analysed by `research/02_input_redundancy/file_read_reuse.py`. The 2026-09-08 runs used trace output mode `full`; the sweep runs used mode `summary` plus the `.reads.jsonl` sidecar, and their `profile_meta` records `git_head` 6218dd6. Several runs executed in parallel scratch checkouts (cwd `/tmp/claude-…/scratchpad/lanes/lane1`–`lane4`).

| Label | Run id | Start | Status · wall | Agents spawned | Used in | Status |
|---|---|---|---|---|---|---|
| X0-baseline | `run_20260908T142637_315792Z_f84d5d0c` | 2026-09-08 14:26 | completed · 19.6 s | 0 | Part A §2 (experiment table only; no results row) | published |
| X1-lead-inspect-r1 | `run_20260908T142714_251159Z_e804e8c7` | 14:27 | completed · 425.9 s | 0 | Part A §3.1, §3.2, §3.7 | published |
| X3-long-context-r1 | `run_20260908T143116_619037Z_c19f98ac` | 14:31 | completed · 808.2 s | 0 | Part A §3.1, §3.3, §3.8 (50k r1); A.W §4 | published |
| X2-team-shared-file-r1 | `run_20260908T143520_865718Z_8fe0885d` | 14:35 | timeout · 1,626.6 s | 3 | Part A §3.1, §3.4, §3.7; A.W §3–§4; Part B §2.6 (X2 run 1) | published |
| X1-lead-inspect-r2 | `run_20260908T143643_515536Z_44f9622a` | 14:36 | completed · 910.8 s | 0 | Part A §3.1, §3.2, §3.7; A.W §4 | published |
| X4-one-shot-r1 | `run_20260908T144512_126305Z_0db1b750` | 14:45 | completed · 260.0 s | 1 one-shot | Part A §3.1, §3.4 | published |
| X1-lead-inspect-notime-r1 | `run_20260908T144709_696444Z_a526a9d2` | 14:47 | completed · 786.0 s | 0 | Part A §3.1, §3.5 | published |
| X5-team-disjoint-files-r1 | `run_20260908T145622_011891Z_b004c56e` | 14:56 | completed · 236.6 s | 3 | Part A §3.1, §3.4; A.W §4; Part B §2.6 | published |
| X3-long-context-bigctx-r1 | `run_20260908T150029_442932Z_fb642730` | 15:00 | completed · 168.0 s | 0 | Part A §3.1, §3.3, §3.8 (200k r1) | published |
| X2-team-shared-file-r2 | `run_20260908T150237_103264Z_67505088` | 15:02 | timeout · 1,519.1 s | 6 | Part A §3.1, §3.4, §3.7; A.W §3–§4; Part B §2.6 (X2 run 2) | published |
| X3-long-context-50k-r2 | `run_20260910T205314_889085Z_77b56866` | 2026-09-10 20:53 | completed · 546.3 s | 0 | Part A §3.8 (50k r2) | published |
| X3-long-context-100k-r1 | `run_20260910T205314_926464Z_c5e965af` | 20:53 | completed · 1,114.0 s | 0 | Part A §3.8 (100k r1) | published |
| X3-long-context-200k-r2 | `run_20260910T205314_889183Z_cd87b874` | 20:53 | completed · 179.8 s | 0 | Part A §3.8 (200k r2) | published |
| X3-long-context-100k-r2 | `run_20260911T004342_016943Z_1e94e013` | 2026-09-11 00:43 | completed · 492.1 s | 0 | Part A §3.8 (100k r2) | published |
| X3-long-context-100k-r3 | `run_20260911T004342_000764Z_8df65660` | 00:43 | completed · 546.0 s | 0 | Part A §3.8 (100k r3) | published |

### `data/reuse_profiling/aborted/` — two interrupted first attempts (previously uncited)

Both committed in f48d5b8; each is a `.jsonl` trace plus its `.inputs.jsonl`.

- `run_20260908T143116_601503Z_9cb0d5cf` — label `X2-team-shared-file-r1`, 14:31:16–14:34:43, `profile_end` "interrupted" after 207.3 s, 3 teammates. Why it was run and what became of it: the first X2 attempt. Its teammate `model-calls` died on a provider 429 at 14:32:35, 36 s after it was spawned, and the lead needed three 429 retries; this is "the first X2 attempt (a teammate killed by a 429) was discarded" in Part A §2. It was re-run as `run_20260908T143520_865718Z_8fe0885d` (X2 r1) at 14:35:20. Its `profile_end` has no `rate_limit_retries` field, which every run from that re-run on records, so the driver's per-agent 429 retry appeared in between. Status: aborted.
- `run_20260908T143220_792155Z_ad90c9f8` — label `X4-one-shot-r1`, 14:32:20–14:34:44, interrupted after 143.3 s by a `KeyboardInterrupt` in the lead, 1 one-shot. Why it was run and what became of it: the first X4 attempt; its one-shot had already returned (14:33:17) and the lead had hit two 429s. It was stopped one second after the X2 attempt, apparently together with it; no writeup mentions it. It was re-run as `run_20260908T144512_126305Z_0db1b750` (X4 r1) at 14:45:12. Status: aborted.

### `data/redundancy_profiling/` — Part B at the 50k lead budget (8 runs)

Written by `research/02_input_redundancy/redundancy_workloads.py` through `profile_run.py` (trace output `full`, `.reads.jsonl` sidecar, 1,200 s ceiling), `git_head` ef352ae, 2026-09-10 16:23–18:02, committed in e7351e6; analysed by `research/common/input_redundancy.py` into `data/input_redundancy_runs.md`. Used in Part B §2.1–§2.5, the 50k arm of §6, and B.W.

| Label | Run id | Start | Status · wall | Teammates spawned | Status |
|---|---|---|---|---|---|
| PF-S-r1 | `run_20260910T162359_520533Z_871a5dd9` | 16:23 | timeout · 1,323.7 s | 4 | published |
| PF-P-r1 | `run_20260910T164624_156947Z_85d53560` | 16:46 | completed · 257.7 s | 3 | published |
| DC-S-r1 | `run_20260910T165102_836988Z_fdf59b69` | 16:51 | timeout · 1,333.8 s | 3 | published |
| DC-P-r1 | `run_20260910T171337_573021Z_c2c62242` | 17:13 | completed · 293.2 s | 3 | published |
| PF-S-r2 | `run_20260910T172000_330529Z_ed28df6f` | 17:20 | completed · 467.3 s | 3 | published |
| PF-P-r2 | `run_20260910T172808_708997Z_7c757c32` | 17:28 | completed · 338.2 s | 3 | published |
| DC-S-r2 | `run_20260910T173407_879825Z_fd69307d` | 17:34 | timeout · 1,364.6 s | 5 | published |
| DC-P-r2 | `run_20260910T175713_459406Z_1546f974` | 17:57 | completed · 335.2 s | 3 | published |

### `data/redundancy_profiling_ctx512k/` — Part B §6 at the 512k lead budget (8 runs)

Same runner and prompts with `--trace-dir` pointing here, `git_head` e7351e6 (`CONTEXT_LIMIT` 512,000 characters), 2026-09-10 19:59–21:09, committed in d67ab7f. Published side by side with the 50k arm in Part B §6 as a second configuration, not a replacement.

| Label | Run id | Start | Status · wall | Teammates spawned | Status |
|---|---|---|---|---|---|
| PF-S-c512-r1 | `run_20260910T195950_659429Z_3dbb95db` | 19:59 | completed · 788.5 s | 3 | published |
| PF-P-c512-r1 | `run_20260910T201320_073255Z_afd10af8` | 20:13 | completed · 316.8 s | 3 | published |
| DC-S-c512-r1 | `run_20260910T201857_813969Z_137f7c47` | 20:18 | completed · 673.2 s | 3 | published |
| DC-P-c512-r1 | `run_20260910T203031_959157Z_b16079a7` | 20:30 | completed · 783.1 s | 3 | published |
| PF-S-c512-r2 | `run_20260910T204356_114697Z_863f4ae3` | 20:43 | completed · 446.9 s | 3 | published |
| PF-P-c512-r2 | `run_20260910T205143_917586Z_a7ca2214` | 20:51 | completed · 275.0 s | 3 | published |
| DC-S-c512-r2 | `run_20260910T205639_815805Z_d5f611b0` | 20:56 | completed · 518.2 s | 3 | published |
| DC-P-c512-r2 | `run_20260910T210538_971622Z_73cac693` | 21:05 | completed · 237.9 s | 3 | published |

### `data/input_redundancy_runs.md` — per-run reports

1,548 lines of `research/common/input_redundancy.py` output: the eight 50k runs and a cross-run summary, the eight 512k runs and a summary, and the earlier runs re-measured (X2 runs 1 and 2 and X5 reconstructed at `f48d5b8`, the Qwen run at `6218dd6` with its own trace files excluded). Source of the Part B §2 and §6 tables and of the figures in the cleanup note under Part B §2.5. Frozen: `research/05_latency_breakdown/provider_probe.py` reads it as filler text, so it must not be edited. Status: published.

### Outside this folder

`s15_integrated_harness/traces/run_20260902T004901_584576Z_dc6a9685.jsonl` — run Q (Part A §3.1 Q rows, §3.6, §3.7; A.W §3; Part B §2.6); stays in `s15_integrated_harness/` with topic 01.

### Git-ignored local files

`redundancy_workloads.py` writes `<label>.console.log` next to each trace, and the Part B appendix and the 091626 digest mention these console logs. `*.log` is git-ignored and no such file exists in this checkout, so the console output of the Part B runs is not available here. No other local files belong to this topic.

## Source map

One row per section of every source file; the digests are split further where one section was partly kept, so that deck footers can point at the exact place. Section numbers inside Part A and Part B are unchanged: a reference to "§3.8" of the old Part A report is now "Part A §3.8".

| Old file · old section | New location | Status |
|---|---|---|
| `s15_integrated_harness/file_read_reuse_profile.md` · title, profiling question, short answer | Part A (top) | kept |
| `s15_integrated_harness/file_read_reuse_profile.md` · §1 Where file input enters a model, and why it repeats (code facts) | Part A §1 | kept (+ cleanup note on item 3) |
| `s15_integrated_harness/file_read_reuse_profile.md` · §2 Method | Part A §2 | kept |
| `s15_integrated_harness/file_read_reuse_profile.md` · §3 Results | Part A §3 | kept |
| `s15_integrated_harness/file_read_reuse_profile.md` · §3.1 Cross-run summary | Part A §3.1 | kept |
| `s15_integrated_harness/file_read_reuse_profile.md` · §3.2 Lead-only inspection (X1) | Part A §3.2 | kept |
| `s15_integrated_harness/file_read_reuse_profile.md` · §3.3 Long context (X3) | Part A §3.3 | kept |
| `s15_integrated_harness/file_read_reuse_profile.md` · §3.4 Teams -- shared file (X2) vs. disjoint files (X5), one-shot (X4) | Part A §3.4 | kept |
| `s15_integrated_harness/file_read_reuse_profile.md` · §3.5 Why the lead's prompt cache misses (replay evidence) | Part A §3.5 | kept (+ cleanup note) |
| `s15_integrated_harness/file_read_reuse_profile.md` · §3.6 Observational: the committed 28-teammate Qwen run | Part A §3.6 | kept (+ cleanup note) |
| `s15_integrated_harness/file_read_reuse_profile.md` · §3.7 What each agent actually holds, and for how long | Part A §3.7 | kept (+ two cleanup notes) |
| `s15_integrated_harness/file_read_reuse_profile.md` · §3.8 X3 budget sweep: the small budget costs rounds, not decode (five runs) | Part A §3.8 | kept (+ cleanup note) |
| `s15_integrated_harness/file_read_reuse_profile.md` · §4 Qualitative conclusions | Part A §4 | kept (+ cleanup note on item 5) |
| `s15_integrated_harness/file_read_reuse_profile.md` · §5 What would reduce the repetition | Part A §5 | kept |
| `s15_integrated_harness/file_read_reuse_profile.md` · Appendix: reproduce | Part A, Appendix: reproduce | kept |
| `weekly_progress/090926/teammate_overlap.md` · title and header (date; full report, tooling and trace pointers) | (none) | dropped (metadata; points to Part A) |
| `weekly_progress/090926/teammate_overlap.md` · §1 Questions | Part A §A.W (was §1) | kept |
| `weekly_progress/090926/teammate_overlap.md` · §2 Experiments: setup paragraph | (none) | dropped (duplicate of Part A §2 "Provider" and §1 item 3) |
| `weekly_progress/090926/teammate_overlap.md` · §2 Experiments: experiment table | (none) | dropped (duplicate of Part A §2 experiment table) |
| `weekly_progress/090926/teammate_overlap.md` · §2 Experiments: instrumentation paragraph | Part A §A.W (was §2) | kept |
| `weekly_progress/090926/teammate_overlap.md` · §3 Qualitative results: lead sentence and summary table | (none) | dropped (duplicate of Part A short answer and §3.1 aggregate table) |
| `weekly_progress/090926/teammate_overlap.md` · §3 Qualitative results: the seven bullets | Part A §A.W (was §3) | kept (+ two cleanup notes) |
| `weekly_progress/090926/teammate_overlap.md` · §4 Follow-up experiment: latency headroom from reusing repeated file content | Part A §A.W (was §4) | kept (+ two correction notes) |
| `weekly_progress/090926/teammate_overlap.md` · §5 Conclusion | Part A §A.W (was §5) | kept (+ correction note) |
| `s15_integrated_harness/input_redundancy_profile.md` · title, profiling question, short answer, pointer to §6 | Part B (top) | kept (+ cleanup note on bullet 5) |
| `s15_integrated_harness/input_redundancy_profile.md` · §1 Method | Part B §1 | kept |
| `s15_integrated_harness/input_redundancy_profile.md` · §1.1 What is measured | Part B §1.1 | kept |
| `s15_integrated_harness/input_redundancy_profile.md` · §1.2 Instrumentation | Part B §1.2 | kept |
| `s15_integrated_harness/input_redundancy_profile.md` · §1.3 Workloads | Part B §1.3 | kept |
| `s15_integrated_harness/input_redundancy_profile.md` · §2 Results | Part B §2 | kept |
| `s15_integrated_harness/input_redundancy_profile.md` · §2.1 Cross-teammate redundancy (byte-weighted, exact `range` provenance) | Part B §2.1 | kept |
| `s15_integrated_harness/input_redundancy_profile.md` · §2.2 The same quantity under each identification method | Part B §2.2 | kept |
| `s15_integrated_harness/input_redundancy_profile.md` · §2.3 Workload shape of the teammates | Part B §2.3 | kept (+ correction note) |
| `s15_integrated_harness/input_redundancy_profile.md` · §2.4 Age of the duplicates | Part B §2.4 | kept |
| `s15_integrated_harness/input_redundancy_profile.md` · §2.5 What happened in each workload | Part B §2.5 | kept (+ cleanup note on PF-P) |
| `s15_integrated_harness/input_redundancy_profile.md` · §2.6 The earlier team runs, re-measured on bytes | Part B §2.6 | kept |
| `s15_integrated_harness/input_redundancy_profile.md` · §3 Interpretation | Part B §3 | kept (+ correction note on item 5) |
| `s15_integrated_harness/input_redundancy_profile.md` · §4 Harness observations made along the way | Part B §4 | kept |
| `s15_integrated_harness/input_redundancy_profile.md` · §5 What would remove the redundancy | Part B §5 | kept |
| `s15_integrated_harness/input_redundancy_profile.md` · §6 Rerun with the lead's context budget at 512,000 characters | Part B §6 | kept |
| `s15_integrated_harness/input_redundancy_profile.md` · Appendix: reproduce | Part B, Appendix: reproduce | kept |
| `weekly_progress/091626/teammate_input_redundancy.md` · title and header (date; full report, tooling and trace pointers) | (none) | dropped (metadata; points to Part B) |
| `weekly_progress/091626/teammate_input_redundancy.md` · §1 Questions | (none) | dropped (restates the Part B profiling question; answered in Part B §2-§3) |
| `weekly_progress/091626/teammate_input_redundancy.md` · §2 Setup (harness, instrumentation, measure, granularity table, synthetic check, workloads) | (none) | dropped (duplicate of Part B §1.1-§1.3) |
| `weekly_progress/091626/teammate_input_redundancy.md` · §3 Results: A. Cross-teammate redundancy | Part B §2.1 | dropped (duplicate of Part B §2.1) |
| `weekly_progress/091626/teammate_input_redundancy.md` · §3 Results: B. Under each granularity | Part B §2.2 | dropped (duplicate of Part B §2.2) |
| `weekly_progress/091626/teammate_input_redundancy.md` · §3 Results: C. Workload shape of the teammates | Part B §2.3 | dropped (duplicate of Part B §2.3) |
| `weekly_progress/091626/teammate_input_redundancy.md` · §3 Results: D. Age of the duplicates (and the late-tails sentence) | Part B §2.4 | dropped (duplicate of Part B §2.4) |
| `weekly_progress/091626/teammate_input_redundancy.md` · §3 Results: E. Earlier runs re-measured on bytes | Part B §2.6 | dropped (duplicate of Part B §2.6) |
| `weekly_progress/091626/teammate_input_redundancy.md` · §3 Results: per-workload observations paragraph | Part B §B.W (was §3) | kept |
| `weekly_progress/091626/teammate_input_redundancy.md` · §4 Conclusions: item 1 | Part B §B.W (was §4 item 1) | kept |
| `weekly_progress/091626/teammate_input_redundancy.md` · §4 Conclusions: item 2 | Part B (short answer bullet 3; §3 item 3) | dropped (duplicate of Part B short answer bullet 3 and §3 item 3) |
| `weekly_progress/091626/teammate_input_redundancy.md` · §4 Conclusions: item 3 | Part B §B.W (was §4 item 3) | kept |
| `weekly_progress/091626/teammate_input_redundancy.md` · §4 Conclusions: item 4 | Part B (short answer bullet 5; §3 item 5) | dropped (duplicate of Part B short answer bullet 5 and §3 item 5) |
| `weekly_progress/091626/teammate_input_redundancy.md` · §4 Conclusions: item 5 | Part B §3 item 4 | dropped (duplicate of Part B §3 item 4 and §2.6) |
| `weekly_progress/091626/teammate_input_redundancy.md` · §4 Conclusions: item 6 | Part B §5 | dropped (duplicate of Part B §5) |
| `weekly_progress/091626/teammate_input_redundancy.md` · §5 Rerun at 512k: introduction | Part B §B.W (was §5) | kept (+ cleanup note) |
| `weekly_progress/091626/teammate_input_redundancy.md` · §5 Rerun at 512k: side-by-side table | Part B §6 | dropped (duplicate of Part B §6) |
| `weekly_progress/091626/teammate_input_redundancy.md` · §5 Rerun at 512k: note on shrinks | Part B §B.W (was §5) | kept |
| `weekly_progress/091626/teammate_input_redundancy.md` · §5 Rerun at 512k: granularity table | Part B §6 | dropped (duplicate of Part B §6) |
| `weekly_progress/091626/teammate_input_redundancy.md` · §5 Rerun at 512k: workload-shape table | Part B §6 | dropped (duplicate of Part B §6) |
| `weekly_progress/091626/teammate_input_redundancy.md` · §5 Rerun at 512k: observations | Part B §6 | dropped (duplicate of Part B §6 "What changed and what did not") |
| `weekly_progress/091626/teammate_input_redundancy.md` · §6 Conclusions after the rerun | Part B §B.W (was §6) | kept (+ cleanup note on item 2) |

## Cleanup notes

Every bracketed note added to the verbatim text, one bullet each. Paths in the verbatim text were rewritten to the 2026-09-23 layout (scripts, data and merged notes point to `research/…`); bracketed notes, `_Formerly …_` lines and the Source map keep the old names on purpose.

- Part A §1, item 3 — cleanup note: the identical-request probe, the §3.5 replay and the §3.7 lifetime probe have no committed script or output.
- Part A §3.5, closing paragraph — cleanup note: no committed replay; the committed X2 traces give per-teammate cache hit rates of 79-94%, so neither 84-90% (here) nor 84-94% (A.W) is reproduced exactly.
- Part A §3.6 — cleanup note: six, not five, teammate calls were rejected at the 262,144-token limit in the committed trace (090926 slide 8 says the same).
- Part A §3.7, task-history paragraph — cleanup note: six, not five (pointer to §3.6).
- Part A §3.7, cache-lifetime probe — cleanup note: no committed script or output.
- Part A §3.8, per-round autopsy paragraph — cleanup note: the 2026-09-10 streaming probe has no committed script or output.
- Part A §4, item 5 — cleanup note: with all seven X3 runs of §3.8 the sub-working-set range is 30-120 rounds (0-92% re-acquiring), not 45-120 (67-92%).
- A.W (was §3), teammate-context bullet — cleanup note: six rejected teammates, not five.
- A.W (was §3), lead-cache bullet — cleanup note: 84-94% here versus 84-90% in Part A §3.5.
- A.W (was §4), after the Method paragraph — correction note (2026-09-10 probe: 4.6 s + 0.052 ms per uncached token) and cleanup note (no committed script fits the model; 090926 slide 10 says a refit on the committed sidecars reproduces it).
- A.W (was §4), after the prefill-headroom table — correction note: ~8x smaller (X2 run 2 ~12 s, not 95 s).
- A.W (was §5) — correction note: the "1-3%" rests on the superseded pooled fit.
- Part B short answer, bullet 5 — cleanup note: decode shares and prefill seconds here and in §2.3, §3 item 5 and §6 use the superseded pooled fit and were not recomputed.
- Part B §2.3, caption — correction note: the fit named there was superseded on 2026-09-10.
- Part B §2.5, PF-P paragraph — cleanup note: the shared file is 79% of the teammate bytes fetched; 52.8% is its second and third copies.
- Part B §3, item 5 — correction note: decode shares and ~30-47 s rest on the superseded fit.
- B.W (was §5), introduction — cleanup note: "the lead table below" is the side-by-side table in Part B §6.
- B.W (was §6), item 2 — cleanup note: 45-53% matches neither the 512k tables (47-53%) nor both budgets (45-54%).
