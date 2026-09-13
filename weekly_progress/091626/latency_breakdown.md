# End-to-end latency breakdown of one agentic round, by task category

Weekly progress, 2026-09-16 (experiment run 2026-09-12 evening). Full report:
`s15_integrated_harness/latency_profile.md`; all tables:
`s15_integrated_harness/traces/latency_profiling/latency_tables.md`.
Tooling: `scripts/profile_run.py` (`--stream`, `--write-root/--sandbox-from`, `--allow-python`,
`profile_timing` events), `scripts/latency_workloads.py` (workloads + scoring),
`scripts/latency_breakdown.py` (round reconstruction + tables), bench problems in
`scripts/latency_bench/coding/`.

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

## 4. Conclusions

1. **Per round, the agent call is everything (93-99.9%); context preparation and tool execution are
   about 10 ms and 10-50 ms.** Preparation only costs when the harness calls the model to do it.
2. **Prefill is under 2% of call time; the fixed provider component (3.7 s) and decode (17 ms per
   token) are the rest.** Re-prefilling a 5k-token history costs about 0.17 s. KV/prefix reuse
   cannot shorten these rounds measurably; its value is capacity, not latency.
3. **The harness's turn-end memory extraction (13-21 s per lead turn, every team event is a turn)
   is the largest non-agent cost: 35% of all wall time, 42-58% of the lead's busy time in team runs.**
   Running it once per user request or asynchronously would cut team-run wall time by 28-48%.
4. **Teammate redundancy is mostly self-redundancy (33-86% re-sent, served by the provider cache);
   cross-teammate duplication is 8-27% of prompt tokens only when tasks share files.**
5. **Rounds per task: math 3, file Q&A 4, coding 7.5 (teammate); the lead adds 10-11 orchestration
   rounds per run.** For these small tasks a solo lead was 1.4-1.8x faster end to end at equal quality.

Caveats: single provider/model on one evening (fixed component varies 1.5-7 s); streaming vs plain
requests were checked (7.5 vs 8.3 s, no inflation); 24 denied tool calls (mostly a driver filter
false positive on `>` inside quoted python, fixed) cost about one round per run; scoring is
heuristic for file Q&A.
