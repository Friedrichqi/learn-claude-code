# Keeping the KV cache across micro-compaction edits: splice + RoPE re-rotation vs re-prefill

**Question.** When the lead's history exceeds `CONTEXT_LIMIT`, `micro_compact` rewrites an old tool
result in the *middle* of the prompt into one line, `[Earlier tool result saved at <spill path>]`.
The prompt prefix is now different from that point on, so a provider re-prefills everything after
the edit. Could the serving side instead prefill only the placeholder, keep the KV entries of the
following history, and only re-rotate their RoPE to the new positions? Two sub-questions:

1. **Fidelity and cost.** How far does the model's next output move when it attends to a stale
   suffix, and how many prefill tokens does the splice avoid at the 50k-char budget?
2. **Retention.** The stale suffix entries were computed while the evicted file was still in
   context. Do they carry enough of the file's semantics that the model no longer needs the
   grep/zoom-in rounds that earlier profiling attributed to eviction?

Everything below is measured at `CONTEXT_LIMIT = 50,000` chars on the committed harness, with none of
the resident-set interventions (`--batch-read`, `--skeleton-demote`, `--stable-prefix`) enabled.

## 1. Setup

### 1.1 Harness runs (real lead, real provider)

Three fresh non-interactive sessions of workload X3 (*read all 17 chapter READMEs and compare context
management, delegation and stopping; no delegation; no writes*) with `glm-5.3-flash` via z.ai,
labels `KV-base-50k-r1..r3`, traces in `s15_integrated_harness/traces/kv_splice/`. The driver gained
`--dump-requests`, which writes `<trace>.requests.jsonl`: the exact system prompt, tool list, message
list and full response of every lead model call, so the prompt the provider saw at every round can be
rebuilt byte for byte offline.

What an edit looks like in these runs (all three kinds occur):

| edit | mechanism (code.py) | what the splice must do |
|---|---|---|
| placeholder | `micro_compact`: once `json.dumps(messages)` > 50k chars, consumed tool results older than the 3 most recent are rewritten to the one-line spill marker until the size is below 40k | drop the result's KV, prefill ~60 tokens of marker, keep the KV of everything after it |
| snip | `snip_compact`: above 50 messages the middle of the list is replaced by `[N messages archived at <path>]`; the path changes every time it fires | drop a long span, prefill one marker, keep the tail |
| summary | `compact_history`: whole history replaced by a model-written summary | almost nothing survives; only the system prompt is reusable |

### 1.2 Offline replay on a local model

The provider does not expose its KV cache and this machine has no GPU, so the splice is tested on a
local open-weight model with plain RoPE: **Qwen2.5-1.5B-Instruct in fp32** on 64 CPU cores (one
NUMA node; 417 tok/s prefill, 84 ms/token decode at 12k context), with a confirmation subset on
Qwen2.5-7B-Instruct. Every dumped request is rendered into Qwen's own chat template (system prompt,
Hermes-style `<tool_call>` blocks, `<tool_response>` results; the renderer reproduces
`tokenizer.apply_chat_template` exactly) and tokenised segment by segment, so an edit changes exactly
the segments the harness rewrote. One normalisation: the harness stamps `Current time: <second>` into
the system prompt at every call, which would make *every* round an edit; the replay fixes that line.
Its cost on the provider side was measured separately (stable-prefix study).

Scripts: `scripts/kv_splice.py` (engine + `--selftest`), `scripts/kv_render.py`,
`scripts/kv_replay.py`, `scripts/kv_edit_stats.py`, `scripts/kv_analyze.py`.

**Cache policies compared at every lead call k** (the cache carried over from call k-1 already
contains the model's own response R_{k-1}; the harness then appends new tool results, the *tail*):

| policy | what is reused | positions |
|---|---|---|
| `recompute` | common prefix up to the first changed token; everything after it is recomputed (what providers do) | exact |
| `shift` | every block that survives the edit, spliced from the previous *spliced* cache (compounds across the run); placeholders and the tail are prefilled | stale keys rotated by R((m+d)θ)=R(dθ)R(mθ) to their compacted positions |
| `gap` | same blocks, keys left at their old positions; new tokens continue after the largest position | positions keep growing (evicted spans are never reclaimed) |
| `shift1` | like `shift` but from an exact cache, i.e. one edit of staleness | rotated |
| `oracle` | the prompt as it would be **if this step's edit had not happened**: previous prompt + tail, with the results this step evicted still present (earlier evictions stay applied, so it isolates one step's worth of eviction) | exact |

The engine's self-test checks that an identity splice is bit-exact, that rotating cached keys by d
equals recomputing them at shifted positions (max relative error 2e-6 over all layers), and that
values are position-free.

**Metrics.** Tokens each policy had to run. Over the real GLM response R_k, teacher-forced: mean KL of
each policy's next-token distribution from `recompute` and from `oracle`, top-1 agreement, and the
NLL GLM's actual tokens receive. Greedy next action (64 tokens) under each policy on edit steps,
classified by tool and by whether it re-fetches content evicted at this or any earlier step. Probes
about the largest result evicted at the step, asked as the next user turn ("earlier you saw file X;
without tools, list its headings / complete this sentence / continue this passage verbatim / does it
contain phrase P"): scored by the NLL per token of the true answer (works regardless of whether a
small model complies) and, for presence, by forced choice between yes and no with a balanced positive
and negative phrase. Cosine similarity between stale and recomputed K/V on the reused blocks, per layer.

## 2. What the real runs did at 50k

`scripts/ix_analyze.py traces/kv_splice` (same analyzer as the intervention report):

| run | rounds | model s | wall s | re-acquisition rounds | reads | uncached tok | cached tok | hit% | shrinks | summary compactions |
|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|
| KV-base-50k-r1 | 65 | 736 | 807 | 39 (60%) | 34 | 578,793 | 128,000 | 18 | 45 | 1 |
| KV-base-50k-r2 | 36 | 391 | 461 | 21 (58%) | 45 | 313,581 | 73,408 | 19 | 18 | 1 |
| KV-base-50k-r3 | 33 | 336 | 363 | 10 (30%) | 18 | 310,039 | 60,416 | 16 | 24 | 0 |

Re-acquisition breakdown: r1 = 70 greps on already-read READMEs, 14 re-reads of evicted READMEs,
6 spill-file reads; r2 = 16 spill-file reads, 12 evicted re-reads, 16 re-reads of still-live files;
r3 = 12 spill-file reads, 9 greps, 1 evicted re-read. All three covered 17/17 chapters. This matches
the earlier baseline@50k arm (median 69 rounds, 15% hit).

### 2.1 Where the edits sit and what a splice would save (tokenizer-only pass over the dumps)

`scripts/kv_edit_stats.py` diffs consecutive prompts at segment granularity (Qwen tokenizer as the
proxy tokenizer; on the 11 fully uncached lead calls GLM reported a median 0.925x of the Qwen count, so provider-side numbers are ~8% lower):

| run | steps | edit steps | placeholder / snip / summary edits | ctx tokens median (max) | tokens after first change on edit steps: median / sum | tail tokens (unavoidable new input) | tokens computed: recompute / splice | evicted tokens |
|---|---:|---:|---|---|---|---:|---|---:|
| r1 | 64 | 45 | 76 / 23 / 1 | 11,802 (14,758) | 7,151 / 317,140 | 110,523 | 354,938 / 127,314 (36%) | 118,874 |
| r2 | 35 | 17 | 29 / 6 / 1 | 12,465 (15,179) | 6,912 / 121,536 | 52,005 | 146,131 / 60,283 (41%) | 53,775 |
| r3 | 32 | 24 | 36 / 12 / 0 | 12,338 (14,376) | 8,310 / 196,124 | 58,708 | 210,736 / 65,964 (31%) | 59,265 |

Reading: on an edit step the first change sits roughly in the middle of a ~12k-token prompt, so
`recompute` re-runs a median 7-8k tokens; a splice runs only the markers plus the tail. Over a whole
run the splice computes 31-41% of the tokens `recompute` does (the rest of the difference is the tail,
which both must run). In provider-latency terms this is small: with the measured `glm-5.3-flash`
profile (4.6 s fixed + 0.052 ms per uncached token, and the 0.925 token-count ratio above) the
avoided re-prefill is 15 s in r1, 6 s in r2 and 9 s in r3, i.e. 1.3-2.6% of wall time (807, 461 and
363 s). The re-acquisition *rounds* (39, 21, 10 in
the three runs, ~5 s each) are the cost that matters, which is why sub-question 2 is the important one.

## 3. Replay results (Qwen2.5-1.5B, three runs replayed end to end)

The three runs give 117 lead calls, 69 of them edit steps (56 with placeholders, 24 with a snip
marker, 2 summary compactions; a step can carry several kinds). 135 tool results totalling 174k
tokens were evicted. Full per-run and pooled tables are in `traces/kv_splice/kv_splice_tables.md`;
the numbers below are pooled.

### 3.1 Cost: what the model has to run

| policy | tokens computed over the three runs | on edit steps | share of recompute |
|---|---:|---:|---:|
| recompute (what providers do) | 575,670 | 498,665 | 100% |
| shift / gap (splice) | 225,565 | 148,560 | 39% |
| shift1 (splice from an exact cache) | 148,560 | 148,560 | 26% |
| oracle (this step's eviction undone) | 123,494 | 123,494 | 21% |

Both policies must run the tail (199k tokens of genuinely new input); the difference is the
7,137-token median suffix that `recompute` re-runs on every edit step. The splice removes 61% of the
prefill tokens of these runs.

### 3.2 Fidelity: how far the next output moves

Teacher-forced over GLM's real response at every edit step (n = 69):

| policy | KL from recompute, nats/token (mean / median / p90 / max) | top-1 agreement with recompute | NLL of GLM's real tokens (recompute = 0.464) |
|---|---|---:|---:|
| shift (compounded, RoPE-corrected) | 0.045 / 0.032 / 0.092 / 0.29 | 96.8% | 0.471 (+1.5%) |
| shift1 (one edit of staleness) | 0.014 / 0.010 / 0.033 / 0.07 | 98.1% | 0.459 (-1.1%) |
| gap (positions not corrected) | 0.089 / 0.046 / 0.179 / 1.02 | 95.5% | 0.508 (+9.5%) |
| oracle (one step's eviction undone) | 0.046 / 0.014 / 0.093 / 0.75 | 97.0% | 0.476 (+2.6%) |

Four readings:

- **A stale suffix moves the distribution about as much as the eviction it accompanies.** The
  corrected splice sits 0.045 nats/token from a full re-prefill; undoing a single step's eviction
  (recompute vs oracle) moves it 0.046. Whatever error the splice introduces is the same order as an
  edit the harness already makes deliberately, and 96.8% of argmax decisions are unchanged.
- **Leaving RoPE uncorrected is not neutral.** `gap` keeps every stale key at its original position,
  so the counter grows with everything the session has ever seen rather than with what is resident:
  against 10-15k resident tokens the largest position reached 35.7k in r2 and 64.5k in r3 (r1's
  summary compaction reset it from 51k to 16k). Qwen2.5-1.5B is trained to 32k, and the divergence
  tail opens once past it: in r3 `gap` tracks `shift` for the first seven edits (KL 0.02-0.12) and
  then produces 0.67, 0.32 and 1.02 nats/token at edits 8-10. The correction is one rotation of the
  cached keys by R(dθ), costs nothing, and the self-test reproduces truly shifted keys to 2e-6.
- **Compounding is weak but not zero.** Per-run regressions of `shift` KL on the splice index give
  +0.0021 (r1, 28 edits), +0.0011 (r2, 17 edits) and -0.0004 (r3, 24 edits) nats/token per splice;
  pooled correlation with the splice index is r = 0.23. The one-shot `shift1` has a flat-to-negative
  slope in all three runs, so the drift is genuinely from repeated splicing rather than from later
  steps being intrinsically harder. It is not explained by how much of the prompt is stale (pooled
  r = -0.01 with the stale fraction; means by stale-fraction quartile 0.042 / 0.042 / 0.058 / 0.038).
  Over the 17-45 edits per run it stays around 0.03-0.08, but a much longer session would want a
  periodic full re-anchor; the natural place is the summary-compaction boundary, where the prompt is
  rebuilt anyway.
- **The first generated token is the most sensitive point.** Median first-token KL is 0.165 for
  `shift` and its argmax matches `recompute` in 87% of edit steps (91% for `shift1` and `oracle`).
  The disagreements are text-versus-`<tool_call>` flips at the very start of the response.

### 3.3 Behaviour: does the model act differently, and does it re-fetch less?

Greedy next action (up to 64 tokens) at each edit step, re-parsed from the stored text:

| policy | emits a tool call | same tool as recompute | identical text to recompute | re-fetches content evicted at this step | re-fetches any evicted content |
|---|---:|---:|---:|---:|---:|
| recompute | 78% | 100% | 100% | 3% | 29% |
| shift | 77% | 71% | 39% | 3% | 36% |
| gap | 75% | 75% | 35% | 4% | 30% |
| shift1 | 83% | 85% | 49% | 6% | 38% |
| oracle | 81% | 85% | 52% | 4% | 35% |

The splice changes the exact action text about 60% of the time and the tool about 29% of the time.
That is the same order as the difference between the compacted prompt and the uncompacted one
(`oracle`: 48% and 15%), so the action is perturbed, not derailed.

It does **not** reduce re-fetching. Content evicted at the current step is almost never re-fetched
immediately under any policy, and older evicted content is re-fetched slightly *more* often under the
spliced caches (36%) than under recompute (29%). The oracle row is the calibration: even with the
content present in the prompt this small model re-reads it in 35% of steps, so a single greedy action
is a noisy instrument. The probes below test retention directly.

### 3.4 Retention: does the stale suffix remember the evicted file?

For the largest result evicted at each edit step (55 items: chapter READMEs and `sed -n` windows of
them), four questions are asked as the next user turn and scored by the NLL per token the model
assigns to the *true* answer, plus a forced choice between "Yes" and "No" for phrase presence:

| probe (truth scored) | recompute | shift | gap | oracle | n |
|---|---:|---:|---:|---:|---:|
| list the file's headings | 3.56 | 3.66 | 3.68 | **0.75** | 55 |
| complete a sentence from the file | 4.56 | 4.61 | 4.63 | **0.78** | 55 |
| continue a passage verbatim (30 words) | 3.50 | 3.52 | 3.55 | **0.44** | 54 |
| phrase-presence forced choice, true phrase | 82% | 87% | 95% | 96% | 55 |
| phrase-presence forced choice, false phrase | 33% | 15% | 15% | 29% | 55 |
| balanced presence accuracy | 57% | 51% | 55% | 63% | |

With the file still in the prompt (oracle) the model reproduces headings, sentences and passages at
0.44-0.78 nats/token. With the placeholder (recompute) it is at the language-model prior, 3.5-4.6.
**The spliced caches are at the prior too** — in fact 0.02-0.12 nats/token *worse* than recompute on
every recall probe, never better. Presence looks better only on true phrases because the stale suffix
raises the log-odds of "Yes" by about 0.46 nats for true and false phrases alike: that is a yes-bias,
not knowledge, and balanced accuracy does not improve (51% for `shift` against 57% for recompute and
63% for oracle).

This is not an artefact of *where* the evicted blocks sit. `micro_compact` evicts the oldest consumed
results, so a probed block has a median 5.4-6.3k tokens of prompt after it, 46-47% of the request,
with a p10 of 2.4-3.5k. There is ample stale suffix that could have carried the content.

| run | evicted blocks > 800 chars | suffix tokens, all blocks (median / p10) | suffix tokens, probed block (median / min) | suffix share of prompt (median) |
|---|---:|---|---|---:|
| r1 | 57 | 5,498 / 2,366 | 5,411 / 754 | 47% |
| r2 | 38 | 5,676 / 3,030 | 5,676 / 1,112 | 46% |
| r3 | 33 | 6,303 / 3,541 | 6,303 / 2,940 | 46% |

The K/V vectors say why. On the blocks reused after an edit, the stale keys have cosine 0.987 to the
recomputed ones (values 0.963), layer 0 at 1.000 falling to 0.96-0.98 in layers 16-23, and only 1.6%
of tokens fall below 0.9. The suffix entries are *nearly the same vectors* the recomputed prompt
produces, which is another way of saying they barely encoded the evicted file in the first place: a
re-prefill without the file changes them by 1-4%. The information about a file lives in that file's
own KV entries, and the edit drops exactly those.

### 3.5 Confirmation on Qwen2.5-7B

Run r3 was replayed again on Qwen2.5-7B-Instruct (fp32, same four policies minus `shift1`), giving
32 steps and 23 edit steps. Everything qualitative reproduces; the splice error is larger on the
bigger model:

| metric (edit steps, n = 23) | Qwen2.5-1.5B (r3) | Qwen2.5-7B (r3) |
|---|---|---|
| shift: KL from recompute (mean / median / max) | 0.043 / 0.033 / 0.19 | 0.085 / 0.066 / 0.28 |
| shift: top-1 agreement with recompute | 97.4% | 96.3% |
| shift: NLL of GLM's real response vs recompute | +2% | +13% (0.451 vs 0.400) |
| oracle: KL from recompute (one eviction undone) | 0.055 | 0.043 |
| gap: KL from recompute (mean / max) | 0.147 / 1.02 | 0.168 / 0.78 |
| gap: NLL of real response vs recompute | +14% | +33% |
| stale vs recomputed keys: cosine (values) | 0.987 (0.963) | 0.970 (0.914) |
| tokens computed, splice vs recompute | 31% | 32% |

On the 7B model the compounded splice costs about twice what one step's eviction costs
(0.085 against 0.043) rather than matching it, its stale keys deviate more (7.3% of tokens below
cosine 0.9 against 1.6%), and the drift across a run is again mildly upward (0.057, 0.065, 0.086
nats/token for the first 3, next 7 and next 6 splices). The direction of every conclusion is
unchanged, but the size of the perturbation is model-dependent and grows with model size here, so a
production decision should be validated on the serving model rather than extrapolated from these.

Retention is, if anything, more clearly absent on the 7B: with the file evicted it scores the true
headings at 2.99 nats/token under `shift` against 2.72 under recompute and **0.33** under oracle;
verbatim continuation 3.65 / 3.48 / **0.28**; greedy verbatim overlap 0.14 / 0.14 / **0.74**. The
phrase-presence probe degenerates on this model (it answers "No" to essentially everything once the
file is gone: 100% correct on false phrases, 0% on true ones, under every policy including
recompute), which removes even the yes-bias the 1.5B showed.

One process note: the 7B run's output file was unlinked while the process held it open, so the
records were recovered from the live file descriptor. 32 of 33 steps were captured; the final step's
record was written after the last copy and is missing from the table above.

## 4. What this means for the harness

1. **Splicing with RoPE re-rotation is a legitimate serving optimisation, within limits.** At the
   50k budget it removes 61% of prefill tokens, keeps 96-97% of argmax decisions, and drifts only
   weakly over dozens of successive edits. Its perturbation is the same order as the compaction edit
   it is serving: 1.0x on Qwen2.5-1.5B (0.045 against 0.046 nats/token) and 2.0x on Qwen2.5-7B
   (0.085 against 0.043). That the ratio doubled with model size is the one result that should stop
   a production rollout from being decided here; it needs checking on the serving model. A provider
   that exposed "edit the prompt in place" could offer this, and the harness cannot reach it through
   the Messages API today.
2. **The RoPE correction is mandatory, not cosmetic.** Without it the position counter tracks the
   cumulative history rather than the resident context (64k positions against 12k resident tokens
   here), leaves the trained range, and the divergence tail reaches 1.0 nats/token.
3. **It saves FLOPs, not rounds.** The cost that dominates these runs is the re-acquisition round
   (39, 21 and 10 per run at roughly 5 s each), and the splice does not remove any of them: the
   stale suffix carries no usable trace of the evicted text, recall probes sit at the prior, and the
   model re-fetches as often as before. The latency saving is 1.3-2.6% of wall time.
4. **Where the information actually is.** The oracle rows show what having it looks like: verbatim
   recall at 0.44 nats/token instead of 3.5, and +6 points of balanced presence accuracy. That
   information sits in the evicted block's own KV entries, not in the entries that follow them. So
   the design that would remove zoom-in rounds is *keeping the evicted block's KV in a lower tier and
   re-attaching it when needed*, rather than re-reading the text through a tool call. Splicing is the
   mechanism that makes such a re-attach affordable, because the suffix does not have to be
   recomputed around the re-inserted block; on its own it is only a FLOP saver.

## 5. Limitations

- The provider model's cache is not observable, so all fidelity and retention numbers come from
  Qwen2.5-1.5B (three runs) and Qwen2.5-7B (one run) replaying GLM's transcripts; the real responses were produced by
  GLM under the `recompute` context, which is why NLL is reported relative to that policy.
- The replay fixes the system prompt's per-second timestamp; the harness itself still breaks the
  provider prefix every round (16-19% cache hit in these runs), a separate, already-measured cost.
- Behaviour is a single greedy action; the probes are structural/verbatim recall
  and yes/no phrase presence. A weaker "gist" retention that none of these detect cannot be
  excluded, but the presence probe would be the natural place for it to show, and it does not.
- The compounding slope is measured over 17-45 consecutive edits per run and two of the three runs
  summary-compacted mid-way, which partially rebuilds the cache. It bounds drift over a session of
  this length, not over an arbitrarily long one.
- One workload (X3), three runs, one budget (50k chars, ~12k tokens), fp32 CPU replay.

## 6. Full analyzer output

Per-run and pooled tables for every metric above are in
`traces/kv_splice/kv_splice_tables.md`, regenerated with:

```
python3 s15_integrated_harness/scripts/kv_analyze.py \
    s15_integrated_harness/traces/kv_splice/*.replay.jsonl --tables > \
    s15_integrated_harness/traces/kv_splice/kv_splice_tables.md
```

## 7. Reproduction

```
# harness runs (three lanes, baseline harness, 50k chars, request dumps)
python3 s15_integrated_harness/scripts/profile_run.py --label KV-base-50k-r1 --prompt "<X3 prompt>" \
    --context-limit 50000 --dump-requests --max-seconds 1800 --trace-dir s15_integrated_harness/traces/kv_splice
# engine self-test, edit statistics, replay, tables  (venv /home/yq335/kv_env: torch 2.14 cpu, transformers 5.17)
OMP_NUM_THREADS=64 numactl --cpunodebind=0 --membind=0 python3 s15_integrated_harness/scripts/kv_splice.py --selftest
python3 s15_integrated_harness/scripts/kv_edit_stats.py traces/kv_splice/*.requests.jsonl
OMP_NUM_THREADS=64 numactl --cpunodebind=0 --membind=0 python3 s15_integrated_harness/scripts/kv_replay.py \
    --requests traces/kv_splice/<run>.requests.jsonl --out traces/kv_splice/<label>.qwen1.5b.replay.jsonl --threads 64
python3 s15_integrated_harness/scripts/kv_analyze.py traces/kv_splice/*.replay.jsonl --tables
```
