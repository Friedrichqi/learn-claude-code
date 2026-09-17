# Summary — s15 Integrated Harness GLOSSARY.md
Alphabetized, source-grounded definitions (README.md, ARCHITECTURE.md, code.py) of the harness's domain vocabulary.
Core loop: the shared `agent_loop` runs every lead turn — inject triggers, compact context, call the model, dispatch tools, repeat until no tool_use.
Context management: layered cheap-first `prepare_context` compaction (budget, snip, micro, fit, full summarize) against a 512000-char CONTEXT_LIMIT.
Scheduling and async: cron jobs on a one-second daemon scheduler, plus an async event loop waking the lead for cron, team, and background triggers.
Background tasks: marked bash calls move to daemon worker threads; real output arrives later as `<task_notification>` user messages.
Permission boundary: bash deny list plus interactive Allow? (main thread only), file tools confined to WORKDIR, and PreToolUse hook strings that deny dispatch.
Hooks: the `HOOKS` registry with UserPromptSubmit/PreToolUse/PostToolUse/Stop events, run in registration order with the first non-None result winning.
Tracing: line-per-JSONL trace events with a fixed envelope, spans, and contextvars attribution; NullTraceRecorder default; trace_stats.py validates and aggregates runs.
Error recovery: `with_retry` (3 attempts, exponential backoff, fallback model), max_tokens escalation/continuations, and one-shot reactive_compact on prompt-too-long.
Teams: persistent teammates on daemon threads with reduced tool pools and MessageBus inboxes; the lead agent is the only interactive-permission thread.
Plan gate: per-teammate approval states hard-block write tools and complete_task until lead approval, re-armed to required on each new assignment.
Task board: cross-session, dependency-aware `.tasks/task_*.json` records guarded by an RLock plus flock; claiming verifies blockers and binds a cwd lease.
Extensibility: MCP tools merged under `mcp__{server}__{tool}` names via host policy; a skills catalog with on-demand load_skill; file-backed memory recall/consolidation.
Isolation: one-shot subagents (own messages, 30 rounds, five tools) and task-bound Git worktrees that become the owner's cwd with host-owned removal.
