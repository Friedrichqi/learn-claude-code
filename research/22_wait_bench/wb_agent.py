"""Topic 22's Harbor agent: harness_eval's S15Agent, for one Harbor job that mixes SWE-bench Verified, SWE-bench Pro and
Terminal-Bench, running today's s15 (with the `wait` tool) through wb_child.py.

- bench: read from each trial's task (the queue links point into harness_eval's datasets), so the SWE prompt suffix,
  the bash timeout and the patch step follow the task rather than the job.
- the child: wb_child.py (s15_child.py with the pacing tools allowed). S15_VENDOR (set by wb_eval.sh) is topic 22's
  pinned copy of today's s15.
- since 2026-11-01 20:05 UTC: the child's RPC execs keep at most wb_cap.CAP bytes of output (wb_cap).

Concurrency is Harbor's own. --n-concurrent-agents N takes a permit when a trial's agent phase starts, before its
timeout and agent_execution.started_at, and returns it when the agent ends, before the verifier; -n bounds the trials in
any phase, so environments are set up while N agents run.

    harbor run -p data/queue/main -e docker -a wb_agent:WBAgent -n 48 --n-concurrent-agents 32 ...
"""
from __future__ import annotations

import contextvars
import json
from pathlib import Path

import adapter.harbor_agent as ha
from harbor.environments.base import ExecResult
from harbor.environments.docker.docker import DockerEnvironment

import wb_cap
from wb_tasks import DATASETS

ha.CHILD = Path(__file__).resolve().parent / "wb_child.py"

# since 2026-11-01 20:05 UTC: an agent's command keeps at most wb_cap.CAP bytes of output (wb_cap). (cap, log) while
# WBAgent._handle runs an RPC exec, else None, so Harbor's own execs (setup, the patch step, the verifier) are as before
_CAP = contextvars.ContextVar("wb_exec_cap", default=None)
_buffered, _streamed = DockerEnvironment._collect_buffered_output, DockerEnvironment._collect_streamed_output


async def _collect_buffered(process, *, timeout_sec, stdin_data=None):
    got = _CAP.get()
    if got is None or stdin_data is not None:
        return await _buffered(process, timeout_sec=timeout_sec, stdin_data=stdin_data)
    return await wb_cap.collect(process, timeout_sec=timeout_sec, cap=got[0], log=got[1],
                                terminate=DockerEnvironment._terminate_process, result=ExecResult)


async def _collect_streamed(process, *, timeout_sec, stdin_data=None, on_output):
    got = _CAP.get()
    if got is None or stdin_data is not None:
        return await _streamed(process, timeout_sec=timeout_sec, stdin_data=stdin_data, on_output=on_output)
    return await wb_cap.collect(process, timeout_sec=timeout_sec, cap=got[0], log=got[1], on_output=on_output,
                                terminate=DockerEnvironment._terminate_process, result=ExecResult)


DockerEnvironment._collect_buffered_output = staticmethod(_collect_buffered)
DockerEnvironment._collect_streamed_output = staticmethod(_collect_streamed)


def bench_of(task: Path) -> str:
    """The benchmark whose dataset folder holds the task (following the queue link)."""
    real = task.resolve()
    for bench, root in DATASETS.items():
        if real.parent == root.resolve():
            return bench
    raise ValueError(f"{task} is not a task of {sorted(DATASETS)}")


class WBAgent(ha.S15Agent):

    def _task_path(self) -> Path:
        cfg = json.loads((self.logs_dir.parent / "config.json").read_text())
        return Path(cfg["task"]["path"])

    async def run(self, instruction, environment, context) -> None:
        self.bench = bench_of(self._task_path())
        self.bash_timeout = ha.BASH_TIMEOUT[self.bench]
        await super().run(instruction, environment, context)

    async def _handle(self, req, environment, writer, lock, tmpdir) -> None:
        token = _CAP.set((wb_cap.CAP, self.logs_dir / "exec_cap.jsonl") if req.get("op") == "exec" else None)
        try:
            await super()._handle(req, environment, writer, lock, tmpdir)
        finally:
            _CAP.reset(token)
