#!/usr/bin/env python3
"""Health watch of a live EPIC server during a round: prints a line only when something needs a look, plus an
hourly heartbeat (for a Monitor).

    python3 -u run/epic_health.py --server minimax-m2.7-epic [--since EPOCH]

Alerts (each kind at most once per 10 min):
- preemptions (vLLM metric) went up;
- new fallback / error / entry_reset records in EPIC_LOG (counted since --since);
- the host pool is above 90 % of its rows (sessions would fall back to prefix when it runs out);
- the server does not answer.

It also appends a snapshot of the live counters (preemptions, KV use, pool, sessions; every 5 min the per-rank
connector counters from /epic/workers) to runs/epic/health_<server>.jsonl, so the gates can be read after the round's
runner has stopped the server (epic/pilot_gates.py falls back to the last snapshot).
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path

ROOT = Path("/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval")


def get(url: str, timeout: int = 20):
    return urllib.request.urlopen(url, timeout=timeout).read().decode()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True)
    ap.add_argument("--since", type=float, default=0.0)
    ap.add_argument("--interval", type=int, default=60)
    a = ap.parse_args()
    state_file = ROOT / "runs" / "servers" / f"{a.server}.json"
    log_path = ROOT / "runs" / "epic" / f"{a.server}.calls.jsonl"
    since = a.since or time.time()
    pos = 0
    counts = {"call": 0, "fallback": 0, "error": 0, "reset": 0}
    last_alert: dict[str, float] = {}
    reported = {"preempt": 0.0, "fallback": 0, "error": 0, "reset": 0}
    t_beat = time.time()
    t_workers = 0.0
    snap_path = ROOT / "runs" / "epic" / f"health_{a.server}.jsonl"

    def alert(kind: str, msg: str) -> None:
        if time.time() - last_alert.get(kind, 0) >= 600:
            last_alert[kind] = time.time()
            print(f"{time.strftime('%H:%M:%SZ', time.gmtime())} {kind}: {msg}", flush=True)

    while True:
        if not state_file.exists():
            print(f"{time.strftime('%H:%M:%SZ', time.gmtime())} server state file gone: {a.server} stopped", flush=True)
            return
        url = json.loads(state_file.read_text())["url"]
        if log_path.exists():
            with open(log_path) as f:
                f.seek(pos)
                for line in f:
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if r.get("t", 0) < since:
                        continue
                    if r.get("ev") == "call":
                        counts["call"] += 1
                        counts["fallback"] += bool(r.get("fallback"))
                        counts["reset"] += bool(r.get("entry_reset"))
                    elif r.get("ev") == "error":
                        counts["error"] += 1
                pos = f.tell()
        for k in ("fallback", "error", "reset"):
            if counts[k] > reported[k]:
                alert(k, f"{counts[k]} {k}s in {counts['call']} calls since the round started "
                         f"(+{counts[k] - reported[k]})")
                reported[k] = counts[k]
        try:
            metrics = get(f"{url}/metrics")
            preempt = sum(float(l.split()[-1]) for l in metrics.splitlines()
                          if l.startswith("vllm:num_preemptions_total"))
            kv = [float(l.split()[-1]) for l in metrics.splitlines() if l.startswith("vllm:kv_cache_usage_perc")]
            st = json.loads(get(f"{url}/epic/stats"))
        except Exception as exc:                                    # noqa: BLE001
            alert("unreachable", repr(exc)[:200])
            time.sleep(a.interval)
            continue
        if preempt > reported["preempt"]:
            alert("preempt", f"vLLM preemptions now {preempt:.0f}")
            reported["preempt"] = preempt
        rows = int(st.get("pool_rows") or 0)
        used = int(st.get("pool_used") or 0)
        snap = {"t": time.time(), "preemptions": preempt, "kv": kv[0] if kv else None, "pool_used": used,
                "pool_rows": rows, "sessions": st.get("sessions"), "stats": st.get("stats")}
        if time.time() - t_workers >= 300:
            try:
                snap["workers"] = json.loads(get(f"{url}/epic/workers", timeout=60)).get("workers")
                t_workers = time.time()
            except Exception:                                       # noqa: BLE001
                pass
        with open(snap_path, "a") as f:
            f.write(json.dumps(snap, default=str) + "\n")
        if rows and used > 0.9 * rows:
            alert("pool", f"host pool {used}/{rows} rows ({used / rows:.0%}), {st.get('sessions')} sessions")
        if time.time() - t_beat >= 3600:
            t_beat = time.time()
            print(f"{time.strftime('%H:%M:%SZ', time.gmtime())} heartbeat: calls={counts['call']} "
                  f"fallbacks={counts['fallback']} resets={counts['reset']} errors={counts['error']} "
                  f"preemptions={preempt:.0f} pool={used}/{rows} sessions={st.get('sessions')} "
                  f"kv={(kv[0] if kv else 0):.0%}", flush=True)
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
