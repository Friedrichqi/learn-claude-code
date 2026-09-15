# Per-tool execution latency: lead pool vs teammate pool

Probe runs: run_20260915T201117_531820Z_313a25f7.records.jsonl, run_20260915T201133_281733Z_97e47bdf.records.jsonl; each case holds 12-48 timed dispatches (warm-up pass dropped, shuffled order, no model calls).

## Basis A. Lead pool (assemble_tool_pool: 26 built-ins + mock MCP)

| case | n | mean ms | median ms | p90 ms | max ms | status |
|---|---:|---:|---:|---:|---:|---|
| bash_ls | 48 | 8.66 | 7.55 | 8.73 | 35.0 | ok 48 |
| bash_grep | 48 | 10.9 | 8.63 | 9.44 | 51.7 | ok 48 |
| bash_python_file | 48 | 135 | 134 | 136 | 159 | ok 48 |
| bash_background_start | 48 | 2.39 | 2.40 | 2.80 | 6.88 | scheduled 48 |
| bash_denied_mutating | 48 | 0.25 | 0.28 | 0.33 | 0.48 | denied 48 |
| read_file_13KB | 48 | 6.39 | 6.81 | 8.77 | 10.1 | ok 48 |
| read_file_26KB | 48 | 9.99 | 9.73 | 14.6 | 16.0 | ok 48 |
| read_file_45KB | 48 | 16.4 | 14.8 | 23.6 | 24.0 | ok 48 |
| read_file_146KB | 48 | 37.3 | 39.5 | 50.7 | 51.2 | ok 48 |
| read_file_limit50 | 48 | 2.88 | 3.46 | 3.87 | 4.21 | ok 48 |
| write_file_200B | 48 | 2.13 | 1.91 | 3.08 | 3.61 | ok 48 |
| write_file_20KB | 48 | 6.10 | 7.10 | 7.64 | 10.0 | ok 48 |
| edit_file_small | 48 | 2.29 | 2.82 | 3.09 | 3.23 | ok 48 |
| glob_shallow | 48 | 2.05 | 1.97 | 2.40 | 18.3 | ok 48 |
| glob_deep_recursive | 48 | 23.1 | 25.8 | 34.4 | 34.9 | ok 48 |
| todo_write_5 | 48 | 0.59 | 0.49 | 0.87 | 1.26 | ok 48 |
| task_dispatch_only | 48 | 0.85 | 0.93 | 1.15 | 1.27 | error 48 |
| load_skill_hit | 48 | 1.66 | 1.75 | 2.40 | 2.68 | ok 48 |
| load_skill_miss | 48 | 0.51 | 0.54 | 0.66 | 2.66 | ok 48 |
| compact_marker | 48 | 0.20 | 0.22 | 0.29 | 0.43 | ok 48 |
| create_task | 48 | 1.43 | 1.68 | 1.91 | 2.71 | ok 48 |
| update_task | 48 | 2.12 | 2.26 | 3.08 | 3.60 | ok 48 |
| list_tasks | 48 | 1.57 | 1.35 | 1.54 | 20.2 | ok 48 |
| get_task | 48 | 1.05 | 1.09 | 1.46 | 2.18 | ok 48 |
| claim_task | 48 | 2.77 | 3.38 | 3.87 | 4.23 | ok 48 |
| complete_task | 48 | 2.50 | 3.20 | 3.45 | 3.92 | ok 48 |
| schedule_cron | 48 | 0.65 | 0.66 | 0.92 | 1.17 | ok 48 |
| list_crons | 48 | 0.43 | 0.43 | 0.61 | 0.68 | ok 48 |
| cancel_cron | 48 | 0.41 | 0.40 | 0.59 | 0.61 | ok 48 |
| spawn_teammate | 48 | 1.89 | 1.90 | 2.18 | 6.82 | ok 48 |
| list_teammates | 48 | 0.46 | 0.35 | 0.59 | 4.59 | ok 48 |
| send_message | 48 | 1.21 | 1.32 | 1.52 | 1.78 | ok 48 |
| request_plan | 48 | 0.92 | 0.87 | 1.36 | 1.71 | ok 48 |
| request_shutdown | 48 | 1.11 | 1.35 | 1.58 | 1.99 | ok 48 |
| review_plan | 48 | 1.21 | 1.35 | 1.48 | 1.54 | ok 48 |
| create_worktree_denied | 48 | 0.27 | 0.30 | 0.37 | 0.43 | denied 48 |
| create_worktree_real | 12 | 607 | 581 | 680 | 707 | ok 12 |
| connect_mcp_cold | 48 | 0.46 | 0.45 | 0.64 | 0.91 | ok 48 |
| mcp_docs_search | 48 | 0.42 | 0.42 | 0.59 | 0.82 | ok 48 |
| mcp_docs_get_version | 48 | 0.37 | 0.35 | 0.54 | 0.66 | ok 48 |
| mcp_deploy_status | 48 | 0.43 | 0.47 | 0.60 | 0.70 | ok 48 |
| mcp_deploy_trigger_confirm | 48 | 0.76 | 0.75 | 0.89 | 6.87 | ok 48 |

Pooled by tool over all argument classes:

| tool | n | ok | denied | error/sched | mean ms | median ms | p90 ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| create_worktree | 60 | 12 | 48 | 0 | 607 | 581 | 680 |
| bash | 240 | 144 | 48 | 48 | 51.5 | 8.79 | 135 |
| read_file | 240 | 240 | 0 | 0 | 14.6 | 9.41 | 38.3 |
| glob | 96 | 96 | 0 | 0 | 12.6 | 10.9 | 34.0 |
| write_file | 96 | 96 | 0 | 0 | 4.12 | 3.19 | 7.39 |
| claim_task | 48 | 48 | 0 | 0 | 2.77 | 3.38 | 3.87 |
| complete_task | 48 | 48 | 0 | 0 | 2.50 | 3.20 | 3.45 |
| edit_file | 48 | 48 | 0 | 0 | 2.29 | 2.82 | 3.09 |
| update_task | 48 | 48 | 0 | 0 | 2.12 | 2.26 | 3.08 |
| spawn_teammate | 48 | 48 | 0 | 0 | 1.89 | 1.90 | 2.18 |
| list_tasks | 48 | 48 | 0 | 0 | 1.57 | 1.35 | 1.54 |
| create_task | 48 | 48 | 0 | 0 | 1.43 | 1.68 | 1.91 |
| send_message | 48 | 48 | 0 | 0 | 1.21 | 1.32 | 1.52 |
| review_plan | 48 | 48 | 0 | 0 | 1.21 | 1.35 | 1.48 |
| request_shutdown | 48 | 48 | 0 | 0 | 1.11 | 1.35 | 1.58 |
| load_skill | 96 | 96 | 0 | 0 | 1.08 | 0.73 | 2.28 |
| get_task | 48 | 48 | 0 | 0 | 1.05 | 1.09 | 1.46 |
| request_plan | 48 | 48 | 0 | 0 | 0.92 | 0.87 | 1.36 |
| schedule_cron | 48 | 48 | 0 | 0 | 0.65 | 0.66 | 0.92 |
| todo_write | 48 | 48 | 0 | 0 | 0.59 | 0.49 | 0.87 |
| mcp__* | 192 | 192 | 0 | 0 | 0.50 | 0.45 | 0.83 |
| list_teammates | 48 | 48 | 0 | 0 | 0.46 | 0.35 | 0.59 |
| connect_mcp | 48 | 48 | 0 | 0 | 0.46 | 0.45 | 0.64 |
| list_crons | 48 | 48 | 0 | 0 | 0.43 | 0.43 | 0.61 |
| cancel_cron | 48 | 48 | 0 | 0 | 0.41 | 0.40 | 0.59 |
| compact | 48 | 48 | 0 | 0 | 0.20 | 0.22 | 0.29 |

## Basis A. Teammate pool (the ten sub_handlers of the teammate loop)

| case | n | mean ms | median ms | p90 ms | max ms | status |
|---|---:|---:|---:|---:|---:|---|
| tm_bash_display | 48 | 8.96 | 8.92 | 9.66 | 10.8 | ok 48 |
| tm_bash_denied_async | 48 | 1.00 | 0.99 | 1.04 | 1.33 | denied 48 |
| tm_read_file_13KB | 48 | 7.94 | 8.52 | 9.93 | 10.1 | ok 48 |
| tm_write_file_2KB | 48 | 3.68 | 4.33 | 4.76 | 5.98 | ok 48 |
| tm_edit_file_small | 48 | 2.70 | 2.58 | 3.97 | 4.98 | ok 48 |
| tm_glob_deep_recursive | 48 | 24.4 | 20.7 | 35.6 | 45.4 | ok 48 |
| tm_send_message | 48 | 1.55 | 1.20 | 1.87 | 16.8 | ok 48 |
| tm_submit_plan | 48 | 1.16 | 1.08 | 1.71 | 1.87 | ok 48 |
| tm_list_tasks | 48 | 1.37 | 1.56 | 1.73 | 2.63 | ok 48 |
| tm_claim_task | 48 | 3.18 | 3.49 | 4.25 | 4.54 | ok 48 |
| tm_complete_task | 48 | 2.85 | 3.06 | 3.84 | 4.22 | ok 48 |
| tm_plan_gate_blocked | 48 | 0.44 | 0.49 | 0.56 | 0.81 | denied 48 |

Pooled by tool over all argument classes:

| tool | n | ok | denied | error/sched | mean ms | median ms | p90 ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| glob | 48 | 48 | 0 | 0 | 24.4 | 20.7 | 35.6 |
| bash | 96 | 48 | 48 | 0 | 8.96 | 8.92 | 9.66 |
| read_file | 48 | 48 | 0 | 0 | 7.94 | 8.52 | 9.93 |
| write_file | 48 | 48 | 0 | 0 | 3.68 | 4.33 | 4.76 |
| claim_task | 48 | 48 | 0 | 0 | 3.18 | 3.49 | 4.25 |
| edit_file | 48 | 48 | 0 | 0 | 2.70 | 2.58 | 3.97 |
| submit_plan | 48 | 48 | 0 | 0 | 1.16 | 1.08 | 1.71 |
| complete_task | 48 | 48 | 0 | 0 | 2.85 | 3.06 | 3.84 |
| send_message | 48 | 48 | 0 | 0 | 1.55 | 1.20 | 1.87 |
| list_tasks | 48 | 48 | 0 | 0 | 1.37 | 1.56 | 1.73 |

## Basis B. Observational tool spans of the 2026-09-12 latency-profiling runs

24 run traces, tool_start -> tool_end pairs, split by agent_kind. Real arguments, real in-run conditions; only the tools those workloads called.

### lead

| tool | calls | ok | denied | error | mean ms | median ms | p90 ms | max ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| bash | 22 | 13 | 4 | 5 | 102 | 134 | 187 | 193 |
| spawn_teammate | 45 | 45 | 0 | 0 | 21.1 | 23.9 | 40.4 | 61.1 |
| read_file | 21 | 21 | 0 | 0 | 10.7 | 6.32 | 25.5 | 43.7 |
| glob | 4 | 4 | 0 | 0 | 5.50 | 4.36 | 9.20 | 9.20 |
| request_shutdown | 45 | 45 | 0 | 0 | 5.03 | 2.71 | 10.8 | 15.5 |
| write_file | 9 | 9 | 0 | 0 | 3.13 | 3.17 | 3.20 | 3.52 |
| send_message | 1 | 1 | 0 | 0 | 3.00 | 3.00 | 3.00 | 3.00 |
| edit_file | 7 | 7 | 0 | 0 | 2.94 | 2.84 | 3.13 | 3.73 |
| create_task | 45 | 45 | 0 | 0 | 2.44 | 2.03 | 3.47 | 4.79 |
| todo_write | 82 | 82 | 0 | 0 | 0.95 | 0.93 | 1.03 | 1.62 |

### teammate

| tool | calls | ok | denied | error | mean ms | median ms | p90 ms | max ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| bash | 59 | 36 | 19 | 4 | 70.9 | 64.0 | 184 | 220 |
| read_file | 57 | 57 | 0 | 0 | 13.5 | 12.0 | 23.8 | 46.6 |
| glob | 17 | 17 | 0 | 0 | 9.43 | 10.4 | 11.7 | 18.7 |
| write_file | 15 | 15 | 0 | 0 | 8.54 | 5.72 | 25.1 | 29.8 |
| edit_file | 9 | 7 | 1 | 1 | 4.63 | 5.03 | 5.32 | 5.35 |
| complete_task | 33 | 33 | 0 | 0 | 4.83 | 4.83 | 4.99 | 5.07 |
| send_message | 5 | 5 | 0 | 0 | 4.40 | 2.68 | 8.22 | 8.22 |
| list_tasks | 5 | 5 | 0 | 0 | 2.75 | 2.82 | 2.97 | 2.97 |
| claim_task | 22 | 22 | 0 | 0 | 1.64 | 1.60 | 1.75 | 1.85 |

## Basis A vs B, same tool (success paths only)

| tool | probe lead ms | observed lead ms | probe teammate ms | observed teammate ms |
|---|---:|---:|---:|---:|
| bash | 51.5 | 120 | 8.96 | 97.1 |
| cancel_cron | 0.41 | -- | -- | -- |
| claim_task | 2.77 | -- | 3.18 | 1.64 |
| compact | 0.20 | -- | -- | -- |
| complete_task | 2.50 | -- | 2.85 | 4.83 |
| connect_mcp | 0.46 | -- | -- | -- |
| create_task | 1.43 | 2.44 | -- | -- |
| create_worktree | 607 | -- | -- | -- |
| edit_file | 2.29 | 2.94 | 2.70 | 5.15 |
| get_task | 1.05 | -- | -- | -- |
| glob | 12.6 | 5.50 | 24.4 | 9.43 |
| list_crons | 0.43 | -- | -- | -- |
| list_tasks | 1.57 | -- | 1.37 | 2.75 |
| list_teammates | 0.46 | -- | -- | -- |
| load_skill | 1.08 | -- | -- | -- |
| mcp__* | 0.50 | -- | -- | -- |
| read_file | 14.6 | 10.7 | 7.94 | 13.5 |
| request_plan | 0.92 | -- | -- | -- |
| request_shutdown | 1.11 | 5.03 | -- | -- |
| review_plan | 1.21 | -- | -- | -- |
| schedule_cron | 0.65 | -- | -- | -- |
| send_message | 1.21 | 3.00 | 1.55 | 4.40 |
| spawn_teammate | 1.89 | 21.1 | -- | -- |
| submit_plan | -- | -- | 1.16 | -- |
| todo_write | 0.59 | 0.95 | -- | -- |
| update_task | 2.12 | -- | -- | -- |
| write_file | 4.12 | 3.13 | 3.68 | 8.54 |

