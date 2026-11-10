"""Topic 23's Harbor agent: topic 22's WBAgent (the benchmark read from each task, the 64 MiB exec-output cap) running
et_child.py, with each task's time limit counted on active time.

The limit is the task's own agent timeout (task.toml) times `limit_mult` (2, as topics 16 and 22), counted from the
agent's start but not counting the seconds the session's requests were held at the gate proxy's session gate
(et_proxy.py, GET /gate/session/<id>). In the setups without a gate that time is 0, so the rule is topic 22's. At the
limit the child is stopped (S15Agent.run kills it on cancellation) and asyncio.TimeoutError is raised; Harbor turns
it into the AgentTimeoutError of its own timeout and still runs the verifier (Harbor 0.24 trial/trial.py:659-662,
trial/single_step.py:83-86). Harbor's own timeout (--agent-timeout-multiplier, set high by et_cell.py) stays as a
backstop. Each stop is logged to agent/s15/et_limit.json.

    harbor run ... -a et_agent:ETAgent --ak gate_url=http://127.0.0.1:<proxy port> --ak limit_mult=2 ...
"""
from __future__ import annotations

import asyncio
import json
import time
import tomllib
import urllib.request
from pathlib import Path

import adapter.harbor_agent as ha
import wb_agent

ha.CHILD = Path(__file__).resolve().parent / "et_child.py"
POLL_S = 5.0


def blocked_seconds(gate_url: str | None, session: str, last: float) -> float:
    """Seconds the session's requests have been held at the gate so far (the last value if the proxy can't say)."""
    if not gate_url:
        return 0.0
    try:
        with urllib.request.urlopen(f"{gate_url.rstrip('/')}/gate/session/{session}", timeout=5) as r:
            return float(json.loads(r.read()).get("blocked_s") or 0.0)
    except Exception:                                            # noqa: BLE001
        return last


class ETAgent(wb_agent.WBAgent):

    def __init__(self, *args, gate_url: str | None = None, limit_mult: str | float = 2, **kwargs):
        super().__init__(*args, **kwargs)
        self.gate_url = gate_url
        self.limit_mult = float(limit_mult)

    def _limit_s(self) -> float | None:
        conf = tomllib.loads((self._task_path() / "task.toml").read_text())
        timeout = (conf.get("agent") or {}).get("timeout_sec")
        return float(timeout) * self.limit_mult if timeout else None

    async def run(self, instruction, environment, context) -> None:
        limit = self._limit_s()
        session = self.logs_dir.parent.name
        inner = asyncio.create_task(super().run(instruction, environment, context))
        t0, blocked = time.monotonic(), 0.0
        try:
            while True:
                done, _ = await asyncio.wait({inner}, timeout=POLL_S)
                if done:
                    return inner.result()
                if limit is None:
                    continue
                blocked = await asyncio.to_thread(blocked_seconds, self.gate_url, session, blocked)
                active = time.monotonic() - t0 - blocked
                if active > limit:
                    inner.cancel()
                    try:
                        await inner
                    except BaseException:                       # noqa: BLE001
                        pass
                    rec = {"t": time.time(), "limit_s": limit, "wall_s": round(time.monotonic() - t0, 1),
                           "gate_blocked_s": round(blocked, 1), "active_s": round(active, 1)}
                    (self.logs_dir / "s15").mkdir(parents=True, exist_ok=True)
                    (self.logs_dir / "s15" / "et_limit.json").write_text(json.dumps(rec) + "\n")
                    raise asyncio.TimeoutError(f"active-time limit {limit:.0f} s reached "
                                               f"({blocked:.0f} s held at the gate not counted)")
        finally:
            if not inner.done():
                inner.cancel()
                try:
                    await inner
                except BaseException:                           # noqa: BLE001
                    pass
