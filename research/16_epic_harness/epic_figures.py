#!/usr/bin/env python3
"""Static SVG figures for research/16_epic_harness's README (no matplotlib in the venv).

    python research/16_epic_harness/epic_figures.py l1 --live data/live/main [--source shadow|replay]
    python research/16_epic_harness/epic_figures.py dose --live live/main --ea live/main --ea live/screen \
        --ea live/pilot [--jct data/live/main/latency/jct_replay.jsonl]

l1    KV deviation of reused rows from a fresh recompute, per arm: by edits survived (age), by offset from
      the run head, and by layer (D^K = sum |K - K*|^2 / sum |K*|^2), one panel each, log y axis.
dose  The answer by k (shift = EPIC-0, EPIC-32/64/128) at four levels: stale-row KV deviation and reply KL
      on identical calls (E-A; compounded and one-step), task score against prefix (E-B, with the A/A
      arm), and modeled prefill time per call (E-A calls priced by the E-C model).

Style (as research/12_hw_utilization/hw_figures.py): the dataviz reference palette (light), 2-px lines,
hairline grid, one y axis per panel, a legend for every multi-series panel plus a direct label at the end
of each line.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import epic_analyze as ea  # noqa: E402
import epic_replay as er  # noqa: E402

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
FONT = "font-family='Inter, system-ui, sans-serif'"
ARM_COLOR = {"shift": SERIES[1], "epic32": SERIES[2], "epic64": SERIES[3], "epic128": SERIES[0],
             "shift1": SERIES[4], "epic128_1": SERIES[6], "prefix": SERIES[7]}
ARM_LABEL = {"shift": "shift (k=0)", "epic32": "EPIC-32", "epic64": "EPIC-64", "epic128": "EPIC-128",
             "shift1": "shift, one step", "epic128_1": "EPIC-128, one step"}


def log_ticks(lo: float, hi: float) -> list[float]:
    out = []
    e = math.floor(math.log10(lo))
    while 10 ** e <= hi * 1.0001:
        if 10 ** e >= lo * 0.9999:
            out.append(10 ** e)
        e += 1
    return out


class LogPanel:
    """One panel with a categorical x axis and a log10 y axis."""

    def __init__(self, svg: list, x, y, w, h, cats: list[str], yd: tuple[float, float], title: str, ylabel: str):
        self.svg, self.x, self.y, self.w, self.h, self.cats = svg, x, y, w, h, cats
        self.lo, self.hi = math.log10(yd[0]), math.log10(yd[1])
        svg.append(f"<text x='{x}' y='{y - 22}' {FONT} font-size='13' font-weight='600' fill='{INK}'>{title}</text>")
        svg.append(f"<text x='{x}' y='{y - 8}' {FONT} font-size='11' fill='{INK2}'>{ylabel}</text>")
        for t in log_ticks(*yd):
            yy = self.sy(t)
            svg.append(f"<line x1='{x}' x2='{x + w}' y1='{yy:.1f}' y2='{yy:.1f}' stroke='{GRID}' stroke-width='1'/>")
            svg.append(f"<text x='{x - 6}' y='{yy + 4:.1f}' {FONT} font-size='10' fill='{INK2}' "
                       f"text-anchor='end'>{t:g}</text>")
        base = y + h
        svg.append(f"<line x1='{x}' x2='{x + w}' y1='{base}' y2='{base}' stroke='{INK2}' stroke-width='1'/>")
        for i, c in enumerate(cats):
            svg.append(f"<text x='{self.sx(i):.1f}' y='{base + 14}' {FONT} font-size='10' fill='{INK2}' "
                       f"text-anchor='middle'>{c}</text>")

    def sx(self, i: int) -> float:
        n = len(self.cats)
        return self.x + (i + 0.5) / n * self.w

    def sy(self, v: float) -> float:
        lv = min(max(math.log10(max(v, 10 ** self.lo)), self.lo), self.hi)
        return self.y + self.h - (lv - self.lo) / ((self.hi - self.lo) or 1) * self.h

    def xlabel(self, text: str):
        self.svg.append(f"<text x='{self.x + self.w}' y='{self.y + self.h + 28}' {FONT} font-size='11' "
                        f"fill='{INK2}' text-anchor='end'>{text}</text>")

    def line(self, ys: list, color: str, label: str | None = None):
        d, pen, pts = [], "M", []
        for i, v in enumerate(ys):
            if v is None or not (isinstance(v, (int, float)) and v > 0 and math.isfinite(v)):
                pen = "M"
                continue
            px, py = self.sx(i), self.sy(v)
            d.append(f"{pen}{px:.1f},{py:.1f}")
            pts.append((px, py))
            pen = "L"
        if not pts:
            return
        self.svg.append(f"<path d='{' '.join(d)}' fill='none' stroke='{color}' stroke-width='2' "
                        f"stroke-linejoin='round' stroke-linecap='round'/>")
        for px, py in pts:
            self.svg.append(f"<circle cx='{px:.1f}' cy='{py:.1f}' r='4' fill='{color}' stroke='{SURFACE}' "
                            f"stroke-width='2'/>")
        if label:
            px, py = pts[-1]
            used = self.__dict__.setdefault("_labels", [])
            if all(abs(py - uy) >= 11 for _ux, uy in used):
                used.append((px, py))
                self.svg.append(f"<text x='{px + 7:.1f}' y='{py + 4:.1f}' {FONT} font-size='10' "
                                f"fill='{INK}'>{label}</text>")


def legend_row(svg: list, x: float, y: float, items: list[tuple[str, str]], max_w: float) -> float:
    """One legend for the whole figure, left to right from (x, y), wrapping at max_w; returns the next free y."""
    cx = x
    for label, color in items:
        w = 7 * len(label) + 24
        if cx > x and cx + w > x + max_w:
            cx, y = x, y + 18
        svg.append(f"<rect x='{cx:.1f}' y='{y - 9}' width='10' height='10' rx='2' fill='{color}'/>")
        svg.append(f"<text x='{cx + 14:.1f}' y='{y}' {FONT} font-size='11' fill='{INK2}'>{label}</text>")
        cx += w
    return y + 18


def svg_doc(w: int, h: int, body: list[str], title: str) -> str:
    return (f"<svg xmlns='http://www.w3.org/2000/svg' width='{w}' height='{h}' viewBox='0 0 {w} {h}' role='img'>"
            f"<title>{title}</title><rect width='{w}' height='{h}' fill='{SURFACE}'/>" + "".join(body) + "</svg>")


# ------------------------------------------------------------------------------------------------- l1
def l1_figure(calls: list[dict], arms: list[str], title: str) -> str:
    acc = ea.l1_accumulate(calls)
    arms = [a for a in arms if a in acc]
    ages = [[ea.l1_dev(acc[a], lambda c, g, o, i=i: c == 1 and g == i)[0] for i in range(len(ea.L1_AGE))]
            for a in arms]
    offs = [[ea.l1_dev(acc[a], lambda c, g, o, i=i: c == 1 and o == i)[0] for i in range(len(ea.L1_OFF))]
            for a in arms]
    layers = [[ea.l1_dev(acc[a], lambda c, g, o: c == 1, slice(i, i + 1))[0] for i in range(16)] for a in arms]
    vals = [v for block in (ages, offs, layers) for row in block for v in row
            if v is not None and math.isfinite(v) and v > 0]
    lo = 10 ** math.floor(math.log10(min(vals))) if vals else 1e-4
    hi = 10 ** math.ceil(math.log10(max(vals))) if vals else 1
    W, left, pw, ph, gap = 980, 70, 820, 170, 92
    body: list[str] = []
    items = [(ARM_LABEL.get(a, a), ARM_COLOR.get(a, INK)) for a in arms]
    y = legend_row(body, left, 26, items, pw) + 44
    panels = [("Reused rows by edits survived since last computed", ages, list(ea.L1_AGE), "edits survived (age)"),
              ("Reused rows by offset from the start of their run", offs, list(ea.L1_OFF),
               "tokens from the run head (EPIC-k recomputes the first k)"),
              ("Reused rows by layer", layers, [f"{4 * g}-{4 * g + 3}" for g in range(16)], "layers")]
    for name, rows, cats, xl in panels:
        p = LogPanel(body, left, y, pw, ph, cats, (lo, hi), name, "D^K = sum |K - K*|^2 / sum |K*|^2 (log)")
        for a, row in zip(arms, rows):
            p.line(row, ARM_COLOR.get(a, INK), ARM_LABEL.get(a, a))
        p.xlabel(xl)
        y += ph + gap
    return svg_doc(W, int(y - gap + 60), body, title)


def cmd_l1(args) -> None:
    live = ea.live_dir(args.live)
    if args.source == "shadow":
        calls = [r for path in ea.ec.server_logs(live) for r in ea.open_jsonl(path) if r.get("ev") == "call"]
        arms = ["shift", "epic32", "epic64", "epic128"]
    else:
        recs = er._load(args.ea) if args.ea else ea.open_jsonl(live / "replay" / "replay_calls.jsonl")
        calls = [{"arm": arm, "shadow": {"l1": a["l1"]}} for r in recs for arm, a in r["arms"].items() if a.get("l1")]
        arms = ["shift", "epic32", "epic64", "epic128", "shift1", "epic128_1"]
    out = Path(args.out) if args.out else live / "figures" / f"l1_{args.source}.svg"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(l1_figure(calls, arms, f"KV deviation of reused rows ({args.source})"), encoding="utf-8")
    print(out)


# ----------------------------------------------------------------------------------------------- dose
class DotPanel:
    """One panel: categorical x, linear or log10 y, a point with a 95% interval per category."""

    def __init__(self, svg: list, x, y, w, h, cats: list[str], yd: tuple[float, float], title: str, ylabel: str,
                 log: bool = False, ticks: list[float] | None = None, fmt: str = "{:g}"):
        self.svg, self.x, self.y, self.w, self.h, self.cats, self.log = svg, x, y, w, h, cats, log
        self.lo, self.hi = (math.log10(yd[0]), math.log10(yd[1])) if log else yd
        svg.append(f"<text x='{x}' y='{y - 22}' {FONT} font-size='13' font-weight='600' fill='{INK}'>{title}</text>")
        svg.append(f"<text x='{x}' y='{y - 8}' {FONT} font-size='11' fill='{INK2}'>{ylabel}</text>")
        for t in ticks or (log_ticks(*yd) if log else []):
            yy = self.sy(t)
            svg.append(f"<line x1='{x}' x2='{x + w}' y1='{yy:.1f}' y2='{yy:.1f}' stroke='{GRID}' stroke-width='1'/>")
            svg.append(f"<text x='{x - 6}' y='{yy + 4:.1f}' {FONT} font-size='10' fill='{INK2}' "
                       f"text-anchor='end'>{fmt.format(t)}</text>")
        svg.append(f"<line x1='{x}' x2='{x + w}' y1='{y + h}' y2='{y + h}' stroke='{INK2}' stroke-width='1'/>")
        for i, c in enumerate(cats):
            svg.append(f"<text x='{self.sx(i):.1f}' y='{y + h + 14}' {FONT} font-size='10' fill='{INK2}' "
                       f"text-anchor='middle'>{c}</text>")

    def sx(self, i: float) -> float:
        return self.x + (i + 0.5) / len(self.cats) * self.w

    def sy(self, v: float) -> float:
        if self.log:
            v = math.log10(max(v, 10 ** self.lo))
        v = min(max(v, self.lo), self.hi)
        return self.y + self.h - (v - self.lo) / ((self.hi - self.lo) or 1) * self.h

    def ref(self, v: float, label: str, dash: str = "4 3"):
        yy = self.sy(v)
        self.svg.append(f"<line x1='{self.x}' x2='{self.x + self.w}' y1='{yy:.1f}' y2='{yy:.1f}' stroke='{INK2}' "
                        f"stroke-width='1' stroke-dasharray='{dash}'/>")
        self.svg.append(f"<text x='{self.x + self.w + 6}' y='{yy + 4:.1f}' {FONT} font-size='10' fill='{INK2}'>"
                        f"{label}</text>")

    def dot(self, i: float, v, lo, hi, color: str, label: str | None = None, hollow: bool = False):
        if v is None or not math.isfinite(v) or (self.log and v <= 0):
            return
        px, py = self.sx(i), self.sy(v)
        if lo is not None and hi is not None and math.isfinite(lo) and math.isfinite(hi):
            self.svg.append(f"<line x1='{px:.1f}' x2='{px:.1f}' y1='{self.sy(lo):.1f}' y2='{self.sy(hi):.1f}' "
                            f"stroke='{color}' stroke-width='2' stroke-linecap='round'/>")
        fill = SURFACE if hollow else color
        self.svg.append(f"<circle cx='{px:.1f}' cy='{py:.1f}' r='5' fill='{fill}' stroke='{color}' "
                        f"stroke-width='2'/>")
        if label:
            self.svg.append(f"<text x='{px + 9:.1f}' y='{py + 4:.1f}' {FONT} font-size='10' "
                            f"fill='{INK}'>{label}</text>")


def _boot(units: dict, stat, reps: int = 2000, seed: str = "fig") -> tuple[float, float, float]:
    """stat over all units' items, 95% interval resampling units (runs or cells)."""
    keys = sorted(units)
    point = stat([x for k in keys for x in units[k]])
    rng = __import__("random").Random(seed)
    vals = sorted(v for v in (stat([x for _ in keys for x in units[rng.choice(keys)]]) for _ in range(reps))
                  if v == v)
    if not vals:
        return point, float("nan"), float("nan")
    return point, vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1]


def dose_data(live: str, ea_srcs: list[str], jct_path: str | None) -> dict:
    data = {}
    recs = [r for r in er._load(ea_srcs) if r.get("edited")] if ea_srcs else []
    by_run = {}
    for r in recs:
        by_run.setdefault(r["src"] + "/" + r["run"], []).append(r)

    def dev(arm):
        def stat(rs):
            v = []
            for r in rs:
                a = r["arms"].get(arm)
                if a and a.get("l1"):
                    d, k = er._l1_reused(a)
                    if k:
                        v.append(d / k)
            return sum(v) / len(v) if v else float("nan")
        return stat

    def kl(arm):
        def stat(rs):
            v = [r["arms"][arm]["kl"] for r in rs if r["arms"].get(arm, {}).get("kl") is not None]
            return sum(v) / len(v) if v else float("nan")
        return stat

    for arm in ("shift", "epic32", "epic64", "epic128", "shift1", "epic128_1"):
        data[("dev", arm)] = _boot(by_run, dev(arm))
        data[("kl", arm)] = _boot(by_run, kl(arm))
    rows = [json.loads(l) for l in (ea.live_dir(live) / "runs_metrics.jsonl").read_text().splitlines()]
    rows = [r for r in rows if r.get("regime") == "R1"]
    for arm in ("prefix_aa", "shift", "epic32", "epic64", "epic128"):
        pairs = ea.paired_blocks(rows, "prefix", arm, "score")
        data[("score", arm)] = ea.cluster_bootstrap(pairs) if pairs else (float("nan"),) * 3
        data[("pairs", arm)] = len(pairs)
    if jct_path and Path(jct_path).exists():
        jr = [json.loads(l) for l in Path(jct_path).read_text().splitlines()]
        units = {r["src"] + "/" + r["run"]: [r] for r in jr}
        for arm in ("recompute", "prefix", "shift", "epic32", "epic64", "epic128"):
            data[("prefill", arm)] = _boot(units, lambda rs, arm=arm: 1e3 * sum(r["prefill_s"][arm] for r in rs)
                                           / max(1, sum(r["calls"] for r in rs)))
    data["n_calls"], data["n_runs"] = len(recs), len(by_run)
    return data


def dose_figure(d: dict) -> str:
    W, left, pw, ph, gapx, gapy = 1000, 74, 380, 210, 120, 110
    body: list[str] = []
    ks = ["shift", "epic32", "epic64", "epic128"]
    cats = ["k = 0 (shift)", "EPIC-32", "EPIC-64", "EPIC-128"]
    comp, one = SERIES[0], SERIES[1]
    y0 = 70
    # (a) stale-row deviation, (b) reply KL: compounded (filled) and one-step (hollow)
    for col, (key, title, ylabel) in enumerate((
            ("dev", "a. KV of reused rows vs fresh recompute (E-A)", "D^K of reused-stale rows, mean per call (log)"),
            ("kl", "b. Next-reply KL vs fresh recompute (E-A)", "top-20 KL, nats per token (log)"))):
        vals = [v for a in ks + ["shift1", "epic128_1"] for v in d[(key, a)] if v == v and v > 0]
        yd = (10 ** math.floor(math.log10(min(vals))), 10 ** math.ceil(math.log10(max(vals)))) if vals else (1e-4, 1)
        p = DotPanel(body, left + col * (pw + gapx), y0, pw, ph, cats, yd, title, ylabel, log=True)
        for i, a in enumerate(ks):
            p.dot(i - 0.08, *d[(key, a)], comp)
        p.dot(0 + 0.12, *d[(key, "shift1")], one, hollow=True)
        p.dot(3 + 0.12, *d[(key, "epic128_1")], one, hollow=True)
    # (c) task score difference vs prefix, with the A/A arm
    sc = [d[("score", a)] for a in ["prefix_aa"] + ks]
    span = max([abs(v) for t in sc for v in t if v == v] + [0.1])
    lim = math.ceil(span * 10) / 10
    ticks = [round(-lim + i * lim / 2, 2) for i in range(5)]
    p = DotPanel(body, left, y0 + ph + gapy, pw, ph, ["A/A (prefix)"] + cats, (-lim, lim),
                 "c. Task score minus prefix (E-B, paired blocks)", "difference, 95% CI over cells",
                 ticks=ticks, fmt="{:+.2f}")
    p.ref(0.0, "no difference")
    p.ref(-0.08, "NI margin -0.08", dash="2 3")
    for i, a in enumerate(["prefix_aa"] + ks):
        p.dot(i, *d[("score", a)], INK2 if a == "prefix_aa" else comp)
    # (d) prefill per call
    arms = ["recompute", "prefix"] + ks
    pv = [d.get(("prefill", a), (float("nan"),) * 3) for a in arms]
    top = max([t[2] for t in pv if t[2] == t[2]] + [t[0] for t in pv if t[0] == t[0]] + [100])
    step = 10 ** math.floor(math.log10(top)) / (2 if top / 10 ** math.floor(math.log10(top)) < 3 else 1)
    hi = math.ceil(top / step) * step
    p = DotPanel(body, left + pw + gapx, y0 + ph + gapy, pw, ph, ["recompute", "prefix"] + cats, (0, hi),
                 "d. Prefill time per lead call (E-A calls, E-C model)", "ms per call, one H100",
                 ticks=[i * step for i in range(int(hi / step) + 1)], fmt="{:,.0f}")
    for i, (a, t) in enumerate(zip(arms, pv)):
        p.dot(i, *t, INK2 if a in ("recompute", "prefix") else comp)
    # legend
    lx, ly = left, 30
    for label, color, hollow in (("compounded (as served)", comp, False),
                                 ("one-step (fresh KV before the call)", one, True), ("references", INK2, False)):
        fill = SURFACE if hollow else color
        body.append(f"<circle cx='{lx + 5}' cy='{ly - 4}' r='5' fill='{fill}' stroke='{color}' stroke-width='2'/>")
        body.append(f"<text x='{lx + 15}' y='{ly}' {FONT} font-size='11' fill='{INK2}'>{label}</text>")
        lx += 7 * len(label) + 40
    foot = (f"E-A: {d['n_calls']} edited lead calls of {d['n_runs']} recorded prefix runs; E-B pairs per arm: "
            + ", ".join(f"{a} {d[('pairs', a)]}" for a in ["prefix_aa"] + ks))
    body.append(f"<text x='{left}' y='{y0 + 2 * ph + gapy + 52}' {FONT} font-size='10' fill='{INK2}'>{foot}</text>")
    return svg_doc(W, y0 + 2 * ph + gapy + 66, body, "EPIC-k against full re-prefill at four levels")


def cmd_dose(args) -> None:
    out = Path(args.out) if args.out else ea.ec.DATA / "figures" / "dose.svg"
    out.parent.mkdir(parents=True, exist_ok=True)
    d = dose_data(args.live, args.ea, args.jct)
    out.write_text(dose_figure(d), encoding="utf-8")
    print(out)
    for k, v in d.items():
        print(k, v)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("l1")
    p.add_argument("--live", required=True)
    p.add_argument("--source", choices=["shadow", "replay"], default="shadow")
    p.add_argument("--ea", action="append", default=[], help="with --source replay: E-A sources live_dir[:replay_name]")
    p.add_argument("--out", default=None)
    d = sub.add_parser("dose")
    d.add_argument("--live", required=True, help="E-B live dir with runs_metrics.jsonl")
    d.add_argument("--ea", action="append", default=[], help="E-A source live_dir[:replay_name], repeatable")
    d.add_argument("--jct", default=None, help="jct_replay.jsonl from epic_latency.py jct")
    d.add_argument("--out", default=None)
    args = ap.parse_args()
    {"l1": cmd_l1, "dose": cmd_dose}[args.cmd](args)


if __name__ == "__main__":
    main()
