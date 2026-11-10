# 19 · Efficiency signals: does a stronger model use the server's state? (MiniMax-M2.7 rerun of topic 18)

> **Question.** Topic 18 exposed live vLLM status to the s15 harness and gave the model a `wait(seconds)` tool.
> With Qwen3-32B deciding, waiting did not relieve a crowded server. This topic asks two things:
> - Does a much stronger model, MiniMax-M2.7, use exposed system and efficiency metrics to organize its work?
> - Does that improve end-to-end efficiency when the server is crowded (N=16, N=32)?
>
> The model gets topic 18's `wait`, done right, plus three efficiency tools.
>
> **Plan:** `~/.claude/plans/now-i-want-to-jiggly-waffle.md` (approved 2026-10-26). Pre-registration: `prereg.md`
> (written after the pilot).
>
> **Budget:** GPUs 0-3 only (four H100 80GB), hard cap 120 GPU-h (projection ~97). MiniMax-M2.7 runs at TP=4, so
> cells run one at a time.
>
> **Status: complete.** The analysis is in the Short answer and S3.
> - **Dates (UTC):** S0 on 2026-10-26 before 03:40; smoke 04:00-04:58; pilot 04:59-08:55; main run 2026-10-26
>   08:56 to 2026-10-27 05:29; analysis 05:30-06:00.
> - **Follow-ups, 2026-10-29:**
>   - completion time per task, on CPU;
>   - a high-N follow-up (N=64 and N=128, 20:38-22:42 UTC).
> - **Follow-up, 2026-10-30/31:** a KV-threshold `wait` prompt and its replay probe (screen 19:56-21:39 UTC; revised
>   screen 00:39-01:01 UTC).
>   - No wording is usable.
>   - With the earlier lines capped below 80 %, MiniMax follows the rule (58 % at 93 %, 0 % at 62 %), but often waits
>     instead of working.
>   - See its section.
> - **GPU time:** 111.51 of the 120 GPU-h cap:
>   - smoke 3.69;
>   - pilot 15.57;
>   - main 81.01 (74.39 for the ten valid cells and 6.62 for an invalid first attempt);
>   - high-N follow-up 8.25;
>   - KV-threshold probe 1.57 (2026-10-30);
>   - its revised screen 1.42 (2026-10-31).
>
>   All of it was inside the user's lab GPU sheet claims.
> - **Commits:** none. Everything here is uncommitted.

**Contents** — Short answer · Why this rerun · Arms · What was built · S0 results · S1 smoke · S2 pilot · S3 main ·
Completion time per task · High-N follow-up · GPU-hour log · Reproduction · Changes

## Short answer

- **No.** MiniMax-M2.7 does not use the efficiency signals, so they do not change how efficiently it works.
  - In the five model-arm cells it made 3 pacing calls in 21,081 lead calls. All three were `wait` calls attached to
    a final answer. It never called `wait_for_capacity` or `server_status`.
  - Its reasoning mentions the server twice in 21,081 calls. It never mentions the `[cost]` receipt, other
    sessions, `wait_for_capacity` or `server_status`.
  - The paragraph does reach its thinking: it plans work "in parallel" about twice as often as in `none`.
  - Otherwise it works much as it does without the signals:
    - multi-tool responses rise no more than in `sgate`, which has no text;
    - it makes about one fewer call per turn;
    - its median output is 6-14 % longer per call.
- **The pre-registered decisions** (completed turns/h; floor F = 10 % of `none`):
  - R1 is "neither" for both model arms at N=16 and N=32, with R from −0.18 to 0.09;
  - R2 shows no clear difference between the toolbox and plain `wait`;
  - R3, no harm at N=8, passes.
- **The gain the signals were meant to unlock exists,** and the server-side gate (K=8) takes it:
  - turns/h 1.44× `none`'s at N=16 and 1.42× at N=32;
  - hit rate 14 % → 82 % and 1.4 % → 82 %;
  - turn p95 2.2× and 3.7× lower;
  - 30 % fewer GPU-seconds per turn.
- **The user's first concern, that Qwen3-32B was too weak to time the wait, is answered no.**
  - The stronger model does not time it better: it does not wait at all.
  - Topic 18's Qwen3-32B acted on the same kind of signal too often, in loops, at 0.7-0.9× `none`.
  - MiniMax-M2.7 ignores it, at 0.92-1.04× `none`.
  - Neither model turns the signal into efficiency. Only mechanisms outside the model do: topic 11's server gate,
    here and in topic 18, and topic 18's harness-decided twins.
- **The user's second concern, that N ≤ 32 was too few users for four GPUs, is also answered no** (high-N
  follow-up, 2026-10-29).
  - At N=64 and N=128 the status line showed the sessions ahead holding 3.1× and 5.7× the KV pool, with 51 and 113
    requests waiting.
  - The model put `wait` in 1 of 1,778 and 0 of 1,849 calls. The one wait was a 1-s "no-op".
  - The extra users only queued: the call median went from 34 s to 78 s and 160 s at the same throughput, and the
    task median doubled with each doubling of N.
- **Caveats:**
  - one 90-min cell per arm (the floor F stands for the between-cell spread);
  - the model arms ran 2-12 h after their references;
  - one wording of the texts;
  - one workload: topic 11's four-turn code sessions, on one TP=4 server.

## Why this rerun

- **The tool was in the right place; the model never used it that way.** `wait` sat in the tool list of every
  request (`s15_integrated_harness/code.py:3215`) and in the system prompt's tool line (`code.py:969-974`). A
  `wait` call ran after the round's other tools (`code.py:3788-3867`), and its description asked for exactly that.
  - Even so, Qwen3-32B sent `wait` alone in 7,501 of 7,502 wait rounds of topic 18's six model cells.
  - It made one tool call per response in 98.7-99.5 % of all its responses, in every arm checked.
  - So each wait became an extra model call that re-prefilled the evicted context: 22-32 % of GPU time at N ≥ 16.
- **MiniMax-M2.7 is far stronger in this harness.** Its topic-16 pass@1 against Qwen3-32B's:

  | benchmark | MiniMax-M2.7 | Qwen3-32B |
  |---|---|---|
  | SWE-bench Verified | 0.644 | 0.09 |
  | SWE-bench Pro | 0.867 | 0.07 |
  | Terminal-Bench | 0.478 | 0.02 |

  It also thinks before every call. Even so, it made one tool call per response in 87.6 % of topic 16's 13,463 lead
  calls (94.1 % on SWE). So this round does not count on combining: a system paragraph asks for it, and a blocking
  `wait_for_capacity` costs one model call per hold.

## Arms

| arm | system prompt | pushed every round | tools added | who holds, when |
|---|---|---|---|---|
| `none` | — | — | — | nobody |
| `sgate` | — | — | — | the server: topic 11's session gate, K by the pilot rule, 5 s idle |
| `m_wait` | "shared server" paragraph (wait version) | `[server]` share line (topic 18's `m_share` line) | `wait(seconds)`, text v3 | the model, after the round's other tools |
| `m_tools` | "shared server" paragraph (tools version) | `[cost]` receipt of the session's own last step | `wait`, `wait_for_capacity`, `server_status` | the model decides; the harness times a step-back's release |

- **`wait_for_capacity(max_seconds)`** runs at the end of the round. It then holds the session until the sessions
  ahead of it plus its own context fit in 0.9 of the pool, or until `max_seconds` (at most 1,800).
- **`server_status()`** returns the stock `/metrics` fields and the session's share fields.
- **The `[cost]` receipt** is pushed every round. Example: "[cost] your last step: 24,512 prompt tokens, 18,304
  reused from the server's cache, 4,806 recomputed because they had been evicted, 1,402 new or changed; queued
  4.1 s; 412 tokens generated; 14.2 s in all."
- **The texts** are in `s15_integrated_harness/hold_runtime.py` (`WAIT_TEXTS["v3"]`, `YIELD_TOOL`, `STATUS_TOOL`,
  `SERVER_SECTIONS`).

## What was built (S0, CPU)

**The harness: `s15_integrated_harness/`.** Every change is default-off.
- **`hold_runtime.py`:**
  - flags: `S15_TOOLS` (any of wait, wait_for_capacity, server_status; `S15_WAIT_TOOL=1` still means wait),
    `S15_SERVER_SECTION` (none, wait or tools) and `S15_RECEIPT`;
  - `run_wait_for_capacity`, which reuses the share rule and the heartbeat; `run_server_status`;
  - `note_call` and `receipt_line`. The once-per-round append now carries the status line and/or the receipt.
- **`code.py`:**
  - the server paragraph after the efficiency section;
  - the pacing tools and their handlers, in the lead, teammate and subagent loops;
  - `wait` and `wait_for_capacity` deferred to the end of the round;
  - a status read does not count toward the todo reminder;
  - each lead call timed for the receipt;
  - `S15_MAX_TOKENS`, `S15_MAX_TOKENS_ESCALATED` and `S15_SUMMARY_MAX_TOKENS` (defaults 8,000 / 16,000 / 2,000);
  - the compaction summary drops a leading `<think>…</think>`.
- **Tests:** `tests/test_s15_server_hold.py`, 20 tests, 7 of them new.

**The server: `research/18_harness_wait/`.**
- `wt_scheduler.py` keeps each session's last lead request's first admission (np, ideal, hit, queue wait).
  `wt_server_ext.py` serves it as `me.last`; the receipt reads it.
- `wt_pipeline.py`:
  - GPU group `Q` (GPUs 0-3, CPUs 0-51,104-155), which runs alone;
  - the model taken from the condition;
  - the cap, the data root and the lanes from the environment;
  - the GPU-hour estimate scaled by GPU count and a per-condition startup allowance;
  - a check of the scheduler's tool-call token.
- **Topic 11:** `mu_scheduler.py` logs the tool-call token in its header; `mu_sweep.py` accepts a condition's own
  session-length estimate; `mu_analyze.py` makes the template tail (`TEMPLATE_TAIL`) a constant.
- **Tests:** `test_wt_scheduler.py` (5 tests, 1 new) and `test_wt_server_ext.py` (8, 1 new).

**This folder.**
- **`minimax_m2_append_only.jinja`:** topic 16's append-only template, with two fixes.
  - The text parts of one message are joined with a newline. vLLM passes this template content lists, so a turn
    prompt and the `[cost]` line used to run together.
  - A reasoning block the model closed at once is rendered as it was generated.
- **`ef_render.py`:** vLLM 0.31's own request rendering on CPU.
- **`ef_calibrate.py`:** the context limit and the gate anchor.
- **`ef_workloads.py`:** settings, arms and conditions.
- **`ef_pipeline.py`:** `wt_pipeline` on group Q, with this topic's cap.
- **`ef_analyze.py`:** smoke checks, pilot rules, tables and decisions.
- **Tests:** `test_minimax_template.py`, `test_ef_e2e.py` and `test_ef_analyze.py`.
- **The one allowed text revision, written before pilot P2 ran:** `WAIT_TEXTS["v4"]` and the paragraph
  `SERVER_SECTIONS["wait_v2"]`. Each adds one sentence: "Never reply with only wait: if you would only wait, take
  your next useful step and add wait to that response." They are used only if P2 trips the plan's rule, as cell
  `p16_m_wait_v4`.

## S0 results

- **The template keeps every prompt append-only** (`test_minimax_template.py`, vLLM's own rendering on CPU).
  - 225 of 225 recorded topic-16 call pairs (SWE, Pro, Terminal-Bench) are text and token prefixes of the next.
  - So are the synthetic 3-turn sessions of every arm, across final answers and new turns.
  - Before the fixes, 6 of the 225 broke at the generation prompt: the model had closed its reasoning at once.
  - For comparison, the vendor template breaks 8 of 9 synthetic pairs.
- **Context limit** (`ef_calibrate.py`, 400 of topic 18's lead requests over 30k characters, rendered for MiniMax):

  | | MiniMax-M2.7 (topic 19) | Qwen3-32B (topic 18) |
  |---|---|---|
  | characters per token, 5th percentile | 4.276 | 4.33 |
  | fixed prompt (tokens) | 2,573 (`none`), 3,198 (`m_tools`) | 1,795 |
  | window, output reserved | 65,536, 32,000 | 40,960, 8,000 |
  | history budget | 30,338 tokens | 31,165 tokens |
  | context limit | **129,000 characters** | 134,000 characters |

- **Gate anchor:** topic 18's `w08_none` lead calls averaged 23,322 prompt tokens. The rule is
  K = clip(round(8 × 23,322 / P1 `none8`'s mean), 4, 12).
- **End to end** (`test_ef_e2e.py`): real sessions through `wt_sweep`, `mu_session`, `profile_run` and the harness
  confirm, for every client arm:
  - the model id, the 16,000-token budget and the 129,000-character limit;
  - each arm's tools, paragraph, lines and receipts;
  - the receipt's eviction split;
  - the status reads, and step-backs that run after the round's other tools.

## S1 smoke

**Setup:** four ~15-min cells on GPUs 0-3, run one at a time. N=4 users on a 2,200-block pool (35,200 tokens) in a
32,768-token window, with KV events on.

**`s_m_tools`** (04:00-04:15 UTC, 0.96 GPU-h):
- **Server:** ready in 140 s; the pipeline's checks pass (WTScheduler, vLLM 0.31.0, the pool, tool-call token
  200052, `/wt/status`).
- **Integrity:** 328 of 328 lead calls joined to the server. The admission hit equals `cache_read_input_tokens` on
  every call.
- **Evictions:** the observer's count equals the KV-event removals (148,388; no gaps).
- **Preemptions:** vLLM's counter equals the observer's (8).
- **Status age:** p50 224 ms, p99 495 ms. That is by construction: the stock fields are sampled every 0.5 s.
- **The scheduler's tool-call token** agrees with the replies that called a tool on 100 % of calls.
- **Receipts:** 321 of 321 `[cost]` receipts match the server's record of the call they describe (prompt, reuse,
  eviction split, queue wait).
- **Append-only:** the previous prompt is reused on 212 of 212 pairs the harness did not rewrite, including 7 of 7
  turn boundaries. The previous reply is reused in full on 209 of 212 (98.6 %).
  - The three misses are replies whose text contains a triple newline before a tool call. The template's split of
    reasoning from text is then ambiguous. Topic 16 saw the same residual.
  - Each miss costs that reply's tokens (400-850).
  - The 109 other pairs are the harness's own rewrites: micro_compact placeholders, frequent at the smoke's small
    context limit.
- **Natural pool: 15,174 blocks, below the 16,137 pin.**
  - Cause: vLLM's default `max_num_seqs` (1,024) makes its memory profile reserve logits for 1,024 sequences over
    the 200k-token vocabulary.
  - Every topic-19 cell now runs with `--max-num-seqs 64`. With at most 32 sessions, 64 never binds.
  - The next cell's natural pool was 17,317 blocks (277,072 tokens), 7 % above the pin.
- **Behaviour:**
  - The model put two or more tool calls in 165 of its 328 responses (topic 16, SWE: 6 %).
  - It never called `wait`, `wait_for_capacity` or `server_status`, though its receipts often showed most of the
    prompt recomputed (p90: 94 %).
  - Output tokens per call: p50 149, p95 1,504; no max_tokens escalation.

**`s_none`, `s_m_wait` and `s_gate2`** (04:17-04:58 UTC, 2.7 GPU-h; server ready in 75 s once the weights were in
the page cache):

| | `s_none` | `s_m_wait` | `s_gate2` |
|---|---|---|---|
| lead calls joined; hit = `cache_read` | 404 / 404; 100 % | 381 / 381; 100 % | 449 / 450; 100 % |
| evictions: observer / KV events | 112,557 / 113,075 | 154,811 / 154,812 | 64,582 / 64,610 |
| preemptions: vLLM / observer | 6 / 6 | 5 / 5 | 0 / 0 |
| tool-call token agrees | 100 % | 100 % | 100 % |
| prompt reused (pairs not rewritten) | 292 / 292 | 278 / 278 | 341 / 341 |
| reply reused in full | 285 / 292 | 271 / 278 | 336 / 341 |
| of these, turn boundaries | 13 / 14 | 12 / 13 | 9 / 9 |
| natural pool (blocks) | 17,317 | 17,340 | 17,340 |
| gate: most sessions active | — | — | 2 (K=2) |

- **The unjoined call in `s_gate2`** was presumably in flight when the window ended.
- **The eviction counts differ by at most 0.5 %.** Topic 18's smoke matched in two of four cells too, with similar
  small gaps in the others.
- **The reply misses are rare and cheap.** Mid-turn misses are the triple-newline case above.
  - The two turn-boundary misses are final answers whose reuse stopped in the block holding the `</think>` boundary.
    The text there has the usual form `\n</think>\n\n`.
  - The likely cause is the model's sampled tokens differing from the canonical tokenization of the same text. The
    template cannot fix that.
  - It costs part of one reply, in every arm alike.
- **`s_m_wait`:** every lead call carried exactly one `[server]` share line, and 17.8 % of them showed the server
  crowded (sessions ahead + own context > pool). The model never called `wait`.

**Verdict:** the smoke passes. The prompts are append-only across turns, the receipts are exact, the gate holds K,
and the observer agrees with vLLM. Cost: 3.7 GPU-h. The model's indifference to the signals in the smoke (N=4) is a
behaviour to measure in the pilot, not a defect.

## S2 pilot

**Setup:** 50-min cells: 15 min warm-up, a 30-min window, the pool pinned at 16,137 blocks. Order: P1 `p08_none`,
`p16_none`; P2 `p16_m_wait`; P3 `p16_m_tools`; then P4 `p16_sgate`, once K is set from P1.

**P1 `p08_none`** (04:59-05:46 UTC, 3.15 GPU-h):

| turns/h | work lead calls/h | turn p50 / p95 (s) | hit rate | GPU: decode / fresh prefill / evicted recompute | busy | output tokens p50 / p95 |
|---|---|---|---|---|---|---|
| 350 | 3,462 | 37 / 149 | 85.1 % | 89.9 % / 8.4 % / 1.6 % | 99.9 % | 164 / 1,528 |

- **MiniMax works very differently from Qwen3-32B** (topic 18's `w08_none`):
  - about 10 lead calls per turn, against 47;
  - a median turn of 37 s, against 100 s;
  - 350 turns/h, against 142.
- **The server is saturated by decoding already at N=8.** It is busy 99.9 % of the window, 90 % of that decoding
  the model's thinking and replies.
- **The rules fixed in the plan give:**
  - N_light = 8 (hit rate ≥ 80 % and evicted recompute ≤ 10 %);
  - K = clip(round(8 × 23,322 / 22,748), 4, 12) = 8.
- **Step model** refit on this cell: R² 0.969 (topic 18, Qwen: 1.000).

**P1 `p16_none`** (05:46-06:33 UTC, 3.10 GPU-h), next to `p08_none` (step model refit on both: R² 0.992):

| cell | turns/h | work lead calls/h | turn p50 / p95 (s) | call p50 (s) | hit rate | GPU: decode / fresh / evicted | preemptions |
|---|---|---|---|---|---|---|---|
| `p08_none` | 350 | 3,462 | 37 / 149 | 2.4 | 85.1 % | 89.8 % / 8.5 % / 1.6 % | 0 |
| `p16_none` | 306 | 2,836 | 108 / 466 | 11.2 | 17.6 % | 61.0 % / 6.1 % / 32.9 % | 19 |

- **The regime gate passes, narrowly:** at N=16 the hit rate is 17.6 % (< 50 %) and evicted recompute is 32.9 % of
  GPU time (> 30 %).
- **Contention costs MiniMax far less than it cost Qwen.** Going from 8 to 16 users loses 13 % of the turns per
  hour (Qwen, topic 18: 74 %), although a third of the GPU now goes to recomputing evicted prefixes.
  - The reason: decoding the model's thinking and replies still takes most of the GPU.
  - Turns take 3× longer at the median, and calls 4.7×.
- **Warm-up filler estimates** (complete sessions' mean wall time): 316 s at N=8, 713 s at N=16. N=4 and N=32 are
  scaled by topic 11's ratios: 287 s and 1,121 s.

**P2 `p16_m_wait`** (06:33-07:19 UTC, 3.1 GPU-h):

| cell | turns/h | work lead calls/h | turn p50 / p95 (s) | hit rate | GPU: decode / evicted | `wait` calls | overhead-only calls |
|---|---|---|---|---|---|---|---|
| `p16_none` | 306 | 2,836 | 108 / 466 | 17.6 % | 61.0 % / 32.9 % | — | — |
| `p16_m_wait` | 306 | 3,040 | 114 / 391 | 16.5 % | 57.4 % / 36.1 % | 2 | 0 |

- **MiniMax all but ignores the wait tool.** In about 1,500 lead calls it called `wait` twice, both times next to
  work, asking for 1 s. 32 % of the `[server]` lines it read showed the server crowded (sessions ahead + own context
  > pool).
- **The plan's text-revision rule is not triggered:** 0 wait-only calls per 100 work calls, and no session with a
  run of wait-only rounds. No P2b; the main run keeps text v3.
  - Topic 18's Qwen3-32B waited too much, in loops of wait-only rounds.
  - MiniMax-M2.7, given the same information and a sentence of policy, does not wait.
- **Other behaviour:**
  - 2.8 tool calls per work response when its line showed crowding, against 2.1 when calm. This is not causal:
    crowding coincides with phases of a task;
  - 9.8 lead calls per turn;
  - almost no exact re-reads of unchanged files: 0.4 per 100 reads, against topic 18's Qwen at 26-30.

**P3 `p16_m_tools`** (07:19-08:06 UTC, 3.1 GPU-h):

| cell | turns/h | work lead calls/h | turn p50 / p95 (s) | hit rate | evicted recompute | pacing calls |
|---|---|---|---|---|---|---|
| `p16_m_tools` | 262 | 2,684 | 116 / 441 | 6.5 % | 37.7 % | none (0 waits, 0 step-backs, 0 status reads) |

- **MiniMax ignores the whole toolbox.** In about 1,340 lead calls it never called `wait`, `wait_for_capacity` or
  `server_status`.
  - Every round it read a `[cost]` receipt, and the hit rate was 6.5 %, so most receipts reported most of the prompt
    recomputed after eviction.
  - Its own status showed the server crowded 34 % of the time.
- **The throughput and hit rate gaps to `p16_none`** (262 against 306 turns/h; 6.5 % against 17.6 %) are within
  what single 30-min pilot windows vary by.
- **P1-P3 together:** given the server's state, its own costs and the tools to act on them, plus a paragraph that
  says what crowding costs and what to do, MiniMax-M2.7 does not change how it works. Topic 18's Qwen3-32B acted on
  the same information too often and in loops. Neither model uses it well.

**P4 `p16_sgate`, K=8** (08:07-08:55 UTC, 3.12 GPU-h; step model refit on the five pilot cells: R² 0.994):

| cell | turns/h | work lead calls/h | turn p50 / p95 (s) | call p50 (s) | hit rate | evicted recompute | GPU-s per turn | preemptions |
|---|---|---|---|---|---|---|---|---|
| `p16_none` | 306 | 2,836 | 108 / 466 | 11.2 | 17.6 % | 32.6 % | 47 | 19 |
| `p16_sgate` | 410 | 4,004 | 91 / 188 | 3.9 | 80.5 % | 5.0 % | 35 | 0 |

- **The gate recovers most of the cache:** hit rate 80.5 % against 17.6 %, evicted recompute 5 % against 33 %.
- **Its throughput gain for MiniMax is modest:** 1.34× turns/h (topic 18, Qwen at N=16: 3.8×). The turn p95 falls
  2.5× and GPU-seconds per turn fall a quarter.
  - The reason: decoding takes most of MiniMax's GPU time, so there is less recompute to win back.
- **By the plan's rule K stays 8:** the gate's hit rate is 80.5 %, threshold 80 %.

**The pilot's verdict.**
- The regime gate passes, and K, N_light and the warm-up estimates are set by the rules.
- The text-revision rule is not triggered.
- The model arms barely use their tools, which makes the main run's model arms close copies of `none`. The
  pre-registration therefore runs the reference cells first and leaves the user an amendment slot for the model
  arms, to be decided on the pilot alone.

## Pre-registration

`prereg.md` was frozen 2026-10-26 08:55 UTC; `prereg.sha256` holds the hashes of it, the template, the harness, the
workload settings and the analysis code.
- **Cells:** block 1 (references) `w16_sgate`, `w16_none`, `w32_none`, `w32_sgate`; block 2 (model arms, default)
  `w16_m_tools`, `w16_m_wait`, `w32_m_wait`, `w32_m_tools`; block 3 `w08_none`, `w08_m_tools`.
- **Primary metric:** completed turns/h.
- **Rules:** R1 recovery, R2 toolbox vs wait, R3 no harm (floor 10 % of `none`), the quality guard (5 points) and
  behaviour B1-B5.

## S3 main

- **Block 1, first attempt** (started 08:56 UTC): its first cell, `w16_sgate`, failed.
  - It used up the manifest's 128 counted specs 14 min before its window's end, and the sweep stopped it
    (INCOMPLETE). The chain stopped with it.
  - Amendment 1 in `prereg.md` makes the main cells cycle through the specs.
  - The partial cell is kept in `data/cells_invalid/w16_sgate_a1`.
- **Block 1, restarted** at 10:47 UTC (`w16_sgate`, `w16_none`, `w32_none`, `w32_sgate`), until about 18:30 UTC.
  Its results are held back until the model arms are settled (prereg, amendment slot).
- **Amendment 1's check, early** (12:21 UTC): `w16_sgate` queued pass 2. Its first four pass-2 sessions (`S001`,
  `S002`, `S004`, `S007`) ended `ok`. Each first call had the same prompt as its pass-1 twin (2,643-2,661 tokens)
  and the same cache hit (320 tokens, the prefix every session shares). The full check comes with the analysis.
- **Block 1 done** at 18:15 UTC. All four cells are valid by the prereg's rules:
  - no server `err` record;
  - every finished lead call joined to its server record;
  - every window completed, and GPUs 0-3 were empty after each cell.
- **Block 2** began at 18:17 UTC with `w16_m_tools`, which is in every option of the model-arm decision. The
  decision stayed open until it ended.
- **`w16_m_tools` done** at 20:09 UTC, valid: no `err` record, all 4,894 lead calls joined, window complete, and
  GPUs 0-3 empty.
- **No amendment was made,** so blocks 2 and 3 ran as frozen. The other five cells (`w16_m_wait`, `w32_m_wait`,
  `w32_m_tools`, `w08_none`, `w08_m_tools`) ran as one chain from 20:11 to 05:29 UTC (log
  `data/jobs/chain_main_block2b.log`). The frozen files still matched `prereg.sha256` (9/9) at launch.
- **All ten cells are valid:**
  - no server `err` record;
  - 0 unjoined lead calls (4,824-6,376 per cell);
  - every window complete, and GPUs 0-3 empty after each cell.

  `ef_analyze.py` regenerates `data/tables/main.md` and `main.json` byte for byte. `ef_verify.py` agrees on all 10
  cells (turns/h, work calls/h, overhead calls, hit rate).

### Results (90-min windows; `data/tables/main.md`)

| N | cell | turns/h [95 %] | × `none` | work lead calls/h | turn p50 / p95 (s) | GPU-s per turn | hit rate | GPU on evicted recompute | output tokens p50 |
|---|---|---|---|---|---|---|---|---|---|
| 16 | `none` | 276.7 [190, 356] | 1 | 3,016 | 110 / 559 | 52.0 | 13.9 % | 37.0 % | 158 |
| 16 | `sgate` | 397.3 [378, 424] | 1.44 | 4,177 | 93 / 253 | 36.2 | 81.7 % | 4.8 % | 160 |
| 16 | `m_wait` | 288.0 [240, 365] | 1.04 | 2,943 | 105 / 562 | 50.0 | 17.3 % | 34.5 % | 174 |
| 16 | `m_tools` | 255.3 [214, 298] | 0.92 | 2,611 | 119 / 649 | 56.4 | 6.6 % | 35.9 % | 180 |
| 32 | `none` | 256.7 [199, 320] | 1 | 2,865 | 281 / 1,690 | 56.1 | 1.4 % | 40.8 % | 158 |
| 32 | `sgate` | 365.3 [313, 421] | 1.42 | 4,103 | 265 / 451 | 39.4 | 82.3 % | 4.4 % | 173 |
| 32 | `m_wait` | 244.7 [198, 294] | 0.95 | 2,702 | 267 / 1,780 | 58.9 | 1.3 % | 39.0 % | 168 |
| 32 | `m_tools` | 250.7 [205, 290] | 0.98 | 2,595 | 270 / 1,424 | 57.4 | 1.3 % | 37.7 % | 178 |
| 8 | `none` | 318.0 [266, 361] | 1 | 3,613 | 38 / 192 | 45.2 | 86.1 % | 1.5 % | 162 |
| 8 | `m_tools` | 300.0 [242, 347] | 0.94 | 3,201 | 40 / 207 | 47.9 | 84.9 % | 1.7 % | 183 |

No cell had a preemption. One `max_tokens` escalation happened in all, in `w08_m_tools`.

**Decisions** (pre-registered; completed turns/h; block bootstrap, 4,000 draws):

| contrast | gain [95 %] | R [95 %] | verdict | R on work calls |
|---|---|---|---|---|
| R1 `m_wait` @16 | 11.3 [−36.0, 62.0] | 0.09 [−0.41, 0.42] | neither | −0.06 |
| R1 `m_tools` @16 | −21.3 [−60.7, 22.0] | −0.18 [−0.71, 0.14] | neither | −0.35 |
| R1 `m_wait` @32 | −12.0 [−44.7, 20.7] | −0.11 [−0.52, 0.16] | neither | −0.13 |
| R1 `m_tools` @32 | −6.0 [−35.3, 23.3] | −0.06 [−0.41, 0.19] | neither | −0.22 |
| R2 `m_tools` − `m_wait` @16 | −32.7 [−67.3, 0.7] | — | no clear difference | — |
| R2 `m_tools` − `m_wait` @32 | 6.0 [−21.3, 35.3] | — | no clear difference | — |
| R3 `m_tools` − `none` @8 | −18.0 (floor 31.8; hit rate −1.1 points) | — | no harm | — |

- **Neither model arm recovers any of the gate's gain.** Every R interval covers 0, and none reaches 0.5.
  - At N=16, `m_tools` falls short of "toolbox worse" only narrowly: the upper bound is +0.7 turns/h, and the
    threshold was −F = −27.7.
  - Given B1-B5 below, the model arms differ from `none` only in their texts, and the gaps are within the
    between-cell spread.
- **Quality guard:** 98.9-100 % of turns ended normally in every cell. 93.9-100 % of complete sessions' M turns
  made their edit. Each model arm is within 2.8 points of `none` at its N, so no gain or loss is from doing less work.
- **Contention costs MiniMax little, and the gate wins it back.**
  - `none` loses 13 % of its turns/h from N=8 to N=16, and 19 % to N=32.
  - The gate at K=8 is above N=8's `none` at both loads.

**Behaviour** (B1-B5; the five model-arm cells, 21,081 lead calls in their windows):

- **Pacing calls:** 3 in all, all `wait` and all in `w32_m_wait`:
  - 30 s and 15 s in one session (`S012`, turns 1 and 2), both with the reason "Server crowded; pause after
    delivering result";
  - 1 s in `S092`: "Allow server to catch up on queued requests".

  It made no `wait_for_capacity` call and no `server_status` read. Overhead-only calls are 0.0-0.1 per 100 work
  calls, using 0-0.2 % of GPU time.
- **Placement (B1):** each `wait` was attached to the turn's final answer, with no work tool beside it.
  - The deferral then cost one more model call, in which the model re-stated or acknowledged its answer (38-349
    output tokens).
  - In `S092` it read the returned `[server]` line as an empty user message: "The user hasn't said anything - this
    is just a server status message".
  - By the share rule, the server was crowded for only one of the three waits (`S092`: 315,768 tokens ahead, plus
    19,272 of its own, against a 258,176-token pool). For `S012`, 26,936 and 158,397 tokens were ahead. The model
    seems to have read "17-20 requests waiting" as crowding.
- **Tracking (B3):** P(step-back | crowded) − P(step-back | calm) is 0 to −0.1 points (threshold 20). The status the
  harness fetched showed crowding before 31-35 % of calls at N=16 and 67 % at N=32.
- **Workflow (B4), against `none` and `sgate`:**
  - **Multi-tool responses** are 37.6-41.2 % of work responses in the model arms, against 35.7-41.0 %.
  - **Calls per turn:** 9.5-10.3 against 10.3-11.3 in `none` and 10.5-11.2 in `sgate`.
  - **Exact re-reads:** 0.4-0.8 per 100 reads, against 0.3-0.6.
  - **Output tokens per call (p50):** 168-183 in the model arms, against 158-162 in `none` and 160-173 in `sgate`.
    `m_tools` is the highest at every N (178-183), so the receipts may lengthen its thinking slightly. This is
    descriptive and not tested.
  - **Crowded against calm,** at N=16 and 32: model arms put 2.5-3.0 work tools in a response when crowded, against
    1.8-2.2 when calm.
    - The gap is the same in `m_wait`, which sees the server line, and in `m_tools`, which sees only its own receipt.
    - As in the pilot, this is the phase of the task, not a reaction.
- **Information seeking (B5):** 0 status reads per 100 work calls.
- **What the model thinks about.** This was an exploratory keyword scan, not pre-registered, of every lead call's
  thinking (`<think>`, a median of 127-150 characters per call).
  - **The signals come up twice in 21,081 model-arm calls.**
    - In `w16_m_wait`: "Actually, looking at the server load, I should be efficient. Let me read a few files at a
      time…".
    - In `S092`, the `[server]` line as "just a server status message".
  - **Never mentioned:**
    - the `[cost]` receipt;
    - reused or recomputed tokens;
    - other sessions;
    - `wait_for_capacity` and `server_status`.

    `wait` comes up only in the two calls after `S012`'s waits ("The wait completed").
  - **Batching comes up twice as often.** 1.8-2.9 % of model-arm calls plan to do things "in parallel", against
    0.8-1.3 % in `none` and `sgate`. "In one (or the same) response" appears in 18 `m_tools` calls, 3 `m_wait`
    calls and 1 call of the other arms.
    - The paragraphs' only advice on form is to pause "at the end of a response that also does work".
    - The share of multi-tool responses still rose only 1.9-4.1 points over `none`. `sgate`, which has no text,
      rose 2.5-5.3.
  - **"evict"** appears in 4 calls, all about the harness's own trimming of old tool results ("the files were in a
    cache that was evicted").
  - **"server"** appears in 0.04-0.15 % of calls in every arm, `none` included, almost always about the code.

  The model reads the signals every round, but it does not reason about the server. The paragraph's advice about
  combining calls shows up in how it talks about batching, but not in how it batches.

**Amendment 1's check** (pass-k first calls must not hit more cache than pass-1 first calls):
- 105 of the 1,333 counted sessions that made a call were pass 2: `w16_sgate` 47, `w32_sgate` 32, `w08_none` 18,
  `w08_m_tools` 6 and `w16_m_wait` 2. None reached pass 3.
- **At N=16 and 32,** all 81 pass-2 first calls hit exactly 320 tokens. That is the prefix every session shares,
  and the same as 963 of the 972 pass-1 first calls there.
- **At N=8,** pass-2 first calls hit 2,560 tokens (`m_tools`: 3,200) in 18 of 24 cases. Pass-1 first calls hit the
  same length in 225 of 256.
  - Every first-call hit above 320 tokens, in any pass, stops 76-114 tokens before the prompt's end.
  - A cached copy of the pass-1 twin's identical prompt would have been hit to within one block (16 tokens).
- **No excess:** pass 2 reused nothing from pass 1.

## Completion time per task (exploratory, added 2026-10-29)

The run reported completed turns/h. This section derives what a single task experiences, from the traces on disk,
on CPU: `research/18_harness_wait/wt_latency.py --topic 19`, with tables in `data/tables/latency.{md,json}`. It was
not pre-registered.

**Definitions** (as in topic 18's section):
- **Task:** one turn. **Completion time:** `turn_start` to `turn_end`. It includes queueing, model calls, tools,
  waits and gate waits, but not think pauses.
- **Kaplan-Meier (KM):** over the turns of counted sessions that started in the window, censored at its end. Here
  only 1-8 % of them were cut.
- **Mean:** the area under the curve.
- **Intervals:** 95 % intervals from a bootstrap over task specs.

| cell | tasks | cut | mean (s) | p50 | p95 | p99 | mean ÷ none | p50 ÷ none | p95 ÷ none |
|---|---|---|---|---|---|---|---|---|---|
| w08_none | 478 | 5 | 56 [51, 61] | 38 [32, 43] | 192 [142, 233] | 306† | — | — | — |
| w08_m_tools | 452 | 7 | 62 [57, 68] | 40 [37, 43] | 207 [166, 256] | 380† | 1.10 [0.98, 1.23] | 1.05 [0.90, 1.22] | 1.08 [0.78, 1.56] |
| w16_none | 410 | 11 | 175 [159, 192] | 110 [99, 121] | 559 [422, 783] | 973† | — | — | — |
| w16_sgate | 599 | 14 | 111 [106, 116] | 93 [89, 98] | 253 [215, 283] | 399 [330, 481] | 0.70 [0.64, 0.76] | 0.84 [0.75, 0.96] | 0.45 [0.31, 0.63] |
| w16_m_wait | 431 | 14 | 165 [150, 180] | 105 [95, 118] | 562 [427, 687] | 833† | 0.94 [0.83, 1.07] | 0.95 [0.83, 1.13] | 1.00 [0.63, 1.50] |
| w16_m_tools | 383 | 15 | 192* [170, 215] | 119 [106, 133] | 649 [496, 819] | 1,114† | 1.10 [0.94, 1.26] | 1.08 [0.92, 1.28] | 1.16 [0.76, 1.76] |
| w32_none | 383 | 29 | 438 [389, 490] | 283 [246, 314] | 1,690 [1,123, 1,964] | 2,956† | — | — | — |
| w32_sgate | 544 | 26 | 280 [273, 288] | 265 [260, 273] | 451 [403, 485] | 725 [560, 830] | 0.78 [0.73, 0.85] | 0.94 [0.84, 1.08] | 0.27 [0.23, 0.40] |
| w32_m_wait | 368 | 29 | 460* [400, 523] | 267 [238, 300] | 1,780 [1,302, 2,131] | 3,168† | 1.04 [0.87, 1.22] | 0.94 [0.82, 1.12] | 1.05 [0.76, 1.57] |
| w32_m_tools | 378 | 29 | 439* [387, 499] | 271 [248, 307] | 1,424 [1,069, 1,789] | 2,969† | 0.98 [0.83, 1.16] | 0.96 [0.83, 1.14] | 0.84 [0.61, 1.31] |

`*`: under 1 % of tasks were still running at the longest follow-up. †: fewer than 5 tasks lie beyond the quantile.

**Findings:**
- **The signal setups leave task time unchanged.** Every ratio's interval contains 1: mean 0.94-1.10×, median
  0.94-1.08× and p95 0.84-1.16×. That fits a model that does not wait.
- **The gate shortens tasks, most of all in the tail.**
  - At N=16: mean 0.70×, median 0.84×, p95 0.45×.
  - At N=32: mean 0.78× and median 0.94×, while the p95 falls from 1,690 s to 451 s (0.27×).
- **The gate moves waiting into the first call of a task.**
  - The call p95 is 49 s at N=16 (`none`: 48 s) and 207 s at N=32 (`none`: 69 s), yet the task p95 falls.
  - Without the gate the wait is spread over every call: at N=32 the median call waits 24 s in vLLM's queue,
    against 0 s with the gate (L4).
- **Sessions** (4 tasks and 3 think pauses, L6): mean 839 s → 555 s at N=16 and 1,950 s → 1,226 s at N=32 with the
  gate.
- **Checks:**
  - With so few tasks cut, Little's law agrees with the KM mean within 0-5 %.
  - The first and second halves of each window give the same median.
  - `--verify` reproduces the stored KM p50/p95, call p50/mean and turns/h of all 10 cells exactly.
  - Killed warm-up turns add at most 4 to a cell's stored turns/h count (L5).

## High-N follow-up (2026-10-29): does MiniMax-M2.7 wait when far more users compete?

**The user's hypothesis:** N ≤ 32 was too few users for four GPUs, so the model was not crowded enough to call
`wait`. Plan: `followups/plan_1029.md` (approved 2026-10-29; it also covers the completion-time section).
Pre-registration: `followups/highn_prereg.md`, frozen by `followups/highn_prereg.sha256` before the first cell.
Code: `ef_highn.py`. Tables: `data/tables/highn.{md,json}`, plus the x rows of `data/tables/latency.md`.

**Setup:**
- **Cells:** `x64_m_wait` (N=64) and `x128_m_wait` (N=128). Each is topic 19's `m_wait` setup, unchanged:
  - the `wait N seconds` tool;
  - a `[server]` status line on every call;
  - the paragraph about the shared server.
- **Server and harness:** the same as the N ≤ 32 cells (their hashes were checked before each cell).
- **Timing:** a 20-min warm-up, then a 40-min window. The main cells had 90 min.
- **Outcome r:** the share of counted lead calls, received in the window, whose response calls `wait`.
  - It comes with an exact Poisson 95 % interval.
  - Verdicts: below 0.1 % "still ignores it", 0.1-1 % "rare", 1 % or more "uses it".

**Result: not supported.** Both cells read "still ignores it", so by the pre-registered rule the hypothesis is not
supported. The conditional no-signal cell (`x<N>_none`) was therefore not run.

| cell | users | lead calls | with `wait` | rate [95 %] | verdict | sessions ahead ÷ pool, p50 (p90) | requests waiting, p50 | status said "won't fit" | hit rate | call / queue p50 (s) | reasoning mentions |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `w16_m_wait` | 16 | 4,415 | 0 | 0.00 % [0.00, 0.08] | still ignores it | 0.65 (1.20) | 3 | 31 % | 17.3 % | 11 / 4 | 3 |
| `w32_m_wait` | 32 | 4,053 | 3 | 0.07 % [0.02, 0.22] | still ignores it | 1.46 (2.60) | 20 | 67 % | 1.35 % | 34 / 26 | 6 |
| `x64_m_wait` | 64 | 1,778 | 1 | 0.06 % [0.00, 0.31] | still ignores it | 3.08 (5.34) | 51 | 85 % | 1.39 % | 78 / 68 | 2 |
| `x128_m_wait` | 128 | 1,849 | 0 | 0.00 % [0.00, 0.20] | still ignores it | 5.67 (10.21) | 113 | 92 % | 1.50 % | 160 / 152 | 0 |

- **Baseline:** the pooled N ≤ 32 cells give 3 of 8,468 = 0.04 % [0.01 %, 0.10 %].
  - Neither new cell is clearly above the baseline: each lower bound (0.00 %) is under the baseline's upper bound.
- **Columns:**
  - "sessions ahead ÷ pool" and "requests waiting" are what the status line told the model;
  - "won't fit" is the share of calls whose status said the session's context would not fit in the KV pool;
  - "reasoning mentions" counts calls whose thinking matches `ef_highn.SIGNAL_RE` (server, crowding, congestion,
    other sessions or users, requests waiting, KV pool). The `none` cells, which show no status line, have 2 each.

**What the model saw and did:**
- **The status line grew far more alarming.** At N=128:
  - the sessions ahead held 5.7× the KV pool at the median;
  - 113 requests were waiting;
  - 92 % of the statuses said the session would not fit.
- **The one `wait` (N=64, 151 s into the window) was not a reaction to the crowding.**
  - It asked for 1 s, with the reason "no-op to yield token budget", as the model finished exploring a codebase.
  - Its next call was the final answer.
  - The status it saw (sessions ahead 0.91× the pool, 49 requests waiting) was milder than the cell's median.
- **The reasoning ignores the status line.**
  - Both N=64 mentions are about the code: an HTTP library request "sent to server", and a failed edit "perhaps by
    other sessions".
  - N=128 has none.

**What more users did to the server and to the tasks** (descriptive; one cell per N):
- **The extra users only queue.**
  - The median call took 34 s at N=32, 78 s at N=64 and 160 s at N=128.
  - Almost all of that is time in vLLM's queue: 26, 68 and 152 s.
  - The prefill (about 0.8 s) and decode (about 5 s) medians did not change.
- **The server's throughput did not change:** about 2,700-2,900 lead calls per hour from N=32 to N=128 (L4).
- **The hit rate stayed at 1.4-1.5 %:** the cache was already thrashing at N=32.
- **Task (turn) completion time** (`wt_latency.py`, L1):
  - the median roughly doubles with each doubling of N: 267 s at N=32 (`w32_m_wait`), 593 s [506, 766] at N=64
    and 1,116 s [1,002, 1,206] at N=128;
  - 30 % (N=64) and 45 % (N=128) of the tasks started in the window were still running at its end, so the mean
    (≥ 821 s, ≥ 1,180 s) and the p95 (> 2,259 s, > 2,351 s) are only bounded.

**Validity:**
- **Both cells are valid:**
  - each completed its window (`COMPLETE`);
  - there were no request or scheduler errors;
  - GPUs 0-3 were empty after each cell;
  - both hash listings matched before each cell.
- **GPU time:** 4.12 + 4.13 = 8.25 GPU-h. The pre-registration estimated about 5.3 per cell.
- **Sheet claim:** the cells ran 4:38-6:42 PM EDT on Thu 10/29, inside the user's lab sheet claim (GPUs 0-3,
  4:30-8:00 PM).

**Deviations from the pre-registration:**
- **The answer line was applied by hand.** The report prints each cell's verdict and the conditional-cell trigger,
  but not the answer line. The answer above applies the frozen rule by hand: both cells "still ignore it", so
  "not supported". `ef_highn.py` is unchanged.
- **Some warm-up sessions ran into the window.** The pre-registration says the warm-up sessions stop inside the
  warm-up. In fact:
  - each stops U(0.1, 1) × 1,200 s after its slot starts, and the slots start over the first 300 s;
  - so the last warm-up sessions ended 200 s (N=64) and 294 s (N=128) into the window;
  - 9 and 19 of their turns were killed there. The main cells' last warm-up sessions ended up to 176 s in.
  - The effect on the outcome: each slot still held one user, so the load stayed at N. Warm-up calls are not in r,
    which counts counted sessions only.

## A KV-threshold `wait` prompt (follow-up, 2026-10-30)

**The ask:** a `wait` prompt that MiniMax-M2.7 acts on when HBM (vLLM KV-cache) use approaches 80 %. Topic 19's
`m_wait` got 3 waits in 21,081 calls.

**Why that prompt failed:**
- The KV cache was ≥ 80 % behind 92 % of `w16_m_wait`'s `[server]` lines and 97 % of `w32_m_wait`'s, but the share
  line shows token counts and never that number.
- Nothing defined "crowded".
- The texts mostly warn about the cost of a wait-only reply, which was written against topic 18's Qwen loops.

**The new prompt** (`s15_integrated_harness/hold_runtime.py`, additive and opt-in; every earlier setup is byte-identical,
tested against the frozen copy):
- `S15_WAIT_TEXT=v5`: a tool description with a rule. Whenever the newest `[server]` line shows the KV cache 80 %
  full or more, add one `wait` (about 10-30 s) next to the other tool calls. Below 80 %, and in a final answer, don't.
- `S15_SERVER_SECTION=wait_kv`: the same rule in the system paragraph.
- `S15_STATUS_FORMAT=kv`: the line reads `[server] KV cache 94% full; 12 requests waiting`. `kv_cue` adds `At 80% or
  more: add a wait call to your response.` at or above the threshold.
- `S15_WAIT_KV=0.8`: the threshold, shown as "80%".
- The tool stays advertised at all times. Adding it only above 80 % would change the top of the prompt and drop every
  cached prefix.

**The probe** (`ef_kvwait.py`; `followups/kvwait_prereg.md`, frozen 2026-10-30 18:05:39 UTC; plan
`followups/plan_1030_kvwait.md`):
- **Requests:** recorded `w16_m_wait` / `w32_m_wait` requests, sampled again (S = 4) with the texts swapped in
  three places. Earlier lines show the KV use the session saw; the newest line shows the level under test.
- **Screen:** `B0` (as recorded) against `T` (v3 + the kv line), `R` (v5 + wait_kv + kv), `C` (v3 + kv_cue) and `RC`
  (v5 + wait_kv + kv_cue), at 0.62 and 0.93. A frozen rule picks the winner.
- **Confirm:** the winner on 150 fresh items at 0.60, 0.75, 0.85 and 0.95.
- **Server:** it started as a second client on topic 21's `s2a_probe` server (19:56-20:04 UTC, 13 records). Topic 21's
  probe filled the KV pool, so the rest runs on a stage of its own (`ef_kvserve.py`, the user's choice). See Changes.
- **CPU check:**
  - all 210 items' rewrites differ from the recording only in the three places;
  - `B0` renders to the recorded prompt tokens 210/210;
  - rewritten prompts stay append-only 60/60;
  - `test_ef_kvwait.py` (6 tests) runs the whole probe against a fake model.

**Results:** the screen ran 540 of 540 requests `ok`, with 1 retry. It ran 19:56-20:04 UTC on topic 21's stage and
21:15-21:39 UTC on a stage of its own. Tables: `data/tables/kvwait.{md,json}`, `data/kvwait/winner.json`.

| condition | work replies with `wait`, line at 62 % | at 93 % [95 %] | samples with `wait` at 93 % | wait-only at 93 % | kept at 93 % |
|---|---|---|---|---|---|
| `B0`: as recorded | 0 % (as recorded) | | 0 of 240 | 0 % | 61 % |
| `T`: v3 text, kv line | 0 % | 0 % [0, 0] | 0 of 240 | 0 % | 60 % |
| `R`: v5 text + `wait_kv` paragraph, kv line | 0 % | 1 % [0, 3] | 4 of 240 | 0.4 % | 58 % |
| `C`: v3 text, kv_cue line | 1 % | 4 % [1, 8] | 11 of 240 | 4.2 % | 60 % |
| `RC`: v5 + `wait_kv`, kv_cue line | 1 % | 2 % [0, 6] | 12 of 240 | 2.5 % | 57 % |

- **No winner.**
  - The best work wait rate at 93 % is 4 % (`C`), against the 30 % bar.
  - All four candidates were eligible: ≤ 10 % at 62 %, wait-only ≤ 5 %, kept within 10 points of `B0`.
  - As frozen, no confirm ran, and the stage stopped after the screen.
- **The model hardly reads the line.** At 93 %, its thinking mentions the server, the KV cache or a pause in 0-2 %
  of samples. That is the same as `B0`'s keyword noise (0.8 %).
- **The waits mostly fall in the wrong places:**
  - `C`'s 11: 6 replace a work call, 4 replace a final answer, and 1 sits next to other calls.
  - `RC`'s 12: 6 at turn starts (5 next to other calls), 4 in work replies (3 of them wait-only), and 2 replace a
    final answer.
  - One `RC` thinking reads "I should pause to let the cache clear before documenting everything".
  - The requested seconds have a p50 of 5-15.
- **The replayed history holds it back.** The rate falls with the number of earlier lines at ≥ 80 % in the context:
  - with 1-5 such lines, the 93 % work wait rate is 11 % for `RC` and 7 % for `C` and `R`;
  - with more than 20, it is 0 % for every condition.

  The replies after those lines never waited, and the model follows its own history. At turn starts, `RC` waits in
  17 % of first replies at 93 %.
- **Answer to the ask:**
  - In replayed crowded contexts, an explicit 80 % rule, the KV number and a cue move MiniMax from 0 to at most
    about 5 % of replies at 93 % KV. Without the cue it is 2 % at most.
  - That is up from topic 19's 3 in 21,081 calls, but far from "tends to wait".
  - The texts stay in the harness, opt-in. No env line is recommended.
- **Limit:** the replayed histories are the probe's known limit, since a live session starts without ignored lines.
  Even with 1-5 such lines, though, the rate stays ≤ 11 %.
- **GPU time:**
  - 1.57 GPU-h on its own stage (`data/kvwait/gpu_hours.jsonl`);
  - 8 minutes of topic 21's 2.47 GPU-h `s2a_probe` stage, logged there.

**Revised screen** (2026-10-31; `ef_kvwait2.py`, `followups/kvwait2_prereg.md`): the user chose one revised screen to
separate the two suspects. It crosses two factors:
- **History:** the earlier `[server]` lines as recorded, or capped. Capped, each shows at most 75 %, so no earlier reply
  ignored the rule.
- **Wording:** `RC` (v5), or `RX` (wait text v6: v5 plus a worked example at 90 % and 55 %).

**Setup:**
- 60 fresh items from the unused confirm pool;
- the newest line at 62 % and 93 %;
- 540 requests, on a stage of its own (`ef_kvserve.py run --probe kvwait2`).

**Readings:**
- **usable:** eligible and ≥ 30 % at 93 %;
- **"the history holds it back"** and **"the example helps":** each needs a mean paired difference ≥ 15 points, with
  the lower 95 % bound > 5.

**Results** (2026-10-31 00:39-01:01 UTC): 540 of 540 requests `ok`, with 1 retry; 1.42 GPU-h. Tables:
`data/tables/kvwait2.{md,json}`.

| condition | work replies with `wait`, line at 62 % | at 93 % [95 %] | wait-only (all items, 93 %) | kept at 93 % (`B0` 60 %) | thinking mentions the line at 93 % |
|---|---|---|---|---|---|
| `RC`: v5, history as recorded | 0 % | 3 % [0, 7] | 1.2 % | 55 % | 2.9 % |
| `RX`: v6, history as recorded | 1 % | 4 % [0, 8] | 1.2 % | 54 % | 3.8 % |
| `RCcap`: v5, earlier lines ≤ 75 % | 0 % | **58 %** [49, 66] | 30.0 % | 46 % | 57.5 % |
| `RXcap`: v6, earlier lines ≤ 75 % | 0 % | **57 %** [48, 65] | 26.7 % | 39 % | 55.8 % |

- **The replayed history holds it back,** as pre-registered.
  - Capping the earlier lines raises the 93 % work wait rate by +53.9 points [+46.2, +61.0]: `RCcap` − `RC` is
    +54.8, and `RXcap` − `RX` is +53.0.
  - At 62 % every condition stays at 0-1 %. With a clean history, MiniMax follows the 80 % rule as a sharp
    threshold, and its thinking says so: "The KV cache is at 93%, I need to add a wait call".
- **The worked example does not help:** −0.3 points [−4.5, +4.0].
- **Usable: none.** Under the cap, the form of compliance fails the frozen eligibility rule:
  - **Wait-only replies:** 30.0 % and 26.7 % of samples, against a limit of 5 %. In work replies, 40 of `RCcap`'s 97
    waits replace the work call instead of joining it.
  - **Final answers:** in 26 of 36 final-answer samples it replies with only `wait` instead of answering, despite
    "never add wait to your final answer".
  - **Kept:** the recorded action is kept in 46 % and 39 % of work replies, against `B0`'s 60 %; the limit is a
    10-point drop.
- **Per the pre-registration,** the next step is the user's. A live session starts without ignored lines, as under
  the cap, so live MiniMax would likely wait often above 80 %. The open questions are the wait-only and final-answer
  waits, and whether the waits help the server at all, which only a live cell measures.
- **GPU time:** 1.42 GPU-h, which brings the topic 19 tally to 111.51 of 120.

**The user's re-reading and the final prompt (2026-11-01):**
- **The ruling:** a reply with only one `wait` call is acceptable when there is nothing useful to do yet. That replaces
  the frozen wait-only limit (5 %), so this is a re-reading, not the pre-registered rule.
- **Under it, `RCcap` passes the screen's other criteria:**
  - 58 % of work replies wait at 93 %, and 0 % at 62 %;
  - in replies that are not wait-only, the recorded action is kept in 58 % [46, 70], against `B0`'s 60 %.
- **`RXcap` does not pass:** it keeps 48 % [35, 61], 12 points below `B0`. The worked example (v6) adds nothing.
- **The final prompt** is v5 + `wait_kv` + kv_cue: `S15_WAIT_TOOL=1 S15_WAIT_TEXT=v5 S15_SERVER_SECTION=wait_kv
  S15_STATUS_LINE=1 S15_STATUS_FORMAT=kv_cue S15_WAIT_KV=0.8`. The line reads `kv_usage` from the status endpoint
  (`S15_STATUS_URL`, topic 18's `wt_server_ext`), which the default stock view carries.
- **Open:**
  - At 93 %, in final-answer replies it waits instead of answering (26 of 36 samples), despite "never add wait to
    your final answer". That delays the answer by the wait plus one model call.
  - Whether the waits lower KV use or help throughput needs a live cell.
  - The evidence is replay only. The capped history stands in for a live session, which has no ignored lines.

## GPU-hour log

| stage | cells | GPU-h | tally |
|---|---|---|---|
| S1 smoke | `s_m_tools` 0.96, `s_none` 0.94, `s_m_wait` 0.89, `s_gate2` 0.89 | 3.68 | 3.7 |
| S2 pilot | `p08_none` 3.15, `p16_none` 3.10, `p16_m_wait` 3.10, `p16_m_tools` 3.10, `p16_sgate` 3.12 | 15.57 | 19.25 |
| S3 block 1, attempt 1 | `w16_sgate` 6.62 (INCOMPLETE: the counted specs ran out; amendment 1) | 6.62 | 25.88 |
| S3 block 1 | `w16_sgate` 7.43, `w16_none` 7.43, `w32_none` 7.44, `w32_sgate` 7.44 | 29.74 | 55.62 |
| S3 block 2 | `w16_m_tools` 7.44, `w16_m_wait` 7.45, `w32_m_wait` 7.45, `w32_m_tools` 7.44 | 29.77 | 85.39 |
| S3 block 3 | `w08_none` 7.43, `w08_m_tools` 7.45 | 14.89 | 100.27 |
| High-N follow-up (2026-10-29) | `x64_m_wait` 4.12, `x128_m_wait` 4.13 | 8.25 | 108.52 |
| KV-threshold probe (2026-10-30) | `kvwait` stage, screen only (21:15-21:39 UTC) | 1.57 | 110.09 |
| KV-threshold revised screen (2026-10-31) | `kvwait2` stage (00:39-01:01 UTC) | 1.42 | 111.51 |

The KV-threshold probe's first 13 requests ran on topic 21's `s2a_probe` stage (19:56-20:04 UTC). Those 8 minutes
are in topic 21's log, not here.

## Reproduction

```bash
PY=/data/fritz/venv/bin/python
# CPU
research/16_epic_harness/harness_eval/.venv/bin/python -m pytest tests -q
$PY research/19_efficiency_signals/test_minimax_template.py
$PY research/19_efficiency_signals/test_ef_e2e.py
$PY research/18_harness_wait/test_wt_scheduler.py && $PY research/18_harness_wait/test_wt_server_ext.py
$PY research/18_harness_wait/test_wt_sweep.py
$PY research/19_efficiency_signals/ef_calibrate.py                 # data/calibration.json
$PY research/19_efficiency_signals/ef_workloads.py conditions     # data/conditions.json
# GPU (after the sheet claim): one cell at a time on GPUs 0-3
$PY research/19_efficiency_signals/ef_pipeline.py chain s_none s_m_wait s_m_tools s_gate2
$PY research/19_efficiency_signals/ef_analyze.py --smoke s_none,s_m_wait,s_m_tools,s_gate2
$PY research/19_efficiency_signals/ef_pipeline.py hours
# main run (S3) and its analysis
$PY research/19_efficiency_signals/ef_pipeline.py chain w16_sgate w16_none w32_none w32_sgate
$PY research/19_efficiency_signals/ef_pipeline.py chain w16_m_tools w16_m_wait w32_m_wait w32_m_tools w08_none w08_m_tools
$PY research/19_efficiency_signals/ef_analyze.py --n-light 8 --out research/19_efficiency_signals/data/tables/main.md \
    --cells w16_sgate,w16_none,w32_none,w32_sgate,w16_m_tools,w16_m_wait,w32_m_wait,w32_m_tools,w08_none,w08_m_tools
$PY research/19_efficiency_signals/ef_verify.py --json research/19_efficiency_signals/data/tables/main.json
# completion time per task (added 2026-10-29)
$PY research/18_harness_wait/wt_latency.py --topic 19 --verify     # data/tables/latency.{md,json}
# high-N follow-up (2026-10-29)
$PY research/19_efficiency_signals/ef_highn.py dryrun --users 128  # CPU: the sweep against the fake server
$PY research/19_efficiency_signals/ef_highn.py chain x64_m_wait x128_m_wait   # GPU, after the sheet claim
$PY research/19_efficiency_signals/ef_highn.py report              # data/tables/highn.{md,json}
$PY research/18_harness_wait/wt_latency.py --topic 19 --verify     # adds the x rows to latency.{md,json}
```

## Changes

- 2026-10-26: plan approved; S0 built and tested (above).
- 2026-10-26 03:30 UTC: `ef_analyze.py` fixes found by running it on topic 18's cells, before any topic-19 data:
  - the step model is now fit before the GPU split;
  - the M3 check reads the diff's path field (the `+++` line ends in a timestamp);
  - GPU-seconds per turn count every GPU (busy time × TP).
- 2026-10-26 04:20 UTC, after smoke `s_m_tools`: `--max-num-seqs 64` for every cell (natural pool; S1 above).
- 2026-10-26 ~06:35 UTC, during the pilot chain: importing `ef_pipeline` for a test rewrote `data/conditions.json`
  with P1's new warm-up estimates.
  - The file was restored a minute later to what P1 and P2 ran with. `p16_m_wait`'s sweep had read it at
    06:34:36, before the rewrite.
  - Sessions started in that minute read only fields that did not change (the harness flags and the context limit).
  - Since then `ef_pipeline` writes the file only when it runs cells.
- 2026-10-26 06:30 UTC: `ef_pipeline` checks the lab GPU sheet before every cell. Each 30-min slot to the cell's
  expected end must carry the member's claim on GPUs 0-3.
- 2026-10-26 after P1, by the plan's rules: `GATE_K = 8`, `N_LIGHT = 8` and the warm-up estimates in
  `ef_workloads.py`. They take effect for P4 and the main run, through the conditions file written when the pilot
  chain is over.
- 2026-10-26 10:47 UTC, prereg amendment 1: main cells cycle through the counted specs.
  - The trigger: `w16_sgate`'s first attempt used up all 128 counted specs 14 min before its window's end.
  - The code: `cycle_specs` in `ef_workloads.py`; `wt_sweep.WindowCell`, off by default.
  - New tests: `research/18_harness_wait/test_wt_sweep.py` and `test_ef_e2e.py`'s cycle case.
- 2026-10-27 05:30-06:00 UTC: the main analysis, with `ef_analyze.py` and `ef_verify.py` unchanged since the
  freeze.
  - The B4 numbers for `none` and `sgate` come from `main.json`, whose `b` rows cover every cell; the Markdown
    table lists only the model arms.
  - Three checks were run once, in the session, and are not in the analysis code:
    - amendment 1's pass-k check;
    - the list of the three `wait` calls;
    - the keyword scan of the thinking.
- 2026-10-29: the section "Completion time per task", from `research/18_harness_wait/wt_latency.py`.
  - `ef_analyze.py` and `ef_verify.py` are unchanged; `prereg.sha256` still matches.
  - `wt_latency.py --verify` reproduces the stored KM p50/p95, call p50/mean and turns/h of all 10 cells.
- 2026-10-29: the high-N follow-up (`ef_highn.py`, `followups/highn_prereg.md`, frozen before its first cell). It
  uses its own conditions file, `data/conditions_highn.json`; topic 19's frozen files are untouched, and their
  hashes are checked before each cell.
- 2026-10-29 20:38-22:42 UTC: the high-N cells ran (`x64_m_wait`, then `x128_m_wait`).
  - The analysis was then run with `ef_highn.py` unchanged since the freeze.
  - `wt_latency.py --topic 19` was rerun. It added the x rows; every earlier row is identical.
  - The two deviations are listed in the section.
- 2026-10-30, before any edit of the harness: a byte-identical copy of `s15_integrated_harness/hold_runtime.py` (sha256
  `8cf61048…`, as in `prereg.sha256`) at `research/19_efficiency_signals/s15_integrated_harness/hold_runtime.py`.
  - Topic 19's own check (`ef_highn.changed`) and topic 21's `pw_serve.py` look a listed path up inside this folder
    first, so `prereg.sha256` now verifies that copy, which is exactly what topic 19 ran.
  - The cost (the user's choice): neither check guards the live harness file any more.
- 2026-10-30: the live `hold_runtime.py` gained the KV rule (wait text v5, paragraph `wait_kv`, `S15_STATUS_FORMAT`
  kv / kv_cue, `S15_WAIT_KV`). Defaults are unchanged.
  - `tests/test_s15_server_hold.py` loads the frozen copy and checks that every earlier setup's tool schemas,
    paragraph and lines are byte-identical: 543 pass, with the 2 known failures.
  - `test_ef_e2e.py` and `test_ef_analyze.py` pass.
- 2026-10-30: the KV-threshold probe (`ef_kvwait.py`, `test_ef_kvwait.py`, `followups/kvwait_prereg.*`, frozen
  18:05:39 UTC), above.
- 2026-10-30 19:56-20:04 UTC: the probe ran as frozen, as a second client on topic 21's `s2a_probe` stage.
  - Topic 21's own probe (60-70k-token prompts, S = 8, 16 in flight) filled the 277,440-token KV pool. The probe got
    about 2 requests a minute, and topic 21's probe would have run past its stage limit.
  - At topic 21's request the client was stopped (SIGTERM, 20:04:38 UTC) with 13 of 540 screen records, all
    complete. Those 8 minutes are in topic 21's GPU-hour log.
- 2026-10-30 21:00 UTC, before the rest of the probe: deviations from `kvwait_prereg.md`, after the user chose a
  stage of its own:
  1. **Server:** a stage of its own. `ef_kvserve.py` (tested by `test_ef_kvserve.py`) is topic 20's stage runner with
     the shared stage's `profile` config. It logs to `data/kvwait/gpu_hours.jsonl` against the rest of topic 19's cap
     (11.48 GPU-h).
  2. **8 requests in flight instead of 4.** The 4 only spared topic 21's probe. No measure depends on it, and the
     seeds stay per request.
  3. **The server check:** the frozen guard accepts only topic 21's `s2a` stages. The client therefore runs with it
     off (`any_server`), and `ef_kvserve.check_server` checks the same things for this stage: stage `kvwait`, config
     `profile`, MiniMax-M2.7 listed.
  4. **The first 13 screen records** came from the shared stage: the same model, config and seeds.

  `ef_kvwait.py` and every hashed file are unchanged (`changed()` is empty).
- 2026-10-30 21:39 UTC: the screen ended with no winner, so no confirm ran, as frozen. Results are in the section.
- 2026-10-31 00:30-00:45 UTC: the user chose one revised screen, which `kvwait_prereg.md` hands back to the user.
  - **The harness:** `hold_runtime.py` gained wait text `v6` (v5 plus a worked example at 90 % and 55 %), opt-in.
    - `tests/test_s15_server_hold.py` checks it: 543 pass, with the 2 known failures.
    - The live file no longer matches `kvwait_prereg.sha256`. That probe is complete, and only a rerun of its `run`
      would refuse.
  - **The new probe:** `ef_kvwait2.py` + `test_ef_kvwait2.py` (2 tests). `ef_kvserve.py` gained `--probe kvwait2`
    (4 tests).
  - **Items and checks:** `data/kvwait2/items.json` has 60 items from the unused confirm pool. `check` is OK: the
    rewrites, the caps, 60/60 renders.
  - **The pre-registration:** `followups/kvwait2_prereg.md`, frozen in `followups/kvwait2_prereg.sha256` before the
    first request.
