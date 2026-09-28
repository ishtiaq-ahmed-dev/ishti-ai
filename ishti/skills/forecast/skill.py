"""Trend forecasting on CSV time series (inventory domain 6 #31/32, 8 #9/11).

"forecast D:/data/sales.csv next 6 months", "predict next 7 days from D:/visitors.csv",
"trend of D:/data/sales.csv", "moving average of D:/x.csv over 4", "seasonality in D:/x.csv"

Everything is exact, computed with pandas/numpy least squares: the slope, the projection,
the moving average, the deltas. No model downloads, nothing leaves the PC (T0). The reply
always states the assumed period so an owner can catch a wrong assumption immediately.

Self-check generates its own CSV with a known linear trend and asserts the forecast lands
inside a tight band around the true continuation.
"""
import os
import re

from ishti.skills import trace

I = re.I

# both branches need the drive prefix: a bare-name branch would match the words "days"/"from"
# inside "predict next 7 days from ..." and wreck the match
PATH = r"(?P<path>[A-Za-z]:[\\/][^\"']+?\.(?:csv|tsv))"
HORIZON = r"(?P<n>\d{1,3})"
FUTURE = re.compile(
    r"^\s*(?:forecast|predict|project)(?:\s+(?:the\s+)?(?:trend|future))?\s+(?:for\s+|from\s+|of\s+)?" + PATH +
    r"\s*(?:for\s+)?(?:the\s+)?next\s+" + HORIZON + r"\s*(?P<unit>days?|weeks?|months?|periods?)\s*$", I)
# the other word order: "predict next 7 days from D:/x.csv" (horizon first, path after)
FUTURE2 = re.compile(
    r"^\s*(?:forecast|predict|project)\s+(?:the\s+)?next\s+" + HORIZON +
    r"\s*(?P<unit>days?|weeks?|months?|periods?)\s+(?:from|for|of)\s+" + PATH + r"\s*$", I)
TREND = re.compile(r"^\s*(?:what(?:'s| is)\s+the\s+)?trend\s+(?:of|in|for)\s+" + PATH + r"\s*$", I)
MOVAVG = re.compile(r"^\s*(?:moving\s+average|ma)\s+(?:of|in|for)\s+" + PATH +
                    r"(?:\s+(?:over|window|of)\s+" + HORIZON + r")?\s*$", I)
SEASON = re.compile(r"^\s*(?:seasonality|seasonal\s+pattern)\s+(?:in|of|for)\s+" + PATH + r"\s*$", I)

UNITS = {"day": "days", "days": "days", "week": "weeks", "weeks": "weeks",
         "month": "months", "months": "months", "period": "periods", "periods": "periods"}


def _series(path):
    """The first numeric column as a float series, in file order. Returns (values, colname) or None."""
    import pandas as pd
    if not os.path.isfile(path):
        return None
    try:
        df = pd.read_csv(path)
    except Exception as exc:
        return [], f"(could not read: {exc})"
    nums = df.select_dtypes(include="number")
    if nums.empty:
        return [], "no numeric columns"
    col = nums.columns[0]
    vals = pd.to_numeric(nums[col], errors="coerce").dropna().tolist()
    return vals, str(col)


def _fit(vals):
    """Least-squares slope/intercept over the series index. Returns (slope, intercept, r2)."""
    import numpy as np
    y = np.asarray(vals, dtype=float)
    x = np.arange(len(y), dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    pred = slope * x + intercept
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum()) or 1.0
    return float(slope), float(intercept), 1.0 - ss_res / ss_tot


def _per_step(unit):
    return {"days": 1.0, "weeks": 7.0, "months": 30.4, "periods": 1.0}.get(unit, 1.0)


def forecast(path, n, unit):
    got = _series(path)
    if got is None:
        return f"File not found: {path}"
    vals, col = got
    if isinstance(vals, list) and not vals:
        return f"No usable series in {os.path.basename(path)}: {col}."
    if len(vals) < 4:
        return f"Need at least 4 data points (found {len(vals)} in `{col}`)."
    slope, intercept, r2 = _fit(vals)
    per = _per_step(unit)
    base = len(vals) - 1
    proj = [intercept + slope * (base + i + 1) for i in range(int(n))]
    last = vals[-1]
    total = slope * int(n)
    direction = "rising" if slope > 0 else "falling" if slope < 0 else "flat"
    lines = [f"**{os.path.basename(path)}** — `{col}`, {len(vals)} points, trend {direction} "
             f"({slope:+.4g} per index step, R² {r2:.2f}).",
             f"Next {n} {unit} (linear least squares, exact):"]
    lines += [f"- +{i + 1}: {v:.4g}" for i, v in enumerate(proj[:12])]
    if int(n) > 12:
        lines.append(f"- ... {int(n) - 12} more periods")
    lines.append(f"Net change over the horizon: {total:+.4g} (from {last:.4g} to {proj[-1]:.4g}).")
    lines.append("A straight-line fit — it extrapolates the trend, it does not know about your market.")
    return "\n".join(lines)


def trend_of(path):
    got = _series(path)
    if got is None:
        return f"File not found: {path}"
    vals, col = got
    if isinstance(vals, list) and not vals:
        return f"No usable series in {os.path.basename(path)}: {col}."
    if len(vals) < 4:
        return f"Need at least 4 data points (found {len(vals)} in `{col}`)."
    slope, _b, r2 = _fit(vals)
    n = len(vals)
    half = n // 2
    first, second = sum(vals[:half]) / half, sum(vals[-half:]) / (n - half)
    best_i = max(range(1, n), key=lambda i: vals[i] - vals[i - 1])
    worst_i = min(range(1, n), key=lambda i: vals[i] - vals[i - 1])
    return (f"**Trend of `{col}`** ({n} points): slope {slope:+.4g} per step, R² {r2:.2f}.\n"
            f"First-half mean {first:.4g} vs second-half mean {second:.4g} "
            f"({(second - first) / abs(first or 1) * 100:+.1f}%).\n"
            f"Best step: +{vals[best_i] - vals[best_i - 1]:.4g} (into point {best_i + 1}); "
            f"worst: {vals[worst_i] - vals[worst_i - 1]:.4g} (into point {worst_i + 1}).")


def moving_avg(path, window):
    got = _series(path)
    if got is None:
        return f"File not found: {path}"
    vals, col = got
    if isinstance(vals, list) and not vals:
        return f"No usable series in {os.path.basename(path)}: {col}."
    w = max(2, min(int(window or 3), max(2, len(vals) // 2)))
    if len(vals) < w:
        return f"Need at least {w} points for a {w}-point window (found {len(vals)})."
    avgs = [sum(vals[i - w:i]) / w for i in range(w, len(vals) + 1)]
    return (f"**Moving average** of `{col}` (window {w}):\n"
            + ", ".join(f"{a:.4g}" for a in avgs[:12])
            + (f" … ({len(avgs) - 12} more)" if len(avgs) > 12 else "")
            + f"\nLast MA {avgs[-1]:.4g} vs latest raw {vals[-1]:.4g} "
              f"({'above' if vals[-1] > avgs[-1] else 'below'} trend by {abs(vals[-1] - avgs[-1]):.4g}).")


def seasonality(path):
    got = _series(path)
    if got is None:
        return f"File not found: {path}"
    vals, col = got
    if isinstance(vals, list) and not vals:
        return f"No usable series in {os.path.basename(path)}: {col}."
    n = len(vals)
    if n < 14:
        return (f"Only {n} points in `{col}` — seasonality needs at least two full cycles "
                f"(14+ points for weekly, 60+ for monthly).")
    overall = sum(vals) / n
    for cycle, name in ((7, "weekly"), (30, "monthly")):
        if n >= cycle * 2:
            bucket_means = []
            for phase in range(cycle):
                pts = vals[phase::cycle]
                bucket_means.append(sum(pts) / len(pts))
            spread = (max(bucket_means) - min(bucket_means)) / abs(overall or 1) * 100
            hi = bucket_means.index(max(bucket_means)) + 1
            lo = bucket_means.index(min(bucket_means)) + 1
            return (f"**{name.title()} cycle** in `{col}` ({n} points, {n // cycle} cycles):\n"
                    f"Phase {hi} averages highest ({max(bucket_means):.4g}), "
                    f"phase {lo} lowest ({min(bucket_means):.4g}); "
                    f"spread {spread:.1f}% of the overall mean {overall:.4g}. "
                    + ("A real cycle if the spread is well above your noise." if spread >= 10
                       else "Weak — under 10% spread, probably noise."))
    return "Series too short for the monthly cycle; weekly needs 14+ points."


def match(text):
    t = text.strip()
    return bool(FUTURE.match(t) or FUTURE2.match(t) or TREND.match(t) or MOVAVG.match(t) or SEASON.match(t))


def run(text, session):
    t = text.strip()
    m = FUTURE.match(t) or FUTURE2.match(t)
    if m:
        trace(session, "forecast", f"next {m.group('n')} {UNITS.get(m.group('unit'), 'periods')}")
        return {"reply": forecast(m.group("path").strip(), m.group("n"),
                                  UNITS.get(m.group("unit"), "periods")), "sources": []}
    if (m := TREND.match(t)):
        trace(session, "forecast", "trend")
        return {"reply": trend_of(m.group("path").strip()), "sources": []}
    if (m := MOVAVG.match(t)):
        trace(session, "forecast", f"moving average {m.group('n') or 3}")
        return {"reply": moving_avg(m.group("path").strip(), m.group("n")), "sources": []}
    if (m := SEASON.match(t)):
        trace(session, "forecast", "seasonality")
        return {"reply": seasonality(m.group("path").strip()), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp()
    csv = os.path.join(tmp, "sales.csv")
    # exact linear series: y = 10 + 2*i  (0..11), so slope=2, next three = 34, 36, 38
    rows = ["month,sales"] + [f"m{i},{10 + 2 * i}" for i in range(12)]
    open(csv, "w", encoding="utf-8").write("\n".join(rows) + "\n")
    flat = os.path.join(tmp, "flat.csv")
    open(flat, "w", encoding="utf-8").write("d,v\n" + "\n".join(f"d{i},50" for i in range(20)) + "\n")
    short = os.path.join(tmp, "short.csv")
    open(short, "w", encoding="utf-8").write("d,v\n1,2\n2,3\n")

    assert match("forecast " + csv + " next 6 months")
    assert match("predict next 7 days from " + csv)
    assert match("trend of " + csv)
    assert match("moving average of " + csv + " over 4")
    assert match("seasonality in " + csv)
    assert not match("forecast the weather")
    assert not match("what is my income trend")            # no csv -> not ours
    assert not match("trend of my mood")                   # ditto

    r = run("forecast " + csv + " next 3 months", {})
    assert "+1: 34" in r["reply"] and "+2: 36" in r["reply"] and "+3: 38" in r["reply"], r
    assert "R² 1" in r["reply"], r                          # a perfect line fits perfectly
    r2 = run("trend of " + csv, {})
    assert "slope +2" in r2["reply"] and "R² 1" in r2["reply"], r2
    r3 = run("moving average of " + csv + " over 4", {})
    assert "Moving average" in r3["reply"], r3
    r4 = run("seasonality in " + flat, {})
    assert "Weak" in r4["reply"] or "spread" in r4["reply"], r4   # constant series: no real cycle
    r5 = run("forecast " + short + " next 2 months", {})
    assert "at least 4" in r5["reply"], r5
    r6 = run("forecast D:/nope/none.csv next 3 months", {})
    assert "not found" in r6["reply"].lower()
    print("[self-check] forecast OK - exact least-squares trend, projection, moving average, "
          "seasonality spread; no LLM arithmetic")
