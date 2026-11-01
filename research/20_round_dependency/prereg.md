# Pre-registration, part 1: the counterfactual profile (S3)

Written 2026-10-28 before the main replay (S3) ran. It is frozen by `prereg.sha256` together with the code and the
sample that define the measurement. Part 2 (the load tests, S5) is added below before any load cell runs.

## Question

Does a strong dependency run through all agentic rounds of MiniMax-M2.7 in the s15 harness? Specifically:
- does round j's next action need round j-1's tool results;
- how far could the calls of a turn be merged;
- which rounds could also run in another order?

## Units and sample

- **Sample:** `data/cf/sample_main.json` (seed 20, drawn by `rd_cf.py sample` before the pilot ran).
  - **Topic 19:** 60 complete counted sessions from w08/w16/w32_none, 6 per codebase, all four turns ending
    normally.
  - **Topic 16:** one e4r0 `prefix` trial per task with at most 100 lead rounds (e4r1 when e4r0 is longer): 20
    SWE-bench Verified, 30 SWE-bench Pro, 28 Terminal-Bench.
  - The pilot used disjoint units: topic 19's pilot cells and topic 16's e4r1 trials.
- **A tested round:** every round of the sample's turns. A *pair* is a round j that has a predecessor in its turn and
  is not the turn's final answer.

## Measurement (rd_cf.py, frozen)

- **Server:** MiniMax-M2.7 at TP=4 on vLLM 0.31.0 (`rd_serve.py`, configuration `profile`).
- **Prompts:** rendered on CPU by vLLM's own Anthropic path with each source's append-only template, and sent as
  token ids to `/v1/completions`.
- **Sampling:** n = S, temperature 1.0, top_p 0.95, top_k 40 (the model's generation_config, which the live runs
  used), max_tokens 8,192, one seed per (round, variant).
- **Warm-up:** a 1-token request goes first, so all S samples share one prefill.
- **Variants for round j:**
  - **C:** the recorded request of L_j.
  - **P:** C with every result of round j-1 replaced by "[Background task bg_NNNN started] Result will arrive as a
    task_notification.", s15's own background placeholder.
  - **M:** the merged world for a chain that starts at k: L_k's request, then a_k with the tool calls of rounds
    k+1..j-1 appended (their thinking dropped), then placeholders for every call of rounds k..j-1, then L_j's
    messages after round j-1's results.
- **Action keys, target level (primary):**
  - read_file / write_file: path;
  - edit_file: path + first line of old_text;
  - glob: pattern;
  - bash: program + paths, patterns, identifiers and line anchors;
  - todo_write: one key;
  - any other tool: all its arguments.

  A sample matches round j when it reproduces at least half of c_j's keys. A final answer's key is "no tool call".
  p_X is the share of the S samples of variant X that match.
- **Per-pair labels:**
  - determinate iff p_C ≥ 2/S;
  - independent of round j-1 iff p_P ≥ TAU · p_C, dependent otherwise;
  - mergeable iff the chain takes it: p_P (k = j-1) or p_M (k < j-1) ≥ TAU · p_C, with p_C ≥ 2/S, and the
    recent history was not rewritten between j-1 and j (tail_ok);
  - reorderable iff mergeable and its effects commute with the group's earlier rounds (rd_static eff_deps; unknown
    shell commands conflict with everything);
  - order-free iff the samples of C_{j-1} reproduce c_j at a rate of at least TAU · p_C.
- **Pilot-set constants:** S = **8** and TAU = **0.5**, set by the rules below.

## Pilot rules (applied to the pilot run `pilot`, before this file was frozen)

- **TAU:** 0.5, unless more than 10 % of the pilot's determinate A/A rounds (a second control in place of P) would
  be called dependent at 0.5. In that case it is the largest of {0.4, 0.3} that keeps the share at 10 % or less.
- **S:** 8 if the projected main-run cost fits what is left of 35 GPU-h for S1-S3; otherwise 6, with the sample cut
  by whole units in sample order if even 6 does not fit. S=4 was dropped before the pilot ended, on resolution alone:
  with 4 samples the reproducibility floor 2/S is 50 %, too coarse for per-round labels.
  - The projection is the pilot's GPU-h per sampling batch (C, A, P, M), times the batches the main run sends.
    Those are C and P per pair plus M at the pilot's rate per pair, with no A.
  - 75 % of a batch's cost scales with S.
  - The main run's higher client concurrency is not counted, so the projection errs high.
- **Gates; if one fails, stop and ask:**
  - **V1:** at least 95 % of control renders equal the recorded prompt tokens.
  - **V2:** on pairs whose arguments copy a string first introduced by the previous results (rd_static lex_prev),
    ρ = Σp_P / Σp_C ≤ 0.30.
  - **V3:** A/A ρ = Σp_A / Σp_C within [0.85, 1.15].

## Outcomes of the main run (rd_analyze.py)

Per source (topic 19, topic 16), per task kind and per tool class, with percentile-bootstrap 95 % intervals over
sessions or trials (2,000 draws):
- **Q1 strength (primary):**
  - the share of determinate pairs that are independent of round j-1;
  - ρ = Σp_P / Σp_C over all pairs;
  - the outcomes of the P samples.
- **Q2 merge:**
  - the share of a turn's lead calls that the chain groups remove;
  - the output tokens and prompt tokens of those calls.
- **Q3:** the reorderable share. **Q4:** the order-free share.
- **Q5 overlap by resource:**
  - the share of tool seconds spent while an independent next call could already run;
  - rounds with at least 1 s of tools that are followed by an independent round.
- **Static census against the replay:** precision and recall of "copies the previous results" as a predictor of
  "dependent".
- **Validity:** V1 and V2 hold in the main run too. If V1 or V2 fails, the main run is reported as invalid.

**Wording of the answer to the user's question, by source** (the point estimate of Q1's independent share, read
with its interval):
- below 20 %: "a strong dependency runs through the rounds";
- 20-40 %: "mostly chained, with a minority of independent rounds";
- above 40 %: "weak dependency: many rounds do not need the previous results".

## Load-test gates (decided on the main run, before part 2)

- **G_merge:** the topic-19 chain groups remove at least 10 % of lead calls per turn.
- **G_reorder:** at least 10 % of SWE-bench Pro and Terminal-Bench tool seconds run alongside an independent next
  call.

If a gate fails, its load test is reported as not run. Part 2 fixes the cells, metrics and rules before the first
load cell.

## Pilot outcome (run `pilot`, 2026-10-28 20:24-21:00 UTC, 2.40 GPU-h)

- **Size:** 375 rounds; 325 pairs; 1,170 sampling batches.
- **Gates:**
  - V1: 375/375 control renders equal the recorded prompt tokens (100 %);
  - V2: ρ = 0.06 [0.01, 0.11] on 25 copied pairs;
  - V3: A/A ρ = 1.03 [1.00, 1.05] on 325 pairs.

  All pass.
- **TAU:** at 0.5, 4 % of determinate A/A rounds are called dependent (8 % at 0.6, 10 % at 0.7), so **TAU = 0.5**.
- **S:**
  - the cost is 2.40 GPU-h for 1,170 batches;
  - the main sample is 4,415 rounds, about 9,360 batches;
  - projected 19.2 GPU-h at S=8 against 32.3 left, so **S = 8** on the whole sample.
- **The main run's client concurrency** is 16 batches in flight, against the pilot's 8. The server is unchanged.

# Pre-registration, part 2: the load tests (S5)

Drafted 2026-10-28 before the main replay ended. It is frozen, with its own hashes in `prereg.sha256`, after the
gates are read and before the first load cell.

**Tool:** `rd_loadreplay.py`. The variants (rec, merge, reorder_low, reorder_cpu) are defined in its docstring, from
the chain groups of the main run `main`.
- **Every cell:**
  - a closed loop of N slots;
  - copies of the units with fresh run nonces;
  - U(5, 30) s between sessions;
  - a 15-min warm-up, then a 60-min window.
- **The calls:** prompts rendered with the source's template, sent as `/v1/completions` with the recorded or derived
  output length and `ignore_eos`.

**Merge test** (if G_merge passes):
- **Units:** topic 19's 60 sampled sessions.
- **Server `load19`:** topic 19's: pool pinned at 16,137 blocks, max_num_seqs 64, a 65,536-token window, topic 11's
  MU scheduler log, FCFS.
- **Cells:** `m16_rec`, `m16_merge`, `m32_rec`, `m32_merge` (N=16 and N=32).

**Reorder test** (if G_reorder passes):
- **Units:** the sampled SWE-bench Pro and Terminal-Bench trials.
- **Server `load16`:** as load19, with a 131,072-token window and `--scheduling-policy priority`.
- **Regime pilot:** `rec` at N=8 and N=16 with a 10-min warm-up and a 15-min window.
  - N_c is the smallest of them with a prompt-token hit rate below 50 %;
  - N=16 if neither is below 50 %.
- **Cells at N_c:** `r_rec` (all priority 0), `r_low` and `r_cpu`.
- **If at least 8 GPU-h remain afterwards:** `r_merge` at N_c (exploratory).

**Metrics:**
- **Primary:** recorded rounds completed per hour inside the window. A round is complete when its call has returned
  and its tool step has ended.
- **Secondary:**
  - turns (trials) completed per hour and calls per hour;
  - the prompt-token hit rate;
  - call latency p50 / p95 and queue wait p50 / p95 (scheduler log);
  - GPU-seconds per round and the GPU share by class (topic 11's step model, refit per cell);
  - preemptions.

**Decision per variant against `rec` at the same N:**
- the gain in rounds/h, (variant − rec) / rec, with a 95 % bootstrap interval over each cell's six 10-min blocks;
- **"helps":** the interval's lower bound is > 0 and the gain is ≥ F = 10 % (topic 19's between-cell spread);
- **"hurts":** the upper bound is < 0 and the gain is ≤ −F;
- otherwise **"no clear difference"**.

**Validity of a cell:**
- no request error;
- every window complete;
- GPUs 0-3 empty after the cell;
- the `rec` cells' call prompts match the recorded prompt tokens except for the nonce. That is checked on CPU
  before the cells.

## Deviations

None so far.
