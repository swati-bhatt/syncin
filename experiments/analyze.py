#!/usr/bin/env python3
"""Aggregate experiments/results/raw/*.json into summary tables, derived numbers and
the paper figures. Every number the write-ups quote comes from one of these outputs.

Outputs:
  experiments/results/summary.csv / summary.md    one row per (scenario, strategy, failure_rate)
  experiments/results/derived.json / derived.md   injection validity, outage timing and
                                                  recovery load per run, the effective outage
                                                  window W, the coverage model (a Monte Carlo
                                                  of computeBackoff, plus the reach of the
                                                  shipped 5-attempt default), steady-state
                                                  comparisons
  experiments/results/figures/fig*.png            the five figures (THEME=dark writes the
                                                  same figures to figures-dark/)

Run:  python3 experiments/analyze.py && THEME=dark python3 experiments/analyze.py
Needs numpy and matplotlib. Never touches results/raw/.
"""
import json, glob, os, csv, math
from collections import defaultdict, Counter
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "results", "raw")
OUTD = os.path.join(HERE, "results")
# THEME=dark renders the same figures stepped for a dark surface (used by the
# web version of the report, which follows the reader's theme).
THEME = os.environ.get("THEME", "light")
FIGD = os.path.join(OUTD, "figures" if THEME == "light" else "figures-dark")
os.makedirs(FIGD, exist_ok=True)

# ── style: validated categorical palette (dataviz reference, light mode) ──
STRATS = ["none", "fixed", "exp", "full_jitter", "equal_jitter", "decorrelated"]
LABELS = {"none": "none", "fixed": "fixed", "exp": "exponential",
          "full_jitter": "full jitter", "equal_jitter": "equal jitter",
          "decorrelated": "decorrelated"}
if THEME == "dark":
    COLORS = dict(zip(STRATS, ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#4fb04f"]))
    SURFACE, INK, INK2, GRID = "#1a1a19", "#ffffff", "#c3c2b7", "#33332f"
    BAND_ALPHA = 0.20
else:
    COLORS = dict(zip(STRATS, ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]))
    SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
    BAND_ALPHA = 0.14
plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "text.color": INK, "axes.edgecolor": GRID, "axes.labelcolor": INK2,
    "xtick.color": INK2, "ytick.color": INK2, "axes.grid": True,
    "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True,
    "axes.spines.top": False, "axes.spines.right": False,
    "font.size": 10, "axes.titlesize": 11, "figure.titlesize": 13,
})

# ── coverage model constants: mirror computeBackoff in src/queues/reminder.worker.ts ──
# (env defaults REMINDER_BACKOFF_BASE_MS / REMINDER_BACKOFF_CAP_MS; run-one.sh does not
# override them). n = attemptsMade, 1-based; an A-attempt budget has A-1 waits.
BASE_MS, CAP_MS = 1000, 15000
OUTAGE_ATTEMPTS = 8            # sweep.sh: MAX_ATTEMPTS=8 for the outage runs
DEFAULT_ATTEMPTS = 5           # env.ts REMINDER_MAX_ATTEMPTS default (the shipped budget; not swept)
MC_SEED, MC_TRIALS = 20261005, 400_000
JITTERED = ("full_jitter", "equal_jitter", "decorrelated")
WINDOW_MS = 1000               # recovery-load window: any 1-s sliding window


def check_env_defaults():
    """Fail loudly if the constants above drift from the defaults in src/config/env.ts."""
    import re
    path = os.path.join(HERE, "..", "src", "config", "env.ts")
    if not os.path.exists(path):
        print("warning: src/config/env.ts not found; default-budget constants not checked")
        return
    with open(path) as fh:
        src = fh.read()
    for name, want in (("REMINDER_MAX_ATTEMPTS", DEFAULT_ATTEMPTS),
                       ("REMINDER_BACKOFF_BASE_MS", BASE_MS),
                       ("REMINDER_BACKOFF_CAP_MS", CAP_MS)):
        m = re.search(rf"process\.env\.{name}\s*\?\?\s*(\d+)", src)
        assert m and int(m.group(1)) == want, f"env.ts default for {name} != {want}"
    m = re.search(r"process\.env\.REMINDER_BACKOFF_STRATEGY\s*\?\?\s*'(\w+)'", src)
    assert m and m.group(1) == "equal_jitter", "env.ts default strategy is not equal_jitter"


check_env_defaults()


def pct(x, q):
    return float(np.percentile(x, q)) if len(x) else float("nan")


def sample_sd(xs):
    """Sample standard deviation (ddof=1); 0.0 for a single value."""
    return float(np.std(xs, ddof=1)) if len(xs) > 1 else 0.0


def rnd(x, k=3):
    if x is None:
        return None
    x = float(x)
    return None if math.isnan(x) else round(x, k)


runs = []
for f in sorted(glob.glob(os.path.join(RAW, "*.json"))):
    with open(f) as fh:
        d = json.load(fh)
    d["_file"] = os.path.basename(f)
    runs.append(d)
print(f"loaded {len(runs)} runs")

steady = defaultdict(list)   # (strategy, failure) -> [run]
outage = defaultdict(list)   # strategy -> [run]
for r in runs:
    c = r["config"]
    if c["recover_after_ms"] > 0:
        outage[c["strategy"]].append(r)
    else:
        steady[(c["strategy"], c["failure_rate"])].append(r)
for rs in list(steady.values()) + list(outage.values()):
    rs.sort(key=lambda x: x["config"]["repeat"])


def delivered_pct(r):
    return 100.0 * r["result"]["sent"] / r["config"]["n_reminders"]


# ══════════════════════════════ summary table ══════════════════════════════
# failed_attempts_per_100_mean: result.retries is sum(reminder_jobs.attempts), which the
# worker writes only on failure, so it counts FAILED provider attempts (== provider
# metrics "failed" in every run). True retries are failed - dead.
rows = []
for (s, f), rs in sorted(steady.items()):
    n = rs[0]["config"]["n_reminders"]
    del_pct = [delivered_pct(x) for x in rs]
    lats = np.concatenate([np.array(x["latencies_s"], dtype=float) for x in rs]) if rs else np.array([])
    calls = [x["metrics"]["provider"]["calls"] / max(1, x["result"]["sent"]) for x in rs]
    rows.append({
        "scenario": "steady", "strategy": s, "failure_rate": f, "repeats": len(rs),
        "delivered_pct_mean": round(float(np.mean(del_pct)), 2),
        "delivered_pct_sd": round(sample_sd(del_pct), 2),
        "dead_mean": round(float(np.mean([x["result"]["dead"] for x in rs])), 2),
        "failed_attempts_per_100_mean": round(float(np.mean(
            [100.0 * x["result"]["retries"] / n for x in rs])), 1),
        "provider_calls_per_delivered": round(float(np.mean(calls)), 3),
        "latency_p50_s": round(pct(lats, 50), 3),
        "latency_p95_s": round(pct(lats, 95), 3),
        "latency_p99_s": round(pct(lats, 99), 3),
    })
for s, rs in sorted(outage.items()):
    n = rs[0]["config"]["n_reminders"]
    del_pct = [delivered_pct(x) for x in rs]
    lats = np.concatenate([np.array(x["latencies_s"], dtype=float) for x in rs]) if rs else np.array([])
    rows.append({
        "scenario": "outage", "strategy": s, "failure_rate": "",
        "repeats": len(rs),
        "delivered_pct_mean": round(float(np.mean(del_pct)), 2),
        "delivered_pct_sd": round(sample_sd(del_pct), 2),
        "dead_mean": round(float(np.mean([x["result"]["dead"] for x in rs])), 2),
        "failed_attempts_per_100_mean": round(float(np.mean(
            [100.0 * x["result"]["retries"] / n for x in rs])), 1),
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
        fh.write(
            "\nColumns: `delivered_pct_sd` is the sample standard deviation (ddof = 1) over "
            "the repeats. `failed_attempts_per_100_mean` counts failed provider attempts per "
            "100 reminders (not retries: a dead-lettered job's last failure is not followed "
            "by a retry, so retries = failed attempts - dead). `outage` rows: the mock fails "
            "every call until 60 s of process uptime; the effective window the cohort faced "
            "is in derived.md. Latency percentiles pool all delivered reminders of the cell. "
            "Generated by experiments/analyze.py.\n")
    print(f"wrote summary.csv / summary.md ({len(rows)} rows)")


# ══════════════════════════════ derived numbers ══════════════════════════════
def sliding_peak(ts):
    """Most attempts in any window [t, t + 1 s) over sorted ms timestamps."""
    ts = np.asarray(ts, dtype=np.int64)
    if ts.size == 0:
        return 0
    return int((np.searchsorted(ts, ts + WINDOW_MS, side="left") - np.arange(ts.size)).max())


derived = {
    "generated_by": "experiments/analyze.py",
    "definitions": {
        "t0": "the cohort's first provider attempt in a run (all times below are seconds after t0 unless named otherwise)",
        "recovery_bracket": "[last failed attempt, first successful attempt] after t0; recovery happened inside it. Used only for the jittered strategies, whose attempts are dense around recovery",
        "W": "effective outage window: time from the cohort's first attempt to provider recovery",
        "peak_recovery_load": "the most provider attempts in any 1-second sliding window at or after the cohort's first successful delivery",
        "peak_per_survivor": "peak_recovery_load / delivered",
        "calls_before_first_success": "provider calls before the run's first successful delivery (during the outage); all calls if nothing was delivered",
        "peak_1s_before_first_success": "the most provider attempts in any 1-second sliding window before the first successful delivery",
        "failed_attempts": "failed provider calls; equals result.retries in every run. retries = calls - reminders = failed - dead",
        "binomial_se": "sqrt(f(1-f)/calls) at the configured f",
    },
}

# ── checks on the raw data ──
derived["checks"] = {
    "runs_total": len(runs),
    "runs_steady": sum(len(v) for v in steady.values()),
    "runs_outage": sum(len(v) for v in outage.values()),
    "retries_field_equals_failed_calls_all_runs": all(
        r["result"]["retries"] == sum(not a["ok"] for a in r["provider_attempts"]) for r in runs),
    "provider_calls_equals_attempt_log_all_runs": all(
        r["metrics"]["provider"]["calls"] == len(r["provider_attempts"]) for r in runs),
    "calls_minus_reminders_equals_failed_minus_dead_all_runs": all(
        len(r["provider_attempts"]) - r["config"]["n_reminders"]
        == r["result"]["retries"] - r["result"]["dead"] for r in runs),
}
for r in runs:
    if r["config"]["recover_after_ms"] > 0:
        assert r["config"]["max_attempts"] == OUTAGE_ATTEMPTS, r["_file"]

# ── (a) injection validity, pooled over every steady run ──
inj = {"configured": {}}
total_steady_calls = 0
for f in sorted({f for (_, f) in steady}):
    rs = [r for (s, ff), v in steady.items() if ff == f for r in v]
    calls = sum(len(r["provider_attempts"]) for r in rs)
    failed = sum(sum(not a["ok"] for a in r["provider_attempts"]) for r in rs)
    obs = failed / calls
    se = math.sqrt(f * (1 - f) / calls)
    total_steady_calls += calls
    inj["configured"][str(f)] = {
        "runs": len(rs), "calls": calls, "failed": failed,
        # 3 dp is rounded from the exact ratio (not from the 4 dp value) to avoid double rounding
        "observed_rate": rnd(obs, 4), "observed_rate_3dp": rnd(obs, 3),
        "binomial_se": rnd(se, 4), "z": rnd((obs - f) / se, 2),
    }
inj["runs_pooled"] = sum(v["runs"] for v in inj["configured"].values())
inj["total_steady_calls"] = total_steady_calls
inj["max_abs_z"] = max(abs(v["z"]) for v in inj["configured"].values())
derived["injection_validity"] = inj

# ── lead times: due instant vs create burst ──
st_lead = [(min(a["t"] for a in r["provider_attempts"]) - r["result"]["t_create_ms"]) / 1000
           for v in steady.values() for r in v]
ou_lead = [(min(a["t"] for a in r["provider_attempts"]) - r["result"]["t_create_ms"]) / 1000
           for v in outage.values() for r in v]
derived["lead"] = {
    "note": "first provider attempt after the create burst (t_create); run-one.sh LEAD_S = 40 (steady), sweep.sh LEAD_S = 25 (outage)",
    "steady_first_attempt_after_create_s": [rnd(min(st_lead)), rnd(max(st_lead))],
    "outage_first_attempt_after_create_s": [rnd(min(ou_lead)), rnd(max(ou_lead))],
}

# ── (b) per outage run ──
outage_runs = []
for s in [s for s in STRATS if s in outage]:
    for r in outage[s]:
        c, res = r["config"], r["result"]
        A = sorted(r["provider_attempts"], key=lambda a: a["t"])
        ts = np.array([a["t"] for a in A], dtype=np.int64)
        t0 = int(ts[0])
        oks = [a["t"] for a in A if a["ok"]]
        fs = min(oks) if oks else None
        rec = {
            "strategy": s, "repeat": c["repeat"], "file": r["_file"],
            "t0_ms": t0, "t0_after_create_s": rnd((t0 - res["t_create_ms"]) / 1000),
            "delivered": res["sent"], "dead": res["dead"], "calls": int(ts.size),
            "failed_attempts": int(sum(not a["ok"] for a in A)),
            "retries": int(ts.size) - c["n_reminders"],
        }
        pre = ts if fs is None else ts[ts < fs]
        rec["calls_before_first_success"] = int(pre.size)
        rec["peak_1s_before_first_success"] = sliding_peak(pre)
        if fs is None:
            rec.update(first_success_s=None, last_failure_before_success_s=None,
                       peak_recovery_load=None, peak_per_survivor=None)
        else:
            lf = max(a["t"] for a in A if not a["ok"] and a["t"] < fs)
            rec["last_failure_before_success_s"] = rnd((lf - t0) / 1000)
            rec["first_success_s"] = rnd((fs - t0) / 1000)
            if s in JITTERED:
                rec["recovery_bracket_s"] = [rnd((lf - t0) / 1000), rnd((fs - t0) / 1000)]
                rec["recovery_bracket_mid_s"] = rnd(((lf + fs) / 2 - t0) / 1000)
            peak = sliding_peak(ts[ts >= fs])
            rec["peak_recovery_load"] = peak
            rec["peak_per_survivor"] = rnd(peak / res["sent"])
        if s == "exp":
            per = defaultdict(list)
            for a in A:
                per[a["key"]].append(a)
            idx = Counter()
            succ = []
            for key, atts in per.items():
                for i, a in enumerate(atts, start=1):
                    if a["ok"]:
                        idx[i] += 1; succ.append((a["t"] - t0) / 1000)
            rec["success_attempt_index_counts"] = {str(k): v for k, v in sorted(idx.items())}
            rec["success_time_s"] = {"min": rnd(min(succ)), "median": rnd(np.median(succ)),
                                     "max": rnd(max(succ)), "spread": rnd(max(succ) - min(succ))}
        outage_runs.append(rec)

# ── (c) effective outage window W from the jittered runs' brackets ──
br = [x for x in outage_runs if x["strategy"] in JITTERED and x.get("recovery_bracket_s")]
W_LO = min(x["recovery_bracket_s"][0] for x in br)
W_HI = max(x["recovery_bracket_s"][1] for x in br)
W_MID = rnd(float(np.median([x["recovery_bracket_mid_s"] for x in br])))
RECOVER_S = outage[next(iter(outage))][0]["config"]["recover_after_ms"] / 1000 if outage else 60.0
derived["W"] = {
    "source_runs": len(br),
    "bracket_overall_s": [W_LO, W_HI],
    "bracket_midpoints_s": [x["recovery_bracket_mid_s"] for x in br],
    "W_mid_s": W_MID,
    "configured_recovery_uptime_s": RECOVER_S,
    "uptime_at_first_attempt_s": {"at_W_mid": rnd(RECOVER_S - W_MID),
                                  "range": [rnd(RECOVER_S - W_HI), rnd(RECOVER_S - W_LO)]},
    "note": "the mock fails every call until RECOVER_S of process uptime; the cohort's first attempt comes that much earlier than recovery, so it faces W, not RECOVER_S",
}


# ── (d) coverage model: Monte Carlo of computeBackoff ──
def mc_wait_sums(strategy, attempts, rng, trials):
    """Sum (ms) of the attempts-1 waits computeBackoff returns, one entry per trial."""
    total = np.zeros(trials, dtype=np.int64)
    prev = np.full(trials, float(BASE_MS))          # decorrelated: lastDelay ?? base
    for n in range(1, attempts):
        e = min(CAP_MS, BASE_MS * 2 ** (n - 1))
        if strategy == "none":
            continue
        if strategy == "fixed":
            w = np.full(trials, BASE_MS, dtype=np.int64)
        elif strategy == "exp":
            w = np.full(trials, e, dtype=np.int64)
        elif strategy == "equal_jitter":
            u = rng.random(trials)
            w = np.floor(e / 2 + u * (e / 2)).astype(np.int64)
        elif strategy == "decorrelated":
            u = rng.random(trials)
            d = np.minimum(CAP_MS, BASE_MS + u * np.maximum(0.0, prev * 3 - BASE_MS))
            prev = d
            w = np.floor(d).astype(np.int64)
        else:                                       # full_jitter (and the default branch)
            u = rng.random(trials)
            w = np.floor(u * e).astype(np.int64)
        total += w
    return np.sort(total)


def survival_pct(sorted_sums, W_s):
    """% of trials whose waits sum to >= W (the last attempt lands at or after recovery)."""
    k = np.searchsorted(sorted_sums, W_s * 1000.0, side="left")
    return 100.0 * (sorted_sums.size - k) / sorted_sums.size


rng = np.random.default_rng(MC_SEED)
SUMS = {s: mc_wait_sums(s, OUTAGE_ATTEMPTS, rng, MC_TRIALS) for s in STRATS}
SUMS_FJ10 = mc_wait_sums("full_jitter", 10, rng, MC_TRIALS)

model = {
    "seed": MC_SEED, "trials": MC_TRIALS, "base_ms": BASE_MS, "cap_ms": CAP_MS,
    "attempts": OUTAGE_ATTEMPTS, "waits": OUTAGE_ATTEMPTS - 1,
    "rule": "a job survives iff the sum of its waits >= W (its last attempt lands at or after recovery)",
    "assumptions": [
        "waits only: replicates computeBackoff exactly (floors included)",
        "ignores per-attempt processing time and worker queueing (concurrency 5), which delay attempts slightly and so help survival a little",
        "W taken from the jittered runs' recovery brackets",
        "every job starts its first attempt at t0 (the cohort is synchronized)",
    ],
    "per_strategy": {},
}
for s in STRATS:
    S = SUMS[s]
    p_mid = survival_pct(S, W_MID)
    p_lo, p_hi = survival_pct(S, W_LO), survival_pct(S, W_HI)
    entry = {
        "predicted_survival_pct_at_W_mid": rnd(p_mid, 2),
        "predicted_survival_pct_at_bracket_lo": rnd(p_lo, 2),
        "predicted_survival_pct_at_bracket_hi": rnd(p_hi, 2),
        "predicted_survival_pct_at_60s": rnd(survival_pct(S, 60.0), 2),
        "predicted_survival_pct_at_34s_audit_check": rnd(survival_pct(S, 34.0), 2),
        "wait_sum_s": {"min": rnd(S[0] / 1000), "mean": rnd(S.mean() / 1000),
                       "max": rnd(S[-1] / 1000), "sd": rnd(S.std(ddof=1) / 1000)},
    }
    if s in outage:
        obs_runs = [delivered_pct(r) for r in outage[s]]
        n_tot = sum(r["config"]["n_reminders"] for r in outage[s])
        obs = 100.0 * sum(r["result"]["sent"] for r in outage[s]) / n_tot
        p = p_mid / 100
        se_pp = 100 * math.sqrt(p * (1 - p) / n_tot)
        lo_pred, hi_pred = min(p_lo, p_hi), max(p_lo, p_hi)
        entry.update({
            "observed_pct_mean": rnd(obs, 2),
            "observed_pct_runs": [rnd(x, 2) for x in obs_runs],
            "diff_pp_observed_minus_W_mid": rnd(obs - p_mid, 2),
            "binomial_se_pp_at_W_mid": rnd(se_pp, 2),
            "n_jobs_pooled": n_tot,
            "agrees": bool(lo_pred - 2 * se_pp - 1e-9 <= obs <= hi_pred + 2 * se_pp + 1e-9),
        })
    model["per_strategy"][s] = entry
model["agreement_rule"] = ("agrees = pooled observed delivery lies within the model's prediction "
                           "over the W bracket, widened by 2 binomial SE at n = 300")
model["all_strategies_agree"] = all(v.get("agrees", True) for v in model["per_strategy"].values())

# equal jitter: floor, reach, random half
floors = [math.floor(min(CAP_MS, BASE_MS * 2 ** (n - 1)) / 2) for n in range(1, OUTAGE_ATTEMPTS)]
max_possible = [min(CAP_MS, BASE_MS * 2 ** (n - 1)) - 1 for n in range(1, OUTAGE_ATTEMPTS)]
need_att, acc = None, 0
for n in range(1, 100):
    acc += math.floor(min(CAP_MS, BASE_MS * 2 ** (n - 1)) / 2)
    if acc >= 60000:
        need_att = n + 1   # n waits -> n+1 attempts
        break
SEJ = SUMS["equal_jitter"]
model["equal_jitter"] = {
    "floor_sum_s": rnd(sum(floors) / 1000),
    "max_possible_sum_s": rnd(sum(max_possible) / 1000),
    "mc_sum_min_s": rnd(SEJ[0] / 1000), "mc_sum_mean_s": rnd(SEJ.mean() / 1000),
    "mc_sum_max_s": rnd(SEJ[-1] / 1000),
    "random_half_needed_at_W_mid_s": rnd(W_MID - sum(floors) / 1000),
    "random_half_needed_over_bracket_s": [rnd(W_LO - sum(floors) / 1000),
                                          rnd(W_HI - sum(floors) / 1000)],
    "min_attempts_for_floor_sum_60s": need_att,
    "min_waits_for_floor_sum_60s": need_att - 1,
}
# shipped default budget (env.ts: 5 attempts, base 1 s, cap 15 s): arithmetic on the same
# computeBackoff replica. Not measured: every outage run used OUTAGE_ATTEMPTS. The Monte
# Carlo draws come last from rng, so every sum above is unchanged by this block.
dflt_exp = [min(CAP_MS, BASE_MS * 2 ** (n - 1)) for n in range(1, DEFAULT_ATTEMPTS)]
SEJ5 = mc_wait_sums("equal_jitter", DEFAULT_ATTEMPTS, rng, MC_TRIALS)
model["default_budget"] = {
    "source": "src/config/env.ts defaults: REMINDER_MAX_ATTEMPTS 5, REMINDER_BACKOFF_BASE_MS 1000, REMINDER_BACKOFF_CAP_MS 15000, strategy equal_jitter",
    "attempts": DEFAULT_ATTEMPTS, "waits": DEFAULT_ATTEMPTS - 1,
    "equal_jitter_floor_sum_s": rnd(sum(math.floor(e / 2) for e in dflt_exp) / 1000),
    "equal_jitter_max_possible_sum_s": rnd(sum(e - 1 for e in dflt_exp) / 1000),
    "equal_jitter_mc_sum_min_s": rnd(SEJ5[0] / 1000), "equal_jitter_mc_sum_mean_s": rnd(SEJ5.mean() / 1000),
    "equal_jitter_mc_sum_max_s": rnd(SEJ5[-1] / 1000),
    "exp_wait_sum_s": rnd(sum(dflt_exp) / 1000),
    "note": "reach of the shipped default: equal jitter's waits sum to at least the floor sum and to just under the exponential schedule's sum; no run used this budget in an outage",
}
model["full_jitter"] = {
    "sum_mean_s_8_attempts": rnd(SUMS["full_jitter"].mean() / 1000),
    "sum_sd_s_8_attempts": rnd(SUMS["full_jitter"].std(ddof=1) / 1000),
    "expected_sum_s_10_attempts_analytic": rnd(sum(min(CAP_MS, BASE_MS * 2 ** (n - 1)) for n in range(1, 10)) / 2 / 1000),
    "sum_mean_s_10_attempts": rnd(SUMS_FJ10.mean() / 1000),
    "predicted_survival_pct_10_attempts_at_W_mid": rnd(survival_pct(SUMS_FJ10, W_MID), 2),
    "predicted_survival_pct_10_attempts_at_bracket_lo": rnd(survival_pct(SUMS_FJ10, W_LO), 2),
    "predicted_survival_pct_10_attempts_at_bracket_hi": rnd(survival_pct(SUMS_FJ10, W_HI), 2),
    "predicted_survival_pct_10_attempts_at_60s": rnd(survival_pct(SUMS_FJ10, 60.0), 2),
    "note": "10 attempts gives full jitter the same expected total wait as equal jitter at 8",
}
exp_cum = np.cumsum([0] + [min(CAP_MS, BASE_MS * 2 ** (n - 1)) for n in range(1, OUTAGE_ATTEMPTS)])
model["exp"] = {
    "attempt_time_s_by_attempt": {str(i + 1): rnd(v / 1000) for i, v in enumerate(exp_cum)},
    "wait_sum_s": rnd(exp_cum[-1] / 1000),
    "at_60s": "boundary: the 8th attempt lands exactly at 60 s of waiting, so W = 60 s is the largest window it covers",
}
grid = list(range(0, 91, 5))
model["survival_grid_8_attempts"] = {
    "W_s": grid,
    **{s: [rnd(survival_pct(SUMS[s], W), 2) for W in grid] for s in STRATS},
}
fine = np.round(np.arange(0, 90.001, 0.1), 1)
over = [W for W in fine if survival_pct(SUMS["decorrelated"], W) > survival_pct(SUMS["equal_jitter"], W)
        and W > 1]
model["decorrelated_exceeds_equal_jitter_from_W_s"] = rnd(over[0], 1) if over else None
derived["model"] = model

# per-run model check for the jittered runs (their own bracket midpoint)
for x in outage_runs:
    if x["strategy"] in JITTERED and x.get("recovery_bracket_mid_s") is not None:
        x["predicted_survival_pct_at_own_mid"] = rnd(
            survival_pct(SUMS[x["strategy"]], x["recovery_bracket_mid_s"]), 2)

derived["outage_runs"] = outage_runs

# exp wave timing
exp_runs = [x for x in outage_runs if x["strategy"] == "exp"]
if exp_runs:
    all_idx = Counter()
    for x in exp_runs:
        for k, v in x["success_attempt_index_counts"].items():
            all_idx[k] += v
    derived["exp_wave"] = {
        "success_attempt_index_counts_all_runs": dict(sorted(all_idx.items())),
        "first_success_s": [x["first_success_s"] for x in exp_runs],
        "success_spread_s": [x["success_time_s"]["spread"] for x in exp_runs],
        "last_failure_before_success_s": [x["last_failure_before_success_s"] for x in exp_runs],
        "first_success_after_W_mid_s": [rnd(x["first_success_s"] - W_MID) for x in exp_runs],
        "first_success_after_recovery_range_s": [
            rnd(min(x["first_success_s"] for x in exp_runs) - W_HI),
            rnd(max(x["first_success_s"] for x in exp_runs) - W_LO)],
        "t0_after_create_s": [x["t0_after_create_s"] for x in exp_runs],
    }

# ── (f) recovery load and outage-phase load per strategy ──
load = {}
for s in [s for s in STRATS if s in outage]:
    xs = [x for x in outage_runs if x["strategy"] == s]
    ent = {
        "delivered": [x["delivered"] for x in xs],
        "calls": [x["calls"] for x in xs],
        "calls_before_first_success": [x["calls_before_first_success"] for x in xs],
        "calls_before_first_success_mean": rnd(np.mean([x["calls_before_first_success"] for x in xs]), 1),
        "peak_1s_before_first_success": [x["peak_1s_before_first_success"] for x in xs],
        "peak_1s_before_first_success_mean": rnd(np.mean([x["peak_1s_before_first_success"] for x in xs]), 1),
    }
    pk = [x["peak_recovery_load"] for x in xs if x["peak_recovery_load"] is not None]
    if pk:
        pps = [x["peak_per_survivor"] for x in xs if x["peak_per_survivor"] is not None]
        ent.update({
            "peak_recovery_load": pk, "peak_recovery_load_min": min(pk),
            "peak_recovery_load_max": max(pk), "peak_recovery_load_mean": rnd(np.mean(pk), 2),
            "peak_per_survivor": pps, "peak_per_survivor_mean": rnd(np.mean(pps), 3),
        })
    load[s] = ent
derived["load_by_strategy"] = load
if "equal_jitter" in load and "exp" in load:
    ej, ex = load["equal_jitter"]["peak_recovery_load_mean"], load["exp"]["peak_recovery_load_mean"]
    derived["recovery_load_headline"] = {
        "equal_jitter_mean_peak": ej, "exp_mean_peak": ex,
        "ratio_exp_over_equal_jitter": rnd(ex / ej, 2),
        "note": "exponential's whole cohort lands inside one 1-s window (see exp_wave.success_spread_s), so 100 is capped by N and the ratio grows at finer windows",
    }
if "full_jitter" in load and "exp" in load:
    fj, ex = load["full_jitter"], load["exp"]
    derived["full_jitter_vs_exp_during_outage"] = {
        "calls_before_first_success_mean": {"full_jitter": fj["calls_before_first_success_mean"],
                                            "exp": ex["calls_before_first_success_mean"]},
        "peak_1s_before_first_success_mean": {"full_jitter": fj["peak_1s_before_first_success_mean"],
                                              "exp": ex["peak_1s_before_first_success_mean"]},
        "peak_ratio_full_jitter_over_exp": rnd(fj["peak_1s_before_first_success_mean"]
                                               / ex["peak_1s_before_first_success_mean"], 2),
    }

# ── (e) steady-state comparisons ──
stdy = {"per_f": {}}
for f in sorted({f for (_, f) in steady}):
    cells = {s: steady[(s, f)] for s in STRATS if (s, f) in steady}
    cost = {s: float(np.mean([r["metrics"]["provider"]["calls"] / max(1, r["result"]["sent"])
                              for r in rs])) for s, rs in cells.items()}
    cmean = {s: float(np.mean([delivered_pct(r) for r in rs])) for s, rs in cells.items()}
    single = [(delivered_pct(r), s, r["config"]["repeat"], r) for s, rs in cells.items() for r in rs]
    lo_run = min(single, key=lambda t: t[0]); hi_run = max(single, key=lambda t: t[0])
    lats = {s: np.concatenate([np.array(r["latencies_s"], dtype=float) for r in rs])
            for s, rs in cells.items()}
    perc = {q: {s: pct(l, q) for s, l in lats.items()} for q in (50, 95, 99)}
    sent = sum(r["result"]["sent"] for rs in cells.values() for r in rs)
    nn = sum(r["config"]["n_reminders"] for rs in cells.values() for r in rs)
    att = cells[next(iter(cells))][0]["config"]["max_attempts"]
    lo_r = lo_run[3]
    ent = {
        "cost_per_delivered": {s: rnd(v) for s, v in cost.items()},
        "cost_per_delivered_range": [rnd(min(cost.values())), rnd(max(cost.values()))],
        "cost_cheapest": min(cost, key=cost.get), "cost_dearest": max(cost, key=cost.get),
        "delivery_cell_mean_pct": {s: rnd(v, 2) for s, v in cmean.items()},
        "delivery_cell_mean_range_pct": [rnd(min(cmean.values()), 2), rnd(max(cmean.values()), 2)],
        "delivery_single_run_range_pct": [rnd(lo_run[0], 2), rnd(hi_run[0], 2)],
        "lowest_single_run": None if lo_run[0] == hi_run[0] else {
            "strategy": lo_run[1], "repeat": lo_run[2],
            "delivered_pct": rnd(lo_run[0], 2),
            "observed_failure_fraction": rnd(
                sum(not a["ok"] for a in lo_r["provider_attempts"])
                / len(lo_r["provider_attempts"]), 3)},
        "pooled_delivery_pct": rnd(100.0 * sent / nn, 2),
        "expected_delivery_pct_1_minus_f_pow_attempts": rnd(100.0 * (1 - f ** att), 3),
        "attempts": att,
        "latency_s": {f"p{q}": {s: rnd(v) for s, v in perc[q].items()} for q in (50, 95, 99)},
        "latency_samples_per_cell_range": [min(len(l) for l in lats.values()),
                                           max(len(l) for l in lats.values())],
        "p95_stretch_vs_none": {s: rnd(perc[95][s] / perc[95]["none"], 2)
                                for s in ("exp", "decorrelated") if s in perc[95]},
        "none_rank_lowest_is_1": {f"p{q}": 1 + sorted(perc[q], key=perc[q].get).index("none")
                                  for q in (50, 95, 99)},
        "strategies_ranked": len(cells),
    }
    stdy["per_f"][str(f)] = ent
if "0.5" in stdy["per_f"]:
    p95 = stdy["per_f"]["0.5"]["latency_s"]["p95"]
    stdy["f05_p95_exp_s"] = p95["exp"]; stdy["f05_p95_full_jitter_s"] = p95["full_jitter"]
    stdy["f05_p95_ratio_exp_over_full_jitter"] = rnd(p95["exp"] / p95["full_jitter"], 2)
    stdy["f05_p95_ratio_full_jitter_over_exp"] = rnd(p95["full_jitter"] / p95["exp"], 3)
derived["steady"] = stdy

# ── precision of a 3 × 100 design ──
def mdd_pp(p, n):
    """Smallest difference between two cells of n jobs detectable at 95% (two-sided)."""
    return 100 * 1.96 * math.sqrt(2 * p * (1 - p) / n)

prec = {"note": "binomial approximations; between-run spread can exceed binomial noise, so real thresholds are at least this large"}
if "0.5" in stdy["per_f"]:
    p_c = stdy["per_f"]["0.5"]["pooled_delivery_pct"] / 100
    n_c = sum(r["config"]["n_reminders"] for r in steady[("none", 0.5)]) if ("none", 0.5) in steady else 300
    prec["near_ceiling"] = {"p": rnd(p_c, 4), "jobs_per_cell": n_c,
                            "min_detectable_difference_pp": rnd(mdd_pp(p_c, n_c), 2),
                            "per_run_binomial_sd_pp_n100": rnd(100 * math.sqrt(p_c * (1 - p_c) / 100), 2)}
if "full_jitter" in outage:
    p_o = model["per_strategy"]["full_jitter"]["observed_pct_mean"] / 100
    n_o = model["per_strategy"]["full_jitter"]["n_jobs_pooled"]
    prec["outage_regime_full_jitter"] = {"p": rnd(p_o, 4), "jobs_per_cell": n_o,
                                         "min_detectable_difference_pp": rnd(mdd_pp(p_o, n_o), 2),
                                         "per_run_binomial_sd_pp_n100": rnd(100 * math.sqrt(p_o * (1 - p_o) / 100), 2),
                                         "observed_runs_pct": model["per_strategy"]["full_jitter"]["observed_pct_runs"]}
samples = [len(np.concatenate([np.array(r["latencies_s"]) for r in rs])) for rs in steady.values()]
prec["p99_tail_observations_per_cell"] = [rnd(0.01 * min(samples), 2), rnd(0.01 * max(samples), 2)]
derived["precision"] = prec

with open(os.path.join(OUTD, "derived.json"), "w") as fh:
    json.dump(derived, fh, indent=1)
    fh.write("\n")


# ── derived.md: the same values, human-readable ──
def fmt(v):
    if v is None:
        return "–"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, list):
        return "[" + ", ".join(fmt(x) for x in v) + "]"
    return str(v)


def table(fh, hdr, rws):
    fh.write("| " + " | ".join(hdr) + " |\n|" + "---|" * len(hdr) + "\n")
    for rw in rws:
        fh.write("| " + " | ".join(fmt(x) for x in rw) + " |\n")
    fh.write("\n")


with open(os.path.join(OUTD, "derived.md"), "w") as fh:
    D = derived
    fh.write("# Derived numbers\n\nGenerated by `experiments/analyze.py` from `results/raw/`; "
             "the same values as `derived.json`. Times are seconds after the cohort's first "
             "provider attempt (t0) unless stated.\n\n")
    fh.write("## Definitions\n\n")
    for k, v in D["definitions"].items():
        fh.write(f"- **{k}**: {v}\n")
    fh.write("\n## Injection validity (all steady runs pooled)\n\n")
    table(fh, ["configured f", "runs", "calls", "failed", "observed rate", "observed (3 dp)",
               "binomial SE", "z"],
          [[f, v["runs"], v["calls"], v["failed"], v["observed_rate"], v["observed_rate_3dp"],
            v["binomial_se"], v["z"]]
           for f, v in D["injection_validity"]["configured"].items()])
    fh.write(f"Runs pooled: {D['injection_validity']['runs_pooled']}. Total steady calls: "
             f"{D['injection_validity']['total_steady_calls']}. Largest |z|: "
             f"{D['injection_validity']['max_abs_z']}.\n\n")
    fh.write("## Lead time (first attempt after the create burst)\n\n")
    fh.write(f"- steady runs: {fmt(D['lead']['steady_first_attempt_after_create_s'])} s\n"
             f"- outage runs: {fmt(D['lead']['outage_first_attempt_after_create_s'])} s\n\n")
    fh.write("## Effective outage window W\n\n")
    Wd = D["W"]
    fh.write(f"- recovery bracket over the {Wd['source_runs']} jittered runs: "
             f"{fmt(Wd['bracket_overall_s'])} s\n"
             f"- bracket midpoints: {fmt(Wd['bracket_midpoints_s'])} s\n"
             f"- **W_mid (median midpoint) = {Wd['W_mid_s']} s**\n"
             f"- configured: provider fails until {Wd['configured_recovery_uptime_s']} s of uptime, "
             f"so the first attempt came at {Wd['uptime_at_first_attempt_s']['at_W_mid']} s of "
             f"uptime (range {fmt(Wd['uptime_at_first_attempt_s']['range'])} s)\n\n")
    fh.write("## Outage runs\n\n")
    table(fh, ["strategy", "rep", "t0 − create (s)", "delivered", "calls", "failed",
               "calls before 1st success", "1-s peak before 1st success",
               "last failure (s)", "first success (s)", "bracket mid (s)",
               "peak recovery load", "per survivor", "model at own mid (%)"],
          [[x["strategy"], x["repeat"], x["t0_after_create_s"], x["delivered"], x["calls"],
            x["failed_attempts"], x["calls_before_first_success"], x["peak_1s_before_first_success"],
            x["last_failure_before_success_s"], x["first_success_s"],
            x.get("recovery_bracket_mid_s"), x["peak_recovery_load"], x["peak_per_survivor"],
            x.get("predicted_survival_pct_at_own_mid")] for x in D["outage_runs"]])
    if "exp_wave" in D:
        E = D["exp_wave"]
        fh.write("### Exponential's wave\n\n")
        fh.write(f"- attempt on which each job succeeded (all runs): {E['success_attempt_index_counts_all_runs']}\n"
                 f"- first success: {fmt(E['first_success_s'])} s; last failure before it: "
                 f"{fmt(E['last_failure_before_success_s'])} s\n"
                 f"- whole cohort delivered within {fmt(E['success_spread_s'])} s\n"
                 f"- first success after W_mid: {fmt(E['first_success_after_W_mid_s'])} s "
                 f"(after recovery, allowing for the bracket: {fmt(E['first_success_after_recovery_range_s'])} s)\n\n")
        table(fh, ["rep", "success min (s)", "median (s)", "max (s)", "spread (s)"],
              [[x["repeat"], x["success_time_s"]["min"], x["success_time_s"]["median"],
                x["success_time_s"]["max"], x["success_time_s"]["spread"]] for x in exp_runs])
    fh.write("## Recovery load and outage-phase load\n\n")
    table(fh, ["strategy", "delivered", "peak recovery load", "mean", "per survivor", "mean/survivor",
               "calls before 1st success", "mean", "1-s peak before 1st success", "mean"],
          [[LABELS[s], v["delivered"], v.get("peak_recovery_load"), v.get("peak_recovery_load_mean"),
            v.get("peak_per_survivor"), v.get("peak_per_survivor_mean"),
            v["calls_before_first_success"], v["calls_before_first_success_mean"],
            v["peak_1s_before_first_success"], v["peak_1s_before_first_success_mean"]]
           for s, v in D["load_by_strategy"].items()])
    if "recovery_load_headline" in D:
        H = D["recovery_load_headline"]
        fh.write(f"**Headline:** equal jitter mean peak {H['equal_jitter_mean_peak']}, exponential "
                 f"{H['exp_mean_peak']}, ratio {H['ratio_exp_over_equal_jitter']}×. {H['note']}.\n\n")
    if "full_jitter_vs_exp_during_outage" in D:
        F = D["full_jitter_vs_exp_during_outage"]
        fh.write(f"During the outage full jitter made {F['calls_before_first_success_mean']['full_jitter']} "
                 f"calls (mean) vs exponential's {F['calls_before_first_success_mean']['exp']}, with a 1-s "
                 f"peak of {F['peak_1s_before_first_success_mean']['full_jitter']} vs "
                 f"{F['peak_1s_before_first_success_mean']['exp']} (ratio {F['peak_ratio_full_jitter_over_exp']}).\n\n")
    M = D["model"]
    fh.write("## Coverage model (Monte Carlo of computeBackoff)\n\n")
    fh.write(f"Seed {M['seed']}, {M['trials']} trials, base {M['base_ms']} ms, cap {M['cap_ms']} ms, "
             f"{M['attempts']} attempts ({M['waits']} waits). Rule: {M['rule']}.\n\nAssumptions:\n\n")
    for a in M["assumptions"]:
        fh.write(f"- {a}\n")
    fh.write("\n")
    table(fh, ["strategy", f"pred @ {W_LO} s", f"pred @ W_mid {W_MID} s", f"pred @ {W_HI} s",
               "pred @ 60 s", "observed mean", "observed runs", "obs − pred (pp)", "SE (pp)", "agrees",
               "Σ waits min", "mean", "max", "sd"],
          [[LABELS[s], v["predicted_survival_pct_at_bracket_lo"], v["predicted_survival_pct_at_W_mid"],
            v["predicted_survival_pct_at_bracket_hi"], v["predicted_survival_pct_at_60s"],
            v.get("observed_pct_mean"), v.get("observed_pct_runs"),
            v.get("diff_pp_observed_minus_W_mid"), v.get("binomial_se_pp_at_W_mid"), v.get("agrees"),
            v["wait_sum_s"]["min"], v["wait_sum_s"]["mean"], v["wait_sum_s"]["max"], v["wait_sum_s"]["sd"]]
           for s, v in M["per_strategy"].items()])
    fh.write(f"{M['agreement_rule']}. All strategies agree: {fmt(M['all_strategies_agree'])}.\n\n")
    fh.write("Audit cross-check at W = 34 s: " + ", ".join(
        f"{LABELS[s]} {v['predicted_survival_pct_at_34s_audit_check']}%" for s, v in M["per_strategy"].items())
             + ".\n\n")
    EJ, FJ, EX = M["equal_jitter"], M["full_jitter"], M["exp"]
    fh.write("### Equal jitter\n\n"
             f"- floor sum (guaranteed reach): {EJ['floor_sum_s']} s; largest possible sum: {EJ['max_possible_sum_s']} s\n"
             f"- Monte Carlo sum min / mean / max: {EJ['mc_sum_min_s']} / {EJ['mc_sum_mean_s']} / {EJ['mc_sum_max_s']} s\n"
             f"- random half must add {EJ['random_half_needed_at_W_mid_s']} s to reach W_mid "
             f"({fmt(EJ['random_half_needed_over_bracket_s'])} s over the bracket)\n"
             f"- floor sum reaches 60 s only with {EJ['min_attempts_for_floor_sum_60s']} attempts "
             f"({EJ['min_waits_for_floor_sum_60s']} waits)\n\n")
    DB = M["default_budget"]
    fh.write(f"### Shipped default budget ({DB['attempts']} attempts, not measured)\n\n"
             f"- {DB['source']}\n"
             f"- equal jitter, {DB['waits']} waits: floor sum (guaranteed reach) {DB['equal_jitter_floor_sum_s']} s; "
             f"largest possible sum {DB['equal_jitter_max_possible_sum_s']} s\n"
             f"- Monte Carlo sum min / mean / max: {DB['equal_jitter_mc_sum_min_s']} / "
             f"{DB['equal_jitter_mc_sum_mean_s']} / {DB['equal_jitter_mc_sum_max_s']} s\n"
             f"- exponential's {DB['waits']} waits sum to {DB['exp_wait_sum_s']} s\n"
             f"- {DB['note']}\n\n")
    fh.write("### Full jitter\n\n"
             f"- 8 attempts: sum mean {FJ['sum_mean_s_8_attempts']} s, sd {FJ['sum_sd_s_8_attempts']} s\n"
             f"- 10 attempts (expected sum {FJ['expected_sum_s_10_attempts_analytic']} s, MC mean "
             f"{FJ['sum_mean_s_10_attempts']} s): predicted survival {FJ['predicted_survival_pct_10_attempts_at_W_mid']}% "
             f"at W_mid ({FJ['predicted_survival_pct_10_attempts_at_bracket_lo']}–"
             f"{FJ['predicted_survival_pct_10_attempts_at_bracket_hi']}% over the bracket; "
             f"{FJ['predicted_survival_pct_10_attempts_at_60s']}% at 60 s)\n\n")
    fh.write("### Exponential\n\n"
             f"- attempt times (s of waiting): {EX['attempt_time_s_by_attempt']}\n"
             f"- {EX['at_60s']}\n\n")
    fh.write("### Predicted survival (%) vs W, 8 attempts\n\n")
    G = M["survival_grid_8_attempts"]
    table(fh, ["W (s)"] + [LABELS[s] for s in STRATS],
          [[W] + [G[s][i] for s in STRATS] for i, W in enumerate(G["W_s"])])
    fh.write(f"Decorrelated's predicted survival exceeds equal jitter's from W = "
             f"{M['decorrelated_exceeds_equal_jitter_from_W_s']} s.\n\n")
    fh.write("## Steady state\n\n")
    for f, v in D["steady"]["per_f"].items():
        fh.write(f"### f = {f}\n\n")
        lr = v["lowest_single_run"]
        low_txt = (" (every run equal)" if lr is None else
                   f" (lowest: {lr['strategy']} rep {lr['repeat']}, observed failure fraction "
                   f"{lr['observed_failure_fraction']})")
        fh.write(f"- cost per delivered: {fmt(v['cost_per_delivered_range'])} "
                 f"(cheapest {v['cost_cheapest']}, dearest {v['cost_dearest']})\n"
                 f"- delivery, cell means: {fmt(v['delivery_cell_mean_range_pct'])} %; single runs: "
                 f"{fmt(v['delivery_single_run_range_pct'])} %{low_txt}\n"
                 f"- pooled delivery: {v['pooled_delivery_pct']} % (expected 1 − f^{v['attempts']} = "
                 f"{v['expected_delivery_pct_1_minus_f_pow_attempts']} %)\n"
                 f"- p95 stretch vs none: {v['p95_stretch_vs_none']}\n"
                 f"- rank of none (1 = lowest latency of {v['strategies_ranked']}): {v['none_rank_lowest_is_1']}\n"
                 f"- latency samples per cell: {fmt(v['latency_samples_per_cell_range'])}\n\n")
        table(fh, ["strategy", "cost/delivered", "delivered % (mean)", "p50 (s)", "p95 (s)", "p99 (s)"],
              [[LABELS[s], v["cost_per_delivered"][s], v["delivery_cell_mean_pct"][s],
                v["latency_s"]["p50"][s], v["latency_s"]["p95"][s], v["latency_s"]["p99"][s]]
               for s in v["cost_per_delivered"]])
    S5 = D["steady"]
    if "f05_p95_ratio_exp_over_full_jitter" in S5:
        fh.write(f"At f = 0.5, p95 exponential {S5['f05_p95_exp_s']} s vs full jitter "
                 f"{S5['f05_p95_full_jitter_s']} s: ratio {S5['f05_p95_ratio_exp_over_full_jitter']} "
                 f"(full jitter / exp = {S5['f05_p95_ratio_full_jitter_over_exp']}).\n\n")
    fh.write("## Precision (3 runs × 100 jobs per cell)\n\n")
    P = D["precision"]
    for key, lab in (("near_ceiling", "near the delivery ceiling (steady f = 0.5 pooled)"),
                     ("outage_regime_full_jitter", "outage regime (full jitter)")):
        if key in P:
            v = P[key]
            fh.write(f"- {lab}: p = {v['p']}, smallest detectable difference between two cells "
                     f"{v['min_detectable_difference_pp']} pp; per-run binomial SD "
                     f"{v['per_run_binomial_sd_pp_n100']} pp\n")
    fh.write(f"- observations above p99 per steady cell: {fmt(P['p99_tail_observations_per_cell'])}\n"
             f"- {P['note']}\n\n")
    fh.write("## Data checks\n\n")
    for k, v in D["checks"].items():
        fh.write(f"- {k}: {fmt(v)}\n")
print("wrote derived.json / derived.md")


# ══════════════════════════════ figures ══════════════════════════════
def strat_iter():
    return [s for s in STRATS if any(k[0] == s for k in steady)]

W_TXT = f"{W_MID:.0f}"

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
    ax.set_title(f"Provider outage, effective W ≈ {W_TXT} s: survival by strategy (8 attempts)",
                 loc="left")
    fig.tight_layout(); fig.savefig(os.path.join(FIGD, "fig3_outage.png"), dpi=200)
    plt.close(fig); print("fig3 done")

# ── fig 4: retry herd around recovery (small multiples) ──
if outage:
    order = [s for s in STRATS if s in outage]
    fig, axes = plt.subplots(len(order), 1, figsize=(7, 1.35 * len(order) + 1.6),
                             sharex=True, sharey=True)
    axes = np.atleast_1d(axes)
    for ax, s in zip(axes, order):
        binned = defaultdict(float)
        for r in outage[s]:
            atts = r["provider_attempts"]
            if not atts: continue
            t0 = min(a["t"] for a in atts)
            for a in atts:
                binned[int((a["t"] - t0) / 1000)] += 1.0 / len(outage[s])
        # one shared recovery line (W_mid) with its bracket, on every panel
        ax.axvspan(W_LO, W_HI, color=INK2, alpha=BAND_ALPHA, lw=0, zorder=0)
        ax.axvline(W_MID, color=INK2, lw=1.2, zorder=1)
        if binned:
            xs = np.arange(0, max(binned) + 1)
            ax.fill_between(xs, [binned.get(int(x), 0) for x in xs],
                            color=COLORS[s], alpha=0.28, lw=0, zorder=2)
            ax.plot(xs, [binned.get(int(x), 0) for x in xs], color=COLORS[s], lw=1.6, zorder=3)
        fs = [x["first_success_s"] for x in outage_runs
              if x["strategy"] == s and x["first_success_s"] is not None]
        if fs:   # first successful delivery, marked separately from recovery
            ax.axvline(float(np.median(fs)), color=INK, lw=1.1, ls=(0, (1.2, 1.6)), zorder=4)
        ax.annotate(LABELS[s], (0.99, 0.82), xycoords="axes fraction",
                    ha="right", fontsize=10, color=COLORS[s])
        ax.grid(axis="x", visible=False)
    axes[-1].set_xlabel("seconds since the cohort's first attempt  (1-s bins, mean of 3 runs)")
    axes[len(axes) // 2].set_ylabel("provider attempts / s (mean per run)")
    fig.suptitle(f"The herd: retry arrivals through a provider outage (effective W ≈ {W_TXT} s)",
                 x=0.01, ha="left")
    handles = [
        Patch(facecolor=INK2, alpha=BAND_ALPHA * 1.6, lw=0,
              label=f"recovery bracket, jittered runs ({W_LO:.1f}–{W_HI:.1f} s)"),
        Line2D([0], [0], color=INK2, lw=1.2, label=f"provider recovery, W ≈ {W_MID:.1f} s"),
        Line2D([0], [0], color=INK, lw=1.1, ls=(0, (1.2, 1.6)),
               label="first successful delivery (median of runs)"),
    ]
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.01, 0.962), ncol=2,
               frameon=False, fontsize=8.5, handlelength=2.2, columnspacing=1.6)
    fig.tight_layout(rect=(0, 0, 1, 0.935))
    fig.savefig(os.path.join(FIGD, "fig4_herd.png"), dpi=200); plt.close(fig)
    print("fig4 done")

# ── fig 5: coverage model, predicted survival vs W (MODEL, not measurement) ──
if outage:
    fig, ax = plt.subplots(figsize=(7, 4.6))
    Wx = np.arange(0, 90.0001, 0.05)
    ax.axvspan(W_LO, W_HI, color=INK2, alpha=BAND_ALPHA, lw=0, zorder=0)
    ax.axvline(W_MID, color=INK2, lw=1.2, zorder=1)
    ax.axvline(60.0, color=INK2, lw=1.0, ls=(0, (4, 3)), zorder=1)
    for s in STRATS:
        ys = [survival_pct(SUMS[s], W) for W in Wx]
        ax.plot(Wx, ys, color=COLORS[s], lw=2, zorder=3, label=LABELS[s])
    # measured points at W_mid, mean and per-run range; overlapping pairs dodged ±0.9 s
    dodge = {"none": -0.9, "fixed": 0.9, "exp": -0.9, "equal_jitter": 0.9}
    for s in [s for s in STRATS if s in outage]:
        vals = [delivered_pct(r) for r in outage[s]]
        m = float(np.mean(vals))
        x = W_MID + dodge.get(s, 0.0)
        ax.errorbar([x], [m], yerr=[[m - min(vals)], [max(vals) - m]], fmt="o", ms=7,
                    color=COLORS[s], mfc=COLORS[s], mec=SURFACE, mew=1.5,
                    elinewidth=1.6, capsize=3, zorder=5)
    ax.set_xlim(-1.5, 90); ax.set_ylim(-4, 112)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.text(W_MID - 0.8, 106, f"measured W ≈ {W_MID:.1f} s", ha="right", va="center",
            fontsize=8.5, color=INK2)
    ax.text(60.8, 106, "W = 60 s", ha="left", va="center", fontsize=8.5, color=INK2)
    ax.set_xlabel("effective outage W (s): cohort's first attempt → provider recovery")
    ax.set_ylabel("jobs surviving (%)")
    ax.set_title("lines: model, a Monte Carlo of computeBackoff (waits only), not measured\n"
                 "dots: measured runs, mean and range of 3 (overlapping dots offset ±0.9 s)",
                 loc="left", fontsize=8.5, color=INK2, linespacing=1.4)
    handles, labels = ax.get_legend_handles_labels()
    handles.append(Line2D([0], [0], marker="o", ls="none", color=INK2, ms=6,
                          label="measured at W ≈ %.1f s" % W_MID))
    ax.legend(handles=handles, loc="upper right", bbox_to_anchor=(1.0, 0.93), frameon=False,
              fontsize=8.5, ncol=1)
    fig.suptitle("MODEL: predicted survival vs effective outage W (8 attempts)",
                 x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(os.path.join(FIGD, "fig5_coverage_model.png"), dpi=200); plt.close(fig)
    print("fig5 done")

print("analysis complete")
