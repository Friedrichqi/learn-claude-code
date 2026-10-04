#!/usr/bin/env python3
"""Static SVG figures for research/12_hw_utilization's README (no matplotlib in the venv).

    python research/12_hw_utilization/hw_figures.py timeline --cell p1a --level hw_p1a [--minutes 0:20]
    python research/12_hw_utilization/hw_figures.py load      # from data/tables/summary.json

timeline  one level, small multiples on a shared time axis: CPU cores by group (stacked), GPU SM / HBM
          bandwidth / kernel-active %, the KV pool (live / valid-but-idle / empty, stacked), and how many
          model calls and tools are in flight.
load      per windowed level of p2a / p2b / p2c: lead calls per hour, CPU cores, GPU SM and HBM bandwidth %,
          KV live %, one panel each, N on the x axis.
Style: the dataviz reference palette (light), 2-px lines, ~10 % area washes, hairline grid, one y axis per
panel, a legend for every multi-series panel plus a direct label at the line end.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import hw_analyze as ha  # noqa: E402

FIG = ha.DATA / "figures"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
FONT = "font-family='Inter, system-ui, sans-serif'"


def nice_ticks(lo: float, hi: float, n: int = 4) -> list[float]:
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = min((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), default=raw)
    start = math.floor(lo / step) * step
    out, v = [], start
    while v <= hi + 1e-9:
        if v >= lo - 1e-9:
            out.append(round(v, 10))
        v += step
    return out


def fmt_tick(v: float) -> str:
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    return f"{v:g}"


class Panel:
    def __init__(self, svg: list, x: float, y: float, w: float, h: float, xd, yd, title: str, ylabel: str):
        self.svg, self.x, self.y, self.w, self.h = svg, x, y, w, h
        self.xd, self.yd = xd, yd
        svg.append(f"<text x='{x}' y='{y - 22}' {FONT} font-size='13' font-weight='600' fill='{INK}'>{title}</text>")
        svg.append(f"<text x='{x}' y='{y - 8}' {FONT} font-size='11' fill='{INK2}'>{ylabel}</text>")
        for t in nice_ticks(*yd):
            yy = self.sy(t)
            svg.append(f"<line x1='{x}' x2='{x + w}' y1='{yy:.1f}' y2='{yy:.1f}' stroke='{GRID}' stroke-width='1'/>")
            svg.append(f"<text x='{x - 6}' y='{yy + 4:.1f}' {FONT} font-size='10' fill='{INK2}' text-anchor='end'>{fmt_tick(t)}</text>")

    def sx(self, v):
        lo, hi = self.xd
        return self.x + (v - lo) / ((hi - lo) or 1) * self.w

    def sy(self, v):
        lo, hi = self.yd
        return self.y + self.h - (min(max(v, lo), hi) - lo) / ((hi - lo) or 1) * self.h

    def xaxis(self, label: str, ticks=None, labels=None):
        ticks = ticks if ticks is not None else nice_ticks(*self.xd, n=6)
        base = self.y + self.h
        self.svg.append(f"<line x1='{self.x}' x2='{self.x + self.w}' y1='{base}' y2='{base}' stroke='{INK2}' stroke-width='1'/>")
        for i, t in enumerate(ticks):
            xx = self.sx(t)
            text = labels[i] if labels else fmt_tick(t)
            self.svg.append(f"<text x='{xx:.1f}' y='{base + 14}' {FONT} font-size='10' fill='{INK2}' text-anchor='middle'>{text}</text>")
        self.svg.append(f"<text x='{self.x + self.w}' y='{base + 28}' {FONT} font-size='11' fill='{INK2}' text-anchor='end'>{label}</text>")

    def line(self, xs, ys, color, label=None, dots=False):
        """A 2-px line; a missing value (None / NaN) leaves a gap instead of breaking the path."""
        pts, d, pen = [], [], "M"
        for a, b in zip(xs, ys):
            if b is None or (isinstance(b, float) and math.isnan(b)):
                pen = "M"
                continue
            px, py = self.sx(a), self.sy(b)
            pts.append((px, py))
            d.append(f"{pen}{px:.1f},{py:.1f}")
            pen = "L"
        if not pts:
            return
        d = " ".join(d)
        self.svg.append(f"<path d='{d}' fill='none' stroke='{color}' stroke-width='2' stroke-linejoin='round' stroke-linecap='round'/>")
        if dots:
            for px, py in pts:
                self.svg.append(f"<circle cx='{px:.1f}' cy='{py:.1f}' r='4' fill='{color}' stroke='{SURFACE}' stroke-width='2'/>")
        if label:
            px, py = pts[-1]
            used = self.__dict__.setdefault("_labels", [])
            if all(abs(py - uy) >= 11 or abs(px - ux) >= 60 for ux, uy in used):   # converging ends: the legend carries it
                used.append((px, py))
                self.svg.append(f"<text x='{px + 6:.1f}' y='{py + 4:.1f}' {FONT} font-size='10' fill='{INK}'>{label}</text>")

    def stack(self, xs, layers: list[tuple[str, list[float], str]]):
        """Stacked areas (bottom first): each layer = (label, values, color); 2-px surface gap between."""
        base = [0.0] * len(xs)
        for label, vals, color in layers:
            top = [b + (v or 0) for b, v in zip(base, vals)]
            up = " ".join(f"{'M' if i == 0 else 'L'}{self.sx(x):.1f},{self.sy(t):.1f}" for i, (x, t) in enumerate(zip(xs, top)))
            down = " ".join(f"L{self.sx(x):.1f},{self.sy(b):.1f}" for x, b in zip(reversed(xs), reversed(base)))
            gap = f"stroke='{SURFACE}' stroke-width='1'" if len(xs) <= 60 else "stroke='none'"   # dense series: no seams
            self.svg.append(f"<path d='{up} {down} Z' fill='{color}' fill-opacity='0.85' {gap}/>")
            base = top

    def legend(self, items: list[tuple[str, str]]):
        x = self.x + self.w - 6
        for label, color in reversed(items):
            width = 7 * len(label) + 18
            x -= width
            self.svg.append(f"<rect x='{x:.1f}' y='{self.y - 31}' width='10' height='10' rx='2' fill='{color}'/>")
            self.svg.append(f"<text x='{x + 14:.1f}' y='{self.y - 22}' {FONT} font-size='11' fill='{INK2}'>{label}</text>")


def svg_doc(w: int, h: int, body: list[str], title: str) -> str:
    return (f"<svg xmlns='http://www.w3.org/2000/svg' width='{w}' height='{h}' viewBox='0 0 {w} {h}' role='img'>"
            f"<title>{title}</title><rect width='{w}' height='{h}' fill='{SURFACE}'/>" + "".join(body) + "</svg>")


# ------------------------------------------------------------------------------------------ timeline
def timeline(args) -> Path:
    cell = ha.CELLS / args.cell
    for srv_dir in sorted((cell / "server").glob("*")):
        srv = ha.Server(srv_dir)
        lv = next((x for x in srv.levels() if x["level"] == args.level), None)
        if lv:
            break
    else:
        raise SystemExit(f"level {args.level} not found in {cell}")
    w0, w1 = lv["w0"], lv["w1"]
    if args.minutes:
        a, b = (float(x) for x in args.minutes.split(":"))
        w0, w1 = w0 + 60 * a, min(w1, w0 + 60 * b)
    m0 = srv.mono(w0)
    host = srv.window(srv.host, srv.host_t, w0, w1)
    rates = ha.cpu_rates(host)
    fast = srv.window(srv.fast, srv.fast_t, w0, w1)
    sessions = ha.level_sessions(cell / "levels" / args.level)
    idx = ha.StateIndex(sessions)
    pools = [r for r in srv.pools if m0 <= r["t"] <= srv.mono(w1)]
    nblk = srv.info.get("num_gpu_blocks")
    span = (w1 - w0) / 60
    W, left, pw, ph, gap = 980, 70, 820, 120, 70
    body: list[str] = []
    y = 60
    xs = [(r["t"] - m0) / 60 for r in rates]
    cmax = max([r["engine"] + r["api"] + r["harness"] + r["tools"] for r in rates] + [1])
    p = Panel(body, left, y, pw, ph, (0, span), (0, cmax * 1.1), "CPU cores in use", "cores (1-s means)")
    layers = [("EngineCore", [r["engine"] for r in rates], SERIES[0]), ("vLLM API server", [r["api"] for r in rates], SERIES[1]),
              ("harness", [r["harness"] for r in rates], SERIES[2]), ("tools", [max(0, r["tools"]) for r in rates], SERIES[3])]
    p.stack(xs, layers)
    p.legend([(n, c) for n, _, c in layers])
    y += ph + gap
    if fast and fast[0].get("gpu"):
        bins: dict[int, list[dict]] = {}
        for r in fast:                                   # 1-s means of the 10-Hz samples
            bins.setdefault(int(r["t"] - m0), []).append(r["gpu"][0])
        fx = [(k + 0.5) / 60 for k in sorted(bins)]
        g = [{key: ha._mean(x.get(key) for x in bins[k]) for key in ("gr", "sm", "dram", "util", "tensor")} for k in sorted(bins)]
        p = Panel(body, left, y, pw, ph, (0, span), (0, 100), "GPU activity (GPU 0)", "% (1-s means of 100-ms GPM samples)")
        series = [("GPU busy", "gr", SERIES[6]), ("SM active", "sm", SERIES[0]), ("HBM bandwidth", "dram", SERIES[1])]
        for name, key, color in series:
            if any(x.get(key) is not None for x in g):
                p.line(fx, [x.get(key) for x in g], color)
        p.legend([(n, c) for n, k, c in series if any(x.get(k) is not None for x in g)])
        y += ph + gap
    if pools and nblk:
        px_ = [(r["t"] - m0) / 60 for r in pools]
        usable = nblk - 1
        p = Panel(body, left, y, pw, ph, (0, span), (0, 100), "KV pool (HBM vLLM reserved for KV)", "% of the pool")
        layers = [("live (referenced)", [100 * (usable - r["free"]) / usable for r in pools], SERIES[0]),
                  ("valid but idle", [100 * r["cfree"] / usable for r in pools], SERIES[2]),
                  ("empty", [100 * (r["free"] - r["cfree"]) / usable for r in pools], GRID)]
        p.stack(px_, layers)
        p.legend([(n, c) for n, _, c in layers])
        y += ph + gap
    ts = [m0 + i * 0.5 for i in range(int((srv.mono(w1) - m0) / 0.5))]
    counts = [idx.counts(t) for t in ts]
    hmax = max([a for a, _ in counts] + [b for _, b in counts] + [1])
    p = Panel(body, left, y, pw, ph * 0.7, (0, span), (0, hmax), "Agent activity", "in flight")
    tx = [(t - m0) / 60 for t in ts]
    p.line(tx, [a for a, _ in counts], SERIES[0])
    p.line(tx, [b for _, b in counts], SERIES[3])
    p.legend([("model calls", SERIES[0]), ("tool calls", SERIES[3])])
    p.xaxis("minutes into the window")
    H = int(y + ph * 0.7 + 50)
    FIG.mkdir(parents=True, exist_ok=True)
    out = FIG / f"timeline_{args.cell}_{args.level}.svg"
    title = f"{args.cell} / {args.level}: CPU, GPU, KV pool and agent activity over time"
    out.write_text(svg_doc(W, H, body, title))
    return out


# ------------------------------------------------------------------------------------------ load curve
def load(args) -> Path:
    res = json.loads((ha.TABLES / "summary.json").read_text())
    pts: dict[str, list[tuple[int, dict]]] = {}
    for cname, levels in res["cells"].items():
        for lname, r in levels.items():
            if cname in ("p1a", "p1b") and lname in ("hw_p1a", "hw_p1b"):     # N=1 (mixed sessions) starts both series
                for key in (("p2a", "p2b") if cname == "p1a" else ("p2c_l", "p2c_h")):
                    pts.setdefault(key, []).append((1, r))
            elif cname in ("p2a", "p2b"):
                pts.setdefault(cname, []).append((r["n"], r))
            elif cname == "p2c":
                pts.setdefault("p2c_l" if "_l" in lname else "p2c_h", []).append((r["n"], r))
    names = {"p2a": "32B LIGHT", "p2b": "32B heavy mix", "p2c_l": "8B LIGHT", "p2c_h": "8B heavy mix"}
    order = [k for k in ("p2a", "p2b", "p2c_l", "p2c_h") if k in pts]
    ns = sorted({n for k in order for n, _ in pts[k]})
    if not ns:
        raise SystemExit("no load-curve levels in summary.json")
    xd = (0, len(ns) - 1)
    W, left, pw, ph, gap = 980, 70, 760, 120, 70
    body: list[str] = []
    y = 60
    metrics = [("Lead calls per hour", "calls / h", lambda r: (r.get("agent_time") or {}).get("lead_calls_per_h")),
               ("CPU cores in use (all groups)", "cores, mean", lambda r: ((r.get("cpu") or {}).get("total") or {}).get("mean")),
               ("GPU SM active", "%, mean", lambda r: (ha._g(r).get("sm") or {}).get("mean")),
               ("GPU HBM bandwidth", "% of peak, mean", lambda r: (ha._g(r).get("dram") or {}).get("mean")),
               ("KV pool live", "%, mean", lambda r: 100 * (((r.get("hbm") or {}).get("kv_frac") or {}).get("live") or {}).get("mean", float("nan")))]
    for title, unit, fn in metrics:
        vals = [fn(r) for k in order for _, r in pts[k] if fn(r) is not None and not (isinstance(fn(r), float) and math.isnan(fn(r)))]
        if not vals:
            continue
        p = Panel(body, left, y, pw, ph, xd, (0, max(vals) * 1.15 or 1), title, unit)
        for i, k in enumerate(order):
            rows = sorted(pts[k], key=lambda x: x[0])
            xs = [ns.index(n) for n, _ in rows]
            ys = [fn(r) for _, r in rows]
            ys = [None if (v is None or (isinstance(v, float) and math.isnan(v))) else v for v in ys]
            p.line(xs, ys, SERIES[i], label=names[k] if len(order) <= 4 else None, dots=True)
        p.legend([(names[k], SERIES[i]) for i, k in enumerate(order)])
        y += ph + gap
    p.xaxis("concurrent agents N (N = 1: Phase 1, mixed sessions)", ticks=list(range(len(ns))), labels=[str(n) for n in ns])
    H = int(y - gap + 50)
    FIG.mkdir(parents=True, exist_ok=True)
    out = FIG / "load_curve.svg"
    out.write_text(svg_doc(W, H, body, "Throughput and utilization as concurrent agents grow"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("timeline")
    t.add_argument("--cell", required=True)
    t.add_argument("--level", required=True)
    t.add_argument("--minutes", default="")
    sub.add_parser("load")
    args = ap.parse_args()
    out = timeline(args) if args.cmd == "timeline" else load(args)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
