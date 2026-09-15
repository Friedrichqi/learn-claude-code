# Exposing measured cost to the model: does the harness's own price list change tool choice?

2026-09-14. Full report: `s15_integrated_harness/tool_cost_profile.md`. Tables:
`s15_integrated_harness/traces/tool_cost/tool_cost_tables.md`. Page: https://claude.ai/code/artifact/6de7f01b-27ae-4151-911f-42293a286e98
Provider z.ai, `glm-5.3-flash`.

## 1. Why

Everything this series has priced is invisible to the agent that causes it. A round costs 3.72 s
fixed plus 0.038 ms per uncached prompt token plus 17.4 ms per output token; a lead turn ends with
13-21 s of memory extraction; teammates re-send 33-86% of their input. The harness knows all of it.
The tool descriptions the model reads say what a tool does and never what it costs.

If the harness published that price list -- in the tool descriptions, or as a cost table in the
system prompt, or as the hardware facts behind it (a working set staged from DRAM across PCIe versus
one already resident in HBM) -- would the model route around the expensive tools? If yes, the
cheapest cost-aware scheduler is a paragraph of prompt. If no, scheduling stays in the harness.

## 2. What was run

Three stages, 893 single-shot trials + 80 multi-round runs + 24 full s15 sessions.

1. **Single-shot probes** (`scripts/tool_cost_probe.py`), one model call per trial, outcome = the
   tool named in the first `tool_use` block. Probe A: two tools described as interchangeable,
   differing only in advertised cost, crossed over framing (stated seconds / hardware facts only /
   the words fast and slow) and placement (tool description / system-prompt cost table / both), with
   the expensive label and the list order counterbalanced. Probe B: the same, sweeping the gap from
   a tie to 7500x. Probe C: a realistic pool (`read_file`, `grep`, `glob`, `bash`,
   `recall_summary`) and a real question, `read_file` annotated at 0.05/5/60 s, plus a placebo arm
   that annotates `grep` instead. Probe D: probe C plus a one-line cost-aware policy, and in one
   cell a hard budget.
2. **Multi-round loop** (`scripts/tool_cost_loop.py`), tools really execute against repository
   files, 2x2 over advertised cost and a really enforced 5 s sleep, four gradeable questions.
3. **The real s15 harness** (`scripts/tool_cost_harness.py`), via a new
   `profile_run.py --tool-cost` intervention that rewrites the `tools` argument of every outgoing
   request, so lead, teammates and subagents are annotated in one place. Two workloads, three arms.

## 3. Findings

**Short answer: telling the model that a tool is slow does not stop it from using that tool, as long
as that tool is the right one for the job.** The same picture came out of all three stages.

- **When the model needs a specific tool, it ignores the price tag.** We labelled the file-reading
  tool as costing anywhere from a twentieth of a second to a full minute, in three different
  wordings. Its usage did not change. The model's own reasoning shows why: it almost never brought
  the cost up at all. It picked the tool that fit the task and moved on.

- **When two tools do the same thing, the price tag decides.** We offered two file readers described
  as identical except for cost. With no cost shown, the model had a strong habit of picking one
  particular name. Once a cost was shown, it picked the cheaper one nearly every time and the habit
  vanished. This worked whether the cost was written in seconds, described only as hardware (a
  working set either sitting in HBM or staged in from DRAM over PCIe, leaving the model to do the
  division), or written as the bare words "fast" and "slow". It also did not matter whether the cost
  sat in the tool description or in a table in the system prompt.

- **The model treats cost as a tiebreaker, not a budget.** It does not weigh how much slower one
  option is. A tool five times slower and a tool thousands of times slower were avoided at the same
  rate. When the two costs were equal, the model went back to picking at random. So published costs
  only need to be in the right order. They do not need to be accurate, and an agent that "respects"
  them is not thereby scheduling well.

- **What changes behaviour is being told to care.** Adding one sentence to the prompt, "Tool calls
  are not free. Prefer the cheapest tool that can still answer the question correctly", cut file
  reads in a realistic tool pool from about a third of first moves to under a tenth. The cost table
  on its own did nothing. Adding a hard latency budget on top did not change the choice further, but
  it did make the model talk about the cost in most of its replies. The budget makes it reason about
  the number; the sentence makes it act.

- **In the real harness there was nothing to fix.** When the questions asked for specific values,
  the lead already answered with a quick grep through the shell and never opened the file, with or
  without the labels, in all twelve runs. When the questions asked it to explain what a document
  says, it read the whole file every time, again with or without the labels and even when told to
  prefer the cheapest tool. That second choice is correct. You cannot summarise a document from a
  handful of matching lines.

- **The labels are almost free to send, and cost most when they are used.** Annotating the real s15
  tool pool adds about 230 tokens, all in the stable prefix, so it is cached after the first call.
  The policy sentence adds a couple of dozen more. The real cost shows up on the output side and only
  when the model actually uses the labels: it narrates the comparison, and replies grow by 18 to 31
  tokens, which is a third to half a second of decode per call. When it ignores the labels, reply
  length barely moves.

Two cautions for reading the numbers. One stage-2 arm made the file reader genuinely sleep for five
seconds without telling the model. A model cannot see a clock, so any difference in that arm is
pure noise, and it came out at about 15 percentage points. Nothing smaller than that can be trusted
in the stage-2 and stage-3 tables. And everything was measured on one model from one provider. The
finding that the model ignores costs in a realistic pool is the one most worth checking on a
different model, which takes about 15 minutes with the scripts as they stand.

The numbers behind the bullets, for reference:

| what was measured | without cost shown | with cost shown |
|---|---|---|
| two interchangeable tools: chose the cheaper one | 53% | 97-100% in all nine cells, p = 7e-06 |
| the same pair: chose the habitually preferred name | 91% | 50% |
| dose sweep, 5x to 7500x gap: chose the cheaper one | 47% at a tie | 100% at every gap |
| realistic pool: first move is `read_file` | 44% | 45% over 160 trials, p = 1.000 |
| realistic pool: reply mentions cost | 0% | 0-3% (interchangeable pair: 75-100%) |
| realistic pool + policy sentence: first move is `read_file` | 31% | 9%, p = 0.0098 |
| stage 2, 80 real runs: used `read_file` | 33 of 40 | 32 of 40, p = 1.000 |
| stage 3, s15 value-lookup workload: `read_file` calls per run | 0 in 4 runs | 0 in 8 runs |
| stage 3, s15 summary workload: `read_file` calls per run | 1 in 4 runs, 12.5 KB | 1 in 8 runs, 12.5 KB |
| token cost of annotating the s15 pool | 6,103 chars of tools | 7,013 chars, about 230 tokens |

## 4. Consequence for the argument

Cost-aware routing stays in the harness. The costs this project has priced at each tier boundary
cannot be handed to the model by writing them into the prompt, because in a realistic pool the model
picks on task fit and never looks at the price. The levers remain the ones already identified on
the harness side: batching reads, demoting outlines, keeping a stable prefix, and taking memory
extraction off the critical path.

There is one exception, and it is the tiered-memory case exactly. Exposing costs to the model works
when the pool deliberately offers two routes to the same bytes, a hot one and a cold one, because
those are genuine substitutes. That is the shape a tiered-memory tool pool would have. There, one
sentence in a tool description moved the model from a coin flip to the cheap route every time, for
nine tokens and no loss of accuracy. A prototype that exposes a resident and a staged route to the
same working set should publish the costs, keep them in the right order rather than precise, and
say alongside them that cheaper is preferred.

## 5. Next

* Re-run stage 1 against a second model. It is 15 minutes for 900 trials, and finding 4 is the one
  claim here that could be model-specific.
* Make the enforced cost proportional to bytes moved rather than flat per call, which would also
  reward reading less of a file rather than not reading it (`read_file` with offset/limit) -- the
  behaviour a real tier boundary should incentivise.
* When the shared-file-block or KV-re-attach prototype exists, give it two advertised routes to the
  same bytes and measure whether finding 1 survives outside a synthetic pool.
