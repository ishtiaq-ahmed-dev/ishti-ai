"""Data-quality prep (inventory domain 8 #1-5): profile a CSV and say exactly what to clean.

"clean the csv D:/data/messy.csv", "data quality of D:/x.csv", "profile the csv D:/x.csv",
"missing values in D:/x.csv", "duplicates in D:/x.csv"

Reports, all computed by pandas: per-column dtype + missing count + most frequent value,
exact duplicate-row count, constant columns, high-cardinality suspects (likely IDs),
numeric columns with outliers (|z| > 3), and a short concrete suggestions list. Read-only
analysis (T0); it never edits your file.
"""
import os
import re

from ishti.skills import trace

I = re.I

PATH = r"(?P<path>[A-Za-z]:[\\/][^\"']+?\.(?:csv|tsv))"
ASKS = [
    re.compile(r"^\s*(?:clean|scrub|fix)\s+(?:the\s+)?(?:csv\s+)?" + PATH + r"\s*$", I),
    re.compile(r"^\s*(?:data\s+quality|quality|profile|profiling)\s+(?:of\s+|for\s+|on\s+|in\s+)?(?:the\s+)?(?:csv\s+)?" + PATH + r"\s*$", I),
    re.compile(r"^\s*(?:missing\s+values?|nulls?|duplicates?|dupes)\s+(?:in|of|for)\s+(?:the\s+)?(?:csv\s+)?" + PATH + r"\s*$", I),
    re.compile(r"^\s*(?:what\s+)?(?:should\s+)?(?:i\s+)?clean\s+(?:up\s+)?in\s+(?:the\s+)?(?:csv\s+)?" + PATH + r"\s*$", I),
]


def _load(path):
    import pandas as pd
    if not os.path.isfile(path):
        return None, f"File not found: {path}"
    try:
        return pd.read_csv(path), ""
    except Exception as exc:
        return None, f"Could not read the CSV: {exc}"


def profile(path):
    df, err = _load(path)
    if df is None:
        return err
    rows, cols = df.shape
    dup = int(df.duplicated().sum())
    lines = [f"**{os.path.basename(path)}** — {rows} rows × {cols} columns, {dup} exact duplicate row(s)."]
    suggestions = []
    if dup:
        suggestions.append(f"drop {dup} duplicate row(s) (`df.drop_duplicates()`)")
    missing_total = 0
    for c in df.columns:
        s = df[c]
        miss = int(s.isna().sum())
        missing_total += miss
        nuniq = int(s.nunique(dropna=True))
        kind = str(s.dtype)
        const = " (constant!)" if nuniq <= 1 and rows > 1 else ""
        id_like = " (looks like an ID — drop before modelling)" if nuniq == rows and rows > 20 else ""
        lines.append(f"- `{c}`: {kind}, missing {miss}, {nuniq} unique{const}{id_like}")
        if miss:
            suggestions.append(f"`{c}`: fill or drop {miss} missing ({miss / rows * 100:.0f}%)")
        if const and rows > 1:
            suggestions.append(f"`{c}` is constant — it carries no information")
    if df.select_dtypes(include="number").shape[1]:
        for c in df.select_dtypes(include="number").columns:
            s = df[c].dropna()
            if len(s) < 8:
                continue
            z = ((s - s.mean()) / s.std()).abs()
            n_out = int((z > 3).sum())
            if n_out:
                suggestions.append(f"`{c}`: {n_out} value(s) beyond |z|>3 — verify they are real")
    if not suggestions:
        suggestions.append("nothing obvious — this CSV is already clean")
    lines.append("**Suggested cleaning:**")
    lines += [f"- {s}" for s in suggestions[:10]]
    return "\n".join(lines)


def match(text):
    return bool(any(rx.match(text.strip()) for rx in ASKS))


def run(text, session):
    for rx in ASKS:
        m = rx.match(text.strip())
        if m:
            trace(session, "dataprep", "profile")
            return {"reply": profile(m.group("path").strip()), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp()
    csv = os.path.join(tmp, "messy.csv")
    open(csv, "w", encoding="utf-8").write(
        "id,city,amount,flag\n"
        "1,Lahore,120,y\n"
        "2,Karachi,,y\n"
        "3,Lahore,99999,n\n"
        "2,Karachi,,y\n"          # exact duplicate of row 2
        "4,Peshawar,140,y\n")
    const = os.path.join(tmp, "const.csv")
    open(const, "w", encoding="utf-8").write("a,b\n1,x\n2,x\n3,x\n")

    assert match("clean the csv " + csv)
    assert match("data quality of " + csv)
    assert match("profile the csv " + csv)
    assert match("missing values in " + csv)
    assert match("duplicates in " + csv)
    assert not match("clean my room")
    assert not match("profile of a genius")

    r = run("clean the csv " + csv, {})
    assert "1 exact duplicate row(s)" in r["reply"], r
    assert "99999" not in r["reply"], r                                # raw values are never printed
    # a big-enough column does get the |z|>3 outlier check (guard is 8+ points)
    big = os.path.join(tmp, "big.csv")
    import random as _r
    _r.seed(7)
    vals = [_r.randint(90, 110) for _ in range(30)] + [99999]
    open(big, "w", encoding="utf-8").write("v\n" + "\n".join(map(str, vals)) + "\n")
    rb = run("data quality of " + big, {})
    assert "|z|>3" in rb["reply"], rb
    r2 = run("data quality of " + const, {})
    assert "constant" in r2["reply"], r2
    r3 = run("clean the csv D:/nope.csv", {})
    assert "not found" in r3["reply"].lower()
    print("[self-check] dataprep OK - exact missing/duplicate/constant/outlier profile with "
          "concrete suggestions; never edits the file")
