"""Documents, PDF and Office - pikepdf/pypdf, pdfplumber, python-docx, python-pptx, pandas/openpyxl, matplotlib,
and Microsoft Word itself (COM) for Word <-> PDF.

PDF:     merge, split (every page or a range), compress, extract tables
Convert: Word -> PDF, PDF -> Word (Word opens PDFs as editable documents)
Read:    summarise any PDF / Word / Excel / PowerPoint / text file, compare two Word documents
Excel:   what's in a sheet (columns, sizes, stats), clean it (empty rows/columns, spaces, duplicates), chart a column
Create:  a Word document or a PowerPoint deck on a topic (the local model writes it)
Notes:   capture a note (Markdown in Documents\\ISHTI Notes), search notes
Outputs go next to the source file or to Documents\\ISHTI."""
import datetime as dt
import os
import re
import time

from ishti import llm
from ishti.untrusted import guard
from ishti.skills import trace

I = re.I
DOC_EXT = (".pdf", ".docx", ".doc", ".xlsx", ".xls", ".csv", ".pptx", ".txt", ".md")
# FIX-8 (full-skill sweep): Windows accepts both separators; users type D:/path constantly and
# the backslash-only pattern made docs.reply "give me the full path" for existing files.
PATH = re.compile(r"[A-Za-z]:[\\/][^\"'<>|?*\n]+?\.(?:pdf|docx?|xlsx?|csv|pptx|txt|md)\b", I)
HOME = os.path.join(os.path.expanduser("~"), "Documents", "ISHTI")
NOTES = os.path.join(os.path.expanduser("~"), "Documents", "ISHTI Notes")
OPS = [
    ("invoice", re.compile(r"\b(?:make|create|generate|write|prepare|send)\s+(?:an?\s+)?invoice\b", I)),
    ("pdf_form", re.compile(r"\b(?:fill(?:\s+in|\s+out)?|complete)\b.+\.pdf\b|\b(?:what|which|list)\s+(?:form\s+)?fields\b.+\.pdf\b", I)),
    ("note_add", re.compile(r"^\s*(?:take\s+a\s+note|note(?:\s+this)?|make\s+a\s+note|jot\s+down|write\s+down)\s*[:,-]?\s*(?P<body>.+)", I | re.S)),
    ("note_find", re.compile(r"\b(?:search|find|show|list)\s+(?:in\s+)?(?:my\s+)?notes\b", I)),
    ("make_deck", re.compile(r"\b(?:make|create|build|generate|prepare)\s+(?:a\s+|an\s+)?(?:powerpoint|presentation|slides?|deck|ppt)\b", I)),
    # "research X and write a report" is deep research (sources, citations), not a document written from memory
    ("make_doc", re.compile(r"^(?!.*\b(?:research|investigat\w*|in-depth)\b).*\b(?:make|create|write|draft|prepare)\s+(?:a\s+|an\s+)?(?:word\s+)?(?:document|doc|docx|report|letter)\b(?!.*\bpdf\b.*\bto\b)", I)),
    ("pdf_merge", re.compile(r"\b(?:merge|combine|join)\b.+\.pdf\b.+\.pdf\b", I)),
    ("pdf_split", re.compile(r"\b(?:split|separate|extract\s+pages?)\b.+\.pdf\b", I)),
    ("pdf_compress", re.compile(r"\b(?:compress|shrink|reduce(?:\s+the)?\s+size\s+of|make\s+smaller)\b.+\.pdf\b", I)),
    ("pdf_tables", re.compile(r"\b(?:tables?)\b.+\.pdf\b|\.pdf\b.+\btables?\b", I)),
    ("to_pdf", re.compile(r"\b(?:convert|save|export|turn)\b.+\.docx?\b.+\b(?:to|as|into)\s+(?:a\s+)?pdf\b", I)),
    ("to_word", re.compile(r"\b(?:convert|turn|export)\b.+\.pdf\b.+\b(?:to|into|as)\s+(?:a\s+)?(?:word|docx?)\b", I)),
    ("compare_docs", re.compile(r"\b(?:compare|diff|what\s+changed\s+between)\b.+\.docx?\b.+\.docx?\b", I)),
    ("excel_clean", re.compile(r"\bclean\b.+\.(?:xlsx?|csv)\b", I)),
    ("excel_chart", re.compile(r"\b(?:chart|graph|plot)\b.+\.(?:xlsx?|csv)\b|\.(?:xlsx?|csv)\b.+\b(?:chart|graph|plot)\b", I)),
    ("summarize", re.compile(r"\b(?:summari[sz]e|summary\s+of|what(?:'s| is)\s+in|read|tldr|key\s+points\s+(?:of|in))\b.+\.(?:pdf|docx?|xlsx?|csv|pptx|txt|md)\b", I)),
]


def match(text):
    return any(rx.search(text) for _, rx in OPS)


def run(text, session):
    for name, rx in OPS:
        if m := rx.search(text):
            trace(session, "docs", name.replace("_", " "), status="start")
            try:
                out = globals()["op_" + name](m, text, session)
            except IndexError:  # an op that needs a file found no existing path in the request
                out = "Give me the full path of the file (it has to exist), e.g. "+os.path.join(os.path.expanduser("~"), "Documents", "report.docx")+"."
            trace(session, "docs", "done", status="done")
            return out if isinstance(out, dict) or out is None else {"reply": out, "sources": []}
    return None


def paths(text, ext=DOC_EXT):
    return [p for p in PATH.findall(text) if os.path.isfile(p) and p.lower().endswith(ext)]


def beside(src, suffix, ext):
    stem = os.path.splitext(src)[0]
    out, n = f"{stem}_{suffix}{ext}", 1
    while os.path.exists(out):
        out, n = f"{stem}_{suffix}_{n}{ext}", n + 1
    return out


# ---------------------------------------------------------------- reading any document
def text_of(path, limit=60000):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        import pypdfium2 as pdfium
        from ishti import PDFIUM_LOCK
        with PDFIUM_LOCK:
            doc = pdfium.PdfDocument(path)
            body = "\n".join(doc[i].get_textpage().get_text_range() for i in range(min(len(doc), 60)))
        if len(body.strip()) < 40 * min(len(doc), 60):  # (almost) no text layer: a scan - read the page images
            from ishti.ocr import ocr_pdf
            body = ocr_pdf(path).replace("\f", "\n")
        return body[:limit]
    if ext == ".docx":
        import docx
        d = docx.Document(path)
        body = [p.text for p in d.paragraphs] + [" | ".join(c.text for c in r.cells) for t in d.tables for r in t.rows]
        return "\n".join(body)[:limit]
    if ext == ".pptx":
        from pptx import Presentation
        return "\n".join(sh.text_frame.text for s in Presentation(path).slides for sh in s.shapes if sh.has_text_frame)[:limit]
    if ext in (".xlsx", ".xls", ".csv"):
        df = _frame(path)
        return df.head(200).to_csv(index=False)[:limit]
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read(limit)


def op_summarize(m, text, session):
    got = paths(text)
    if not got:
        return "Give me the document's full path."
    try:
        body = text_of(got[0])
    except Exception as exc:  # truncated download, wrong extension, password-protected
        return f"I can't open {os.path.basename(got[0])} - it looks damaged or protected ({type(exc).__name__})."
    if len(body.strip()) < 40:
        return f"{os.path.basename(got[0])} has no readable text, even with OCR."
    focus = re.sub(PATH, "", text)
    return llm.ask(f"Document: {os.path.basename(got[0])}\n\n{guard(body[:14000], os.path.basename(got[0]), session)}\n\nThe owner asks: {focus}\n"
                   "Summarise it for them: what it is, the key points, numbers, dates and anything they need to act on. "
                   "Short sections.", max_tokens=700)


def op_compare_docs(m, text, session):
    import difflib
    a, b = paths(text, (".docx", ".doc"))[:2]
    diff = [l for l in difflib.unified_diff(text_of(a).splitlines(), text_of(b).splitlines(), lineterm="", n=0)
            if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))]
    if not diff:
        return "The two documents have the same text."
    return f"{len(diff)} changed lines ({os.path.basename(a)} -> {os.path.basename(b)}):\n```diff\n" + "\n".join(diff[:80]) + "\n```"


# ---------------------------------------------------------------- PDF
def op_pdf_merge(m, text, session):
    import pikepdf
    files = paths(text, (".pdf",))
    if len(files) < 2:
        return "Give me at least two PDF paths."
    out = pikepdf.Pdf.new()
    for f in files:
        with pikepdf.open(f) as src:
            out.pages.extend(src.pages)
    dst = beside(files[0], "merged", ".pdf")
    out.save(dst)
    return f"Merged {len(files)} PDFs ({len(out.pages)} pages): {dst}"


def op_pdf_split(m, text, session):
    import pikepdf
    src = paths(text, (".pdf",))[0]
    rng = re.search(r"pages?\s+(\d+)\s*(?:-|to|–)\s*(\d+)", text, I)
    with pikepdf.open(src) as pdf:
        n = len(pdf.pages)
        if rng:
            a, b = max(1, int(rng.group(1))), min(n, int(rng.group(2)))
            out = pikepdf.Pdf.new()
            out.pages.extend(pdf.pages[a - 1:b])
            dst = beside(src, f"pages_{a}-{b}", ".pdf")
            out.save(dst)
            return f"Pages {a}-{b} of {n}: {dst}"
        folder = os.path.splitext(src)[0] + "_pages"
        os.makedirs(folder, exist_ok=True)
        for i, page in enumerate(pdf.pages, 1):
            one = pikepdf.Pdf.new()
            one.pages.append(page)
            one.save(os.path.join(folder, f"page_{i:03d}.pdf"))
    return f"Split into {n} single-page PDFs in {folder}"


def op_pdf_compress(m, text, session):
    import pikepdf
    src = paths(text, (".pdf",))[0]
    dst = beside(src, "small", ".pdf")
    with pikepdf.open(src) as pdf:
        pdf.remove_unreferenced_resources()
        pdf.save(dst, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate,
                 recompress_flate=True, linearize=True)
    a, b = os.path.getsize(src), os.path.getsize(dst)
    note = "" if b < a * 0.9 else " (little gain: most PDF size is images; recompressing those needs Ghostscript, which is not installed)"
    if b >= a * 0.9:
        from ishti.skills import log_need
        log_need("gap", "docs", text, "PDF image recompression (Ghostscript) for real size reduction")
    return f"{a / 2**20:.1f} MB -> {b / 2**20:.1f} MB: {dst}{note}"


def op_pdf_tables(m, text, session):
    import pandas as pd
    import pdfplumber
    src = paths(text, (".pdf",))[0]
    frames = []
    with pdfplumber.open(src) as pdf:
        for i, page in enumerate(pdf.pages[:40], 1):
            for t in page.extract_tables():
                if t and len(t) > 1:
                    frames.append((i, pd.DataFrame(t[1:], columns=[str(c or f"col{j}") for j, c in enumerate(t[0])])))
    if not frames:
        return "No tables found (if it's a scanned PDF, the tables are images)."
    dst = beside(src, "tables", ".xlsx")
    with pd.ExcelWriter(dst) as xl:
        for n, (page, df) in enumerate(frames[:30], 1):
            df.to_excel(xl, sheet_name=f"p{page}_t{n}"[:31], index=False)
    first = frames[0][1].head(8).fillna("")
    preview = "| " + " | ".join(map(str, first.columns)) + " |\n|" + "---|" * len(first.columns) + "\n" + \
              "\n".join("| " + " | ".join(str(v)[:30] for v in row) + " |" for row in first.itertuples(index=False))
    return f"{len(frames)} tables extracted to {dst}. First one (page {frames[0][0]}):\n\n{preview}"


# ---------------------------------------------------------------- Word <-> PDF (Microsoft Word via COM)
def _word():
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    w = win32com.client.DispatchEx("Word.Application")
    w.Visible, w.DisplayAlerts = False, 0
    return w


def docx_to_pdf(src, dst=None):
    dst = dst or (beside(src, "pdf", ".pdf").replace("_pdf.pdf", ".pdf") if not os.path.exists(os.path.splitext(src)[0] + ".pdf")
                  else beside(src, "export", ".pdf"))
    w = _word()
    try:
        doc = w.Documents.Open(os.path.abspath(src), ReadOnly=True)
        doc.SaveAs2(os.path.abspath(dst), FileFormat=17)  # wdFormatPDF
        doc.Close(False)
    finally:
        w.Quit()
    return dst


def op_to_pdf(m, text, session):
    return f"PDF: {docx_to_pdf(paths(text, ('.docx', '.doc'))[0])}"


def op_invoice(m, text, session):
    from ishti.skills.docs.forms import op_invoice as make
    return make(m, text, session, docx_to_pdf)


def op_pdf_form(m, text, session):
    from ishti.skills.docs.forms import op_pdf_form as form
    return form(m, text, session, paths)


def op_to_word(m, text, session):
    src = paths(text, (".pdf",))[0]
    dst = beside(src, "editable", ".docx")
    w = _word()
    try:
        doc = w.Documents.Open(os.path.abspath(src), ConfirmConversions=False, ReadOnly=True)  # Word's PDF reflow
        doc.SaveAs2(os.path.abspath(dst), FileFormat=16)  # wdFormatDocumentDefault
        doc.Close(False)
    finally:
        w.Quit()
    return f"Editable Word document: {dst} (layout may shift a little - Word rebuilds the PDF as text)"


# ---------------------------------------------------------------- Excel
def _frame(path):
    import pandas as pd
    return pd.read_csv(path) if path.lower().endswith(".csv") else pd.read_excel(path)


def op_excel_clean(m, text, session):
    src = paths(text, (".xlsx", ".xls", ".csv"))[0]
    df = _frame(src)
    before = df.shape
    df = df.dropna(how="all").dropna(axis=1, how="all")
    for c in df.select_dtypes(include=["object", "string"]):
        df[c] = df[c].astype(str).str.strip().replace({"nan": None, "": None})
    dups = int(df.duplicated().sum())
    df = df.drop_duplicates()
    dst = beside(src, "clean", ".xlsx")
    df.to_excel(dst, index=False)
    return (f"{before[0]}x{before[1]} -> {df.shape[0]}x{df.shape[1]}: removed empty rows/columns, trimmed spaces, "
            f"dropped {dups} duplicate rows. Saved: {dst}")


def op_excel_chart(m, text, session):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    src = paths(text, (".xlsx", ".xls", ".csv"))[0]
    df = _frame(src)
    nums = list(df.select_dtypes("number").columns)
    if not nums:
        return "That sheet has no numeric column to chart."
    want = next((c for c in df.columns if str(c).lower() in text.lower()), None)
    y = want if want in nums else nums[0]
    label_col = next((c for c in df.columns if c != y and df[c].dtype == object), None)
    data = df[[c for c in (label_col, y) if c is not None]].dropna().head(30)
    fig, ax = plt.subplots(figsize=(9, 4.5), dpi=120)
    if label_col and re.search(r"\b(?:bar|compare)\b", text, I) or (label_col and len(data) <= 15):
        ax.bar(data[label_col].astype(str), data[y], color="#6e56cf")
        plt.xticks(rotation=35, ha="right")
    else:
        ax.plot(range(len(data)), data[y], color="#6e56cf", linewidth=2)
    ax.set_title(f"{y}" + (f" by {label_col}" if label_col else ""))
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    col = re.sub(r"\W+", "_", str(y))[:20]
    dst = beside(src, f"chart_{col}", ".png")
    fig.savefig(dst)
    plt.close(fig)
    return f"Chart: {dst}"


# ---------------------------------------------------------------- create
def op_make_doc(m, text, session):
    import docx
    topic = re.sub(r"^.*?\b(?:document|doc|docx|report|letter)\b\s*(?:about|on|for|regarding)?\s*", "", text, flags=I).strip() or text
    trace(session, "model", "writing the document", status="start")
    body = llm.ask(f"Write the complete document the owner asked for: {text}\nWrite real content - full paragraphs with the "
                   "facts, figures and examples you know (say when a figure may be out of date) - not an outline or "
                   "placeholders. Use '# ' for the title, '## ' for section headings, '- ' only for genuine lists. "
                   "No preamble.", max_tokens=2200)
    d = docx.Document()
    for line in body.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("# "):
            d.add_heading(s[2:], 0)
        elif s.startswith("## "):
            d.add_heading(s[3:], 1)
        elif s.startswith(("- ", "* ")):
            d.add_paragraph(s[2:], style="List Bullet")
        else:
            d.add_paragraph(s.replace("**", ""))
    os.makedirs(HOME, exist_ok=True)
    name = re.sub(r"[^\w -]", "", topic)[:50].strip() or "document"
    dst = os.path.join(HOME, f"{name} {dt.date.today()}.docx")
    d.save(dst)
    return f"Word document: {dst}\n\n" + body[:600]


def op_make_deck(m, text, session):
    import json
    from pptx import Presentation
    from pptx.util import Pt
    trace(session, "model", "outlining the slides", status="start")
    raw = llm.ask(f"Make a presentation outline for: {text}\nReturn JSON {{\"title\": \"...\", \"subtitle\": \"...\", "
                  "\"slides\": [{\"title\": \"...\", \"bullets\": [\"...\"]}]}} with 6-10 slides, 3-5 short bullets each.",
                  system=None, temperature=0.4, max_tokens=1500, response_format={"type": "json_object"})
    try:
        plan = json.loads(raw)
    except ValueError:
        return "The model didn't return a usable outline - try again with a clearer topic."
    prs = Presentation()
    cover = prs.slides.add_slide(prs.slide_layouts[0])
    cover.shapes.title.text = plan.get("title", "Presentation")
    cover.placeholders[1].text = plan.get("subtitle", "")
    for s in plan.get("slides", [])[:15]:
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = s.get("title", "")
        body = slide.placeholders[1].text_frame
        for i, b in enumerate(s.get("bullets", [])[:6]):
            p = body.paragraphs[0] if i == 0 else body.add_paragraph()
            p.text = b
            p.font.size = Pt(20)
    os.makedirs(HOME, exist_ok=True)
    name = re.sub(r"[^\w -]", "", plan.get("title", "deck"))[:50].strip() or "deck"
    dst = os.path.join(HOME, f"{name} {dt.date.today()}.pptx")
    prs.save(dst)
    return f"PowerPoint ({len(plan.get('slides', [])) + 1} slides): {dst}\n" + "\n".join(f"- {s.get('title', '')}" for s in plan.get("slides", [])[:12])


# ---------------------------------------------------------------- notes
def op_note_add(m, text, session):
    body = m.group("body").strip()
    os.makedirs(NOTES, exist_ok=True)
    day = os.path.join(NOTES, f"{dt.date.today()}.md")
    with open(day, "a", encoding="utf-8") as f:
        f.write(f"- {time.strftime('%H:%M')} {body}\n")
    return f"Noted ({day})."


def op_note_find(m, text, session):
    q = re.sub(r"^.*?\bnotes?\b\s*(?:for|about|on)?\s*", "", text, flags=I).strip(" ?.'\"").lower()
    hits = []
    for f in sorted((f for f in os.listdir(NOTES) if f.endswith(".md")), reverse=True) if os.path.isdir(NOTES) else []:
        with open(os.path.join(NOTES, f), encoding="utf-8") as fh:
            hits += [f"- {f[:-3]} {line.strip()[2:]}" for line in fh if not q or q in line.lower()]
    return ("\n".join(hits[:30])) if hits else ("No notes yet." if not q else f"No note mentions '{q}'.")


if __name__ == "__main__":
    import tempfile
    cases = {r"merge D:\a\1.pdf and D:\a\2.pdf": "pdf_merge", r"split D:\a\x.pdf": "pdf_split",
             r"compress D:\a\x.pdf": "pdf_compress", r"extract the tables from D:\a\x.pdf": "pdf_tables",
             r"convert D:\a\cv.docx to pdf": "to_pdf", r"convert D:\a\cv.pdf to word": "to_word",
             r"compare D:\a\v1.docx and D:\a\v2.docx": "compare_docs", r"summarize D:\a\report.pdf": "summarize",
             r"clean D:\a\sales.xlsx": "excel_clean", r"make a chart of revenue in D:\a\sales.xlsx": "excel_chart",
             "make a presentation about local AI assistants": "make_deck", "write a report about solar energy in Pakistan": "make_doc",
             "take a note: call the bank tomorrow": "note_add", "search my notes for bank": "note_find"}
    for t, want in cases.items():
        got = next((n for n, rx in OPS if rx.search(t)), None)
        assert got == want, (t, got, want)
    assert not match("what's the weather") and not match("open youtube")
    import pikepdf
    d = tempfile.mkdtemp()
    for n in ("a", "b"):
        p = pikepdf.Pdf.new()
        p.add_blank_page()
        p.save(os.path.join(d, f"{n}.pdf"))
    out = op_pdf_merge(None, f"merge {d}\\a.pdf and {d}\\b.pdf", {})
    assert "2 pages" in out, out
    import pandas as pd
    x = os.path.join(d, "s.xlsx")
    pd.DataFrame({"name": [" A ", "B", "B", None], "sales": [1, 2, 2, None]}).to_excel(x, index=False)
    assert "dropped 1 duplicate" in op_excel_clean(None, f"clean {x}", {})
    print("[self-check] docs OK - routing, PDF merge, Excel clean")
