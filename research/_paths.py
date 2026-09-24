"""sys.path shim for the research scripts.

The scripts import each other by bare module name (profile_run, input_redundancy, dag_workloads,
latency_breakdown, trace_task_dag, ...). They used to share one folder; each topic now has its own.
Module names are unique across folders, so putting research/common, every research/NN_* topic
folder and s15_integrated_harness/scripts (trace_task_dag, trace_workflow_viz) on sys.path keeps
every import working unchanged. A script activates it with two lines:

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
    import _paths

Scripts must sit exactly one level below research/ (research/<topic>/x.py): most of them compute
REPO = Path(__file__).resolve().parents[2], and profile_run.py / tool_latency_probe.py chdir there.
"""
import sys
from pathlib import Path

RESEARCH = Path(__file__).resolve().parent
REPO = RESEARCH.parent

_DIRS = [RESEARCH / "common",
         *sorted(p for p in RESEARCH.glob("[0-9][0-9]_*") if p.is_dir()),
         REPO / "s15_integrated_harness" / "scripts"]
for _d in reversed(_DIRS):
    if str(_d) not in sys.path:
        sys.path.insert(1, str(_d))
