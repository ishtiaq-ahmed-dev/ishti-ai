"""Chart generation (inventory domain 8 #33): a CSV column -> a real PNG on disk.

"bar chart of D:/data/sales.csv by region with amount", "line chart of D:/visitors.csv",
"histogram of D:/data/ages.csv with age", "chart the csv D:/x.csv"

Kinds: bar (categories vs values), line (values in file order), histogram (distribution).
The PNG is written to data/charts/<name>.png and the reply gives the exact path plus the
min/mean/max it can see in the plotted column. matplotlib's Agg backend: no window opens.
Self-check redirects the output dir into a tempdir and asserts a real PNG exists.
"""
import os
import re

from ishti.skills import trace

I = re.I

PATH = r"(?P<path>[A-Za-z]:[\\/][^\"']+?\.(?:csv|tsv))"
# the column name is greedy: a lazy name would eat the "csv" out of the path's own extension
COL = r"(?:\s+(?:by|with|using|for)\s+(?:the\s+)?(?:column\s+)?(?P<col>[\w -]+))?"
CHART = re.compile(
    r"^\s*(?P<kind>bar|line|pie|histogram|hist|column)\s*(?:chart|plot|graph)?\s+(?:of|for|from)\s+" + PATH +
    r"(?:\s+by\s+(?:the\s+)?(?:column\s+)?(?P<col>[\w -]+?))?(?:\s+with\s+(?:the\s+)?(?:column\s+)?(?P<col2>[\w -]+))?\s*$", I)
CHART2 = re.compile(r"^\s*(?:chart|plot|graph)\s+(?:the\s+)?(?:csv\s+)?" + PATH + COL + r"\s*$", I)
CHART3 = re.compile(r"^\s*(?:make|create|draw|generate)\s+(?:me\s+)?(?:a\s+)?(?P<kind2>bar|line|histogram)\s+(?:chart|plot)\s+(?:of|for|from)\s+" + PATH + COL + r"\s*$", I)
HEAD = re.compile(r"^\s*(?:show|open)\s+(?:the\s+)?(?:last\s+|latest\s+)?charts?\s*$", I)


def _out_dir(session=None):
    d = (session or {}).get("_chart_out") or os.path.join("data", "charts")
    os.makedirs(d, exist_ok=True)
    return d


def _columns(path):
    import pandas as pd
    if not os.path.isfile(path):
        return None, []
    try:
        df = pd.read_csv(path)
    except Exception:
        return None, []
    return df, [str(c) for c in df.columns]


def _pick(df, colname, numeric=True):
    """Resolve a column by fuzzy contains, else first numeric (or first non-numeric for x)."""
    if colname:
        for c in df.columns:
            if str(c).lower().strip() == colname.lower().strip():
                return c
        for c in df.columns:
            if colname.lower() in str(c).lower():
                return c
        return None
    if numeric:
        nums = df.select_dtypes(include="number")
        return nums.columns[0] if not nums.empty else None
    return df.columns[0]


def make_chart(path, kind, colname, session=None):
    df, cols = _columns(path)
    if df is None:
        return f"File not found: {path}"
    if df.empty:
        return f"No data rows in {os.path.basename(path)}."
    try:
        import matplotlib
        matplotlib.use("Agg")           # never open a window
        import matplotlib.pyplot as plt
    except ImportError:
        return "matplotlib isn't installed — say \"install matplotlib in ISHTI's python\"."
    kind = {"hist": "histogram", "column": "bar", "pie": "bar"}.get(kind or "", kind or "line")
    if kind not in ("bar", "line", "histogram"):
        kind = "line"
    y = _pick(df, colname, numeric=(kind != "bar"))
    if y is None:
        return f"No numeric column found in {os.path.basename(path)} (columns: {', '.join(cols)})."
    vals = df[y]
    out_dir = _out_dir(session)
    stem = re.sub(r"[^\w-]", "_", os.path.splitext(os.path.basename(path))[0])
    fp = os.path.join(out_dir, f"{stem}-{kind}.png")
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=120)
    if kind == "bar":
        # keep the category axis readable: at most 24 bars, top values win
        counts = vals.value_counts().head(24) if vals.dtype == object else vals.head(24)
        ax.bar([str(i)[:14] for i in counts.index], counts.values, color="#4a7dbd")
        ax.set_ylabel(str(y))
    elif kind == "line":
        nums = vals.dropna()
        ax.plot(range(len(nums)), nums.values, color="#4a7dbd", linewidth=1.8)
        ax.set_ylabel(str(y))
    else:  # histogram
        nums = vals.dropna()
        ax.hist(nums, bins=min(20, max(5, len(nums) // 3)), color="#4a7dbd", edgecolor="white")
    ax.set_title(f"{kind} of {os.path.basename(path)}" + (f" — {y}" if y is not None else ""), fontsize=11)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(fp)
    plt.close(fig)
    nums = vals.select_dtypes(include="number") if hasattr(vals, "select_dtypes") else vals
    try:
        stats = f"min {nums.min():.4g}, mean {nums.mean():.4g}, max {nums.max():.4g}"
    except (TypeError, ValueError):
        stats = f"{len(vals)} values"
    return (f"**{kind} chart** saved to `{fp}` — `{y}` from {os.path.basename(path)} ({stats}).\n"
            f"Open it from the dashboard or the folder; every plotted value comes from the file.")


def recent(session=None):
    d = _out_dir(session)
    files = sorted((f for f in os.listdir(d) if f.endswith(".png")),
                   key=lambda f: os.path.getmtime(os.path.join(d, f)), reverse=True) if os.path.isdir(d) else []
    if not files:
        return "No charts yet — say \"bar chart of D:/data/sales.csv\"."
    return (f"{len(files)} chart(s) in `{d}` (newest first):\n"
            + "\n".join(f"- {f}" for f in files[:10]))


def match(text):
    t = text.strip()
    if HEAD.match(t):
        return True
    return bool(CHART.match(t) or CHART2.match(t) or CHART3.match(t))


def run(text, session):
    t = text.strip()
    if HEAD.match(t):
        trace(session, "chart", "list")
        return {"reply": recent(session), "sources": []}
    m = CHART.match(t) or CHART2.match(t) or CHART3.match(t)
    if not m:
        return None
    trace(session, "chart", "render")
    kind = (m.groupdict().get("kind") or m.groupdict().get("kind2") or
            ("line" if CHART2.match(t) else "bar"))
    col = m.groupdict().get("col") or m.groupdict().get("col2")
    path = m.group("path").strip()
    return {"reply": make_chart(path, kind, col, session), "sources": []}


if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "out")
    csv = os.path.join(tmp, "sales.csv")
    open(csv, "w", encoding="utf-8").write(
        "region,amount\nnorth,120\nsouth,340\neast,90\nwest,260\nnorth,180\n")
    ages = os.path.join(tmp, "ages.csv")
    open(ages, "w", encoding="utf-8").write(
        "age\n" + "\n".join(str(20 + (i * 7) % 40) for i in range(60)) + "\n")

    assert match("bar chart of " + csv)
    assert match("bar chart of " + csv + " by region with amount")
    assert match("line chart of " + csv)
    assert match("histogram of " + ages)
    assert match("chart the csv " + csv)
    assert match("create a bar chart of " + csv)
    assert match("show charts")
    assert not match("chart a course for success")     # no csv -> not ours
    assert not match("what is a bar chart")            # a question

    r = run("bar chart of " + csv + " by region with amount", {"_chart_out": out})
    fp = os.path.join(out, "sales-bar.png")
    assert os.path.isfile(fp) and "bar chart" in r["reply"].lower(), (r, os.listdir(out))
    assert open(fp, "rb").read(8).startswith(b"\x89PNG"), "must be a real PNG"
    r2 = run("histogram of " + ages, {"_chart_out": out})
    fp2 = os.path.join(out, "ages-histogram.png")
    assert os.path.isfile(fp2), (r2, os.listdir(out))
    assert "min " in r2["reply"] and "max " in r2["reply"], r2
    r3 = run("line chart of " + csv, {"_chart_out": out})
    assert os.path.isfile(os.path.join(out, "sales-line.png")), (r3, os.listdir(out))
    r4 = run("show charts", {"_chart_out": out})
    assert "chart(s)" in r4["reply"] and "sales-bar.png" in r4["reply"], r4
    r5 = run("bar chart of D:/nope.csv", {})
    assert "not found" in r5["reply"].lower()
    print("[self-check] chart OK - bar/line/histogram PNGs in data/charts via Agg backend, "
          "exact paths and stats in the reply")
