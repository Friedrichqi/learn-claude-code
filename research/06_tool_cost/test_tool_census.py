"""Unit tests for the Part C measurement definitions (research/06_tool_cost/tool_census.py) and the
--tool-delay spec of research/common/profile_run.py, on synthetic traces."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1])); import _paths  # noqa: E401,E402,F401
import profile_run as pr                                                     # noqa: E402
import tool_census as tc                                                     # noqa: E402


def ev(event, t, span=None, parent=None, agent="agent-root", kind="lead", data=None, thread="MainThread",
       parent_agent=None):
    return {"event": event, "elapsed_ms": t, "monotonic_ns": int(t * 1e6), "span_id": span, "parent_span_id": parent,
            "agent_id": agent, "parent_agent_id": parent_agent, "agent_kind": kind, "thread": {"name": thread},
            "data": data or {}}


def write(tmp_path, records, name="run_20260930T000000_000000Z_test0001.jsonl"):
    path = tmp_path / name
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def solo_trace():
    """One user request: a tool round (a bash call with a 100 ms human permission wait inside a 300 ms span,
    handler 150 ms), then a final answer.  Wall 10 s."""
    return [
        ev("run_start", 0, data={"cwd": "/tmp/yqi10/lane", "model": "Qwen/Qwen3-8B"}),
        ev("profile_meta", 0.1, data={"label": "FQA-solo-r1", "stream": False}),
        ev("agent_active_start", 1000, span="a1", data={"reason": "user"}),
        ev("model_request", 1000, span="m1", data={"purpose": "lead"}),
        ev("model_response", 3000, span="m1", data={"purpose": "lead", "requested_actions": [{"type": "tool_use"}]}),
        ev("tool_start", 3000, span="t1", data={"tool": "bash", "arguments": {"command": "grep -n x f.py"}}),
        ev("permission_wait_start", 3010, span="w1", parent="t1"),
        ev("permission_wait_end", 3110, span="w1", parent="t1"),
        ev("tool_execution_start", 3120, span="x1", parent="t1"),
        ev("tool_execution_end", 3270, span="x1", parent="t1"),
        ev("tool_end", 3300, span="t1", data={"tool": "bash", "status": "ok", "result": {"characters": 10}}),
        ev("model_request", 3300, span="m2", data={"purpose": "lead"}),
        ev("model_response", 6000, span="m2", data={"purpose": "lead", "requested_actions": [{"type": "text"}]}),
        ev("agent_active_end", 6500, span="a1"),
        ev("profile_end", 10000, data={"status": "completed", "wall_seconds": 10.0}),
    ]


def test_intervals():
    assert tc.union_len([(0, 2), (1, 3), (5, 6)]) == 4
    assert tc.union_len([(0, 10)], 2, 4) == 2
    # tool 0-10, model 2-4 and 3-6 -> blocked 0-2 and 6-10
    assert tc.blocked_len([(0, 10)], [(2, 4), (3, 6)], 0, 10) == 6
    assert tc.blocked_len([(0, 1)], [], 0, 10) == 1
    assert tc.blocked_len([], [(0, 1)], 0, 10) == 0


def test_solo_call_run_and_request(tmp_path):
    res = tc._parse("x/y", write(tmp_path, solo_trace()))
    (call,) = res["calls"]
    assert call["span_ms"] == 300 and call["wait_ms"] == 100 and call["net_ms"] == 200
    assert call["handler_ms"] == 150 and call["human_wait"] and call["bash_class"] == "grep"
    run = res["summary"]
    assert run["wall_ms"] == 10000 and run["tool_ms"] == 200 and run["host"] == "cluster-local"
    assert run["blocked_ms"] == 200 and run["model_ms_main"] == 4700
    (req,) = res["requests"]
    assert req["turn_ms"] == 5500                    # 1000 -> 6500
    assert req["ftr_req_ms"] == 2300                 # final call's request start
    assert req["ftr_end_ms"] == 5000
    assert req["tools_ftr_req_ms"] == 200            # net tool time before the final call
    assert req["ftr_stream_ms"] is None              # not a streamed run


def test_team_overlap_is_not_blocked(tmp_path):
    """A teammate tool that runs while the lead's model call is in flight is not on the critical path."""
    records = solo_trace()[:-1] + [
        ev("agent_create", 3400, agent="agent-tm", kind="teammate", data={"name": "tm"}, parent_agent="agent-root"),
        ev("tool_start", 3500, span="t2", agent="agent-tm", kind="teammate", data={"tool": "read_file", "arguments": {"path": "a"}}),
        ev("tool_end", 3900, span="t2", agent="agent-tm", kind="teammate", data={"tool": "read_file", "status": "ok"}),
        ev("profile_end", 10000, data={"status": "completed", "wall_seconds": 10.0}),
    ]
    run = tc._parse("x/y", write(tmp_path, records))["summary"]
    assert run["tool_ms"] == 600                     # 200 (lead, net) + 400 (teammate)
    assert run["blocked_ms"] == 200                  # the teammate's read overlaps model call m2


def test_task_span_splits_model_time(tmp_path):
    records = [
        ev("run_start", 0, data={"cwd": "/mnt/home/yqi10/lanes/x", "model": "glm-5.3-flash"}),
        ev("tool_start", 100, span="t1", data={"tool": "task", "arguments": {"description": "d"}}),
        ev("agent_create", 101, agent="agent-task-1", kind="one_shot", parent_agent="agent-root"),
        ev("model_request", 110, span="m1", agent="agent-task-1", kind="one_shot", data={"purpose": "one_shot"}),
        ev("model_response", 1110, span="m1", agent="agent-task-1", kind="one_shot", data={"purpose": "one_shot"}),
        ev("tool_start", 1110, span="t2", parent="t1", agent="agent-task-1", kind="one_shot", data={"tool": "read_file"}),
        ev("tool_end", 1160, span="t2", parent="t1", agent="agent-task-1", kind="one_shot", data={"tool": "read_file", "status": "ok"}),
        ev("model_request", 1160, span="m2", agent="agent-task-1", kind="one_shot", data={"purpose": "one_shot"}),
        ev("model_response", 2160, span="m2", agent="agent-task-1", kind="one_shot", data={"purpose": "one_shot"}),
        ev("tool_end", 2200, span="t1", data={"tool": "task", "status": "ok"}),
    ]
    res = tc._parse("x/y", write(tmp_path, records))
    task = next(c for c in res["calls"] if c["tool"] == "task")
    assert task["span_ms"] == 2100 and task["model_ms"] == 2000 and task["nested_tool_ms"] == 50
    run = res["summary"]
    assert run["tool_ms_plain"] == 50                # the subagent's read, not the task span
    assert run["host"] == "cluster-nfs" and run["model"] == "glm-hosted"


def test_probe_traces_are_skipped_unless_allowed(tmp_path):
    path = write(tmp_path, solo_trace()[:2] + [ev("probe_meta", 1, data={"label": "p"})])
    assert tc._parse("x/y", path).get("probe")
    assert "calls" in tc._parse("x/y", path, allow_probe=True)


def test_artifact_flags():
    assert tc.bash_class("sleep 30") == "sleep"
    assert tc.bash_class("python3 -c \"import time; time.sleep(5)\"") == "sleep"
    assert tc.bash_class("cd sandbox && python3 test_x.py") == "python-test"
    assert tc.bash_class("python3 -m pytest -q tests") == "pytest"
    assert tc.bash_class("curl -s https://example.org") == "network"
    assert tc.FIND_ROOT.search("find / -name x")
    assert not tc.FIND_ROOT.search("find ./src -name x")


def test_tool_delay_spec():
    rules = pr.parse_tool_delay("bash,read_file=fixed:0.29; *=lognormal:6:1")
    assert rules[0] == (frozenset({"bash", "read_file"}), "fixed", (0.29,))
    assert rules[1] == (None, "lognormal", (6.0, 1.0))
    a = pr.draw_tool_delay("lognormal", (6.0, 1.0), 7, "agent-root", 3)
    assert a == pr.draw_tool_delay("lognormal", (6.0, 1.0), 7, "agent-root", 3)     # keyed, not stateful
    assert a != pr.draw_tool_delay("lognormal", (6.0, 1.0), 7, "agent-root", 4)
    assert pr.draw_tool_delay("fixed", (1.09,), 7, None, 1) == 1.09
    for bad in ("bash", "*=uniform:1", "*=fixed:-1", "*=lognormal:6"):
        try:
            pr.parse_tool_delay(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")


def test_tool_delay_sleeps_inside_the_execution_span():
    calls = []

    class Trace:
        def current_agent_id(self):
            return "agent-root"

        def emit(self, event, data):
            calls.append((event, data))

    class Mod:
        TRACE = Trace()

        @staticmethod
        def call_tool_handler(handler, args, name):
            return handler(**args)

    mod = Mod()
    pr.install_tool_delay(mod, "read_file=fixed:0.01", 1)
    import time
    t0 = time.perf_counter()
    assert mod.call_tool_handler(lambda path: f"read {path}", {"path": "a"}, "read_file") == "read a"
    assert time.perf_counter() - t0 >= 0.01
    assert calls and calls[0][0] == "tool_delay" and calls[0][1]["delay_s"] == 0.01
    assert mod.call_tool_handler(lambda: "ok", {}, "glob") == "ok"            # no rule: untouched
    assert len(calls) == 1
