# 18 · Follow-ups: what the run says next, and what to test (brainstorm, 2026-10-25)

> **What this is:** a two-hour brainstorm after the run (2026-10-25, 16:30-18:30 UTC). It covers offline analyses of the
> recorded cells (mostly the 17 main and exploratory ones) and a small trace-driven simulator built to screen the ideas
> before any GPU time is spent. **No GPU was used and nothing was committed.**
>
> **Measured or simulated:** every number is labelled.
> - "Measured" numbers come from the recorded cells. The scripts are in `followups/offline/`.
> - "Simulated" numbers come from `followups/sim/`.
>   - The policy results are in `followups/sim_tables.md` (`tables.py`; the mean of three session orders,
>     regenerating byte for byte).
>   - The calibration diagnostics, and the share rule v2 runs, come from the scripts named next to them.
> - The simulator matches the no-wait and server-gate cells but is optimistic for the harness rules. Treat its numbers
>   for client-side rules as upper bounds (see "The simulator").
>
> **Read with:** `README.md` (the run and its pre-registered results).

**Contents** — Short version · What the recorded cells say beyond the README · The simulator · What the
simulator says · Thoughts · Follow-up experiments · Related work · Files and reproduction · Caveats · One-slide
summary

## Short version

- **Under thrash, a call still has its prefix when it arrives and loses it in vLLM's queue (measured).**
  - At N ≥ 16 without waiting, calls in mid-turn arrived with 87-94 % of their reusable prefix cached. They had 1-5 %
    left at admission, after a median queue wait of 10 s (N=16) and 34-41 s (N=32).
  - Under contention a queued prefix is gone after 1-2 s.
  - The three working cells checked (`w16_h_backoff`, `w16_h_share`, `w16_sgate`) kept 84-97 % of calls under 0.5 s
    in vLLM's queue.
  - First-come-first-served admission plus LRU eviction means the request admitted next is the one whose blocks are
    oldest, so it is evicted first.
- **Only bounding the set of active sessions fixes it; reordering requests doesn't (simulated).**
  - Request-level server policies give 0.9-1.7× no-wait: protecting queued prefixes, evicting the queue's tail first,
    cache-hot-first admission and mid-turn-first priority.
  - Session-level load control gives 3.7× (the gate) in the same simulator.
- **Hold where the cache is already gone (measured, then simulated).**
  - A harness hold after a tool round kept the session's prefix only if it lasted under about 4 s. Longer ones lost
    almost all of it.
  - A hold at a turn start costs nothing, because the think pause already evicted the context.
  - With long tools, "after a long tool" is the same kind of place. In the simulator at N=32, holding at turn starts
    and after long tools reaches 0.94× the gate, while turn starts alone fail.
- **Once eviction is fixed, the harness's own `micro_compact` is the largest cost left (measured, then simulated).**
  - It rewrites an old tool result in place about every 2-3 lead calls, and each rewrite re-prefills about half the
    context.
  - That is about 28 % of GPU time under the server gate, about 6× the gate's leftover eviction recompute.
  - **An offline replay of the recorded conversations through other rules (measured, upper bounds):**
    - **Deduplicating exact re-reads** cuts the rewritten tokens by 75-79 %, about +27 % throughput by the GPU-share
      arithmetic (simulated: +25-27 %). The rule: answer a re-read of an unchanged file that is still in the prompt in
      full with a pointer. It removes only about 15 % of tool-result text, but that keeps contexts under the clearing
      limit.
    - **Clearing every consumed result except the latest**, once over the limit, cuts them by 67-69 %. With
      deduplication as well, by about 91 %.
    - **Batching the clearing** cuts them by only 18-20 %. The simulator's batched-clearing rows (+24-31 %) overstate
      that fix.
    - **The price:** deduplication changes nothing the model can see. Aggressive clearing does, and needs a quality
      check.
- **If the model decides, it should decide only whether to yield, not how long (simulated).**
  - Looping short waits reproduce the model arms' loss at N=32: 0.69×, against 0.70-0.73× measured. At N=16 they
    overstate it (0.70× against 0.89-0.91×).
  - A blocking `wait` is one call, after which the harness holds the session until it fits.
    - At N=16 it gives 1.3× at the model's measured decision rate, and 2.7× if the model yields at 30 % of rounds.
    - At N=32 it gives only 1.25× even at 30 %.
  - So a blocking wait removes the loop's harm but doesn't replace the harness rule. Turn starts, where the model
    can't act, still need the harness.
- **Harness-side gating needs everyone to cooperate (simulated).**
  - With half the sessions ignoring the rule, N=16 falls to 1.5× no-wait.
  - At N=32, a quarter ignoring it brings back the full collapse.
  - A server gate cannot be ignored. The harness rule is for servers you cannot change. It can run on an unmodified
    vLLM: every input is client-known, and stock `/metrics` gives the pool size (F7).
- **Top GPU follow-ups**, in order:
  1. confirm turn-start holds at N=32 and replicate N=16;
  2. give the share rule a margin (τ = 0.7);
  3. fix the harness's rewrites: deduplicate re-reads first, then clear all but the latest result;
  4. a blocking model `wait`.

  The first three total about 22-26 GPU-h plus a quality check for F3, about the 22.7 GPU-h left under the
  original cap. A six-cell round of 22.3 GPU-h is laid out under "A next round, ready to pre-register". F4 and the
  rest need a new budget, and any GPU work needs new GPU sheet claims.

## What the recorded cells say beyond the README (measured)

All of this is CPU-only and from the 17 main and exploratory cells. Each figure comes from one cell.

### 1. The prefix is lost in the server's queue, not in the gap before the call

Columns, per cell:
- **arrival:** the share of the reusable prefix still cached when the call reached the engine;
- **admission:** the share it got;
- **queue wait:** from arrival to admission.

The rows are lead calls in mid-turn (gap < 3 s).

| cell | arrival | admission | queue wait p50 |
|---|---|---|---|
| `w16_none`, `w16_none_aa` | 90-91 % | 2-5 % | 10.4-10.6 s |
| `w32_none`, `w32_m_*` | 87-94 % | 1-2 % | 34-41 s |
| twins, N=16 | 96-97 % | 93 % | 0 s |
| twins, N=32 | 94-96 % | 81-83 % | 0 s |
| `x16_h_share_start` | 100 % | 99 % | 0 s |
| `w16_sgate`, `w32_sgate` | 100 % | 100 % | 0 s |

**Retention by queue wait.** The table gives the admission hit over the hit at arrival, with the share of the cell's
calls in each band.

| queue wait | `w16_none` | `w16_h_share` | `w16_sgate` |
|---|---|---|---|
| < 0.5 s | 0.98 (4 % of calls) | 1.00 (88 %) | 1.00 (97 %) |
| 1-2 s | 0.39 (1 %) | 0.68 (4 %) | — |
| 2-4 s | 0.11 (2 %) | 0.29 (3 %) | — |
| ≥ 4 s | 0.02 (91 %) | 0.04 (2 %) | 0.11 (2 %; the gate's own wait) |

- **Why the queue is the place.** With FCFS admission and LRU eviction, the queued request whose previous call ended
  first is also the one admitted next. Its blocks are the oldest unreferenced ones, so each admitted re-prefill evicts
  exactly the prefix that will be needed next. This is LRU's classic worst case, a cyclic access pattern larger than
  the cache.
- **The user's original picture, revisited.** The run found no requeue-to-the-back of evicted requests.
  - vLLM 0.31 puts a preempted request at the front of the queue, and the cells saw 0-14 preemptions each.
  - The cost was idle and queued prefixes being evicted, then re-prefilled on admission.

### 2. Holds have the same cliff, set by load

- **Harness holds after a tool round.** The prefix kept is given as p50 / mean.

  | cell | held 0-2 s | 2-4 s | 4-8 s | 8-16 s | ≥ 16 s |
  |---|---|---|---|---|---|
  | `w16_h_share` | 1.00 / 0.91 | 1.00 / 0.76 | 0.07 / 0.32 | 0.01 / 0.11 | ≈ 0.01 |
  | `x16_h_share_start` | 1.00 / 0.94 | 1.00 / 0.93 | 0.71 / 0.65 | 0.01 / 0.29 | ≈ 0.01 |
  | `w32_h_share` | 1.00 / 0.91 | 1.00 / 0.77 | 0.11 / 0.35 | 0.02 / 0.15 | ≈ 0.01 |

- **A session's prefix survives an idle gap up to:**
  - 10-30 s at N=8 (so the 30-s think pause sits at the edge);
  - 3-10 s under the gate and in `x16`;
  - 1-3 s under the share rule and under backoff at N=16;
  - under 1 s in `w32_h_backoff`.

### 3. Where the waste is once a policy works

Evicted tokens are given as a share of all prefilled lead tokens.

| cell | total | at turn starts | in mid-turn, gap < 10 s | in mid-turn, gap ≥ 10 s |
|---|---|---|---|---|
| `w08_none` | 7.7 % | 6.9 % | 0.0 % | 0.6 % |
| `w16_sgate` | 11.5 % | 10.8 % | 0.0 % | 0.3 % |
| `w32_sgate` | 12.1 % | 11.1 % | 0.0 % | 0.5 % |
| `x16_h_share_start` | 23.4 % | 8.7 % | 7.4 % | 6.9 % |
| `w16_h_share` | 48.9 % | 6.6 % | 30.4 % | 11.7 % |
| `w32_h_share` | 66.9 % | 4.9 % | 49.9 % | 11.5 % |

- **The gate's leftover eviction is all at turn starts**, after the think pause plus a gate wait of 161 s (N=16) and
  468 s (N=32) at the median.
- **Keeping that context in a host tier is worth at most about 4-5 % of GPU time** (simulated: +4 %).
- **What separates `x16` and the twins from the gate is in mid-turn:** after holds, and after the few long tools.

### 4. The harness's own rewrites are the largest remaining cost

- **How often:** lead calls that break their own prefix (`lcp` well below the previous prompt) are 14-18 % of the calls
  in the no-wait, twin and gate cells, and 6-18 % across all cells. 86-97 % of those breaks come from `micro_compact`
  replacing an old tool result in place with `"[Earlier tool result saved at …]"`.
- **How it happens** (`s15_integrated_harness/code.py:2549-2565, 3615-3618`):
  - `prepare_context` calls `micro_compact` whenever the context is over the limit. The workload's limit is about
    134k characters.
  - `micro_compact` clears consumed results oldest first, down to 80 % of the limit, but never the 3 most recent
    (`KEEP_RECENT_TOOL_RESULTS = 3`).
  - Once everything older is already a placeholder, each new large result pushes the context over the limit again. Only
    the result that just aged out of the 3-window can be cleared, so the 80 % target is never reached. The context
    hovers at the limit and breaks every 2-3 calls.
- **What each break costs:**
  - it rewrites a third to a half of the way into the prompt (p50 offset 0.36-0.52 in the no-wait, twin and gate
    cells);
  - it prefills 8.6-16.6k fresh tokens, against 55-140 for a normal call.
- **The total:**
  - 67-69 % of all fresh prefill tokens in the no-wait, twin and gate cells (59-72 % across all cells);
  - about 26 % of GPU time at N=8 and 27-28 % under the gate (an estimate scaled from H5's fresh share);
  - about 6× the gate's leftover eviction recompute.
- **What other clearing rules would do (offline replay, `followups/offline/compact/replay_compact.py`).**
  - **The method:** the recorded lead conversations of `w16_sgate` and `w08_none`, with the original tool results
    restored from earlier calls, replayed through each rule within segments where the history only grows.
  - **How close the replay is:** it reproduces the recorded break rate fairly well (14.9 against 17.2 per 100 calls in
    `w16_sgate`). Its rewrite volume is 1.22-1.24× the recorded, since snips, summaries and overflow handling are not
    modeled.
  - **The results,** as rewritten tokens against replaying the run's own rule:

    | rule | `w16_sgate` | `w08_none` | breaks per 100 calls |
    |---|---|---|---|
    | clear in batches of 8 | −18 % | −20 % | 10-12 |
    | keep 2 recent results instead of 3 | −24 % | −22 % | 11-13 |
    | keep 1 | −37 % | −34 % | 10-11 |
    | clear all consumed results at once (keep 3) | −19 % | −21 % | 11-13 |
    | **clear all but the latest result at once** | **−69 %** | **−67 %** | 7.5-8.6 |

  - **Why the 80 % target hurts:** clearing stops once the context is under 0.8 of the limit, so older uncleared
    results stay in the middle of the context. Each later clearing then breaks at the oldest of them, and everything
    newer is re-prefilled.
  - **Why the last rule works:** clearing everything at once leaves only the one-result window at the tail to clear
    later.
- **Re-reads (measured, `followups/offline/compact/rereads.py`).** The lead makes 70-79 `read_file` calls per 100 lead
  calls. Exact re-reads (the same path, offset and limit) are common:
  - 28.5-29.8 % of reads re-read a result that `micro_compact` had already cleared to a placeholder;
  - 26.4-29.5 % re-read a result that is still in the prompt in full;
  - 4.3-4.5 % re-read a large output shown only as its 2k-character preview.

  These are upper bounds, because the file may have been edited in between.
  - **For the fix:** clearing more aggressively will turn some "still in full" re-reads into re-reads of cleared
    results, at the cost of extra rounds. But the model already re-reads about as often when the content is still
    there.
  - **A new lever:** the harness could answer an exact re-read of an unchanged file that is still in the prompt with a
    short pointer, not the content again. That would slow context growth, and with it the clearing.
  - **How big (C4, measured, `followups/offline/compact/dedup.py`):**
    - `read_file` is 94-99 % of all tool-result text added to the lead's context.
    - Exact repeats of a result still in full, for a file not written or edited in between, are 26-30 % of reads and
      14.5-15.8 % of all tool-result text (6.3-6.7M characters per cell).
    - So such a pointer would cut fresh tool-result prefill by about 15 %, and push each `micro_compact` clearing later.
  - **Replayed with the clearing rules (`replay_compact.py --dedup`; rewritten tokens against the same replay without
    deduplication):**

    | cell | the run's rule | the run's rule with dedup | clear all but the latest, with dedup |
    |---|---|---|---|
    | `w16_sgate` | 32.1M tokens, 14.9 breaks per 100 calls | **8.1M (−75 %), 3.1** | **2.9M (−91 %), 2.0** |
    | `w08_none` | 28.5M, 13.1 | **6.1M (−79 %), 2.5** | **2.0M (−93 %), 1.5** |

  - **Why so large:** with about 15 % less text, most growing segments now stay under the clearing limit. Before,
    they hovered at it.
  - **These are upper bounds.** The replay holds the model's behavior fixed, and its pointer is a fixed string. A pointer
    that names the result's saved copy stays valid when the original is cleared.
- **Earlier topics agree on the class of cost.** Topic 11 found compaction at the model's window was 88-92 % of the
  harness's own re-prefill. Topic 17's D1 found edits are 30 % of GPU time at N=4. New here: the share under the gate
  at N=16/32, the exact mechanism, and a fix estimate (above).

### 5. A client can see thrashing in its own usage, with no status endpoint

- **The signal is a session's own cache-read ratio on mid-turn calls**: `cache_read_input_tokens` over the expected
  prefix, which equals the admission hit on 100 % of calls.
  - It is bimodal. It is above 0.9 for 100 % of calls at N=8 and under the gate.
  - It is below 0.1 for 92-98 % of calls in thrashing cells.
- **Across 153 ten-minute blocks:**
  - Spearman ρ = 0.93 with lead calls/h, and −0.91 with evicted tokens.
  - A 0.5 threshold puts 62 of 63 thrashing blocks below and 45 of 45 calm ones above.
  - It also tracks `w32_h_share`'s swings (ρ = 0.93 over its 9 blocks).
- **The catch:** under thrash the reading arrives only after the 10-40 s queue wait. And as a blind backoff trigger it is
  weak (simulated, see below).

### 6. The engine, the gate's size, and the N=32 cap releases

- **The engine loop was never idle**, in any cell: requests were always running.
  - The gate runs 7.5 requests with half its step time decode-only, while the step overhead (12.9 ms) is shared by
    about 7.5 sequences.
  - `none` at N ≥ 16 runs 10.3-11.0 requests, with 90-97 % of step time in mixed steps (the re-prefill).
- **K=8 is near the pool's ceiling.**
  - The 8 active sessions use 0.71 / 0.80 / 0.84 of the pool (p50 / p90 / p99).
  - K=10 would gain about 6 % from decode batching but fill the pool in 3.5-4.8 % of seconds.
- **Holds never left the GPU idle.** The share rule held through 27-71 % of hold-seconds that the stock status would
  call uncrowded; that is by design.
- **The N=32 cap releases are the back of the line.**
  - At hold start, cap-released sessions stood at rank 30 of 32, with 2.4× the pool ahead of them. Their contexts were
    the same size as those of the sessions released because they fit.
  - The line moved one place about every 55 s. Reaching the part of the line that fits takes about 1,100 s, nearly
    twice the 600-s cap.
  - Each cap release put the pool at 1.65× (median).

### 7. The collapse is abrupt and self-sustaining

- **`w16_none_aa` ran healthy before it collapsed.** For about 15 minutes it ran with admission hits of 19-33 % and
  2,340-2,664 calls/h. From minute 20 it was at 1-3 % and 1,600-1,900 calls/h, until the end.
- **`w16_none` collapsed within 5 minutes.** It recovered once, briefly (35 % hits and 2,832 calls/h at minute 20),
  then fell back.
- **At N=32 the queue wait kept growing**, from 25 s to 45-48 s, as the contexts grew.
- **This is the signature of a metastable failure.** Slower turns keep more sessions mid-turn, which overfills the pool
  and slows the turns further.

### 8. Checked and ruled out

- **No timeouts in any cell, and few aborts:** 10-19 in each of the 4 cells checked (`w16_sgate`, `w16_h_backoff`,
  `w16_h_share`, `w32_h_share`), about the same across arms.
- **Non-lead traffic is at most 3 % of prompt tokens.**

## The simulator

**What it is.** `followups/sim/kvsim.py`, about 560 lines.
- **Sessions:** a closed loop over 238 recorded sessions (34,785 lead calls from `w08_none`, `w16_sgate` and
  `w32_sgate`). Each call carries:
  - the client delay before it (tool time or think pause);
  - its prompt tokens;
  - its reusable prefix (`ideal`, which includes the harness's own rewrites);
  - its output tokens;
  - whether it opens a turn.
- **Server:**
  - FCFS admission with an 8,192-token step budget;
  - the full prompt's blocks reserved at admission;
  - one pinned chain for the shared system prompt (167 blocks);
  - per-session chains eaten from the tail by LRU across sessions, garbage included;
  - recompute preemption.
- **Step time:** topic 18's fitted step model.
- **Policies:** the gate, the share and backoff rules, and the new candidates below.

**A bug, found and fixed during the brainstorm.**
- **What was wrong:** the first version took the shared system prompt as a free hit in every session's chain without
  allocating blocks for it. Each session carried about 167 blocks the pool never paid for: about 17 % extra capacity
  at N=16, more with ended sessions' garbage.
- **How it was found:** under the client rules the simulator evicted mostly garbage. Only 18-23 % of its evictions hit
  live chains, against 60-74 % measured.
- **The fix:**
  - one pinned shared chain;
  - every session-owned block allocated, including in the offload variant, whose reloaded prefix needs blocks too.
- **All numbers here come from the fixed version.**

**Validation, against the recorded cells** (simulated mean of three session orders → measured):

| arm | N=8 | N=16 | N=32 |
|---|---|---|---|
| `none`: work calls/h | 6,529 → 6,713 | 1,905 → 1,717 | 1,905 → 1,807 |
| `none`: GPU on evicted recompute | 2 % → 3 % | 78 % → 78 % | 73 % → 76 % |
| `gate:8`: work calls/h | — | 6,948 → 6,539 | 7,007 → 5,837 |
| `gate:8`: hit rate | — | 0.87 → 0.88 | 0.87 → 0.87 |
| `backoff` (after tool rounds) | — | 6,155 → **4,157** | 5,144 → **3,173** |
| `share`, τ = 0.9 (after tool rounds) | — | 6,695 → **4,837** | 6,330 → **3,934** |
| `share` (turn starts too) | — | 7,111 → 6,096 | — |
| running requests, time-weighted (one session order) | 6.3 → 6.3 | gate 7.4 → 7.5, share 9.0 → 9.7, backoff 10.2 → 9.7 | gate 7.4 → 7.5, share 9.2 → 9.8 |

**Evictions** (one session order, simulated → measured). "Live" is the share of evicted blocks that belonged to a live
session's latest prompt (`cum.evict_live`).

| | `none`, N=8 | `none`, N=16 | `gate`, N=16 | `x16` | `backoff`, N=16 | `share`, N=16 | `share`, N=32 |
|---|---|---|---|---|---|---|---|
| blocks/s | 290 → 309 | 695 → 713 | 328 → 341 | 350 → 399 | 414 → 522 | 372 → 462 | 429 → 555 |
| live | 6 % → 17 % | 99 % → 92 % | 16 % → 21 % | 17 % → 34 % | 38 % → 73 % | 26 % → 60 % | 46 % → 74 % |

**Where it is still wrong.**
- **The two firm regimes match:**
  - no wait: throughput within 3-11 %, evicted share within 3 points;
  - the gate: throughput within 6-20 %, hit rate within 1 point.
- **The harness rules are 1.2-1.6× optimistic.**
  - Real vLLM evicts 14-29 % more under them, more of it from live chains.
  - A held prefix lives longer in the simulator. Under the share rule at N=16, mid-turn calls after a 3-10 s gap kept
    1.00 of their prefix at the median, against 0.09 measured.
- **Part of the reason is a larger LRU buffer.**
  - Concurrency matches within about 0.7 requests.
  - The share of the pool held by running requests (window means, simulated → measured `KV in use`):
    - share rule at N=16: 0.74 → 0.85, so the simulator's buffer is about 1.7× larger;
    - share rule at N=32: 0.79 → 0.87;
    - `x16`: 0.73 → 0.79;
    - the gate: 0.62-0.63 → 0.67-0.68;
    - `w08_none`: 0.52 → 0.54.
  - **That isn't all of it.** Under backoff the share matches (0.84 → 0.84), yet the simulator is still 1.5×
    optimistic there.
- **Backoff also holds a third as often in the simulator, and for longer:**
  - N=16: 10 % of checks at a 5.5-s median, against 31 % at 2.6 s measured;
  - N=32: 18 % at 23 s, against 49 % at 6.8 s.

  The real rule reads stock `/metrics`, sampled every 0.5 s, and KV in use jumped by up to 0.40 between reads in the
  smoke. A flickering signal means more frequent short holds and herded releases (85-94 % within 2 s of another). The
  simulator's signal is exact and smooth.
- **Giving the simulator that signal closes most of the backoff gap** (0.5-s samples, KV noise sd 0.2):
  - N=32: 5,144 → 3,284 calls/h, against 3,173 measured;
  - N=16: 6,155 → 5,057, against 4,157 (from 1.48× optimistic to 1.22×).
- **The share rule is unaffected by status age** (0.5-1 s) and the 10-s mid-turn expiry on the fixed simulator
  (6,695 → 6,687-6,700 at N=16). Its gap stays open; its lead is the lower referenced share.
- **Release bunching separates the two rules** (`followups/sim/bunching.py`; releases within 2 s of another, as H2
  counts them):
  - **The share rule's dynamics match the run:** 374 releases against 374 measured at N=16, and 0.43-0.56 bunched
    against 0.44-0.46. Its gap is in the server, not the rule.
  - **Backoff's herding appears only with the realistic signal:** 0.46-0.51 bunched with the exact signal, 0.80-0.86
    with the noisy one, against 0.85-0.94 measured.
- **Ruled out:**
  - a smaller effective pool (−15 % or −25 %);
  - hysteresis: client rules recover from 30 minutes of thrash;
  - truncated sessions: scripts from completed sessions only give the same picture;
  - other traffic, timeouts and aborts.

**What points to the edge.**
- **The open-loop replay.** Each cell's recorded arrivals, replayed through the simulator's server:
  - reproduce `w08_none` (hit 0.893 against 0.896) and the gate (0.876 against 0.881);
  - roughly reproduce `w16_h_share` (0.847 against 0.784);
  - flip the others: `w16_none` comes out healthier (0.615), and `x16`, `w16_h_backoff`, `w32_h_share` and
    `w32_h_backoff` collapse (0.07-0.11).
- **The τ cliff, closed loop.** The share rule collapses past τ of about 1.0. At N=16, τ = 1.0 gives 23 % evicted and
  τ = 1.2 collapses to 2,614 calls/h. At N=32, τ = 1.1 collapses.
- **The real twins at τ = 0.9 behave like the simulator at τ of about 1.0.**
- **The likely reading:** the harness rules ran the real server within about 10-15 % of the cliff. A few percent more
  referenced KV halves the LRU buffer there. The gate ran at 0.71 of the pool, clear of it.

**How to use it:**
- To compare server-side policies.
- To bound client-side ones from above.
- To rank ideas, and to pre-register predictions, before GPU cells.

**Between-run spread.** Six session orders give a 9-10 % coefficient of variation in absolute work calls/h for healthy
configurations (2 % when thrashing). Paired contrasts are tight:
- gate / no wait: 3.63 (sd 0.36);
- turn-start share rule / gate: 1.02 (sd 0.01);
- K=10 / K=8: 1.04 (sd 0.02).

## What the simulator says

All rows are simulated, the mean of three session orders (`followups/sim_tables.md`). Ratios are against the
simulator's own `none` or `gate:8` at the same N.

**Request-level server policies, no session gate.** None comes near load control.

| policy | N=16 | N=32 |
|---|---|---|
| pin queued requests' cached prefixes until admission (≤ 50 % of the pool) | 0.98× | 0.87× |
| queue-aware eviction (idle chains first, then the queue's tail; the head last) | 1.09× | 1.00× |
| cache-hot first: largest cached share admitted first, FCFS after 120 s | **1.70×** | **1.52×** |
| mid-turn calls before turn-start calls | 1.14× | 1.10× |
| *for scale: the session gate, K=8* | 3.65× | 3.68× |

- **Queue pins turn eviction into preemption.** The pinned queue leaves running requests no room to grow.
- **Cache-hot-first admission is the best of these**, because it lets hot sessions keep running. That is an implicit
  gate with no bound on the active set.

**Hold points, margin, and a client-only signal.** The share rule:
- **turn starts only:** 6,832 calls/h at N=16. At N=32, 6,532 with the cap raised to 3,600 s; one of the three
  session orders fell to 4,915.
- **turn starts and after tool rounds, N=32 (cap 3,600 s):** 7,242.
- **τ sweep at N=16 (after tool rounds):** τ = 0.7 gives 6,495, 0.9 gives 6,695, 1.0 gives 6,002 (23 % evicted), and
  1.2 collapses (2,614).
- **Client-only rules fail to bound the active set:**
  - after a mid-turn hit below 0.5, sleeping before the next turn start for 60 s·2^k·U(0.5, 1.5) gives 1.12× at N=16
    and 1.00× at N=32;
  - turn-start holds on a timed probe (any request queued) give 1.25× and 1.0×.

**Capacity** (gate unless stated; against `gate:8` at the same N):

| change | N=16 | N=32 |
|---|---|---|
| K=10 | +3 % | — |
| K=12 | collapses: 3,371, 57 % evicted | — |
| working-set gate (admit while the active contexts fit in 0.9 of the pool) | +1 % | −7 % |
| natural pool (20,011 blocks), no gate | 3,139: thrashes less (62 % evicted) | 1,928: thrash |
| natural pool, K=12 | — | +13 % |
| FP8-sized pool (32,272), no gate | 8,309: no thrash | 1,946: thrash |
| FP8-sized pool, K=16 | — | +21 % |

On today's pool, K=8 is already about the ceiling. A larger K needs a larger pool.

**The harness's own rewrites** (against `none` at N=8, or `gate:8`). The two batched rows overstate the fix:
- they assume that a batch costs one rewrite starting at its oldest result;
- the offline replay (§4) finds batching cuts rewritten tokens by only 18-20 %;
- the "no in-place rewrites" row remains the upper bound.

| scripts | N=8 `none` | N=16 gate | N=32 gate |
|---|---|---|---|
| batched clearing, 4 at a time (rewrite tokens −75 %; the batch's rewrite starts at its oldest result) | +26 % | +24 % | +26 % |
| batched clearing, 8 at a time (−87 %) | +31 % | +29 % | +31 % |
| 77 % of rewrites removed: F3a, deduplicating re-reads, as replayed in §4 | +27 % | +25 % | +27 % |
| no in-place rewrites at all (bound) | +37 % | +36 % | +36 % |
| no rewrites + FP8-sized pool + K=20 | — | — | 12,757 (+82 %) |
| think-pause offload (turn starts reload the whole prefix from a host tier) | — | +4 % | +4 % |

**F3a also gives the share rule more margin** (simulated, against the gate on the same scripts):
- **The share rule (τ = 0.9, after tool rounds):** 0.96× → 0.99× the gate at N=16 and 0.90× → 0.97× at N=32. Less
  rewrite garbage competes with held prefixes.
- **Backoff on the realistic signal** doesn't gain: 0.73× → 0.76× and 0.47× → 0.40×. Its problem is the signal.

**The levers stacked, at N=32:**

| levers | work calls/h | × no wait | × today's gate |
|---|---|---|---|
| no wait | 1,905 | 1.00 | — |
| the gate (K=8) | 7,007 | 3.68 | 1.00 |
| + F3a | 8,897 | 4.67 | 1.27 |
| + an FP8-sized pool, K=16-20 | 10,958-11,569 | 5.75-6.07 | 1.56-1.65 |

**A model-called wait.** The share of rounds that start a wait run, and how runs continue, are set to the measured
rates. Each wait-only round is a model call.

| N | looping short waits | one blocking wait, the harness holds until fit (same decision rate) | blocking, decided at 30 % of rounds |
|---|---|---|---|
| 8 | 0.77× (measured 0.68-0.98×) | 1.00× | — |
| 16 | 0.70× (measured 0.89-0.91×) | 1.34× | 2.67× |
| 32 | 0.69× (measured 0.70-0.73×) | 0.96× | 1.25× |

- **The loop structure alone reproduces the measured loss at N=32.** At N=16 it overstates it a little.
- **The simulated wait rate is too high at N=8:** 97 wait-only calls per 100 work calls, against 9-32 measured. At N=16
  and N=32 it is 42 and 54, against 33-49.
- **A blocking wait removes the loop's harm but holds too few sessions to replace the rule.** It acts only at the
  rounds the model picks, never at turn starts.

**Partial adoption.** The share rule (turn starts and after tool rounds) runs in part of the sessions.

| N | sessions with the rule | total calls/h | per session/h: with the rule / without |
|---|---|---|---|
| 16 | 50 % | 2,881 (most of the gain lost) | 137 / 223 |
| 16 | 75 % | 5,950 | 361 / 405 |
| 32 | 50 % | 1,896 (collapse) | 32 / 86 |
| 32 | 75 % | 1,899 (collapse) | 42 / 112 |

**Admission order under the gate** (N=32). The median turn falls but the tail grows:

| order | median turn | turn p95 | throughput |
|---|---|---|---|
| first come, first served | 612 s | 1,531 s | — |
| least attained service | 226 s | 2,001 s | +4 % |
| remaining work in the turn (oracle) | 231 s | 2,166 s | −5 % |

Remaining-work hints from the model would buy little here.

**Longer think pauses** (4× longer, about 2 minutes at the median, closer to human users) move the knee only a
little:
- N=12 no longer thrashes (6,125 calls/h against the gate's 6,438).
- N=16 thrashes partly (2,680, hit rate 0.39).
- N ≥ 24 collapses as before.
- The gate's throughput doesn't change.

A turn is dozens of calls long, so sessions spend most of their time mid-turn whatever the think time.

**Long tools** (10 % of tool calls take 10-60 s more):
- Without waiting, N=16 thrashes less: 2,814 calls/h, hit rate 0.43.
- At N=32:
  - turn starts only fail (2,372, hit 0.29);
  - turn starts plus after long tools reach 0.94× the gate (4,955 against 5,297).

## Thoughts

1. **This is 1960s virtual-memory thrashing, with sessions as processes and the KV pool as memory.**

   | virtual memory | here |
   |---|---|
   | global LRU replacement | vLLM's prefix cache |
   | load control: suspend whole processes so the rest fit | the gate and the harness holds |
   | Denning's working-set admission | the share rule |
   | page-fault frequency control | the own-hit signal of §5 |
   | swapping | KV offload |
   | thrashing's self-sustaining collapse | §7 |

   The old lessons carry over:
   - **Control the load; don't reorder within it.** The simulated request-level policies are 0.9-1.7×, and topic 11's
     FCFS priority and TTL pins 1.2-1.4×.
   - **Leave margin below the cliff.** The gate's 0.71 of the pool against the rules' τ = 0.9. A rule near the cliff
     fails occasionally rather than on average: v2 at N=48 collapsed in one of six simulated session orders. Judge
     follow-ups by their worst blocks as well as their means.
2. **The decision cost belongs off the contended resource.**
   - A model-in-the-loop controller pays for every decision with a call on the server it is trying to relieve, and pays
     most exactly when that server is crowded (the re-prefill).
   - Let the model choose to yield, if at all; let the harness time the hold.
   - Even then the harness must hold turn starts itself. A blocking wait alone gives 1.3-2.7× at N=16 and 1.0-1.25× at
     N=32 (simulated), because the model only acts at the rounds it picks.
   - Topic 06's lesson (models act on cost information only with a policy sentence) is the weaker half. The structural
     half is that a decision made by a model call costs a model call.
3. **Hold where the cache is already gone.** Turn starts, and in other workloads the end of a long tool call. That is why
   `x16` closed most of the gap. Any hold rule should know where it is in the turn.
   - Holds after a tool round cost the session its prefix beyond about 4 s.
   - But keep them as a safety valve. Without them, the simulated rule slid off the cliff in one of six orders at N=48;
     with them it never did.
4. **The queue belongs where waiting is free.** A request waiting in vLLM's queue loses its prefix and evicts others
   on admission. A session waiting in the harness (or at the gate) costs only its own, already-gone context.
   - **What users feel:** the wait moves to the start of a turn. At turn starts the gate's wait was 161 s (N=16) and
     468 s (N=32) at the median. `x16`'s turn-start holds were 107 s (p95 342 s).
   - After that the turn runs fast, where without waiting every call in it was slow (turn p50 1,094 s).
   - A product would show "queued" there.
5. **Harness-side gating is a cooperative protocol.** It works when every client runs it (simulated):
   - at N=16, half the sessions defecting loses most of the gain, and a defector gets 12-63 % more work than a
     cooperator;
   - at N=32, a quarter defecting brings back the collapse.

   For a shared server you control, put the gate in the server. The harness rule is for servers you can't change. Even
   then, it wants a shared view of who is active rather than a global signal everyone reacts to at once (backoff's
   herds). That shared view can be a registry the harnesses keep themselves, since every input of the share rule is
   client-known and stock `/metrics` gives the pool size (F7).
6. **After load control, the bottleneck moves to the harness's own prefix discipline**, then to pool capacity and
   decode batching. The order to attack them:
   1. stop rewriting history mid-context (cheap):
      - deduplicating exact re-reads cuts rewritten tokens by about 75-79 % in the offline replay (§4), about +27 %
        throughput by the GPU-share arithmetic;
      - clearing all but the latest result as well takes the cut to about 91 %;
      - the no-rewrite bound is +36 % (simulated);
   2. then capacity, with a larger K to use it: the natural pool or FP8 KV (+13-21 % at N=32, simulated; FP8 needs a
      quality check).

   On today's pool, K above 8 or a working-set gate buys little (+1-3 %), and K=12 falls off the cliff. Stacked at
   N=32 (simulated): gate, then F3a, then an FP8-sized pool with K=16-20 reach 1.6× today's gate and 6× no wait.
7. **The GPU cost of a finished turn is the plainest way to say all this** (measured: 2 GPUs × 3,600 s over the
   README's turns/h).

   | cell | GPU-seconds per finished turn |
   |---|---|
   | `w08_none` | 51 |
   | `w16_none` | 193 |
   | `w16_sgate` | 55 |
   | `x16_h_share_start` | 56 |
   | `w16_h_share` | 66 |
   | `w16_h_backoff` | 86 |
   | `w16_m_stock` | 270 |
   | `w32_sgate` | 47 |
   | `w32_h_share` | 81 |
   | `w32_m_stock` | 240 |

   The gate and turn-start holds bring a turn back to what it costs at N=8.
8. **Design experiments for between-run variation.** Different session orders move absolute throughput by 9-10 %
   (simulated). Pair arms on the same spec sequence, as the run did, and replicate the key contrasts on fresh sequences.

## Follow-up experiments (ranked)

Each GPU cell is a TP=2 pair for a 20-min warm-up plus a 90-min window, about 3.71 GPU-h, as in the run. The original
cap has 22.7 GPU-h left (100 − 77.33). The GPU sheet claims have expired, so any GPU work needs new claims and the
user's budget decision.

**F1 · Confirm turn-start holds (the exploratory result).**
- **Cells:** `x16_h_share_start` again on a fresh spec sequence, `x32_h_share_start`, and optionally
  `x32_h_share_startonly`.
- **Change:** no cap (or 3,600 s) for turn-start holds, since they are cheap.
- **Worth testing in the same cells, "share rule v2"** (simulated with `followups/sim/v2.py`):
  - **Hold where the cache is already gone:** at turn starts and after long tools only. Never after a short tool
    round.
  - **Margin:** τ = 0.75-0.8.
  - **No cap** on turn-start holds.
    - First-come ranking by turn start guarantees progress, because a holding session's turn start doesn't move.
    - That holds only while stale entries expire: a crashed client must not stay ahead forever.
    - Keep a long safety cap (say 1 h) that re-queues the session rather than force-admitting it.
  - **Simulated:**
    - 7,000-7,090 calls/h at N=16 and 7,070 at N=32, against the gate's 6,948 and 7,007;
    - a better turn tail at N=32 (p95 1,118-1,205 s against 1,531 s);
    - gate level with batched clearing too (9,092 against 9,188 at N=32).
  - **The cost of the lower τ:** with long tools, 0.83× the gate, against 0.94× at τ = 0.9.
  - **Its limit at high N** (simulated, `followups/sim/scale.py`):
    - It tracks the gate up to N=32, but falls to 0.81× at N=48 (5,677 against 7,036 calls/h) and 0.93× at N=64
      (6,587 against 7,114).
    - Its median turn is 20-25 % longer than the gate's there.
    - **Over six session orders at N=48,** v2 against the gate was 0.98, 0.59, 0.88, 0.96, 0.98 and 1.00 (mean 0.90).
      It is usually at the gate's level, but one order slid off the cliff.
    - So the client rule's risk is occasional collapse, not a lower average, and the gate never did that. F1 should
      count collapsed blocks, not only the mean.
    - At N=64 the six orders give 0.95, 0.88, 0.96, 0.92, 0.87 and 0.93 (mean 0.92): a steady deficit of about 8 %,
      with no collapse.
    - **`x16`'s rule** (τ = 0.9, holds at turn starts and after tool rounds, 3,600-s cap) stayed at 1.00-1.03× the
      gate in all six orders at N=48, and at 0.99-1.03× at N=64.
    - **The reading:** holds after tool rounds are a safety valve when contexts grow during turns.
  - **So F1's primary arm should be `x16`'s rule with the long cap; v2 is a variant.** The two are equal up to N=32, and
    `x16`'s rule is the more robust at N=48.
  - **Deployable on an unmodified vLLM** through a client-side registry (see F7).
- **Prediction:** R ≥ 0.8 at both N.
  - The simulator puts turn starts plus tool rounds within 2 % of the gate at N=16 and above it at N=32.
  - At τ = 0.9, turn starts alone were less stable at N=32: one of three session orders fell to 0.70× the gate. At
    τ = 0.75 (v2) every order held (below).
  - **Over six orders at N=32,** `x16`'s rule with the long cap and v2 stayed at 1.01-1.05× and 0.96-1.03× the gate
    in every order (paired). The collapse risk only appears from N=48 (see v2's limit above). The simulator is
    optimistic for client rules, so the real margin is smaller.
- **Decision:** call it confirmed if both R lower bounds clear 0.5.
- **Cost:** 7.4-11 GPU-h.

**F2 · Margin: the share rule at τ = 0.7.**
- **Cells:** `w16_h_share_t70` and `w32_h_share_t70`, holding after tool rounds only, as in the main run.
- **Tests:** the cliff-edge reading of the simulator gap.
- **Prediction:**
  - The simulator's cliff is at τ of about 1.0, and the real twins at 0.9 behave like it. So the real margin is about
    10-15 % smaller.
  - τ = 0.7 should then bring the rule near the gate: the simulator gives 6,495 against the gate's 6,948 at N=16.
  - If it doesn't move, the gap is mechanism, not margin.
- **Cost:** 7.4 GPU-h.

**F3 · Fix the harness's mid-context rewrites.**
- **F3a, first:** answer an exact re-read of an unchanged file that is still in the prompt in full with a pointer.
  - Replayed: −75-79 % rewritten tokens.
  - Nothing the model sees changes.
  - The pointer should also name the result's saved copy, as `micro_compact`'s placeholders do. Then a later clearing
    of the original leaves it valid, with no rewrite.
  - **Where it goes:** the lead loop's tool dispatch in `s15_integrated_harness/code.py`, where a tool's output becomes a
    `tool_result` (around line 3,800). The subagent and teammate loops build theirs at lines 1,279, 1,898 and 2,361.
    - **The check:** before appending a `read_file` result, look for an earlier `read_file` with the same path, offset
      and limit in `messages`. Its result must still be in full, and no `write_file`/`edit_file` on the path may follow
      it. Comparing the file's mtime or a content hash is stricter.
    - **If so:** append a pointer instead, naming the earlier `tool_use_id` and its saved copy (`save_output`,
      line 2,458).
    - **Guard:** if the model asks for the same unchanged file again right after a pointer, return the full content.
      It may need the text nearer the end of its context, and it must not loop on pointers.
- **F3b:** once over the limit, clear every consumed result except the latest, instead of clearing oldest first down
  to 0.8 of the limit with 3 kept.
  - Replayed: −67-69 % alone, −91-93 % with F3a.
  - It changes what the model sees.
- **Alternatives:** keep 2 (−22-24 % rewritten tokens), or batching (−18-20 %).
- **Both default-off** behind flags.
- **Cells:** `w08_none` and `w16_sgate` with the change.
- **Quality:** a check on harness_eval's benchmarks is essential for F3b. The model sees fewer full results, and topic
  02 found that the lead re-reads what compaction removed: here 28.5-29.8 % of reads already do (§4).
- **Prediction (offline replay, §4; GPU-share arithmetic):** under the gate and at N=8,
  - F3a: about +27 % throughput (the simulator: +25-27 %);
  - F3a + F3b: about +34 %, near the simulator's no-rewrite bound of +36 %.
  - Extra re-reads under F3b would eat into it.
- **Done offline already (C2, C4):** the replay.
- **Next offline:** run the real `prepare_context`, including snips and summaries, and count the context sizes the
  model would see.
- **Cost:** 7.4 GPU-h, plus the quality check.

**F4 · A blocking model `wait`.**
- **The tool:** the model's `wait(max_s)` returns when the share rule says the session fits, or at `max_s`, with a
  result that says so and tells it to continue.
- **Guard:** a rate limit (at most one wait per few rounds) against loops.
- **Cells:** `w16_m_share_block`, `w32_m_share_block`, and `w08_m_share_block` for no harm.
- **Prediction (simulated):**
  - At N=16: 1.3× `none` at the model's measured decision rate, and 2.7× if it yields at 30 % of rounds.
  - At N=32: 1.0-1.25×.
  - It removes the loop's harm but doesn't reach the twin.
- **Variant F4b:** add harness-held turn starts, to see whether the model's mid-turn yields add anything on top.
- **Cost:** 11 GPU-h.

**F5 · Capacity.**
- **Cells:** the natural pool (20,011 blocks) at N=16/32 with the gate at K=8/12, and FP8 KV at N=32 with K=16.
- **Quality:** FP8 needs a quality check.
- **Prediction (simulated):**
  - The natural pool only softens N=16 without a gate (1.6× `none`). The FP8-sized pool cures it.
  - With the gate at N=32: K=12 on the natural pool gives +13 %, and K=16 on FP8 +21 %.
- **Cost:** 11-15 GPU-h.

**F6 · Partial adoption.**
- **Cells:** N=16 and N=32 with half the slots running `h_share_start` and half `none`.
- **Code change:** per-slot client env in `wt_sweep`.
- **Prediction (simulated):**
  - At N=16, most of the gain is lost (1.5× `none`), and non-holders get 63 % more work per session.
  - At N=32, a collapse.
- **Cost:** 7.4 GPU-h.

**F7 · A client-only gate.**
- **Screened in the simulator, and it fails.**
  - Turn-start holds on a probe signal (any request queued) give 1.25× `none` at N=16 and 1.0× at N=32.
  - Holding after tool rounds as well gives 2.2× and 1.57×.
  - The share rule and the gate give 3.7×.
- **Why:** a queue signal doesn't say how much of the pool the active sessions' working sets will need. Turn-start
  holds work because of the share rule's per-session working-set accounting and first-come ranking.
- **What a client-only version needs:** that same accounting in a small shared coordinator among the harnesses. A probe
  isn't enough.
  - **Every input of the share rule is client-known:**
    - each session's context size (prompt plus completion tokens, from the `usage` of its own responses);
    - whether it is mid-turn;
    - its turn start.
  - **The one server fact, the pool size, is in stock vLLM's `/metrics`:** `vllm:cache_config_info` carries
    `num_gpu_blocks="16137"` and `kv_cache_size_tokens="258192"` in these cells.
  - **So the share rule, v2 included, can run against an unmodified vLLM.** It needs a tiny registry (a file in
    `/dev/shm` on one host, or a small service) where each harness posts its context size and turn start.
- **The simulator's share rule already is that coordinator** (`kvsim._share_holds`). It reads only:
  - each session's context `ctx`, prompt plus output of its last finished call, which a client has from `usage`;
  - its turn start and mid-turn state;
  - the pool size.

  So its share-rule rows apply to a client-side registry as well.
- **Next:** the share rule v2 cells of F1, run through a registry instead of `/wt/status`. That tests deployability on
  stock vLLM at no extra GPU cost.
- **Where it goes:** `s15_integrated_harness/hold_runtime.py`.
  - `fetch()` (line 189) would build the share view from the registry instead of GETting `/wt/status`.
  - Each harness would post `{ws, turn_t0, mid, t}` atomically (write a temp file, then rename) on four events: a
    turn start (`begin_turn`, line 169), each finished call (`ws` from `usage`), a hold, and a turn end.
  - Stale entries (older than the server's 10-s mid-turn expiry) would count as not mid-turn.
  - The pool size comes from `vllm:cache_config_info` once at start-up.
- **A stale registry costs little (simulated, `followups/sim/v2_registry.py`).** v2 read 1 s old on average, with
  mid-turn entries expiring 10 s after a session's last call, gives 6,757 calls/h at N=16 and 6,819 at N=32. That is
  3.5 % below fresh reads and 0.97× the gate. It is an upper bound, like every client-side number.

**F8 · Metastability.**
- **The cell:** N ramped 8 → 16 → 8 in 15-min steps.
- **Tests:** hysteresis (§7). The simulator shows it partly near its knee of about 11-13 sessions:
  collapse-started runs were 3-19 % lower in 5 of 6 pairs.
- **Cost:** about 6 GPU-h.

**F9 · Replicates.**
- **Cells:** `none`, `sgate`, `h_share` and `x16` at N=16 on fresh spec sequences.
- **Why:** to measure between-run variation, which the block bootstrap cannot see.
- **Cost:** 15 GPU-h.

**F10 · A workload with long tools.**
- **The workload:** SWE-style test runs, or topic 11's workload with a slow tool.
- **Tests:** "hold after long tools" against TTL pins (Continuum) against the gate.
- **Prediction (simulated):** turn-start holds alone fail at N=32, and holds after long tools fix it.
- **Cost:** 11 GPU-h.

**CPU-only, before or alongside:**
- **C1 · Close the simulator's gap.**
  - **Found and fixed so far:** the shared-prefix blocks.
  - **Done for backoff:** a 0.5-s sampled, noisy signal brings it within 4 % at N=32 and to 1.22× at N=16
    (`backoff_noise.py`; it held at a third of the measured rate before, `backoff_check.py`).
  - **Open for the share rule:**
    - staleness doesn't explain it (`share_stale.py`);
    - neither do the rule's own dynamics: its releases and their bunching match the run (`bunching.py`);
    - its running requests hold 0.74 of the pool, against 0.85 measured (window means). That is 9.0 against 9.7
      running requests, each with about 7 % less KV;
    - its calls are faster than the run's (`calllat.py`, median simulated against measured):

      | arm | median call |
      |---|---|
      | `none`, N=8 | 1.4 s against 1.5 s |
      | the gate | 1.6 s against 1.9 s; about 0.3 s is client and API overhead the simulator doesn't model |
      | the share rule | 2.0 s against 2.6 s; the extra 0.3 s points to more re-prefill in the run |
      | backoff, with the realistic signal | 3.2 s against 3.7 s |
  - **Ruled out as well:** 0.3 s of client and API overhead per call (`overhead.py`). It moves the share rule only
    from 6,695 to 6,535 calls/h at N=16, against 4,837 measured.
  - **Also open:** the gate at N=32 stays 16-20 % above the run (6,778-7,007 against 5,837). That is about twice the
    simulated spread between session orders, so the N=32 cell may have had a heavier session mix (F9 would tell).
  - **Next:**
    - compare the server's per-session `ws` and ranking with the simulator's;
    - log blocks per running request and per eviction victim in `WTScheduler`;
    - check vLLM 0.31's block allocation (decode lookahead, chunk alignment, partial blocks) and free-queue order
      against the model.
- **C2 · Replay `micro_compact`.**
  - **Done for clearing rules:** §4, `followups/offline/compact/replay_compact.py`.
  - **Next:** the same through the real `prepare_context` (snips, summaries, overflow handling), so the baseline matches
    the recorded rewrite volume and not 1.22-1.24× of it.
- **C3 · Run the simulator** for every GPU candidate above before its cells, and pre-register the predictions.
- **C4 · Deduplicate exact re-reads.**
  - **Done:**
    - the size: §4, `followups/offline/compact/dedup.py`, about 15 % of tool-result text;
    - the replay: `replay_compact.py --dedup`, −75-79 % rewritten tokens.
  - **Next:** F3a in the harness (pointer naming the saved copy), then on GPU.

**Suggested order:**
1. C2 and C3 (no GPU);
2. F1, F2 and F3 (about 22-26 GPU-h plus F3's quality check, about the left-over budget);
3. F4 and F5 (a new budget);
4. the rest as questions arise.

### A next round, ready to pre-register (proposal, not approved)

**Cells:** six, run as in the main run.
- topic 11's workload, the same harness and server;
- the pool pinned at 16,137 blocks;
- a 20-min warm-up and a 90-min window;
- paired spec sequences, two cells at a time on pairs A and B;
- 3.71 GPU-h per cell, 22.3 GPU-h in all.

| round | pair A | pair B | question |
|---|---|---|---|
| 1 | `x32_h_share_start` (`x16`'s rule; no cap on turn-start holds) | `w32_h_share_t70` (τ = 0.7, after tool rounds only) | F1 at N=32; F2 at N=32 |
| 2 | `x16_h_share_start_rep` (`x16` again, fresh spec sequence) | `w16_h_share_t70` | F1 replicate; F2 at N=16 |
| 3 | `w16_sgate_dedup` (gate + F3a) | `w08_none_dedup` | F3a under the gate and without contention (F3b after its quality check) |

**Comparisons and rules.** These compare across rounds with the main run's cells. The main run's A/A floor (21 calls/h)
was measured within one round, so the rules use a between-run floor of 10 % instead (simulated spread 9-10 %).
- **F1:**
  - turn-start holds are confirmed if `x16_h_share_start_rep` reaches R ≥ 0.5 with its block-bootstrap lower bound
    above 0;
  - they work at N=32 if `x32_h_share_start` reaches R ≥ 0.5 with zero cap releases.
- **F2:** margin matters if τ = 0.7 beats τ = 0.9 (the main run's `w16_h_share` and `w32_h_share`) by ≥ 10 % at
  both N.
- **F3a:** adopt it if work calls/h rise ≥ 10 % against `w16_sgate` and `w08_none`, and the rewrite share of GPU
  time falls (expected: about 28 % → under 10 %).

**Not covered by the 22.3 GPU-h:**
- F3b's quality check. It needs a model with a meaningful pass rate: Qwen3-32B's pass@1 on harness_eval's benchmarks
  was 0.02-0.09. F3a hides nothing from the model, but a light check is still prudent.
- Any rerun.

**Before any GPU:** the user's budget decision and new GPU sheet claims (3 rounds, about 6 hours on GPUs 0-3).

## Related work, as it bears on these follow-ups

- **Denning, "The working set model for program behavior" (CACM 1968), and "Thrashing: its causes and prevention"
  (AFIPS 1968).** Working-set admission and load control. These are the frame for thoughts 1 and 3.
- **Continuum (KV time-to-live pins across tool calls; reading note in `related_work/`).**
  - Here tools take 0.09 s at the median, so the loss is in vLLM's queue, not in the tool gap. That fits topic 11's
    1.24× for 10-s TTL pins.
  - With long tools (F10) the tool gap becomes the place to protect or to hold.
- **SGLang's cache-aware (longest-prefix-match) scheduling** is the "cache-hot first" row: simulated at 1.5-1.7×
  without load control.
- **Mooncake's early rejection under overload** reports the same kind of load oscillation as the share rule's cap
  releases at N=32.
- **Metastable failures** (Bronson et al., HotOS 2021; Huang et al., OSDI 2022): §7's self-sustaining collapse.
- **Autellix (program-level least-attained-service)** is the admission-order row: it cuts the median turn and costs the
  tail here.
- **CachedAttention / Pensieve / InferCept (keep or offload KV across turns and tool calls)** is the think-pause
  offload row: worth about 4 % under a gate in this workload.
- **TCP congestion control (AIMD, delay-based Vegas)** is F7's client-only gate. Its signals are delay or loss, and
  both the own-hit backoff and a queue probe failed as such signals in the simulator. Bounding the KV working set
  needs working-set accounting (Denning, not Jacobson).

## Files and reproduction

| path | what |
|---|---|
| `FOLLOWUPS.md` | this file |
| `followups/sim/kvsim.py` | the simulator (policies listed in its docstring) |
| `followups/sim/extract.py`, `variants.py` | session scripts from the cells (`scripts.pkl`) and the append-only variant |
| `followups/sim/tables.py` | the policy results ("What the simulator says") → `followups/sim_tables.md`; `--transforms` makes the other script variants |
| `followups/sim/{seeds,hyst,rescue,survival,evrate,replay,live,evage,conc,complete,backoff_check,backoff_noise,share_stale,bunching,calllat,overhead}.py` | the calibration and diagnostic runs behind "The simulator" section (spread, hysteresis, rescue, survival by gap, eviction rate, open-loop replay, live evictions, eviction age, concurrency, completed sessions only, the backoff and share-rule signals, release bunching, call latency, client overhead) |
| `followups/sim/{mix,mw,lat,v2,v2_registry,scale,dedup_sim,synergy,stack,probe,think4,run1,run2}.py` | the exploratory runs (partial adoption, model waits, admission order and long tools, share rule v2, deduplication and its effect on the rules, stacked levers, client-only gate, longer think pauses, ad-hoc cells; `run2.py` takes `SCRIPTS=` and `POOL=`) |
| `followups/offline/queue/{warmup2,qloss,holdkeep}.py` | §1, §2 and §7: queue-wait retention, hold retention, warm-up dynamics |
| `followups/offline/arrivals/` | §1-§5 from arrival and admission records: survival by gap, the own-hit signal, prefix breaks and their causes, leftover waste (`REPORT.md`) |
| `followups/offline/compact/replay_compact.py` | §4: the recorded conversations replayed through other `micro_compact` clearing rules (`--dedup` for C4) |
| `followups/offline/compact/rereads.py` | §4: exact re-reads of cleared, full and previewed results |
| `followups/offline/compact/dedup.py` | §4 (C4): tool-result text that repeats an unchanged, still-visible read |
| `followups/offline/steps/` | §6: engine idle, batch makeup, K headroom, holds against server state, the N=32 cap releases (`summary.md`) |
| `data/followups/*.pkl` | session scripts and caches (gitignored) |

```bash
PY=/data/fritz/venv/bin/python
cd research/18_harness_wait/data/followups
$PY ../../followups/sim/extract.py w08_none w16_sgate w32_sgate
$PY ../../followups/sim/variants.py
PYTHONPATH=../../followups/sim $PY ../../followups/sim/tables.py --transforms
PYTHONPATH=../../followups/sim $PY ../../followups/sim/tables.py > ../../followups/sim_tables.md   # about 2 min, 16 cores
cd ../..
$PY followups/offline/queue/qloss.py w16_none w32_none w16_h_backoff w16_h_share w16_sgate
$PY followups/offline/queue/holdkeep.py w16_h_share x16_h_share_start w32_h_share
$PY followups/offline/arrivals/load.py && $PY followups/offline/arrivals/analyze.py
$PY followups/offline/compact/replay_compact.py w16_sgate w08_none                # about 7 min
$PY followups/offline/compact/rereads.py w16_sgate w08_none
$PY followups/offline/compact/dedup.py w16_sgate w08_none
$PY followups/offline/compact/replay_compact.py --dedup w16_sgate w08_none        # about 3 min
```

## Caveats

- **Measured figures come from one 90-min cell each.** The simulator suggests runs differ by 9-10 % in absolute
  throughput.
- **The simulator is a prototype.**
  - It matches the no-wait and gate regimes.
  - It is 1.2-1.6× optimistic for client-side rules. That is after fixing one cause found here (the shared-prefix
    blocks).
  - For backoff, a realistic signal brings it to 1.0-1.2×. For the share rule the rest is open (C1).
  - Its workload is the recorded sessions, replayed with their recorded outputs. A policy that changes what the model
    does (F3, F4) is only approximated.
- **The fix estimates are bounds, not measurements.**
  - The `micro_compact` estimates hold the model's behavior fixed.
    - The replay's best rule changes what the model sees, which can change what it does (re-reads, more rounds).
    - The simulator's batched rows overstate the gain (§4).
  - The FP8 rows assume the same step times and model behavior.
- **One model, one workload, one hardware pair:** Qwen3-32B, topic 11's sessions, H100 TP=2, a pool pinned at 16,137
  blocks. As in the README.

## One-slide summary (for a progress deck)

- **The mechanism is load, not luck (measured).** Calls arrive with 87-94 % of their prefix cached and lose it while
  queued in vLLM: first-come admission plus LRU evicts exactly the next prefix needed. Only bounding the active sessions
  fixes it. Reordering requests gives at most 1.7× in simulation, against 3.7× for a gate.
- **Hold where the cache is already gone.** Turn-start holds are free and closed most of the gap to the gate (`x16`,
  R = 0.91). The next round tests them at N=32 and with margin (τ = 0.7). In simulation, `x16`'s rule, which keeps
  holds after tool rounds as a safety valve, matched the gate in every session order up to N=64. The rule needs no
  server change: its inputs are client-known, and stock `/metrics` gives the pool size. But it only works if every
  client runs it.
- **The harness wastes about 28 % of GPU time on its own rewrites once the server is gated (measured).** Deduplicating
  exact re-reads alone removes 75-79 % of that in an offline replay, worth about +25-27 % throughput, with nothing
  hidden from the model.
- **A model-called wait needs a blocking design.** The model says "yield"; the harness decides how long. Even so, turn
  starts still need the harness (simulated 1.3-2.7× at N=16, against the measured 0.9×).
- **Ask:** 22.3 GPU-h for a pre-registered six-cell round (F1 + F2 + F3a), and new GPU sheet claims.
