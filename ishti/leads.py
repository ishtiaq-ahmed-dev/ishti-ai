"""Leads store (Part E4): business leads live HERE — a dedicated table, deliberately NOT
in personal memory (they're work data, not facts about the owner). Dedupe across runs by
(name+company) and (website/email); verification + relevance ranking; CSV/XLSX export.
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3
import threading
import time

from ishti import ROOT

DB = os.path.join(ROOT, "data", "leads.db")
_lock = threading.RLock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT, company TEXT, role TEXT, website TEXT, email TEXT, phone TEXT,
    location TEXT, industry TEXT, source_url TEXT, date_found REAL,
    relevance REAL DEFAULT 0.5, notes TEXT DEFAULT '', verified INTEGER DEFAULT 0,
    run_id TEXT DEFAULT '', first_seen REAL, last_seen REAL,
    UNIQUE(name, company, source_url));
CREATE INDEX IF NOT EXISTS ix_leads_run ON leads(run_id);
"""


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            os.makedirs(os.path.dirname(DB), exist_ok=True)
            _conn = sqlite3.connect(DB, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.executescript(SCHEMA)
            _conn.commit()
        return _conn


def set_db_path(path: str):
    """Tests only."""
    global DB, _conn
    with _lock:
        DB = path
        _conn = None


def add_lead(**f) -> int | None:
    """Insert or refresh one lead; returns the row id, None when filtered by quality rules."""
    name = (f.get("name") or "").strip()
    if not name:
        return None
    now = time.time()
    c = conn()
    with _lock, c:
        row = c.execute("SELECT id, first_seen FROM leads WHERE name=? AND COALESCE(company,'')=? "
                        "AND COALESCE(source_url,'')=?",
                        (name, f.get("company") or "", f.get("source_url") or "")).fetchone()
        if row:
            c.execute("""UPDATE leads SET last_seen=?, website=COALESCE(NULLIF(?,''), website),
                         email=COALESCE(NULLIF(?,''), email), phone=COALESCE(NULLIF(?,''), phone),
                         role=COALESCE(NULLIF(?,''), role), location=COALESCE(NULLIF(?,''), location),
                         relevance=MAX(relevance, ?), verified=MAX(verified, ?) WHERE id=?""",
                      (now, f.get("website") or "", f.get("email") or "", f.get("phone") or "",
                       f.get("role") or "", f.get("location") or "",
                       float(f.get("relevance") or 0.5), 1 if f.get("verified") else 0, row["id"]))
            return row["id"]
        cur = c.execute("""INSERT INTO leads(name, company, role, website, email, phone, location,
                           industry, source_url, date_found, relevance, notes, verified, run_id,
                           first_seen, last_seen)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (name, f.get("company") or "", f.get("role") or "", f.get("website") or "",
                         f.get("email") or "", f.get("phone") or "", f.get("location") or "",
                         f.get("industry") or "", f.get("source_url") or "", now,
                         float(f.get("relevance") or 0.5), f.get("notes") or "",
                         1 if f.get("verified") else 0, f.get("run_id") or "", now, now))
        return cur.lastrowid


def add_many(leads: list, run_id: str = "") -> dict:
    added = refreshed = skipped = 0
    for f in leads:
        try:
            fid = dict(f)
            fid["run_id"] = run_id
            rid = add_lead(**fid)
            if rid is None:
                skipped += 1
            else:
                row = conn().execute("SELECT first_seen, last_seen FROM leads WHERE id=?", (rid,)).fetchone()
                if row and row["first_seen"] == row["last_seen"]:
                    added += 1
                else:
                    refreshed += 1
        except Exception:
            skipped += 1
    return {"added": added, "refreshed": refreshed, "skipped": skipped}


def rows(query: str = "", limit: int = 200, run_id: str = "", verified_only: bool = False) -> list[dict]:
    q, args = "SELECT * FROM leads WHERE 1=1", []
    if query:
        like = f"%{query}%"
        q += " AND (name LIKE ? OR company LIKE ? OR location LIKE ? OR industry LIKE ? OR notes LIKE ?)"
        args += [like] * 5
    if run_id:
        q += " AND run_id=?"
        args.append(run_id)
    if verified_only:
        q += " AND verified=1"
    q += " ORDER BY relevance DESC, last_seen DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn().execute(q, args).fetchall()]


def verify(lead_id: int, ok: bool, note: str = "") -> bool:
    """Fact-Checker verdict on one lead (website exists / data matches the source)."""
    c = conn()
    with _lock, c:
        c.execute("UPDATE leads SET verified=?, notes=CASE WHEN ?='' THEN notes ELSE ? END WHERE id=?",
                  (1 if ok else 0, note, note, lead_id))
        return c.total_changes > 0


def stats() -> dict:
    c = conn()
    total = c.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    verified = c.execute("SELECT COUNT(*) FROM leads WHERE verified=1").fetchone()[0]
    runs = c.execute("SELECT COUNT(DISTINCT run_id) FROM leads").fetchone()[0]
    return {"leads": total, "verified": verified, "runs": runs, "db": DB}


def export(path: str | None = None, query: str = "", fmt: str = "csv") -> str:
    """CSV (default) or XLSX (when openpyxl is present) of the current lead set."""
    rows_ = rows(query, limit=5000)
    stamp = time.strftime("%Y%m%d_%H%M")
    out = path or os.path.join(ROOT, "data", "exports", f"leads-{stamp}.{fmt}")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cols = ["name", "company", "role", "website", "email", "phone", "location", "industry",
            "source_url", "relevance", "verified", "notes", "date_found"]
    if fmt == "xlsx":
        try:
            from openpyxl import Workbook
        except ImportError:
            fmt = "csv"
            out = out.replace(".xlsx", ".csv")
        else:
            wb = Workbook()
            ws = wb.active
            ws.title = "leads"
            ws.append(cols)
            for r in rows_:
                ws.append([r.get(c) for c in cols])
            wb.save(out)
            return out
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows_:
            r = dict(r)
            r["date_found"] = time.strftime("%Y-%m-%d", time.localtime(r["date_found"])) if r.get("date_found") else ""
            r["verified"] = "yes" if r.get("verified") else "no"
            w.writerow(r)
    return out


if __name__ == "__main__":
    import tempfile
    set_db_path(os.path.join(tempfile.mkdtemp(), "leads.db"))
    a = add_lead(name="Acme LLC Services", company="Acme", website="acme.example",
                 email="info@acme.example", location="Texas", source_url="https://src.example/a",
                 relevance=0.8, run_id="r1")
    assert a
    # dedupe across runs: same name+company+source refreshes instead of duplicating
    b = add_lead(name="Acme LLC Services", company="Acme", source_url="https://src.example/a",
                 relevance=0.9, phone="+1 555")
    assert b == a
    new = add_lead(name="Acme LLC Services", company="Acme", source_url="https://other.example",
                   run_id="r2")
    assert new != a, "different source_url = a separate lead row (recorded per source)"
    assert len(rows()) == 2
    verify(a, True, "website resolved, email matches source")
    assert rows(verified_only=True)[0]["verified"] == 1
    assert rows("texas")[0]["location"] == "Texas"
    st = stats()
    assert st["leads"] == 2 and st["verified"] == 1 and st["runs"] == 2, st
    p = export(fmt="csv")
    assert os.path.isfile(p) and "Acme" in open(p, encoding="utf-8-sig").read()
    m = add_many([{"name": "Bulk Co", "source_url": "s1", "relevance": 0.6},
                  {"name": "", "source_url": "s2"}], run_id="r3")
    assert m["added"] == 1 and m["skipped"] == 1, m
    print(f"[self-check] ishti.leads OK — dedupe, verify, filter, export, stats {st}")
