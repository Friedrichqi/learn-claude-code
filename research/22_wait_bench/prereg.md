# Pre-registration: topic 22, the KV-threshold `wait` prompt with 4-32 concurrent MiniMax-M2.7 agents

Frozen after the smoke run and before the first of the 8 runs.
- `prereg.sha256` (`wb_cell.py freeze`) lists, with repo-relative paths:
  - this file and topic 22's code;
  - the pinned s15 copy (`vendor/s15`);
  - `data/tasks.json` and the queue (link names and targets);
  - the shared files the server, the harness and the analysis use (`rd_serve.py`, `wt_server_ext.py`,
    `mu_scrape.py`, the template, harness_eval's adapter).
- `wb_cell.py run` refuses a main-queue run if any of them changed.
- Plan: `PLAN.md`; design details: `README.md`.

## Question

What does topic 19's final `wait` prompt change when N = 4, 8, 16 or 32 MiniMax-M2.7 agents share one vLLM server,
for request latency, entry (task) latency and throughput? The prompt is wait text v5 + paragraph `wait_kv` + the
kv_cue `[server]` line at 80 %.

## Runs

| run | N | setup | Harbor `-n` |
|---|---|---|---|
| `n32_wait`, `n32_none` | 32 | `wait`, `none` | 48 |
| `n16_none`, `n16_wait` | 16 | `none`, `wait` | 24 |
| `n8_wait`, `n8_none` | 8 | `wait`, `none` | 12 |
| `n4_none`, `n4_wait` | 4 | `none`, `wait` | 8 |

- **Order:** as listed. High N runs first, where an effect is expected, and the first setup alternates.
- **The setups:**
  - `none`: s15 with pacing off;
  - `wait`: `S15_WAIT_TOOL=1 S15_WAIT_TEXT=v5 S15_SERVER_SECTION=wait_kv S15_STATUS_LINE=1
    S15_STATUS_FORMAT=kv_cue S15_WAIT_KV=0.8`.
- **Every run:**
  - a fresh `bench` server (`wb_cell.py`);
  - the same 96-task queue (`data/queue/main`: 32 SWE-bench Verified, 32 SWE-bench Pro, 32 Terminal-Bench 2.1, all
    passing their reference solution);
  - `--n-concurrent-agents N`, `--agent-timeout-multiplier 2`, one attempt per task;
  - the same agent settings and sampling (the server's defaults, seed 0).

## Measures (`wb_analyze.py`; definitions as in the README)

| measure | statistics | over |
|---|---|---|
| request latency (s) | avg, p50, p95, p99 | the model calls that start in the full-N window |
| entry latency (s) | avg, p50, p95, p99 | the 96 tasks' agent phases |
| requests/h, tokens/s, generation tokens/s | rate | vLLM counters over the full-N window |

**Also reported:**
- the same measures over the whole run, and by benchmark;
- pass rate and limit hits;
- running agents;
- KV use and the time at ≥ 80 %;
- prefix-cache hit rate and preemptions;
- waits (calls with `wait`, wait-only calls, waits and seconds per task);
- tool and model seconds per task.

## Readings at each N (`wait` − `none`)

- **Request latency:** each statistic's paired difference. The task is resampled, with the same draw for both runs.
- **Entry latency:** each statistic's paired difference over the tasks both runs finished.
- **Throughput:** the `wait`/`none` ratio, with an interval from the two runs' 10-min blocks resampled
  independently.
- **Labels:** a difference is called lower or higher when its 95 % interval excludes 0 (a ratio when it excludes 1).
  Otherwise it is "no clear difference".
- **The summary per N** names:
  - which of request p50/p95/p99, entry avg/p50/p95/p99, requests/h and tokens/s are lower or higher;
  - the pass-rate difference (paired by task, 95 % interval).

  A pass-rate drop whose interval excludes 0 is flagged as a cost of waiting. There is no single pass/fail.

## Validity of a run

- All 96 tasks reach an end state: done, limit or error.
- Infrastructure failures (any exception other than `AgentTimeoutError`) affect at most 2 tasks. Otherwise those
  trials are rerun (Harbor resumes the job) before the analysis, and the rerun is logged.
- N agents ran at once at some point.
- GPUs 0-3 are empty after the stage.

## Stages

- **One GPU stage per run,** chained by `wb_chain.py` in the order above. `--hours` is 3.0 for N = 32, 16 and 8,
  and 4.0 for N = 4.
- **A run its stage stops at the time limit** is resumed once, in a new stage:
  - the client first removes the trials the stop cancelled, which Harbor would otherwise count as done, so those
    tasks run again;
  - throughput sums each server's own counter deltas;
  - the gap between the stages is not counted.
- **The smoke run (N=4, `wait`, 12 reserve tasks):**
  - 1.09 GPU-h;
  - no call errors;
  - every round carried the `[server]` line;
  - KV use averaged 15 % and never reached 80 %.

  So at N=4 the rule is expected to fire rarely, if at all.

## Budget and stop rule

- **Cap:** 75 GPU-h in `data/gpu_hours.jsonl`, the smoke run included.
- **Stop rule:** after each block (both setups of one N), project the rest from the measured run times. If the
  projection passes the cap, or the remaining claim cannot hold the next block, stop and ask the user.
- **GPUs:** 0-3 only, inside the user's sheet claims.

## Known limits

- **One run per cell.** Trajectories diverge between runs (sampling), so the paired task differences include chance.
- **The tail:** with 96 tasks, the p99 per task is near the slowest task.
- **When the rule fires:** only when the KV cache is 80 % full or more. At N=4 it may rarely be.
- **The comparison is the whole package:** tool, paragraph and line, not the tool alone. `none` has no status line.
- **Narrow scope:** one model, one server size (277k-token KV pool), one task mix.

Any change after the freeze is logged in the README's Changes, with its time and reason.
