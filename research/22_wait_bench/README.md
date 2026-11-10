# 22 · Does the KV-threshold `wait` prompt help MiniMax-M2.7 serve 4-32 concurrent benchmark agents?

> **Plan:** `PLAN.md` (approved 2026-11-01). **Pre-registration:** `prereg.md` (frozen after the smoke run).
>
> **Status:** done (2026-11-02 03:01 UTC). All 8 runs are valid; 83.34 GPU-h on GPUs 0-3 (`data/gpu_hours.jsonl`).
> Results below. Docker-in-docker and both images are removed (03:13 UTC). Nothing committed.

## Question

The user asked (2026-11-01) for live profiling of topic 19's final `wait` prompt. N = 4, 8, 16 and 32 concurrent agents
work on a mixed random subset of SWE-bench Verified, SWE-bench Pro and Terminal-Bench. Measured:
- **Request latency:** avg, p50, p95 and p99 per model call.
- **Entry latency:** avg, p50, p95 and p99 end to end per task.
- **vLLM throughput:** requests/h and tokens/s.

The question is what introducing the `wait` tool buys.

**The prompt** (topic 19 README, "The user's re-reading and the final prompt"): `S15_WAIT_TOOL=1 S15_WAIT_TEXT=v5
S15_SERVER_SECTION=wait_kv S15_STATUS_LINE=1 S15_STATUS_FORMAT=kv_cue S15_WAIT_KV=0.8`.
- **The tool:** a `wait(seconds)` tool whose description states the rule: wait when the KV cache is 80 % full or more.
- **The system paragraph:** the same rule.
- **The `[server]` line:** appended to the newest user message every round, e.g. `KV cache 93% full; 12 requests
  waiting. At 80% or more: add a wait call to your response.`

**What topic 19's replay showed:** the rule works when the context does not show it ignored. With a clean history,
58 % of work replies waited at 93 % and 0 % at 62 %. This is the first live test.

## Setups

| setup | what the agents get |
|---|---|
| `none` | s15 with pacing off: no `wait` tool, no `[server]` line, no paragraph (harness_eval's MiniMax baseline) |
| `wait` | the final prompt above |

**Runs:** `n<N>_<setup>` for N ∈ {4, 8, 16, 32}, 8 in all. Each run works through the same 96-task queue to the end.

## Results

**All 8 runs are valid.** In each one:
- all 96 tasks reached an end state, with no infrastructure failures;
- exactly N agents ran at the peak.

**How to read the tables:**
- **Units:** seconds unless noted.
- **Window:** request latency and throughput are over the full-N window (all N agents running, until the queue is
  empty).
- **Differences:** `wait` − `none`, paired by task, with 95 % bootstrap intervals.
- **Replicates:** one run per cell.

**Tables:**
- **Pre-registered:** `data/tables/wb.md`.
- **Post hoc:** `wb_waits.md` (waits by the KV use the model saw), `wb_mix.md` (request latency without wait-only
  calls) and `wb_numad.md` (numad's moves).

**What `wait` did.**

| N | time at KV ≥ 80 %, `none` → `wait` | calls with `wait` | wait-only calls | s waited per task | calls that saw ≥ 80 % and waited | calls that saw < 80 % and waited |
|---|---|---|---|---|---|---|
| 4 | 0 → 0 % | 0 % | 0 % | 0 | - (no call saw 80 %) | 0 of 3,355 |
| 8 | 1 → 4 % | 1.0 % | 0.5 % | 4 | 58.7 % of 46 | 0.2 % |
| 16 | 71 → 35 % | 13.4 % | 8.2 % | 119 | 76.5 % of 591 | 0.8 % |
| 32 | 93 → 71 % | 29.0 % | 16.9 % | 354 | 64.9 % of 1,651 | 3.9 % |

**Request latency** (one model call, send → full reply).

| N | `none` avg / p50 / p95 / p99 | `wait` avg / p50 / p95 / p99 | − avg | − p50 | − p95 | − p99 |
|---|---|---|---|---|---|---|
| 4 | 4.2 / 1.9 / 13.8 / 31.8 | 5.1 / 2.1 / 19.7 / 44.6 | +0.9 [0.2, 1.8] | +0.2 [−0.1, 0.5] | +5.8 [1.4, 11.5] | +12.8 [4.1, 25.6] |
| 8 | 5.4 / 2.3 / 19.7 / 36.3 | 7.0 / 3.0 / 27.8 / 57.3 | +1.7 [0.5, 3.3] | +0.7 [0.4, 1.1] | +8.0 [0.8, 18.0] | +21.1 [2.3, 49.2] |
| 16 | 13.8 / 7.7 / 44.7 / 91.6 | 8.8 / 3.8 / 31.5 / 62.2 | −5.0 [−7.3, −3.0] | −3.9 [−5.5, −2.3] | −13.2 [−22.5, −2.2] | −29.4 [−56.5, −4.7] |
| 32 | 24.9 / 19.1 / 56.3 / 115.4 | 13.5 / 7.6 / 42.5 / 90.4 | −11.5 [−13.4, −9.6] | −11.5 [−12.9, −10.5] | −13.7 [−23.0, −3.0] | −24.8 [−63.2, 10.6] |

**Entry latency** (one task's agent phase, end to end: model calls, tools and waits).

| N | `none` avg / p50 / p95 / p99 | `wait` avg / p50 / p95 / p99 | − avg | − p50 | − p95 | − p99 |
|---|---|---|---|---|---|---|
| 4 | 331 / 131 / 1,677 / 3,600 | 300 / 108 / 1,555 / 1,890 | −31 [−118, 53] | −22 [−60, 9] | −122 [−731, 745] | −1,710 [−1,933, 1,710] |
| 8 | 329 / 140 / 1,645 / 3,600 | 364 / 159 / 1,199 / 3,600 | +35 [−27, 106] | +18 [−7, 65] | −447 [−868, 1,743] | 0 [−476, 1,800] |
| 16 | 636 / 305 / 2,066 / 3,478 | 561 / 241 / 2,148 / 3,600 | −75 [−202, 47] | −64 [−144, −11] | +82 [−1,046, 1,099] | +122 [−1,347, 1,311] |
| 32 | 952 / 660 / 2,297 / 3,600 | 947 / 578 / 3,187 / 3,685 | −4 [−173, 167] | −82 [−267, 137] | +889 [−414, 1,531] | +85 [−145, 2,603] |

**vLLM throughput.**

| N | requests/h, `none` → `wait` | ratio | tokens/s, `none` → `wait` | ratio | generation tokens/s | prefix-cache hit rate | preemptions/min |
|---|---|---|---|---|---|---|---|
| 4 | 1,815 → 1,748 | 0.96 [0.77, 1.24] | 10,060 → 10,794 | 1.07 [0.87, 1.40] | 193 → 217 | 0.52 → 0.56 | 0 → 0 |
| 8 | 3,061 → 2,836 | 0.93 [0.78, 1.15] | 17,198 → 15,971 | 0.93 [0.77, 1.07] | 327 → 338 | 0.65 → 0.58 | 0 → 0 |
| 16 | 3,573 → 3,580 | 1.00 [0.62, 1.55] | 19,239 → 19,252 | 1.00 [0.78, 1.28] | 378 → 371 | 0.36 → 0.49 | 0.4 → 0.1 |
| 32 | 3,770 → 4,201 | 1.11 [0.67, 1.49] | 19,256 → 20,593 | 1.07 [0.86, 1.23] | 343 → 353 | 0.09 → 0.30 | 1.0 → 0.4 |

**Pass rate,** `none` → `wait` (difference in points):
- N=4: 80 → 73 % (−7.3 [−15.6, 0.0]);
- N=8: 76 → 73 % (−3.1 [−10.4, 4.2]);
- N=16: 69 → 69 % (0.0 [−8.3, 8.3]);
- N=32: 70 → 74 % (+4.2 [−3.1, 11.5]).

**Reading:**
- **The model follows the rule live.** 59-77 % of the calls whose `[server]` line showed 80 % or more waited, and at
  most 4 % below. In topic 19's live runs, by contrast, MiniMax ignored the earlier signals (3 waits in 21,000 calls).
- **Where the server is crowded (N=16 and 32), each model call gets faster, but nothing downstream improves.**
  - **What the waits changed:** they kept the KV cache under 80 % for more of the time, with fewer evictions (prefix
    hit rate 0.49 vs 0.36 and 0.30 vs 0.09) and fewer preemptions.
  - **Calls:** the calls that ran were faster: p50 −3.9 s at N=16 and −11.5 s at N=32.
  - **Tasks:** the agents spent the time they saved waiting. Entry latency did not change clearly; the one clear
    difference is N=16's p50, −64 s [−144, −11].
  - **The server:** it did not serve clearly more requests or tokens per hour (×1.00 and ×1.11, intervals from 0.6 to
    1.5).
  - **Pass rates** did not move.
  - **Compared with topic 19:** there, in its own workload, the server-side gate gave 1.42-1.44×.
- **Part of the drop in request latency is the call mix** (`wb_mix.py`, post hoc).
  - **Why:** wait-only calls are short. At N=32 the median is 5.4 s and about 60 generated tokens, against 19.1 s for
    a `none` call.
  - **N=32 without them:** the avg and p50 drops hold (−9.9 [−12.0, −7.6] and −10.5 [−12.0, −9.3]). The p95 and p99
    drops become unclear (−6.4 [−17.6, 6.4] and −6.2 [−54.9, 21.0]).
  - **N=16 without them:** the drops hold (p99 −28.4 [−54.7, −2.3]).
- **Where the server is not crowded (N=4 and 8), `wait` hardly waits, and its calls were slower.**
  - **How little it waited:** the cache was at 80 % or more 0 % and 4 % of the time, and `wait`'s agents waited in 0
    % and 1 % of calls.
  - **What was slower, with no wait to cause it:** all four request statistics at N=8, and avg, p95 and p99 at N=4.
  - **Where the gap sits:** in Terminal-Bench calls, which generated about 30 % more tokens under `wait` (865 vs 640
    at N=4, 893 vs 696 at N=8). At N=16 and 32 they did not (655 vs 645 and 545 vs 554).
  - **Run-to-run spread is as large:** across the complete and the killed N=8 runs, request p50 was 2.2-2.8 s for
    `none` and 2.7-3.0 s for `wait` (Process).
  - With one run per cell, a prompt effect cannot be told from that spread.
- **Pass rates** differ clearly at no N. The largest gap is N=4's −7.3 points, whose interval reaches 0.
- **A side effect:** at N=32, one no-network SWE-bench Pro task's agent used `wait 60` as a network back-off for 88
  minutes (68 waits off the rule).
  - It made that run 115 minutes long, against 71 for `none` (Process).
  - It recurred in `n8_wait_s1` (29 waits), and only twice in `n8_wait`.

**Caveats:**
- **One run per cell.** The intervals resample tasks within a run, not runs, so they leave out run-to-run spread.
- **Placement:** the CPU placement changed between N ≥ 16 and N ≤ 8 (Design, Changes). Each N's two runs share one
  placement, but comparisons across N mix N with placement. The same `wait` setup at N=8 gave request p50 2.7 s under
  the old placement (`n8_wait_s1`) and 3.0 s under the new (`n8_wait`).
- **numad** (the host's NUMA daemon) moved vLLM's memory to the other node (`wb_numad.md`):
  - 15-23 % of `n8_none` against 1-6 % of `n8_wait`;
  - at most 3 % at N=4;
  - almost none at N ≥ 16.

  `n8_none`, the run with the most time off node 0, had the faster calls.
- **The output cap** (64 MiB per agent command, from 20:04 UTC 11-01) covered `n8_none`, `n4_none` and `n4_wait` only.
  It never fired.
- **Entry p99** is a task at its limit (3,600 s, a 30-min Terminal-Bench limit × 2) in most runs, with 4-8 limit hits
  per run.
- **The window** ends when the last task starts, so it differs between a pair's two runs.

## Design

**Tasks** (`wb_tasks.py`, `data/tasks.json`):
- **The draw:** 32 SWE-bench Verified + 32 SWE-bench Pro + 32 Terminal-Bench 2.1, seed 2211, from harness_eval's full
  local sets (500 / 587 / 67 eligible).
- **Excluded:**
  - 55 Pro protonmail tasks (19.6 GB images);
  - 22 Terminal-Bench tasks: 7 disk-heavy, 2 whose reference solution fails, 13 with an agent timeout over 30 min.
- **The check:** each task's reference solution (Harbor's oracle agent) must pass. A failure is replaced by the
  benchmark's next reserve, at the same queue position.
- **The queue:** it interleaves the benchmarks, one task of each per round, in a seeded order. It is identical in
  every run. Harbor reads `data/queue/main` (links `q001__<task>` …) in name order.

**Concurrency** (Harbor 0.24's own):
- `--n-concurrent-agents N` takes a permit when a trial's agent phase starts, before its timeout and
  `agent_execution.started_at`. It returns the permit when the agent ends, before the verifier.
- `-n N + max(4, N/2)` lets the next environments be set up meanwhile.
- **Each task's limit:** its `task.toml` agent timeout × 2 (`--agent-timeout-multiplier 2`), counted from the permit.
- **Agent settings,** as harness_eval's MiniMax: 250 lead calls, window 131,072 tokens, `max_tokens` 32,000
  (escalated 64,000).

**Server** (`wb_cell.py run`, one fresh server per run): MiniMax-M2.7, TP=4 on GPUs 0-3 (CPU socket 0 / NUMA node 0,
which hosts GPUs 0-3; its cores below).
- **Flags:** harness_eval's: `--gpu-memory-utilization 0.92 --max-model-len 131072 --seed 0
  --enable-auto-tool-choice --tool-call-parser minimax_m2 --reasoning-parser minimax_m2_append_think
  --enable-prefix-caching --enable-prompt-tokens-details`.
- **Plus:**
  - `--max-num-seqs 64`;
  - topic 19's append-only template;
  - topic 18's status middleware (`--middleware wt_server_ext.Middleware`, stock `/wt/status`). Every status read
    is logged to `data/runs/<run>/wt_ext.jsonl`.
- Both setups use the same server.

**Harness:**
- **The task containers** run in a docker-in-docker daemon (harness_eval `run/dind.md`).
- **The agents** run in harness_eval's eval image (`wb_eval.sh`, tag `b4e57657ba6d`).
- **CPU placement** (changed after N=16, see Changes). Each N's two runs share one placement.

  | runs | vLLM | the client, the agents' eval container, the task containers | memory |
  |---|---|---|---|
  | N=32, 16 (and the smoke run) | socket 0, all of it (0-51, 104-155) | socket 1 (52-103, 156-207) | not bound |
  | N=8, 4 | socket 0's cores 0-51, shared with the rest; no hyperthreads (104-155) | the same cores 0-51; each container at most 100 GB | NUMA node 0 |
- **s15:** a pinned copy of today's s15 (`vendor/s15`, `wb_vendor.sh`; `hold_runtime.py` sha `dc2af8b3…`). It runs
  through `wb_child.py`, which is harness_eval's `s15_child.py` with the pacing tools allowed past its permission
  hook.

## Measures (`wb_analyze.py` → `data/tables/wb.{md,json}`)

- **Full-N window:** from the first moment N agents run at once until the queue is empty, i.e. when the last task's
  agent starts.
- **Request latency:** a model call's duration as the agent sees it (send → full reply; calls are not streamed),
  over the calls that started in the window. Waits fall between calls.
- **Entry latency:** a task's agent phase (model calls, tools, waits), over all 96 tasks. `wait` − `none` is paired
  by task.
- **Throughput:** vLLM counter deltas over the window: requests/h (`request_success_total`), tokens/s (prompt +
  generation), and generation tokens/s.
- **Also reported:**
  - pass rate;
  - limit hits;
  - agents running in the window;
  - KV use and the share of time at ≥ 80 %;
  - prefix-cache hit rate;
  - preemptions;
  - waits per task and seconds waited;
  - wait-only calls;
  - tool time;
  - results by benchmark.
- **Intervals:** 95 % bootstrap. Tasks are resampled, and throughput resamples the window's 10-min blocks.

## Process

**Done:**
1. **Setup:** docker-in-docker started (05:10 UTC); the eval image built (`b4e57657ba6d`); `vendor/s15` pinned.
2. **Tasks:** `wb_tasks.py draw` (05:12 UTC), then `oracle` (05:13-05:34 UTC): 105 of 108 passed.
   - Three main tasks score 0 with their own reference solution: `qemu-alpine-ssh` (Terminal-Bench, queue position
     22), `protein-assembly` (Terminal-Bench, 53) and `django__django-10097` (SWE-bench Verified, 71).
   - Each was replaced by its benchmark's next reserve: `feal-differential-cryptanalysis`, `polyglot-c-py` and
     `django__django-16901`. All 3 passed.
   - Then `queue`.
3. **The dry run** against `wb_fake.py` (CPU, 05:36-05:40 UTC), on the 12 smoke tasks with N=2:
   - all 24 `wait` requests carried the v5 tool, the paragraph and the `[server]` line; none of the 12 `none`
     requests did;
   - Harbor kept at most 2 agents running and took the tasks in queue order;
   - the analysis ran end to end.
4. **The smoke run** (GPU, 05:42-05:58 UTC, 1.09 GPU-h): N=4, `wait`, on the 12 smoke tasks.
   - **The model in the harness:** real MiniMax tool calls parse, there were no call errors, and every round carried
     the line.
   - **Agents:** 4.0 running on average in the full-N window.
   - **Request latency:** p50 / p95 / p99 of 1.4 / 8.4 / 16.2 s.
   - **The server:** 3,900 requests/h and 15,000 tokens/s; KV use averaged 15 %, so there were no waits.
   - **Pass rate:** 7 of 12. The run was stopped by hand after 10 tasks, to spare the 2-task tail and to test the
     stop path.
5. **Fixes after the smoke** (see Changes), each tested on the CPU against `wb_fake.py`, including stop-then-resume.
6. **Tests:** `test_wb.py` (6 tests) passes.

7. **Freeze:** `prereg.sha256` at 06:06:09 UTC (34 entries).
8. **The chain** (`wb_chain.py`, log `data/runs/chain.log`) runs the 8 runs in the order 32 (wait, none), 16 (none,
   wait), 8 (wait, none), 4 (none, wait).
   - **`n32_wait`** (06:07-08:03 UTC, 7.79 GPU-h) is valid:
     - all 96 tasks reached an end state: 89 done, 7 at their limit, no infrastructure failures;
     - 32 agents at the peak, never more;
     - the 4,000 calls in `calls.jsonl` equal vLLM's request count;
     - the scrape covers the run; GPUs empty after.
     - One Terminal-Bench task (`model-extraction-relu-logits`) has no reward. Its agent hit the limit, then its
       verifier ran its full 15 min without a result. It counts as a fail.
   - **08:03 UTC:** the chain stopped at `n32_none`'s start on the port check (see Changes) and restarted at
     08:05 UTC.
   - **`n32_none`** (08:06-09:18 UTC, 4.90 GPU-h) is valid:
     - all 96 tasks reached an end state: 88 done, 8 at their limit, no infrastructure failures;
     - 32 agents at the peak;
     - GPUs empty after.
   - **After the N=32 block** the stop rule projected 51.88 of 75 GPU-h, so the chain went on to N=16 at 09:19 UTC.
   - **Post hoc** (`wb_waits.py`, not in `prereg.md`):
     - **Rule following:** in `n32_wait`, 64.9 % of the calls whose newest `[server]` line read 80 % or more included
       `wait`, against 3.9 % below 80 %.
     - **The off-rule waits** (91 calls):
       - 68 come from one SWE-bench Pro task (`teleport-7744f72c6`, queue position 82). Its agent phase has no
         network by the task's design (`[agent] network_mode = "no-network"`). Its agent read the failed module
         download as a flaky network and alternated `go build` with `wait 60` ("Waiting for network to stabilize")
         until its 250-call budget ran out: 88 min, 4,215 s of it waiting.
       - The rest are mostly waits at 74-79 %.
     - **The `none` run:** the same task's agent gave up on the download and finished in 17 min. Both runs score it
       0.
     - **The effect on the run:** this one task makes `n32_wait`'s run 115 min against `n32_none`'s 71 min.
   - **`n16_none`** (09:20-10:47 UTC, 5.90 GPU-h) is valid:
     - all 96 tasks reached an end state: 90 done, 6 at their limit, no infrastructure failures;
     - 16 agents at the peak;
     - GPUs empty after.
     - Its 1 call error is a context overflow (HTTP 400, past 131,072 tokens) in `path-tracing-reverse`. The harness
       flags it as an overflow and goes on.
   - **`n16_wait`** (10:48-12:18 UTC, 6.07 GPU-h) is valid:
     - all 96 tasks reached an end state: 90 done, 6 at their limit, no infrastructure failures;
     - 16 agents at the peak;
     - GPUs empty after.
     - Post hoc: 76.5 % of the calls whose newest line read 80 % or more included `wait`, against 0.8 % below 80 %.
       There was no long off-rule loop this time; the most in one trial was 8 calls.
   - **After the N=16 block** the stop rule projected 50.41 of 75 GPU-h, so the chain went on to N=8 at 12:18 UTC.
   - **`n8_wait`** (12:18-15:06 UTC, 11.18 GPU-h) was valid but is superseded, kept as `n8_wait_s1`:
     - all 96 tasks reached an end state: 88 done, 8 at their limit; 8 agents at the peak.
     - Its KV use was at 80 % or more only 4 % of the time.
     - The same no-network task again looped on `wait` ("Persistent network issues"): 29 off-rule waits asking
       5,995 s.
   - **15:06-16:39 UTC:** `n8_none` ran until the user's rulings (Changes). It was stopped after 93 min and kept as
     `n8_none_s1_partial`; it is not analyzed.
   - **16:43 UTC:** the chain restarted on socket 0, starting with a rerun of `n8_wait`.
   - **16:45 UTC:** stopped again for the user's tighter limits (Changes). The 3-minute partial is kept as
     `n8_wait_split_partial`. The chain restarted at 16:47 UTC.
   - **16:52 UTC:** stopped again: `taskset` did not hold vLLM on 0-51 (Changes). The 5-minute partial is kept as
     `n8_wait_ht_partial`. The chain restarted at 16:53 UTC, with the stage holding vLLM's threads on 0-51.
   - **`n8_wait`** (16:53-18:51 UTC, 7.84 GPU-h) was valid:
     - all 96 tasks reached an end state: 92 done, 4 at their limit, no infrastructure errors; 8 agents at the peak;
     - KV use was at 80 % or more 4 % of the time, as in `n8_wait_s1`;
     - the no-network task waited twice, not 29 times;
     - numad moved its vLLM processes 136 times in its first 31 minutes (Changes, 18:51 UTC).
   - **`n8_none`** (18:51-19:52 UTC, 4.07 GPU-h) was killed after 61 minutes, with 81 tasks done: an agent's command
     flooded Harbor's memory (Changes, 19:52 UTC). Kept as `n8_none_oom_partial`; not analyzed.
   - **20:04 UTC:** the chain restarted with a cap on each command's output, rerunning `n8_none` from the start.
   - **`n8_none`** (20:04-21:58 UTC, 7.57 GPU-h) is valid:
     - all 96 tasks reached an end state: 91 done, 5 at their limit, no infrastructure errors; 8 agents at the peak;
       GPUs empty after;
     - `model-extraction-relu-logits` again has no reward (its verifier ran its full 15 min), a fail as in `n32_wait`;
     - the output cap never fired (no `exec_cap.jsonl`);
     - numad moved its vLLM processes 331 times, 166 of them to node 1, in 42 of its 112 minutes. Each process had its
       memory on node 1 for 15-23 % of the run, against 1-6 % in `n8_wait` (`wb_numad.py`).
   - **Interim N=8** (`data/tables/interim_n8.md`), `wait` − `none`:
     - **request latency:** avg / p50 / p95 / p99 +1.7 / +0.7 / +8.0 / +21.1 s, all four intervals above 0;
     - **entry latency and throughput:** unclear (requests/h × 0.93 [0.78, 1.15]);
     - **pass rate:** −3.1 points [−10.4, 4.2];
     - **waits:** 1.0 % of `wait`'s calls; KV use was at 80 % or more 4 % of the time, against 1 % under `none`.
   - **Post hoc, how much one N=8 run varies** (not in `prereg.md`; partial runs, so rough):
     - **request p50:** the two killed `none` runs give 2.8 s (`n8_none_s1_partial`, old placement) and 2.2 s
       (`n8_none_oom_partial`); the two `wait` runs give 2.7 s (`n8_wait_s1`) and 3.0 s;
     - **per call:** in `n8_none` the median call took 14.2 s per 1,000 tokens it generated, against 15.4-16.2 s in
       the other four.
   - **`n4_none`** (21:58 UTC-00:24 UTC, 9.75 GPU-h) is valid:
     - all 96 tasks reached an end state: 92 done, 4 at their limit, no infrastructure errors; 4 agents at the peak;
       GPUs empty after;
     - all 96 have a reward; pass rate 80 %;
     - the output cap never fired;
     - numad moved its vLLM processes 17 times (6 to node 1) in 4 minutes. Memory was off node 0 for at most 1 % of
       the run (`wb_numad.py`; see Changes, 00:30 UTC).
   - **`n4_wait`** (00:24-03:01 UTC, 10.45 GPU-h) is valid:
     - all 96 tasks reached an end state: 92 done, 4 at their limit, no infrastructure errors; 4 agents at the peak;
       GPUs empty after;
     - all 96 have a reward; pass rate 73 %;
     - the output cap never fired;
     - the KV cache never reached 80 %, and no call waited;
     - numad moved its vLLM processes 71 times (30 to node 1) in 15 minutes. Memory was off node 0 for at most 3 % of
       the run.
9. **The analysis** (03:01-03:10 UTC 11-02):
   - `wb_cell.py check`: every frozen file was unchanged after the runs.
   - `wb_analyze.py` over the 8 main runs gave `data/tables/wb.{md,json}`; `wb_waits.py`, `wb_numad.py` and the new
     post hoc `wb_mix.py` gave the rest (Results).
   - `wb_mix.py`'s hash was appended to `prereg.sha256` (the list before it: `data/runs/prereg.sha256.before-mix`).
10. **Teardown** (03:08-03:13 UTC 11-02), per harness_eval `run/dind.md`:
    - `s15-dind` stopped and removed (no inner containers were left);
    - its data root `/data/fritz/docker` (113 task images, 128 GB) emptied through a container that mounted only that
      folder, then both folders removed;
    - the `alpine:3.23` image the guide uses was not on the host, so the dind image's own shell ran the delete,
      and nothing new was pulled;
    - the two images this topic pulled or built removed: `docker:29.4.3-dind` and `s15-eval:b4e57657ba6d`
      (rebuild with harness_eval `docker/build.sh`).

**GPU:** GPUs 0-3 only, logged in `data/gpu_hours.jsonl`.
- **Until 16:39 UTC:** a cap of 75 GPU-h, and each stage checked the user's sheet claims (Yijiahao, GPUs 0-3, Sun
  11/1 1:00 AM - midnight Eastern).
- **Since then:** no cap, and the user handles the claims (Changes).

## Changes

- 2026-11-01: plan approved. `harness_eval/docker/build.sh` rewrote topic 16's tracked `docker/CURRENT_TAG`; it was
  restored with `git checkout` right away. Topic 22 keeps its own tag in `wb_eval.sh`.
- 2026-11-01, during the build: Harbor 0.24 has its own agent limit (`--n-concurrent-agents`). It takes the permit
  before the agent's timeout starts and returns it before the verifier, so the planned custom gate in `wb_agent.py`
  was not needed; Harbor's is used.
- 2026-11-01 05:58-06:10 UTC, after the smoke run:
  - **The first stop** (by hand) showed that `rd_serve` gives a stopped client only 30 s before SIGKILL. That killed
    the docker CLI but left the eval container and 2 task containers running, and `cell.jsonl` was never written
    (the smoke's was then written by hand).
  - **Fixes:**
    1. the client writes `cell.jsonl` at its start;
    2. its stop handler stops the eval container (`docker stop -t 120`), so Harbor cancels its trials;
    3. it removes every container and Harbor network left in docker-in-docker, at its start and at its end;
    4. `wb_cell.configure` gives the stopped client 180 s;
    5. trials a stop cancelled count as done when Harbor resumes a job, so the client removes them first and they run
       again;
    6. the analysis computes throughput within each server's own counters.
  - **Tested on the CPU against `wb_fake.py`:**
    - SIGTERM to the client's process group: the client exits in 4 s and nothing is left;
    - the resume reruns the 3 cancelled trials;
    - all 12 complete.
  - `wb_chain.py` runs the 8 runs back to back, with the stop rule.
- 2026-11-01 08:03-08:05 UTC, after `n32_wait`: `n32_none`'s stage refused at once with "port 50222 is busy".
  - **Cause:** the previous server had stopped 3 s earlier, and its closed connections were still in TIME-WAIT.
    `wb_cell.fixed_port` bound without `SO_REUSEADDR`, which vLLM itself sets. So the check failed although nothing
    was listening.
  - **Fix:** `fixed_port` sets `SO_REUSEADDR`, so only a live listener is refused. Reproduced with TIME-WAIT sockets
    on the port: the old check refuses and the new one passes; a live listener is still refused.
  - **The freeze:** `wb_cell.py` was re-hashed in `prereg.sha256`, with a dated comment line. The 06:06 list is kept
    as `data/runs/prereg.sha256.frozen-0606Z`.
  - **No measure, setting or task changed.** The chain restarted at 08:05 UTC; `n32_wait` counts as done.
- 2026-11-01 09:23 UTC: added `wb_waits.py`, a post-hoc diagnostic not in `prereg.md`. It sorts each `wait` run's
  calls by the KV use on the newest `[server]` line, and each wait by the reason it gives. It was prompted by
  `n32_wait`'s long tail. It leaves the pre-registered analysis's results unchanged.
- 2026-11-01 09:25 UTC: `prereg.md` asks for each N's pass-rate difference, paired by task with a 95 % interval.
  `wb_analyze.py`'s contrast lacked it.
  - **Added:** each task's reward (no reward counts 0, as in the pass rate) and `boot_paired` over the tasks. The
    contrast table shows it as a column, in points.
  - **Tests:** `test_wb.py` gained a test of it; all 7 tests pass.
  - **The freeze:** both files were re-hashed in `prereg.sha256` with a dated line. The 06:06 analysis is kept as
    `data/runs/wb_analyze.py.frozen-0606Z`.
  - **Nothing else changed.** The N=32 request, entry and throughput results are identical.
- 2026-11-01 15:10-16:43 UTC: the 4-agent runs' projected cost passed the 75 GPU-h cap, so I asked the user.
  - **The user's rulings** (by 16:39 UTC):
    - no GPU-hour cap;
    - no sheet check: the user handles the GPU claims;
    - all of topic 22's CPU work on socket 0 / NUMA node 0, which hosts GPUs 0-3, so that other users' work on
      socket 1 is not disturbed;
    - restart the vLLM server to limit its CPU use.
  - **The placement until then:**
    - vLLM had all of socket 0 (rd_serve's `SERVER_CPUS`);
    - the client (rd_serve's `CLIENT_CPUS`), the agents' eval container and the docker-in-docker task containers ran
      on socket 1 (52-103, 156-207);
    - memory was not bound.
  - **16:39 UTC:** `n8_none` was stopped with SIGINT to its stage, which stopped Harbor, docker-in-docker and vLLM
    within 7 s.
  - **The new placement:**
    - vLLM on 0-15 and 104-119, with memory bound to node 0: `wb_cell.SERVER_CPUS` and `wb_vllm.sh`
      (`numactl --membind=0`);
    - everything else on 16-51 and 120-155 with node-0 memory:
      - the client: `wb_cell.HOST_CPUS`;
      - the eval container: `wb_eval.sh` `--cpuset-cpus --cpuset-mems 0`;
      - docker-in-docker: `docker update` on `s15-dind`. A nested container sees `16-51,120-155`, node 0;
      - the chain: `taskset` plus `numactl`.
  - **No cap and no sheet check:**
    - `CAP_GPU_H` is infinite in `wb_cell.py` and `wb_chain.py`;
    - the chain passes `--no-claim-check`.
  - **Stage time limits:** raised to 5 h (N=8) and 7 h (N=4), from 3 and 4, so no run needs a resume. A resume
    restarts its cancelled tasks from the beginning.
  - **N=8 is rerun on the new placement,** so each N's pair shares one placement:
    - the first `n8_wait` is kept as `n8_wait_s1`, and the partial `n8_none` as `n8_none_s1_partial`;
    - `n8_wait_s1` against the new `n8_wait` (the same setup on both placements) shows what the placement changes.
  - **The analysis** pairs only runs named `n<N>_<cond>`, so a superseded copy is never paired.
  - **The freeze:** re-frozen at 16:43:15 UTC (36 entries, now with `wb_vllm.sh` and `wb_waits.py`). Earlier lists
    and files are kept in `data/runs/`.
  - **Superseded 4 minutes later** by the next entry. The split above (vLLM on 0-15 and 104-119, the rest on 16-51
    and 120-155) ran only `n8_wait` from 16:43 to 16:46 UTC.
- 2026-11-01 16:45-16:47 UTC: the user set `--cpuset-cpus 0-51 --cpuset-mems 0 --memory 100g` for this session.
  - **The rerun stopped:** the `n8_wait` rerun was stopped after 3 min (SIGINT; 16 task containers removed) and is
    kept as `n8_wait_split_partial`.
  - **The containers** have those limits:
    - docker-in-docker: `docker update`, with `--memory-swap 100g`, which Docker requires with `--memory` on an update.
      The host has no swap. A nested container sees CPUs `0-51` and node 0, under the 100 GB;
    - the agents' eval container: `wb_eval.sh`.
  - **The host processes:**
    - vLLM, the stage runner, the client and the scrape run on CPUs 0-51 with node-0 memory. vLLM's memory is not
      capped;
    - the chain runs under `taskset -c 0-51 numactl --membind=0`;
    - `wb_cell.SERVER_CPUS = HOST_CPUS = "0-51"`;
    - none uses the hyperthreads 104-155 now.
  - **The freeze:** re-frozen at 16:47:01 UTC; the 16:43 list is kept as `data/runs/prereg.sha256.split-1643Z`.
- 2026-11-01 16:48-16:53 UTC: `taskset` did not hold vLLM on 0-51.
  - **What the check found:** a check of every process showed that vLLM's processes had been moved to node 0's whole
    CPU list (0-51 and the hyperthreads 104-155). At first workers TP2 and TP3 had also been moved to socket 1
    (52-103, 156-207).
    - I first blamed a compiled library under vLLM. The cause is the host's numad daemon (the 18:51 entry).
    - A hard CPU set for a host process needs the cgroup `cpuset` controller, which the user's systemd service does
      not delegate (only `cpu memory pids`).
  - **The fix:** the stage process now runs `wb_cell.pin_tree`, a thread that holds every thread of its descendants
    (vLLM and its workers, the client, the scrape) on CPUs 0-51.
    - It runs from before vLLM starts until it stops, checks every 2 s, and logs each reset to
      `data/runs/<run>/pin.jsonl`.
    - `test_wb.py` gained a test of it; all 8 tests pass.
    - The containers' limits are Docker cpusets, enforced by the kernel.
  - **The partial run:** the `n8_wait` rerun of 16:47-16:52 UTC (5 min) is kept as `n8_wait_ht_partial`.
  - **Restart:** re-frozen at 16:53 UTC; the chain restarted at 16:53 UTC.
- 2026-11-01 18:51-19:15 UTC, after `n8_wait`: the host's numad daemon is what moves vLLM's processes.
  - **What numad does:** it runs as root since 2026-10-02 (`/usr/bin/numad -i 15`) and checks every 5-15 s.
    - It rebinds any process over 300 MB and half a CPU to one NUMA node's whole CPU list.
    - It migrates the process's memory there too, whatever the process's own `taskset` or `numactl --membind`.
    - `/var/log/numad.log` lists every move.
  - **What it did to topic 22** (`wb_numad.py`, new, post hoc; it reads a saved copy of the log,
    `data/runs/numad.log`):
    - N=32, N=16 and `n8_wait_s1` (vLLM on node 0's whole list): 1-3 moves per run, none to node 1.
    - `n8_wait` (vLLM held on 0-51): 136 moves, 64 of them to node 1, all in its first 31 minutes; none in its last 85.
      - The stage reset the CPUs within 2 s each time (`pin.jsonl`: 140 resets).
      - The memory stayed on node 1 until numad moved it back: about 3 GB per worker and 1.3 GB for EngineCore.
        Per process, that was 1-6 % of the run.
    - It also moved a 104-thread `python3` in one of the run's containers to node 1 for 8 minutes (17:10-17:18 UTC).
      - The container's cpuset blocked the CPU part: `sched_setaffinity` failed with EINVAL. Only the run's two
        containers are held to 0-51 on this host.
      - Root's page migration is not bound by a cpuset.
    - `n8_none`: 8 moves in its first 2 minutes, 3 of them to node 1; all were back on node 0 by 18:53 UTC.
    - **A likely trigger:** with all of topic 22's work on node 0 since 16:47 UTC, node 1 looks the less busy node.
      It is not tested.
  - **What could stop it:** only root, with `numad -x PID` for each process or by stopping numad. This account has no
    sudo.
    - Pulling the memory back from the stage could set off more moves, so the run is unchanged.
    - Each run's moves are reported as a caveat.
  - **The comments** in `wb_cell.pin_tree` and `wb_vllm.sh` blamed a library; they were corrected.
    - Re-frozen at 18:58:55 UTC: those two files' hashes only.
    - The 16:53 list is kept as `data/runs/prereg.sha256.pin-1653Z`.
- 2026-11-01 19:52-20:04 UTC: `n8_none` died at 19:52 UTC. Harbor got SIGKILL (exit 137) with 81 of 96 tasks done.
  - **The cause:**
    - At 19:42:55 UTC the agent of `password-recovery` ran `zip -FF` (zip's interactive repair mode) on a damaged
      archive.
    - Its commands get no input, so zip read EOF for its question and asked again, without end. In the task's image
      that prints 153 MB/s (763 MB in 5 s).
    - Harbor 0.24's exec keeps a command's whole output in memory (`communicate()`), and the task's bash limit is
      1,800 s. After 548 s, about 84 GB of output, the eval container passed its 100 GB, and the kernel killed Harbor
      with the whole run.
    - The kernel log is not readable from this account, so the OOM kill itself is inferred from the exit code, the
      timing and the rate.
    - No earlier run's `password-recovery` agent used `zip -FF`. Before 16:47 UTC the eval container had no memory
      limit, so a flood then would have taken the host's memory instead of ending the run.
  - **Docker-in-docker's 88 OOM kills** come from the tasks' own per-container memory limits, which every run has had.
    Its own 100 GB limit killed nothing: `memory.events.local` shows oom 0, and all 88 kills are in its `docker/`
    subtree.
  - **The fix:** `wb_cap.py` (new) and `wb_agent.py`.
    - The agent's commands, and the child's file helpers, keep the first 64 MiB of output and read and drop the rest.
      A command still runs to its end or its time limit, as before.
    - Harbor's own commands (setup, the patch step, the verifier) are unchanged.
    - The model sees the first 50,000 characters either way (`s15_child.run_bash`). The child's file reads are at
      most 20 MB (27 MB in base64), so what the agent sees does not change.
    - Each drop is logged to the trial's `agent/exec_cap.jsonl`.
  - **Tests:**
    - `test_wb.py` gained `test_exec_output_cap`; all 9 tests pass.
    - In the eval image, both of Harbor's output paths keep the full output outside the agent's commands and cap it
      inside, with the exit code kept.
  - **Re-frozen** at 20:04:06 UTC (with `wb_numad.py`). The 18:58 list is kept as
    `data/runs/prereg.sha256.numad-1858Z`.
  - **The killed run** is kept as `n8_none_oom_partial`. `n8_none` is rerun from the start under one server, as
    `n8_wait` ran, rather than resumed.
- 2026-11-02 00:30 UTC: the host's numad log rotated at 23:59 UTC, so `wb_numad.py --copy`, which reads only
  `/var/log/numad.log`, missed the lines from 21:58 to 23:59 UTC. Most of `n4_none`'s run is in that gap, and the
  table showed it with 0 moves.
  - **The fix:** `data/runs/numad.log` was rebuilt from the rotated log (`/var/log/numad.log.1.gz`) and the current
    one, in time order, with the same filter as `--copy`.
    - The old copy was a strict subset of the new one, which adds 1,618 lines.
    - The old copy is kept as `data/runs/numad.log.before-rotation`.
    - `wb_numad.py` is unchanged.
  - **The table** changes only for `n4_none`: 17 moves, 6 of them to node 1, in 4 minutes. Memory was off node 0 for
    at most 1 % of the run.

## Data inventory

Everything is under `data/` (5.6 GB, not in git). Runs and their stage logs:

| run | what | analyzed |
|---|---|---|
| `n32_wait`, `n32_none`, `n16_none`, `n16_wait`, `n8_wait`, `n8_none`, `n4_none`, `n4_wait` | the 8 main runs | `wb.md`, `wb_mix.md`, `wb_waits.md` (wait runs), `wb_numad.md` |
| `n8_wait_s1` | the first N=8 `wait` run, under the old placement; superseded by the user's CPU rulings | `interim_n8_wait_s1.md`, `interim_waits_n8_s1.md`, `wb_numad.md` |
| `n8_none_s1_partial` | `n8_none` under the old placement, stopped after 93 min for the rulings | `wb_numad.md`; request p50 in Process (post hoc) |
| `n8_wait_split_partial`, `n8_wait_ht_partial` | 3- and 5-minute starts, stopped for placement fixes | `wb_numad.md` only |
| `n8_none_oom_partial` | `n8_none` killed by the output flood (81 of 96 tasks) | `wb_numad.md`; request p50 in Process (post hoc) |
| `smoke_wait` | the smoke run: N=4, 12 reserve tasks | `smoke.md` |
| `dry_none`, `dry_wait`, `dry_stop` | the CPU dry runs against `wb_fake.py` | `dry.md` |

**Files:**
- **Per run:**
  - `data/jobs/<run>/`: Harbor's job. Per trial: `result.json`, `agent/s15/calls.jsonl`, the s15 traces, the
    verifier's output, and `agent/exec_cap.jsonl` if the output cap fired (it never did). Not in git or the
    file mirror: on Drive as one `.tar.zst` per run ([research/DATA.md](../DATA.md)).
  - `data/runs/<run>/`:
    - `cell.jsonl`: the times, N, the setup and the server flags;
    - `harbor.txt`: Harbor's console;
    - `scrape_*.jsonl`: vLLM's `/metrics` once a second;
    - `wt_ext.jsonl`: every `/wt/status` read;
    - `pin.jsonl`: CPU resets, from 16:53 UTC 11-01;
    - `metrics_final.prom`.
  - `data/runs/<run>.stage.log` and `data/servers/<run>_<UTC start>/` (one per server start): the stage's and vLLM's
    logs.
- **The setups:** `data/env/none.env` and `wait.env`, the `S15_*` settings the agents get.
- **The dry runs:** `data/dry/`, the client logs and the fake server's records.
- **The chain:** `data/runs/chain.log`, `chain.out`.
- **GPU-hours:** `data/gpu_hours.jsonl` (83.34 in all, the smoke run included).
- **numad:** `data/runs/numad.log` is today's numad lines, rebuilt after the log rotated (Changes, 00:30 UTC). The
  copy from before the rotation is `numad.log.before-rotation`.
- **Freezes:**
  - each superseded freeze list: `data/runs/prereg.sha256.*`;
  - the code files they covered: `data/runs/wb_*.py.*`, `wb_eval.sh.*`.
- **Tasks:**
  - `data/tasks.json`: the draw, the reserves and the oracle results;
  - `data/queue/main`: the queue links;
  - `data/dataset/`: one link per main and smoke task to its folder in harness_eval's datasets (`wb_tasks.py draw`);
  - `data/jobs/oracle/` and `data/oracle_*`: the oracle checks.
- **Tables:** `data/tables/`:
  - `wb.*`: the final, pre-registered analysis;
  - `wb_waits.*`, `wb_mix.*`, `wb_numad.*`: post hoc;
  - `interim_*`: written as each block finished, kept as they were.
