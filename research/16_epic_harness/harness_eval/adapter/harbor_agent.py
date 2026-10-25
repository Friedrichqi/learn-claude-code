"""s15 as a Harbor agent: one adapter for SWE-bench Verified, SWE-bench Pro, Terminal-Bench and GAIA.

    harbor run -d terminal-bench/terminal-bench-2-1 -e docker \
        -a adapter.harbor_agent:S15Agent -m qwen3-32b \
        --ak base_url=http://127.0.0.1:8100 --ak bench=tbench --ak window_tokens=32768 ...

The model loop (s15) runs on the host side in a child process (adapter/s15_child.py); this class answers its
tool requests against the task container through Harbor's environment API (exec, upload_file) and finishes the
trial: SWE tasks get `logs_dir/model.patch` (git diff against the starting commit, as Scale's
host_mini_swe.py does), GAIA gets /app/answer.txt if the model answered in text only. Agent kwargs (--ak) are
strings; see __init__ for the list.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import shlex
import signal
import sys
import tempfile
import uuid
from pathlib import Path

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

HERE = Path(__file__).resolve().parent
CHILD = HERE / "s15_child.py"
VENDOR = Path(os.environ.get("S15_VENDOR") or HERE.parent / "vendor" / "s15")

SWE = ("swe", "swepro")
BASH_TIMEOUT = {"swe": 600, "swepro": 600, "tbench": 1800, "gaia": 300}
SWE_SUFFIX = ("\n\nThe repository is checked out at {cwd}. Resolve the issue above by editing the repository's "
              "source files. You may write and run scripts and tests to reproduce the problem and check your "
              "fix. Do not commit. When you are done, reply with a short summary of the change.")
CHARS_PER_TOKEN = 3.2       # code-heavy prompts; s15's own estimate (4) would overflow the window first


def _bool(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _read_env_file(path: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path or not Path(path).is_file():
        return out
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip().removeprefix("export ").strip()] = value.strip().strip('"').strip("'")
    return out


class S15Agent(BaseAgent):

    def __init__(self, *args, base_url: str | None = None, served_model: str | None = None,
                 bench: str = "swe", max_lead_calls: str | int = 150, out_cap: str | int = 0,
                 bash_timeout: str | int | None = None, window_tokens: str | int = 0,
                 max_tokens_reserve: str | int = 16000, context_limit: str | int = 0,
                 reactive_limit: str | int = 10, deadline_s: str | int = 0, call_timeout: str | int = 1800,
                 python: str | None = None, log_requests: str | bool = True, extra_body: str | None = None,
                 env_file: str | None = None, max_tokens: str | int = 0, max_tokens_escalated: str | int = 0,
                 epic_arm: str | None = None, epic_seed_key: str | None = None,
                 **kwargs):
        super().__init__(*args, **kwargs)
        self.base_url = base_url or os.environ.get("S15_BASE_URL", "http://127.0.0.1:8000")
        self.served_model = served_model or self.model_name or os.environ.get("S15_MODEL", "")
        if self.served_model and "/" in self.served_model and self.served_model.split("/", 1)[0] in (
                "hosted_vllm", "openai", "anthropic"):
            self.served_model = self.served_model.split("/", 1)[1]
        self.bench = bench
        self.max_lead_calls = int(max_lead_calls)
        self.out_cap = int(out_cap)
        self.bash_timeout = int(bash_timeout or BASH_TIMEOUT.get(bench, 600))
        window = int(window_tokens)
        reserve = int(max_tokens_reserve)
        self.context_limit = int(context_limit) or (int((window - reserve) * CHARS_PER_TOKEN) if window else 0)
        self.reactive_limit = int(reactive_limit)
        self.deadline_s = int(deadline_s)
        self.call_timeout = int(call_timeout)
        self.python = python or sys.executable
        self.log_requests = _bool(log_requests)
        # Harbor decodes JSON-looking --ak values itself, so extra_body may already be a dict
        self.extra_body = extra_body if isinstance(extra_body, dict) else (json.loads(extra_body) if extra_body else None)
        # EPIC round: the arm this trial's lead calls are tagged with, and the block key its seeds derive from
        # (the arms of one block share it: <phase>|<task>, so their per-call seeds are common random numbers)
        self.epic_arm = str(epic_arm) if epic_arm else None
        self.epic_seed_key = str(epic_seed_key) if epic_seed_key else None
        self.secrets = _read_env_file(env_file or os.environ.get("S15_ENV_FILE"))
        self.max_tokens = int(max_tokens)
        self.max_tokens_escalated = int(max_tokens_escalated)

    def _epic_tag(self) -> dict | None:
        """{arm, spec, seed_key} for the child's lead calls, or None outside the EPIC round. spec = the trial
        name (unique per trial and arm); seed_key = <block key>|<task>, the task read from the trial config."""
        if not self.epic_arm:
            return None
        trial_dir = self.logs_dir.parent
        task = trial_dir.name.rsplit("__", 1)[0]
        cfg = trial_dir / "config.json"
        if cfg.is_file():
            try:
                task = Path(json.loads(cfg.read_text())["task"]["path"]).name
            except (KeyError, TypeError, ValueError):
                pass
        return {"arm": self.epic_arm, "spec": trial_dir.name,
                "seed_key": f"{self.epic_seed_key or 'block'}|{task}"}

    @staticmethod
    def name() -> str:
        return "s15"

    def version(self) -> str | None:
        snap = VENDOR / "SNAPSHOT.json"
        if snap.is_file():
            files = json.loads(snap.read_text()).get("files", {})
            return files.get("s15_integrated_harness/code.py", "")[:12] or None
        return None

    async def setup(self, environment: BaseEnvironment) -> None:
        return None

    async def _detect_cwd(self, environment: BaseEnvironment) -> str:
        if self.bench in SWE:
            cmd = "cd /testbed 2>/dev/null || cd /app 2>/dev/null || cd /; pwd"
        else:
            cmd = "pwd"
        r = await environment.exec(command=cmd, timeout_sec=60)
        lines = (r.stdout or "").strip().splitlines()
        return lines[-1].strip() if lines else "/"

    def _prompt(self, instruction: str, cwd: str) -> str:
        if self.bench == "swe":
            return instruction.rstrip() + SWE_SUFFIX.format(cwd=cwd)
        return instruction

    async def _handle(self, req: dict, environment: BaseEnvironment, writer: asyncio.StreamWriter,
                      lock: asyncio.Lock, tmpdir: Path) -> None:
        reply: dict = {"id": req.get("id")}
        try:
            if req.get("op") == "exec":
                try:
                    r = await environment.exec(command=req["cmd"], cwd=req.get("cwd") or None,
                                               timeout_sec=int(req.get("timeout") or 600))
                    reply.update(stdout=(r.stdout or "") + (r.stderr or ""), rc=r.return_code)
                except RuntimeError as exc:
                    if "timed out" in str(exc).lower():
                        reply["timeout"] = True
                    else:
                        raise
            elif req.get("op") == "upload":
                tmp = tmpdir / uuid.uuid4().hex
                tmp.write_bytes(base64.b64decode(req["data_b64"]))
                try:
                    await environment.upload_file(tmp, req["path"])
                finally:
                    tmp.unlink(missing_ok=True)
                reply["ok"] = True
            else:
                reply["error"] = f"unknown op {req.get('op')!r}"
        except Exception as exc:                                # noqa: BLE001
            reply["error"] = f"{type(exc).__name__}: {exc}"[:4000]
        data = (json.dumps(reply) + "\n").encode()
        async with lock:
            writer.write(data)
            await writer.drain()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                     environment: BaseEnvironment, tmpdir: Path) -> None:
        lock = asyncio.Lock()
        tasks: set[asyncio.Task] = set()
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                task = asyncio.create_task(self._handle(json.loads(line), environment, writer, lock, tmpdir))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        finally:
            for task in list(tasks):
                task.cancel()
            writer.close()

    @staticmethod
    async def _kill(proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        try:
            proc.send_signal(signal.SIGTERM)
            await asyncio.wait_for(proc.wait(), timeout=15)
        except (ProcessLookupError, asyncio.TimeoutError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        staging = (self.logs_dir / "s15").resolve()
        staging.mkdir(parents=True, exist_ok=True)
        tmpdir = staging / "tmp"
        tmpdir.mkdir(exist_ok=True)
        cwd = await self._detect_cwd(environment)
        base = None
        if self.bench in SWE:
            r = await environment.exec(command="git rev-parse HEAD", cwd=cwd, timeout_sec=60)
            base = (r.stdout or "").strip().splitlines()[-1] if r.return_code == 0 and r.stdout else None
        prompt = self._prompt(instruction, cwd)
        sock = os.path.join(tempfile.gettempdir(), f"s15rpc-{uuid.uuid4().hex[:12]}.sock")
        # one JSON line carries a whole file (base64): lift asyncio's 64 KiB line limit
        server = await asyncio.start_unix_server(
            lambda r, w: self._serve(r, w, environment, tmpdir), path=sock, limit=256 * 1024 * 1024)
        cfg = {"staging": str(staging), "cwd": cwd, "prompt": prompt, "bench": self.bench,
               "model_id": self.served_model, "base_url": self.base_url, "rpc_path": sock,
               "max_lead_calls": self.max_lead_calls, "out_cap": self.out_cap,
               "bash_timeout": self.bash_timeout, "context_limit": self.context_limit,
               "reactive_limit": self.reactive_limit, "deadline_s": self.deadline_s,
               "call_timeout": self.call_timeout, "web": self.bench == "gaia",
               "log_requests": self.log_requests, "extra_body": self.extra_body,
               "max_tokens": self.max_tokens, "max_tokens_escalated": self.max_tokens_escalated,
               "epic": self._epic_tag()}
        (staging / "config.json").write_text(json.dumps(cfg, indent=1))
        (staging / "instruction.md").write_text(instruction)
        env = dict(os.environ)
        env.update(self.secrets)
        env["S15_VENDOR"] = str(VENDOR)
        env["PYTHONUNBUFFERED"] = "1"
        log = open(staging / "child.log", "wb")
        proc = await asyncio.create_subprocess_exec(
            self.python, str(CHILD), str(staging / "config.json"),
            stdout=log, stderr=asyncio.subprocess.STDOUT, cwd=str(staging), env=env,
            start_new_session=True)
        try:
            await proc.wait()
        except asyncio.CancelledError:
            await self._kill(proc)
            raise
        finally:
            log.close()
            server.close()
            try:
                os.unlink(sock)
            except OSError:
                pass
        result = {}
        if (staging / "result.json").is_file():
            result = json.loads((staging / "result.json").read_text())
        fin = await self._finalize(environment, cwd, base, result)
        (staging / "finalize.json").write_text(json.dumps(fin, indent=1))
        counter = result.get("counter") or {}
        context.n_input_tokens = counter.get("in_tokens")
        context.n_output_tokens = counter.get("out_tokens")
        context.n_cache_tokens = counter.get("cache_read_tokens")
        context.metadata = {"s15_status": result.get("status"), "stop_reason": result.get("stop_reason"),
                            "lead_calls": counter.get("lead"), "compactions": result.get("compactions"),
                            "wall_s": result.get("wall_s"), "cwd": cwd, **fin}

    async def _finalize(self, environment: BaseEnvironment, cwd: str, base: str | None, result: dict) -> dict:
        fin: dict = {}
        if self.bench in SWE:
            ref = shlex.quote(base) if base else "HEAD"
            r = await environment.exec(
                command=f"cd {shlex.quote(cwd)} && git add -A && git diff --cached --binary {ref}",
                timeout_sec=300)
            patch = r.stdout or ""
            (self.logs_dir / "model.patch").write_text(patch)
            await environment.exec(command=f"cd {shlex.quote(cwd)} && git reset -q", timeout_sec=120)
            fin.update(patch_bytes=len(patch), patch_sha=hashlib.sha256(patch.encode()).hexdigest()[:16],
                       base_commit=base)
        elif self.bench == "gaia":
            r = await environment.exec(command="test -s /app/answer.txt && cat /app/answer.txt", timeout_sec=60)
            if r.return_code == 0 and (r.stdout or "").strip():
                fin.update(answer_source="file", answer=(r.stdout or "").strip()[:500])
            else:
                answer = extract_answer(result.get("final_text") or "")
                fin.update(answer_source="final_text" if answer else "none", answer=answer)
                if answer:
                    await environment.exec(
                        command=f"mkdir -p /app && printf %s {shlex.quote(answer)} > /app/answer.txt",
                        timeout_sec=60)
        return fin


def extract_answer(text: str) -> str:
    """GAIA fallback when the model answered in text and did not write /app/answer.txt: the value after the
    last 'FINAL ANSWER:' (GAIA's own convention), else the last non-empty line."""
    text = (text or "").strip()
    if not text:
        return ""
    found = re.findall(r"FINAL ANSWER\s*[:：]\s*(.+)", text, flags=re.I)
    if found:
        return found[-1].strip().strip("`*").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1].strip("`*").strip()[:300] if lines else ""
