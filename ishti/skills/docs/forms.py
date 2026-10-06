"""PDF form filling and invoices.

- fill: a PDF's fillable fields (pypdf AcroForm) from "with name: X, date: Y" and/or "with my details" (the owner's
  facts, matched to field names); unmatched fields are listed, a flat/scanned PDF is said to have none
- invoice: "make an invoice for Ali: logo design 15000, hosting 3000" -> numbered Word invoice (owner's name, city,
  currency) converted to PDF by Word, both in data/invoices"""
import datetime as dt
import difflib
import os
import re

from ishti import ROOT
from ishti.test_mode import scratch_path

I = re.I
INVOICES = scratch_path(os.path.join(ROOT, "data", "invoices"))
# owner fact -> field-name words it fills
FACT_FIELDS = {"name": ("name", "full name", "applicant", "student name"), "email": ("email", "e-mail"),
               "phone": ("phone", "mobile", "contact", "cell"), "city": ("city", "town"), "country": ("country", "nationality"),
               "address": ("address", "residence"), "university": ("university", "institute", "institution", "school")}


def fields_of(path):
    """{field name: current value} of a fillable PDF ({} for a flat one)."""
    from pypdf import PdfReader
    return {k: (v.get("/V") or "") for k, v in (PdfReader(path).get_fields() or {}).items() if v.get("/FT") == "/Tx"}


def wanted_values(text, fields, facts):
    """Field -> value from the request's "field: value" pairs and (if asked) the owner's facts."""
    names = list(fields)
    by_low = {n.lower(): n for n in names}
    out = {}
    for key, value in re.findall(r"([\w .-]{2,30}?)\s*[:=]\s*([^,;\n]+)", re.sub(r"^.*?\bwith\b", "", text, flags=I)):
        hit = difflib.get_close_matches(key.strip().lower(), by_low, n=1, cutoff=0.6)
        if hit:
            out[by_low[hit[0]]] = value.strip()
    if re.search(r"\b(?:my|owner'?s?)\s+(?:details|info|information|data)\b", text, I):
        for fact, words in FACT_FIELDS.items():
            if not facts.get(fact):
                continue
            for name in names:
                if name not in out and any(w in name.lower().replace("_", " ") for w in words):
                    out[name] = facts[fact]
        for name in names:  # today's date where a date is asked
            if name not in out and re.search(r"\bdate\b", name, I) and not re.search(r"birth|dob", name, I):
                out[name] = dt.date.today().strftime("%d %B %Y")
    return out


def fill(path, values, dest=None):
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(path)
    writer = PdfWriter(clone_from=reader)
    for page in writer.pages:
        writer.update_page_form_field_values(page, values, auto_regenerate=False)
    writer.set_need_appearances_writer(True)
    dest = dest or os.path.splitext(path)[0] + "_filled.pdf"
    with open(dest, "wb") as f:
        writer.write(f)
    return dest


def op_pdf_form(m, text, session, paths):
    from ishti.facts import get_facts
    got = paths(text, (".pdf",))
    if not got:
        return "Which PDF form? Give me its full path."
    fields = fields_of(got[0])
    if not fields:
        return (f"{os.path.basename(got[0])} has no fillable fields (it's a flat or scanned form). I can read it, but "
                "filling it means typing on top of the page - not built yet.")
    if not re.search(r"\b(?:fill|complete)\b", text, I):
        return f"{os.path.basename(got[0])} has {len(fields)} fields: " + ", ".join(fields)
    values = wanted_values(text, fields, get_facts().get("owner"))
    if not values:
        return (f"Tell me what to put in: e.g. \"fill {got[0]} with {', '.join(f'{n}: ...' for n in list(fields)[:3])}\" "
                "or \"... with my details\". Fields: " + ", ".join(fields))
    out = fill(got[0], values)
    empty = [n for n in fields if n not in values]
    return (f"Filled {len(values)} field(s) -> {out}\n" + "\n".join(f"- {k}: {v}" for k, v in values.items())
            + (f"\nStill empty: {', '.join(empty)}" if empty else ""))


# ---------------------------------------------------------------- invoices
AMOUNT = re.compile(r"(?:\b(?:rs\.?|pkr|usd)\s*|\$)?([\d,]*\.?\d+)\s*(k)?\s*(?:rs|pkr|rupees|usd|dollars)?\s*$", I)


def parse_invoice(text):
    """(client, [(description, amount)], due_days)."""
    client = re.search(r"\binvoice\s+(?:for|to)\s+(?P<c>[^:,\n]+?)\s*(?::|,|\bfor\b)", text, I)
    body = text[client.end():] if client else text.split(":", 1)[-1]
    due = re.search(r"\bdue\s+in\s+(\d{1,3})\s+days?\b", body, I)
    if due:
        body = body[:due.start()] + body[due.end():]
    items = []
    for chunk in re.split(r",(?!\d{3})|;|\band\b|\n", body):  # "3,000" is a number, not two items
        amount = AMOUNT.search(chunk.strip())
        desc = chunk.strip()[:amount.start()].strip(" -:=@") if amount else ""
        if amount and desc:
            items.append((desc, float(amount.group(1).replace(",", "")) * (1000 if amount.group(2) else 1)))
    return (client["c"].strip() if client else "Client"), items, int(due.group(1)) if due else 14


def next_number():
    from ishti.facts import get_facts
    f = get_facts()
    year = dt.date.today().year
    last = f.get("invoices").get(f"last_{year}")
    n = int(last) + 1 if last else 1
    f.set("invoices", f"last_{year}", str(n))
    return f"INV-{year}-{n:04d}"


def make_invoice(client, items, due_days, owner, currency, number=None, folder=INVOICES):
    import docx
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt
    os.makedirs(folder, exist_ok=True)
    number = number or next_number()
    today = dt.date.today()
    d = docx.Document()
    d.add_heading("INVOICE", 0)
    d.add_paragraph(f"{owner.get('name', '')}\n{', '.join(x for x in (owner.get('city'), owner.get('country')) if x)}")
    meta = d.add_paragraph(f"Invoice no: {number}\nDate: {today:%d %B %Y}\nDue: {today + dt.timedelta(days=due_days):%d %B %Y}")
    meta.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    d.add_paragraph(f"Bill to: {client}").runs[0].bold = True
    table = d.add_table(rows=1, cols=3)
    table.style = "Light Grid Accent 1"
    for cell, head in zip(table.rows[0].cells, ("#", "Description", f"Amount ({currency})")):
        cell.text = head
    for i, (desc, amount) in enumerate(items, 1):
        row = table.add_row().cells
        row[0].text, row[1].text, row[2].text = str(i), desc, f"{amount:,.2f}"
    total = sum(a for _, a in items)
    row = table.add_row().cells
    row[1].text, row[2].text = "Total", f"{total:,.2f}"
    for cell in row:
        for r in cell.paragraphs[0].runs:
            r.bold, r.font.size = True, Pt(12)
    d.add_paragraph(f"\nPlease pay {currency} {total:,.2f} by {today + dt.timedelta(days=due_days):%d %B %Y}. Thank you!")
    slug = re.sub(r"[^\w]+", "_", client).strip("_")
    path = os.path.join(folder, f"{number}_{slug}.docx")
    d.save(path)
    return path, number, total


def op_invoice(m, text, session, to_pdf):
    from ishti.facts import get_facts
    from ishti.skills.ledger.skill import currency
    client, items, due = parse_invoice(text)
    if not items:
        return "What's on it? e.g. \"make an invoice for Ali: logo design 15000, hosting 3000, due in 14 days\"."
    docx_path, number, total = make_invoice(client, items, due, get_facts().get("owner"), currency())
    try:
        pdf = to_pdf(docx_path)
    except Exception:
        pdf = None  # no Word: the .docx is the invoice
    return (f"Invoice {number} for {client}: {len(items)} item(s), total {currency()} {total:,.2f}, due in {due} days.\n"
            f"- {pdf or docx_path}" + (f"\n- {docx_path} (editable)" if pdf else ""))


if __name__ == "__main__":
    c, items, due = parse_invoice("make an invoice for Ali Khan: logo design 15000, website hosting 3,000, 2 banners 2.5k, due in 7 days")
    assert c == "Ali Khan" and due == 7, (c, due)
    assert items == [("logo design", 15000.0), ("website hosting", 3000.0), ("2 banners", 2500.0)], items
    import tempfile
    from pypdf import PdfWriter
    from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject, TextStringObject
    folder = tempfile.mkdtemp()
    w = PdfWriter()
    page = w.add_blank_page(612, 792)
    widgets = []
    for i, name in enumerate(("Full Name", "City", "Date", "Course Code")):
        field = DictionaryObject({NameObject("/FT"): NameObject("/Tx"), NameObject("/T"): TextStringObject(name),
                                  NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"),
                                  NameObject("/Rect"): ArrayObject([FloatObject(x) for x in (72, 700 - 40 * i, 300, 720 - 40 * i)]),
                                  NameObject("/F"): NumberObject(4), NameObject("/V"): TextStringObject("")})
        widgets.append(w._add_object(field))
    page[NameObject("/Annots")] = ArrayObject(widgets)
    w._root_object[NameObject("/AcroForm")] = DictionaryObject({NameObject("/Fields"): ArrayObject(widgets)})
    form = os.path.join(folder, "form.pdf")
    with open(form, "wb") as f:
        w.write(f)
    assert set(fields_of(form)) == {"Full Name", "City", "Date", "Course Code"}
    owner = {"name": "Ishtiaq Ahmed", "city": "Peshawar"}
    vals = wanted_values(f"fill {form} with my details, course code: AI-401", fields_of(form), owner)
    assert vals["Full Name"] == "Ishtiaq Ahmed" and vals["City"] == "Peshawar" and vals["Course Code"] == "AI-401" and vals["Date"], vals
    done = fields_of(fill(form, vals))
    assert done["Full Name"] == "Ishtiaq Ahmed" and done["Course Code"] == "AI-401", done
    path, number, total = make_invoice("Ali Khan", items, 7, owner, "PKR", number="INV-TEST-0001", folder=folder)
    import docx
    body = "\n".join(c.text for t in docx.Document(path).tables for r in t.rows for c in r.cells)
    assert total == 20500 and "20,500.00" in body and "logo design" in body
    from ishti.skills.docs.skill import docx_to_pdf
    pdf = docx_to_pdf(path)
    import pypdfium2 as pdfium
    text = pdfium.PdfDocument(pdf)[0].get_textpage().get_text_range()
    assert "INV-TEST-0001" in text and "20,500.00" in text, text[:300]
    print("[self-check] forms OK - invoice parsing, fillable PDF read/fill (my details + explicit), invoice docx + Word PDF")
