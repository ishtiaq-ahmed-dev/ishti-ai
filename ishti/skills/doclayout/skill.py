"""Document layout analysis (inventory domain 3 #10): pdfplumber, measured, no guessing.

"layout of D:/docs/report.pdf", "doc layout analysis of D:/forms/form.pdf",
"analyze the pdf D:/docs/invoice.pdf", "structure of D:/docs/thesis.pdf"

Per page, measured from the PDF itself: page count and size, text lines grouped by their
y-position, the largest-font line (the title), heading-like lines (>= 80% of title size),
ruled lines (pdfplumber's lines/rects objects) and detected tables. A scanned page with no
text layer is reported as exactly that instead of an empty analysis. T0, read-only.
"""
import os
import re

from ishti.skills import trace

I = re.I

PATH = r"[A-Za-z]:[\\/][^\"']+?\.pdf|[\w./\\ -]+?\.pdf"
LAYOUT = re.compile(
    rf"^\s*(?:doc\s+)?(?:layout|structure)\s+(?:analysis\s+)?(?:of|for)\s+(?:the\s+)?(?P<path>{PATH})\s*$|"
    rf"^\s*(?:analyse|analyze)\s+(?:the\s+)?(?:pdf\s+)?(?P<path2>{PATH})\s*$", I)


def _page_report(page, idx):
    words = page.extract_words(extra_attrs=["size"]) or []
    lines = {}
    for w in words:
        lines.setdefault(round(w["top"] / 4), []).append(w)
    sizes = [w.get("size") or 10 for w in words]
    title_size = max(sizes) if sizes else 0
    title = next((" ".join(x["text"] for x in sorted(ws, key=lambda x: x["x0"]))
                  for _, ws in sorted(lines.items())
                  if any((x.get("size") or 0) >= title_size - 0.1 for x in ws)), "")
    headings = sum(1 for _, ws in lines.items()
                   if any((x.get("size") or 0) >= title_size * 0.8 for x in ws))
    ruled = len(page.lines) + len(page.rects)
    try:
        tables = len(page.find_tables())
    except Exception:
        tables = 0
    return (f"Page {idx}: {len(lines)} text line(s); title: \"{title[:70] or '-'}\" "
            f"({title_size:.0f} pt); {headings} heading-like line(s); "
            f"{ruled} ruled line(s); {tables} table(s).")


def analyze(path):
    if not os.path.isfile(path):
        return f"File not found: {path}"
    try:
        import pdfplumber
    except ImportError:
        return "pdfplumber isn't installed."
    try:
        with pdfplumber.open(path) as pdf:
            n = len(pdf.pages)
            first = pdf.pages[0]
            size = f"{first.width:.0f}×{first.height:.0f} pt"
            reports = []
            for i, page in enumerate(pdf.pages[:10], 1):
                if not page.extract_text().strip():
                    reports.append(f"Page {i}: no text layer — this page is a scan/image (OCR it first).")
                else:
                    reports.append(_page_report(page, i))
    except Exception as exc:
        return f"Could not parse that PDF: {exc}"
    if n > 10:
        reports.append(f"(+{n - 10} more pages not shown)")
    return (f"**{os.path.basename(path)}** — {n} page(s) ({size}).\n" + "\n".join(reports))


def match(text):
    t = text.strip()
    return bool(LAYOUT.match(t)) and t.lower().endswith(".pdf")


def run(text, session):
    t = text.strip()
    if (m := LAYOUT.match(t)):
        trace(session, "doclayout", "analyze")
        return {"reply": analyze(m.group("path") or m.group("path2")), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile

    def _make_test_pdf(path):
        """Raw content-stream PDF: a 16pt title, three 10pt body lines, ruled lines."""
        def esc(s):
            return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        parts = ["BT /F1 16 Tf 1 0 0 1 60 780 Tm (QUARTERLY REPORT) Tj",
                 "/F1 10 Tf 1 0 0 1 60 750 Tm (Revenue grew in every region this quarter.) Tj",
                 "1 0 0 1 60 735 Tm (Costs stayed flat under the new supplier terms.) Tj",
                 "1 0 0 1 60 720 Tm (Outlook for next quarter remains positive.) Tj", "ET",
                 "60 700 m 500 700 l S", "60 690 m 500 690 l S"]
        stream = "\n".join(parts).encode("latin-1")
        objs = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ]
        with open(path, "wb") as fh:
            fh.write(b"%PDF-1.4\n")
            for i, o in enumerate(objs, 1):
                fh.write(f"{i} 0 obj\n".encode() + o + b"\nendobj\n")
            xref = fh.tell()
            fh.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
            for o in objs:
                pass
        # simple xref-less trailer works for pdfplumber's lenient parser? No - build offsets properly
        with open(path, "wb") as fh:
            fh.write(b"%PDF-1.4\n")
            offsets = []
            for i, o in enumerate(objs, 1):
                offsets.append(fh.tell())
                fh.write(f"{i} 0 obj\n".encode() + o + b"\nendobj\n")
            xref = fh.tell()
            fh.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
            for off in offsets:
                fh.write(f"{off:010d} 00000 n \n".encode())
            fh.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())

    tmp = tempfile.mkdtemp()
    pdf_path = os.path.join(tmp, "report.pdf")
    _make_test_pdf(pdf_path)

    assert match("layout of " + pdf_path)
    assert match("doc layout analysis of " + pdf_path)
    assert match("analyze the pdf " + pdf_path)
    assert not match("layout out the furniture in my room")
    assert not match("analyze the csv D:/x.pdf")

    r = run("layout of " + pdf_path, {})
    assert "1 page(s)" in r["reply"], r
    assert "QUARTERLY REPORT" in r["reply"] and "16 pt" in r["reply"], r
    assert "4 text line(s)" in r["reply"], r
    assert "2 ruled line(s)" in r["reply"], r
    assert "0 table(s)" in r["reply"], r
    r2 = run("layout of D:/definitely/missing.pdf", {})
    assert "not found" in r2["reply"].lower(), r2
    print("[self-check] doclayout OK - real hand-crafted PDF parsed: page size, title by font size, "
          "text-line grouping, ruled-line count, missing-file path honest")
