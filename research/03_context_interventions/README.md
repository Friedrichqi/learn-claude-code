# 03 · Context interventions on the lead loop

> **Status: ARCHIVED.** Labelled "no-use" in commit `64d1b11` (2026-09-11, "no-use: three experimental optimization with claude"). The capture flags `--batch-read`, `--skeleton-demote`, `--stable-prefix` and `--deny-bash` of `profile_run.py` (now `research/common/profile_run.py`) and the `apply_interventions` hook they relied on were never committed: the working tree was reset (recorded in `research/related_work/teammate_memory_management.md` §7, the "reset to `d67ab7f`" note), so these runs cannot be reproduced. `research/03_context_interventions/ix_analyze.py` still runs on the committed traces and reproduces the per-run numbers behind the tables (re-checked in the 2026-09-23 cleanup). The offline test `s15_integrated_harness/tests/test_context_interventions.py` (hard-coded `/home/yq335` path; called the never-committed `apply_interventions`) was deleted in the cleanup.
>
> **Dates.** Runs 2026-09-11, 03:24-10:01 UTC (2026-09-10 23:24 to 2026-09-11 06:01 -0400). Report committed 2026-09-11 (`64d1b11`); shell-denied matrix and threats rewritten 2026-09-13 (`bdc4788`). Weekly digest: session 2026-09-10 to 2026-09-12, committed 2026-09-13 (`bdc4788`), extended 2026-09-15 (`e0e522b`).
>
> **Model and provider.** `glm-5.3-flash` via z.ai's Anthropic-compatible endpoint (`https://api.z.ai/api/anthropic`); workload X3, the 17 chapter READMEs, at `CONTEXT_LIMIT` 50k, 100k and 200k chars.
>
> **Presented.** `weekly_progress/091626/slides.html` slide 10 (digest Q1, Q2, Q4 and this report) and slide 11 (digest Q1 and Q4 behind direction 12); the digest is also a row of slide 4's table of notes. Slide numbers are the deck's bottom-right counter.

**Contents**

- *Three context-hierarchy interventions on the s15 lead loop*: the report, verbatim (formerly `s15_integrated_harness/context_intervention_profile.md`): design, results, blind judging, the shell-denied matrix, conclusions, the compaction ladder (the one kept copy), threats, reproduce.
- *A.W Additions* from the weekly digest `weekly_progress/091626/agent_e2e_bottleneck.md`: its introduction, Q2 (the cost of one round, in part), Q3 (the tiered-memory candidate ladder), Q4 (in part), Q5 (in-engine execution against KV splice), Q6 (product survey), conclusion, open items, artifacts.
- *Data inventory*, *Source map*, *Cleanup notes*.

**Files in this folder**

| path | purpose |
|---|---|
| `research/03_context_interventions/README.md` | this document |
| `research/03_context_interventions/ix_analyze.py` | offline analyzer of the X3 intervention traces: rounds, re-acquisition classes, batch delivery, residency from the sidecars, cache and token totals; `python3 ix_analyze.py <trace-dir> [--answers OUT] [--json OUT] [--rounds LABEL]`, one directory, not recursive |
| `research/03_context_interventions/data/context_interventions/` | the 16 valid runs (trace, `.inputs.jsonl`, `.reads.jsonl` each) |
| `research/03_context_interventions/data/context_interventions/unfixed_driver/` | 9 runs on the pre-review capture driver: the `unfixed` rows |
| `research/03_context_interventions/data/context_interventions/quota_killed/` | 16 runs killed by the provider's 5-hour usage cap |

Elsewhere: `research/common/profile_run.py` (the committed driver; it lacks the intervention flags); `research/related_work/teammate_memory_management.md` (the session that wrote the compaction-ladder section).

---

# Three context-hierarchy interventions on the s15 lead loop
_Formerly `s15_integrated_harness/context_intervention_profile.md` (2026-09-11, commit `64d1b11`; shell-denied matrix added 2026-09-13, commit `bdc4788`)._

Earlier profiling established that the s15 lead's bottleneck under a tight `CONTEXT_LIMIT` is
*round count*: content the model still needs is evicted, and getting it back costs a whole agentic
round whose price is ~4.6 s of fixed provider latency (~80% of a cheap round), not data movement.
Seven runs of one workload showed 30-120 rounds below the working set versus 6-7 above it.

This report tests three fixes derived from that diagnosis:

| # | intervention | flag | mechanism it targets |
|---|---|---|---|
| 1 | batch the faults | `--batch-read` | one `read_files` call fetches many files, so N faults cost 1 round instead of N |
| 2 | demote to a map, not a path | `--skeleton-demote` | an evicted result becomes a structural outline with 0-based `read_file` offsets, so "where is X" needs no fault |
| 4 | keep the prompt prefix stable | `--stable-prefix` | no per-second timestamp, one fixed archive marker, and rare batched evictions instead of one rolling rewrite per round |

All three are flags on `research/common/profile_run.py`, which monkeypatches the loaded harness module, so
the committed harness is byte-identical between control and treated runs. `--deny-bash` is a
control that removes the shell so every acquisition must go through the read path.

[cleanup note: none of these flags, nor `--deny-bash`, was ever committed to `profile_run.py` (now `research/common/profile_run.py`); see the status line at the top.]

## Workload and method

Workload X3, unchanged from the earlier sweep: *read all 17 chapter `README.md` files (162,526
chars, ~41k tokens) and compare how context management, delegation and stopping evolve*. Provider
`glm-5.3-flash` via z.ai. Each run is a fresh non-interactive session in its own repo copy, with
fresh `.memory/.tasks/.transcripts/.task_outputs`.

Metrics come from the harness's own JSONL trace plus two sidecars per run (`.inputs.jsonl`, one
record per model call; `.reads.jsonl`, the full text of every tool result and every later rewrite).
`scripts/../ix_analyze.py` reconstructs rounds, expands a `read_files` call into the files it
**actually delivered** (parsed from the `===== path =====` separators, not from what was
requested), and derives residency exactly by crossing per-call `tool_result_ids` with the call at
which each result became a placeholder.

Two process notes that changed the results:

- **A review pass before the runs paid for itself.** Four reviewers plus adversarial verification
  confirmed 12 defects and refuted 10. The decisive one: a fresh `read_files` result is exempt from
  `micro_compact` (it is "unseen") but **not** from `fit_tool_results`, which replaces the largest
  block with a 1,000-char preview. An 85 KB batch became 1.1 KB *before the model ever read it*.
  The fix caps a batch at 25% of `CONTEXT_LIMIT` and reports the entries it skipped.
- **Runs made before that fix are reported separately** and marked `unfixed`. They are not wasted:
  they measure what an un-budgeted batch tool and a non-rebased outline actually do.

## Results

Medians with ranges; n is runs that completed with >= 10 lead rounds. "placeholder budget" is the
mean chars per evicted slot times the median number of evicted slots in a request, i.e. how much of
the 50,000-char budget the eviction markers themselves occupy.

| arm | n | lead rounds | model time | re-acq rounds | cache hit | uncached prompt tok | chapters read | summary compactions | placeholder budget |
|---|---:|---|---|---:|---:|---|---|---:|---:|
| baseline @50k | 3 | 69 (27-153) | 429 s (369-1227) | 80% | 15% | 633k | 17/17 | 0 | 4.0k |
| **stable prefix @50k** | 3 | **57 (51-66)** | 437 s (379-594) | **11%** | **60%** | **219k** | 13/17 | 0 | 3.0k |
| skeleton @50k `unfixed` | 3 | 129 (111-133) | 982 s (800-1134) | 44% | 17% | 1,258k | 17/17 | 4 | **17.1k** |
| batch @50k `unfixed` | 3 | 98 (83-130) | 995 s (677-1079) | 13% | 18% | 917k | 2/17 | 0 | 4.1k |
| all three @50k `unfixed` | 2 | 97 (46-148) | 1002 s (513-1491) | 30% | 54% | 431k | 6/17 | 2 | 6.5k |
| baseline @100k | 1 | 134 | 964 s | 80% | 9% | 2,576k | 17/17 | 0 | 3.3k |
| **batch @100k** `unfixed` | 1 | **56** | **600 s** | 73% | 10% | 966k | 17/17 | 0 | 5.8k |

Batch delivery, the variable that mediates intervention 1:

| run | budget | batch calls | files delivered / asked | lead rounds | chapters read |
|---|---:|---:|---|---:|---|
| I1-batch-r1 | 50k | 2 | 2 / 17 (11%) | 130 | 2/17 |
| I1-batch-r3 | 50k | 3 | 3 / 29 (10%) | 98 | 2/17 |
| I1-batch-r2 | 50k | 4 | 13 / 21 (61%) | 83 | 12/17 |
| ALL-r1 / ALL-r2 | 50k | 4 / 5 | 0 / 37, 0 / 46 (0%) | 46 / 148 | 7/17, 6/17 |
| **I1-100-batch-r1** | **100k** | 26 | **45 / 52 (86%)** | **56** | **17/17** |

### Shell-denied matrix (fixed driver, 2026-09-12)

Same workload with `--deny-bash`, so every acquisition goes through `read_file`/`read_files` and
`chapters read` is a valid completion proxy. These runs use the post-review driver (batch char cap,
rebased outline offsets), so they supersede the `unfixed` batch and outline rows above. Wall time is
1,800 s per turn; `timeout` means the lead never returned an answer. Answers were not blind-judged.

[cleanup note: the nine runs of this matrix are timestamped 2026-09-11, 08:19-10:01 UTC; the heading's 2026-09-12 matches the digest's "shell-denied matrix added 2026-09-12", i.e. when the section was added. The turn limit is not recorded in the traces and the capture driver was not committed: `profile_end` gives wall times of 2,463 s, 5,909 s and 1,660 s for the three timeouts (NB-base-r1, NB-I2-r1, NB-I2-r2), and NB-I2-r1's last three lead calls (09:53:55-10:01:33 UTC, 18 rate-limit retries) failed on the provider's 5-hour usage cap. NB200-I1-r1 lost only its post-turn `memory_extract` call to the cap.]

| arm | budget | rounds | model time | status | cache hit | uncached prompt tok | reads | re-acq rounds | summary compactions | chapters | notes |
|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---|---|
| NB-base-r1 | 50k | 319 | 2,247 s | timeout | 19% | 2,804k | 409 | 295 | 4 | 17/17 | paged READMEs in 45-line windows; 87% of re-reads windowed |
| NB-base-r2 | 50k | 14 | 343 s | completed | 14% | 121k | 17 | 7 | 0 | 17/17 | took notes; 24 spill-file reads |
| NB-I1-r1 (batch) | 50k | 20 | 188 s | completed | 18% | 178k | 26 | 9 | 0 | 17/17 | 17 batch calls, 26/45 delivered, 6 truncated by the cap |
| NB-I1-r2 (batch) | 50k | 20 | 348 s | completed | 15% | 190k | 30 | 11 | 1 | 17/17 | 9 calls, 10/31 delivered, 9 truncated |
| NB-I2-r1 (outline) | 50k | 242 | 3,973 s | timeout | 21% | 1,706k | **809** | 171 | **37** | 17/17 | 241 outlines; 146k output tokens; 1,090 chars per evicted slot, 16.4k of budget |
| NB-I2-r2 (outline) | 50k | 87 | 1,174 s | timeout | 20% | 656k | 252 | 66 | 11 | 17/17 | 59 outlines; 1,131 chars per slot, 22.6k of budget |
| NB-I4-r1 (stable) | 50k | 11 | 336 s | completed | **39%** | **66k** | 17 | 6 | 1 | 17/17 | |
| NB200-base-r1 | 200k | 5 | 182 s | completed | 4% | 117k | 17 | 0 | 0 | 17/17 | |
| NB200-I1-r1 (batch) | 200k | 7 | 150 s | completed | 5% | 162k | 17 | 0 | 0 | 17/17 | 4 calls, 17/24 delivered, zero re-acquisition |

What the shell ban changes: the baseline's escape hatch (grepping a README it already read) is gone,
so it either pages the corpus in small windows until it times out (r1) or takes notes (r2). Batching
becomes the most consistent 50k arm (20 and 20 rounds) even though the 12.5k-char cap truncates most
batches and delivers 32-58% of what was asked. The outline arm is the worst: 33-45% of the budget goes
to outlines, 11-37 summary compactions follow, and the lead loops outline -> offset re-read -> eviction
(720 of the 809 reads in r1 are re-reads of evicted READMEs) without finishing. At 200k nothing
misses, with or without batching.

## Answer quality (blind, 3 judges per answer, 45 judgements)

Every run's final answer was scored 0-5 on four axes by three independent judges who were given the
task, the repository and a fixed rubric, and were not told which configuration produced which
candidate (labels A-O). Judges were free to open the chapter READMEs to check claims.

| arm | n | total /20 (median, range) | chapters substantively covered | accuracy /5 |
|---|---:|---|---:|---:|
| skeleton @50k `unfixed` | 3 | 19 (17-19) | 17 | 4 |
| baseline @50k | 3 | 18 (14-19) | 15 | 4 |
| batch @50k `unfixed` | 2 | 18 (17-18) | 16 | 4 |
| all three @50k `unfixed` | 2 | 18 (17-18) | 16 | 4 |
| baseline @100k | 1 | 16 | 17 | 4 |
| batch @100k `unfixed` | 1 | 15 | 16 | 2 |
| **stable prefix @50k** | 3 | **14 (13-15)** | 15 | **2** |

[cleanup note: the answers, the rubric and the 45 judgements are not in the repo, and the traces keep no response text (output mode `summary`), so this table cannot be re-derived from `research/03_context_interventions/data/`.]

This reverses the headline. The stable-prefix arm is the only arm whose every repetition scored at
or below 15, while two of three baselines scored 18-19, and its median accuracy is half the
baseline's. One stable run covered only 6 chapters. Its token win is therefore **bought with answer
quality**, and the likely cause is the low-water-mark snip: keeping 12 messages discards the model's
own reasoning trail and its earlier per-chapter findings, which is exactly the material the final
synthesis needs.

The skeleton arm, the most expensive in rounds, scored highest and was the only arm with full
17-chapter coverage in every repetition. The map it keeps resident is apparently worth something to
the final answer even though it costs a third of the budget.

A caveat that applies to every arm: judges found fabricated quotations in nearly all answers
(passages presented in quotation marks that appear nowhere in the repository). That is a property of
this model on this workload, not of the interventions, but it means "accuracy 4/5" should be read as
"structurally right, specifics partly unreliable".

## What each intervention did

### 4. Stable prefix: a large token win that costs answer quality

Provider cache hits rose from 15% (10-18) to 60% (50-66), uncached prompt tokens fell 65%
(633k -> 219k), and history rewrite events fell 75% (60 -> 15). The mechanism is visible per call:
baselines oscillate between 0 and ~2,100 cached tokens, which is only the static tools-plus-system
block, while the stable arm climbs to 8,000-12,000, meaning the conversation body itself starts
hitting.

Round count did not improve at the median (57 vs 69, inside the noise) but its **variance
collapsed**: 51-66 rounds and 379-594 s, against 27-153 rounds and 369-1227 s for baseline. The
pathological baseline run does not recur. Re-acquisition rounds fell from 80% to 11%.

The cost is real and it shows up in the output, not just in coverage. Two of three stable runs read
12 and 13 of 17 chapters against 17/17 for every baseline, and the blind judges put the arm last:
median 14/20 against 18 for baseline, with median accuracy 2/5 against 4/5. Prefix stability was
obtained by throwing away more history, and the discarded history included the notes the final
synthesis was built from. A version that stabilises the prefix *without* the aggressive snip (drop
only the timestamp and batch the evictions) is the obvious next variant and has not been run.

### 1. Batching: helps exactly in proportion to what the resident set can accept

This is the sharpest result, and it is not the one the intervention was designed to show.

At the 100k budget, where an 85 KB batch fits under the limit and survives the pipeline, batching
delivered 86% of requested files and **halved the work**: 56 rounds against 134 for the 100k
baseline, 600 s against 964 s, with full 17/17 coverage.

At the 50k budget, the same tool delivered 10-61% of requested files, and round count tracked
delivery almost monotonically (11% delivered -> 130 rounds; 61% -> 83 rounds). Coverage collapsed to
2/17 in two runs. The model asks for 17 files, receives one, and falls back to shell exploration:
one run spent 109 of its 130 rounds on greps, including a `find /` over the filesystem.

So the round-count saving from batching is bounded by the resident set, not by the tool. **You
cannot fault in more than the upper tier can hold.** The character cap added after the review makes
this explicit rather than silent: at 50k it delivers 1-2 files per call and says what it skipped.

### 2. Demote to a map: the map cost more budget than the faults it saved

The outline mechanism fires (135-182 outlines per run) and is used: re-reads with an explicit offset
rose from 0% in two baselines to 13-27%, and to 83% in one combined run, so the model does convert
outline rows into targeted windows.

But it lost on budget. An outline occupies **856 chars per evicted slot against 248 for the bare
placeholder**, and with ~20 evicted slots in a request that is **17.1k of the 50,000-char budget
(34%) spent on maps**. The consequence is visible downstream: 4 summary compactions per run against
0-1 for baseline, and 129 rounds against 69. The map crowded out the live content it was meant to
help the model find.

These runs used the pre-review outline (offsets not rebased for windowed or batched reads, and
`persisted_preview` also patched), so part of the round penalty may be bug-driven. The budget
arithmetic, however, is independent of those bugs and is the dominant effect.

The fixed-driver, shell-denied re-run (matrix above) removes that doubt: with rebased offsets and
the preview untouched, both outline runs timed out (242 and 87 rounds, 37 and 11 summary
compactions), with outlines at 1,090-1,131 chars per evicted slot occupying 16-23k of the 50k
budget. The representation is not wrong in kind, since 25-28% of its re-reads were offset-targeted,
but at this size it manufactures more re-read rounds than it saves.

## Conclusions

1. **Prefix stability is a large token win that is not free.** 4x the cache hit rate, 65% fewer
   uncached prompt tokens, 75% fewer rewrite events and a collapsed latency tail, but the worst
   answer quality of any arm (median 14/20 against baseline 18, accuracy 2/5 against 4/5). It buys
   cheap tokens by discarding history the final answer needed. It does not reduce round count,
   because rounds are set by residency, not by caching. Separating its three sub-changes is the
   first thing to do next.
2. **Batching is bounded by residency, not by the tool.** With headroom it halves rounds and time;
   without headroom it is worse than useless, because it lures the model into requesting a batch
   that the pipeline then discards. Any "fetch more per fault" design needs an admission check
   against the live budget, and should tell the model what it did not deliver.
3. **A map is not free, but it may be worth paying for.** Replacing a 250-byte pointer with an
   860-byte outline consumed a third of the budget, triggered summary compaction and nearly doubled
   round count. Yet this arm produced the best answers (median 19/20, 17/17 chapters in every
   repetition) and shifted re-reads towards targeted windows (0% -> 13-27% offset-addressed). So
   outlines trade latency for quality, which is the opposite of the stable-prefix trade. Metadata in
   context still has to be budgeted like content: a one-line-per-file index is the cheaper variant
   to try.
4. **For the tiered-memory question the three results separate along different axes, and two of
   them trade against each other.** Cache stability is a property of the *transfer path*: it makes
   re-sending cheap but, as implemented here, only by evicting more. Batching is a property of the
   *upper tier's capacity*: it pays exactly in proportion to what the resident set can accept.
   Outlines are a property of the *representation*: they compete with the payload for budget and buy
   accuracy with latency. No single one dominates, which is itself the argument for sizing the
   resident set correctly first, since that is the only change measured so far (50k -> 200k, 5x
   fewer rounds) that improved latency without a quality cost.

## Where the s15 cascade sits among the compaction choices

The three interventions are points on a longer ladder, and this section places them on it. The
ladder was compiled on 2026-09-11 from the Claude Code documentation, the Claude API documentation
and published analyses of the leaked Claude Code v2.1.88 source. The choices differ on three axes:
what is removed (tool results only, or the whole history), who removes it (client or server) and
whether the cached prompt prefix survives.

| choice | trigger | what goes / what stays | cache effect | cost |
|---|---|---|---|---|
| persist a large result to disk | one result > 50k chars (the `Read` tool is exempt), or > 200k chars of results in one turn | body written to a file; a ~2 KB preview plus the path stays | none: the body never enters the prefix | zero |
| microcompact, cached variant | many tool results while the cache is warm | old `Bash`/`Read`/`Grep`/`Glob`/`WebFetch`/`WebSearch`/`Edit`/`Write` results deleted from the server-side cached copy; the most recent N kept; local messages untouched; MCP and `Agent` results never cleared | prefix preserved through `cache_edits` | zero |
| microcompact, time-based | idle gap longer than the cache lifetime | the same results replaced locally by `[Old tool result content cleared]`; last N kept; older thinking blocks cleared | a rebuild, but the cache was already cold | zero |
| API context editing (`clear_tool_uses_20250919`, `clear_thinking_20251015`) | default 100k input tokens | oldest tool results cleared server-side; keep 3 by default; `clear_at_least` batches the clearing; `exclude_tools` protects named tools | invalidates the prefix from the first cleared block | zero |
| session-memory compaction | token and tool-call thresholds, notes available | history replaced by notes a background pass kept up to date; a 10k-40k token tail with >= 5 text messages kept; falls back to full compaction | conversation layer rebuilt | zero at compaction time |
| full compaction (auto, or `/compact [instructions]`) | the model's limit: ~967k on native-1M models, the 200k boundary on Sonnet 4.6 / Opus 4.6; the leaked build computed window - min(max output, 20k) - 13k, ~167k on 200k | an LLM summary replaces the history; system prompt, project CLAUDE.md, auto memory and the plan come back from disk; up to 5 recently modified files re-read at <= 5k tokens each; invoked skills re-injected at 5k each, 25k total; path-scoped rules reload lazily; hook context is summarised away | conversation layer rebuilt for the short summary, system layer kept; the summariser is a fork that reads the warm prefix | one summarisation call; up to 3 retries dropping the oldest rounds on prompt-too-long; auto-compaction stops after 3 consecutive failures |
| server-side compaction API (`compact_20260112`, header `compact-2026-01-12`) | default 150k input tokens, minimum 50k | the API emits a `compaction` block and ignores everything before it on later requests; custom `instructions` replace the default prompt | system prompt stays cached behind its own breakpoint; the summary is written once | one extra sampling iteration, reported under `usage.iterations` |
| avoid compaction structurally | by design | `/clear` starts over; `/rewind` truncates to a still-cached prefix; subagents keep verbose output out of the caller's context; a fork inherits the caller's cache; teammates are shut down and respawned when task overlap is low | append-only, or reuse of an existing prefix | free to cheap |

Availability. The cached microcompact is marked Anthropic-internal in the source analyses, and the
`/usage` cache line in the shipped build counts tool-result clearing as an "expected rebuild", so the
public build should be assumed to rebuild the prefix whenever it clears. Session-memory compaction is
experimental. The server-side compaction API is a public beta on Opus 4.6 and later, Sonnet 4.6 and 5
and the Fable and Mythos models; on Fable 5.1, thinking blocks before the compaction block are not
carried forward. Teammates run the same client-side cascade as a main session, sit in the 5-minute
cache-TTL bucket unless `subagentPromptCacheTtl` is raised to `1h`, and Anthropic's coordinator
prompt tells the lead to message the same worker when file overlap is high and to spawn a fresh one
when it is low.

The s15 lead's `prepare_context` cascade maps onto the ladder as follows. Teammates get none of it.

- `tool_result_budget` is the persist-to-disk rung with the same 200k aggregate cap, applied to the
  current turn's results, largest first.
- `snip_compact` has no counterpart. Claude Code's 50-entry cap is a UI snapshot; the model's
  transcript keeps full history. In s15 the rung rewrites the middle of the history every time it
  fires, which is why PF-S-c512-r1 shrank its history 7x with zero summary compactions and the
  lead's cache-hit rate fell to 4-23%. Intervention 4's low-water-mark snip is the fix for this rung.
- `micro_compact` is the time-based microcompact (`KEEP_RECENT_TOOL_RESULTS = 3`, placeholder
  `[Earlier tool result saved at ...]`), except that it fires on size rather than on a cold cache,
  so every firing is a paid rebuild instead of a free one. Intervention 2 replaces its placeholder
  with an outline; intervention 4 batches its firings.
- `fit_tool_results` (largest first, 1,000-char preview) has no counterpart; the nearest is the
  preview left behind by the persist rung.
- `compact_history` is a full compaction whose summariser gets a different system prompt and the
  first 80k chars of the JSON history, so it cannot read the warm prefix the way Claude Code's fork
  does, and it restores nothing afterwards: no files, skills, plan or memory come back.
- `reactive_compact` is the prompt-too-long path: it summarises everything but the last 5 messages
  in a single attempt, where Claude Code drops the oldest rounds and retries the same summarisation
  up to 3 times.

Decision rule that follows from the ladder and from the three results above:

1. Keep content out of the prefix in the first place: persist large results at creation, delegate
   verbose work to a subagent, respawn a teammate for an unrelated task.
2. When the history must be edited in place, edit when the cache is already cold or batch the
   deletions (a low-water mark, `clear_at_least`) so that each rebuild is amortised. This is what
   intervention 4 measured: 4x the cache hit rate from turning many small rewrites into few large
   ones.
3. Summarise last. When summarising, share the warm prefix by appending the instruction to the same
   system prompt and history, and restore the working set afterwards (recent files, plan, memory),
   which is the half of full compaction that `compact_history` lacks.
4. Placeholders are budgeted like content: intervention 2's ~860-byte outline against a ~250-byte
   pointer took a third of the budget and forced summary compactions, so any richer marker needs
   its own budget line.
5. For teammates, the cheapest addition is a time-based microcompact at task boundaries when the
   idle gap exceeded the provider's cache lifetime, plus a respawn rule for low-overlap tasks.

Sources (all read 2026-09-11):
- Claude Code docs: [context window, what survives compaction](https://code.claude.com/docs/en/context-window),
  [auto-compact window](https://code.claude.com/docs/en/model-config),
  [prompt caching](https://code.claude.com/docs/en/prompt-caching), [costs](https://code.claude.com/docs/en/costs),
  [agent teams](https://code.claude.com/docs/en/agent-teams), [subagents](https://code.claude.com/docs/en/sub-agents)
- Claude API docs: [compaction](https://platform.claude.com/docs/en/build-with-claude/compaction),
  [context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing)
- Analyses of the leaked Claude Code v2.1.88 source (the source itself was not read):
  [Context Compaction in Claude Code: a five-layer cascade](https://finisky.github.io/en/claude-code-context-compaction/),
  [Claude Reviews Claude, ch. 11: the compaction system](https://openedclaude.github.io/claude-reviews-claude/chapters/11-compact-system),
  [Claude Code from Source, ch. 10: tasks, coordination and swarms](https://claude-code-from-source.com/ch10-coordination/)

## Threats to validity

- **Strategy variance dominates round count.** Baseline ranged 27-153 rounds at a fixed budget
  because the model chooses between reading files and shelling out. Mechanism-level metrics (cache
  hits, delivery rate, placeholder budget, rewrite events) are stable; round count is not. The
  `--deny-bash` condition removes the shell escape but not the variance: the shell-denied baseline
  ranged 14-319 rounds at 50k.
- **The provider's 5-hour usage cap** killed the first shell-denied matrix: 13 of its 16 runs returned zero
  successful model calls after 18 rate-limit retries each [corrected 2026-09-23: earlier context_intervention_profile.md said "16 runs returned zero successful model calls after 18 rate-limit retries each". Counting `model_response` events per trace in `research/03_context_interventions/data/context_interventions/quota_killed/` gives 0 for 13 runs and 5, 6 and 8 for NB-I2-r1, NB-base-r1 and NB-I1-r1; `profile_end` records 6 rate-limit retries, not 18, for NB-base-r1 and NB-I1-r1, which ended with status `error`. The 16 are 11 shell-denied runs (NB-*) and 5 runs with the shell-allowed prompt (FX-*).] (quarantined under
  `research/03_context_interventions/data/context_interventions/quota_killed/`). The matrix was re-run after the reset on
  2026-09-11 [corrected 2026-09-23: earlier context_intervention_profile.md said 2026-09-12; the nine re-run traces at the top of `research/03_context_interventions/data/context_interventions/` start between 08:19 and 09:51 UTC on 2026-09-11 and were committed in `64d1b11` at 16:58 -0400 that day] with n=1-2 per arm; its answers have not been blind-judged.
- **`chapters read` undercounts in the free-strategy condition**, because a chapter read with
  `cat` through the shell is not attributed to a chapter. It is a valid completion proxy only in
  the shell-denied condition.
- Intervention 1 has n=1 at each budget where it works (100k with shell, 200k without), and
  intervention 2's only post-review runs are the two shell-denied timeouts.
- The trace-derived timing model is client-observed latency, and a direct streaming probe of this
  endpoint puts a cheap round at ~80% fixed latency, ~10% prefill, ~5% decode.

## Reproduce

```sh
# one run per arm, 50k budget (the thrashing regime)
python3 research/common/profile_run.py --label B  --context-limit 50000 --prompt "$(cat x3_prompt.txt)"
python3 research/common/profile_run.py --label I1 --context-limit 50000 --batch-read      --prompt ...
python3 research/common/profile_run.py --label I2 --context-limit 50000 --skeleton-demote --prompt ...
python3 research/common/profile_run.py --label I4 --context-limit 50000 --stable-prefix   --prompt ...
# isolation control: the shell cannot substitute for the read path
python3 research/common/profile_run.py --label NB --context-limit 50000 --deny-bash       --prompt ...
# the budget where batching has headroom
python3 research/common/profile_run.py --label I1-200 --context-limit 200000 --batch-read --prompt ...

python3 ix_analyze.py research/03_context_interventions/data/context_interventions
```

[cleanup note: these commands cannot be run from the repo. The four flags were never committed to `profile_run.py` (now `research/common/profile_run.py`), `x3_prompt.txt` is not in the repo (each trace's `profile_meta` records the exact prompt), and the analyzer is now `research/03_context_interventions/ix_analyze.py`, which reads one directory without recursing, so `research/03_context_interventions/data/context_interventions/unfixed_driver/` and `research/03_context_interventions/data/context_interventions/quota_killed/` have to be passed on their own.]

## A.W Additions from `weekly_progress/091626/agent_e2e_bottleneck.md` (2026-09-10 to 2026-09-12; committed 2026-09-13, extended 2026-09-15)

[cleanup note: this digest walks one arc, Q1-Q6. Blocks that restate another source were dropped; the source map lists each with its new home. Q1 (the X3 budget sweep, its per-round autopsy and the miss-rate argument) is `research/02_input_redundancy/README.md` Part A §3.8. Three Q2 bullets are in Part A §3.8 and in the `teammate_overlap.md` §4 material merged into Part A. Q4's two result tables and its batching and sizing answers are this README's "Results", "Answer quality", "Shell-denied matrix (fixed driver, 2026-09-12)", "What each intervention did" and "Conclusions". Everything below is verbatim apart from the bracketed notes.]

_(was the introduction)_

One session, one arc: from "are eviction-driven re-reads the s15 lead's main bottleneck?" to
"what would a tiered memory change, does any of it work, and does anyone already ship the engine-side
version?" Companion to `weekly_progress/091626/tiered_memory_conclusion.md` (which folds these findings into the wider
argument). Terminology as in the 2026-09-09 deck: a *turn* is one lead activation, a *round* is one
agent-loop iteration (one model call plus its tool calls). Model: `glm-5.3-flash` via the z.ai
Anthropic-compatible endpoint, which prefix-caches automatically. Workload X3 throughout: read all 17
chapter READMEs (162,526 chars, ~41k tokens) and compare them.

_(was Q2; its first three bullets were dropped, see the source map)_

### Q2. What does one round cost, and where does the time go?

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

- Confirmed on 2026-09-13 across three other task classes (`research/05_latency_breakdown/README.md` Part A, 24 runs): a
  regression over 446 agent calls gives 3.7 s fixed + 0.033 ms per uncached token + 17.4 ms per
  output token (r2 0.96), and a dedicated size sweep gives 0.0384 ms per token (26,000 tok/s), so
  the 0.052 ms/token above holds to within a factor of 1.4. The prefill *share* scales with the
  uncached tokens a call carries: about 10% at the 9-19k of these runs, 0.4-1.6% at the 0.9-3.8k of
  the file-Q&A, coding and math workloads, and seconds only above roughly 50k per call.

**Answer.** A round is a fixed-cost event. Skipping the prefill of repeated content saves at most
~10% of a round; removing the round saves all of it. Decoding dominates only the answer rounds.

_(was Q3)_

### Q3. What would a tiered memory change?

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
| 5 | teammate task-boundary compaction, respawn on low overlap | teammate tier | bound the N resident copies (see `research/02_input_redundancy/README.md` Part B) |
| 6 | shared, position-stable file block per team | provider cache | prefill a shared file once |
| 7 | in-engine execution / KV splice for whitelisted fetch tools | engine | remove the round trip per miss |
| 8 | publish the tier costs in the tool descriptions or the system prompt | model-side routing | let the model avoid the expensive tier by itself |

**Item 8 was tested on 2026-09-14 and does not work in a mixed pool**
(`research/06_tool_cost/README.md` Part B). Annotating `read_file` at anything from 0.05 to
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

_(was Q4; its two result tables and its batching and sizing answers were dropped, see the source map)_

### Q4. Do the batch read, outline demotion and stable prefix work?

**Experiment.** Three flags on `research/common/profile_run.py` that monkeypatch the loaded harness module
(the committed harness is identical across arms): `--batch-read` (12 files, char cap = 25% of the
budget, entries past the cap are skipped with a note), `--skeleton-demote` (outline with rebased
0-based offsets per file), `--stable-prefix`, plus `--deny-bash` so every acquisition goes through
the read path. 34-check offline test suite. Two matrices: shell allowed (phase 1/2; the batch and
outline arms there ran on an unfixed driver and are quarantined), and shell denied with the fixed
driver (phase 3, run after the provider's 5-hour usage cap reset). Answer quality: blind judging by
three judges for the shell-allowed arms; the shell-denied answers were not judged (this session only),
so chapter coverage and completion are the proxy.

[cleanup note: none of these flags was committed to `profile_run.py`, and the 34-check suite (`s15_integrated_harness/tests/test_context_interventions.py`, which hard-coded `/home/yq335` and called the never-committed `apply_interventions`) was deleted in the 2026-09-23 cleanup. The quarantined phase 1/2 runs are `research/03_context_interventions/data/context_interventions/unfixed_driver/`; the first phase 3 attempt is `research/03_context_interventions/data/context_interventions/quota_killed/`.]

All shell-denied arms except the two outline runs and baseline r1 covered 17/17 chapters.

[cleanup note: the shell-denied matrix above (section "Shell-denied matrix (fixed driver, 2026-09-12)") and `ix_analyze.py` report 17/17 chapters read for all nine runs; the three runs this sentence excludes are the three timeouts, which never returned an answer.]

**Answers.**

- **Stable prefix is a transfer-path fix.** Cache hit 15% -> 60% (39% with shell denied), uncached
  prompt tokens down 3-5x, round count unchanged, because rounds are set by residency, not by what
  each re-send costs. It is the only arm that lost answer quality (14/20 vs 18/20; coverage 12/17 and 13/17 in
  two of three runs [corrected 2026-09-23: earlier agent_e2e_bottleneck.md said "coverage 13/17 in two of three runs"; section "4. Stable prefix: a large token win that costs answer quality" above and `ix_analyze.py` give 12/17 (I4-stable-r1) and 13/17 (I4-stable-r3)]), and which sub-change causes that is still to be attributed. [cleanup note: "3-5x" is not reproduced by the committed tables: 633k -> 219k (shell allowed) is 2.9x, and 121k -> 66k (shell denied, the completed baseline against the stable arm) is 1.8x.]
- **Outline demotion is a representation cost, and with the shell denied it is the worst arm.**
  Each evicted slot costs 856-1,131 chars against 201-538 for the bare pointer, 33-45% of the
  budget; that forces 11-37 summary compactions, and the lead loops outline -> offset re-read ->
  eviction (809 reads, 720 of them re-reads of evicted READMEs) until it times out. The map crowds
  out the payload and manufactures the rounds it was meant to save. The one place it paid off was
  answer quality with the shell available (19/20), from targeted re-reads.

_(was Q5)_

### Q5. Item 7, "in-engine KV splice for fetch tools": what is it exactly?

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

_(was Q6)_

### Q6. Is the whitelisted server-side file read already shipped by Claude Code or Codex?

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

_(was Conclusion)_

### Conclusion

The agent's end-to-end bottleneck is the number of fixed-cost rounds the lead spends re-acquiring
content its own budget evicted: a resident set larger than the working set removes those rounds
outright (5-20x), a stable prefix makes the remaining ones 3-5x cheaper to re-send without removing
any, batching helps only where the batch fits, outline metadata that competes with payload makes it
worse, and moving the fetch into the engine would remove the ~4.6 s queue floor per round, which no
product does today.

[cleanup note: on "3-5x", see the note under Q4 above.]

_(was Open items from this arc)_

### Open items from this arc

1. Blind-judge the shell-denied answers (`answers_final/NB-*.txt`) with the existing three-judge
   script so the phase-3 table has a quality column.
   [cleanup note: neither `answers_final/` nor the `<label>.console.log` files that `ix_analyze.py --answers` reads are in the repo, and the traces keep no response text (output mode `summary`), so the shell-denied answers cannot be recovered from the committed data.]
2. Attribute the stable-prefix quality loss to its sub-changes (snip, archive marker, timestamp).
3. Re-run the outline arm with a budgeted outline (cap total outline chars at ~10% of the budget)
   to test whether the representation is wrong or only its size.
4. Batch arm at 50k with the cap raised to 50% of the budget, to separate the tool from the cap.

_(was Artifacts)_

### Artifacts

- `research/02_input_redundancy/README.md` Part A section 3.8: X3 budget sweep, autopsy, cost
  decomposition and the prefill correction.
- this README: intervention design, results, blind
  judging, compaction ladder; shell-denied matrix added 2026-09-12.
- `research/common/profile_run.py` (flags), `research/03_context_interventions/ix_analyze.py` (analyzer),
  `tests/test_context_interventions.py`.
  [cleanup note: the intervention flags were never committed to `profile_run.py`, and `tests/test_context_interventions.py` was deleted in the 2026-09-23 cleanup.]
- Traces: `research/02_input_redundancy/data/reuse_profiling/` (X3 sweep),
  `research/03_context_interventions/data/context_interventions/` (valid runs at top level; `unfixed_driver/`, `quota_killed/`).
- `research/02_input_redundancy/README.md` Part A (was `teammate_overlap.md` section 4): the original latency-headroom estimate and
  its correction.
- `research/06_tool_cost/README.md` Part B:
  ladder item 8 tested over three stages (893 single-shot trials, 80 multi-round runs, 24 s15
  sessions); `scripts/tool_cost_{probe,loop,harness,analyze}.py`, `profile_run.py --tool-cost`,
  traces in `research/06_tool_cost/data/tool_cost/`.

---

## Data inventory

Everything is under `research/03_context_interventions/data/` (moved from `s15_integrated_harness/traces/context_interventions/`; all 110 files committed in `64d1b11`, 2026-09-11). A run is a harness trace `<stem>.jsonl` (`run_start`, `profile_meta` with label, prompt and flags, model, tool and context events, `profile_end` with status, wall time and rate-limit retries), a per-call sidecar `<stem>.inputs.jsonl` (message and tool-result composition, placeholders, `tool_result_ids`, usage) and, when a tool ran, a content sidecar `<stem>.reads.jsonl` (full text of every tool result and every later rewrite). All were written by the uncommitted capture version of `profile_run.py` (`profile_meta.driver` = `scripts/profile_run.py`) against `glm-5.3-flash` at `https://api.z.ai/api/anthropic`, workload X3. The shell denial is not a `profile_meta` field: an NB run shows it by its label, by a prompt extended with "the shell is not available in this session", and by zero `bash` calls. "lead rounds" below are successful lead `model_response` events, which equal `ix_analyze.py`'s round counts. `ix_analyze.py` reads one directory without recursing, so the two subfolders stay out of the top-level tables on purpose.

| directory | what it holds | written by | used in | status |
|---|---|---|---|---|
| `research/03_context_interventions/data/` | only `context_interventions/` | | | |
| `research/03_context_interventions/data/context_interventions/` | 16 valid runs, 48 files (26.5 MB), 2026-09-11 03:24-10:01 UTC: 3 baseline, 3 stable-prefix and 1 100k baseline with the shell (phase 1/2), 9 shell-denied runs at 50k and 200k (phase 3) | capture `profile_run.py` | "Results", "Answer quality", "Shell-denied matrix", sections 4, 1, 2, "Threats to validity"; digest Q4 | published |
| `research/03_context_interventions/data/context_interventions/unfixed_driver/` | 9 runs, 27 files (16.8 MB), 2026-09-11 03:54-05:09 UTC: batch, outline and all-three arms at 50k and batch at 100k, shell allowed, on the pre-review driver | capture `profile_run.py` (before the review fixes) | every `unfixed` row: "Results", the batch-delivery table, "Answer quality", sections 1 and 2; digest Q4 | published as `unfixed`; superseded for mechanism claims by the shell-denied matrix |
| `research/03_context_interventions/data/context_interventions/quota_killed/` | 16 runs, 35 files (1.4 MB; the 13 runs without a response have no `.reads.jsonl`), 2026-09-11 05:13-05:57 UTC: 11 shell-denied labels and 5 shell-allowed FX labels | capture `profile_run.py` | "Threats to validity" (5-hour cap bullet) | aborted (provider quota) |

**`research/03_context_interventions/data/context_interventions/` (top level), one row per run.**

| label | trace stem | start-end (UTC) | budget | flags | shell | lead rounds | wall (s) | status (`profile_end`; inventory) | used in |
|---|---|---|---:|---|---|---:|---:|---|---|
| B-base-r1 | `run_20260911T032452_801883Z_6ff7e07c` | 03:24-03:31 | 50k | none | allowed | 27 | 399 | completed; published | Results (baseline @50k); Answer quality; section 4 |
| B-base-r2 | `run_20260911T032452_812157Z_341a45e9` | 03:24-03:46 | 50k | none | allowed | 153 | 1,302 | completed; published | Results (baseline @50k); Answer quality; section 4 |
| B-base-r3 | `run_20260911T035403_055647Z_ecb580a9` | 03:54-04:01 | 50k | none | allowed | 69 | 456 | completed; published | Results (baseline @50k); Answer quality; section 4 |
| I4-stable-r1 | `run_20260911T040140_145067Z_49467c2f` | 04:01-04:12 | 50k | `--stable-prefix` (snip keeps 12) | allowed | 66 | 620 | completed; published | Results (stable prefix @50k); Answer quality; section 4 |
| I4-stable-r2 | `run_20260911T041351_271878Z_9c5f6141` | 04:13-04:20 | 50k | `--stable-prefix` (snip keeps 12) | allowed | 51 | 404 | completed; published | Results (stable prefix @50k); Answer quality; section 4 |
| I4-stable-r3 | `run_20260911T043558_154760Z_56efd1d2` | 04:35-04:43 | 50k | `--stable-prefix` (snip keeps 12) | allowed | 57 | 471 | completed; published | Results (stable prefix @50k); Answer quality; section 4 |
| B100-base-r1 | `run_20260911T044350_547324Z_4a849600` | 04:43-05:00 | 100k | none | allowed | 134 | 993 | completed; published | Results (baseline @100k); Answer quality; section 1 |
| NB-I1-r1 | `run_20260911T081923_883382Z_bf1a6be1` | 08:19-08:23 | 50k | `--batch-read` (max 12) | denied | 20 | 222 | completed; published | Shell-denied matrix; digest Q4 |
| NB-base-r1 | `run_20260911T081923_988183Z_1b9a57c4` | 08:19-09:00 | 50k | none | denied | 319 | 2,463 | timeout; published | Shell-denied matrix; digest Q4 |
| NB-I2-r1 | `run_20260911T082306_809461Z_1e4882a7` | 08:23-10:01 | 50k | `--skeleton-demote` | denied | 242 | 5,909 | timeout; its last 3 lead calls failed on the 5-hour cap (09:53:55-10:01:33 UTC); published | Shell-denied matrix; section 2 (fixed-driver re-run); digest Q4 |
| NB-base-r2 | `run_20260911T090027_601183Z_c0602446` | 09:00-09:06 | 50k | none | denied | 14 | 369 | completed; published | Shell-denied matrix; digest Q4 |
| NB-I1-r2 | `run_20260911T090637_789312Z_d7991ed0` | 09:06-09:13 | 50k | `--batch-read` (max 12) | denied | 20 | 414 | completed; published | Shell-denied matrix; digest Q4 |
| NB-I2-r2 | `run_20260911T091333_028516Z_cb70e6c0` | 09:13-09:41 | 50k | `--skeleton-demote` | denied | 87 | 1,660 | timeout; published | Shell-denied matrix; section 2 (fixed-driver re-run); digest Q4 |
| NB-I4-r1 | `run_20260911T094113_330419Z_48cc6f78` | 09:41-09:47 | 50k | `--stable-prefix` (snip keeps 12) | denied | 11 | 387 | completed; published | Shell-denied matrix; digest Q4 |
| NB200-base-r1 | `run_20260911T094741_501627Z_d9481314` | 09:47-09:51 | 200k | none | denied | 5 | 211 | completed; published | Shell-denied matrix; digest Q4 |
| NB200-I1-r1 | `run_20260911T095113_273352Z_3f13b8b5` | 09:51-09:56 | 200k | `--batch-read` (max 12) | denied | 7 | 304 | completed; its post-turn `memory_extract` call failed on the cap; published | Shell-denied matrix; digest Q4 |

**`research/03_context_interventions/data/context_interventions/unfixed_driver/`** (previously uncited by name). *Why it was run:* phase 1/2 of the matrix, the three treated arms with the shell allowed (batch, outline, all three at 50k; batch at 100k), captured before the review pass described in "Workload and method". "Unfixed driver" means that pre-review capture driver: a fresh `read_files` batch was not budgeted, so `fit_tool_results` cut an 85 KB batch to a 1,000-char preview before the model read it (the fix caps a batch at 25% of `CONTEXT_LIMIT` and names the skipped entries), and the outline's offsets were not rebased for windowed or batched reads (with `persisted_preview` also patched). *What became of it:* the runs were quarantined in this subfolder and reported as the `unfixed` rows (digest Q4 quotes two of them: outline @50k and batch @100k). Their fixed-driver, shell-allowed re-runs (the FX labels) were killed by the quota and never repeated, so these nine remain the only shell-allowed batch and outline data.

| label | trace stem | start-end (UTC) | budget | flags | lead rounds | wall (s) | status (`profile_end`; inventory) | used in |
|---|---|---|---:|---|---:|---:|---|---|
| I1-batch-r1 | `run_20260911T035403_056311Z_c9b339ec` | 03:54-04:12 | 50k | `--batch-read` (max 12) | 130 | 1,115 | completed; published as `unfixed` | Results (batch @50k `unfixed`); batch-delivery table; Answer quality; section 1 |
| I2-skel-r1 | `run_20260911T035403_056346Z_191b95f6` | 03:54-04:13 | 50k | `--skeleton-demote` | 111 | 1,187 | completed; published as `unfixed` | Results (skeleton @50k `unfixed`); Answer quality; section 2; digest Q4 |
| I1-batch-r2 | `run_20260911T041201_358536Z_24cd54a4` | 04:12-04:23 | 50k | `--batch-read` (max 12) | 83 | 708 | completed; published as `unfixed` | Results (batch @50k `unfixed`); batch-delivery table; Answer quality; section 1 |
| I2-skel-r2 | `run_20260911T041239_336824Z_0c244cfa` | 04:12-04:35 | 50k | `--skeleton-demote` | 133 | 1,398 | completed; published as `unfixed` | Results (skeleton @50k `unfixed`); Answer quality; section 2; digest Q4 |
| ALL-r1 | `run_20260911T042036_135545Z_363fc1fc` | 04:20-04:30 | 50k | `--batch-read` (max 12), `--skeleton-demote`, `--stable-prefix` (snip keeps 12) | 46 | 572 | completed; published as `unfixed` | Results (all three @50k `unfixed`); batch-delivery table (ALL-r1 / ALL-r2); Answer quality |
| I1-batch-r3 | `run_20260911T042350_174677Z_60b00b71` | 04:23-04:41 | 50k | `--batch-read` (max 12) | 98 | 1,061 | completed; published as `unfixed` | Results (batch @50k `unfixed`); batch-delivery table; Answer quality; section 1 |
| I2-skel-r3 | `run_20260911T043009_628177Z_22766c9f` | 04:30-04:45 | 50k | `--skeleton-demote` | 129 | 900 | completed; published as `unfixed` | Results (skeleton @50k `unfixed`); Answer quality; section 2; digest Q4 |
| ALL-r2 | `run_20260911T044131_735071Z_b8a27aa0` | 04:41-05:09 | 50k | `--batch-read` (max 12), `--skeleton-demote`, `--stable-prefix` (snip keeps 12) | 148 | 1,686 | timeout; published as `unfixed` | Results (all three @50k `unfixed`); batch-delivery table (ALL-r1 / ALL-r2); Answer quality |
| I1-100-batch-r1 | `run_20260911T044510_939102Z_57faffd0` | 04:45-04:55 | 100k | `--batch-read` (max 12) | 56 | 627 | completed; published as `unfixed` | Results (batch @100k `unfixed`); batch-delivery table; Answer quality; section 1; digest Q4 |

**`research/03_context_interventions/data/context_interventions/quota_killed/`** (killed by the provider's 5-hour usage cap: every failed call is a 429 with z.ai error 1308, "Usage limit reached for 5 hour"). *Why it was run:* the first attempt at phase 3 (the shell-denied matrix) together with five FX runs, which carry the standard shell-allowed prompt and the batch, outline and all-three flags; the FX label is not explained in any writeup. *What became of it:* 13 runs got no model response at all and 3 got 5, 6 and 8 lead responses before the cap. The 9 labels NB-I1-r1, NB-I1-r2, NB-I2-r1, NB-I2-r2, NB-I4-r1, NB-base-r1, NB-base-r2, NB200-I1-r1, NB200-base-r1 were re-run at the top level from 08:19 UTC; FX-ALL-r1, FX-I1-r1, FX-I1-r2, FX-I2-r1, FX-I2-r2, NB-ALL-r1, NB-I4-r2 were never re-run. `ix_analyze.py` on this folder reproduces 0 rounds for the 13 and 5, 6 and 8 rounds for the 3.

| label | trace stem | start-end (UTC) | budget | flags | shell | `model_response` events | rate-limit retries (`profile_end`) | status (`profile_end`; inventory) | sidecars | re-run at the top level |
|---|---|---|---:|---|---|---:|---:|---|---|---|
| NB-I2-r1 | `run_20260911T051334_169302Z_03031b1d` | 05:13-05:24 | 50k | `--skeleton-demote` | denied | 5 | 18 | completed; aborted (quota) | inputs + reads | yes |
| NB-base-r1 | `run_20260911T051334_169792Z_2f9564c2` | 05:13-05:18 | 50k | none | denied | 6 | 6 | error; aborted (quota) | inputs + reads | yes |
| NB-I1-r1 | `run_20260911T051334_262894Z_a044cb82` | 05:13-05:19 | 50k | `--batch-read` (max 12) | denied | 8 | 6 | error; aborted (quota) | inputs + reads | yes |
| NB-I4-r1 | `run_20260911T051834_977139Z_9840dc96` | 05:18-05:26 | 50k | `--stable-prefix` (snip keeps 12) | denied | 0 | 18 | completed; aborted (quota) | inputs | yes |
| NB-base-r2 | `run_20260911T051931_020102Z_62695c9d` | 05:19-05:27 | 50k | none | denied | 0 | 18 | completed; aborted (quota) | inputs | yes |
| NB-I1-r2 | `run_20260911T052418_846774Z_ff89b0b5` | 05:24-05:31 | 50k | `--batch-read` (max 12) | denied | 0 | 18 | completed; aborted (quota) | inputs | yes |
| NB-I2-r2 | `run_20260911T052617_429172Z_6ad3cb10` | 05:26-05:33 | 50k | `--skeleton-demote` | denied | 0 | 18 | completed; aborted (quota) | inputs | yes |
| NB-I4-r2 | `run_20260911T052710_848835Z_9810116c` | 05:27-05:34 | 50k | `--stable-prefix` (snip keeps 12) | denied | 0 | 18 | completed; aborted (quota) | inputs | no |
| NB-ALL-r1 | `run_20260911T053159_405169Z_c9fa8ac7` | 05:31-05:39 | 50k | `--batch-read` (max 12), `--skeleton-demote`, `--stable-prefix` (snip keeps 12) | denied | 0 | 18 | completed; aborted (quota) | inputs | no |
| NB200-base-r1 | `run_20260911T053400_340172Z_8504cbfe` | 05:34-05:41 | 200k | none | denied | 0 | 18 | completed; aborted (quota) | inputs | yes |
| NB200-I1-r1 | `run_20260911T053455_162979Z_9d71f649` | 05:34-05:42 | 200k | `--batch-read` (max 12) | denied | 0 | 18 | completed; aborted (quota) | inputs | yes |
| FX-I1-r1 | `run_20260911T053939_898252Z_077e0d52` | 05:39-05:47 | 50k | `--batch-read` (max 12) | allowed | 0 | 18 | completed; aborted (quota) | inputs | no |
| FX-I2-r1 | `run_20260911T054143_760985Z_a97265f0` | 05:41-05:49 | 50k | `--skeleton-demote` | allowed | 0 | 18 | completed; aborted (quota) | inputs | no |
| FX-ALL-r1 | `run_20260911T054237_753977Z_050ac0ce` | 05:42-05:50 | 50k | `--batch-read` (max 12), `--skeleton-demote`, `--stable-prefix` (snip keeps 12) | allowed | 0 | 18 | completed; aborted (quota) | inputs | no |
| FX-I1-r2 | `run_20260911T054720_693486Z_1e43c923` | 05:47-05:55 | 50k | `--batch-read` (max 12) | allowed | 0 | 18 | completed; aborted (quota) | inputs | no |
| FX-I2-r2 | `run_20260911T054925_690475Z_f70e7101` | 05:49-05:57 | 50k | `--skeleton-demote` | allowed | 0 | 18 | completed; aborted (quota) | inputs | no |

**Result files and local files.** There are no result tables or JSON summaries under `research/03_context_interventions/data/`; the report's tables are `ix_analyze.py` output (re-checked in the 2026-09-23 cleanup: the per-run numbers behind every baseline, stable-prefix, shell-denied and `unfixed` row reproduce). No git-ignored or untracked local files exist under `research/03_context_interventions/data/` (`git status --ignored`). Not in the repo at all, neither committed nor local: the per-run `<label>.console.log` files that `ix_analyze.py --answers` reads the final answers from, the digest's `answers_final/` directory, the blind-judging rubric and scores, and `x3_prompt.txt`. The flag-free baselines (B-base, B100-base) use only options the committed `research/common/profile_run.py` still has (`--label`, `--context-limit`, both sidecars); every flagged or shell-denied arm needs the uncommitted capture driver.

## Source map

One row per `##` section of both sources, and per block where a section was split. "§ A.W › Qn" is the additions section of this README.

| old file | old section | new location | status |
|---|---|---|---|
| `s15_integrated_harness/context_intervention_profile.md` | # Three context-hierarchy interventions on the s15 lead loop (intro, intervention table) | research/03_context_interventions/README.md # Three context-hierarchy interventions on the s15 lead loop | kept (cleanup note added) |
| `s15_integrated_harness/context_intervention_profile.md` | ## Workload and method | research/03_context_interventions/README.md § Workload and method | kept |
| `s15_integrated_harness/context_intervention_profile.md` | ## Results | research/03_context_interventions/README.md § Results | kept |
| `s15_integrated_harness/context_intervention_profile.md` | ### Shell-denied matrix (fixed driver, 2026-09-12) | research/03_context_interventions/README.md § Results › Shell-denied matrix (fixed driver, 2026-09-12) | kept (cleanup note added) |
| `s15_integrated_harness/context_intervention_profile.md` | ## Answer quality (blind, 3 judges per answer, 45 judgements) | research/03_context_interventions/README.md § Answer quality (blind, 3 judges per answer, 45 judgements) | kept (cleanup note added) |
| `s15_integrated_harness/context_intervention_profile.md` | ## What each intervention did | research/03_context_interventions/README.md § What each intervention did | kept |
| `s15_integrated_harness/context_intervention_profile.md` | ### 4. Stable prefix: a large token win that costs answer quality | research/03_context_interventions/README.md § What each intervention did › 4. Stable prefix | kept |
| `s15_integrated_harness/context_intervention_profile.md` | ### 1. Batching: helps exactly in proportion to what the resident set can accept | research/03_context_interventions/README.md § What each intervention did › 1. Batching | kept |
| `s15_integrated_harness/context_intervention_profile.md` | ### 2. Demote to a map: the map cost more budget than the faults it saved | research/03_context_interventions/README.md § What each intervention did › 2. Demote to a map | kept |
| `s15_integrated_harness/context_intervention_profile.md` | ## Conclusions | research/03_context_interventions/README.md § Conclusions | kept |
| `s15_integrated_harness/context_intervention_profile.md` | ## Where the s15 cascade sits among the compaction choices | research/03_context_interventions/README.md § Where the s15 cascade sits among the compaction choices | kept; the only copy of the compaction-ladder table (the identical table in research/related_work/teammate_memory_management.md §4 is now a pointer here) |
| `s15_integrated_harness/context_intervention_profile.md` | ## Threats to validity | research/03_context_interventions/README.md § Threats to validity | kept (two corrections) |
| `s15_integrated_harness/context_intervention_profile.md` | ## Reproduce | research/03_context_interventions/README.md § Reproduce | kept (cleanup note added) |
| `weekly_progress/091626/agent_e2e_bottleneck.md` | # The agent's end-to-end bottleneck (2026-09-10 to 2026-09-12): H1 and introduction | research/03_context_interventions/README.md § A.W (introduction) | kept (H1 replaced by the A.W header) |
| `weekly_progress/091626/agent_e2e_bottleneck.md` | ## Q1. Do evicted files force new tool rounds, and is that the main bottleneck? | research/02_input_redundancy/README.md Part A §3.8 | dropped (duplicate of Part A §3.8: the X3 budget sweep table, per-round autopsy, miss-rate argument, token multiplication, answer) |
| `weekly_progress/091626/agent_e2e_bottleneck.md` | ## Q2. What does one round cost, and where does the time go? — questions, experiment, results table | research/03_context_interventions/README.md § A.W › Q2 | kept (the 371-token TTFT floor is only here) |
| `weekly_progress/091626/agent_e2e_bottleneck.md` | ## Q2 — bullet 1 (consistent check: 19.4k vs 9.4k uncached tokens at ~4.5 s) | research/02_input_redundancy/README.md Part A §3.8 | dropped (duplicate of Part A §3.8) |
| `weekly_progress/091626/agent_e2e_bottleneck.md` | ## Q2 — bullet 2 (why 100k did not help) | research/02_input_redundancy/README.md Part A §3.8 | dropped (duplicate of Part A §3.8) |
| `weekly_progress/091626/agent_e2e_bottleneck.md` | ## Q2 — bullet 3 (correction to the 2026-09-09 estimate) | research/02_input_redundancy/README.md Part A §3.8, and the teammate_overlap.md §4 bullet "Correction (2026-09-10 probe)" merged into the same Part A | dropped (duplicate) |
| `weekly_progress/091626/agent_e2e_bottleneck.md` | ## Q2 — bullet 4 (confirmed on 2026-09-13) | research/03_context_interventions/README.md § A.W › Q2 | kept (its fit and sweep figures are also in research/05_latency_breakdown/README.md Part A; the factor-of-1.4 check is only here) |
| `weekly_progress/091626/agent_e2e_bottleneck.md` | ## Q2 — Answer | research/03_context_interventions/README.md § A.W › Q2 | kept |
| `weekly_progress/091626/agent_e2e_bottleneck.md` | ## Q3. What would a tiered memory change? | research/03_context_interventions/README.md § A.W › Q3 | kept (candidate ladder by tier boundary, not the compaction ladder; the item-8 paragraph restates research/06_tool_cost/README.md Part B figures but ties them to ladder items 6 and 7) |
| `weekly_progress/091626/agent_e2e_bottleneck.md` | ## Q4. Do the batch read, outline demotion and stable prefix work? — experiment | research/03_context_interventions/README.md § A.W › Q4 | kept (cleanup note added) |
| `weekly_progress/091626/agent_e2e_bottleneck.md` | ## Q4 — results table, shell allowed | research/03_context_interventions/README.md § Results and § Answer quality (tables) | dropped (duplicate of the base tables) |
| `weekly_progress/091626/agent_e2e_bottleneck.md` | ## Q4 — results table, shell denied | research/03_context_interventions/README.md § Results › Shell-denied matrix (fixed driver, 2026-09-12) | dropped (duplicate of the base matrix) |
| `weekly_progress/091626/agent_e2e_bottleneck.md` | ## Q4 — "All shell-denied arms except the two outline runs and baseline r1 covered 17/17 chapters." | research/03_context_interventions/README.md § A.W › Q4 | kept (cleanup note added) |
| `weekly_progress/091626/agent_e2e_bottleneck.md` | ## Q4 — answer 1 (stable prefix) | research/03_context_interventions/README.md § A.W › Q4 | kept (one correction, one cleanup note) |
| `weekly_progress/091626/agent_e2e_bottleneck.md` | ## Q4 — answer 2 (batching) | research/03_context_interventions/README.md § What each intervention did › 1. Batching; § Conclusions item 2; paragraph after the shell-denied matrix | dropped (duplicate of the base) |
| `weekly_progress/091626/agent_e2e_bottleneck.md` | ## Q4 — answer 3 (outline demotion) | research/03_context_interventions/README.md § A.W › Q4 | kept (201-538 chars for the bare pointer is only here) |
| `weekly_progress/091626/agent_e2e_bottleneck.md` | ## Q4 — answer 4 (sizing) | research/03_context_interventions/README.md § Conclusions item 4; § Results › Shell-denied matrix rows NB200-* | dropped (duplicate of the base) |
| `weekly_progress/091626/agent_e2e_bottleneck.md` | ## Q5. Item 7, "in-engine KV splice for fetch tools": what is it exactly? | research/03_context_interventions/README.md § A.W › Q5 | kept (7b's KL figures restate research/04_kv_splice/README.md §4 item 1; 7a, the per-fault estimate and the self-hosted comparison are only here) |
| `weekly_progress/091626/agent_e2e_bottleneck.md` | ## Q6. Is the whitelisted server-side file read already shipped by Claude Code or Codex? | research/03_context_interventions/README.md § A.W › Q6 | kept |
| `weekly_progress/091626/agent_e2e_bottleneck.md` | ## Conclusion | research/03_context_interventions/README.md § A.W › Conclusion | kept (cleanup note added) |
| `weekly_progress/091626/agent_e2e_bottleneck.md` | ## Open items from this arc | research/03_context_interventions/README.md § A.W › Open items from this arc | kept (cleanup note added) |
| `weekly_progress/091626/agent_e2e_bottleneck.md` | ## Artifacts | research/03_context_interventions/README.md § A.W › Artifacts | kept (cleanup note added) |

Deck citations, rewritten from the rows above (`weekly_progress/091626/slides.html`, bottom-right counter):

- Slide 10, "agent_e2e_bottleneck.md Q1, Q2, Q4; s15_integrated_harness/context_intervention_profile.md": Q1 -> research/02_input_redundancy/README.md Part A §3.8; Q2 -> research/03_context_interventions/README.md § A.W › Q2 (and research/02_input_redundancy/README.md Part A §3.8); Q4 -> research/03_context_interventions/README.md § Results, § Answer quality, § Results › Shell-denied matrix, § What each intervention did, § Conclusions and § A.W › Q4; the report -> research/03_context_interventions/README.md. The slide's closing sentence (one task at 33-65 rounds, 363-807 s, 18-45 shrink events) quotes `latency_breakdown.md` on the KV-splice baselines, i.e. research/05_latency_breakdown/README.md Part A and research/04_kv_splice/README.md §2, not this topic.
- Slide 11, direction 12, "agent_e2e_bottleneck.md Q1 and Q4": Q1 -> research/02_input_redundancy/README.md Part A §3.8 (the 77-100% miss figures); Q4 -> research/03_context_interventions/README.md § Conclusions and § A.W › Q4. Its "39, 21 and 10 rounds" come from research/04_kv_splice/README.md §2 and §4 item 3.

## Cleanup notes

- Added the ARCHIVED status line (commit `64d1b11`; flags and `apply_interventions` never committed; `ix_analyze.py` still runs; the broken offline test was deleted).
- Report intro: cleanup note that none of the intervention flags, nor `--deny-bash`, was ever committed.
- "Shell-denied matrix": cleanup note on the run dates (2026-09-11, 08:19-10:01 UTC, against the heading's 2026-09-12), the unrecorded turn limit (timeout wall times 2,463 / 5,909 / 1,660 s) and NB-I2-r1's last three lead calls failing on the 5-hour cap. The heading itself is unchanged.
- "Answer quality": cleanup note that the answers, rubric and 45 judgements are not in the repo, so the table cannot be re-derived.
- "Threats to validity", 5-hour cap bullet: corrected "16 runs returned zero successful model calls after 18 rate-limit retries each" to 13 of 16 (the other 3 got 5, 6 and 8 responses; two of them recorded 6 retries, not 18; 5 of the 16 were shell-allowed FX runs).
- "Threats to validity", same bullet: corrected the re-run date 2026-09-12 to 2026-09-11 (trace timestamps; commit `64d1b11`).
- "Reproduce": cleanup note that the commands cannot run (flags never committed, `x3_prompt.txt` absent, analyzer path and non-recursion).
- A.W: one bracketed note listing the dropped digest blocks and their homes (Q1; three Q2 bullets; Q4's two tables and its batching and sizing answers).
- A.W › Q4 experiment: cleanup note on the never-committed flags, the deleted 34-check test suite and which subfolders hold the quarantined runs.
- A.W › Q4 "All shell-denied arms except ...": cleanup note that all nine runs read 17/17 chapters; the three excluded runs are the timeouts.
- A.W › Q4 stable-prefix answer: corrected "coverage 13/17 in two of three runs" to 12/17 and 13/17; cleanup note that "3-5x" is not reproduced by the committed tables (2.9x and 1.8x).
- A.W › Conclusion: cleanup note pointing to the "3-5x" note.
- A.W › Open items, item 1: cleanup note that the shell-denied answers are not recoverable from the repo.
- A.W › Artifacts: cleanup note that the flags were never committed and the test file was deleted.
- The compaction-ladder table is kept once, here; `research/related_work/teammate_memory_management.md` §4 (lines 132-141, ten byte-identical rows) now points to this README's section "Where the s15 cascade sits among the compaction choices". The digest's Q3 table is a different ladder (candidate changes by tier boundary) and is kept.
- Deleted in the cleanup, outside this folder: `s15_integrated_harness/tests/test_context_interventions.py` (34 checks; hard-coded `/home/yq335`; called the never-committed `apply_interventions`).
