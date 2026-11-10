# Pre-registration: topic 23's prompt screens (S2), written 2026-11-02 before the pilots ran

Hashed in `screen_prereg.sha256` before the screens' first request, together with `et_screen.py`, `et_prompts.py`,
`et_controller.py`, `screen_rules.json`, the pinned `vendor/s15/s15_integrated_harness/hold_runtime.py` and the two
input files (`data/screen/items.json`, `data/screen/snapshots.json`). Any change afterwards goes to the README's Changes
with its time and reason.

## Why

The user asked (2026-11-02) for prompt engineering first, so that the model understands and uses the new tools: the
LLM-set session limit (the `agate` controller) and the LMCache save (`save_context`). The final `wait` prompt was
tuned on MiniMax-M2.7 (topic 19); whether Qwen3.8-27B follows it is checked in the same screen.

## Agent screen (`et_screen.py run --probe agent`)

- **Items:** about 60 lead calls from the `wait` pilot (`pilot_wait`, Qwen3.8 on the pilot tasks), drawn with seed 2213:
  42 work calls, 9 first calls and 9 final calls, at most 2 per session, every one with at least one `[server]` line
  and from a session with at least 3 such calls.
- **Rewrite:** the newest `[server]` line, regenerated with the pinned harness's own `kv_line`, at KV 0.62 or 0.93,
  with its waiting count as recorded. The earlier lines stay as recorded.
  - *Amended 2026-11-02 22:06 UTC*, after 89 samples (set aside in `data/screen/agent/records_cap_superseded.jsonl`,
    not analyzed).
  - The freeze capped the earlier lines at 0.75, as topic 19's `cap` did. Topic 19 capped them because MiniMax's
    histories showed the rule ignored.
  - Qwen followed the rule live, so its histories have waits next to lines at 80 % or more. Capping made them read
    "the line said 75 % and I waited", which teaches waiting below the threshold. The first item waited in 4 of 4
    samples at 0.62, while live Qwen waited in 0.2 % of the calls below 80 %.
  - Leaving the earlier lines as recorded is what a live session shows the model.
- **Conditions:** `B0` (the request as recorded), `W` (wait v5 as recorded), `S1`-`S4` (`W` plus `save_context` with
  description s1-s4, advertised as the runs do: the tool list and "Also available: wait, save_context.").
- **Sampling:** 4 replies per prompt through vLLM's Anthropic endpoint, the model's sampling defaults (temperature 1.0,
  top_p 0.95, top_k 20), `max_tokens` 8,192; 16 requests in flight.
- **Measures** (work stratum; an item's value is the share of its samples; item means with a 95 % session-cluster
  bootstrap): wait, save_context, save with wait, wait-only, kept (the reply still makes one of the recorded work
  tool calls, or both are final answers).

## Controller screen (`et_screen.py run --probe controller`)

- **Snapshots:** every minute of the `none` pilot (`pilot_none`) from its third minute on, including its K sweep (16, 8,
  4): the last 60 s and the 5 minutes before as `et_hw.summarize` computes them, and the gate's state.
- **Reference direction** (`et_screen.label`, topics 11 and 18: keep the active sessions' contexts within the pool):
  `lower` when the active contexts exceed 0.9 of the KV cache, or the GPU prefix hit rate is under 50 % while requests
  are preempted or wait for KV capacity; `raise` when sessions wait at the gate and the active contexts plus the next
  waiting session's fit in 0.8 of the cache; else `keep`.
- **Conditions:** variants c1-c4 × reasoning effort default and low; 3 samples per snapshot; `tool_choice` required.
- **Measures:** the share of valid `set_session_limit` calls, and the share of valid calls whose K moves in the
  reference direction (overall and per direction).

## Rules (`screen_rules.json`)

- **`wait` v5 passes** with ≥ 30 % of work replies adding `wait` at 0.93, ≤ 10 % at 0.62, and `kept` at 0.93 within 10
  points of `B0`. Wait-only replies don't count against it (the user ruled them acceptable on 2026-11-01). If v5 does
  not pass, I report it and ask the user before changing the final prompt.
- **A `save_context` variant passes** with ≥ 30 % of work replies adding both `save_context` and `wait` at 0.93, ≤ 10 %
  adding `save_context` at 0.62, and wait-only replies at 0.93 no more than 10 points above `W`'s. The winner is the
  passing variant with the highest rate at 0.93; ties go to the lower number (the shorter text).
- **A controller condition passes** with ≥ 95 % valid calls and ≥ 70 % agreement. The winner is the passing condition
  with the highest agreement; ties go to the lower variant number, then the low effort.
- **If no variant passes** a screen, I stop and report to the user before the smoke runs.

## Known limits

- Replay is not live: the replies after the rewritten line are still the recorded ones.
- One model, one pilot's sessions; the controller's reference is a rule, not a measured optimum.

## Revised `save_context` screen (added 2026-11-02, before its first request)

The user chose it on 2026-11-02 after s1-s4 failed. s1 was the best at 18 % [9, 28] save-with-wait at 0.93; s2-s4 reached
0-6 %. `wait` v5 itself passed (63 % at 0.93, 5 % at 0.62).
- **Conditions:** on the same 60 items, 4 samples per prompt, the newest line at 0.62 and 0.93, the earlier lines as
  recorded. `B0` and `W` are the first screen's.
  - `S5`: s1's description, with the newest `[server]` line's cue naming the tool (`S15_STATUS_FORMAT=kv_cue_save`:
    "At 80% or more: add a wait call and a save_context call to your response.").
  - `S6`: description s6 (s1 plus a worked example), with the usual `kv_cue` line.
- **Rules:** as for s1-s4. The winner is the higher rate at 0.93, ties going to `S5`. If neither passes, I stop and
  report.
- **In the runs:** `S5` gives `lmc` S15_SAVE_TEXT=s1 and S15_STATUS_FORMAT=kv_cue_save. `S6` gives S15_SAVE_TEXT=s6
  with kv_cue.
