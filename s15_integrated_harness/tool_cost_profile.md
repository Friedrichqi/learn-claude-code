# Exposing hardware cost to the harness: does an advertised tool latency change the tool_use the model generates?

Run 2026-09-14. Provider z.ai, model `glm-5.3-flash`, the same endpoint as every other measurement
in this series.

## 0. Summary

**Telling the model that a tool is slow does not stop it from using that tool, as long as that tool
is the right one for the job.** Asked in its original form -- if `read_file` is advertised at 5 s,
does the model still generate `read_file` tool_use? -- the answer is yes, at the same rate. Labelled
at anything from a twentieth of a second to a full minute, in three wordings, the file reader kept
its 44-45% share of first moves over 160 trials, and the model's own reasoning almost never
mentioned the cost at all. It picked the tool that fit the task and moved on.

The same model is not blind to the numbers. Given two tools described as identical except for cost,
it chose the cheaper one 97-100% of the time against a 53% baseline, wiping out a strong habit of
picking one particular name. That held whether the cost was written in seconds, described only as
hardware and left for the model to work out, or written as the words "fast" and "slow", and whether
it sat in the tool description or the system prompt. But it is a tiebreaker, not a budget: a tool
five times slower and a tool thousands of times slower were avoided at the same rate, and an equal
cost returned the model to a coin flip.

What changes behaviour in a realistic pool is being told to care. One added sentence -- "Tool calls
are not free. Prefer the cheapest tool that can still answer the question correctly" -- cut file
reads from about a third of first moves to under a tenth. The cost table on its own moved nothing.

In the real s15 harness there was nothing to fix in either direction. On questions asking for
specific values the lead already answered with a grep through the shell and never opened the file,
in 12 runs of 12, with or without the labels. On questions asking what a document says it read the
whole file in 12 runs of 12, again with or without the labels, which is correct: a handful of
matching lines cannot summarise a document.

Practical consequence: cost-aware routing stays in the harness. Publishing measured costs to the
model is nearly free -- about 230 tokens in the stable prefix, cached after the first call -- and
buys nothing on its own. It pays only in a pool that deliberately offers a hot and a cold route to
the same bytes, which is the shape a tiered-memory tool pool would have, and only if the harness
also says that cheaper is preferred.

## 1. The question

Every cost this project has measured is invisible to the agent that causes it. The latency
breakdown (`latency_profile.md`, `weekly_progress/091626/latency_breakdown.md`) prices an agentic
round at 3.72 s of fixed provider time, 0.033-0.042 ms per uncached prompt token, 17.4 ms per
output token, and 13-21 s of turn-end memory extraction. The byte-level redundancy work prices the
same file content being re-sent 33-86% of the time. The harness knows all of this. The model does
not: a tool description says what a tool does and never what it costs.

So: if the harness published its own measured costs -- or the hardware facts behind them, such as
the DRAM-to-HBM staging latency of the data a tool must touch -- in the tool descriptions or in the
system prompt, would the model route around the expensive tools? Put in the concrete form the
question was asked in: if `read_file` is advertised at 5 s per call, does the model still generate
`read_file` tool_use blocks?

The answer has a direct design consequence. If advertised cost steers tool choice, a harness can
publish a live cost table and let the model do its own scheduling, which is cheaper than building a
cost-aware planner into the harness. If it does not, cost-aware routing has to stay in the harness,
where it already is.

## 2. Design

Three stages, from a fully controlled single-shot choice to the real s15 harness. Each stage
answers a different objection to the one before it.

### 2.1 Stage 1: single-shot probes (`scripts/tool_cost_probe.py`)

One model call per trial, no tool execution; the outcome is the tool named in the first `tool_use`
block. Four probes:

**A. Discrimination.** Two tools, `read_via_alpha` and `read_via_beta`, described as interchangeable
("both return the same bytes for the same path"), differing only in advertised cost. This is the
most favourable case possible: choosing the cheap one costs nothing, because the tools are
identical. Crossed factors:

* *framing* -- how the cost is worded. `seconds` states it ("Measured latency: 5.00 s per call");
  `hardware` states only the hardware and leaves the division to the model (a 128 GiB working set
  either resident in HBM3 at 3.35 TB/s, giving 0.04 s, or staged across PCIe 4.0 x16 at 25 GB/s,
  giving 5.5 s); `qualitative` says only "This channel is fast" / "This channel is slow". All three
  describe the same ~125x ratio.
* *placement* -- in each tool's `description`, in a cost table appended to the system prompt, or
  both.
* *baseline* -- no cost shown anywhere.

Two nuisance variables are counterbalanced in balanced blocks of four: which of the two names
carries the expensive label, and the order of the two tools in the `tools` array. Both matter; the
baseline cell measures how much.

**B. Dose-response.** Probe A with the cheap channel pinned at 0.04 s and the expensive one swept
over 0.04 (a tie), 0.2, 1, 5, 30 and 300 s. How large must the advertised gap be before the model
acts on it, and does a larger gap produce a stronger preference?

**C. Substitution.** A realistic pool -- `read_file`, `grep`, `glob`, `bash`, `recall_summary` --
and a real question about a real repository file ("what does GLOSSARY.md say 'compaction' means?").
Here the tools are *not* interchangeable: `grep` returns only matching lines and `recall_summary`
returns a deliberately vague cached blurb, so the cheap routes trade information for speed.
`read_file` is annotated at 0.05, 5 or 60 s. A placebo arm annotates `grep` at 5 s instead, so a
drop in `read_file` can be told apart from "something in this prompt is expensive".

**D. Information or objective?** The same pool and question as C, but the cost table is accompanied
by a one-line policy ("Tool calls are not free. Prefer the cheapest tool that can still answer the
question correctly") and, in one cell, a hard budget ("You have a total tool-latency budget of
1.00 s for this task"). If C is null, this separates "the model was not told the cost" from "the
model was not told to care".

### 2.2 Stage 2: multi-round loop with real execution (`scripts/tool_cost_loop.py`)

The tools actually run against real repository files, so a route has consequences: rounds, bytes
pulled into context, wall time, and whether the answer is right. A 2x2 factorial separates the
advertised cost from the real one, which only the harness can tell apart:

| arm | `read_file` advertised at 5 s | `read_file` really sleeps 5 s |
|---|---|---|
| `plain` | no | no |
| `advertise` | yes | no |
| `enforce` | no | yes |
| `both` | yes | yes |

`advertise` minus `plain` is the effect of the text alone -- nothing is actually slower, so any
change in the route is caused by the description. `both` minus `enforce` is the payoff: the seconds
that telling the agent about a real cost actually saves. `advertise` minus `plain` on correctness
is the risk, because the cheap routes here are lossy.

Four questions with exact gradeable answers, chosen so the whole-file read and the targeted search
both work but differ by two orders of magnitude in returned bytes (GLOSSARY.md is 12.8 KB, code.py
is 146 KB). T4 is the accuracy-risk task: its answer needs the whole Compaction entry, so the cheap
routes are genuinely insufficient.

### 2.3 Stage 3: the real s15 harness (`scripts/tool_cost_harness.py`)

`profile_run.py` gained a `--tool-cost` intervention: it rewrites the `tools` argument of every
outgoing request, so the lead, the teammates and the one-shot subagents are annotated in one place
and the auxiliary calls that carry no tools (memory extraction, summarisation) are left alone.
Nothing else about s15 changes: the lead keeps its full pool, and in particular it keeps `bash`,
so it can run `grep` itself if it wants to avoid an expensive `read_file`.

## 3. Results

Full tables: `traces/tool_cost/tool_cost_tables.md` (regenerate with `scripts/tool_cost_analyze.py`).
Percentages carry 95% Wilson intervals; `p` is a two-sided Fisher exact test. Stage 1 is 893
successful trials of 928 attempted (35 lost to provider rate limiting and re-run afterwards in
balanced top-up, so every cell holds 30-32 trials).

### 3.1 Between interchangeable tools the advertised cost decides everything

| framing | placement | chose the cheap channel | chose `read_via_alpha` | cost mentioned in the reply |
|---|---|---|---|---|
| none (baseline) | -- | 53% [36-69] | 91% [76-97] | 0% |
| seconds | tool description | 100% [89-100] | 50% | 100% |
| seconds | system prompt | 100% [89-100] | 50% | 100% |
| hardware | tool description | 97% [84-99] | 53% | 75% |
| hardware | system prompt | 100% [89-100] | 50% | 100% |
| qualitative | tool description | 100% [89-100] | 50% | 100% |
| qualitative | system prompt | 100% [89-100] | 50% | 100% |

Every annotated cell is at 97-100% against a 53% baseline, p = 7.1e-06 in eight of the nine cells
and 7.7e-05 in the ninth. Three things follow.

*The cost overrides a strong prior.* Unannotated, the model picks `read_via_alpha` 91% of the time
-- it is simply biased towards that name. Annotated, `alpha` is chosen exactly 50% of the time,
which is the rate the counterbalancing produces when the choice is made purely on cost. The
advertised number does not nudge the prior, it replaces it.

*The model will do the arithmetic.* The `hardware` framing never states a latency: it gives a
working-set size and two bandwidths. The model divides and acts on the result, at 97-100%, the same
as when it is handed the seconds. Its reasoning shows the derivation ("Alpha is faster (HBM3
resident, 3.35 TB/s) vs beta (PCIe staging)").

*Placement does not matter.* Tool description, system-prompt cost table and both together are
indistinguishable. The question of where to put the information has no measurable answer, because
the information is not what is scarce.

### 3.2 There is no dose-response: this is a tie-break, not a calculation

| advertised cost of the expensive channel | ratio to the cheap one | chose the cheap channel |
|---|---|---|
| 0.04 s | 1x (a tie) | 47% [31-64] |
| 0.2 s | 5x | 100% [89-100] |
| 1 s | 25x | 100% [89-100] |
| 5 s | 125x | 100% [89-100] |
| 30 s | 750x | 100% [89-100] |
| 300 s | 7500x | 100% [89-100] |

A genuine tie returns the model to chance, which confirms the outcome measures cost and nothing
else. Every non-tie is saturated: a 5x gap of 0.16 s produces the same 100% as a 7500x gap of
299.96 s. The data are completely separated, so no threshold is estimable; it lies at or below 5x.
The model is not weighing a cost against a benefit. It is applying a rule -- when two options are
otherwise equal, take the one labelled cheaper -- and the size of the label is irrelevant.

### 3.3 Between tools that differ in function, the advertised cost does nothing

Probe C replaces the two interchangeable channels with a realistic pool and a real question.

| annotation on `read_file` | first move is `read_file` | p vs baseline |
|---|---|---|
| none | 44% [28-61] | -- |
| 0.05 s | 53% [36-69] | 0.617 |
| 5 s | 44% [28-61] | 1.000 |
| 60 s | 34% [20-52] | 0.609 |
| 5 s, hardware framing | 59% [42-74] | 0.317 |
| 5 s, qualitative framing | 34% [20-52] | 0.609 |
| (placebo: 5 s on `grep` instead) | 62% [45-77] | 0.210 |

Pooled over all five `read_file` annotations, 45% [37-53] of 160 trials against 44% of 32
unannotated trials, p = 1.000. A 60-second advertised latency on the tool the task obviously needs
moves its selection rate by less than the noise between two samples of the same condition (the
probe C and probe D baselines are the identical prompt run at different times and differ by 12
percentage points, p = 0.44).

The reasoning traces say why, and the contrast with probe A is total. In probe A the model
mentioned the cost in 75-100% of trials. In probe C it mentions it in **0-3%**. It does not weigh
the cost and decide to pay it; it never raises the subject. A representative trace from the 60 s
cell reads in full: "The user wants to know what the glossary says about 'compaction'. Let me read
that file." The tool is chosen on fit, and once one tool fits, the cost table is not consulted.

Stage 2 reproduces this where the tools really run and the route has consequences. Across 80
multi-round runs, `read_file` was used in 32 of 40 runs that were told it costs 5 s and 33 of 40
that were not (p = 1.000); per task the split is 10/10 vs 9/10, 2/10 vs 4/10, 10/10 vs 10/10 and
10/10 vs 10/10. Rounds (2.6-2.8), median bytes pulled into context (1.2-1.5 KB) and correctness
(95-100%) are flat across all four arms.

Stage 2 also carries its own noise gauge. The `enforce` arm makes `read_file` really sleep 5 s
without telling the model, and a model cannot perceive wall-clock time inside a request, so any
`plain`-to-`enforce` difference must be noise. It is 90% versus 75%, a 15-point gap -- larger than
any difference the annotation produced. That is the scale at which this outcome can be read.

One caveat against over-reading a tail: the mean bytes returned in the `plain` arm is 10.0 KB
against 3.3-4.9 KB in the others, but that is one single run that pulled all 143 KB of `code.py`
into context. The medians are identical. With one run it is an anecdote, not an effect.

### 3.4 What changes behaviour is an objective, not information

Probe D holds the pool, the question and the cost table fixed and adds a one-line policy.

| cost table | policy line | hard budget | first move is `read_file` | cost mentioned | p vs "nothing said" |
|---|---|---|---|---|---|
| no | no | no | 31% [18-49] | 0% | -- |
| **yes** | no | no | 32% [19-50] | 3% | 1.000 |
| no | **yes** | no | 17% [7-34] | 0% | 0.240 |
| **yes** | **yes** | no | 9% [3-24] | 16% | 0.060 |
| **yes** | **yes** | **yes** | 9% [3-24] | 69% | 0.060 |

Publishing the cost table alone moves the selection rate by one point. Adding "Tool calls are not
free. Prefer the cheapest tool that can still answer the question correctly" takes it from 31% to
9%; pooling the two cells that carry both the table and the policy gives 6 of 64 against 10 of 32,
p = 0.0098. The hard budget changes the choice no further but changes the reasoning: mentions of
cost jump from 16% to 69%, so the budget is what makes the model reason about the number out loud,
while the policy is what makes it act.

The asymmetry is the finding. The model was never short of information -- in probe A it used the
same numbers perfectly. What it lacked in probe C was a reason to trade correctness for speed, and
one sentence supplies it.

### 3.5 What the intervention costs to run

Annotating the two-tool pool adds, per request: 12 tokens for the qualitative wording, 18 for the
seconds wording, 84 for the hardware wording. At the measured 0.038 ms per uncached prompt token
that is 0.5-3.2 ms of prefill, and it sits in the stable prefix, so after the first call it is
served from the prompt cache.

The larger cost is on the output side, and only appears when the annotation is actually used. In
probe A the mean reply grows from 53 output tokens unannotated to 71 with seconds and 84 with the
hardware framing, because the model narrates the comparison and, for the hardware framing, does the
division. At the measured 17.4 ms per output token that is 0.30 s and 0.53 s of extra decode per
call. In probe C, where the model ignores the cost, output tokens barely move (50 to 50-62).

So the intervention is close to free when it is ignored, costs about half a second of decode per
call when it is used, and the hardware framing is the most expensive way to say the same thing.

### 3.6 In the real s15 harness the intervention has no room in either direction

Two workloads, three arms (`plain`, `advertise` = `read_file` at 5.00 s and everything else at
0.05 s, `policy` = the same table plus the cost-aware sentence), four runs each, run interleaved.

| workload | arm | rounds | `read_file` calls | `bash` calls | KB read | wall s | coverage |
|---|---|---|---|---|---|---|---|
| constants | plain | 3.5 | 0.00 | 2.00 | 0.0 | 39.0 | 1.00 |
| constants | advertise | 3.0 | 0.00 | 1.50 | 0.0 | 38.5 | 1.00 |
| constants | policy | 3.2 | 0.00 | 2.00 | 0.0 | 39.0 | 1.00 |
| summary | plain | 3.2 | 1.00 | 0.25 | 12.5 | 44.5 | 0.88 |
| summary | advertise | 3.2 | 1.00 | 0.25 | 12.5 | 44.2 | 0.69 |
| summary | policy | 3.2 | 1.00 | 0.25 | 12.5 | 42.1 | 0.90 |

Neither workload moves at all, and the two fail in opposite directions.

On `constants` -- three questions with specific values as answers -- the lead answered with `bash`
and a grep-shaped command in 12 runs out of 12, in every arm, and never called `read_file` once.
The unannotated harness was already taking the cheap route, so there was nothing for an advertised
cost to remove. This is the floor that motivated the second workload.

On `summary` -- explain what three glossary entries say -- the lead called `read_file` exactly once
and pulled all 12.5 KB of the file in 12 runs out of 12, in every arm, including the arm that was
told the call costs 5 s and instructed to prefer the cheapest tool that still works. This is a
ceiling, and it is the correct behaviour: `grep` returns matching lines, and no set of matching
lines answers "explain what this entry says". The model declined to trade the answer for the
latency, which is what the policy sentence asked for ("the cheapest tool that can still complete the
step correctly").

The coverage column varies (0.54 to 1.00) but not with the arm. It is graded by looking for the
five literal stage identifiers, and a prompt that says "in your own words" sometimes gets
paraphrase instead; every missing item in every run is one of those five names. The route --
one `read_file`, 12.5 KB, three to four rounds -- is identical across all twelve runs.

Together with stage 2 this bounds where the effect found in probes A and D can live. It needs a
pool where a cheaper route exists *and* can actually do the job. On the two real workloads tested,
the harness either already took the cheap route without being told, or the cheap route could not
answer the question.

The annotation's footprint in the real pool: the s15 tool definitions grow from 6,103 to 7,013
characters (+910, about 230 tokens, 8.7 ms of prefill at 0.038 ms/token, cached after the first
call), and the policy sentence adds 95 characters to the system prompt.

## 4. What this means for the harness

**Publishing a cost table is not a scheduling mechanism.** The result that matters is the gap
between probe A and probe C. Given two tools that do the same thing, the advertised cost decides
the choice completely, overriding a 91% name prior, in every framing and at every magnitude above a
5x ratio. Given a pool of tools that do different things, the same advertised cost changes nothing
and is not even mentioned in the model's reasoning. Real harness tool pools are the second kind.
`read_file`, `grep` and `bash` are not substitutes from the model's point of view: they answer
different questions, and the model picks on fit.

**Cost information is cheap; a cost objective is what is scarce.** Annotating the real s15 pool
costs 910 characters of tool definitions (about 230 tokens, 8.7 ms of prefill at the measured
0.038 ms/token) and it sits in the stable prefix, so it is cached after the first call. One extra
sentence of policy costs 95 characters. The sentence is what moves behaviour: cost table alone
31% -> 32%, cost table plus policy 31% -> 9% (p = 0.0098). If a harness wants cost-aware routing
from the model, it has to state the objective, not just the numbers.

**The model applies a rule, it does not optimise.** Probe B shows a 5x advertised gap and a 7500x
advertised gap produce identical behaviour, and a genuine tie returns the model to chance. There is
no cost-benefit arithmetic happening, so the numbers a harness publishes do not need to be
accurate; they need to be ordered. That is convenient for implementation and a warning for
interpretation: an agent that "respects" a published cost is not thereby making good scheduling
decisions, and cannot be expected to trade a 60 s tool against an answer that is 10% better.

**Where this leaves the tiered-memory argument.** The costs this project has priced -- prefill per
uncached token, decode per output token, the fixed per-round provider cost, memory extraction at the
turn boundary -- cannot be delegated to the model by writing them into the prompt. In a realistic
pool, the model routes on task fit and ignores the price. Cost-aware routing therefore stays where
it already is, in the harness: batching reads, demoting outlines, keeping a stable prefix, taking
memory extraction off the critical path. The one place model-side cost exposure does pay is a pool
that deliberately contains substitutable tiers -- a hot and a cold route to the same bytes -- which
is exactly the shape a tiered-memory tool pool would have. There, one sentence in the description
moves the model from 53% to 100%.

## 5. Threats to validity

* **One model, one provider.** Everything here is `glm-5.3-flash` on z.ai. The size of the probe A
  effect is unlikely to be model-specific (it is a trivial discrimination), but the probe C null is
  a claim about how a particular model weighs task fit against a stated cost, and a model trained
  with more explicit budget-following could behave differently. The stage-1 probes are cheap
  (about 15 minutes for 900 trials) and are the right thing to re-run against another model.
* **Stage 1 is single-shot.** It measures the first `tool_use` block, not a whole trajectory.
  Stage 2 and stage 3 cover the multi-round case and agree with it.
* **Stage 2 and 3 are small.** 20 runs per stage-2 arm and 4 per stage-3 arm. The stage-2 negative
  control quantifies what that buys: the `plain`-to-`enforce` difference, which must be noise
  because a model cannot perceive an injected sleep, is 15 percentage points. The stage-2 system
  prompt carries no clock and the tool results carry no timing, so there is genuinely no channel
  through which the sleep could reach the model (the s15 lead prompt does carry a `Current time`
  line, but stage 3 has no enforce arm). Differences smaller
  than that cannot be read, and none of the annotation effects in stage 2 or 3 exceed it.
* **Stage-1 cells lost 3.8% of trials to provider rate limiting.** Losses were spread over a
  shuffled schedule and the missing (cell, rep) pairs were re-run afterwards, so the cells are
  balanced; but the re-run trials were sent later than the rest.
* **Probe D and the C top-up ran while stage 2 was running**, so all three share provider load.
  Stage 2's run order is shuffled across arms, which protects the between-arm contrasts, but its
  absolute wall times are measured under variable load and should not be compared with the wall
  times in `latency_profile.md`.
* **The stage-3 `constants` workload has a floor.** The lead answered it with `bash` and grep in
  all 12 runs, in all three arms, so there was no `read_file` for the intervention to remove. The
  `summary` workload was added for exactly this reason.
* **The enforced delay is a flat 5 s per call.** A real tier boundary costs time in proportion to
  bytes moved, which would also reward reading less of a file rather than not reading it. That
  variant is not tested here.
* **`advertised_bill_s` in stage 2 is a counterfactual for the unannotated arms**: it prices the
  route the agent actually took using the same cost model, whether or not the agent was shown it.
  It measures the policy, not what the agent was charged.

## 6. Reproduction

```
# stage 1: about 900 single-shot trials, about 15 minutes at concurrency 8
python3 s15_integrated_harness/scripts/tool_cost_probe.py --probe all --reps 32 \
    --concurrency 8 --out s15_integrated_harness/traces/tool_cost/stage1.jsonl
# re-run whatever the provider rate-limited, keeping cells balanced
python3 s15_integrated_harness/scripts/tool_cost_probe.py --probe all --reps 32 \
    --topup --out s15_integrated_harness/traces/tool_cost/stage1.jsonl
# see what any cell actually sends
python3 s15_integrated_harness/scripts/tool_cost_probe.py --probe D --reps 1 --dry-run

# stage 2: 80 multi-round runs with real tool execution
python3 s15_integrated_harness/scripts/tool_cost_loop.py --reps 5 --concurrency 4 \
    --out s15_integrated_harness/traces/tool_cost/stage2.jsonl

# stage 3: the real harness, sequential (each run wipes .memory/.tasks/... at the repository root)
python3 s15_integrated_harness/scripts/tool_cost_harness.py --reps 4 --workload constants
python3 s15_integrated_harness/scripts/tool_cost_harness.py --reps 4 --workload summary

# tables
python3 s15_integrated_harness/scripts/tool_cost_analyze.py \
    --stage1 s15_integrated_harness/traces/tool_cost/stage1.jsonl \
    --stage2 s15_integrated_harness/traces/tool_cost/stage2.jsonl \
    --md s15_integrated_harness/traces/tool_cost/tool_cost_tables.md \
    --json s15_integrated_harness/traces/tool_cost/tool_cost_tables.json
```

The intervention itself is `profile_run.py --tool-cost 'read_file=5.0' --tool-cost-default 0.05
[--tool-cost-framing seconds|qualitative|hardware] [--tool-cost-placement tool_desc|system_prompt|both]
[--tool-cost-policy]`. It rewrites the `tools` argument of every outgoing request rather than the
harness's tool tables, so the lead, the teammates and the one-shot subagents are covered in one
place and the auxiliary calls that carry no tools are left alone; `profile_meta` records the
settings, and the existing `tools_chars`/`system_chars` fields of the inputs sidecar measure what
the annotation added. With no `--tool-cost` flag the request is untouched, so the driver's default
behaviour is unchanged (the diff is additive).

The results also exist as a page: https://claude.ai/code/artifact/6de7f01b-27ae-4151-911f-42293a286e98

Artefacts: traces, logs and per-run JSON in `s15_integrated_harness/traces/tool_cost/`
(`stage1.jsonl`, `stage2.jsonl`, `harness/` with one console log and trace per stage-3 run,
`tool_cost_tables.md` and `.json`).
