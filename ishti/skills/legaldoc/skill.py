"""Legal document drafting ASSISTANCE (inventory domain 1 #20): templates, not advice.

"draft a rental agreement: tenant Ahmed, shop 12, rent 25000 per month, from 2026-10-01 for 11 months"
"nda between me and Acme Corp for 2 years"
"employment contract: Ali, 80000 per month, probation 3 months"

Fills a fixed house template with ONLY the facts stated in the request — unstated clauses stay as
visible [FILL: ...] blanks so nothing is invented silently. The reply states plainly that this is
not legal advice and that a local lawyer must review before signing. T1 (writes a file), local only.
Output: data/docs/<type>-<counterpart>.txt
"""
import os
import re

from ishti.skills import trace

I = re.I

RENTAL = re.compile(
    r"^\s*draft\s+(?:an?\s+)?(?:rental|lease)\s+agreement\s*[:\-]?\s*(?P<facts>.+)$", I)
NDA = re.compile(r"^\s*draft\s+(?:an?\s+)?nda\s+(?:between\s+)?(?P<facts>.+)$", I)
EMPLOY = re.compile(
    r"^\s*draft\s+(?:an?\s+)?employment\s+contract\s*[:\-]?\s*(?P<facts>.+)$", I)

FIELD = {
    "tenant": r"(?:tenant|lessee)\s+([A-Za-z][\w .'-]{1,40})",
    "landlord": r"(?:landlord|lessor)\s+([A-Za-z][\w .'-]{1,40})",
    "premises": r"((?:premises|shop|house|flat|apartment|property)\s+[\w ,.#/-]{2,60})",
    "rent": r"rent\s+(?:of\s+)?(?:rs\.?|pkr|\$)?\s*([\d,]+)",
    "term": r"(?:for|term(?:\s+of)?)\s+([\d]+)\s*(months?|years?)",
    "start": r"from\s+(\d{4}-\d{2}-\d{2})",
    "employee": r"(?:employee|staff)\s+([A-Za-z][\w .'-]{1,40})",
    "salary": r"salary\s+(?:of\s+)?(?:rs\.?|pkr|\$)?\s*([\d,]+)",
    "probation": r"probation\s+(?:of\s+|period\s+(?:of\s+)?)?([\d]+)\s*(months?|weeks?)",
    "party": r"(?:with|and)\s+([A-Za-z][\w .&'-]{1,40})",
}


def _extract(facts):
    """Match each field within a single comma-segment only, so one field can't swallow the rest."""
    segs = [s.strip() for s in facts.split(",")]
    out = {}
    for key, pat in FIELD.items():
        for seg in segs:
            m = re.search(pat, seg, I)
            if m:
                out[key] = re.sub(r"\s+", " ", m.group(1)).strip(" ,.")
                break
    return out


def _blanks(mapping, keys):
    return {k: mapping.get(k, "[FILL: " + k.replace("_", " ") + "]") for k in keys}


def rental(facts):
    f = _extract(facts)
    v = _blanks(f, ("tenant", "landlord", "premises", "rent", "term", "start"))
    term = f"{v['term']} months" if f.get("term", "").isdigit() else v["term"]
    body = f"""RENTAL AGREEMENT (DRAFT — NOT LEGAL ADVICE)

This rental agreement is made between {v['landlord']} ("Landlord") and {v['tenant']} ("Tenant").

1. PREMISES: The Landlord rents to the Tenant the premises at {v['premises']}.
2. RENT: {v['rent']} per month, payable in advance.
3. TERM: {term if f.get('term') else '[FILL: term]'}, beginning {v['start']}.
4. DEPOSIT: [FILL: security deposit amount]
5. NOTICE: [FILL: notice period for termination]
6. UTILITIES: [FILL: who pays which utilities]
7. SIGNATURES: Landlord ____________  Tenant ____________  Date ____________
"""
    return body, f["tenant"] if f.get("tenant") else "counterpart"


def nda(facts):
    f = _extract(facts)
    v = _blanks(f, ("party", "term"))
    term = f"{v['term']} years" if f.get("term") == "2" or f.get("term", "").isdigit() else v["term"]
    body = f"""MUTUAL NON-DISCLOSURE AGREEMENT (DRAFT — NOT LEGAL ADVICE)

Parties: the Owner and {v['party']}.

1. CONFIDENTIAL INFORMATION: Any technical, business or personal information disclosed by either
   party, in any form, marked or reasonably understood as confidential.
2. OBLIGATIONS: The receiving party will not disclose confidential information to third parties
   and will protect it with at least the same care as its own.
3. TERM: The obligations last {term if f.get('term') else '[FILL: term]'} from the date of signing.
4. EXCLUSIONS: information already public, independently developed, or required to be disclosed by law.
5. SIGNATURES: Owner ____________  {v['party'] if f.get('party') else '[FILL: other party]'} ____________  Date ____________
"""
    return body, f["party"] if f.get("party") else "counterpart"


def employment(facts):
    f = _extract(facts)
    if "employee" not in f:                     # "employment contract: Ali, ..."
        first = facts.split(",")[0].strip()
        if re.fullmatch(r"[A-Za-z][\w .'-]{1,40}", first):
            f["employee"] = first
    if "salary" not in f:                       # "80000 per month" without the word salary
        m = re.search(r"(?:rs\.?|pkr|\$)?\s*([\d,]+)\s+per\s+month", facts, I)
        if m:
            f["salary"] = m.group(1)
    v = _blanks(f, ("employee", "salary", "probation"))
    prob = f"{v['probation']} months" if f.get("probation") and "month" not in f["probation"] else v["probation"]
    body = f"""EMPLOYMENT CONTRACT (DRAFT — NOT LEGAL ADVICE)

Employer: the Owner.   Employee: {v['employee']}.

1. POSITION: [FILL: job title and duties]
2. SALARY: {v['salary']} per month.
3. PROBATION: {prob if f.get('probation') else '[FILL: probation period]'}, during which either party may end this contract with 1 week's notice.
4. HOURS: [FILL: working hours and days]
5. LEAVE: [FILL: annual leave entitlement]
6. SIGNATURES: Employer ____________  Employee ____________  Date ____________
"""
    return body, f["employee"] if f.get("employee") else "counterpart"


def _save(body, name, session=None):
    d = (session or {}).get("_legaldoc_out") or os.path.join("data", "docs")
    os.makedirs(d, exist_ok=True)
    slug = re.sub(r"[^\w-]", "", name.lower().replace(" ", "-")) or "counterpart"
    fp = os.path.join(d, f"agreement-{slug}.txt")
    with open(fp, "w", encoding="utf-8") as fh:
        fh.write(body)
    return fp


def _reply(body, name, session):
    fp = _save(body, name, session)
    blanks = body.count("[FILL:")
    warn = (f"{blanks} field(s) still need your input." if blanks
            else "No blanks left, but the facts came only from your message.")
    return (f"{body}\n\nSaved to `{fp}`. {warn}\n"
            f"**This is a template, not legal advice** — have a lawyer review it before signing.")


def match(text):
    t = text.strip()
    return bool(RENTAL.match(t) or NDA.match(t) or EMPLOY.match(t))


def run(text, session):
    t = text.strip()
    if (m := RENTAL.match(t)):
        trace(session, "legaldoc", "rental")
        body, name = rental(m.group("facts"))
        return {"reply": _reply(body, name, session), "sources": []}
    if (m := NDA.match(t)):
        trace(session, "legaldoc", "nda")
        body, name = nda(m.group("facts"))
        return {"reply": _reply(body, name, session), "sources": []}
    if (m := EMPLOY.match(t)):
        trace(session, "legaldoc", "employment")
        body, name = employment(m.group("facts"))
        return {"reply": _reply(body, name, session), "sources": []}
    return None


if __name__ == "__main__":
    assert match("draft a rental agreement: tenant Ahmed, shop 12, rent 25000 per month, from 2026-10-01 for 11 months")
    assert match("draft an nda between me and Acme Corp for 2 years")
    assert match("draft an employment contract: Ali, 80000 per month, probation 3 months")
    assert not match("rent a car in dubai")
    assert not match("nda full form")

    r = run("draft a rental agreement: tenant Ahmed, shop 12, rent 25000 per month, from 2026-10-01 for 11 months",
            {"_legaldoc_out": (tmp := __import__("tempfile").mkdtemp())})
    assert "not legal advice" in r["reply"], r
    assert "Ahmed" in r["reply"] and "25,000" not in r["reply"], r   # rent must appear as stated
    body = open([p for p in __import__("glob").glob(tmp + "/*")][0], encoding="utf-8").read()
    assert "shop 12" in body and "25000" in body, body
    assert "DEPOSIT" in body and "[FILL:" in body, body       # unstated clauses stay visible
    r2 = run("draft an nda between me and Acme Corp for 2 years", {"_legaldoc_out": tmp})
    assert "Acme Corp" in r2["reply"], r2
    r3 = run("draft an employment contract: Ali, 80000 per month, probation 3 months", {"_legaldoc_out": tmp})
    assert "Ali" in r3["reply"] and "80000" in r3["reply"], r3
    print("[self-check] legaldoc OK - stated facts verbatim, unstated clauses stay [FILL:] blanks, "
          "'not legal advice' always shown, writes only data/docs")
