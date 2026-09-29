# 10 · RoPE-shifted KV reuse across harness compaction: does it hurt the next round?

> **Status:** complete (2026-09-25).
> - Part A: 500 live runs, 683 compaction events, each run under six arms on three models (19,560
>   scored records); answer in §5.
> - Part B: can selective recompute (CacheBlend, EPIC) repair Part A's two costs? The same events
>   under seven arms on the three models (29,106 scored records); answer in Part B's short answer
>   and §B4.
> - Part B latency (2026-09-27): the time to first token each repair costs, on 200 events per model
>   (§B3.6).
>
> **Dates:** 2026-09-25. Part A: selftests and live smoke 11:15–12:05 EDT on alphagpu20 (Slurm 100813);
> sweep 11:40–14:07 EDT in six 2-GPU shards (Slurm 100831–100836: alphagpu17, 22, 12, 13, 13, 15; five
> H100 pairs at TP=2, one H200 pair with a replica per GPU). Part B: 14:59–17:57 EDT in 2-GPU jobs on
> alphagpu13 (H100), 19 and 20 (H200). Slurm 101267–101270 were the shards, 101273–101275 the
> follow-ups after the memory fixes, and 101366 and 101414 the last events (§B5). Part B latency:
> 2026-09-27 18:04–18:39 EDT on alphagpu24 (2×H200, Slurm 104293).
>
> **Models / providers:** `Qwen/Qwen3-32B` (bf16, GQA) served by vLLM 0.29.0 for the 500 live runs and
> every arm; `Qwen/Qwen3-8B` (GQA) and `zai-org/GLM-4.7-Flash` (MLA) replay the same events. H200 and H100
> GPUs on the `alpha` partition.
>
> **Commits:** — (uncommitted).
>
> **Presented:** —

**Contents**

- Part A — the experiment: Short answer · §1 Question · §2 What the harness writes at the budget · §3 Method
  (§3.1 live runs, §3.2 events and tails, §3.3 the arms and the splice connector, §3.4 metrics, §3.5
  statistics, §3.6 verification) · §4 Results (§4.1 runs and events, §4.2 what the cure saves, §4.3 does the
  next round change, §4.4 is it worse, §4.5 does stale KV leak evicted content) · §5 Conclusions · §6 Caveats
  · §7 Reproduction
- Part B — selective recompute: Short answer · §B1 Question · §B2 Method (§B2.1 arms, §B2.2 mechanism,
  §B2.3 what stays from Part A, §B2.4 verification) · §B3 Results (§B3.1 mechanism check, §B3.2 the two
  costs, §B3.3 next-round fidelity, §B3.4 what the selections pick, §B3.5 by edit kind, §B3.6 what the repairs cost in time) · §B4
  Conclusions · §B5 Caveats · §B6 Reproduction
- Data inventory · Source map · Cleanup notes

**Files in this folder** (`research/10_rope_shift/`)

| path | purpose |
|---|---|
| `README.md` | this document |
| `workloads.py` | builds the 500-run manifest: 10 real codebases × 6 templates (EXPLAIN E1–E3, MODIFY M1–M3); `--check` validates the prompts; offline |
| `live_run.py` | one harness session through `research/common/profile_run.py` with full request capture (patches the Anthropic SDK before the harness loads), sandbox reset, xz compression, MODIFY diff; starts a harness session |
| `run_sweep.py` | dispatches the manifest over lanes (repo copies with their own `.env`) and local vLLM replicas; resumable per label; starts harness sessions |
| `render.py` | renders a captured Messages request into the exact prompt token ids vLLM's `/v1/messages` builds; `check` compares with the server's `usage`; offline (CPU, no weights) |
| `events.py` | finds compaction events (anchored harness markers), samples ≤2 per run, builds the probe and retention tails with gold answers; offline |
| `splice_connector.py` | the vLLM KV connector: loads a spliced, re-rotated prefix, saves computed KV and (Part B) overrides chosen positions' K/V layer by layer through attention pre-hooks, driven by a per-request JSON plan; imported by vLLM |
| `arms.py` | the arms (recompute, recompute_alt, prefix, shift, norope, oracle) on offline vLLM, teacher-forced passes, and the connector selftests T1–T5; GPU |
| `score.py` | quality metrics per record (tool-call AST match, invocation F1, validity, edit application, citations, gold probes, retention); offline |
| `analyze.py` | the tables in `data/rope_shift_live/rope_shift_tables.md`, and with `--repair` Part B's in `data/rope_shift_repair/rope_shift_repair_tables.md`; offline |
| `repair.py` | Part B: the selective-recompute arms (shift, EPIC, CacheBlend, random control) on the same events through the connector's per-layer override, teacher-forced passes, and the override selftests R1–R5; GPU |
| `test_rope_shift.py` | CPU tests (pytest-compatible, or run the file directly: the project venv has no pytest) |
| `rope_shift_pipeline.sh` | one shard: serve → runs → stop → events → selftest → arms (Qwen3-32B, Qwen3-8B, GLM-4.7-Flash) |
| `rope_shift.sbatch` | Slurm wrapper of one shard (2 GPUs, H100 or H200) |
| `repair_pipeline.sh`, `repair.sbatch` | Part B: one shard (selftest → repair arms on the three models) and its Slurm wrapper |
| `latency.py` | Part B latency: the time to first token of every arm as a real implementation would run it (shape-equivalent requests through the connector; CacheBlend's selection pass timed with CUDA events), and its report; GPU for `run`, offline for `report` |
| `latency.sbatch` | the latency job (2×H200, TP=1 per model) |
| `data/rope_shift_live/` | the manifest, the 500 runs, events, arms outputs, scores, tables, selftests, pipeline logs |
| `data/rope_shift_repair/` | Part B: repair-arm outputs, scores, tables, selftests, pipeline logs |
| `data/pilot_hf_synthetic/` | the superseded pilot of 2026-09-24 (see Data inventory) |

Shared, outside this folder: `research/common/profile_run.py` (the session driver; `--context-limit`,
`--no-timestamp`, `--write-root`, `--prompt-file`, `--answer-out`), `s15_integrated_harness/code.py`
(the harness under test, unmodified), `research/08_context_inheritance/codebase_workloads.py` (the
EXPLAIN/MODIFY item texts, read with `ast`), `research/04_kv_splice/data/kv_splice/*.requests.jsonl`
(real 50k-limit harness requests used as test fixtures).

---

# Part A — Does reusing the KV cache across a compaction edit, with the RoPE fixed, hurt the next round?

**Short answer.** Measured on 683 real compaction events from 500 live harness runs (Qwen3-32B, 50k
limit), replayed on Qwen3-32B, Qwen3-8B and GLM-4.7-Flash:

- **The next round changes.** Keeping the surviving KV and only re-rotating its RoPE changes about 30%
  of next rounds, far above numerical noise (4–6%).
- **It changes no more than the compaction itself does.** Undoing the compaction changes as many or
  more next rounds, with higher KL, on every model.
- **The rounds stay valid.** Tool calls are as well-formed and as grounded as recompute's.
- **It is cheaper.** It skips about 80% of the prefill that prefix caching spends after the edit.
- **Two costs.**
  - Long verbatim copies from the shifted region get worse: −8.9 points on SEARCH/REPLACE edits for
    Qwen3-32B, −6.3 for GLM.
  - Qwen3-8B stops admitting that evicted content is gone.
- **The rotation itself is essential.** Without it, 71–76% of GQA rounds change and probes drop 22–54
  points. MLA tolerates the missing rotation far better, but not safely.

## 1. Question

When the harness's history reaches its budget, `prepare_context` rewrites earlier content in place.
Every token after the first edit then sits at a different position, so a server with prefix caching
re-prefills everything after that point, even though most of those tokens are unchanged. A cheaper
policy is to keep the KV of every chunk that survives the edit, re-rotate the positional part of its
keys to the new positions ("fix the RoPE"), and prefill only the new text: the placeholder sentences and
the new turn. The question is whether that changes the round that follows: which tool the agent calls
with which arguments, whether an explanation or an edit is still right.

Only the RoPE cure is tested. Selective-recompute and correction schemes (CacheBlend, EPIC/LegoLink,
AgentKVShift, PatchKV) are left to follow-up experiments.

Two scope notes:

- **Why not Qwen3.8-27B, the harness's usual model.** It is a hybrid: 48 of its 64 layers are
  Gated-DeltaNet recurrent layers with no RoPE and no per-token KV. Their state after an edit depends
  on everything before it and cannot be patched, so "shift without re-prefill" does not exist for them
  (llama.cpp disables context shift for this model family for the same reason). The experiment uses
  dense models.
- **Why GLM-4.7-Flash for MLA.** The DeepSeek-R1 8B/32B checkpoints are distills into GQA models
  (`LlamaForCausalLM` / `Qwen2ForCausalLM`), not MLA; DeepSeek's own MLA models are 16B-A2.4B (V2-Lite)
  or 671B. GLM-4.7-Flash (31B, 3B active) is an agentic coding model whose attention is
  `DeepseekV2MLAAttention`: each cache entry is a 512-dim latent plus a 64-dim decoupled RoPE key, so
  the RoPE fix rotates only 64 of 576 dims.

## 2. What the harness writes at the budget

`prepare_context` (`s15_integrated_harness/code.py:3492`) runs before every lead call, in this order,
with `CONTEXT_LIMIT` set to 50,000 characters for this experiment (`profile_run.py --context-limit`;
the committed default is 512,000). The sentences are the harness's own:

| step | when | what replaces the content |
|---|---|---|
| `tool_result_budget` (2378) | the newest tool results exceed 200,000 chars | `<persisted-output>\nFull output: <path>\nPreview:\n<first 2000 chars>\n</persisted-output>` (2368) |
| `snip_compact` (2413) | more than 50 messages | messages 4..n−46 become one user message `[<n> messages archived at <path>]` (2434); fires every round once past 50 messages |
| `micro_compact` (2438) | over the limit | older consumed tool results (all but the newest 3) become `[Earlier tool result saved at <path>]` (2452), down to 40,000 chars |
| `fit_tool_results` (2456) | still over the limit | largest results become the persisted-output preview (1,000 chars) |
| `compact_history` (2499) | still over the limit | the whole history becomes `[Compacted]\n\nAuthoritative request:\n…` with a model-written summary (2505) |

## 3. Method

### 3.1 Live runs (500, real harness, real code)

- **Workloads** (`workloads.py`, manifest `data/rope_shift_live/manifest.jsonl`): 10 real codebases,
  namely requests 2.34.2, click 8.5.0, jinja2 3.1.6, rich 15.0.0, httpx 0.28.1, PyYAML 6.0.3, packaging
  26.3, tqdm 4.70.1, typer 0.27.2 (copied from site-packages) and this repository's harness. Six
  templates: EXPLAIN (code explanation, read-only) E1 inventory, E2 trace one call, E3 orientation
  note, adapted from 08's EXPLAIN items; MODIFY (fake modification in a throwaway sandbox) M1 BUILD_TAG
  plus changelog (08's MODIFY items 1 and 4), M2 rename a function everywhere (auto-picked, 3–10
  references in ≥2 files), M3 docstrings for one module. 60 cells × 8–9 repeats = 500 runs; every
  prompt is solo ("Work alone: do not delegate …") and carries a run nonce.
- **Driver**: `run_sweep.py` → `live_run.py` → `research/common/profile_run.py` with `--context-limit
  50000 --no-timestamp --no-reads-log --client-max-retries 0 --server-info --max-seconds 900
  --quiet-seconds 20` (MODIFY adds `--write-root profiling_sandbox/<codebase>`). Each run happens in a
  lane (an rsync copy of the repository on node-local disk whose `.env` points at a local vLLM).
- **Deadline.** `profile_run.py --max-seconds` bounds only the wait for teammates, not the lead's own
  loop, and at the 50k limit a lead can loop for a long time: after `snip_compact` archives the
  evidence, it re-reads files it already read, or files that do not exist. One smoke run made 2,035
  lead calls in 32 minutes. `live_run.py` therefore raises `RunDeadline` on the first lead call after
  900 s; the harness treats it as a model error and ends the turn, and the run finalizes normally
  (status `deadline`).
  - The fix reached the lanes about 15 minutes into the sweep. First-wave runs that were still
    looping were killed by the dispatcher's 30-minute backstop.
  - Their captures, flushed per call, were finalized by `run_sweep.py --salvage` (status `killed`).
    The events they contain are as valid as any other; only their trajectories are longer.
- **Server**: `vllm serve Qwen/Qwen3-32B --tool-call-parser hermes --enable-auto-tool-choice
  --reasoning-parser qwen3 --default-chat-template-kwargs '{"enable_thinking": false}'
  --enable-prefix-caching --enable-prompt-tokens-details --max-model-len 40960`; TP=2 on an H100 pair,
  one replica per H200. The harness sets no sampling parameters, so the model's defaults apply.
- **Capture**: `live_run.py` patches `anthropic…Messages.create` before the harness loads and writes
  every call, serialized at call time, to `<label>.requests.jsonl.xz` (04_kv_splice's schema plus
  `extra_body` and the trace context). `render.py check` reproduces the server's prompt token count
  exactly (40/40 calls of the smoke run).

### 3.2 Events and tails

`events.py`: an event is a pair of consecutive lead calls (k−1, k) where call k's request is not
call k−1's request plus its reply with new messages appended. Edit kinds come from the harness's own
anchored matchers (the marker strings also occur inside files the agent reads). At most two events
per run are sampled (one with a placeholder edit and one with a snip edit when both exist); after a
summary nothing survives to be reused, so summary events are counted but not sampled.

Each sampled event gets three tails:

- **natural**: the harness's real call-k request (what the round after the compaction actually was).
- **probe**: one question appended after the tool results, about a `read_file` result of a `.py`
  file that survives the edit and sits after the first edited message, i.e. content whose KV the RoPE
  cure reuses at shifted positions. Families rotate: *explain* (purpose + every top-level function
  and class), *modify* (one SEARCH/REPLACE block renaming a function), *tool_call* (an `edit_file` call
  renaming a function on its def line).
- **retention**: a question about a read evicted by this step's edit, whose answer appears nowhere in
  call k's request ("what is the first class defined in `<file>`?").

### 3.3 The arms and the splice connector

| arm | what it computes |
|---|---|
| recompute | full prefill of call k's request: what the harness does today (the reference) |
| recompute_alt | the same prompt in a different batch (numerical noise floor; one event in four) |
| prefix | standard prefix caching: the unchanged prefix loaded, everything after the first edit recomputed |
| **shift** | **the RoPE cure**: every token run that survives the edit keeps its KV from call k−1, keys re-rotated by its position change δ; only fresh tokens are prefilled |
| norope | the same reuse without the rotation (the no-cure control) |
| oracle | this step's compaction undone: call k−1's history plus the new messages |

`splice_connector.py` is a vLLM v1 KV connector (`KVConnectorBase_V1`, loaded through
`kv_connector_module_path`). A request's plan travels in `SamplingParams.extra_args["kv_transfer_params"]`:
the scheduler side reports the loaded prefix as external tokens (synchronous load), and the worker side
writes it into the request's blocks before the forward, assembling it from a GPU store of raw cache
rows and rotating by δ in fp32 with R(m+δ) = R(δ)R(m). For GQA it rotates the full 128-dim key (NeoX
halves, θ = 10⁶); for MLA only the trailing 64 k_pe dims of each 576-dim entry (GPT-J pairs, as vLLM
builds that rotary), copying the latent unchanged. Saving copies computed slots into the store after
each forward. Prefix caching is off in the arm engine, because synchronously loaded blocks are
otherwise committed to the prefix cache and would silently contaminate the recompute arms.

The splice is staged, per event: prefill `old` (call k−1's history plus reply) and save its KV; for each
fresh segment before the tail, load the spliced prefix, prefill the segment, save it; the final
request loads everything up to the tail and prefills only the tail, then decodes greedily. Reuse runs
shorter than 16 tokens are recomputed.

### 3.4 Metrics

On the natural tail, against the same model's recompute:

- **Tool calls:** BFCL-style AST match of the call multiset (name plus normalized arguments; Patil et
  al., *The Berkeley Function Calling Leaderboard*, ICML 2025); tool-name match; ReCache's invocation F1
  and ID-F1 (turn-level, parallel calls).
- **Text rounds:** ROUGE-L F1, the metric CacheBlend and AgentKVShift report against full recompute.

On the natural tail, with no reference needed:

- **Tool-call validity:** malformed tool calls, unknown tool names, schema violations.
- **Grounding in the sandbox:** read/edit targets that do not exist, and `edit_file` whose `old_text`
  is not in the file as the run had left it (seed plus replayed writes).
- **Wasted rounds:** re-fetches of a file whose full content is still in context (TRACE's "repeated
  action" at a compaction boundary).
- **Citations:** validity of `file.py:N` citations in explanations.

Distribution, all loading arms (04_kv_splice and Leyline measures):

- **Reference:** recompute's own greedy decode with top-20 logprobs.
- **Per arm:** a teacher-forced pass that loads the arm's prefix and computes its tail plus
  recompute's output.
- **Measures:** top-1 agreement, ΔNLL of recompute's tokens, top-20 KL, first-token agreement, first
  divergence index.

Gold probes:

- **explain:** recall of the top-level names.
- **modify:** Aider's edit-format measures: well-formed, SEARCH copied verbatim, renamed, and the
  patched file compiles.
- **tool_call:** BFCL exact match of `edit_file(path, old_text, new_text)`, plus per-argument accuracy.
- **retention:** each answer is classed as recalls the evicted content, says it is gone, re-reads the
  file, or gives a wrong answer.

### 3.5 Statistics

The unit is one event, paired across arms. Rates carry Wilson 95% intervals, and paired binary
comparisons use exact McNemar tests. Means carry a bootstrap interval that resamples runs, since events
of one run are correlated. Every "disagrees with recompute" rate is read against recompute_alt
(recompute's own batch nondeterminism) and prefix (the loaded-context path with no reuse after the
edit).

### 3.6 Verification

**CPU tests** (`test_rope_shift.py`, 12 tests):

- splice plans tile the new sequence exactly;
- events and oracle histories on 04's real harness dumps (placeholder, snip and summary kinds);
- anchored markers;
- gold probes;
- the three tool-call formats and AST canonicalization;
- ROUGE-L and SEARCH/REPLACE parsing;
- retention categories;
- Wilson and McNemar;
- the manifest.

**Connector selftests** (`arms.py selftest`, H200, TP=1; `data/rope_shift_live/selftest_*.json`):

| check | Qwen3-8B | Qwen3-32B | GLM-4.7-Flash (MLA) |
|---|---|---|---|
| cache view per layer | [30342, 8, 16, 256] | [10376, 8, 16, 256] | [56431, 1, 16, 576] |
| T1 load all-but-last token of an exact prefix: same 32-token greedy text / max Δ first-token logprob | yes / 0.0 | yes / 0.0 | yes / 0.49 |
| T2 layer-0 keys: rotate(x at 0, δ=97) vs x prefilled at δ, max rel. error (unrotated) | 0.00055 (0.094) | 0.00051 (0.114) | 0.0049 (0.913) |
| T2 values / MLA latent identical | yes | yes | yes |
| T3 prompt_logprobs under a load, max Δ NLL | 0.0 | 0.0 | 0.40 |
| T4 recompute unchanged after splices (no contamination) | yes | yes | yes |
| T5 toy splice, first-token logprob gap to recompute: shift / norope | −0.06 / 2.80 | −0.31 / 2.75 | −0.06 / 1.36 |

GLM's T1/T3 differences are not a load error: a request whose context is loaded runs vLLM's absorbed
(latent) MLA attention path instead of the MHA prefill path, so logits differ at bf16 level while the
greedy text matches. That is why `prefix` (loaded exact prefix) is the path-matched control for the
shift arm on GLM.

At TP=2 (H200 pair; `selftest_{qwen32b,glm47flash}_tp2.json`, the layout the H100 shards use) the
correctness checks are unchanged:

- **Rotation (T2):** error 0.00051 for Qwen3-32B and 0.0049 for GLM; values and the latent are
  identical.
- **Contamination (T4):** none.
- **Toy splice (T5):** first-token gap, shift vs norope, is −0.28 vs 2.84 (Qwen3-32B) and −0.15 vs
  1.51 (GLM).
- **Cache per rank:** Qwen3-32B holds 4 of its 8 KV heads; GLM holds a full copy of the latent.

A 1-token forward and a 3,000-token forward now take different GEMM and all-reduce shapes, so a
loaded exact prefix no longer reproduces full-prefill logits bit for bit:

| | T1 max Δ first-token logprob | T3 max Δ NLL | T1 32-token greedy text |
|---|---|---|---|
| Qwen3-32B, TP=2 | 0.12 | 0.18 | same |
| GLM-4.7-Flash, TP=2 | 0.49 | 0.49 | diverges at a near-tie |

The selftest therefore gates on the exact checks: rotation, copied values, contamination, the load
itself, and shift beating norope. Logit deltas are only reported, with a 2-nat ceiling that a wrong
load would blow through.

## 4. Results

All tables come from `data/rope_shift_live/rope_shift_tables.md` (regenerate with `analyze.py`).
Rates carry Wilson 95% intervals; means carry a bootstrap interval that resamples runs.

### 4.1 Runs and events (T0)

| family | runs | finished / 900 s deadline / killed | lead calls, median (p10–p90) | runs with ≥1 compaction event |
|---|---|---|---|---|
| EXPLAIN | 251 | 176 / 44 / 31 | 35 (5–382) | 190 |
| MODIFY | 249 | 179 / 48 / 22 | 27 (3–264) | 172 |
| all | 500 | 355 / 92 / 53 | 30 (4–325) | 362 |

- **Which runs compact.** At the 50k limit, 362 of the 500 runs compacted; the other 138 finished
  their task below it. In total they produced 47,085 compaction events.
- **Snip dominates.** Once a lead passes 50 messages, `snip_compact` fires every round, so 96.7% of
  events include a snip edit, 18.2% include a placeholder, and 0.02% (11) are summaries.
- **The sample.** 683 events (≤2 per run) carry the arms. 475 have a gold probe (165 explain,
  157 modify, 153 tool_call) and 228 have a retention probe.

### 4.2 What the RoPE cure saves (T1, Qwen3 tokenizer)

| mean per event | tokens |
|---|---|
| prompt of call k | 9,054 |
| exact common prefix (what prefix caching reuses) | 3,191 |
| reused after the edit, shifted | 4,648 |
| fresh (what shift prefills: placeholder sentences, template glue, the new turn) | 1,215 |
| median largest position shift \|δ\| | 669 |

- **Prefill saved.** Shift prefills 20% of what prefix caching re-prefills after the edit point, a
  saving of 79.6% [77.7, 81.4].
- **Cost of staging.** The splice needs 3.15 stages per event on average: one extra prefill per fresh
  segment before the tail.

### 4.3 Does the next round change? (natural tail, T2, T3, T5)

| model | arm | identical next round | same tool calls (AST) | KL(recompute‖arm), nats/token | top-1 agreement |
|---|---|---|---|---|---|
| Qwen3-32B | recompute_alt (batch noise) | 95.9% (n=169) | 97.5% | — | — |
| | prefix (loaded exact prefix) | 95.6% | 97.5% | 0.0007 | 99.96% |
| | **shift** | **69.4%** [65.8, 72.7] | **71.9%** | **0.020** | **99.08%** |
| | oracle (compaction undone) | 67.3% | 70.3% | 0.028 | 98.94% |
| | norope | 29.0% | 29.5% | 0.79 | 88.8% |
| Qwen3-8B | recompute_alt | 94.1% (n=169) | 98.7% | — | — |
| | prefix | 95.2% | 97.9% | 0.0002 | 99.97% |
| | **shift** | **68.1%** | **72.1%** | **0.052** | **98.85%** |
| | oracle | 68.2% | 71.5% | 0.057 | 98.77% |
| | norope | 23.7% | 25.6% | 1.90 | 76.7% |
| GLM-4.7-Flash (MLA) | recompute_alt | 85.8% (n=169) | 87.2% | — | — |
| | prefix | 84.9% | 86.2% | 0.0036 | 99.76% |
| | **shift** | **71.2%** | **72.1%** | **0.017** | **99.20%** |
| | oracle | 65.3% | 66.5% | 0.031 | 98.77% |
| | norope | 30.7% | 31.5% | 0.11 | 96.65% |

- **Shift does change the next round, beyond numerical noise.** About 30% of next rounds are not
  token-identical to recompute, against 4–6% for the noise floors (15% for GLM, whose MoE and MLA
  numerics are noisier).
  - Against recompute_alt, on the 169 events with both: Qwen3-32B 56 discordant pairs vs 1
    (p = 8×10⁻¹⁶); Qwen3-8B 53 vs 1; GLM 36 vs 5.
  - When the call differs, the tool name almost never does: same tool 94–97% of the time.
- **The change is no larger than the compaction's own.** Undoing this step's compaction changes the
  next round as often or more often.
  - Paired on all 683 events, the counts are:

    | model | oracle agrees, shift does not | shift agrees, oracle does not | McNemar p |
    |---|---|---|---|
    | Qwen3-32B | 32 | 46 | 0.14 |
    | Qwen3-8B | 53 | 52 | 1.0 |
    | GLM | 34 | 74 | 0.00015 (shift significantly closer to recompute) |

  - Shift's KL is below oracle's on every model.
  - This holds within every stratum (T8). On snip events, shift/oracle agree with recompute 79.5% /
    77.9% of the time; on placeholder events 51.6% / 52.5% (Qwen3-32B). The harder events are
    harder for the compaction too: a placeholder removes a file the agent read a few rounds ago.
- **Larger shifts matter more.** For Qwen3-32B with |δ| < 512 tokens, 77.0% of next rounds are
  identical and the KL is 0.0095; for |δ| in 2k–8k, 62.5% and 0.032. The oracle degrades the same way
  (72.7% → 59.4%).
- **The rotation is necessary: without it, reuse breaks the round.**
  - For GQA, norope keeps 24–29% of rounds identical.
  - For Qwen3-32B it produces malformed tool calls in 10.5% of rounds and runs to the 512-token cap in
    24.2% (recompute: 0.1% and 5.0%).
  - MLA is far more tolerant (KL 0.11 vs 0.79–1.90), consistent with only 64 of each head's 256
    query-key dims carrying position. Even so, 69% of its next rounds change.

### 4.4 Is the next round worse? (T4, T6)

**Validity of the natural next round, Qwen3-32B, recompute → shift**

| check | recompute | shift |
|---|---|---|
| malformed tool calls | 0.1% | 0.1% |
| unknown tools | 0% | 0% |
| schema violations | 0% | 0% |
| read/edit target missing from the sandbox | 14.1% | 12.9% |
| `edit_file` old_text not in the file | 42.9% | 42.3% |
| re-fetch of a file still in context | 8.3% | 8.8% |

Qwen3-8B and GLM show the same pattern: shift never differs from recompute by more than the
intervals. The actions it takes instead are as well-formed and as grounded.

**Gold probes: pass rate, recompute → shift (McNemar), with the oracle for scale**

| probe | Qwen3-32B | Qwen3-8B | GLM-4.7-Flash |
|---|---|---|---|
| explain: list the top-level functions/classes of a surviving file | 91.5 → 87.3% (p = 0.04); oracle 87.3% | 83.0 → 83.0% (p = 1) | 89.7 → 89.1% (p = 1) |
| modify: SEARCH/REPLACE rename copied from a surviving file | **71.3 → 62.4%** (p = 0.02); oracle 75.2% | 72.0 → 69.4% (p = 0.39) | **43.9 → 37.6%** (p = 0.013); oracle 43.3% |
| tool_call: `edit_file` with the def line copied verbatim | 75.8 → 73.9% (p = 0.55) | 53.6 → 57.5% (p = 0.21) | 65.4 → 61.4% (p = 0.18) |
| norope, for scale (explain / modify / tool_call) | 44.2 / 17.8 / 41.2% | 34.5 / 22.3 / 31.4% | 66.7 / 10.8 / 30.7% |

- **The one cost is verbatim copying of a long span from the shifted region.** For Qwen3-32B,
  well-formed blocks drop from 87.9% to 75.8%, and SEARCH blocks copied byte-exact from 79.0% to
  68.8%, while the oracle does not drop (89.2%, 82.2%). GLM shows the same direction; Qwen3-8B only
  slightly.
- **Short copies are unaffected.** The tool_call probe copies a single def line, and no model shows a
  significant drop.

### 4.5 Does stale KV leak what was evicted? (retention, T7)

| model | arm | recalls evicted content | says it is gone | wrong answer |
|---|---|---|---|---|
| Qwen3-32B | recompute | 8.3% | 31.1% | 60.5% |
| | shift | 9.6% | 30.7% | 59.6% |
| | oracle (content still there) | 63.2% | 0.9% | 36.0% |
| Qwen3-8B | recompute | 1.8% | **57.5%** | **40.8%** |
| | shift | 3.9% | **38.6%** | **57.5%** |
| GLM-4.7-Flash | recompute | 0.0% | 99.6% | 0.4% |
| | shift | 0.0% | 99.1% | 0.9% |

- **No leak.** The surviving tokens' KV was computed while the evicted read was visible, but recall of
  the evicted content does not rise (Qwen3-32B 8.3 → 9.6%, p = 0.51).
- **Qwen3-8B loses track of what it no longer has.** With shift it admits the content is gone far less
  often: 45 events where only recompute abstains vs 2 where only shift does (p = 2×10⁻¹¹). It
  guesses wrong instead (42 vs 4, p = 5×10⁻⁹). Qwen3-32B and GLM show no such effect (p ≈ 1).

## 5. Conclusions

1. **On these three models, fixing the RoPE and skipping the re-prefill does not make the next round
   worse at the level that matters for an agent loop.**
   - Tool calls stay as valid and grounded as recompute's: no rise in malformed calls, unknown tools,
     missing paths or failed edits.
   - How far the next round moves is within what the compaction itself does to it: on every model the
     oracle changes the next round as often or more often, with higher KL.
   - It removes about 80% of the prefill that prefix caching spends after the edit.
2. **"Not worse" is not "the same."** About 30% of next rounds differ from recompute, far above
   numerical noise (4–6%). An agent served this way follows a different, equally valid trajectory,
   the same kind of divergence the harness already accepts when it compacts.
3. **Two measurable costs, both size- or task-specific.**
   - Long verbatim copies from the shifted region get worse on Qwen3-32B (−8.9 points on
     SEARCH/REPLACE edits) and GLM (−6.3), but not on single-line copies.
   - Qwen3-8B stops admitting that evicted content is gone and guesses instead.
   - Neither is repaired by the rotation; both come from KV computed in a context that no longer
     exists. That is the target for the compensation methods (CacheBlend, EPIC, PatchKV) left to a
     follow-up; Part B tests CacheBlend and EPIC.
4. **The rotation is not optional.** Without it, reuse on the GQA models:
   - changes 71–76% of next rounds;
   - makes 10.5% of Qwen3-32B's tool calls malformed;
   - cuts gold-probe pass rates by 22–54 points.

   MLA's decoupled RoPE key makes the model much less fragile, but not safe: its probes still drop
   23–35 points.
5. **Where it is worst.** Large position shifts (|δ| ≥ 2k tokens) and placeholder edits of recently
   read files. The compaction is equally disruptive there.

## 6. Caveats

- **One step of staleness.** The shift arm reuses call k−1's KV, which was itself computed fresh. A
  server that shifted at every compaction would compound staleness across steps (04's "shift" vs
  "shift1"); this experiment measures the single-step case the question asks about.
- **Greedy decoding, thinking off.** Both are held fixed across arms; the live runs sampled with the
  model's defaults.
- **Replays are off-policy.** Qwen3-8B and GLM-4.7-Flash replay contexts produced by Qwen3-32B.
- **Approximate KL.** It is computed over the reference's top-20 tokens, with missing arm entries
  floored at the arm's 20th logprob.
- **Re-rendered reply.** `old` re-renders call k−1's reply through the chat template rather than
  using the raw generated tokens, the same approximation 04 made.
- **Token cap.** Natural rounds are decoded up to 512 tokens. A `write_file` call carrying a whole file
  (M1 runs) is cut before its JSON closes. Such rounds are compared by tool name and exact tokens only,
  and are not counted as malformed. Recompute hits the cap on 5.0% of rounds for Qwen3-32B, 2.2% for
  Qwen3-8B and 3.2% for GLM.
- **GLM's noise floor is higher.** It is 15% non-identical, against 4–6% for Qwen. The prefix arm
  shows why: loaded context runs vLLM's absorbed-latent MLA kernel. Read GLM's shift numbers against
  prefix and recompute_alt, not against zero.
- **Solo leads only.** Teammates never compact in the s15 harness; only the lead does (08 and 02
  found the same). The prompts forbid delegation, so every event is a lead event.
- **Probe revision before the sweep.** The explain probe asks for top-level definitions and the
  retention probe for the first class. Both were revised after the smoke check showed ambiguous gold
  answers; the sweep's events were all extracted with the revised probes (`smoke/` keeps the old ones).

## 7. Reproduction

```bash
# manifest (offline) and CPU tests
python research/10_rope_shift/workloads.py build
python research/10_rope_shift/test_rope_shift.py
# connector selftests on one GPU
python research/10_rope_shift/arms.py selftest --model qwen32b --tp 1
# the sweep: 6 shards, each serve -> runs -> events -> selftest -> arms on the three models
for s in 0 1 2 3 4 5; do sbatch --export=NONE research/10_rope_shift/rope_shift.sbatch $s 6; done
# merge, score and tables on the login node
for m in qwen32b qwen8b glm47flash; do python research/10_rope_shift/score.py --model $m; done
python research/10_rope_shift/analyze.py
```

---

# Part B — Can selective recompute repair the two costs?

**Short answer.** On the same 683 events and three models, each replayed under seven arms (29,106
scored records):

- **EPIC repairs both costs on the GQA models.** It recomputes the first tokens of every reused run,
  4–11% of the reused tokens.
  - *Verbatim copies.* Qwen3-32B's SEARCH/REPLACE pass rate climbs from 61.1% under shift to 72.0%
    (epic32) and 73.2% (epic128), level with recompute's 72.6%.
  - *Evicted content.* With epic128, Qwen3-8B again says the content is gone in 55.3% of retention
    probes (recompute 57.5%, shift 38.2%).
  - *Next-round KL* falls 33–58%, from epic32 on Qwen3-32B to epic128 on Qwen3-8B. With epic128 it
    goes 0.021 → 0.012 on Qwen3-32B and 0.052 → 0.022 on Qwen3-8B.
- **CacheBlend repairs the copy cost but only part of the retention cost.** Recomputing the 5–15% of
  tokens whose layer-1 key deviates most brings Qwen3-32B's copies back (74.5% and 72.6%) but fixes
  only half to two thirds of Qwen3-8B's abstention. At matched budgets, EPIC keeps more next rounds
  identical to recompute: epic128 against blend15, 60 vs 33 events on Qwen3-32B and 52 vs 20 on
  Qwen3-8B.
- **Which tokens are recomputed matters, not how many.** A random 15% leaves both costs in place:
  Qwen3-32B copies 65.0%, Qwen3-8B abstention 38.6%.
- **The MLA model is not repaired.** GLM-4.7-Flash's copy cost stays under every arm (39.5% → 40.8–43.3%,
  recompute 47.1%), and its KL falls only 15–29%.
- **Tool calls stay valid under every arm.**
- **In time, EPIC is nearly free and CacheBlend costs about three times more** (§B3.6).
  - Qwen3-32B, time to first token on one H200, median over 200 events: recompute 1,097 ms, prefix
    caching 765, shift 90; epic32 99 and epic128 121; blend05 139 and blend15 223. The CacheBlend
    times include its 12.5 ms selection pass.
  - Qwen3-8B: EPIC adds 2–6 ms to shift's 36 ms, and CacheBlend 12–26 ms.

## B1. Question

Part A left two costs of reusing the surviving KV with re-rotated RoPE:

- **Long verbatim copies from the shifted region get worse.** SEARCH/REPLACE edits fall 8.9 points on
  Qwen3-32B (p = 0.02) and 6.3 on GLM-4.7-Flash (p = 0.013).
- **Qwen3-8B stops admitting that evicted content is gone.** It says so in 2 events where only shift
  does, against 45 where only recompute does (p = 2×10⁻¹¹), and guesses wrong instead (42 vs 4).

The rotation cannot touch either one. The reused KV of a token was computed while it attended to a
context that the compaction has since rewritten, and that part of the KV is not positional. The
KV-reuse literature repairs it by recomputing a small share of the reused tokens:

- **CacheBlend** (Yao et al., *CacheBlend: Fast Large Language Model Serving for RAG with Cached
  Knowledge Fusion*, EuroSys 2025; implemented in LMCache's blender): at an early check layer, compare
  every reused token's freshly computed key with its reused key and recompute the r% that deviate most.
- **EPIC** (Hu et al., *EPIC: Efficient Position-Independent Caching for Serving Large Language
  Models*, ICML 2025, "LegoLink"): recompute the first k tokens of every reused chunk. These tokens were
  computed at a chunk start and act as attention sinks.

Which of them, at what budget, repairs the two costs on Part A's 683 events? And does the selection
matter, compared with a random set of the same size?

## B2. Method

### B2.1 Arms

Every arm loads the unchanged prefix before the first edit exactly (as prefix caching would) and
computes the fresh text: placeholders, compaction sentences, new messages and the tail. The arms differ
in which reused tokens after the first edit are recomputed.

| arm | reused tokens recomputed | at which layers |
|---|---|---|
| recompute | all (the reference, rerun in this session) | all |
| shift | none: Part A's RoPE cure, rerun through Part B's mechanism | — |
| epic32, epic128 | the first 32 / 128 tokens of every reused run after the first edit | all |
| blend05, blend15 | the 5% / 15% with the largest ‖k_fresh − k_reused‖² at layer 1 | layer 0 for every token, then the selected tokens from layer 1 on |
| rand15 | a uniformly random set, as large as blend15's | as blend15 |

The CacheBlend arms follow LMCache's blender (`lmcache/v1/compute/blend/blender.py`, `process_qkv`):

- **Check layer.** It is layer 1, and the selection takes effect in that layer's own attention.
  Layers below it are recomputed for every token. From it on, an unselected token keeps its reused KV,
  and other tokens attend to that KV.
- **Deviation.** `diff_k` is the squared L2 distance of the post-RoPE key over all heads; the reused
  key is re-rotated to its new position first. The fresh keys come from the recompute pass, whose
  layers 0–1 compute exactly what CacheBlend's full first layers compute.
- **Ratio.** It counts the reused tokens after the first edit. CacheBlend counts every token of the
  reused chunks; the exactly reused prefix has zero deviation and the fresh text is always computed,
  so both are left out.
- **EPIC's chunk** is a reused run: a maximal span copied from call k−1 with one position change δ.

### B2.2 Mechanism: one forward, KV overridden per layer

Part A's staged splice (§3.3) cannot run token-scattered selections. Every selected span becomes a
stage, and merging stages to keep their number bounded recomputes whatever lies between them. In a CPU
dry run, a 510-token CacheBlend selection grew to 2,474 recomputed tokens, 73% of the reused region.
Part B instead runs one forward per (event, arm, tail) over new[P0:ts], where P0 is the end of the
exact prefix and ts the start of the tail, and the connector (`splice_connector.py`, "override")
replaces KV layer by layer:

- **Where it hooks.** A forward pre-hook sits on every attention module. Before the module sees its
  post-RoPE K/V rows (GQA: `key` and `value`; MLA: the latent `kv_c_normed` and `k_pe`), the hook
  overwrites, in place, the rows of the unselected reused tokens at every layer ≥ `from_layer` with
  their call-(k−1) rows re-rotated by δ. The module then writes those rows into the paged cache and
  attends with them.
- **Why the module's inputs, not the cache.** vLLM's MLA prefill attends to the current chunk
  from the in-flight latents, so overwriting only the paged cache would change what later requests see
  but not this forward. FlashAttention, used for the Qwen models, reads the current chunk back from the
  cache, so either would do there.
- **Finding the rows.** Positions are located in the step's flattened token batch through the forward
  context's slot mapping.
- **Save and load.** The forward saves the KV of [P0, ts). The generation and the teacher-forced pass
  then load new[0:ts] and compute only the tail, as Part A's final waves did.

Budgets are therefore exact: nothing is staged or merged. The emulation computes every token of
[P0, ts), so it measures what a real CacheBlend or EPIC implementation would output, not how fast it
would run; §B3.6 measures that separately.

### B2.3 What stays from Part A

- **Same as Part A:** events, tails (natural, probe, retention), greedy decoding, token caps, metrics
  (§3.4) and statistics (§3.5).
- **Recompute rerun.** Recompute is rerun in the same session, so every arm is paired with a
  same-session reference.
- **Mechanism check.** Part B's shift arm is compared with Part A's staged shift on the same prompts
  (§B3).

### B2.4 Verification

**CPU tests** (`test_rope_shift.py`, now 15 tests):

- override plans partition the reused region;
- every overridden span's source tokens in call k−1 equal its target tokens;
- the budgets are exact;
- grouping arms under the store's memory budget.

**Override selftests** (`repair.py selftest`; `data/rope_shift_repair/selftest_*.json`, and each
shard's own copy under `pipeline/`). They run on a real event, RS-click-E1-r1#c289, whose shifted run
is 3,041 tokens with δ = −92:

| check | Qwen3-8B (TP=1) | Qwen3-32B (TP=1 / TP=2) | GLM-4.7-Flash (MLA, TP=1 / TP=2) |
|---|---|---|---|
| R1 overridden rows = re-rotated call-(k−1) rows, max abs diff at the first / middle / last layer | 0 / 0 / 0 | 0 / 0 / 0 (both) | 0 / 0 / 0 (both) |
| R2 `from_layer` 2: layer-1 rows are fresh, squared error vs recompute (vs the reused rows) | 0.0 (1,470) | 0.0 (520) / 0.0 (248) | 0.030 (3.5) / 0.039 (3.5) |
| R2 `from_layer` 2: layer-2 rows are the reused ones, max abs diff | 0 | 0 / 0 | 0 / 0 |
| R3 selected tokens keep fresh KV (layer-1 error ratio) and unselected neighbours are reused exactly | 0.0, yes | 0.0, yes (both) | 0.014 / 0.016, yes |
| R4 hooks / layers; overrides fired | 36 / 36, yes | 64 / 64, yes | 47 / 47, yes |
| R5 recompute unchanged afterwards (no contamination) | yes | yes | yes |

(At TP=2 each Qwen3-32B rank holds 4 of the 8 KV heads, so its squared errors cover half the heads;
each GLM rank holds a full copy of the latent.)

GLM's fresh layer-1 rows are not bit-exact because its prefill over a loaded prefix attends in two
parts (the context from the cache, the chunk in flight) merged by log-sum-exp. This is the same
path noise Part A's `prefix` arm measures.

## B3. Results

All tables come from `data/rope_shift_repair/rope_shift_repair_tables.md` (regenerate with
`analyze.py --repair`). Rates carry Wilson 95% intervals, means a bootstrap interval over runs.
McNemar cells read b / c: b counts events where only the base arm has the property, c those where
only the compared arm has it.

### B3.1 The mechanism reproduces Part A (B0)

**Same generated text as Part A's run of the same arm, natural tail:**

| model | Part B recompute | Part B shift (one-pass override) |
|---|---|---|
| Qwen3-32B | 95.0% | 92.7% |
| Qwen3-8B | 94.6% | 95.8% |
| GLM-4.7-Flash | 87.6% | 84.2% |

The one-pass override and Part A's staged splice differ by batch noise only.

**Part A's costs reproduce:**

| cost | Part A | Part B |
|---|---|---|
| Qwen3-32B modify pass, recompute vs shift | 71.3% vs 62.4% | 72.6% vs 61.1% |
| Qwen3-8B abstention, discordant events (only recompute / only shift) | 45 vs 2 | 47 vs 3 |
| GLM modify pass, recompute vs shift | 43.9% vs 37.6% | 47.1% vs 39.5% |

### B3.2 The two costs under each repair (B1)

| arm | reused tokens recomputed (Qwen) | Qwen3-32B modify pass | GLM modify pass | Qwen3-8B says it is gone | Qwen3-8B wrong answer |
|---|---|---|---|---|---|
| recompute | 100% | 72.6% | 47.1% | 57.5% | 40.8% |
| shift | 0 | 61.1% | 39.5% | 38.2% | 57.9% |
| epic32 | 4.4% | 72.0% | 40.8% | 50.0% | 46.5% |
| epic128 | 10.8% | 73.2% | 40.8% | 55.3% | 41.7% |
| blend05 | 5% | 74.5% | 42.7% | 46.9% | 49.6% |
| blend15 | 15% | 72.6% | 42.0% | 50.9% | 45.2% |
| rand15 | 15% | 65.0% | 43.3% | 38.6% | 57.9% |

(n = 157 modify probes and 228 retention probes per model. GLM recomputes 3.9% / 10.3% under the
EPIC arms, because its tokenizer makes runs shorter.)

- **Qwen3-32B, verbatim copies.**
  - Every non-random arm beats shift (McNemar p ≤ 0.006) and is indistinguishable from recompute
    (p ≥ 0.65).
  - The SEARCH block is again copied verbatim from context: 77.7–80.3%, against 67.5% under shift
    and 79.0% under recompute.
  - rand15 does not beat shift (11 vs 5, p = 0.21) and stays below recompute (p = 0.065).
- **Qwen3-8B, evicted content.** Share of the shift→recompute gap closed:
  - epic128: 89%; its remaining difference to recompute (14 vs 9 events) is not significant,
    p = 0.4.
  - blend15: 66%; epic32: 61%; blend05: 45%. These three stay below recompute (p ≤ 0.008).
  - rand15: 2%.
  - Wrong answers follow: epic128 41.7% against recompute 40.8% and shift 57.9%.
- **GLM, verbatim copies.**
  - No arm differs from shift (p ≥ 0.21), and the EPIC arms stay below recompute (14 vs 4,
    p = 0.031).
  - Two recomputes of the same prompts already disagree on 7 vs 2 modify probes. Differences of a
    few points between GLM's repair arms are therefore within noise.
- **No cost to repair elsewhere.** Qwen3-32B and GLM showed no retention cost in Part A. On Qwen3-32B,
  epic32 even says "gone" more often than recompute (38.2% vs 31.1%, p = 0.029) and answers wrongly
  less often (52.6% vs 61.0%).
- **Validity is untouched** (B5). On every model, malformed calls, unknown tools, schema violations,
  missing paths, failed edits and re-fetches stay at recompute's level. Qwen3-32B has 0.1–0.3%
  malformed calls and no schema violations; GLM's 2–2.5% schema violations appear under recompute
  too.

### B3.3 Next-round fidelity (B2, B6)

KL(recompute‖arm) per token on the natural tail (reduction against shift), and identical next rounds:

| arm | Qwen3-32B KL | Qwen3-8B KL | GLM KL | identical: Qwen3-32B / Qwen3-8B / GLM |
|---|---|---|---|---|
| shift | 0.0213 | 0.0518 | 0.0170 | 68.4% / 67.8% / 74.5% |
| epic32 | 0.0142 (−33%) | 0.0288 (−44%) | 0.0132 (−22%) | 72.9% / 72.2% / 74.5% |
| epic128 | 0.0115 (−46%) | 0.0218 (−58%) | 0.0128 (−25%) | 74.7% / 75.7% / 74.8% |
| blend05 | 0.0187 (−12%) | 0.0394 (−24%) | 0.0144 (−15%) | 69.0% / 69.3% / 74.4% |
| blend15 | 0.0155 (−27%) | 0.0301 (−42%) | 0.0121 (−29%) | 70.7% / 71.0% / 75.8% |
| rand15 | 0.0174 (−18%) | 0.0443 (−14%) | 0.0143 (−16%) | 70.0% / 68.4% / 74.5% |

Halving the KL does not bring the rounds back to recompute's noise floor (95–96% identical in Part
A). Even under epic128 about a quarter of greedy next rounds still differ somewhere, because one
changed token redirects the rest of a greedy continuation.

### B3.4 What the selections pick, and equal budgets (B3, B3b)

- **CacheBlend's signal is concentrated, least so on MLA.** The top 5% of reused tokens carry this
  share of the layer-1 key deviation (top 15% in brackets): Qwen3-8B 61% (76%), Qwen3-32B 46% (63%),
  GLM 43% (63%).
- **It finds run heads, but partly.** Run heads make up 14–16% of blend15's picks, against 3.9–4.4%
  of all reused tokens, so 3.2–3.6× what chance would give. The rest of its budget is scattered over
  340–430 spans per event; EPIC recomputes 3–4.
- **At matched budgets, EPIC keeps more next rounds identical** (events where only that arm is
  identical to recompute):

  | pair | Qwen3-32B | Qwen3-8B | GLM |
  |---|---|---|---|
  | epic32 vs blend05 (about 5%) | 47 vs 20 | 42 vs 22 | 27 vs 26 |
  | epic128 vs blend15 (EPIC with 28% fewer tokens) | 60 vs 33 | 52 vs 20 | 34 vs 41 |

  On both Qwen models the paired KL difference points the same way.
- **CacheBlend's selection still beats chance:**
  - lower KL on every model;
  - the Qwen3-32B copy probe, 20 vs 8 (p = 0.036);
  - Qwen3-8B's abstention, 29 vs 1.

### B3.5 By edit kind (B3c)

- **Qwen3-8B.** Shift loses the abstention after both kinds of edit, and epic128 restores both:
  - snip: 68.1% under recompute → 41.6% under shift → 66.4% under epic128;
  - placeholder: 47.7% → 35.1% → 45.0%.
- **KL is higher after placeholder edits** than after snips under every arm (Qwen3-32B shift: 0.041
  vs 0.012). epic128 cuts both kinds by 40–60% on the Qwen models.

### B3.6 What the repairs cost in time (`latency.py`)

The quality numbers above come from an emulation that computes every token after the exact prefix, so
its run time says nothing about speed. `latency.py` times, for the natural next round of 200 of the 683
events (one random sample, the same for every model), the request a real implementation of each arm
would run:

- **The request.** The connector loads the arm's reused KV from the previous call's KV, which stays
  on the GPU: the exact prefix as it is and every surviving run re-rotated. Only the arm's computed
  tokens are prefilled: the fresh text and the tail, plus EPIC's run heads or CacheBlend's selected
  tokens.
  - The computed tokens are placed after the loaded context. Each request therefore has the arm's
    true numbers of loaded and computed tokens and its true context length, and it does the real load
    and rotation.
  - As a result, a computed token attends to the whole context instead of only what precedes it: an
    upper bound on that part of its work.
  - Which tokens EPIC or CacheBlend pick doesn't change the work, only how many; the counts follow
    Part B's rules.
- **CacheBlend's selection pass.** To find the deviating tokens, CacheBlend runs every token after
  the prefix through layer 0 and layer 1's projections. That pass is timed with CUDA events inside the
  prefix-caching request, which computes exactly those tokens, and it is added to CacheBlend's times.
- **Setup.**
  - One H200 per model at TP=1, running vLLM 0.29 in eager mode, as all runs here did.
  - One request at a time on an idle engine. Time to first token (TTFT) is measured end to end in the
    driver.
  - Each event is run three times in rotating arm order; its value is the median (repeats agree within
    about 2%).

**Time to first token, median over the 200 events** (in brackets: the per-event median share of
recompute's TTFT):

| arm | tokens computed, mean (Qwen / GLM tokenizer) | Qwen3-32B | Qwen3-8B | GLM-4.7-Flash |
|---|---|---|---|---|
| recompute | 8,941 / 8,440 | 1,097 ms (100%) | 254 ms (100%) | 197 ms (100%) |
| prefix caching | 5,608 / 5,173 | 765 ms (71%) | 184 ms (73%) | 154 ms (82%) |
| shift | 1,174 / 1,143 | 90 ms (9%) | 36 ms (18%) | 73 ms (47%) |
| epic32 | 1,300 / 1,238 | 99 ms (10%) | 38 ms (18%) | 73 ms (48%) |
| epic128 | 1,479 / 1,388 | 121 ms (13%) | 42 ms (19%) | 80 ms (49%) |
| blend05 | 1,396 / 1,345 | 139 ms (13%) | 52 ms (21%) | 85 ms (52%) |
| blend15 | 1,840 / 1,748 | 223 ms (20%) | 72 ms (28%) | 93 ms (58%) |

**What each repair adds to shift's TTFT, mean per event [95% CI]:**

| arm | Qwen3-32B | Qwen3-8B | GLM-4.7-Flash |
|---|---|---|---|
| epic32 | +12.3 ms [10.8, 14.0] | +1.8 ms [1.4, 2.2] | +0.8 ms [0.4, 1.2] |
| epic128 | +32.6 ms [29.5, 36.0] | +6.0 ms [5.2, 6.7] | +3.5 ms [2.8, 4.2] |
| blend05 | +37.6 ms [34.0, 41.2] | +11.5 ms [10.3, 12.6] | +7.8 ms [6.9, 8.7] |
| blend15 | +95.0 ms [85.8, 103.9] | +26.2 ms [23.6, 28.6] | +16.4 ms [14.7, 17.9] |
| of which CacheBlend's selection pass, median | 12.5 ms | 5.7 ms | 3.3 ms |

The full tables, with means and the connector's load time per arm, are in
`data/rope_shift_repair/latency/latency_tables.md`.

- **The reuse is what saves time; the repairs keep most of the saving.** Shift answers in 9% of
  recompute's time on Qwen3-32B (90 against 1,097 ms) and in 18% on Qwen3-8B. Prefix caching saves
  only 27–29%. The EPIC arms stay at 10–13% of recompute on Qwen3-32B.
- **EPIC's repair is nearly free.** epic32 restores Qwen3-32B's verbatim copies (§B3.2) for +12 ms,
  and epic128 restores Qwen3-8B's abstention for +6 ms.
- **CacheBlend costs about three times as much as EPIC at similar budgets:** +38 against +12 ms at
  about 5% on Qwen3-32B, and +95 against +33 ms for blend15 against epic128. Two things add up:
  - its selection pass (12.5 ms on Qwen3-32B);
  - its larger budget: blend15 recomputes 666 extra tokens against epic128's 305, at about
    0.10–0.12 ms per token on Qwen3-32B.
- **GLM is dominated by a fixed cost.** A 16-token request already takes 65 ms end to end in eager mode,
  because its mixture-of-experts layers launch many small kernels (Qwen3-32B: 25 ms, Qwen3-8B: 15 ms).
  So shift reaches only 47% of recompute's time, and every repair adds only 1–16 ms. Those repairs
  don't fix GLM's copy cost either (§B3.2).
- **Loading and re-rotating the reused KV is part of every reuse arm's time.** It takes a median of
  23–26 ms on Qwen3-32B (25.5 of shift's 90 ms), 13–16 ms on Qwen3-8B and 5–9 ms on GLM, and a fused
  kernel could shrink it. Prefix caching's 2–6 ms copy is what native prefix caching, which shares
  cache blocks, avoids.
- **Means run higher than medians** (Qwen3-32B shift: 193 against 90 ms). Some events carry thousands
  of new tokens, which every arm has to prefill.

## B4. Conclusions

1. **On the GQA models, both costs of the RoPE cure are repairable cheaply.** Recomputing the first
   32–128 tokens of every reused run (EPIC) costs 4–11% of the reused tokens. It restores Qwen3-32B's
   verbatim copies and Qwen3-8B's awareness of evicted content, and it halves next-round KL.
2. **The damage sits in particular tokens.** A random 15% of reused tokens repairs neither cost. By
   contrast, 4% chosen by position (epic32) restores Qwen3-32B's copies, and 11% (epic128) restores
   Qwen3-8B's abstention. The tokens that matter most are the first ones after each edit point: they
   were computed right after content that the compaction has removed or moved.
3. **CacheBlend's rule works but spends its budget less well here.** Its key-deviation signal does
   pick run heads 3–4× more often than chance, but most of its budget goes to scattered
   high-deviation tokens that matter less for the next round. At equal or larger budgets it keeps
   fewer rounds identical than EPIC and repairs only part of the retention cost.
4. **MLA needs something else.** On GLM-4.7-Flash neither rule repairs the copy cost, and KL falls by
   at most 29%. Its reused-KV error is spread more evenly (the top 5% of tokens carry 43% of the
   deviation), so no small token set captures it. Candidates for a follow-up:
   - recomputing whole reused runs near the tail;
   - correcting the latent directly (PatchKV-style).
5. **For a server that applies the RoPE cure on GQA models,** recomputing the first ~128 tokens after
   every edit point is the cheap default these results support. On an H200 it adds 12–33 ms to
   shift's 90 ms time to first token on Qwen3-32B, which keeps the next round at 10–13% of a full
   recompute's 1.1 s. CacheBlend's budgets cost about three times as much (§B3.6).

## B5. Caveats

- **Quality by emulation, speed by shape-equivalent requests.** The quality arms compute every token
  of [P0, ts) and overwrite the unselected ones. The time a real implementation would take is
  measured separately (§B3.6), assuming an ideal implementation:
  - the computed tokens' attention covers the whole context, an upper bound;
  - not included are the per-layer gathering and scattering of the selected tokens' activations that
    LMCache's blender performs, and the cost of keeping the previous call's KV (it stays in GPU
    memory here);
  - absolute times come from eager-mode vLLM, one request at a time. A compiled deployment with CUDA
    graphs would cut the fixed per-request cost, most of all GLM's 65 ms. Under concurrent load the
    saved prefill work becomes throughput, which is not measured.
- **One selection per event.** CacheBlend's deviation is computed on the natural tail and applied to
  the probe and retention tails, whose reused regions coincide with it up to the tail. EPIC's run heads
  are taken per tail.
- **One check layer.** Layer 1, as in LMCache's examples. The paper's gradual filtering over several
  layers is not tried.
- **One step of staleness,** as in Part A: call k−1's KV was computed fresh.
- **Out-of-memory restarts.** The per-arm KV store shares the GPU with vLLM, and it took two fixes.
  - The first attempt sized the store from CUDA's free memory plus PyTorch's cached blocks. vLLM's
    next forward reuses those blocks for activations, and two Qwen3-32B engines on H200 ran out of
    memory on their second chunk. The store is now budgeted only outside vLLM's
    `gpu_memory_utilization` share (`store_room`), in (arm, event) units.
  - Two Qwen3-8B engines then died after ~15 chunks from fragmentation: 11 GiB of PyTorch's cache
    was reserved but unusable. PyTorch's expandable segments would prevent this, but vLLM refuses
    them whenever a KV connector is configured. So freeing store entries now returns the cache
    (`torch.cuda.empty_cache`), the margin is 16 GiB, and a chunk's old KV may fill at most half
    the room.
  - Engines that died, and orphaned engine processes left behind by killed drivers, cost
    some events their first pass. Chunks are written whole, so the follow-up jobs rerun exactly the
    missing events. None of the fixes changes what an arm computes, only how the store is grouped.
- **Two stalled chunks.** On H200 at TP=1, the Qwen3-32B engine's KV pool holds about 91k tokens.
  Two 4-event chunks never finished there: all engine threads waited, with no progress for 45
  minutes and again after a restart. The same events ran without trouble on Qwen3-8B and GLM, whose
  pools are about five times larger. Run one event at a time with a larger pool
  (`--gpu-util 0.72`) and 16 concurrent sequences, all 8 finished in 3–43 s each. So the stall
  comes from the chunk's combined demand on a small pool, not from any single event.
  `repair.py` now carries a per-chunk watchdog (`--chunk-timeout`, 15 min). It records a stalled
  chunk's events in `stuck_{model}.txt` and exits, and `--only` / `--ignore-stuck` retry them.
  Their records are in `arms_qwen32b_s{0,1}_stuck.jsonl`.

## B6. Reproduction

```bash
python research/10_rope_shift/test_rope_shift.py
python research/10_rope_shift/repair.py selftest --model qwen8b
# 4 shards; each: selftest -> repair arms on Qwen3-32B, Qwen3-8B, GLM-4.7-Flash (resumable;
# steps can be named after the shard count, e.g. `... repair.sbatch 1 4 repair32`)
for s in 0 1 2 3; do sbatch --export=NONE research/10_rope_shift/repair.sbatch $s 4; done
D=research/10_rope_shift/data/rope_shift_repair
# events the watchdog recorded in $D/stuck_<model>.txt: one per engine, larger KV pool, fewer sequences
python research/10_rope_shift/repair.py run --model qwen32b --tp 1 --shard 1 --shards 4 --subshard 1 \
  --subshards 2 --only 'RS-typer-M2-r2#c40' --ignore-stuck --gpu-util 0.72 --max-num-seqs 16 \
  --out $D/arms_qwen32b_s1_stuck.jsonl
# merge, score and tables on the login node
for m in qwen32b qwen8b glm47flash; do
  python research/10_rope_shift/score.py --model $m --arms $D/arms_$m.jsonl --out $D/scored_$m.jsonl
done
python research/10_rope_shift/analyze.py --repair
# latency: time to first token of every arm (one 2xH200 job: Qwen3-32B on GPU 0, Qwen3-8B then GLM on
# GPU 1; resumable), then the report
sbatch --export=NONE research/10_rope_shift/latency.sbatch 200
python research/10_rope_shift/latency.py report
```

## Data inventory

| path | what it holds | written by | status |
|---|---|---|---|
| `data/rope_shift_live/manifest.jsonl`, `manifest_meta.json` | the 500 run specs; codebase versions; the 08 item texts | `workloads.py build` | input |
| `data/rope_shift_live/runs/<label>/` (500) | per run: `<label>.requests.jsonl.xz` (every model call, serialized at call time), `run_*.jsonl.xz` (trace), `run_*.inputs.jsonl.xz` (profile_run sidecar), `.meta.json` (status, wall time, calls), `.answer.json`, `.prompt.txt`, `.diff` (MODIFY sandbox), `.done`; 51 MB in total. `console.log` is git-ignored. RS-requests-E2-r1 and RS-click-M2-r1 are the two live-smoke runs (same configuration; the first made 2,035 lead calls before its server was stopped at 1,919 s) | `run_sweep.py`, `live_run.py` | published |
| `data/rope_shift_live/events_all_s{0..5}.jsonl`, `events_s{0..5}.jsonl` | every compaction event of each shard's runs (kinds, geometry); the ≤2 sampled per run with their probe and retention tails and gold answers | `events.py extract --shard` | published |
| `data/rope_shift_live/arms_{qwen32b,qwen8b,glm47flash}_s{shard}_{engine}.jsonl` | one record per (event, tail, arm): output text, identity with recompute, first divergence, splice plan, teacher-forced top-1 / NLL / KL | `arms.py run` | published |
| `data/rope_shift_live/scored_{qwen32b,qwen8b,glm47flash}.jsonl` | the arms records merged over shards, with every metric of §3.4 | `score.py` | published |
| `data/rope_shift_live/rope_shift_tables.md`, `.json` | the tables of §4 | `analyze.py` | published |
| `data/rope_shift_live/selftest_{qwen8b,qwen32b,glm47flash}.json`, `selftest_{qwen32b,glm47flash}_tp2.json` | connector selftests T1–T5 on H200 at TP=1 and TP=2 (the `ok` flags of the first TP=1 GLM file and the two TP=2 files predate the final gating rule, see §3.6) | `arms.py selftest` | published |
| `data/rope_shift_live/pipeline/shard{0..5}/` | per shard: `log.txt` (stages), `selftest_*_tp{1,2}.json` (the pre-arms gate on the shard's own GPUs), `server_info.txt`, `base_urls`; the step logs (`sweep.log`, `events.log`, `arms_*.log`, `vllm_*.log`, `slurm-*.log`) stay local, git-ignored by the repository's `*.log` rule | `rope_shift_pipeline.sh` | published |
| `data/rope_shift_live/smoke/` | pipeline checks: arms on 4 events from the smoke runs' partial captures (Qwen3-32B, Qwen3-8B, GLM-4.7-Flash; probes built before the explain/retention gold fix), and a 30-event early look (`*_early*`, `tables_early.md`) | `events.run_events`, `arms.py`, `score.py`, `analyze.py` | smoke |
| `data/rope_shift_repair/arms_{qwen32b,qwen8b,glm47flash}_s{shard}_{engine}.jsonl` | Part B: one record per (event, tail, arm) for recompute and the six repair arms: output text, identity with recompute, teacher-forced top-1 / NLL / KL, the arm's budget (`repair`: reused tokens, selected, spans, from_layer) and the event's CacheBlend deviation profile (`deviation`) | `repair.py run` | published |
| `data/rope_shift_repair/stuck_qwen32b.txt` | the 8 events of the two chunks that stalled on the Qwen3-32B engine (§B5); since rerun one per engine, their records are in `arms_qwen32b_s{0,1}_stuck.jsonl` | `repair.py` watchdog, by hand for the first chunk | published |
| `data/rope_shift_repair/scored_{qwen32b,qwen8b,glm47flash}.jsonl` | Part B records merged over shards, with every metric of §3.4 | `score.py --arms … --out …` | published |
| `data/rope_shift_repair/latency/latency_{qwen32b,qwen8b,glm47flash}.jsonl`, `latency_*_meta.json`, `latency_tables.md`, `.json`, `gpus.txt` | per event (200 per model): prompt geometry, tokens computed per arm, and three timed runs per arm (TTFT, connector load, CacheBlend's selection pass); the engine and GPU per model; the report behind §B3.6. The run logs (`run_*.log`, `slurm-*.log`) stay local, git-ignored by the `*.log` rule | `latency.py run`, `latency.py report` | published |
| `data/rope_shift_repair/rope_shift_repair_tables.md`, `.json` | the tables of §B3 | `analyze.py --repair` | published |
| `data/rope_shift_repair/selftest_*_tp{1,2}.json` | override selftests R1–R5 per model and TP layout (the first passing copy; every shard also keeps its own run under `pipeline/`) | `repair.py selftest` | published |
| `data/rope_shift_repair/pipeline/shard{0..3}/` | per shard: `log.txt` (stages), `selftest_*_tp{1,2}.json`; the step logs (`repair_*.log`, `selftest_*.log`, `slurm-*.log`) stay local, git-ignored by the `*.log` rule | `repair_pipeline.sh` | published |
| `data/pilot_hf_synthetic/` | the 2026-09-24 pilot: 528 generations of an HF-transformers splice engine on Qwen3.8-27B over synthetic read-only conversations; kept small, its scripts and 37 MB corpus deleted | previous session | superseded |

## Source map

New topic; no merged notes. The pilot's scripts (`corpus_build.py`, `splice_engine.py`, `run_arms.py`,
`score.py`, `analyze.py`, `vllm_check.py`, `rope_shift_pipeline.sh`, `rope_shift.sbatch`) were never
committed and were deleted on 2026-09-25; `score.py`, `analyze.py`, `rope_shift_pipeline.sh` and
`rope_shift.sbatch` are new files that reuse only the names and the Wilson/McNemar helpers.

## Cleanup notes

- **Pilot defects (why it was superseded rather than completed).**
  - Its tool-call scorer parsed only JSON, while Qwen3.8 emits XML-style calls
    (`<function=…><parameter=…>`), so every tool_call row scored 0.
  - Its evicted-content probe was stored but never asked; the scorer looked for the probe's answer in
    the task output instead.
  - Its splice carried the hybrid model's recurrent state from before the edit, so evicted content
    stayed visible to 48 of 64 layers.
  - Its vLLM stage crashed at startup (`max_num_seqs (1024) exceeds available Mamba cache blocks
    (535)`).
  - Its conversations were synthetic, not live harness runs.
