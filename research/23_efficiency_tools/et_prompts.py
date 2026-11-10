"""Topic 23's controller prompt (agate): what the model sees every minute when it chooses K, the session gate's limit.

Four variants, screened in S2 (et_screen.py); the winner and its reasoning effort are frozen before the main runs.
  c1  plain: the job, the goal and the metrics.
  c2  c1 plus the capacity arithmetic: how many tokens the KV cache holds, how much of it the active sessions' contexts
      take, and what happens on either side of it.
  c3  c2 plus decision cues (the rule topics 11 and 18 support: keep the active contexts within the pool).
  c4  c3 plus the last 10 decisions and what followed each.
render() turns et_hw.py's /hw/summary (the last 60 s, and the previous minutes) and the gate's state into the user
message; the same text goes to the screen's snapshots and to the live controller.
"""
from __future__ import annotations

VARIANTS = ("c1", "c2", "c3", "c4")

_JOB = (
    "You manage admission to one GPU inference server (vLLM serving Qwen3.8-27B on one H100) shared by {n} coding "
    "agents. Each agent is a session with its own context, which grows as it works. A session gate lets at most K "
    "sessions send requests at a time; the others wait at the gate until an active session has been idle for 5 "
    "seconds. Once a minute you see the server's hardware metrics and choose K for the next minute by calling "
    "set_session_limit. Goal: the most agent work per hour, measured as completed requests and generated tokens per "
    "minute, without leaving sessions waiting when the GPU has room for them.")
_CAPACITY = (
    " The GPU's KV cache holds the contexts of the requests it serves; the metrics show its size in tokens and how "
    "many tokens the active sessions' contexts take. While they fit, each session's next request reuses its cached "
    "context (high prefix-cache hit rate). When they no longer fit, the server evicts contexts and must recompute "
    "them: the hit rate falls, recomputed prompt tokens rise, requests wait for KV capacity or are preempted, and "
    "every request slows down. With too few active sessions the GPU idles instead: few running requests, low SM and "
    "HBM-bandwidth utilization, and sessions waiting at the gate for no reason.")
_CUES = (
    " Lower K when the active sessions' contexts exceed about 90% of the KV cache, or when the prefix-cache hit rate "
    "is under 50% while requests are preempted or wait for KV capacity. Raise K when the active contexts plus the "
    "next waiting session's context fit in about 80% of the KV cache and sessions are waiting at the gate. Otherwise "
    "keep K. Change K by at most a factor of two per minute.")
_HISTORY = (
    " The history lists your last decisions and what the next minute showed; if a change made the work rate worse, "
    "move back.")

SYSTEM = {"c1": _JOB, "c2": _JOB + _CAPACITY, "c3": _JOB + _CAPACITY + _CUES,
          "c4": _JOB + _CAPACITY + _CUES + _HISTORY}


def tool(n: int) -> dict:
    return {"type": "function", "function": {
        "name": "set_session_limit",
        "description": (f"Set K, how many sessions may send requests to the server during the next minute (1 to {n}). "
                        "Lowering K does not interrupt active sessions; they leave the gate as they go idle."),
        "parameters": {"type": "object", "properties": {
            "k": {"type": "integer", "minimum": 1, "maximum": n, "description": "The session limit."},
            "reason": {"type": "string", "description": "One sentence: why."}},
            "required": ["k"]}}}


def _pct(x) -> str:
    return "n/a" if x is None else f"{100 * x:.0f}%"


def _num(x, fmt: str = "{:,.0f}") -> str:
    return "n/a" if x is None else fmt.format(x)


def window_lines(w: dict, gate: dict) -> list[str]:
    cap = w.get("kv_capacity_tokens")
    act = gate.get("active_context_tokens")
    nxt = gate.get("next_waiting_context_tokens")
    share = (act / cap) if cap and act is not None else None
    prompt = w.get("prompt_tok_s") or 0.0
    part = (lambda k: (w.get(k) or 0.0) / prompt if prompt else None)
    return [
        f"- gate: K = {gate.get('k')}; {gate.get('active')} sessions active ({gate.get('active_idle')} of them idle), "
        f"{gate.get('waiting')} waiting at the gate (the longest for {_num(gate.get('oldest_wait_s'))} s)",
        f"- KV cache: {_pct(w.get('kv_usage_mean'))} full on average, {_pct(w.get('kv_usage_max'))} at most, of "
        f"{_num(cap)} tokens; the active sessions' contexts total {_num(act)} tokens ({_pct(share)} of the cache); "
        f"the next waiting session's context is {_num(nxt)} tokens",
        f"- requests at the server: {_num(w.get('running_mean'), '{:.1f}')} running, "
        f"{_num(w.get('waiting_capacity_mean'), '{:.1f}')} waiting for KV capacity; "
        f"{_num(w.get('preemptions_per_min'), '{:.1f}')} preemptions per minute",
        f"- prompt tokens: {_num(prompt)} per second; {_pct(part('computed_tok_s'))} computed (new or evicted), "
        f"{_pct(part('gpu_hit_tok_s'))} prefix-cache hits on the GPU, {_pct(part('offload_hit_tok_s'))} reloaded "
        f"from host memory",
        f"- generation: {_num(w.get('gen_tok_s'))} tokens per second; inter-token latency "
        f"{_num(w.get('itl_ms'), '{:.0f}')} ms; time to first token {_num(w.get('ttft_s'), '{:.1f}')} s; queue time "
        f"{_num(w.get('queue_s'), '{:.1f}')} s",
        f"- GPU: SM {_pct(w.get('sm_util'))} busy, SM occupancy {_pct(w.get('sm_occupancy'))}, tensor cores "
        f"{_pct(w.get('tensor_util'))}, HBM bandwidth {_pct(w.get('dram_bw_util'))}, {_num(w.get('power_w'))} W",
        f"- work: {_num(w.get('requests_per_min'), '{:.1f}')} requests completed per minute",
    ]


def render(summary: dict, gate: dict, history: list[dict] | None, variant: str) -> str:
    """The controller's user message: now, the previous minutes, and (c4) the decision history."""
    now = summary.get("now") or {}
    lines = [f"Server metrics, last {summary.get('window_s', 60):.0f} s:"] + window_lines(now, gate)
    minutes = summary.get("minutes") or []
    if minutes:
        lines.append("Previous minutes (oldest first): K, requests/min, generated tokens/s, GPU prefix-cache hit rate, "
                     "KV cache use:")
        for m in minutes:
            lines.append(f"- K {m.get('k')}: {_num(m.get('requests_per_min'), '{:.1f}')} req/min, "
                         f"{_num(m.get('gen_tok_s'))} tok/s, hits {_pct(m.get('gpu_hit_rate'))}, "
                         f"KV {_pct(m.get('kv_usage_mean'))}")
    if variant == "c4" and history:
        lines.append("Your last decisions (oldest first) and the minute after each:")
        for h in history[-10:]:
            after = h.get("after") or {}
            lines.append(f"- K {h.get('old')} -> {h.get('new')} ({h.get('reason', '')[:120]}); then "
                         f"{_num(after.get('requests_per_min'), '{:.1f}')} req/min, {_num(after.get('gen_tok_s'))} "
                         f"tok/s, hits {_pct(after.get('gpu_hit_rate'))}")
    lines.append("Choose K for the next minute: call set_session_limit.")
    return "\n".join(lines)


def messages(summary: dict, gate: dict, history: list[dict] | None, variant: str, n: int) -> list[dict]:
    return [{"role": "system", "content": SYSTEM[variant].format(n=n)},
            {"role": "user", "content": render(summary, gate, history, variant)}]
