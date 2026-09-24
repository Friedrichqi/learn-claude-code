# 05 · End-to-end latency breakdown (GLM, then Qwen on vLLM)

> **Status:** complete and published: two 24-run matrices of the same workloads (FQA / CODE / MATH × team r1–r5 and solo r1–r3), one per provider, at identical quality.
>
> **Dates:** GLM runs 2026-09-12 evening US Eastern (traces 2026-09-13 01:37–02:52 UTC), provider probes 2026-09-13, weekly digest 2026-09-16; Qwen rerun 2026-09-21 21:35 – 2026-09-22 00:36 EDT (Slurm job 95260), writeup dated 2026-09-23.
>
> **Models / providers:** `glm-5.3-flash` via z.ai (Anthropic-compatible Messages API, streaming); `Qwen/Qwen3.8-27B` (bf16) served by vLLM 0.29.0 on one RTX PRO 6000 Blackwell (96 GB) through its loopback `/v1/messages` endpoint.
>
> **Commits:** GLM runs at harness `64d1b11`; GLM data, Part A and the digest committed in `bdc4788` (2026-09-13), both revised in `e0e522b` (2026-09-15); Qwen runs at harness `aded0d0`; Qwen data, Part A Appendix C and Part B committed in `0cbddf8` (2026-09-22).
>
> **Presented:** deck 091626 slides 5, 6 and 9 (the digest is also cited on slides 3 and 4); deck 092326 slides 11 and 12 (Part B is also cited on slides 2 and 14).

**Contents**

- Part A — the GLM run (`glm-5.3-flash`, z.ai): Short answer · §1 Method · §2 Results (§2.1–§2.9, including §2.5.1, the direct provider probe) · §3 Reading the numbers · §4 Threats to validity · Appendix A per-run breakdown · Appendix B reproduction · Appendix C the Qwen3.8-27B / vLLM rerun (setup and reproduction of Part B)
  - A.W Additions from the 2026-09-16 weekly digest: totals over the 24 runs; why client-side streaming timing is not a prefill measure; the prefill share by prompt size; the digest's §5 (why context preparation is cheap although teammate inputs are highly redundant, and the 50k- against the 512k-character limit); four of its conclusions; two analyzer fixes
- Part B — the rerun on Qwen3.8-27B through local vLLM, compared with the GLM run: §1 Question · §2 Setup · §3 Results A–G · §4 Conclusions · §5 Caveats
- Data inventory · Source map · Cleanup notes

**Files in this folder** (`research/05_latency_breakdown/`)

| path | purpose |
|---|---|
| `README.md` | this document |
| `latency_workloads.py` | runs the FQA / CODE / MATH workloads (team or solo) through `research/common/profile_run.py`, scores each run into `<label>.score.json`; calls the model |
| `latency_breakdown.py` | round reconstruction and every table (`latency_tables.md` / `.json`); offline |
| `latency_compare.py` | side-by-side tables T0–T10 of two run sets (`compare_glm_vs_qwen.md` / `.json`); offline |
| `provider_probe.py` | direct provider probes: prefill sweep, cached resends, decode rate, cache semantics, headers; calls the provider |
| `smoke_check.py` | gate on one FQA smoke run before the vLLM matrix; offline |
| `qwen_vllm_pipeline.sh` | one-job pipeline env → download → serve → lane → probe → smoke → matrix → tables → compare → stop; `OPT=1` runs the optimized arm of `research/09_efficiency/` |
| `qwen_vllm.sbatch` | Slurm wrapper of the pipeline (job 95260) |
| `data/latency_profiling/` | the GLM run set (Part A) |
| `data/latency_profiling_qwen/` | the Qwen / vLLM run set (Part A Appendix C, Part B) |
| `research/common/fixtures/latency_bench/coding/` (outside) | the three coding-bench problems (`expr`, `intervals`, `ttl_cache`): README specification and unittest file each |
| `research/common/profile_run.py` (outside) | the profiling driver (`--stream`, `--write-root` / `--sandbox-from`, `--allow-python`, `--vllm-metrics`, `--client-max-retries`, `--server-info`) |

---

# Part A — End-to-end latency breakdown of the s15 agentic loop, by task category

_Formerly `s15_integrated_harness/latency_profile.md` (runs 2026-09-12/13; revised 2026-09-15; Appendix C added 2026-09-22)._

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
`research/05_latency_breakdown/data/latency_profiling/`.

## Short answer

Twenty-four runs completed (five team runs and three solo runs per category); every coding problem
passed its tests (24/24), every math answer was correct (24/24) and the file-Q&A answers covered
97-100% of the expected facts. All numbers below come from `research/05_latency_breakdown/latency_breakdown.py` over
`research/05_latency_breakdown/data/latency_profiling/` (full tables in `research/05_latency_breakdown/data/latency_profiling/latency_tables.md`).

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
`research/common/input_redundancy.py`), and the share of teammate prompt tokens those bytes occupy.

### 1.2 Instrumentation

- `research/common/profile_run.py` (extended): `--stream` sends every request through
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
- `research/05_latency_breakdown/latency_workloads.py`: workload prompts, sequential runner, scoring.
- `research/05_latency_breakdown/latency_breakdown.py`: round reconstruction and all tables in this report
  (`--per-run`, `--tools`, `--json`).
- `research/05_latency_breakdown/provider_probe.py`: direct probes of the provider used in section 2.5.1 (prefill sweep,
  cached resends, decode rate, prefix-cache semantics, response headers).

### 1.3 Workloads

Each category is a set of three tasks. In **team** mode the lead is told to create three task-board
items, spawn one teammate per item without asking for confirmation, synthesise the three results and
shut the team down. In **solo** mode the lead is told to do the three items itself without
delegating.

| id | category | the three tasks | expected shape |
|---|---|---|---|
| FQA | file question answering | Q1 compaction layers (ARCHITECTURE.md section 6 + GLOSSARY.md); Q2 how teammates obtain work, result vs idle events, plan gate (s13 README + GLOSSARY.md); Q3 task states, blockedBy check, who adds dependencies (s10 README + GLOSSARY.md); answers <= 15 lines with citations | prefill-heavy reads (45 KB / 20 KB / 9 KB private docs + shared 13 KB glossary), short answers |
| CODE | coding bench | implement `intervals` (merge / free gaps / max overlap), `ttl_cache` (LRU + per-entry TTL, injected clock), `expr` (tokenizer + precedence parser with functions) against unittest files in `research/common/fixtures/latency_bench/coding/`; write `solution.py` in `profiling_sandbox/coding/<p>/`, run `python3 .../test_<p>.py`, iterate until green | read spec, write code, run tests, fix: mixed tool use and decode |
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
(`research/05_latency_breakdown/provider_probe.py --prefill`): a prompt-size sweep with the output length held fixed and
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
`research/02_input_redundancy/README.md` Part A). Solo leads re-send 10-92% depending on how many rounds the run took.

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
python3 research/05_latency_breakdown/latency_workloads.py --rep r6 --mode team --repo /home/yq335/lanes/latency
python3 research/05_latency_breakdown/latency_workloads.py --rep r4 --mode solo --repo /home/yq335/lanes/latency
# tables (add --json OUT for the per-round data behind them)
python3 research/05_latency_breakdown/latency_breakdown.py research/05_latency_breakdown/data/latency_profiling --per-run --tools
# the provider probes behind section 2.5.1 (about 25 calls; --headers alone is one call)
python3 research/05_latency_breakdown/provider_probe.py --all --json /tmp/provider_probe.json
```

Reference solutions for the three coding problems are in
`research/05_latency_breakdown/data/latency_profiling/reference_solutions/`; each run's sandbox is archived as
`research/05_latency_breakdown/data/latency_profiling/<label>.sandbox/`, its score as `<label>.score.json`, its console output as
`<label>.console.log`. [cleanup note: no GLM `*.console.log` in research/05_latency_breakdown/data/latency_profiling/; `*.log` is git-ignored and these logs are not on this host]

## Appendix C. Rerun on Qwen3.8-27B through a local vLLM server (2026-09-21)

The same experiment -- same workloads and prompts (`research/05_latency_breakdown/latency_workloads.py`), same repetitions (five team
and three solo runs per category), same `CONTEXT_LIMIT` (512,000 chars), streaming requests, fresh state per run,
lane copy -- was repeated with `Qwen/Qwen3.8-27B` (bf16) served by vLLM 0.29.0 on one RTX PRO 6000 Blackwell
(96 GB) through vLLM's Anthropic-compatible `/v1/messages` endpoint on the loopback interface, so the harness code
(`code.py`) is unchanged and only the lane's `.env` differs (`ANTHROPIC_BASE_URL=http://127.0.0.1:<port>`,
`MODEL_ID=Qwen/Qwen3.8-27B`, `ANTHROPIC_API_KEY=EMPTY`). Everything runs from one Slurm job:

```bash
# submit (priority QoS counts wall time double, so 12 h is the largest limit it accepts)
sbatch --export=NONE research/05_latency_breakdown/qwen_vllm.sbatch
# ... or run the steps by hand inside any allocation that owns a GPU:
bash research/05_latency_breakdown/qwen_vllm_pipeline.sh          # download serve lane probe smoke matrix tables compare stop
touch research/05_latency_breakdown/data/latency_profiling_qwen/pipeline/READY   # releases the lane copy once the code is final
```

The server (`research/05_latency_breakdown/qwen_vllm_pipeline.sh`, `start_vllm`):

```bash
vllm serve Qwen/Qwen3.8-27B --host 127.0.0.1 --port $PORT --served-model-name Qwen/Qwen3.8-27B \
  --language-model-only --reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser qwen3_coder \
  --enable-prompt-tokens-details --enable-prefix-caching \
  --max-model-len 262144 --gpu-memory-utilization 0.90 --max-num-seqs 32 --max-num-batched-tokens 16384 \
  --uvicorn-log-level warning
```

`--enable-prompt-tokens-details` is what makes vLLM fill `usage.cache_read_input_tokens` and
`usage.cache_creation_input_tokens`; without it every call looks uncached. vLLM's usage follows Anthropic's
convention (`input_tokens = prompt - cache_read - cache_creation`), but unlike z.ai it also reports the computed
tokens it wrote into its prefix cache as `cache_creation_input_tokens`, so `research/05_latency_breakdown/latency_breakdown.py` now
defines *uncached* (computed) tokens as `input_tokens + cache_creation_input_tokens` (a no-op on the GLM traces,
where the field is always null). Qwen3.8-27B is a hybrid model (48 Gated-DeltaNet layers, 16 full-attention
layers): vLLM aligns its prefix-cache blocks to the recurrent state pages, so a cache block is 784 tokens rather
than 16, which lowers the usage-level cache-read share for short prefixes; the server-side hit counters are
reported next to it. Sampling is the model's `generation_config` (temperature 1.0, top_p 0.95, top_k 20; the
harness sets none) and the chat template's defaults (thinking on, `reasoning_effort=xhigh`,
`preserve_thinking=true`), so no chat-template override was applied in the baseline arm.

Driver additions (`research/common/profile_run.py`): `--vllm-metrics URL` scrapes the server's Prometheus `/metrics`
after every model call under a lock and records the deltas since the previous scrape in the inputs sidecar
(`record["vllm"]`: `request_prefill_time_seconds`, `request_decode_time_seconds`, `request_queue_time_seconds`,
`e2e_request_latency_seconds`, `time_to_first_token_seconds`, KV-computed / prompt / cached / generation tokens,
prefix-cache queries and hits, finished requests by reason, preemptions, running / waiting / KV-usage gauges);
when exactly one request finished in between the deltas are that call's own (`exact`), otherwise only the run
totals (`profile_end.vllm_totals`) are used. The scrape costs 5-10 ms per call and is taken out of the `other`
bucket as `instrument`. `--client-max-retries 0` switches off the SDK's silent retries (the profiler's own 429
loop and the harness's lead-call retries stay); `--server-info` records `/version` and `/v1/models`.
`research/05_latency_breakdown/latency_workloads.py` gained `--driver-arg` (pass-through) and `--skip-existing` (resumable matrix);
`research/05_latency_breakdown/smoke_check.py` gates the matrix on one FQA team run (tool_use blocks, thinking blocks, cache fields,
streaming timing, server metrics, coverage); `research/05_latency_breakdown/latency_breakdown.py` gained a server-side table and a
stability table (mean +- sd over repetitions); `research/05_latency_breakdown/latency_compare.py` renders the two run sets side by side.

Outputs: `research/05_latency_breakdown/data/latency_profiling_qwen/` (traces, `.inputs.jsonl` / `.reads.jsonl` sidecars, sandboxes, scores,
console logs), `research/05_latency_breakdown/data/latency_profiling_qwen/latency_tables.md|json`, `provider_probe.json` (vLLM probes),
`compare_glm_vs_qwen.md|json` (the side-by-side tables), `pipeline/` (job metadata, server info, startup lines,
restarts) and `vllm_server_*.log`. The comparison is discussed in Part B.

## A.W Additions from `weekly_progress/091626/latency_breakdown.md` (2026-09-16)

_(was the preamble)_

Weekly progress, 2026-09-16 (experiment run 2026-09-12 evening). Full report:
Part A; all tables:
`research/05_latency_breakdown/data/latency_profiling/latency_tables.md`.
Tooling: `research/common/profile_run.py` (`--stream`, `--write-root/--sandbox-from`, `--allow-python`,
`profile_timing` events), `research/05_latency_breakdown/latency_workloads.py` (workloads + scoring),
`research/05_latency_breakdown/latency_breakdown.py` (round reconstruction + tables), bench problems in
`research/common/fixtures/latency_bench/coding/`. Sections 4 and 5 were added on 2026-09-13 after follow-up probes
of the provider's timing and cache behaviour and a re-measurement of the 50k-limit runs.

_(was §3 E, closing paragraph)_

Totals over 24 runs: agent calls 4,704 s, turn-end memory work 1,364 s, context preparation 32 s,
tool execution 9.4 s, wall 3,946 s.

_(was §4, “How prefill and decode were separated (probes, 2026-09-13)”)_

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

_(was §4, “How prefill and decode were separated (probes, 2026-09-13)”, after the probe table)_

Decode measured the same way is 81 tokens/s for 300-token outputs and 39 tokens/s for a 1,200-token
output, that is 12-26 ms per token, bracketing the regression's 17.4 ms. The share of a round that
prefill occupies is therefore set by the uncached tokens per call: 0.4-1.6% at this week's 0.9-3.8k,
about 10% at the 9-19k of the X3 runs (`research/03_context_interventions/README.md` (was `agent_e2e_bottleneck.md` Q2)), and seconds only above roughly
50k per call.

_(was §5, “Why the context-preparation share is small here, when earlier runs showed 52-80% redundancy” — the whole section)_

Two different quantities have been called a context cost this week. The share in section 3 is
**harness wall time** spent preparing a request. The figures of `research/02_input_redundancy/README.md` Part B are
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
runs (X3 workload, the `research/04_kv_splice/data/kv_splice` baselines) sat at the 50k-character limit, where eviction
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

_(was §6, conclusion 2)_

2. **Prefill is under 2% of call time; the fixed provider component (3.3-3.7 s) and decode
   (12-26 ms per token) are the rest.** Three independent measurements agree on 0.033-0.042 ms per
   uncached token, so re-prefilling a 5k-token history costs about 0.19 s, and prefill becomes
   seconds only above roughly 50k tokens per call. KV or prefix reuse cannot shorten these rounds
   measurably; its value at this scale is capacity, not latency.

_(was §6, conclusion 5)_

5. **Rounds per task: math 3, file Q&A 4, coding 7.5 (teammate); the lead adds 10-11 orchestration
   rounds per run.** For these small tasks a solo lead was 1.4-1.8x faster end to end at equal quality.

_(was §6, conclusion 6)_

6. **That repetition is as high as in the earlier runs; it simply does not buy latency.** Cross-team
   duplication tracks each workload's design ceiling exactly, 22.8% measured against a 22.8% ceiling
   in file Q&A and 0% with disjoint problems, so it measures how the tasks were cut rather than the
   harness; and at 0.038 ms per uncached token the repeated input costs tokens and KV memory, not
   time.

_(was §6, conclusion 7)_

7. **Under context pressure the bill arrives as rounds.** At the 50k limit the same harness needed
   33-65 rounds and 363-807 s for a single task, with 18-45 shrink events and up to 46% of the
   lead's file bytes re-read, while preparation per round was still 0.65 s of which 95% was one
   summary call. Sizing the resident set stays the lever (`research/03_context_interventions/README.md` (was `agent_e2e_bottleneck.md` Q1)).

_(was §6, closing paragraph)_

Two analyzer fixes made while checking these numbers, both affecting reproduction:
`latency_breakdown.py` no longer collects `.requests.` or `.replay.` sidecars as traces, and its
compaction column no longer double counts the summary-compaction model call, which runs inside the
`context_prepare` span and is now reported separately. `input_redundancy.py`'s file-content share
assumes an item stays resident in every later call of that agent, so it overstates for runs with
eviction or lead-side persistence; it is used here only for runs where nothing was evicted.

# Part B — End-to-end latency breakdown, rerun on Qwen3.8-27B (local vLLM) and compared with the GLM run

_Formerly `weekly_progress/092326/latency_qwen_vs_glm.md` (dated 2026-09-23; runs 2026-09-21 evening). Its setup and reproduction are Part A Appendix C._

Weekly progress, 2026-09-23 (experiment run 2026-09-21 evening). GLM baseline: Part A
(run 2026-09-12, `glm-5.3-flash` via z.ai). All Qwen tables: `research/05_latency_breakdown/data/latency_profiling_qwen/latency_tables.md`;
the side-by-side tables this note quotes from: `research/05_latency_breakdown/data/latency_profiling_qwen/compare_glm_vs_qwen.md`
(generated by `research/05_latency_breakdown/latency_compare.py`). Reproduction: Part A, Appendix C.

## 1. Question

Same questions as two weeks ago, now with the model under our control: for file Q&A, the coding bench and
competition math, what does one agentic round cost end to end and how does it split into context preparation,
the agent call (prefill vs decode, now measured on the server instead of regressed) and tool execution; how many
input and output tokens per call for the lead and the teammates; how much teammate input is redundant; how many
agent runs does one task take -- and how much of the GLM picture was the remote provider rather than the harness?

## 2. Setup

Identical to the GLM run except for the model and where it runs: s15 harness at the same commit (`aded0d0`),
`CONTEXT_LIMIT` 512k chars, streaming, lane copy, fresh state per run, the same prompts and the same matrix
(**team** = lead + 3 teammates, 5 repetitions; **solo** = lead alone, 3 repetitions; 24 runs). Model
`Qwen/Qwen3.8-27B` in bf16 (a hybrid of 48 Gated-DeltaNet and 16 full-attention layers, 256K context, thinking on
by default at `reasoning_effort=xhigh`, sampling from its `generation_config`: T 1.0, top_p 0.95, top_k 20) served
by vLLM 0.29.0 on one RTX PRO 6000 Blackwell (96 GB) through vLLM's Anthropic-compatible `/v1/messages`
endpoint on the loopback interface (`--max-model-len 262144`, `--gpu-memory-utilization 0.90`,
`--max-num-seqs 32`, prefix caching on, `--enable-prompt-tokens-details`, reasoning parser `qwen3`, tool parser
`qwen3_coder`). The harness code is unchanged; the whole run is one Slurm job driven by
`research/05_latency_breakdown/qwen_vllm_pipeline.sh` (16 CPUs, so the harness and the server never compete for a core).

What is new in the measurement:

- **Server-side timing.** After every model call the driver scrapes vLLM's `/metrics` and records the deltas of
  the per-request histograms (prefill, decode, queue, end-to-end seconds, KV-computed tokens, prefix-cache hits).
  When exactly one request finished since the previous scrape the deltas are that call's own ("exact"); with
  teammates finishing together only the run totals are exact. The scrape costs 5-10 ms per call and is booked in
  its own bucket (`instrument`), outside the harness buckets.
- **Computed tokens.** vLLM reports the tokens it computed *and wrote into its prefix cache* as
  `cache_creation_input_tokens` and leaves in `input_tokens` only the tail that did not fill a block. "Uncached"
  tokens are therefore `input_tokens + cache_creation_input_tokens` on both providers (the field is null on z.ai,
  so the GLM numbers are unchanged).
- **784-token cache blocks.** vLLM aligns the attention pages of this hybrid model to its recurrent-state pages,
  so a prefix-cache block is 784 tokens rather than 16: a resend is served from cache only in whole blocks, and
  the usage-level cache-read share understates reuse for short shared prefixes. The server's own hit counters are
  reported next to it.
- **Stability.** Every group now reports mean +- sd over its repetitions (`dispersion` table), which the GLM
  tables lacked (the GLM values were recomputed from the archived traces with the same code).
- **No SDK retries** (`--client-max-retries 0`), so a slow request cannot be silently doubled.

## 3. Results (team mode: GLM -> Qwen; means over pooled rounds or calls unless noted)

All 24 Qwen runs completed. Quality is the same on both providers: coding 24/24 problems pass, math 24/24
answers correct, file Q&A keyword coverage 0.98 +- 0.02 team / 0.97 +- 0.03 solo (GLM 0.99 / 0.97); no agent call
on either side ended in `max_tokens`. Qwen runs take 1.8-3.4x the wall time (team: CODE 470 +- 160 s vs 215 +- 32,
FQA 420 +- 49 vs 180 +- 21, MATH 306 +- 57 vs 165 +- 12; solo: 386 +- 38 vs 161 +- 53, 346 +- 57 vs 101 +- 28,
209 +- 25 vs 118 +- 3).

**A. One round** (GLM -> Qwen)

| | CODE lead | CODE teammate | FQA lead | FQA teammate | MATH lead | MATH teammate |
|---|---:|---:|---:|---:|---:|---:|
| gross per round, mean (s) | 7.7 -> 11.8 | 10.0 -> 27.6 | 9.7 -> 21.5 | 8.9 -> 24.6 | 7.6 -> 14.5 | 17.2 -> 27.0 |
| gross median (s) | 6.9 -> 8.6 | 5.5 -> 5.3 | 8.3 -> 11.1 | 6.2 -> 7.9 | 6.6 -> 7.6 | 12.1 -> 23.8 |
| context preparation | 7.0% -> 0.1% | 0.0% -> 0.0% | 0.1% -> 0.0% | 0.0% -> 0.0% | 0.1% -> 5.1% | 0.0% -> 0.0% |
| agent call | 92.9% -> 99.6% | 99.6% -> 99.5% | 99.8% -> 99.8% | 99.8% -> 99.8% | 99.8% -> 94.6% | 99.9% -> 99.7% |
| tool execution | 0.1% -> 0.2% | 0.4% -> 0.4% | 0.1% -> 0.1% | 0.2% -> 0.1% | 0.1% -> 0.2% | 0.1% -> 0.2% |
| client time to first block (s) | 5.5 -> 0.7 | 5.6 -> 0.4 | 6.1 -> 1.0 | 5.5 -> 1.0 | 5.9 -> 0.9 | 6.1 -> 0.4 |
| streamed tail after it (s) | 1.6 -> 11.0 | 4.4 -> 27.0 | 3.6 -> 20.4 | 3.3 -> 23.6 | 1.6 -> 12.8 | 11.0 -> 26.5 |
| turn-end memory work per lead turn (s) | 19.1 -> 20.0 | | 20.6 -> 35.0 | | 13.1 -> 12.4 | |
| (prep + turn-end) share of lead-side time | 57.6% -> 40.8% | | 44.2% -> 37.5% | | 42.1% -> 27.1% | |

Preparation stays at 7-9 ms per round on Qwen (the two exceptions are model calls the harness makes to prepare
context: GLM's CODE runs recalled memory before 2.4 lead calls per run, 4.9 s each; in one Qwen MATH run the lead itself called the harness's `compact` tool and paid a 52 s
summary-compaction model call, 5.1% of that group's lead time; no run on either provider reached the 512k-char limit). Tool execution is 8-32 ms per
lead round and 30-120 ms per teammate round (CODE teammates run the unit tests) on both. The profiler's `/metrics`
scrape adds 19-21 ms per call and is booked separately (0.1%).

**B. Inside the agent call** -- regression over all agent calls of each side, `duration = a + b x computed
prompt tokens + c x output tokens` (least squares; the GLM fit is over its 446 agent calls, the Qwen fit over the
501 agent calls of all 24 runs):

| | GLM (446 calls) | Qwen (501 calls) |
|---|---:|---:|
| a, fixed (s) | 3.72 | 0.43 |
| b, per computed prompt token (ms) -> implied prefill rate | 0.033 -> 30,600 tok/s | 0.117 -> 8,600 tok/s |
| c, per output token (ms) -> decode rate | 17.4 -> 57 tok/s | 39.9 -> 25 tok/s |
| r2 | 0.96 | 1.00 |
| lead calls only: a / b / c (r2) | 3.16 / 0.086 / 17.8 (0.95) | 0.35 / 0.162 / 38.3 (1.00) |
| teammate calls only: a / b / c (r2) | 4.10 / -0.002 / 17.1 (0.97) | 0.26 / 0.411 / 40.6 (1.00) |

The Qwen intercept is a fitting artefact, not a provider cost: the server reports a queue time of 0.000 s and the
client-minus-server gap is 27-61 ms per call (table below), while the regression's prefill slope (0.117 ms per
token) sits below the server-measured 0.17 ms (leads alone on the GPU) to 0.25-0.34 ms (teammates sharing it) and
the intercept absorbs the difference. The GLM intercept is real: z.ai delivered the first block after a flat
5.6-6.1 s regardless of prompt size.

| share of call time (team), pooled coefficients | CODE lead | CODE tm | FQA lead | FQA tm | MATH lead | MATH tm |
|---|---:|---:|---:|---:|---:|---:|
| fixed (network, queue, start-up) | 52% -> 3.7% | 37% -> 1.6% | 38% -> 2.0% | 42% -> 1.8% | 49% -> 3.1% | 22% -> 1.6% |
| prefill of computed tokens | 1.4% -> 3.6% | 0.3% -> 0.6% | 1.3% -> 2.7% | 1.0% -> 1.6% | 1.5% -> 3.8% | 0.2% -> 0.4% |
| decode | 56% -> 96% | 59% -> 97% | 62% -> 97% | 58% -> 89% | 50% -> 96% | 77% -> 96% |

Applying each side's own coefficients to each group's token counts decomposes the call-time ratio (seconds per
agent call, team mode; "meas." = measured mean call duration):

| | CODE lead | CODE tm | FQA lead | FQA tm | MATH lead | MATH tm |
|---|---:|---:|---:|---:|---:|---:|
| GLM: fixed + prefill + decode | 3.7 + 0.1 + 4.0 = 7.9 (meas. 7.2) | 3.7 + 0.0 + 5.9 = 9.6 (10.0) | 3.7 + 0.1 + 6.0 = 9.8 (9.7) | 3.7 + 0.1 + 5.2 = 9.0 (8.9) | 3.7 + 0.1 + 3.8 = 7.6 (7.6) | 3.7 + 0.0 + 13.2 = 17.0 (17.1) |
| Qwen: fixed + prefill + decode | 0.4 + 0.4 + 11.4 = 12.2 (11.8) | 0.4 + 0.2 + 26.7 = 27.3 (27.5) | 0.4 + 0.6 + 20.8 = 21.8 (21.5) | 0.4 + 0.4 + 21.7 = 22.5 (24.5) | 0.4 + 0.5 + 13.2 = 14.2 (13.7) | 0.4 + 0.1 + 25.8 = 26.3 (27.0) |
| decode ratio = (ms per token ratio) x (output tokens ratio) | 2.8x = 2.3 x 1.2 | 4.6x = 2.3 x 2.0 | 3.5x = 2.3 x 1.5 | 4.2x = 2.3 x 1.8 | 3.5x = 2.3 x 1.5 | 1.9x = 2.3 x 0.85 |
| measured call ratio | 1.6x | 2.7x | 2.2x | 2.8x | 1.8x | 1.6x |

Every group sheds the same 3.3 s of fixed cost and pays 2.3x per decoded token; how much longer its calls get is
set by how many more tokens the model writes: 1.2x for the CODE lead (1.6x call), 2.0x for CODE teammates (2.7x),
0.85x for MATH teammates (1.6x; the only group where Qwen writes less than GLM).

On Qwen the split is also measured directly by the server (vLLM `/metrics`, all 501 agent calls exactly attributable,
teammates included, with at most 2.2 requests in flight):

| server-side, Qwen | CODE lead | CODE tm | FQA lead | FQA tm | MATH lead | MATH tm |
|---|---:|---:|---:|---:|---:|---:|
| prefill per call (s) / share of the client call | 0.61 / 5.2% | 0.34 / 1.2% | 0.86 / 4.0% | 0.79 / 3.2% | 0.77 / 5.6% | 0.35 / 1.3% |
| decode per call (s) / share | 11.1 / 94% | 27.0 / 98% | 20.4 / 95% | 23.6 / 96% | 12.9 / 94% | 26.5 / 98% |
| queue (s) | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| client call minus server e2e (HTTP + SDK), mean (s) | 0.059 | 0.034 | 0.058 | 0.029 | 0.061 | 0.027 |
| prefill rate (computed tokens / prefill s) | 5,920 | 3,940 | 5,690 | 4,150 | 5,850 | 2,970 |
| decode (ms per token) | 39.0 | 40.4 | 39.3 | 43.4 | 38.9 | 41.1 |
| requests running when the call finished | 0.9 | 1.4 | 1.0 | 2.2 | 0.9 | 2.0 |
| KV cache in use | 1.2% | 2.0% | 2.8% | 4.7% | 0.9% | 2.3% |

The direct probes (`provider_probe.py`, 27 calls) agree: time to first block = 0.00 s + 0.175 ms per prompt token
(r2 1.00, 491-35,746 tokens; 5,700 tok/s), an identical resend of 35,280 cached tokens cuts it from 6.31 s to
0.42 s (0.167 ms saved per cached token), decode is 26 tok/s at every prompt size and for a 1,200-token output,
and no response arrives in a burst (GLM: 29-59% of responses arrived whole; z.ai's first block came after a flat
5.6-6.1 s). The cache is strictly prefix-based on both providers (identical resend 99.4% cached, new prefix 0%,
never-sent document 0%); a unique block inserted mid-prompt gave 0% on vLLM even though 15k tokens of prefix were
shared, because with the hybrid model's `align` cache mode a recurrent state is only saved where a scheduler step
ended on a 784-token boundary, so a different request can resume only from those positions.

**C. Tokens per call** (GLM -> Qwen)

| | CODE lead | CODE teammate | FQA lead | FQA teammate | MATH lead | MATH teammate |
|---|---:|---:|---:|---:|---:|---:|
| prompt tokens, mean | 4,796 -> 5,948 | 3,717 -> 5,936 | 5,547 -> 7,265 | 5,373 -> 8,830 | 5,267 -> 6,826 | 1,921 -> 2,698 |
| computed (uncached) tokens, mean | 3,099 -> 3,596 | 905 -> 1,321 | 3,766 -> 4,913 | 2,586 -> 3,292 | 3,577 -> 4,474 | 983 -> 1,022 |
| provider cache-read share | 35% -> 40% | 76% -> 78% | 32% -> 32% | 52% -> 63% | 32% -> 35% | 49% -> 62% |
| output tokens, mean / median | 231 / 143 -> 285 / 197 | 337 / 68 -> 670 / 122 | 343 / 189 -> 521 / 213 | 296 / 95 -> 544 / 150 | 215 / 152 -> 332 / 166 | 760 / 518 -> 646 / 571 |
| thinking share of output | 22% -> 33% | 51% -> 68% | 37% -> 49% | 51% -> 65% | 25% -> 47% | 56% -> 50% |
| prompt growth per round, median (tok) | 242 -> 283 | 276 -> 485 | 580 -> 652 | 847 -> 1,625 | 360 -> 520 | 546 -> 624 |
| streamed tail, tok/s | 142 -> 26 | 77 -> 25 | 94 -> 26 | 89 -> 23 | 132 -> 26 | 69 -> 24 |

Token counts are not directly comparable across the two tokenizers: on the same text Qwen3.8's tokenizer yields
12-15% more tokens than GLM's (3.8-3.9 vs 4.3-4.5 characters per prompt token on the code and file-Q&A prompts,
3.3-3.6 vs 3.9-4.1 per output token; `compare_glm_vs_qwen.md` T4 carries the character columns). In characters,
which are tokenizer-independent, Qwen's prompts are 1.08-1.16x larger for leads and 1.14-1.51x for teammates
(its chat template re-renders every earlier thinking block, `preserve_thinking`, and the harness replays them),
and its outputs are 1.05-1.35x longer for leads, 1.76-1.77x for CODE and FQA teammates and 0.75x for MATH
teammates (thinking 33-68% of output characters; every response starts with a thinking block). Output length, not prompt length, is what
the round pays for: at 40 ms per token a 500-token response is 20 s.

**D. Teammate input redundancy** (ranges over the 5 runs; GLM -> Qwen)

| | CODE | FQA | MATH |
|---|---:|---:|---:|
| re-sent from the same teammate's previous call | 76-86% -> 72-88% | 33-58% -> 62-67% | 45-63% -> 37-72% |
| served by the provider cache (usage) | 68-79% -> 67-85% | 27-58% -> 60-64% | 44-52% -> 46-66% |
| server prefix-cache hit rate, all requests of a run (vLLM) | 51-77% | 44-51% | 34-49% |
| file bytes another teammate fetched first | 0% -> 0% | 12-23% -> 23% | no file reads |
| cross-redundant share of teammate prompt tokens | 0% -> 0% | 8-27% -> 16-20% | 0% -> 0% |
| file content share of teammate prompt tokens | 21-30% -> 15-25% | 76-86% -> 78-81% | 0% -> 0% |
| lead: re-sent / cache-read | 87-91% / 28-42% -> 88-91% / 36-44% | 84-88% / 20-38% -> 83-89% / 26-35% | 85-89% / 29-36% -> 81-90% / 30-40% |

The redundancy is a property of the harness and the workload, not of the provider: the same 62-88% of a
teammate's prompt is a re-send, cross-teammate duplication again equals the FQA workload's 22.8% design ceiling
and 0% for disjoint problems, and the lead's cache-read share stays low (26-44%) because its per-second
timestamp line invalidates the prefix on every round. vLLM serves a larger share of the re-sent prefix from its
cache than z.ai did for FQA and MATH (60-66% vs 27-58%): it never evicts here (the 512k-token cache is 1-5% used),
and its 784-token blocks cost the lead's partially filled last block, nothing more.

**E. Agent runs per task and end-to-end time** (GLM -> Qwen)

| | CODE team | FQA team | MATH team |
|---|---:|---:|---:|
| teammate rounds per task, mean / median / max | 7.5 / 6 / 15 -> 7.1 / 6 / 16 | 4.0 / 4 / 6 -> 4.2 / 4 / 5 | 2.9 / 3 / 5 -> 2.9 / 3 / 5 |
| teammate active time per task (s) | 75 -> 195 | 36 -> 103 | 49 -> 78 |
| lead rounds / turns per run | 11.2 / 5.4 -> 12.8 / 5.2 | 10.2 / 3.8 -> 11.4 / 4.2 | 11.0 / 4.6 -> 14.2 / 5.0 |
| model calls per run (incl. memory) | 41.4 -> 39.2 | 26.8 -> 28.2 | 24.2 -> 31.0 |
| wall per run (s) / per task (s) | 215 / 72 -> 470 / 157 | 180 / 60 -> 420 / 140 | 165 / 55 -> 306 / 102 |
| lead busy share of wall | 88% -> 54% | 98% -> 93% | 87% -> 88% |
| of which lead agent calls (s) / turn-end memory extraction (s) | 80 / 103 -> 151 / 104 | 99 / 78 -> 245 / 147 | 83 / 60 -> 195 / 62 |
| teammate active share of wall | 52% -> 69% | 26% -> 29% | 50% -> 51% |

The number of agent runs per task did not move (teammate rounds per task 7.1 / 4.2 / 2.9 vs 7.5 / 4.0 / 2.9,
lead rounds per run within +-3): the control flow of the harness is the same on both models, and the whole
1.9-2.3x wall-time difference is the per-round decode time. Turn-end memory extraction is still the largest
harness-side cost: 62-147 s per run (12-35 s per lead turn; 4-81% of those 1,000-token calls end in `max_tokens`
on Qwen, 35-74% on GLM).

**F. Stability** (mean +- sd over the 5 runs of each group; the GLM values recomputed from the archived traces)

| | CODE team | FQA team | MATH team |
|---|---:|---:|---:|
| wall per run (s) | 215 +- 32 -> 470 +- 160 | 180 +- 21 -> 420 +- 49 | 165 +- 12 -> 306 +- 57 |
| lead gross per round (s) | 7.9 +- 1.4 -> 11.8 +- 1.7 | 9.8 +- 1.3 -> 21.9 +- 5.7 | 7.6 +- 0.5 -> 14.8 +- 5.0 |
| teammate gross per round (s) | 10.3 +- 1.9 -> 27.3 +- 4.2 | 8.9 +- 0.8 -> 25.0 +- 4.4 | 17.3 +- 1.6 -> 28.1 +- 5.3 |
| lead rounds per run | 11.2 +- 1.6 -> 12.8 +- 1.1 | 10.2 +- 1.3 -> 11.4 +- 1.5 | 11.0 +- 1.6 -> 14.2 +- 2.2 |
| output tokens per agent call | 307 +- 72 -> 521 +- 84 | 321 +- 58 -> 535 +- 57 | 457 +- 61 -> 475 +- 67 |

Run-to-run spread is 7-34% (CV) on both providers; the widest Qwen cell is one CODE run of 754 s in which a
teammate needed 16 rounds. Five repetitions separate every GLM-vs-Qwen difference above by more than two standard
deviations except the MATH lead round.

**G. Solo arm** (lead alone, 3 runs per category; GLM -> Qwen)

| | CODE solo | FQA solo | MATH solo |
|---|---:|---:|---:|
| lead rounds per run | 15.7 -> 14.7 | 3.7 -> 10.3 | 2.3 -> 2.3 |
| gross per round, mean / median (s) | 9.5 / 6.1 -> 25.7 / 10.2 | 21.0 / 9.0 -> 29.6 / 12.0 | 41.5 / 42.5 -> 76.9 / 83.5 |
| context preparation / tool execution | 0.1% / 0.5% -> 0.0% / 0.7% | 0.0% / 0.1% -> 0.0% / 0.1% | 0.0% / 0.1% -> 0.0% / 0.1% |
| client first block / streamed tail (s) | 5.2 / 4.3 -> 1.3 / 24.2 | 6.0 / 15.0 -> 1.1 / 28.4 | 5.7 / 35.8 -> 0.6 / 76.2 |
| server prefill / decode per call (s), Qwen | 1.16 / 24.2 | 0.99 / 28.4 | 0.47 / 76.2 |
| prefill tok/s / decode ms per token, Qwen (alone on the GPU) | 6,140 / 38.4 | 6,110 / 38.5 | 5,660 / 38.1 |
| prompt tokens, mean | 7,136 -> 9,445 | 13,801 -> 8,380 | 4,533 -> 4,986 |
| prompt growth per round, median (tok) | 285 -> 478 | 6,954 -> 1,312 | 2,688 -> 2,380 |
| output tokens, mean / thinking share | 332 / 38% -> 633 / 55% | 943 / 49% -> 739 / 62% | 2,107 / 68% -> 2,001 / 61% |
| turn-end memory extraction per turn (s) | 10.1 -> 7.1 | 22.2 -> 38.4 | 19.1 -> 27.8 |
| wall per run (s) | 161 +- 53 -> 386 +- 38 | 101 +- 28 -> 346 +- 57 | 118 +- 3 -> 209 +- 25 |
| solo wall vs team wall, same provider | 0.75x -> 0.82x | 0.56x -> 0.82x | 0.72x -> 0.68x |

The solo arm shows the same mechanics with one twist: the FQA solo lead on Qwen pages through the documents in
10 rounds where GLM read them whole in 4 (prompt growth per round 1,312 vs 6,954 tokens), so its 3.4x wall time is
2.5x more rounds at 1.4x the round time, while MATH solo is a single 77 s decode of a 2,000-token solution
(41.5 s on GLM). Alone on the GPU the server prefills at 5,700-6,100 tok/s and decodes at 38.1-38.5 ms per
token, the numbers the probes predicted. A solo lead is still faster than a team on Qwen (0.68-0.82x), but by
less than on GLM (0.56-0.75x): when decode is the cost, three teammates decoding in parallel on one GPU recover
more of the lead's orchestration overhead than a remote API did.

## 4. Conclusions

1. **The round is still the model call (94-99.8%), and the harness buckets did not move:** context preparation
   7-9 ms, tool execution 8-120 ms, per round, on a local model exactly as on the remote API. What changed is
   *inside* the call: the 3.7 s fixed provider component of the GLM run is gone on the local server (server queue
   0.000 s, HTTP + SDK 27-61 ms per call; the 0.43 s regression intercept is a fitting artefact), so the first block arrives after 0.4-1.0 s instead of 5.5-6.1 s -- and that
   first-block time is now a real prefill measurement (server 0.3-0.9 s per call).
2. **Prefill is 1-6% of a call, decode 90-99%.** The 27B model prefills at 5,700-5,900 tok/s alone and 3,000-4,200
   tok/s while teammates share the GPU (0.17 ms per computed token, r2 1.00 in the probe); decode is
   39-43 ms per token measured on the server (26 tok/s per stream, weight-bandwidth-bound for 54 GB of bf16
   weights) against GLM's 17.4 ms by regression. With 1.2-2x more output tokens (1.05-1.8x more output characters; thinking 33-68% of output at the
   model's default `xhigh` effort) at 2.3x the cost per token, decode time per call is 1.9-4.6x GLM's, rounds are
   1.5-2.8x longer, team runs 1.9-2.3x and solo runs 1.8-3.4x longer, at identical quality.
3. **Prefix reuse buys capacity, not latency, on this hardware too:** re-sending a 5k-token history costs
   5k x 0.17 ms = 0.85 s, 3-6% of a 15-25 s round; the cache saved 60-85% of teammate prompt tokens and 34-77% of
   all prompt tokens per run, with the KV cache 1-5% full. Two vLLM-specific facts matter for KV-sharing designs:
   cache blocks are 784 tokens on this hybrid model, and a foreign request can only resume from positions where
   the producing request checkpointed its recurrent state (`--mamba-cache-mode align`), so cross-agent prefix
   sharing needs either `--mamba-cache-mode all` (154 MB of state per checkpoint) or block-aligned prompts.
4. **Rounds per task are model-independent at this scale** (7 / 4 / 3 teammate rounds, 11-14 lead rounds per
   team run, same tool-call rate), and so is the redundancy pattern (72-88% re-sent, cross-teammate duplication
   equal to the workload ceiling). The wall-time gap is decode speed times output length; the one behavioural
   difference is the solo FQA lead paging files in 10 rounds instead of 4, which alone makes that group 3.4x.
5. **Turn-end memory extraction remains the largest non-agent cost** (12-35 s per lead turn, 20-35% of run wall),
   and it is worse with a thinking model because the 1,000-token extraction call is mostly thinking (4-81% of
   them truncated). Asynchronous or once-per-request extraction is the harness change with the largest payoff on
   both providers.

## 5. Caveats

One GPU, one model, one evening: decode speed is a property of dense-27B-in-bf16 on a 1.8 TB/s card (an FP8
checkpoint, speculative decoding with the built-in MTP head, or an H200 would each cut it), not of the harness;
z.ai's serving stack, batch load and hardware are unknown. Qwen ran at its default `reasoning_effort=xhigh` with
earlier thinking re-sent (`preserve_thinking`), the model's own defaults; a `medium`/`low` arm or
`preserve_thinking=false` (both via `--default-chat-template-kwargs`) would shorten rounds and prompts but changes
the model's behaviour. Teammates finishing together would make per-call server attribution ambiguous; in these runs
every agent call was exactly attributable (at most 2.2 requests in flight). The harness commit is the GLM run's,
except that the driver's mutation filter no longer rejects `>` inside quoted Python (denied tool calls per run:
GLM 0.8-2.2, Qwen 0.0-1.0). GLM CODE runs made memory-recall calls before some lead rounds and Qwen runs did not
(Qwen's extraction produced no records to recall), which is why the CODE lead's context-preparation share fell from
7.0% to 0.1%. File-Q&A scoring is keyword coverage, not a reader. The Qwen usage numbers count computed tokens as
`input_tokens + cache_creation_input_tokens`; the GLM analysis is unchanged by that definition. Token counts are
each provider's own tokenizer's: Qwen3.8's is 12-15% denser than GLM's on these prompts, so token ratios overstate
size differences by that much and per-token rates (ms per token, tok/s) are not convertible between the two
providers; the character columns of T4 are the tokenizer-independent comparison. The regression intercept is
only a provider cost where the server cannot be observed (GLM); on vLLM the measured queue and HTTP/SDK gap
replace it.

---

## Data inventory

All paths are under `research/05_latency_breakdown/data/`. Run labels are `<category>-<mode>-r<rep>` (FQA / CODE / MATH; team r1–r5, solo r1–r3). Every run trace `run_<stamp>_<id>.jsonl` has two sidecars, `.inputs.jsonl` (per-call usage, timing and, on vLLM, `/metrics` deltas) and `.reads.jsonl` (file-read provenance). Git-tracked unless marked local.

| path | what it holds | written by | used in | status |
|---|---|---|---|---|
| `latency_profiling/run_20260913T0137…T0249*.jsonl` + sidecars (72 files) | 24 GLM runs, 2026-09-13 01:37:44–02:51:44 UTC, `glm-5.3-flash` via z.ai, streaming, `CONTEXT_LIMIT` 512,000 chars, harness `64d1b11`, lane `/home/yq335/lanes/latency`; all `completed`, wall 81.8–248.9 s | `latency_workloads.py` → `research/common/profile_run.py` | Part A §2 and Appendix A; the GLM side of Part B (recomputed on the fly by `latency_compare.py`) | published |
| `latency_profiling/<label>.score.json` (24) | per-run score (CODE: unit tests re-run on the archived sandbox; MATH: `Final answer` lines; FQA: keyword coverage), wall, denials, trace path at run time | `latency_workloads.py` | Part A §2.1 | published |
| `latency_profiling/CODE-{solo-r1…r3,team-r1…r5}.sandbox/` (8) | archived coding sandbox of each CODE run: `expr/`, `intervals/`, `ttl_cache/`, each with the README specification, the unittest file and the agents' `solution.py` | `profile_run.py --write-root/--sandbox-from` | CODE scores, Part A §2.1 | published |
| `latency_profiling/latency_tables.md` / `.json` | every Part A table (`--per-run --tools`) and the per-round data behind them | `latency_breakdown.py` as of `bdc4788` (2026-09-13) | Part A §2 (the Short answer points to it) | published, older format: regenerating it with the current script changes 28 diff lines (summary-compaction columns added to the context-preparation table, token-table caption reworded for the computed-token definition, stability table appended) and no value (regenerated and diffed during the cleanup, 2026-09-24). `latency_compare.py` recomputes GLM with the current script, so the comparison tables are consistent |
| `latency_profiling/smoke/` | 2 GLM runs, 2026-09-13 01:32 UTC: `SMOKE-plain` (reply "OK", no tools; 16.3 s) and `SMOKE-sandbox` (write `intervals/solution.py` in the sandbox, run its test, try a write outside the root, which was denied as `write_file-outside-root`; 47.1 s), both `completed`; `SMOKE-sandbox.sandbox/` keeps the one-line solution | `profile_run.py` (labels in `profile_meta`) | none (the analyzers glob only the top level) | smoke |
| `latency_profiling/reference_solutions/` (`README.md`; `expr/`, `intervals/`, `ttl_cache/solution.py`) | one passing reference solution per coding problem, kept outside the bench so agents cannot find them with grep during a run | not determinable from the repo (no generating script; committed in `bdc4788`) | Part A Appendix B (folder only) | reference; the README's copy-and-test loop uses `../../scripts/latency_bench/coding/`, which no longer resolves after the move (the bench is now `research/common/fixtures/latency_bench/coding/`) |
| `latency_profiling_qwen/run_20260922T0202…T0431*.jsonl` + sidecars (72 files) | 24 Qwen runs, 2026-09-22 02:02:47–04:35:44 UTC (2026-09-21 22:02 – 2026-09-22 00:35 EDT), `Qwen/Qwen3.8-27B` via vLLM at `127.0.0.1:40727`, harness `aded0d0`, `--vllm-metrics`, `--client-max-retries 0`; all `completed`, wall 181.7–753.9 s | pipeline `matrix` step → `latency_workloads.py` → `profile_run.py` (Slurm job 95260 on alphagpu53) | Part B; Part A Appendix C | published |
| `latency_profiling_qwen/<label>.score.json` (24), `CODE-*.sandbox/` (8) | as for GLM | as for GLM | Part B §3 (quality) | published |
| `latency_profiling_qwen/latency_tables.md` / `.json` | every Qwen table, including the server-side (`/metrics`) and stability tables | pipeline `tables` step (`latency_breakdown.py --per-run --tools --json`), 2026-09-22 00:35 EDT | Part B §3; deck 092326 slide 11 | published; the .md regenerates byte-identically (re-checked 2026-09-24), the .json differs only in its stored absolute trace paths |
| `latency_profiling_qwen/compare_glm_vs_qwen.md` / `.json` | side-by-side GLM (A) and Qwen (B) tables T0–T10; the .json holds the numbers behind every table | `latency_compare.py --out … --json …`, re-rendered after the pipeline run (the pipeline's own render at 00:35 EDT had 267 lines; the committed .md has 274 and repo-relative paths) | Part B §3 A–G; deck 092326 slides 11–12 | published; both regenerate identically except for the path strings (header line, T10 heading, JSON `dir` fields) |
| `latency_profiling_qwen/provider_probe.json` | 27 direct probe calls against the vLLM server (prefill sweep, cached resends, decode, cache semantics), 2026-09-21 21:48–21:55 EDT | pipeline `probe` step (`provider_probe.py --all --json`) | Part B §3 B (probe paragraph, via compare T10) | published; written before the probe script's computed-token fix: its `uncached_tok` is `input_tokens` only (e.g. 480 of a 24,744-token prompt), which is why the inline summary in `slurm-95260.out` reads "slope not positive"; compare T10 refits it (0.1746 ms per token, r2 1.00), the value Part B quotes |
| `latency_profiling_qwen/smoke/` | `FQA-team-smoke`: one FQA team run, 2026-09-22 01:55:38 UTC (21:55 EDT), 414.0 s, coverage 1.0, 31 model calls; trace, sidecars, `.score.json` | pipeline `smoke` step + `smoke_check.py` (verdict `PASS` in `slurm-95260.out`, marker `pipeline/smoke.ok`) | the gate only | smoke (passed) |
| `latency_profiling_qwen/pipeline/` | `meta.json` (job 95260: host alphagpu53, GPU, vLLM flags, repetitions, lane, `git_head` `aded0d0`, package versions), `server_info.json` (`/version`, `/v1/models`, 98 metric families), `server_startup.txt` (server arguments and startup lines, e.g. the 784-token attention block and Mamba cache mode `align`), `port` (40727), `jobid` (95260), `READY`, `smoke.ok` | `qwen_vllm_pipeline.sh` (`step_env`, `ensure_vllm`, `step_smoke`); `READY` is touched by hand; no script in the repo writes `jobid` | Part A Appendix C, Part B §2; `latency_compare.py` reads `meta.json` and `server_info.json` for T0 (nothing reads `server_startup.txt`) | published (metadata and control files) |
| `latency_profiling_qwen/slurm-95260.out` | stdout of Slurm job 95260, the tracked copy of the pipeline log: environment, download, serve (healthy after 460 s), lane wait for `READY` (21:43:56–21:47:56), probe summary, smoke gate, 24 runs with scores and walls, tables, compare, stop (00:35:58) | `qwen_vllm.sbatch` | provenance of Part A Appendix C and Part B | published (log); byte-identical to the local `pipeline/pipeline_20260921T213458.log` |

Local only (git-ignored `*.log`, not in the repository):

- `latency_profiling_qwen/<label>.console.log` (24) and `latency_profiling_qwen/smoke/FQA-team-smoke.console.log`: the driver's console output of each run.
- `latency_profiling_qwen/vllm_server_20260921T213611.log`: the vLLM server log of the whole job.
- `latency_profiling_qwen/vllm_server.log`: symlink to that server log (`vllm_server_20260921T213611.log`); it pointed at the old absolute path under `s15_integrated_harness/traces/latency_profiling_qwen/` and was re-pointed as a relative link during the cleanup.
- `latency_profiling_qwen/provider_probe.log`: the full console output of the probe step.
- `latency_profiling_qwen/pipeline/pipeline_20260921T213458.log` (identical to `slurm-95260.out`) and `latency_profiling_qwen/pipeline/restarts.log` (one line: server started 21:43:56, pid 291668).
- The GLM runs' `<label>.console.log` named in Part A Appendix B do not exist in this checkout (never tracked; the runs executed under `/home/yq335/`, which is not on this host).

Previously uncited items:

- `latency_profiling/smoke/`: run on 2026-09-13 01:32 UTC as pre-matrix checks of streaming (`SMOKE-plain`) and of the sandbox write root and `--allow-python` (`SMOKE-sandbox`); both passed, the outside-root write was denied as intended, and the matrix started five minutes later. Not used by any table.
- `latency_profiling/reference_solutions/expr`, `latency_profiling/reference_solutions/intervals`, `latency_profiling/reference_solutions/ttl_cache`: written to show that each coding-bench problem is solvable against its unittest file, and kept out of the bench so agents cannot find them; committed with the GLM data in `bdc4788`, never used by a run or a table, README paths stale after the move.
- `latency_profiling_qwen/smoke/`: the `smoke_check.py` gate (one FQA team run) before the 24-run matrix; it passed (coverage 1.0; cache fields, `/metrics` deltas and streaming timing present) and the matrix started at 22:02:38 EDT. Not in the tables.
- `latency_profiling_qwen/compare_glm_vs_qwen.json`: the machine-readable numbers behind `compare_glm_vs_qwen.md`, written by the same `latency_compare.py` call; committed in `0cbddf8`, never cited by name; regenerates identically apart from paths.
- `latency_profiling_qwen/pipeline/meta.json`: job metadata recorded by the pipeline's `env` step (host, GPU, flags, versions, `git_head`); feeds the T0 setup table of `compare_glm_vs_qwen.md` (GPU, server flags, versions, harness commit); still describes job 95260.
- `latency_profiling_qwen/pipeline/server_info.json`: `/version` and `/v1/models` of the vLLM server, recorded once it was healthy; read by `latency_compare.py` for T0; unchanged since job 95260.
## Source map

One row per section of every source (numbered bold sub-blocks included where decks cite them). `new_location` is in this file unless it names another one.

| old file · old section | new location | status |
|---|---|---|
| `s15_integrated_harness/latency_profile.md` · (title and opening: profiling question, setup) | Part A (opening) | kept |
| `s15_integrated_harness/latency_profile.md` · Short answer | Part A Short answer | kept |
| `s15_integrated_harness/latency_profile.md` · §1 Method | Part A §1 | kept |
| `s15_integrated_harness/latency_profile.md` · §1.1 What is measured | Part A §1.1 | kept |
| `s15_integrated_harness/latency_profile.md` · §1.2 Instrumentation | Part A §1.2 | kept |
| `s15_integrated_harness/latency_profile.md` · §1.3 Workloads | Part A §1.3 | kept |
| `s15_integrated_harness/latency_profile.md` · §2 Results | Part A §2 | kept |
| `s15_integrated_harness/latency_profile.md` · §2.1 Runs | Part A §2.1 | kept |
| `s15_integrated_harness/latency_profile.md` · §2.2 One agentic round | Part A §2.2 | kept |
| `s15_integrated_harness/latency_profile.md` · §2.3 Context preparation, attributed | Part A §2.3 | kept |
| `s15_integrated_harness/latency_profile.md` · §2.4 Tokens and model-call timing per call | Part A §2.4 | kept |
| `s15_integrated_harness/latency_profile.md` · §2.5 Prefill vs decode | Part A §2.5 | kept |
| `s15_integrated_harness/latency_profile.md` · §2.5.1 Direct probe of the provider (2026-09-13) | Part A §2.5.1 | kept |
| `s15_integrated_harness/latency_profile.md` · §2.6 The harness's own model calls | Part A §2.6 | kept |
| `s15_integrated_harness/latency_profile.md` · §2.7 Teammate input redundancy | Part A §2.7 | kept |
| `s15_integrated_harness/latency_profile.md` · §2.8 Agent runs per task and where the wall time goes | Part A §2.8 | kept |
| `s15_integrated_harness/latency_profile.md` · §2.9 Tool execution | Part A §2.9 | kept |
| `s15_integrated_harness/latency_profile.md` · §3 Reading the numbers | Part A §3 | kept |
| `s15_integrated_harness/latency_profile.md` · §4 Threats to validity | Part A §4 | kept |
| `s15_integrated_harness/latency_profile.md` · Appendix A. Per-run round breakdown | Part A Appendix A | kept |
| `s15_integrated_harness/latency_profile.md` · Appendix B. Reproduction | Part A Appendix B | kept (cleanup note appended to its last line: GLM console logs not in the repo) |
| `s15_integrated_harness/latency_profile.md` · Appendix C. Rerun on Qwen3.8-27B through a local vLLM server (2026-09-21) | Part A Appendix C | kept |
| `weekly_progress/091626/latency_breakdown.md` · (title and preamble) | Part A §A.W (was the preamble) | preamble paragraph kept; title dropped (replaced by the A.W header) |
| `weekly_progress/091626/latency_breakdown.md` · §1 Questions | Part A (opening) | dropped (duplicate of Part A opening, the profiling question) |
| `weekly_progress/091626/latency_breakdown.md` · §2 Setup | Part A (opening), §1.1, §1.3, Short answer | dropped (duplicate of Part A opening setup, §1.1, §1.3 and the Short answer: 24 runs, 24/24, 24/24, 97-100%) |
| `weekly_progress/091626/latency_breakdown.md` · §3 Results (team mode unless noted; means over pooled rounds) | Part A §2 and §A.W | split by sub-block, see the five rows below |
| `weekly_progress/091626/latency_breakdown.md` · §3 A One round | Part A §2.2, §2.3, Short answer | dropped (duplicate of Part A §2.2 and §2.3; the solo-lead paragraph duplicates the Short answer and §2.2) |
| `weekly_progress/091626/latency_breakdown.md` · §3 B Inside the agent call | Part A Short answer, §2.5 | dropped (duplicate of Part A Short answer and §2.5: same regression and shares) |
| `weekly_progress/091626/latency_breakdown.md` · §3 C Tokens per call | Part A §2.4, Short answer | dropped (duplicate of Part A §2.4 and the Short answer) |
| `weekly_progress/091626/latency_breakdown.md` · §3 D Teammate input redundancy | Part A §2.7, Short answer | dropped (duplicate of Part A §2.7 and the Short answer; the same GLM ranges also appear in Part B §3 D) |
| `weekly_progress/091626/latency_breakdown.md` · §3 E Agent runs per task and end-to-end time | Part A §2.8 and §A.W (was §3 E, closing paragraph) | table dropped (duplicate of Part A §2.8 and the Short answer); closing 'Totals over 24 runs' paragraph kept (context preparation 32 s and wall 3,946 s are not in Part A) |
| `weekly_progress/091626/latency_breakdown.md` · §4 How prefill and decode were separated (probes, 2026-09-13) | Part A §2.5.1 and §A.W (was §4) | partly kept: the three-reasons list and the prefill-share paragraph go to §A.W (first bullet kept only to keep the list whole); the header facts, method paragraph, probe table and the two further facts are dropped (duplicate of Part A §2.5.1) |
| `weekly_progress/091626/latency_breakdown.md` · §5 Why the context-preparation share is small here, when earlier runs showed 52-80% redundancy | Part A §A.W (was §5) | kept (whole section) |
| `weekly_progress/091626/latency_breakdown.md` · §6 Conclusions | Part A §A.W (was §6) and Part A Short answer, §3, §4 | conclusions 2, 5, 6, 7 and the closing analyzer-fixes paragraph kept in §A.W; conclusions 1, 3, 4 dropped (duplicate of Part A Short answer and §3 items 1, 2, 7); caveats paragraph dropped (duplicate of Part A §4) |
| `weekly_progress/092326/latency_qwen_vs_glm.md` · (title and opening) | Part B (opening) | kept |
| `weekly_progress/092326/latency_qwen_vs_glm.md` · §1 Question | Part B §1 | kept |
| `weekly_progress/092326/latency_qwen_vs_glm.md` · §2 Setup | Part B §2 | kept |
| `weekly_progress/092326/latency_qwen_vs_glm.md` · §3 Results (team mode: GLM -> Qwen; means over pooled rounds or calls unless noted) | Part B §3 | kept |
| `weekly_progress/092326/latency_qwen_vs_glm.md` · §3 A One round | Part B §3 A | kept |
| `weekly_progress/092326/latency_qwen_vs_glm.md` · §3 B Inside the agent call | Part B §3 B | kept |
| `weekly_progress/092326/latency_qwen_vs_glm.md` · §3 C Tokens per call | Part B §3 C | kept |
| `weekly_progress/092326/latency_qwen_vs_glm.md` · §3 D Teammate input redundancy | Part B §3 D | kept |
| `weekly_progress/092326/latency_qwen_vs_glm.md` · §3 E Agent runs per task and end-to-end time | Part B §3 E | kept |
| `weekly_progress/092326/latency_qwen_vs_glm.md` · §3 F Stability | Part B §3 F | kept |
| `weekly_progress/092326/latency_qwen_vs_glm.md` · §3 G Solo arm | Part B §3 G | kept |
| `weekly_progress/092326/latency_qwen_vs_glm.md` · §4 Conclusions | Part B §4 | kept |
| `weekly_progress/092326/latency_qwen_vs_glm.md` · §5 Caveats | Part B §5 | kept |

## Cleanup notes

- Part A Appendix B, last line: flag appended — the GLM runs' `<label>.console.log` files are not in the repository (`*.log` is git-ignored) and not on this host.
- Part A §A.W: kept from the digest only what Part A does not already state with the same numbers — the preamble (when §4 and §5 were added), the "Totals over 24 runs" paragraph (context preparation 32 s and wall 3,946 s are new; the 4,704 s and 1,364 s beside them repeat Part A), the three reasons why streaming timing is not a prefill measure (the first bullet duplicates Part A §2.5 and is kept only so the list stays whole), the prefill-share-by-prompt-size paragraph, the whole of §5 (50k against 512k limit: 33-65 rounds, 363-807 s, 18-45 shrink events), conclusions 2, 5, 6 and 7, and the analyzer-fixes paragraph.
- Part A §A.W, digest conclusion 2: kept rather than dropped because its 0.19 s (5k tokens at the sweep's 0.038 ms per token) differs from Part A §3's 0.17 s (at the regression's 0.033 ms per token); both are right for their slope, so no correction note.
- Dropped as duplicates of Part A (same finding, same numbers): the digest's §1, §2, §3 A–D, the §3 E table, most of §4 (including the regression line "3.7 s + 0.033 ms x uncached + 17.4 ms x output (r2 0.96)" and the probe table), conclusions 1, 3 and 4 and the caveats paragraph.
- In §A.W the digest's cross-references keep the digest's numbering: “section 3” in the was-§5 block is the digest's §3 (its content is Part A §2.2–§2.8), and the preamble's “Sections 4 and 5” are the blocks labelled was §4 and was §5.
- No numbers of Part A or Part B were changed.
