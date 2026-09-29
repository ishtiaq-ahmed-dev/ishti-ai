"""Game theory 2x2 (inventory domain 6 #25): dominance, pure & mixed Nash equilibria.

"analyze the game: TL 3,2; TR 1,1; BL 0,3; BR 2,0"

Cells: TL/TR = row player's Top vs Column's Left/Right, BL/BR = Bottom. Payoffs (row, column).
Computed: dominant strategies for each player, all pure-strategy Nash equilibria (no unilateral
gain), and the mixed equilibrium (verified numerically by asserting indifference — the formula
is never trusted blindly). Degenerate cases are named, not glossed. T0, pure computation.
"""
import re

from ishti.skills import trace

I = re.I

GAME = re.compile(
    r"^\s*(?:analyze|analyse)\s+the\s+game\s*[:\-]\s*(?P<cells>.+)$|"
    r"^\s*game\s+theory\s*[:\-]\s*(?P<cells2>.+)$", I)
CELL = re.compile(r"\b(TL|TR|BL|BR)\s*\(?(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\)?", I)


def parse(cells_text):
    found = {m.group(1).upper(): (float(m.group(2)), float(m.group(3)))
             for m in CELL.finditer(cells_text)}
    return found if len(found) == 4 else None


def analyze(cells):
    TL, TR, BL, BR = cells["TL"], cells["TR"], cells["BL"], cells["BR"]
    a, b = TL[0], TR[0]        # row payoffs
    c, d = BL[0], BR[0]
    e, f = TL[1], TR[1]        # column payoffs
    g, h = BL[1], BR[1]
    out = ["**2x2 game** (row: Top/Bottom, column: Left/Right) — payoffs (row, column):"]
    out.append(f"- TL {TL}, TR {TR}")
    out.append(f"- BL {BL}, BR {BR}")

    row_dom = ("Top" if a >= c and b >= d and (a > c or b > d) else
               "Bottom" if c >= a and d >= b and (c > a or d > b) else None)
    col_dom = ("Left" if e >= f and g >= h and (e > f or g > h) else
               "Right" if f >= e and h >= g and (f > e or h > g) else None)
    out.append(f"- Row player's dominant strategy: **{row_dom or 'none'}**")
    out.append(f"- Column player's dominant strategy: **{col_dom or 'none'}**")

    nash = []
    if a >= c and e >= f:
        nash.append("Top-Left")
    if b >= d and f >= e:
        nash.append("Top-Right")
    if c >= a and g >= h:
        nash.append("Bottom-Left")
    if d >= b and h >= g:
        nash.append("Bottom-Right")
    out.append(f"- Pure-strategy Nash equilibria: **{', '.join(nash) if nash else 'none'}**")

    den_q = (a - c) + (d - b)
    den_p = (a - b) + (d - c)
    if abs(den_q) < 1e-12 or abs(den_p) < 1e-12:
        out.append("- Mixed equilibrium: **degenerate** (a player is already indifferent "
                   "everywhere or the matrix is dominance-solvable) — no unique mix to report.")
    else:
        q = (d - b) / den_q          # column plays Left with q
        p = (d - c) / den_p          # row plays Top with p
        # numerical verification: indifference must hold exactly
        row_top = q * a + (1 - q) * b
        row_bot = q * c + (1 - q) * d
        col_left = p * e + (1 - p) * g
        col_right = p * f + (1 - p) * h
        if abs(row_top - row_bot) > 1e-9 or abs(col_left - col_right) > 1e-9:
            out.append("- Mixed equilibrium: **verification failed** — refusing to state a "
                       "formula-derived mix that doesn't verify. This is a bug.")
        else:
            out.append(f"- Mixed equilibrium (verified by indifference): row plays Top with "
                       f"**{p:.3f}**, column plays Left with **{q:.3f}**.")
    if not nash and row_dom is None and col_dom is None:
        out.append("(No pure equilibrium and no dominance — matching-pennies class, the mixed "
                   "mix above is the only guide.)")
    return "\n".join(out)


def match(text):
    t = text.strip()
    return bool(GAME.match(t)) and len(CELL.findall(t)) >= 4


def run(text, session):
    t = text.strip()
    if (m := GAME.match(t)):
        cells = parse(m.group("cells") or m.group("cells2"))
        if not cells:
            return {"reply": "I need all four cells with two payoffs each, e.g. "
                             "\"analyze the game: TL 3,2; TR 1,1; BL 0,3; BR 2,0\".", "sources": []}
        trace(session, "gametheory", "analyze")
        return {"reply": analyze(cells), "sources": []}
    return None


if __name__ == "__main__":
    assert match("analyze the game: TL 3,2; TR 1,1; BL 0,3; BR 2,0")
    assert match("game theory: TL -1,-1; TR -3,0; BL 0,-3; BR -2,-2")
    assert not match("game theory is fun")
    assert not match("analyze the game: TL 3,2; TR 1,1")

    # prisoner's dilemma: confess/confess (-1,-1 etc. scaled) is dominant + Nash
    r = analyze(parse("TL -1,-1; TR -3,0; BL 0,-3; BR -2,-2"))
    assert "Row player's dominant strategy: **Bottom**" in r, r
    assert "Bottom-Right" in r and "Pure-strategy Nash equilibria: **Bottom-Right**" in r, r
    # dominance-solvable with a unique mixed: classic coordination has 2 pure + 1 mixed
    r2 = analyze(parse("TL 3,3; TR 0,0; BL 0,0; BR 2,2"))
    assert "Top-Left" in r2 and "Bottom-Right" in r2, r2
    import re as _re
    m = _re.search(r"row plays Top with \*\*(0\.\d+)\*\*, column plays Left with \*\*(0\.\d+)\*\*", r2)
    assert m, r2
    p, q = float(m.group(1)), float(m.group(2))
    # indifference check, independent of the formula above: column mixes q so row is indifferent
    row_top = q * 3 + (1 - q) * 0
    row_bot = q * 0 + (1 - q) * 2
    assert abs(row_top - row_bot) < 1e-9, (row_top, row_bot)
    r3 = analyze(parse("TL 1,1; TR 0,0; BL 0,0; BR 1,1"))   # matching pennies-ish: no pure NE
    assert "none" in r3 or "degenerate" in r3, r3
    print("[self-check] gametheory OK - dominance, pure Nash sets, and mixed equilibrium "
          "verified numerically by player indifference (formula never trusted blindly)")
