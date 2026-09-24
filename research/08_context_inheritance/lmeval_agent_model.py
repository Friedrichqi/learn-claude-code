#!/usr/bin/env python3
"""The s15 agentic harness exposed to lm-evaluation-harness as a model endpoint.

    lm_eval --model s15-agent \\
            --model_args repo=/home/yq335/learn-claude-code,arm=none,trace_dir=research/08_context_inheritance/data/lmeval \\
            --tasks gsm8k --limit 5 --log_samples --output_path research/08_context_inheritance/data/lmeval/out

WHY THIS EXISTS.  lm-evaluation-harness has no agentic benchmark and no way to express one: its
`OutputType` is `loglikelihood | loglikelihood_rolling | generate_until | multiple_choice`, its
`TaskConfig` has no tool, environment or step fields, and its chat-template flags only render
few-shot examples as turns before flattening them back (`lm_eval.api.utils.multiturn_to_singleturn`).
Requests to add agentic tasks -- issues #2926 (BFCL) and #3776 (AgentThreatBench) -- are open and
unanswered.  Models, however, ARE a first-class extension point.  So the agent loop goes on our side
of the interface: lm-eval hands us a rendered task prompt, the harness runs as many rounds as it
likes, and the lead's final message goes back as the completion for the task's own `process_results`
to score.  lm-eval supplies real datasets and official graders; nothing about the scoring is ours.

WHAT THIS CAN AND CANNOT RUN.  Only `generate_until` tasks.  `loglikelihood` asks for the logprob of
a fixed continuation, which is not a thing an agentic session has -- an agent that ran forty rounds
and wrote a paragraph did not "continue" the prompt.  Both loglikelihood methods therefore raise
rather than return a plausible-looking number.

THE ANSWER CONTRACT.  lm-eval scores one string, extracted by the task's `filter_list` regex --
gsm8k wants `#### <number>`, minerva_math wants a boxed expression.  An agent finishing a long
session does not spontaneously emit those, so a short per-task instruction is appended telling it
what its last message must contain.  The contract is part of the PROMPT, so it is byte-identical
across arms: arms may differ only in the context injected before the first turn, never in what is
asked.  A contract that differed by arm would confound every comparison in the study.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from lm_eval.api.model import LM
from lm_eval.api.registry import register_model

REPO = Path(__file__).resolve().parents[2]
DRIVER = REPO / "research" / "common" / "profile_run.py"

# What the last message must contain, per task, so lm-eval's own filters can extract from it.
# Keys are matched as a prefix of the lm-eval task name, longest first.
CONTRACTS = {
    "gsm8k": "When you are done, end your final message with a line of exactly this form:\n"
             "#### <the numeric answer>",
    "minerva_math": "End your final message with the answer inside \\boxed{...}.",
    "hendrycks_math": "End your final message with the answer inside \\boxed{...}.",
    "aime": "End your final message with the answer inside \\boxed{...}.",
    "gpqa": "End your final message with a line of exactly this form:\nANSWER: <A, B, C or D>",
    "mmlu_pro": "End your final message with a line of exactly this form:\n"
                "The answer is (<letter>).",
    "humaneval": "End your final message with the complete function in a single ```python block.",
    "mbpp": "End your final message with the complete solution in a single ```python block.",
    "": "End your final message with the answer on its own line, prefixed `ANSWER:`.",
}


def contract_for(task_name: str) -> str:
    name = task_name or ""
    for key in sorted(CONTRACTS, key=len, reverse=True):
        if key and name.startswith(key):
            return CONTRACTS[key]
    return CONTRACTS[""]


@register_model("s15-agent")
class S15AgentLM(LM):
    """One lm-eval request -> one full non-interactive s15 session -> its final message."""

    def __init__(self, repo: str = str(REPO), trace_dir: str = "research/08_context_inheritance/data/lmeval",
                 arm: str = "none", evict: str = "lru", budget: str = "unlimited",
                 max_seconds: int = 600, quiet_seconds: int = 20, concurrency: int = 1,
                 rep: str = "r1", contract: str = "auto", keep_traces: bool = True,
                 model: str = "glm-5.3-flash", extra: str = "", **_: object) -> None:
        super().__init__()
        self.repo = Path(repo)
        self.trace_dir = trace_dir
        # lm-eval parses --model_args with `simple_parse_args_string`, which maps the LITERAL
        # string "none" onto Python None.  `arm=none` is the baseline arm of this whole study, so
        # it arrives as None and lands in the argv as a None element -- coerce it back.
        self.arm = "none" if arm is None else str(arm)
        self.evict = "lru" if evict is None else str(evict)
        self.budget = "unlimited" if budget is None else str(budget)
        self.max_seconds, self.quiet_seconds = int(max_seconds), int(quiet_seconds)
        self.concurrency = max(int(concurrency), 1)
        self.rep = "r1" if rep is None else str(rep)
        self.contract = "auto" if contract is None else str(contract)
        self.keep_traces = bool(keep_traces)
        # the repo .env pins MODEL_ID=glm-5.3-flashx, which this z.ai plan is not entitled to
        # (429 code 1311), and code.py:77 load_dotenv(override=True) means exporting MODEL_ID does
        # not help -- profile_run --model patches the loaded module instead
        self.model = "glm-5.3-flash" if model is None else str(model)
        self.extra = [a for a in str(extra or "").split() if a]
        self._lock = threading.Lock()
        self._n = 0

    # ---------------------------------------------------------------- the unsupported half of LM
    def loglikelihood(self, requests, **kwargs):
        raise NotImplementedError(
            "s15-agent runs an agent loop and returns what the lead finally said; there is no "
            "logprob of a fixed continuation to report.  Use generate_until tasks.")

    def loglikelihood_rolling(self, requests, **kwargs):
        raise NotImplementedError("s15-agent supports generate_until tasks only.")

    # ---------------------------------------------------------------- one session per request
    def _run_one(self, index: int, context: str, gen_kwargs: dict, task_name: str) -> str:
        label = f"{task_name or 'task'}-{self.arm}-{self.evict}-{self.budget}-{self.rep}-{index:04d}"
        label = label.replace("/", "_")
        out_dir = self.repo / self.trace_dir / (task_name or "task")
        out_dir.mkdir(parents=True, exist_ok=True)
        answer_path = out_dir / f"{label}.answer.json"
        contract = contract_for(task_name) if self.contract == "auto" else self.contract
        prompt = f"{context.rstrip()}\n\n{contract}" if contract else context

        cmd = [sys.executable, str(DRIVER), "--label", label, "--trace-output", "full",
               "--trace-dir", str(out_dir), "--max-seconds", str(self.max_seconds),
               "--quiet-seconds", str(self.quiet_seconds), "--prompt", prompt,
               "--prewarm", self.arm, "--prewarm-evict", self.evict,
               "--prewarm-budget", self.budget, "--model", self.model,
               "--answer-out", str(answer_path), *self.extra]
        if not self.keep_traces:
            cmd += ["--no-reads-log"]
        try:
            subprocess.run(cmd, cwd=self.repo, check=False, capture_output=True, text=True,
                           timeout=self.max_seconds + 180)
        except subprocess.TimeoutExpired:
            return ""
        if not answer_path.exists():
            return ""
        try:
            return json.loads(answer_path.read_text()).get("answer", "")
        except (ValueError, OSError):
            return ""

    def generate_until(self, requests, disable_tqdm: bool = False) -> list[str]:
        jobs = []
        for i, req in enumerate(requests):
            args = req.args
            context = args[0] if args else ""
            gen_kwargs = args[1] if len(args) > 1 and isinstance(args[1], dict) else {}
            jobs.append((i, context, gen_kwargs, getattr(req, "task_name", "") or ""))
        results: list[str] = [""] * len(jobs)
        # Sequential by default.  The lane convention from the earlier studies applies: one provider
        # is driven by one process at a time, and parallelism comes from separate repo copies with
        # their own .env, not from raising concurrency here.
        if self.concurrency == 1:
            for job in jobs:
                results[job[0]] = self._run_one(*job)
                self._progress(len(jobs))
        else:
            with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
                for idx, text in zip([j[0] for j in jobs],
                                     pool.map(lambda j: self._run_one(*j), jobs)):
                    results[idx] = text
                    self._progress(len(jobs))
        return results

    def _progress(self, total: int) -> None:
        with self._lock:
            self._n += 1
            print(f"[s15-agent] {self._n}/{total} sessions "
                  f"(arm={self.arm} evict={self.evict} budget={self.budget})",
                  file=sys.stderr, flush=True)
