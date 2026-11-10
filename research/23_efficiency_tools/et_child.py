#!/usr/bin/env python3
"""Topic 23's s15 child: harness_eval's adapter/s15_child.py, unchanged but for four things.

- Its bench permission hook denies every tool outside KEEP_TOOLS. s15 adds the pacing tools to the lead's tool pool
  itself (code.py assemble_tool_pool: BUILTIN_TOOLS + wait_tools(lead=True)), so the hook also lets those through:
  `wait` and `save_context` when their S15_* switches are on, nothing otherwise (as topic 22's wb_child.py did for
  `wait`).
- Every model request carries X-Session-ID: the trial's name, so the gate proxy (et_proxy.py) can tell the sessions
  apart. S15_SESSION_ID is set to the same name before s15 loads.
- The child's absolute start time goes to <staging>/wb_t0.json (as wb_child.py): calls.jsonl's `t` counts from it,
  so the analysis can place every model call on the wall clock. The first full tool list goes to
  <staging>/tools.json.
- Nothing else: the budgets, the call log and the timeouts are s15_child.py's.

Started by et_agent.ETAgent with s15_child.py's arguments (the config path).
"""
import json
import os
import sys
from pathlib import Path

ADAPTER = Path(os.environ.get("WB_ADAPTER_DIR", "/opt/harness_eval/adapter"))
sys.path.insert(0, str(ADAPTER))
import s15_child as sc  # noqa: E402

_install_profile = sc.install_profile
_install_client_patch = sc.install_client_patch
_run_init = sc.Run.__init__


def install_profile(mod, run) -> None:
    hooks = mod.HOOKS["PreToolUse"]
    at = hooks.index(mod.permission_hook)
    _install_profile(mod, run)
    bench_permission = hooks[at]
    pacing = {tool["name"] for tool in mod.wait_tools(lead=True)}

    def permission(block):
        if block.name in pacing:
            return None
        return bench_permission(block)

    hooks[at] = permission


def install_client_patch(run) -> None:
    """s15_child's patch (budgets, call log, timeout), then the session header around it. The call log keeps only the
    tools' names, so the full tool list of the first call that has one goes to <staging>/tools.json (the prompt screens
    replay recorded calls with it)."""
    _install_client_patch(run)
    from anthropic.resources.messages import messages as sdk_messages
    inner = sdk_messages.Messages.create
    sid = os.environ.get("S15_SESSION_ID", "")
    tools_file = run.staging / "tools.json"

    def create(self, *args, **kwargs):
        headers = dict(kwargs.get("extra_headers") or {})
        headers.setdefault("X-Session-ID", sid)
        kwargs["extra_headers"] = headers
        if kwargs.get("tools") and not tools_file.exists():
            try:
                tools_file.write_text(json.dumps(sc.jsonable(kwargs["tools"])))
            except Exception:                                    # noqa: BLE001
                pass
        return inner(self, *args, **kwargs)

    sdk_messages.Messages.create = create


def run_init(self, cfg: dict) -> None:
    _run_init(self, cfg)
    (self.staging / "wb_t0.json").write_text(json.dumps({"t0": self.t0, "pid": os.getpid()}))


def session_id(config_path: str) -> str:
    """The trial's name: the config's staging folder is <trial>/agent/s15."""
    return Path(json.loads(Path(config_path).read_text())["staging"]).parents[1].name


sc.install_profile = install_profile
sc.install_client_patch = install_client_patch
sc.Run.__init__ = run_init

if __name__ == "__main__":
    os.environ["S15_SESSION_ID"] = session_id(sys.argv[1])
    sc.main()
