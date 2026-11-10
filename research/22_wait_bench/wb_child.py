#!/usr/bin/env python3
"""Topic 22's s15 child: harness_eval's adapter/s15_child.py, unchanged but for two things.

- Its bench permission hook denies every tool outside KEEP_TOOLS. s15 adds the pacing tools to the tool pool itself
  (code.py assemble_tool_pool: BUILTIN_TOOLS + wait_tools(), with their handlers), so the hook now also lets those
  through: `wait` when S15_WAIT_TOOL=1, nothing otherwise. KEEP_TOOLS and the "Available tools" sentence stay as they
  were; s15 announces `wait` in its own words ("Also available: wait.").
- The child's absolute start time goes to <staging>/wb_t0.json: calls.jsonl's `t` counts from it, so the analysis can
  place every model call on the wall clock.

Started by wb_agent.WBAgent with s15_child.py's arguments (the config path).
"""
import json
import os
import sys
from pathlib import Path

ADAPTER = Path(os.environ.get("WB_ADAPTER_DIR", "/opt/harness_eval/adapter"))
sys.path.insert(0, str(ADAPTER))
import s15_child as sc  # noqa: E402

_install_profile = sc.install_profile
_run_init = sc.Run.__init__


def install_profile(mod, run) -> None:
    hooks = mod.HOOKS["PreToolUse"]
    at = hooks.index(mod.permission_hook)
    _install_profile(mod, run)
    bench_permission = hooks[at]
    pacing = {tool["name"] for tool in mod.wait_tools()}

    def permission(block):
        if block.name in pacing:
            return None
        return bench_permission(block)

    hooks[at] = permission


def run_init(self, cfg: dict) -> None:
    _run_init(self, cfg)
    (self.staging / "wb_t0.json").write_text(json.dumps({"t0": self.t0, "pid": os.getpid()}))


sc.install_profile = install_profile
sc.Run.__init__ = run_init

if __name__ == "__main__":
    sc.main()
