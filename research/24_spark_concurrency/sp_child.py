#!/usr/bin/env python3
"""Topic 24's s15 child: harness_eval's adapter/s15_child.py, unchanged but for four things.

- Every model request carries `X-Request-Id: <trial>~<n>` (n counts this child's calls from 0) and
  `X-Session-ID: <trial>`. vLLM names the request `chatcmpl-<X-Request-Id>` (vllm/entrypoints/serve/engine/serving.py:133),
  and that id is what the stat-logger plugin writes (sp_reqlog), so each server record joins its harness call.
- Each calls.jsonl record gains `rid`, `t_send` (CLOCK_MONOTONIC ns before the SDK call) and `t_log` (when s15_child
  logged the call, right after the reply or the error).
- A reply that stops at max_tokens is kept and continued, never thrown away: s15 would discard it and ask again with
  S15_MAX_TOKENS_ESCALATED (code.py:3782-3792). Here `has_escalated` always reads True, so code.py appends the reply
  and its CONTINUATION_PROMPT (at most MAX_RECOVERY_RETRIES times per turn, as before).
- `<staging>/wb_t0.json` records the child's start on both clocks: `t0` (wall, the origin of calls.jsonl's `t`) and
  `t0_mono` (ns).

Started by sp_agent.SPAgent with s15_child.py's arguments (the config path). WB_ADAPTER_DIR points at
harness_eval/adapter (default: derived from this file).
"""
import itertools
import json
import os
import sys
import threading
import time
from pathlib import Path

ADAPTER = Path(os.environ.get("WB_ADAPTER_DIR") or
               Path(__file__).resolve().parents[1] / "16_epic_harness" / "harness_eval" / "adapter")
sys.path.insert(0, str(ADAPTER))
import s15_child as sc  # noqa: E402

_install_profile = sc.install_profile
_install_client_patch = sc.install_client_patch
_run_init = sc.Run.__init__


def install_profile(mod, run) -> None:
    _install_profile(mod, run)
    base = mod.RecoveryState

    class KeepLongReplies(base):
        @property
        def has_escalated(self):
            return True

        @has_escalated.setter
        def has_escalated(self, value):
            pass

    mod.RecoveryState = KeepLongReplies


def install_client_patch(run) -> None:
    """s15_child's patch (budgets, call log, timeout), then request ids and clock stamps around it."""
    _install_client_patch(run)
    from anthropic.resources.messages import messages as sdk_messages
    inner = sdk_messages.Messages.create
    sid = os.environ.get("S15_SESSION_ID", "")
    seq = itertools.count()
    local = threading.local()
    log_call = run.log_call

    def logged(rec):
        ctx = getattr(local, "ctx", None)
        if ctx:
            rec.update(rid=ctx["rid"], t_send=ctx["t_send"], t_log=time.monotonic_ns())
        return log_call(rec)

    run.log_call = logged

    def create(self, *args, **kwargs):
        rid = f"{sid}~{next(seq)}"
        headers = dict(kwargs.get("extra_headers") or {})
        headers["X-Session-ID"] = sid
        headers["X-Request-Id"] = rid
        kwargs["extra_headers"] = headers
        local.ctx = {"rid": rid, "t_send": time.monotonic_ns()}
        try:
            return inner(self, *args, **kwargs)
        finally:
            local.ctx = None

    sdk_messages.Messages.create = create


def run_init(self, cfg: dict) -> None:
    t0_mono = time.monotonic_ns()
    _run_init(self, cfg)
    (self.staging / "wb_t0.json").write_text(json.dumps({"t0": self.t0, "t0_mono": t0_mono, "pid": os.getpid()}))


def session_id(config_path: str) -> str:
    """The trial's name: the config's staging folder is <trial>/agent/s15."""
    return Path(json.loads(Path(config_path).read_text())["staging"]).parents[1].name


sc.install_profile = install_profile
sc.install_client_patch = install_client_patch
sc.Run.__init__ = run_init

if __name__ == "__main__":
    os.environ["S15_SESSION_ID"] = session_id(sys.argv[1])
    sc.main()
