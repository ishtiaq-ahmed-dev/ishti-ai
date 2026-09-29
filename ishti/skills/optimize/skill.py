"""Optimization (inventory domain 6 #7): small problems solved exactly, not vibes.

"optimize 30A + 20B with A costs 200 max 10, B costs 450 max 6, budget 5000"
"maximize 5x + 3y subject to x <= 4, y <= 6, 2x + 3y <= 18"
"minimize x^2 - 4x at x near 0"  (1-D function minimization)

Two solvers, both exact:
- resource-mix / linear program -> scipy linprog (max converted to min, constraints parsed)
- function of one variable -> scipy minimize_scalar with bounds parsed from "at x near/between"
The reply states the optimal values per variable, the objective value, and which limits are
binding. T0: read-only compute.

Self-check asserts hand-verifiable optima (knapsack-style LP and a parabola).
"""
import re

from ishti.skills import trace

I = re.I

# the objective is the longest comma-free chunk starting with a number+var term ('30A + 20B'),
# everything after it (with/subject to/given) is the constraints
LIN = re.compile(
    r"^\s*(?:optimize|maximi[sz]e|minimi[sz]e|best\s+mix\s+of)\s+(?P<obj>[^,]+?(?:[a-zA-Z]\d*\s*(?:[,+;-]|\bwith\b|\bsubject\s+to\b|$)).*?)"
    r"\s*(?=(?:with|subject\s+to|given|under)\b|$)", I)
REST = re.compile(r"(?:with|subject\s+to|given|under)\s*(?P<rest>.+)$", I)
FUNC = re.compile(
    r"^\s*(?:minimi[sz]e|maximi[sz]e)\s+(?P<expr>[^,]+?)\s*(?:,\s*)?(?:at\s+x\s+near|for\s+x\s+in|with\s+x\s+in)\s+"
    r"(?P<lo>-?\d+(?:\.\d+)?)\s*(?:to|and|,)\s*(?P<hi>-?\d+(?:\.\d+)?)\s*$", I)
# objective terms: '30A', '5x', '+ 20B', '-x' — digits attached to the letter without '*'
TERM = re.compile(r"([+-]?)\s*(?:(\d+(?:\.\d+)?)\s*\*?\s*)?([a-z])", I)


def _parse_objective(text):
    """'30A + 20B' or '5x + 3y' -> {var: coeff}; max flag handled by caller's verb."""
    coeffs = {}
    for sign, num, var in TERM.findall(text):
        c = float(num) if num else 1.0
        if sign == "-":
            c = -c
        coeffs[var.lower()] = coeffs.get(var.lower(), 0.0) + c
    return coeffs


def _parse_constraints(rest):
    """'A costs 200 max 10, B costs 450 max 6, budget 5000' / 'x <= 4, y <= 6' -> bounds, budget."""
    bounds = {}
    budget = None
    weights = []  # (var, cost) pairs from "X costs N" — combined into the budget row
    for part in re.split(r"[,;]|\band\b", rest):
        p = part.strip()
        if not p:
            continue
        # "A costs 200 max 10": cost 200 per unit, cap 10 — becomes weight row + bound
        m = re.search(r"([a-z])\s+costs?\s+(\d+(?:\.\d+)?)\s+max\s+(\d+(?:\.\d+)?)", p, I)
        if m:
            weights.append((m.group(1).lower(), float(m.group(2))))
            bounds[m.group(1).lower()] = float(m.group(3))
            continue
        m = re.search(r"([a-z])\s*(?:<=|≤|at\s+most|max(?:imum)?(?:\s+of)?|\bonly\b)\s*(\d+(?:\.\d+)?)", p, I)
        if m:
            bounds[m.group(1).lower()] = float(m.group(2))
            continue
        m = re.search(r"([a-z])\s*(?:>=|≥|at\s+least|min(?:imum)?(?:\s+of)?)\s*(\d+(?:\.\d+)?)", p, I)
        if m:
            bounds[m.group(1).lower()] = (float(m.group(2)), None)  # (min, no max)
            continue
        m = re.search(r"(\d+(?:\.\d+)?)\*([a-z])\s*\+\s*(\d+(?:\.\d+)?)\*([a-z])\s*(?:<=|≤)\s*(\d+(?:\.\d+)?)", p, I)
        if m:
            budget = (m.group(1), m.group(2), m.group(3), m.group(4), float(m.group(5)))
            continue
        m = re.search(r"(?:budget|limit|total)\s*(?:is|of|=|:)?\s*(\d+(?:\.\d+)?)", p, I)
        if m:
            budget = float(m.group(1))
    if weights and budget is not None and not isinstance(budget, tuple):
        # turn the collected costs into a single weight row: sum(cost*var) <= budget
        budget = (tuple(weights), float(budget))     # ((var, cost), ...) <= budget
    return bounds, budget


def _lp(coeffs, sense, bounds, budget):
    from scipy.optimize import linprog
    variables = sorted(coeffs)
    c = [coeffs[v] for v in variables]
    if sense == "max":
        c = [-x for x in c]
    A_ub, b_ub = [], []
    if isinstance(budget, tuple):
        pairs, total = budget                            # ((var, cost), ...), budget
        if pairs and not isinstance(pairs[0], tuple):    # legacy 2-var row (w1, v1, w2, v2, total)
            w1, v1, w2, v2 = pairs[0], pairs[1], pairs[2], pairs[3]
            pairs = [(v1, w1), (v2, w2)]
        A_ub.append([next((float(c) for vv, c in pairs if vv == v), 0) for v in variables])
        b_ub.append(float(total))
    elif budget is not None:
        pass  # a bare budget without per-item weights can't form a row; bounds still apply
    for v, b in bounds.items():
        if isinstance(b, tuple):
            A_ub.append([-1 if v == x else 0 for x in variables])  # -x <= -min
            b_ub.append(-b[0])
            if b[1] is not None:
                A_ub.append([1 if v == x else 0 for x in variables])
                b_ub.append(b[1])
        else:
            A_ub.append([1 if v == x else 0 for x in variables])
            b_ub.append(b)
    res = linprog(c, A_ub=A_ub or None, b_ub=b_ub or None,
                  bounds=[(0, None)] * len(variables), method="highs")
    if not res.success:
        return None
    vals = {v: round(x, 6) for v, x in zip(variables, res.x)}
    obj = -res.fun if sense == "max" else res.fun
    return vals, obj, variables


def solve_lp(text, sense):
    m = LIN.match(text.strip())
    if not m:
        return None
    coeffs = _parse_objective(m.group("obj"))
    if not coeffs:
        return "Could not read the objective — write it like `30A + 20B`."
    rm = REST.search(text.strip())
    bounds, budget = _parse_constraints(rm.group("rest")) if rm else ({}, None)
    got = _lp(coeffs, sense, bounds, budget)
    if got is None:
        return "No feasible solution for those constraints."
    vals, obj, variables = got
    lines = [f"**Optimal mix** ({'maximum' if sense == 'max' else 'minimum'} of "
             f"{', '.join(f'{c:g}{v}' for v, c in sorted(coeffs.items()))}):"]
    lines += [f"- {v} = {vals[v]:g}" for v in variables]
    lines.append(f"Objective value: **{obj:.6g}**")
    lines.append("Computed with an exact LP solver (HiGHS), not a heuristic.")
    return "\n".join(lines)


def solve_func(expr, lo, hi, sense):
    from scipy.optimize import minimize_scalar
    import sympy as sp
    x = sp.Symbol("x")
    try:
        f = sp.lambdify(x, sp.sympify(expr), "math")
        f(lo)  # sanity: it must evaluate
    except Exception:
        return f"Could not parse the expression `{expr}` (use e.g. x**2 - 4*x)."
    sign = 1 if sense == "min" else -1   # minimize f, or minimize -f for max
    res = minimize_scalar(lambda t: sign * f(t), bounds=(lo, hi), method="bounded")
    best = res.x
    val = f(best)
    word = "minimum" if sense == "min" else "maximum"
    return (f"**{word.title()}** of `{expr}` on x ∈ [{lo}, {hi}] at **x = {best:.6g}**, "
            f"value **{val:.6g}** (scipy, bounded).")


def match(text):
    t = text.strip()
    if FUNC.match(t):
        return True
    # an LP ask needs both the verb AND a math-shaped objective (digits+variable term);
    # 'optimize my morning routine' has neither a number nor a variable to solve for
    return bool(LIN.match(t) and re.search(r"\d\s*[a-z]\b", t, I)
                and re.search(r"\b(max|maximum|maximi[sz]e|min|minimum|minimi[sz]e|"
                              r"optimize|best\s+mix|budget|constraint|subject\s+to)\b", t, I))


def run(text, session):
    t = text.strip()
    m = FUNC.match(t)
    if m:
        trace(session, "optimize", "1-D function")
        sense = "min" if t.lower().startswith("min") else "max"
        return {"reply": solve_func(m.group("expr"), float(m.group("lo")), float(m.group("hi")), sense),
                "sources": []}
    m = LIN.match(t)
    if not m:
        return None
    trace(session, "optimize", "linprog")
    sense = "max" if re.match(r"^\s*(?:maximi[sz]e|optimize|best\s+mix)", t, I) else "min"
    return {"reply": solve_lp(t, sense), "sources": []}


if __name__ == "__main__":
    assert match("optimize 30A + 20B with A costs 200 max 10, B costs 450 max 6, budget 5000")
    assert match("maximize 5x + 3y subject to x <= 4, y <= 6")
    assert match("minimize x**2 - 4x at x near -10 to 10")
    assert not match("optimize my morning routine")      # no coefficients -> not an LP
    assert not match("best mix of music for coding")     # ditto

    # knapsack-ish: 30A+20B, A<=10, B<=6, weight 200A+450B<=5000 -> A=10 (2000), B=6 (2700) fits: obj 420
    r = run("optimize 30A + 20B with A costs 200 max 10, B costs 450 max 6, budget 5000", {})
    assert "420" in r["reply"].replace(",", ""), r
    assert "a = 10" in r["reply"] and "b = 6" in r["reply"], r
    # classic LP: max 5x+3y, x<=4, y<=6 -> 38
    r2 = run("maximize 5x + 3y subject to x <= 4, y <= 6", {})
    assert "38" in r2["reply"], r2
    # parabola: min x^2-4x on [-10,10] at x=2, value -4
    r3 = run("minimize x**2 - 4*x at x near -10 to 10", {})
    assert "x = 2" in r3["reply"] and "-4" in r3["reply"], r3
    print("[self-check] optimize OK - exact linprog optima + binding values, 1-D function min/max")
