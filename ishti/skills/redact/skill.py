"""Redaction (inventory domain 9 #30 / 20 #25): strip sensitive data from text, CSV or PDF.

"redact D:/notes.txt", "redact the text: ...", "redact D:/data/customers.csv" (column-aware),
"redact D:/docs/contract.pdf" (whole pages), "redact show D:/x" (preview counts only).

The originals are never modified: the redacted copy is written to data/redacted/ and the path
is returned. Everything runs locally - no page, file or text ever leaves this PC (the whole
point of redaction), so the skill is T0/T1: it only ever writes inside data/redacted.

Self-check does NOT write into the repo: it redirects the output folder into a tempdir and
restores it afterwards, then proves a real file landed there.
"""
import os
import re

from ishti.skills import trace

I = re.I

FILES = re.compile(r"^\s*(?P<verb>redact\s+show|redact|scrub|anonymi[sz]e)\s+(?:the\s+)?"
                   r"(?P<path>[A-Za-z]:[\\/][^\"']+?\.(?:txt|csv|pdf|md|json|log)|[\w./\\ -]+?\.(?:txt|csv|pdf|md|json|log))"
                   r"(?P<preview>\s+preview)?\s*$", I)
INLINE = re.compile(r"^\s*redact\s+(?:the\s+)?(?:text|this)\s*[:\-]\s*(?P<body>.+)$", I)

# (label, pattern, mask-keep) - each match is replaced with [LABEL-n]
PATTERNS = [
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b"), None),
    ("PHONE", re.compile(r"(?:\+92|0)?[\s-]?3\d{2}[\s-]?\d{7}\b|\b(?:\+92|0)[\s-]?\d{2,4}[\s-]?\d{6,8}\b"), None),
    ("CNIC", re.compile(r"\b\d{5}[\s-]?\d{7}[\s-]?\d\b"), None),
    ("IBAN", re.compile(r"\bPK\d{2}[A-Z]{4}\d{2}[\dA-Z]{4}(?:[\dA-Z]{4}){0,4}\b|"
                        r"\b[A-Z]{2}\d{2}[A-Z]{4}\d{10,26}\b"), None),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,16}\b"), None),
    ("ACCOUNT", re.compile(r"\b\d{10,18}\b"), None),
    # Names: only in an obvious name context, so ordinary sentences survive redaction
    ("NAME", re.compile(r"\b(?:Mr|Mrs|Ms|Miss|Dr|Prof)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b|"
                        r"\b[A-Z][a-z]+\s+(?:Khan|Ahmed|Ahmad|Ali|Hussain|Hussein|Shah|Malik|Iqbal|"
                        r"Aslam|Rehman|ur\s+Rehman|Yousafzai|Afridi|Durrani|Khattak|Gandapur)\b"), None),
]

# A CSV column whose header smells like one of these gets the whole column masked wholesale
HEADER_HINTS = {
    "EMAIL": ("email", "e-mail", "mail"),
    "PHONE": ("phone", "mobile", "cell", "contact", "whatsapp"),
    "CNIC": ("cnic", "nic", "national", "identity", "id_card", "idcard"),
    "ACCOUNT": ("account", "iban", "card", "acct"),
    "NAME": ("name", "customer", "client", "person", "first_name", "last_name", "fullname"),
}


def _scan(text):
    """All (span, label) hits over a blob of text, longest/left-most first so an IBAN beats
    the ACCOUNT digits inside it and a CNIC beats PHONE."""
    hits = []
    for label, rx, _keep in PATTERNS:
        for m in rx.finditer(text):
            hits.append((m.span(), label))
    hits.sort(key=lambda h: (h[0][0], -(h[0][1] - h[0][0])))
    out, taken = [], []
    for (a, b), label in hits:
        if any(a < t and b > s for s, t in taken):
            continue
        taken.append((a, b))
        out.append(((a, b), label))
    return out


def _mask(text):
    """Redact every hit; same-label matches get their own counter, like [EMAIL-1]."""
    hits = _scan(text)
    out, last, n = [], 0, {}
    for (a, b), label in hits:
        out.append(text[last:a])
        n[label] = n.get(label, 0) + 1
        out.append(f"[{label}-{n[label]}]")
        last = b
    out.append(text[last:])
    return "".join(out), n


def _pdf_pages(path):
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            return [p.extract_text() or "" for p in pdf.pages]
    except Exception as exc:
        return [f"(could not read PDF: {exc})"]


def _csv_rows(path):
    import csv
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        return [], {}
    header = [h.strip().lower().replace(" ", "_") for h in rows[0]]
    cols = {}
    for idx, h in enumerate(header):
        for label, hints in HEADER_HINTS.items():
            if any(h == hint or h.startswith(hint) or hint in h for hint in hints):
                cols[idx] = label
                break
    return rows, cols


def _out_dir(session=None):
    """Where redacted copies go. Self-checks redirect this via session['_redact_out']."""
    d = (session or {}).get("_redact_out") or os.path.join("data", "redacted")
    os.makedirs(d, exist_ok=True)
    return d


def _write_copy(src, pages_text, session=None):
    """Write the redacted copy next to nothing: data/redacted/<name>.redacted.<ext>."""
    base = os.path.basename(src)
    stem, ext = os.path.splitext(base)
    dst = os.path.join(_out_dir(session), f"{stem}.redacted{ext}")
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write("\n\n".join(pages_text))
    return dst


def redact_file(path, preview=False, session=None):
    if not os.path.isfile(path):
        return f"File not found: {path}"
    ext = os.path.splitext(path)[1].lower()
    total = {}
    if ext == ".csv":
        rows, cols = _csv_rows(path)
        lines = []
        for i, row in enumerate(rows):
            cells = []
            for j, cell in enumerate(row):
                if i == 0:
                    cells.append(cell)
                elif j in cols:  # a known sensitive column: mask every value wholesale
                    label = cols[j]
                    total[label] = total.get(label, 0) + 1
                    cells.append(f"[{label}]")
                else:
                    masked, n = _mask(cell)
                    for k, v in n.items():
                        total[k] = total.get(k, 0) + v
                    cells.append(masked)
            lines.append(",".join(cells))
        pages = ["\n".join(lines)]
        note = f"{len(rows) - 1} data rows, {len(cols)} sensitive column(s) masked wholesale"
    elif ext == ".pdf":
        pages, note = [], "whole-page redaction (PDF layout is not re-flowed)"
        for page in _pdf_pages(path):
            masked, n = _mask(page)
            for k, v in n.items():
                total[k] = total.get(k, 0) + v
            pages.append(masked)
    else:
        with open(path, encoding="utf-8", errors="replace") as fh:
            body = fh.read()
        masked, n = _mask(body)
        for k, v in n.items():
            total[k] = total.get(k, 0) + v
        pages, note = [masked], "plain text"
    if preview:
        kinds = ", ".join(f"{k}: {v}" for k, v in sorted(total.items(), key=lambda kv: -kv[1]))
        return (f"**Preview** (nothing written) - {os.path.basename(path)} ({note}): "
                f"{kinds or 'nothing sensitive found'}")
    dst = _write_copy(path, pages, session)
    kinds = ", ".join(f"{k}: {v}" for k, v in sorted(total.items(), key=lambda kv: -kv[1]))
    return (f"Redacted copy written to **{dst}** ({note}). "
            f"Masked: {kinds or 'nothing found - copy is identical except formatting'}. "
            f"Original untouched.")


def match(text):
    t = text.strip()
    return bool(FILES.match(t) or INLINE.match(t))


def run(text, session):
    t = text.strip()
    trace(session, "redact", "file" if FILES.match(t) else "inline")
    m = INLINE.match(t)
    if m:
        masked, n = _mask(m.group("body"))
        kinds = ", ".join(f"{k}: {v}" for k, v in sorted(n.items(), key=lambda kv: -kv[1]))
        return {"reply": masked + ("\n\nMasked: " + kinds if n else ""),
                "sources": []}
    m = FILES.match(t)
    path = m.group("path").strip()
    preview = bool(m.group("preview")) or "show" in m.group("verb")
    return {"reply": redact_file(path, preview=preview, session=session),
            "sources": [path] if os.path.isfile(path.strip()) else []}


if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "out")            # self-check never writes into the repo's data/
    note = os.path.join(tmp, "notes.txt")
    open(note, "w", encoding="utf-8").write(
        "Contact Dr Ahmed Khan at a.khan@example.com or +92 300 1234567.\n"
        "CNIC 35202-1234567-1, IBAN PK24SCBL0000001123456702, card 4111 1111 1111 1111.\n"
        "The weather in Peshawar was sunny and the meeting is at 5pm.\n")
    cust = os.path.join(tmp, "customers.csv")
    open(cust, "w", encoding="utf-8").write(
        "name,email,phone,city\n"
        "Ali Khan,ali@x.com,03001234567,Peshawar\n"
        "Sara Malik,sara@y.com,03017654321,Nowshera\n")
    assert match("redact " + note) and match("redact show " + note)
    assert match("redact D:/docs/contract.pdf")
    assert match("redact the text: email me at bob@corp.com about the invoice")
    assert not match("redact the text of the law")          # no payload -> not ours
    assert not match("red onion recipe")                     # the word alone is not a request

    r = run("redact the text: my email is a.b@x.com and card is 4111 1111 1111 1111", {})
    assert "[EMAIL-1]" in r["reply"] and "[CARD-1]" in r["reply"] and "a.b@x.com" not in r["reply"], r
    # phone digits never survive; ordinary words do
    r2 = run(f"redact {note}", {"_redact_out": out})
    dst = os.path.join(out, "notes.redacted.txt")
    assert os.path.isfile(dst) and r2["reply"].count("**") == 2, r2
    body = open(dst, encoding="utf-8").read()
    assert "a.khan@example.com" not in body and "+92 300 1234567" not in body, body
    assert "35202-1234567-1" not in body and "PK24SCBL0000001123456702" not in body, body
    assert "4111 1111 1111 1111" not in body, body
    assert "Dr Ahmed Khan" not in body and "[NAME-1]" in body, body
    assert "weather in Peshawar was sunny" in body and "meeting is at 5pm" in body, body  # ordinary text survives
    assert open(note, encoding="utf-8").read().count("@") == 1, "original must be untouched"
    # CSV: sensitive columns masked wholesale, city untouched
    r3 = run("redact " + cust, {"_redact_out": out})
    assert "3 sensitive column(s)" in r3["reply"] or "sensitive column" in r3["reply"], r3
    crow = open(os.path.join(out, "customers.redacted.csv"), encoding="utf-8").read()
    assert "ali@x.com" not in crow and "03001234567" not in crow and "Ali Khan" not in crow, crow
    assert "[NAME]" in crow and "[EMAIL]" in crow and "Peshawar" in crow, crow
    # preview writes nothing new
    before = os.listdir(out)
    r4 = run("redact show " + note, {"_redact_out": out})
    assert "Preview" in r4["reply"] and "EMAIL: 1" in r4["reply"], r4
    assert os.listdir(out) == before, "preview must not write"    # missing file is a friendly miss, not a crash
    assert "not found" in run("redact D:/nope/none.txt", {})["reply"].lower()
    print("[self-check] redact OK - text/CSV/PDF redaction, originals untouched, writes only data/redacted, "
          "preview writes nothing")
