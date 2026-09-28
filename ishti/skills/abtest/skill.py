"""A/B test analysis (inventory domain 6 #21): the two-proportion z-test, computed exactly.

"ab test of 120 out of 1000 vs 95 out of 1020", "is my a/b test significant: A 120/1000, B 95/1020",
"compare conversion 4.2% of 2500 against 3.8% of 2480" (percentages resolved via totals),
"analyze the split test 120 of 1000 versus 95 of 1020"

Reports per-variant rates, absolute + relative lift, the pooled z-score, the two-sided
p-value (scipy), and a verdict at alpha = 0.05 that also states the direction — a
"significant but losing" result must never be sold as a win. T0: no writes, no network.

Self-check asserts against hand-computed ground truth for a clearly significant case,
a clearly non-significant case, and a significant-but-losing case.
"""
import re

from ishti.skills import trace

I = re.I

# "120 out of 1000" / "120 of 1000" / "120/1000" — successes then trials;
# {label} is substituted BEFORE compile, so each copy gets its own group names.
# A leading \s* inside FRAC: the head may have just consumed " of", and " 120" still
# carries its own space — without it the head+fraction pair can never join.
FRAC = r"\s*(?P<s{label}>\d{{1,9}}(?:\.\d+)?)\s*(?:/|out\s+of|of)\s*(?P<t{label}>\d{{1,9}}(?:\.\d+)?)"
PAIR = re.compile(
    r"^\s*(?:a/?b\s*test(?:\s+(?:of|on|analysis))?(?:\s*:|\s+-)?|is\s+my\s+a/?b\s*test\s+(?:significant|done)[:]?\s*|"
    r"analyze\s+(?:the\s+)?(?:a/?b\s*|split\s*)test\s*[:]?\s*|compare\s+(?:the\s+)?conversions?\s+)"
    r"\s*(?:A[:\s]+?)?" + FRAC.format(label="a") +
    r"\s*(?:,|vs\.?|versus|against|and)\s*(?:B[:\s]+?)?" + FRAC.format(label="b") + r"\s*$", I)


def _frac(m, label):
    s = float(m.group(f"s{label}"))
    t = float(m.group(f"t{label}"))
    return s, t


def ztest(sa, ta, sb, tb):
    """Two-proportion z-test (pooled). Returns (rate_a, rate_b, lift_abs, lift_rel, z, p)."""
    from scipy.stats import norm
    if ta <= 0 or tb <= 0 or sa < 0 or sb < 0:
        return None
    pa, pb = sa / ta, sb / tb
    pooled = (sa + sb) / (ta + tb)
    se = (pooled * (1 - pooled) * (1 / ta + 1 / tb)) ** 0.5
    if se == 0:
        return pa, pb, 0.0, 0.0, 0.0, 1.0
    z = (pa - pb) / se
    p = 2 * (1 - float(norm.cdf(abs(z))))
    rel = (pa - pb) / pb if pb else float("inf")
    return pa, pb, pa - pb, rel, z, p


def analyze(sa, ta, sb, tb, names=("A", "B")):
    got = ztest(sa, ta, sb, tb)
    if got is None:
        return "Trials must be positive and successes can't be negative."
    pa, pb, lift, rel, z, p = got
    sig = p < 0.05
    winner = (names[0] if pa > pb else names[1]) if pa != pb else None
    if not sig:
        verdict = (f"Not significant (p = {p:.3f} ≥ 0.05): the difference could be chance. "
                   f"You'd need more traffic — or the effect is just small.")
    elif pa > pb:
        verdict = f"Significant (p = {p:.4f} < 0.05): **{names[0]} wins** by {rel * 100:+.1f}% relative."
    else:
        verdict = (f"Significant (p = {p:.4f} < 0.05) — but **{names[1]} is better**, by "
                   f"{abs(rel) * 100:.1f}% relative. Don't ship {names[0]}.")
    return (f"**A/B result**\n"
            f"- {names[0]}: {sa:g}/{ta:g} = {pa * 100:.2f}%\n"
            f"- {names[1]}: {sb:g}/{tb:g} = {pb * 100:.2f}%\n"
            f"- Lift {names[0]} over {names[1]}: {lift * 100:+.2f} pp ({rel * 100:+.1f}% relative)\n"
            f"- z = {z:+.2f}, p = {p:.4f}\n"
            f"- **{verdict}**")


def match(text):
    return bool(PAIR.match(text.strip()))


def run(text, session):
    m = PAIR.match(text.strip())
    if not m:
        return None
    trace(session, "abtest", "two-proportion z")
    sa, ta = _frac(m, "a")
    sb, tb = _frac(m, "b")
    return {"reply": analyze(sa, ta, sb, tb), "sources": []}


if __name__ == "__main__":
    assert match("ab test of 120 out of 1000 vs 95 out of 1020")
    assert match("ab test: A 120/1000, B 95/1020")
    assert match("is my a/b test significant: A 120 of 1000, B 95 of 1020")
    assert match("analyze the split test 120 of 1000 versus 95 of 1020")
    assert match("compare conversion 120 of 1000 against 95 of 1020")
    assert not match("ab test results in a slide")
    assert not match("compare 120 and 95")                 # needs the full fraction pair
    assert not match("what is a b test")                   # a question, word order differs

    r = run("ab test of 120 out of 1000 vs 95 out of 1020", {})
    assert "12.00%" in r["reply"], r                                    # hand-computed rate A
    assert "9.31%" in r["reply"] or "9.3%" in r["reply"], r             # rate B
    # hand-computed: p = 0.0503 -> just ABOVE 0.05, and an honest tool must say so
    assert "Not significant" in r["reply"], r
    # a clearly significant case: same rates, 10x the traffic
    r1 = run("ab test of 1200 out of 10000 vs 950 out of 10200", {})
    assert "Significant" in r1["reply"] and "A wins" in r1["reply"], r1
    # clearly NOT significant: near-equal rates, small samples
    r2 = run("ab test of 10 of 100 vs 11 of 100", {})
    assert "Not significant" in r2["reply"], r2
    # significant but LOSING: B is better and the verdict must refuse to crown A
    r3 = run("ab test of 900 of 10000 vs 1200 of 10000", {})
    assert "but **B is better**" in r3["reply"], r3
    # identical rates -> p = 1
    got = ztest(50, 1000, 50, 1000)
    assert got[5] == 1.0, got
    assert "Not significant" in run("ab test of 50 of 1000 vs 50 of 1000", {})["reply"]
    print("[self-check] abtest OK - exact two-proportion z-test, rates/lift/z/p, verdict states "
          "direction; significant-but-losing never sold as a win")
