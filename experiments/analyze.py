#!/usr/bin/env python3
"""Aggregate experiments/results/raw/*.json into summary tables + paper figures.

Runs on partial or complete sweeps. Outputs:
  experiments/results/summary.csv        one row per (scenario, strategy, failure_rate)
  experiments/results/summary.md         the same, human-readable
  experiments/results/figures/fig*.png   the four paper figures
"""
import json, glob, os, csv
from collections import defaultdict
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "results", "raw")
OUTD = os.path.join(HERE, "results")
FIGD = os.path.join(OUTD, "figures")
os.makedirs(FIGD, exist_ok=True)

# ── style: validated categorical palette (dataviz reference, light mode) ──
STRATS = ["none", "fixed", "exp", "full_jitter", "decorrelated"]
LABELS = {"none": "none", "fixed": "fixed", "exp": "exponential",
          "full_jitter": "full jitter", "decorrelated": "decorrelated"}
COLORS = dict(zip(STRATS, ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]))
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "text.color": INK, "axes.edgecolor": GRID, "axes.labelcolor": INK2,
    "xtick.color": INK2, "ytick.color": INK2, "axes.grid": True,
    "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True,
    "axes.spines.top": False, "axes.spines.right": False,
    "font.size": 10, "axes.titlesize": 11, "figure.titlesize": 13,
})

def pct(x, q):
    return float(np.percentile(x, q)) if len(x) else float("nan")

runs = []
for f in sorted(glob.glob(os.path.join(RAW, "*.json"))):
    with open(f) as fh:
        runs.append(json.load(fh))
print(f"loaded {len(runs)} runs")

steady = defaultdict(list)   # (strategy, failure) -> [run]
outage = defaultdict(list)   # strategy -> [run]
for r in runs:
    c = r["config"]
    if c["recover_after_ms"] > 0:
        outage[c["strategy"]].append(r)
    else:
        steady[(c["strategy"], c["failure_rate"])].append(r)

rows = []
for (s, f), rs in sorted(steady.items()):
    n = rs[0]["config"]["n_reminders"]
    del_pct = [100.0 * x["result"]["sent"] / n for x in rs]
    lats = np.concatenate([np.array(x["latencies_s"], dtype=float) for x in rs]) if rs else np.array([])
    calls = [x["metrics"]["provider"]["calls"] / max(1, x["result"]["sent"]) for x in rs]
    rows.append({
        "scenario": "steady", "strategy": s, "failure_rate": f, "repeats": len(rs),
        "delivered_pct_mean": round(float(np.mean(del_pct)), 2),
        "delivered_pct_sd": round(float(np.std(del_pct)), 2),
        "dead_mean": round(float(np.mean([x["result"]["dead"] for x in rs])), 2),
        "retries_per_100_mean": round(float(np.mean([x["result"]["retries"] for x in rs])), 1),
        "provider_calls_per_delivered": round(float(np.mean(calls)), 3),
        "latency_p50_s": round(pct(lats, 50), 3),
        "latency_p95_s": round(pct(lats, 95), 3),
        "latency_p99_s": round(pct(lats, 99), 3),
    })
for s, rs in sorted(outage.items()):
    n = rs[0]["config"]["n_reminders"]
    del_pct = [100.0 * x["result"]["sent"] / n for x in rs]
    lats = np.concatenate([np.array(x["latencies_s"], dtype=float) for x in rs]) if rs else np.array([])
    rows.append({
        "scenario": "outage60s", "strategy": s, "failure_rate": "",
        "repeats": len(rs),
        "delivered_pct_mean": round(float(np.mean(del_pct)), 2),
        "delivered_pct_sd": round(float(np.std(del_pct)), 2),
        "dead_mean": round(float(np.mean([x["result"]["dead"] for x in rs])), 2),
        "retries_per_100_mean": round(float(np.mean([x["result"]["retries"] for x in rs])), 1),
        "provider_calls_per_delivered": "",
        "latency_p50_s": round(pct(lats, 50), 3),
        "latency_p95_s": round(pct(lats, 95), 3),
        "latency_p99_s": round(pct(lats, 99), 3),
    })

if rows:
    with open(os.path.join(OUTD, "summary.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    hdr = list(rows[0].keys())
    with open(os.path.join(OUTD, "summary.md"), "w") as fh:
        fh.write("| " + " | ".join(hdr) + " |\n")
        fh.write("|" + "---|" * len(hdr) + "\n")
        for r in rows:
            fh.write("| " + " | ".join(str(r[k]) for k in hdr) + " |\n")
    print(f"wrote summary.csv / summary.md ({len(rows)} rows)")

def strat_iter():
    return [s for s in STRATS if any(k[0] == s for k in steady)]

# ── fig 1: steady-state delivery rate ──
if steady:
    fig, ax = plt.subplots(figsize=(7, 4.2))
    for s in strat_iter():
        xs = sorted(f for (ss, f) in steady if ss == s)
        ys = [np.mean([100.0 * r["result"]["sent"] / r["config"]["n_reminders"]
                       for r in steady[(s, f)]]) for f in xs]
        ax.plot(xs, ys, "-o", color=COLORS[s], lw=2, ms=5, label=LABELS[s])
        ax.annotate(LABELS[s], (xs[-1], ys[-1]), xytext=(6, 0),
                    textcoords="offset points", color=COLORS[s], fontsize=9, va="center")
    lo = min(min(100.0 * r["result"]["sent"] / r["config"]["n_reminders"]
                 for rs in steady.values() for r in rs), 99)
    ax.set_xlim(0.05, 0.62); ax.set_ylim(max(0, lo - 2), 100.6)
    ax.set_xticks([0.1, 0.3, 0.5]); ax.set_xlabel("provider failure rate")
    ax.set_ylabel("delivered (%)")
    ax.set_title("Steady transient failure: every retry policy recovers delivery", loc="left")
    ax.legend(frameon=False, fontsize=8, loc="lower left")
    fig.tight_layout(); fig.savefig(os.path.join(FIGD, "fig1_delivery_steady.png"), dpi=200)
    plt.close(fig); print("fig1 done")

# ── fig 2: latency dumbbells p50→p95, one panel per failure rate ──
if steady:
    fails = sorted({f for (_, f) in steady})
    fig, axes = plt.subplots(1, len(fails), figsize=(3.1 * len(fails), 4.0), sharey=True)
    axes = np.atleast_1d(axes)
    order = [s for s in STRATS if any(k[0] == s for k in steady)][::-1]
    allp95 = [pct(np.concatenate([np.array(r["latencies_s"]) for r in steady[(s, f)]]), 95)
              for (s, f) in steady]
    logx = (max(allp95) / max(1e-3, min(p for p in allp95 if p > 0))) > 20
    for ax, f in zip(axes, fails):
        for i, s in enumerate(order):
            if (s, f) not in steady: continue
            lats = np.concatenate([np.array(r["latencies_s"]) for r in steady[(s, f)]])
            p50, p95 = pct(lats, 50), pct(lats, 95)
            ax.plot([p50, p95], [i, i], color=COLORS[s], lw=2, alpha=0.55, zorder=2)
            ax.plot([p50], [i], "o", color=COLORS[s], ms=7, zorder=3)
            ax.plot([p95], [i], "o", color=COLORS[s], ms=7, mfc=SURFACE, mew=2, zorder=3)
        ax.set_yticks(range(len(order))); ax.set_yticklabels([LABELS[s] for s in order])
        if logx: ax.set_xscale("log")
        ax.set_title(f"failure = {f:.0%}", loc="left", fontsize=10, color=INK2)
        ax.set_xlabel("delivery latency (s)")
    fig.suptitle("Latency cost of waiting: ● p50 → ○ p95 by backoff strategy", x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(os.path.join(FIGD, "fig2_latency.png"), dpi=200); plt.close(fig)
    print("fig2 done")

# ── fig 3: outage survival ──
if outage:
    fig, ax = plt.subplots(figsize=(7, 3.8))
    order = [s for s in STRATS if s in outage]
    vals = [np.mean([100.0 * r["result"]["sent"] / r["config"]["n_reminders"]
                     for r in outage[s]]) for s in order]
    bars = ax.bar(range(len(order)), vals, width=0.62,
                  color=[COLORS[s] for s in order], edgecolor=SURFACE, linewidth=2)
    for i, v in enumerate(vals):
        ax.annotate(f"{v:.0f}%", (i, v), xytext=(0, 4), textcoords="offset points",
                    ha="center", fontsize=10, color=INK)
    ax.set_xticks(range(len(order))); ax.set_xticklabels([LABELS[s] for s in order])
    ax.set_ylim(0, 108); ax.set_ylabel("delivered (%)")
    ax.set_title("60 s provider outage: survival by backoff strategy (8 attempts)", loc="left")
    fig.tight_layout(); fig.savefig(os.path.join(FIGD, "fig3_outage.png"), dpi=200)
    plt.close(fig); print("fig3 done")

# ── fig 4: retry herd around recovery (small multiples) ──
if outage:
    order = [s for s in STRATS if s in outage]
    fig, axes = plt.subplots(len(order), 1, figsize=(7, 1.35 * len(order) + 1.2),
                             sharex=True, sharey=True)
    axes = np.atleast_1d(axes)
    for ax, s in zip(axes, order):
        binned = defaultdict(float); rec_marks = []
        for r in outage[s]:
            atts = r["provider_attempts"]
            if not atts: continue
            t0 = min(a["t"] for a in atts)
            for a in atts:
                binned[int((a["t"] - t0) / 1000)] += 1.0 / len(outage[s])
            ok = [a["t"] for a in atts if a["ok"]]
            if ok: rec_marks.append((min(ok) - t0) / 1000)
        if binned:
            xs = np.arange(0, max(binned) + 1)
            ax.fill_between(xs, [binned.get(int(x), 0) for x in xs],
                            color=COLORS[s], alpha=0.28, lw=0)
            ax.plot(xs, [binned.get(int(x), 0) for x in xs], color=COLORS[s], lw=1.6)
        if rec_marks:
            ax.axvline(float(np.median(rec_marks)), color=INK2, lw=1, ls="--", alpha=0.8)
        ax.annotate(LABELS[s], (0.99, 0.82), xycoords="axes fraction",
                    ha="right", fontsize=10, color=COLORS[s])
        ax.grid(axis="x", visible=False)
    axes[-1].set_xlabel("seconds since first attempt   (dashed = first success ≈ recovery)")
    axes[len(axes) // 2].set_ylabel("provider attempts / s (mean per run)")
    fig.suptitle("The herd: retry arrivals through a provider outage", x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(os.path.join(FIGD, "fig4_herd.png"), dpi=200); plt.close(fig)
    print("fig4 done")

print("analysis complete")
