# Hand-off to topic 21: a refined CPU-work line (2026-10-30, from the topic 19 KV-threshold `wait` follow-up)

**Context:**
- At 4:50 PM EDT on 10-30 the user asked for two experiments:
  1. near 80-90 % KV, prompt MiniMax-M2.7 to start CPU-heavy tool work so the GPU server catches up;
  2. a `wait` prompt that MiniMax acts on near 80 %.
- **Who does what:** experiment 1 is topic 21's study, and the user chose that topic 21's session builds and runs
  the refined line. This session runs only experiment 2.
- **Why a file:** topic 21's sessions had exited (stale socket), so this note replaces the message.

## Why topic 21's line F was ignored

Topic 21's line F reads: "Capacity is short and your next call will wait. Put any long-running commands that are
ready (tests, builds, installs, downloads, searches) in this response."

- **The model rarely reads it:**
  - only 105 of 2,416 F samples (4 %) mention it in their thinking, and that is a generous keyword match;
  - the median thinking is 126 characters.
- **No number and no standing rule:** F shows no number and states no standing rule. Topic 19's `wait` texts failed
  the same way: 3 waits in 21,081 calls, with a status line that never showed the KV percentage.
- **The result** (`research/21_precautionary_work/data/tables/probe_main.md`): valid early issue on the ≥ 10 s
  pairs:

  | condition | rate |
  |---|---|
  | F | 9.9 % |
  | N | 4.4 % |
  | C | 5.3 % |

  F − N is +5.4 points [+1.5, +10.8]. H1 needs ≥ 15, so it fails, and so does the probe part of G2.

## The suggested refinement (parallel to topic 19's `wait` prompt)

1. **Show the number** with the line `hold_runtime.kv_line` writes: `[server] KV cache 93% full; 12 requests
   waiting`.
2. **A standing rule** in the system prompt, with an explicit 80 % threshold (draft below).
3. **A cue on the line** that names the action, at ≥ 80 % only (draft below).
4. **Two levels with the same text:** 62 % and 93 %. That tests "acts above" and "refrains below".
5. **Reuse:**
   - the frozen items (`data/probe/items.json`);
   - `pw_probe.score_request`, `boot` and `ProbeRunner`;
   - the stored F / N / C samples.

   N stays the reference for the +15-point H1 bar, so the result compares directly with F.

**Draft rule paragraph** (appended to the system prompt): "Shared server: you share one model server with other
sessions. When its KV-cache memory is full, your next model call waits until memory frees up. Each round ends with a
[server] line that shows how full the KV cache is. Server rule: when that line shows 80% or more, put the
long-running commands you will need and can already run (tests, builds, installs, downloads) into this response, next
to your other tool calls, so they run while the server catches up. Never start a command that needs results you do
not have yet. Below 80%, work as usual."

**Draft cue** (after the line, at ≥ 80 % only): "At 80% or more: put the long-running commands that are ready
(tests, builds, installs, downloads) in this response."

**Cost:**
- The system-prompt rule changes the prefix of the 60-70k-token prompts. Each request's pair of levels then needs one
  full prefill: about 302 × 65k ≈ 20M tokens, roughly 15-20 min at about 20k tok/s.
- A line-only cue stays prefix-cached.
- At S = 8 with 16 in flight, topic 21's probe filled the 277,440-token KV pool.

## Inputs from topic 19's run

`ef_kvwait.py`'s screen compares, for the `wait` rule:
- `T`: the KV line only;
- `R`: the rule in the tool text and system paragraph;
- `C`: the line cue only;
- `RC`: both.

It shows which placement MiniMax acts on. Results: `research/19_efficiency_signals/data/tables/kvwait.md`, from
about 22:30 UTC on 10-30.

**The outcome** (screen done 21:39 UTC; the README's "A KV-threshold `wait` prompt" section):
- No placement reached the bar. At 93 % KV, the work-reply wait rate was:

  | condition | rate | samples with `wait` |
  |---|---|---|
  | `C` (the line cue) | 4 % | 11 of 240 |
  | `RC` (rule + cue) | 2 % | 12 of 240 |
  | `R` (the rule) | 1 % | |
  | `T` (the KV line) | 0 % | |
- The thinking almost never mentions the line.
- The replayed history mattered:
  - with 1-5 earlier ignored lines at ≥ 80 %, `RC` waits 11 % of the time; with more than 20, 0 %;
  - at turn starts, `RC` waits 17 %.
- **For the CPU line:** expect the same weak reading. Topic 20's trials carry no earlier `[server]` lines, so that
  part of the confound is absent there.

**The revised screen** (2026-10-31, `ef_kvwait2.py`) caps the earlier lines at 75 %, so no earlier reply ignored the
rule:
- **The rule then works as a sharp threshold:** 58 % of work replies wait at 93 % and 0 % at 62 %, against 3 % with
  the history as recorded. The thinking mentions the line in 57 % of samples.
- **The worked example (v6) adds nothing.**
- **The form fails:**
  - 41 % of the work-reply waits replace the work call;
  - most final answers become a wait-only reply.
- **Lesson for the CPU line:**
  - an explicit numeric rule on the line, plus a standing rule in the system prompt, can move MiniMax a lot when the
    context does not show the rule being ignored;
  - check the form (does it add the command, or replace its current step?) as well as the rate.

## Coordination

- **GPUs 0-3:** topic 19's stage `kvwait` (`ef_kvserve.py`) held them 21:15-21:39 UTC on 10-30. They are empty
  again.
- **`s15_integrated_harness/hold_runtime.py`:** the kvwait probe is complete, so it is no longer held for it. If
  topic 19 runs a revised screen, its new pre-registration hashes the file as it is then.
