"""Monte Carlo simulation (inventory domain 6 #34): seeded, reproducible, honestly bounded.

"monte carlo: portfolio 1000000, yearly return 8% +/- 15%, 20 years",
"simulate 10000 rolls of 2 dice and the sum distribution",
"monte carlo project finish: 3 tasks, each 5 days +/- 2"

Estimator (no heavy libs): normally-distributed yearly returns via Box-Muller from a seeded
numpy Generator (default_rng(seed)) — the seed and every setting are stated in the reply so
anyone can reproduce the run exactly. Reports percentiles P5/P50/P95, P(loss), and the mean —
all MEASURED from the run. Dice mode validates the analytical expectation (7 is the modal sum).
T0, numpy only.
"""
import re

from ishti.skills import trace

I = re.I

PORT = re.compile(
    r"^\s*(?:monte\s+carlo|simulate)\s*[:\-]?\s*(?:portfolio|starting\s+(?:amount|capital))\s*"
    r"(?P<start>[\d,]+(?:\.\d+)?)\s*(?:\.\s*)?yearly\s+return\s*(?P<mean>\d*\.?\d+)\s*%\s*"
    r"(?:\+/-|\+/-|±|\+-|plus\s*minus)\s*(?P<vol>\d*\.?\d+)\s*%\s*"
    r"(?:,?\s*(?:for\s+)?(?P<years>\d+)\s*years?)?\s*$", I)
DICE = re.compile(
    r"^\s*(?:monte\s+carlo|simulate)\s*[:\-]?\s*(?P<n>[\d,]+)\s*(?:rolls|throws)\s+of\s+"
    r"(?P<dice>\d+)\s*dice\s*(?:and\s+(?:the\s+)?sum\s+distribution)?\s*$", I)
TASKS = re.compile(
    r"^\s*(?:monte\s+carlo|simulate)\s*[:\-]?\s*(?:project\s+)?finish\s*[:\-]?\s*(?P<tasks>\d+)\s*tasks?\s*,?\s*"
    r"each\s+(?P<d>\d+)\s*days?\s*(?:\+/-|±|\+-)\s*(?P<vol>\d+)\s*$", I)


def _portfolio(start, mean_pct, vol_pct, years, n=20000, seed=7):
    import numpy as np
    rng = np.random.default_rng(seed)
    mean, vol = mean_pct / 100.0, vol_pct / 100.0
    # Box-Muller normal draws, clipped to sane return range
    u1 = rng.uniform(size=(n, years))
    u2 = rng.uniform(size=(n, years))
    z = np.sqrt(-2 * np.log(u1)) * np.cos(2 * np.pi * u2)
    returns = np.clip(mean + vol * z, -0.6, 1.5)
    paths = start * np.prod(1 + returns, axis=1)
    return paths


def run_portfolio(m):
    import numpy as np
    start = float(m.group("start").replace(",", ""))
    mean, vol = float(m.group("mean")), float(m.group("vol"))
    years = int(m.group("years") or 20)
    if start <= 0 or abs(mean) > 40 or vol > 60 or years > 100:
        return "Out of sane range: |return| ≤ 40%, volatility ≤ 60%, ≤ 100 years."
    paths = _portfolio(start, mean, vol, years)
    p5, p50, p95 = np.percentile(paths, [5, 50, 95])
    p_loss = (paths < start).mean()
    return (f"**Monte Carlo portfolio** — start {start:,.0f}, return {mean:g}% ± {vol:g}%/yr, "
            f"{years} years, 20,000 seeded paths (seed 7, Box-Muller):\n"
            f"- P5 **{p5:,.0f}** · median **{p50:,.0f}** · P95 **{p95:,.0f}**\n"
            f"- chance of ending below start: **{p_loss:.1%}** · mean {paths.mean():,.0f}\n"
            f"All numbers MEASURED from this run; re-run with the same seed to reproduce. "
            f"Normal-returns model — fat tails are understated.")


def run_dice(m):
    import numpy as np
    n = int(m.group("n").replace(",", ""))
    ndice = int(m.group("dice"))
    if not (1 <= n <= 1_000_000 and 1 <= ndice <= 10):
        return "Keep it between 1 and 1,000,000 rolls and 1–10 dice."
    rng = np.random.default_rng(7)
    rolls = rng.integers(1, 7, size=(n, ndice)).sum(axis=1)
    lo, hi = ndice, 6 * ndice
    mode = int(np.bincount(rolls - lo).argmax()) + lo
    exp = sum(k * (rolls == k).mean() for k in range(lo, hi + 1))
    return (f"**{n:,} rolls of {ndice} dice** (seed 7):\n"
            f"- modal sum **{mode}** (analytical mode is {int(3.5 * ndice)})\n"
            f"- mean {exp:.3f} vs exact {3.5 * ndice:.1f} — "
            f"{'matches' if abs(exp - 3.5 * ndice) < 0.1 else 'off, check n'}\n"
            f"- min {rolls.min()}, max {rolls.max()}")


def run_tasks(m):
    import numpy as np
    tasks, d, vol = int(m.group("tasks")), int(m.group("d")), int(m.group("vol"))
    if not (1 <= tasks <= 50 and vol <= d * 2):
        return "1–50 tasks, and the variation should be plausible (≤ 2× the base duration)."
    rng = np.random.default_rng(7)
    u1, u2 = rng.uniform(size=(20000, tasks)), rng.uniform(size=(20000, tasks))
    z = np.sqrt(-2 * np.log(u1)) * np.cos(2 * np.pi * u2)
    days = np.clip(d + vol * z, 0.1, None).sum(axis=1)
    p5, p50, p95 = np.percentile(days, [5, 50, 95])
    naive = tasks * d
    return (f"**Project finish** — {tasks} tasks × {d}d ± {vol}d, 20,000 seeded paths (seed 7):\n"
            f"- P5 **{p5:,.0f} days** · median **{p50:,.0f}** · P95 **{p95:,.0f}**\n"
            f"- naive estimate {naive} days; variation widens the tail, not the middle\n"
            f"MEASURED from this run; independent-task model (no inter-task dependencies).")


def match(text):
    t = text.strip()
    return bool(PORT.match(t) or DICE.match(t) or TASKS.match(t))


def run(text, session):
    t = text.strip()
    if (m := PORT.match(t)):
        trace(session, "montecarlo", "portfolio")
        return {"reply": run_portfolio(m), "sources": []}
    if (m := DICE.match(t)):
        trace(session, "montecarlo", "dice")
        return {"reply": run_dice(m), "sources": []}
    if (m := TASKS.match(t)):
        trace(session, "montecarlo", "project")
        return {"reply": run_tasks(m), "sources": []}
    return None


if __name__ == "__main__":
    assert match("monte carlo: portfolio 1000000, yearly return 8% +/- 15%, 20 years")
    assert match("simulate 10000 rolls of 2 dice")
    assert match("monte carlo: project finish: 5 tasks, each 5 days +/- 2")
    assert not match("monte carlo racing is a sport")
    assert not match("simulate my exam results")

    r = run("monte carlo: portfolio 1000000, yearly return 8% +/- 15%, 20 years", {})
    assert "seed 7" in r["reply"] and "P5" in r["reply"], r
    import numpy as np
    paths = _portfolio(1_000_000, 8, 15, 20)
    p50 = np.percentile(paths, 50)
    assert f"{p50:,.0f}" in r["reply"], (r, p50)        # deterministic: seed 7 must reproduce
    r2 = run("simulate 10000 rolls of 2 dice", {})
    assert "modal sum **7**" in r2["reply"], r2          # analytical ground truth
    assert "matches" in r2["reply"], r2
    r3 = run("monte carlo: project finish: 5 tasks, each 5 days +/- 2", {})
    assert "P5" in r3["reply"] and "independent-task" in r3["reply"], r3
    r4 = run("monte carlo: portfolio 1000000, yearly return 900% +/- 15%", {})
    assert "sane range" in r4["reply"], r4
    print("[self-check] montecarlo OK - seeded Box-Muller paths reproduce exactly, dice mode "
          "matches the analytical mode 7 / mean 3.5n, percentile tails stated, model limits declared")
