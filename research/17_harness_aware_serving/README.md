# 17 · Harness-aware serving: does a version-aware design pay? The offline check E0

> **Status: E0 done (2026-10-13).** The decision rules and metrics below were pre-registered on 2026-10-13, before
> any analysis ran. E0 is offline only and used no GPUs.
>
> **Headline:**
> - **D1 passes.** Edits take 9.7-17.3 % of the lead chain at N = 1 (R1). At N = 4 (snip-off) they take 30 % of GPU
>   time and 29 % of the critical path. Snip hysteresis removes 94 % of R1's edit work, but not the window-compaction
>   edits at N = 4-8.
> - **D4 passes.** 99.5 % of edit tokens have their layout fixed at the previous decode's end.
> - **D2 fails.** The best implementable refresh recovers 49 % of the value at 0.32 of the tokens.
> - **D3 fails.** 6-11 % of refresh work fits in tool windows at N = 1. A 10 % decode slowdown buys 3-25 tokens per
>   step at N = 4-16.
> - So C2 (drift-bounded refresh) reduces to topic 16's EPIC.
>
> **Question (user, 2026-10-13):** complete, refine and develop the brainstorm. Its pieces:
> - four request types: modify, re-prefill, resume, off-path;
> - a per-request DAG with valid/ready signals;
> - four components: request scheduler, memory manager, GPU reconfigurator, harness.
>
> **The refined design** is in the Claude Doc *Harness-aware LLM serving: refined design and offline check
> (topic 17)*, <https://claude.ai/code/artifact/da5f9800-e97c-4273-aabf-c91fb66df17d>. E0 tests its riskiest claims
> on data that already exists.
>
> **User decisions (2026-10-13):**
> - the design goes in a Claude Doc;
> - E0 runs in full, including the data that lives only on Drive (rclone installed on this host);
> - every component stays, each labeled with the prior work that covers it;
> - nothing is committed unless asked.
>
> **Inputs (all read-only):**
> - `/data/fritz/harness_eval/jobs`: the s15 harness on SWE-bench Verified / Pro and Terminal-Bench, with four open
>   models;
> - topic 16's local data;
> - topic 11's tables (committed) and its 32 scheduler logs (121 MB), pulled from Drive on 2026-10-13 with
>   `rclone copy --include "multiuser_live/cells/*/server/*/sched.jsonl*"` into its ignored `data/`. Topics 12, 13
>   and 15 were not needed in the end.
>
> **Commits:** none.

## 1. Configurations and terms

- **snip-off:** topics 11-15. H200, Qwen3-32B, a 40,960-token window, `snip_compact` off, the original chat template.
- **R1:** topic 16 and harness_eval. Rolling snip past 50 messages, a static system message, the append-only
  template.
- **Effect × urgency** (the doc's §3). Effect is Append, Modify or Side; urgency is Critical, Deferrable or
  Background. Purposes map as follows:
  - `ld`, `os`, `tm` → Append. `ld` is Critical; `os` is Critical because the lead blocks; `tm` is Critical when the
    lead waits on it.
  - `cs` → Modify / Critical (an LLM summary).
  - `mr` → Side, appended to the next prompt / Critical.
  - `mx`, `mc` → Side / Deferrable, Background.
  - Non-LLM edits (snip, placeholder, fit, reactive, template re-render) → Modify. Their cost is the re-prefill they
    cause, charged to the next call.
- **Edit work:** the GPU work for Modify calls, plus the re-prefill of tokens the server computed before at another
  position because of an edit.
- **Critical-path time:** the sum of the lead chain's call times. At N = 1 these are modeled with 16's TTFT/TPOT
  models; at N > 1 they come from 11's step attribution.
- **Refresh value of an edited call:** reply KL(compounded arm) − reply KL(one-step arm), from 16's E-A replay. It is
  what an exact refresh of the reused rows before that call would remove.

## 2. Pre-registered decision rules (written before running)

Each rule has its threshold, and what the doc says if the rule fails.

- **D1 · edits matter.**
  - Passes if, in at least one (config, N) cell, edit work is **≥ 5 % of critical-path time or ≥ 20 % of GPU
    time**.
  - D1 *holds fully* only if the rule still passes under E0f's harness-fixed counterfactual: append-only plus snip
    hysteresis, i.e. trim to 30 messages whenever the history passes 50.
  - If D1 passes only as run, the harness fix is the cheaper remedy, and claim C2 is reported as secondary.
  - If D1 fails, C2 is dropped and C3/C4 lead.
- **D2 · refresh helps.**
  - Passes if some refresh policy recovers **≥ 50 % of the total refresh value** while computing **≤ 1/3 of the
    tokens** a full re-prefill of the same calls computes.
  - Candidate policies: refresh every N edits (N = 2, 4, 8); refresh when the predicted drift is ≥ θ; read-aware
    refresh, i.e. refresh before calls predicted to copy from moved spans.
  - Policies other than "every call" are estimated from the measured relation between KL and the reused rows' age
    (`age_mean_over`, `age_max_over`), which is a model-based estimate.
  - If D2 fails, C2 reduces to EPIC (topic 16), and the doc says so.
- **D3 · refresh fits.**
  - Passes if **≥ 50 %** of the D2 policy's refresh work fits in measured slack.
  - At N = 1, slack is the idle window before the call, counted only when it is longer than the refresh time
    (16's TTFT model).
  - At N = 4-8, slack is the spare prefill budget per engine step at ≤ 10 % TBT inflation (E0d), summed over the
    steps before the call.
  - If D3 fails, refresh needs its own prefill capacity, and that goes under C4.
- **D4 · lookahead.**
  - Passes if **≥ 50 % of edit tokens** have their surviving layout fixed at the previous round's decode end. The
    layout is which runs survive and where they move.
  - If D4 fails, edit-ahead repair (R2) is dropped.

## 3. E0 parts and results

| part | question | script → table | result |
|---|---|---|---|
| E0a census, N = 1 | Share of the lead chain per effect × urgency; edit work | `hs_census.py` → `E0a_census_n1.md` | Edit work 9.7-17.3 % per model (11.2 % pooled over 1,116 harness_eval trials); topic 16's prefix arms 13.3 %. Validated against topic 16's measured alignments: estimate / truth 0.975, edited-call recall 99.7 % |
| E0a census, N > 1 | The same on a shared H200 | `hs_census_mu.py` → `E0a_census_mu.md` | From topic 11's T3/T4/T4b, first order: edit work is 30 % / 27 % of GPU time and 29 % / 25 % of a lead call's time at N = 4 / 8. Behind the session gate (s3gate16/32) it is 34-39 % of GPU time |
| E0b lookahead | Edit tokens with their layout known at round start / decode end / tool end | `hs_lookahead.py` → `E0b_lookahead.md` | 97.9 / 99.5 / 100 % over 85,527 transitions. The rolling snip is 99.9 % of edit tokens. Re-running s15's rules at tool end reproduces 85,507 of 85,507 logged requests |
| E0c slack, N = 1 | Refresh work that fits in tool windows | `hs_slack.py` → `E0c_slack_n1.md` | 6-11 % (whole windows), 17-22 % (partial). Window p50 0-0.25 s, p90 0.3-2.3 s |
| E0d stall curve | Prefill tokens a decode step absorbs at 5 / 10 / 20 % TBT inflation | `hs_stall.py` → `E0d_stall.md` | 0 / 3-25 / 15-140 tokens per step at N = 4-16 (a mixed step costs ≥ ~2 ms); 74-304 spare tokens/s per GPU at 10 % |
| E0e drift | Refresh value, its predictors, the policy frontier | `hs_drift.py` → `E0e_drift.md` | Reproduces 16 §4.5 exactly. Best implementable: every 7 edits, 49 % at 0.32 (post hoc); pre-registered every 8, 43 % at 0.30; an oracle reaches 89 % at 0.33. Held-out Spearman of the ex-ante predictor: 0.37 |
| E0f hysteresis | Edit work with snip hysteresis (trim to 30 past 50) | `hs_hysteresis.py` → `E0f_hysteresis.md` | Edit work 10.0 % → 0.7 % of the N = 1 lead chain; calls with an edit 75.2 % → 9.3 % |

**Verdicts:**

- **D1 passes**, and holds under the counterfactual at N = 4-8 only. Those edits are window compaction (88-92 % of
  edit tokens, topic 11 T7), which snip hysteresis does not touch.
- **D2 fails.**
- **D3 fails** at N = 1, and is moot at N = 4-8 because D2 failed.
- **D4 passes.**

Consequences for the design (the doc's §10): C2 reduces to EPIC. The harness fix comes first for one session.
Edit-aware KV targets compaction edits on shared servers. Background work needs its own prefill capacity (C4).

**Caveats:**

- Every model is priced with topic 16's Qwen3-32B H100 TTFT/TPOT models.
- E0a at N > 1 attributes prefill time to edits by their share of fresh prefill tokens.
- E0f is a static first-order replay.
- E0e's periodic policies rest partly on a fitted age model (M1).

## 4. Data inventory

`data/` is gitignored except `*.md` and `*.svg`. This host has no Drive watcher, so the data stays local unless it is
pushed with `python3 research/common/drive_data.py push 17`.

| path | what it is | from |
|---|---|---|
| `data/tables/E0a_census_n1.md`, `E0a_census_mu.md`, `E0b_lookahead.md`, `E0c_slack_n1.md`, `E0d_stall.md`, `E0e_drift.md`, `E0f_hysteresis.md` | the tables (tracked) | each script |
| `data/e0a/calls_he.jsonl` (46 MB), `calls_t16.jsonl`, `validation_t16.jsonl` | one record per served call; topic 16 validation | `hs_census.py` |
| `data/e0b/transitions.jsonl` (34 MB), `summary.json` | one record per lead-call transition with its edit kind and known shares | `hs_lookahead.py` |
| `data/e0c/windows_he.jsonl` (27 MB), `windows_t16.jsonl` | idle windows with class and refresh time | `hs_slack.py` |
| `data/e0d/fit.json` | hinge-fit coefficients, budgets and D3 numbers per cell | `hs_stall.py` |
| `data/e0e/calls.jsonl`, `policies.jsonl`, `summary.json` | refresh value per edited call; 61 policies | `hs_drift.py` |
| `data/e0f/calls.jsonl` (39 MB), `side_calls.jsonl`, `summary.json` | per-call edit work, as run vs hysteresis | `hs_hysteresis.py` |
| `research/11_multiuser_kv/data/multiuser_live/cells/*/server/*/sched.jsonl.xz` (32 logs, 121 MB) | topic 11's scheduler logs, pulled from Drive | rclone (E0d input) |

## 5. Reproducing

Every script sits one level below `research/` and imports through `_paths.py`. CPU only; times are with 24 workers.
Tests need pytest; the scratch environment that ran them had pytest and numpy only.

```
/data/fritz/venv/bin/python research/17_harness_aware_serving/hs_census.py --workers 24       # E0a N = 1 (~15 s)
/data/fritz/venv/bin/python research/17_harness_aware_serving/hs_census_mu.py                 # E0a N > 1 (from topic 11's tables)
/data/fritz/venv/bin/python research/17_harness_aware_serving/hs_lookahead.py --workers 24    # E0b (~15 s)
/data/fritz/venv/bin/python research/17_harness_aware_serving/hs_slack.py                     # E0c (after hs_census)
/data/fritz/venv/bin/python research/17_harness_aware_serving/hs_hysteresis.py --workers 24   # E0f (~15 s)
/data/fritz/venv/bin/python research/17_harness_aware_serving/hs_drift.py                     # E0e (~25 s)
/data/fritz/venv/bin/python research/17_harness_aware_serving/hs_stall.py --cells n04,n08,n12,n16,s3gate16,n01a --refresh-tokens-per-call 384   # E0d (~30 s; needs the pulled logs)
python -m pytest research/17_harness_aware_serving                                           # 62 tests
```
