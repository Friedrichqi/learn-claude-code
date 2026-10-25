"""Shared helpers for topic 17's offline check (E0): paths, the effect x urgency map, data loaders, cost models.

Every E0 script imports this module through research/_paths.py:

    sys.path.insert(0, str(Path(__file__).resolve().parents[1])); import _paths; import hs_common as hc

Inputs are read-only:
  harness_eval   research/16_epic_harness/harness_eval/jobs/<model>/<bench>/<phase>__cNN/<trial>/agent/s15/{calls,events}.jsonl
                 (the profile = q-phase trials, 250 lead calls, as REPORT.md section 2; HS_HE_JOBS overrides the root)
  topic 16       research/16_epic_harness/data (local): runs, server_calls.jsonl, E-A replays, latency models
  topics 11-15   research/<topic>/data, or HS_DATA_ROOT/research/<topic>/data when Drive data was pulled elsewhere
"""
from __future__ import annotations

import json
import lzma
import os
import sys
from pathlib import Path
from typing import Iterator

HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parent
REPO = RESEARCH.parent
DATA = HERE / "data"
TABLES = DATA / "tables"
HE_JOBS = Path(os.environ.get("HS_HE_JOBS", RESEARCH / "16_epic_harness" / "harness_eval" / "jobs"))
T16 = RESEARCH / "16_epic_harness" / "data"
HE_MODELS = ("qwen3-32b", "qwen3.8-27b", "deepseek-v4-flash", "minimax-m2.7")
HE_BENCHES = ("swe", "swepro", "tbench")


def topic_data(topic: str) -> Path:
    """research/<topic>/data, or its copy under HS_DATA_ROOT (Drive pulls kept off the root disk)."""
    root = os.environ.get("HS_DATA_ROOT")
    if root:
        alt = Path(root) / "research" / topic / "data"
        if alt.exists():
            return alt
    return RESEARCH / topic / "data"


# -- effect x urgency (the doc's section 3) -----------------------------------------------------------------

# purpose (s15 trace / harness_eval calls.jsonl / 11's request ids) -> (effect, urgency)
#   effect:  A = append, M = modify history, S = side context
#   urgency: C = critical (someone waits now), D = deferrable (a consumer with a soft deadline), B = background
PURPOSE_TYPE = {
    "lead": ("A", "C"),
    "one_shot": ("A", "C"),            # the synchronous `task` subagent: the lead blocks on it
    "teammate": ("A", "D"),            # async; critical only when the lead waits on its result
    "workflow_agent": ("A", "C"),      # s16 nodes run inside the lead's tool call
    "compaction_summary": ("M", "C"),
    "memory_recall": ("S", "C"),       # appended to the next prompt; runs before the lead call
    "memory_extract": ("S", "D"),      # consumer: the next turn's recall
    "memory_consolidate": ("S", "B"),
}
PURPOSE_CODE = {"ld": "lead", "mx": "memory_extract", "mr": "memory_recall", "mc": "memory_consolidate",
                "cs": "compaction_summary", "os": "one_shot", "tm": "teammate"}


def call_type(purpose: str | None) -> tuple[str, str]:
    p = PURPOSE_CODE.get(purpose or "", purpose or "")
    return PURPOSE_TYPE.get(p, ("?", "?"))


# -- harness_eval -------------------------------------------------------------------------------------------

def he_trials(models: tuple[str, ...] = HE_MODELS, benches: tuple[str, ...] = HE_BENCHES,
              phase_prefix: str = "q") -> Iterator[tuple[str, str, Path]]:
    """(model, bench, trial dir) for every harness_eval trial of the given phases that logged calls."""
    for model in models:
        for bench in benches:
            base = HE_JOBS / model / bench
            if not base.is_dir():
                continue
            for job in sorted(base.iterdir()):
                if not job.is_dir() or not job.name.startswith(phase_prefix):
                    continue
                for trial in sorted(job.iterdir()):
                    if (trial / "agent" / "s15" / "calls.jsonl").exists():
                        yield model, bench, trial


def read_jsonl(path: Path) -> Iterator[dict]:
    opener = lzma.open if path.suffix == ".xz" else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def he_calls(trial: Path) -> Iterator[dict]:
    yield from read_jsonl(trial / "agent" / "s15" / "calls.jsonl")


def he_events(trial: Path) -> Iterator[dict]:
    p = trial / "agent" / "s15" / "events.jsonl"
    if p.exists():
        yield from read_jsonl(p)


# -- cost models --------------------------------------------------------------------------------------------

# 11's step model (research/11_multiuser_kv/data/multiuser_live/multiuser_tables.md:5; mu_analyze.py:234-290),
# H200, Qwen3-32B, vLLM 0.29, 2,033,364 steps, R^2 = 1.000. Seconds.
STEP_MODEL_11 = {"fixed": 0.01846, "mixed": -0.005877, "pf_tok": 0.09689, "pf_attn": 0.003464,
                 "dec_seq": 2.775e-05, "dec_ctx": 0.07729}


def step_time_11(chunks: list[tuple[int, int]], decode_ctx: list[int], c: dict = STEP_MODEL_11) -> float:
    """Seconds for one engine step: chunks = [(n new tokens, a cached tokens before them)], decode_ctx = the
    context length of every decoding sequence. Same features as mu_analyze.step_features."""
    pf_tok = sum(n for n, _ in chunks)
    pf_attn = sum(n * (a + n / 2) for n, a in chunks) / 1e6
    return (c["fixed"] + c["mixed"] * (1.0 if pf_tok else 0.0) + c["pf_tok"] * pf_tok / 1e3
            + c["pf_attn"] * pf_attn + c["dec_seq"] * len(decode_ctx) + c["dec_ctx"] * sum(decode_ctx) / 1e6)


def latency_models_16() -> tuple[dict, list[dict]]:
    """16's H100 TTFT models (compute and reuse families) and TPOT table (epic_latency.py report)."""
    mj = json.loads((T16 / "live" / "main" / "latency" / "models.json").read_text())
    return mj["models"], mj["tpot"]


def ttft_ms_16(models: dict, family: str, computed: float, loaded: float, prompt: float) -> float:
    """TTFT = b0 + b1*C + b2*C*P/1e3 + b3*S (ms); family 'compute' (recompute / prefix) or 'reuse' (shift / EPIC)."""
    b = models[family]["beta"]
    return b[0] + b[1] * computed + b[2] * computed * prompt / 1e3 + b[3] * loaded


def tpot_ms_16(tp: list[dict], ctx: float) -> float:
    """ms per output token at a context, one sequence, linear between the measured contexts (epic_latency.tpot_at)."""
    pts = sorted((t["ctx"], t["tpot_ms"]) for t in tp if t["n_seq"] == 1)
    if ctx <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if ctx <= x1:
            return y0 + (y1 - y0) * (ctx - x0) / (x1 - x0)
    return pts[-1][1]


# -- output -------------------------------------------------------------------------------------------------

def write_table(name: str, title: str, lines: list[str], note: str = "") -> Path:
    """Write data/tables/<name>.md (tracked: *.md under data/ is not ignored)."""
    TABLES.mkdir(parents=True, exist_ok=True)
    out = TABLES / f"{name}.md"
    body = [f"# {title}", "", f"Generated by `research/17_harness_aware_serving/{Path(sys.argv[0]).name}`.", ""]
    if note:
        body += [note, ""]
    out.write_text("\n".join(body + lines) + "\n", encoding="utf-8")
    return out
