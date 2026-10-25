"""GET /wt/status for research/18_harness_wait: the live server status the s15 harness reads.

Loaded into vLLM's API-server process (PYTHONPATH must contain this folder):

    vllm serve ... --middleware wt_server_ext.Middleware

Two views (query `view=`):

  stock  only what vLLM's /metrics also exposes, read from the API process's own Prometheus registry
         every WT_SAMPLE_S = 0.5 s: running, waiting (all / waiting for capacity), KV in use
         (vLLM's kv_cache_usage_perc: blocks held by live requests), and over the last 10 s the
         prompt-cache hit rate (hit / queried prompt tokens) and the preemptions;
  share  the stock fields plus the engine snapshot that wt_scheduler writes to WT_STATUS_PATH: the
         pool and, for the calling session, its working set, its cached prefix and its place among
         the mid-turn sessions (first come, first served by the start of their current turn) with the
         working sets of the sessions ranked ahead of it.

Other query fields: sid (the caller's X-Session-ID), state = line | check | hold | wait (what the
caller is doing: a session that holds or waits is still mid-turn although nothing of it is in flight,
for WT_HOLD_TTL_S = 10 s after its last request here), turn (the caller's own turn start, wall clock;
it overrides the server's inference). view=all returns the snapshot and the registry (debugging).
Every request is appended to WT_EXT_LOG (JSONL) when that is set. The middleware never touches a
request to any other path.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from urllib.parse import parse_qs

SAMPLE_S = float(os.environ.get("WT_SAMPLE_S", "0.5"))
WINDOW_S = 10.0
HOLD_TTL_S = float(os.environ.get("WT_HOLD_TTL_S", "10"))
WAIT_STATES = {"hold", "wait"}


def read_registry(registry) -> dict:
    """The stock fields from a Prometheus registry (summed over engines and label sets)."""
    out = {"running": 0.0, "waiting": 0.0, "waiting_capacity": 0.0, "waiting_deferred": 0.0,
           "kv_usage": 0.0, "prefix_q": 0.0, "prefix_h": 0.0, "preempt": 0.0}
    for family in registry.collect():
        for sample in family.samples:
            name, value = sample.name, sample.value
            if name == "vllm:num_requests_running":
                out["running"] += value
            elif name == "vllm:num_requests_waiting":
                out["waiting"] += value
            elif name == "vllm:num_requests_waiting_by_reason":
                reason = sample.labels.get("reason")
                if reason in ("capacity", "deferred"):
                    out[f"waiting_{reason}"] += value
            elif name == "vllm:kv_cache_usage_perc":
                out["kv_usage"] = max(out["kv_usage"], value)
            elif name == "vllm:prefix_cache_queries_total":
                out["prefix_q"] += value
            elif name == "vllm:prefix_cache_hits_total":
                out["prefix_h"] += value
            elif name == "vllm:num_preemptions_total":
                out["preempt"] += value
    return out


class Sampler:
    """Samples the stock fields every SAMPLE_S and keeps WINDOW_S + a margin of history."""

    def __init__(self, read=None, start: bool = True):
        self.read = read
        self.samples: deque = deque(maxlen=int((WINDOW_S + 5) / max(SAMPLE_S, 0.05)) + 4)
        self.lock = threading.Lock()
        self.errors = 0
        if start:
            threading.Thread(target=self._run, daemon=True, name="wt-sampler").start()

    def _reader(self):
        if self.read is None:
            from vllm.v1.metrics.prometheus import get_prometheus_registry
            registry = get_prometheus_registry()
            self.read = lambda: read_registry(registry)
        return self.read

    def sample(self, now: float | None = None) -> None:
        try:
            values = self._reader()()
        except Exception:
            self.errors += 1
            return
        with self.lock:
            self.samples.append((time.monotonic() if now is None else now, values))

    def _run(self) -> None:
        while True:
            self.sample()
            time.sleep(SAMPLE_S)

    def stock(self, now: float | None = None) -> dict | None:
        now = time.monotonic() if now is None else now
        with self.lock:
            if not self.samples:
                return None
            t_last, last = self.samples[-1]
            base = None
            for t, values in self.samples:            # the newest sample at least WINDOW_S old
                if t <= t_last - WINDOW_S:
                    base = (t, values)
            if base is None:
                base = self.samples[0]
        dq = last["prefix_q"] - base[1]["prefix_q"]
        dh = last["prefix_h"] - base[1]["prefix_h"]
        return {"age_ms": round((now - t_last) * 1000, 1),
                "running": int(last["running"]), "waiting": int(last["waiting"]),
                "waiting_capacity": int(last["waiting_capacity"]), "waiting_deferred": int(last["waiting_deferred"]),
                "kv_usage": round(last["kv_usage"], 4),
                "hit_rate_10s": round(dh / dq, 4) if dq > 0 else None,
                "preemptions_10s": int(last["preempt"] - base[1]["preempt"]),
                "window_s": round(t_last - base[0], 2)}


class Snapshot:
    """The engine snapshot file, re-read only when it changes."""

    def __init__(self, path: str | None):
        self.path = path
        self.mtime = None
        self.data = None

    def get(self) -> dict | None:
        if not self.path:
            return None
        try:
            mtime = os.stat(self.path).st_mtime_ns
        except OSError:
            return None
        if mtime != self.mtime:
            try:
                with open(self.path, "rb") as handle:
                    self.data = json.loads(handle.read())
                self.mtime = mtime
            except (OSError, ValueError):
                return self.data
        return self.data


class Status:
    def __init__(self, sampler: Sampler, snapshot: Snapshot, log_path: str | None = None):
        self.sampler = sampler
        self.snapshot = snapshot
        self.callers: dict[str, dict] = {}         # sid -> {state, t, turn}
        self.lock = threading.Lock()
        self.log = open(log_path, "a", encoding="utf-8", buffering=1) if log_path else None   # line-buffered
        self.requests = 0

    def note(self, sid: str | None, state: str, turn: float | None, now: float) -> None:
        if not sid:
            return
        with self.lock:
            entry = self.callers.setdefault(sid, {"state": state, "t": now, "turn": None, "n": 0})
            entry["state"], entry["t"] = state, now
            entry["n"] += 1
            if turn is not None:
                entry["turn"] = turn

    def ranking(self, snap: dict, now: float) -> list[tuple]:
        """Mid-turn sessions in line: (turn start, first seen, sid, info), first come first served."""
        line = []
        sessions = snap.get("sessions") or {}
        with self.lock:
            callers = {sid: dict(entry) for sid, entry in self.callers.items()}
        for sid, info in sessions.items():
            caller = callers.get(sid)
            holding = caller is not None and caller["state"] in WAIT_STATES and now - caller["t"] < HOLD_TTL_S
            if not (info.get("mid") or holding):
                continue
            turn = caller["turn"] if caller is not None and caller.get("turn") is not None else info.get("turn_t0")
            line.append((turn or 0.0, info.get("first") or 0.0, sid, info))
        line.sort(key=lambda row: (row[0], row[1], row[2]))
        return line

    def build(self, view: str, sid: str | None, state: str, turn: float | None) -> dict:
        now = time.monotonic()
        self.note(sid, state, turn, now)
        out = {"view": view}
        stock = self.sampler.stock(now)
        if stock is None:
            out.update(age_ms=1e9, error="no metrics sample yet")
        else:
            out.update(stock)
        if view == "stock":
            return out
        snap = self.snapshot.get()
        if snap is None:
            out.update(age_ms=1e9, error="no engine snapshot")
            return out
        snap_age = max(0.0, (time.time() - float(snap.get("wall") or 0.0)) * 1000)
        drained = not snap.get("running") and not snap.get("waiting")
        # an idle engine stops writing snapshots: its last one (written as it drained) stays valid
        out["snapshot_age_ms"] = round(snap_age, 1)
        if not drained:
            out["age_ms"] = round(max(float(out.get("age_ms") or 0.0), snap_age), 1)
        pool = snap.get("pool") or {}
        out.update(pool_tokens=pool.get("tokens"), pool_blocks=pool.get("blocks"), free_blocks=pool.get("free"))
        line = self.ranking(snap, now)
        out["mid_sessions"] = len(line)
        with self.lock:
            out["holders"] = sum(1 for e in self.callers.values()
                                 if e["state"] in WAIT_STATES and now - e["t"] < HOLD_TTL_S)
        if view == "all":
            out["snapshot"] = snap
            with self.lock:
                out["callers"] = {k: dict(v, age_s=round(now - v["t"], 3)) for k, v in self.callers.items()}
            return out
        sessions = snap.get("sessions") or {}
        info = sessions.get(sid) if sid else None
        ahead = []
        for row in line:
            if row[2] == sid:
                break
            ahead.append(row)
        else:
            ahead = line                         # not mid-turn (yet): behind every mid-turn session
        me = {"sid": sid, "known": info is not None,
              "ws": int(info.get("ws") or 0) if info else 0,
              "prefix": int(info.get("prefix") or 0) if info else 0,
              "res": int(info.get("res") or 0) if info else 0,
              "mid": any(row[2] == sid for row in line),
              "ahead_ws": int(sum(int(row[3].get("ws") or 0) for row in ahead)),
              "ahead_n": len(ahead), "rank": len(ahead) + 1}
        out["me"] = me
        return out

    def write_log(self, record: dict) -> None:
        if self.log is None:
            return
        with self.lock:
            self.log.write(json.dumps(record, separators=(",", ":")) + "\n")
            self.requests += 1


def _query(scope) -> dict:
    raw = parse_qs((scope.get("query_string") or b"").decode())
    return {k: v[0] for k, v in raw.items()}


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _send_json(send, status: int, obj) -> None:
    body = json.dumps(obj, default=str).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


STATUS: Status | None = None


def status_service() -> Status:
    global STATUS
    if STATUS is None:
        STATUS = Status(Sampler(), Snapshot(os.environ.get("WT_STATUS_PATH")), os.environ.get("WT_EXT_LOG"))
    return STATUS


class Middleware:
    def __init__(self, app):
        self.app = app
        self.status = status_service()

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path") == "/wt/status":
            q = _query(scope)
            view = q.get("view", "stock")
            if view not in ("stock", "share", "all"):
                await _send_json(send, 400, {"error": "view must be stock, share or all"})
                return
            out = self.status.build(view, q.get("sid"), q.get("state", "line"), _float(q.get("turn")))
            self.status.write_log({"wall": round(time.time(), 3), "sid": q.get("sid"), "state": q.get("state"),
                                   "view": view, "turn": _float(q.get("turn")),
                                   "out": {k: out.get(k) for k in ("age_ms", "running", "waiting", "waiting_capacity",
                                                                     "kv_usage", "hit_rate_10s", "preemptions_10s",
                                                                     "mid_sessions", "holders", "me")}})
            await _send_json(send, 200, out)
            return
        await self.app(scope, receive, send)
