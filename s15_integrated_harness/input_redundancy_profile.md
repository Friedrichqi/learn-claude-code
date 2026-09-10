# Byte-level redundancy of file input across s15 teammates, by workload

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

Per-run reports for every run (workload runs, X2, X5, Qwen) are in `input_redundancy_runs.md`.

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

- `scripts/profile_run.py` (extended): besides the trace and the per-call `.inputs.jsonl`, it now
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
- `scripts/input_redundancy.py`: the analyzer (standard library only; ~1 s per run).
- `scripts/redundancy_workloads.py`: the workload prompts and sequential runner.

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


## Appendix: reproduce

```sh
# four workloads, one repetition (traces + sidecars + console logs in traces/redundancy_profiling/)
python3 s15_integrated_harness/scripts/redundancy_workloads.py --rep r1

# byte-level report for every run in a directory (sidecar mode), or for old traces via git reconstruction
python3 s15_integrated_harness/scripts/input_redundancy.py s15_integrated_harness/traces/redundancy_profiling
python3 s15_integrated_harness/scripts/input_redundancy.py s15_integrated_harness/traces/reuse_profiling/run_20260908T150237_103264Z_67505088.jsonl --git-rev f48d5b8
python3 s15_integrated_harness/scripts/input_redundancy.py s15_integrated_harness/traces/run_20260902T004901_584576Z_dc6a9685.jsonl --git-rev 6218dd6 --exclude 'traces/|\.task_outputs'
```
