## EPIC on DeepSeek-V4-Flash (stage C: design and selftests only)

**Why a different design.** DeepSeek-V4-Flash has 43 layers. vLLM 0.31's model code, surveyed for this port, shows
three kinds of cached rows:
- **Sliding window.** Every layer keeps a 128-token window of its latent: 448 no-RoPE dims stored FP8, plus 64 RoPE
  dims stored bf16 and rotated at the token's position, GPT-J pairs.
- **Pooled entries.** 41 of the 43 layers also keep pooled entries: one per 4 tokens on C4 layers (overlapping
  windows) and one per 128 tokens on C128 layers. Each is a softmax-weighted sum of per-token compressor inputs plus
  a bias by offset in the window, rotated at the window start.
- **Indexer keys.** The 21 C4 layers also keep FP8 indexer keys, rotated before quantization. The indexer picks the
  top 512 pooled entries for each query.

These rows cannot be re-rotated in place:
- a shift that is not a multiple of the pooling ratio changes which tokens share a window and their offsets;
- indexer keys lose precision if rotated after quantization;
- the window keeps only 128 tokens, so there is nothing to copy back afterwards.

**Design B (`epic/epic_dsv4.py`).**
- **What is position-free.** Every cached quantity is computed by fused kernels from the current positions and from
  inputs that do not depend on position: the latent before RoPE, and the compressor and indexer inputs. All of
  these are functions of the layer's attention input, after the hyper-connection mix and the attention norm.
- **Store and substitute.** The connector keeps each computed token's attention input at every layer (43 × 4096 bf16
  per token, about 344 KB). At reused positions it substitutes the stored row through a forward pre-hook on
  `model.layers.i.attn`. The kernels then redo RoPE, FP8 quantization, pooling and the indexer keys at the new
  positions, which is shift's re-rotation done exactly.
- **Side effects.** Reused tokens' own queries and outputs change too. Their attention input is substituted again at
  every layer, so nothing from them reaches another position except through the substituted inputs.
- **The approximation.** Pooled windows that straddle an edit mix stale and fresh inputs.
- **Replication across ranks.** The latent and compressor GEMMs are replicated across tensor-parallel ranks, so every
  rank stores the same rows.
- **Locating rows.** The batch rows of planned positions come from the slot mapping of layer 0's sliding-window cache.
- **Server requirements.**
  - A connector that supports vLLM's hybrid memory allocator: DeepSeek-V4 has 5 KV cache groups, and without it
    vLLM would size every cache for every token.
  - `--enforce-eager`: vLLM captures CUDA graphs for DeepSeek-V4 even under compilation mode 0, and graphs skip the
    hooks.
  - No loads; the overshoot reserve is off.
  - No shadow probes: their K-row comparison needs per-head rows, which this cache does not have.
- **Compute saving.** None in this form: vLLM only serves prefix hits, so a reused token is still recomputed. A real
  implementation would skip the reused tokens' layers and rebuild only the cached quantities from the stored inputs.
  It would pay full price for any pooled window that straddles an edit.

**Selftests** (TP=4, eager, 32K context, on GPUs 0–3):

Four runs; the checks were extended after each run so that the next could tell more apart (`runs/epic/stage_c.log`,
last run in `runs/epic/selftest_dsv4.json`):

| check | result |
|---|---|
| hooks and ranks | 43 pre-hooks on each of the 4 ranks; identical substitution counts on every rank in every run |
| pool | empty after the sessions end (every run) |
| errors | none in any scenario |
| a snip of 5,342 tokens (not a multiple of 128) | about 1,860 rows reused per call (shift), 1,740 (epic128 and epic128a1) |
| D^X, the relative L2 deviation of substituted from freshly computed attention inputs, at delta 0 (no edit) | 0.093 and 0.096, with rows from decode; 0.074 with prefill rows only. A misplaced row would read about 1.4 |
| D^X after the 5,342-token snip | 0.29–0.30 for shift, epic128 and epic128a1; 0.24 for the 128-aligned shift of 5,248 tokens |
| first-token KL at delta 0 | shift against epicinf 0.006–0.05; epicinf (substitutes nothing) against prefix 0.010–0.070; shift against prefix 0.008–0.076 |
| first-token KL after the snip, against prefix, identical prompts | 0.022–0.087 by arm and run |

**Reading.**
- **Mechanics: verified.** Rows are saved and substituted at the planned positions, on every layer and rank, and
  the pool is freed.
- **Exactness: cannot be verified below DeepSeek-V4's own run-to-run noise.** Two computations of the same tokens'
  attention inputs, in different batches and chunks, differ by 7–10 % even with prefill rows only. Two
  recomputes of the same prompt differ in the first token by KL 0.01–0.07.
  - For comparison, MiniMax's reused K rows deviate by about 2 % at age 0, and its arms agree to KL 0.0.
  - The likely cause is discrete choices under FP8 numerics: MoE routing and the indexer's top-512 selection both
    flip on near-ties.
- **Formal checks.** Three checks still failed in the last run; their thresholds are calibrated on models that
  reproduce exactly:
  - D^X below 0.05 at delta 0, both with and without decode rows;
  - epicinf against prefix below KL 0.05.
- **After a snip,** reused inputs deviate three to four times the noise: about 0.30 against 0.08. The 128-aligned
  shift is somewhat lower (0.24), as kept window membership would suggest. In this short synthetic test the
  first-token KL after the snip cannot be told apart from the noise.
- **Not done:** live runs, as the plan said (only with an extended budget). A live round on DeepSeek-V4 would need
  replicates and a noise-floor arm (prefix_aa) to resolve effects of this size.
- **GPU time:** about 1.4 GPU-h for the four selftest runs (TP=4; the server starts in under 2 minutes in eager mode).
