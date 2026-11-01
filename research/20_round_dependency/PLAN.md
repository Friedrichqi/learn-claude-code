<!-- Topic 20's plan as approved on 2026-10-28 19:47 UTC. Recovered on 2026-10-30 from the session transcript, because the
     plan file ~/.claude/plans/i-desire-to-run-moonlit-panda.md was later overwritten by other plans. -->

# Topic 20 · How strongly do agentic rounds depend on each other? (MiniMax-M2.7, s15 harness)

## Context

**The user's goal (2026-10-28).** Find out whether a strong dependency runs through all agentic rounds of MiniMax-M2.7
in the s15 harness.
- If it does not, independent requests can be reordered by what they consume. When the server is busy, it can delay
  GPU-heavy model requests and run independent CPU work, the tool executions, in the meantime.
- Requests that lead to CPU-heavy work can also be given priority.
- Profile two properties, and test both under load:
  - **mergeable:** the request can be folded into the previous response;
  - **reorderable:** it can run while the previous round's tools are still running.
- Workloads: topic 19's code sessions and topic 16's SWE-bench / Terminal-Bench traces. Cap: 90 GPU-h on GPUs 0-3.

**What exploration found:**
- **No new agent sessions are needed.** Every MiniMax call is recorded in full: system, tool schemas, the exact
  messages, and the response with its thinking and tool arguments.
  - Topic 19: `research/19_efficiency_signals/data/cells/*/runs/*/<label>.requests.jsonl.xz`.
  - Topic 16: `research/16_epic_harness/harness_eval/jobs/minimax-m2.7*/…/agent/s15/calls.jsonl`. Tool schemas there
    are names only, and `epic_replay_live.builtin_tools()` rebuilds them.
- **Nothing measures round dependency today.** `depends_on_event_ids` is always `[]`, and topic 17's critical path
  assumes a chain.
- **The harness runs a response's tool calls one at a time, in order** (`s15_integrated_harness/code.py:3814-3890`).
  - Its system prompt already asks for batching (`code.py:951-955`).
  - It has an out-of-order mechanism, background bash: a "[Background task … started] Result will arrive as a
    task_notification" placeholder, and later a `<task_notification>` (`code.py:2742-2850, 3657`). MiniMax used it
    1 time in about 7,000 bash calls.
- **Tool time differs by workload.** Tool share of (tool + model) time, measured on sampled traces:

  | workload | tool share | bash detail |
  |---|---|---|
  | topic 19 | 0.01-0.14 % | millisecond reads and edits |
  | SWE-bench Verified | 9.9 % | — |
  | SWE-bench Pro | 41.6 % | p90 30 s, p99 153 s |
  | Terminal-Bench | 46.4 % | p99 89 s |

  So the reorder test needs Pro and Terminal-Bench. Topic 19 is the crowded-server testbed for merging: about 10-11
  calls per turn, 39 % of responses already multi-call, and at N=16 a third of the GPU on recompute after eviction.
- **The sequential runs are visible in the traces.** For example, `MU-w08_none-S000` makes four rename edits in four
  rounds, all listed in one earlier thought, and its docstring turn spans 87 rounds.
- **Standing rule:** no time conclusions from single-session timing. Resources are counted in tokens and tool
  seconds, and load effects are measured with many sessions.

## Definitions

- **Round j:** lead call L_j returns a_j (thinking + tool calls c_j). The harness then runs c_j, which gives the
  results O_j.
- **Pending test P_j:** L_j's recorded prompt with every result of round j-1 replaced by s15's own background
  placeholder: "[Background task bg_N started] Result will arrive as a task_notification." Round j is
  **decision-independent** of round j-1 if the model still produces c_j.
- **Chain group:** a maximal run of rounds k..m. Each round in it is produced while all earlier results of the group
  are pending. Groups are found greedily with merged-world contexts.
- **Mergeable:** a round inside a group, after the group's first round. Its calls could have come in a_k, and
  in-order execution keeps any effect order.
- **Reorderable:** mergeable, and its tool effects commute with the pending ones. That means no read/write overlap on
  files or state.
- **Order-free:** c_j already appears in the samples of an earlier call L_s.
- **Dependency strength:**
  - the share of rounds that need the immediately previous result;
  - group lengths;
  - the turn's group count against its round count (the merged critical path).

## Data

- **Static census, CPU, all rounds:**
  - topic 19 `none` + `sgate` cells, which have identical prompts: about 1,070 sessions and 40.8k lead calls;
  - topic 16's MiniMax profile plus the EPIC `prefix` / `prefix_aa` trials: about 78k lead calls.
- **Replay sample, GPU.** Every round of:
  - topic 19: 60 complete counted sessions from w08/w16/w32_none, 6 per codebase, all 4 turns normal (about 2,600
    rounds);
  - topic 16 e4r0 `prefix` (append-only template), one trial per task with ≤ 100 lead calls: 20 SWE-bench Verified,
    30 Pro and 30 Terminal-Bench (about 1,900 rounds).

## Stages

### S0 · CPU build, census and resource profile (`research/20_round_dependency/`)

**`rd_rounds.py`:** one rounds table for both sources.
- **Readers:** `mu_analyze.Cell` (`research/11_multiuser_kv/mu_analyze.py:333`), the turn logic of
  `ef_analyze.turn_records` (`research/19_efficiency_signals/ef_analyze.py:126`), and harness_eval
  `report/collect.py load_trials()`.
- **Flags:**
  - error, overflow and max_tokens re-issues;
  - turn boundaries;
  - history rewrites between calls (micro_compact, compaction, snip): is prompt j = prompt j-1 + a_{j-1} + O_{j-1}
    plus appends?

**`rd_static.py`:** classifies every round on CPU.
- **Lexical provenance:**
  - It takes the salient strings in c_j's arguments: paths, identifiers, line anchors, grep patterns, and the first
    line of old_text.
  - Each string is traced to the result round that first introduced it, or to the task or the model's own text.
  - It reuses the shingle method of `hs_drift.reply_proxy()` (`research/method_develop/hs_drift.py:196`) and
    `traj_ingest.read_key / classify_bash` (`research/08_context_inheritance/traj_ingest.py:122,138`).
- **Effects:** read/write sets, with bash through `_is_read_only_command` (`code.py:2145`). In topic 19 the driver
  denied mutating bash.
- **Static DAG per turn:** dependency distance, critical path and width.
- **Resources per step:**
  - **GPU side:** prompt, cached, computed and output tokens. These are load-independent.
  - **CPU side:** tool seconds, from the trace `tool_end` durations. A step counts as **CPU-heavy** at 1 s or more.

**`rd_cf.py`:** the counterfactual replay client. It follows `harness_eval/run/epic_replay_live.py`.
- **Render:** `ef_render.render(request, gen=True, template=…)` (`research/19_efficiency_signals/ef_render.py`), with
  each source's own append-only jinja.
- **Request:** POST `/v1/completions` with the token ids and `n=S`.
  - temperature 1.0, top_p 0.95, top_k 40: the generation_config the live runs used;
  - max_tokens 8,192, with truncations reported;
  - `skip_special_tokens: false`;
  - a seed per (round, variant).
- **Warm-then-sample:** a 1-token request first, so all S samples share one prefill.
- **Parse:** after the `<think>` split, with vLLM's `vllm/tool_parsers/minimax_m2_tool_parser.py`.
- **Action keys (target level is primary):**
  - read_file: path;
  - edit_file: path + first line of old_text;
  - write_file: path;
  - glob: pattern;
  - bash: program + paths or pattern.

  Exact arguments are a secondary level. A sample matches if it reproduces at least half of c_j's keys. todo_write
  is reported separately, because the reminder triggers it.
- **Variants for round j:**
  - **C_j, control:** the recorded prompt.
  - **P_j, pending.**
  - **M_j, merged-world:** L_k's prompt, then a_k with the calls of k+1..j-1 folded in (their thinking dropped, so
    nothing leaks), then placeholders for every call in the group. A chain ends on a fail, a rewrite or a turn end.
  - **Order-free:** taken from earlier controls' samples at no extra cost.
- **Outcome categories per sample:** same action, re-issues a pending call, other tool, final/no call, truncated.

**`rd_serve.py`:** starts and stops MiniMax-M2.7 at TP=4 on GPUs 0-3.
- Flags: `ef_workloads.SERVER_ARGS` (`ef_workloads.py:75-86`) with `--max-model-len 131072`.
- The pool pin and scheduler extension come off for the profile; topic 19's exact configuration is used for the
  load tests.
- The sheet check uses `ef_pipeline.claims_cover` (`ef_pipeline.py:92`).
- GPU-hours are logged as in `wt_pipeline.py:332-337`.

**Analysis and checks:**
- `rd_analyze.py` builds the tables and decisions.
- `rd_verify.py` independently recomputes the headline numbers.
- `test_rd_*.py` holds the tests (see Verification).

### S1 · Smoke (about 1 GPU-h)

20 rounds per source.
- The control renders must match the recorded prompt tokens: input + cache_read + cache_creation.
- Parsing must reproduce the recorded tool calls.
- Warm-then-sample prefix hits must be visible.
- The smoke also checks how the placeholder is handled and measures tokens/s, which gives the GPU-h projection.

### S2 · Pilot (about 4 GPU-h), then freeze `prereg.md` and `prereg.sha256` (part 1)

About 150 rounds per source, with 2S controls, which gives an A/A comparison.
- **The independence rule is calibrated here:** independent iff p_P ≥ τ·p_C and p_C ≥ 2/S.
  - τ defaults to 0.5, set so that at most 10 % of A/A rounds are called dependent.
  - S ∈ {4, 6, 8} and the sample size are chosen so that S1-S3 fit within 35 GPU-h, which protects the load tests.
- **Gates; if any fails, stop and ask:**
  - **V1:** at least 95 % of renders match.
  - **V2, positive control:** rounds whose arguments carry a string first introduced by O_{j-1} give
    ρ = Σp_P / Σp_C ≤ 0.3.
  - **V3:** A/A ρ is within [0.85, 1.15].

### S3 · Main counterfactual replay (about 25 GPU-h)

The whole sample, with all variants.

### S4 · Profile analysis (CPU): the answer to the user's question

Per workload, template or benchmark, and tool class, with session- or trial-bootstrap 95 % CIs:
- **Q1, strength:** the share of rounds that need the previous round's result, group lengths, and groups per turn
  against rounds per turn. Also how often a round needs nothing from earlier results in its turn.
- **Q2, merge:** lead calls per turn removed, plus the output and computed-prefill tokens removed.
- **Q3, reorderable:** the share of rounds that are mergeable and whose effects also commute. **Q4:** the order-free
  share.
- **Q5, overlap by resource:**
  - the tool seconds that run while an independent next call could proceed;
  - the GPU tokens of calls that could be issued early;
  - how many of those calls lead to CPU-heavy steps.
- **Static census against the replay labels:** precision and recall. The census extends the answer to all ~120k
  rounds only if it agrees well enough.
- **Gates:**
  - **G_merge:** the topic-19 chain groups remove at least 10 % of lead calls per turn.
  - **G_reorder:** at least 10 % of Pro / Terminal-Bench tool time runs alongside an independent next call.
  - If a gate fails, that load test is reported as not run.

### S5 · Load tests (about 45 GPU-h; prereg part 2 frozen before the main cells)

**`rd_loadreplay.py`** ports `research/11_multiuser_kv/mu_replay.py` to MiniMax by rendering with `ef_render`.
- **Closed loop:**
  - N slots; the sampled sessions or trials as nonce-unique copies;
  - the recorded output lengths with `ignore_eos`;
  - the recorded tool gaps and think pauses;
  - a per-request `priority`.
- **Variants:**
  - **`rec`:** the recorded order.
  - **`merge`:** each chain group folded into its first call. The results go into one message, and the output
    grows by the folded calls' tokens plus the extra output per added call, fitted on recorded multi-call responses.
  - **`reorder`:** inside a group, each later call is sent as soon as the previous call returns.
    - Its predecessors' unfinished results appear as s15's background placeholder.
    - Late results arrive as `<task_notification>` blocks carrying the full output.
    - The group's tools run concurrently when their effects commute, and otherwise in order.
    - The next group's first call waits for all of the group's tools.
- **Primary metric:** recorded rounds completed per hour, since every variant does the same work.
- **Also measured:** turns or trials per hour, GPU-s per recorded round, hit rate, recompute after eviction, queue
  wait, and latency p50/p95.
- **Statistics:** block bootstrap against `rec`, with floor F = 10 % (topic 19's between-cell spread).

**Merge test** (topic 19 workload; topic 19's server: pool pinned at 16,137 blocks, WTScheduler, `--max-num-seqs 64`):
- `rec` vs `merge` at N=16 and N=32;
- 15-min warm-up + 60-min window;
- 4 cells, about 22 GPU-h.

**Reorder test** (SWE-bench Pro + Terminal-Bench replay). The server is topic 19's configuration, with
`--max-model-len 131072` for these longer prompts and `--scheduling-policy priority` in all its cells. S0 tests that
WTScheduler keeps its logging under the priority policy.
- **Regime pilot P5:** `rec` at N=8 and N=16, short windows, about 5 GPU-h. It picks N_c, the smallest N with a hit
  rate below 50 %.
- **Then 3 cells at N_c** (about 17 GPU-h):

  | cell | rule |
  |---|---|
  | `rec` | FCFS |
  | `reorder_low` | "server decides": requests that block their session get priority 0; early-sent requests get 1, so a busy GPU delays them while tools run |
  | `reorder_cpu` | as `reorder_low`, but an early-sent (independent) request whose round runs at least 1 s of tools gets −1, so CPU-heavy work starts first |

  `reorder_cpu` takes the tool time from the recording, so it is an oracle predictor.
- **If at least 8 GPU-h remain:** add a `merge` cell on this replay, to compare merge and reorder on one workload.

### S6 · Write-up

- `research/20_round_dependency/README.md`: Short answer; method; prereg; results; caveats (replay drops reply-KV
  reuse in every variant; the model is assumed to produce the recorded outputs under merged or reordered histories);
  GPU-hour log; data inventory; reproduction.
- One row in `research/README.md`.
- A summary to the user.

## Budget and rules

- **Budget:** cap 90 GPU-h: profile about 30, merge test about 22, reorder test about 22, which leaves about 15
  spare. GPUs 0-3 only, one TP=4 server at a time.
- **GPU sheet:**
  - before each GPU stage I post the exact US Eastern slot ranges, and the user enters them as Yijiahao;
  - I verify them with the CSV export (gid 700000005; GPUs 0-3 are unclaimed Wed-Fri now);
  - `rd_serve` refuses to start without a claim.
- **Servers:** stop vLLM after every stage and confirm the GPUs are empty.
- **Git and Drive:** no commits or Drive pushes unless asked.
- **Wording:** user-facing text says "setup/condition/variant", never "arm", and explains every setup name.

## Verification

- **CPU tests (`test_rd_*.py`):**
  - The rounds table reproduces topic 19's published numbers: calls per turn 10.3-11.3 in `none`, and the
    `tools_per_response` buckets in `data/tables/main.json`.
  - Masking changes only O_{j-1}.
  - Control render equals the recorded length on 200 random rounds per source.
  - Recorded responses survive a round trip through render and parse.
  - Action keys work on hand-made cases.
  - Merged-world and reorder prompts are valid: every tool_use id is paired, and they are append-only.
  - Provenance and effects work on synthetic sessions.
- **End to end:** `rd_cf` and `rd_loadreplay` (all variants, priorities included) run against the fake MiniMax
  server of `research/19_efficiency_signals/test_ef_e2e.py` or `research/18_harness_wait/wt_fake_vllm.py`.
- **GPU gates:**
  - V1-V3 in the smoke and pilot;
  - load cells follow topic 19's validity rules: no err record, every request joined, every window complete;
  - `rec` renders must match the recorded usage.
- **Reproducibility:** `rd_verify.py` agrees on every headline value, and the tables regenerate byte for byte.
