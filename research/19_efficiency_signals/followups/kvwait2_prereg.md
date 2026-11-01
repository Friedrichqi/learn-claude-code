# Pre-registration: revised screen of the KV-threshold `wait` prompt (topic 19 follow-up, 2026-10-31)

Frozen before the screen's first request. `kvwait2_prereg.sha256` lists, with repo-relative paths:
- this file;
- `ef_kvwait2.py`, `ef_kvwait.py` and `ef_kvserve.py`;
- `data/kvwait2/items.json`;
- the live `s15_integrated_harness/hold_runtime.py`;
- `rd_cf.py`, `rd_rounds.py` and `ef_render.py`;
- the template.

`ef_kvwait2.py run` refuses if any of them changed.

## Why

- **The first screen found nothing usable** (`kvwait_prereg.md`, run 2026-10-30). No wording reached its 30 % bar.
  At a newest `[server]` line of 93 %, `RC` (rule plus cue) put `wait` in 2 % of work replies (12 of 240 samples)
  and `C` in 4 %. The thinking mentioned the line in 0-2 % of samples.
- **The rate fell with the history:** the more earlier lines at ≥ 80 % in the replayed context, the lower it was.
  `RC` was at 11 % with 1-5 such lines and at 0 % with more than 20. The recorded replies after those lines never
  waited.
- **The user's choice:** the first pre-registration hands any revised wording back to the user. On 2026-10-31,
  about 00:30 UTC, the user chose this revised screen.

## Questions

1. **History:** does MiniMax act on the rule when the replayed history no longer shows the rule ignored? (`cap`
   against as recorded)
2. **Example:** does a worked example in the wait text help? (`v6` against `v5`)
3. **Usable:** is any condition usable by the first screen's rule?

## Design

- **Items:** 60, drawn with seed 1031 from the first probe's 150 unused confirm items. Their sessions are disjoint from
  the first screen's. Strata: work 42, start 9, final 9, from 56 sessions.
- **Conditions:** S = 4 samples per prompt, with the newest line at 0.62 and at 0.93.

  | name | `wait` text | paragraph | line | earlier `[server]` lines |
  |---|---|---|---|---|
  | `B0` | v3 | "wait" | share | as recorded (the request as recorded) |
  | `RC` | v5 | `wait_kv` | kv_cue | as recorded: the session's own KV use |
  | `RCcap` | v5 | `wait_kv` | kv_cue | capped: each shows min(KV use, 75 %) |
  | `RX` | v6 | `wait_kv` | kv_cue | as recorded |
  | `RXcap` | v6 | `wait_kv` | kv_cue | capped |

  Under the cap, no earlier line carries the cue. Each line's count of waiting requests is unchanged.
- **The rewrite:** `ef_kvwait.rewrite`, in the same three places.
- **The `v6` text** (`hold_runtime.WAIT_TEXTS["v6"]`, at `S15_WAIT_KV` = 0.8) is v5 plus: ' Example: if the newest
  line reads "[server] KV cache 90% full; 12 requests waiting", call your next tools and add wait with seconds 20 in
  the same response; if it reads "[server] KV cache 55% full; 2 requests waiting", do not call wait.' The example
  levels (90 % and 55 %) differ from the test levels.
- **Sampling,** as in the first screen:
  - temperature 1.0, top_p 0.95, top_k 40, max_tokens 8,192;
  - a seed per (session, turn, call, condition);
  - 8 requests in flight.
- **Server:** a stage of its own, `ef_kvserve.py run --probe kvwait2`.
  - Config `profile`: MiniMax-M2.7 TP=4 with the append-only template, on GPUs 0-3, under the user's sheet claim.
  - About 0.4 h; at most 1.0 h (4 GPU-h), from topic 19's cap.
- **Volume:** 60 × (1 + 4 × 2) = 540 sampling requests.

## Measures

They are the first screen's (`ef_kvwait.item_metrics`): per sample, an item's value is the mean of its S samples.
- wait;
- wait-only;
- final;
- kept;
- output tokens;
- the requested seconds;
- **mentions:** the thinking matches `ef_kvwait2.MENTION` (the line, the rule or a pause). This is the manipulation
  check.

## Readings

1. **Usable** means both:
   - **Eligible:** ≤ 10 % of work replies at 0.62; wait-only ≤ 5 % of samples at either level; kept at 0.93 within
     10 points of `B0`.
   - **High:** ≥ 30 % of work replies at 0.93.
2. **History holds it back:** the mean of the paired item differences (`RCcap` − `RC` and `RXcap` − `RX`) in the work
   wait rate at 0.93 is ≥ 15 points, with the lower 95 % bound > 5 points.
3. **The example helps:** the same test on (`RX` − `RC`, `RXcap` − `RCcap`).

**Statistics:** item means, with 95 % intervals from a session-cluster bootstrap with 2,000 draws.

**Exploratory:**
- each pair separately;
- the start and final strata;
- the rates by the number of earlier lines at ≥ 80 % as recorded;
- the seconds;
- thinking excerpts.

## What follows (the user decides)

- **A usable condition with the history as recorded (`RC` or `RX`):** its wording becomes a candidate for a confirm
  on the 90 remaining confirm items, or for a live N=16 cell.
- **Usable only under `cap`:** the replayed history is the obstacle. Only a live cell can test the wording, since a
  live session starts without ignored lines.
- **Nothing usable:** MiniMax does not act on the rule even with a clean history and an example. Report it and stop.

## Known limits

- **`cap` is a replay construct:** the replies after the capped lines are still the recorded ones.
- **The scope is as narrow as the first screen's:**
  - one model;
  - one workload (topic 11's four-turn code sessions);
  - contexts from N=16/32.

Any change after the freeze is logged in the topic README's Changes, with its time and reason.
