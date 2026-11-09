"""Page extraction and filing (C4).

"extract the product names and prices from <url> into a spreadsheet", "collect every job title and company on
<url>, save it in my notes". Steps (each reported live, so a long run is visibly alive):
  1. read the public page over plain HTTP (SSRF-guarded: public hosts only, redirects re-checked) and keep its
     visible text with list/table structure; no headless browser, so pages that only render with JavaScript or
     behind a login are reported as unreadable instead of being scraped
  2. the model turns the text into JSON rows with exactly the requested fields, chunk by chunk (a chunk that
     comes back malformed is retried once), rows de-duplicated
  3. filed durably: an .xlsx in Documents/ISHTI Extracts (+ JSON), a note, or a memory table - if the request
     doesn't say where, ISHTI asks and the answer continues the same job"""
import datetime as dt
import json
import os
import re
import time

from ishti import ROOT, llm
from ishti.skills import trace
from ishti.untrusted import guard

I = re.I
URL = re.compile(r"https?://\S+|(?<![@\w.])(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|org|net|edu|pk|io|ai|co|info|dev|app)(?:/\S*)?", I)
TRIGGER = re.compile(r"\b(?:extract|scrape|collect|pull|harvest|gather|list\s+all|get\s+(?:me\s+)?(?:all|every)|export)\b.+?"
                     r"\b(?:from|on|off|at)\b", I)
DEST = {"spreadsheet": re.compile(r"\b(?:spreadsheet|excel|xlsx|sheet|csv|table)\b", I),
        "note": re.compile(r"\b(?:note|notes|notebook)\b", I),
        "memory": re.compile(r"\b(?:memory|remember|save\s+it\s+in\s+you)\b", I)}
OUT = os.path.join(os.path.expanduser("~"), "Documents", "ISHTI Extracts")



def match(text):
    return bool(TRIGGER.search(text) and URL.search(text))


def destination(text):
    return next((d for d, rx in DEST.items() if rx.search(text)), None)


def fields_of(text):
    """The columns asked for ("the product names and prices" -> ["product name", "price"])."""
    raw = llm.ask(f"Request: {text}\nWhich fields should be extracted for each item? Return JSON {{\"fields\": [\"...\"]}} "
                  "with 1-8 short lowercase field names in the order asked (e.g. [\"product name\", \"price\"]).",
                  system=None, temperature=0, max_tokens=80, response_format={"type": "json_object"})
    try:
        fields = [str(f).strip().lower() for f in json.loads(raw).get("fields", []) if str(f).strip()]
    except (ValueError, AttributeError):
        fields = []
    return fields[:8] or ["item", "details"]


# ---------------------------------------------------------------- page reading
CHUNK = 6000


def read_all(url, session):
    """[text chunks] of a public page: visible text, one line per block, scripts/styles/navigation dropped."""
    from bs4 import BeautifulSoup
    from tools.web.fetch import safe_get
    r = safe_get(url if url.startswith("http") else "https://" + url, timeout=20)
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "form"]):
        tag.decompose()
    lines = [" ".join(t.split()) for t in soup.get_text("\n").splitlines()]
    text = "\n".join(line for line in lines if line)
    trace(session, "read", f"read {len(text)} characters of page text", status="done")
    return [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)]


def rows_from(chunks, fields, text, session):
    rows, keys = [], set()
    for i, chunk in enumerate(chunks, 1):
        for attempt in (1, 2):
            raw = llm.ask(f"The owner asked: {text}\nExtract every item from this page text. Return JSON "
                          f"{{\"rows\": [{{{', '.join(json.dumps(f) + ': ...' for f in fields)}}}]}} - exactly these fields, "
                          "values copied from the page (empty string if missing), no invented items.\n\n"
                          + guard(chunk[:7000], "the page", session),
                          system=None, temperature=0, max_tokens=2000, response_format={"type": "json_object"})
            try:
                got = [r for r in json.loads(raw).get("rows", []) if isinstance(r, dict)]
                break
            except (ValueError, AttributeError):
                got = []
                if attempt == 1:
                    trace(session, "extract", f"part {i}: malformed answer - retrying", status="error")
        for r in got:
            row = {f: str(r.get(f, "")).strip() for f in fields}
            key = json.dumps(row, sort_keys=True).lower()
            if any(row.values()) and key not in keys:
                keys.add(key)
                rows.append(row)
        trace(session, "extract", f"part {i}/{len(chunks)}: {len(rows)} items so far", status="done")
    return rows


def file_rows(rows, fields, where, url, text):
    """Save the rows durably; returns a description of where."""
    site = re.sub(r"^www\.", "", re.sub(r"^https?://", "", url)).split("/")[0]
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    if where == "note":
        from ishti.skills.docs.skill import op_note_add
        body = f"Extract from {site} ({stamp}):\n" + "\n".join(" | ".join(r[f] for f in fields) for r in rows)
        op_note_add(re.match(r"(?P<body>.+)", body, re.S), body, {})
        return "your notes"
    if where == "memory":
        from ishti.facts import get_facts
        f = get_facts()
        with f.lock, f.db:
            f.db.execute("CREATE TABLE IF NOT EXISTS extracts(ts REAL, site TEXT, request TEXT, rows TEXT)")
            f.db.execute("INSERT INTO extracts VALUES(?,?,?,?)", (time.time(), site, text[:200], json.dumps(rows)))
        return "my memory (extracts table)"
    from openpyxl import Workbook
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"{site}_{stamp}.xlsx")
    wb = Workbook()
    ws = wb.active
    ws.title = site[:30]
    ws.append([f.title() for f in fields])
    for r in rows:
        ws.append([r[f] for f in fields])
    wb.save(path)
    with open(path.replace(".xlsx", ".json"), "w", encoding="utf-8") as fh:
        json.dump({"source": url, "request": text, "rows": rows}, fh, ensure_ascii=False, indent=1)
    return path


def run(text, session):
    p = session.get("pending")
    if p and p.get("skill") == "extract" and p.get("job"):  # the answer to "where should I save them?"
        job = p["job"]
        where = destination(text)
        if not where:
            return {"reply": "Spreadsheet, note or memory?", "sources": []}
        session["pending"] = None
        saved = file_rows(job["rows"], job["fields"], where, job["url"], job["text"])
        return {"reply": f"Saved {len(job['rows'])} items to {saved}.", "sources": []}
    m = URL.search(text)
    if not m:
        return None
    url = m.group(0).rstrip(".,)")
    fields = fields_of(text)
    trace(session, "extract", f"fields: {', '.join(fields)}", status="done")
    try:
        chunks = read_all(url, session)
    except Exception as exc:
        return {"reply": f"I couldn't read {url}: {exc}", "sources": [], "no_escalate": True}
    if not chunks:
        return {"reply": f"I couldn't read anything on {url}.", "sources": []}
    rows = rows_from(chunks, fields, text, session)
    if not rows:
        return {"reply": f"I read the page but found no items with {', '.join(fields)}.", "sources": [{"id": 1, "url": url, "title": url}]}
    from ishti.skills import md_table
    preview = md_table([f.title() for f in fields], [[r[f][:60] for f in fields] for r in rows[:8]])
    where = destination(text)
    if not where:  # C4: file it durably - but where is the owner's call
        session["pending"] = {"skill": "extract", "job": {"rows": rows, "fields": fields, "url": url, "text": text}}
        return {"reply": f"Found {len(rows)} items:\n\n{preview}\n\nWhere should I save them - a spreadsheet, a note, or my memory?",
                "sources": [{"id": 1, "url": url, "title": url}]}
    saved = file_rows(rows, fields, where, url, text)
    return {"reply": f"Found {len(rows)} items and saved them to {saved}.\n\n{preview}", "sources": [{"id": 1, "url": url, "title": url}]}


if __name__ == "__main__":
    import tempfile
    assert match("extract the product names and prices from https://priceoye.pk/mobiles into a spreadsheet")
    assert match("scrape all job titles and companies from rozee.pk")
    assert not match("extract D:/x.zip") and not match("what's on example.com")
    assert destination("... into a spreadsheet") == "spreadsheet" and destination("save it in my notes") == "note"
    assert destination("extract names from x.com") is None
    OUT = tempfile.mkdtemp()
    rows = [{"product name": "Galaxy A55", "price": "Rs 94,999"}, {"product name": "Redmi 14", "price": "Rs 38,999"}]
    path = file_rows(rows, ["product name", "price"], "spreadsheet", "https://priceoye.pk/mobiles", "t")
    from openpyxl import load_workbook
    ws = load_workbook(path).active
    assert [c.value for c in ws[1]] == ["Product Name", "Price"] and ws["A3"].value == "Redmi 14" and os.path.isfile(path.replace(".xlsx", ".json"))
    print("[self-check] extract OK - routing, destinations, spreadsheet + JSON filing")
