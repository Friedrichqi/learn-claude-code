# 11 · Multi-user KV contention: is it the bottleneck of a harness that serves many users?

> **Status:** complete. 502 counted user sessions (4-turn harness traces) in 12 live cells, plus 410
> filler sessions that kept the load constant around them and 27 pilot / smoke sessions: 947 harness
> traces and 97,498 model calls in all. 14 trace-replay policy runs, 3 replay validation runs. Answer in
> the Short answer and §4.
>
> **Dates:** code and tests 2026-09-26; live runs 2026-09-26 21:23 → 2026-09-27 14:05 EDT; replays
> 2026-09-27 10:08 → 16:59; final analysis 2026-09-29.
>
> **Slurm:** 2-GPU H200 jobs, one independent cell per GPU (the job-submit plugin routes every 1-GPU job
> to the RTX 6000 pool): 102748 (pilot + smoke), 102767 (n01a → n04 | n01b → n08), 102768 (n16 | n32),
> 102994 (n08kvh → n08mem | n12 → n08ts), 103142 (s3gate16 | s3gate32), 102937 (smoke2 and the replay
> validation), 103233 / 103234 (replays of n16 / n32); CPU jobs 102936, 103121, 103232, 109480-109533
> (replay plans, analysis). 91 GPU-hours.
>
> **Model / server:** `Qwen/Qwen3-32B` (bf16, dense GQA, 256 KiB of KV per token), native
> `--max-model-len 40960`, thinking off, vLLM 0.29.0 at TP=1 on one H200 (KV pool 16,137 blocks =
> 258,192 tokens at the default 0.92 memory utilization).
>
> **Commits:** code and data in `d0109f13` (2026-09-29); this README's results and the analyzer fixes of
> 2026-09-29 (throughput definition, paired table) are uncommitted.
>
> **Presented:** —

**Contents** — Short answer · §1 Question · §2 Method · §3 Results (§3.1 what ran, §3.2 the load curve,
§3.3 where a call's time goes, §3.4 the mechanism above the knee, §3.5 the causal test, §3.6 the
harness's own cache busting, §3.7 policies, §3.8 verification) · §4 Conclusions · §5 Caveats ·
§6 Reproduction · Data inventory · Changes during the run

## Short answer

- **KV contention is the bottleneck, but only past a knee, and past the knee it dominates.** The knee
  is where the users' combined working sets exceed the KV pool: 8-12 users here (258k-token pool,
  about 20-30k tokens of context per user).
- **Below the knee (N ≤ 8) the GPU is saturated by useful work.**
  - A user's calls slow down 1.7-2.4× from sharing steps with other users. 93-94% of that is compute
    sharing: other users' prefill chunks in my steps cost 1.6-2.8 s per call, decode batching 0.5-1.6 s.
  - Eviction touches 0-1% of prompt tokens and costs ≤ 4% of the excess latency.
  - Throughput grows to 3,077 lead calls/h (95 turns/h) at N=8.
- **Above the knee the prefix cache stops working and the server thrashes.**
  - At N ≥ 16 the prefix-hit rate falls from 86% to 1-2%, and 84-85% of prompt tokens are histories
    re-prefilled because their KV had been evicted.
  - Re-prefilling evicted prefixes takes 65-74% of GPU time.
  - Lead calls take 28-112 s instead of 3-4 s solo, and the work throughput drops by two thirds:
    3,077 → 976-990 lead calls/h at N=16-32, with twice to four times the users.
- **The mechanism is the one you suspected, minus preemption.**
  - The running requests' own contexts fill the pool, so a paused user's prefix is gone even across
    sub-second tool pauses.
  - The returning call re-prefills its whole history. Those re-prefills fill every step's token budget:
    they stall everyone else's decode (13.6-23.7 s per call, the largest single cost at N=12-16) and
    make new requests queue (3-75 s per call; at N=32 the queue is the largest cost).
  - Preemption plays no part (0.00 s per call at every load). Frontend CPU plays none either (≤ 0.9 s).
- **Causal test.** Halving the pool at N=8 (8,068 blocks) turns the same 8 users into the thrashing
  regime:
  - hit rate 87% → 12%;
  - sessions 3.4× longer (paired, 95% CI 1.6-4.6);
  - 3,077 → 978 lead calls/h, the same as doubling the users on the full pool.
- **"Let the ongoing sessions finish first" is the right policy.** A server-side session gate (at most 8
  sessions active, the pool's worth; the others wait queued) restores throughput:
  - Live at N=16: 976 → 3,394 lead calls/h (3.5×). At N=32: 990 → 3,793 (3.8×).
  - Sessions finish 3.3-3.8× faster, gate waits included.
  - Turn p95 falls 5.5-6.3×.
  - The replay study agrees: 2.8-3.1× calls/h.
  - A turn gate works only when sized to the pool, not to the number of users: N/2 = 8 at N=16 gives
    2.9×, N/2 = 16 at N=32 gives 1.05×.
  - CPU offload (swap instead of recompute) works while the CPU tier holds the working set: 2.8× at
    N=16, 1.1× at N=32.
  - Session-FCFS priority helps 1.3-1.4×, TTL pins 1.2×, and smaller prefill chunks not at all.
- **The harness can cause the same damage by itself.** The as-shipped per-second `Current time` line in
  the system prompt busts the prefix on every call:
  - At N=8, with no eviction, hit rate is 2% and work throughput 935 lead calls/h, as low as thrashing.
  - The memory feature costs almost nothing at N=8 (3,084 calls/h).
  - Compaction at the model's window accounts for 88-92% of the harness's own re-prefill, 8-13% of
    prompt tokens, at every load.

## Files in this folder (`research/11_multiuser_kv/`)

| path | purpose |
|---|---|
| `README.md` | this document |
| `mu_workloads.py` | session specs (10 codebases × 4-turn scripts from 10_rope_shift's templates), think-time draws, the condition table (live cells and replay policies); `build / check / cond`; offline |
| `mu_session.py` | one simulated user: a 4-turn s15 session through `research/common/profile_run.py`, with request capture, vLLM tags, per-call timeout, lead-call budget, and the context-overflow shim; starts a harness session |
| `mu_sweep.py` | one closed-loop cell: N user slots (lanes) against one server, warm-up/drain fillers, retries, resume, stall watchdog; starts harness sessions |
| `mu_scheduler.py` | the vLLM scheduler subclass loaded with `--scheduler-cls`: per-request, per-step and per-eviction log, the ideal prefix hit; optional policies: TTL pins (`MU_PIN`) and the session gate (`MU_GATE`); imported by vLLM |
| `mu_scrape.py` | 1 Hz `/metrics` + GPU + CPU poller; KV-event counter for the smoke test |
| `mu_replay.py` | trace replay of recorded cells under alternative policies (`prepare / run / show`); `prepare` is offline, `run` calls a server |
| `mu_analyze.py` | the tables (`data/multiuser_live/multiuser_tables.md`, `data/multiuser_replay/replay_tables.md`); `--smoke` for the smoke checks; offline |
| `test_multiuser.py` | CPU tests, including an end-to-end run of real harness sessions and their replay against a fake server |
| `test_mu_scheduler.py` | CPU tests of `mu_scheduler` with vLLM's real scheduler in the loop (fake model outputs): decisions identical to stock, log consistency, pins, the session gate |
| `mu_pipeline.sh`, `mu.sbatch` | one job = two H200 cells: serve → scrape → sweep or replay → stop, per GPU, resumable |
| `mu_cpu.sbatch` | CPU-only jobs (replay plans, analysis): the login node allows 2 cores and 8 GB per user |
| `data/multiuser_live/` | sessions manifest, `cells/<cond>/` (runs, server logs, scrapes, cell logs), job logs, tables |
| `data/multiuser_replay/` | replay plans (rendered prompt ids) and replay tables |

Shared, outside this folder: `research/common/profile_run.py` (new flags `--think-seconds`, `--no-snip`,
`--memory`, `--tool-result-budget`, `--reactive-compact-limit`; defaults unchanged),
`research/10_rope_shift/{workloads,live_run,run_sweep,render}.py` (templates, capture/finalize, lanes,
prompt rendering), `s15_integrated_harness/code.py` (the harness, unmodified).

## 1. Question

When one harness serves many users, a user's KV cache sits idle while that user waits for tool results
or for the human's next message. Serving other users meanwhile can evict it, so the user's next call
re-prefills its whole history; admitting the returning user can preempt requests that are running; its
long re-prefill slows everyone's decode. A policy that lets the ongoing session finish first might be
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
  as the harness's own `input_wait` span. Tool pauses are the harness's real tool times (milliseconds;
  bash up to seconds).
- **Closed loop:** N users; when a session ends, its user starts the next spec after U(5, 30) s. First
  starts are staggered over 300 s; warm-up fillers (stopped at a random fraction of a session) and drain
  fillers keep N users active around the counted sessions and are not counted.
- **Balance:** specs come in blocks of 8 (8 of the 10 codebases, rotating E- and M-template pairs); a
  condition with M sessions runs specs 0..M−1, so conditions are nested and paired on task mix and think
  draws.
- **Load-independent budget:** at most 100 lead calls per turn and 300 per session, refused before any
  HTTP request; a 4 h session backstop; a per-call timeout of 3600 s and no client retries.

### 2.2 Harness settings: the harness context is never shorter than the model's

- **Context limit = the model's input window.** The harness counts characters of JSON-serialized
  messages; the model's window is 40,960 tokens, of which vLLM reserves the lead's max_tokens (8,000) for
  output, and the system prompt and tool schemas (~1,795 tokens) are outside the harness's count. On
  15,403 lead calls of 10_rope_shift, tokens = 1,795 + chars / r with r = 4.61 chars/token at the median
  and 4.33 at the 5th percentile for contexts above 30k characters. The limit is therefore
  (40,960 − 8,000 − 1,795) × 4.33 ≈ 134,000 characters: the harness's own compaction pipeline
  (micro_compact → fit_tool_results → compact_history) starts at 94-100% of the model's input window.
- `snip_compact` is off: it archives the middle of the history on every round once a history passes 50
  messages (a message-count limit that would rewrite the prefix every round).
- `--no-timestamp`: the per-second `Current time` line in the system prompt otherwise changes the prefix
  on every call; the n08ts arm keeps it.
- A prompt that still does not fit gets vLLM's 400, which the harness's `is_prompt_too_long_error` does
  not recognise; `mu_session.py` re-raises it as "prompt is too long ..." (digit-free: the harness
  retries any error text containing "429" or "529") so `reactive_compact` runs, at most 3 times per turn.
- The per-message tool-result budget scales with the window (200,000 → 64,000 characters).
- Memory is off in the sweep (no recall, no catalog, no extraction calls: the system prompt is identical
  across turns) and on as shipped in the n08mem and n08ts arms.

### 2.3 Conditions

| stage | cond | N | counted sessions run | question |
|---|---|---|---|---|
| 1 | n01a + n01b | 1 | 12 + 12 | solo reference |
| 1 | n04, n08, n12, n16, n32 | 4 / 8 / 12 / 16 / 32 | 40 / 56 / 36 / 79 / 89 | load sweep |
| 2 | n08kvh | 8 | 24 | KV pool halved (8,068 blocks) at the same batch: the causal test of KV capacity |
| 2 | n08mem | 8 | 40 | memory on as shipped |
| 2 | n08ts | 8 | 18 (of 40 planned) | memory on + timestamp on: the as-shipped prompt |
| 3 | s3gate16, s3gate32 | 16 / 32 | 48 / 48 | the session gate (at most 8 sessions active), live, paired with n16 / n32 |

All cells: `--enable-prefix-caching --enable-prompt-tokens-details --enable-auto-tool-choice
--tool-call-parser hermes --reasoning-parser qwen3`, thinking off, `--kv-cache-metrics`, the MU scheduler;
everything else vLLM default (FCFS, recompute preemption, watermark 0, 8,192 batched tokens per step).

### 2.4 Instrumentation

- **Client** (`mu_session.py`): every call carries `X-Request-Id: <label>~a<attempt>~<call>~<purpose>~<agent>`
  and `X-Session-ID`; the full request, send/receive wall clock and usage are captured; `response.id`
  (`chatcmpl-<X-Request-Id>`) checks the join (100% of calls joined in every cell).
- **Server** (`mu_scheduler.py`, a subclass of vLLM's AsyncScheduler that makes no decision unless a
  policy is switched on): per request the arrival, the **ideal hit** (longest leading run of its block
  hashes ever committed on the server, i.e. what an infinite cache would return), the hit it would get at
  arrival and the hit it got at admission, preemptions, first token, finish; per step the stamps (GPU
  time ≈ u0[n] − max(u0[n−1], launch[n])), prefill chunks classified fresh / evicted recompute /
  preemption recompute, decode requests, free and cached blocks, the head of the queue and why it waits;
  per eviction the owning chain, whether the block was still reusable, and how long the owner had been
  idle.
- **Scraper**: vLLM metrics, GPU and CPU once a second.

### 2.5 Trace replay

The recorded n16 and n32 cells are replayed (`mu_replay.py`) closed-loop, with the same N and slot order,
each call's exact prompt ids (rendered from the capture: 110 sessions / 11,692 calls for n16, 165 /
11,774 for n32, 0 length mismatches against the server's reported prompt tokens),
its recorded output length (`ignore_eos`) and its recorded gaps, for 100 minutes, measured after a
10-minute warm-up. Policies run: default (twice for n16: the noise floor); session FCFS (`--scheduling-policy
priority`, priority = session arrival); TTL pins for tool pauses (`MU_PIN`, 10 s); a client-side turn gate
(at most N/2 sessions mid-turn); the server-side session gate (`MU_GATE` = 8, and 6 at N=32); native CPU
offload (128 GiB, `--kv-offloading-size 128`); smaller prefill chunks (`--max-num-batched-tokens 2048`,
n16). Generated tokens differ from live, which leaves reuse unchanged: Qwen3's template re-renders
earlier assistant turns without the empty think block of the generation prompt, so live KV of generated
tokens is never reused either.

### 2.6 Analysis

- **Per lead call:** the latency waterfall (HTTP in, template + tokenize, queue, prefill, decode,
  response); prompt tokens as hit / evicted (ideal − hit) / after a harness edit / new; GPU time by class
  from a Huber-fit step model (t = 18.5 ms + 96.9 µs per prefill token + attention and decode-context
  terms; R² 1.000 over 2.03 M steps, median error 1.9%); every second of the call's server time charged
  to what the GPU was doing in it, and every second of queueing to the reason the head of the queue
  waited.
- **Excess over solo:** observed latency minus a solo model (the same call alone with an ideal cache;
  it reproduces the solo cells to 0.03 s), split into frontend, queue (KV vs other), own recompute,
  preemption, batching (others' decode), interference (others' prefill, fresh vs recompute).
- **Throughput:** completed lead calls (and turns) of all sessions during the periods with at least N−1
  sessions live, per hour. Lead calls/h is the primary measure: at N=32, 40 sessions hit the 4 h backstop,
  after which each remaining turn ends at once and would inflate turns/h.
- **Paired sessions:** the same specs under two conditions, session wall time (think pauses and gate
  waits included), median ratio with a bootstrap CI.

## 3. Results

The full tables are in `data/multiuser_live/multiuser_tables.md` (T1-T10) and
`data/multiuser_replay/replay_tables.md`. The tables below are excerpts.

### 3.1 What ran

| cond | N | counted sessions | status (ok / budget / backstop / killed at job end) | lead calls | overflows → reactive compactions |
|---|---|---|---|---|---|
| n01a + n01b | 1 | 24 | 15 / 9 / 0 / 0 | 2,767 | 32 → 29 |
| n04 | 4 | 40 | 24 / 16 / 0 / 0 | 4,837 | 35 → 30 |
| n08 | 8 | 56 | 30 / 26 / 0 / 0 | 7,360 | 59 → 52 |
| n12 | 12 | 36 | 18 / 18 / 0 / 0 | 4,601 | 28 → 23 |
| n16 | 16 | 80 | 41 / 32 / 6 / 1 | 9,759 | 77 → 68 |
| n32 | 32 | 96 | 39 / 10 / 40 / 7 | 9,574 | 71 → 63 |
| n08kvh | 8 | 24 | 19 / 5 / 0 / 0 | 2,192 | 34 → 28 |
| n08mem | 8 | 40 | 18 / 22 / 0 / 0 | 5,389 | 36 → 31 |
| n08ts | 8 | 18 | 13 / 5 / 0 / 0 | 1,577 | 7 → 6 |
| s3gate16 | 16 | 48 | 24 / 24 / 0 / 0 | 6,532 | 58 → 50 |
| s3gate32 | 32 | 48 | 20 / 28 / 0 / 0 | 7,066 | 38 → 33 |

*Key.* **budget**: a turn reached 100 lead calls (mostly the turn-1 module inventory), the same cap in
every cell. **backstop**: the session ran 4 h and its remaining turns were refused. **killed**: the
session was running when the 12 h job ended. No cell logged a scheduler error. *What it says:* the
workload is the same everywhere (budget hits are common in every cell); only at N=16 and N=32 do
sessions run into the 4 h backstop, so those cells' slowdowns are underestimates.

### 3.2 The load curve: throughput rises to N=8, then collapses

| cond | N | lead calls / h | turns / h | lead-call latency p50 / mean (s) | turn latency p50 / p95 (s) | session vs the row below it (paired wall ratio, 95% CI) |
|---|---|---|---|---|---|---|
| n01a / n01b | 1 | 719 / 752 | 23 / 29 | 1.1 / 3.3-3.8 | 43-49 / 368-529 | |
| n04 | 4 | 2,030 | 69 | 2.1 / 5.8 | 62 / 773 | n04 vs n01a: 1.26 (0.61-2.50) |
| n08 | 8 | **3,077** | **95** | 2.4 / 8.4 | 137 / 1,086 | n08 vs n04: 1.39 (1.10-2.54) |
| n12 | 12 | 1,496 | 45 | 13.2 / 28.2 | 496 / 3,448 | n12 vs n08: 2.78 (2.07-4.12) |
| n16 | 16 | 976 | 33 | 33.9 / 56.4 | 970 / 5,603 | n16 vs n08: **7.62 (5.34-8.79)** |
| n32 | 32 | 990 | 43 | 94.4 / 112.0 | 1,706 / 9,340 | n32 vs n16: 1.73 (1.35-2.00) |

*Key.* **lead calls / h**: completed lead-agent model calls of all sessions per hour while N users were
live (work done by the GPU for the users). **turns / h**: user requests answered per hour. **turn
latency**: from a user's message to the harness's final answer. **paired wall ratio**: median, over the
same specs, of session wall time in the row's cell over the other cell's. *What it says:* one H200
serves up to about 8 of these users with a 1.7-2.4× per-call slowdown and rising throughput; from 12
users on, every extra user reduces the total work done, and a session takes 7.6× as long at N=16 as at
N=8. Past the knee the curve is flat (N=16 and N=32 do the same work per hour); the extra users only
queue.

### 3.3 Where a lead call's time goes

Excess over the solo model, seconds per lead call (T4b):

| cond | latency | solo model | excess | queue: KV | queue: step budget | own recompute (evicted) | others' decode (batching) | others' fresh prefill | others' recompute prefill | preemption | frontend | KV contention share of excess |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| n04 | 5.79 | 3.51 | 2.28 | 0.00 | 0.01 | 0.00 | 0.52 | 1.56 | 0.01 | 0.00 | 0.09 | 0% |
| n08 | 8.42 | 3.57 | 4.84 | 0.00 | 0.01 | 0.03 | 1.63 | 2.78 | 0.15 | 0.00 | 0.09 | 4% |
| n12 | 28.17 | 2.82 | 25.36 | 1.54 | 1.63 | 1.47 | 1.79 | 4.56 | 13.58 | 0.00 | 0.56 | 65% |
| n16 | 56.40 | 3.19 | 53.21 | 9.12 | 9.85 | 2.75 | 2.06 | 4.61 | **23.73** | 0.00 | 0.78 | 67% |
| n32 | 112.02 | 2.67 | 109.35 | 32.21 | 43.05 | 2.72 | 1.67 | 4.95 | 23.55 | 0.00 | 0.81 | 53% |

GPU time by class, share of wall time (T3):

| cond | evicted-prefix recompute | fresh prefill | decode | idle |
|---|---|---|---|---|
| n01a | 0% | 10% | 66% | 24% |
| n04 | 0% | 39% | 61% | 0% |
| n08 | 2% | 41% | 56% | 0% |
| n12 | 65% | 19% | 16% | 0% |
| n16 | **74%** | 14% | 12% | 0% |
| n32 | **74%** | 16% | 10% | 0% |

*Key.* **solo model**: the same call on an idle server with an ideal cache (fresh prompt tokens in
8,192-token chunks plus its decode, from the step model; matches the solo cells within 0.03 s).
**queue: KV / step budget**: waiting for admission because free blocks were short, or because the step's
8,192-token budget was taken by other requests' prefill (either as the head of the queue or behind it).
**others' recompute prefill**: time of the steps my request was in that went to other requests
re-prefilling evicted prefixes. **KV contention share**: (queue KV + own recompute + preemption + others'
recompute) / excess; it does not count the step-budget queueing, most of which is also recompute (at N=16,
84% of prefill GPU time is evicted-prefix recompute), so it is a lower bound. **fresh prefill** includes compaction re-prefill.
*What it says:* up to N=8 the whole slowdown is sharing a busy GPU: other users' new prompt tokens and
decodes sharing my steps. At N ≥ 12 the dominant cost is other users re-prefilling their evicted
histories inside my steps (13.6-23.7 s per call), then queueing for KV and for step budget. That is the
"ongoing requests are straggled" effect, and it comes from recompute, not from preemption (0.00 s per call
everywhere) or the frontend (≤ 0.9 s).

### 3.4 The mechanism above the knee

Prompt tokens by class, share of all lead-call prompt tokens (T3):

| cond | hit | evicted (had been computed, was gone) | after a harness edit | new |
|---|---|---|---|---|
| n01a | 86% | 0% | 11% | 3% |
| n08 | 87% | 1% | 8% | 4% |
| n12 | 45% | 43% | 8% | 4% |
| n16 | 2% | **85%** | 10% | 4% |
| n32 | 1% | **84%** | 10% | 4% |

Survival of a session's own reusable prefix across its pauses: mean share still cached when the next
request arrives (T6, excerpt):

| cond | tool pause < 1 s (all kept) | think 15-30 s | think 30-60 s | think 60-120 s |
|---|---|---|---|---|
| n04 | 1.00 (100%) | 1.00 | 0.97 | 0.70 |
| n08 | 1.00 (100%) | 0.76 | 0.58 | 0.04 |
| n12 | 0.95 (79%) | 0.07 | 0.05 | 0.00 |
| n16 | 0.89 (62%) | 0.00 | 0.00 | 0.00 |
| n32 | 0.89 (64%) | 0.00 | 0.00 | 0.00 |

Queueing (T8): mean queue per lead call 0.01 s at N ≤ 8, 3.2 s at N=12, 19 s at N=16, 75 s at N=32. At
N=16 the head of the queue waited for free KV blocks 48% of the time (9% as head, 39% behind a blocked
head) and for step budget 52% (11% + 41%); at N=32 43% and 57%.

*Key.* **evicted**: tokens inside the ideal hit that the request did not hit, i.e. recomputed only
because their KV had been evicted. **after a harness edit**: the prompt diverged from the chain's
previous prompt before its reusable end (88-92% of these tokens come from the harness's proactive
compaction at the window, T7). **all kept**: share of pauses that lost nothing. *What it says:* at N=8
the cache already loses think pauses of 30 s or more, but that re-prefill happens once per turn and costs
1% of the tokens. At N ≥ 12 the pool is held by the running requests' own contexts (about 10 running
requests × 25k tokens fill 258k), so every finished request's blocks are reused almost immediately: even
sub-second tool pauses lose part of the prefix, every think pause loses all of it, and each returning call
re-prefills its history. Thrashing, not a tail effect.

### 3.5 The causal test: halve the pool, keep the users

| | n08 (full pool) | n08kvh (half pool, 8,068 blocks) |
|---|---|---|
| prompt-token hit rate | 87% | 12% (75% evicted) |
| GPU on evicted recompute | 2% | 65% |
| lead-call latency p50 / mean | 2.4 / 8.4 s | 19.1 / 33.2 s |
| lead calls / h | 3,077 | 978 |
| paired session wall ratio | | 3.39 (95% CI 1.60-4.55) |

*What it says:* the same 8 users with the same tasks move from the compute-bound regime into the
thrashing regime when only the KV capacity changes, and land where 16 users on the full pool land (978
vs 976 lead calls/h). The knee is set by KV capacity against the users' working sets, not by the number
of users or by compute.

### 3.6 The harness's own cache busting (N=8)

| | n08 | n08mem (memory on) | n08ts (memory on + per-second timestamp: the as-shipped prompt) |
|---|---|---|---|
| hit rate | 87% | 85% | **2%** (98% re-prefilled after the timestamp edit) |
| lead calls / h | 3,077 | 3,084 | **935** |
| lead-call latency mean | 8.4 s | 8.6 s | 30.1 s |
| GPU on (fresh) prefill | 41% | 48% | 68% |
| edit re-prefill caused by the system prompt | 0% | 14% | 100% |

*What it says:* the harness's per-second timestamp alone costs as much throughput as KV thrashing does,
at a load where nothing is evicted: every call re-prefills its whole history because the system prompt
changes. The memory feature rewrites the system prompt only when memories change and costs about nothing
here. (n08ts ran 18 of 40 sessions before its job ended; the throughput comparison is over 1.8 loaded
hours.)

### 3.7 Policies: let the ongoing sessions finish

**Live** (the same first 48 specs, paired):

| | n16 | s3gate16 (gate: 8 active) | n32 | s3gate32 (gate: 8 active) |
|---|---|---|---|---|
| lead calls / h | 976 | **3,394 (3.5×)** | 990 | **3,793 (3.8×)** |
| hit rate | 2% | 85% | 1% | 87% |
| GPU on evicted recompute | 74% | 6% | 74% | 6% |
| lead-call latency p50 / mean (s) | 33.9 / 56.4 | 3.2 / 15.7 | 94.4 / 112.0 | 3.4 / 26.4 |
| queue per call (s) | 19.0 | 7.1 (gate) | 75.3 | 19.0 (gate) |
| turn latency p50 / p95 (s) | 970 / 5,603 | 433 / **1,017** | 1,706 / 9,340 | 903 / **1,489** |
| paired session wall ratio (gate / no gate) | | **0.26 (0.23-0.39)** | | **0.30 (0.28-0.38)** |

**Replay** (recorded n16 / n32 sessions, 100 minutes closed loop; ratios vs default):

| policy | n16 calls / h | n16 call p50 | n16 evicted / prompt | n32 calls / h | n32 call p50 |
|---|---|---|---|---|---|
| default | 1,036 (1.00) | 33.6 s | 0.81 | 1,141 (1.00) | 85.6 s |
| default, second run | 1,061 (1.02) | 32.7 s | 0.81 | | |
| session gate, 8 active | **2,937 (2.83)** | 2.9 s | 0.02 | **3,512 (3.08)** | 3.8 s |
| session gate, 6 active | | | | 3,397 (2.98) | 2.9 s |
| turn gate, N/2 mid-turn | 2,971 (2.87) | 2.9 s | 0.02 | 1,194 (1.05) | 37.8 s |
| CPU offload, 128 GiB | 2,905 (2.80) | 9.9 s | 0.01 | 1,253 (1.10) | 87.1 s |
| session FCFS priority | 1,384 (1.34) | 15.1 s | 0.44 | 1,641 (1.44) | 17.2 s |
| TTL pins on tool pauses (10 s) | 1,286 (1.24) | 32.7 s | 0.46 | | |
| smaller prefill chunks (2,048) | 1,053 (1.02) | 36.2 s | 0.81 | | |

*Key.* **session gate** (`MU_GATE`): at most K sessions are active; a waiting request of another session
is not admitted until an active session has been idle for 5 s (a think pause) and gives up its slot.
**turn gate**: client side, at most N/2 sessions mid-turn; a new turn waits for a free slot. **CPU
offload**: vLLM's native offloading connector; evicted prefixes come back from host memory instead of
being recomputed. *What it says:*
- Bounding the set of sessions whose prefixes compete for the pool to what the pool holds (8 × ~25k
  tokens) removes the thrashing (hit rate back to 85-87%) and roughly triples the work done per hour,
  live and in replay. Users wait at the gate instead of in a thrashing queue; median turns take longer
  than at N=8 (433 vs 137 s) but tails collapse (turn p95 5.5-6.3× lower) and sessions finish 3.3-3.8×
  sooner.
- A gate must be sized to the pool, not to the number of users: N/2 works at N=16 (8 sessions) and fails
  at N=32 (16 sessions, still twice the pool).
- Swapping instead of recomputing works only while the CPU tier holds the working set: at N=32 the
  working set (~32 × 20-25k tokens) is larger than the 128 GiB CPU cache (~520k tokens) and thrashing
  moves one tier down.
- Priority by session age and TTL pins soften thrashing (1.2-1.4×) but do not stop it; smaller prefill
  chunks do nothing, because the problem is the amount of recompute, not how it is interleaved.

### 3.8 Verification

| check | result |
|---|---|
| scheduler log vs vLLM's own records (smoke2: 4 users, 1,100-block pool, KV events on) | evictions 71,142 vs 71,145 KV-event removals; preemptions 9 vs 9; prefill tokens 1,108,272 vs 1,108,263; admission hit = `cache_read_input_tokens` for 100% of calls |
| observer makes no decision (CPU, vLLM's scheduler in the loop) | identical step-by-step schedules to the stock AsyncScheduler |
| observer overhead (replay of pilot8, stock vs observer) | lead-call latency p50 1.738 vs 1.734 s, mean 6.600 vs 6.599 s; 3,050.6 vs 3,048.7 calls/h |
| replay fidelity (replay of pilot8 vs its live recording) | latency p50 1.73 vs 1.77 s, p90 5.04 vs 5.07 s; hit rate 0.913 vs 0.914; evicted blocks −2% |
| replay noise floor | pilot8 default ×2: aggregate within 0.2%, per-call median difference 0.3%; n16 default ×2: calls/h +2.4% |
| solo model | reproduces the solo cells within 0.03 s per call |
| client joins | 100% of calls joined to their server request in every cell |

## 4. Conclusions

1. **The real bottleneck of a multi-user harness on one GPU is KV capacity against the users' concurrent
   working set.** With contexts that approach the model's window (here 20-30k tokens per user), 258k
   tokens of KV hold about 8 users. Below that the server is compute-bound and batching works: throughput
   grows with users and each call slows 1.7-2.4×. Above it the prefix cache stops working and the server
   thrashes: two thirds of the throughput is lost, most GPU time goes to re-prefilling evicted histories,
   and latency grows 10-42×.
2. **The damage is done by recompute, not by eviction as such and not by preemption.** A paused user's
   KV is lost within about a second once the pool is full; the returning call's re-prefill fills the step
   budget, stalls the decodes of everyone in flight (the largest cost per call) and makes new requests
   queue. vLLM preempted almost nothing; the scheduler's admission is optimistic enough that the running
   set is simply always too large for the pool to keep anyone's idle prefix.
3. **The fix is admission control at the session level.** Letting at most a pool's worth of sessions be
   active, and making the others wait until an active session pauses, restores the cache (hit rate back to
   85-87%) and gives 3.5-3.8× the throughput live, with far better tails. It must be sized by KV capacity,
   not by user count. Swapping to CPU is an alternative only while the CPU tier holds the working set;
   priority and TTL pins are partial remedies; chunk sizing is not one.
4. **Two harness-side conditions decide whether the cache can work at all.** A per-second timestamp in
   the system prompt defeats the cache for every call and costs as much as thrashing at a load where
   nothing is evicted; compaction at the window re-prefills 8-13% of prompt tokens at every load. The
   memory feature is cheap. A multi-user deployment has to fix the first before any serving policy
   matters.

## 5. Caveats

- One model (dense GQA, 256 KiB/token), one GPU type, vLLM 0.29 defaults, one harness, solo sessions.
  The knee moves with KV per token, pool size and context length: an MLA or hybrid model, fp8 KV, or a
  shorter context would put it at more users.
- Think times are synthetic (lognormal, median 30 s) and tool pauses are this sandbox's (milliseconds).
  Longer tool pauses (tests, builds, web calls) would expose prefixes to eviction below N=8.
- N=16 and N=32 ran 79/80 and 89/96 counted sessions before their 12 h job ended; 6 and 40 of their
  sessions hit the 4 h backstop, so their slowdowns and the gate's gains are underestimates. n08ts ran
  18 of 40 sessions.
- Turn 1 (module inventory) often reaches the 100-call cap; the cap is the same in every cell.
- The gate's parameters (8 sessions, 5 s idle) were set from the pool size, not tuned; it trades a
  bounded wait at the gate for throughput and tails.
- Replays reproduce timing, not content (fixed output lengths, `/v1/completions`, 100-minute windows);
  frontend results come from the live cells only. Per-call timings diverge between runs even when
  aggregates agree, so policies are compared on aggregates.
- The step model's attribution is approximate (median error 1.9%); the queue shares are read from the
  head of the queue at each step.
- The pilot8 cell ran with the first context setting (reactive compaction only) and is used only for
  the replay validation.

## 6. Reproduction

```bash
# CPU tests and the session manifest (login node)
python research/11_multiuser_kv/test_multiuser.py
python research/11_multiuser_kv/test_mu_scheduler.py
python research/11_multiuser_kv/mu_workloads.py build
# live cells: one job = two H200 cells (resubmitting resumes)
sbatch --export=NONE --time=04:00:00 research/11_multiuser_kv/mu.sbatch smoke,pilot1 pilot8
sbatch --export=NONE research/11_multiuser_kv/mu.sbatch n01a,n04 n01b,n08
sbatch --export=NONE research/11_multiuser_kv/mu.sbatch n16 n32
sbatch --export=NONE research/11_multiuser_kv/mu.sbatch n08kvh,n08mem n12,n08ts
sbatch --export=NONE research/11_multiuser_kv/mu.sbatch s3gate16 s3gate32
sbatch --export=NONE --time=03:00:00 research/11_multiuser_kv/mu.sbatch smoke2,r_pilot8_stock r_pilot8_default,r_pilot8_default2
# replays: plans on a CPU node, then two 2-GPU jobs (offload needs host memory)
sbatch --export=NONE research/11_multiuser_kv/mu_cpu.sbatch prepare n16 n32
sbatch --export=NONE --mem=320G research/11_multiuser_kv/mu.sbatch r_n16_default,r_n16_sgate8,r_n16_gate,r_n16_prio r_n16_default2,r_n16_pin10t,r_n16_off128,r_n16_mnbt2k
sbatch --export=NONE --mem=320G research/11_multiuser_kv/mu.sbatch r_n32_default,r_n32_sgate8,r_n32_gate r_n32_sgate6,r_n32_prio,r_n32_off128
# tables (CPU node: the full live set needs ~20-40 GB)
python research/11_multiuser_kv/mu_analyze.py --smoke
sbatch --export=NONE --mem=180G research/11_multiuser_kv/mu_cpu.sbatch analyze --cond n01a,n01b,n04,n08,n12,n16,n32,n08kvh,n08mem,n08ts,s3gate16,s3gate32
sbatch --export=NONE research/11_multiuser_kv/mu_cpu.sbatch replay
```

## Data inventory

| path | what | why | status |
|---|---|---|---|
| `data/multiuser_live/sessions.jsonl`, `sessions_meta.json` | 128 counted + 32 filler session specs, think draws, balance summary | the workload | used |
| `data/multiuser_live/cells/{n01a,n01b,n04,n08,n12,n16,n32}/` | the load sweep | §3.2-3.4 | used; n16 79/80, n32 89/96 |
| `data/multiuser_live/cells/{n08kvh,n08mem,n08ts}/` | the N=8 contrasts | §3.5-3.6 | used; n08ts 18/40 |
| `data/multiuser_live/cells/{s3gate16,s3gate32}/` | the live session gate | §3.7 | used |
| `data/multiuser_live/cells/{smoke,smoke2}/` | forced-contention checks (16k window; 2,600 / 1,100 blocks) | §3.8; smoke received no KV events (publisher endpoint bug), smoke2 did | validation only |
| `data/multiuser_live/cells/{pilot1,pilot8}/` | pilot (pilot8 with the first, reactive-only context setting) | found the overflow problem; pilot8 is the replay-validation source | not in the results |
| `data/multiuser_live/cells/{n16kvh,n16mem,n16ts}/` | a `COMPLETE` marker only | the planned N=16 contrasts, skipped when stage 2 moved to N=8 | empty |
| `data/multiuser_live/cells/r_*/` | the replay runs (`server/<job>/replay_calls.jsonl`, scheduler log, scrape) | §3.7-3.8 | used |
| `cells/<cond>/runs/<label>/` | per session: capture `*.requests.jsonl.xz`, trace `run_*.jsonl.xz`, inputs sidecar, meta, turn prompts, sandbox diff | the traces | — |
| `cells/<cond>/server/<job>_<gpu>_<time>/` | `vllm.log`, `server_info.json`, `sched.jsonl.xz` (MU scheduler log), `scrape.jsonl` (1 Hz metrics), `metrics_final.prom` | the server side | `scrape.jsonl` is gitignored (33 files, up to 230 MB each, over GitHub's 100 MB limit) and not used by the tables |
| `data/multiuser_live/jobs/` | pipeline and Slurm logs per job | provenance | — |
| `data/multiuser_live/multiuser_tables.md` / `.json` | T1-T10 | §3 | final (2026-09-29) |
| `data/multiuser_live/interim_tables.md` / `.json` | tables for N ≤ 8 of 2026-09-27 | interim look | superseded |
| `data/multiuser_replay/plans/{pilot8,n16,n32}/` | rendered prompt ids per session (`.npz`) and the replay plan | replay input | — |
| `data/multiuser_replay/replay_tables.md` / `.json` | the replay tables | §3.7 | final |

## Changes during the run

- **Context limit** (2026-09-26, during the pilot): the plan kept the harness's 512,000-character limit
  and relied on reactive compaction; `reactive_compact` keeps the last five messages verbatim, so after a
  large file read the retried call overflowed again and 2 of 3 overflowing turns ended in errors. The
  limit became the model's input window in the harness's units (134,000 characters, §2.2).
- **Stage 2 moved from N=16 to N=8** (2026-09-27): N=16 was deep in the thrashing regime, where each
  contrast would drown and cost ~7 GPU-hours; N=12 was added to locate the knee.
- **Stage 3 = the session gate live** (2026-09-27): the gate was added to `mu_scheduler.py` (tested on
  CPU with vLLM's scheduler in the loop) because it is the direct form of "let the ongoing sessions
  finish" and needs no coordination between harness processes; it ran live before the replay study
  finished, and the replays confirm it.
- **Replay study scope:** the planned watermark, 60/240 s pin, priority + pin and think-scale arms were
  not run; the smaller-chunk and session-gate arms were added.
- **Analyzer fixes** (2026-09-26 → 29): the admission-time "ideal hit" counted a request's own first
  chunk (vLLM commits it inside `schedule()`); servers started before the scheduler fix logged it, and the
  analyzer reclassifies every chunk from the raw fields with the arrival-time ideal, so all cells read
  alike. Throughput counts every session's work over all loaded periods (the first version counted only
  counted sessions inside the longest steady interval and gave N=12 an impossible 1.35 turns/h).
