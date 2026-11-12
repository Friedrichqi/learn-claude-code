"""Topic 24: shared constants and helpers (paths, core map, server flags, exp02 imports, clocks, JSON lines).

Every topic-24 script imports this module. It never starts anything by itself.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

DIR = Path(__file__).resolve().parent
REPO = DIR.parents[1]
DATA = DIR / "data"
RUNS = DATA / "runs"
HW = DATA / "hw"
TABLES = DATA / "tables"
PLUGIN = DIR / "plugin"
TASKS_JSON = DATA / "tasks.json"
TASKDIRS = DATA / "taskdirs"
QUEUE = DATA / "queue"
JOBS = DATA / "jobs"

# exp02 (~/dgxspark-profiling, commit 35b3781): imported, never copied
SPARK = Path(os.environ.get("SP_SPARK_PROFILING", Path.home() / "dgxspark-profiling"))
EXP02 = SPARK / "exp02_vllm_um"
EXP02_COMMIT = "35b3781"
EXP01_VENV_PY = SPARK / "exp01_um_profiling" / ".venv" / "bin" / "python"   # pandas/matplotlib for the analysis
VLLM_PY = Path.home() / ".venv" / "bin" / "python"                        # vLLM 0.32.0, torch, pynvml
HARBOR_VENV = Path.home() / ".venv-harbor"

HF_HOME = os.environ.get("HF_HOME") or "/home/hf_data"

# -- model variants (PLAN2.md): same architecture, three weight precisions --------------------------------------------
# flags: added to the server command of that variant only. The KV cache is pinned to BF16 for the quantized
# checkpoints (the unsloth one carries FP8 KV scales that vLLM could otherwise pick up), so a pool size means the
# same number of tokens for all three. BF16 keeps exactly the flags of the part-1 cell n08.
VARIANTS = {
    "bf16": {"repo": "Qwen/Qwen3.8-27B", "label": "BF16", "pool": "36G", "flags": []},
    "fp8": {"repo": "Qwen/Qwen3.8-27B-FP8", "label": "FP8", "pool": "36G",
            "flags": ["--kv-cache-dtype", "bfloat16"]},
    "nvfp4": {"repo": "unsloth/Qwen3.8-27B-NVFP4", "label": "NVFP4 (mixed 4-bit / FP8)", "pool": "36G",
              "flags": ["--kv-cache-dtype", "bfloat16"]},
}
VARIANT_ORDER = ["bf16", "fp8", "nvfp4"]
VARIANTS_JSON = DATA / "variants.json"     # written by `sp_check.py server --variant`: pool and flags per variant
VARIANT = "bf16"
MODEL = VARIANTS[VARIANT]["repo"]
PORT = 8000
URL = f"http://127.0.0.1:{PORT}"

# -- cores (20: X925 = 5-9, 15-19; A725 = 0-4, 10-14) -----------------------------------------------------------------
VLLM_CORES = "9,17,18,19"
PROBE_CPU = 8
AUX_CORES = "0,11,13"                     # samplers, counter samplers, cell runner, umbench's main thread
AGENT_CORES = "1,2,3,4,5,6,7,10,12,14,15,16"
AGENT_CORES_RANGE = "1-7,10,12,14-16"
CORE_GROUPS = {"vllm": [9, 17, 18, 19], "probe": [8], "aux": [0, 11, 13],
               "agents": [1, 2, 3, 4, 5, 6, 7, 10, 12, 14, 15, 16]}
X925 = [5, 6, 7, 8, 9, 15, 16, 17, 18, 19]
A725 = [0, 1, 2, 3, 4, 10, 11, 12, 13, 14]

# -- server -------------------------------------------------------------------------------------------------------------
POOL = os.environ.get("SP_POOL", "36G")    # fixed by the memory rule after S2 (36G or 32G); the cell records it
CHAT_TEMPLATE = REPO / "research" / "23_efficiency_tools" / "qwen3_8_append_only.jinja"
HIST_BUCKETS = {
    "time_to_first_token": [0.25, 0.5, 1, 1.5, 2, 3, 4, 5, 7.5, 10, 15, 20, 30, 45, 60, 90, 120, 150, 180, 240, 300,
                            360, 420, 480, 540, 600, 750, 900, 1200, 1800, 3600],
    "inter_token_latency": [0.05, 0.1, 0.15, 0.2, 0.225, 0.25, 0.275, 0.3, 0.325, 0.35, 0.375, 0.4, 0.45, 0.5, 0.6,
                            0.75, 1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10, 20, 40, 80, 160, 320, 640],
    "request_latency": [0.5, 1, 2, 3, 5, 7.5, 10, 15, 20, 30, 45, 60, 90, 120, 150, 180, 240, 300, 360, 420, 480, 600,
                        750, 900, 1200, 1800, 2700, 3600, 7200],
    "request_num_preemptions": [0.5, 1, 2, 3, 4, 5, 10, 20],
}
# exp02's Server builds: taskset -c VLLM_CORES vllm serve MODEL + BASE + --kv-cache-memory-bytes POOL +
# --enable-prefix-caching + extra (run_study.py:64-66, 189-190)
BASE = ["--host", "127.0.0.1", "--port", str(PORT), "--served-model-name", MODEL, "--seed", "0", "--dtype", "bfloat16",
        "--language-model-only", "--max-model-len", "131072", "--max-num-seqs", "64"]
EXTRA = ["--gpu-memory-utilization", "0.85", "--enable-auto-tool-choice", "--tool-call-parser", "qwen3_coder",
         "--reasoning-parser", "qwen3", "--enable-prompt-tokens-details", "--chat-template", str(CHAT_TEMPLATE),
         "--enable-logging-iteration-details", "--custom-histogram-buckets", json.dumps(HIST_BUCKETS)]
_EXTRA0 = list(EXTRA)


def variant_settings(key: str) -> dict:
    """The variant's table entry, with the pool and flags fixed by its server check (data/variants.json) on top."""
    v = dict(VARIANTS[key])
    try:
        v.update(json.loads(VARIANTS_JSON.read_text()).get(key, {}))
    except (OSError, ValueError):
        pass
    return v


def use_variant(key: str, pool: str | None = None) -> dict:
    """Select the model for this process: sets VARIANT, MODEL, POOL and the server flags (BASE, EXTRA) in place.
    Call it before exp02(), which hands the model to exp02's Server, smoke request and page-cache eviction."""
    global VARIANT, MODEL, POOL
    v = variant_settings(key)
    VARIANT, MODEL = key, v["repo"]
    POOL = pool or os.environ.get("SP_POOL") or v["pool"]
    BASE[BASE.index("--served-model-name") + 1] = MODEL
    EXTRA[:] = _EXTRA0 + list(v.get("flags") or [])
    return v


def hub_dir(repo: str | None = None) -> Path:
    return Path(HF_HOME) / "hub" / ("models--" + (repo or MODEL).replace("/", "--"))


def snapshot_dir(repo: str | None = None) -> Path | None:
    snaps = sorted((hub_dir(repo) / "snapshots").glob("*"))
    return snaps[-1] if snaps else None


def server_env(reqlog_dir: Path) -> dict:
    """Extra environment for the vLLM server: the plugin and no usage reporting. exp02's Server adds HF_HUB_OFFLINE,
    CUDA_CACHE_MAXSIZE and CPATH itself (run_study.py:187-188); passing those again would raise TypeError."""
    pp = os.pathsep.join(p for p in (str(PLUGIN), os.environ.get("PYTHONPATH", "")) if p)
    # the venv's bin first on PATH: FlashInfer builds its FP4 GEMM with the venv's ninja on the first NVFP4 start
    path = os.pathsep.join([str(VLLM_PY.parent), os.environ.get("PATH", "")])
    return {"PYTHONPATH": pp, "SP_REQLOG_DIR": str(reqlog_dir), "VLLM_NO_USAGE_STATS": "1", "DO_NOT_TRACK": "1",
            "HF_HOME": HF_HOME, "PATH": path}


def exp02(raw: Path, logs: Path, log_path: Path | None = None):
    """Import exp02's run_study (and its src/ modules) and point its output folders at this topic's run."""
    os.environ.setdefault("HF_HOME", HF_HOME)
    for p in (EXP02 / "scripts", EXP02 / "src"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    import run_study as rs  # noqa: E402
    raw.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    rs.RAW, rs.LOGS = raw, logs
    rs.BASE = list(BASE)
    rs.MODEL = MODEL                          # exp02's Server serves rs.MODEL and its smoke request names it
    rs.HF_HUB = hub_dir()                     # evict_model_pages() drops this repo's blobs from the page cache
    if log_path is not None:
        rs._logf = open(log_path, "a")
    return rs


def exp02_commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(SPARK), "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except Exception:                                           # noqa: BLE001
        return ""


# -- clocks and records -------------------------------------------------------------------------------------------------
def mono() -> int:
    return time.monotonic_ns()


def wall() -> int:
    return time.time_ns()


def clocks() -> dict:
    """A pair of clock readings, so any wall-clock stamp can be mapped to CLOCK_MONOTONIC."""
    return {"t": time.monotonic_ns(), "w": time.time_ns()}


def append_jsonl(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(rec, separators=(",", ":"), default=str) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    out = []
    if not Path(path).exists():
        return out
    with open(path, errors="ignore") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def log(msg: str, path: Path | None = None) -> None:
    s = time.strftime("%H:%M:%S ") + msg
    print(s, flush=True)
    if path is not None:
        with open(path, "a") as f:
            f.write(s + "\n")


def meminfo() -> dict:
    out = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":", 1)
            out[k] = int(v.split()[0]) * 1024
    return out
