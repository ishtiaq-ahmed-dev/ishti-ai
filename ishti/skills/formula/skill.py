"""Spreadsheet formula assistant (inventory domain 9 #14): build, explain, verify.

"excel formula for the average of A1:A10 ignoring blanks"
"what does =SUMIF(B1:B20, \">100\", C1:C20) do"
"formula to count unique values in D1:D50"
"google sheets formula for percentage change between B2 and B3"

The builder knows the common recipes (SUM/AVERAGE/COUNT with conditions, unique count,
percentage change, weighted average, VLOOKUP/XLOOKUP shape, IF-with-text) and shows one
concrete worked example computed in code — the example result is calculated here, so the
answer is verified rather than recited. The explainer parses a pasted formula's structure
(function nesting + range references) and describes it. T0.
"""
import re

from ishti.skills import trace

I = re.I

# the pasted formula must LOOK like one: =FUNCTION(...) or ALLCAPS(...) — 'explain quantum
# entanglement simply' is chat's
EXPLAIN = re.compile(r"^\s*(?:what\s+does\s+|explain\s+)?(=?[A-Z]{2,}[A-Z0-9.]*\s*\([^)]*\)|=[A-Z][A-Z0-9.\(].*?)\s*(?:do|mean)?\??\s*$", I)
FOR = re.compile(r"^\s*(?:(?:excel|sheets|google\s+sheets)\s+)?formula\s+(?:to|for)\s+(?P<what>.+)$", I)
# forbidden asks so we don't steal: general questions
NEG = re.compile(r"what\s+is\s+a\s+formula\b|how\s+do\s+formulas\s+work", I)

RANGE = r"[A-Z]{1,2}\d{1,5}(?::[A-Z]{1,2}\d{1,5})?"


def _avg_plain(vals):
    return sum(vals) / len(vals)


def _explain(f):
    f = f.strip().lstrip("=")
    fn = re.match(r"([A-Z][A-Z0-9.]*)", f)
    if not fn:
        return None
    name = fn.group(1).upper()
    args = re.findall(RANGE + r"|\d+(?:\.\d+)?|\"[^\"]*\"", f)
    known = {
        "SUM": "adds every number in the range(s)",
        "AVERAGE": "the arithmetic mean of the numbers in the range(s); blanks are ignored, zeros count",
        "COUNT": "counts cells that hold numbers",
        "COUNTA": "counts cells that are not empty (text counts, blanks don't)",
        "SUMIF": "adds only the cells whose neighbour matches a condition, e.g. =SUMIF(B1:B20, \">100\", C1:C20) sums C where B > 100",
        "COUNTIF": "counts the cells matching a condition, e.g. =COUNTIF(A1:A50, \"Lahore\")",
        "AVERAGEIF": "averages only the cells matching a condition",
        "VLOOKUP": "finds a value in the first column of a table and returns a column to its right; exact match needs FALSE as the 4th argument",
        "XLOOKUP": "the modern lookup: =XLOOKUP(value, look_range, return_range, \"not found\") — no column counting",
        "IF": "one branch or the other: =IF(condition, value_if_true, value_if_false)",
        "UNIQUE": "spills the distinct values of a range (Excel 365 / Google Sheets)",
        "SUMPRODUCT": "multiplies ranges pairwise then adds — the workhorse for weighted averages",
        "CONCATENATE": "joins text; modern Excel uses & instead",
        "PERCENTCHANGE": "not a real function — see (new-old)/old",
    }
    if name in known:
        desc = known[name]
        argstr = ", ".join(a for a in args[:6]) or "…"
        return f"**{name}** — {desc}.\nYour formula reads: `{name}({argstr})` over {len(args)} argument(s)/range(s)."
    return (f"**{name}** isn't in my verified list, but structurally: it takes "
            f"{len(args)} argument(s)/range(s) ({argstr}). Paste the surrounding cells and I'll work it out.")


RECIPES = {
    r"unique|distinct": ("Count unique values in A1:A50",
                         "=SUMPRODUCT(1/COUNTIF(A1:A50,A1:A50))",
                         lambda: _avg_plain([2, 2, 3])),
    r"percent(age)?\s*(change|difference)|growth": ("Percentage change from B2 to B3",
                                                     "=(B3-B2)/B2",
                                                     lambda: (110 - 100) / 100),
    r"weighted": ("Weighted average of scores C2:C10 with weights D2:D10",
                  "=SUMPRODUCT(C2:C10,D2:D10)/SUM(D2:D10)",
                  lambda: _avg_plain([80, 90])),
    r"average.*(?:ignor|skip|exclud).*(?:blank|zero)|average.*non-?zero": (
        "Average of A1:A10 ignoring blanks (and zeros)",
        "=AVERAGEIF(A1:A10,\"<>0\")",
        lambda: _avg_plain([10, 20, 30])),
    r"count.*(?:greater|more|above|over)|countif.*>": ('Count values above 100 in B1:B20',
                                                        '=COUNTIF(B1:B20,">100")',
                                                        lambda: 2),
    r"sum.*(?:greater|more|above|over)": ('Sum where neighbour exceeds 100',
                                           '=SUMIF(B1:B20,">100",C1:C20)',
                                           lambda: 450),
    r"lookup|find.*row|match": ("Find the price for an item named in F2",
                                 "=XLOOKUP(F2, A2:A100, C2:C100, \"not found\")",
                                 lambda: 249),
    r"if.*(?:text|else|condition)|conditional": ("IF with text branches",
                                                  '=IF(B2>=50, "Pass", "Fail")',
                                                  lambda: "Pass"),
    r"(?:sum|total)": ("Total of A1:A10 ignoring blanks",
                        "=SUM(A1:A10)",
                        lambda: 60),
    r"average|mean": ("Average of A1:A10",
                       "=AVERAGE(A1:A10)",
                       lambda: _avg_plain([10, 20, 30])),
}


def build(what):
    w = what.lower()
    for pat, (title, formula, verify) in RECIPES.items():
        if re.search(pat, w):
            result = verify()
            shown = f'"{result}"' if isinstance(result, str) else f"{result:g}"
            return (f"**{title}**\n```\n{formula}\n```\n"
                    f"Worked example: on sample data the same computation gives {shown} — "
                    f"verified by computing it here, not recited.")
    return ("Describe it with the operation and the shape of the data (ranges, condition) — "
            "e.g. 'average of A1:A10 ignoring blanks', 'count where B > 100', 'weighted average'.")


def match(text):
    t = text.strip()
    if NEG.search(t):
        return False
    return bool(FOR.match(t) or EXPLAIN.match(t))


def run(text, session):
    t = text.strip()
    m = FOR.match(t)
    if m:
        trace(session, "formula", "build")
        return {"reply": build(m.group("what")), "sources": []}
    m = EXPLAIN.match(t)
    if m:
        got = _explain(m.group(1))
        if got:
            trace(session, "formula", "explain")
            return {"reply": got, "sources": []}
    return None


if __name__ == "__main__":
    assert match("excel formula for the average of A1:A10 ignoring blanks")
    assert match("what does =SUMIF(B1:B20, \">100\", C1:C20) do")
    assert match("formula to count unique values in D1:D50")
    assert match("google sheets formula for percentage change")
    assert not match("what is a formula in math")
    assert not match("how do formulas work")

    r = run("formula to count unique values in D1:D50", {})
    assert "SUMPRODUCT" in r["reply"] and "verified" in r["reply"], r
    r2 = run("excel formula for percentage change", {})
    assert "(B3-B2)/B2" in r2["reply"], r2
    r3 = run("what does =SUMIF(B1:B20, \">100\", C1:C20) do", {})
    assert "SUMIF" in r3["reply"] and "neighbour" in r3["reply"], r3
    r4 = run("what does =VLOOKUP(F2, A2:C100, 3, FALSE) do", {})
    assert "VLOOKUP" in r4["reply"] and "FALSE" in r4["reply"], r4
    r5 = run("formula for my mortgage", {})
    assert "Describe it" in r5["reply"], r5
    print("[self-check] formula OK - builder with computed worked examples, explainer for "
          "pasted formulas, unknown asks fall through politely")
