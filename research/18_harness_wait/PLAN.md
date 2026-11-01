# Topic 18: a model-called `wait` tool driven by live vLLM status (harness-side gating)

> Approved 2026-10-24. Kept here verbatim on 2026-10-26: it was first saved at
> `~/.claude/plans/now-i-want-to-jiggly-waffle.md`, which now holds topic 19's plan.

## Context

The user wants a new profiling experiment. Many s15 harness sessions share one vLLM server. Past a point the
server gets crowded: it evicts KV and recomputes it, which costs GPU time and end-to-end latency. The idea:

1. Expose the server's live status to the harness.
2. Add a `wait(seconds)` tool, a specialization of bash `sleep`. The model calls it, and it runs at the end of
   the agentic round, holding the session's next request.
3. The server can then drain instead of thrashing. This is gating done from the harness side.

**What prior work and the code say:**
- **Topic 11** (`research/11_multiuser_kv/README.md`; Qwen3-32B, one H200, a 258k-token pool, vLLM 0.29):
  - The turning point is 8–12 users. At N ≥ 16, 84–85 % of prompt tokens are re-prefills of evicted prefixes,
    which take 74 % of GPU time.
  - **Preemption cost 0.00 s per call.** 74–89 % of evicted tokens were lost while the returning call waited
    in vLLM's queue (topic 15, F3).
  - A **server-side** session gate (K=8 active, a slot frees after 5 s idle) gave 3.5–3.8× lead calls/h and a
    5.5–6.3× lower turn p95.
  - A fixed-size client turn gate worked only when it was sized to the pool (2.87× at N=16, 1.05× at N=32).
- **vLLM 0.31.0** (`/data/fritz/venv`, the only install):
  - A preempted request goes to the **front** of the waiting queue (`v1/core/sched/scheduler.py:1580`). Its
    blocks are freed and it recomputes whatever is no longer cached.
  - `kv_cache_usage_perc` counts cached idle prefixes as free (`block_pool.py:880-891`), and there is no
    eviction counter.
  - So stock `/metrics` sees queueing and running contexts, but not eviction thrashing.
- **Why a dedicated tool, not bash `sleep`:** in s15, `sleep` needs permission, is denied off the main thread
  and times out at 120 s.
- **Topic 06** found that models act on cost information only when given a policy sentence.

**Mechanism to test, not assume.** A held session's own prefix is exposed to LRU eviction while it waits, as
it is in vLLM's queue. A hold pays only if it keeps a stable set of sessions whose working sets fit the pool,
as the server gate does. A global signal (requests are waiting in 98–99 % of steps at N ≥ 16 in topic 11's
logs) may make sessions herd.

## User decisions (2026-10-24)

- **The model decides when and how long to wait.** It sees a one-line server status after **every round**.
- **Model arms plus harness twins.**
  - Two model arms differ only in the status they see: stock `/metrics` fields vs per-session pool share.
  - Each has a harness-decided twin on the same status.
  - Plus the server gate (reference) and no-wait.
- **Testbed: topic 11's workload.**
  - Sessions: s15 4-turn sessions on 10 local codebases, think pauses with median 30 s, solo, Qwen3-32B with
    thinking off.
  - Serving: TP=2 on 2 H100s, with the pool pinned to topic 11's 16,137 blocks (258k tokens).
  - Two cells run at once, on GPUs 0–1 and 2–3.
- **Budget:** GPUs 0–3, a hard cap of 100 GPU-h.

## Arms

| arm | `wait` tool | status the model sees each round | who holds, and when |
|---|---|---|---|
| `none` | — | — | nobody |
| `m_stock` | advertised | `[server] running 12, waiting 5 (4 for memory); KV in use 97%; prompt-cache hits 4% and preemptions 0 in the last 10 s` | the model, after its round's other tools |
| `m_share` | advertised | `[server] KV pool 258,192 tokens; sessions ahead of you hold 231,040; your context 24,512, 18,304 still cached; 5 waiting` | the model, after its round's other tools |
| `h_backoff` | — | — (timing-only twin of `m_stock`) | the harness, at round ends: enters "crowded" at capacity-waiting ≥ 1 or KV ≥ 0.92 and leaves at waiting = 0 with KV < 0.85; holds d₀·2ᵏ·U(0.5, 1.5) and re-checks, up to a cap |
| `h_share` | — | — (timing-only twin of `m_share`) | the harness, at round ends: holds while the working sets of the sessions ahead plus its own exceed τ·pool, re-checks every ~2 s with jitter, up to a cap |
| `sgate` | — | — | the server: topic 11's gate, K=8, 5 s idle |

- **"Ahead" means first come, first served by the start of each session's current turn**, as in sgate. It does
  not starve young sessions.
- **The twins hold only at round ends.** The model can never hold a turn's first call, so the comparison
  isolates the decider. A turn-start variant is the one optional extra (see Budget).
- **The rule parameters and the `wait` text are settled in the pilot.** These are θ, d₀, the caps and
  τ ∈ {0.75, 0.9}. The starting points come from an offline pass over topic 11's local scheduler logs:
  - Under the gate, the active sessions' summed prompts were 0.65 of the pool at p50 and 0.78 at p90.
  - At N=8, (others + own working set) / pool never exceeded 0.9.
  - Requests were waiting in 98–99 % of steps at N ≥ 16.

  The parameters are then frozen in a pre-registration.

## Design

### Server side: `research/18_harness_wait/` (new; prefix `wt_`)
- **Port `research/11_multiuser_kv/mu_scheduler.py` in place to vLLM 0.31.0**, keeping the 0.29 branches:
  - **Versions:** a version allowlist replaces the assert at `:71`/`:204`. Today the assert makes the
    scheduler fail open without notice, so `sgate` would quietly run as `none`.
  - **Queue view:** for `skipped_waiting` (`:457-482`), count `len(waiting) + len(kv_holding_waiting) −
    len(deferred_waiting)`, and use deferred as `sk`.
  - **Wait reasons:** new `defer` and `stale` reasons for the queue head.
  - **Classes:** an `MUMixin`, with `MUScheduler(AsyncScheduler)` (TP=2 runs async) and
    `MUSyncScheduler(Scheduler)`.
  - **Callbacks:** `_on_commit` / `_on_evict` callbacks in `_cache_hook` (`:640-661`) and `_evict_hook`
    (`:619-638`).
  - The log schema stays the same, so `mu_analyze`'s fresh / evicted / preemption classes carry over.
  - Record the port in topic 11's README "Changes" section.
- **`wt_scheduler.py`, `WTScheduler(MUScheduler)`:**
  - per-session resident blocks, maintained incrementally (+1 on commit, −1 on evict);
  - `prefix_tok` by binary search over the chain's hashes with `get_cached_block`; LRU eats a chain from its
    tail;
  - `ws_tok`, `inflight`, `mid` (with idle expiry) and turn start;
  - global running, waiting by reason, KV usage, and 10-s windows of prefix-cache hits, preemptions and
    evictions;
  - a snapshot every ≤ 200 ms (and forced when the engine drains), written by a writer thread to
    `$WT_STATUS_PATH` in `/dev/shm` via `os.replace`, plus a `wt` record in the sched log every 1 s.
- **`wt_server_ext.py`**, an ASGI `--middleware` in the pattern of
  `harness_eval/epic/epic_server_ext.py:743-781`:
  - `GET /wt/status?view=stock|share&sid=…`, with `age_ms` added.
  - `stock` returns only fields that stock `/metrics` also has, cross-checked in the smoke.
  - `share` adds `ahead_ws`, own `ws`/`prefix_tok` and a registry of holders (sid, turn start, need). The
    decision stays in the harness, and FCFS ranking keeps holders from releasing all at once.
  - An audit log of every request.

### Harness side: s15, everything default-off and byte-identical when unset
- **New `s15_integrated_harness/hold_runtime.py`**, loaded by path like `trace_runtime` (`code.py:56-75`):
  - config (`S15_WAIT_TOOL`, `S15_STATUS_VIEW`, `S15_HOLD_POLICY`, `S15_HOLD_PHASES`, `S15_HOLD_MAX_S`,
    `S15_SESSION_ID`, the rule parameters);
  - a fail-open status client: 0.5 s timeout, a circuit breaker, it never raises, and it never puts
    "429"/"529" in error text (`with_retry` keys on those);
  - the `backoff` and `share` rules, `maybe_hold(kind, need)` and `status_line(view)`.
- **`code.py`:**
  - **The tool:** a `WAIT_TOOL` schema, not in `BUILTIN_TOOLS`, whose length 26 a test asserts. It is
    appended in `assemble_tool_pool` (`:3138`) and in the prompt's tool list (`:885-893`) only when enabled.
  - **Lead dispatch:** treat `wait` like `compact` (`:3706-3720`): a placeholder result, the round's other
    tools first, then one capped sleep before `:3773`.
    - The result text is the seconds slept plus a status line.
    - It does not count toward `rounds_since_todo` (`:3760-3763`).
    - It is traced as a `wait` tool span carrying the status and `prefix_tok` before and after.
  - **Teammates and subagents:** the same deferral through a shared helper (`:1824-1835`, `:2266-2291`). Only
    the lead is exercised here.
  - **Status line (model arms):** appended once per round to the newest user message (a turn prompt or tool
    results), right before `call_llm` (`:3622`). Append-only, like `append_context_updates`
    (`:966-991`); never re-appended on the retry paths at `:3632`, `:3657` and `:3668`.
  - **Twin hold:** `hold_kind` is set to `"mid"` after `:3773` and consumed at the top of the loop (`:3595`),
    outside `with_retry`. It is traced as `hold_start` / `hold_end` with the rule's signals.
- **`mu_session.py`:** export `S15_SESSION_ID` (it equals `X-Session-ID`, `:131`/`:177`) so the harness can find
  itself in `/wt/status`.

### Drivers and workload
- **`wt_workloads.py`:**
  - **Freeze the workload first:** copy the 9 site-packages seeds and `s15_integrated_harness/` at HEAD into
    `data/seeds/`. The harness is itself one of the codebases and we are about to edit it.
  - Rebuild topic 11's manifest with the seed paths rewritten, and diff it against topic 11's.
  - Conditions go through `MU_EXTRA_CONDITIONS` (`mu_workloads.py:166-170`), with server env and client env per
    arm.
- **`wt_sweep.py`** (subclasses `mu_sweep.Cell`):
  - a fixed window: 20 min warm-up, then 90 min of measurement, then `window-end` with no retry;
  - per-slot spec sequences, so arms are paired;
  - topic 11's staggered starts and fillers;
  - lane excludes that skip `harness_eval/` (30 GB per lane otherwise);
  - a watchdog for the case where every session is holding.
- **`wt_pipeline.py`** (lifecycle copied from `harness_eval/serve/serve.py:38-149`), per pair {0,1} / {2,3}:
  1. Check the GPUs are empty.
  2. Start `vllm serve Qwen/Qwen3-32B` with: TP=2, util 0.92, `--num-gpu-blocks-override 16137`,
     `--max-model-len 40960`, the hermes and qwen3 parsers, thinking off, the append-only template
     `harness_eval/vendor/s15/templates/qwen3_append_only.jinja`, prefix caching, prompt-token details,
     `--kv-cache-metrics`, `--scheduler-cls wt_scheduler.WTScheduler` and
     `--middleware wt_server_ext.Middleware`.
  3. Fail fast if the sched log header has an `err` record or the wrong gate config.
  4. Start `mu_scrape` (given a GPU list).
  5. Run the sweep.
  6. Stop everything and verify the GPUs are empty.

  vLLM is pinned to NUMA-0 cores and the sessions to NUMA-1.

### Analysis
- **`wt_analyze.py`** uses `mu_analyze` as a library:
  - joins and token/GPU classes;
  - the step model refit on these H100 cells only;
  - clipped to the window.
- **New tables:**
  - **H1 holds and waits:** share held, seconds per call, turn and session, and the model's wait count and
    requested seconds against its status.
  - **H2 decision signals and oscillation:** release burstiness and autocorrelation of the queue length.
  - **H3 held sessions' prefix survival:** `prefix_tok` at hold start and at release, then the admission hit.
  - **H4 fairness:** the Jain index.
  - **H5 throughput and latency:** calls/h, turns/h, Kaplan–Meier turn latency with censoring, effective call
    latency including holds, hit rate, recompute share.
  - **H6 overhead.**
- **`wt_verify.py`:** recomputes the primary table independently from the raw logs.

## Stages and budget (two TP=2 cells at a time ≈ 4 GPU-h per 2-h cell)

| stage | content | GPU-h |
|---|---|---|
| S0 (CPU) | everything above + tests; offline calibration; frozen manifest | 0 |
| S1 smoke | natural-pool start (read the block count); forced contention (1,100 blocks, 16k window, 4 users, KV events): observer vs KV events, residency invariants, status freshness, stock view vs `/metrics`; `sgate` with K=2; one `m_stock` and one `h_share` session end to end | ~4 |
| S2 pilot (~1 h cells: 15 min warm-up + 30 min window) | P1 `none8 ∥ none16`: is the regime reproduced on H100 TP=2? P2 `m_stock16 ∥ m_share16`: does the model wait, and with sane seconds? P3 `h_share16 ∥ h_backoff16`, with P4 for τ only if it is still open | ~12–16 |
| prereg | `prereg.md` + sha256: arms, N, window, frozen parameters and tool text, metrics, rules, cell order and seed | 0 |
| S3 main (16 cells) | **N=16:** all 6 arms + an A/A `none16`, run on the other pair at the same time. **N=32:** all 6 arms. **N=8:** `none`, `m_stock`, `m_share` (no-harm and needless-wait check). Each arm's N=16 and N=32 cells run on different pairs | 64 |
| contingency | reruns; if budget remains, `h_share16` with turn-start holds (labelled exploratory) | ≤ 16 |
| **total** | about 20 h of GPU wall time | **~80–84, cap 100** |

## Metrics and pre-registered rules

- **Primary metric: work lead calls per hour.** These are lead calls that are not wait-only, counted while at
  least N−1 sessions are live.
- **Key secondaries:**
  - turns/h and Kaplan–Meier turn p50/p95;
  - effective call latency, including holds and waits;
  - hit rate;
  - GPU share on evicted and on preempted recompute;
  - calls per turn and prompt tokens per call (the model arms carry about 20 status tokens per round).
- **Recovery ratio** R_X = (X − none) / (sgate − none) at N=16 and N=32, for each wait arm. It is "meaningful"
  if R ≥ 0.5 and its block-bootstrap lower bound is outside the A/A band.
- **Decider effect:** m_X − h_X on the same status. **Information effect:** share vs stock.
- **No harm at N=8:** the model arms stay within the A/A band and the hit rate within 2 points. Wait frequency
  is reported.

## Operating rules

- GPUs 0–3 only.
- Before S1, I post the exact slots (US Eastern) for the user to enter in the lab GPU sheet. The Oct 19–25 and
  Oct 26 – Nov 1 tabs show no claims for the weekend or next week. I verify the entries in the CSV export
  before every GPU stage.
- Every server is stopped right after its batch, and the GPUs are checked to be empty.
- A running GPU-hour tally is kept against the 100 GPU-h cap. If a projection exceeds the cap, stop and ask.
- No commits unless asked.
- `data/` is gitignored and goes to Drive per `research/DATA.md`.
- At the end: `research/18_harness_wait/README.md` in the house style, and a row in `research/README.md`.

## Verification

1. **CPU tests.**
   - `research/16_epic_harness/harness_eval/.venv/bin/python -m pytest tests -q`: all existing tests plus the
     new `tests/test_s15_server_hold.py`. With every flag unset, the system prompt and tool list must be
     byte-identical, `BUILTIN_TOOLS` must still be 26, and no HTTP is made.
   - With flags set:
     - `wait` runs after the other tools and is capped;
     - one status line per round, none on retries;
     - holds only at round ends;
     - the status client fails open.
   - `/data/fritz/venv/bin/python research/11_multiuser_kv/test_mu_scheduler.py`, ported to 0.31.
   - `research/18_harness_wait/test_wt_scheduler.py`, with the real 0.31 scheduler on CPU:
     - decisions identical to stock;
     - Σ resident equals the hashed blocks with an owner;
     - `prefix_tok` ≤ the ideal hit;
     - the gate bounds active sessions.
   - `test_wt_e2e.py`: real s15 sessions against a fake server with scripted `/wt/status` and `/metrics`, run
     for every arm.
   - `test_multiuser.py` still passes.
2. **S1 smoke gates.**
   - Evictions match KV-event removals.
   - Preemptions match vLLM's counter.
   - The admission hit equals `cache_read_input_tokens` on 100 % of calls.
   - Status `age_ms` p99 < 500 ms.
   - The `wait` and `hold` spans appear in the traces.
3. **Pilot gates before the prereg.**
   - `none16` thrashes: hit rate < 50 %, evicted recompute > 30 % of GPU time. `none8` does not.
   - The model arms make valid `wait` calls.
   - Holds trigger with no deadlock.
4. **Main.** For every cell:
   - no `err` records;
   - 100 % of client calls joined to the server;
   - the window completed;
   - the GPUs empty afterwards.

   `wt_analyze` must regenerate byte-identically, and `wt_verify` must agree cell by cell.

## Critical files

- **Modify:**
  - `s15_integrated_harness/code.py`
  - `research/11_multiuser_kv/mu_scheduler.py`, `test_mu_scheduler.py`, `mu_session.py` and `mu_scrape.py`
- **New:**
  - `s15_integrated_harness/hold_runtime.py`
  - `tests/test_s15_server_hold.py`
  - in `research/18_harness_wait/`: `wt_scheduler.py`, `wt_server_ext.py`, `wt_workloads.py`, `wt_sweep.py`,
    `wt_pipeline.py`, `wt_analyze.py`, `wt_verify.py`, the `test_wt_*.py` files, `README.md` and `prereg.md`
- **Reuse:**
  - `research/11_multiuser_kv/{mu_sweep,mu_analyze,mu_workloads}.py`
  - `research/common/profile_run.py` (`VllmMetrics.scrape`, `:647-681`)
  - `research/16_epic_harness/harness_eval/serve/serve.py`
  - `research/16_epic_harness/harness_eval/epic/epic_server_ext.py`
