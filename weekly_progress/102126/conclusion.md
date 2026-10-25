# Weeks of 2026-10-07 and 2026-10-14: KV reuse after the harness's own edits is safe for accuracy; what it buys with many users is open

This note synthesises three studies for the deck in this folder (`slides.html`, for the 2026-10-21 meeting). It was
written 2026-10-19 and revised 2026-10-21 after review: the job-time conclusions were withdrawn (they priced one session
at a time), and a KL-by-shifts analysis, a prior-work map and two new next steps were added. It covers 2026-10-07 to
2026-10-20:

- `research/16_epic_harness/README.md`: topic 16, EPIC inside the live s15 loop, Qwen3-32B, 2026-10-09.
- `research/16_epic_harness/harness_eval/REPORT.md` and `MODELS.md`: the s15 harness on three agentic benchmarks with
  four open models, then reuse rounds e1–e4 on MiniMax-M2.7, 2026-10-12 to 10-17.
- `research/method_develop/README.md` (topic 17 before the rename) and its Claude Doc: a harness-aware serving design
  and the offline check E0, 2026-10-13.

## The question

An agent harness rewrites its own context. Once a conversation passes 50 messages, s15's rolling snip archives the middle
on every call and moves everything after message 3 to new positions. A prefix-caching server must then re-prefill text it
has already computed.

The question has four parts:
- Does reusing the surviving KV cost accuracy? The reuse arms are shift (re-rotate the RoPE) and EPIC-k (also recompute
  the first k tokens of each moved run).
- How far does the reused KV drift from the original as shifts accumulate?
- Should divergence between the original and the curated KV trigger a full recompute?
- Does a harness-aware serving design, built around such edits, pay, and what does prior work already cover?

## The answer, in one line

**Reuse after the snip costs less than 5 points of accuracy (pre-registered) and computes 91 % fewer prompt tokens,
although the reused KV drifts further with every shift.** What those savings buy when many users fill the HBM, where
prefill, decode and cache residency compete, is open. Our time models priced one session's calls in isolation, and the
emulating server computed every token, so nothing here measures it.

## Every experiment of the fortnight, and where it is in the deck

| date (UTC) | experiment | scale | key result | slides |
|---|---|---|---|---|
| 10-08 | Hourly Drive auto-sync watcher for research data (b64bc9f; it runs on alpha1, not on this host) | infrastructure | — | 13 |
| 10-09 | Topic 16 E-0: KV reuse of the original s15 and its append-only versions (stock vLLM) | 54 sessions | reuse 4.2 % → 89.3 % (compaction off), 51.1 % with snips (R1); the template change moves the first token (KL 0.1318 mean) | 2, 14 |
| 10-09 | Topic 16 selftests of the EPIC connector | 12 checks | 12 of 12 pass (loads bit-identical; re-rotation rel. error 0.0010) | 14 |
| 10-09 | Topic 16 smoke and batch-invariance runs | 41-call smoke; 4 batch-invariant runs | batch-invariant kernels pair arms exactly but slow decode 2.6×, so the live runs use standard kernels | 14 |
| 10-09 | Topic 16 pilot and its gates; R2 (LLM-summary compaction) dropped | 64 runs | G2 8 / 12 blocks identical through the first edit; 52 % of runs end at the call cap; stale-row D^K 0.087 (shift) vs 0.043 (EPIC-128); TTFT 1,044 / 411 / 58–72 ms | 14, 22 |
| 10-09 | Topic 16 screen of the other cells | 48 cells | 12 compacting cells on 8 codebases kept for the main sweep | 14 |
| 10-09 | Topic 16 E-B live sweep (prefix, A/A, shift, EPIC-32/64/128) | 240 runs | every EPIC arm passes the 0.08 margin, yet the identical A/A copy fails it, so the test cannot tell arms from noise; shift loops (+0.250 at the budget); no EPIC-k beats shift (Holm p 1.000 / 0.321 / 1.000) | 3, 15, 22 |
| 10-09 | Topic 16 E-A compounded replay on identical prompts | 30 runs, 2,290 calls | EPIC-32/64/128 remove 11/22/30 % of shift's K deviation and 11/23/28 % of its reply KL; deep layers 21 %; compounding costs about 3× (2.96×) | 3, 22 |
| 10-09 | Topic 16 E-C time on one H100 | 600 timed calls | TTFT 494 → 70–86 ms; time model MdAPE 1.8 % / 3.8 % | 15 |
| 10-09 | Topic 16 X2: SWE-agent with observation masking, SWE-bench Verified Mini | 200 runs | 7/50 resolved under full re-prefill vs 6, 11, 9/50 under EPIC-32/64/128 (McNemar p = 1.00 / 0.29 / 0.63; one seed can only rule out about 15-20 points); prefill 44 % → 6 % | 3, 15 |
| 10-09 | Topic 16 incidents: a second GPU for 46 s, pilot session resets (79 of 801 EPIC-arm calls), a replay bookkeeping bug, the X2 client timeout | — | each fixed, the affected runs superseded and rerun; 2 repository tests fail since the s15 change | 20 |
| 10-09 | BACKGROUND.md: how harnesses, agentic capability and KV-reuse fidelity are evaluated | survey | §0–§8, ending in what the study adopts | 20 |
| 10-11 | MODELS.md: survey of open models that fit 4 H100s | 15 candidates | SWE-bench Verified published vs s15: MiniMax I 73.8 vs 0.644, Qwen3.8 I 86.0 vs 0.789, DeepSeek I 88.8 vs 0.900 | 16, 20 |
| 10-11 → 12 | harness_eval pilot: s15 vs SWE-agent on the same 50 instances (Qwen3-32B) | 50 instances | s15 4/50, SWE-agent 7/50; grader parity 49/49 | 16 |
| 10-12 → 13 | harness_eval profile: 4 models × SWE-bench Verified, Pro, Terminal-Bench | 1,080 trials | pass@1 0.90 / 0.94 / 0.59 (DeepSeek-V4-Flash) … 0.09 / 0.07 / 0.02 (Qwen3-32B); after the snip, hit rate 9–54 % | 2, 16 |
| 10-13 | e1 pilots: p0r0 and ep0 aborted, ep1 run | 15 trials | ep1 failed E-0 (97.3 %) and preemptions (4); fixes: last-split template, gpu_util 0.93, watermark 0.08 | 20 |
| 10-13 | e1: MiniMax-M2.7 confirmatory round on SWE-bench Verified | 450 trials | the pre-registered sequence stopped at a1 (−0.061 [−0.130, +0.007]); EPIC-128 +0.017; 3 of 62 trials hit the agent timeout, a bias against EPIC (shift without them −0.075 [−0.150, +0.001]) | 4, 10, 17, 20 |
| 10-13 | Qwen3.8-27B stage B: 10-trajectory replay and 5-task pilot | 20 trials, 522 replayed calls per arm | KL to prefix grows with snips (0.0353 for shift after 21+); age cap at the A/A floor (0.0070); reply metrics valid on 1 of 4 servers | 3, 9, 17, 21 |
| 10-13 | DeepSeek-V4-Flash stage C: design B and selftests | 4 selftest runs | mechanics verified; exactness hidden by 7–10 % run-to-run noise | 17 |
| 10-13 | e2: MiniMax on SWE-bench Pro and Terminal-Bench | 300 trials | Pro: a1 −0.083 [−0.155, −0.012]; Terminal-Bench A/A −0.300 [−0.485, −0.115], no conclusion; one replicate (150 GPU-h budget) | 10, 17, 21 |
| 10-13 | Topic 17 design (Claude Doc, 27 sources) and the offline check E0 (E0a–E0f) | CPU only | D1, D4 pass; D2, D3 fail; snip hysteresis cuts edit work 10.0 % → 0.7 % (offline) | 6, 7, 19 |
| 10-13 | P:D control deep dive (E0g rules proposed, not run) | CPU only | queue wait 0.01 s per lead call below the turning point; P per D 0.45–9 by harness state (model-based) | 12, 19 |
| 10-14 | e3 oracle check, then e3: pre-registered test on 100 new SWE-bench Verified tasks | 1,500 trials | gold patches all scored 1.0; EPIC-128 −0.010 [−0.043, +0.023], non-inferior at 5 points; a1 −0.000; one false interim stop (02:01), resumed at 02:25 | 4, 18, 20 |
| 10-15 | harness_eval moved into `research/16_epic_harness/`; topic 17 renamed to `method_develop/` | repository | uncommitted | 13 |
| 10-15 | e4 pilot and pre-run checks | 42 trials | selftest bounds raised from KL < 1e-3 to 0.02 (server noise 0.0019–0.0048); TCP MSS clamp after 10 verifiers stalled; E-0 99.06–99.30 % | 20 |
| 10-15 → 17 | e4: JSD-triggered full recompute, 7 arms × 3 benchmarks | 1,890 trials | 14 of 15 reuse intervals cover 0; JSD floor 66–72 % of its mean; probe floor first-token KL 0.0118 / 0.0069 / 0.0169 | 4, 5, 10, 11, 21 |
| 10-19 | e4 Docker-in-Docker host torn down (98 images, 88.21GB) | — | — | 13 |
| 10-21 | Topic 16 E-A replay re-analysed by the number of snips so far (`epic_kl_by_moves.py`, CPU) | 30 runs, 2,260 edited calls | shift's reply KL 0.0006 after the first snip, 0.0165 by snips 16–31, 0.0104 at 64+; a KV rebuilt fresh and shifted once stays at or below 0.0062 | 3 |

In all: 5,240 main-round trials plus 107 pilot trials in harness_eval; topic 16's 240 live runs, 200 SWE-agent runs, 30 replays and 600 timed calls, plus 54 E-0 sessions, 64 pilot runs and 48 screen runs; about 402 H100 GPU-hours, pilots included. Slides 1–9 carry the argument. Backup slides 10–22 hold every experiment in full: e4, the JSD decisions, P:D control, the ledger, topic 16, the profile, e1/e2 with Qwen3.8 and DeepSeek, e3, E0 with P:D sizing, the pilots, checks and incidents, and the secondary results.

## The evidence

| claim | evidence | source |
|---|---|---|
| The harness first had to stop busting its own cache | A per-second timestamp in the system prompt held KV reuse at 4.2 %. A static prompt plus an append-only template give 89.3 % (99 % of what is reusable). | topic 16 §4.0, `e0_tables.md` |
| After that, the snip is the one frequent edit | Hit rate before → after the snip falls from 61–92 % to 9–54 % across 4 models × 3 benchmarks. 83–96 % of re-prefilled tokens come after it. | REPORT §3 |
| EPIC-128 costs less than 5 points | **e3, pre-registered**, 100 new SWE-bench Verified tasks × 3: −0.010 [−0.043, +0.023], against an A/A of +0.020 [−0.017, +0.057] | REPORT §7 e3 |
| The earlier age-cap drop was noise | e3 H2: a1 −0.000 [−0.033, +0.033]. Pooled with e1 and e2: −0.023 [−0.050, +0.004]. | `report/e3_pooled.md` |
| Nothing beyond noise on three benchmarks | e4 (exploratory, 30 tasks × 3 per benchmark): 14 of 15 reuse intervals cover 0. At 90 %, about 1.5 exclusions are expected by chance. The exclusion, EPIC-128 on Pro (+0.117), was −0.083 on the same tasks in e2. | REPORT §7 e4 |
| The reused KV drifts further with every shift | Topic 16 replay (30 runs, 2,260 edited calls): shift's reply KL is 0.0006 after the first snip and 0.0165 by snips 16–31 (0.0104 at 64+); EPIC-128 0.0110 at 16–31; a KV rebuilt fresh before each call and shifted once stays at or below 0.0062. Qwen3.8 replay: shift's first-token KL 0.0073 → 0.0133 → 0.0179 → 0.0353 over 0, 1–5, 6–20 and 21+ snips, while the age cap a1 (0.0066–0.0103) and the A/A copy (0.0054–0.0062) stay flat. | `data/replay_kl_by_moves.md`; REPORT §7 |
| Fidelity is not restored, and accuracy does not need it | On MiniMax, EPIC-128's first-token KL equals plain shift's (0.0327 vs 0.0323, e4 SWE). In e3, a1 has 2.3× lower KL (0.0139 vs 0.0314) but the same accuracy (epic128 − a1: −0.010 [−0.053, +0.033]). On Qwen3-32B, EPIC-32/64/128 remove 11/22/30 % of shift's deviation, and the deep layers least (21 %). | REPORT §7; topic 16 §4.5 |
| Reuse cuts the prompt tokens computed by about 90 % | e3: 6,937 → 634 computed tokens per call (−91 %). Topic 16 (Qwen3-32B, one H100): time to first token 494 → 70–86 ms per call. | REPORT §7; topic 16 §4.6 |
| A JSD trigger is mostly noise | ρ against the real error is +0.529 / +0.599 / +0.442. But two probes of one cache already differ by 66–72 % of the mean JSD. It recomputes 5.55 % / 5.28 % / 6.96 % of decisions at 1.7–2.6× EPIC-128's tokens, with no measurable accuracy gain. | REPORT §7 e4 |
| Edits are predictable and material under load | 99.5 % of edit tokens are laid out at the previous decode's end (D4). Edits are 30 % / 27 % of GPU time at 4 / 8 sessions (D1; topic 11's cells, snip off, first order). | method_develop E0 |
| Exact refresh within a drift bound does not pay | D2: the best policy, chosen post hoc, recovers 49 % of the value at 0.32 of the tokens (bar: 50 % at 1/3). D3: 6–11 % fits tool windows at one session; at 4–16 sessions a 10 % decode slowdown would host 52–100 % of it (illustrative), moot because D2 fails. | method_develop E0 |

**What the savings buy is open.** The job-time models priced one session's calls in isolation. With many users filling
the HBM, prefill competes with decode for the GPU and with other sessions for cache space, so what a 91 % cut in computed
prompt tokens buys depends on scheduling and residency, which is the design's subject (slides 6 and 7). The emulating
server could not measure it, because it computes every token.

## What is deployable

1. **The append-only harness.** A static system prompt, an append-only template, and context updates appended to the
   newest message. This is already in s15 (a03117a), and it closes 093026's step 19.
2. **EPIC-128 reuse after the snip, for full-attention models.** It is non-inferior at a 5-point margin
   (pre-registered), computes 3–8 % of the prompt instead of 33–53 %, and MiniMax does not loop under it (44–48 lead
   calls per run in every e3 arm).
3. **Not plain shift.** It has no pre-registered verdict: e1's sequence stopped before reaching it, and e3 dropped it.
   Pooled it is −0.033 [−0.086, +0.021]. On Qwen3-32B it loops: runs at the call budget +0.250 [+0.125, +0.375].

## What is retired or parked

- **Drift-bounded exact refresh** (the design's C2 "exact later"): it neither pays (D2) nor fits (D3). C2 reduces to
  EPIC.
- **Divergence-triggered recompute** (JSD, and TVD, EXP and KL, which track the error with ρ within 0.02 of JSD's):
  parked until the probes are batch-invariant, which costs 2.6× decode with today's kernels (topic 16). The noise floor
  is 66–72 % of the signal.
- **Qwen3-32B as the study model**: its pass@1 is 0.022–0.089 under s15. It stays only as topic 16's link.
- **GAIA**: dropped on 10-12, after its search key returned 403.
- **Live DeepSeek reuse**: it would need the stored-input design and many replicates, because its noise is 7–10 %
  between two computations of the same attention inputs.

## Threats, and what is not established

- **The payoff with many users.** With the HBM full, prefill competes with decode and with other sessions' cache. What
  reuse buys there is the open question, and these runs cannot answer it.
- **Margins below 5 points.** 2 points at 80 % power needs about 6× e3's 300 blocks.
- **The 30-task rounds.** Signs flip between rounds on the same tasks. Only e3 supports "within 5 points".
- **Pairing.** In no e3 block is the identical A/A copy the same run as prefix, and the median first divergence is at
  call 0, because the server is not batch-invariant. Pairing is by task, not by trajectory.
- **Terminal-Bench.**
  - Its round-trip check stays below the 99 % gate (95.7–96.6 % in e4) until the harness keeps token-level history.
  - In e2 its two identical control arms differed by −0.300 [−0.485, −0.115].
- **Reuse is emulated.** The server computes every token and substitutes the reused rows, so no run here measures time
  under load. E0 prices every model with Qwen3-32B H100 timing.
- **Other attention designs.** Hybrid (Qwen3.8, DeltaNet) and compressed-KV (DeepSeek, pooled CSA/HCA) models save no
  compute in this live form. Step 23 takes up the hybrid case.
- **The design's scheduling (C1) and residency (C3) claims** are not tested offline. C4 is strengthened, not tested.

## The paper, as it stands

- **Claim.** An agent harness edits its context predictably: 99.5 % of edits are known a round ahead. Cheap approximate
  KV repair is safe for accuracy (pre-registered). So a server can schedule edits as repair work and manage KV versions
  under contention.
- **Prior work** (slide 7) covers 13 of the design's 22 parts. Of the 5 that no paper covers, 4 concern the harness's
  edits: drift tracking and refresh, dead-version GC, server-to-harness feedback, and live edit diffs. The fifth is a
  runtime TP-vs-DP choice.
- **Missing.** The payoff under contention, and a real (not emulated) reuse path.
- **Go / no-go.** The E1 simulator must show at least 10 % over the best baseline, the gate the design doc proposes.

## Prior work by component (slide 7)

The design doc's prior-work table (rev 32) maps 23 design parts to papers, as "covered by" or "partly by". Here the rows
are grouped by the four components; row 22, contention past the turning point, is the regime itself. Corresponding
authors were read from each paper's own author block on 2026-10-21 (arXiv; USENIX for CacheSlide). "(last)" means the
paper marks no corresponding author, so its last author is given. The doc's P:D tab also cites HeteroPanacea and
Autopoiesis, which cover no part.

| component | design part | covered by | partly by | here |
|---|---|---|---|---|
| Request scheduler | 1. Taxonomy with a Modify effect | — | SmoothAgent, xLLM, Dynamo Planner, Tokencake, Leyline, SMetric | Extend: the scheduler sees Modify |
| Request scheduler | 2. Online or speculative DAG | HexAGenT, Autellix | SmoothAgent, Slipstream, Tokencake, KVFlow, CacheScout, TOPAS | Reuse; add speculative nodes from intent |
| Request scheduler | 3. Lifecycle (VALID / READY) | — | KVFlow, Tokencake, SmoothAgent, HexAGenT, TOPAS, Autellix, UNISON | Extend: READY revocable |
| Request scheduler | 4. Priority: classes, deadlines, critical path | SmoothAgent, HexAGenT, TOPAS, Autellix, Tokencake | Continuum, xLLM, SMetric, Dynamo Planner, CacheWise | Reuse |
| Request scheduler | 5. Cost model | SmoothAgent, HexAGenT, TOPAS, SMetric, xLLM | Continuum, Tokencake, UNISON, CacheWise, Dynamo Planner | Reuse topics 11 and 16 models |
| Request scheduler | 6. Stall budget | SmoothAgent, xLLM | CacheScout, SMetric | Reuse; size it from E0d |
| Request scheduler | 7. Background compaction | SmoothAgent, Slipstream, LCM | Parallel Context Compaction | Reuse |
| Request scheduler | 8. Exact lookahead KV | SmoothAgent | CacheScout | Reuse, below the turning point only |
| Memory manager and repair planner | 9. Approximate repair | Leyline, CacheSlide, AgentKVShift, EPIC | — | Reuse shift + EPIC on GQA |
| Memory manager and repair planner | 10. Drift tracking and refresh | — | Leyline, CacheSlide, AgentKVShift | New |
| Memory manager and repair planner | 11. Admission / gate | — | HexAGenT, TOPAS, Tokencake, CacheWise, xLLM | Extend: gate by DAG state |
| Memory manager and repair planner | 12. Queue-time protection | Continuum | — | Reuse |
| Memory manager and repair planner | 13. Next-use eviction | Continuum, Tokencake, KVFlow, CacheScout, UNISON, CacheWise, Ask the Tool | TOPAS, SMetric, Leyline | Reuse; add barrier-aware next use |
| Memory manager and repair planner | 14. Dead-version GC | — | KVFlow, CacheSlide, Leyline | New: dead-first by edit version, DRAM included |
| Memory manager and repair planner | 15. Preload | Tokencake, KVFlow, CacheScout, UNISON, Ask the Tool | TOPAS, SMetric, Leyline, CacheSlide | Reuse |
| GPU reconfigurator | 19. P/D placement | HexAGenT, xLLM, PPD, AMPD, ConServe | Autellix, SMetric, Dynamo Planner | Reuse |
| GPU reconfigurator | 20. Dynamic P:D or layout | xLLM, Dynamo Planner, PEARL | HeteroScale, AMPD | Reuse; add DAG forecasts |
| GPU reconfigurator | 21. TP vs DP from the working set | — | xLLM, Dynamo Planner, AMPD | New: a runtime choice |
| Harness | 16. Duration or progress hints | Continuum, Tokencake, CacheWise, Ask the Tool | UNISON, Autellix, Parallel Context Compaction | Reuse progress streams |
| Harness | 17. Server → harness feedback | — | Ask the Tool | New: feedback that times edits |
| Harness | 18. Edit diff protocol | — | Leyline, CacheSlide | New: live diffs for GQA |
| Harness | 23. Harness co-design | — | SmoothAgent, Slipstream, LCM, Parallel Context Compaction, Ask the Tool, Leyline, CacheSlide | Extend: cold-cache edit timing |
| (the regime) | 22. Contention past the turning point | CacheWise | Continuum, Tokencake, KVFlow, CacheScout, UNISON, TOPAS, Autellix, SMetric, Leyline, CacheSlide | Reuse the regime; add edits |

| system | arXiv id or venue | corresponding author(s), as the paper marks them | affiliation |
|---|---|---|---|
| AgentKVShift | 2607.21604 | Nilesh Prasad Pandey | UC San Diego |
| AMPD | 2602.14516 v2 (ICML 2026) | Fangcheng Fu | Shanghai Jiao Tong University |
| Ask the Tool, Don't Guess | 2609.18849 | Huanchen Zhang (last) | Tsinghua University |
| Autellix | 2502.13965 | Ion Stoica (last) | UC Berkeley |
| Autopoiesis | 2604.07144 | Fangcheng Fu; Binhang Yuan | Shanghai Jiao Tong University; HKUST |
| CacheScout | 2608.14624 | Liting Hu (last) | UC Santa Cruz |
| CacheSlide | FAST '26 | Yunfei Gu, Chentao Wu | Shanghai Jiao Tong University |
| CacheWise | 2606.16824 | Haiying Shen (last) | University of Virginia |
| ConServe | 2606.01839 | Jianru Ding | University of Chicago |
| Continuum | 2511.02230 | Hanchen Li (marked in v1; v7 marks none, last author Ion Stoica) | UC Berkeley |
| Dynamo Planner | docs, not a paper | — | NVIDIA |
| EPIC | 2410.15332 (ICML 2025) | Tao Xie; Yizhou Shan | Peking University; Huawei Cloud |
| HeteroPanacea | 2608.03741 | George A. Constantinides (last) | Imperial College London |
| HeteroScale | 2508.19559 | Shufan Liu | ByteDance Seed |
| HexAGenT | 2605.16637 | Binhang Yuan (last) | HKUST |
| KVFlow | 2507.07400 | Zhengding Hu | UC San Diego |
| LCM | 2605.04050 | Theodore Blackman (last) | Voltropy PBC |
| Leyline | 2606.01065 | Harald Köstler (last) | Erlangen National High Performance Computing Center (NHR@FAU) |
| Parallel Context Compaction | 2605.23296 | Mahmut Kandemir (last) | Penn State |
| PEARL | 2609.35158 | Youhui Bai | USTC |
| PPD | 2603.13358 v2 (ICML 2026) | Zongze Li | University of Chicago |
| Slipstream | 2605.08580 | Ravi Netravali (last) | Princeton University |
| SMetric | 2607.08565 | Xingda Wei | Shanghai Jiao Tong University (IPADS) |
| SmoothAgent | 2607.00151 | Yufei Ding (last) | UC San Diego |
| Tokencake | 2510.18586 v5 | Youwei Zhuo | Peking University |
| TOPAS | 2608.25523 | Haisheng Tan (last) | USTC |
| UNISON | 2609.09643 | Yan Li | Fudan University |
| xLLM | 2510.14686 v2 | 9 marked: Tongxuan Liu, Ke Zhang (JD.com); Guyue Liu, Tong Yang (Peking University); Hailong Yang (Beihang University); Jing Li (USTC); Hui Chen, Guiguang Ding (Tsinghua University); Yitao Hu (Tianjin University) | JD.com and five universities |

## Next steps

Numbering continues 093026's 19–22. The dates are proposals.

23. **KV management for hybrid models** (Qwen3.8-27B; CPU first, then 1 GPU).
    - *Why it is open.* Qwen3.8 is 16 × (3 Gated DeltaNet + 1 gated attention). The 16 attention layers keep 64 KiB of
      KV per token and can be moved by re-rotation (RoPE on 64 of 256 dims). The 48 DeltaNet layers keep a recurrent
      state instead, ~72 MiB per sequence (the size of 1,152 tokens of attention KV), which sums up every earlier
      token. After a snip every later token must run through the whole model again to rebuild it, so our port computed
      them exactly (`epic/epic_connector.py`) and reuse saved no compute: the arms computed what full re-prefill does
      (66.5 % of the prompt), not the 5.7–6.1 % the attention layers alone need. With the DeltaNet states exact, the
      stale attention KV alone raised first-token KL from 0.007 before the first snip to 0.032–0.035 after 20 or more;
      in a 5-task pilot, EPIC runs took 118–128 lead calls against 78.
    - *23a, survey* (by 10-28): how vLLM 0.31.0, SGLang and DeepInfer keep, checkpoint and evict a per-sequence state
      beside paged KV; the papers on caching for hybrid models.
    - *23b, repair the linear layers*: recompute from the head's checkpoint (exact, today's cost); re-run only the
      recurrence over each DeltaNet layer's stored per-token inputs (approximate like shift; memory for compute); or
      recompute only when drift is large.
    - *23c, measure* (1 GPU): KL against full re-prefill, tokens computed and memory per session on the Qwen3.8 replay;
      then accuracy.
    - *23d, two pools*: paged KV and a fixed state per session compete for HBM at 4–16 sessions: what to keep, offload
      or drop.
24. **DeepSeek DeepInfer**, open-sourced 2026-10-20 (reading, no GPU). Sources: `github.com/deepseek-ai/DeepInfer`
    (README, `csrc/engine/scheduler.hpp`, `doc/inference_deployment_qa.md`) and DeepSeek's Zhihu post
    (`zhuanlan.zhihu.com/p/2095560295930508953`), read 2026-10-21; its numbers are DeepSeek's own.
    - *What it is.* DeepSeek's in-house inference engine, derived from vLLM, Apache-2.0. It serves only
      DeepSeek-V4.1-Flash, on NVIDIA SM100 or Huawei Ascend 950, so not on our H100s.
    - *Open*: a C++ scheduler and block manager with two pools, paged KV blocks plus one fixed state slot per sequence,
      admitted together; chunked prefill, preemption, prefix forks. *Withheld*: the KV-cache system, P/D
      disaggregation and data-parallel load balancing.
    - *Documented for production*: KV in HBM; sliding-window KV in host DRAM (TTL about 180 s); paged KV on a 3FS SSD
      cluster, kept 3 days for agent traffic and 7 for chat. 96% of input tokens hit the cache, at 77K-token average
      inputs; P:D roughly 1:1, autoscaled; prefill FIFO within 4 context-length tiers; no priorities or fairness.
    - *24a, read the engine*: its two-pool admission and state slot are the closest open design for step 23's state
      pool.
    - *24b, place it on slide 7's map*: it covers tiered residency (memory manager) and P:D sizing (GPU
      reconfigurator), with no harness hints, edit handling or typed scheduling.
    - *24c, the agent question*: how does a hash-chain prefix match reach 96% hits when, in our profile, the snip cuts
      vLLM's hit rate to 9–54 %? Which of its clients edit their context, and how?

**Still open from the 10-19 plan:**
- the E1 simulator (CPU): repair plus version-aware residency against vLLM FCFS, a session gate with a DRAM tier, and
  Continuum-, KVFlow-, SmoothAgent-, CacheWise- and Autellix-style baselines at 4–16 sessions, built from topic 15's
  pool simulator, topic 11's step model and E0d, with a pre-registered metric (proposed: lead calls per hour at equal
  p95 turn time);
- E0g (CPU): does the harness forecast P:D demand better than the waiting queue? Rules G1–G4 on harness_eval and
  topic 11 logs;
- token-level history in the harness, which removes MiniMax's last round-trip misses and is needed before any new
  Terminal-Bench round;
- E2, a real reuse path, live (1–2 GPUs): s15 at 4–32 sessions against the harness-fixed baseline, only if E1 shows at
  least 10 %;
- housekeeping: commit harness_eval and the method_develop rename, archive the 28 GB of traces to Drive, and regenerate
  the web app's versions.json (2 repository tests fail since the s15 change).

**Carried:**
- step 20, the session gate;
- step 21, the turning point;
- step 22, the board scan;
- 13's S3–S5;
- 14's P/D sweep, which the layout stage E4 needs.

## Corrections made to earlier numbers

- **This deck's own 10-19 version.** It concluded that reuse saves one session little time (EPIC-128's modeled job
  time 0.94–0.99×; a derived 1.4× throughput ceiling). Those figures priced one session's calls in isolation, so they
  say nothing about many users sharing the HBM; they were withdrawn on 10-21.
- **MODELS.md §1 on hybrid models.** It says "shift" on a hybrid means re-rotated attention KV plus a stale DeltaNet
  state. The port computes the DeltaNet layers exactly instead (`epic/epic_connector.py`), as REPORT §7 says.
- **Topic 16 README, "no arm differs detectably".** The claim is too strong. Shift's score interval excludes 0 on the
  better side (+0.052 [+0.020, +0.088]) while it loops more (+0.250 at the call budget). The identical A/A copy fails
  the 0.08 margin (−0.031 [−0.106, +0.051]), so the job-level test is weak.
- **Topic 16 README.** "Commits: — (uncommitted)" is stale: the study is in a03117a.
- **method_develop README.** "Commits: none" is stale: it is in 59a24c1. The rename from `17_harness_aware_serving`
  is not committed.
- **Share of re-prefilled tokens after the snip.** `harness_eval/report/epic_summary.md` (in REPORT §7) says 82–97 %.
  Its own §3 table gives 83–96 %.
- **DeepSeek stage C GPU-hours.** The server records give 1.0; REPORT's stage text says "about 1.4".
- **"Baseline".** It means the profile run in e1 and e2 (Pro 0.867), but the full re-prefill arm in e4 (Pro 0.811).
- **The e1 plan.** The plan file REPORT cites for e1 was later overwritten by the e3 plan. e1's sequential test
  survives only in REPORT.md.
- **Topic 16's two "prefill savings" bases.**
  - "About 70 %" is modeled prefill time on identical trajectories (28–30 % of full re-prefill's).
  - "9–12 % vs 55 %" is live computed tokens.
- **E0's D3.** It failed at one session. At 4–16 sessions E0d's illustrative check finds a 10 % decode slowdown would
  host 52–100 % of the refresh, but the README calls it moot because D2 failed; the design doc's "fits neither tool
  pauses nor decode steps" overstates this.
