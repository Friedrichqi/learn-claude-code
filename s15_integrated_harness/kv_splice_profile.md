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

The three runs give 99 lead calls, 57 of them edit steps (45 with placeholders, 21 with a snip
marker, 2 summary compactions; several steps carry more than one kind). Per-run and pooled tables
are reproduced in full in section 6; the numbers quoted here are the pooled ones.

### 3.1 Cost: what the model has to run

| policy | tokens computed over the three runs | on edit steps | share of recompute |
|---|---:|---:|---:|
| recompute (provider today) | 493k | 424k | 100% |
| shift / gap (splice) | 192k | 123k | 39% |
| oracle (nothing evicted) | 102k | 102k | 21% |

Both policies must run the tail (170k tokens of new tool results and messages); the difference is
the 7.6k-token median suffix that `recompute` re-runs on every edit step. The splice removes 61% of
the prefill tokens of these runs, which at the provider profile of section 2.1 is worth 1.3-2.6% of
wall time.

### 3.2 Fidelity: how far the next output moves

Teacher-forced over GLM's real response at every edit step (57 steps):

| policy | KL from recompute, nats/token (mean / median / p90 / max) | top-1 agreement with recompute | NLL of GLM's real tokens (recompute = 0.446) |
|---|---|---:|---:|
| shift (compounded, RoPE-corrected) | 0.037 / 0.028 / 0.073 / 0.19 | 97.3% | 0.456 (+2%) |
| shift1 (one edit of staleness) | 0.014 / 0.009 / 0.033 / 0.07 | 98.3% | 0.441 (-1%) |
| gap (positions not corrected) | 0.086 / 0.044 / 0.162 / 1.02 | 96.1% | 0.503 (+13%) |
| oracle (one step's eviction undone, for scale) | 0.050 / 0.013 / 0.097 / 0.75 | 97.1% | 0.464 (+4%) |

Three readings:

- **The stale suffix moves the distribution about as much as the eviction it accompanies.** The
  corrected splice sits 0.037 nats/token from what a full re-prefill would produce; undoing a single
  step's eviction (recompute vs oracle) moves it 0.050. Per step, the real response's NLL
  under `shift` is within 1% of `recompute` at the median and within 13-32% at p90 (per run).
- **Staleness does not accumulate.** Bucketing edit steps by how many splices the cache has already
  absorbed gives mean KL 0.044 (1-3 splices), 0.033 (4-10), 0.038 (11-20), 0.035 (21+). Run r3 is the
  clean test: it never summary-compacted, so its cache absorbs 24 consecutive splices with no reset.
  Regressing its per-step KL on the splice index gives a slope of **-0.0004 nats/token per splice**
  (mean 0.043; first five edits 0.056, last five 0.030). Each splice adds a one-off perturbation and
  the compounded cache settles at about 3.6x the one-shot value (`shift` 0.043 vs `shift1` 0.012)
  rather than growing with the number of edits.
- **Leaving RoPE uncorrected is not neutral.** `gap` keeps every stale key at its original position,
  so the position counter grows with everything the session has ever seen rather than with what is
  resident: with 10-15k tokens resident, the largest position reached 35.7k in r2 and 64.5k in r3
  (r1's summary compaction reset it from 51k to 16k). Qwen2.5-1.5B is trained to 32k positions, and
  once past it the divergence tail opens (p90 0.16, max 1.02 nats/token; NLL +13%). The correction is
  a rotation of the cached keys by R(dθ) and costs nothing; the self-test shows it reproduces truly
  shifted keys to 2e-6. The damage is concentrated exactly where the positions run away: in r3,
  `gap` stays near `shift` for the first seven edits (KL 0.02-0.12, positions below 32k) and then
  produces 0.67, 0.32 and 1.01 nats/token at edits 8-10, once the counter passes the trained range.

The first generated token has the same argmax as `recompute` in 95% of edit steps under `shift`
(91% under `gap`); the disagreements are text-vs-`<tool_call>` flips.

### 3.3 Behaviour: does the model act differently, and does it re-fetch less?

Greedy next action (up to 64 tokens) at each edit step, re-parsed from the stored text:

| policy | emits a tool call | same tool as recompute | identical text to recompute | re-fetches the content evicted at this step | re-fetches any evicted content |
|---|---:|---:|---:|---:|---:|
| recompute | 83% | 100% | 100% | 3% | 28% |
| shift | 86% | 76% | 47% | 3% | 38% |
| gap | 84% | 81% | 40% | 5% | 31% |
| shift1 | 88% | 86% | 55% | 7% | 34% |
| oracle | 86% | 86% | 57% | 5% | 31% |

The splice changes the exact action text about half the time and the tool one time in four, which is
the same order as the gap between the compacted prompt and the uncompacted one (`oracle`: 57% and
86%). It does **not** reduce re-fetching. Content evicted at the current step is almost never
re-fetched immediately by any policy, and older evicted content is re-fetched *more* often under the
spliced caches (38%) than under recompute (28%). The oracle row is the calibration: even with the
content present in context, this small model re-reads it in 31% of steps, so the immediate next
action is a noisy instrument; the probes below are the direct test.

### 3.4 Retention: does the stale suffix remember the evicted file?

For the largest result evicted at each edit step (44 items with enough text: READMEs and `sed -n`
windows of READMEs), four questions are asked as the next user turn and scored by the NLL per token
the model assigns to the *true* answer, plus a forced yes/no choice for phrase presence:

| probe (truth scored) | recompute | shift | gap | oracle | n |
|---|---:|---:|---:|---:|---:|
| list the file's headings | 3.51 | 3.63 | 3.65 | **0.82** | 44 |
| complete a sentence from the file | 4.57 | 4.63 | 4.66 | **0.85** | 44 |
| continue a passage verbatim (30 words) | 3.40 | 3.42 | 3.46 | **0.48** | 43 |
| phrase present, forced choice correct (true phrase) | 82% | 89% | 93% | 95% | 44 |
| phrase present, forced choice correct (false phrase) | 27% | 14% | 16% | 27% | 44 |
| balanced presence accuracy | 55% | 51% | 55% | 61% | |

When the file is in context (oracle) the model reproduces headings, sentences and passages at
0.5-0.9 nats/token; with the placeholder (recompute) it is at the language-model prior, 3.4-4.6.
The spliced caches are **at the prior too**, in fact 0.03-0.14 nats/token *worse* than recompute on
every recall probe. Presence looks better only on true phrases: the stale suffix raises the log-odds
of "yes" by about 0.5 nats for true *and* false phrases alike, so it adds a yes-bias, not knowledge;
balanced accuracy is unchanged at 51-55% (oracle 61%).

This is not an artefact of *where* the evicted blocks sit. `micro_compact` evicts the oldest
consumed results, so a probed block has a median 5.4-6.3k tokens of prompt after it, 46-47% of the
request; the p10 is still 2.4-3.5k. There is ample stale suffix that could have carried the content.

| run | evicted blocks > 800 chars | suffix tokens, all blocks (median / p10) | suffix tokens, probed block (median / min) | suffix share of prompt (median) |
|---|---:|---|---|---:|
| r1 | 57 | 5,498 / 2,366 | 5,411 / 754 | 47% |
| r2 | 38 | 5,676 / 3,030 | 5,676 / 1,112 | 46% |
| r3 | 33 | 6,303 / 3,541 | 6,303 / 2,940 | 46% |

Mechanistically this is what the K/V vectors say. On the reused blocks after an edit the stale keys
have cosine 0.987 to the recomputed ones (values 0.963), with layer 0 at 1.000 falling to 0.96-0.98
in layers 16-23; only 1.5% of tokens fall below 0.9. The suffix entries are *almost the same* vectors
the recomputed prompt produces, i.e. they encode the file's presence weakly enough that a
re-prefill without the file barely changes them. The information about the file lived in the
file's own KV entries, which the edit drops.

### 3.5 Confirmation on Qwen2.5-7B

_(pending: replay of r3 with `recompute, shift, gap, oracle`)_

## 4. What this means for the harness

1. **Splicing with RoPE re-rotation is a legitimate serving optimisation.** At the 50k budget it
   removes ~60% of prefill tokens, perturbs the next-token distribution by ~0.03-0.04 nats/token
   (no worse than the compaction edit itself), does not compound over a session, and keeps 97% of
   argmax decisions. A provider that exposed "edit the prompt in place" could use it; the harness
   cannot do it through the Messages API today.
2. **The RoPE correction is mandatory, not cosmetic.** Without it the position counter tracks the
   cumulative history (64k positions for 12k resident tokens here) and leaves the trained range.
3. **It does not buy back the rounds.** The cost that dominates these runs is the re-acquisition
   round (39 / 21 / 10 per run at ~5 s each, section 2), and the stale suffix carries no usable
   trace of the evicted text: recall probes are at the prior, and the model re-fetches as often as
   before. The latency saving is 1.3-2.6% of wall time.
4. **Where the information is.** The oracle rows show what recovering it looks like (0.5 nats/token
   verbatim recall, +6 points on presence). That information sits in the evicted block's own KV
   entries, not in what follows them. The design that would remove zoom-in rounds is therefore
   *keep the evicted block's KV resident in a lower tier and re-attach it when it is needed*, rather
   than re-reading the text through a tool call; splicing is the mechanism that makes such a
   re-attach cheap (the suffix does not have to be recomputed), but on its own it is a FLOP saver.

## 5. Limitations

- The provider model's cache is not observable, so all fidelity and retention numbers come from
  Qwen2.5-1.5B (and the 7B subset) replaying GLM's transcripts; the real responses were produced by
  GLM under the `recompute` context, which is why NLL is reported relative to that policy.
- The replay fixes the system prompt's per-second timestamp; the harness itself still breaks the
  provider prefix every round (16-19% cache hit in these runs), a separate, already-measured cost.
- Behaviour is a single greedy action from a 1.5B model; the probes are structural/verbatim recall
  and yes/no phrase presence. A weaker "gist" retention that none of these detect cannot be
  excluded, but the presence probe would be the natural place for it to show, and it does not.
- One workload (X3), three runs, one budget (50k chars, ~12k tokens), fp32 CPU replay.

## 6. Full analyzer output

_(regenerated with `python3 scripts/kv_analyze.py traces/kv_splice/*.replay.jsonl --tables`)_

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
