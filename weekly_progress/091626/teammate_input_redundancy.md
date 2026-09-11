# Byte-level redundancy of teammate file input, by workload

Weekly progress, 2026-09-16. Full report: `s15_integrated_harness/input_redundancy_profile.md`
(per-run tables: `s15_integrated_harness/input_redundancy_runs.md`).
Tooling: `s15_integrated_harness/scripts/profile_run.py` (driver, now with a full-content
`.reads.jsonl` sidecar), `scripts/redundancy_workloads.py` (workload prompts + runner),
`scripts/input_redundancy.py` (byte-level analyzer; `--tables` prints the summary tables).
Traces: `s15_integrated_harness/traces/redundancy_profiling/` (8 runs, console logs).

## 1. Questions

1. Of the file content that different **teammate agents** pull into their model inputs, how much
   is the **same bytes** another teammate already pulled in? Measured on bytes, not on whole-result
   SHA-256 (too strict: a read of lines 1-700 and a read of lines 0-800 share nothing under it).
2. Does the answer depend on the workload: **prefill-heavy** (read a lot, write little) versus
   **decode-heavy** (read little, write a lot)?
3. What do the redundant bytes cost in prompt volume, KV memory and latency, and how fresh are
   the duplicates (could any cache serve them)?

## 2. Setup

Harness: s15 integrated harness at `ef352ae` (lead loop + persistent teammate threads; teammates
may run display-only shell since `968a33c`), model `glm-5.3-flash` via z.ai, repository
read-only (writes and mutating shell denied), fresh state per run, 1,200 s ceiling, lead + 3
teammates per run, two repetitions per workload.

Instrumentation. The trace's own `HARNESS_TRACE_OUTPUT=full` truncates any tool result over
2,048 characters (`emit()` re-summarises with `argument_chars`), so the driver now writes
`<trace>.reads.jsonl` with the **full text of every tool_result the first time it appears in an
agent's request**, plus per-call result ids and the output's text/thinking split. That is exactly
the bytes the model saw, per agent. Earlier runs (X2, X5, Qwen) are reconstructed from the git
blob of their commit and accepted only when the SHA-256 matches the trace (178/178 for X2 run 2).

Measure. Every file-input result (`read_file`, plus `cat`/`head`/`tail`/`grep`/`sed -n` shell
reads) is split into byte-weighted units and classified chronologically as **new**, **intra**
(same agent had it) or **cross** (another agent had it first). Five granularities:

| granularity | unit | catches / misses |
|---|---|---|
| `whole` | SHA-256 of the whole result | only byte-identical results (last week's measure) |
| `range` | (file, line number) | any overlap of paging windows of the same file; misses copies in other files |
| `line` | exact line text (>= 8 non-blank chars) | copied code across files; over-counts common statements |
| `cdc256` | content-defined chunk, gear hash, ~350 B mean | alignment-insensitive duplicate stretches anywhere (~a 64-token KV block) |
| `cdc1k` | content-defined chunk, ~1.6 KB mean | only long identical stretches |

Synthetic check: two agents paging one 3,000-line text with different windows give the second
agent 0% under `whole`, 100% under `range`/`line`, 98% under `cdc256`, 93% under `cdc1k`.

Workloads (three task-board items, one teammate each, lead synthesises and shuts the team down):

| id | axis | teammate tasks |
|---|---|---|
| PF-S | prefill-heavy, shared corpus | three audits of `s15/code.py` (146 KB) + `trace_runtime.py` (22 KB), read completely, report <= 15 lines |
| PF-P | prefill-heavy, partial overlap | compare own chapter `code.py` (s08 24 KB / s10 20 KB / s13 71 KB) against the shared s15 `code.py`, report <= 15 lines |
| DC-S | decode-heavy, shared small input | tutorial / 40-case test plan / design critique, all from `GLOSSARY.md` (13 KB), >= 1,200 words each |
| DC-P | decode-heavy, disjoint inputs | one >= 1,200-word tutorial per chapter from that chapter's README (s06 / s10 / s13) |

## 3. Results

**A. Cross-teammate redundancy** (share of teammate-fetched bytes another teammate already held,
`range`; SHA-256 whole-result figure for comparison)

| workload | run 1 | run 2 | `whole` (SHA-256) r1 / r2 | resident copies | redundant file content as share of teammate prompt tok |
|---|---:|---:|---:|---:|---:|
| PF-S | 68.8% | 64.1% | 3.0% / 0.0% | 4.0x / 3.0x | 45% / 54% |
| PF-P | 52.8% | 52.8% | 0.0% / 0.0% | 2.1x / 2.1x | 50% / 46% |
| DC-S | 66.7% | 80.0% (5 teammates) | 66.7% / 80.0% | 3.0x / 5.0x | 15% / 14% |
| DC-P | 0.0% | 0.0% | 0.0% / 0.0% | 1.0x / 1.0x | 0% / 0% |

**B. Under each granularity** (cross-teammate share; `range` and `cdc256` agree within 1-4 points)

| run | `whole` | `range` | `line` | `cdc256` | `cdc1k` |
|---|---:|---:|---:|---:|---:|
| PF-S r1 / r2 | 3.0 / 0.0% | 68.8 / 64.1% | 59.3 / 55.2% | 66.9 / 61.6% | 58.9 / 49.2% |
| PF-P r1 / r2 | 0.0 / 0.0% | 52.8 / 52.8% | 46.9 / 46.9% | 52.4 / 50.5% | 46.6 / 37.3% |
| DC-S r1 / r2 | 66.7 / 80.0% | 66.7 / 80.0% | 66.3 / 79.6% | 66.7 / 80.0% | 66.7 / 80.0% |
| DC-P r1 / r2 | 0.0 / 0.0% | 0.0 / 0.0% | 11.5 / 4.0% | 2.3 / 0.1% | 1.4 / 0.0% |

**C. Workload shape of the teammates** (prefill/decode split from last week's glm-5.3-flash
latency fit; "file content" counts every re-send)

| run | calls | prompt tok | cache hit | output tok | thinking share | output / prompt | est. decode share of model time | file content share of prompt | redundant first sends (tok) | share of uncached |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| PF-S r1 | 69 | 2,929,726 | 90.6% | 121,040 | 91% | 0.041 | 80% | 71% | 111,652 | 40% |
| PF-S r2 | 46 | 1,354,872 | 88.7% | 28,046 | 81% | 0.021 | 63% | 84% | 74,298 | 49% |
| PF-P r1 | 17 | 369,583 | 63.4% | 10,664 | 81% | 0.029 | 61% | 89% | 64,365 | 48% |
| PF-P r2 | 35 | 836,835 | 82.6% | 18,962 | 77% | 0.023 | 61% | 87% | 63,964 | 44% |
| DC-S r1 | 24 | 379,303 | 65.8% | 92,780 | 52% | 0.245 | 92% | 15% | 9,027 | 7% |
| DC-S r2 | 47 | 815,452 | 80.6% | 165,188 | 44% | 0.203 | 92% | 14% | 14,529 | 9% |
| DC-P r1 | 13 | 58,649 | 61.7% | 11,410 | 23% | 0.195 | 82% | 76% | 0 | 0% |
| DC-P r2 | 14 | 71,102 | 65.7% | 13,045 | 30% | 0.183 | 82% | 75% | 0 | 0% |

**D. Age of the duplicates** (time since another teammate first fetched the same bytes)

| run | cross bytes | p50 | p90 | within 60 s | over 300 s |
|---|---:|---:|---:|---:|---:|
| PF-S r1 / r2 | 504 KB / 336 KB | 5 s / 12 s | 970 s / 29 s | 67% / 94% | 33% / 0% |
| PF-P r1 / r2 | 293 KB / 293 KB | 21 s / 11 s | 26 s / 31 s | 100% / 100% | 0% / 0% |
| DC-S r1 / r2 | 26 KB / 51 KB | 23 s / 3 s | 33 s / 362 s | 100% / 50% | 0% / 50% |

The late tails are second waves: a fourth teammate spawned 16 min into PF-S run 1, and two extra
teammates spawned in DC-S run 2 after provider 429s stalled the first wave.

**E. Earlier runs re-measured on bytes**

| run | teammates | bytes | `whole` cross | `range` cross | `cdc256` cross | resident copies | redundant share of teammate prompt tok |
|---|---:|---:|---:|---:|---:|---:|---:|
| X2 run 1 (3 analysts on `code.py`) | 3 | 483 KB | 2.7% | 59.5% | 54.7% | 3.0x | 41% |
| X2 run 2 (two waves of 3) | 6 | 1.08 MB | 66.5% | 70.4% | 69.5% | 4.9x | 64% |
| X5 (disjoint chapter files) | 3 | 118 KB | 0.0% | 0.0% | 1.4% | 1.0x | 0% |
| Qwen 28-teammate stress run | 28 | 3.23 MB | 39.9% | 77.1% | 74.9% | 7.4x | 28% |

Per-workload observations: PF-S teammates page the shared file in 500/700/800/1,000-line windows
starting at line 0 or 1 (only 4 of 77 results byte-identical), then fall back to `grep -n`, of
which 10-12 calls per run were denied (double-quoted alternations and `cd dir && grep` are not
classified read-only); the lead lost a report to its own compaction and spawned a duplicate
teammate (run 1 hit the ceiling; run 2 finished in 467 s). PF-P finished cleanly both times
(258 s, 338 s) with every teammate loading the whole 146 KB shared file to compare one chapter.
DC-S teammates read the glossary once and wrote 22-47k output tokens each (4-8 calls truncated
at `max_tokens=8000`); the lead's "assignment corrections" kept both runs at the ceiling. DC-P
finished in ~300 s with nothing shared.

## 4. Conclusions

1. **When teammates share files, 52-80% of the bytes they load are copies of bytes another
   teammate already holds; when they do not, 0%.** The number is essentially arithmetic: with N
   teammates loading one shared file the redundant share of the shared bytes is (N-1)/N (66.7%
   for 3, 80% for 5), diluted by private files (PF-P: 2/3 of the shared file's 79% share =
   52.8%). It is set by corpus overlap, not by the prefill/decode balance.
2. **SHA-256 of whole results does not measure it.** 0-3% on the four prefill-heavy shared-corpus
   runs (and X2 run 1) against 53-69% on bytes, because paging windows differ; it agrees only
   when everyone reads a file in one call. Exact (file, line) provenance and ~350 B
   content-defined chunks agree within a few points and are the measures to use.
3. **The workload decides the cost.** In the prefill-heavy runs file content is 70-89% of teammate
   prompt tokens and the redundant copies are 45-54% of all prompt tokens (40-49% of the
   uncached ones); the other file half is each teammate's own first copy plus private files,
   which no cross-teammate scheme removes. In the decode-heavy shared run the redundancy rate is
   just as high but the volume is small: 14-15% of prompt tokens, 7-9% of uncached, and output
   dominates.
4. **KV memory sees it more than latency does.** Teammates never compact, so the team holds 2-5
   resident copies of the same file at the end of a run. Latency headroom is small on this
   provider: decode is 61-92% of teammate model time, the prefix cache serves 63-91% of re-sent
   tokens, and skipping the redundant prefill would save at most the first sends (74-112k tokens,
   ~30-47 s summed over the team, in PF-S).
5. **Duplicates are fresh within a wave, stale across waves.** 94-100% of duplicated bytes arrive
   within 60 s of the first copy in single-wave runs (the three teammates open the shared file
   nearly simultaneously); second waves re-fetch them 6-16 min or hours later, past the
   provider cache's ~5 min idle lifetime, and no hosted prefix cache can share them across
   contexts anyway (positions differ).
6. **Remedies are harness-side:** a shared, position-stable file block at the start of every
   teammate prompt (one prefill serves the team; removes the duplicate KV copies on a self-hosted
   server), normalised paging windows so whole-window hashes match across teammates, task
   descriptions that carry line ranges instead of forcing whole-file loads, and a read-only shell
   classifier that accepts double-quoted patterns and `sed -n`.

## 5. Rerun with the lead's context budget raised from 50k to 512k characters

Change (commit `e7351e6`): `CONTEXT_LIMIT = CONTEXT_TOKEN_LIMIT (128,000) * CHARS_PER_TOKEN (4)` =
512,000 characters, up from 50,000. This is the **lead's** compaction budget (micro-compaction
placeholders, `fit_tool_results`, summary compaction); teammates never compact, and `snip_compact`
still archives the middle of the history whenever the lead exceeds 50 messages. Same four
workloads, same prompts, two repetitions, traces in
`s15_integrated_harness/traces/redundancy_profiling_ctx512k/` (labels `*-c512-r1/r2`); the lead
table below comes from `input_redundancy.py --lead-table`.

**Side by side (50k r1 / r2 -> 512k r1 / r2)**

| workload | cross-teammate redundancy (`range`) | teammates spawned | status / wall | lead calls | lead summary compactions / shrinks | lead cache hit | teammate prompt tok | redundant share of teammate prompt tok |
|---|---|---|---|---|---|---|---|---|
| PF-S | 68.8 / 64.1 -> **61.3 / 61.5%** | 4 / 3 -> 3 / 3 | timeout 1324 s / 467 s -> 788 s / 447 s | 49 / 18 -> 36 / 17 | 2+6 / 0 -> 0+7 / 0 | 25 / 26 -> 9 / 18% | 2.93M / 1.35M -> 2.08M / 2.08M | 45 / 54 -> 47 / 53% |
| PF-P | 52.8 / 52.8 -> **52.8 / 52.8%** | 3 / 3 -> 3 / 3 | 258 s / 338 s -> 317 s / 275 s | 14 / 16 -> 15 / 15 | 0 / 0 -> 0 / 0 | 33 / 28 -> 21 / 23% | 370k / 837k -> 1.01M / 426k | 50 / 46 -> 48 / 47% |
| DC-S | 66.7 / 80.0 -> **66.7 / 59.9%** | 3 / 5 -> 3 / 3 | timeout 1334 s / timeout 1365 s -> 673 s / 518 s | 52 / 55 -> 56 / 26 | 4+6 / 4+4 -> 0 / 0 | 17 / 15 -> 6 / 10% | 379k / 815k -> 140k / 90k | 15 / 14 -> 24 / 27% |
| DC-P | 0.0 / 0.0 -> **0.0 / 0.0%** | 3 / 3 -> 3 / 3 | 293 s / 335 s -> 783 s / 238 s | 14 / 17 -> 18 / 20 | 1+1 / 1+1 -> 0 / 0 | 21 / 24 -> 4 / 22% | 59k / 71k -> 825k / 66k | 0 / 0 -> 0 / 0% |

(shrinks = `context_prepared` events that reduced the history: micro/fit compaction at 50k,
`snip_compact` at both limits.)

**512k runs under each granularity** (cross-teammate share of teammate bytes)

| run | `whole` | `range` | `line` | `cdc256` | `cdc1k` | teammate bytes | resident copies |
|---|---:|---:|---:|---:|---:|---:|---:|
| PF-S-c512 r1 / r2 | 4.0 / 9.4% | 61.3 / 61.5% | 52.8 / 53.0% | 59.7 / 59.5% | 52.9 / 51.1% | 549 / 547 KB | 3.0x / 3.0x |
| PF-P-c512 r1 / r2 | 1.3 / 26.4% | 52.8 / 52.8% | 46.9 / 46.8% | 50.6 / 52.5% | 32.9 / 48.8% | 554 / 555 KB | 2.1x / 2.1x |
| DC-S-c512 r1 / r2 | 66.7 / 59.9% | 66.7 / 59.9% | 66.3 / 59.6% | 66.7 / 59.9% | 66.7 / 59.9% | 39 / 43 KB | 3.0x / 3.0x |
| DC-P-c512 r1 / r2 | 0.0 / 0.0% | 0.0 / 0.0% | 10.5 / 0.9% | 2.0 / 0.0% | 1.1 / 0.0% | 83 / 49 KB | 1.0x / 1.0x |

**512k workload shape (teammates)**

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

Observations:

- **Teammate-side redundancy did not move.** PF-P is 52.8% in all four runs; PF-S is 61% twice
  (a few points under the 64-69% of the 50k runs because no fourth teammate appeared and the
  auditors took more of their input through shell searches); DC-S is 66.7% and 59.9% (one writer
  also pulled 4.4 KB of `find` output); DC-P stays at 0%. With three teammates the shared bytes
  cannot exceed (N-1)/N = 66.7% redundancy, and every shared-corpus run sits at or just under it.
- **The lead-side pathologies disappeared.** Zero summary compactions and zero placeholder
  rewrites in all eight runs (the 50k runs had 1-4 summary compactions in five of eight), so no
  report was lost to compaction, the lead never re-requested a result, created a duplicate task or
  spawned a second wave, and every run finished before the 1,200 s ceiling: PF-S 788 / 447 s
  (was a timeout and 467 s), DC-S 673 / 518 s (was two timeouts). `snip_compact` still shrank the
  PF-S run-1 lead history seven times once it passed 50 messages (52-58k chars).
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

## 6. Conclusions after the rerun

1. The 50k -> 512k change fixes the **lead**: no compaction, no lost reports, no second waves, no
   timeouts, 1.7-2.6x shorter wall time where runs had hit the ceiling. It does not touch the
   **teammates'** input redundancy, which stayed at 52-67% for shared corpora and 0% for disjoint
   ones, because that redundancy is set by corpus overlap and team size ((N-1)/N), not by any
   context budget: teammates never compact regardless of the limit.
2. The cost picture for prefill-heavy teams is unchanged: duplicated file content is still
   45-53% of teammate prompt tokens (39-47% of the uncached ones), with 2-3 resident copies of the
   146 KB file. For decode-heavy shared teams the rate is the same but the volume is small
   (24-31% of a much smaller prompt).
3. The remedies for teammate redundancy therefore remain harness-side sharing (a position-stable
   shared file block, normalised paging windows, line ranges in task descriptions) and a
   read-only shell classifier that accepts double-quoted patterns and pipes into `wc`/`head`; the
   lead's budget should stay large, and the remaining lead-side item is a cacheable prefix
   (timestamp out of the system prompt, stable `snip_compact` markers).
