#!/usr/bin/env python3
"""Shared plumbing for research/16_epic_harness: paths, the one-GPU vLLM lifecycle, lanes, rendering.

Every GPU stage of this topic runs exactly one engine on exactly one GPU and must leave that GPU empty
when it ends, however it ends. `served()` starts `vllm serve` in its own process group (setsid),
waits for /health, and on exit (normal, exception, SIGINT/SIGTERM/SIGHUP) sends TERM to the group,
waits, sends KILL, and then polls nvidia-smi until the pinned GPU has no compute process and under
1 GiB in use. `pick_gpu()` refuses busy GPUs; `free_port()` avoids ports whose digits contain 429/529
(the harness retries any error text containing them, topic 11).

Lanes (10_rope_shift/run_sweep.py's device): an rsync copy of a source tree on /data with its own
`.env`, so profile_run's per-run state wipe and every sandbox write stay inside the lane.
"""
from __future__ import annotations

import contextlib
import json
import os
import random
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

os.environ.setdefault("HF_HOME", "/data/shared/huggingface")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DATA = HERE / "data"
SCRATCH = Path(os.environ.get("EPIC_SCRATCH", "/data/fritz/epic"))
LANES = SCRATCH / "lanes"
MODEL = "Qwen/Qwen3-32B"
TEMPLATE = HERE / "qwen3_append_only.jinja"
PYTHON = "/data/fritz/venv/bin/python"
VLLM = "/data/fritz/venv/bin/vllm"
RSYNC_EXCLUDES = [".git", "s15_integrated_harness/traces", "research/*/data", "profiling_sandbox",
                  "__pycache__", ".memory", ".tasks*", ".mailboxes", ".transcripts", ".task_outputs",
                  ".worktrees", ".scheduled_tasks.json", ".teams", "*.log", ".env", "weekly_progress",
                  ".zcode", ".claude", "web/node_modules", "web/.next"]
SERVE_COMMON = ["--max-model-len", "32768", "--max-num-seqs", "8", "--seed", "0",
                "--enable-auto-tool-choice", "--tool-call-parser", "hermes", "--reasoning-parser", "qwen3",
                "--default-chat-template-kwargs", '{"enable_thinking": false}',
                "--uvicorn-log-level", "warning"]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# -- GPU and ports --------------------------------------------------------------------------------

def gpu_apps(gpu: int) -> list[str]:
    out = subprocess.run(["nvidia-smi", f"--id={gpu}", "--query-compute-apps=pid",
                          "--format=csv,noheader"], capture_output=True, text=True)
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def gpu_mem_used(gpu: int) -> int:
    out = subprocess.run(["nvidia-smi", f"--id={gpu}", "--query-gpu=memory.used",
                          "--format=csv,noheader,nounits"], capture_output=True, text=True)
    return int(out.stdout.strip() or 0)


def gpu_free(gpu: int) -> bool:
    return not gpu_apps(gpu) and gpu_mem_used(gpu) < 1024


def pick_gpu(preferred: int | None = None, wait_s: float = 4 * 3600) -> int:
    """This process's GPU: EPIC_GPU (default 0), and only that one -- a process uses one H100, so a busy
    GPU means wait, never fall back to another one. (From 2026-10-09 14:30 UTC up to four processes ran
    at once, each pinned to its own GPU claimed in the lab's GPU sheet.) `preferred` is ignored unless it
    equals the pinned GPU (kept for call compatibility)."""
    gpu = int(os.environ.get("EPIC_GPU", "0"))
    deadline = time.monotonic() + wait_s
    while not gpu_free(gpu):
        if time.monotonic() > deadline:
            raise SystemExit(f"GPU {gpu} still busy after {wait_s:.0f} s: {gpu_apps(gpu)}")
        time.sleep(10)
    return gpu


def wait_gpu_free(gpu: int, timeout: float = 180.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if gpu_free(gpu):
            return True
        time.sleep(2)
    return False


def free_port() -> int:
    while True:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if "429" not in str(port) and "529" not in str(port):
            return port


def server_logs(live: Path) -> list[Path]:
    """The middleware's per-call logs of a live directory: server_calls.jsonl, plus server_calls.<tag>.jsonl
    for every further server of a sweep split over several GPUs (each run lives on one server)."""
    return sorted(live.glob("server_calls*.jsonl"))


def healthy(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=5) as response:
            return response.status == 200
    except Exception:
        return False


# -- engine lifecycle -----------------------------------------------------------------------------

def kill_group(proc: subprocess.Popen, grace: float = 30.0) -> None:
    if proc.poll() is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(grace)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(30)


SESSIONS: set = set()            # live epic_session processes (their own process groups)
STOPPING = threading.Event()     # set on a signal: sweep workers start no further runs


def stop_sessions() -> None:
    STOPPING.set()
    for proc in list(SESSIONS):
        kill_group(proc, grace=20)


@contextlib.contextmanager
def guarded(gpu: int, procs: list):
    """Kill every registered process group on exit or signal, then wait for the GPU to empty.
    A signal also stops running sessions at once, so worker threads waiting on them return."""
    def on_signal(signum, _frame):
        stop_sessions()
        raise SystemExit(128 + signum)
    previous = {s: signal.signal(s, on_signal) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        yield
    finally:
        for proc in procs:
            kill_group(proc)
        free = wait_gpu_free(gpu)
        log(f"GPU {gpu} {'free' if free else 'STILL BUSY: ' + str(gpu_apps(gpu))} "
            f"({gpu_mem_used(gpu)} MiB used)")
        for s, handler in previous.items():
            signal.signal(s, handler)


@contextlib.contextmanager
def served(gpu: int, log_path: Path, extra: list[str], env_extra: dict | None = None,
           util: float = 0.92, timeout: float = 1800.0):
    """`vllm serve` on one GPU; yields the base URL; always stops the engine and frees the GPU."""
    if not gpu_free(gpu):
        raise SystemExit(f"GPU {gpu} is busy before start: {gpu_apps(gpu)}")
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": str(gpu), "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
           "PYTHONUNBUFFERED": "1", **(env_extra or {})}
    cmd = [VLLM, "serve", MODEL, "--host", "127.0.0.1", "--port", str(port),
           "--gpu-memory-utilization", str(util), *SERVE_COMMON, *extra]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("a", encoding="utf-8")
    handle.write(f"\n# {time.strftime('%Y-%m-%dT%H:%M:%S')} GPU {gpu}\n# {' '.join(cmd)}\n")
    handle.flush()
    proc = subprocess.Popen(cmd, stdout=handle, stderr=subprocess.STDOUT, env=env,
                            start_new_session=True, cwd=REPO)
    procs = [proc]
    with guarded(gpu, procs):
        started = time.monotonic()
        while not healthy(base_url):
            if proc.poll() is not None:
                raise RuntimeError(f"vllm serve exited with {proc.returncode}; see {log_path}")
            if time.monotonic() - started > timeout:
                raise RuntimeError(f"vllm serve not healthy after {timeout:.0f} s; see {log_path}")
            time.sleep(5)
        log(f"vllm up on GPU {gpu} at {base_url} after {time.monotonic() - started:.0f} s")
        try:
            yield base_url
        finally:
            handle.flush()
    handle.close()


def run_isolated(gpu: int, cmd: list[str], log_path: Path, env_extra: dict | None = None) -> int:
    """A GPU child process (offline engine) in its own process group, killed with it on any exit."""
    if not gpu_free(gpu):
        raise SystemExit(f"GPU {gpu} is busy before start: {gpu_apps(gpu)}")
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": str(gpu), "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
           "PYTHONUNBUFFERED": "1", **(env_extra or {})}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n# {' '.join(cmd)}\n")
        handle.flush()
        proc = subprocess.Popen(cmd, stdout=handle, stderr=subprocess.STDOUT, env=env,
                                start_new_session=True, cwd=REPO)
        with guarded(gpu, [proc]):
            return proc.wait()


# -- lanes ----------------------------------------------------------------------------------------

def make_lane(lane: Path, base_url: str, source: Path = REPO, extra_files: list[Path] = ()) -> Path:
    """rsync `source` into `lane` and point its .env at `base_url`; `extra_files` (paths relative to
    REPO) are copied on top, e.g. this topic's scripts into a lane made from an older tree."""
    lane.mkdir(parents=True, exist_ok=True)
    cmd = ["rsync", "-a", "--delete"]
    for pattern in RSYNC_EXCLUDES:
        cmd += ["--exclude", pattern]
    subprocess.run(cmd + [f"{source}/", f"{lane}/"], check=True)
    for rel in extra_files:
        target = lane / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if (REPO / rel).is_dir():
            subprocess.run(["rsync", "-a", "--exclude", "data", "--exclude", "__pycache__",
                            f"{REPO / rel}/", f"{target}/"], check=True)
        else:
            shutil.copy2(REPO / rel, target)
    (lane / ".env").write_text(f"ANTHROPIC_API_KEY=EMPTY\nANTHROPIC_BASE_URL={base_url}\nMODEL_ID={MODEL}\n",
                               encoding="utf-8")
    return lane


def export_commit(commit: str, target: Path) -> Path:
    """A clean tree of `commit` (e.g. the pre-change harness for E-0's configuration A)."""
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    archive = subprocess.run(["git", "-C", str(REPO), "archive", commit], capture_output=True, check=True)
    subprocess.run(["tar", "-x", "-C", str(target)], input=archive.stdout, check=True)
    return target


def run_session(lane: Path, row: dict, arm: str, regime: str, run_dir: Path, label: str,
                lead_cap: int, deadline_s: float, timeout: float | None = None, out_cap: int = 0) -> dict:
    """One epic_session.py run inside `lane`; returns its meta (or a failure stub)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    row_file = run_dir / f"{label}.row.json"
    row_file.write_text(json.dumps(row, ensure_ascii=False, indent=1), encoding="utf-8")
    cmd = [PYTHON, str(lane / "research" / "16_epic_harness" / "epic_session.py"), "--row", str(row_file),
           "--arm", arm, "--regime", regime, "--run-dir", str(run_dir), "--label", label,
           "--lead-cap", str(lead_cap), "--deadline-s", str(deadline_s), "--out-cap", str(out_cap)]
    started = time.time()
    with (run_dir / "console.log").open("w", encoding="utf-8") as console:
        proc = subprocess.Popen(cmd, cwd=lane, stdout=console, stderr=subprocess.STDOUT,
                                env={**os.environ, "PYTHONUNBUFFERED": "1"}, start_new_session=True)
        SESSIONS.add(proc)
        try:
            code = proc.wait(timeout=timeout or deadline_s + 1800)
        except subprocess.TimeoutExpired:
            kill_group(proc, grace=30)
            code = "timeout"
        finally:
            SESSIONS.discard(proc)
    meta_path = run_dir / f"{label}.meta.json"
    if meta_path.exists():
        return json.loads(meta_path.read_text(encoding="utf-8"))
    return {"label": label, "status": f"failed (exit {code})", "wall_s": round(time.time() - started, 1)}


# -- rendering --------------------------------------------------------------------------------------

def renderer(template: str | None = None):
    """10_rope_shift/render.py's exact /v1/messages renderer, optionally with a --chat-template."""
    from render import MODELS, Renderer  # 10_rope_shift (sets HF_HOME only if unset)

    class TemplateRenderer(Renderer):
        def __init__(self, spec, chat_template):
            super().__init__(spec, max_model_len=32768)
            self.chat_template = chat_template

        def render(self, request: dict, add_generation_prompt: bool = True):
            from vllm.entrypoints.chat_utils import parse_chat_messages
            from vllm.renderers.hf import resolve_chat_template_content_format, safe_apply_chat_template

            chat = self.chat_request(request, add_generation_prompt)
            tool_dicts = [tool.model_dump() for tool in chat.tools] if chat.tools else None
            defaults = dict(self.default_kwargs, tools=tool_dicts, tokenize=False)
            params = chat.build_chat_params(self.chat_template, "auto").with_defaults(defaults)
            content_format = resolve_chat_template_content_format(
                chat_template=params.chat_template, tools=tool_dicts,
                given_format=params.chat_template_content_format, tokenizer=self.tokenizer,
                model_config=self.model_config)
            conversation, _, _ = parse_chat_messages(chat.messages, self.model_config,
                                                     content_format=content_format)
            prompt = safe_apply_chat_template(self.model_config, self.tokenizer, conversation,
                                              **params.get_apply_chat_template_kwargs())
            return prompt, self.tokenizer.encode(prompt, add_special_tokens=False)

    return TemplateRenderer(MODELS["qwen32b"], template)


def seeded(seed: str) -> random.Random:
    return random.Random(seed)


if __name__ == "__main__":
    print(json.dumps({"repo": str(REPO), "scratch": str(SCRATCH), "gpu": pick_gpu(),
                      "port": free_port(), "python": sys.executable}))
