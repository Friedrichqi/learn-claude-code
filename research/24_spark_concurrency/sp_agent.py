"""Topic 24's Harbor agent: topic 22's WBAgent (harness_eval's S15Agent plus the 64 MiB cap on an agent command's output,
wb_cap.py), fixed to SWE tasks and running sp_child.py.

- bench: always "swe" (every task is SWE-bench Lite or Verified): the SWE prompt suffix, a 600 s bash timeout, and the
  patch step at the end (git diff into the trial's model.patch). WBAgent.run would look the task up in harness_eval's
  datasets and fail on topic 24's task folders, so SPAgent.run calls S15Agent.run directly.
- the child: sp_child.py (request ids, clock stamps, long replies kept).

Concurrency is Harbor's own: --n-concurrent-agents N takes a permit when a trial's agent phase starts and returns it
when the agent ends; -n bounds trials in any phase, so the next containers start while N agents run.

    harbor run -p data/queue/main -e docker -a sp_agent:SPAgent -k 8 -n <N+slack> --n-concurrent-agents N ...
"""
from __future__ import annotations

from pathlib import Path

import adapter.harbor_agent as ha
import wb_agent

ha.CHILD = Path(__file__).resolve().parent / "sp_child.py"


class SPAgent(wb_agent.WBAgent):

    async def run(self, instruction, environment, context) -> None:
        self.bench = "swe"
        self.bash_timeout = ha.BASH_TIMEOUT["swe"]
        await ha.S15Agent.run(self, instruction, environment, context)
