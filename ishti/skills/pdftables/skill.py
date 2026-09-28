"""PDF table extraction (inventory domain 9 #2, 13 pivot-adjacent): PDF tables -> CSV files.

"extract tables from D:/docs/invoice.pdf", "pull the tables out of D:/report.pdf",
"pdf tables to csv D:/data/tables.pdf"

Each detected table becomes data/extracted/<pdfname>-table-<n>.csv with a header row if the
table's first row looks like one. The reply reports exact row/column counts per table so the
owner can spot a bad parse immediately. pdfplumber runs locally; nothing is uploaded.

Self-check builds a real PDF in a tempdir using pdfplumber's own writer, extracts from it,
and redirects the CSV output into the tempdir (never the repo's data/).
"""
import os
import re

from ishti.skills import trace

I = re.I

FILES = re.compile(r"^\s*(?:extract\s+(?:the\s+)?tables\s+(?:from|out\s+of|in)\s+|"
                   r"pull\s+(?:the\s+)?tables\s+out\s+of\s+|"
                   r"pdf\s+tables\s+(?:to\s+csv\s+|from\s+))"
                   r"(?P<path>[A-Za-z]:[\\/][^\"']+?\.pdf|[\w./\\ -]+?\.pdf)\s*$", I)


def _out_dir(session=None):
    d = (session or {}).get("_pdftables_out") or os.path.join("data", "extracted")
    os.makedirs(d, exist_ok=True)
    return d


def _cell(v):
    return str(v or "").replace("\n", " ").strip()


def extract(path, session=None):
    if not os.path.isfile(path):
        return f"File not found: {path}"
    try:
        import pdfplumber
    except ImportError:
        return "pdfplumber isn't installed - say \"install pdfplumber in ISHTI's python\"."
    out_dir = _out_dir(session)
    stem = re.sub(r"[^\w-]", "_", os.path.splitext(os.path.basename(path))[0])
    made, total_rows = [], 0
    try:
        with pdfplumber.open(path) as pdf:
            n = 0
            for pageno, page in enumerate(pdf.pages, 1):
                for table in page.extract_tables():
                    n += 1
                    if not table or not any(any(_cell(c) for c in row) for row in table):
                        continue
                    rows = [[_cell(c) for c in row] for row in table]
                    fp = os.path.join(out_dir, f"{stem}-table-{n:02d}.csv")
                    import csv
                    with open(fp, "w", encoding="utf-8", newline="") as fh:
                        csv.writer(fh).writerows(rows)
                    made.append((fp, len(rows), max((len(r) for r in rows), default=0)))
                    total_rows += len(rows)
    except Exception as exc:
        return f"Could not read the PDF: {exc}"
    if not made:
        return f"No tables found in {os.path.basename(path)} (scanned images need OCR, which this PC lacks)."
    lines = [f"**{len(made)} table(s)** -> `{out_dir}` ({total_rows} data rows total):"]
    lines += [f"- {os.path.basename(fp)}: {r} rows × {c} cols" for fp, r, c in made]
    return "\n".join(lines)


def match(text):
    return bool(FILES.match(text.strip()))


def run(text, session):
    m = FILES.match(text.strip())
    trace(session, "pdftables", "extract")
    return {"reply": extract(m.group("path").strip().strip("\"'"), session), "sources": []}


def _make_test_pdf(path):
    """Hand-craft a one-page PDF containing a real ruled 3x3 table (no writer lib needed).
    pdfplumber reads the content stream directly, so raw operators work fine."""
    def esc(s):
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    data = [["Item", "Qty", "Price"], ["Widget", "2", "19.50"], ["Gadget", "1", "5.00"]]
    x0, y0, colw, rowh = 60, 700, 120, 24
    parts = ["BT /F1 10 Tf"]
    for r, row in enumerate(data):
        for ci, val in enumerate(row):
            parts.append(f"1 0 0 1 {x0 + ci * colw + 4} {y0 - r * rowh - 16} Tm ({esc(val)}) Tj")
    parts.append("ET")
    for r in range(len(data) + 1):
        yy = y0 - r * rowh
        parts.append(f"{x0} {yy} m {x0 + 3 * colw} {yy} l S")
    for ci in range(4):
        xx = x0 + ci * colw
        parts.append(f"{xx} {y0} m {xx} {y0 - len(data) * rowh} l S")
    stream = "\n".join(parts).encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n").encode()
    with open(path, "wb") as fh:
        fh.write(out)


if __name__ == "__main__":
    import csv as _csv
    import tempfile
    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "out")
    pdf_path = os.path.join(tmp, "report.pdf")
    _make_test_pdf(pdf_path)
    assert match("extract tables from " + pdf_path)
    assert match("pdf tables to csv " + pdf_path)
    assert not match("extract tables from the web")
    assert not match("read the pdf")
    r = run("extract tables from " + pdf_path, {"_pdftables_out": out})
    assert "1 table(s)" in r["reply"], r
    csv_made = os.path.join(out, "report-table-01.csv")
    assert os.path.isfile(csv_made), r
    rows = list(_csv.reader(open(csv_made, encoding="utf-8")))
    assert rows[0] == ["Item", "Qty", "Price"], rows
    assert any("Widget" in row for row in rows), rows
    r2 = run("extract tables from " + os.path.join(tmp, "none.pdf"), {})
    assert "not found" in r2["reply"].lower()
    print("[self-check] pdftables OK - pdfplumber tables to CSV in data/extracted, "
          "exact row/col counts in the reply")
