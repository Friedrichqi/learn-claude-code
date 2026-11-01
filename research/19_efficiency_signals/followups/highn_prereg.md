# Pre-registration: topic 19's high-N follow-up (does MiniMax-M2.7 wait more when far more users compete?)

Written 2026-10-29, before any high-N cell ran. Frozen by `followups/highn_prereg.sha256`, which hashes:
- this file;
- `ef_highn.py`;
- `data/conditions_highn.json`.

Topic 19's own `prereg.sha256` (harness, server template, workload, analysis) must also still match. `ef_highn.py`
checks both before each cell.

## Question

In topic 19, MiniMax-M2.7 made 3 pacing calls in 21,081 lead calls across the five cells where it could pace itself,
at N = 8-32. The user's hypothesis: N ≤ 32 was too few users for four GPUs, so the model was not crowded enough to
call `wait N seconds`.

**What the N=32 status line already showed:**
- `w32_m_wait`'s status line read, for example, "[server] KV pool 258,176 tokens; sessions ahead of you hold 402,755;
  …; 14 requests waiting".
- Under `none`, the cache hit rate was 1.4 %.

This follow-up asks whether more extreme crowding changes the model's behaviour.

## Cells

- **`x64_m_wait` (N=64) and `x128_m_wait` (N=128).** Each is topic 19's `m_wait` setup, unchanged:
  - the `wait` tool, text v3;
  - a `[server]` line in the share view every round;
  - the server paragraph `wait`.
- **Server and harness:** unchanged from topic 19. MiniMax-M2.7 at TP=4, pool pinned at 16,137 blocks,
  `--max-num-seqs 64`, `wt_scheduler`, and the s15 harness at topic 19's hashes.
- **Timing:**
  - a 20-min warm-up, then a 40-min window;
  - fillers stop inside the warm-up (`session_est_s` = 1,200 s);
  - cycled specs;
  - think pauses and gaps as in topic 19.
- **Order:** `x64_m_wait`, then `x128_m_wait`.
- **Conditional cell:** `x<N>_none` at the lowest N whose verdict below is "rare" or "uses it". It runs only then,
  and `ef_highn.py` refuses any other `none` cell. It measures what the waits do to throughput and completion time
  against the same load without signals.
- **Budget:** topic 19's cap is 120 GPU-h, with 100.27 spent. The pipeline's estimate is about 5.3 GPU-h per cell.
- **GPU claims:** GPUs 0-3, entered in the lab sheet by the user. The pipeline refuses to start without them.

## Outcome (ef_highn.py report)

- **Lead calls:**
  - lead calls of counted sessions (not fillers), received in the window (`t_recv` in [window start, window end]);
  - wait-only calls are included;
  - read with topic 19's `EfCell`.
- **r:** the share of those calls whose response holds at least one `wait` tool call.
- **Interval:** exact (Garwood) Poisson 95 % interval.
- **Baseline:** pooled `w16_m_wait` + `w32_m_wait`, recomputed by the same code.
- **Verdict per cell (on the point estimate):**

  | r | verdict |
  |---|---|
  | < 0.1 % | still ignores it |
  | 0.1-1 % | rare |
  | ≥ 1 % | uses it |

- **"Clearly above the baseline":** the cell's lower bound exceeds the baseline's upper bound. The ratio to the
  baseline is also reported.
- **Answer to the user's hypothesis:**
  - **"supported"** if `x128_m_wait` or `x64_m_wait` reads "rare" or "uses it" *and* is clearly above the baseline;
  - **"not supported"** if both still ignore it;
  - **"inconclusive"** otherwise.

## Context (descriptive, not tested)

- **What the model saw,** parsed from each call's `[server]` line:
  - sessions-ahead tokens over the pool, p50 and p90;
  - requests waiting, p50 and p90;
  - its own cached share.
- **Crowded share:** the share of calls whose status said the session would not fit, from `EfCell.crowded_at`.
- **Congestion:** hit rate, call latency p50/p95, queue wait p50/p95.
- **Reasoning:** calls whose thinking matches `ef_highn.SIGNAL_RE` (server, crowd, congest, other sessions or users,
  requests waiting, KV pool).
- **Waits:** the waits run and the seconds requested.
- **Completion times:** per task, by `research/18_harness_wait/wt_latency.py --topic 19` with the new cells added.
- **Comparison cells:** `w16_none` and `w32_none` give the congestion without signals.

## Validity of a cell

- **A cell is valid when:**
  - no request error;
  - the window is complete (`COMPLETE`);
  - GPUs 0-3 are empty afterwards;
  - both hash listings match before it starts.
- **An invalid cell is run once more if the claim allows.** Otherwise it is reported as not run.

## Checks before any GPU time (done 2026-10-29)

- **CPU dry run:** `ef_highn.py dryrun --users 128`, the sweep with these settings against topic 19's fake MiniMax
  server, for 30 s of warm-up and 90 s of window. All checks passed:
  - all 128 slots ran;
  - fillers cycled past the 32 filler specs and ended inside the warm-up;
  - counted sessions started and ended ok, budget or terminated;
  - requests carried `wait`;
  - the cell completed.
- **One harmless race:** in a first run, one session was launched 0.3 s before the window end and stopped before it
  started. The check now allows that.
- **Frozen files:** topic 19's `prereg.sha256` matches.
