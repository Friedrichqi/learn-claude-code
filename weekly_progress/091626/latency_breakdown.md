# End-to-end latency breakdown of one agentic round, by task category

Weekly progress, 2026-09-16 (experiment run 2026-09-12 evening). Full report:
`s15_integrated_harness/latency_profile.md`; all tables:
`s15_integrated_harness/traces/latency_profiling/latency_tables.md`.
Tooling: `scripts/profile_run.py` (`--stream`, `--write-root/--sandbox-from`, `--allow-python`,
`profile_timing` events), `scripts/latency_workloads.py` (workloads + scoring),
`scripts/latency_breakdown.py` (round reconstruction + tables), bench problems in
`scripts/latency_bench/coding/`. Sections 4 and 5 were added on 2026-09-13 after follow-up probes
of the provider's timing and cache behaviour and a re-measurement of the 50k-limit runs.

## 1. Questions

For file Q&A, a small coding bench and competition math: what does one agentic loop iteration cost
end to end, and how much of it is context preparation, the agent call (prefill vs decode) and tool
execution? How many input and output tokens per call (lead vs teammate)? How much teammate input is
redundant? How many agent runs does one task take?

## 2. Setup

s15 harness (`CONTEXT_LIMIT` 512k chars), `glm-5.3-flash` via z.ai with streaming, lane copy of the
repository, fresh state per run. Three categories x two modes: **team** = lead + 3 teammates, one
task-board item per teammate (5 repetitions); **solo** = the lead does the three tasks itself
(3 repetitions). 24 runs, all completed; coding 24/24 problems pass their tests, math 24/24 answers
correct (60 / 890 / 486), file Q&A 97-100% keyword coverage.

| category | the three tasks | shape |
|---|---|---|
| FQA | three questions over repository docs (45 / 20 / 9 KB private doc + shared 13 KB glossary), <= 15 lines with citations | prefill-heavy reads, short answers |
| CODE | implement `intervals`, `ttl_cache`, `expr` against unittest files in a sandbox, iterate until green | read spec, write, run tests, fix |
| MATH | AIME 1983-1 (60), 1986-5 (890), 1987 (486), full written solution | decode-heavy reasoning, tiny input |

A **round** = one model call of one agent plus the harness work around it, split into four buckets
that sum exactly to the round: **prep** (previous tool_end -> model_request: compaction pipeline,
memory recall, prompt/tool assembly, inbox), **model** (request -> response), **tools**
(tool_start -> tool_end spans), **other**. The lead's turn-end work (Stop hook + memory extraction)
is reported separately as **post**.

## 3. Results (team mode unless noted; means over pooled rounds)

**A. One round**

| | FQA lead | FQA teammate | CODE lead | CODE teammate | MATH lead | MATH teammate |
|---|---:|---:|---:|---:|---:|---:|
| gross per round (s), mean / median | 9.7 / 8.3 | 8.9 / 6.2 | 7.7 / 6.9 | 10.0 / 5.5 | 7.6 / 6.6 | 17.2 / 12.1 |
| context preparation | 0.1% | 0.0% | 7.0% (memory recall) | 0.0% | 0.1% | 0.0% |
| agent call | 99.8% | 99.8% | 92.9% | 99.6% | 99.8% | 99.9% |
| tool execution | 0.1% | 0.2% | 0.1% | 0.4% | 0.1% | 0.1% |
| turn-end memory work, amortised per lead round (s) | 7.7 | | 9.2 | | 5.5 | |
| (prep + turn-end work) share of lead-side time | 44% | | 58% | | 42% | |

Solo lead rounds: CODE 9.5 s, FQA 21.0 s, MATH 41.5 s (the lead decodes the answers itself);
prep 0.0-0.1%, tools 0.1-0.5%; turn-end work 6-22% of lead-side time (one turn per run).

**B. Inside the agent call** -- regression over all 446 agent calls:
`duration = 3.7 s + 0.033 ms x uncached prompt tokens + 17.4 ms x output tokens` (r2 0.96).

| share of call time | FQA lead | FQA teammate | CODE lead | CODE teammate | MATH lead | MATH teammate | solo MATH lead |
|---|---:|---:|---:|---:|---:|---:|---:|
| fixed (network, queue, start-up) | 38% | 42% | 52% | 37% | 49% | 22% | 9% |
| prefill of uncached tokens | 1.3% | 1.0% | 1.4% | 0.3% | 1.5% | 0.2% | 0.2% |
| decode | 62% | 58% | 56% | 59% | 50% | 77% | 89% |

The client-side time to the first delivered block is a flat 5.6-6.1 s in every group, but this
provider delivers thinking and tool_use blocks in bursts (29-59% of responses arrive whole), so it
is not a prefill measurement; the regression is.

**C. Tokens per call**

| | FQA lead | FQA teammate | CODE lead | CODE teammate | MATH lead | MATH teammate |
|---|---:|---:|---:|---:|---:|---:|
| prompt tokens, mean / median | 5,547 / 5,490 | 5,373 / 3,348 | 4,796 / 4,937 | 3,717 / 3,407 | 5,267 / 5,267 | 1,921 / 1,582 |
| uncached prompt tokens, mean | 3,766 | 2,586 | 3,099 | 905 | 3,577 | 983 |
| provider cache-read share | 32% | 52% | 35% | 76% | 32% | 49% |
| output tokens, mean / median | 343 / 189 | 296 / 95 | 231 / 143 | 337 / 68 | 215 / 152 | 760 / 518 |
| thinking share of output | 37% | 51% | 22% | 51% | 25% | 56% |

Solo lead: 7.1k / 13.8k / 4.5k prompt tokens and 332 / 943 / 2,107 output tokens per round (CODE / FQA / MATH).

**D. Teammate input redundancy** (ranges over the 5 runs)

| | FQA | CODE | MATH |
|---|---:|---:|---:|
| re-sent: prompt tokens already sent in the same teammate's previous call | 33-58% | 76-86% | 45-63% |
| served by the provider cache | 28-58% | 68-79% | 44-52% |
| file bytes another teammate fetched first (exact (file,line)) | 12-23% | 0% | no file reads |
| cross-redundant share of teammate prompt tokens | 8-27% | 0% | 0% |
| file content share of teammate prompt tokens | 76-86% | 21-30% | 0% |

Lead: 84-91% re-sent but only 20-42% cache reads (per-second timestamp + memory catalog break the prefix).

**E. Agent runs per task and end-to-end time**

| | FQA team | CODE team | MATH team | FQA solo | CODE solo | MATH solo |
|---|---:|---:|---:|---:|---:|---:|
| teammate rounds per task, mean / median / max | 4.0 / 4 / 6 | 7.5 / 6 / 15 | 2.9 / 3 / 5 | | | |
| teammate active time per task (s), mean | 36 | 75 | 49 | | | |
| lead rounds / turns per run | 10.2 / 3.8 | 11.2 / 5.4 | 11.0 / 4.6 | 3.7 / 1 | 15.7 / 1 | 2.3 / 1 |
| model calls per run (incl. memory) | 26.8 | 41.4 | 24.2 | 4.7 | 16.7 | 3.3 |
| wall per run (s) / per task (s) | 180 / 60 | 215 / 72 | 165 / 55 | 101 / 34 | 161 / 54 | 118 / 39 |
| lead busy share of wall | 98% | 88% | 87% | 98% | 99% | 98% |
| of which turn-end memory extraction (s) | 78 | 103 | 60 | 22 | 10 | 19 |
| teammate active share of wall | 26% | 52% | 50% | | | |

Totals over 24 runs: agent calls 4,704 s, turn-end memory work 1,364 s, context preparation 32 s,
tool execution 9.4 s, wall 3,946 s.

## 4. How prefill and decode were separated (probes, 2026-09-13)

The provider's API returns no timing. `usage` carries token counts only, and
`cache_creation_input_tokens` is always null. The HTTP response does carry `x-process-time`, the
server's own processing seconds, next to `x-log-id` and `x-request-id`. On a streaming request with
a 27k-token prompt it reads 2.2-2.7 s when the prompt is new and 1.0 s when the same prompt is
served from cache, so it does track prefill; the client meanwhile waits 3.3-5.5 s for the first
block, which puts 2-3 s of the fixed component outside anything the server counts.

Client-side streaming timing cannot serve as a prefill measure directly, for three reasons visible
in the runs:

- **Burst delivery.** The provider withholds thinking and tool_use blocks and ships them when
  generation ends: 29-59% of agent calls had a streamed tail under 0.1 s, so for those the time to
  the first block is the whole call.
- **Delta timing is not token timing.** The median gap between `content_block_delta` events is
  0.2 ms while the mean is 13.4 ms, because server-sent events arrive in bursts. Only the aggregate
  rate, output tokens over the streamed tail, is usable.
- **The fixed component jitters more than prefill costs.** It moves by +-2 s between calls, while
  the whole prefill term at these prompt sizes is 30-150 ms.

Prefill and decode are therefore separated by regressing call duration on uncached prompt tokens and
output tokens. A dedicated sweep confirms the coefficient by two independent routes: a prompt-size
sweep with the output held fixed, and re-sending an identical prompt so its tokens come from cache.
Both, plus the cache test and the header dump below, are `scripts/provider_probe.py`
(`--prefill`, `--cache`, `--headers`).

| method | fixed part | per uncached token | implied prefill rate |
|---|---:|---:|---:|
| size sweep 460 -> 32,565 uncached tokens, 300-token output, no tools (r2 0.88) | 3.34 s | 0.0384 ms | 26,000 tok/s |
| identical resend, 11,584 tokens served from cache | | 0.0342 ms saved | |
| identical resend, 32,512 tokens served from cache | | 0.0416 ms saved | |
| in-run regression over all 446 agent calls (r2 0.96) | 3.72 s | 0.033 ms | 30,000 tok/s |
| direct probe of 2026-09-10, separate session | 4.6 s | 0.052 ms | 19,000 tok/s |

Decode measured the same way is 81 tokens/s for 300-token outputs and 39 tokens/s for a 1,200-token
output, that is 12-26 ms per token, bracketing the regression's 17.4 ms. The share of a round that
prefill occupies is therefore set by the uncached tokens per call: 0.4-1.6% at this week's 0.9-3.8k,
about 10% at the 9-19k of the X3 runs (`agent_e2e_bottleneck.md` Q2), and seconds only above roughly
50k per call.

Two further facts from the probes:

- **The provider's prompt cache is strictly prefix-based.** An identical resend came back 99.9%
  cached; the same body behind a new prefix 0%; a unique block inserted mid-prompt 0%; a document
  never sent before 0% on each of two sends. A shared file block is reusable only if it sits at the
  same position in the same prefix, which is exactly the constraint on next step 5 of
  `tiered_memory_conclusion.md`.
- **SDK retries can masquerade as cache hits.** One nominally fresh call in the sweep returned
  99.8% cached with an elevated first-block time; the SDK retries twice by default and the retry
  hits the entry its failed attempt created. Cache measurements have to check the retry count.

## 5. Why the context-preparation share is small here, when earlier runs showed 52-80% redundancy

Two different quantities have been called a context cost this week. The share in section 3 is
**harness wall time** spent preparing a request. The figures of `teammate_input_redundancy.md` are
shares of **input bytes or tokens** that are repeated: 52-80% of the bytes a teammate loads are
copies another teammate already holds when they share files, and file content is 70-89% of teammate
prompt tokens in the prefill-heavy runs. Both quantities were measured in these runs too, and the
repetition is still there:

| team runs | re-sent from that teammate's own previous call | file content as a share of teammate prompt tokens | bytes another teammate fetched first |
|---|---:|---:|---:|
| FQA | 33-58% | 76-86% | 12-23% |
| CODE | 76-86% | 21-30% | 0% |
| MATH | 45-63% | 0% | no file reads |

What changed is that none of it converts into time. The repeated prefix is cache-served, 28-79% of
teammate prompt tokens, and at 0.038 ms per uncached token re-sending a 5k-token history costs
0.19 s, about 2% of a round.

**Cross-teammate sharing is set by the workload, exactly.** The measured rate equals each workload's
design ceiling, so it says nothing about the harness:

| workload | what the teammates read | ceiling | measured |
|---|---|---:|---|
| FQA, this week | a private doc plus the shared 12,871-byte glossary | 22.8% | 22.8, 22.8, 22.8, 15.6, 12.4% |
| CODE, this week | three disjoint problem directories | 0% | 0% in all five runs |
| MATH, this week | no files at all | n/a | 0 bytes fetched |
| PF-S, 2026-09-10 | all three read the same two files in full | 66.7% | 61.3, 61.5% |

Teammate self re-reads were 0.0% in all ten team runs measured: every agent fetched each byte once.

**The larger difference from the codebase experiments is context pressure, not file sharing.** Those
runs (X3 workload, the `traces/kv_splice` baselines) sat at the 50k-character limit, where eviction
forces re-acquisition:

| | X3 codebase task, 50k limit | this week, 512k limit |
|---|---|---|
| lead rounds per run | 33-65, for one task | 2-20, for three tasks |
| wall per run | 363-807 s | 82-249 s |
| context shrink events | 18-45 | 0 |
| placeholders resident in one call | 26-35 | 0 |
| summary compactions | 0-1 | 0 |
| lead file bytes that were re-reads | 46 / 7 / 0% | 0% in all 24 runs |
| lead cache-read share | 16-19% | 20-42% |
| preparation per round | 0.65 s, 5.6% | 0.01 s, 0.1% |

The last row resolves the question. Even under heavy eviction, preparation per round is 0.65 s, and
0.62 s of that is one summary-compaction model call amortised over the run; the compaction code
itself costs 25 ms. Redundancy and eviction are paid in **round count**, three to five times here,
and in KV memory, not in preparation time. Neither experiment could have shown a large preparation
share.

## 6. Conclusions

1. **Per round, the agent call is everything (93-99.9%); context preparation and tool execution are
   about 10 ms and 10-50 ms.** Preparation only costs when the harness calls the model to do it.
2. **Prefill is under 2% of call time; the fixed provider component (3.3-3.7 s) and decode
   (12-26 ms per token) are the rest.** Three independent measurements agree on 0.033-0.042 ms per
   uncached token, so re-prefilling a 5k-token history costs about 0.19 s, and prefill becomes
   seconds only above roughly 50k tokens per call. KV or prefix reuse cannot shorten these rounds
   measurably; its value at this scale is capacity, not latency.
3. **The harness's turn-end memory extraction (13-21 s per lead turn, every team event is a turn)
   is the largest non-agent cost: 35% of all wall time, 42-58% of the lead's busy time in team runs.**
   Running it once per user request or asynchronously would cut team-run wall time by 28-48%.
4. **Teammate redundancy is mostly self-redundancy (33-86% re-sent, served by the provider cache);
   cross-teammate duplication is 8-27% of prompt tokens only when tasks share files.**
5. **Rounds per task: math 3, file Q&A 4, coding 7.5 (teammate); the lead adds 10-11 orchestration
   rounds per run.** For these small tasks a solo lead was 1.4-1.8x faster end to end at equal quality.
6. **That repetition is as high as in the earlier runs; it simply does not buy latency.** Cross-team
   duplication tracks each workload's design ceiling exactly, 22.8% measured against a 22.8% ceiling
   in file Q&A and 0% with disjoint problems, so it measures how the tasks were cut rather than the
   harness; and at 0.038 ms per uncached token the repeated input costs tokens and KV memory, not
   time.
7. **Under context pressure the bill arrives as rounds.** At the 50k limit the same harness needed
   33-65 rounds and 363-807 s for a single task, with 18-45 shrink events and up to 46% of the
   lead's file bytes re-read, while preparation per round was still 0.65 s of which 95% was one
   summary call. Sizing the resident set stays the lever (`agent_e2e_bottleneck.md` Q1).

Caveats: single provider and model on one evening, with the fixed component varying 1.5-7 s between
probes; streaming against plain requests was checked (7.5 against 8.3 s, no inflation);
`x-process-time` excludes whatever queueing happens before the server starts counting; 24 denied
tool calls, mostly a driver-filter false positive on `>` inside quoted python and fixed afterwards,
cost about one round per run; file-Q&A scoring is keyword coverage, not a reader.

Two analyzer fixes made while checking these numbers, both affecting reproduction:
`latency_breakdown.py` no longer collects `.requests.` or `.replay.` sidecars as traces, and its
compaction column no longer double counts the summary-compaction model call, which runs inside the
`context_prepare` span and is now reported separately. `input_redundancy.py`'s file-content share
assumes an item stays resident in every later call of that agent, so it overstates for runs with
eviction or lead-side persistence; it is used here only for runs where nothing was evicted.
