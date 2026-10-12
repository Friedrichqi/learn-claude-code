# 16 · EPIC inside the live harness: how much of the full re-prefill KV can EPIC-k restore after compaction, and what does that do to the job?

> **Status:** complete (2026-10-09); the parts of the plan that were not run are listed in §6.
>
> **Dates:** 2026-10-09 01:11-23:18 UTC (GPU stages). H100 80GB GPUs, one per process, pinned with `CUDA_VISIBLE_DEVICES`: GPU 0 only
> until 2026-10-09 14:30 UTC, then up to four at once (user's allowance), each claimed in the lab's GPU
> sheet before use. Every vLLM engine is stopped at the end of its stage and its GPU verified empty.
>
> **Models / providers:** `Qwen/Qwen3-32B` (BF16, non-thinking) served by vLLM 0.31.0; SWE-agent 1.0.1
> (JetBrains "Complexity Trap" fork, commit bf15b5f) for X2.
>
> **Commits:** — (uncommitted).
>
> **Background research:** [BACKGROUND.md](BACKGROUND.md), a survey of how agent harnesses and agentic
> systems are evaluated, and of how KV-reuse methods measure fidelity against full prefill.

**Short answer.** Qwen3-32B (BF16, non-thinking) on vLLM 0.31, one H100 per process, inside the s15 harness,
first made append-only.

- **The original system** reused 4 % of its prompt KV, because of a per-second timestamp in the system message.
  A static system message and an append-only chat template raise that to 89 %, which is 99 % of what is
  reusable. After that, only compaction breaks the cache: with snip and placeholders (R1) reuse falls to 51 %
  (§4.0).
- **How much KV EPIC restores: little, once edits compound.** On identical prompts (E-A), EPIC-32/64/128 remove
  11/22/30 % of the reused rows' K deviation that plain re-rotation (shift) leaves, and 11/23/28 % of the
  next-reply KL. But a sliding window moves every surviving row again on each round, and EPIC recomputes only
  the first k tokens of each moved run. Served in the loop, EPIC-128 is still twice as far from full
  re-prefill (reply KL 0.0091) as shift with a single step of staleness (0.0043) (§4.5).
- **Accuracy: no measurable harm.** On 48 paired blocks of 12 compaction-heavy coding tasks (240 live runs),
  every EPIC arm is non-inferior to full re-prefill on the task score at the pre-registered margin of 0.08
  (differences −0.023 / +0.044 / +0.026). No arm differs detectably, and no run passed in any arm. The one
  job-level effect belongs to shift: without repair the model loops more (+25 points of runs ending at the call
  budget); the EPIC arms sit in between (+4 to +8, not significant) (§4.4). In a SWE-agent replica on 50
  SWE-bench Verified instances, the resolve rate stays where full re-prefill has it (7/50 vs 6, 11 and
  9/50) (§4.7).
- **Performance: prefill drops about 70 %, the job about 15 %.** EPIC computes 9-12 % of each lead call's prompt
  instead of 55 %. Time to first token on an H100 falls from 494 ms to 70-86 ms (median; full recompute
  904 ms). On identical trajectories the modeled job completion time is 0.85× full re-prefill, because decode
  is three quarters of each job. The time model predicts measured call times on stock vLLM within 3.4 % (§4.6).

**Contents**

- §1 Question · §2 What changed in the harness first (append-only system message and chat template)
- §3 Method (§3.1 arms, §3.2 regimes, §3.3 tasks and graders, §3.4 metrics, §3.5 experiments, §3.6
  statistics, §3.7 the live EPIC server)
- §4 Results (§4.0 E-0 reuse of the original system, §4.1 selftests, §4.2 pilot, §4.3 base-rate screen and
  the main design, §4.4 E-B live runs, §4.5 E-A compounded replay, §4.6 E-C time, §4.7 X2 SWE-agent replica)
- §5 Conclusions · §6 Caveats · §7 Reproduction · GPU time log · Data inventory · Source map · Cleanup notes

**Files in this folder** (`research/16_epic_harness/`)

| path | purpose |
|---|---|
| `README.md` | this document |
| `BACKGROUND.md` | the background survey (how harnesses and agents are evaluated; how KV reuse is evaluated) |
| `qwen3_append_only.jinja` | the official Qwen3 chat template plus one change: an assistant turn without reasoning is re-rendered with the empty think block the non-thinking generation prompt ended with |
| `epic_common.py` | paths, vLLM lifecycle of one process on one GPU (`served`, `run_isolated`: process groups, guaranteed stop, GPU-empty check), lanes, template-aware rendering, `server_logs` |
| `epic_session.py` | one live s15 session: capture, arm tagging of lead calls, deterministic tool ids and transcript names, overflow shim, lead-call budget |
| `epic_reuse.py` | E-0: reuse of configurations A/B/C on stock vLLM, miss attribution, template-shift KL |
| `epic_plan.py` | the planner: block-anchored alignment, EPIC run heads, per-row slot / birth / age bookkeeping, the slot pool |
| `epic_connector.py` | the vLLM KV connector: executes plans (loads below the exact prefix, overrides, saves of computed and decode rows) against a pinned host-RAM row pool; RPCs for selftests and L1 deviation |
| `epic_server_ext.py` | the API-server middleware: per-call planning, cache salts, common-random-number seeds, shadow probes, per-call log |
| `epic_workloads.py` | cells, block designs and manifests (pilot, main) |
| `epic_grade.py` | the six deterministic task graders and the audit sheet |
| `epic_sweep.py` | live runs of a schedule on one GPU over lanes; a sweep split over GPUs runs one process per GPU on whole blocks (`--tag` names its logs, `--lane-offset` its lanes) |
| `epic_selftest.py` | GPU selftests of the connector (T1, T2, R1, R4, R6, R8, R9, R11, G5, T5, T6, T4) |
| `epic_replay.py` | E-A: compounded replay of recorded prefix trajectories under every arm |
| `epic_latency.py` | E-C: shape-equivalent TTFT per arm on H100, TTFT/TPOT models, JCT, and the models' check against the E-0 calls on stock vLLM (`validate`) |
| `epic_swe.py` | X2: SWE-agent observation masking on SWE-bench Verified Mini under EPIC serving (one process per GPU; runs shared through claim directories) |
| `epic_analyze.py` | grading, per-run metrics, pilot gates, tables |
| `epic_figures.py` | hand-written SVG figures (L1 deviation by layer and age; dose-response over k) |
| `epic_pipeline.sh` | stage runner with a trap that stops every engine |
| `test_epic_harness.py`, `test_epic_grade.py` | CPU tests (C1-C9, C12, C13; graders) |

## 1. Question

The s15 harness edits its own history: `snip_compact` archives the middle of the conversation every
round once it passes 50 messages, `micro_compact` replaces old tool results with placeholders, and the
LLM-summary compactions (`compact_history`, `reactive_compact`) replace the history with a summary (the
reactive one keeps the last five messages). After such an edit a prefix-caching server re-prefills
everything after the first edited token, although most of it is unchanged text at new positions.

EPIC (Hu et al., ICML 2025, "LegoLink") reuses the surviving KV, re-rotates its RoPE to the new
positions and recomputes only the first k tokens of every reused run plus the fresh text. Topic 10
Part B measured EPIC-32/128 one event at a time (one step of staleness). This topic asks it of the whole
harness: served live, call after call, so that staleness compounds over many edits, **how much of the
full re-prefill KV does EPIC-k (k = 32, 64, 128) restore, and what does that do to the job's quality and
its time?**

## 2. What changed in the harness first: an append-only original system

*(E-0 results in §4.0.)* Two things broke the prefix cache on every call, independently of compaction:

- **The system message.** `assemble_system_prompt` (`s15_integrated_harness/code.py`) appended a
  per-second `Current time`, the per-turn recalled memory records, the memory catalog and the MCP server
  list. Topic 11 measured the prefix-hit rate falling from 87 % to 2 % because of the timestamp alone.
  The system message is now built once per session from static sections (plus a session-start snapshot
  of the memory catalog); recalled records and catalog updates are appended to the newest, not yet sent
  user message (`append_context_updates`); an MCP connection is announced by `connect_mcp`'s own tool
  result. Tests: `tests/test_append_only_context.py`; `tests/test_agent_teams_runtime.py` updated.
- **Qwen3's chat template** drops the empty `<think></think>` block when it re-renders the previous
  reply, so every reply's KV shifted by 4 tokens (topic 13: 99.9 % of calls). `qwen3_append_only.jinja`
  keeps it.

## 3. Method

### 3.1 Arms

One `vllm serve` (Qwen3-32B BF16, one H100, `--max-model-len 32768`, `--gpu-memory-utilization 0.92`,
prefill eager so the override hooks fire, decode replayed from full CUDA graphs, prefix caching on,
FlashAttention, no async scheduling, the append-only template) serves every arm; epic_session tags each
lead call with its arm, and the middleware gives every arm-session its own cache salt, so each arm's
exact prefix comes from vLLM's own prefix cache and never from another arm.

| arm | KV of a lead call after the first edit |
|---|---|
| `prefix` (baseline: full re-prefill) | vanilla prefix caching: the exact prefix from the cache, everything after the first edit recomputed |
| `prefix_aa` | `prefix` again (same prompts, same seeds): the noise floor |
| `shift` (EPIC-0) | every surviving run reused from the arm's own as-used cache, re-rotated; only fresh text computed |
| `epic32` / `epic64` / `epic128` | shift, plus the first k tokens of every reused run recomputed at all layers (LegoLink) |

The reused rows are applied by the connector as overrides inside one forward (topic 10 Part B's
emulation): outputs equal a real EPIC server's; time is measured separately (E-C). Rows keep their birth
position and are rotated once from it; the session entry is the KV each arm actually used (stale rows
included, decode-time rows of every reply), so staleness compounds across edits exactly as on a real
EPIC server. **Common random numbers:** per-call seeds SHA256(block, call) set by the middleware, and
byte-identical prompts across arms (deterministic tool ids and transcript names, one lane per block).
With batch-invariant kernels (`VLLM_BATCH_INVARIANT=1`) this made `prefix_aa` reproduce `prefix` call
for call over all 41 calls of the smoke, and every EPIC arm identical to `prefix` until its first reuse
after an edit; but those kernels slowed decode 2.6×, so the live runs use standard kernels, two sessions
at a time. A block's arms then share task, prompt bytes and seeds but not batch composition: they are
paired samples of the same task that can part ways before any edit, `prefix_aa` measures how often that
happens, and the step-level effect of the KV itself is measured on identical prompts by the shadow
probes (§3.7) and by E-A.

### 3.2 Regimes

- **R1** (topic 10's): `--context-limit 50000`: snip every round past 50 messages, placeholders at the limit.
- **R2** (LLM-call compaction): no snip, no character limit; a prompt that overflows the 24,768-token
  input window (vLLM's 400, translated by the session's shim) triggers `reactive_compact` (summary plus
  the last five messages, which EPIC reuses at a large shift); model-initiated `compact` (summary only)
  is the built-in negative control.

### 3.3 Tasks and graders

Topic 10's six templates on ten real codebases (`epic_workloads.py`: 60 cells, M2/M3 targets fixed, seed
trees hashed), graded deterministically (`epic_grade.py`): E1 inventory (module recall, citation
validity), E2 call trace (executed spine from `sys.setprofile`; file/function recall, precision), E3
orientation (AST class of each cited line: entry point / data structure / error handling), M1 BUILD_TAG
(+ changelog lines), M2 rename (all AST sites, collateral-free, the package imports), M3 docstrings
(coverage, AST behaviour unchanged). Scores give partial credit where a template has a content and a
grounding part (E1, E2, M1: half the content, half the content that is also grounded); pass/fail is
strict. Lead-call cap 150 per run; from the screen on, also 32,000 output tokens per run.

### 3.4 Metrics

L1 KV: per-layer K/V deviation from a fresh recompute of the same prompt, by row class (exact, reused
stale, run head, computed) × age (edits survived) × offset from the run head. L2 step: first-token KL,
teacher-forced KL / top-1 over the arm's own reply against the fresh prefix, counterfactual next round
with the same seed. L3 job: task score / pass, pass^k, process (calls, edit failures, caps), divergence
from `prefix`. L4 system: computed / reused / recomputed tokens per call, KV hit rate, H100 TTFT model and
modeled JCT (E-C).

### 3.5 Experiments

E-0 (§4.0) · selftests (§4.1) · pilot (§4.2) · base-rate screen and main design (§4.3) · E-B live runs
(§4.4) · E-A compounded replay on recorded `prefix` trajectories (§4.5) · E-C time on H100 (§4.6) · X2
SWE-agent replica (§4.7).

### 3.6 Statistics (fixed before the main runs)

Primary endpoint: R1 task score, paired by block, non-inferiority of each EPIC arm against `prefix` at a
margin of 0.08, one-sided α = 0.05, tested in the fixed sequence epic128 → epic64 → epic32 → shift; 95 %
intervals from a cluster bootstrap over cells (10k resamples). Secondary: pass/fail by exact McNemar,
pass^k over repeats, the dose trend over k, process counts; a difference is read against the `prefix_aa`
interval. R2 is exploratory. Step and KV metrics (thousands of calls) are summarized per arm with
Benjamini-Hochberg control (q = 0.10) where they are tested.

### 3.7 The live EPIC server

`epic_server_ext` (vLLM `--middleware`) patches `AsyncLLM.generate`. For a tagged EPIC call it aligns the
prompt with the session's last sequence (`epic_plan.block_plan`, ~5 ms), allocates host-pool slots for
the rows the call will compute and decode, and sends the plan to `epic_connector`: loads below the exact
prefix on a cache miss, overrides of reused rows (re-rotated from their birth position) inside the
attention pre-hooks, saves of computed and decode rows. When vLLM's prefix cache serves beyond the
planned prefix, the connector copies those rows into a reserve and the entry takes them (cleanup note,
05:03). On a sampled call whose KV holds rows that survived an edit, and on every call that shifts a run
by ≥ 8,000 tokens (an LLM-summary compaction), a **shadow probe** recomputes the same prompt fresh and
measures L1 (per layer and row class), first-token KL, teacher-forced KL / top-1 over the arm's own reply,
and on a third of them a counterfactual next round from the fresh KV with the same seed; `prefix` arms get
the same probes as the noise floor of the probe itself. Every patch fails open.

## 4. Results

### 4.0 E-0: reuse of the original system

Stock vLLM 0.31 (prefix caching on, CUDA graphs, `--enable-prompt-tokens-details`), 2026-10-09
01:11–02:17 on one H100; 54 sessions (12 compaction-off cells × 3 configurations at 40 lead calls, 6 R1
cells × 3 at 100). Tables: `data/e0_reuse/e0_tables.md` (`epic_reuse.py report`). Configurations:
**A** the harness as it was (per-second timestamp, live memory/MCP sections; official template),
**B** append-only system message, official template, **C** append-only system message and template.

| condition | config | lead calls ≥ 2 | token-weighted reuse | exact extensions | where call k first differs from call k−1 + reply |
|---|---|---|---|---|---|
| compaction off | A | 238 | **4.2 %** | 0 % | system prompt: 100 % |
| compaction off | B | 187 | **87.0 %** | 0 % | re-rendered reply (think-strip): 96.8 %, compaction 3.2 % |
| compaction off | C | 170 | **89.3 %** | **95.3 %** | none 95.3 %, compaction 4.1 %, reply 0.6 % |
| R1 (snip + micro) | A | 227 | 5.6 % | 0 % | system prompt: 100 % |
| R1 | B | 239 | 50.5 % | 0 % | reply 43.9 %, compaction 56.1 % |
| R1 | C | 315 | 51.1 % | 16.2 % | none 16.2 %, compaction **83.8 %** |

- **The timestamp alone made the original system a cache miss on every call** (4–6 % reuse; the hits
  are the system prompt's first sections). Topic 11 measured the same (87 % → 2 %).
- **The static system message recovers almost everything** (87 %); what B still loses on every call is
  the previous reply, which the official Qwen3 template re-renders without its empty think block.
- **The append-only template removes that too:** 95.3 % of C's compaction-free calls (99.4 % of those
  that do not compact) are exact extensions of the previous call plus its reply. On those calls C reuses
  91.3 % of prompt tokens against a ceiling of 92.0 % (the rest is new content: tool results, the new
  turn), i.e. **99.3 % of everything reusable**. Gate E0 passes in its corrected form (exact extensions
  ≥ 98 % and ≥ 98 % of the reusable prefix realized); the pre-registered "token-weighted reuse ≥ 95 %"
  could not be met by any cache, since its ceiling is 92 %.
- **What remains is compaction.** In R1, 84 % of C's calls start with a compaction edit (snip fires
  every round past 50 messages), and reuse falls to 51 %: half of every prompt is re-prefilled although
  it is unchanged text at new positions. That residual is what EPIC is for.

**Template shift** (`epic_reuse.py kl`: 150 sampled C calls, each rendered with the official and with the
append-only template; greedy, 48 tokens): first-token KL mean 0.132 / median 0.004 nats, top-1
agreement 96.0 %, 78.0 % identical 48-token continuations (A/A floor: KL 0, 100 %, 98.7 %). The template
change moves the model about as much as a compaction does; it is the same in every arm of the EPIC
study, so it does not confound the arm comparison, and it is part of the "original system" the user
asked to make append-only. (E-0's own task scores cannot compare B and C: in the compaction-off
condition sessions end at the 32K window by design, and the R1 sessions were capped at 100 calls.)

### 4.1 Selftests (`epic_selftest.py`, one H100, vLLM 0.31; `data/selftest/selftest.json`)

| check | result |
|---|---|
| T1 load of saved rows vs vLLM's prefix cache on the same split | bit-identical (max Δ logprob 0.0, same 32 tokens) |
| T2 layer-0 keys re-rotated by +97 vs computed at +97 | rel. error 0.0010 (unrotated: 0.20); values identical |
| R1 overridden rows vs stored rows re-rotated (layers 0, 31, 63) | max abs diff 0.0 |
| R4 pre-hooks fired | 64 layers, 2,985 / 2,985 expected tokens overridden |
| R6 overrides across chunked prefill (12,115 tokens) | max abs diff 0.0 |
| R8 decode-time rows vs teacher-forced prefill rows | rel. error 0.0 / 0.010 (layers 0 / 63); next top-1 same |
| R9 one rotation by (p − birth) vs two chained bf16 rotations | 0.14 % relative drift avoided |
| R11 a load naming unknown slots | the request fails visibly (`finish: error`) |
| G5 null EPIC (every reused run recomputed) on a spliced prompt | same greedy output as prefix |
| T5 a plan whose exact prefix covers the whole prompt | capped below the last token; served |
| T6 prefix cache serving 96 tokens beyond the planned p0 | those rows copied to the reserve bit-identically (max abs diff 0.0) |
| T4 a plain request after all of the above | unchanged (no contamination) |

### 4.2 Pilot (`data/live/pilot/`, 2026-10-09 03:47-08:44, excluded from the confirmatory analysis)

**Design.** R1: 12 cells (2 per template) × {prefix, prefix_aa, shift, epic128}; R2: 8 large-context cells
× {prefix, epic128}; 150 lead calls per run, two sessions at a time, shadow probes on 1 in 4 eligible
calls. The first hour ran with the session-reset bug (cleanup note 05:03); the three runs it touched were
rerun, and every number below comes from runs without a reset.

**Gates.**

| gate | result | consequence |
|---|---|---|
| G1 rendering | 689 / 689 lead calls re-render offline to the server's prompt length | E-A can replay the recorded calls |
| G2 common random numbers | `prefix_aa` identical to `prefix` through the first edit in 8 / 12 blocks | arms are analyzed as paired but independent samples (planned fallback) |
| G3 edits | R1: 83 % of runs edit, median 127 edit calls per run; R2: 31 % reach an LLM-summary compaction | R1 kept; R2 dropped (below) |
| G4 base rates | pass rate of prefix-type runs: E1 0.29, M3 0.25, E2 / E3 / M1 / M2 0.00 | cells reselected from the screen (§4.3) |
| G5 null EPIC | selftest: k = ∞ reproduces prefix's greedy output | — |
| G6 run behaviour | emulated arms 1.23× prefix's median wall time; 52 % of runs end at the call cap | cap hits are an outcome (loops), reported per arm |
| G7 graders | blind audit 15 / 15 on pass/fail; partial credit added (cleanup note 08:50); second batch (15 screen runs) 13 / 15, both misses one citation-format defect of the EXPLAIN graders, fixed: 30 / 30 (cleanup note 14:45) | MODIFY graders (the main endpoints) frozen before the main runs, 13 / 13 |
| G8 memory | no preemption, no OOM; host pool peak 50,235 of 200,000 rows | — |

**Where the job goes.** Most R1 runs that compact also loop: the model re-reads files that snips or
placeholders took away, re-issues todo lists, or retries edits, until the 150-call cap. Loops end runs
in every arm (cap hits: prefix 6, prefix_aa 8, shift 6, epic128 7 of 12), and runs short enough to
succeed often finish before the first snip (about 25 calls), so that EPIC never acts in them. Pass
rates were 1 / 12 (prefix), 2 / 12 (prefix_aa), 0 / 12 (shift), 1 / 12 (epic128).

**R2 was dropped.** s15's LLM-summary compactions leave EPIC little to reuse. `compact_history` keeps
only a summary, and `reactive_compact` keeps the last five messages: across the eight epic128 R2 runs there
were 7 compaction events, and EPIC reused 212-963 tokens in six of them and 7,467 in one (a 12.8K-token
shift), against 2.4-21K-token shifts. When a kept message is itself larger than the window, the
compaction loops without end: 7 of the 16 R2 runs (cleanup note 07:05). The 36 planned R2 runs went to
E-A instead.

**On-policy KV and step fidelity** (R1 shadow probes; rows are what each arm actually attended to):

| arm | probes | reused rows D^K | rows computed over stale context D^K | first-token KL (median) | reply KL (mean) | reply top-1 | counterfactual next round identical |
|---|---|---|---|---|---|---|---|
| shift | 218 | 0.087 | 0.020 | 2.7e-4 | 0.0090 | 99.6 % | 71 / 84 |
| epic128 | 249 | 0.043 | 0.009 | 4.7e-5 | 0.0092 | 99.2 % | 65 / 80 |
| prefix (probe floor) | 285 | — | — | 0 | 0.00006 | 99.9 % | 78 / 84 |

EPIC-128 halves the stale-row deviation and cuts the first-token KL 6×, but its reply KL is no lower
than shift's in these probes. The arms ran different trajectories: shift's runs sat in near-deterministic
loops more often, where any KV gives the same tokens. The paired comparison on identical prompts is E-A
(§4.5).

**Prefill.** Per lead call, prefix recomputed 61 % of the prompt (5,577 tokens: every snip breaks the
prefix right after the head), shift 13 % (1,169) and epic128 10 % (811). Shape-equivalent timing on these
calls (E-C first run, 24 calls): median TTFT recompute 1,044 ms, prefix 411 ms, shift 58 ms, epic32 59 ms,
epic64 61 ms, epic128 72 ms.

### 4.3 Base-rate screen and the main design (`data/live/screen/`, `data/cell_profile.json`, `data/live/main/selection.json`)

The screen ran the other 48 cells once each under `prefix` (R1, 08:54-10:54). With the pilot's prefix-type
runs, every cell has 1-2 measured runs. The cells split into three groups, with almost nothing between
them:

| group | cells | what EPIC can change |
|---|---|---|
| finish before the first snip (< 25 lead calls) | most E1 and E3, many M1 / M2 / M3 (e.g. `packaging-E1` 0.91, `tqdm-M3` 0.70, `typer-E3` 0.67) | nothing: no context edit, so every arm serves the same KV |
| compact and loop to the 150-call cap with nothing done | all ten E2 cells, every compacting E1 / E3 cell, `packaging-M2`, `rich-M2`, `tqdm-M2`, `typer-M3`, `httpx-M3`, ... | little: the outcome is 0 in every arm |
| compact and can score | `httpx-M2` 0.67, `requests-M2` 0.50, `s15_harness-M2` 0.50, `typer-M1` 0.31, `tqdm-M1` 0.15, `click-M2` 0.14, `requests-M1` 0.06 (and `yaml-M2` 0.25 under epic128) | the job-level effect |

**Main design (deviation from the plan, fixed before any main run).** The plan's 32 cells over all six
templates would mostly have measured loops or runs without edits. The main sweep uses the 12 R1 cells
where compaction happens and the outcome can move: the eight above and four more MODIFY cells whose
partial credit can move (`packaging-M1`, `httpx-M1`, `yaml-M1`, `packaging-M3-version`); 8 codebases,
M1 / M2 / M3 only. Each cell runs 4 repeats of {prefix, epic32, epic64, epic128}, plus shift on 2 of the
repeats and prefix_aa on the other 2: 48 blocks, 240 runs. Shadow probes on 1 in 8 eligible calls and on
every large-shift call; 150 lead calls and 32,000 output tokens per run. EXPLAIN tasks and the other
cells are covered at the KV and step level by E-A (§4.5), which replays recorded trajectories whatever
their outcome.

### 4.4 E-B live runs (`data/live/main/`, 2026-10-09 11:26-21:00 UTC; tables `main_tables.md`, `l1_tables.md`)

All 240 runs finished (12 cells × 4 repeats = 48 blocks; prefix, epic32, epic64 and epic128 in every block,
shift in 24 and prefix_aa in the other 24): 127 ended on their own, 113 at their budget (109 at 150 lead
calls, 4 at 32,000 output tokens), none failed or reached the wall-clock backstop. Generated by
`epic_analyze.py runs` and `main` (`--live live/main`); differences are arm minus prefix within a block, with
95 % intervals from a cluster bootstrap over the 12 cells.

**Task outcome (primary).**

| arm | blocks | score: arm (prefix) | difference [95 % CI] | non-inferior at −0.08 |
|---|---|---|---|---|
| prefix_aa (A/A) | 24 | 0.120 (0.151) | −0.031 [−0.106, +0.051] | (noise floor) |
| shift | 24 | 0.176 (0.123) | +0.052 [+0.020, +0.088] | yes |
| epic32 | 48 | 0.114 (0.137) | −0.023 [−0.065, +0.019] | yes |
| epic64 | 48 | 0.181 (0.137) | +0.044 [−0.027, +0.117] | yes |
| epic128 | 48 | 0.163 (0.137) | +0.026 [−0.047, +0.088] | yes |

- **Every EPIC arm is non-inferior to full re-prefill** at the pre-registered margin, in the fixed sequence
  epic128 → epic64 → epic32 → shift (all four pass, here with the 95 % interval where the one-sided test
  needs only the 90 % one). No arm is detectably worse or better than `prefix`; the A/A row shows how much
  two runs of the same arm with the same seeds differ: an interval as wide as the margin.
- **No run passed, in any arm (0 / 240).** The pass rules need a complete, collateral-free change, and these
  cells were chosen because their runs compact, which is also what makes them loop. Pass/fail, McNemar and
  pass^k are therefore uninformative here (all 0); the partial-credit score is the outcome that moves.
- **Secondary (Holm over four tests): no repair benefit is visible in the score.** EPIC-k minus shift on
  the 24 shift blocks: −0.025 / +0.075 / −0.015 for k = 32 / 64 / 128 (Holm p 1.00 / 0.32 / 1.00); slope
  over k = 0, 32, 64, 128: +0.045 per 100 tokens [−0.036, +0.118]. By template, M1 (6 cells) and M2 (5)
  show the same picture; M3 is one cell (`packaging-M3-version`) in which every run scored 0.

**Loops: the one job-level effect, and it is shift's.**

| arm | runs ending at the budget | arm − prefix [95 % CI] | arm − shift (exploratory) | lead calls per run | arm − prefix |
|---|---|---|---|---|---|
| prefix | 20 / 48 | — | −0.250 [−0.375, −0.125] | 82.9 | — |
| prefix_aa | 10 / 24 | −0.083 [−0.250, +0.083] | — | 83.8 | −3.5 [−29.8, +17.6] |
| shift | 14 / 24 | **+0.250 [+0.125, +0.375]** | — | 97.8 | **+19.2 [+1.3, +39.5]** |
| epic32 | 23 / 48 | +0.062 [−0.062, +0.188] | −0.125 [−0.292, +0.042] | 89.2 | +6.2 [−12.9, +26.6] |
| epic64 | 22 / 48 | +0.042 [−0.083, +0.188] | −0.125 [−0.333, +0.083] | 91.1 | +8.1 [−5.0, +22.4] |
| epic128 | 24 / 48 | +0.083 [−0.042, +0.208] | −0.083 [−0.250, +0.083] | 92.5 | +9.6 [−3.6, +23.8] |

Reusing stale KV with no repair makes the model loop more: shift ran out its budget in 14 of its 24
blocks where `prefix` did in 8, and ran 19 more lead calls per run. The EPIC arms sit between `prefix` and
shift (+4 to +8 points against `prefix`, −8 to −12 against shift), so recomputing run heads appears to take
back most of shift's extra looping, but neither of their intervals excludes 0 at 48 (24) blocks.

**Prefill and KV reuse.** Per lead call, `prefix` computed 4,359 of 7,889 prompt tokens (55 %: every snip
breaks the exact prefix right after the head, so the cache serves the head and little else); the reuse arms
computed 9-12 % (shift 899, epic32 789, epic64 731, epic128 1,029 tokens per call, each on its own trajectory,
with somewhat longer prompts), a KV hit rate of 88-91 % against 45 %. The run heads EPIC recomputes are 1.6 % (k = 32), 2.3 %
(64) and 3.9 % (128) of the reused rows.

**On-policy KV and step fidelity** (shadow probes: the same prompt recomputed fresh; D^K = Σ‖K − K\*‖² / Σ‖K\*‖²
over all 64 layers; the `prefix` probes, on exact KV, are the floor of the probe itself):

| arm | probes | reused rows D^K | run heads D^K | computed rows D^K | first-token KL | reply KL | reply top-1 | counterfactual next round identical |
|---|---|---|---|---|---|---|---|---|
| prefix (floor) | 248 | — | — | — | 0.0001 | 0.0001 | 99.97 % | 92 % |
| shift | 256 | 0.067 | — | 0.025 | 0.021 | 0.012 | 99.54 % | 84 % |
| epic32 | 442 | 0.066 | 0.024 | 0.023 | 0.009 | 0.013 | 99.52 % | 85 % |
| epic64 | 485 | 0.056 | 0.015 | 0.016 | 0.013 | 0.010 | 99.30 % | 82 % |
| epic128 | 501 | 0.055 | 0.010 | 0.018 | 0.012 | 0.008 | 99.58 % | 81 % |

Reused rows by edits survived since they were computed (share of reused rows, then D^K; shift / epic32 / epic64 /
epic128): age 1, 16 / 14 / 14 / 19 %, 0.049 / 0.038 / 0.034 / 0.036; age 2-3, 27 / 26 / 22 / 31 %, 0.065 / 0.056
/ 0.049 / 0.057; age 4-15, 37 / 49 / 52 / 38 %, 0.057-0.081 / 0.058-0.078 / 0.042-0.059 / 0.043-0.064; age 16+,
20 / 11 / 12 / 11 %, 0.080 / 0.118 / 0.116 / 0.087. (Age 0, rows reused without having crossed an edit, is under
1 % of the rows in every arm.)

**How much of the full re-prefill KV EPIC restores in the closed loop: little, because staleness compounds.**
EPIC recomputes only the first k tokens of each moved run; every other moved row keeps the K and V it was
computed with. In R1, snip moves the surviving tail on every round, so 80-86 % of the reused rows have crossed
two or more edits since they were computed, and their deviation grows with that age (shift: 0.049 after one
edit, 0.065 after two or three, 0.08 after eight or more). EPIC lowers it by up to 30 % at ages 1-15, and beyond
16 edits every EPIC arm is above shift (0.087-0.118 vs 0.080; on-policy, so different rows). Over all reused
rows the restoration fraction 1 − D_epic / D_shift is 2 % (k = 32), 16 % (64)
and 18 % (128); the deviation sits in the deep layers (layers 48-51: 0.16-0.19 in every arm). The step metrics
follow: EPIC roughly halves the first-token KL against shift, but reply KL and top-1 agreement barely move, and
a counterfactual next round from fresh KV is identical about as often as under shift (81-85 % vs 84 %; 92 % on
exact KV). These probes sit on each arm's own trajectory; §4.5 compares the arms on identical prompts, with
one-step controls.

**Job time as run** (E-C models, §4.6): the EPIC arms spent 28-32 s less in prefill per run than `prefix`
in the same block (prefix: 44.5 s per run; intervals exclude 0), but their trajectories differ, and decode
(200-250 s per run) varies far more than that: job completion time differs by −39 to −60 s with intervals
spanning 0, and the A/A pair alone differs by −74 s. The paired comparison on identical trajectories is in §4.6.

### 4.5 E-A compounded replay (`data/live/*/replay*/`, tables `data/replay_tables.md`, figures `data/figures/`)

**Design.** 30 recorded `prefix` runs with at least 40 lead calls (a seeded draw per source: 10 from main, 12
from the screen, 8 from the pilot, two of them R2), their first 80 lead calls each: 2,290 calls, 2,260 of them
after an edit. Every arm is replayed on the same prompts (`epic_replay.py`, offline engine with the connector):
a fresh full recompute is the reference; the **compounded** arms (shift, epic32, epic64, epic128) keep their own
session entry across the run, as the live server does; the **one-step** arms (shift1, epic128_1) are rebuilt
from the fresh entry of the previous call, topic 10's setting. Each arm is teacher-forced over the recorded
reply; every fifth edited call also decodes a greedy 512-token next round.

| arm | reply KL [95 % CI] | reply KL vs shift, paired | first-token KL vs shift | top-1 | greedy next round identical | KV restoration ρ (reused rows) |
|---|---|---|---|---|---|---|
| shift | 0.0127 [0.0083, 0.0174] | 1 | 1 | 99.29 % | 77.9 % | 0 |
| epic32 | 0.0113 [0.0075, 0.0154] | **0.89 [0.86, 0.92]** | 0.86 [0.73, 1.05] | 99.35 % | 79.6 % | 11 % [8, 14] |
| epic64 | 0.0098 [0.0063, 0.0137] | **0.77 [0.66, 0.90]** | 0.82 [0.68, 1.08] | 99.38 % | 80.0 % | 22 % [17, 28] |
| epic128 | 0.0091 [0.0057, 0.0131] | **0.72 [0.59, 0.85]** | **0.66 [0.48, 0.95]** | 99.40 % | 81.0 % | 30 % [24, 37] |
| shift1 (one step) | 0.0043 [0.0026, 0.0061] | 1 | 1 | 99.68 % | 89.8 % | (64 % below compounded shift) |
| epic128_1 (one step) | 0.0031 [0.0019, 0.0045] | **0.73 [0.67, 0.80]** vs shift1 | 0.88 [0.76, 1.00] | 99.73 % | 91.0 % | (67 %) |

ρ = 1 − D_arm / D_shift, D = Σ‖K − K\*‖² / Σ‖K\*‖² over the reused-stale rows, pooled over calls; intervals
resample runs. By layer group, EPIC-128 restores 55 % (layers 0-15), 50 % (16-31), 35 % (32-47) and 21 %
(48-63): the deep layers carry most of the deviation and get the least repair.

- **On identical prompts EPIC's repair is real and grows with k.** Reply KL against a fresh recompute falls by
  11 % (k = 32), 23 % (64) and 28 % (128) relative to shift, all three intervals excluding 1, and the reused
  rows' K deviation by 11 / 22 / 30 %. One step from fresh KV, EPIC-128 cuts reply KL by 27 % (topic 10 measured
  33-46 % on next-round KL over 683 one-step events).
- **Compounding is the larger effect.** The same prompts with fresh KV before each call give about 3× less
  reply KL (shift 2.96× [2.56, 3.50], EPIC-128 2.91× [2.47, 3.52]): EPIC-128 served as it would be (0.0091) is
  twice as far from full re-prefill as shift with one step of staleness (0.0043). In KV terms, EPIC-128 restores
  30 % of shift's deviation in the loop; refreshing every row before each call would remove 64 %.
- **Where it matters.** Reply KL is largest where the model re-reads or copies what moved: E1 inventories
  (0.035 under shift), M2 renames (0.018) and M3 docstrings (0.020). EPIC-128 helps most on M2 (0.0116) and
  E1 (0.0268) and not on M3 (0.0202). Top-1 agreement stays above 99.3 % in every arm, and 78-81 % of greedy
  next rounds are identical to the fresh one in the compounded arms (90-91 % one step).

`data/figures/dose.svg` puts the E-A KV and reply-KL dose-response next to the E-B task scores and the E-C
prefill time per call; `data/figures/l1_replay.svg` breaks the reused rows' deviation down by age, offset from
the run head and layer.

### 4.6 E-C time on one H100 (`data/live/main/latency/`, 2026-10-09 18:35-20:35 UTC, GPU 2)

600 lead calls drawn from the main sweep's EPIC-arm calls (`epic_latency.py run --calls 600`, at 197 of 240
runs, when the logs held 12,229 such calls), each timed under every arm as the request a real
implementation would run (median of 3, rotating arm order; CUDA graphs; reused rows loaded from a GPU store):

| arm | tokens computed (mean) | TTFT median (ms) | TTFT mean (ms) | share of recompute (median) |
|---|---|---|---|---|
| recompute | 8,126 | 904 | 945 | 100 % |
| prefix | 4,571 | 494 | 573 | 62 % |
| shift | 590 | 65 | 119 | 9 % |
| epic32 | 650 | 70 | 124 | 9 % |
| epic64 | 708 | 77 | 130 | 10 % |
| epic128 | 826 | 86 | 143 | 11 % |

TTFT model, fitted on 400 calls and checked on the other 200: computing family 41.2 ms + 0.0875 ms per
computed token + 0.00268 ms per computed token per 1k context (held-out MdAPE 1.8 %); reuse family 31.7 ms
+ 0.0842 · C + 0.00353 · C · P / 1e3 + 0.00355 ms per loaded row (MdAPE 3.8 %). TPOT is 26.3-28.9 ms per
token from 2k to 30k context with one sequence (26.5-29.0 ms with two). The planner adds 0.38-0.45 s per
run in the reuse arms (4-5 ms per call, measured live).

**The model against real serving** (`epic_latency.py validate`): every lead call of E-0's configuration C
(503 calls, stock vLLM with its prefix cache, two sessions at a time, non-streamed), priced from its own
usage exactly as the JCT model prices a `prefix` call, against its measured time: MdAPE 3.4 % per call and
4.3 % per run (sum of a run's calls), median signed error −2.8 % (the API server's own per-call overhead,
visible on short calls: −4.7 % for replies of ≤ 50 tokens); with the two-sequence TPOT for the share of a
call another request was running, 4.1 % / −0.8 %. The reuse family cannot be checked this way, since no
EPIC server exists to time; it rests on the shape-equivalent method (topic 10).

**Job completion time on identical trajectories** (`epic_latency.py jct`, the E-A calls priced under every
arm: 30 runs, 2,290 lead calls; decode 4,164 s and tool time 239 s are the same in every arm):

| arm | prefill per call (ms) | vs prefix | vs recompute | JCT (s, all 30 runs) | JCT vs prefix |
|---|---|---|---|---|---|
| recompute | 1,005 | 192 % | 100 % | 6,706 | 1.197 |
| prefix | 523 | 100 % | 52 % | 5,600 | 1.000 |
| shift | 143 | 27 % | 14 % | 4,730 | 0.845 |
| epic32 | 147 | 28 % | 15 % | 4,741 | 0.847 |
| epic64 | 151 | 29 % | 15 % | 4,749 | 0.848 |
| epic128 | 157 | 30 % | 16 % | 4,764 | 0.851 |

EPIC cuts the prefill of a lead call to 28-30 % of full re-prefill's (to 15-16 % of a full recompute's), and
the job completion time by 15 %: decode is 74 % and prefill 21 % of these jobs' modeled time under full
re-prefill, so removing all prefill would save at most 21 %. The heads EPIC recomputes cost 4-14 ms per call
over shift; the planner's 4-5 ms per call is included.

### 4.7 X2: SWE-agent observation masking on SWE-bench Verified Mini (`data/x2_swe/`, table `x2_tables.md`)

JetBrains' SWE-agent fork with their observation-masking configuration (the last 10 observations kept; every
turn past ten masks the oldest one, a mid-context edit on every call), function calling, Qwen3-32B on the same
EPIC server through OpenAI chat completions (temperature 0.8, 8K output tokens per call, 24,576 input tokens,
150 calls); the 50 instances of SWE-bench Verified Mini × {prefix, epic32, epic64, epic128} × 1 seed, 14:48-21:29
UTC on up to three GPUs; graded by the official harness (`epic_swe.py report`).

| arm | resolved [95 % CI] | patch produced | tests timed out | model calls per run (median / mean) | runs at 150 calls |
|---|---|---|---|---|---|
| prefix | 7 / 50 (14 %) [7, 26] | 47 | 2 | 20.5 / 35.8 | 4 |
| epic32 | 6 / 50 (12 %) [6, 24] | 46 | 1 | 19.5 / 30.4 | 2 |
| epic64 | 11 / 50 (22 %) [13, 35] | 43 | 2 | 14.5 / 34.5 | 5 |
| epic128 | 9 / 50 (18 %) [10, 31] | 47 | 1 | 16.5 / 38.9 | 6 |

Paired with `prefix` on the same instance (only prefix / only arm resolved): 3 / 2 (epic32), 2 / 6 (epic64),
1 / 3 (epic128); exact McNemar p = 1.00 / 0.29 / 0.63. Qwen3-32B under this masking configuration resolved 15.0 % of all
500 Verified instances in JetBrains' study; `prefix` here resolves 14 %.

**Prefill.** Masking stops vLLM's prefix cache at the newly masked observation, so `prefix` computed 44 % of
each prompt (4,684 tokens per call; KV hit rate 56 %); the EPIC arms computed 6 % (634-664 tokens per call; hit
rate 94 %). Masking moved runs on 65-71 % of the EPIC arms' calls, and those calls supply 99.9 % of the reused
rows; the other edited calls differ only where the client re-serialized the previous reply (no shift).

**On-policy fidelity** (shadow probes): reused rows D^K 0.035 / 0.044 / 0.043 (epic32 / 64 / 128), first-token
KL 0.020 / 0.009 / 0.008, reply KL 0.005 / 0.004 / 0.003 against 0.0003 on exact KV, top-1 agreement 99.3 % in
every arm. One mask edit per call with a short placeholder leaves less staleness than s15's snip (reused-row
D^K 0.055-0.067).

**Reading.** In a second harness, with a different compaction (observation masking) and real repository
tasks, serving the reused KV through EPIC left the resolve rate where full re-prefill has it, while computing
6 % of each prompt instead of 44 %. One seed on 50 instances can only rule out differences of about 15-20
points.

## 5. Conclusions

1. **Make the cache work before measuring a cache method.** The original s15 reused 4 % of its prompt KV
   because of one per-second timestamp; a static system message plus an append-only chat template gave 89 %
   (99 % of what is reusable), and what is left is compaction (§4.0). Every EPIC number here is against that
   strong baseline, not against a harness that misses on every call.
2. **EPIC does not hurt the job in these harnesses.** Every EPIC arm is non-inferior to full re-prefill on the
   s15 task score at the pre-registered margin (§4.4), no job-level metric differs detectably, and the
   SWE-agent replica resolves as many instances under EPIC as under full re-prefill (§4.7). Per call, top-1
   agreement with a fresh recompute stays above 99.3 %.
3. **But EPIC restores only a small part of the full re-prefill KV once edits compound.** On identical prompts,
   EPIC-32/64/128 remove 11/22/30 % of the reused rows' K deviation and 11/23/28 % of the reply KL left by plain
   re-rotation (§4.5); on the live runs' own trajectories, 2/16/18 % (§4.4). The reason is structural: R1's
   snip moves the surviving tail every round, so 80-86 % of reused rows have crossed two or more edits, EPIC
   recomputes only the first k tokens of each moved run, and the remaining rows keep KV computed over contexts
   that no longer exist. Served in the loop, EPIC-128 is still about twice as far from full re-prefill as plain
   re-rotation with one step of staleness, and the deep layers (48-63), where the deviation is, get the least
   repair (21 %).
4. **The repair is monotone in k and cheap.** Reply KL, first-token KL and KV deviation fall steadily from
   k = 0 to 128 for 4-14 ms more prefill per call; the only job-level effect in the study, more looping
   without repair (shift: +25 points of runs ending at the budget), shrinks to +4 to +8 points with any k
   (point estimates). If one k is chosen, 128.
5. **Performance: prefill falls by 70 %, the job by 15 %.** EPIC computes 9-12 % of each prompt instead of
   55 %, so a lead call's time to first token drops from about 0.5 s to 70-90 ms on an H100; on identical
   trajectories the modeled job completion time is 0.85× full re-prefill, because decode is three quarters of
   these single-session jobs. The gain would be larger where prefill weighs more (longer contexts, shorter
   replies, many sessions sharing a GPU), which this study did not measure.
6. **Implication for a real deployment (not tested here):** since compounding, not the single step, is the
   error source in a sliding-window harness, an EPIC server should bound the age of reused rows, e.g. recompute
   rows that have crossed more than a few edits or fall back to full re-prefill every N edits; the one-step
   arms show that rows refreshed before each call would cut the deviation by about two thirds.

## 6. Caveats

- **Emulated EPIC.** The live server computes every token after the exact prefix and overrides the reused
  rows with their stored, re-rotated values, so outputs equal a real EPIC server's (selftests, §4.1) but its
  wall clock does not. Time comes from E-C's shape-equivalent requests; the reuse-family TTFT model is
  validated on held-out shape-equivalent calls (MdAPE 3.8 %), not against a real EPIC server, while the
  full-re-prefill family is validated against stock vLLM serving (3.4 %).
- **Arms are independent samples of a task.** Without batch-invariant kernels (2.6× slower decode), a block's
  arms part ways before any edit in a third of blocks (G2), so job-level comparisons carry trajectory noise
  as wide as the A/A interval; the KV and step metrics (thousands of paired calls) carry the precision.
- **Floor and scope of the job outcome.** No main run passed (strict rules on compaction-heavy MODIFY tasks);
  the endpoint is the partial-credit score, on 12 cells of 8 codebases with M1 / M2 / M3 only (M3 is one cell
  that always scores 0). EXPLAIN tasks enter only through E-A. One model (Qwen3-32B, non-thinking) and one
  compaction regime (R1); the LLM-summary regime (R2) was dropped after the pilot (§4.2).
- **The JCT model** prices one session at a time (E-C at batch 1-2, no queueing) and takes tool time from the
  recorded gaps; contention between sessions, where saved prefill also unblocks other users' decode, is not
  modeled.
- **X2** is one seed on 50 instances (it can see differences of about 15-20 points), with a 32K window and 150
  calls instead of JetBrains' 128K and 250; a patch whose tests time out counts as unresolved; three prefix
  runs were rerun after a client-timeout fix (cleanup note 18:35).
- **Four servers.** From 14:48 UTC the main sweep ran on two identically configured servers and X2 on up to
  three; every block and every instance-arm ran on one server, and the analyses pool them.
- **The append-only template** changes the model's next tokens about as much as a compaction does (§4.0); it is
  the same in every arm.
- **Not run from the plan.** R2's main runs (dropped after the pilot, §4.2). X1, the off-policy masking replay
  of Open-SWE / SWE-smith trajectories (X2 measures masking on-policy instead). The E-A controls `epicK_noreply`
  (with the append-only template the previous reply is part of the exact prefix, so there is no reply edit to
  remove) and `recompute_alt` (the `prefix` probes give the numerical floor). Separate streamed runs on stock vLLM
  to validate the time model (replaced by pricing E-0's stock-vLLM calls, §4.6). The failure taxonomy and a
  blinded review of discordant blocks (no block had a pass/fail discordance). Of the planned figures, the
  dose-response (F1) and the KV-deviation breakdowns by age, offset and layer (F3/F4) were made.

## 7. Reproduction

From the repository root, with `/data/fritz/venv/bin/python` (vLLM 0.31.0) as `python`; every GPU stage pins
one GPU with `EPIC_GPU` and stops its engine itself (`epic_common.served` / `run_isolated`).

```bash
# CPU tests (C1-C9, C12, C13; graders; C11 is in tests/)
python research/16_epic_harness/test_epic_harness.py && python research/16_epic_harness/test_epic_grade.py
python -m pytest tests/test_append_only_context.py tests/test_agent_teams_runtime.py -q

# E-0 and selftests (one GPU each)
research/16_epic_harness/epic_pipeline.sh e0
research/16_epic_harness/epic_pipeline.sh selftest

# manifests: the 60 cells, the pilot, then main from the measured cell profile (§4.3); the screen's manifest
# (the 48 cells the pilot did not run, prefix only) is kept as written in data/live/screen/
python research/16_epic_harness/epic_workloads.py cells --out research/16_epic_harness/data/cells.json
python research/16_epic_harness/epic_workloads.py build --phase pilot --out research/16_epic_harness/data/live/pilot/manifest_pilot.jsonl
python research/16_epic_harness/epic_analyze.py profile --live live/pilot --live live/screen   # -> data/cell_profile.json
python research/16_epic_harness/epic_workloads.py build --phase main --cells research/16_epic_harness/data/cells.json \
    --selection research/16_epic_harness/data/live/main/selection.json --repeats 4 --shift-reps 2 --aa-reps 2 \
    --out research/16_epic_harness/data/live/main/manifest_main.jsonl

# live runs (E-B): EPIC_BATCH_INVARIANT=0 EPIC_CUDAGRAPH=1; main also EPIC_SHADOW_EVERY=8
python research/16_epic_harness/epic_sweep.py --schedule research/16_epic_harness/data/live/pilot/manifest_pilot.jsonl --out live/pilot --lead-cap 150
python research/16_epic_harness/epic_sweep.py --schedule research/16_epic_harness/data/live/screen/manifest_screen.jsonl --out live/screen --lead-cap 150 --out-cap 32000
EPIC_GPU=0 python research/16_epic_harness/epic_sweep.py --schedule research/16_epic_harness/data/live/main/schedule_gpu0.jsonl --out live/main --lead-cap 150 --out-cap 32000
EPIC_GPU=1 python research/16_epic_harness/epic_sweep.py --schedule research/16_epic_harness/data/live/main/schedule_gpu1.jsonl --out live/main --tag g1 --lane-offset 2 --lead-cap 150 --out-cap 32000

# E-A replays (one process per shard and GPU)
python research/16_epic_harness/epic_replay.py run --live live/screen --runs 12 --min-calls 40 --max-calls 80
python research/16_epic_harness/epic_replay.py run --live live/main --runs 10 --min-calls 40 --max-calls 80 --shard 0/2 --out-name replay     # and 1/2 -> replay_b
python research/16_epic_harness/epic_replay.py run --live live/pilot --runs 8 --min-calls 40 --max-calls 80 --shard 0/4 --out-name replay    # 1/4 2/4 -> replay_b/_c; 3/8 7/8 -> replay_d/_e

# E-C
python research/16_epic_harness/epic_latency.py run --live live/main --calls 600

# X2 (one process per GPU; EPIC_CUDAGRAPH=1 EPIC_SHADOW_EVERY=8), then catch-up grading
EPIC_GPU=3 python research/16_epic_harness/epic_swe.py run --lanes 2 --tag g3
python research/16_epic_harness/epic_swe.py grade

# analysis (CPU)
python research/16_epic_harness/epic_analyze.py runs --live live/main && python research/16_epic_harness/epic_analyze.py main --live live/main
python research/16_epic_harness/epic_analyze.py l1 --live live/main
python research/16_epic_harness/epic_replay.py report --src live/main:replay --src live/main:replay_b --src live/screen \
    --src live/pilot:replay --src live/pilot:replay_b --src live/pilot:replay_c --src live/pilot:replay_d --src live/pilot:replay_e
python research/16_epic_harness/epic_latency.py report --live live/main && python research/16_epic_harness/epic_latency.py validate --live live/main
python research/16_epic_harness/epic_latency.py jct --live live/main --replay live/main --replay live/main:replay_b --replay live/screen \
    --replay live/pilot --replay live/pilot:replay_b --replay live/pilot:replay_c --replay live/pilot:replay_d \
    --replay live/pilot:replay_e
python research/16_epic_harness/epic_swe.py report
python research/16_epic_harness/epic_figures.py l1 --live live/main
python research/16_epic_harness/epic_figures.py dose --live live/main --ea live/main:replay --ea live/main:replay_b --ea live/screen \
    --ea live/pilot:replay --ea live/pilot:replay_b --ea live/pilot:replay_c --ea live/pilot:replay_d --ea live/pilot:replay_e \
    --jct research/16_epic_harness/data/live/main/latency/jct_replay.jsonl
```

## GPU time log (2026-10-09 UTC; H100 80GB; GPUs 0-3 claimed in the lab GPU sheet as Yijiahao, Fri 10/9 US Eastern)

| GPU | from | to | stages |
|---|---|---|---|
| 0 | 01:11 | 14:48 | E-0 (01:11-02:27); smoke and batch-invariance runs (02:31-03:45, selftests between stages); pilot (03:47-08:44); E-C and E-A tests; screen (08:54-10:54); main sweep from 11:26 |
| 0 | 14:48 | 22:53 | main sweep part A (to 20:59); E-A main shard 0/2 (21:00-22:41); E-A pilot 7/8 (22:42-22:53, row pool exhausted) |
| 1 | 14:48 | 23:18 | main sweep part B (to 20:46); X2 worker (20:48-21:11); E-A main shard 1/2 (21:11-23:18) |
| 2 | 14:48 | 22:47 | E-A screen (to 18:25); E-C (18:35-20:35); X2 worker (20:35-21:23); E-A pilot 0/4 and 1/4 (21:24-22:47) |
| 3 | 14:48 | 23:17 | X2 (lane 0 looping 15:35-18:30, cleanup note 18:35); X2 (18:35-21:29; engine stopped, worker grading until 22:14); E-A pilot 2/4 (22:15-22:41), 3/8 (22:41-23:03), 7/8 rerun (23:03-23:17) |

About 47 GPU-hours held in all (GPU 0 21.7 h, GPUs 1-3 8.0-8.5 h each, including GPU 3's 45 idle minutes), against
the plan's 70-75. Every engine stopped at the end of its own stage (`epic_common.served` / `run_isolated` wait
until the GPU has no compute process). **Final state, 23:18 UTC: `nvidia-smi` shows GPUs 0-3 at 0 MiB with no
compute process, and no vLLM or stage process of this study is running.** X2's grading (CPU and Docker only)
finished at 23:47 UTC; each instance's image was removed after its grading (only images this study pulled,
listed in `data/x2_swe/pulled_images.txt`), and at 23:48 none of the 50 remained and no container of this study
existed. Rechecked at 23:48: all GPUs at 0 MiB with no compute process.

## Data inventory

Under `research/16_epic_harness/data/`. Git tracks the `*.md` tables, the `*.svg` figures and the frozen inputs
the scripts read (`cells.json`, `cell_profile.json`, `e2_gold/`, the manifests, `live/main/selection.json` and
`schedule_gpu*.jsonl`; `.gitignore`); everything else is local until pushed to the Drive mirror
(`python3 research/common/drive_data.py push 16` on a machine with the `gdrive:` remote; this one has no rclone).
Run directories hold each run's captured requests `*.requests.jsonl.xz`, harness trace, diff or answer, metadata
and `.done` marker.

| path | contents |
|---|---|
| `cells.json`, `cell_profile.json`, `e2_gold/` | the 60 cells with seed hashes; per-cell outcome profile from pilot + screen; E2 gold call paths |
| `e0_reuse/` | E-0: 54 sessions on stock vLLM (`runs/`), `per_call.jsonl` (reuse and miss cause per call), `kl.jsonl` (template shift), `e0_tables.md` |
| `selftest/` | `selftest.json` (T/R/G checks, §4.1) |
| `live/smoke`, `live/smoke_bi`, `live/pilot_bi` | smoke runs and the batch-invariant runs (cleanup note 03:45) |
| `live/pilot/` | 64 pilot runs, `graded.jsonl`, `runs_metrics.jsonl`, `pilot_gates.md`, `audit_*.md`, `server_calls.jsonl`, `superseded_overshoot/`, `harness_reactive_loop/`, `replay_test/`, `latency_test/`, E-A shards `replay*/` |
| `live/screen/` | 48 screen runs, grading, `audit_*.md`, E-A `replay/` (12 runs) |
| `live/main/` | 240 main runs; `manifest_main.jsonl`, `selection.json`, `schedule_gpu0/1.jsonl`; server logs `server_calls.jsonl` (GPU 0) and `server_calls.g1.jsonl` (GPU 1) with every lead call's plan, accounting and shadow probe; `superseded_split_calls.jsonl`; `graded.jsonl`, `runs_metrics.jsonl`, `main_tables.md`, `l1_tables.md`; `figures/`; E-A `replay/`, `replay_b/`; E-C `latency/` (`latency_calls.jsonl`, `models.json`, `tpot.json`, `latency_tables.md`, `validation.md`, `jct_*.jsonl`, `jct_tables.md`) |
| `x2_swe/` | X2: `runs/<instance>-<arm>/` (SWE-agent trajectory, `preds.json`, `done.json`, harness `report.json` and logs), `server_calls.g*.jsonl`, `serve.g*.log`, `run_g*.log`, `claims/`, `superseded/` and `superseded_timeout_calls.jsonl` (cleanup note 18:35), `pulled_images.txt`, `x2_tables.md` |

Outside the repository: lanes (rsync copies of the repository the sessions ran in) under `/data/fritz/epic/lanes/`,
seed trees under `/data/fritz/epic/trees/`, the SWE-agent fork and its venv under `/data/fritz/epic/x2/`.

## Source map

| question | where |
|---|---|
| what changed in s15 to make it append-only | `s15_integrated_harness/code.py` (`assemble_system_prompt`; `append_context_updates`, which appends `<memory-recall>` / `<memory-catalog-update>` blocks), `qwen3_append_only.jinja`, `tests/test_append_only_context.py` |
| how a call's plan is made (exact prefix, reused runs, EPIC heads, ages) | `epic_plan.py` (`block_plan`, `run_heads`, `Entry`) |
| how reused rows reach the model | `epic_connector.py` (loads, `prehook` overrides, saves, APC reserve) |
| seeds, cache salts, shadow probes, per-call log | `epic_server_ext.py` |
| one live session (tagging, budget, overflow shim) | `epic_session.py`; sweeps `epic_sweep.py` |
| graders and audits | `epic_grade.py`, `data/live/*/audit_results.md` |
| E-A compounded / one-step replay | `epic_replay.py` (`Replayer.replay_run`) |
| time: TTFT/TPOT models, JCT, validation | `epic_latency.py` |
| X2 | `epic_swe.py` |
| tables, statistics | `epic_analyze.py` (`cmd_main`, `secondary_tables`, `l1_tables`) |

## Cleanup notes (running log of corrections and incidents)

- **2026-10-09 02:33–02:34, a second GPU was used for 46 s.** A queued diagnostic waited on the wrong
  process id (`setsid` had forked), started while the live smoke still held GPU 0, and `pick_gpu` fell
  back to the idle GPU 1. The study's constraint is one H100, so `epic_common.pick_gpu` now returns
  only the pinned GPU (`EPIC_GPU`, default 0) and waits while it is busy, and stage queues are single
  sequential scripts. Nothing from that run is used except the diagnosis below, which was repeated on
  GPU 0 by the selftest.
- **Selftest T1 redefined.** A load of saved rows followed by a 17-token tail differs from a one-shot
  full prefill at bf16 level (greedy divergence at token 15), and so does vLLM's own prefix cache over
  the same split. The connector's load is **bit-identical to the prefix-cache path** (max logprob
  difference 0.0, identical 32 tokens), which is the correct reference for a load.
- **Failed loads now fail visibly.** With `kv_load_failure_policy: recompute`, vLLM 0.31 served the
  blocks of a load the connector reported as failed (garbage output) instead of recomputing them. The
  policy is now `fail` (selftest R11 checks the request errors); the planner never emits a load of
  unknown slots, so the path is a safety net only.
- **Gate E0 corrected** (§4.0): the pre-registered "token-weighted reuse ≥ 95 %" exceeds its own ceiling
  (92.0 %); the gate is exact extensions ≥ 98 % and ≥ 98 % of the reusable prefix realized.
- **Grader citation formats widened** (`epic_grade.extract_citations`): the model cites as
  "`specifiers.py` (Line 123)", "core.py, starting from line 1", "line 45 of _ranges.py"; topic 10's
  `file.py:N` pattern counted those answers as citing nothing.
- **Engine crash in the pilot (03:03), fixed.** The counterfactual shadow request loaded the prompt's rows
  rounded down to the 16-token block; when the prompt length was a multiple of 16 the load covered the
  whole prompt and vLLM's scheduler asserted (`num_new_tokens > 0`), killing the engine. The middleware now
  rounds `P − 1`, and the connector caps every load below the request's last token (selftest T5). The two
  sessions cut short by the crash (their final answer is the 500 error) are rerun: `epic_sweep` treats
  runs ending in engine or connection errors, or terminated by a stopped sweep, as unfinished.
- **Shadow trigger.** Shadows are taken on calls whose KV holds any row that has survived an edit (age >
  0), which includes the append-only calls after an edit that inherit stale rows through the exact prefix;
  the first version triggered on any overridden row, which also sampled zero-staleness calls (KL 0.0).
- **Stopping a sweep.** A signal now stops running sessions and keeps workers from starting new runs.
- **Batch invariance demonstrated, then not used (2026-10-09 03:45).** `VLLM_BATCH_INVARIANT=1` gives exact
  common random numbers (smoke: `prefix_aa` identical to `prefix` over all 41 calls; every EPIC arm
  identical to `prefix` until its first reuse after an edit), but on this H100 it slowed decode about
  2.6× (83-90 ms vs ~33 ms per output token with 2 sessions), against the plan's ≤ 25 % allowance.
  The live runs therefore use standard kernels, with decode-only steps replayed from full CUDA graphs
  (`--compilation-config {"mode": 0, "cudagraph_mode": "FULL_DECODE_ONLY"}`: prefill stays eager, so the
  override hooks fire; all selftests pass in this mode). Arms of a block are then independent samples
  of the same task (seeds still shared); `prefix_aa` measures the run-to-run floor; the step-level causal
  effect of the KV comes from the shadow probes (same prompt, same seed, fresh vs as-used KV), whose own
  noise floor is measured by shadows on the `prefix` arms (exact KV). The 4 batch-invariant pilot runs
  are kept in `data/live/pilot_bi/`.
- **EPIC-arm session resets fixed (2026-10-09 05:03; pilot restarted).** In R1 every call past the snip
  threshold rewrites the archive marker (`[3 messages archived at …/transcript_00012.jsonl]`), and an
  older sequence of the same arm-session often shares 16-32 more tokens of it than the previous call does.
  vLLM's prefix cache then served past the planned exact prefix (79 of 801 EPIC-arm calls in the first
  pilot hour). The middleware reset the session on such an "overshoot", and the call after a reset recorded
  store slots for the cache-served positions that were never written. Consequences: (a) the shadow L1
  deviation of every later call compared those unwritten rows (relative ΔK 0.33-0.40 instead of
  0.02-0.03), and (b) the arm fully recomputed one call in every few, diluting the treatment. The model's
  own inputs were not corrupted: unwritten slots only covered the always-cached head, and teacher-forced KL
  stayed at 1e-4. Fix: every EPIC plan now carries a 256-row `apc_save` reserve from p0, and the connector
  copies whatever the cache served beyond p0 into it at the first step (selftest T6: bit-identical). The
  entry takes those rows as computed at their own positions (`epic_plan.absorb_apc`; the call record
  carries `apc_over`, `apc_over_S` and `apc_over_C` for the token accounting). A larger overshoot resets
  the session under a new cache salt, so no row is ever unwritten (tests C13). Prefix arms were
  unaffected. The three finished pilot runs that had resets (`rich-E1` epic128, `s15_harness-E2` shift
  and epic128) are kept in `data/live/pilot/superseded_overshoot/` with their 435 server records, and
  rerun.
- **A reactive-compaction loop in s15 (pilot, 07:05-07:33; a harness finding, not an EPIC effect).** In
  `EHP-R2-rich-M1-r1-prefix` the model read `rich/_emoji_codes.py` (122,104 characters). The next prompt
  (206,632 tokens) overflowed, and `reactive_compact` kept the last five messages, which still held that
  result, so every retry overflowed again: 142 summary calls and 142 overflowing lead calls on 6 completed
  lead calls. The lead-call budget counts the overflowing calls and would have ended the run at 150; it
  was stopped at 148 (`terminated`) and is kept in `data/live/pilot/harness_reactive_loop/`. The same
  mechanism shows why R2 gives EPIC little to restore in s15: what the reactive compaction keeps is either
  small (212-963 tokens reused in six of the pilot's seven epic128 compaction events, 7,467 in one) or too
  large to fit (7 of 16 R2 runs looped; the last three were stopped by a watcher once they passed 5
  compactions, which also stopped one progressing run, `rich-M2` epic128 at 83 lead calls).
- **Grader calibration (G7, 2026-10-09 08:50), before any main run.** A blind audit of 15 pilot runs agreed
  with the graders on pass/fail 15/15 (`data/live/pilot/audit_results.md`), but the scores were strict
  products: an inventory with every module and no line numbers scored 0, and so did a run that tagged 9 of
  18 files with wrong changelog lines. 39 of 48 R1 pilot runs scored exactly 0. E1, E2 and M1 now score
  half the content plus half the grounded content (`epic_grade.partial`; the product is kept as
  `details.score_strict`); E3, M2 and M3 are unchanged (their content is defined by the grounding, or a
  collateral change zeroes them); pass rules are unchanged. The change uses no arm comparison. It still
  leaves 36 of 48 R1 pilot runs at 0: most runs that compact loop to the call cap in every arm.
- **E-A slot bookkeeping fixed (2026-10-09 11:40), before the full replay.** `epic_replay.replay_run`
  released a call's new slots before the arm's entry retained them, so they went back to the free list while
  the entry still named them, and the next arm's allocation overwrote them. With arms replayed in the order
  shift, epic32, epic64, epic128, the compounded entries of shift and epic64 were overwritten by epic32's
  and epic128's rows from the third call on (stale-row deviation 0.6-0.7 instead of 0.03-0.3). The first E-A
  run (`data/live/pilot/replay_test/`, 40 calls) is therefore valid only for epic32, epic128 and the one-step
  arms; its shift and epic64 rows are not used. The entry now retains first, and `SlotPool.assert_held`
  checks every entry after each call. The live middleware always retained first (tests C8, C13; its shadow
  deviations show no such outliers).
- **Second grader audit and an EXPLAIN-grader fix (2026-10-09 14:45).** 15 screen runs
  (`data/live/screen/audit_results.md`) agreed with the graders on 13: two E1 answers cited every module in a
  layout the citation extractor missed ("- `api.py`: description. (Line 1)", and a block whose "(line 10)"
  sits on the line after the module's file name), so they scored as citing nothing. `epic_grade.extract_citations`
  now also takes a parenthesized line reference on a line naming exactly one path, and a line reference on
  the following lines of that path's block (`test_citation_block_formats`). Re-grading changed 4 of 48 screen
  runs (3 E1 runs now pass, one E3 run 0.0 -> 0.33, still a fail) and none of the 64 pilot runs; G7 is 30/30
  over both batches. Only EXPLAIN templates use citation extraction; the main sweep's MODIFY graders (13/13 in
  the audits) are unchanged.
- **Four GPUs from 2026-10-09 14:48 UTC (user's allowance; each claimed in the lab's GPU sheet as Yijiahao,
  Fri 10/9 US Eastern).** The main sweep, which had run on GPU 0 since 11:26 (75 of 240 runs done), was
  stopped at a run boundary of neither lane: the two sessions in flight (`requests-M1-r4-epic64`,
  `s15_harness-M2-update_task-r1-epic32`) were terminated and rerun from scratch, and their 108 call records
  moved to `data/live/main/superseded_split_calls.jsonl` (the rerun reuses the spec `label~a1`; a gzip copy of
  the whole log before the move is `server_calls.jsonl.pre_split.gz`). The remaining blocks were split, whole
  blocks only, into `schedule_gpu0.jsonl` (part A: the two started blocks plus the next 8 untouched blocks of
  each lane, 87 runs, lane directories `main_lane0/1`, logs `serve.log` / `server_calls.jsonl` /
  `sweep.log`) and `schedule_gpu1.jsonl` (part B: the last 8 untouched blocks of each lane, 80 runs, GPU 1,
  `--lane-offset 2` -> `main_lane2/3`, `--tag g1` -> `serve.g1.log` / `server_calls.g1.jsonl` /
  `sweep.g1.log`). Every arm of a block still runs in one lane, so the absolute paths in its prompts agree.
  Both servers have identical flags, model and code; the analysis reads all `server_calls*.jsonl`
  (`epic_common.server_logs`). E-A on the screen and pilot runs moved to GPU 2, X2 started on GPU 3 (instances
  shared through claim directories, so GPUs that finish their own stage join it), and E-A on main plus E-C
  follow on GPU 0 once both parts are done.
- **Stage plan revised at 15:00 UTC**, after the first X2 runs took under a minute each: X2 runs on GPU 3
  only, then E-A on the pilot runs; E-C moves to GPU 2 after its screen replay; E-A on main is split over
  GPUs 0 and 1 (`epic_replay.py run --shard 0/2` -> `replay/`, `--shard 1/2` -> `replay_b/`, the same ten
  picked runs; the report merges both). Running stages were not touched; only their waiting wrapper
  scripts were replaced.

- **X2 client timeout raised from 600 s to 1800 s (2026-10-09 18:35 UTC); three prefix runs rerun.** SWE-agent's
  litellm client waited 600 s for a reply and then resent the call. An 8K-token reply (the per-call output cap)
  with both lanes decoding and KV-pool preemption can take longer, and the server derives the seed from the
  session and call index, so the resend repeated it: `django__django-12050-prefix` resent its call 21
  seventeen times (15:35-18:30, lane 0 of GPU 3 idle for three hours). Two other prefix runs had a resent
  call that then finished in 8-10 s from the KV the aborted attempt had saved (`12209`, call 21, once;
  `12774`, call 89, twice), so a degenerate reply was silently resampled instead of being returned at the
  cap, as it was for every other run (e.g. `11885-epic128`, call 35: 8,192 tokens in 488 s). No other X2
  call and no s15 call (the main sweep has 0 aborted calls) was affected. All three runs are rerun under
  the new timeout: the worker on GPU 3 was stopped, its two in-flight runs (12050, 12774) cut, the finished
  `12209-prefix` moved to `data/x2_swe/superseded/`, and the three runs' 199 call records moved to
  `data/x2_swe/superseded_timeout_calls.jsonl` (gzip copy of the log before the move:
  `server_calls.g3.jsonl.pre_timeout_fix.gz`). 12209 is regraded when its prefix run finishes.
- **Stage plan revised again at 18:35 UTC.** E-C runs on GPU 2 now instead of after the main sweep: 197 of
  240 main runs had finished, and the sample of 600 EPIC lead calls is drawn from the call logs at start
  (`calls_from_live`), which already hold far more calls than the sample. Every GPU whose own stages are
  done then takes from a shared pool: an X2 worker while at least 3 X2 runs are unclaimed, then E-A on the
  pilot's 8 picked runs in four shards (`--shard i/4` -> `live/pilot/replay`, `replay_b`, `replay_c`,
  `replay_d`, merged in the report). Main part B finished at 20:46, 20 minutes before part A, whose last block
  still had a prefix run (E-A on main draws its runs from all finished prefix runs, so it waits for it):
  GPU 1 ran an X2 worker until part A was done, stopping it after its in-flight runs. E-A runs of main's
  long trajectories took 24-49 minutes each, so the pilot's last shard (3/4: runs 3 and 7 of the 8 picked) was
  split in two before any GPU took it (`--shard 3/8` -> `replay_d`, `--shard 7/8` -> `replay_e`, the same runs),
  for the first two GPUs to finish.
- **X2 grading (2026-10-09 21:20-22:15 UTC).** The official harness stops a patch's tests after 1,800 s and then
  writes no `report.json`, only an error in its summary; the first version of `epic_swe.py report` left such
  runs out of the denominator. A test timeout or evaluation error now counts as unresolved, as SWE-bench scores
  it (`graded_outcome`; fixed before any X2 number was reported), and catch-up grading skips runs the harness
  has already evaluated instead of rerunning a 30-minute timeout. Each worker grades in one thread after its
  runs, so a few timeouts kept the GPU 3 worker alive with its engine already stopped: GPU 3 sat idle from 21:29
  until 22:14, when the worker was stopped (its in-flight evaluation finished on its own) and GPU 3 went on to
  the pilot's E-A shards; the remaining backlog was graded by `epic_swe.py grade` (CPU and Docker only), with
  a second grader taking the five instances queued behind `sphinx-8269` (whose patches hit the timeout) in
  reverse order, so the two never evaluated the same run.
- **E-A pilot run rerun with a larger row pool (2026-10-09 22:53 UTC).** `EHP-R2-rich-M2-pick_unit_and_suffix-r1-prefix`
  (shard 7/8, an R2 run with 20K-token prompts) exhausted the replay's 160,000-row host pool: seven session
  entries (six arms and the fresh reference) of that size do not fit. Nothing was written for it. It was rerun on
  GPU 3 with `--pool-rows 256000` (64 GB of pinned host RAM; capacity changes no result). The queue scripts'
  "exit" lines for E-A stages were unreliable (`$(date)` ran before `$?` was read), so every stage's outcome was
  checked in its own log instead: no other replay or latency stage failed.
- **Repository test suite after the s15 change (2026-10-09 23:30 UTC).** `tests/` passes except two tests:
  `test_web_scenarios.py::test_generated_s16_metadata_extends_s15_without_registry_false_positives` checks that
  `web/src/data/generated/versions.json` embeds the current `s15_integrated_harness/code.py`, which the
  append-only change (§2) edited; `cd web && npm run extract` regenerates it (not run here: it needs the web
  app's npm packages, which this machine does not have). `test_s08_context_compact.py::test_prepare_persists_oversized_unseen_result_before_full_compact`
  tests chapter 8's harness, which this study did not touch.
