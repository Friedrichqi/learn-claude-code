# Pre-registration: a KV-threshold `wait` prompt for MiniMax-M2.7 (topic 19 follow-up, 2026-10-30)

Frozen before the probe's first request. `kvwait_prereg.sha256` lists, with repo-relative paths, this file,
`ef_kvwait.py`, `data/kvwait/items.json`, the live `s15_integrated_harness/hold_runtime.py`, `rd_cf.py`, `rd_rounds.py`,
`ef_render.py` and the template. `ef_kvwait.py run` refuses if any of them changed. Plan:
`followups/plan_1030_kvwait.md`.

## Question

The user asked (2026-10-30) for a `wait` prompt that MiniMax-M2.7 acts on when HBM (vLLM KV-cache) use approaches
80 %. In topic 19 it called `wait` 3 times in 21,081 lead calls. It got text v3, the "wait" paragraph and a share-view
line, which shows token counts and never the KV percentage, and nothing defined "crowded".

The question: with a stated threshold and a line that shows the number, does it add `wait` at ≥ 80 % and refrain
below?

## Design

- **Server:** topic 21's `s2a_probe` stage on GPUs 0-3, which topic 21 runs: MiniMax-M2.7 TP=4, config `profile`.
  The probe is a second client on that server (the user's choice). `run` refuses any server that is not an `s2a`
  stage with config `profile` serving MiniMaxAI/MiniMax-M2.7.
- **Items** (`data/kvwait/items.json`, seed 1030):
  - **Source:** lead calls of counted sessions of `w16_m_wait` and `w32_m_wait`. Fillers and the 2 sessions that
    called `wait` are left out, which leaves 240 sessions.
  - **Split:** the screen and the confirm draw from disjoint sessions (80 and 160), at most 2 items per session.
  - **Strata:** work (a reply with tool calls after the turn's first call), start (the turn's first call), final (the
    turn's answer).
  - **Counts:** screen 42 / 9 / 9; confirm 105 / 22 / 23.
  - Every `[server]` line of every item joins its status answer.
- **Rewrite** (`hold_runtime` itself builds every text). Three places change, each exactly once:
  - the "wait" paragraph;
  - the `wait` description;
  - every `[server]` block.

  Earlier lines show the KV use and queue the session saw then. The newest line, this call's, shows the level
  under test.
- **Conditions:**

  | name | `wait` text + paragraph | line |
  |---|---|---|
  | `B0` | v3 + "wait" | share (the request as recorded) |
  | `T` | v3 + "wait" | kv |
  | `R` | v5 + "wait_kv" | kv |
  | `C` | v3 + "wait" | kv_cue |
  | `RC` | v5 + "wait_kv" | kv_cue |

- **Sampling:** S = 4 samples per prompt, as topic 20's: temperature 1.0, top_p 0.95, top_k 40, max_tokens 8,192,
  and a seed per (session, turn, call, condition).
- **Screen:** 60 items × (`B0`, plus each of `T`, `R`, `C`, `RC` with the newest line at 0.62 and at 0.93).
- **Confirm:** 150 items × (`B0`, plus the winner at 0.60, 0.75, 0.85 and 0.95).

### Texts (S15_WAIT_KV = 0.8)

- **v5 description:** "Pause this session for \`seconds\` after this response's other tool calls, so the shared model
  server can catch up. Rule: whenever the newest [server] line shows the KV cache 80% full or more, include one wait
  call in your response next to your other tool calls; the harness runs them first and then pauses. Wait about 10-30
  seconds, longer when many requests are waiting. Below 80%, do not wait, and never add wait to your final answer.
  The result reports the server status after the pause."
- **wait_kv paragraph:** "Shared server: you share one model server with other sessions. When its KV-cache memory is
  full, it evicts cached context, and every session's next step must then recompute its context, so all sessions
  slow down. Each round ends with a [server] line that shows how full the KV cache is. Server rule: when that line
  shows 80% or more, include a wait call in your response next to your other tool calls; the harness runs them first
  and then pauses. Below 80%, keep working without waiting."
- **kv line:** `[server] KV cache 62% full; 3 requests waiting`.
- **kv_cue line:** the same; at 80 % or more, compared in the whole percentages shown, it adds `. At 80% or more: add
  a wait call to your response.`

## Measures (per sample; an item's value is the mean of its S samples)

| measure | definition |
|---|---|
| wait | the reply calls `wait` |
| wait-only | every call in the reply is `wait` |
| final | no tool call and not cut at max_tokens |
| kept | the recorded action is still issued (rd_cf's target-level keys; a final item's recorded action is the final answer) |
| output tokens | per sample |
| seconds | requested in the first `wait` |

A condition's rate is the mean over items. Its 95 % interval comes from a session-cluster bootstrap with 2,000 draws.

## Screen rule (`ef_kvwait.pick`)

1. **Eligible** means all of these hold:
   - the wait rate on work items at 0.62 is ≤ 10 %;
   - wait-only samples are ≤ 5 % of all samples, at either level;
   - the work items' kept rate at 0.93 is at least `B0`'s minus 10 points.
2. **The winner** has the highest work wait rate at 0.93 among eligible candidates. Within 5 points of the best, the
   order R, RC, C, T decides, the least per-round text first.
3. **No winner** (the best is below 30 %, or nothing is eligible) means no confirm runs. The result is reported as
   is. Any revised wording comes back to the user.

## Confirm: the prompt passes if all hold

- the work wait rate at 0.85 and at 0.95 is ≥ 50 %, with the lower 95 % bound ≥ 30 %;
- the work wait rate at 0.60 is ≤ 10 %;
- wait-only is ≤ 5 % of samples at every level;
- waits on final-answer items at 0.85 and 0.95 are ≤ 10 %;
- the work kept rate at 0.85 and 0.95 is within 10 points of `B0`;
- the p50 of output tokens is at most 15 % above `B0`'s.

**Exploratory:** the 0.75 level (how early "approaching 80 %" acts), the start stratum, the requested seconds,
rates by the number of earlier lines at or above 80 %, and thinking excerpts.

## Known limits

- **The histories are replayed as recorded.** The earlier replies never waited, and under kv_cue the earlier lines
  carry cues that those replies ignored. Live, the model would see its own earlier waits, so this probe may
  understate compliance in a running session.
- **No feedback.** A wait cannot lower the KV use the next line shows. Only a live cell, with its own server and
  real crowding, tests that. It is not part of this pre-registration.
- **Narrow scope:** one model, one workload (topic 11's four-turn code sessions), and contexts from N=16/32.

Any change after the freeze is logged in the topic README's Changes, with its time and reason.
