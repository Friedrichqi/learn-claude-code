# Weekly sync, Wed 2026-11-04: slide outline

Built 2026-11-03; topic 23's results added 16:30 UTC, its LLM call and task latency split after the README's 18:25 UTC
revision; reorganized after your 11-03 comments (slides deleted and merged, the KV-rule slide dropped). Cut on
11-04 at your request: topic 23's `lmc` (a save by re-send, which does not test your direct GPU → DRAM copy idea)
on every slide, the save slide, the hypotheses slide, the next-steps slide and every backup. Deck: `slides.html`
in this folder (13 slides, no backups), published as a private Artifact. Period: everything since the 10-28 deck (`weekly_progress/102826/new_exps_*`), i.e. 2026-10-28 → 11-03.

**Sources** (in this repo unless noted):
- `research/19_efficiency_signals/README.md` (Completion time per task; High-N follow-up; A KV-threshold `wait` prompt)
  and `data/tables/{highn,kvwait,kvwait2}.md`, `kvwait2.json`;
- `research/18_harness_wait/README.md` (Completion time per task);
- `research/20_round_dependency/README.md`;
- `research/21_precautionary_work/` (`PLAN.md`, `prereg_2a.md`, `data/tables/{census,probe_main}.md`) and
  `research/19_efficiency_signals/followups/handoff_topic21_cpu_line.md`;
- `research/22_wait_bench/README.md` and `data/tables/{wb,wb_waits,wb_mix}.md`;
- `research/23_efficiency_tools/README.md` (done 11-03 16:05 UTC; Results, revised 18:25 UTC), `PLAN.md`, `prereg.md`,
  `data/tables/et.{md,json}`, post hoc `data/tables/latency.{md,json}` (`et_latency.py`),
  `data/runs/agate/controller.jsonl`;
- `research/related_work/{unified_memory_table,self_evolving_harness}_related_work.md`, `README.md`;
- `s15_integrated_harness/hold_runtime.py` (the prompt texts);
- **post hoc, not in a README** (scratch scripts that only read topic 22's data): the cache hit rate of the lead call
  after a wait or another tool, vLLM's running requests and time per request, the wait rate by the KV % the model saw,
  how often a lone wait came right before a final answer, and the cached share by the idle time before a call;
  for topic 23, the wait tool's seconds per task and per LLM call (`post/wait_time.py`, read only on the run
  files).

**Rules used** (the user's standing rules):
- main slides: 1–2 bullets, at most 20 words each; a chart or diagram on every slide;
- every setup name gets a few-word gloss; every chart says what it shows;
- no "arm" wording; models run on different GPU counts are never compared;
- no time conclusions from single-session timing;
- fewer words on a slide: tables and figures over text (your 11-03 comment);
- next steps continue the numbering at 28.

**Checks:** every number in the deck and in this outline matches a cited source line or a recomputed value (none
unexplained); every chart value matches its source table position by position, topic 23's against `et.md`'s and
`latency.md`'s cells and the README's tables; the rules check passes; every slide renders without overflow in light and dark, standards
and quirks mode.

## Slides

**1. This week: the model now follows a wait rule; where else is the slack?** (overview)
- An explicit 80% KV rule makes MiniMax wait, live; waiting alone still buys no throughput.
- On Qwen, no model-decided tool clearly raised throughput; gates slowed tasks; offloading everything raised tokens/s.
- *Visual:* date ribbon Oct 28 → Nov 3, one lane per study (topic 19 follow-ups, 20, 21, 22, 23, the survey), each
  with its model, GPUs, GPU-hours and a one-line answer; 156.77 GPU-h this week.

**2. Live, MiniMax follows the 80% rule as a sharp threshold** (topic 22, 11-01 → 11-02)
- 96 SWE-bench, Pro and Terminal-Bench tasks at 4–32 agents: s15 alone vs s15 with the final prompt.
- 59–77% of calls that saw 80% or more waited; at most 4% below.
- *Final prompt* (wait text v5, the `wait_kv` paragraph, the `kv_cue` line): the `wait` tool's text and the system
  prompt state the rule, wait at a KV cache 80% full or more; each model call's `[server]` line shows the KV % and,
  at 80% or more, the cue "At 80% or more: add a wait call to your response."
- *Visual:* the setup (96 tasks, N agents, MiniMax TP=4, KV pool 277,440 tokens); post hoc, the share of calls with
  `wait` by the KV % on their line in 5-point bins, 16 and 32 agents; time at KV ≥ 80%, 71 → 35% and 93 → 71%.

**3. At 16–32 agents each call got faster, but tasks, throughput and pass rate did not move**
- At 16 and 32 agents the median call fell 7.7 → 3.8 s and 19.1 → 7.6 s.
- Agents spent the saving waiting: task time, requests/h (×1.00, ×1.11) and pass rate show no clear change.
- *Visual:* forest plots of `wait` − `none` per N: request p50, task p50, requests/h ratio, pass rate.

**4. Topic 23: a harness with automatic, customized efficiency tools** (design and runs, 11-02 → 11-03)
- The model decides: agents call wait; a controller call sets the session limit.
- All runs valid; no tool the model decides clearly raised throughput.
- *Visual:* seven setups as a table (`lmc` left off), each with its tool, what it does and who decides: `none`, the s15 harness as
  is; `gate8`/`gate16`, a session gate (at most K = 8 or 16 sessions send requests, the rest wait at the gate; fixed);
  `agate` (adaptive gate), `set_session_limit(K)`, a separate Qwen controller call that reads GPU metrics and sets K
  each minute (the model);
  `wait`, `wait(seconds)`, the agent pauses (median 20 s) when its line shows KV ≥ 80% (the model);
  `lmc_all` (LMCache, every request), LMCache always on,
  every request's KV stored in DRAM and reloaded; `none_r2`, none again (the noise). Then the readings fixed in
  advance with their answers, numbered as pre-registered: 1 offload alone, partly (tokens/s 1.21×, clear; requests/h 1.09×, task time not clear); 3
  the model sets K, no
  (throughput and task time not clear; LLM call p95 −138 s, p50 +8.5 s); 4 what counts (the 95% interval excludes no
  change; the A/A pair differs only in the LLM call p50, +4.5 s). 35.69 GPU-h.

**5. The controller agrees with a reference rule in replay, but acts on metrics over a minute old** (controller
screen; pair 3)
- Replay: the controller's K agreed with a reference rule 94–98.5% of the time.
- Live in pair 3, K swung 6–32 for 144 min, then held at 20.
- *Visual:* left, for c1–c4 at the default and at low effort: valid calls (a usable `set_session_limit`
  call) and agreement with the reference rule (K moves the way it says: lower when the active contexts pass 90% of
  the KV cache, or the hit rate is under 50% with preemptions or requests waiting for KV; raise when sessions wait
  and the next one's context would fit in 80%; else keep). c3 and c4 are told the rule in their prompt. The live c4
  timeouts; right, agate's K in pair 3 over the 194-minute run, drawn from `runs/agate/controller.jsonl`: 122
  rounds, all answered, 75 s per call at the median; 61 changes in the first 144 min (26 cuts, 35 raises), then 20.

**6. Only offloading every request raised throughput clearly, and only in tokens/s** (topic 23, result)
- lmc_all reloaded 38% of prompt tokens from DRAM: tokens/s 1.21×, clear; requests/h 1.09×, not clear.
- No setup where the model decides clearly raised throughput; gate8 cut requests/h to 0.66×.
- *Visual:* forest plot, each setup's requests/h and tokens/s ÷ none (1,868 an hour, 9,805 tokens/s) with 95%
  intervals, log scale, glossed, green for a clear gain and red for a clear loss; then reading 3 head to head
  (agate ÷ gate16 1.03×, 0.97×). Tokens/s counts prompt tokens (96% of it) whether cached or
  computed; requests/h is closer to the agents' progress. Throughput is net of the harness's own calls.

**7. Gates and the wait tool both cut time at the server, and both add time spent waiting** (topic 23, LLM call time)
- Gates: 22.7–45.2 s per call at the server against none's 55.7 s; some calls waited minutes.
- wait: calls about 8 s faster on average, but its agents spent 8.9 s per call waiting.
- *Visual:* left, time per LLM call at the server plus time waiting (post hoc): at the gate for the
  gated runs (gate8 22.7 + 70.9, gate16 30.9 + 31.8, agate 45.2 + 14.9; only 7–10% of their calls waited there), and in
  the wait tool for `wait` (48.0 + 8.9 = 56.9 s per call: the wait tool's seconds
  spread over all the agents' calls, outside the call itself; a wait followed about 1 in 3 calls, median 20 s;
  `post/wait_time.py`, this deck); a tick at the median, and the average and
  median as the agents saw them (none 54.8 and 26.3; gate8 92.8 and 8.7; wait 48.3 and 19.1). Right, the average and
  median call minus none over working calls, with intervals (gate8 +36.4 and −17.6; wait −7.4 and −7.1; none_r2 +4.5 at the
  median), hollow rings at the pre-registered all-calls values where they differ.

**8. Gated tasks took 10–26 minutes longer on average; no setup made the median task clearly faster** (topic 23, task time)
- Fixed gates cut working time 29–49% but added more at the gate; agate only added gate time.
- wait's tasks spent 331 s in the wait tool, yet ran 240 s shorter on average, about the A/A gap.
- *Visual:* each run's average task time as working time (model calls and tools) plus time waiting: at the gate (gate8
  1,265 + 2,763; gate16 1,771 + 1,317; agate 2,419 + 682) or in the wait tool (wait 1,909 + 331), a
  tick at the median, and the average and median (none 2,480 and 1,890; gate8 4,028 and 3,560); the average and the
  median task minus none with intervals (gate8 +1,548 and +1,670, gate16 +608 and +925, agate +621 and +611, clear and
  longer; wait −240 on average, clear and shorter; the rest not clear). wait's tasks made fewer calls (37.7 against 43.1; none_r2 45.3), so per call the wait tool took what the
  server saved. Pass rates 71–78%, no difference clear; the gated runs had fewer tasks at the limit (11–21 against 25
  and 31).

**9. About half of the rounds do not need the previous round's results** (topic 20, 10-28 → 10-29)
- With the previous results hidden, MiniMax still chose the recorded next step in 49% and 43% of reproducible rounds.
- A planned series of edits runs ahead (edit → edit 97%); reading then acting does not (read → edit 9%).
- *Visual:* the pending test (results replaced by s15's background placeholder, 8 samples); the share independent by
  source (topic 16 by benchmark: SWE-bench Verified 36%, Pro 51%, Terminal-Bench 40%; Pro and Terminal-Bench are the
  trials of pages 10–11) and by transition. Before and after a command: the command is often planned ahead (edit →
  shell 95%); the step after it rarely is (shell → edit 4%).

**10. Merging could remove a quarter of the calls; overlapping tool time has little to work with**
- Folding each chain of independent rounds into one call removes 28% and 25% of lead calls.
- Only 4.3% of Pro and Terminal-Bench tool seconds sit beside an independent next call (≤ 1.2% of trial time).
- *Visual:* the two gates (merge 28.2% vs 10%; reorder 4.3% vs 10%); calls a merge removes by benchmark; the share of
  time spent in tools in the replayed trials (topic 19 0.01–0.14%, SWE-bench Verified 3.9%, Pro 18.8%, Terminal-Bench
  31.5%; Pro + TB 27%, 4.02 h of 14.99 h); Pro + TB tool time by the round that follows (post hoc,
  `post/rd_reorder_align.py`): 4.3% independent, so it could run beside the tool, 36% needs its output, 58% cannot
  be judged (mostly Terminal-Bench), 1.7% other; the ceiling, ≤ 1.2% of trial time (≤ 3.5% at the most generous 13%),
  and none at saturation (page 11); the merge test's status (ready, not run: no GPU claim on 10-29; ≈ 22 GPU-h). The
  README's ≤ 6.4% used the wider planning sample's tool share (Pro 41.6%, TB 46.4%).
- *Alignment (11-04):* pages 9–11 use the same 58 Pro + TB trials for reorder gain, gains in time as a share of
  their 14.99 h, and "before" (could the long command start earlier? page 11) vs "after" (could the next call run
  beside it? page 10).

**11. Asking MiniMax to front-load long commands barely moves it** (topic 21, 10-30)
- Ceiling: merging up to the long commands removes at most 9.1% of calls, even with full compliance.
- The front-load line: 9.9% early issue vs 4.4% for a neutral line; the bar was +15 points.
- *Visual:* census ceilings on the same 58 trials as page 10: calls removed (of 1,557) by merging every chain
  26.1% (page 10's Pro 33% and TB 18% together), up to the long command (≥ 1 s) 9.1%, up to a ≥ 10 s command 4.8%;
  trial time saved (of 14.99 h), before: start long commands earlier 5.2%; after: the next call beside the tool
  (page 10) 1.2%, ≤ 3.5% at the most generous reading; rerun repeated commands 0.3%. Before a long command 44% of
  its time could start earlier (two 1,800-s timeouts hold 58% of it; 11 of 97 commute); after it only 4.3% has an
  independent next call. The probe F / N / C with intervals; the H1 verdict and the costs within limits.

**12. Literature: neither candidate direction has been built** (11-01 survey, 1,092 papers)
- One memory table over six tiers, HBM to flash: the pieces exist, in separate layers.
- A self-evolving harness: three lines each build half; six papers agree agents ignore advisory signals.
- *Visual:* two coverage matrices (closest works × tiers; closest works × loop stages).

**13. Takeaways**
- At a steady load, the wait tool took about what each call saved; no throughput gain.
- On Qwen, gates slowed tasks 10–26 min; only offloading every request to DRAM raised tokens/s (1.21×).
- *Visual:* ✓ / ✗ / partly / open board, each model in its own group. MiniMax-M2.7 (topics 19, 22): an explicit 80% KV
  rule works live (59–77% wait above it, ≤ 4% below); no throughput or task time from waiting. Session structure: rounds
  not needing the previous round (43–49%), merging (open), overlapping tools (no), a front-load line (no). Qwen3.8-27B
  (topic 23): follows the wait rule live; no throughput from waiting at 32 agents (calls about 8 s faster, 8.9 s per
  call in the wait tool); session gates, no (tasks 10–26 min longer on
  average); DRAM offload of every request, partly (tokens/s 1.21×).
