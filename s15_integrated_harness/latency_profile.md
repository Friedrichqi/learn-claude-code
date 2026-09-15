# End-to-end latency breakdown of the s15 agentic loop, by task category

Profiling question (2026-09-12): for three kinds of tasks -- file question answering, a small coding
bench and competition-style math problems -- what does one agentic loop iteration ("round": one
model call of one agent plus the harness work around it) cost end to end, and how does that time
split into **context preparation**, the **agent call** (prefill vs decode) and **tool execution**?
How many input tokens does a call carry and how many tokens does it decode, for the lead and for
teammates? How much of a teammate's input is redundant? How many agent runs does one task take?

Setup: s15 integrated harness (`s15_integrated_harness/code.py`, `CONTEXT_LIMIT` 512,000 chars),
model `glm-5.3-flash` via z.ai (Anthropic-compatible Messages API, streaming), lead + 3 teammates
per team run, one lead per solo run, fresh state per run, repository read-only except a per-run
sandbox for the coding bench. Runs execute from a lane copy of the working tree
(`/home/yq335/lanes/latency`) so a stray write cannot touch the repository; traces are written to
`s15_integrated_harness/traces/latency_profiling/`.

## Short answer

Twenty-four runs completed (five team runs and three solo runs per category); every coding problem
passed its tests (24/24), every math answer was correct (24/24) and the file-Q&A answers covered
97-100% of the expected facts. All numbers below come from `scripts/latency_breakdown.py` over
`traces/latency_profiling/` (full tables in `traces/latency_profiling/latency_tables.md`).

- **One agentic round costs 7-10 s for the lead and 9-17 s for a teammate** in team runs (means;
  medians 6-8 s and 6-12 s). File Q&A: lead 9.7 s / teammate 8.9 s; coding: 7.7 / 10.0 s; math:
  7.6 / 17.2 s. A solo lead that does the work itself takes 9.5 s (coding), 21 s (file Q&A) and
  42 s (math) per round because it decodes the answers itself.
- **Context preparation is 0.1% of a round (about 10 ms) as long as it stays inside the harness:**
  the compaction pipeline takes 2 ms, system-prompt and tool-pool assembly under 1 ms, inbox reads
  about 1 ms. It becomes visible only when the harness calls the model to prepare context: memory
  recall inside `update_context` costs 4.9 s per call and fired in 21% of the coding team's lead
  rounds (7% of round time, 24% in one run), and the turn-end memory extraction
  (`remember_after_turn`) costs 13-21 s per lead turn. Amortised over lead rounds that turn-end work
  is 5.5-9.2 s per round in team mode, i.e. **context maintenance is 42-58% of the lead's busy time**
  (6-22% in solo runs, which have one turn).
- **The agent call is 93-99.9% of a round.** A regression over all 446 agent calls gives
  `duration = 3.7 s + 0.033 ms x uncached prompt tokens + 17.4 ms x output tokens` (r2 0.96), and a
  direct prompt-size sweep run afterwards agrees at 0.038 ms per uncached token (section 2.5.1):
  a fixed provider component (9-52% of call time by group), **prefill of uncached tokens under 2%
  everywhere**, and decode 50-89%. Decode dominates reasoning rounds (math teammates 77%, solo math
  lead 89%); the fixed component dominates short tool-call rounds (lead 38-52%). The client-side
  time to the first delivered block is a flat 5.6-6.1 s for every group, but it is not prefill: the
  provider delivers thinking and tool_use blocks in bursts (29-59% of calls arrive whole), so only
  the regression separates prefill from decode.
- **Tool execution is 0.1-0.5% of a round:** 10-50 ms. The slowest tool is `bash` running the unit
  tests (79 ms mean, 220 ms max); `read_file` takes 13 ms; task-board and team tools 1-21 ms. Over
  all 24 runs tools took 9.4 s against 4,704 s of agent calls and 1,364 s of turn-end memory work.
- **Tokens per call.** Lead: 4.8-5.5k prompt tokens (3.1-3.8k uncached, 26-40% served from the
  provider cache), 215-343 output tokens (medians 143-189, 22-37% thinking). Teammates: 1.9k (math),
  3.7k (coding), 5.4k (file Q&A) prompt tokens with 49-76% cache reads; 296-760 output tokens
  (medians 68-518, 51-56% thinking). A solo lead decodes 332 (coding), 943 (file Q&A) and 2,107
  (math) tokens per round.
- **Teammate input redundancy.** 33-86% of a teammate's prompt tokens were already sent in its own
  previous call (append-only history), and the provider's prefix cache served almost all of them
  (28-79% cache reads). Content another teammate had already fetched is 12-23% of file bytes in file
  Q&A (the shared glossary, 8-27% of teammate prompt tokens), 0% in coding (disjoint problems) and
  absent in math (no file reads). File content is 76-86% of teammate prompt tokens in file Q&A and
  21-30% in coding.
- **Agent runs per task.** A teammate needs 2.9 rounds for a math problem, 4.0 for a file question
  and 7.5 for a coding problem (medians 3 / 4 / 6, maximum 15), i.e. 36-75 s of active time per
  task. The lead adds 10-11 rounds over 4-5 turns (user turns 4.5 rounds, team-event turns 1.7) to
  create tasks, spawn, react, synthesise and shut down: 24-41 model calls per run. A solo lead
  finishes all three tasks in 2.3 (math), 3.7 (file Q&A) or 15.7 (coding) rounds.
- **End to end, teams were slower than a solo lead on these small tasks:** 165-215 s per team run
  (55-72 s per task) against 101-161 s solo (34-54 s per task). The lead is busy 87-98% of the wall
  time (80-99 s of its own calls plus 60-103 s of turn-end memory extraction), teammate work
  (48-111 s) overlaps it only partly, and the idle tail is 1-2%.

## 1. Method

### 1.1 What is measured

A **round** is one model call of one agent together with the harness work that surrounds it. Rounds
are reconstructed from the JSONL trace of each run; the four buckets add up exactly to the round's
gross time:

| bucket | lead | teammate |
|---|---|---|
| **prep** (context preparation) | from the end of the previous round (last `tool_end`, or the turn's `agent_active_start`) to the `model_request`: `prepare_context` compaction pipeline (traced span), memory recall in `update_context` (a model call when memory records exist), tool-pool and system-prompt assembly, hooks | from the last `tool_end` to the `model_request`: inbox read, team-state bookkeeping; for the first round of a task the idle wait before the model cycle is excluded and reported separately |
| **model** (agent call) | `model_request` to `model_response` including provider 429 retries; streaming additionally records the client-side time to the **first delivered block** and the **streamed tail** after it, and prefill vs decode is estimated by a regression of call duration on uncached prompt tokens and output tokens (section 2.5), because this provider delivers thinking and tool_use blocks in bursts | same |
| **tools** | sum of `tool_start` to `tool_end` spans dispatched for the response (permission hooks, execution, result summarisation) | same |
| **other** | decision events, result appends, gaps between tools | same |

A round without a `tool_use` block is **final**. For the lead it is followed by turn-end work (Stop
hook, `remember_after_turn` memory extraction, post-turn memory recall), reported as **post** per
turn; for a teammate it is followed by idle waiting on the message bus, excluded from the round.
A **teammate task** is the sequence of rounds from an assignment (or a lead message) to the final
reply. A **lead turn** is one activation of the lead (user prompt or team event).

Token counts come from the provider's `usage`: prompt = `input_tokens` + `cache_read_input_tokens`
(z.ai reports `input_tokens` excluding cached tokens); uncached = `input_tokens`; output =
`output_tokens` (thinking + text + tool arguments). Decode rate = output tokens / streamed-tail time,
pooled; the first-block time is fitted as `a + b * uncached tokens` per agent kind and the call
duration as `a + b * uncached tokens + c * output tokens` over all calls.

Teammate input redundancy is reported three ways: (a) **re-sent share** -- the share of a call's
prompt tokens that were already sent in the same agent's previous call (teammate histories are
append-only, so `prompt_i - prompt_{i-1}` is the new part); (b) **cache-read share** -- what the
provider's prefix cache actually served; (c) **cross-teammate redundancy** -- the share of file
bytes a teammate fetched that another teammate had fetched first (exact (file, line) provenance,
`scripts/input_redundancy.py`), and the share of teammate prompt tokens those bytes occupy.

### 1.2 Instrumentation

- `scripts/profile_run.py` (extended): `--stream` sends every request through
  `client.messages.stream` and records per call the time of `message_start`, of the first content
  block, of the first visible (non-thinking) block and of the end of the stream, plus the
  first block's type; the final message object is identical in shape to a non-streaming response,
  so the harness is unchanged. `profile_timing` trace events wrap `update_context`,
  `assemble_system_prompt`, `assemble_tool_pool`, `remember_after_turn`, `consume_lead_inbox`,
  `format_team_events` and `MessageBus.read_inbox`. `--write-root DIR --sandbox-from SRC` confines
  `write_file`/`edit_file` to a sandbox that is copied in before the run and archived next to the
  trace afterwards; `--allow-python` lets asynchronous teammates run `python3` commands that pass the
  mutation filter (the harness otherwise denies every non-display shell command off the main
  thread); `create_worktree` is denied so all agents share the repository directory.
- `scripts/latency_workloads.py`: workload prompts, sequential runner, scoring.
- `scripts/latency_breakdown.py`: round reconstruction and all tables in this report
  (`--per-run`, `--tools`, `--json`).
- `scripts/provider_probe.py`: direct probes of the provider used in section 2.5.1 (prefill sweep,
  cached resends, decode rate, prefix-cache semantics, response headers).

### 1.3 Workloads

Each category is a set of three tasks. In **team** mode the lead is told to create three task-board
items, spawn one teammate per item without asking for confirmation, synthesise the three results and
shut the team down. In **solo** mode the lead is told to do the three items itself without
delegating.

| id | category | the three tasks | expected shape |
|---|---|---|---|
| FQA | file question answering | Q1 compaction layers (ARCHITECTURE.md section 6 + GLOSSARY.md); Q2 how teammates obtain work, result vs idle events, plan gate (s13 README + GLOSSARY.md); Q3 task states, blockedBy check, who adds dependencies (s10 README + GLOSSARY.md); answers <= 15 lines with citations | prefill-heavy reads (45 KB / 20 KB / 9 KB private docs + shared 13 KB glossary), short answers |
| CODE | coding bench | implement `intervals` (merge / free gaps / max overlap), `ttl_cache` (LRU + per-entry TTL, injected clock), `expr` (tokenizer + precedence parser with functions) against unittest files in `scripts/latency_bench/coding/`; write `solution.py` in `profiling_sandbox/coding/<p>/`, run `python3 .../test_<p>.py`, iterate until green | read spec, write code, run tests, fix: mixed tool use and decode |
| MATH | math problems | AIME 1983 #1 (log_z w = 60), AIME 1986 #5 (n = 890), AIME 1987 (k = 486): full written solution ending in `Final answer: <integer>`; `python3 -c` allowed for arithmetic | decode-heavy reasoning, tiny inputs |

Scoring: CODE = number of problems whose archived `solution.py` passes its test file (re-run by the
runner); MATH = number of problems whose teammate result (or lead reply) contains the expected
`Final answer` line; FQA = keyword coverage of the expected facts (10 keys per question) in the
teammate results.

## 2. Results

Group names are `<category>-<mode>`: FQA = file Q&A, CODE = coding bench, MATH = math; team = lead
+ 3 teammates (5 runs), solo = lead alone (3 runs). Means are over pooled rounds or calls unless a
column says otherwise.

### 2.1 Runs

**Runs** (lead turns = lead activations; rounds = model calls of the agent loop; memory/compaction = auxiliary model calls; score: CODE tests passed, MATH answers correct, FQA keyword coverage)
| run | status | wall (s) | lead turns | lead rounds | teammates | teammate rounds | teammate tasks (closed) | memory calls | compaction calls | model errors (429 etc.) | tool calls (denied) | score |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| CODE-solo-r1 | completed | 106 | 1 | 14 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 15 (0) | 3/3 |
| CODE-solo-r2 | completed | 165 | 1 | 13 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 14 (0) | 3/3 |
| CODE-solo-r3 | completed | 213 | 1 | 20 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 22 (0) | 3/3 |
| FQA-solo-r1 | completed | 133 | 1 | 4 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 6 (0) | coverage 0.967 |
| FQA-solo-r2 | completed | 89 | 1 | 2 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 4 (0) | coverage 0.967 |
| FQA-solo-r3 | completed | 82 | 1 | 5 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 9 (0) | coverage 0.967 |
| MATH-solo-r1 | completed | 115 | 1 | 2 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 1 (1) | 3/3 |
| MATH-solo-r2 | completed | 118 | 1 | 3 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 4 (2) | 3/3 |
| MATH-solo-r3 | completed | 121 | 1 | 2 | 0 | 0 | 0 (0) | 1 | 0 | 0 | 1 (1) | 3/3 |
| CODE-team-r1 | completed | 211 | 5 | 10 | 3 | 24 | 3 (3) | 8 | 0 | 0 | 38 (1) | 3/3 |
| CODE-team-r2 | completed | 206 | 5 | 11 | 3 | 27 | 3 (3) | 5 | 0 | 0 | 40 (4) | 3/3 |
| CODE-team-r3 | completed | 249 | 5 | 11 | 3 | 23 | 3 (3) | 14 | 0 | 0 | 36 (0) | 3/3 |
| CODE-team-r4 | completed | 242 | 5 | 10 | 3 | 18 | 3 (3) | 5 | 0 | 0 | 32 (0) | 3/3 |
| CODE-team-r5 | completed | 168 | 7 | 14 | 3 | 20 | 3 (3) | 7 | 0 | 0 | 37 (0) | 3/3 |
| FQA-team-r1 | completed | 178 | 3 | 9 | 3 | 11 | 3 (3) | 3 | 0 | 0 | 27 (1) | coverage 1.0 |
| FQA-team-r2 | completed | 162 | 4 | 11 | 3 | 13 | 3 (3) | 4 | 0 | 0 | 29 (1) | coverage 1.0 |
| FQA-team-r3 | completed | 214 | 5 | 12 | 3 | 14 | 4 (4) | 5 | 0 | 0 | 31 (1) | coverage 1.0 |
| FQA-team-r4 | completed | 185 | 4 | 10 | 3 | 16 | 3 (3) | 4 | 0 | 0 | 30 (0) | coverage 1.0 |
| FQA-team-r5 | completed | 163 | 3 | 9 | 3 | 10 | 3 (3) | 3 | 0 | 0 | 25 (1) | coverage 0.967 |
| MATH-team-r1 | completed | 174 | 5 | 12 | 3 | 8 | 3 (3) | 5 | 0 | 0 | 19 (2) | 3/3 |
| MATH-team-r2 | completed | 157 | 4 | 11 | 3 | 8 | 3 (3) | 4 | 0 | 0 | 20 (2) | 3/3 |
| MATH-team-r3 | completed | 169 | 5 | 13 | 3 | 10 | 3 (3) | 5 | 0 | 0 | 24 (3) | 3/3 |
| MATH-team-r4 | completed | 176 | 4 | 9 | 3 | 8 | 3 (3) | 4 | 0 | 0 | 19 (1) | 3/3 |
| MATH-team-r5 | completed | 148 | 5 | 10 | 3 | 9 | 3 (3) | 5 | 0 | 0 | 20 (3) | 3/3 |

### 2.2 One agentic round

**Latency breakdown of one agentic round** (time-weighted shares of the pooled round time; means per round in seconds)
| group | kind | rounds | final rounds | gross mean | gross median | gross p90 | prep | model | tools | other | prep share | model share (to first delivered block / streamed tail) | tools share | other share |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo | lead | 47 | 3 | 9.5 | 6.1 | 20.2 | 0.01 | 9.5 | 0.05 | 0.002 | 0.1% | 99.4% (54.3% / 45.2%) | 0.5% | 0.0% |
| FQA-solo | lead | 11 | 3 | 21.0 | 9.0 | 57.6 | 0.01 | 21.0 | 0.02 | 0.008 | 0.0% | 99.8% (28.6% / 71.2%) | 0.1% | 0.0% |
| MATH-solo | lead | 7 | 3 | 41.5 | 42.5 | 69.7 | 0.00 | 41.5 | 0.02 | 0.001 | 0.0% | 99.9% (13.7% / 86.3%) | 0.1% | 0.0% |
| CODE-team | lead | 56 | 27 | 7.7 | 6.9 | 12.6 | 0.54 | 7.2 | 0.01 | 0.001 | 7.0% | 92.9% (71.8% / 21.1%) | 0.1% | 0.0% |
| CODE-team | teammate | 112 | 15 | 10.0 | 5.5 | 15.0 | 0.00 | 10.0 | 0.04 | 0.001 | 0.0% | 99.6% (55.8% / 43.8%) | 0.4% | 0.0% |
| FQA-team | lead | 51 | 19 | 9.7 | 8.3 | 14.2 | 0.01 | 9.7 | 0.01 | 0.002 | 0.1% | 99.8% (62.4% / 37.5%) | 0.1% | 0.0% |
| FQA-team | teammate | 64 | 16 | 8.9 | 6.2 | 17.0 | 0.00 | 8.9 | 0.01 | 0.003 | 0.0% | 99.8% (62.3% / 37.5%) | 0.2% | 0.0% |
| MATH-team | lead | 55 | 23 | 7.6 | 6.6 | 11.7 | 0.01 | 7.6 | 0.01 | 0.001 | 0.1% | 99.8% (78.2% / 21.6%) | 0.1% | 0.0% |
| MATH-team | teammate | 43 | 15 | 17.2 | 12.1 | 34.7 | 0.00 | 17.1 | 0.01 | 0.001 | 0.0% | 99.9% (35.5% / 64.4%) | 0.1% | 0.0% |

The four buckets add up to the round's gross time by construction. `final rounds` are rounds
without tool use (the lead's reply of a turn, a teammate's final result). Medians are well below
the means: the p90 rounds are the ones that decode long thinking or a long document.

### 2.3 Context preparation, attributed

**Context preparation, attributed** (lead rounds; seconds per round, pooled by group; `memory recall model` = model calls made by update_context; `post` = turn-end work after a final round: Stop hook + memory extraction + post-turn recall)
| group | rounds | prep mean | compaction pipeline | memory recall (model) | memory recall calls / round | prompt+tool assembly | inbox | unattributed | turns | post per turn | memory extract (model) per turn | memory calls per turn | post amortised per round | (prep + post) share of lead-side time |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo | 47 | 0.01 | 0.002 | 0.00 | 0.00 | 0.000 | 0.000 | 0.004 | 3 | 10.1 | 10.1 | 1.00 | 0.6 | 6.4% |
| FQA-solo | 11 | 0.01 | 0.002 | 0.00 | 0.00 | 0.000 | 0.000 | 0.006 | 3 | 22.2 | 22.2 | 1.00 | 6.1 | 22.4% |
| MATH-solo | 7 | 0.00 | 0.001 | 0.00 | 0.00 | 0.000 | 0.000 | 0.003 | 3 | 19.1 | 19.1 | 1.00 | 8.2 | 16.5% |
| CODE-team | 56 | 0.54 | 0.002 | 0.53 | 0.11 | 0.000 | 0.000 | 0.004 | 27 | 19.1 | 19.1 | 1.22 | 9.2 | 57.6% |
| FQA-team | 51 | 0.01 | 0.002 | 0.00 | 0.00 | 0.000 | 0.000 | 0.004 | 19 | 20.6 | 20.6 | 1.00 | 7.7 | 44.2% |
| MATH-team | 55 | 0.01 | 0.002 | 0.00 | 0.00 | 0.000 | 0.000 | 0.004 | 23 | 13.1 | 13.1 | 1.00 | 5.5 | 42.1% |

`compaction pipeline` is the traced `prepare_context` span; `prompt+tool assembly` and `inbox` come
from the `profile_timing` events; `unattributed` is the remaining gap (hooks, trace writes,
Python overhead). `post per turn` is the work after the lead's final response until the turn ends,
almost entirely the `memory_extract` model call. Compaction never fired: the largest lead history
was 24k uncached tokens, far below the 512,000-character `CONTEXT_LIMIT`.

### 2.4 Tokens and model-call timing per call

**Tokens and model-call timing per call** (agent calls only: purpose lead / teammate; prompt = input + cache_read (+cache_creation); uncached = input_tokens as reported; first block = client time to the first delivered content block (NOT prefill alone: thinking/tool_use blocks arrive in bursts, see the delivery-pattern table); tail = first block to stream end; fit: first-block time = a + b x uncached tokens)
| group | kind | calls | prompt tok mean | prompt tok median | uncached mean | cache-read share | output tok mean | output tok median | thinking share of output chars | model call mean | first block mean | first block median | tail mean | tail tok/s (pooled) | first-block fit a (s) + b (ms/tok), r2 | first block is thinking |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| CODE-solo | lead | 47 | 7,136 | 6,686 | 5,249 | 26.4% | 332 | 116 | 37.8% | 9.5 | 5.2 | 5.2 | 4.3 | 77.3 | 5.45 s + -0.052 ms/tok, r2 0.00 | 46.8% |
| FQA-solo | lead | 11 | 13,801 | 18,183 | 12,073 | 12.5% | 943 | 302 | 49.3% | 21.0 | 6.0 | 6.1 | 15.0 | 63.0 | 6.26 s + -0.020 ms/tok, r2 0.10 | 100.0% |
| MATH-solo | lead | 7 | 4,533 | 5,223 | 2,722 | 39.9% | 2,107 | 2,387 | 68.0% | 41.5 | 5.7 | 6.1 | 35.8 | 58.8 | 5.92 s + -0.087 ms/tok, r2 0.01 | 100.0% |
| CODE-team | lead | 56 | 4,796 | 4,937 | 3,099 | 35.4% | 231 | 143 | 22.1% | 7.2 | 5.5 | 5.9 | 1.6 | 142.2 | 5.38 s + 0.054 ms/tok, r2 0.00 | 71.4% |
| CODE-team | teammate | 112 | 3,717 | 3,407 | 905 | 75.7% | 337 | 68 | 51.0% | 10.0 | 5.6 | 5.4 | 4.4 | 76.6 | 5.66 s + -0.061 ms/tok, r2 0.00 | 57.1% |
| FQA-team | lead | 51 | 5,547 | 5,490 | 3,766 | 32.1% | 343 | 189 | 36.8% | 9.7 | 6.1 | 6.2 | 3.6 | 94.3 | 5.09 s + 0.257 ms/tok, r2 0.08 | 72.5% |
| FQA-team | teammate | 64 | 5,373 | 3,348 | 2,586 | 51.9% | 296 | 95 | 51.1% | 8.9 | 5.5 | 5.7 | 3.3 | 88.5 | 5.41 s + 0.055 ms/tok, r2 0.04 | 75.0% |
| MATH-team | lead | 55 | 5,267 | 5,267 | 3,577 | 32.1% | 215 | 152 | 25.2% | 7.6 | 5.9 | 5.9 | 1.6 | 131.9 | 6.38 s + -0.128 ms/tok, r2 0.02 | 67.3% |
| MATH-team | teammate | 43 | 1,921 | 1,582 | 983 | 48.8% | 760 | 518 | 56.1% | 17.1 | 6.1 | 6.1 | 11.0 | 68.8 | 5.79 s + 0.307 ms/tok, r2 0.04 | 88.4% |
| pooled fit | lead | 227 | | | uncached 629-24,254 | | | | | | | | | 84.4 | first block vs uncached: 5.73 s + -0.007 ms/tok (r2 0.00); vs prompt: 5.81 s + -0.018 ms/tok (r2 0.00); tail vs output tok: -2.15 s + 17.7 ms/tok (r2 0.97) | |
| pooled fit | teammate | 219 | | | uncached 48-16,374 | | | | | | | | | 75.6 | first block vs uncached: 5.64 s + 0.033 ms/tok (r2 0.01); vs prompt: 5.63 s + 0.014 ms/tok (r2 0.00); tail vs output tok: -1.41 s + 16.7 ms/tok (r2 0.98) | |
| pooled fit | all | 446 | | | uncached 48-24,254 | | | | | | | | | 79.6 | first block vs uncached: 5.68 s + 0.006 ms/tok (r2 0.00); vs prompt: 5.70 s + -0.002 ms/tok (r2 0.00); tail vs output tok: -1.76 s + 17.1 ms/tok (r2 0.97) | |

### 2.5 Prefill vs decode

**Prefill vs decode by regression** (agent calls only; model-call duration = a + b x uncached prompt tokens + c x output tokens, least squares; the per-group shares apply the pooled coefficients to each group's own token counts, so they add to ~100% of that group's call time)
| group | kind | calls | mean call (s) | fixed a | prefill b x uncached | decode c x output | residual |
|---|---|---:|---:|---:|---:|---:|---:|
| CODE-solo | lead | 47 | 9.5 | 39.2% | 1.8% | 61.2% | -2.2% |
| FQA-solo | lead | 11 | 21.0 | 17.7% | 1.9% | 78.4% | 2.0% |
| MATH-solo | lead | 7 | 41.5 | 9.0% | 0.2% | 88.5% | 2.3% |
| CODE-team | lead | 56 | 7.2 | 51.8% | 1.4% | 56.2% | -9.4% |
| CODE-team | teammate | 112 | 10.0 | 37.2% | 0.3% | 58.7% | 3.8% |
| FQA-team | lead | 51 | 9.7 | 38.3% | 1.3% | 61.7% | -1.3% |
| FQA-team | teammate | 64 | 8.9 | 41.8% | 1.0% | 58.0% | -0.8% |
| MATH-team | lead | 55 | 7.6 | 49.2% | 1.5% | 49.7% | -0.4% |
| MATH-team | teammate | 43 | 17.1 | 21.7% | 0.2% | 77.3% | 0.9% |

| fit over | calls | a fixed (s) | b prefill (ms per uncached tok) | c decode (ms per output tok) | r2 |
|---|---:|---:|---:|---:|---:|
| all agent calls | 446 | 3.72 | 0.033 | 17.44 | 0.96 |
| lead | 227 | 3.16 | 0.086 | 17.82 | 0.95 |
| teammate | 219 | 4.10 | -0.002 | 17.13 | 0.97 |
| responses ending in tool_use | 322 | 3.76 | -0.004 | 17.61 | 0.96 |
| responses ending in end_turn | 124 | 3.57 | 0.177 | 16.22 | 0.96 |

**Streaming delivery pattern** (why the first-block time is not prefill: the provider delivers thinking and tool_use blocks in bursts; 'first block' = client time to the first delivered content block, 'tail' = first block to end of stream; a tail under 0.1 s means the whole response arrived at once)
| kind | response ends with | calls | output tok mean | first block mean (s) | tail mean (s) | tail share of call | calls with tail < 0.1 s | first-block fit: a + c x output tok (r2) |
|---|---|---:|---:|---:|---:|---:|---:|---|
| lead | tool_use | 149 | 396 | 5.73 | 4.64 | 44.8% | 28.9% | 5.78 s + 0.23 ms/tok (0.01) |
| lead | end_turn | 78 | 308 | 5.66 | 3.75 | 39.9% | 50.0% | 5.43 s + -0.13 ms/tok (0.01) |
| teammate | tool_use | 173 | 396 | 5.60 | 5.43 | 49.2% | 59.0% | 5.37 s + 0.51 ms/tok (0.12) |
| teammate | end_turn | 46 | 451 | 5.99 | 5.24 | 46.7% | 32.6% | 6.21 s + -0.62 ms/tok (0.06) |

The first-block time is flat (5.4-6.2 s) and its fit against output tokens has r2 <= 0.12, while a
third to a half of all responses arrive in one burst after the model has finished: the provider
holds thinking and tool_use blocks back and streams only visible text. The regression on token
counts is therefore the measure of prefill and decode used in the short answer.

#### 2.5.1 Direct probe of the provider (2026-09-13)

The regression coefficient was checked afterwards against two measurements that do not depend on it
(`scripts/provider_probe.py --prefill`): a prompt-size sweep with the output length held fixed and
no tools, so the provider streams incrementally and the first-block time is a real time to first
token; and re-sending an identical prompt so the same tokens come back from the cache, where the
difference in first-block time is what those tokens cost to prefill.

| method | fixed part | per uncached token | implied prefill rate |
|---|---:|---:|---:|
| size sweep 460 -> 32,565 uncached tokens, 300-token output (r2 0.88) | 3.34 s | 0.0384 ms | 26,000 tok/s |
| identical resend, 11,584 tokens served from cache | | 0.0342 ms saved | |
| identical resend, 32,512 tokens served from cache | | 0.0416 ms saved | |
| the regression above, 446 agent calls (r2 0.96) | 3.72 s | 0.033 ms | 30,000 tok/s |
| direct probe of 2026-09-10, separate session | 4.6 s | 0.052 ms | 19,000 tok/s |

Decode measured the same way is 81 tokens/s for 300-token outputs and 39 tokens/s for a 1,200-token
output, i.e. 12-26 ms per token, bracketing the regression's 17.44 ms. Pooling the sweep's three
repetitions gives a much weaker fit (0.027 ms/token, r2 0.08) than one clean sweep does, because the
fixed component drifts by seconds between repetitions; that drift, not the token count, is what the
low r2 of the per-group first-block fits above reflects.

Three further facts the probe settled:

- **The API exposes no timing, but the HTTP response does.** `usage` carries token counts only and
  `cache_creation_input_tokens` is always null; the response headers carry `x-process-time`, the
  server's own processing seconds. On a streaming 27k-token request it reads 2.2-2.7 s fresh against
  1.0 s for the same prompt served from cache, so it tracks prefill, while the client waits
  3.3-5.5 s for the first block. Two to three seconds of the fixed component is therefore outside
  anything the server counts, which is why the intercept is a provider-and-evening property.
- **The prompt cache is strictly prefix-based** (`--cache`): an identical resend came back 99.9%
  cached, the same body behind a new prefix 0%, a unique block inserted mid-prompt 0%, and a
  document never sent before 0% on each of two sends. Content is reusable only where it sits at the
  same position in the same prefix.
- **SDK retries can look like cache hits.** One nominally fresh call in an early sweep returned
  99.8% cached with an elevated first-block time; the client retries twice by default and the retry
  hits the entry its failed attempt created. The probe script disables retries for this reason.

### 2.6 The harness's own model calls

**Auxiliary model calls made by the harness itself** (context maintenance: memory recall inside update_context before a lead call, memory extraction after a lead turn, summary compaction; pooled by group)
| group | purpose | calls | calls / run | calls / lead round | mean duration | median | prompt tok mean | output tok mean | stop=max_tokens share | first block mean | tail mean | total per run (s) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo | memory_extract | 3 | 1.0 | 0.06 | 10.1 | 10.2 | 303 | 416 | 0.0% | 3.3 | 6.8 | 10.1 |
| FQA-solo | memory_extract | 3 | 1.0 | 0.27 | 22.2 | 22.3 | 2,119 | 1,000 | 100.0% | 2.9 | 19.3 | 22.2 |
| MATH-solo | memory_extract | 3 | 1.0 | 0.43 | 19.1 | 16.0 | 1,756 | 761 | 0.0% | 3.3 | 15.8 | 19.1 |
| CODE-team | memory_extract | 27 | 5.4 | 0.48 | 18.1 | 20.5 | 774 | 770 | 55.6% | 3.6 | 14.5 | 97.6 |
| CODE-team | memory_recall | 12 | 2.4 | 0.21 | 4.9 | 4.9 | 332 | 190 | 75.0% | 3.2 | 1.7 | 11.7 |
| FQA-team | memory_extract | 19 | 3.8 | 0.37 | 20.6 | 21.1 | 1,458 | 899 | 73.7% | 3.4 | 17.2 | 78.2 |
| MATH-team | memory_extract | 23 | 4.6 | 0.42 | 13.1 | 11.5 | 1,443 | 585 | 34.8% | 3.2 | 9.9 | 60.3 |

`memory_extract` runs after every lead turn (`remember_after_turn`), including the short turns
triggered by team events; 35-74% of these calls in team mode and 100% in solo file Q&A hit the
1,000-token `max_tokens` of the memory runtime, i.e. the model was still thinking when cut off.
`memory_recall` runs inside `update_context` before a lead call once memory records exist; it fired
in two of the five coding team runs (after an earlier turn had extracted records) and 75% of those
calls hit their 200-token cap.

### 2.7 Teammate input redundancy

**Teammate input redundancy** (a) re-sent = prompt tokens already sent in the same agent's previous call (append-only history), (b) provider cache-read share, (c) cross-teammate file bytes another teammate had fetched first (exact (file,line) provenance) and their share of teammate prompt tokens
| run | teammates | teammate calls | teammate prompt tok | re-sent share (a) | cache-read share (b) | file bytes fetched | cross-teammate redundancy, range (c) | same, whole-result SHA | resident copies | cross-redundant share of prompt tok | file content share of prompt tok | lead: prompt tok | lead re-sent share | lead cache-read |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo-r1 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 76,596 | 90.3% | 33.1% |
| CODE-solo-r2 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 90,509 | 89.0% | 25.7% |
| CODE-solo-r3 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 168,279 | 92.3% | 23.8% |
| FQA-solo-r1 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 50,249 | 49.2% | 12.6% |
| FQA-solo-r2 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 27,076 | 10.4% | 7.8% |
| FQA-solo-r3 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 74,484 | 66.1% | 14.2% |
| MATH-solo-r1 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 7,964 | 34.4% | 26.5% |
| MATH-solo-r2 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 14,234 | 58.8% | 44.5% |
| MATH-solo-r3 | 0 | 0 | 0 | - | - | 0 | - | - | - | - | - | 9,531 | 28.8% | 44.3% |
| CODE-team-r1 | 3 | 24 | 90,618 | 83.4% | 78.9% | 14,359 | 0.0% | 0.0% | 1.00x | 0.0% | 24.6% | 46,844 | 87.3% | 31.6% |
| CODE-team-r2 | 3 | 27 | 104,279 | 85.6% | 78.5% | 13,871 | 0.0% | 0.0% | 1.00x | 0.0% | 24.7% | 50,554 | 88.4% | 41.8% |
| CODE-team-r3 | 3 | 23 | 74,775 | 81.1% | 73.8% | 13,848 | 0.0% | 0.0% | 1.00x | 0.0% | 30.3% | 53,038 | 88.2% | 27.9% |
| CODE-team-r4 | 3 | 18 | 65,743 | 76.5% | 68.4% | 10,802 | 0.0% | 0.0% | 1.00x | 0.0% | 21.3% | 47,660 | 87.3% | 35.5% |
| CODE-team-r5 | 3 | 20 | 80,869 | 80.4% | 76.0% | 13,848 | 0.0% | 0.0% | 1.00x | 0.0% | 25.1% | 70,500 | 90.8% | 38.9% |
| FQA-team-r1 | 3 | 11 | 72,432 | 52.9% | 52.6% | 112,843 | 22.8% | 22.8% | 1.30x | 18.5% | 81.3% | 53,267 | 84.4% | 19.8% |
| FQA-team-r2 | 3 | 13 | 76,130 | 58.4% | 58.2% | 112,843 | 22.8% | 22.8% | 1.30x | 26.8% | 86.3% | 60,136 | 87.7% | 31.6% |
| FQA-team-r3 | 3 | 14 | 78,579 | 56.2% | 55.9% | 112,843 | 22.8% | 22.8% | 1.30x | 25.0% | 76.1% | 65,997 | 87.2% | 38.4% |
| FQA-team-r4 | 3 | 16 | 73,394 | 57.6% | 54.8% | 103,310 | 15.6% | 12.5% | 1.18x | 11.8% | 77.8% | 50,956 | 84.8% | 37.3% |
| FQA-team-r5 | 3 | 10 | 43,356 | 32.9% | 27.5% | 103,492 | 12.4% | 12.4% | 1.15x | 8.1% | 83.4% | 52,545 | 84.1% | 32.2% |
| MATH-team-r1 | 3 | 8 | 15,589 | 49.2% | 48.4% | 0 | - | - | - | 0.0% | 0.0% | 58,361 | 89.1% | 29.0% |
| MATH-team-r2 | 3 | 8 | 15,298 | 49.3% | 48.1% | 0 | - | - | - | 0.0% | 0.0% | 58,149 | 87.0% | 36.3% |
| MATH-team-r3 | 3 | 10 | 24,021 | 62.6% | 50.4% | 0 | - | - | - | 0.0% | 0.0% | 72,727 | 88.5% | 34.8% |
| MATH-team-r4 | 3 | 8 | 13,788 | 44.9% | 44.1% | 0 | - | - | - | 0.0% | 0.0% | 48,731 | 84.6% | 30.3% |
| MATH-team-r5 | 3 | 9 | 13,891 | 53.4% | 52.1% | 0 | - | - | - | 0.0% | 0.0% | 51,718 | 86.3% | 28.6% |

(a) and (b) agree within 0-6 points for teammates: the provider's prefix cache serves the re-sent
history. For the lead they diverge (84-91% re-sent, 20-42% cache reads) because the per-second
timestamp in the system prompt and the growing memory catalog break the cached prefix (see
`file_read_reuse_profile.md`). Solo leads re-send 10-92% depending on how many rounds the run took.

### 2.8 Agent runs per task and where the wall time goes

**Agent runs per task** (mean over runs of a group unless noted; a teammate task = rounds from assignment to its final reply; wall = driver wall time)
| group | runs | wall mean (s) | lead turns / run | lead rounds / run | lead rounds / turn | teammates / run | teammate rounds / task: mean | median | max | teammate task active time (s): mean | median | teammate tasks / run | tool calls / round (lead) | tool calls / round (teammate) | model calls / run (all) | denied tool calls / run |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo | 3 | 161 | 1.0 | 15.7 | 15.7 | 0.0 | - | - | 0 | - | - | 0.0 | 1.09 | 0.00 | 16.7 | 0.0 |
| FQA-solo | 3 | 101 | 1.0 | 3.7 | 3.7 | 0.0 | - | - | 0 | - | - | 0.0 | 1.73 | 0.00 | 4.7 | 0.0 |
| MATH-solo | 3 | 118 | 1.0 | 2.3 | 2.3 | 0.0 | - | - | 0 | - | - | 0.0 | 0.86 | 0.00 | 3.3 | 1.3 |
| CODE-team | 5 | 215 | 5.4 | 11.2 | 2.1 | 3.0 | 7.5 | 6 | 15 | 75 | 70 | 3.0 | 1.20 | 1.04 | 41.4 | 1.0 |
| FQA-team | 5 | 180 | 3.8 | 10.2 | 2.7 | 3.0 | 4.0 | 4 | 6 | 36 | 35 | 3.2 | 1.37 | 1.12 | 26.8 | 0.8 |
| MATH-team | 5 | 165 | 4.6 | 11.0 | 2.4 | 3.0 | 2.9 | 3 | 5 | 49 | 33 | 3.0 | 1.24 | 0.79 | 24.2 | 2.2 |

**Where the wall time of a run goes** (means over the runs of a group; lead active = lead turns incl. turn-end memory work; teammate active = union of teammate round windows; overlap = lead and teammates busy at the same time; idle tail = nobody busy: waits for team events, quiescence timer, shutdown handshake)
| group | runs | wall (s) | lead active (s) | share | of which lead agent calls (s) | of which turn-end memory (s) | teammate active (s) | share | overlap (s) | idle / tail (s) | share |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo | 3 | 161 | 159 | 98.7% | 148 | 10 | 0 | 0.0% | 0 | 2 | 1.3% |
| FQA-solo | 3 | 101 | 99 | 98.0% | 77 | 22 | 0 | 0.0% | 0 | 2 | 2.0% |
| MATH-solo | 3 | 118 | 116 | 98.3% | 97 | 19 | 0 | 0.0% | 0 | 2 | 1.7% |
| CODE-team | 5 | 215 | 190 | 88.2% | 80 | 103 | 111 | 51.7% | 89 | 3 | 1.3% |
| FQA-team | 5 | 180 | 177 | 98.3% | 99 | 78 | 48 | 26.4% | 47 | 3 | 1.4% |
| MATH-team | 5 | 165 | 144 | 87.0% | 83 | 60 | 83 | 50.2% | 64 | 3 | 1.8% |

Lead turns by trigger (team runs): user-triggered turns average 4.5 rounds and 23.0 s of turn-end
memory work; team-event turns average 1.7 rounds and 16.0 s of turn-end memory work. Solo runs are
one user turn of 7.2 rounds on average plus 17.1 s of turn-end work.

### 2.9 Tool execution

**Tool execution time by tool** (tool_start -> tool_end spans, pooled over all runs)
| tool | calls | denied | error | mean (ms) | median (ms) | p90 (ms) | max (ms) | total (s) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| bash | 81 | 23 | 9 | 79 | 66 | 187 | 220 | 6.4 |
| read_file | 78 | 0 | 0 | 13 | 9 | 24 | 47 | 1.0 |
| spawn_teammate | 45 | 0 | 0 | 21 | 24 | 40 | 61 | 0.9 |
| request_shutdown | 45 | 0 | 0 | 5 | 3 | 11 | 15 | 0.2 |
| glob | 21 | 0 | 0 | 9 | 10 | 12 | 19 | 0.2 |
| complete_task | 33 | 0 | 0 | 5 | 5 | 5 | 5 | 0.2 |
| write_file | 24 | 0 | 0 | 7 | 5 | 6 | 30 | 0.2 |
| create_task | 45 | 0 | 0 | 2 | 2 | 3 | 5 | 0.1 |
| todo_write | 82 | 0 | 0 | 1 | 1 | 1 | 2 | 0.1 |
| edit_file | 16 | 1 | 1 | 4 | 4 | 5 | 5 | 0.1 |
| claim_task | 22 | 0 | 0 | 2 | 2 | 2 | 2 | 0.0 |
| send_message | 6 | 0 | 0 | 4 | 3 | 7 | 8 | 0.0 |
| list_tasks | 5 | 0 | 0 | 3 | 3 | 3 | 3 | 0.0 |

`denied` calls are permission refusals (see section 4), `error` calls are non-zero exit codes
(failing unit tests while iterating on a solution).

## 3. Reading the numbers

1. **Round count, not context size, is the latency driver at these prompt sizes.** With
   `3.7 s + 0.033 ms/uncached token + 17.4 ms/output token`, re-prefilling a 5k-token history costs
   about 0.17 s per call, under 2% of the call; the fixed component (network, queueing, the
   provider's scheduling and first-token latency) and the decoded tokens are the rest. A KV-cache or
   prefix-reuse mechanism can therefore not shorten these rounds measurably; its value at this scale
   is capacity (fewer resident copies) rather than latency. Three independent measurements bracket
   the slope at 0.033-0.042 ms/token (section 2.5.1, and the 0.052 ms/token probe of last week), and
   the provider already serves 61-91% of teammate prompt tokens from its cache. Prefill turns into
   seconds only above roughly 50k tokens per call, which is where this argument would have to be
   re-made.
2. **The harness's own model calls are the largest non-agent cost.** Turn-end memory extraction
   took 1,364 s over 24 runs, 35% of the total wall time and 8-58% of the lead's busy time per group,
   and it is serial with the lead's next turn because the turn holds the agent lock. In team runs
   every team event (result, idle, shutdown response) wakes a turn and therefore an extraction call;
   3.8-5.4 turns per run cost 60-103 s. Memory recall adds 4.9 s to a lead round whenever records
   exist. Running extraction once per user request (not per automatic turn), or asynchronously, or
   with a smaller thinking budget, would remove 28-48% of team-run wall time without touching the
   agent loop.
3. **Context preparation proper is free.** Compaction pipeline, system prompt, tool pool and inbox
   together are about 10 ms per round in every category; even the byte-level redundancy work of
   earlier weeks changes nothing here because the lead never reached its budget in these runs. That
   changes only when the budget is hit (summary compaction is a model call; last week's 50k-char
   runs paid it) or when memory records exist (recall calls).
4. **Tool execution is negligible for these task types.** Running a unit-test file costs 50-220 ms,
   reading a 45 KB document 13 ms. The 0.1-0.5% share would grow only with tools that build,
   install, or call remote services.
5. **Decode is the category signature.** Math teammates decode 760 tokens per call (56% thinking)
   and spend 77% of their call time decoding; coding teammates decode 337 tokens with medians of 68
   (a tool call) and spend 59%; file-Q&A teammates 296 tokens and 58%. The lead's rounds are short
   tool calls (medians 143-189 tokens) so 38-52% of its call time is the fixed component.
6. **Teams cost coordination rounds.** For three small independent tasks the lead spends 10-11
   rounds and 4-5 turns on orchestration, and the turn-end memory work makes the lead the busiest
   agent (87-98% of wall). The solo lead finished the same tasks in fewer wall seconds with the same
   quality; the parallel gain of teammates (up to 111 s of overlapped work in coding) did not cover
   the orchestration and memory overhead.
7. **Teammate redundancy is mostly self-redundancy.** Half to four fifths of every teammate call is
   its own previous prompt, already served from the provider cache; cross-teammate duplication
   matters only when tasks share files (file Q&A: 8-27% of teammate prompt tokens) and at the
   measured prefill slope it is worth milliseconds of latency, not seconds.

Consistency check with last week's eight non-streaming runs (PF/DC workloads at the same context
limit, same analyzer): lead rounds 7.9-14.7 s, teammate rounds 12.3-71.1 s (1,200-word documents),
preparation 0.01 s except DC-S (1.85 s from memory recall, 0.40 calls per round), tools
0.01-0.02 s, turn-end memory 20.5-22.3 s per turn. Same shape.

## 4. Threats to validity

- **Streaming.** All runs used streaming requests so the first-block time could be recorded. A
  direct A/B on identical 400-token requests (6 pairs, `glm-5.3-flash`, same prompt) gave 7.52 s mean
  (median 7.14) streaming against 8.27 s (median 7.48) non-streaming, so streaming did not inflate
  the model-call times. The provider's burst delivery of thinking/tool_use blocks means the
  first-block time is a delivery artefact, which is why prefill and decode are estimated by
  regression (r2 0.95-0.97) instead.
- **The fixed component is provider-side and variable.** A probe of the same request with no tools,
  5 tools and the harness's 26 tool definitions, at `max_tokens` 400 and 8,000, gave first-block
  times between 1.5 and 6.9 s with no consistent effect of the tool definitions (means 2.7-2.8 s in
  three arms, 5.0-6.1 s in two); the harness calls sat at 5.6-6.1 s. The 3.2-4.1 s fixed intercept
  therefore reflects z.ai's scheduling and reasoning start-up on this evening (US Eastern), not the
  harness; it would differ on another provider or at another time. The server's own
  `x-process-time` accounts for only part of it (2.2-2.7 s of a 3.3-5.5 s wait on a 27k-token
  request), so between 2 and 3 s is queueing or transport that neither side attributes.
- **Permission denials cost about one extra round per run.** 24 tool calls were denied across the
  24 runs: 15 `python3 -c` arithmetic checks whose quoted code contained a `>` comparison (a false
  positive of the driver's read-only filter, which treated it as a redirect; fixed in
  `profile_run.py` after the runs), 8 compound display commands that the harness itself denies for
  asynchronous teammates (double-quoted `grep` alternations are split on `|`), and one `edit_file`
  outside the sandbox. The agents recovered by reasoning without the tool; the round shares are not
  affected but the math teammates' 2.9 rounds per task include some of these retries.
- **Scoring is heuristic.** Coding is scored by re-running the test files on the archived
  sandboxes (exact); math by the `Final answer` line in the teammate result or the lead's reply
  (the trace keeps at most 2,048 characters of a message, which cut two long solutions); file Q&A
  by keyword coverage of the expected facts, not by a reader.
- **Small tasks, one provider, one model.** Runs lasted 2-4 minutes, the lead never compacted, and
  the workloads have three tasks each; a long coding session that hits the context budget would add
  compaction calls to preparation and make prefill matter more. Five and three repetitions per group
  give stable means (per-run tables in Appendix A) but not tight tails.
- **Round reconstruction.** Buckets are exact spans from the trace and sum to the gross round time;
  the teammate idle wait between tasks is excluded from rounds and the lead's turn-end work is
  reported separately as `post`. Timing events are emitted from the driver's wrappers and add
  under 1 ms per round.

## Appendix A. Per-run round breakdown

**Per-run round breakdown** (lead and teammate rounds separately; seconds)
| run | kind | rounds | gross mean | prep mean | model mean | first block mean | tail mean | tools mean | other mean | prep share | model share | tools share | prompt tok mean | output tok mean |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CODE-solo-r1 | lead | 14 | 6.7 | 0.01 | 6.7 | 4.9 | 1.8 | 0.04 | 0.002 | 0.1% | 99.3% | 0.7% | 5,471 | 210 |
| CODE-solo-r2 | lead | 13 | 11.7 | 0.01 | 11.7 | 5.0 | 6.7 | 0.05 | 0.002 | 0.1% | 99.5% | 0.4% | 6,962 | 437 |
| CODE-solo-r3 | lead | 20 | 10.0 | 0.01 | 10.0 | 5.5 | 4.5 | 0.05 | 0.002 | 0.1% | 99.5% | 0.4% | 8,414 | 350 |
| FQA-solo-r1 | lead | 4 | 26.9 | 0.01 | 26.8 | 6.5 | 20.4 | 0.03 | 0.008 | 0.0% | 99.8% | 0.1% | 12,562 | 1,255 |
| FQA-solo-r2 | lead | 2 | 32.3 | 0.00 | 32.2 | 5.5 | 26.8 | 0.02 | 0.009 | 0.0% | 99.9% | 0.1% | 13,538 | 1,421 |
| FQA-solo-r3 | lead | 5 | 11.9 | 0.01 | 11.8 | 5.9 | 5.9 | 0.02 | 0.007 | 0.1% | 99.7% | 0.2% | 14,897 | 503 |
| MATH-solo-r1 | lead | 2 | 43.5 | 0.00 | 43.5 | 5.0 | 38.6 | 0.00 | 0.001 | 0.0% | 100.0% | 0.0% | 3,982 | 2,422 |
| MATH-solo-r2 | lead | 3 | 33.6 | 0.01 | 33.5 | 6.3 | 27.3 | 0.05 | 0.002 | 0.0% | 99.8% | 0.2% | 4,745 | 1,484 |
| MATH-solo-r3 | lead | 2 | 51.4 | 0.00 | 51.4 | 5.5 | 45.9 | 0.00 | 0.001 | 0.0% | 100.0% | 0.0% | 4,766 | 2,725 |
| CODE-team-r1 | lead | 10 | 8.6 | 0.49 | 8.1 | 6.2 | 1.9 | 0.01 | 0.001 | 5.7% | 94.2% | 0.1% | 4,684 | 248 |
| CODE-team-r1 | teammate | 24 | 9.5 | 0.00 | 9.5 | 5.4 | 4.0 | 0.03 | 0.002 | 0.0% | 99.6% | 0.3% | 3,776 | 324 |
| CODE-team-r2 | lead | 11 | 7.7 | 0.01 | 7.7 | 6.2 | 1.4 | 0.01 | 0.001 | 0.1% | 99.8% | 0.1% | 4,596 | 223 |
| CODE-team-r2 | teammate | 27 | 8.3 | 0.00 | 8.2 | 5.7 | 2.6 | 0.03 | 0.002 | 0.0% | 99.5% | 0.4% | 3,862 | 237 |
| CODE-team-r3 | lead | 11 | 9.4 | 2.29 | 7.1 | 5.5 | 1.7 | 0.01 | 0.001 | 24.3% | 75.6% | 0.1% | 4,822 | 246 |
| CODE-team-r3 | teammate | 23 | 9.7 | 0.00 | 9.6 | 5.9 | 3.7 | 0.04 | 0.001 | 0.0% | 99.5% | 0.4% | 3,251 | 280 |
| CODE-team-r4 | lead | 10 | 7.9 | 0.01 | 7.9 | 5.9 | 2.1 | 0.01 | 0.001 | 0.1% | 99.8% | 0.1% | 4,766 | 264 |
| CODE-team-r4 | teammate | 18 | 13.2 | 0.00 | 13.2 | 5.5 | 7.7 | 0.03 | 0.001 | 0.0% | 99.7% | 0.3% | 3,652 | 512 |
| CODE-team-r5 | lead | 14 | 5.6 | 0.01 | 5.6 | 4.4 | 1.2 | 0.01 | 0.001 | 0.1% | 99.7% | 0.2% | 5,036 | 192 |
| CODE-team-r5 | teammate | 20 | 10.6 | 0.00 | 10.6 | 5.5 | 5.1 | 0.04 | 0.001 | 0.0% | 99.6% | 0.4% | 4,043 | 394 |
| FQA-team-r1 | lead | 9 | 11.1 | 0.01 | 11.1 | 6.9 | 4.2 | 0.01 | 0.002 | 0.1% | 99.8% | 0.1% | 5,919 | 425 |
| FQA-team-r1 | teammate | 11 | 9.9 | 0.00 | 9.9 | 5.8 | 4.1 | 0.01 | 0.004 | 0.0% | 99.8% | 0.1% | 6,585 | 377 |
| FQA-team-r2 | lead | 11 | 8.0 | 0.01 | 7.9 | 5.6 | 2.3 | 0.01 | 0.002 | 0.1% | 99.8% | 0.1% | 5,467 | 259 |
| FQA-team-r2 | teammate | 13 | 7.9 | 0.00 | 7.8 | 5.5 | 2.3 | 0.02 | 0.003 | 0.1% | 99.7% | 0.2% | 5,856 | 224 |
| FQA-team-r3 | lead | 12 | 9.5 | 0.01 | 9.5 | 6.3 | 3.2 | 0.01 | 0.001 | 0.1% | 99.8% | 0.1% | 5,500 | 307 |
| FQA-team-r3 | teammate | 14 | 9.5 | 0.00 | 9.5 | 5.9 | 3.6 | 0.01 | 0.003 | 0.0% | 99.8% | 0.2% | 5,613 | 323 |
| FQA-team-r4 | lead | 10 | 9.5 | 0.01 | 9.5 | 5.8 | 3.7 | 0.01 | 0.001 | 0.1% | 99.8% | 0.1% | 5,096 | 331 |
| FQA-team-r4 | teammate | 16 | 8.5 | 0.00 | 8.5 | 5.3 | 3.3 | 0.01 | 0.003 | 0.1% | 99.8% | 0.1% | 4,587 | 290 |
| FQA-team-r5 | lead | 9 | 11.0 | 0.01 | 10.9 | 5.8 | 5.2 | 0.01 | 0.002 | 0.1% | 99.8% | 0.1% | 5,838 | 427 |
| FQA-team-r5 | teammate | 10 | 8.9 | 0.00 | 8.9 | 5.4 | 3.5 | 0.02 | 0.004 | 0.0% | 99.7% | 0.2% | 4,336 | 269 |
| MATH-team-r1 | lead | 12 | 7.1 | 0.01 | 7.1 | 6.0 | 1.1 | 0.01 | 0.001 | 0.1% | 99.8% | 0.1% | 4,863 | 172 |
| MATH-team-r1 | teammate | 8 | 18.9 | 0.00 | 18.9 | 6.7 | 12.2 | 0.02 | 0.001 | 0.0% | 99.9% | 0.1% | 1,949 | 749 |
| MATH-team-r2 | lead | 11 | 7.8 | 0.01 | 7.8 | 6.0 | 1.8 | 0.01 | 0.001 | 0.1% | 99.8% | 0.1% | 5,286 | 234 |
| MATH-team-r2 | teammate | 8 | 18.7 | 0.00 | 18.7 | 6.1 | 12.5 | 0.01 | 0.001 | 0.0% | 99.9% | 0.1% | 1,912 | 837 |
| MATH-team-r3 | lead | 13 | 7.1 | 0.01 | 7.1 | 5.8 | 1.3 | 0.01 | 0.001 | 0.1% | 99.8% | 0.1% | 5,594 | 193 |
| MATH-team-r3 | teammate | 10 | 15.7 | 0.00 | 15.7 | 5.9 | 9.8 | 0.01 | 0.001 | 0.0% | 99.9% | 0.1% | 2,402 | 744 |
| MATH-team-r4 | lead | 9 | 7.7 | 0.01 | 7.6 | 5.2 | 2.5 | 0.01 | 0.001 | 0.1% | 99.8% | 0.1% | 5,415 | 268 |
| MATH-team-r4 | teammate | 8 | 17.4 | 0.00 | 17.3 | 5.6 | 11.8 | 0.02 | 0.001 | 0.0% | 99.9% | 0.1% | 1,724 | 866 |
| MATH-team-r5 | lead | 10 | 8.4 | 0.01 | 8.4 | 6.5 | 1.9 | 0.01 | 0.001 | 0.1% | 99.8% | 0.1% | 5,172 | 228 |
| MATH-team-r5 | teammate | 9 | 15.6 | 0.00 | 15.6 | 6.2 | 9.4 | 0.00 | 0.001 | 0.0% | 100.0% | 0.0% | 1,543 | 623 |

## Appendix B. Reproduction

```bash
# lane copy (runs must not write into the repository); re-sync after every driver edit
rsync -a --delete --exclude .git --exclude 's15_integrated_harness/traces' --exclude profiling_sandbox \
      --exclude __pycache__ --exclude '.memory' --exclude '.tasks*' --exclude .mailboxes --exclude .transcripts \
      --exclude .task_outputs --exclude .worktrees /home/yq335/learn-claude-code/ /home/yq335/lanes/latency/
# one repetition of the three team workloads (about 10 minutes), then the solo arm
python3 s15_integrated_harness/scripts/latency_workloads.py --rep r6 --mode team --repo /home/yq335/lanes/latency
python3 s15_integrated_harness/scripts/latency_workloads.py --rep r4 --mode solo --repo /home/yq335/lanes/latency
# tables (add --json OUT for the per-round data behind them)
python3 s15_integrated_harness/scripts/latency_breakdown.py s15_integrated_harness/traces/latency_profiling --per-run --tools
# the provider probes behind section 2.5.1 (about 25 calls; --headers alone is one call)
python3 s15_integrated_harness/scripts/provider_probe.py --all --json /tmp/provider_probe.json
```

Reference solutions for the three coding problems are in
`traces/latency_profiling/reference_solutions/`; each run's sandbox is archived as
`traces/latency_profiling/<label>.sandbox/`, its score as `<label>.score.json`, its console output as
`<label>.console.log`.
