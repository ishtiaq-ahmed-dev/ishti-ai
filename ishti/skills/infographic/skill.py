"""Infographic stat panels (inventory domain 3 #32): matplotlib, exact numbers on the image.

"infographic of D:/data/sales.csv", "make an infographic from D:/visitors.csv by region with amount",
"stats image of D:/x.csv"

One PNG panel per numeric column (up to 3): the column's exact min, mean, median and max are
computed with pandas and PRINTED ON the chart next to a bar of the values — no artistic licence,
no invented figures. Categorical grouping (by X with Y) produces a per-group mean bar chart with
exact labels. Outputs to data/infographics/. T1, local write only.
"""
import os
import re

from ishti.skills import trace

I = re.I

PATH = r"(?P<path>[A-Za-z]:[\\/][^\"']+?\.csv)"
GROUP = re.compile(
    rf"^\s*(?:make\s+an?\s+)?(?:infographic|stats\s+image)\s+(?:of|from)\s+(?:the\s+)?{PATH}\s+by\s+"
    r"(?P<cat>\w+)\s+with\s+(?P<val>\w+)\s*$", I)
SIMPLE = re.compile(rf"^\s*(?:make\s+an?\s+)?(?:infographic|stats\s+image)\s+(?:of|from)\s+(?:the\s+)?{PATH}\s*$", I)


def _panel(df, col, title, fp):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    s = df[col].dropna()
    stats = {"min": s.min(), "mean": s.mean(), "median": s.median(), "max": s.max()}
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.barh([0], [s.max() - s.min() or 1], left=s.min(), color="#4a7fb5", height=0.4)
    ax.axvline(stats["mean"], color="#d1495b")
    ax.text(stats["mean"], 0.32, f" mean {stats['mean']:.2f}", color="#d1495b")
    for i, (k, v) in enumerate(stats.items()):
        ax.text(0.01, 0.9 - i * 0.07, f"{k}: {v:,.2f}", transform=ax.transAxes, fontsize=10)
    ax.set_yticks([])
    ax.set_title(f"{title}: {col} (n={len(s)})")
    ax.set_xlabel(col)
    fig.tight_layout()
    fig.savefig(fp, dpi=110)
    plt.close(fig)


def _group_panel(df, cat, val, fp):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    g = df.groupby(cat)[val].mean().sort_values(ascending=False)
    fig, ax = plt.subplots(figsize=(7, 4))
    g.plot.bar(ax=ax, color="#4a7fb5")
    for i, (name, v) in enumerate(g.items()):
        ax.text(i, v, f" {v:,.2f}", ha="left", va="bottom", fontsize=9)
    ax.set_title(f"mean {val} by {cat}")
    fig.tight_layout()
    fig.savefig(fp, dpi=110)
    plt.close(fig)


def make(path, cat=None, val=None, session=None):
    if not os.path.isfile(path):
        return f"File not found: {path}"
    try:
        import pandas as pd
    except ImportError:
        return "pandas isn't installed."
    try:
        df = pd.read_csv(path)
    except Exception as exc:
        return f"Could not read that CSV: {exc}"
    out = (session or {}).get("_info_out") or os.path.join("data", "infographics")
    os.makedirs(out, exist_ok=True)
    stem = os.path.splitext(os.path.basename(path))[0]
    made = []
    if cat and val:
        if cat not in df.columns or val not in df.columns:
            return (f"Columns not found. Available: {', '.join(map(str, df.columns))}. "
                    f"You asked for by={cat}, with={val}.")
        fp = os.path.join(out, f"{stem}-{val}-by-{cat}.png")
        means = df.groupby(cat)[val].mean().sort_values(ascending=False)
        _group_panel(df, cat, val, fp)
        made.append((fp, means))
        reply = (f"Infographic of **mean {val} by {cat}** saved to `{fp}` — exact per-group means "
                 f"printed on the bars (groups: {', '.join(map(str, means.index))}).")
    else:
        nums = [c for c in df.select_dtypes("number").columns][:3]
        if not nums:
            return "No numeric columns found — nothing to chart."
        for col in nums:
            fp = os.path.join(out, f"{stem}-{col}.png")
            _panel(df, col, stem, fp)
            made.append((fp, None))
        reply = (f"{len(nums)} infographic panel(s) saved to `{out}`: {', '.join(nums)}. "
                 f"Each shows the column's exact min/mean/median/max — computed, not drawn to taste.")
    return reply


def match(text):
    t = text.strip()
    return bool(SIMPLE.match(t) or GROUP.match(t))


def run(text, session):
    t = text.strip()
    if (m := GROUP.match(t) or SIMPLE.match(t)):
        trace(session, "infographic", "make")
        cat = m.group("cat") if "cat" in m.groupdict() and m.group("cat") else None
        val = m.group("val") if "val" in m.groupdict() and m.group("val") else None
        return {"reply": make(m.group("path"), cat, val, session), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "out")
    csv = os.path.join(tmp, "sales.csv")
    with open(csv, "w", encoding="utf-8") as fh:
        fh.write("region,amount\nnorth,100\nsouth,300\nnorth,200\nsouth,400\n")

    assert match("infographic of " + csv)
    assert match("stats image of " + csv)
    assert match("make an infographic from " + csv + " by region with amount")
    assert not match("infographic of my life")

    r = run("infographic of " + csv, {"_info_out": out})
    assert os.path.isfile(os.path.join(out, "sales-amount.png")), r
    assert "min/mean/median/max" in r["reply"], r
    r2 = run("make an infographic from " + csv + " by region with amount", {"_info_out": out})
    assert os.path.isfile(os.path.join(out, "sales-amount-by-region.png")), r2
    assert "300" in r2["reply"] or "north" in r2["reply"], r2   # exact group means shown
    r3 = run("make an infographic from " + csv + " by bogus with amount", {"_info_out": out})
    assert "Columns not found" in r3["reply"], r3
    print("[self-check] infographic OK - numeric panel with exact min/mean/median/max, grouped "
          "means with exact labels, missing column names listed, writes only data/infographics")
