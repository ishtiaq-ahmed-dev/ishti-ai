"""Risk register (inventory domain 30 #18): scored risks with likelihood × impact.

"log risk data loss likelihood 3 impact 5", "top risks", "my risk register",
"mitigate data loss with nightly backups", "close risk data loss", "risk data loss"

Each risk gets a computed score 1-25 (likelihood × impact), colour-banded
(low 1-6 / medium 8-12 / high 15-25 — the standard 5x5 matrix bands). Mitigations are
recorded and the score is re-stated with the mitigation. Scores are computed, never guessed.
Store: data/risks.db. T0, local write only.
"""
import os
import re
import sqlite3
import threading

from ishti import CONFIG
from ishti.skills import trace

DB = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "risks.db")
_lock = threading.Lock()
I = re.I

LOG = re.compile(
    r"^\s*(?:log|add|record|new)\s+risk\s+(?P<name>.+?)\s+"
    r"likelihood\s+(?P<lik>[1-5])\s+impact\s+(?P<imp>[1-5])\s*$", I)
TOP = re.compile(r"^\s*(?:show\s+)?top\s+risks?\s*(?:\?(?P<n>\d+))?\s*$", I)
LIST = re.compile(r"^\s*(?:my\s+)?risk\s+registers?\s*$|^\s*(?:show|list)\s+(?:my\s+)?risks?\s*$", I)
MIT = re.compile(
    r"^\s*mitigate\s+(?P<name>.+?)\s+with\s+(?P<plan>.+)$", I)
CLOSE = re.compile(r"^\s*close\s+risk\s+(?P<name>.+)$", I)
SHOW = re.compile(
    r"^\s*(?:what(?:'s| is)\s+the\s+|the\s+)risk\s+(?:of\s+)?(?P<name>[\w &'\-]{2,40}?)\s*\??$"
    r"|^\s*risk\s+(?!of\b)(?P<name2>[\w &'\-]{2,40}?)\s*\??$", I)


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS risks("
              "id INTEGER PRIMARY KEY, name TEXT UNIQUE, likelihood INTEGER, impact INTEGER, "
              "status TEXT DEFAULT 'open', mitigation TEXT, closed_at TEXT)")
    return c


def _norm(s):
    return re.sub(r"\s+", " ", s.strip().lower()).rstrip(".!?")

def _band(score):
    if score >= 15:
        return "HIGH"
    if score >= 8:
        return "medium"
    return "low"

def _score(likelihood, impact):
    return likelihood * impact


def log(name, likelihood, impact):
    name = _norm(name)
    score = _score(likelihood, impact)
    with _lock, _db() as c:
        try:
            c.execute("INSERT INTO risks(name, likelihood, impact) VALUES(?,?,?)",
                      (name, likelihood, impact))
        except sqlite3.IntegrityError:
            c.execute("UPDATE risks SET likelihood=?, impact=?, status='open' WHERE name=?",
                      (likelihood, impact, name))
            return (f"Risk **{name}** updated to L{likelihood}×I{impact} = **{score}/25** "
                    f"({_band(score)}).")
    return (f"Risk **{name}** logged: likelihood {likelihood} × impact {impact} = "
            f"**{score}/25** ({_band(score)}).")


def top(n=5):
    with _lock:
        rows = _db().execute(
            "SELECT name, likelihood, impact, status, mitigation FROM risks "
            "WHERE status='open' ORDER BY likelihood*impact DESC, id LIMIT ?", (n,)).fetchall()
    if not rows:
        return "No open risks — \"log risk data loss likelihood 3 impact 5\"."
    lines = [f"**Top {len(rows)} open risks** (by computed score):"]
    for name, lik, imp, _st, mit in rows:
        score = _score(lik, imp)
        mit_txt = f" — mitigated: {mit}" if mit else ""
        lines.append(f"- **{name}** — L{lik}×I{imp} = {score}/25 ({_band(score)}){mit_txt}")
    return "\n".join(lines)


def list_risks():
    with _lock:
        rows = _db().execute(
            "SELECT name, likelihood, impact, status, mitigation FROM risks ORDER BY "
            "status='closed', likelihood*impact DESC, id").fetchall()
    if not rows:
        return "The register is empty — \"log risk data loss likelihood 3 impact 5\"."
    lines = ["**Risk register:**"]
    for name, lik, imp, status, mit in rows:
        score = _score(lik, imp)
        suffix = " (closed)" if status == "closed" else ""
        mit_txt = f" — mitigated: {mit}" if mit else ""
        lines.append(f"- **{name}** — {score}/25 ({_band(score)}){suffix}{mit_txt}")
    return "\n".join(lines)


def mitigate(name, plan):
    name = _norm(name)
    plan = plan.strip()
    with _lock, _db() as c:
        row = c.execute("SELECT likelihood, impact FROM risks WHERE name=?", (name,)).fetchone()
        if not row:
            return f"No risk named **{name}**. Log it first with its likelihood and impact."
        c.execute("UPDATE risks SET mitigation=? WHERE name=?", (plan, name))
    score = _score(row[0], row[1])
    return (f"Mitigation for **{name}** recorded: {plan}\n"
            f"Score stays **{score}/25** ({_band(score)}) until the mitigation is in place — "
            f"re-assess honestly after it lands.")


def close(name):
    name = _norm(name)
    with _lock, _db() as c:
        import datetime as dt
        n = c.execute("UPDATE risks SET status='closed', closed_at=? WHERE name=?",
                      (dt.date.today().isoformat(), name)).rowcount
    return f"Risk **{name}** closed." if n else f"No open risk named **{name}**."


def show(m):
    name = _norm(m.group("name") or m.group("name2"))
    with _lock:
        row = _db().execute(
            "SELECT likelihood, impact, status, mitigation FROM risks WHERE name=?", (name,)).fetchone()
    if not row:
        return (f"No risk named **{name}**. Log one: \"log risk {name} likelihood 3 impact 5\" "
                f"(both 1-5).")
    lik, imp, status, mit = row
    score = _score(lik, imp)
    parts = [f"**{name}** — L{lik}×I{imp} = {score}/25 ({_band(score)})"]
    if status == "closed":
        parts.append("Status: closed.")
    if mit:
        parts.append(f"Mitigation: {mit}")
    return " · ".join(parts)


def match(text):
    t = text.strip()
    if MIT.match(t) or LOG.match(t) or TOP.match(t) or LIST.match(t) or CLOSE.match(t):
        return True
    m = SHOW.match(t)
    return bool(m and "risk" in t.lower())


def run(text, session):
    t = text.strip()
    if (m := LOG.match(t)):
        trace(session, "riskregister", "log")
        return {"reply": log(m.group("name"), int(m.group("lik")), int(m.group("imp"))), "sources": []}
    if (m := MIT.match(t)):
        trace(session, "riskregister", "mitigate")
        return {"reply": mitigate(m.group("name"), m.group("plan")), "sources": []}
    if (m := CLOSE.match(t)):
        trace(session, "riskregister", "close")
        return {"reply": close(m.group("name")), "sources": []}
    if (m := SHOW.match(t)) and "risk" in t.lower():
        trace(session, "riskregister", "show")
        return {"reply": show(m), "sources": []}
    if TOP.match(t):
        trace(session, "riskregister", "top")
        n = int(TOP.match(t).group("n") or 5)
        return {"reply": top(n), "sources": []}
    if LIST.match(t):
        trace(session, "riskregister", "list")
        return {"reply": list_risks(), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    DB = os.path.join(tempfile.mkdtemp(), "r.db")   # rebind: CONFIG read at import

    assert match("log risk data loss likelihood 3 impact 5")
    assert match("add risk vendor lock-in likelihood 2 impact 4")
    assert match("top risks")
    assert match("my risk register")
    assert match("mitigate data loss with nightly backups")
    assert match("close risk data loss")
    assert match("what's the risk of data loss")
    assert match("the risk of data loss")
    assert match("risk data loss")
    assert not match("log my meals")                  # no risk keyword + no scores
    assert not match("risk of rain tomorrow")         # weather-style phrasing is not a register lookup

    r = run("log risk data loss likelihood 3 impact 5", {})
    assert "**15/25** (HIGH)" in r["reply"], r
    run("add risk vendor lock-in likelihood 2 impact 4", {})        # 8/25 medium
    run("log risk typo likelihood 1 impact 1", {})                  # 1/25 low
    r2 = run("top risks", {})
    assert r2["reply"].index("data loss") < r2["reply"].index("vendor"), r2   # 15 before 8
    assert r2["reply"].index("vendor") < r2["reply"].index("typo"), r2
    r3 = run("mitigate data loss with nightly backups", {})
    assert "nightly backups" in r3["reply"] and "15/25" in r3["reply"], r3
    r4 = run("what's the risk of data loss", {})
    assert "15/25" in r4["reply"] and "nightly backups" in r4["reply"], r4
    r5 = run("close risk typo", {})
    assert "closed" in r5["reply"], r5
    r6 = run("my risk register", {})
    assert "typo** — 1/25 (low) (closed)" in r6["reply"], r6
    r7 = run("log risk data loss likelihood 4 impact 5", {})
    assert "updated to L4×I5 = **20/25** (HIGH)" in r7["reply"], r7
    print("[self-check] riskregister OK - 5x5 matrix scores computed exactly, top-ranking by score, "
          "mitigations recorded without lying about reduced risk, close/status, local data/risks.db")
