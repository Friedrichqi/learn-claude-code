# Serving-side memory: how one request's KV competes with everyone else's

Weekly progress, 2026-09-16. Session "teammate Memory Management", 2026-09-12. Companion notes in this
folder: `teammate_memory_management.md` (how a teammate's context grows across tasks, and the
compaction ladder) and `compaction_kv_reread_related_work.md` (what a harness pays after compaction).
This note is the serving-side view of the same growth: what the GPU that has to hold a request's KV
does while hundreds of other requests are in flight.

## 1. Questions

1. If incoming requests are batched together and HBM were infinite, does batching change the
   context length a request can use? (The model window may be 1M tokens.)
2. When HBM is finite, how do modern serving systems and providers divide KV memory between
   requests, and what happens to a request that would take a large share of it on its own?
3. What do the agentic harnesses themselves, s15, Claude Code and Codex CLI, do about it, given
   that they sit on the client side of an HTTP API?

## 2. Method and sources

Four parallel web-research tracks on 2026-09-12, each returning sourced facts with verbatim quotes:
(A) memory management inside one engine instance (vLLM, SGLang, TensorRT-LLM / Dynamo, and the Orca,
PagedAttention, Sarathi-Serve, DistServe and Splitwise papers); (B) the memory arithmetic of 1M-token
contexts, sequence-sharded serving, and the providers' pricing, TTL and rate-limit pages; (C)
cross-instance routing and the agentic-serving literature; (D) the client-side view from the Codex CLI
source and the Claude Code documentation. Provider numbers are from the official pages as fetched on
2026-09-12 and will drift. Local facts come from `s15_integrated_harness/code.py`, `README.md`,
`.env.example` and the earlier notes under `weekly_progress/`. Items the researchers could not confirm
from a primary source are marked UNVERIFIED.

## 3. The arithmetic: what one request occupies

Per token, the KV cache costs 2 x layers x kv_heads x head_dim x bytes per element (MLA stores one
compressed vector per layer instead). From the models' `config.json`:

| model | geometry | bytes/token, bf16 | 1M tokens, bf16 | 1M tokens, fp8 |
|---|---|---|---|---|
| Llama-3.1-8B | 32 layers, 8 KV heads, head_dim 128 | 131 KB | 131 GB | 66 GB |
| Llama-3.1-70B | 80 layers, 8 KV heads, 128 | 328 KB | 328 GB | 164 GB |
| Qwen3-32B | 64 layers, 8 KV heads, 128 | 262 KB | 262 GB | 131 GB |
| Qwen3-235B-A22B | 94 layers, 4 KV heads, 128 | 193 KB | 193 GB | 96 GB |
| GLM-4.5 / GLM-4.6 | 92 layers, 8 KV heads, 128 | 377 KB | 377 GB | 188 GB |
| DeepSeek-V3 / R1 (MLA) | 61 layers x (512 latent + 64 rope) | 70 KB | 70 GB | 35 GB |

Against one GPU: H100 80 GB at 3.35 TB/s, H200 141 GB at 4.8 TB/s, B200 about 180 GB per GPU (the
DGX B200 lists 1,440 GB for eight; the often-quoted 192 GB is UNVERIFIED on an NVIDIA page). So a
single 1M-token Llama-3.1-70B context needs the KV memory of five H100s before a byte of weights;
Llama-3.1-8B at 1M does not fit one H100 either; DeepSeek's MLA is the outlier that makes 1M cheap
(70 GB) next to its 671 GB of weights. MoE changes nothing here; only the attention geometry matters.

Two consequences frame everything below.

- **Memory adds up linearly, and so does decode time.** Decode is bandwidth-bound: every step
  re-reads every resident token's KV. Per-step traffic is the sum over sequences of length x
  bytes/token, so one 1M-token sequence costs the batch as much HBM traffic as a thousand 1k-token
  sequences: 328 GB per step for Llama-70B in bf16, about 98 ms at one H100's bandwidth even if it
  fit.
- **Tensor parallelism stops helping at the KV-head count.** vLLM's context-parallel docs: sharding
  along the head dimension is plain TP, and beyond `num_kv_heads` GPUs "the KV cache for each GPU
  will be duplicated for `tp_size / H` times". Eight KV heads (Llama-70B, GLM) make TP 8 the
  ceiling, Qwen3-235B's four heads make it 4, and MLA has effectively one. Past that point the only
  way to spread one request's KV is along the sequence.

## 4. Answer to question 1: infinite HBM

Batching does not change how long a request's context can be. The context length is a property of
the model (its trained positions and RoPE scaling, exposed by the engine as `max_model_len` or
`--context-length`) and of nothing else. With paged or variable-length attention there is no
padding, so a 1M-token neighbour does not shorten anyone else's window and a 1k-token neighbour does
not lengthen it. What batching changes is how many sequences run at once and how long each step
takes. With infinite capacity the binding constraints move from space to time.

1. **Every co-batched request pays for the big one's decode.** Steps are lock-step across the batch,
   and a step's attention time grows with the summed context, so the 1M request raises the
   inter-token latency of the 1k requests decoding beside it. This is why sequence-sharded decode
   exists even where capacity is not the problem: vLLM's decode context parallel (`-dcp`), SGLang's
   `--dcp-size`, TensorRT-LLM's Helix mode, and the LoongServe, Infinite-LLM and Medha / Mnemosyne
   systems spread one sequence's KV reads over many GPUs' bandwidth. vLLM's 2026-08 blog reports
   6,091 tokens/s per GPU with DCP against about 1,863 with TP alone on 8xB200 for Kimi K2.6, on a
   workload whose tail reaches about 1M tokens; Helix claims up to 32x larger batches at the same
   latency budget for DeepSeek-R1; Medha reports 5.7x throughput and 30x / 174x lower median / P99
   latency on heterogeneous long-context traffic.
2. **A 1M-token prefill is a compute burst that stalls decodes unless it is chunked.** Meta's
   context-parallel paper prefills 1M tokens on Llama 3 405B in 77 s across 128 H100s; on one
   engine the same work would freeze every other request's next token for its duration. Chunked
   prefill (Sarathi-Serve, now standard in vLLM and SGLang) interleaves slices of the long prompt
   with the running decodes, trading the big request's time-to-first-token for everyone else's
   inter-token latency. Section 5 has the engine details.
3. **The model's usable context is shorter than its window.** From the earlier harness survey:
   RULER puts effective context at roughly 50-65% of the advertised length, NoLiMa finds 11 of 13
   models below 50% at 32k, and Qwen2.5 is trained to 32k and reaches 128k only through YaRN. The
   s15 KV-splice replay saw damage start exactly when positions passed Qwen's 32k. Infinite memory
   does not buy attention quality.

## 5. Answer to question 2, inside one engine: finite HBM

Sources fetched 2026-09-12 from the `main` branches of vLLM, SGLang and TensorRT-LLM and their
"latest" docs; flag names and defaults below are from source, not from memory.

### 5.1 vLLM (V1 engine)

- **Sizing.** The worker profiles peak memory once, then the KV pool is `gpu_memory_utilization`
  (default 0.92) of the GPU minus weights, activations and the CUDA-graph estimate, carved into
  16-token blocks. Startup logs "GPU KV cache size: N tokens, Maximum concurrency for M tokens per
  request: X.XXx". The engine refuses to start unless one full-length sequence fits: "To serve at
  least one request with the model's max seq len (...), (X GiB KV cache is needed, which is larger
  than the available KV cache memory (Y GiB) ... Try increasing `gpu_memory_utilization` ... or
  decreasing `max_model_len`". At request time the only rejection is length: "The decoder prompt
  (length n) is longer than the maximum model length of m."
- **Admission.** The waiting queue is FCFS, or by explicit `priority` (lower value first). With
  `scheduler_reserve_full_isl` (default true) "the scheduler checks whether the full input sequence
  length fits in the KV cache before admitting a new request", to prevent "over-admission and KV
  cache thrashing with chunked prefill". A waiting request that cannot get its blocks waits at the
  head of the queue; the scheduler never preempts a running request to admit a waiting one, and
  admits nothing in a step in which it preempted. `watermark` keeps a fraction of blocks free.
- **Per-step budget.** `max_num_batched_tokens` defaults to 16,384 on GPUs of 70 GiB and more and
  8,192 otherwise, `max_num_seqs` to 1,024 or 256. Chunked prefill is on by default: "the
  scheduling policy prioritizes decode requests. It batches all pending decode requests before
  scheduling any prefill operations ... If a pending prefill request cannot fit into
  max_num_batched_tokens, it automatically chunks it." `long_prefill_token_threshold` (default 0,
  off) caps a long prompt's share of one step. Without chunking, a budget smaller than
  `max_model_len` "effectively limits the maximum sequence length".
- **Out of blocks mid-generation.** "Preempt the lowest-priority request": under the priority
  policy the request with the worst (priority, arrival) key, under FCFS the newest running request,
  `running[-1]`. Its blocks are freed, it returns to the waiting queue with zero computed tokens and
  is prefilled again from scratch: "the default preemption mode is RECOMPUTE rather than SWAP, as
  recomputation has lower overhead in the V1 architecture". `preemption_mode` and `swap_space` are
  gone from the V1 configuration. The remedy the docs give is to raise `gpu_memory_utilization` or
  lower `max_num_seqs` / `max_num_batched_tokens`.
- **Prefix cache as the eviction victim.** Block hashes chain parent hash plus the block's tokens;
  free blocks sit in one LRU queue, and a finished request's blocks are freed tail first because
  "the last block of a request must hash more tokens and is less likely to be reused". A new
  request allocates by popping the LRU head, and "if the head block is a cached block, this also
  'evicts' the block". A 1M-token admission therefore evicts the oldest cached prefixes of everyone
  else before it runs.
- **Second tier.** `kv_offloading_size` (GiB) with backend `native` or `lmcache`: the offloading
  connector "extends the prefix cache by offloading completed KV blocks to slower but larger tiers
  (CPU host memory, plus optional secondary tiers) ... Hits in the offload tiers are promoted back to
  GPU on demand", asynchronous DMA, LRU or ARC. The 2026-01 blog reports TTFT reduced 2-22x and
  throughput up to 9x at high CPU hit rates. `--kv-cache-dtype fp8` halves the per-token footprint.

### 5.2 SGLang

- **Knobs.** `--mem-fraction-static` (weights plus KV pool), `--max-total-tokens` (auto from the
  fraction), `--max-running-requests`, `--max-queued-requests`, `--chunked-prefill-size` (auto, -1
  disables), `--max-prefill-tokens` (16,384), `--schedule-policy` (default fcfs; lpm, random,
  dfs-weight, lof, priority, routing-key), `--schedule-conservativeness` (1.0; "Use a larger value
  if you see requests being retracted frequently"), `--retraction-policy` (default length; or
  priority), `--context-length` (from `config.json`), `--enable-mixed-chunk` (off), and priority
  preemption above a difference of 10.
- **Admission.** The prefill adder charges each admitted request its input plus `max_new_tokens`
  times a `new_token_ratio` that starts at 0.7 x conservativeness, decays to 0.14 of that over 600
  steps, and resets after any retraction; a request that does not fit returns `NO_TOKEN` and waits.
  Cache-aware `lpm` orders the queue by matched prefix length. Unlike vLLM, the loop runs "prefill
  first if possible", then decode, unless mixed chunking is enabled.
- **Retraction.** When the decode memory check fails, "KV cache pool is full. Retract requests":
  running requests are sorted by (output length ascending, input length descending) and the
  least-preferred are popped until memory fits, keeping at least one; victims "release memory and
  don't insert into the tree because we need the space instantly", lose their prefix indices and are
  recomputed later. The default `length` policy "retracts short-output, long-input requests first",
  so the giant prompt is the natural victim. If even the last request cannot fit: "Out of memory
  even after retracting all other requests in the decode batch". In prefill-decode disaggregation
  the retracted KV is offloaded to host so it can be restored without recompute.
- **RadixAttention and HiCache.** Eviction is LRU over unlocked leaves by last access time.
  `--enable-hierarchical-cache` adds host and storage tiers (`--hicache-ratio` 2.0 in cache mode;
  write-back, write-through or selective; backends file, mooncake, hf3fs, nixl, aibrix); "L1 and L2
  are private to a single inference instance; only L3 can be shared". Length rejection: "The input
  (n tokens) is longer than the model's context length (c tokens)", unless `--allow-auto-truncate`.

### 5.3 TensorRT-LLM and Dynamo KVBM

`free_gpu_memory_fraction` defaults to 0.9, 32 tokens per block, block reuse on, and "KV cache
state only becomes reusable after the request that computed the state terminates". Eviction is
"prioritized LRU. All blocks are assigned a priority between 0 and 100 ... All blocks of the lowest
priority must be evicted before any blocks of the next priority", default 35, set per token range
with a `duration_ms` through the retention config; NVIDIA reports about 20% higher hit rate from
priorities. The capacity scheduler defaults to `GUARANTEED_NO_EVICT` ("guarantee that a started
request is never paused"); `MAX_UTILIZATION` packs more and may pause. `max_num_tokens` 8,192,
`max_batch_size` 2,048, and context chunking spreads a prompt over steps, FCFS by default or
`EQUAL_PROGRESS`. Dynamo's KVBM "offloads KV blocks GPU (G1) -> host/CPU (G2) -> disk (G3) -> object
storage (G4)"; "If the CPU cache is smaller than the device cache, KVBM churns".

### 5.4 The papers behind the knobs

Orca (OSDI'22) schedules "at the granularity of iteration (instead of request)" and reports 36.9x
throughput over FasterTransformer on GPT-3 175B. PagedAttention (SOSP'23) found that "only 20.4% -
38.2% of the KV cache memory is used to store the actual token states in the existing systems",
recovers it with paging for 2-4x throughput, and describes the all-or-nothing eviction with swap or
recompute. Sarathi-Serve (OSDI'24) names the problem of section 4: "interleaving of prefill and
decode iterations ... makes it challenging to achieve both high throughput and low latency", and
answers with "chunked-prefills which splits a prefill request into near equal sized chunks" and
"stall-free schedules", 2.6x to 5.6x capacity. DistServe (OSDI'24) removes "prefill-decoding
interference" by disaggregation, 7.4x more requests or 12.6x tighter SLO; Splitwise (ISCA'24)
separates the "compute-intensive prompt computation, and a memory-intensive token generation".

### 5.5 Direct answers

- **(a) A prompt that needs most or all free blocks.** No engine rejects on capacity; rejection is
  only for a prompt longer than the window. vLLM makes it wait at the head of the queue until the
  whole prompt fits, counting idle prefix-cache blocks as free and evicting them LRU, never
  preempting a running request for it; once admitted it is chunked into the per-step budget behind
  the decodes; if it later outgrows the free blocks it is itself the FCFS victim (newest running)
  and is recomputed. SGLang waits (`NO_TOKEN`) until input plus reserved output fits, chunks it
  prefill-first, and on decode-time exhaustion retracts short-output, long-input requests first,
  again the giant. TensorRT-LLM's default admits only what can finish; the alternative admits and
  may pause.
- **(b) Others' maximum context.** Unchanged: the per-request ceiling is fixed at startup and vLLM
  guarantees one full-length sequence fits. The giant lowers the number of sequences that fit and
  the "Maximum concurrency" figure, raises preemptions and retractions, consumes the shared
  per-step token budget so co-scheduled decodes see higher inter-token latency, and evicts other
  sessions' cached prefixes, which raises their next time-to-first-token.
- **(c) Fairness.** vLLM: FCFS or explicit priority, decode-first token budget,
  `long_prefill_token_threshold` to cap one prompt's share of a step, victim = last-arrived or
  lowest priority. SGLang: fcfs or cache-aware ordering, `chunked_prefill_size` as the per-step cap,
  prefill-first unless mixed chunk, retraction by length or priority, conservativeness scaling the
  reservations, priority preemption above a threshold. TensorRT-LLM: FCFS or equal-progress chunk
  scheduling and 0-100 retention priorities for eviction. None of them caps one request's share of
  KV memory; that is left to the layers in sections 6 and 7.

## 6. Answer to question 2, across instances and for agents

### 6.1 Routing and migration between engine instances

| system | how it handles memory pressure | how it places a request | reported effect |
|---|---|---|---|
| Llumnix (OSDI'24) | live migration of a request and its KV between instances, "near-zero downtime that is constant to the sequence length"; de-fragments so a long prefill finds contiguous room | global scheduler on per-instance memory load and per-request "virtual usage" | P99 first-token latency up to 15x better on 16 GPUs; up to 36% cost saving |
| SGLang Model Gateway | workers retract requests when KV is full (`--retraction-policy` length or priority) | approximate radix tree per worker; cache-aware unless load is imbalanced (`--cache-threshold 0.3`, `--balance-abs-threshold 64`, `--balance-rel-threshold 1.5`) | up to 1.9x throughput, prefix hit rate 20% -> 75% |
| llm-d / Gateway API Inference Extension | endpoint picker with a utilisation saturation detector | weighted scorers: approximate or precise prefix-cache index fed by vLLM KV events, in-flight load, session id | P90 TTFT 0.54 s precise vs 31 s approximate vs 93 s random |
| NVIDIA Dynamo | KVBM tiers G1 GPU, G2 pinned host, G3 local NVMe, G4 remote | router cost = prefill blocks to compute + decode blocks + active requests; host and disk hits weighted 0.75 / 0.25; session-affinity header with a TTL | over 20x faster TTFT on production traces (vendor blog) |
| AIBrix (ByteDance) | distributed KV cache across nodes | gateway policies: least-kv-cache, prefix-cache, vtc-basic per-user fairness, session affinity | +50% throughput, -70% latency (paper) |
| Mooncake (Kimi, FAST'25) | KVCache pool over the cluster's DRAM and SSD, blocks hashed with their prefix | conductor picks the prefill instance minimising queue + prefill after the prefix hit; rejects at admission when predicted TTFT or TBT breaks the SLO (30 s / 0.1 s per token in production) | up to 525% throughput in simulation; 75% more requests in production |
| DistServe, Splitwise | separate prefill and decode pools so one huge prefill cannot stall decodes on the same GPU | per-phase allocation | DistServe 7.4x more requests at the same SLO; Splitwise 1.4x throughput at 20% lower cost |

The common thread: the cluster-level answer to "one request holds a lot of memory" is to place it
where its prefix already lives (so the memory is not duplicated), to move it when an instance
fills (Llumnix), to separate the prefill burst from the decode pool, and to refuse it at the door
when the SLO prediction says it will not fit (Mooncake).

### 6.2 Fairness

VTC (OSDI'24) gives a "2x tight upper bound on the service difference between two backlogged
clients" with a token-based cost function; it caps service, not memory. DLPM and Equinox (2025) add
prefix locality to it. CacheOPT (2025) is the closest thing to a per-request KV cap: it "allocates
the estimated KVC demand to a request" and reserves globally to avoid preemption. In production
gateways, AIBrix's `vtc-basic` policy is the deployed form of per-user token fairness.

### 6.3 Agent requests: KV that is held while nothing runs

The agentic-serving literature identifies one problem that ordinary chat serving does not have: an
agent's request ends, the tool runs for seconds to minutes, and the next request wants the same
prefix back.

- **How much idle KV.** TokenCake measured "as much as 18.5% of the GPU KV Cache pool" occupied by
  agents stalled on tool calls under stock vLLM. vLLM RFC #37003 (2026-03): "40-60% of session wall
  time is spent paused on tool calls, and during those pauses the agent's blocks are unreferenced."
  AgentSysBench: production sessions "hold state idle for minutes to hours between active steps."
- **Who holds it, and for how long.** Stock vLLM and SGLang keep a finished request's blocks only
  as unreferenced prefix-cache entries under LRU, so a busy engine evicts them before the tool
  returns (Continuum calls this "end-of-turn eviction"; NVIDIA's Dynamo blog: "A 2-30 second tool
  call pause can age out an agent's entire prefix"). Continuum pins the KV in GPU memory for a
  computed TTL and schedules programs FCFS. TokenCake offloads to CPU at `call_start` and uploads
  before the predicted return: 63.7 ms round trip against 1,815 ms to recompute 4,096 tokens on an
  A100, 28.5x. ThunderAgent pauses whole programs at 0.80 / 0.95 utilisation so the engine may
  evict their KV, and resumes the smallest first. Dynamo with SGLang streaming sessions keeps a
  session's KV outside the radix tree, "invisible to eviction", until explicit close or a 300 s
  idle timeout, and pins the session to a worker with a sticky router. vLLM's RFC proposes a
  `RetentionDirective` with a priority and a duration: the client telling the engine how long to
  hold.
- **Why holding pays.** Agent traffic is highly reusable: SMetric's production trace puts KV reuse
  above 80% of request tokens against 54-62% for chat; vLLM's AgentX blog reports a median of 43
  turns and 142k input tokens per session with over 96% prefix hits under session-sticky routing;
  Dynamo measured Claude Code sessions at 85-97% cache hit. Workflow-aware eviction (KVFlow, PBKV,
  CacheWise) beats LRU by 1.85-2.19x because LRU evicts an agent's prompt "shortly before their
  reuse". The multi-tier caches (Mooncake Store at about 3.8 GB per 100k tokens for Kimi-2.5 FP8,
  SGLang HiCache, Dynamo KVBM, LMCache) turn the hold problem into a placement problem: 92% hit
  rate against 1.7% without the store, and TTFT cut 84% against full recompute in Ant's HiCache
  deployment.

## 7. The provider's rationing: price, TTL and per-minute tokens

Fetched 2026-09-12; these pages change often.

- **Anthropic.** 1M context is the default on Fable 5.x, Mythos, Opus 4.6 and later, and Sonnet 4.6
  and 5, with no beta header and "billed at standard pricing"; the pricing page says "A 900k-token
  request is billed at the same per-token rate as a 9k-token request". Rate limits are a token
  bucket per model on requests, input tokens and output tokens per minute, and "only uncached input
  tokens count toward your ITPM": cache reads are free of the input limit. Prompt-cache writes cost
  1.25x (5 min) or 2x (1 h) the input price, reads 0.1x (0.025x on Fable 5.1). Start-tier limits
  for Fable 5.x are 1,000 RPM, 500k ITPM, 100k OTPM. The Priority Tier is no longer sold. The API's
  own server-side compaction triggers at 150k input tokens by default. The earlier 2x / 1.5x
  premium above 200k is gone from every current page (UNVERIFIED as history).
- **OpenAI.** GPT-4.1 has a 1,047,576-token window and the GPT-5.5 / 5.6 / 6 models 1,050,000, with
  128k output. Above 272k input tokens the whole request is billed at 2x input and 1.5x output.
  Flex processing runs at batch rates (half price) and answers 429 "Resource Unavailable" when
  capacity is short; Fast mode costs twice the standard rate. The rate-limit page states "a
  separate rate limit for long context requests". Prompt caching routes on "a hash of the initial
  tokens" so a conversation stays on one machine; the guidance is about 15 requests per minute per
  prefix; in-memory retention lasts 5-10 minutes of inactivity up to an hour (30 minutes on GPT-5.6
  and later), or 24 hours with `prompt_cache_retention`.
- **Google Gemini.** 1,048,576-token windows; 2M existed only for Gemini 1.5 Pro, retired
  2025-09-29. Prices step up above 200k tokens (2.5 Pro input $1.25 -> $2.50 per MTok, output $10 ->
  $15). Explicit context caching is billed as storage, $4.50 per million tokens per hour on 2.5 Pro
  and 3.1 Pro, default TTL one hour, and cached reads cost about 10% of input.

Read together, these are the provider's memory scheduler. Per-minute input-token limits bound the
rate at which new KV must be materialised on the fleet, and excluding cache reads from that limit
favours traffic whose KV already exists. Cache TTLs are leases on HBM or host memory, and a 2x
write premium for an hour, or $4.50 per million-token-hour, prices the residency of a prefix that
is tens to hundreds of gigabytes at 1M tokens. Prefix-hash routing pins a conversation's KV to one
machine. Step-function tiers at 200k-272k charge the whole request more once it crosses the size at
which one sequence has to be sharded across several GPUs. Batch and Flex sell the same work off-peak
at half price; Fast and the retired Priority Tier sell reserved capacity at twice. Anthropic's move
to flat 1M pricing in March 2026 says the sharding cost has been absorbed into the base rate; the
per-minute buckets are what remain as admission control.

## 8. What the harnesses can do from the client side

None of the three harnesses can see KV placement, batching or eviction. What they control is the
size of each request, the number of concurrent request streams, and how they back off.

### 8.1 Claude Code

- Caps the prompt below the window: auto-compaction at the 200k boundary on Sonnet 4.6 / Opus 4.6
  and at about 967k on native-1M models, tunable from 100k to 1M (`/autocompact`, `--autocompact`,
  `CLAUDE_CODE_AUTO_COMPACT_WINDOW`), or held to 200k with `CLAUDE_CODE_DISABLE_1M_CONTEXT=1`;
  raising `CLAUDE_CODE_MAX_OUTPUT_TOKENS` "reduces the effective context window available before
  auto-compaction triggers". It clears old tool results before summarising, stops after three
  back-to-back compactions that refill at once, and re-reads at most five files afterwards.
- Bounds concurrency: 20 concurrent subagents (`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`), spawn depth
  3, workflow agents 1-256; agent teams have "no hard limit" and cost "approximately 7x more tokens".
- Backs off: transient failures retried up to 10 times (cap 15) with exponential backoff; 429
  throttles retried but not a gateway's spend-limit 429; `CLAUDE_CODE_RETRY_WATCHDOG=1` retries 429
  and 529 indefinitely with up to five minutes between attempts; `--fallback-model` switches on
  overload but "Authentication, billing, rate-limit, request-size, and transport errors ... never
  trigger a switch"; usage-limit auto-continue since v2.1.234.
- Sees only indirect signals: cache-read tokens in responses and the `anthropic-ratelimit-*`
  headers. The 1M window is billed at standard pricing, so its only server-side memory lever is
  the API's compaction.

### 8.2 Codex CLI

- Caps the prompt: auto-compaction at 90% of `model_context_window`, and since v0.100 a user value
  is clamped to that 90% (issue #11805, closed as not planned); a separate
  `effective_context_window_percent` (default 95) is a hard cap. A test fixes a 400k window at 360k
  auto-compact and 380k usable. `/compact` keeps at most 20,000 tokens of recent user messages plus
  a summary. Compaction can be pushed to the server with `context_management.compact_threshold`
  (minimum 1,000) or `POST /responses/compact`, which return an opaque encrypted compaction item.
  `truncation: "auto"` (now deprecated) drops items from the beginning instead of failing with 400.
- Statefulness is transcript-level: `previous_response_id` stores response objects for 30 days and
  "all previous input tokens for responses in the chain are billed as input tokens", so nothing in
  the API promises KV retention.
- Backs off: request retries 4, stream reconnects 5, stream idle timeout 300 s, 200 ms base with
  doubling and 0.9-1.1 jitter; the generic policy has `retry_429: false` and a TODO to honour
  `Retry-After`. `service_tier` may request `flex` or `priority`.
- Production evidence of the size problem: issue #16812 saw a session go from 4 to 26 compactions
  after a threshold change ("more compactions -> more re-reads -> faster next compaction"), and
  #16839 counted one 610 KB file read 53 times.

### 8.3 s15

- One Anthropic-compatible endpoint (`ANTHROPIC_BASE_URL`). The experiments ran glm-5.3-flash on
  z.ai; the committed 28-teammate stress trace ran Qwen3.8-27B on a self-hosted vLLM whose recipe
  sets only `--max-num-seqs 512` and leaves `max_model_len` and the memory fraction at defaults.
- Request size: only the lead is capped (`CONTEXT_LIMIT` = 128k tokens x 4 chars); teammates never
  compact and there is no cap on how many the lead spawns, so a run's concurrent streams are one
  lead plus N teammate threads plus background bash, each carrying its own full history. s16 bounds
  its own fan-out at `CONCURRENCY = 8`.
- Back-off: `MAX_RETRIES = 3`, 500 ms base doubling to a 32 s cap plus 25% jitter; a 529 switches to
  `FALLBACK_MODEL` after two in a row; prompt-too-long triggers one `reactive_compact`. On z.ai the
  harness met 429s with four concurrent sessions and a 5-hour rolling usage cap (error 1308) that
  returned zero model calls for 16 runs.
- Measured cost shape on the hosted endpoint: about 4.6 s fixed time-to-first-token, prefill near
  19k tokens/s, decode 90-150 tokens/s; teammates hit the prefix cache 90% of the time, the lead 17%
  at the 50k budget and 4-23% after `snip_compact` rewrites at 512k. In the provider's terms the
  teammates are cheap traffic (cache reads outside the input limit) and the lead is expensive.

## 9. Conclusions

1. **Infinite HBM: batching never shortens a context.** Window length is a model property; paged
   attention removes padding; a 1M-token neighbour changes only the batch's step time and the
   number of sequences that fit. The costs that remain are the big request's prefill burst
   (answered by chunked prefill) and its per-step KV read (answered by sequence-sharded decode:
   DCP, Helix, Medha), plus the model's own effective-context ceiling.
2. **Finite HBM: three layers of rationing, none of them per-request fairness.** Inside the engine
   (section 5) admission is a token budget per step plus preemption or retraction of whoever is
   cheapest to re-run; across instances the request goes where its prefix is and moves or is
   refused when an instance fills; at the provider the ration is per-minute uncached tokens, cache
   TTLs as memory leases, and price steps at the size where sharding begins.
3. **Agents break the LRU assumption.** Their requests end and resume with the same prefix after a
   pause, so the engine evicts exactly what it will need next. The fixes are all forms of telling
   the engine how long to hold: TTL pinning (Continuum), offload at `call_start` (TokenCake),
   session KV outside the eviction tree with an idle timeout (Dynamo / SGLang), retention
   directives (vLLM RFC), or a second tier that makes the reload cheaper than recompute (Mooncake
   Store, HiCache, KVBM).
4. **For s15 this reframes the teammate result.** A teammate that never compacts is a request that
   grows monotonically and sits idle between tasks for longer than any prefix-cache TTL; on a
   shared engine it is the request the LRU should evict, and on a hosted API it is the request
   whose re-prefill the provider prices. The harness-side levers are the ones this series has been
   measuring: a stable prefix so the reload is a cache read, compaction at task boundaries so the
   resident set stops growing, and respawn for low-overlap tasks. The engine-side lever the
   literature points to, an explicit retention hint per session, is one the harness could emit
   today as a header and the self-hosted vLLM could learn to honour.
5. **What to measure next.** (a) On the self-hosted vLLM: KV blocks held by idle teammates versus
   active ones over a run, and hit rate with and without session-sticky routing; (b) the same run
   with `--max-num-seqs` small enough to force preemption, to see which teammate gets retracted;
   (c) whether a task-boundary compaction on teammates changes the fleet-level number that matters,
   uncached prefill tokens per minute.

## 10. Session log

1. 2026-09-12: question posed; local facts gathered from `code.py`, `README.md`, `.env.example` and
   earlier notes; four research tracks launched in parallel. The single-engine track failed on a
   usage limit and was relaunched after the reset; the other three returned.
2. This note drafted from tracks B, C and D; section 5 filled when the relaunched track returned.

## Sources

Track A, single engine (source `main` branches and docs, 2026-09-12):
- vLLM: https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/config/cache.py ,
  https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/config/scheduler.py ,
  https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/engine/arg_utils.py ,
  https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/worker/gpu_worker.py ,
  https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/core/kv_cache_utils.py ,
  https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/core/sched/scheduler.py ,
  https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/core/block_pool.py ,
  https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/engine/input_processor.py ,
  https://docs.vllm.ai/en/latest/configuration/optimization.html ,
  https://docs.vllm.ai/en/latest/design/prefix_caching.html ,
  https://docs.vllm.ai/en/latest/configuration/engine_args.html ,
  https://docs.vllm.ai/en/latest/features/kv_offloading_usage/ ,
  https://vllm-project.github.io/2026/01/08/kv-offloading-connector.html ,
  https://docs.vllm.ai/en/latest/features/quantization/quantized_kvcache.html
- SGLang: https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/arg_groups/fields/schedule.py ,
  https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/arg_groups/fields/memory.py ,
  https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/managers/schedule_policy.py ,
  https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/managers/scheduler.py ,
  https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/managers/schedule_batch.py ,
  https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/mem_cache/radix_cache.py ,
  https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/managers/tokenizer_manager.py ,
  https://docs.sglang.io/advanced_features/server_arguments.html ,
  https://docs.sglang.io/docs/advanced_features/hicache_best_practices
- TensorRT-LLM / Dynamo: https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/main/docs/source/features/kvcache.md ,
  https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/main/tensorrt_llm/llmapi/llm_args.py ,
  https://nvidia.github.io/TensorRT-LLM/advanced/kv-cache-reuse.html ,
  https://developer.nvidia.com/blog/introducing-new-kv-cache-reuse-optimizations-in-nvidia-tensorrt-llm/ ,
  https://nvidia.github.io/TensorRT-LLM/performance/performance-tuning-guide/useful-runtime-flags.html ,
  https://nvidia.github.io/TensorRT-LLM/performance/performance-tuning-guide/tuning-max-batch-size-and-max-num-tokens.html ,
  https://docs.nvidia.com/dynamo/reference/components/kvbm-configuration
- Papers: Orca https://www.usenix.org/conference/osdi22/presentation/yu ; PagedAttention
  https://arxiv.org/abs/2309.06180 ; Sarathi-Serve https://arxiv.org/abs/2403.02310 ; DistServe
  https://arxiv.org/abs/2401.09670 ; Splitwise https://arxiv.org/abs/2311.18677

Track B, arithmetic, sequence sharding, providers (fetched 2026-09-12):
- Model configs: https://huggingface.co/unsloth/Meta-Llama-3.1-8B/resolve/main/config.json ,
  https://huggingface.co/unsloth/Meta-Llama-3.1-70B/resolve/main/config.json ,
  https://huggingface.co/Qwen/Qwen3-32B/resolve/main/config.json ,
  https://huggingface.co/Qwen/Qwen3-235B-A22B/resolve/main/config.json ,
  https://huggingface.co/deepseek-ai/DeepSeek-V3/resolve/main/config.json ,
  https://huggingface.co/zai-org/GLM-4.5/resolve/main/config.json ,
  https://huggingface.co/zai-org/GLM-4.6/resolve/main/config.json ; MLA: https://arxiv.org/abs/2405.04434
- GPUs: https://www.nvidia.com/en-us/data-center/h100/ , https://www.nvidia.com/en-us/data-center/h200/ ,
  https://www.nvidia.com/en-us/data-center/dgx-b200/ ; bandwidth-bound decode:
  https://developer.nvidia.com/blog/mastering-llm-techniques-inference-optimization/ ; FP8 KV:
  https://vllm.ai/blog/2026-04-22-fp8-kvcache
- Sequence sharding: Ring Attention https://arxiv.org/abs/2310.01889 ; Striped Attention
  https://arxiv.org/abs/2311.09431 ; Meta context parallelism https://arxiv.org/abs/2411.01783 ; Helix
  https://arxiv.org/abs/2507.07120 ; Star Attention https://arxiv.org/abs/2411.17116 ; LoongServe
  https://arxiv.org/abs/2404.09526 ; Infinite-LLM https://arxiv.org/abs/2401.02669 ; Medha / Mnemosyne
  https://arxiv.org/abs/2409.17264 ; vLLM context parallel
  https://docs.vllm.ai/en/latest/serving/context_parallel_deployment/ and
  https://vllm.ai/blog/2026-08-07-decode-context-parallelism ; SGLang server args
  https://docs.sglang.io/advanced_features/server_arguments.html and
  https://github.com/sgl-project/sglang/issues/29736 ; TensorRT-LLM Helix
  https://github.com/NVIDIA/TensorRT-LLM/blob/main/docs/source/blogs/tech_blog/blog22_Helix_Parallelism_Scaling_Multi_Million_Token_Decoding_with_KV_Cache_Sharding.md
- Anthropic: https://platform.claude.com/docs/en/build-with-claude/context-windows ,
  https://platform.claude.com/docs/en/about-claude/pricing , https://platform.claude.com/docs/en/api/rate-limits ,
  https://platform.claude.com/docs/en/api/service-tiers ,
  https://platform.claude.com/docs/en/build-with-claude/prompt-caching ,
  https://platform.claude.com/docs/en/build-with-claude/compaction ,
  https://platform.claude.com/docs/en/release-notes/overview
- OpenAI: https://developers.openai.com/api/docs/pricing , https://developers.openai.com/api/docs/guides/prompt-caching ,
  https://developers.openai.com/api/docs/guides/rate-limits ,
  https://developers.openai.com/api/docs/guides/flex-processing , https://developers.openai.com/api/docs/guides/fast-mode ,
  https://developers.openai.com/api/docs/models/gpt-4.1 , https://developers.openai.com/api/docs/models/gpt-5.5 ,
  https://developers.openai.com/api/docs/models/gpt-5.6-sol , https://developers.openai.com/api/docs/models/gpt-6-astra
- Google: https://ai.google.dev/gemini-api/docs/pricing , https://ai.google.dev/gemini-api/docs/caching ,
  https://ai.google.dev/gemini-api/docs/generate-content/caching ,
  https://ai.google.dev/gemini-api/docs/models/gemini-2.5-pro , https://ai.google.dev/gemini-api/docs/changelog

Track C, cross-instance balancing and agentic serving:
- Routers and migration: Llumnix https://arxiv.org/abs/2406.03243 , https://github.com/AlibabaPAI/llumnix ;
  SGLang router https://www.lmsys.org/blog/2024-12-04-sglang-v0-4/ ,
  https://docs.sglang.io/docs/advanced_features/sgl_model_gateway ; llm-d
  https://llm-d.ai/blog/kvcache-wins-you-can-see ,
  https://github.com/llm-d/llm-d-inference-scheduler/blob/main/docs/architecture.md ,
  https://gateway-api-inference-extension.sigs.k8s.io/ ; Dynamo router and KVBM
  https://docs.nvidia.com/dynamo/knowledge-base/modular-components/router/configuration-and-tuning ,
  https://docs.nvidia.com/dynamo/dev/knowledge-base/modular-components/router/router-design ,
  https://docs.nvidia.com/dynamo/v-0-9-1/design-docs/kvbm-design ,
  https://docs.nvidia.com/dynamo/v1.1.0/user-guides/kv-cache-offloading ,
  https://blog.aks.azure.com/2026/03/16/dynamo-on-aks-part-3 ; vLLM production-stack
  https://github.com/vllm-project/production-stack ; AIBrix https://arxiv.org/abs/2504.03648 ,
  https://aibrix.readthedocs.io/latest/features/gateway-plugins.html ; Mooncake https://arxiv.org/abs/2407.00079 ,
  https://github.com/kvcache-ai/Mooncake ; DistServe https://arxiv.org/abs/2401.09670 ; Splitwise
  https://arxiv.org/abs/2311.18677
- Fairness: VTC https://arxiv.org/abs/2401.00588 ; DLPM https://arxiv.org/abs/2501.14312 ; Equinox
  https://arxiv.org/abs/2508.16646 ; CacheOPT https://arxiv.org/abs/2503.13773 ; Andes
  https://arxiv.org/abs/2404.16283 ; Preble https://arxiv.org/abs/2407.00023 ; SMetric
  https://arxiv.org/abs/2607.08565
- Agentic serving: Autellix https://arxiv.org/abs/2502.13965 ; Parrot https://arxiv.org/abs/2405.19888 ;
  TokenCake https://arxiv.org/abs/2510.18586 ; Continuum https://arxiv.org/abs/2511.02230 ; KVCOMM
  https://arxiv.org/abs/2510.12872 ; ThunderAgent https://arxiv.org/abs/2602.13692 and
  https://docs.nvidia.com/dynamo/agents/thunder-agent-program-scheduler ; CachedAttention
  https://arxiv.org/abs/2403.19708 ; MemServe https://arxiv.org/html/2406.17565 ; Pensieve
  https://arxiv.org/abs/2312.05516 ; Aegaeon https://ennanzhai.github.io/pub/sosp25-aegaeon.pdf ; Prism
  https://arxiv.org/abs/2505.04021 ; KVFlow https://arxiv.org/abs/2507.07400 ; PBKV
  https://arxiv.org/abs/2605.06472 ; CacheScout https://arxiv.org/abs/2608.14624 ; CacheWise
  https://arxiv.org/abs/2606.16824 ; AgentSysBench https://arxiv.org/abs/2608.15127 ; TraceLab
  https://arxiv.org/abs/2606.30560 ; KV-management survey https://arxiv.org/abs/2607.02574
- Vendor agent guidance: vLLM RFC https://github.com/vllm-project/vllm/issues/37003 ;
  https://vllm.ai/blog/2026-09-08-vllm-agentx ; https://vllm.ai/blog/2026-05-06-mooncake-store ;
  https://www.lmsys.org/blog/2025-09-10-sglang-hicache/ ; https://github.com/sgl-project/sglang/issues/21846 ;
  https://docs.nvidia.com/dynamo/v1.2.1/backends/sg-lang/agentic-workloads ;
  https://developer.nvidia.com/blog/full-stack-optimizations-for-agentic-inference-with-nvidia-dynamo/ ;
  https://developer.nvidia.com/blog/streaming-tokens-and-tools-multi-turn-agentic-harness-support-in-nvidia-dynamo/

Track D, harnesses:
- Codex CLI source (main, 2026-09-12): https://raw.githubusercontent.com/openai/codex/main/codex-rs/core/config.schema.json ,
  https://raw.githubusercontent.com/openai/codex/main/codex-rs/protocol/src/openai_models.rs ,
  https://raw.githubusercontent.com/openai/codex/main/codex-rs/core/src/session/context_window.rs ,
  https://raw.githubusercontent.com/openai/codex/main/codex-rs/core/src/compact.rs ,
  https://raw.githubusercontent.com/openai/codex/main/codex-rs/model-provider-info/src/lib.rs ,
  https://raw.githubusercontent.com/openai/codex/main/codex-rs/codex-client/src/retry.rs ,
  https://raw.githubusercontent.com/openai/codex/main/codex-rs/core/tests/suite/retry_after.rs ; config reference
  https://learn.chatgpt.com/docs/config-file/config-reference ; issues https://github.com/openai/codex/issues/11805 ,
  https://github.com/openai/codex/issues/16812 , https://github.com/openai/codex/issues/16839 ,
  https://github.com/openai/codex/issues/31562
- OpenAI API: https://developers.openai.com/api/docs/guides/compaction ,
  https://developers.openai.com/api/docs/guides/conversation-state ,
  https://developers.openai.com/api/docs/api-reference/responses/create ,
  https://developers.openai.com/api/docs/api-reference/responses/compact ,
  https://developers.openai.com/api/docs/models/gpt-5.1-codex-max ; https://simonwillison.net/2025/Nov/19/gpt-51-codex-max/
- Claude Code docs: https://code.claude.com/docs/en/model-config , https://code.claude.com/docs/en/env-vars ,
  https://code.claude.com/docs/en/cli-reference , https://code.claude.com/docs/en/sub-agents ,
  https://code.claude.com/docs/en/agent-teams , https://code.claude.com/docs/en/costs , https://code.claude.com/docs/en/errors ,
  https://code.claude.com/docs/en/interactive-mode , https://code.claude.com/docs/en/how-claude-code-works ,
  https://code.claude.com/docs/en/context-window ; changelog
  https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md ; Anthropic API
  https://platform.claude.com/docs/en/api/errors

Local: `s15_integrated_harness/code.py` (retry, teammate loop, `prepare_context`), `s15_integrated_harness/README.md`
(vLLM recipe), `.env.example`, `research/02_input_redundancy/README.md` Part A (latency model and its
correction; was `weekly_progress/090926/teammate_overlap.md`), `research/01_tracing/understanding_harness_codex.md`
(concurrency table), `research/02_input_redundancy/README.md` Part B (was
`weekly_progress/091626/teammate_input_redundancy.md`), `research/03_context_interventions/README.md`,
`research/04_kv_splice/README.md`.
