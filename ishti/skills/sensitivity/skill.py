"""Sensitivity analysis (inventory domain 6 #33): one variable at a time, exact math.

"sensitivity analysis of f(x) = 30*x - 1000, x from 10 to 50 step 5"
"sensitivity of profit = -2*q**2 + 400*q - 5000, q from 50 to 150 step 10"

The function is parsed with sympy, values and the exact derivative d f/dx are computed
symbolically, and the elasticity (d f/dx · x/f — percent change in f per percent change in x)
is printed per point. Where f crosses zero the elasticity is reported as undefined instead of
a fake number. T0, sympy.
"""
import re

from ishti.skills import trace

I = re.I

SENS = re.compile(
    r"^\s*sensitivity\s+(?:analysis\s+)?(?:of\s+)?(?:f\s*\(\s*(?P<v1>\w+)\s*\)\s*=|"
    r"(?P<name>\w+)\s*=)\s*(?P<expr>[^,]+),\s*(?P<var>\w+)\s+from\s+"
    r"(?P<lo>-?\d+(?:\.\d+)?)\s+to\s+(?P<hi>-?\d+(?:\.\d+)?)"
    r"(?:\s+step\s+(?P<step>-?\d+(?:\.\d+)?))?\s*$", I)


def run_analysis(expr_txt, var, lo, hi, step):
    try:
        import sympy as sp
    except ImportError:
        return "sympy isn't installed."
    x = sp.Symbol(var)
    try:
        expr = sp.sympify(expr_txt, locals={var: x})
        dexpr = sp.diff(expr, x)
    except (sp.SympifyError, TypeError, ValueError) as exc:
        return f"Could not parse the function: {exc}"
    if step == 0 or (hi - lo) * step <= 0:
        return "Step must be non-zero and point from the low value toward the high one."
    points = []
    v = lo
    while (v <= hi if step > 0 else v >= hi) and len(points) <= 20:
        points.append(v)
        v += step
    lines = [f"**Sensitivity of f({var}) = {expr}** over {var} ∈ [{lo:g}, {hi:g}] "
             f"(step {step:g}) — exact derivative and elasticity:", ""]
    lines.append(f"| {var} | f({var}) | d f/d{var} | elasticity |")
    lines.append("|---|---|---|---|")
    for v in points:
        val = float(expr.subs(x, v))
        der = float(dexpr.subs(x, v))
        if abs(val) < 1e-12:
            el = "undefined (f=0)"
        else:
            el = f"{der * v / val:.3f}"
        lines.append(f"| {v:g} | {val:,.4g} | {der:,.4g} | {el} |")
    lines += ["", "Elasticity = d f/dx · x/f: the % change in f for a 1% change in x. "
              "One variable at a time — interactions between variables are not modelled."]
    return "\n".join(lines)


def match(text):
    return bool(SENS.match(text.strip()))


def run(text, session):
    t = text.strip()
    if (m := SENS.match(t)):
        var = m.group("var")
        step = float(m.group("step")) if m.group("step") else (float(m.group("hi")) - float(m.group("lo"))) / 10
        trace(session, "sensitivity", "analyze")
        return {"reply": run_analysis(m.group("expr"), var, float(m.group("lo")),
                                      float(m.group("hi")), step), "sources": []}
    return None


if __name__ == "__main__":
    assert match("sensitivity analysis of f(x) = 30*x - 1000, x from 10 to 50 step 5")
    assert match("sensitivity of profit = -2*q**2 + 400*q - 5000, q from 50 to 150 step 10")
    assert not match("sensitivity of my teeth to cold")
    assert not match("sensitivity analysis of f(x) = 3x")   # no range

    r = run("sensitivity analysis of f(x) = 30*x - 1000, x from 10 to 50 step 5", {})
    assert "d f/dx" in r["reply"], r
    assert "| 10 | -700" in r["reply"], r                     # exact: 30*10-1000 = -700
    assert "| 50 | 500" in r["reply"], r
    assert "undefined" not in r["reply"], r
    # a sampled point where f=0 -> an undefined elasticity must appear
    r2 = run("sensitivity of f(x) = 2*x - 20, x from 5 to 15 step 5", {})
    assert "undefined (f=0)" in r2["reply"], r2
    # derivative of x**2 is 2x, spot-checked through the table
    r3 = run("sensitivity of f(x) = x**2, x from 1 to 5 step 2", {})
    assert "| 3 | 9 | 6" in r3["reply"], r3                   # f(3)=9, f'(3)=6
    r4 = run("sensitivity of f(x) = 30*x, x from 10 to 50 step 0", {})
    assert "Step must" in r4["reply"], r4
    print("[self-check] sensitivity OK - sympy-exact values and derivatives, elasticities per "
          "point, f=0 reported as undefined elasticity rather than a fake number")
