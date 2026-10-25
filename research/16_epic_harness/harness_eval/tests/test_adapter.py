"""CPU tests of the s15 Harbor adapter: a fake task environment (a local directory standing in for the
container) and a fake Anthropic /v1/messages server that plays a scripted model.

    .venv/bin/python -m pytest -q tests/test_adapter.py
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "adapter"))

from adapter.harbor_agent import S15Agent, extract_answer  # noqa: E402
from adapter.vendor_scorers.gaia_scorer import question_scorer  # noqa: E402
import web_tools  # noqa: E402


class FakeResult:
    def __init__(self, stdout, rc):
        self.stdout, self.stderr, self.return_code = stdout, None, rc


class FakeEnv:
    """Runs commands locally; the 'container' is a temp directory addressed by absolute paths."""

    def __init__(self, root: Path):
        self.root = root
        self.commands: list[str] = []

    async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
        self.commands.append(command)
        proc = await asyncio.create_subprocess_exec(
            "bash", "-c", command, cwd=cwd or str(self.root),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(f"Command timed out after {timeout_sec} seconds")
        return FakeResult(out.decode(errors="replace"), proc.returncode)

    async def upload_file(self, source_path, target_path):
        Path(target_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target_path)


class Context:
    n_input_tokens = n_output_tokens = n_cache_tokens = None
    metadata = None


class Model:
    """Scripted model. Each step is a list of content blocks, or ('overflow',) for a 400."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.requests: list[dict] = []
        self.lock = threading.Lock()

    def next(self, body):
        with self.lock:
            self.requests.append(body)
            return self.steps.pop(0) if self.steps else [{"type": "text", "text": "done"}]


def serve(model: Model):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            step = model.next(body)
            if step == ("overflow",):
                payload = json.dumps({"type": "error", "error": {"type": "invalid_request_error",
                    "message": "This model's maximum context length is 32768 tokens. However, you requested "
                               "8000 output tokens and your prompt contains at least 24769 input tokens"}}).encode()
                self.send_response(400)
            else:
                stop = "tool_use" if any(b["type"] == "tool_use" for b in step) else "end_turn"
                payload = json.dumps({"id": "msg_1", "type": "message", "role": "assistant", "model": "fake",
                                      "content": step, "stop_reason": stop, "stop_sequence": None,
                                      "usage": {"input_tokens": 100, "output_tokens": 10,
                                                "cache_read_input_tokens": 60}}).encode()
                self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def tool(name, **inp):
    tool.n = getattr(tool, "n", 0) + 1
    return {"type": "tool_use", "id": f"call_{tool.n}", "name": name, "input": inp}


def last_tool_result(request: dict) -> str:
    """Content of the newest tool_result in a request (s15 may append a todo reminder after it)."""
    for msg in reversed(request["messages"]):
        content = msg.get("content")
        if msg.get("role") == "user" and isinstance(content, list):
            for block in reversed(content):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    c = block.get("content")
                    return c if isinstance(c, str) else json.dumps(c)
    raise AssertionError("no tool_result in request")


def run_agent(tmp_path, steps, bench="swe", **ak):
    root = tmp_path / "container"
    repo = root / "testbed"
    repo.mkdir(parents=True)
    (repo / "a.py").write_text("def f():\n    return 1\n")
    (repo / "run.sh").write_text("#!/bin/sh\necho run\n")
    (repo / "run.sh").chmod(0o755)
    subprocess.run("git init -q && git add -A && git -c user.email=a@b -c user.name=t commit -qm init",
                   shell=True, cwd=repo, check=True)
    model = Model(steps)
    server = serve(model)
    logs = tmp_path / "logs"
    logs.mkdir()
    agent = S15Agent(logs_dir=logs, model_name="fake-model", bench=bench,
                     base_url=f"http://127.0.0.1:{server.server_port}", **ak)

    async def detect(env):
        return str(repo)
    agent._detect_cwd = detect
    env = FakeEnv(root)
    ctx = Context()
    asyncio.run(agent.run("Fix the bug in a.py.", env, ctx))
    server.shutdown()
    result = json.loads((logs / "s15" / "result.json").read_text())
    return agent, env, ctx, model, result, repo, logs


def test_tools_reach_the_environment_and_patch_is_extracted(tmp_path):
    steps = [
        [tool("read_file", path="a.py")],
        [tool("edit_file", path="a.py", old_text="return 1", new_text="return 2")],
        [tool("write_file", path="new/b.py", content="X = 1\n")],
        [tool("edit_file", path="run.sh", old_text="echo run", new_text="echo ran")],
        [tool("glob", pattern="**/*.py")],
        [tool("bash", command="cat a.py && pwd")],
        [tool("task", description="List the files in the repository with bash and summarize.")],
        [tool("bash", command="ls")],
        [{"type": "text", "text": "subagent summary"}],
        [{"type": "text", "text": "Fixed."}],
    ]
    agent, env, ctx, model, result, repo, logs = run_agent(tmp_path, steps)
    assert result["status"] == "completed", result
    assert (repo / "a.py").read_text() == "def f():\n    return 2\n"
    assert (repo / "new" / "b.py").read_text() == "X = 1\n"
    assert oct((repo / "run.sh").stat().st_mode & 0o777) == "0o755"      # mode kept across the upload
    # the read result the model saw is the container file
    first_tool_result = last_tool_result(model.requests[1])
    assert "return 1" in first_tool_result
    glob_result = last_tool_result(model.requests[5])
    assert "a.py" in glob_result and "new/b.py" in glob_result
    bash_result = last_tool_result(model.requests[6])
    assert "return 2" in bash_result and str(repo) in bash_result
    patch = (logs / "model.patch").read_text()
    assert "+    return 2" in patch and "new/b.py" in patch and "echo ran" in patch
    # nothing was written on the host staging dir except s15's own state
    staging = logs / "s15"
    assert not (staging / "a.py").exists() and not (staging / "new").exists()
    # system prompt: benchmark profile
    system = model.requests[0]["system"]
    assert f"Working directory: {repo}" in system
    assert "spawn_teammate" not in system and "wait for the user's confirmation" not in system
    names = [t["name"] for t in model.requests[0]["tools"]]
    assert "spawn_teammate" not in names and "create_worktree" not in names and "bash" in names
    assert ctx.n_input_tokens and ctx.metadata["s15_status"] == "completed"


def persisted_paths(request: dict) -> list[str]:
    out = []
    for msg in request["messages"]:
        content = msg.get("content")
        if msg.get("role") == "user" and isinstance(content, list):
            for block in content:
                c = block.get("content") if isinstance(block, dict) else None
                if isinstance(c, str) and c.startswith("<persisted-output>"):
                    out += [l.removeprefix("Full output: ") for l in c.splitlines() if l.startswith("Full output: ")]
    return out


def test_large_results_are_persisted_on_host_and_read_back_from_host(tmp_path):
    # five 45,000-char results in one message exceed s15's 200,000-byte tool_result_budget, so s15 persists
    # them to its own (host) tool-results dir and shows the host path; reading that path must hit the host
    big = "python3 -c \"print('y' * 45000)\""
    calls = [tool("bash", command=big) for _ in range(5)]

    class Reader(Model):
        def next(self, body):
            with self.lock:
                self.requests.append(body)
                n = len(self.requests)
            if n == 1:
                return calls
            if n == 2:
                paths = persisted_paths(body)
                assert paths, "nothing persisted"
                return [tool("read_file", path=paths[0], limit=1)]
            return [{"type": "text", "text": "ok"}]

    root = tmp_path / "container" / "testbed"
    root.mkdir(parents=True)
    subprocess.run("git init -q", shell=True, cwd=root, check=True)
    model = Reader([])
    server = serve(model)
    logs = tmp_path / "logs"
    logs.mkdir()
    agent = S15Agent(logs_dir=logs, model_name="m", bench="tbench",
                     base_url=f"http://127.0.0.1:{server.server_port}")

    async def detect(env):
        return str(root)
    agent._detect_cwd = detect
    asyncio.run(agent.run("go", FakeEnv(root.parent), Context()))
    server.shutdown()
    paths = persisted_paths(model.requests[1])
    assert all(Path(p).is_relative_to(logs / "s15") for p in paths)
    assert last_tool_result(model.requests[2]).startswith("y" * 100)


def test_overflow_triggers_reactive_compaction(tmp_path):
    steps = [[tool("bash", command="echo one")], ("overflow",), [{"type": "text", "text": "done"}]]
    agent, env, ctx, model, result, repo, logs = run_agent(tmp_path, steps)
    assert result["status"] == "completed", result
    assert result["counter"]["overflows"] == 1
    events = [json.loads(l) for l in (logs / "s15" / "events.jsonl").read_text().splitlines()]
    assert any(e["kind"] == "reactive_compact" for e in events)


def test_lead_call_budget_stops_without_retry(tmp_path):
    steps = [[tool("bash", command="echo %d" % i)] for i in range(10)]
    agent, env, ctx, model, result, repo, logs = run_agent(tmp_path, steps, max_lead_calls=3)
    assert result["status"] == "budget" and result["stop_reason"] == "lead call budget spent"
    assert len(model.requests) == 3


def test_bash_timeout(tmp_path):
    steps = [[tool("bash", command="sleep 5")], [{"type": "text", "text": "done"}]]
    agent, env, ctx, model, result, repo, logs = run_agent(tmp_path, steps, bash_timeout=1)
    seen = last_tool_result(model.requests[1])
    assert "Timeout (1s)" in seen


def test_gaia_answer_fallback_and_scorer():
    assert extract_answer("I checked.\nFINAL ANSWER: 42") == "42"
    assert extract_answer("The answer is below\nParis") == "Paris"
    assert question_scorer("1,000", "1000") and question_scorer("sea gull", "seagull")
    assert not question_scorer("Paris, France", "Paris")
    assert web_tools.blocked("https://huggingface.co/datasets/gaia-benchmark/GAIA/blob/main/x")
    assert not web_tools.blocked("https://en.wikipedia.org/wiki/Paris")


def test_large_file_write_over_rpc(tmp_path):
    big = ("x" * 99 + "\n") * 3000                  # 300 KB: > asyncio's default 64 KiB line limit as base64
    steps = [[tool("write_file", path="big.txt", content=big)], [tool("read_file", path="big.txt", limit=1)],
             [{"type": "text", "text": "done"}]]
    agent, env, ctx, model, result, repo, logs = run_agent(tmp_path, steps)
    assert (repo / "big.txt").read_text() == big
    assert last_tool_result(model.requests[2]).startswith("x" * 99)
