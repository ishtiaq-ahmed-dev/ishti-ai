"""Mail merge (inventory domain 9 #25 / 12 #10): one template, many personalized drafts.

"mailmerge D:/contacts.csv: Dear {name}, your invoice {invoice} total is {total}. - ISHTI"
"mailmerge the csv D:/contacts.csv with template 'Hi {name}, ...'"
"show my mailmerge drafts"

Every row of the CSV becomes a draft in data/mailmerge/<n>-<email>.txt with the {placeholders}
filled from that row's columns. Nothing is ever sent here: each draft is a file the owner can
open in their mail client or hand to the email skill one by one (each send keeps its T2 gate).
Missing placeholders are filled with "" and reported, so no draft silently goes out half-filled.

Self-check redirects the drafts folder into a tempdir and counts the files it made.
"""
import os
import re

from ishti.skills import trace

I = re.I

FIELD = re.compile(r"\{([\w -]+)\}")
MERGE = re.compile(
    r"^\s*mail\s?merge(?:\s+(?:the\s+)?csv)?\s+(?P<path>[A-Za-z]:[\\/][^\"']+?\.(?:csv|tsv)|[\w./\\ -]+?\.(?:csv|tsv))"
    r"\s*(?::\s*|\s+with\s+(?:the\s+)?template\s+|\s+template\s+)(?P<tmpl>.+)$", I)
SHOW = re.compile(r"^\s*(?:show|list|open)\s+my\s+mail\s?merge(?:\s+drafts)?\s*$", I)


def _out_dir(session=None):
    d = (session or {}).get("_mailmerge_out") or os.path.join("data", "mailmerge")
    os.makedirs(d, exist_ok=True)
    return d


def _rows(path):
    import csv
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        rows = list(csv.reader(fh))
    if len(rows) < 2:
        return []
    header = [h.strip().lower() for h in rows[0]]
    out = []
    for raw in rows[1:]:
        if not any(c.strip() for c in raw):
            continue
        out.append({header[i]: (raw[i].strip() if i < len(raw) else "") for i in range(len(header))})
    return out


def _fill(tmpl, row):
    """Fill {placeholders}; report the ones with no matching column. Unknown -> empty string,
    never the raw {brace} (a half-filled draft that LOOKS filled is worse than a clean blank)."""
    missing = set()
    used = set()

    def sub(m):
        key = m.group(1).strip().lower()
        if key in row and row[key] != "":
            used.add(key)
            return row[key]
        missing.add(key)
        return ""

    return FIELD.sub(sub, tmpl), sorted(used), sorted(missing)


EMAIL_RX = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]{2,}$")


def merge(path, tmpl, session=None):
    rows = _rows(path)
    if rows is None:
        return f"File not found: {path}"
    if not rows:
        return f"No data rows in {path}."
    fields = sorted(set(FIELD.findall(tmpl)))
    no_email = 0
    written, problems = [], []
    out_dir = _out_dir(session)
    for n, row in enumerate(rows, 1):
        to = (row.get("email") or row.get("mail") or row.get("address") or "").strip()
        body, used, missing = _fill(tmpl, row)
        if not to or not EMAIL_RX.match(to):   # not a real address -> skip the row, say so
            no_email += 1
            continue
        safe = re.sub(r"[^\w.-]", "_", to)
        fp = os.path.join(out_dir, f"{n:03d}-{safe}.txt")
        with open(fp, "w", encoding="utf-8") as fh:
            fh.write(f"To: {to}\n\n{body}\n")
        written.append((to, fp))
        if missing:
            problems.append(f"row {n} ({to}): missing {', '.join(missing)}")
    if not written:
        return ("No drafts written: every row needs an `email` column with a real address."
                + (f" ({no_email} row(s) had none or invalid)" if no_email else ""))
    head = (f"**{len(written)} drafts** written to `{out_dir}` from "
            f"{os.path.basename(path)} ({len(fields)} placeholder(s)).\n"
            + "\n".join(f"- {t} -> {os.path.basename(fp)}" for t, fp in written[:8])
            + (f"\n- ... and {len(written) - 8} more" if len(written) > 8 else ""))
    if no_email:
        head += f"\nSkipped {no_email} row(s) without an email address."
    if problems:
        head += "\nIncomplete (placeholders had no column): " + "; ".join(problems[:5])
    head += "\nNothing was sent - each draft goes out only when you approve it."
    return head


def match(text):
    t = text.strip()
    return bool(MERGE.match(t) or SHOW.match(t))


def run(text, session):
    t = text.strip()
    if SHOW.match(t):
        d = _out_dir(session)
        files = sorted(f for f in os.listdir(d) if f.endswith(".txt")) if os.path.isdir(d) else []
        if not files:
            return {"reply": "No mailmerge drafts yet.", "sources": []}
        return {"reply": (f"{len(files)} drafts in `{d}`:\n"
                          + "\n".join(f"- {f}" for f in files[:15])
                          + (f"\n- ... and {len(files) - 15} more" if len(files) > 15 else "")),
                "sources": []}
    m = MERGE.match(t)
    trace(session, "mailmerge", "merge")
    path = m.group("path").strip().strip("\"'")
    return {"reply": merge(path, m.group("tmpl").strip().strip("\"'"), session), "sources": []}


if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "out")
    csv = os.path.join(tmp, "contacts.csv")
    open(csv, "w", encoding="utf-8").write(
        "name,email,invoice,total\n"
        "Ali Khan,ali@corp.com,INV-101,2500\n"
        "Sara Malik,sara@corp.com,INV-102,1800\n"
        "NoMail,notanemail,INV-103,99\n")
    assert match("mailmerge " + csv + ": Dear {name}, invoice {invoice} total {total}.")
    assert match("mailmerge the csv " + csv + " with template 'Hi {name}!'")
    assert match("mail merge the csv " + csv + " template 'x {name} y'")
    assert match("show my mailmerge drafts")
    assert not match("merge the two files")
    assert not match("mailmerge only")                       # no csv, no template

    r = run("mailmerge " + csv + ": Dear {name}, invoice {invoice} total {total}. Regards.", {"_mailmerge_out": out})
    assert "2 drafts" in r["reply"], r
    d1 = open(os.path.join(out, "001-ali_corp.com.txt"), encoding="utf-8").read()
    assert "Dear Ali Khan" in d1 and "INV-101" in d1 and "2500" in d1, d1
    assert "To: ali@corp.com" in d1
    assert "Skipped 1 row(s)" in r["reply"], r                      # the notanemail row is invalid, not a recipient
    # unknown placeholder -> empty fill + reported, never a raw {brace} in a draft
    r2 = run("mailmerge " + csv + ": Hello {name}, ref {nope}.", {"_mailmerge_out": out})
    d2 = open(os.path.join(out, "002-sara_corp.com.txt"), encoding="utf-8").read()
    assert "{nope}" not in d2 and "ref ." in d2, d2
    assert "nope" in r2["reply"], r2
    # missing file, empty csv
    assert "not found" in run("mailmerge D:/nope.csv: hi {name}", {})["reply"].lower()
    empty = os.path.join(tmp, "empty.csv")
    open(empty, "w", encoding="utf-8").write("name,email\n")
    assert "No data rows" in run("mailmerge " + empty + ": hi {name}", {})["reply"]
    # show
    r3 = run("show my mailmerge drafts", {"_mailmerge_out": out})
    assert "drafts in" in r3["reply"] and "001-ali_corp.com.txt" in r3["reply"], r3
    print("[self-check] mailmerge OK - CSV+template to per-row drafts in data/mailmerge, "
          "nothing sent, unknown placeholders reported")
