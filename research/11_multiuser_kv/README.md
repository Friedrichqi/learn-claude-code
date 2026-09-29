# 11 · Multi-user KV contention: is it the bottleneck of a harness that serves many users?

> **Status:** in progress (2026-09-26): code, CPU tests and the end-to-end fake-server test done; pilot
> job 102748 queued.
>
> **Dates:** 2026-09-26 →.
>
> **Model / server:** `Qwen/Qwen3-32B` (bf16, dense GQA, 256 KiB of KV per token), native
> `--max-model-len 40960`, thinking off, served by vLLM 0.29.0 at TP=1 on one H200 per server. The
> cluster's job-submit plugin routes every 1-GPU job to the RTX 6000 pool, so each Slurm job holds two
> H200s and runs two independent cells, one server and its own users per GPU.
>
> **Commits:** — (uncommitted).
>
> **Presented:** —

**Contents** — Short answer · §1 Question · §2 Method (§2.1 users and sessions, §2.2 harness settings,
§2.3 conditions, §2.4 instrumentation, §2.5 trace replay, §2.6 analysis, §2.7 verification) · §3 Results ·
§4 Conclusions · §5 Caveats · §6 Reproduction · Data inventory

**Files in this folder** (`research/11_multiuser_kv/`)

| path | purpose |
|---|---|
| `README.md` | this document |
| `mu_workloads.py` | session specs (10 codebases × 4-turn scripts from 10_rope_shift's templates), think-time draws, the condition table (live cells and replay policies); `build / check / cond`; offline |
| `mu_session.py` | one simulated user: a 4-turn s15 session through `research/common/profile_run.py`, with request capture, vLLM tags, per-call timeout, lead-call budget, and the context-overflow shim; starts a harness session |
| `mu_sweep.py` | one closed-loop cell: N user slots (lanes) against one server, warm-up/drain fillers, retries, resume, stall watchdog; starts harness sessions |
| `mu_scheduler.py` | the vLLM scheduler subclass loaded with `--scheduler-cls`: per-request, per-step and per-eviction log, the ideal prefix hit, and the optional TTL-pin policy; imported by vLLM |
| `mu_scrape.py` | 1 Hz `/metrics` + GPU + CPU poller; KV-event counter for the smoke test |
| `mu_replay.py` | trace replay of recorded cells under alternative policies (`prepare / run / show`); `prepare` is offline, `run` calls a server |
| `mu_analyze.py` | the tables (`data/multiuser_live/multiuser_tables.md`, `data/multiuser_replay/replay_tables.md`); offline |
| `test_multiuser.py` | CPU tests, including an end-to-end run of real harness sessions and a replay against a fake server |
| `test_mu_scheduler.py` | CPU tests of `mu_scheduler` with vLLM's real scheduler in the loop (fake model outputs) |
| `mu_pipeline.sh`, `mu.sbatch` | one job = two H200 cells: serve → scrape → sweep or replay → stop, per GPU, resumable |
| `data/multiuser_live/` | sessions manifest, `cells/<cond>/` (runs, server logs, scrapes, cell logs), job logs, tables |
| `data/multiuser_replay/` | replay plans (rendered prompt ids) and replay tables |

Shared, outside this folder: `research/common/profile_run.py` (new flags `--think-seconds`, `--no-snip`,
`--memory`, `--tool-result-budget`, `--reactive-compact-limit`; defaults unchanged),
`research/10_rope_shift/{workloads,live_run,run_sweep,render}.py` (templates, capture/finalize, lanes,
prompt rendering), `s15_integrated_harness/code.py` (the harness, unmodified).

## 1. Question

When one harness serves many users, a user's KV cache sits idle while that user waits for tool results
or for the human's next message. Serving other users meanwhile can evict it, so the user's next call
re-prefills its whole history; admitting the returning user can preempt requests that are running;
its long re-prefill slows everyone's decode. A policy that lets the ongoing session finish first might be
better. Is this the bottleneck of a multi-user harness, and if not, what is?

## 2. Method

### 2.1 Users and sessions

- **A user is one harness session** (one process, one trace): four turns on one codebase, solo (no
  teammates). Turns 1-2 are two EXPLAIN templates (inventory, trace one call, orientation note), turns
  3-4 two MODIFY templates (BUILD_TAG + changelog, rename a function everywhere, docstrings for a module),
  all from `research/10_rope_shift/workloads.py`, on the same ten codebases (nine site-packages copies
  and the harness itself). The sandbox persists across a session's turns.
- **Think time** before each follow-up turn: lognormal, median 30 s, σ 0.8 (p10 11 s, p90 79 s in the
  drawn set), capped at 240 s, drawn per spec, so every condition replays the same pauses. It is traced
  as the harness's own `input_wait` span.
- **Closed loop:** N users; when a session ends, its user starts the next spec after U(5, 30) s. First
  starts are staggered over 300 s; warm-up fillers (stopped at a random fraction of a session) and drain
  fillers keep N users active around the counted sessions and are never counted.
- **Balance:** specs come in blocks of 8 (8 of the 10 codebases, rotating E- and M-template pairs); a
  condition with M sessions runs specs 0..M−1, so conditions are nested and paired on task mix and think
  draws.
- **Load-independent budget:** at most 100 lead calls per turn and 300 per session, refused before any
  HTTP request; a 4 h backstop; a per-call timeout of 3600 s (the SDK's 600 s default fired 30 times in
  10_rope_shift) and no client retries.

### 2.2 Harness settings: the harness context is never shorter than the model's

- **Context limit = the model's input window.** The harness counts characters of JSON-serialized
  messages; the model's window is 40,960 tokens, of which vLLM reserves the lead's max_tokens (8,000) for
  output, and the system prompt and tool schemas (~1,795 tokens) are outside the harness's count. On
  15,403 lead calls of 10_rope_shift, tokens = 1,795 + chars / r with r = 4.61 chars/token at the median
  and 4.33 at the 5th percentile for contexts above 30k characters. The limit is therefore
  (40,960 − 8,000 − 1,795) × 4.33 ≈ 134,000 characters: the harness's own compaction pipeline
  (micro_compact → fit_tool_results → compact_history) starts at 94-100% of the model's input window, and
  95% of prompts fit. [changed during the pilot, 2026-09-26: the plan kept the 512,000-character default
  and relied on reactive compaction alone, but `reactive_compact` keeps the last five messages verbatim,
  so after a large file read the retried call overflows again and the turn ends in an error — 2 of the 3
  overflowing turns of the first pilot8 sessions. Those pilot sessions are kept as `pilot8` and are not
  used in the results.]
- `snip_compact` is off: it archives the middle of the history on every round once a history passes 50
  messages, which rewrites the prefix after message 3 every round (a message-count limit that would cut
  the context far below the model's window).
- `--no-timestamp`: the per-second `Current time` line in the system prompt otherwise changes the prefix
  on every call (the n16ts arm keeps it, to measure that cost).
- A prompt that still does not fit (the 5% tail, or a call re-sent with 16,000 output tokens after a
  max_tokens stop) gets vLLM's 400, which the harness's `is_prompt_too_long_error` does not recognise;
  `mu_session.py` re-raises it as "prompt is too long ..." so `reactive_compact` runs (at most 3 per turn
  instead of 1). The message carries no digits: the harness retries any error whose text contains "429"
  or "529".
- The per-message tool-result budget scales with the window (200,000 → 64,000 characters, the harness's
  own ratio of budget to its 512,000-character design limit): a larger read is persisted and previewed.
- Memory is off in the sweep (no recall, no catalog, no extraction calls: the system prompt is identical
  across turns) and on as shipped in the n16mem arm.

### 2.3 Conditions

| stage | cond | N | counted sessions | question |
|---|---|---|---|---|
| 1 | n01a + n01b | 1 | 24 | solo reference |
| 1 | n04, n08, n12, n16, n32 | 4 / 8 / 12 / 16 / 32 | 40 / 56 / 36 / 80 / 96 | load sweep (the 258,192-token pool, 16,137 blocks, ≈ 8 users' working set) |
| 2 | n08kvh | 8 | 24 | KV pool halved at the same batch, just below the knee: the causal test of KV capacity |
| 2 | n08mem | 8 | 40 | memory on as shipped |
| 2 | n08ts | 8 | 40 | memory on + timestamp on: the as-shipped prompt |
| 3 | s3 | 16 or 32 | ~48 | the best replay policy, live |

[changed 2026-09-27: stage 2 was planned at N=16 (n16kvh, n16mem, n16ts). N=16 turned out to be deep in
the thrashing regime (interim: 73% of prompt tokens re-prefilled after eviction, median session 103 min
against 16 min at N=8), where each contrast would be drowned by thrashing and cost ~7 GPU-hours; the
contrasts moved to N=8, just below the knee, and n12 was added to locate the knee. The N=16 cells carry a
`COMPLETE` marker saying they were skipped and hold no data.]

All cells: `--enable-prefix-caching --enable-prompt-tokens-details --enable-auto-tool-choice
--tool-call-parser hermes --reasoning-parser qwen3`, thinking off, `--kv-cache-metrics`, the MU scheduler;
everything else vLLM default (FCFS, recompute preemption, watermark 0, 8192 batched tokens).

### 2.4 Instrumentation

- **Client** (`mu_session.py`): every call carries `X-Request-Id: <label>~a<attempt>~<call>~<purpose>~<agent>`
  and `X-Session-ID`; the full request, send/receive wall clock and usage are captured; `response.id`
  (`chatcmpl-<X-Request-Id>`) checks the join.
- **Server** (`mu_scheduler.py`, a subclass of vLLM's AsyncScheduler that makes no decision unless a
  policy is switched on): per request the arrival, the **ideal hit** (longest leading run of its block
  hashes ever committed on the server), the hit it would get at arrival and the hit it got at admission
  (so evicted-while-paused and evicted-while-queued are separate), preemptions, first token, finish; per
  step the stamps (GPU time ≈ u0[n] − max(u0[n−1], launch[n])), prefill chunks classified fresh / evicted
  recompute / preemption recompute, decode requests, free and cached blocks, the head of the queue and
  why it waits; per eviction the owning chain, whether the block was still reusable, and how long the
  owner had been idle.
- **Scraper**: vLLM metrics, GPU and CPU once a second.

### 2.5 Trace replay

The recorded n16 and n32 cells are replayed (`mu_replay.py`) with each call's exact prompt ids, recorded
output length (`ignore_eos`) and recorded gaps, closed-loop in the recorded slot order, under: default
(twice, the noise floor), `--watermark 0.05/0.10`, session FCFS (priority = session arrival), TTL pins
(tool pauses 10 s; any pause 60 s / 240 s), session FCFS + pins, a client-side turn gate (at most N/2
sessions mid-turn), native CPU offload, and think time ×0.5 / ×2. Generated tokens differ from live, which
leaves reuse unchanged: Qwen3's template re-renders earlier assistant turns without the empty think block
of the generation prompt, so live KV of generated tokens is never reused either.

### 2.6 Analysis

Per lead call: the latency waterfall (HTTP in, template + tokenize, queue, prefill, decode, response);
prompt tokens as hit / evicted / fresh after a harness edit / new; GPU time by class from a Huber-fit
step model; every second of the call's server time charged to what the GPU was doing (own work, other
requests' decode, other requests' prefill by class, not scheduled) and every second of queueing to the
reason the head waited. Per session: turn latency, think, tool and harness time; per cell: steady-window
throughput and eviction survival by pause kind and length. KV contention is the bottleneck at load N if
evicted + preemption recompute and KV-capacity queueing are the largest share of excess latency and of
wasted GPU time and n16kvh moves them as predicted; otherwise the largest share is named.

### 2.7 Verification

CPU: `test_mu_scheduler.py` (identical decisions to the stock scheduler; consistent logs; pins protect,
release and leak nothing), `test_multiuser.py` (flag installers, shim, ids, budget, workload balance,
metrics parser; end-to-end: two real 4-turn harness sessions and their replay against a fake server).
GPU: a forced-contention smoke (16k window, 2,600-block pool, KV events on) checks the scheduler log
against vLLM's own counters and events; replay fidelity and observer-overhead A/B before any policy
result is used.

## 3. Results

(pending)

## 6. Reproduction

```bash
python research/11_multiuser_kv/test_multiuser.py
python research/11_multiuser_kv/test_mu_scheduler.py
python research/11_multiuser_kv/mu_workloads.py build
sbatch --export=NONE --time=04:00:00 research/11_multiuser_kv/mu.sbatch smoke,pilot1 pilot8
sbatch --export=NONE research/11_multiuser_kv/mu.sbatch n01a,n04 n01b,n08
sbatch --export=NONE research/11_multiuser_kv/mu.sbatch n16,n16kvh n32,n16mem
python research/11_multiuser_kv/mu_analyze.py --smoke
python research/11_multiuser_kv/mu_analyze.py
python research/11_multiuser_kv/mu_replay.py prepare --src n16 && python research/11_multiuser_kv/mu_replay.py prepare --src n32
sbatch --export=NONE research/11_multiuser_kv/mu.sbatch r_n16_default,r_n16_wm05,... r_n16_default2,...
python research/11_multiuser_kv/mu_analyze.py --replay
```
