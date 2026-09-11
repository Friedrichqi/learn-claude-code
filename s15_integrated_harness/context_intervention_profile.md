# Three context-hierarchy interventions on the s15 lead loop

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

All three are flags on `scripts/profile_run.py`, which monkeypatches the loaded harness module, so
the committed harness is byte-identical between control and treated runs. `--deny-bash` is a
control that removes the shell so every acquisition must go through the read path.

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
  `--deny-bash` condition exists to remove this and has not yet produced usable data.
- **The provider's 5-hour usage cap** truncated the shell-denied matrix: 16 runs returned zero
  successful model calls after 18 rate-limit retries each. Those runs are quarantined under
  `traces/context_interventions/quota_killed/` and are queued to re-run after the cap resets.
- **`chapters read` undercounts in the free-strategy condition**, because a chapter read with
  `cat` through the shell is not attributed to a chapter. It is a valid completion proxy only in
  the shell-denied condition.
- Intervention 1 has n=1 at the budget where it works, and intervention 2 has no post-review run.
- The trace-derived timing model is client-observed latency, and a direct streaming probe of this
  endpoint puts a cheap round at ~80% fixed latency, ~10% prefill, ~5% decode.

## Reproduce

```sh
# one run per arm, 50k budget (the thrashing regime)
python3 s15_integrated_harness/scripts/profile_run.py --label B  --context-limit 50000 --prompt "$(cat x3_prompt.txt)"
python3 s15_integrated_harness/scripts/profile_run.py --label I1 --context-limit 50000 --batch-read      --prompt ...
python3 s15_integrated_harness/scripts/profile_run.py --label I2 --context-limit 50000 --skeleton-demote --prompt ...
python3 s15_integrated_harness/scripts/profile_run.py --label I4 --context-limit 50000 --stable-prefix   --prompt ...
# isolation control: the shell cannot substitute for the read path
python3 s15_integrated_harness/scripts/profile_run.py --label NB --context-limit 50000 --deny-bash       --prompt ...
# the budget where batching has headroom
python3 s15_integrated_harness/scripts/profile_run.py --label I1-200 --context-limit 200000 --batch-read --prompt ...

python3 ix_analyze.py s15_integrated_harness/traces/context_interventions
```
