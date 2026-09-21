# Real-world inheritance profiling — pipeline and reproduction

Companion to `weekly_progress/092326/realworld_inheritance.md`, which holds the results. This file
documents the pipeline and how to re-run it.

## 1. Why a new pipeline

Every earlier study in this series measured effects on workloads written to show them. This one
re-derives the same metrics from (a) published traces of real agent harnesses on real benchmarks and
(b) live sessions driven by lm-evaluation-harness. Two layers, deliberately:

| layer | source | measures | cost |
|---|---|---|---|
| **replay** | `nvidia/Open-SWE-Traces`, `agent-evals/hal_traces` | real prompts, rounds, tokens, per-call latency, re-read behaviour; rounds removed is *counterfactual* | none |
| **live** | s15 harness behind an lm-eval model endpoint | measured rounds and latency, lead-built boards, official accuracy | API |

## 2. Modules

| file | role |
|---|---|
| `scripts/evict_policies.py` | 16 eviction policies + measured recovery cost. Used by BOTH layers, so live and replayed cells stay comparable |
| `scripts/traj_ingest.py` | HF trajectory corpora → canonical read-item traces. `read_key` is the single definition of "a read" |
| `scripts/hal_ingest.py` | HAL run archives → the same form, plus measured per-call latency |
| `scripts/replay_sweep.py` | E1-R / E2-R / E3-R: arms, cross-task, and the budget × policy grid |
| `scripts/lmeval_agent_model.py` | registers `s15-agent` as an lm-eval model: one task prompt → one agentic session → its final message |
| `scripts/lmeval_workloads.py` | the G1 census and the live arm/budget/policy sweep |
| `scripts/test_evict_policies.py` | the invariants every policy must satisfy |

Modified: `profile_run.py` (`--prewarm-evict`, `--prewarm-budget`, `--answer-out`, `--model`, plus
two bug fixes), `inherit_loop.py` (`refetch_after_injection`).

## 3. Design decisions that decide the numbers

**The unit of retention is one whole observation.** A fragmented handoff was measured to be re-read
88% of the time (2026-09-16), so no policy may split a span.

**Identity is content, not path.** Agents edit the files they read, so the same span fetched twice is
usually not the same bytes. Matching on path would count an edited re-read as reusable and a cache
built that way would serve it stale. The gap is reported as a `stale` column because it turned out to
be most of what looks like redundancy in a bash-only harness: within a trajectory, ⅓ of paths are
read more than once and only 4–6% of those repeats return identical bytes.

**Two ceilings, and they are ceilings only for their own metric.** Belady/MIN and the byte-density
oracle rank on what the successor will go on to read. They are computable offline and refused by
`profile_run.py --prewarm-evict`. They are *not* ceilings for the line-level metric, which they do
not optimise — the same trap the earlier study hit with a byte-density "oracle" for rounds.

**Recovery cost must be measured per item.** With only the modelled cost — which is monotone in
size — "keep the costliest to recover" becomes "keep the largest" and the two columns come out
byte-identical. The priced version carries each item's reuse count and whether a search round had to
locate it.

**Online caches are not monotone in budget.** LRU and FIFO violate it in 17 of 60 random pools
because LRU's stack property assumes equal-sized objects. Monotonicity is asserted only for the
offline family (`evict_policies.MONOTONE_IN_BUDGET`).

## 4. Environment

```bash
uv venv --python 3.12 /home/yq335/rw_env
uv pip install --python /home/yq335/rw_env/bin/python \
    "lm-eval==0.4.13" datasets huggingface_hub anthropic cryptography pytest "httpx<1" python-dotenv
export HF_HOME=/tmp/<scratch>/hf          # /home has ~12 GB free; corpora are 10-28 GB per split
```

No torch: `lm-eval`'s base dependencies do not include it (609 MB total). The corpora are streamed,
never materialised.

**Provider.** The repo `.env` pins `MODEL_ID=glm-5.3-flashx`, which this z.ai plan is not entitled to
(`429`, code `1311`). `code.py:77` calls `load_dotenv(override=True)`, so exporting `MODEL_ID` does
nothing — pass `--model glm-5.3-flash` instead. The DeepSeek lane returns `Insufficient Balance`.

## 5. Reproduce

```bash
cd /home/yq335/learn-claude-code
export HF_HOME=/tmp/<scratch>/hf
PY=/home/yq335/rw_env/bin/python

# --- invariants (no network, no API) -------------------------------------------------------
$PY -m pytest s15_integrated_harness/scripts/test_evict_policies.py -q

# --- ingest (streams from HF; ~35 MB on disk for 1,200 trajectories) ------------------------
for s in sweagent openhands minisweagent; do
  $PY s15_integrated_harness/scripts/traj_ingest.py --corpus open_swe --split $s --limit 400
done

$PY s15_integrated_harness/scripts/hal_ingest.py --list --pattern "taubench|gaia"   # pick archives
curl -sL -o T.zip "https://huggingface.co/datasets/agent-evals/hal_traces/resolve/main/<archive>"
$PY s15_integrated_harness/scripts/hal_ingest.py --archive T.zip

# --- results (no API) ----------------------------------------------------------------------
R=s15_integrated_harness/scripts/replay_sweep.py
for c in open_swe hal; do
  $PY $R s15_integrated_harness/traces/realworld/$c --stage arms      --json .../arms_$c.md
  $PY $R s15_integrated_harness/traces/realworld/$c --stage cross     --json .../cross_$c.md
  $PY $R s15_integrated_harness/traces/realworld/$c --stage crossgrid --json .../crossgrid_$c.md
done

# --- the lm-eval endpoint, end to end ------------------------------------------------------
$PY - <<'EOF'
import sys; sys.path.insert(0, "s15_integrated_harness/scripts")
import lmeval_agent_model, lm_eval           # the import fires @register_model
print(lm_eval.simple_evaluate(
    model="s15-agent",
    model_args="repo=/home/yq335/learn-claude-code,arm=none,model=glm-5.3-flash,max_seconds=240",
    tasks=["gsm8k"], limit=3, bootstrap_iters=0)["results"])
EOF

# --- G1 census: does the lead build a real board on real tasks? -----------------------------
$PY s15_integrated_harness/scripts/lmeval_workloads.py --census --limit 3
```

`lm-eval 0.4.13` has no `--plugins` flag (it landed after the release), so the CLI cannot see a local
model module — use `simple_evaluate` as above.

## 6. Known gaps

- `qasper` needs a dataset script, removed in `datasets` 5.x. `gpqa` is gated and needs `HF_TOKEN`.
  `ifeval` needs `langdetect`.
- CORE-bench and SciCode ingest to zero reads: SciCode's agent is single-shot, CORE-bench's writes
  rather than retrieves. Their rounds are counted, their reads are not.
- The live arm/budget/policy sweep (P4/P5) has not run. `lmeval_workloads.py` takes `--arms`,
  `--evict` and `--budgets` and is ready for it.
- Line-level content addressing is measured but not implemented in the injector, although it is the
  largest single lever on coding work (2–3× the removable rounds).
