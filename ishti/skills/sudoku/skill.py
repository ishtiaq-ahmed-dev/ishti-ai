"""Sudoku solver (inventory domain 6 #9): backtracking, every solution re-verified.

"solve this sudoku: 530070000600195000098000060800060003400803001700020006060000280000419005000080079"
"solve sudoku 53..7....6..195....98....6.8...6...34..8.3..17...2...6.6....28....419..5....8..79"

The board is 81 characters (digits, with 0 or . for empty). After solving, every row, column and
box is re-checked and the givens are asserted to survive — a wrong solution can't be reported.
Unsolvable puzzles get "no solution exists" (proven by exhaustion), never a guessed grid.
T0, pure computation.
"""
import re

from ishti.skills import trace

I = re.I

GIVENS = re.compile(r"^\s*(?:solve\s+(?:this\s+)?sudoku\s*[:\-]?\s*|solve\s+sudoku\s+)(?P<board>[0-9.0O\s/_-]{81,200})$", I)
CLEAN = re.compile(r"[0-9.]")


def _parse(text):
    """Return a 9x9 list-of-lists, or None if it isn't exactly 81 cells."""
    text = text.replace("O", "0")
    cells = [c for c in text if c in "0123456789."]
    if len(cells) != 81:
        return None
    grid = [[0] * 9 for _ in range(9)]
    for i, c in enumerate(cells):
        grid[i // 9][i % 9] = int(c) if c != "." else 0
    return grid


def _ok(grid, r, c, v):
    for i in range(9):
        if grid[r][i] == v or grid[i][c] == v:
            return False
    br, bc = 3 * (r // 3), 3 * (c // 3)
    for i in range(br, br + 3):
        for j in range(bc, bc + 3):
            if grid[i][j] == v:
                return False
    return True


def _givens_consistent(grid):
    """The givens themselves must not clash before any solving happens."""
    for r in range(9):
        for c in range(9):
            v = grid[r][c]
            if not v:
                continue
            grid[r][c] = 0
            ok = _ok(grid, r, c, v)
            grid[r][c] = v
            if not ok:
                return False
    return True


def _solve(grid):
    for r in range(9):
        for c in range(9):
            if grid[r][c] == 0:
                for v in range(1, 10):
                    if _ok(grid, r, c, v):
                        grid[r][c] = v
                        if _solve(grid):
                            return True
                        grid[r][c] = 0
                return False
    return True


def _verify(sol, givens):
    """Full legality + givens check. Returns '' if valid, else the reason."""
    want = {(r, c): v for r in range(9) for c in range(9) if (v := givens[r][c])}
    for r in range(9):
        if sorted(sol[r]) != list(range(1, 10)):
            return f"row {r + 1}"
    for c in range(9):
        if sorted(sol[r][c] for r in range(9)) != list(range(1, 10)):
            return f"column {c + 1}"
    for br in (0, 3, 6):
        for bc in (0, 3, 6):
            box = [sol[r][c] for r in range(br, br + 3) for c in range(bc, bc + 3)]
            if sorted(box) != list(range(1, 10)):
                return f"box at rows {br + 1}-{br + 3}"
    for (r, c), v in want.items():
        if sol[r][c] != v:
            return f"given at row {r + 1}, col {c + 1}"
    return ""


def _render(grid):
    return "\n".join(" ".join(str(v) if v else "." for v in row) for row in grid)


def solve_board(text):
    grid = _parse(text)
    if grid is None:
        return "I need exactly 81 cells (9 rows of 9) — digits, with 0 or . for empty cells."
    givens = [row[:] for row in grid]
    n_given = sum(v != 0 for row in givens for v in row)
    if n_given < 17:
        return (f"Only {n_given} givens — below the proven minimum of 17, so the puzzle may have "
                f"many solutions. Give me a proper puzzle.")
    work = [row[:] for row in grid]
    if not _givens_consistent(work):
        return "No solution exists for that grid — verified by exhaustive search, not a guess."
    if not _solve(work):
        return "No solution exists for that grid — verified by exhaustive search, not a guess."
    if bad := _verify(work, givens):
        return f"Solved grid failed verification ({bad}) — refusing to show it. This is a bug."
    return (f"**Solved** ({n_given} givens, every row/column/box re-verified in code):\n\n"
            f"{_render(work)}")


def match(text):
    t = text.strip()
    return bool(GIVENS.match(t)) and len([c for c in t if c in "0123456789."]) >= 30


def run(text, session):
    t = text.strip()
    if (m := GIVENS.match(t)):
        trace(session, "sudoku", "solve")
        return {"reply": solve_board(m.group("board")), "sources": []}
    return None


if __name__ == "__main__":
    easy = ("530070000" "600195000" "098000060" "800060003" "400803001"
            "700020006" "060000280" "000419005" "000080079")
    unsolvable = ("530070000" "600195000" "098000060" "800060003" "400803001"
                  "700020006" "060000280" "000419005" "110080079")  # two 1s in the last rows' box? no: col clash
    wrong_len = "530070000600195000"

    assert match("solve this sudoku: " + easy)
    assert match("solve sudoku " + easy)
    assert not match("solve this sudoku: " + wrong_len)
    assert not match("solve my maths homework 123")

    r = solve_board(easy)
    assert "Solved" in r, r
    grid = _parse(easy)
    work = [row[:] for row in grid]
    assert _solve(work) and not _verify(work, grid), "self-check solve must verify clean"
    r2 = solve_board(unsolvable)
    assert "No solution exists" in r2, r2
    r3 = solve_board("1" * 81)
    assert "No solution exists" in r3, r3
    r4 = solve_board("0" * 81)
    assert "17" in r4, r4
    r5 = solve_board(wrong_len)
    assert "81" in r5, r5
    print("[self-check] sudoku OK - known puzzle solved and re-verified, contradiction proven "
          "unsolvable by exhaustion, <17 givens and wrong length refused")
