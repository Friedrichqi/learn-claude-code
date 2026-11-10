# Topic 22: does the KV-threshold `wait` prompt help MiniMax-M2.7 serve 4-32 concurrent benchmark agents?

## Context

**The ask (2026-11-01):** run live profiling with the final `wait` prompt, for N = 4, 8, 16 and 32 concurrent
agents, on a mixed random subset of SWE-bench Verified, SWE-bench Pro and Terminal-Bench. Find the benefit of the
`wait` tool.
- **Per request (LLM call):** latency avg, p50, p95, p99.
- **Per entry (task):** end-to-end latency avg, p50, p95, p99.
- **vLLM throughput:** requests/h and tokens/s.

**Background:**
- **The final prompt (topic 19 README):** `S15_WAIT_TOOL=1 S15_WAIT_TEXT=v5 S15_SERVER_SECTION=wait_kv
  S15_STATUS_LINE=1 S15_STATUS_FORMAT=kv_cue S15_WAIT_KV=0.8`.
- **What topic 19's replay showed:** it works when the context does not show the rule ignored: 58 % of replies wait
  at 93 % KV, 0 % at 62 %. The user rules wait-only replies acceptable. The live effect is unknown.
- **Earlier live results:**
  - Topic 18: Qwen's model-called waits cost throughput (0.7-0.9×).
  - Topic 19: the server-side gate gave 1.42-1.44×.

**User decisions (2026-11-01):**
- 96 tasks per run, about 70 GPU-h.
- A fresh seeded draw from the full benchmark sets.

**What exists:**
- **Topic 16's `research/16_epic_harness/harness_eval`** runs these benchmarks live with s15 through Harbor 0.24.
  - The adapter is `adapter/harbor_agent.py:S15Agent` plus `adapter/s15_child.py`.
  - Each trial records `agent/s15/calls.jsonl` (`ms` per call, usage), s15 traces with absolute times, and Harbor's
    `result.json` (phase timestamps, reward).
  - Its docker-in-docker setup and eval image are torn down.
  - Its s15 copy (10-12) has no `hold_runtime.py`, and its `KEEP_TOOLS` would deny `wait`.
  - It has no driver that holds a fixed N.
- **Topic 18's `wt_server_ext.Middleware`** serves the stock `/wt/status` (`kv_usage`, `waiting`). It is
  observe-only and needs only `--middleware` plus `PYTHONPATH`.
- **Topic 11's `mu_scrape.py`** scrapes `/metrics` at 1 Hz.
- **Topic 20's `rd_serve.run_stage`** provides the claim check, the cap and the GPU-hour log.

## Design

**Runs:** 8 = N ∈ {4, 8, 16, 32} × condition:
- **`none`:** s15 with pacing off: no `wait` tool, no `[server]` line, no paragraph.
- **`wait`:** the final prompt above.

Both run on the same server.

**Tasks:** 96 = 32 SWE-bench Verified + 32 SWE-bench Pro + 32 Terminal-Bench 2.1.
- **The draw:** seed 2211, from harness_eval's full local sets (500 / 642 / 89), with ordered reserves.
- **Excluded:**
  - Pro protonmail tasks (19.6 GB images);
  - Terminal-Bench disk-heavy tasks (`run/state/disk_heavy_tasks.txt`);
  - the 2 Terminal-Bench oracle failures (qemu-startup, mcmc-sampling-stan);
  - Terminal-Bench tasks with an agent timeout over 1,800 s.
- **The oracle check:** Harbor's `oracle` agent applies each reference solution, which also pulls the image.
  Failures are replaced from the reserves, then the list is frozen.
- **Queue:** one seeded order that interleaves the benchmarks, identical in all 8 runs.

**Exactly N agents at once:**
- **One Harbor job per run,** with `-n N + max(2, N/4)`. The extra trials prepare their environments ahead, so a
  free slot refills within seconds.
- **The gate:** topic 22's agent class holds an N-slot gate. A trial enters its agent phase only with a slot and
  releases it when the agent exits, before the verifier.
- **Timeouts:** Harbor's `--agent-timeout-multiplier` is set high, so time spent waiting at the gate never counts.
  The agent class enforces each task's own limit, its `task.toml` agent timeout × 2 (as harness_eval's e3), counted
  from slot acquisition.
- **Agent settings:** as harness_eval's MiniMax: 250 lead calls, window 131,072, `max_tokens` 32,000 (escalated
  64,000), `call_timeout` 1,800 s.

**Server:** one fresh server per run, so each starts with a clean prefix cache.
- MiniMax-M2.7, TP=4 on GPUs 0-3 (pinned to CPU socket 0 by `rd_serve`).
- harness_eval's flags: `--gpu-memory-utilization 0.92 --max-model-len 131072 --seed 0 --enable-auto-tool-choice
  --tool-call-parser minimax_m2 --reasoning-parser minimax_m2_append_think --enable-prefix-caching
  --enable-prompt-tokens-details`.
- **Plus:**
  - `--max-num-seqs 64`; at 16, N=32 would queue at the server;
  - topic 19's append-only template, as in topics 19-21 and harness_eval's e4 control;
  - `--middleware wt_server_ext.Middleware`, with `research/18_harness_wait` on `PYTHONPATH`.
- **Task containers:** they run in docker-in-docker pinned to CPU socket 1 (52-103, 156-207).

**Order:**
1. the smoke run;
2. N=32 (wait, none);
3. N=16 (none, wait);
4. N=8 (wait, none);
5. N=4 (none, wait).

High N comes first, where an effect is expected, and the first condition alternates.

## Measures (`wb_analyze.py` → `data/tables/wb.{md,json}`)

| measure | definition | statistics |
|---|---|---|
| request latency | each LLM call's duration as the agent sees it (`calls.jsonl` `ms`: send → full non-streamed reply); waits fall between calls | avg, p50, p95, p99, over all calls in the full-N window (primary) and the whole run; also by benchmark |
| entry e2e latency | per task, from slot acquisition to agent exit: model calls, tool runs and waits | avg, p50, p95, p99 over the 96 tasks; `wait` − `none` paired by task |
| requests/h | Δ `vllm:request_success_total` / Δt, from the 1 Hz `/metrics` scrape | full-N window (primary) and whole run |
| tokens/s | Δ(`prompt_tokens_total` + `generation_tokens_total`) / Δt, plus generation tokens/s alone | the same |

- **Full-N window:** from the first moment N agents hold slots until the queue is empty, i.e. when the last task takes
  a slot. Taken from the gate's log.
- **Also reported:**
  - trial time (environment setup + agent + verifier);
  - tasks/h;
  - pass rate (reward), so it is visible if waiting costs accuracy;
  - waits per task and seconds waited;
  - the share of calls with `wait`;
  - wait-only calls;
  - KV use, prefix-cache hit rate and preemptions over time;
  - tool time;
  - effective concurrency (the time-average of slots held);
  - tasks that hit their limit.
- **Intervals:** 95 % bootstrap. Requests are clustered by task, entries are paired by task, and throughput uses
  10-min blocks.
- **Replicates:** one run per cell. The pre-registration fixes the analysis before the first GPU run. The summary
  names which metrics differ with an interval that excludes 0, at each N.

## Build: CPU work in a new folder `research/22_wait_bench/` (topic 16's files stay untouched)

1. **Docker-in-docker,** per `harness_eval/run/dind.md`:
   - container `s15-dind`, data root `/data/fritz/docker`, socket `/data/fritz/dind-run/docker.sock`;
   - the MSS clamp;
   - `--cpuset-cpus 52-103,156-207`.

   Then build `s15-eval` with `docker/build.sh`. The code is mounted at runtime, so the image needs no change.
2. **`vendor/s15/`:** a pinned copy of today's `s15_integrated_harness/` (`code.py`, `hold_runtime.py` and what they
   import). Topic 16's 10-12 copy stays as it ran.
3. **`wb_agent.py`:** a subclass of `S15Agent` that adds:
   - the N-slot gate (an asyncio semaphore in the Harbor process, or file locks if Harbor runs trials in several
     processes);
   - a gate log (wait, acquire, release);
   - the per-task limit from acquisition;
   - the child pointed at the vendor copy and `wb_child.py`.
4. **`wb_child.py`:** runs harness_eval's `s15_child.py` with `wait` added to `KEEP_TOOLS`. The condition's `S15_*`
   settings reach it through `env_file`.
5. **`wb_tasks.py`:** the seeded draw with its exclusions, and the oracle check through `run/eval.sh`. Output: the
   frozen `data/tasks.json` with the queue order.
6. **`wb_serve.py`:** `rd_serve.run_stage` with a `bench` config (the server above, plus `PYTHONPATH`), pointed at
   topic 22's `data/gpu_hours.jsonl`, server folders and cap, as `pw_serve.configure` does. Its checks:
   - GPUs 0-3 empty;
   - the sheet claim for every 30-min slot;
   - the cap.
7. **`wb_cell.py run --n N --cond C`:** one stage (server, health check, client). It writes `cell.json` with the
   times, N and the condition. The client is:
   - `mu_scrape.py` at 1 Hz;
   - the Harbor job over the frozen queue, through `run/eval.sh` with the topic folder mounted.
8. **`wb_analyze.py`:** the measures above. **`test_wb.py`** covers:
   - the gate (never more than N, refills, the limit counts from acquisition);
   - the child against a fake vLLM plus `/wt/status`: `wait` and the line appear only under `wait`;
   - the analysis on synthetic trial folders: percentiles, the full-N window, throughput deltas.
9. **Pre-registration:** `prereg.md` + `.sha256`, frozen after the smoke run and before run 1. It covers the tasks,
   the order, the measures, the windows and the code.

## GPU

**The smoke run** (about 1 h, about 3-4 GPU-h), on 12 reserve tasks:
- **N=4 `wait` (8 tasks), then N=4 `none` (4 tasks).**
- **Checks:**
  - under `wait` the requests carry the paragraph, the v5 tool and the `[server]` line; under `none` none of them;
  - waits run and are logged;
  - the gate keeps N and the queue order holds;
  - the scrape works;
  - the analysis runs end to end.

**The 8 runs** take about 6-11 GPU-h each, tails included: about 70 GPU-h, about 18 h of GPU time.
- **Cap:** 75 GPU-h in topic 22's log.
- **Stop rule:** if the projected total passes the cap, stop after the current block and ask.
- **`--hours` per stage:** 3.5 for N=4, 2.5 for the others. A stopped Harbor job resumes, and finished trials are
  skipped.

**Claims:** GPUs 0-3 only, after the user enters each stage's span in the sheet as Yijiahao. Avoid the 1-2 AM hour
of today's daylight-saving change.

## Verification

- **CPU:**
  - `test_wb.py` passes;
  - the oracle check passes on all 96;
  - every smoke check holds;
  - the topic 19 and harness tests still pass.
- **Each run:**
  - all 96 tasks reach an end state, with infrastructure errors under 2 %;
  - call counts match vLLM's request counter within 1 %;
  - the scrape covers the run;
  - GPUs 0-3 are empty afterwards.
- **The analysis** reproduces from files. A spot check recomputes one run's percentiles by hand.

## Docs and rules

- **Docs:**
  - `research/22_wait_bench/README.md` with the results;
  - `PLAN.md` (a copy of this plan);
  - the `research/README.md` row;
  - memory.
- **Rules:**
  - No commits.
  - Never say "arm".
  - Every setup name gets a gloss.
  - GPUs 0-3 only; the user enters the claims.
  - No edits to topic 16's or other topics' frozen files.
  - Remove only images we pulled, never prune.
