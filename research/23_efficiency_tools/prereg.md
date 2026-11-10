# Pre-registration: topic 23's main runs (frozen with prereg.sha256 on 2026-11-03, after the smoke runs)

`et_cell.py freeze` hashes this file, the topic's code, the pinned s15, the template, the task lists, topic 22's queue
manifest and the shared files the runs use (`et_cell.frozen_files`). `et_cell.py run --group main` refuses (exit 4) if
any of them changed. Changes after the freeze go to the README's Changes with time and reason.

## The question

The harness decides by itself whether to call external tools for efficiency (the user, 2026-11-02). Do these help
Qwen3.8-27B serve 32 concurrent agents on one H100, compared with the vanilla harness?
- **Fixed session gates:** `gate8`, `gate16`.
- **An LLM-set gate:** `agate`.
- **The final `wait` tool:** `wait`.
- **LMCache's KV offload:** as an LLM-called tool (`lmc`), and storing everything (`lmc_all`).

## Runs

- **Setups:**
  - `none`, `gate8`, `gate16`, `agate`, `wait`, `lmc`, `lmc_all`, and `none_r2` (a second `none`).
  - The definitions are in README's Setups and in `et_cell.SETUPS`.
  - **From the screens** (`data/screen/winners.json`, `data/tables/screen.md`):
    - `save_context`: description s1, with the `[server]` line's cue naming the tool (`S15_SAVE_TEXT=s1`,
      `S15_STATUS_FORMAT=kv_cue_save`; the revised screen's `S5`).
    - The controller: `c3` (capacity arithmetic and decision cues) at low effort. It is the screen rule's next pick:
      the rule's winner, `c4` at the default effort, failed live in the `agate` smoke run (README Changes, 2026-11-03
      01:26 UTC).
  - **The session gate** (`et_proxy.Gate`; `gate8`, `gate16`, `agate`):
    - at most K sessions hold a slot, first come, first served;
    - a slot frees after 5 s idle with nothing in flight;
    - while more sessions hold slots than K allows (after `agate` lowers K), a session gives its slot up when its
      request ends;
    - gate time is left out of a task's time limit;
    - `agate` starts at K = 32; its controller calls once a minute, counted from each call's start.
- **Tasks:** topic 22's 96 (`22_wait_bench/data/tasks.json`, seed 2211), in its queue order. Each run starts all of
  them.
- **Concurrency:** 32 agents at once (Harbor `--n-concurrent-agents 32 -n 48 -k 1`).
- **Time limits:**
  - **The limit:** the task's agent timeout × 2, counted on active time (gate-held seconds left out; `et_agent.py`).
  - **Backstop:** Harbor's own timeout at × 8.
  - **Per call:** 1,800 s of server time (the proxy).
- **Agent settings:** 250 lead calls, window 131,072, `max_tokens` 32,000 → 64,000, the model's sampling defaults,
  and thinking at the default effort.
- **Server, one fresh server per run:**
  - vLLM 0.31.0 in `/data/fritz/venv23`, TP=1 (`et_cell.BENCH_ARGS`);
  - LMCache for `lmc` / `lmc_all`: `lmcache server --chunk-size 784 --separate-object-groups --l1-size-gb 300
    --eviction-policy LRU`.
- **Schedule:** two runs at a time on GPUs 4 and 5 (`et_chain.py main`), `--hours 5` per stage, resumed once at the
  limit.
  - P1 `none` (GPU 4) | `lmc_all` (GPU 5)
  - P2 `wait` | `lmc`
  - P3 `gate8` | `agate`
  - P4 `gate16` | `none_r2`

## Measures (`et_analyze.py main` → `data/tables/et.{md,json}`)

- **Primary** (as topic 22, over the full-N window: from the first moment 32 agents run to the last task's start):
  - request latency avg/p50/p95/p99, as the agent sees it;
  - entry end-to-end avg/p50/p95/p99, over the 96 tasks;
  - vLLM requests/h and tokens/s, counting the agents' work only: vLLM's counters minus the harness's own calls,
    which are `lmc`'s `save_context` re-sends (the proxy's store-flagged rows) and `agate`'s controller calls
    (`controller.jsonl`). The gross counters are reported alongside. In the `lmc` smoke run the re-sends were 22 % of
    vLLM's requests and 28 % of its prompt tokens, so the gross counters would credit `lmc` with its own overhead.
- **Secondary:**
  - working requests/h (no wait-only or save calls);
  - tasks finished per hour in the window, and the makespan;
  - pass rate;
  - active time and gate-held time per task;
  - vLLM's server-side request time.
- **Mechanism:**
  - KV use and the share at ≥ 80 %;
  - prompt tokens by source: computed, GPU hit, offload hit;
  - recomputed prompt tokens per task;
  - preemptions;
  - SM / tensor-core / HBM-bandwidth utilization;
  - LMCache L1 use;
  - K over time;
  - waits and saves per task, on the rule (the request's newest line ≥ 80 %) and off it;
  - the prompt hit rate of the call after a wait, after a save, and after another tool.
- **Contrasts:** each setup minus `none`, and the two direct contrasts of readings 2 and 3, all paired by task; 95 %
  bootstrap with 2,000 draws (requests clustered by task; throughput ratios from 10-min blocks of both runs; pass rate
  paired).
- **Noise:** `none_r2` − `none` is the yardstick for every other contrast.
- **Validity of a run** (`et_analyze.py valid`):
  - all 96 tasks reach an end state (a verifier reward, after a normal end or at the time limit), with
    infrastructure errors under 2 %;
  - call counts match vLLM's counter within 1 %: the proxy's answered calls, plus `agate`'s answered controller calls
    (which go to vLLM directly), that finished inside each monitor file's span, against the counter's change over it.
    The smoke runs showed why the span matters: 5-6 calls finish in the 10-13 s between the monitor's stop and the
    server's. vLLM does not count calls their client cut (a controller call at its 300-s limit, a 504 at the proxy's
    cap);
  - the monitor covers the full-N window: at least 0.95 records/s, no gap over 10 s;
  - the GPU is empty after every stage of the run.

## Readings fixed in advance

1. **Offload alone:** `lmc_all` − `none` on requests/h, tokens/s and entry p50/p95.
2. **Offload and `wait`:** `lmc` − `wait`. Is the prompt hit rate of the call after a pause higher (after a save in
   `lmc`, after a wait in `wait`), and is recompute per task lower?
3. **The LLM-set K:** `agate` against the better of `gate8` / `gate16` (the one with the higher net requests/h), on the
   same measures.
4. **A difference is called clear** only when its interval excludes 0 (or 1 for ratios). It is compared with
   `none_r2` − `none`.

## Known limits

- One run per setup.
- Two runs share socket 1's CPUs, so cross-pair contrasts include partner effects.
- The controller and the agents run on the server they measure. Under load a controller call takes 1-3 minutes,
  so a round can come later than once a minute (smoke: 60-181 s apart).
