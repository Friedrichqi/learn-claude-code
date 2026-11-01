# Topic 19 pre-registration: MiniMax-M2.7 with server-efficiency signals and tools

Frozen before the main run (S3); `prereg.sha256` holds this file's hash. Plan: `~/.claude/plans/now-i-want-to-jiggly-waffle.md`
(approved 2026-10-26). Pilot results are in `README.md` (S2).

## What the pilot showed (the basis of this document)

- **P1:** at N=16, `none` thrashes on the cache (hit rate 17.6 %, 32.9 % of GPU time on recomputing evicted
  prefixes), but decoding still takes most of the GPU. Going from 8 to 16 users costs 13 % of turns/h.
- **P2 and P3:** MiniMax-M2.7 used the efficiency signals almost not at all.
  - `m_wait`: 2 `wait` calls in about 1,500 lead calls.
  - `m_tools`: no pacing call in about 1,340.
  - Neither arm sent an overhead-only call, so the plan's text-revision rule (written for wait loops) is not
    triggered. The texts stay v3.
- **P4:** the gate at K=8 at N=16.
  - It gave 410 turns/h against `none`'s 306 (1.34×), with a turn p95 of 188 s against 466 s.
  - Its hit rate was 80.5 % (≥ 80 %), so by the plan's rule K stays 8.

## Cells

Each cell is one at a time on GPUs 0-3: a 20-min warm-up, then a 90-min measurement window.

| block | cells, in run order | purpose |
|---|---|---|
| 1, reference | `w16_sgate`, `w16_none`, `w32_none`, `w32_sgate` | needed by every comparison; run first |
| 2, model arms | `w16_m_tools`, `w16_m_wait`, `w32_m_wait`, `w32_m_tools` | R1, R2 (default design; see the amendment slot) |
| 3, light load | `w08_none`, `w08_m_tools` | R3, no harm at N_light = 8 |

- **The order within each N** was drawn before the pilot results, with `random.Random(19)`:
  w16_sgate, w16_m_tools, w16_m_wait, w16_none | w32_none, w32_m_wait, w32_m_tools, w32_sgate | w08_none, w08_m_tools.
- **The reference cells are moved forward,** keeping their order within each N. The model arms' design can then be
  amended (below) while they run, without delaying the GPUs.
- **Time is a confound:** the model arms run several hours after their references. It is noted, not corrected.

**Amendment slot.** Given P2 and P3, the user may replace or add model arms before block 2 starts.
- The decision rests on the pilot only. Main-run results are not used, and block 1's numbers are not reported
  before the decision.
- An amendment is appended below, with its time (UTC), and the file is re-hashed before any affected cell starts.
- Without an amendment, blocks 2 and 3 run as listed.

## Frozen settings

- **Model and server:**
  - `MiniMaxAI/MiniMax-M2.7` (revision `d494266a4a`), vLLM 0.31.0, TP=4 on GPUs 0-3 (vLLM on CPUs 0-51,104-155;
    sessions on 52-103,156-207);
  - `--gpu-memory-utilization 0.92 --max-num-seqs 64 --max-model-len 65536 --num-gpu-blocks-override 16137` (the
    pool is 258,192 tokens; the natural pool is 17,317-17,340 blocks);
  - the `minimax_m2` and `minimax_m2_append_think` parsers;
  - the template `minimax_m2_append_only.jinja` (sha256 `40e4b338…`).
- **Scheduler:** `WTScheduler` and `wt_server_ext`, with `MU_STRICT=1`, `WT_SNAP_S=0.2` and
  `MU_TOOLCALL_TOKEN=200052`. `sgate` adds `MU_GATE=K` and `MU_GATE_IDLE_S=5`, with **K = 8** (P1's rule; P4 below).
- **Harness:** `s15_integrated_harness/hold_runtime.py` (sha256 `8cf61048…`) and `code.py` (sha256 `1b66cb8a…`).
  - Every arm: output budget 16,000 escalated to 32,000; summary budget 8,000; context limit 129,000 characters.
  - Pacing: share-rule τ 0.9; poll 2 s; `wait` cap 300 s; `wait_for_capacity` cap 1,800 s (default 600).
  - `m_wait`: `S15_TOOLS=wait`, `S15_WAIT_TEXT=v3`, `S15_SERVER_SECTION=wait`, `S15_STATUS_LINE=1`,
    `S15_STATUS_VIEW=share`.
  - `m_tools`: `S15_TOOLS=wait,wait_for_capacity,server_status`, `S15_WAIT_TEXT=v3`, `S15_SERVER_SECTION=tools`,
    `S15_RECEIPT=1`, `S15_STATUS_VIEW=share`.
  - All of this is in `ef_workloads.py` (sha256 at freezing time in `prereg.sha256`).
- **Workload:**
  - topic 18's frozen manifest (`research/18_harness_wait/data/sessions.jsonl`, sha256 `8a20ee38…`);
  - 128 counted specs in manifest order, warm-up fillers on;
  - warm-up filler estimates 287 / 316 / 713 / 1,121 s at N = 4 / 8 / 16 / 32.
- **Cap:** 120 GPU-h. Every cell needs the user's GPU-sheet claim to its expected end.

## Metrics

- **Primary: completed turns per hour.** These are trace `turn_end` events inside the measurement window, in
  10-min blocks, with a block bootstrap (4,000 draws, seed 19).
- **Co-reported:**
  - work lead calls/h (an overhead-only call has only pacing tools);
  - Kaplan–Meier turn p50 / p95;
  - GPU-seconds per completed turn (engine busy × 4);
  - hit rate;
  - GPU share on evicted and on preempted recompute;
  - output tokens per call; max_tokens escalations.

## Decision rules

- **Floor:** F = 10 % of `none`'s completed turns/h at the same N.
- **R1 recovery,** for X ∈ {model arms}, N ∈ {16, 32}: R = (X − none) / (sgate − none) on turns/h.
  - "Recovers" if R ≥ 0.5 and the 95 % lower bound of X − none > F.
  - "Hurts" if the 95 % upper bound of X − none < −F.
  - Otherwise "neither".
  - The same R on work lead calls/h is co-reported.
- **R2,** m_tools − m_wait at each N: "toolbox better" if the lower bound > F; "toolbox worse" if the upper bound
  < −F; otherwise "no clear difference".
- **R3, no harm at N=8:** m_tools − none ≥ −F, and the hit rate falls by ≤ 2 points.
- **Quality guard**, each against `none` at the same N:
  - (a) the share of turns that end normally;
  - (b) the share of complete sessions' M turns whose edit is in the session's diff.
  - A gain with (a) or (b) more than 5 points below `none` is reported as doing less work, not as efficiency. P1-P3
    gave 99.3-100 % and 95.7-100 %.
- **Behaviour** (descriptive):
  - **B1:** step-backs placed in responses that also do work; tool calls per response.
  - **B2:** overhead-only calls per 100 work calls and their GPU share.
  - **B3:** "tracks the signal" if P(step-back | crowded) − P(step-back | calm) ≥ 20 points. Crowded means the
    sessions ahead plus the session's own context exceed the pool, by the status the harness fetched before the call.
  - **B4:** tool calls per response, output tokens per call, calls per turn and exact re-reads, crowded vs calm and
    vs `none`.
  - **B5:** status reads per 100 work calls; step-backs after a read; reads after costly vs cheap steps.

## Integrity and reruns

- **A cell is valid if:**
  - it completes its window;
  - the server log has no `err` record;
  - every lead call is joined, apart from at most two in flight when the window closes;
  - GPUs 0-3 are empty afterwards.
- An invalid cell is rerun once, budget permitting.
- `ef_analyze.py` must regenerate `data/tables/main.md` byte for byte, and `ef_verify.py` must agree on turns/h,
  work calls/h, overhead calls and the hit rate.

## Amendments

### 1. The counted specs cycle (2026-10-26, 10:47 UTC, before block 1 restarts)

- **What happened.** Block 1's first cell, `w16_sgate`, used up the manifest's 128 counted specs about 14 min
  before its window's end. The last counted session ended at 10:33 UTC; the window would have closed at 10:47.
  - The sweep (written for topic 18) then stopped the cell: sweep exit 4, INCOMPLETE, 6.62 GPU-h.
  - Topic 18's manifest was sized for Qwen3-32B, and MiniMax-M2.7 finishes sessions faster. The pilot's 45-min
    cells never came near the limit.
  - At the pilot's rates (about 4 turns per session), `w08_none`, `w16_none` and the model arms would also have
    been at or near the limit.
- **Change.** In every main cell, once the last counted spec is handed out, the queue starts the 128 specs again
  in manifest order, as pass 2, 3 and so on. Spec `S000` then runs as label `MU-<cond>-S000-p2`.
  - Code: `cycle_specs` in `ef_workloads.py`, `wt_sweep.WindowCell`; off by default, so topic 18 is unchanged.
  - Unchanged: the arms, the warm-up and window, the metrics and the rules.
  - Pass-k sessions count like pass-1 sessions.
- **Check.** A pass-k session repeats pass 1's prompts (the same task and nonce). Pass 1's blocks should be evicted
  long before, because the pool turns over within minutes. The analysis compares the cache hit of pass-k first
  calls with pass-1 first calls, and reports any excess.
- **Rerun.** `w16_sgate` is invalid by the integrity rule (its window did not complete).
  - It is rerun once, still first in block 1.
  - The partial cell is kept in `data/cells_invalid/w16_sgate_a1` and is not analysed.
- **What this used:** only that the cell ran out of specs, and when. No block-1 metric was computed.
- **Hashes before this amendment:** `prereg.md` `647d15d8…` and `ef_workloads.py` `8924d250…`. `prereg.sha256` now
  holds the new hashes and adds `research/18_harness_wait/wt_sweep.py`.
