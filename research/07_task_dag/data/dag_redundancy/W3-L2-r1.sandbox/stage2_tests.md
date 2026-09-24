# Stage 2 — Ten test cases derived from stage1_notes.md

1. **Loop stop condition**
   Action: Run a lead turn whose model response contains no concrete `tool_use` block, then one that does.
   Expected result: With no `tool_use`, `agent_loop` stops (a `harness_decision` event with decision `stop`, reason `no_tool_use`); with a `tool_use`, the tools are dispatched, results are appended as one user message, and the loop repeats — the `has_tool_use` check governs, never `stop_reason` alone.

2. **Cheap-first compaction ordering**
   Action: Grow context until each compaction layer's condition is met and run `prepare_context`.
   Expected result: Layers fire in cost order — `tool_result_budget` persists oversized outputs of the latest message to `.task_outputs/tool-results/`, `snip_compact` archives old ranges to `.transcripts/` keeping head 3 + tail 46, `micro_compact` reduces the oldest consumed tool results to saved-path markers keeping the 3 newest, and `fit_tool_results` replaces largest results with 1000-char persisted previews.

3. **CONTEXT_LIMIT guard on full summarization**
   Action: Push estimated size (`len(json.dumps(...))`) above 512000 chars and observe `prepare_context`; separately invoke the `compact` tool and trigger a prompt-too-long error.
   Expected result: `compact_history` fires only while the estimate exceeds `CONTEXT_LIMIT` (512000 chars / 128k tokens at 4 chars/token), producing one `[Compacted]` message that still carries the Authoritative request; the `compact` tool path works via the special case in `agent_loop`, and `reactive_compact` runs at most once per turn.

4. **Background bash placeholder and late notification**
   Action: Issue a `bash` call with `run_in_background: true`, then wait for completion while continuing to converse.
   Expected result: The call moves to a `background-<id>` daemon worker thread with its own process session; the model immediately receives a placeholder tool_result, and the real output arrives later as a `<task_notification>` user message injected into a subsequent turn.

5. **Cron scheduling, dedup, durability, and one-shot delivery**
   Action: Register a 5-field cron job firing twice in the same minute, a `durable` job across a restart, and a one-shot job.
   Expected result: Due jobs are enqueued by the per-second `cron_scheduler_loop` but deduped by the per-minute `_last_fired` marker (one fire per minute), consumed as `[Scheduled]` user messages; the durable job reloads from `.scheduled_tasks.json`, and the one-shot uses `pending_delivery` with `acknowledge_cron_jobs`/`restore_cron_jobs` for at-least-once delivery.

6. **Model-call error recovery paths**
   Action: Force a 429 error, two consecutive 529s with `FALLBACK_MODEL_ID` set, a `max_tokens` stop, and a prompt-too-long error.
   Expected result: 429/529 are retried up to 3 attempts with exponential backoff (500 ms base, ×2, capped at 32 s, ≤25% jitter); two consecutive 529s switch to the fallback model; `max_tokens` gets one 8000→16000 escalation plus up to 2 continuation prompts; prompt-too-long triggers the one-time `reactive_compact` detected by `is_prompt_too_long_error`.

7. **Layered permission boundary**
   Action: From the main thread and from an async turn, run a bash command on the deny list (e.g. `rm -rf /` or `sudo`), a benign bash command, a `write_file` to a path outside `WORKDIR`, and an `mcp__*` tool.
   Expected result: Deny-listed commands are rejected outright; benign bash requires an interactive `Allow? [y/N]` answered on the main thread only (asynchronous turns fail closed); out-of-WORKDIR paths fail to resolve inside `WORKDIR` and are denied; the MCP tool follows its host policy entry (default `confirm`), never trusting server-provided descriptions.

8. **Plan gate blocking and re-arming**
   Action: As a teammate with a `required` gate, attempt `bash`, `write_file`, `edit_file`, and `complete_task` before approval; then have the lead approve a submitted plan and complete the task; then claim a new assignment and repeat the write.
   Expected result: All four actions are hard-blocked while the gate is `required`/`pending`/`rejected`; after approval (`approved`) they proceed; `advance_assignment_version` re-arms the gate to `required` on the new claim so the old approval does not leak.

9. **Task board concurrency and dependency safety**
   Action: Concurrently update and claim tasks; attempt to add a `blockedBy` self-dependency, a missing target, and a cycle; claim a task whose blockers are unfinished; call `complete_task` without ownership.
   Expected result: Records under `.tasks/task_*.json` stay consistent under RLock + cross-process flock with atomic tmp+rename writes; invalid `blockedBy` edges are rejected by `update_task` (only on pending, unowned tasks); `claim_task` atomically verifies all blockers via `can_start` before setting owner, `in_progress`, and the cwd lease; `complete_task` refuses without ownership and a cleared plan gate.

10. **Trace runtime defaults and stats validation**
    Action: Import the harness module without `HARNESS_TRACE*` env vars, then run the CLI with tracing enabled and inspect `traces/run_*.jsonl` with `trace_stats.py`, including one malformed line.
    Expected result: The import creates no files (default `NullTraceRecorder`); with env vars set, every event is one 0600-mode, line-buffered, flushed JSON object per line with the fixed envelope (schema 1.0, UTC timestamp, `monotonic_ns`, `run_id`/`turn_id`/`event_id`, span fields, redacted `data`), model calls appear as `model_request` → `model_response`/`model_error` spans with `duration_ms`, worker-thread events stay attributable via contextvars, and `trace_stats.py` emits schema warnings but aggregates calls, stop reasons, tokens, tool frequency, and error/retry counts — exiting 1 when the malformed line is present.
