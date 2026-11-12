#!/usr/bin/env python3
"""Topic 24's figures, from data/tables/sp.json and series_<cell>.json (written by sp_analyze.py).

    ~/dgxspark-profiling/exp01_um_profiling/.venv/bin/python research/24_spark_concurrency/sp_figs.py [--json sp_smoke.json]

Writes data/tables/figs/*.svg. All figures have the number of users N on the x-axis (as ordered positions, one per
cell), except the per-cell time-series pages (minutes on x).

Chart rules followed (the dataviz skill): the reference categorical palette in its documented order, never cycled; one
y-axis per panel (two measures get two panels); 2 px lines, markers with a surface ring, bars at most 24 px with a 2 px
surface gap between stacked segments; hairline grid; text in ink colors, never in a series color; a legend whenever a
panel has two or more series, plus end labels where they fit.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp_common as C  # noqa: E402

SURFACE, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
GRAY = "#c3c2b7"
SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
FIGS = C.TABLES / "figs"
PNG_DIR = None          # --png DIR: also write PNG copies there, to look at the figures

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE, "font.size": 10,
    "font.family": "DejaVu Sans", "text.color": INK, "axes.labelcolor": INK2, "axes.edgecolor": AXIS,
    "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelcolor": INK2, "ytick.labelcolor": INK2,
    "axes.grid": True, "axes.grid.axis": "y", "grid.color": GRID, "grid.linewidth": 1.0, "axes.axisbelow": True,
    "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False, "axes.linewidth": 1.0,
    "axes.titlesize": 11, "axes.titleweight": "bold", "axes.titlelocation": "left", "legend.frameon": False,
    "legend.fontsize": 9, "svg.fonttype": "none", "xtick.major.size": 0, "ytick.major.size": 0,
})


def get(d, *path):
    for p in path:
        if not isinstance(d, dict) or p not in d:
            return None
        d = d[p]
    return d


def vals(results, *path, scale=1.0):
    out = []
    for r in results:
        v = get(r, *path)
        out.append(np.nan if v is None else v * scale)
    return np.array(out, dtype=float)


def has(a) -> bool:
    return bool(np.isfinite(np.asarray(a, dtype=float)).any())


# ---------------------------------------------------------------------------------------------------------------------
# One x position per number of users; one line (or one panel) per model, always in the same colour.
VCOL = {"bf16": SERIES[0], "fp8": SERIES[1], "nvfp4": SERIES[2]}
VNAME = {"bf16": "BF16", "fp8": "FP8", "nvfp4": "NVFP4 (mixed)"}


def users(R) -> list[int]:
    return sorted({r["n"] for r in R})


def variants(R) -> list[str]:
    return [v for v in C.VARIANT_ORDER if any(r.get("variant", "bf16") == v for r in R)]


def pivot(R, variant: str, *path, scale=1.0) -> np.ndarray:
    """The value at `path` for each number of users, for one model (NaN where there is no cell)."""
    out = []
    for n in users(R):
        rr = [r for r in R if r["n"] == n and r.get("variant", "bf16") == variant]
        v = get(rr[0], *path) if rr else None
        out.append(np.nan if v is None else v * scale)
    return np.array(out, dtype=float)


def xaxis(ax, R):
    ax.set_xticks(range(len(users(R))))
    ax.set_xticklabels([str(n) for n in users(R)])
    ax.set_xlabel("concurrent users (N)")
    ax.set_xlim(-0.6, len(users(R)) - 0.4)


def by_model(ax, R, path, title, ylabel, unit="", scale=1.0, fmt="{:,.3g}", top=None):
    """One line per model over the number of users; the last point of each line is labelled."""
    x = np.arange(len(users(R)))
    drawn, hi, ends = 0, 0.0, []
    for v in variants(R):
        y = pivot(R, v, *path, scale=scale)
        if not has(y):
            continue
        ax.plot(x, y, color=VCOL[v], lw=2, marker="o", ms=8, mec=SURFACE, mew=2, label=VNAME[v],
                solid_capstyle="round", solid_joinstyle="round", zorder=3)
        j = np.where(np.isfinite(y))[0][-1]
        ends.append([x[j], y[j], fmt.format(y[j]) + unit])
        hi = max(hi, float(np.nanmax(y)))
        drawn += 1
    headroom(ax, top or hi)
    # end labels, moved apart when the lines end close together (the marker stays where the value is)
    gap = 0.065 * ax.get_ylim()[1]
    ends.sort(key=lambda e: e[1])
    ypos = [e[1] for e in ends]
    for i in range(1, len(ypos)):
        ypos[i] = max(ypos[i], ypos[i - 1] + gap)
    for (xe, ye, text), yl in zip(ends, ypos):
        ax.annotate(text, (xe, ye), xytext=(xe + 0.13, yl), textcoords="data", va="center", fontsize=9, color=INK2)
    ax._sp_legend = min(drawn, 3) if drawn >= 2 else 0
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    xaxis(ax, R)
    if not drawn:
        ax.text(0.5, 0.5, "no data for these cells", transform=ax.transAxes, ha="center", color=MUTED)
    return drawn


def legend_above(ax, ncols):
    """The legend sits between the title and the plot area, so it can never cover a mark."""
    n = len(ax.get_legend_handles_labels()[0])
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncols=ncols, handlelength=1.4, borderaxespad=0.1,
              columnspacing=1.2)
    ax._sp_pad = 24 + 14 * ((n - 1) // max(ncols, 1))
    ax.set_title(ax.get_title(loc="left"), pad=ax._sp_pad)


def headroom(ax, top):
    if np.isfinite(top) and top > 0:
        ax.set_ylim(0, top * 1.18)


def stack(ax, R, variant, series, width=0.5, legend=True, top=None):
    """Stacked columns over the number of users for one model; a 2 px surface gap between segments."""
    x = np.arange(len(users(R)))
    bottom = np.zeros(len(x))
    drawn = 0
    for i, (label, path, scale) in enumerate(series):
        y = np.nan_to_num(pivot(R, variant, *path, scale=scale) if not callable(path) else path(variant))
        if not y.any():
            continue
        ax.bar(x, y, width, bottom=bottom, color=SERIES[i], edgecolor=SURFACE, linewidth=2, label=label, zorder=3)
        bottom += y
        drawn += 1
    for xi, b in zip(x, bottom):
        if b:
            ax.annotate(f"{b:,.3g}", (xi, b), xytext=(0, 4), textcoords="offset points", ha="center", fontsize=9,
                        color=INK2)
    ax._sp_legend = min(drawn, 2) if legend and drawn >= 2 else 0
    headroom(ax, top or bottom.max())
    xaxis(ax, R)
    return drawn, float(bottom.max())


def panels(R, rows=1, h=3.9, sharey=True):
    """One column of panels per model."""
    vs = variants(R)
    fig, axes = plt.subplots(rows, len(vs), figsize=(4.4 * len(vs) + 0.6, h * rows), sharey=sharey, squeeze=False,
                             gridspec_kw={"wspace": 0.12, "hspace": 0.75})
    return fig, axes, vs


def save(fig, name: str, note: str | None = None):
    FIGS.mkdir(parents=True, exist_ok=True)
    pads = []
    for ax in fig.axes:
        if getattr(ax, "_sp_legend", 0):
            legend_above(ax, ax._sp_legend)
            pads.append(ax.title.get_position()[1] and ax._sp_pad)
    for ax in fig.axes:                      # panels without a legend get the same title height as those with one
        if pads and not getattr(ax, "_sp_legend", 0) and not getattr(ax, "_sp_fixed_pad", False) and ax.get_title(loc="left"):
            ax.set_title(ax.get_title(loc="left"), pad=max(pads))
    if note:
        fig.text(0.01, -0.03, note, fontsize=8, color=MUTED, ha="left", va="top")
    fig.savefig(FIGS / name, format="svg", bbox_inches="tight")
    if PNG_DIR:
        fig.savefig(Path(PNG_DIR) / name.replace(".svg", ".png"), format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    print(f"  {FIGS / name}")


# ---------------------------------------------------------------------------------------------------------------------
def fig1(R):
    fig, (a, b, c) = plt.subplots(1, 3, figsize=(14.5, 3.9), gridspec_kw={"wspace": 0.32})
    by_model(a, R, ("throughput", "steady", "output_tok_s"), "Output tokens/s, all users (steady)", "tokens/s")
    by_model(b, R, ("throughput", "per_user_decode_tok_s"), "Decode speed per user (1 / TPOT p50)",
             "tokens/s per user")
    by_model(c, R, ("latency", "all", "tpot_ms", "p50"), "TPOT, p50", "ms", unit=" ms", fmt="{:,.0f}")
    save(fig, "fig1_throughput.svg")


def fig2(R):
    fig, axes, vs = panels(R, rows=2)
    S = lambda ph: [("queue", ("latency", ph, "queue_s", "mean"), 1), ("prefill", ("latency", ph, "prefill_s", "mean"), 1),  # noqa: E731
                    ("decode", ("latency", ph, "decode_s", "mean"), 1),
                    ("tools and harness, until the next call", ("latency", ph, "tool_s", "mean"), 1)]
    for i, ph in enumerate(("append", "snip")):
        tops = []
        for j, v in enumerate(vs):
            n, top = stack(axes[i, j], R, v, S(ph), legend=(i == 0 and j == 0))
            tops.append(top)
            axes[i, j].set_title(f"{VNAME[v]}: one call, {ph} phase")
            if not n:
                axes[i, j].text(0.5, 0.5, "no calls in this phase", transform=axes[i, j].transAxes, ha="center",
                                color=MUTED)
        headroom(axes[i, 0], max(tops))
        axes[i, 0].set_ylabel("mean seconds per call")
    save(fig, "fig2_latency.svg", "First sessions' calls. Queue, prefill and decode are vLLM's own timings. append = "
         "before the session's first trim, snip = after it.")


def fig3(R):
    fig, axes, vs = panels(R, rows=2)
    for j, v in enumerate(vs):
        a, b = axes[0, j], axes[1, j]
        n, _ = stack(a, R, v, [("GPU reads", ("memory", "all", "gpu_read_gbps"), 1),
                               ("GPU writes", ("memory", "all", "gpu_write_gbps"), 1),
                               ("CPU reads", ("memory", "all", "cpu_read_gbps"), 1),
                               ("CPU writes", ("memory", "all", "cpu_write_gbps"), 1)], legend=(j == 0), top=273.06)
        a.axhline(273.06, color=AXIS, lw=1)
        if j == 0:
            a.annotate("theoretical peak 273 GB/s", (len(users(R)) - 0.45, 273.06), xytext=(0, 3),
                       textcoords="offset points", ha="right", fontsize=8, color=MUTED)
            a.set_ylabel("GB/s")
            b.set_ylabel("GB/s")
        a.set_title(f"{VNAME[v]}: DRAM traffic by source")
        if not n:
            a.text(0.5, 0.5, "no hardware counters", transform=a.transAxes, ha="center", color=MUTED)
        stack(b, R, v, [("weights", ("memory", "model_decode_steps", "weights_gbps"), 1),
                        ("DeltaNet state", ("memory", "model_decode_steps", "state_gbps"), 1),
                        ("KV read", ("memory", "model_decode_steps", "kv_read_gbps"), 1)], legend=False, top=273.06)
        m = pivot(R, v, "memory", "model_decode_steps", "measured_gbps")
        if has(m):
            b.plot(np.arange(len(m)) + 0.36, m, ls="none", marker="D", ms=8, color=INK, mec=SURFACE, mew=2, zorder=4,
                   label="measured by the GPU's counters")
        if j == 0:
            b._sp_legend = 2
        b.set_title(f"{VNAME[v]}: decode-only steps, byte model")
    save(fig, "fig3_dram.svg", "Top: whole window. Bottom: bars are the byte model (weights + DeltaNet state + KV "
         "read per step), the diamond is the measured GPU traffic in the same steps.")


def fig4(R):
    fig, axes = plt.subplots(2, 2, figsize=(11, 7.6), gridspec_kw={"wspace": 0.25, "hspace": 0.7})
    by_model(axes[0, 0], R, ("gpu", "all", "tensor_active_pct"), "Tensor cores active", "% of time", unit="%")
    by_model(axes[0, 1], R, ("gpu", "all", "tensor_insts_per_s"), "Tensor-pipe instructions per second",
             "1e9 instructions/s", scale=1e-9)
    by_model(axes[1, 0], R, ("throughput", "all", "workload_tflops"), "Matrix math of the linear layers (from tokens)",
             "TFLOPS")
    by_model(axes[1, 1], R, ("gpu", "all", "sm_issue_pct"), "SM instruction issue rate", "% of peak", unit="%")
    save(fig, "fig4_gpu.svg", "A tensor instruction does about 4,096 FLOPs in BF16, twice that in FP8 and four times "
         "in FP4, so the instruction count is not comparable across models; the TFLOPS panel is.")


def fig5(R):
    fig = plt.figure(figsize=(13.8, 4.0 + 0.42 * len(R) + 2.2))
    vs = variants(R)
    gs = fig.add_gridspec(2, len(vs), height_ratios=[3.6, 0.42 * len(R) + 0.6], hspace=0.6, wspace=0.12)
    G = [("vLLM", ("vllm_api", "vllm_engine", "vllm_other")), ("harness (Harbor, s15)", ("harbor", "child")),
         ("tools (containers, docker CLI)", ("containers", "docker_cli")), ("Docker daemons", ("dockerd", "containerd")),
         ("measurement", ("measurement",))]
    first, tops, axs = None, [], []
    for j, v in enumerate(vs):
        a = fig.add_subplot(gs[0, j], sharey=first)
        first = first or a
        ser = [(lab, (lambda vv, ks=ks: np.nansum([pivot(R, vv, "cpu", "cores_by_group", k) for k in ks], axis=0)), 1)
               for lab, ks in G]
        _, top = stack(a, R, v, ser, legend=(j == 0))
        tops.append(top)
        axs.append(a)
        a.set_title(f"{VNAME[v]}: busy CPU cores by group (of 20)")
        if j:
            plt.setp(a.get_yticklabels(), visible=False)
    headroom(axs[0], max(tops))
    axs[0].set_ylabel("cores")
    c = fig.add_subplot(gs[1, :])
    order = [("vLLM", [9, 17, 18, 19]), ("probe", [8]), ("measure", [0, 11, 13]),
             ("agents", [1, 2, 3, 4, 5, 6, 7, 10, 12, 14, 15, 16])]
    cores = [k for _, ks in order for k in ks]
    M = np.array([[(get(r, "cpu", "per_core_busy") or {}).get(str(k), (get(r, "cpu", "per_core_busy") or {}).get(k, np.nan))
                   for k in cores] for r in R], dtype=float)
    from matplotlib.colors import LinearSegmentedColormap
    im = c.imshow(M, aspect="auto", cmap=LinearSegmentedColormap.from_list("seq", [SURFACE] + SEQ), vmin=0, vmax=1)
    c.grid(False)
    c.set_xticks(range(len(cores)))
    c.set_xticklabels([f"{k}" for k in cores])
    c.set_yticks(range(len(R)))
    c.set_yticklabels([f"{VNAME[r.get('variant', 'bf16')]}, N={r['n']}" for r in R])
    pos = 0
    for name, ks in order:
        c.annotate(name, (pos + len(ks) / 2 - 0.5, -0.5), xytext=(0, 6), textcoords="offset points", ha="center",
                   fontsize=9, color=INK2, annotation_clip=False)
        pos += len(ks)
        if pos < len(cores):
            c.axvline(pos - 0.5, color=SURFACE, lw=4)
    for i in range(M.shape[0]):
        for j in range(M.shape[1]):
            if np.isfinite(M[i, j]) and M[i, j] >= 0.3:
                c.text(j, i, f"{M[i, j] * 100:.0f}", ha="center", va="center", fontsize=8,
                       color="#ffffff" if M[i, j] > 0.55 else INK)
    c.set_title("Busy share of each core, % (shown from 30%)", pad=22)
    c._sp_fixed_pad = True
    c.set_xlabel("core")
    fig.colorbar(im, ax=c, fraction=0.02, pad=0.01).outline.set_visible(False)
    save(fig, "fig5_cpu.svg")


def fig6(R):
    fig, axes = plt.subplots(2, 2, figsize=(11, 7.6), gridspec_kw={"wspace": 0.25, "hspace": 0.7})
    by_model(axes[0, 0], R, ("cache", "all", "recomputed_tok_per_call", "mean"), "Prompt tokens recomputed per call",
             "tokens", fmt="{:,.0f}")
    by_model(axes[0, 1], R, ("cache", "all", "hit_rate"), "Prefix-cache hit rate", "% of prompt tokens", unit="%",
             scale=100, top=100 / 1.18 * 1.15)
    by_model(axes[1, 0], R, ("cache", "all", "kv_pinned_mean"), "KV pool pinned by running requests", "% of the pool",
             unit="%", scale=100)
    by_model(axes[1, 1], R, ("cache", "cache_loss", "share"), "Append calls that lost cached prefix",
             "% of checked calls", unit="%", scale=100)
    save(fig, "fig6_cache.svg", "The KV pool differs by model: 36 GiB for BF16, larger for the quantized models "
         "(the memory their weights free).")


def fig7(R):
    fig, (a, b, c) = plt.subplots(1, 3, figsize=(14, 3.9), gridspec_kw={"wspace": 0.3})
    by_model(a, R, ("gpu", "nvml", "power_w_mean"), "GPU power (GPU rail only)", "W", unit=" W")
    by_model(b, R, ("gpu", "energy_kj_per_1k_tokens"), "GPU energy per 1,000 output tokens", "kJ", unit=" kJ")
    by_model(c, R, ("memory", "gb_per_output_token"), "DRAM traffic per output token", "GB", unit=" GB")
    save(fig, "fig7_power.svg")


def timeseries(cell: str, n: int, label: str = ""):
    p = C.TABLES / f"series_{cell}.json"
    if not p.exists():
        return
    s = json.loads(p.read_text())
    rows = [(k, t, u) for k, t, u in (
        ("output_tok_s", "Output tokens/s", "tok/s"), ("kv_pinned", "KV pool pinned by running requests", "share"),
        ("prefill_time_share", "Share of time in steps that carry prefill", "share"),
        ("gpu_read_gbps", "GPU DRAM reads", "GB/s"), ("tensor_active_pct", "Tensor cores active", "%"),
        ("probe_ns", "Memory latency seen by the probe", "ns"), ("busy_cores", "Busy CPU cores", "cores"),
        ("power_w", "GPU power", "W")) if s.get(k) and any(v is not None for v in s[k])]
    if not rows:
        return
    fig, axes = plt.subplots(len(rows), 1, figsize=(11, 1.55 * len(rows) + 0.6), sharex=True)
    axes = np.atleast_1d(axes)
    per = s.get("period", [])
    for ax, (k, title, unit) in zip(axes, rows):
        y = np.array([np.nan if v is None else v for v in s[k]], dtype=float)
        x = np.arange(len(y)) * 0.5
        # the steady period, shaded: from when half of the active sessions have been trimmed
        for i, pp in enumerate(s.get("steady", [])[: len(y)]):
            if pp:
                ax.axvspan(i * 0.5, (i + 1) * 0.5, color="#f0efec", lw=0, zorder=0)
        ax.plot(x, y, color=SERIES[0], lw=2, solid_capstyle="round", solid_joinstyle="round", zorder=3)
        ax.set_ylim(bottom=0)
        ax.set_title(f"{title} ({unit})", fontsize=10)
    axes[-1].set_xlabel("minutes since the users started")
    fig.suptitle(f"{label + ', ' if label else ''}N = {n} ({cell}): 30-second means. Shaded: steady period", x=0.01, ha="left", fontsize=11,
                 fontweight="bold", color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    save(fig, f"ts_{cell}.svg")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", default="sp.json")
    ap.add_argument("--png", default=None, help="also write PNG copies into this folder")
    a = ap.parse_args()
    global PNG_DIR
    PNG_DIR = a.png
    if PNG_DIR:
        Path(PNG_DIR).mkdir(parents=True, exist_ok=True)
    R = json.loads((C.TABLES / a.json).read_text())
    R = sorted(R, key=lambda r: (r["n"], C.VARIANT_ORDER.index(r.get("variant", "bf16")), r["cell"]))
    for f in (fig1, fig2, fig3, fig4, fig5, fig6, fig7):
        f(R)
    for r in R:
        timeseries(r["cell"], r["n"], VNAME[r.get("variant", "bf16")])
    return 0


if __name__ == "__main__":
    sys.exit(main())
