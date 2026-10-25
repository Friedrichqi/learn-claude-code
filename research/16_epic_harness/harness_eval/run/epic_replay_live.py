#!/usr/bin/env python3
"""E-A, live: every EPIC arm replays the SAME recorded trajectories through running EPIC server(s), call after call
(plan "EPIC round", stage B; topic 16's epic_replay.py, but over the servers' HTTP API).

    /data/fritz/venv/bin/python run/epic_replay_live.py run --urls URL[,URL...] --model qwen3.8-27b-epic \
        --baseline qwen3.8-27b --bench swe --n 20 --arms prefix,prefix_aa,shift,epic128,epic128a1 --tag ea1
    /data/fritz/venv/bin/python run/epic_replay_live.py report --tag ea1 --logs runs/epic/<key>.calls.jsonl[,...]

- Trajectories: the lead calls of `--n` baseline runs that snipped, one run per task (tasks in subset order, the
  first valid trial by start time). Each call's prompt is rendered by the server itself (/tokenize, after vLLM's
  own Anthropic-to-chat conversion of the recorded request), i.e. token for token what the server rendered in the
  baseline; the rendered length is checked against the recorded prompt tokens.
- Each arm keeps its own EPIC session per trajectory, so staleness compounds exactly as live. Replies are generated
  (`--max-tokens`, greedy) but the next prompt carries the recorded reply (teacher forcing): all arms see identical
  prompts, and a generated reply's rows are never reused.
- All arms of a trajectory run on the same server (trajectory i on URL i mod n), at the same time.
- Per call and arm: the first token's top-20 log-probabilities. The report gives first-token KL and top-1 agreement
  against `prefix` on the same prompt (prefix_aa against prefix is the numerical noise floor), by calls since the
  first snip, plus the servers' shadow probes of these sessions (EPIC_LOG).
"""
from __future__ import annotations

import argparse
import json
import math
import statistics as st
import sys
import threading
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path("/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval")
sys.path.insert(0, str(ROOT / "report"))
SNIP = " messages archived at "
SINCE_BINS = ((0, 0), (1, 5), (6, 20), (21, 10 ** 6))      # calls since the first snip (0 = before any snip)


def post(url: str, body: dict, timeout: int = 1800) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get(url: str, timeout: int = 120) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def load_jsonl(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(errors="replace").splitlines() if path.is_file() else []:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def select_trajectories(baseline: str, bench: str, n: int) -> list[dict]:
    import collect
    order = {t: i for i, t in enumerate(json.loads((ROOT / "run" / "subsets.json").read_text())[bench]["tasks"])}
    rows = [r for r in collect.load_trials() if r["model"] == baseline and r["bench"] == bench
            and r["phase"].startswith("q") and not r.get("infra") and r.get("finished")
            and (r.get("compactions") or {}).get("snip_compact", 0) > 0]
    rows.sort(key=lambda r: (order.get(r["task"], 10 ** 6), r["task"], r.get("started") or ""))
    out, seen = [], set()
    for r in rows:
        if r["task"] in seen:
            continue
        seen.add(r["task"])
        out.append({"task": r["task"], "trial": r["trial"], "reward": r.get("reward"),
                    "dir": str(ROOT / "jobs" / baseline / bench / r["job"] / r["trial"])})
        if len(out) >= n:
            break
    return out


def lead_calls(tdir: str) -> list[dict]:
    calls = [c for c in load_jsonl(Path(tdir) / "agent" / "s15" / "calls.jsonl")
             if c.get("purpose") == "lead" and c.get("request") and c.get("response") is not None]
    return calls


_TOOLS: dict[str, dict] = {}


def builtin_tools() -> dict[str, dict]:
    """The s15 tool schemas by name: calls.jsonl records only the names, and the lead call sends the vendored
    module's BUILTIN_TOOLS (a literal) filtered to them, in that order (adapter/s15_child.py install_profile)."""
    if not _TOOLS:
        import ast
        src = (ROOT / "vendor" / "s15" / "s15_integrated_harness" / "code.py").read_text()
        for node in ast.parse(src).body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "BUILTIN_TOOLS"
                                                    for t in node.targets):
                _TOOLS.update({t["name"]: t for t in ast.literal_eval(node.value)})
    return _TOOLS


def render(url: str, model: str, call: dict, merge: bool) -> list[int]:
    from vllm.entrypoints.anthropic.protocol import AnthropicMessagesRequest
    from vllm.entrypoints.anthropic.serving import AnthropicServingMessages as ASM
    r = call["request"]
    tools = [builtin_tools()[t] if isinstance(t, str) else t for t in r.get("tools") or []]
    areq = AnthropicMessagesRequest(model=model, max_tokens=int(call.get("max_tokens") or 1024),
                                    system=r.get("system"), messages=r["messages"], tools=tools or None)
    oai = ASM.to_chat_completion_request(areq, merge_inline_system=merge)
    body = {"model": model, "messages": oai.messages, "add_generation_prompt": True}
    if oai.tools:
        body["tools"] = [t.model_dump(exclude_none=True) if hasattr(t, "model_dump") else t for t in oai.tools]
    if oai.chat_template_kwargs:
        body["chat_template_kwargs"] = oai.chat_template_kwargs
    return post(f"{url}/tokenize", body)["tokens"]


def recorded_prompt_tokens(call: dict) -> int | None:
    u = call.get("usage") or {}
    if u.get("input_tokens") is None:
        return None
    return int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0) + \
        int(u.get("cache_creation_input_tokens") or 0)


def cmd_run(a) -> None:
    urls = a.urls.split(",")
    arms = a.arms.split(",")
    trajs = select_trajectories(a.baseline, a.bench, a.n)
    out_path = ROOT / "runs" / "epic" / f"replay_{a.tag}.jsonl"
    done = {(x["traj"], x["arm"]) for x in load_jsonl(out_path) if x.get("ev") == "end"}
    lock = threading.Lock()
    tmpl = get(f"{urls[0]}/v1/models")                         # the servers answer (and name the model)
    from vllm.entrypoints.anthropic.serving import AnthropicServingMessages as ASM
    from transformers import AutoTokenizer
    import glob as _glob
    snap = sorted(_glob.glob(f"/data/fritz/hf/hub/models--{a.hf.replace('/', '--')}/snapshots/*/"))[0]
    merge = ASM._detect_merge_inline_system(AutoTokenizer.from_pretrained(snap).chat_template)

    def write(obj: dict) -> None:
        with lock, open(out_path, "a") as f:
            f.write(json.dumps(obj) + "\n")

    write({"ev": "start", "tag": a.tag, "t": time.time(), "urls": urls, "arms": arms, "model": a.model,
           "trajectories": trajs, "max_tokens": a.max_tokens, "models": tmpl.get("data", [{}])[0].get("id")})
    prompts: dict[int, list[dict]] = {}

    def prepare(i: int) -> None:
        url = urls[i % len(urls)]
        calls = lead_calls(trajs[i]["dir"])[: a.max_calls or None]
        rows, first_snip = [], None
        for k, c in enumerate(calls):
            ids = render(url, a.model, c, merge)
            msgs = c["request"]["messages"]
            snipped = any(isinstance(m.get("content"), str) and SNIP in m["content"] for m in msgs[:5])
            if snipped and first_snip is None:
                first_snip = k
            rows.append({"ids": ids, "rec": recorded_prompt_tokens(c), "snipped": snipped,
                         "since": 0 if first_snip is None else k - first_snip + 1})
        prompts[i] = rows
        match = sum(1 for r in rows if r["rec"] == len(r["ids"]))
        write({"ev": "prepared", "traj": i, "task": trajs[i]["task"], "calls": len(rows),
               "render_match": match, "render_checked": sum(1 for r in rows if r["rec"] is not None),
               "first_snip": first_snip})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(prepare, range(len(trajs))))

    def session(i: int, arm: str) -> None:
        if (i, arm) in done:
            return
        url = urls[i % len(urls)]
        spec = f"rep-{a.tag}-{i:02d}-{arm}"
        for k, row in enumerate(prompts[i]):
            body = {"model": a.model, "prompt": row["ids"], "max_tokens": a.max_tokens, "temperature": 0.0,
                    "logprobs": 20, "return_token_ids": True,
                    "kv_transfer_params": {"epic": {"arm": arm, "spec": spec, "call": k,
                                                    "seed_key": f"rep|{a.tag}|{i}"}}}
            t0 = time.time()
            try:
                out = post(f"{url}/v1/completions", body)
            except Exception as exc:                                # noqa: BLE001
                write({"ev": "error", "traj": i, "arm": arm, "k": k, "error": repr(exc)[:300]})
                break
            ch = out["choices"][0]
            tops = ((ch.get("logprobs") or {}).get("top_logprobs") or [{}])[0] or {}
            write({"ev": "call", "traj": i, "arm": arm, "k": k, "P": len(row["ids"]), "snipped": row["snipped"],
                   "since": row["since"], "top": {t: float(v) for t, v in tops.items()},
                   "gen": (ch.get("token_ids") or [])[:a.max_tokens], "ms": round(1000 * (time.time() - t0))})
        try:
            get(f"{url}/epic/end?spec={spec}")
        except Exception:                                           # noqa: BLE001
            pass
        write({"ev": "end", "traj": i, "arm": arm})

    jobs = [(i, arm) for i in range(len(trajs)) for arm in arms]
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        list(pool.map(lambda x: session(*x), jobs))
    write({"ev": "finish", "t": time.time()})


def kl(p: dict, q: dict) -> float:
    if not p or not q:
        return float("nan")
    floor = min(q.values())
    return sum(math.exp(lp) * (lp - q.get(t, floor)) for t, lp in p.items())


def cmd_report(a) -> None:
    recs = load_jsonl(ROOT / "runs" / "epic" / f"replay_{a.tag}.jsonl")
    calls = {(x["traj"], x["arm"], x["k"]): x for x in recs if x.get("ev") == "call"}
    prep = [x for x in recs if x.get("ev") == "prepared"]
    arms = sorted({x[1] for x in calls})
    lines = [f"### E-A replay `{a.tag}`: {len(prep)} trajectories, arms {', '.join(arms)}", "",
             f"Rendered prompts equal to the recorded prompt length: "
             f"{sum(p['render_match'] for p in prep)} of {sum(p['render_checked'] for p in prep)} calls.", "",
             "First-token distribution against `prefix` on the identical prompt (top-20 KL; top-1 = same argmax):", "",
             "| arm | calls | KL mean | KL p90 | KL max | top-1 agree | " +
             " | ".join(f"KL since snip {lo}-{hi if hi < 10 ** 6 else '+'}" for lo, hi in SINCE_BINS) + " |",
             "|---|---|---|---|---|---|" + "---|" * len(SINCE_BINS)]
    for arm in arms:
        if arm == "prefix":
            continue
        ks, agree, by = [], [], defaultdict(list)
        for (i, arm2, k), x in calls.items():
            ref = calls.get((i, "prefix", k))
            if arm2 != arm or ref is None or not x["top"] or not ref["top"]:
                continue
            v = kl(ref["top"], x["top"])
            if math.isnan(v):
                continue
            ks.append(v)
            agree.append(max(x["top"], key=x["top"].get) == max(ref["top"], key=ref["top"].get))
            for lo, hi in SINCE_BINS:
                if lo <= x["since"] <= hi:
                    by[(lo, hi)].append(v)
        if not ks:
            continue
        ks_sorted = sorted(ks)
        cells = " | ".join(f"{st.mean(by[b]):.4f} (n={len(by[b])})" if by[b] else "—" for b in SINCE_BINS)
        lines.append(f"| {arm} | {len(ks)} | {st.mean(ks):.4f} | {ks_sorted[int(.9 * (len(ks) - 1))]:.4f} | "
                     f"{ks_sorted[-1]:.3f} | {st.mean(agree):.1%} | {cells} |")
    lines.append("")
    if a.logs:
        import epic_report
        specs_by_arm: dict[str, set[str]] = defaultdict(set)
        for (i, arm, _k) in calls:
            specs_by_arm[arm].add(f"rep-{a.tag}-{i:02d}-{arm}")
        # every instance's log: each holds the sessions of the trajectories it served
        merged = epic_report.server_metrics([Path(lp) for lp in a.logs.split(",")], specs_by_arm) or {}
        if merged:
            lines += ["Server side (shadow probes of the replay sessions):", "",
                      "| arm | calls | computed / prompt | probes | first-token KL | reply KL | reply top-1 |",
                      "|---|---|---|---|---|---|---|"]
            for arm in arms:
                m = merged.get(arm) or {}
                tf = ("invalid", "invalid") if a.tf_invalid else (epic_report.fmt(m.get("tf_kl"), nd=4),
                                                                  epic_report.fmt(m.get("tf_top1"), pct=True))
                lines.append(f"| {arm} | {int(m.get('calls', 0))} | {epic_report.fmt(m.get('computed_share'), pct=True)} | "
                             f"{m.get('probes', 0)} | {epic_report.fmt(m.get('first_kl'), nd=4)} | {tf[0]} | {tf[1]} |")
            if a.tf_invalid:
                lines.append("")
                lines.append(f"Reply KL and top-1 are not shown: {a.tf_invalid}")
            lines.append("")
    text = "\n".join(lines) + "\n"
    (ROOT / "report" / f"replay_{a.tag}.md").write_text(text)
    print(text)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--urls", required=True)
    r.add_argument("--model", required=True)
    r.add_argument("--hf", default="Qwen/Qwen3.8-27B-FP8")
    r.add_argument("--baseline", required=True)
    r.add_argument("--bench", default="swe")
    r.add_argument("--n", type=int, default=20)
    r.add_argument("--arms", default="prefix,prefix_aa,shift,epic128,epic128a1")
    r.add_argument("--max-tokens", type=int, default=16)
    r.add_argument("--max-calls", type=int, default=0)
    r.add_argument("--workers", type=int, default=24)
    r.add_argument("--tag", required=True)
    p = sub.add_parser("report")
    p.add_argument("--tag", required=True)
    p.add_argument("--logs", default="")
    p.add_argument("--tf-invalid", default="", help="reason the teacher-forced reply metrics are not shown")
    a = ap.parse_args()
    cmd_run(a) if a.cmd == "run" else cmd_report(a)


if __name__ == "__main__":
    main()
