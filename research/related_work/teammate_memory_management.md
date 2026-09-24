# Teammate memory management: how a long-lived teammate carries context across tasks

Weekly progress, 2026-09-16. Session "teammate Memory Management", 2026-09-11 to 2026-09-12.
Companion notes: `research/02_input_redundancy/README.md` Part B (was `teammate_input_redundancy.md` in
this folder; its conclusions 4 and 6 rest on "teammates never compact") and
`research/03_context_interventions/README.md` (was `s15_integrated_harness/context_intervention_profile.md`), whose
section "Where the s15 cascade sits among the compaction choices" was written in this session
(commit `64d1b11`). This note is self-contained: it records the question, the evidence, the
compaction ladder, the s15 mapping, the decision rule, the repo changes and the session log.

## 1. Question

s15 teammates are spawned by the lead and stay alive across tasks on the board. When a teammate
claims a brand-new task that has nothing to do with the task it finished a few minutes earlier, what
does the real Claude Code do with the conversation history, the tool results and the KV cache of the
earlier tasks? Does it keep everything unchanged and append the new task at the end, or does it
partition or reset per task?

Sub-questions: (a) what a task boundary looks like inside a teammate; (b) what ever removes an
earlier task's tool results; (c) what happens to the prompt cache across the idle gap between tasks;
(d) which compaction choices exist at all, and where the s15 cascade sits among them.

## 2. Method and sources

Web research on 2026-09-11, three kinds of source in decreasing authority:

1. Claude Code documentation (agent teams, subagents, prompt caching, context window, model
   configuration, costs, hooks) and Claude API documentation (compaction, context editing), all
   current as of the research date.
2. Published analyses of the Claude Code source that leaked through the npm source map of v2.1.88
   on 2026-03-31: Claude Code from Source ch. 10, Claude Reviews Claude ch. 8 and 11, Finisky's
   five-layer cascade, markdown.engineering lesson 22, and a swarm-orchestration gist. The leaked
   source itself was not read; every code identifier below is quoted from these analyses.
3. GitHub issue #23620 (agent team lost when the lead's context is compacted) and community
   write-ups (claudecodecamp, alexop.dev) for observed behaviour.

Cross-checked against the s15 code in `s15_integrated_harness/code.py`: the teammate loop
(`spawn_teammate_thread` / `run_loop`, `claim_next_task`, `IDLE_SCAN_INTERVAL = 2.0`) and the lead's
`prepare_context` cascade (`tool_result_budget`, `snip_compact`, `micro_compact`,
`fit_tool_results`, `compact_history`, `reactive_compact`; `CONTEXT_LIMIT` = 128,000 tokens x 4
chars = 512,000 chars; `KEEP_RECENT_TOOL_RESULTS = 3`).

## 3. Findings: the Claude Code teammate model

Short answer: yes. A teammate is one long-lived Claude Code session with one transcript. A new task
is appended to the end of it as the next user turn. Nothing is partitioned or reset per task. Its
context shrinks only through the same compaction cascade a main session has, and that cascade is
not task-aware. The intended way to a clean context is to shut the teammate down and spawn a fresh
one.

### 3.1 A task boundary inside a teammate

- **Finishing.** The teammate ends its turn, sends its final answer to the lead as an idle
  notification, writes `isActive: false` to the team config and goes idle. Idle means no API
  calls: "An idle teammate is not consuming tokens or API calls - it is simply waiting for the next
  message." Community traces show idle pings to the lead every 2-4 s, and over half the lead's
  inbox is such pings. "Idle is not dead": the lead must not shut an idle teammate down or react
  with alarm.
- **Starting the next task.** It wakes on a mailbox message (a JSON file at
  `~/.claude/teams/{team}/inboxes/{agent}.json`) from the lead or another teammate, or it
  self-claims the next unassigned, unblocked task from the shared list (claims are file-locked).
  Incoming messages sit in a `pendingMessages` queue and are "drained at tool-round boundaries,
  which preserves the agent's turn structure", i.e. they become the next user turn on the same
  conversation. The `TeammateIdle` hook works the same way: exit code 2 "prevents the teammate from
  going idle and wakes it", with the hook's feedback injected into that session.
- **No hidden truncation.** The in-process teammate state has a `messages` field "Capped at 50",
  but "the agent's actual conversation continues with full history; only the UI-facing snapshot is
  truncated". The cap exists because each in-process agent held ~20 MB of RSS at 500+ turns and one
  session spawned 292 agents in 2 minutes, reaching 36.8 GB. A teammate that has exited is
  resurrected with its full JSONL transcript when messaged: `resumeAgentBackground()` reconstructs
  the history, "rebuilds the content replacement state for prompt cache stability" and calls
  `runAgent()` with the restored history plus the new message as prompt.
- **Two levels of interruption.** `currentWorkAbortController` cancels the current turn without
  killing the teammate (the lead's "redirect" pattern: send a higher-priority message, the current
  work is aborted, the teammate picks up the new message); the main abort controller kills it;
  `shutdownRequested` is cooperative, and the teammate may reject a shutdown with a reason.
- **What it starts with.** CLAUDE.md, MCP servers, skills and the spawn prompt. "The lead's
  conversation history does not carry over" ("strip parent messages - teammates start with an empty
  conversation"). Its plain-text output is invisible to the team; only `SendMessage` reaches anyone.

### 3.2 What removes an earlier task's tool results

Nothing task-aware. The subagents documentation: "Subagents support automatic compaction using the
same logic as the main conversation. Compaction triggers under the same conditions", logged in the
transcript as a `compact_boundary` record with `preTokens`. A teammate is the same machinery
launched with a name while agent teams are enabled. So an earlier task's file reads stay in context
verbatim until a size or time trigger fires (section 4 lists them), regardless of their relevance to
the current task. Resumed agents "retain their full conversation history, including all previous
tool calls, results, and reasoning".

### 3.3 KV cache across the idle gap

- Append-only keeps the prefix cache-valid while the cache is warm. Claude Code orders every
  request as system prompt, project context, conversation, and "on a normal turn, the prefix is the
  entire previous request and only the latest exchange is new".
- In-process teammates fall in the "everything else" TTL bucket: five minutes by default,
  "including on a Claude subscription"; `subagentPromptCacheTtl: "1h"` raises it, and 1-hour cache
  writes bill at a higher rate.
- A teammate idle longer than the TTL pays a full cache write on the first request of the next
  task. That is exactly when Claude Code's time-based microcompact clears the teammate's old tool
  results: the rebuild is sunk, so the clearing is free. The `/usage` cache line counts "compaction
  or tool-result clearing" as an "expected rebuild".
- The public context-editing API invalidates the prefix from the first cleared block. The leaked
  code has a `cache_edits` path that deletes old results from the server-side cached copy without
  invalidating the prefix, but one analysis marks it Anthropic-internal, so the shipped build should
  be assumed to rebuild whenever it clears.
- Compaction itself is cache-aware: the summariser is a fork with the same system prompt, tools and
  history plus the instruction appended, so while the cache is warm it reads the prefix from cache
  and pays mostly for the summary. Afterwards the system layer stays cached and only the short
  summary is re-cached. `/rewind` is cheaper still: it truncates to a prefix that is already cached.

### 3.4 Design guidance in Anthropic's own prompts and docs

- Costs page: "Shut down teammates when their work is done. Each active teammate continues
  consuming tokens until it exits or the session ends"; "Keep spawn prompts focused ... everything
  in the spawn prompt adds to their context from the start."
- The coordinator system prompt's "continue-vs-spawn decision", as quoted by the source analysis.
  High overlap, same files: continue, "the worker already has the file contents in its context".
  Low overlap, different domain: spawn fresh, "a worker that just investigated the authentication
  system carries 20,000 tokens of auth-specific context that is dead weight for a CSS refactoring
  task". A failed worker: spawn fresh with explicit guidance about what went wrong. A follow-up that
  needs the worker's own output: continue, with the output in the message.
- Known hazard, issue #23620 (Claude Code 2.1.34, still open): after the **lead** auto-compacts it
  forgets the team exists although the team config and task list are still on disk; the proposed
  fix is to re-inject team state after compaction the way CLAUDE.md is.

## 4. The compaction choices

The choices form a ladder from cheap and lossless to expensive and lossy. They differ on three axes:
what is removed (tool results only, or the whole history), who removes it (client or server) and
whether the cached prompt prefix survives.

[Ladder table moved in the 2026-09-23 cleanup: these ten rows were byte-identical to the table in `research/03_context_interventions/README.md`, section "Where the s15 cascade sits among the compaction choices", which is now the only copy.]

Availability. The cached microcompact is marked Anthropic-internal in the source analyses, and the
`/usage` cache line in the shipped build counts tool-result clearing as an "expected rebuild", so the
public build should be assumed to rebuild the prefix whenever it clears. Session-memory compaction is
experimental. The server-side compaction API is a public beta on Opus 4.6 and later, Sonnet 4.6 and 5
and the Fable and Mythos models; on Fable 5.1, thinking blocks before the compaction block are not
carried forward. Both API-side options apply only when the harness talks to the Claude API; the
z.ai endpoint used for the s15 runs offers neither, so for s15 they are design references, not
drop-in tools.

## 5. Where s15 sits

### 5.1 The lead's cascade, rung by rung

The lead runs `prepare_context` before every model call: `tool_result_budget`, then `snip_compact`,
then, only above `CONTEXT_LIMIT`, `micro_compact` to 80% of the limit, then `fit_tool_results`, then
`compact_history`. Teammates get none of it.

- `tool_result_budget` is the persist-to-disk rung with the same 200k aggregate cap, applied to the
  current turn's results, largest first (`persist_large_output` leaves a preview and the path).
- `snip_compact` has no counterpart. Claude Code's 50-entry cap is a UI snapshot; the model's
  transcript keeps full history. In s15 the rung fires above 50 messages, keeps 3 head messages and
  the tail, archives the middle to `.transcripts/` behind a marker message, and rewrites the middle
  every time it fires. That is why PF-S-c512-r1 shrank its history 7x with zero summary compactions
  and the lead's cache-hit rate fell to 4-23% (redundancy note, section 5). The low-water-mark snip
  of the stable-prefix intervention is the fix for this rung.
- `micro_compact` is the time-based microcompact (`KEEP_RECENT_TOOL_RESULTS = 3`; results over 120
  chars become `[Earlier tool result saved at ...]`), except that it fires on size rather than on a
  cold cache, so every firing is a paid rebuild instead of a free one. The outline intervention
  replaces its placeholder; the stable-prefix intervention batches its firings.
- `fit_tool_results` (largest first, 1,000-char preview) has no counterpart; the nearest is the
  preview left behind by the persist rung.
- `compact_history` is a full compaction whose summariser gets a different system prompt and the
  first 80,000 chars of the JSON history (2,000 output tokens), so it cannot read the warm prefix
  the way Claude Code's fork does, and it restores nothing afterwards: no files, skills, plan or
  memory come back. The result is a single `[Compacted]` user message carrying the authoritative
  request and the summary as untrusted reference state.
- `reactive_compact` is the prompt-too-long path: it summarises everything but the last 5 messages
  in a single attempt, where Claude Code drops the oldest rounds and retries the same summarisation
  up to 3 times, then stops auto-compacting after 3 consecutive failures.

### 5.2 The teammate loop

`run_loop` keeps one `messages` list for the thread's lifetime. It starts with the spawn prompt plus
an `[Assigned task ...]` block, calls the model with its own ten tools (`max_tokens=8000`), executes
tool calls, and when a turn ends with text sends that text to the lead as a `result`, releases the
assignment, marks itself idle and sends "Waiting for more work." as an `idle_notification`. It then
scans its inbox every 2 s; a message becomes a `[Message from ...]` user turn, and if none arrives
the runtime itself calls `claim_next_task` and appends `[Auto-claimed task ...]` with the task's
work directory. No compaction of any kind touches these messages.

This is the append-only half of the Claude Code design without the cascade, and it explains the
redundancy note's conclusion 4: the team ends a run holding 2-5 resident copies of the same file.
Four differences from Claude Code matter:

1. Claude Code teammates compact and microcompact; s15 teammates never do.
2. Cache lifetime is the same order: the provider's ~5 min idle lifetime measured in the
   redundancy note (conclusion 5) matches Claude Code's default 5-minute teammate TTL, so the
   6-16 min second waves would be cold-cache restarts under Claude Code too.
3. Claude Code's lead is prompted with the continue-vs-spawn rule; the s15 lead has no respawn
   rule, so a teammate carries every earlier task forever.
4. In Claude Code the self-claim is a model action (`TaskList` / `TaskUpdate` tool calls, costing a
   turn); in s15 the runtime claims on the model's behalf while idle, which is free but gives the
   model no say in whether its context suits the task.

### 5.3 The three interventions, as points on the ladder

From `context_intervention_profile.md` (workload X3, 17 chapter READMEs, glm-5.3-flash):

- **Stable prefix** (`--stable-prefix`, n=3 at 50k): cache hit 15% -> 60%, uncached prompt tokens
  633k -> 219k, rewrite events 60 -> 15, latency tail collapsed, round median unchanged, but the
  worst answer quality of any arm (median 14/20 against baseline 18). It is the "batch the
  deletions" rung done by evicting more.
- **Batching** (`--batch-read`): bounded by residency, not by the tool. At 100k, 134 -> 56 rounds and
  964 -> 600 s with 17/17 chapters; at 50k only 10-61% of requested files were delivered and
  coverage collapsed to 2/17.
- **Outline demotion** (`--skeleton-demote`): fires and is used (offset-targeted re-reads 0% ->
  13-27%, best answers, median 19/20) but costs 856 chars per evicted slot against 248 for the bare
  placeholder, 34% of a 50k budget, causing 4 summary compactions against 0-1 and 129 rounds
  against 69. A richer marker is a "representation" cost that competes with the payload.

## 6. Decision rule

1. Keep content out of the prefix in the first place: persist large results at creation, delegate
   verbose work to a subagent, respawn a teammate for an unrelated task.
2. When the history must be edited in place, edit when the cache is already cold or batch the
   deletions (a low-water mark, `clear_at_least`) so that each rebuild is amortised. This is what
   the stable-prefix arm measured: 4x the cache hit rate from turning many small rewrites into few
   large ones.
3. Summarise last. When summarising, share the warm prefix by appending the instruction to the same
   system prompt and history, and restore the working set afterwards (recent files, plan, memory),
   which is the half of full compaction that `compact_history` lacks.
4. Placeholders are budgeted like content: the ~860-byte outline against a ~250-byte pointer took a
   third of the budget and forced summary compactions, so any richer marker needs its own budget
   line.
5. For teammates, the cheapest addition is a time-based microcompact at task boundaries when the
   idle gap exceeded the provider's cache lifetime, plus a respawn rule for low-overlap tasks. No
   compaction rung removes duplicates across contexts, so the shared, position-stable file block
   from the redundancy note stays the primary remedy for cross-teammate redundancy; compaction is
   the complementary control on each teammate's own growth.

## 7. Changes made in this session

- `s15_integrated_harness/context_intervention_profile.md`: new section "Where the s15 cascade sits
  among the compaction choices" between Conclusions and Threats to validity (the ladder table, the
  availability caveats, the rung-by-rung mapping of section 5.1 with intervention numbers, the
  decision rule with the measured numbers, sources). Committed on main in `64d1b11` ("no-use: three
  experimental optimization with claude", 2026-09-11 16:58) and fast-forwarded into
  `claude_develop`.
- `weekly_progress/091626/teammate_input_redundancy.md`: a section "7. Compaction choices and where
  s15 sits" (the same table plus three teammate implications) was appended on 2026-09-11 and
  validated, but the working tree was reset to `d67ab7f` at 16:54 before the commit, so the section
  was discarded and the note is back at 239 lines. Its content is sections 4-6 of this note; whether
  to re-add it there is an open choice. [cleanup note 2026-09-23: that note is now Part B of
  `research/02_input_redundancy/README.md`; the ladder is kept once, in
  `research/03_context_interventions/README.md`.]
- Claude memory notes outside the repo: `claude-code-teammate-context-model.md` (new) and an update
  to `s15-context-interventions.md`.

## 8. Open items

1. Add a time-based microcompact to the s15 teammate loop, fired at a task boundary when the idle
   gap exceeded the provider's cache lifetime; measure resident copies and cache writes against the
   redundancy note's PF-S and DC-S runs.
2. Give the lead a continue-vs-spawn rule (respawn when the new task's file overlap with the
   teammate's resident set is low) and measure the same two quantities.
3. Make `compact_history` cache-sharing (same system prompt, instruction appended) and restore the
   working set afterwards (recent files, plan); compare against the current single-message summary
   on the X3 workload.
4. Replace `snip_compact`'s rolling rewrite with the low-water-mark variant on its own, separating
   it from the other two stable-prefix sub-changes, since the arm's quality loss has not yet been
   attributed to one of the three.
5. Decide whether section 7 of `teammate_input_redundancy.md` should be restored from this note.
   [cleanup note 2026-09-23: see the note at the end of section 7.]

## 9. Session log

1. **2026-09-11, question.** How does a Claude Code teammate handle conversation history, tool
   results and KV cache across unrelated task-board tasks; does it just append? Researched the
   docs, the source analyses and issue #23620, and read the s15 teammate loop for comparison.
   Answer: append-only single session, same compaction cascade, 5-minute teammate TTL, respawn for
   a clean context (section 3). Saved a memory note.
2. **Follow-up: "Conclude the different compact choices".** Checked the Claude API compaction and
   context-editing pages and the Claude Code context-window and model-config pages, read the s15
   cascade functions, and produced the ladder, the s15 mapping and the decision rule (sections 4-6).
3. **Follow-up: "drop it to both".** Inserted the section into the report and appended section 7 to
   the redundancy note; both tables validated at ten rows by five columns. Later that afternoon the
   tree was reset and the report committed, which kept the report section and dropped the note's
   (section 7 above).
4. **2026-09-12.** Wrote this note.

## Sources

All read 2026-09-11.

- Claude Code docs: [agent teams](https://code.claude.com/docs/en/agent-teams),
  [subagents](https://code.claude.com/docs/en/sub-agents),
  [prompt caching](https://code.claude.com/docs/en/prompt-caching),
  [context window, what survives compaction](https://code.claude.com/docs/en/context-window),
  [model configuration, auto-compact window](https://code.claude.com/docs/en/model-config),
  [costs](https://code.claude.com/docs/en/costs), [hooks](https://code.claude.com/docs/en/hooks)
- Claude API docs: [compaction](https://platform.claude.com/docs/en/build-with-claude/compaction),
  [context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing)
- Anthropic blog: [Lessons from building Claude Code: prompt caching is everything](https://claude.com/blog/lessons-from-building-claude-code-prompt-caching-is-everything)
- Analyses of the leaked v2.1.88 source (the source itself was not read):
  [Claude Code from Source, ch. 10: tasks, coordination and swarms](https://claude-code-from-source.com/ch10-coordination/),
  [Claude Reviews Claude, ch. 8: agent swarms](https://openedclaude.github.io/claude-reviews-claude/chapters/08-agent-swarms),
  [Claude Reviews Claude, ch. 11: the compaction system](https://openedclaude.github.io/claude-reviews-claude/chapters/11-compact-system),
  [Context Compaction in Claude Code: a five-layer cascade](https://finisky.github.io/en/claude-code-context-compaction/),
  [mdENG lesson 22: teams and swarms](https://www.markdown.engineering/learn-claude-code/22-teams-swarm),
  [Claude Code swarm orchestration skill (gist)](https://gist.github.com/kieranklaassen/4f2aba89594a4aea4ad64d753984b2ea)
- Observed behaviour: [issue #23620, agent team lost when the lead's context is compacted](https://github.com/anthropics/claude-code/issues/23620),
  [Claude Code agent teams: how they work under the hood](https://www.claudecodecamp.com/p/claude-code-agent-teams-how-they-work-under-the-hood),
  [From tasks to swarms: agent teams in Claude Code](https://alexop.dev/posts/from-tasks-to-swarms-agent-teams-in-claude-code/)
