"""Full-page scroll, extraction and filing (C4).

"extract the product names and prices from <url> into a spreadsheet", "collect every job title and company on
<url>, save it in my notes". Steps (each reported live, so a long run is visibly alive):
  1. open the page in the owner's main Chrome (Playwright MCP)
  2. scroll until the page stops growing (infinite-scroll pages load more as you go; max 15 scrolls), reading the
     DOM text each time - fast and exact; if a page is mostly visual (little text) a screenshot goes to the local
     vision model instead
  3. the model turns the text into JSON rows with exactly the requested fields, chunk by chunk (a chunk that
     comes back malformed is retried once), rows de-duplicated
  4. filed durably: an .xlsx in Documents/ISHTI Extracts (+ JSON), a note, or a memory table - if the request
     doesn't say where, ISHTI asks and the answer continues the same job
Every browser step is retried once on failure (per-step error rates compound on long flows)."""
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
MAX_SCROLLS = 15


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


# ---------------------------------------------------------------- browser steps (retried once)
def _step(name, args):
    from ishti.skills.browser import skill as browser
    for attempt in (1, 2):
        try:
            out = browser._call(name, args)
            if not out.startswith("ERROR") or attempt == 2:
                return out
        except Exception:
            if attempt == 2:
                raise
        time.sleep(1.0)


def _js(expr):
    out = _step("browser_evaluate", {"function": f"() => {{ {expr} }}"})
    m = re.search(r"### Result\s*\n(.+?)(?:\n###|\Z)", out, re.S)
    raw = (m.group(1) if m else out).strip()
    try:
        return json.loads(raw)
    except ValueError:
        return raw.strip('"')


def read_all(url, session):
    """[text chunks] of the whole page after scrolling it to the end."""
    _step("browser_navigate", {"url": url if url.startswith("http") else "https://" + url})
    time.sleep(2)
    seen, chunks, last_height, still = set(), [], 0, 0
    for n in range(MAX_SCROLLS + 1):
        text = _js("return document.body.innerText") or ""
        new = [p for p in re.split(r"\n{2,}|\n(?=\S)", str(text)) if p.strip() and p.strip() not in seen]
        seen.update(p.strip() for p in new)
        if new:
            chunks.append("\n".join(new))
        height = _js("window.scrollBy(0, window.innerHeight * 0.9); return document.body.scrollHeight") or 0
        trace(session, "scroll", f"page part {n + 1}: {len(seen)} text blocks so far", status="done")
        still = still + 1 if (not new and height == last_height) else 0
        if still >= 2:  # nothing new twice in a row: the end of the page (or of what it will load)
            break
        last_height = height
        time.sleep(1.2)  # infinite scroll loads the next batch
    return chunks


def looked_at(session):
    """Screenshot + local vision model, for pages whose content isn't in the DOM text (charts, canvas)."""
    from ishti.skills.browser.skill import screenshot
    path = screenshot(f"extract_{int(time.time())}.png")
    if not path:
        return ""
    from ishti.skills.video.pro import look
    trace(session, "vision", "the page is mostly visual - reading a screenshot", status="done")
    return look(path, "List every item visible on this page with its details, one per line.").get("text", "")


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
    url = m.group(0).rstrip(".,)")
    fields = fields_of(text)
    trace(session, "extract", f"fields: {', '.join(fields)}", status="done")
    from ishti.skills.browser import skill as browser
    with browser.LOCK:
        try:
            browser.use_server(browser.profile_for("extract"), session)
        except RuntimeError as exc:
            return {"reply": str(exc), "sources": []}
        chunks = read_all(url, session)
        if sum(len(c) for c in chunks) < 300:
            seen = looked_at(session)
            chunks = [seen] if seen else chunks
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
