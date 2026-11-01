#!/usr/bin/env python3
"""Counterfactual replay of research/20_round_dependency (GPU): does a recorded round's next action survive when the
previous round's results are still pending?

Sample (`sample`):
  - topic 19: 60 complete counted sessions from w08/w16/w32_none, 6 per codebase, all four turns ended normally;
  - topic 16: one e4r0 `prefix` trial per task (the append-only template, exact cache) with at most 100 lead rounds,
    20 SWE-bench Verified, 30 Pro, 30 Terminal-Bench; a task whose e4r0 trial is longer takes its e4r1 trial.
  The pilot (`--pilot`) draws disjoint units: topic 19's pilot cells (p08/p16_none) and topic 16's e4r1 trials.

Variants for round j of a turn, each sampled S times (temperature 1.0, top_p 0.95, top_k 40: the model's
generation_config, which the live runs used because the harness sends no sampling parameters; a seed per round and
variant):
  C  control: the recorded request of L_j;
  P  pending: that request with every result of round j-1 replaced by s15's own background placeholder,
     "[Background task bg_NNNN started] Result will arrive as a task_notification." (code.py start_background_task);
  M  merged world: L_k's request, then a_k with the tool calls of rounds k+1..j-1 appended (their thinking dropped, so
     nothing from a pending result reaches the context through it), then a placeholder for every call of rounds
     k..j-1, then the messages L_j had after round j-1's results (the todo reminder). k is the start of the chain
     that holds so far; M_{k+1} is P_{k+1}.
A sample matches round j when it reproduces at least half of c_j's action keys at the target level (read_file:
path; edit_file: path + first line of old_text; write_file: path; glob: pattern; bash: program + paths, patterns,
identifiers and line anchors). p = matches / S per variant. Round j joins the chain when p_P (or p_M) >= TAU * p_C
and p_C >= 2 / S; a final answer, a rewrite of the recent history (tail_ok false) or the turn's end closes it.
Order-free comes from the controls at no extra cost (does c_j appear among an earlier round's C samples?).
With --aa (the pilot) every round also gets A, a second control with its own seeds: the A/A floor that sets TAU.

Each prompt is first sent once with max_tokens 1, so its S samples (n=S) share one cached prefill. Prompts are
rendered on CPU by ef_render (vLLM 0.31's own Anthropic path) with each source's append-only template and sent as
token ids to /v1/completions; the control's length must equal the recorded prompt tokens.

    python research/20_round_dependency/rd_cf.py sample [--pilot]
    python research/20_round_dependency/rd_cf.py check --sample pilot          # CPU: renders, M=P, parse round trip
    python research/20_round_dependency/rd_cf.py run --url URL --sample pilot --out pilot [--S 8] [--tau 0.5]
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import lzma
import random
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import rd_rounds as rr  # noqa: E402
import rd_static as rs  # noqa: E402

CF = rr.DATA / "cf"
MODEL = "MiniMaxAI/MiniMax-M2.7"
MAX_MODEL_LEN = 131072
MAX_TOKENS = 8192
SAMPLING = {"temperature": 1.0, "top_p": 0.95, "top_k": 40}
S_DEFAULT, TAU_DEFAULT = 8, 0.5
PLACEHOLDER = "[Background task {bg} started] Result will arrive as a task_notification."
MAX_ROUNDS = 100
T19_MAIN_CELLS, T19_PILOT_CELLS = ("w08_none", "w16_none", "w32_none"), ("p08_none", "p16_none")
T16_MAIN = {"swe": 20, "swepro": 30, "tbench": 30}
PILOT_ROUNDS = 150                                   # per source


# -- the sample -----------------------------------------------------------------------------------------------------
def complete_t19(s: dict) -> bool:
    turns = s["turns"]
    return (not s["filler"] and s["status"] == "ok" and len(turns) == 4
            and all(t["normal"] and t["compactions"] == 0 for t in turns.values()))


def draw(pilot: bool, seed: int = 20) -> dict:
    rng = random.Random(seed)
    units = []
    sess19, _ = rr.read_jsonl(rr.ROUNDS / "sessions_t19.jsonl.xz"), None
    cells = T19_PILOT_CELLS if pilot else T19_MAIN_CELLS
    pool = [s for s in sess19 if s["group"] in cells and complete_t19(s) and s["rounds"] <= 4 * MAX_ROUNDS]
    if pilot:
        rng.shuffle(pool)
        n = 0
        for s in pool:
            if n >= PILOT_ROUNDS:
                break
            units.append(s)
            n += s["rounds"]
    else:
        by_cb = defaultdict(list)
        for s in pool:
            by_cb[s["codebase"]].append(s)
        for cb in sorted(by_cb):
            rng.shuffle(by_cb[cb])
            units += by_cb[cb][:6]
    sess16 = rr.read_jsonl(rr.ROUNDS / "sessions_t16.jsonl.xz")
    prefix = [s for s in sess16 if s["job"] == "minimax-m2.7-epic+prefix"]
    by_task = defaultdict(dict)
    for s in prefix:
        rep = s["phase"][:4]                                  # e4r0 / e4r1 / ...
        by_task[(s["bench"], s["task"])][rep] = s
    picked16 = []
    for bench in rr.T16_BENCHES:
        tasks = sorted(t for (b, t) in by_task if b == bench and ("e4r0" in by_task[(b, t)] or "e4r1" in by_task[(b, t)]))
        rng.shuffle(tasks)
        chosen = []
        for t in tasks:
            reps = by_task[(bench, t)]
            order = ("e4r1",) if pilot else ("e4r0", "e4r1")
            s = next((reps[r] for r in order if r in reps and 0 < reps[r]["rounds"] <= MAX_ROUNDS), None)
            if s is not None:
                chosen.append(s)
        if pilot:
            n = 0
            for s in chosen:
                if n >= PILOT_ROUNDS / 3:
                    break
                picked16.append(s)
                n += s["rounds"]
        else:
            picked16 += chosen[:T16_MAIN[bench]]
    units += picked16
    return {"pilot": pilot, "seed": seed, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "units": [{"src": s["src"], "sid": s["sid"], "group": s["group"], "rounds": s["rounds"],
                       "codebase": s.get("codebase"), "bench": s.get("bench"), "task": s.get("task")} for s in units]}


def sample_path(name: str) -> Path:
    return CF / f"sample_{name}.json"


def load_sample(name: str) -> tuple[dict, dict[str, dict], dict[str, list[dict]]]:
    """(sample, sessions by sid, rows by sid) for the sample's units."""
    sample = json.loads(sample_path(name).read_text())
    sids = {u["sid"] for u in sample["units"]}
    sessions, rows = {}, defaultdict(list)
    for src in ("t19", "t16"):
        if not any(u["src"] == src for u in sample["units"]):
            continue
        for s in rr.read_jsonl(rr.ROUNDS / f"sessions_{src}.jsonl.xz"):
            if s["sid"] in sids:
                sessions[s["sid"]] = s
        for r in rr.read_jsonl(rr.ROUNDS / f"rounds_{src}.jsonl.xz"):
            if r["sid"] in sids:
                rows[r["sid"]].append(r)
    for v in rows.values():
        v.sort(key=lambda r: r["call"])
    return sample, sessions, dict(rows)


def session_cwd(sess: dict) -> str | None:
    if sess["src"] != "t16":
        return None
    try:
        return json.loads((rr.REPO / Path(sess["requests"]).parent / "config.json").read_text()).get("cwd")
    except (OSError, ValueError):
        return None


# -- requests -------------------------------------------------------------------------------------------------------
def base_request(row: dict) -> dict:
    req = rr.request(row["ptr"])
    return {"system": req.get("system"), "tools": req.get("tools"), "messages": req.get("messages") or []}


def results_index(messages: list[dict], ids: set[str]) -> int | None:
    """Index of the user message that carries the tool results with these ids."""
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m.get("role") == "user" and not isinstance(m.get("content"), str):
            got = {b.get("tool_use_id") for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"}
            if ids & got:
                return i
    return None


def placeholder_results(calls: list[dict]) -> list[dict]:
    return [{"type": "tool_result", "tool_use_id": c["id"], "content": PLACEHOLDER.format(bg=f"bg_{i + 1:04d}")}
            for i, c in enumerate(calls)]


def control_request(row: dict) -> dict:
    return base_request(row)


def pending_request(row: dict, prev: dict) -> dict | None:
    """L_j with round j-1's results replaced by background placeholders; None when L_j does not carry them."""
    req = copy.deepcopy(base_request(row))
    ids = [c["id"] for c in prev["calls"]]
    i = results_index(req["messages"], set(ids))
    if i is None:
        return None
    holders = {b["tool_use_id"]: b for b in placeholder_results(prev["calls"])}
    content = []
    for b in req["messages"][i]["content"]:
        if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in holders:
            content.append(holders.pop(b["tool_use_id"]))
        else:
            content.append(b)
    if holders:                                      # some of round j-1's results were not in L_j
        return None
    req["messages"][i] = {"role": "user", "content": content}
    return req


def merged_request(group: list[dict], row: dict) -> dict | None:
    """M_j: L_k's request + a_k with rounds k+1..j-1's tool calls appended + placeholders for all + L_j's tail."""
    k_row, prev = group[0], group[-1]
    base = copy.deepcopy(base_request(k_row))
    nxt = base_request(group[1]) if len(group) > 1 else base_request(row)   # the request right after L_k
    k_ids = {c["id"] for c in k_row["calls"]}
    a_k = next((m for m in nxt["messages"] if m.get("role") == "assistant" and not isinstance(m.get("content"), str)
                and k_ids & {b.get("id") for b in m["content"] if isinstance(b, dict)}), None)
    if a_k is None:
        return None
    a_k = copy.deepcopy(a_k)
    extra = [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["input"]}
             for r in group[1:] for c in r["calls"]]
    a_k["content"] = list(a_k["content"]) + extra
    calls = [c for r in group for c in r["calls"]]
    lj = base_request(row)
    i = results_index(lj["messages"], {c["id"] for c in prev["calls"]})
    if i is None:
        return None
    tail = lj["messages"][i + 1:]
    base["messages"] = base["messages"] + [a_k, {"role": "user", "content": placeholder_results(calls)}] + tail
    return base


# -- rendering (CPU, in worker processes) -----------------------------------------------------------------------------
_TPL: dict[str, str] = {}


def _init_worker():
    import ef_render
    ef_render._setup()


def render_ids(args) -> list[int]:
    req, template_rel = args
    import ef_render
    if template_rel not in _TPL:
        _TPL[template_rel] = (rr.REPO / template_rel).read_text()
    return ef_render.tokens(ef_render.render(req, gen=True, template=_TPL[template_rel]))


def render_text(args) -> str:
    req, template_rel, gen = args
    import ef_render
    if template_rel not in _TPL:
        _TPL[template_rel] = (rr.REPO / template_rel).read_text()
    return ef_render.render(req, gen=gen, template=_TPL[template_rel])


# -- parsing and action keys ------------------------------------------------------------------------------------------
_INVOKE = re.compile(r"<invoke name=[\"']?([^\"'>]+)[\"']?>(.*?)(?:</invoke>|$)", re.S)
_TOOLCALL = re.compile(r"<minimax:tool_call>(.*?)(?:</minimax:tool_call>|$)", re.S)


def parse_generation(text: str) -> dict:
    """A completion after the generation prompt ('...]~b]ai\\n<think>\\n'): reasoning, visible text, tool calls."""
    from vllm.parser.minimax_m2 import _PARAM_RE
    text = text.replace("[e~[", "")
    think, sep, rest = text.partition("</think>")
    if not sep:
        return {"think": think, "visible": "", "calls": [], "closed": False}
    calls = []
    for block in _TOOLCALL.findall(rest):
        for name, body in _INVOKE.findall(block):
            args = {}
            for m in _PARAM_RE.finditer(body):
                key = (m.group("dq_name") or m.group("sq_name") or m.group("bare_name") or "").strip()
                if key:
                    args[key] = m.group("value")
            calls.append({"name": name.strip(), "input": args})
    visible = _TOOLCALL.sub("", rest).strip()
    return {"think": think, "visible": visible, "calls": calls, "closed": True}


def first_line(text) -> str:
    for line in str(text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def bash_program(command: str) -> str:
    for seg in re.split(r"&&|\|\||;|\|", command):
        words = [w for w in rs.shell_tokens(seg) if not re.match(r"^[A-Za-z_]\w*=", w)]
        if words and words[0] not in ("cd", "export", "source"):
            return words[0].split("/")[-1]
    return ""


def norm_value(v) -> str:
    """One argument as text: the template writes non-strings as JSON and strings verbatim, so a parsed '[...]' or
    '{...}' and a recorded list are the same value."""
    if isinstance(v, str) and v[:1] in "[{":
        try:
            v = json.loads(v)
        except ValueError:
            return v
    return v if isinstance(v, str) else json.dumps(v, sort_keys=True, ensure_ascii=False)


def keys_of(call: dict, cwd: str | None, level: str) -> tuple:
    """An action key: 'exact' (all arguments), 'target' (the primary level) or 'loose' (the object only)."""
    name, inp = call.get("name"), call.get("input") or {}
    if level == "exact":
        return (name, json.dumps({k: norm_value(v) for k, v in inp.items()}, sort_keys=True, ensure_ascii=False))
    if name == "todo_write":
        return ("todo",)
    if name in ("read_file", "write_file") and isinstance(inp.get("path"), str):
        return (name, rs.norm_path(inp["path"], cwd))
    if name == "edit_file" and isinstance(inp.get("path"), str):
        p = rs.norm_path(inp["path"], cwd)
        return (name, p) if level == "loose" else (name, p, first_line(inp.get("old_text")))
    if name == "glob" and isinstance(inp.get("pattern"), str):
        return (name, inp["pattern"].strip())
    if name == "bash" and isinstance(inp.get("command"), str):
        sal = rs.salient({"name": "bash", "input": {"command": inp["command"]}})
        parts = sorted({(k, rs.norm_path(s, cwd) if k == "path" else s) for k, s in sal
                        if level == "target" or k == "path"})
        return (name, bash_program(inp["command"]), tuple(parts))
    return (name, json.dumps({k: norm_value(v) for k, v in inp.items()}, sort_keys=True, ensure_ascii=False))


def key_set(calls: list[dict], cwd: str | None, level: str) -> set:
    return {keys_of(c, cwd, level) for c in calls} if calls else {("final",)}


def matches(sample_keys: set, recorded_keys: set) -> bool:
    return len(sample_keys & recorded_keys) * 2 >= len(recorded_keys)


# -- the GPU run ------------------------------------------------------------------------------------------------------
def seed_of(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


class Runner:
    def __init__(self, url: str, out: Path, S: int, tau: float, inflight: int, renderers: int, aa: bool = False):
        self.url, self.out, self.S, self.tau, self.aa = url.rstrip("/"), out, S, tau, aa
        self.sem = asyncio.Semaphore(inflight)
        self.pool = ProcessPoolExecutor(renderers, initializer=_init_worker)
        out.mkdir(parents=True, exist_ok=True)
        self.records_path = out / "records.jsonl"
        self.texts = open(out / "texts.jsonl", "a", encoding="utf-8")
        self.done = {}
        for r in rr.read_jsonl(self.records_path):
            self.done[(r["sid"], r["turn"], r["j"], r["variant"], r.get("k"))] = r
        self.records = open(self.records_path, "a", encoding="utf-8")
        self.session = None
        self.stats = Counter()

    async def post(self, body: dict, timeout: float = 3600) -> dict:
        import aiohttp
        for attempt in range(4):
            try:
                async with self.session.post(f"{self.url}/v1/completions", json=body,
                                             timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                    data = await resp.json(content_type=None)
                    if resp.status == 200:
                        return data
                    err = f"HTTP {resp.status}: {str(data)[:300]}"
            except Exception as exc:                  # a dropped connection: retry
                err = f"{type(exc).__name__}: {exc}"
            self.stats["retries"] += 1
            await asyncio.sleep(5 * (attempt + 1))
        raise RuntimeError(err)

    async def variant(self, sess: dict, row: dict, variant: str, req: dict | None, rec_keys: dict, cwd,
                      pending_keys: set, k: int | None = None) -> dict:
        key = (sess["sid"], row["turn"], row["j"], variant, k)
        if key in self.done:
            return self.done[key]
        rec = {"sid": sess["sid"], "src": sess["src"], "turn": row["turn"], "j": row["j"], "call": row["call"],
               "variant": variant, "k": k, "S": self.S}
        if req is None:
            rec.update(status="unbuildable")
            return self._write(key, rec)
        loop = asyncio.get_running_loop()
        ids = await loop.run_in_executor(self.pool, render_ids, (req, sess["template"]))
        rec.update(prompt_tokens=len(ids))
        if variant == "C":
            rec.update(recorded_np=row["np"], render_ok=len(ids) == row["np"])
        max_tokens = min(MAX_TOKENS, MAX_MODEL_LEN - len(ids) - 16)
        if max_tokens < 64:
            rec.update(status="too_long")
            return self._write(key, rec)
        seed = seed_of(sess["sid"], row["turn"], row["j"], variant, k)
        async with self.sem:
            t0 = time.time()
            warm = await self.post({"model": MODEL, "prompt": ids, "max_tokens": 1, "temperature": 0.0})
            t1 = time.time()
            data = await self.post({"model": MODEL, "prompt": ids, "n": self.S, "max_tokens": max_tokens,
                                    "seed": seed, "skip_special_tokens": False, **SAMPLING})
            t2 = time.time()
        rec.update(seed=seed, t_warm=round(t1 - t0, 3), t_sample=round(t2 - t1, 3),
                   cached_warm=((warm.get("usage") or {}).get("prompt_tokens_details") or {}).get("cached_tokens"),
                   cached_sample=((data.get("usage") or {}).get("prompt_tokens_details") or {}).get("cached_tokens"),
                   out_tokens=(data.get("usage") or {}).get("completion_tokens"))
        samples = []
        for i, ch in enumerate(sorted(data.get("choices") or [], key=lambda c: c.get("index", 0))):
            g = parse_generation(ch.get("text") or "")
            ks = {lv: key_set(g["calls"], cwd, lv) for lv in ("target", "exact", "loose")}
            same = {lv: matches(ks[lv], rec_keys[lv]) for lv in ks}
            truncated = ch.get("finish_reason") == "length"
            if same["target"]:
                outcome = "same"
            elif truncated:
                outcome = "truncated"
            elif not g["calls"]:
                outcome = "no_call"
            elif ks["target"] & pending_keys:
                outcome = "reissue_pending"
            else:
                outcome = "other_tool"
            samples.append({"i": i, "finish": ch.get("finish_reason"), "think_chars": len(g["think"]),
                            "n_calls": len(g["calls"]), "tools": [c["name"] for c in g["calls"]],
                            "keys": [list(map(str, x)) for x in sorted(ks["target"], key=str)],
                            "same": same, "outcome": outcome})
            self.texts.write(json.dumps({"sid": sess["sid"], "turn": row["turn"], "j": row["j"], "variant": variant,
                                         "k": k, "i": i, "text": ch.get("text")}, ensure_ascii=False) + "\n")
        n = max(1, len(samples))
        rec.update(status="ok", samples=samples,
                   p={lv: sum(s["same"][lv] for s in samples) / n for lv in ("target", "exact", "loose")},
                   outcomes=dict(Counter(s["outcome"] for s in samples)))
        self.stats[variant] += 1
        return self._write(key, rec)

    def _write(self, key, rec: dict) -> dict:
        self.done[key] = rec
        self.records.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.records.flush()
        return rec

    def passes(self, test: dict, control: dict) -> bool | None:
        if test.get("status") != "ok" or control.get("status") != "ok":
            return None
        pc, pt = control["p"]["target"], test["p"]["target"]
        if pc < 2 / self.S:
            return None                               # the recorded action is not reproducible: undetermined
        return pt >= self.tau * pc

    async def turn(self, sess: dict, rows: list[dict], cwd) -> None:
        rows = sorted(rows, key=lambda r: r["j"])
        rk = [{lv: key_set(r["calls"], cwd, lv) for lv in ("target", "exact", "loose")} for r in rows]
        k = 0
        chain = []
        for j, row in enumerate(rows):
            prev = rows[j - 1] if j > 0 else None
            pend = rk[j - 1]["target"] if prev is not None else set()
            tasks = [self.variant(sess, row, "C", control_request(row), rk[j], cwd, pend)]
            if self.aa:                                  # a second control with its own seeds: the A/A floor
                tasks.append(self.variant(sess, row, "A", control_request(row), rk[j], cwd, pend))
            if prev is not None and not row["final"]:
                tasks.append(self.variant(sess, row, "P", pending_request(row, prev), rk[j], cwd, pend))
            res = await asyncio.gather(*tasks)
            c_rec = res[0]
            if prev is None or row["final"]:
                chain.append({"j": j, "start": True, "why": "first" if prev is None else "final"})
                k = j
                continue
            if not prev.get("tail_ok"):
                chain.append({"j": j, "start": True, "why": "rewrite"})
                k = j
                continue
            if j == k + 1:
                test = res[-1]
            else:
                group = rows[k:j]
                gkeys = set().union(*(rk[i]["target"] for i in range(k, j)))
                test = await self.variant(sess, row, "M", merged_request(group, row), rk[j], cwd, gkeys, k=k)
            ok = self.passes(test, c_rec)
            if ok:
                chain.append({"j": j, "start": False, "k": k})
            else:
                chain.append({"j": j, "start": True, "why": "dependent" if ok is False else "undetermined"})
                k = j
        self._write((sess["sid"], rows[0]["turn"], -1, "chain", None),
                    {"sid": sess["sid"], "src": sess["src"], "turn": rows[0]["turn"], "j": -1, "variant": "chain",
                     "k": None, "tau": self.tau, "S": self.S, "chain": chain})

    async def run(self, sessions: dict, rows: dict, turns_inflight: int) -> None:
        import aiohttp
        conn = aiohttp.TCPConnector(limit=0)
        self.session = aiohttp.ClientSession(connector=conn)
        gate = asyncio.Semaphore(turns_inflight)
        t0 = time.time()

        async def one(sess, turn_rows, cwd):
            async with gate:
                await self.turn(sess, turn_rows, cwd)

        work = []
        for sid, rs_ in rows.items():
            sess = sessions[sid]
            cwd = session_cwd(sess)
            by_turn = defaultdict(list)
            for r in rs_:
                by_turn[r["turn"]].append(r)
            for t in sorted(by_turn):
                work.append((len(by_turn[t]), sid, t, sess, by_turn[t], cwd))
        work.sort(key=lambda w: (-w[0], w[1], w[2]))         # the longest turns first: no long tail at the end
        try:
            await asyncio.gather(*(one(sess, trs, cwd) for _, _, _, sess, trs, cwd in work))
        finally:
            await self.session.close()
            self.texts.close()
            self.records.close()
            self.pool.shutdown()
        print(f"[cf] done in {time.time() - t0:.0f} s: {dict(self.stats)}", flush=True)


# -- CPU checks -------------------------------------------------------------------------------------------------------
def check(name: str, workers: int, limit: int) -> None:
    """Before any GPU time: the control renders match the recorded prompt tokens (V1), M_{k+1} renders exactly as
    P_{k+1}, and the parser recovers the recorded tool calls from the rendered responses (round trip)."""
    sample, sessions, rows = load_sample(name)
    rng = random.Random(1)
    pairs = [(sessions[sid], r) for sid, rs_ in rows.items() for r in rs_]
    rng.shuffle(pairs)
    pairs = pairs[:limit] if limit else pairs
    with ProcessPoolExecutor(workers, initializer=_init_worker) as pool:
        ids = list(pool.map(render_ids, [(control_request(r), s["template"]) for s, r in pairs], chunksize=4))
    ok = [len(i) == r["np"] for i, (s, r) in zip(ids, pairs)]
    by_src = defaultdict(list)
    for (s, r), o in zip(pairs, ok):
        by_src[s["src"]].append(o)
    print("V1 control render == recorded prompt tokens:",
          {k: f"{sum(v)}/{len(v)}" for k, v in by_src.items()}, flush=True)
    bad = [(s["sid"], r["call"], len(i), r["np"]) for i, (s, r), o in zip(ids, pairs, ok) if not o][:10]
    if bad:
        print("  mismatches (sid, call, rendered, recorded):", bad)
    # M_{k+1} == P_{k+1}, and the parse round trip on rendered responses
    eq = rt = rt_ok = 0
    tried = 0
    with ProcessPoolExecutor(workers, initializer=_init_worker) as pool:
        jobs = []
        for sid, rs_ in rows.items():
            for a, b in zip(rs_, rs_[1:]):
                if a["turn"] == b["turn"] and a["calls"] and a.get("append_ok") and not b["final"]:
                    jobs.append((sessions[sid], a, b))
        rng.shuffle(jobs)
        jobs = jobs[:200]
        for sess, a, b in jobs:
            p = pending_request(b, a)
            m = merged_request([a], b)
            tried += 1
            eq += p is not None and m is not None and json.dumps(p, sort_keys=True) == json.dumps(m, sort_keys=True)
        texts = list(pool.map(render_text, [(control_request(a), s["template"], True) for s, a, b in jobs]
                              + [(control_request(b), s["template"], False) for s, a, b in jobs], chunksize=4))
        half = len(jobs)
        for (sess, a, b), ta, tb in zip(jobs, texts[:half], texts[half:]):
            if not tb.startswith(ta):
                continue
            gen = tb[len(ta):].split("[e~[")[0]
            g = parse_generation(gen)
            cwd = session_cwd(sess)
            rt += 1
            rt_ok += key_set(g["calls"], cwd, "exact") == key_set(a["calls"], cwd, "exact")
    print(f"M_(k+1) == P_(k+1): {eq}/{tried}; parse round trip (exact keys): {rt_ok}/{rt} of {len(jobs)} pairs")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--pilot", action="store_true")
    s.add_argument("--seed", type=int, default=20)
    c = sub.add_parser("check")
    c.add_argument("--sample", default="pilot")
    c.add_argument("--workers", type=int, default=32)
    c.add_argument("--limit", type=int, default=0)
    r = sub.add_parser("run")
    r.add_argument("--url", required=True)
    r.add_argument("--sample", default="pilot")
    r.add_argument("--out", required=True, help="folder name under data/cf/")
    r.add_argument("--S", type=int, default=S_DEFAULT)
    r.add_argument("--tau", type=float, default=TAU_DEFAULT)
    r.add_argument("--inflight", type=int, default=8, help="sampling requests in flight")
    r.add_argument("--turns", type=int, default=24, help="turns in flight")
    r.add_argument("--renderers", type=int, default=24)
    r.add_argument("--only", default="", help="comma-separated sids (smoke)")
    r.add_argument("--max-rounds", type=int, default=0, help="first N rounds of each turn (smoke)")
    r.add_argument("--aa", action="store_true", help="also a second control per round (pilot: the A/A floor)")
    a = ap.parse_args()
    if a.cmd == "sample":
        smp = draw(a.pilot, a.seed)
        name = "pilot" if a.pilot else "main"
        CF.mkdir(parents=True, exist_ok=True)
        sample_path(name).write_text(json.dumps(smp, indent=1) + "\n")
        n = Counter(u["src"] for u in smp["units"])
        r_ = Counter()
        for u in smp["units"]:
            r_[u["src"]] += u["rounds"]
        print(f"[cf] sample {name}: units {dict(n)}, rounds {dict(r_)} -> {sample_path(name)}")
    elif a.cmd == "check":
        check(a.sample, a.workers, a.limit)
    else:
        sample, sessions, rows = load_sample(a.sample)
        if a.only:
            keep = set(a.only.split(","))
            rows = {k: v for k, v in rows.items() if k in keep}
        if a.max_rounds:
            rows = {k: [r for r in v if r["j"] < a.max_rounds] for k, v in rows.items()}
        out = CF / a.out
        out.mkdir(parents=True, exist_ok=True)
        (out / "run.json").write_text(json.dumps({**vars(a), "t_start": time.time(), "model": MODEL,
                                                  "sampling": SAMPLING, "max_tokens": MAX_TOKENS,
                                                  "placeholder": PLACEHOLDER}, indent=1) + "\n")
        runner = Runner(a.url, out, a.S, a.tau, a.inflight, a.renderers, a.aa)
        asyncio.run(runner.run(sessions, rows, a.turns))


if __name__ == "__main__":
    main()
