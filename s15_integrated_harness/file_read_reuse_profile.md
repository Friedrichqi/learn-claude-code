# File-read reuse in the s15 integrated harness: lead vs. teammates

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

Everything below is reproducible with `scripts/profile_run.py` (runs one non-interactive session
and records a per-call input sidecar) and `scripts/file_read_reuse.py` (computes the metrics from
the harness's own JSONL traces). Traces of the new runs are in `traces/reuse_profiling/`.

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
4. **Teammates paginate large files** (`offset`/`limit`), which shows up as many `read_file`
   calls on the same path with different content. That is not redundancy, so every table below
   separates *same path* from *byte-identical content* (SHA-256 of the returned text, recorded by
   the trace).

## 2. Method

- **Traces.** Each run writes the harness's JSONL trace (`trace_runtime.py`). Every `tool_end`
  carries tool, arguments (path/offset/limit or command), status, and the result's character
  count and SHA-256, so repeated content is detectable without storing it.
- **Driver.** `scripts/profile_run.py` runs one session non-interactively from the repository
  root: it auto-approves shell prompts but denies mutating commands and `write_file`/`edit_file`
  (read-only policy; denials are counted), wipes `.memory/.tasks/.mailboxes/.transcripts/
  .task_outputs` per run, waits until teammates are idle, shuts them down, and writes a sidecar
  `<trace>.inputs.jsonl` with, for every model call, the characters of `read_file`/`bash`/other
  tool results present in the request, the number of compaction placeholders, and provider usage.
  It also retries provider 429s for every agent (the harness retries only lead calls, 3x with
  sub-second delays; a teammate dies on the first 429). Two interventions are flags:
  `--no-timestamp` and `--context-limit`.
- **Analyzer.** `scripts/file_read_reuse.py <trace|dir> [--calls] [--exclude REGEX]` pairs tool
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
| Q | observational | committed 28-teammate Qwen stress run (`traces/run_20260902T004901_..._dc6a9685.jsonl`), analysed with `--exclude 'traces/'` to drop its self-observation loop | 1 |

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

### 3.6 Observational: the committed 28-teammate Qwen run

Excluding the run's self-observation of its own trace file: 441 reads over 52 files; teammates
made 373 reads of 30 files (235 pagination re-reads, 105 cross-agent same-file, 70 byte-identical
to an earlier read; 91% of teammate reads were of files another teammate had already read); the
lead made 40 reads with 11 re-reads, 9 of them after a compaction shrink (82%). Teammates re-sent
each fetched byte 15.7x and accumulated 34.6M prompt tokens against the lead's 1.4M; five teammate
contexts grew past 250k tokens and the provider rejected them (`maximum context length is 262144
tokens`), which is the failure mode of "no compaction for children". 105 of 109 bash calls were
denied (all teammate calls), forcing `read_file` pagination.

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
python3 s15_integrated_harness/scripts/profile_run.py --label X3 \
  --prompt "Read all chapter README.md files from s01_agent_loop through s17_goal_loop ... do not create or modify any files."
python3 s15_integrated_harness/scripts/profile_run.py --label X3-big --context-limit 200000 --prompt "..."
python3 s15_integrated_harness/scripts/profile_run.py --label X1-notime --no-timestamp --prompt "..."

# metrics for one trace (with per-call request composition) or a directory of traces
python3 s15_integrated_harness/scripts/file_read_reuse.py s15_integrated_harness/traces/reuse_profiling --calls
python3 s15_integrated_harness/scripts/file_read_reuse.py s15_integrated_harness/traces/run_20260902T004901_584576Z_dc6a9685.jsonl --exclude 'traces/'
```
