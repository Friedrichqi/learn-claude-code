### Setup and deviations (MiniMax-M2.7, phase e2: SWE-bench Pro and Terminal-Bench)

**Why.** The user asked for the EPIC arms on the other two benchmarks, compared with the baseline, after e1. e1 had
covered SWE-bench Verified only, as the approved plan specified.

**Same as e1:**
- arms prefix, prefix_aa, shift, epic128, epic128a1;
- the e1 server configuration: `gpu_util` 0.93, `--watermark 0.08`, the append-only template with the last-split
  fix;
- paired blocks, with common random numbers per block;
- one-task chunks, run as forward and reverse batches;
- the baseline profile's first 30 tasks of each benchmark.

**Fixed before any e2 result** (`run/stage_e2.py`):
- Replicates run in lockstep: replicate k of both benchmarks at once.
- Replicate k+1 starts only if the GPU-hours used so far plus the last replicate's cost stay within the study's
  150 GPU-h budget (120.2 were used before e2). A full 3 replicates of both benchmarks was estimated at about 38
  GPU-h, more than the roughly 30 left.
- **The agent timeout is doubled (×2) for every arm.**
  - In e1 the emulating server was 6–8× slower per call than the baseline server, and the 50-min timeout cut EPIC
    arms off earlier.
  - Terminal-Bench is far more timeout-bound: 18 of 90 baseline MiniMax runs timed out at ×1 on the faster
    server.
  - So against the baseline, e2 runs have a larger time budget. This matters most for Terminal-Bench.
- The statistics are e1's, but **exploratory**: e1's pre-registration covered SWE-bench Verified only.

**Code since e1.**
- Unchanged: adapter, template, planner, batch runner.
- Changed:
  - the server middleware and connector gained hybrid-model support, a no-load switch and a teacher-forced probe
    offset fix. All three leave MiniMax's path unchanged; the selftests check it.
  - the eval image was rebuilt as `s15-eval:7d49181bce04`, after the end-of-study teardown. Only its tests
    differ from e1's `fdd1bf0a6ca6`.
- Hashes are in `runs/epic/code_e2.json`.

**Scheduling changed during replicate r0, 13:10 UTC.** The blocks, seeds, arms and budget rule did not change.
- **The problem.** Terminal-Bench arms differ widely in run time (median 8 min, maximum 47 min), and a one-task
  chunk waits for its slowest arm. Two chunks in flight completed about one task per batch every 45 minutes,
  about 11 hours for r0, and the server sat idle in between.
- **What I did.**
  - Stopped the lockstep controller. The server and the Pro round kept running.
  - Let Terminal-Bench's two replicate batches finish their current chunks (stop file `run/state/stop/tbench`).
  - Ran the remaining r0 blocks per block (`epic_round.py --per-block --parallel 8`):
    - each block is one task with its 5 arms started together, under the replicate's seed key;
    - its phase is `e2r0b<i>`, where i is the task's index in the 30-task list;
    - no block waits for another block's slowest arm.
  - Later replicates are started by hand under the same budget rule: another replicate only if the GPU-hours so far
    plus that replicate's cost stay within 150.
- **Host-pool pressure, 14:02 UTC.**
  - With 8 Terminal-Bench blocks and Pro's 2 chunks in flight (39 sessions), the EPIC host pool reached 91 % of its
    640K rows. Terminal-Bench histories grow, and each EPIC session held about 25K rows.
  - New Terminal-Bench blocks were paused with the stop flag; the in-flight blocks ran on.
  - The remaining blocks resume at 4 at a time once at most one is still in flight.
  - No fallback occurred. If one had, the call would have been served as prefix, and the gates count fallbacks.
- **The pool ran out, 15:20–15:23 UTC.**
  - With Terminal-Bench at 4 blocks and Pro at 2 (28–29 sessions), a few very long Terminal-Bench histories held
    most of the 640K rows: the three EPIC sessions of fix-ocaml-gc held about 204K.
  - Planning allocations failed for about 10 calls across 9 sessions; those calls fell back to prefix. Several
    shadow probes also failed to get their temporary rows.
  - Both benchmarks were paused for new blocks and resumed at 2 blocks each once the pool was below 300K rows.
  - Ending sessions from outside was ruled out: `/epic/end` does not take the session lock, so ending a session with
    a call in flight would release its rows twice.
  - The fallbacks are counted in the gates. Each serves one call as prefix, which biases the EPIC arms toward
    prefix.
  - A Terminal-Bench round needs a larger pool, or fewer concurrent sessions sized by history length. The pool size
    is fixed at server start.
- **Terminal-Bench replies are longer than SWE-bench's.**
  - Several exceeded the 4,096-token decode reserve, which was sized on SWE-bench replies. That gave 8
    decode-overflow resets by 14:01, all at calls 1–4, before any snip.
  - A reset restarts the session from fresh rows. Since EPIC only acts after a snip, these barely touch the arms'
    treatment.
- **Gates for replicate r0**, taken live before the server stopped.
  - **SWE-bench Pro passed every gate:**
    - E-0 99.41 % of 1,693 pre-snip pairs;
    - 1,849 post-snip calls with no fallback or reset;
    - one record per call, ranks consistent, no preemptions, no sessions left.
  - **Terminal-Bench passed every gate except E-0, which failed at 94.15 % of 1,384 pairs (81 misses):**
    - fallbacks plus resets were 1.46 % of 2,253 post-snip calls (10 fallbacks from the pool episode, 23
      decode-overflow resets);
    - records, ranks, preemptions and sessions left were all clean.
  - **Why E-0 fails on Terminal-Bench.** The misses are the known MiniMax template limit: reasoning that contains
    `\n\n\n` right before a tool call. Terminal-Bench reasoning is long and full of lists and paragraphs, so the
    case is far more frequent than on SWE-bench (99.2–99.4 %).
  - **Effect.** At those calls the rest of the previous reply is recomputed rather than reused, and the model sees
    part of its reasoning after `</think>`. The template is the same for every arm, so the comparison between arms
    is not biased by it.
  - A Terminal-Bench round would need token-level history in the harness, or a reply repair in the middleware.
- **The two control arms disagree on Terminal-Bench (prefix_aa against prefix).**
  - prefix resolved 0.633 and prefix_aa 0.333 (−0.300, 90 % [−0.485, −0.115]), with 13 agent timeouts against 6.
  - The two arms are identical by design: same model, server and seeds.
  - Latency per call does not differ by arm (median 19–26 s on Terminal-Bench), so the server treated them alike.
    Their trajectories parted and prefix_aa's ran longer: 44 calls against 13 on tune-mjcf, 83 against 16 on
    custom-memory-heap-crash. The time limit then binds hard.
  - Terminal-Bench on this emulating server is time-bound: replies are about 3× slower than Pro's, and 5–13 of 30
    runs per arm hit the doubled timeout.
  - With one replicate, Terminal-Bench's arm differences (+0.017 to +0.150 against the control) cannot be told apart
    from that noise. **Terminal-Bench supports no accuracy conclusion.** Its prefill and fidelity numbers stand.
- **Launch order in per-block mode.**
  - A per-block batch has a single chunk, so `--rotate` never rotated: every e2 block after the switch started its
    arms in the same order (prefix, prefix_aa, shift, epic128, epic128a1), 2 s apart.
  - Latency per call shows no order effect on either benchmark.
  - Fixed afterwards: `epic_round.py --per-block` now passes the block's index as `batch.py --rotate-offset`.
- **A rerun bug, fixed before any rerun was needed.** `epic_round.py` gave every rerun in a round the same phase
  name (`<phase>r<r>x<n>`). With one-task reruns, all tasks then shared one job name, so only the first would have
  run. Reruns are now named `<phase>r<r>b<i>x<n>`. All three names (`b<i>`, `x<n>` and the plain phase) map back to
  the block `<phase>r<r>`.
- Docker-in-Docker was set up again per `run/dind.md`, so task images are pulled anew.
