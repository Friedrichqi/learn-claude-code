# Research on the s15 integrated harness

This fork of [shareAI-lab/learn-claude-code](https://github.com/shareAI-lab/learn-claude-code) uses the
chapter-15 harness (`s15_integrated_harness/`) as the system under test for three weeks of profiling
(2026-09-01 → 2026-09-23). The question running through all of it: **where does a multi-agent coding
harness spend its time and its context, and which of that is redundant?** This folder holds every
experiment's scripts, raw data and writeup, one folder per topic. The weekly decks that presented the
results stay in `weekly_progress/`.

## The topics

Read them in order; each folder's `README.md` is the one document for its topic (Part A/B/C when two or
three experiments share a question), with its scripts beside it and its data in `data/`.

| folder | question | answer, in one line (details and caveats in the README) | when | presented |
|---|---|---|---|---|
| [`01_tracing/`](01_tracing/) | What does a structured trace of the s15 harness record, and what did the first traces show? | JSONL tracing of every model call, tool call, agent, background job and context step was added to s15/s16; the first four traces (Qwen3.8-27B on vLLM) include a 28-teammate stress run, whose team also wrote the docs that later workloads read. | 09-01 → 09-05 | — |
| [`02_input_redundancy/`](02_input_redundancy/) | How much file input do the lead and its teammates re-read or duplicate? | Re-reading is mostly structural: the lead re-reads what compaction evicted (a 50k → 200k limit took one task from 100 calls / 808 s to 7 calls / 168 s), and cross-teammate duplication is by design and is where the identical bytes are. | 09-08 → 09-11 | 090926 s4–s11 · 091626 s9 |
| [`03_context_interventions/`](03_context_interventions/) **(archived)** | Do batch reads, outline demotion or a stable prefix cut eviction-driven rounds on the lead? | A stable prefix gives 4× the cache hits and 65% fewer uncached tokens but the worst answers; batching helps only with headroom; outlines cost a third of the budget yet gave the best answers. Not reproducible (capture flags never committed). | 09-10 → 09-13 | 091626 s10–s11 |
| [`04_kv_splice/`](04_kv_splice/) **(archived)** | Can a server keep the KV cache across a micro-compaction edit by splicing and re-rotating RoPE? | At 50k it removes 61% of prefill tokens and keeps 96–97% of argmax decisions; its perturbation matches the compaction edit on Qwen2.5-1.5B but doubles on 7B; the RoPE correction is mandatory. | 09-11 → 09-13 | — |
| [`05_latency_breakdown/`](05_latency_breakdown/) | What does one agentic round cost end to end, on GLM and on Qwen3.8-27B under vLLM? | On GLM a round costs 7–10 s for the lead and 9–17 s for a teammate, and context preparation is about 0.1% of it; on Qwen/vLLM decode is 90–99% of a call and rounds are 1.5–2.8× longer, at identical quality. | 09-12 → 09-22 | 091626 s3, s5–s6 · 092326 s11–s12 |
| [`06_tool_cost/`](06_tool_cost/) | What does a tool call cost, and does advertising the cost change which tool the model calls? | Every tool costs 0.2–10 ms per call except four (bash, read_file, glob, create_worktree), and even the most expensive is six times cheaper than the fixed provider part of one model call; advertised costs steer the model only between substitutable tools. | 09-14 → 09-15 | 091626 s7–s8 |
| [`07_task_dag/`](07_task_dag/) | Does the lead build a dependency graph, and does it tell a server which bytes a successor needs? | The lead builds the graph unprompted and exactly (45 of 45 reference edges over 21 runs, two models), but the graph is the wrong key for retention; handing a successor its predecessor's bytes cut 7.22 → 2.50 rounds in the micro-loop and 5.17 → 3.00 in the real harness. | 09-16 → 09-17 | 092326 s3–s5 |
| [`08_context_inheritance/`](08_context_inheritance/) | How far up the graph should inheritance reach, and does it survive real trajectories? | On 1,200 real SWE trajectories whole-observation reuse is about 1% (0.8–1.2% of bytes, 2.1–4.3% of reading rounds), not the synthetic 24–39%; τ-bench/GAIA give 11.2% / 12.7%; frequency-aware eviction beats recency modestly (8 KB: LFU 19.2% vs LRU 15.8%). | 09-17 → 09-23 | 092326 s6–s10 |
| [`09_efficiency/`](09_efficiency/) | Which cost term dominates a run, and what does attacking it buy? | Cost model Wall = R×(F+P+D)+H; the optimized harness on Qwen/vLLM ran 2.3× faster (geometric mean) at unchanged quality; the inheritance arms are an inconclusive pilot (every cell hit the 900 s cap). | 09-22 → 09-23 | — |
| [`related_work/`](related_work/) | What do products and papers already do about compaction, KV reuse and agent scheduling? | Four reading notes (compaction and re-read costs, serving-side memory, teammate memory in Claude Code, the Continuum paper) plus the paper itself. | 09-11 → 09-16 | 091626 s2, s11 |

Every README opens with its status, dates, models and commits, and ends with a **Data inventory** (every
data directory and result file, with why it was run, including smoke, pilot, aborted and superseded
runs), a **Source map** (every section of every merged note → where it lives now) and **Cleanup notes**
(every correction or flag added in the merge, marked `[corrected 2026-09-23 …]` / `[cleanup note …]` in
the text). The weekly syntheses stay beside their decks: `weekly_progress/091626/tiered_memory_conclusion.md`
and `weekly_progress/092326/conclusion.md`.

## Layout

```
research/
  README.md          this index
  _paths.py          sys.path shim: every script imports its siblings by bare name through it
  conftest.py        keeps `pytest research` away from the benchmark seeds and archived sandboxes
  common/            shared by several topics
    profile_run.py       the non-interactive session driver behind every live harness run
    input_redundancy.py  byte-level read analyzer (library + CLI)
    evict_policies.py    the 16 eviction policies (library) + test_evict_policies.py
    fixtures/            seed workspaces copied into run sandboxes: latency_bench, census_bench, dag_bench
  NN_topic/
    README.md        the one writeup for the topic (merged from the per-week notes and s15 reports)
    *.py *.sh        the topic's scripts
    data/<leaf>/     raw traces, sidecars, sandboxes and generated tables (leaf names unchanged)
  related_work/      reading notes and the Continuum paper
weekly_progress/<MMDDYY>/slides.html   the weekly decks; 091626 and 092326 also keep their week's synthesis
s15_integrated_harness/                the harness itself, its trace tooling, and 4 sample traces
```

**Rules that keep the scripts working** (break one and the scripts fail, some of them silently):

- **Scripts sit exactly one level below `research/`.** Most compute
  `REPO = Path(__file__).resolve().parents[2]`, and `common/profile_run.py` and
  `06_tool_cost/tool_latency_probe.py` `chdir` there and wipe the harness run state (`.tasks`,
  `.memory`, …) before loading `s15_integrated_harness/code.py`.
- **Imports go through `_paths.py`.** Each script starts with
  `sys.path.insert(0, str(Path(__file__).resolve().parents[1])); import _paths`, which puts
  `common/`, every `NN_*` folder and `s15_integrated_harness/scripts/` on `sys.path`. Module names are
  unique across folders. Several imports are wrapped in `try/except` and degrade silently when a module
  is missing (`07_task_dag/dag_census.py` then computes no edge recall).
- **Frozen fixtures.** `s15_integrated_harness/{ARCHITECTURE,DESIGN,GLOSSARY,CHANGELOG}.md` are the
  reading material of most workloads: prompts name them by path and scripts read fixed line ranges
  from them. `02_input_redundancy/data/input_redundancy_runs.md` is the filler document of
  `05_latency_breakdown/provider_probe.py`. Keep all five byte-identical.
- **Data directories stay sibling leaves.** `dag_census.py`, `dag_redundancy.py`, `inherit_analyze.py`
  and `prewarm_analyze.py` recurse into subdirectories, so nesting one experiment inside another pools
  their runs.
- **Run everything from the repository root** with the project's Python (`~/.venv/bin/python` on the
  cluster). Live runs need a provider in `.env`; the analyzers are offline.

## Reproducing

Every topic README ends with the commands that produced its tables, and each data directory's tables
can be regenerated offline. On 2026-09-23 the analyzers were re-run over the moved data and reproduced
the committed tables byte-for-byte apart from path strings. The exceptions are tables that were
already out of date before the move; the topic READMEs say which.

Live reruns see a different repository tree from the recorded runs. The workloads explore the repo
(the scripted loops grep and glob it), and the cleanup moved about 1,800 files out of
`s15_integrated_harness/`. To rerun on the exact recorded tree, check out the tag
**`pre-cleanup-2026-09-23`** (a worktree works: `git worktree add ../lcc-pre pre-cleanup-2026-09-23`).
The vLLM pipeline (`05_latency_breakdown/qwen_vllm_pipeline.sh`) copies the repository into a lane
directory with `research/*/data` excluded; a lane copied before the cleanup must be re-copied.

## Where everything came from

Paths before the 2026-09-23 reorganization, per topic folder. Scripts came from
`s15_integrated_harness/scripts/`, trace data from `s15_integrated_harness/traces/`, and the leaf names
did not change. Each topic README's *Source map* traces every section of the merged notes, and the
deck footers already cite the new locations.

| folder | writeups (old path → new) | scripts (from `scripts/`) | data (from `traces/`, into `data/`) |
|---|---|---|---|
| `common/` | — | `profile_run.py`, `input_redundancy.py`, `evict_policies.py`, `test_evict_policies.py`, `latency_bench/`, `census_bench/`, `dag_bench/` | — |
| `01_tracing/` | s15_integrated_harness/trace_analysis.md → README.md<br>weekly_progress/090226/understanding_harness_{codex,GLM}.md<br>s15_integrated_harness/{hygiene_report,image_audit_report,link_report,trace_stats_review,trace_view_changes,demo_verification}.md → stress_run_outputs/ (link_audit_report.md, a duplicate, deleted)<br>s15_integrated_harness/trace_{task_dag,workflow_viz}.html | — | the 7 GLM `run_20260903…`/`run_20260905…` sessions → `data/glm_sessions/` |
| `02_input_redundancy/` | s15_integrated_harness/file_read_reuse_profile.md + weekly_progress/090926/teammate_overlap.md → README Part A<br>s15_integrated_harness/input_redundancy_profile.md + weekly_progress/091626/teammate_input_redundancy.md → README Part B | `file_read_reuse.py`, `redundancy_workloads.py` | `reuse_profiling`, `redundancy_profiling`, `redundancy_profiling_ctx512k`, `s15_integrated_harness/input_redundancy_runs.md` |
| `03_context_interventions/` | s15_integrated_harness/context_intervention_profile.md + weekly_progress/091626/agent_e2e_bottleneck.md → README | `ix_analyze.py` | `context_interventions` |
| `04_kv_splice/` | s15_integrated_harness/kv_splice_profile.md → README | `kv_splice.py`, `kv_render.py`, `kv_replay.py`, `kv_edit_stats.py`, `kv_analyze.py` | `kv_splice` |
| `05_latency_breakdown/` | s15_integrated_harness/latency_profile.md + weekly_progress/091626/latency_breakdown.md → README Part A<br>weekly_progress/092326/latency_qwen_vs_glm.md → README Part B | `latency_workloads.py`, `latency_breakdown.py`, `latency_compare.py`, `provider_probe.py`, `smoke_check.py`, `qwen_vllm_pipeline.sh`, `qwen_vllm.sbatch` | `latency_profiling`, `latency_profiling_qwen` |
| `06_tool_cost/` | s15_integrated_harness/tool_latency_profile.md + weekly_progress/091626/tool_execution_cost.md → README Part A<br>s15_integrated_harness/tool_cost_profile.md + weekly_progress/091626/tool_cost_exposure.md → README Part B<br>s15_integrated_harness/traces/tool_cost/tool_cost_page.html → tool_cost_page.html | `tool_latency_probe.py`, `tool_latency_analyze.py`, `tool_cost_probe.py`, `tool_cost_loop.py`, `tool_cost_harness.py`, `tool_cost_analyze.py` | `tool_latency`, `tool_cost` |
| `07_task_dag/` | s15_integrated_harness/dag_kv_reuse_profile.md + weekly_progress/092326/dag_kv_reuse.md → README Part A<br>s15_integrated_harness/dag_census_profile.md + weekly_progress/092326/dag_census.md → README Part B | `dag_workloads.py`, `dag_redundancy.py`, `dag_census.py`, `census_workloads.py`, `census_score.py`, `prewarm_smoke.py`, `prewarm_loop.py`, `prewarm_harness.py`, `prewarm_analyze.py` | `dag_redundancy`, `dag_redundancy_probe`, `prewarm`, `dag_census`, `dag_census_ds`, `dag_census_smoke`, `dag_census_partial` |
| `08_context_inheritance/` | weekly_progress/092326/inheritance_depth.md → README Part A<br>weekly_progress/092326/realworld_inheritance.md → README Part B<br>s15_integrated_harness/realworld_inheritance_profile.md → README Part C | `inherit_loop.py`, `inherit_workloads.py`, `inherit_analyze.py`, `coverage_sweep.py`, `budget_sweep.py`, `traj_ingest.py`, `hal_ingest.py`, `replay_sweep.py`, `lmeval_agent_model.py`, `lmeval_workloads.py`, `codebase_workloads.py` | `inherit`, `realworld`, `lmeval`, `lmeval_smoke`, `codebase` |
| `09_efficiency/` | weekly_progress/efficiency_methodology.md → README Part A<br>weekly_progress/efficiency_impl_report.md → README Part B | `term_scoreboard.py`, `effort_probe.py`, `inherit_trace_stats.py`, `qwen_vllm_opt.sbatch`, `qwen_inherit.sbatch` | `latency_profiling_qwen_opt`, `provider_probe_zai_20260922.json` |
| `related_work/` | weekly_progress/091626/{compaction_kv_reread_related_work,serving_memory_balancing,teammate_memory_management,continuum_paper_reading}.md, continuum_reading.html, the Continuum PDF | — | — |

Unchanged places: the weekly decks and syntheses (`weekly_progress/`), the harness and its trace
tooling (`s15_integrated_harness/`, including `scripts/{benchmark,trace_task_dag,trace_workflow_viz}.py`),
and the four sample traces in `s15_integrated_harness/traces/`. Deleted as temporary or broken:
`scripts/probe_tokens.py` (one-off migration, already applied), `scripts/env_check.py` (a stress-run
artifact that could never pass), `tests/test_context_interventions.py` (tested a function that was
never committed and aborted `make test`), `link_audit_report.md` (duplicate), the 12 broken submodule
pointers `traces/codebase/MODIFY/*.sandbox`, and all `__pycache__`.

## What this fork changes relative to upstream

Upstream `main` is still at `0dcafa2`, which is also the last upstream merge in this fork's history, so
`git diff 0dcafa2` is the complete difference: **16 upstream files modified, 1,858 files added, none
deleted.**

**The harness (lesson code)**

- `s15_integrated_harness/code.py` (+755 −189):
  - **Structured JSONL tracing** of every model call, tool call (including permission waits), agent
    lifecycle, teammate thread, background job, cron event, mailbox message, context-preparation
    step and retry. The recorder is the new sibling module `trace_runtime.py`. It is on for the CLI
    and a no-op when the lesson is imported, as the tests do.
  - **Bearer-token auth** for z.ai GLM: `ANTHROPIC_AUTH_TOKEN` is no longer dropped when a base URL
    is set.
  - **`CONTEXT_LIMIT` raised from 50,000 to 512,000 characters** (128k tokens at 4 characters per
    token).
  - A **prompt section that asks the model to batch independent tool calls**.
  - **Display-only shell commands run without a permission prompt**, including from asynchronous
    turns: `ls`, `cat`, `grep`, read-only `git` subcommands, `find` without write flags, and similar.
  - An **advisory `reacquire_warn` trace event** when a file is re-read unchanged.
  - **Turn-end memory extraction on a background worker**; `HARNESS_SYNC_MEMORY=1` restores the
    synchronous path.
- **New in `s15_integrated_harness/`:**
  - `trace_view.py` (tree, timeline and metrics viewer) and `trace_stats.py` (validator and
    cross-run stats)
  - `scripts/{trace_task_dag,trace_workflow_viz,benchmark}.py`, `examples/`,
    `tests/test_cli_smoke.py`, `Makefile`, `run_tests.sh`, `CONTRIBUTING.md`
  - four sample traces
  - `ARCHITECTURE.md`, `DESIGN.md`, `GLOSSARY.md` and `CHANGELOG.md`, written by the harness's own
    agent team during the 2026-09-02 stress run and now frozen workload fixtures
- `s16_workflow_runtime/code.py` (+426 −85): the same recorder for workflows (orchestrator and
  per-node agents, node dependencies, phases, logs) and a traced deterministic demo CLI.
- `s08_context_compact/code.py` and its three READMEs: the same 50k → 512k limit.
- `s09_memory/code.py`: memory recall is cached per user turn (harness reminders are stripped from
  the cache key), and extraction `max_tokens` goes from 1,000 to 1,500.
- **Docs.**
  - The s15 README gains "Structured Execution Traces", "Qwen/Qwen3.8-27B through Remote vLLM" and
    "Trace Experiments", plus a note on child-agent topology.
  - The s16 READMEs document workflow topology in the trace.
  - The root README gains a pointer to this folder.
  - The Chinese and Japanese translations are upstream's unchanged. s15's lag its English README,
    and the sync marker says so.
- **Tests.**
  - New: `tests/test_trace_runtime.py`, `tests/test_s15_readonly_permission.py`.
  - `tests/test_compaction_tool_pairs.py` pins the old limit for the one case that needs it.
- **Config.**
  - `.env.example` documents Qwen through vLLM, GLM bearer auth and `HARNESS_TRACE_*`.
  - `requirements.txt` requires `anthropic>=1.2.0,<2`.
  - `.gitignore` adds `.tasks*/`, `.zcode`, `.claude` and `profiling_sandbox/`.

**The research (added):** this folder (369.6 MB in 1,829 tracked files, almost all raw traces) and
`weekly_progress/` (three decks and two weekly syntheses).

**Known consequences:** two upstream tests fail on this fork; the other 515 pass.
- `tests/test_s08_context_compact.py::test_prepare_persists_oversized_unseen_result_before_full_compact`
  assumes the old 50k limit.
- `tests/test_web_scenarios.py::test_generated_s16_metadata_extends_s15_without_registry_false_positives`
  compares the lessons with web data generated from upstream's s15/s16 sources.
