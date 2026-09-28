"""CSV data analysis (pandas — exact numbers, no LLM arithmetic).

"stats D:/data/sales.csv", "summarize the csv D:/x.csv",
"correlations in D:/x.csv", "find anomalies in D:/x.csv",
"cluster D:/x.csv into 3 groups".
Every number is computed by pandas; the reply is a table the dashboard renders.
"""
import os
import re

from ishti.skills import trace

I = re.I

PATH = r"(?P<path>[A-Za-z]:[\\/][^\"']+\.(?:csv|tsv)|[\w./\\ -]+\.(?:csv|tsv))"  # both / and \\ — the owner types D:/data/sales.csv
SUMMARY = re.compile(r"^\s*(?:stats|summarize|summarise|describe)\s+(?:the\s+)?(?:csv\s+)?" + PATH + r"\s*$", I)
CORR = re.compile(r"^\s*(?:correlations?|correlate)\s+(?:in|for|of)\s+" + PATH + r"\s*$", I)
ANOM = re.compile(r"^\s*(?:find\s+)?anomal(?:ies|y)\s+(?:in|for)\s+" + PATH + r"\s*$", I)
CLUST = re.compile(r"^\s*(?:cluster|group)\s+" + PATH + r"\s+(?:into\s+)?(?P<k>\d{1,2})\s+groups?\s*$", I)


def _load(path):
    import pandas as pd
    if not os.path.isfile(path):
        return None
    return pd.read_csv(path)


def _num_cols(df):
    return df.select_dtypes(include="number").columns.tolist()


def _summary(path):
    import pandas as pd
    df = _load(path)
    if df is None:
        return f"File not found: {path}"
    rows, cols = df.shape
    out = [f"**{os.path.basename(path)}** — {rows} rows × {cols} columns.",
           f"Columns: {', '.join(map(str, df.columns))}"]
    nums = _num_cols(df)
    if nums:
        desc = df[nums].describe().T[["mean", "std", "min", "max"]]
        out.append("| Column | Mean | Std | Min | Max |\n|---|---|---|---|---|")
        out += [f"| {c} | {r['mean']:.3g} | {r['std']:.3g} | {r['min']:.3g} | {r['max']:.3g} |"
                for c, r in desc.iterrows()]
    missing = df.isna().sum()
    miss = missing[missing > 0]
    if len(miss):
        out.append("Missing: " + ", ".join(f"{c} ({v})" for c, v in miss.items()))
    out.append(f"{len(nums)} numeric, {cols - len(nums)} non-numeric columns.")
    return "\n".join(out)


def _corr(path):
    df = _load(path)
    if df is None:
        return f"File not found: {path}"
    nums = _num_cols(df)
    if len(nums) < 2:
        return "Need at least 2 numeric columns for correlations."
    c = df[nums].corr()
    pairs = []
    for i, a in enumerate(nums):
        for b in nums[i + 1:]:
            pairs.append((abs(c.loc[a, b]), c.loc[a, b], a, b))
    pairs.sort(reverse=True)
    if not pairs:
        return "No column pairs to correlate."
    lines = [f"| {a} ↔ {b} | r = {v:+.2f} |" for _av, v, a, b in pairs[:10]]
    strong = [p for p in pairs if p[0] >= 0.7]
    head = f"Top correlations in **{os.path.basename(path)}**" + \
        (f" — {len(strong)} strong (|r| ≥ 0.7):" if strong else " (none strong, |r| ≥ 0.7):")
    return head + "\n| Pair | Pearson r |\n|---|---|\n" + "\n".join(lines)


def _anoms(path):
    df = _load(path)
    if df is None:
        return f"File not found: {path}"
    nums = _num_cols(df)
    if not nums:
        return "No numeric columns to scan for anomalies."
    found = []
    for col in nums:
        s = df[col].dropna()
        if len(s) < 8:
            continue
        z = ((s - s.mean()) / s.std()).abs()
        out = s[z > 3]
        for idx, val in out.items():
            found.append((idx, col, val, float(z.loc[idx])))
    if not found:
        return f"No z-score > 3 anomalies in **{os.path.basename(path)}**."
    lines = [f"| row {int(idx)} | {col} = {val:.4g} | z = {zv:.1f} |" for idx, col, val, zv in found[:15]]
    return (f"**{len(found)} anomal{'y' if len(found) == 1 else 'ies'}** (|z| > 3) in "
            f"**{os.path.basename(path)}**:\n| Row | Value | Score |\n|---|---|---|\n" + "\n".join(lines))


def _clusters(path, k):
    df = _load(path)
    if df is None:
        return f"File not found: {path}"
    try:
        from sklearn.cluster import KMeans
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        return "scikit-learn isn't installed yet — say \"install scikit-learn in ISHTI's python\" or use the z-score anomaly scan."
    nums = _num_cols(df)
    if len(nums) < 2:
        return "Need at least 2 numeric columns to cluster."
    k = max(2, min(int(k), 8))
    X = StandardScaler().fit_transform(df[nums].dropna())
    labels = KMeans(n_clusters=k, n_init=10, random_state=0).fit_predict(X)
    lines = []
    for g in range(k):
        size = int((labels == g).sum())
        centre = X[labels == g].mean(axis=0)
        top = nums[int(abs(centre).argmax())]
        lines.append(f"- Cluster {g + 1}: **{size} rows**, most-deviating column: {top}")
    return f"K-means (k={k}) over {', '.join(nums)}:\n" + "\n".join(lines) + \
        "\nStandardised features; sizes are exact row counts."


def match(text):
    t = text.strip()
    return bool(SUMMARY.match(t) or CORR.match(t) or ANOM.match(t) or CLUST.match(t))


def run(text, session):
    t = text.strip()
    m = SUMMARY.match(t)
    if m:
        trace(session, "stats", "summary")
        return {"reply": _summary(m.group("path").strip()), "sources": []}
    m = CORR.match(t)
    if m:
        trace(session, "stats", "correlations")
        return {"reply": _corr(m.group("path").strip()), "sources": []}
    m = ANOM.match(t)
    if m:
        trace(session, "stats", "anomaly scan")
        return {"reply": _anoms(m.group("path").strip()), "sources": []}
    m = CLUST.match(t)
    if m:
        trace(session, "stats", f"k-means k={m.group('k')}")
        return {"reply": _clusters(m.group("path").strip(), m.group("k")), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    import textwrap
    tmp = tempfile.mkdtemp()
    csv = os.path.join(tmp, "sales.csv")
    rows = ["day,units,price"]
    for i in range(1, 31):
        rows.append(f"{i},{10 + i},2.5")
    rows.append("31,900,2.5")   # obvious outlier
    with open(csv, "w", encoding="utf-8") as f:
        f.write("\n".join(rows))
    P = csv.replace("\\", "\\\\") if False else csv
    assert match("stats " + csv)
    assert match("summarize the csv " + csv)
    assert match("correlations in " + csv)
    assert match("find anomalies in " + csv)
    assert match("cluster " + csv + " into 3 groups")
    assert match("stats D:/data/sales.csv")          # forward slashes (the natural typing) — laya must not steal this to files
    assert match("correlations in D:/data/sales.csv")
    assert match("find anomalies in D:/data/sales.csv")
    assert match("cluster D:/data/sales.csv into 3 groups")
    assert not match("stats D:/notes.txt")            # not a csv/tsv
    assert not match("analyze this text: hello world")   # textstats
    r = run("stats " + csv, {"trace": None})
    assert "31 rows" in r["reply"] and "units" in r["reply"], r
    r2 = run("correlations in " + csv, {"trace": None})
    assert "Pearson" in r2["reply"], r2
    r3 = run("find anomalies in " + csv, {"trace": None})
    assert "900" in r3["reply"] and "z =" in r3["reply"], r3
    r4 = run("cluster " + csv + " into 2 groups", {"trace": None})
    assert ("Cluster 1" in r4["reply"]) or ("scikit-learn" in r4["reply"]), r4
    r5 = run("stats " + os.path.join(tmp, "nope.csv"), {"trace": None})
    assert "not found" in r5["reply"].lower(), r5
    print("[self-check] stats OK - summary/corr/anomalies/clusters exact from pandas, no LLM arithmetic")
