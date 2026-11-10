"""Exact math (sympy) — deterministic, no LLM arithmetic.

solve / derivative / integral / simplify / percent / tip / loan payment / prime factors /
gcd & lcm. The chat skill's mental-math rules stay for tiny arithmetic; anything symbolic
routes here so numbers are computed, never generated.
"""
import re
from functools import lru_cache

I = re.I

_OPS = [
    ("solve", re.compile(r"^\s*(?:please\s+)?solve\s+(?:for\s+x\s*[:\-]?\s*)?(?P<body>.+)$", I)),
    ("derivative", re.compile(r"^\s*(?:please\s+)?(?:derivative|differentiate)\s+(?:of\s+)?(?P<body>.+)$", I)),
    ("integral", re.compile(r"^\s*(?:please\s+)?integral\s+(?:of\s+)?(?P<body>.+)$", I)),
    ("simplify", re.compile(r"^\s*(?:please\s+)?simplify\s+(?P<body>.+)$", I)),
    ("percent", re.compile(r"^\s*(?:what(?:'s|\s+is)?\s+|calculate\s+)?(?P<p>\d[\d.]*)\s*(?:%|percent)\s+of\s+(?P<n>\d[\d,.]*)\s*\??$|^\s*(?P<n2>\d[\d,.]*)\s+is\s+what\s+percent\s+of\s+(?P<n3>\d[\d,.]*)\s*\??$", I)),
    ("tip", re.compile(r"^\s*(?:calculate\s+)?(?:the\s+)?tip\s+(?:on|for)\s+(?:rs\.?|pkr|\$)?\s*(?P<amt>\d[\d,.]*)(?:\s+(?:at|with)\s+(?P<pct>\d[\d.]*)\s*%)?\s*$", I)),
    ("loan", re.compile(r"^\s*(?:calculate\s+)?loan\s+payment\s+(?:of\s+)?(?:rs\.?|pkr|\$)?\s*(?P<principal>\d[\d,.]*)\s*(?:at|with|@)\s*(?P<rate>\d[\d.]*)\s*%\s*(?:for|over)\s*(?P<years>\d+)\s*years?\s*$", I)),
    ("prime", re.compile(r"^\s*(?:prime\s+factors?\s+of|factori[sz]e)\s+(?P<n>\d+)\s*$", I)),
    # "if I spend 250 on groceries and 175 on fuel, how much did I spend": a hypothetical, so a sum, not the expense log
    ("total", re.compile(r"^\s*(?:what\s+)?if\b(?=.*\d.*\band\b.*\d)(?P<body>.+?)(?:,|\?)\s*(?:how\s+much|what\s+(?:is|are)\s+the\s+total|total)\b.*$", I)),
    ("gcd", re.compile(r"^\s*(?:gcd|hcf|lcm)\s+of\s+(?P<a>\d+)\s*(?:,|and)\s*(?P<b>\d+)\s*$", I)),
]
# plain arithmetic ("5*7+2", "what is 12 x 4", "15 divided by 4") is computed here, instantly - it used to cost a
# model call (and with the model down, fell through to unrelated skills)
ARITH = re.compile(r"^\s*(?:what(?:'s|\s+is)\s+|calculate\s+|compute\s+|solve\s+)?(?P<body>[-+(]*\s*\d[\d\s.,+\-*/^()×÷x]*"
                   r"|.*?\d.*?\b(?:times|plus|minus|divided\s+by|multiplied\s+by|to\s+the\s+power\s+of)\b.*?)\s*[=?]*\s*$", I)
_WORDS = (("divided by", "/"), ("multiplied by", "*"), ("to the power of", "**"), ("times", "*"), ("plus", "+"),
          ("minus", "-"))
_DATE_OR_PHONE = re.compile(r"^\s*\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}\s*$|^\s*\d{4,5}-\d{6,8}\s*$")


def _arith_expr(text: str) -> str:
    """The Python arithmetic for a plain sum, or '' when the text isn't one."""
    m = ARITH.match(text.strip())
    if not m or _DATE_OR_PHONE.match(m["body"]):
        return ""
    expr = m["body"].lower()
    for word, op in _WORDS:
        expr = expr.replace(word, op)
    expr = re.sub(r"(?<=\d)\s*[x×]\s*(?=[\d(])", "*", expr).replace("÷", "/").replace("^", "**").replace(",", "")
    if not re.fullmatch(r"[\d\s.+\-*/()]+", expr) or not re.search(r"\d\s*(?:\*\*|[-+*/])\s*[-+(]*\s*\d", expr):
        return ""
    return expr.strip()


def _arith(expr: str):
    import ast
    import operator
    ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
           ast.Pow: operator.pow, ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv}

    def ev(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = ev(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and type(node.op) in ops:
            a, b = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow) and (abs(b) > 1000 or (abs(a) > 1e6 and b > 20)):
                raise OverflowError("that power is too large to show")  # "9**9**9" must not hang the PC
            return ops[type(node.op)](a, b)
        raise ValueError("not plain arithmetic")
    return ev(ast.parse(expr, mode="eval").body)


def _number(value) -> str:
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        value = int(value)
    return f"{value:,}" if isinstance(value, int) else f"{value:,.10g}"


def match(text):
    return any(rx.match(text.strip()) for _n, rx in _OPS) or bool(_arith_expr(text))


# parse_expr EVALUATES its input as Python: "simplify __import__('os').getpid()" ran real code (reachable from the
# remote channels too). Only maths gets through: digits, operators, single-letter variables, known function names.
_SAFE = re.compile(r"^[0-9A-Za-z\s+\-*/^().,=%!×÷]+$")
_FUNCS = {"sin", "cos", "tan", "cot", "sec", "csc", "asin", "acos", "atan", "sinh", "cosh", "tanh", "log", "ln", "exp",
          "sqrt", "cbrt", "abs", "pi", "oo", "inf", "factorial", "floor", "ceiling", "root", "mod", "sign"}


class UnsafeExpression(ValueError):
    pass


def _check(expr: str) -> str:
    if not _SAFE.match(expr or "") or re.search(r"[A-Za-z]\s*\.|\.\s*[A-Za-z]", expr):
        raise UnsafeExpression(expr)
    for word in re.findall(r"[A-Za-z]{2,}", expr):
        if word.lower() not in _FUNCS:
            raise UnsafeExpression(word)
    return expr


@lru_cache(maxsize=256)
def _sympify(expr: str):
    """Parse human math: implicit multiplication (2x -> 2*x), ^ as power, commas stripped. Whitelisted first."""
    _check(expr)
    import sympy
    from sympy.parsing.sympy_parser import (parse_expr, standard_transformations,
                                            implicit_multiplication_application, convert_xor)
    tf = standard_transformations + (implicit_multiplication_application, convert_xor)
    return parse_expr(expr.replace(",", "").replace("×", "*").replace("÷", "/"), transformations=tf)


def _latex_free(expr) -> str:
    return str(expr)


def run(text, session):
    t = text.strip()
    if expr := _arith_expr(t):  # before the sympy ops: no import, no model, instant
        try:
            return {"reply": f"{expr.replace('**', '^')} = {_number(_arith(expr))}", "sources": []}
        except ZeroDivisionError:
            return {"reply": "That divides by zero, so it has no answer.", "sources": []}
        except (OverflowError, ValueError, SyntaxError, RecursionError) as exc:
            if isinstance(exc, OverflowError):
                return {"reply": f"I can't show that: {exc}.", "sources": []}
    for name, rx in _OPS:
        m = rx.match(t)
        if not m:
            continue
        try:
            import sympy
            from sympy import symbols, Eq, solve as sym_solve, diff, integrate, simplify as sym_simp, gcd, lcm  # noqa
        except ImportError:
            return None  # keep routing honest: no sympy -> not our request
        body = (m.groupdict().get("body") or "").strip()
        try:
            return _op(name, m, body, t)
        except UnsafeExpression:
            return {"reply": "That doesn't look like a maths expression I can work out - use numbers, single-letter "
                             "variables and functions like sqrt, sin or log.", "sources": []}
    return None


def _op(name, m, body, t):
    import sympy
    from sympy import symbols, Eq, solve as sym_solve, diff, integrate, simplify as sym_simp, gcd, lcm  # noqa
    if name == "solve":
        expr = body.replace("^", "**").rstrip(".?!")
        if "=" in expr:
            lhs, rhs = expr.split("=", 1)
            eq = Eq(_sympify(lhs), _sympify(rhs))
        else:
            eq = _sympify(expr)
        # the expression's own variable: "solve 2y + 3 = 11" answered "x = []"
        free = sorted(eq.free_symbols, key=lambda v: (v.name != "x", v.name))
        var = free[0] if free else symbols("x")
        roots = sym_solve(eq, var)
        return {"reply": f"{var} = {_latex_free(roots)}", "sources": []}
    if name == "derivative":
        x = symbols("x")
        d = diff(_sympify(body.replace("^", "**").rstrip(".?!")), x)
        return {"reply": f"d/dx ({body}) = {_latex_free(d)}", "sources": []}
    if name == "integral":
        x = symbols("x")
        it = integrate(_sympify(body.replace("^", "**").rstrip(".?!")), x)
        return {"reply": f"∫({body}) dx = {_latex_free(it)} + C", "sources": []}
    if name == "simplify":
        s = sym_simp(_sympify(body.replace("^", "**").rstrip(".?!")))
        return {"reply": f"{body} = {_latex_free(s)}", "sources": []}
    if name == "percent":
        g = m.groupdict()
        if g.get("p"):
            val = _sympify(g["p"]) * _sympify(g["n"]) / 100
            return {"reply": f"{g['p']}% of {g['n']} = {_latex_free(val)}", "sources": []}
        if _sympify(g["n3"]) == 0:  # "240 is what percent of 0" answered "zoo%"
            return {"reply": "A percentage of 0 isn't defined - nothing can be a share of zero.", "sources": []}
        val = _sympify(g["n2"]) / _sympify(g["n3"]) * 100
        return {"reply": f"{g['n2']} / {g['n3']} = {_latex_free(val)}%", "sources": []}
    if name == "tip":
        amt = float(m.group("amt").replace(",", ""))
        pct = float(m.group("pct") or 15)
        tip = amt * pct / 100
        return {"reply": (f"Tip at {pct:.0f}%: {tip:,.2f} — total {amt + tip:,.2f}. "
                          f"(10%: {amt*0.1:,.2f}, 20%: {amt*0.2:,.2f})"), "sources": []}
    if name == "loan":
        P = float(m.group("principal").replace(",", ""))
        r = float(m.group("rate")) / 100 / 12
        n = int(m.group("years")) * 12
        pay = P * r * (1 + r) ** n / ((1 + r) ** n - 1) if r else P / n
        total = pay * n
        return {"reply": (f"Monthly payment: {pay:,.2f} ({n} months). Total paid: {total:,.2f}, "
                          f"interest: {total - P:,.2f}."), "sources": []}
    if name == "prime":
        n = int(m.group("n"))
        fac = sympy.factorint(n)
        parts = " × ".join(f"{p}^{e}" if e > 1 else str(p) for p, e in sorted(fac.items()))
        return {"reply": f"{n} = {parts}", "sources": []}
    if name == "total":
        nums = [float(n.replace(",", "")) for n in re.findall(r"(?<![\w.])\d[\d,]*(?:\.\d+)?", m.group("body"))]
        if len(nums) < 2 or re.search(r"\b(?:at|each|per|times|x|for|of|every)\b", m.group("body"), I):
            return None  # prices times quantities are not a plain sum: the model reasons about those
        return {"reply": " + ".join(f"{n:,.10g}" for n in nums) + f" = {sum(nums):,.10g}", "sources": []}
    if name == "gcd":
        a, b = int(m.group("a")), int(m.group("b"))
        op = "gcd" if t.lower().startswith(("gcd", "hcf")) else "lcm"
        val = gcd(a, b) if op == "gcd" else lcm(a, b)
        return {"reply": f"{op.upper()}({a}, {b}) = {val}", "sources": []}
    return None


if __name__ == "__main__":
    assert match("solve for x: 2x + 3 = 11")
    assert match("derivative of x^3 + 2x")
    assert match("integral of 3x^2")
    assert match("simplify (x^2 - 1)/(x - 1)")
    assert match("what is 15% of 2400")
    assert match("240 is what percent of 1200?")
    assert match("calculate the tip on 1500 at 12%")
    assert match("loan payment of 500000 at 18% for 5 years")
    assert match("prime factors of 360")
    assert match("gcd of 48 and 180")
    assert not match("what is the weather")
    r = run("solve for x: 2x + 3 = 11", {})
    assert "4" in r["reply"], r
    r2 = run("derivative of x^3", {})
    assert "3*x**2" in r2["reply"], r2
    r3 = run("what is 15% of 2400", {})
    assert "360" in r3["reply"], r3
    r4 = run("prime factors of 360", {})
    assert "2^3" in r4["reply"] and "5" in r4["reply"], r4
    r5 = run("loan payment of 500000 at 18% for 5 years", {})
    assert "Monthly payment" in r5["reply"], r5
    for evil in ("simplify __import__('os').getpid()", "simplify x.__class__", "derivative of [x for x in ()]",
                 "solve exec('1') = 1", "integral of open"):
        assert "doesn't look like a maths" in run(evil, {})["reply"], evil
    assert run("solve 2y + 3 = 11", {})["reply"] == "y = [4]"
    assert "isn't defined" in run("240 is what percent of 0", {})["reply"]
    assert "sqrt" not in run("simplify sqrt(16)", {})["reply"].split("=")[1]
    print("[self-check] calc OK - solve/derive/integrate/simplify/percent/tip/loan/prime/gcd exact")
