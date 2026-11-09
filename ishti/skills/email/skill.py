"""Gmail through the Gmail REST API (ishti/gmail_api.py, OAuth connector): unread mail, open and summarise one,
search, order tracking, invoices, and write / send / forward. Every send shows the full draft first and goes out only
after the owner's yes. Nothing is scraped from a browser, and nothing is ever reported that the API did not return."""
import re

from ishti import llm
from ishti.untrusted import guard
from ishti.skills import ask_first, trace

I = re.I
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
OPS = [
    ("send", re.compile(r"\b(?:send|write|compose|draft)\s+(?:an?\s+|this\s+|the\s+|that\s+|my\s+)?(?:e-?mail|mail)\b|\be-?mail\s+(?!me\b)(?P<to>[\w.+-]+@[\w.-]+|\w+)\s+(?:saying|about|that)\b", I)),
    ("open", re.compile(r"\b(?:read|open|show)\s+(?:me\s+)?(?:the\s+)?(?:latest\s+|last\s+|first\s+)?e-?mail\s+from\s+(?P<who>[\w .@-]{2,40})", I)),
    ("search", re.compile(r"\bsearch\s+(?:my\s+)?(?:e-?mails?|mail|gmail|inbox)\s+for\s+(?P<q>.+)$|\b(?:e-?mails?|mail)\s+from\s+(?P<who>[\w .@-]{2,40}?)\s*$", I)),
    ("orders", re.compile(r"\b(?:where\s+(?:is|are)|track|status\s+of|check)\s+(?:my\s+)?(?:online\s+)?(?:orders?|packages?|parcels?|"
                          r"deliver(?:y|ies)|shipments?)\b|\bmy\s+(?:orders|deliveries|packages|parcels)\b|\border\s+(?:status|tracking)\b", I)),
    ("forward", re.compile(r"\bforward\b.*\be-?mails?\b|\bforward\s+(?:the|this|my)\b", I)),
    # "message Ahmed" (no "email" in the request) is a WhatsApp/chat task, not email (checklist #77).
    ("write", re.compile(r"^\s*(?:please\s+)?(?:email|e-mail|mail|write\s+to|reply\s+to)\s+"
                         r"(?P<who>[A-Za-z][\w'-]*(?:\s+[A-Za-z][\w'-]*)?)\b.*"
                         r"|^\s*(?:please\s+)?reply\s+(?:saying|that|with)\b", re.I)),
    ("inbox", re.compile(r"\b(?:check|any|new|unread|read|show|summari[sz]e|what(?:'s| is)\s+in)\s+(?:my\s+)?(?:new\s+|unread\s+)?(?:e-?mails?|mail|gmail|inbox)\b|\bdo\s+i\s+have\s+(?:any\s+)?(?:new\s+)?(?:e-?mails?|mail)\b", I)),
    ("invoices", re.compile(r"\b(?:pull|find|get|check|fetch|search|read)\s+(?:my\s+)?(?:open\s+)?invoices?\b", I)),
    ("email_self", re.compile(r"\be-?mail\s+(?:myself|me)\b", I)),
]

DISCONNECT = re.compile(r"^\s*(?:disconnect|revoke|unlink|remove|forget)\s+(?:my\s+)?"
                        r"(?:gmail|google|e-?mail|mail|oauth)\b.*$", I)
CONNECT = re.compile(r"^\s*(?:connect|authori[sz]e|link|set\s+up)\s+(?P<svc>gmail|e-?mail|google)\b"
                     r"(?:.*?(?:client[\s_-]?id\s*[:=]?\s*(?P<cid>[\w.-]+))?)?"
                     r"(?:.*?(?:client[\s_-]?secret\s*[:=]?\s*(?P<sec>[\w.-]+))?)?\s*$", I | re.S)
# "Find my invoice PDFs" / "locate the invoice files on my pc": a local-file search the files/folders
# skills own, not a mail-inbox question (checklist #73).
LOCAL_FILE = re.compile(r"\b(?:pdfs?|files?|folders?|documents?|downloads|desktop|on\s+my\s+(?:pc|computer|laptop)|local)\b", I)
NOT_CONNECTED = ("Gmail isn't connected yet. Say \"connect gmail\" to link your Google account "
                 "(one-time setup: docs/OAUTH_SETUP.md). Until then I can't read or send email for you.")

ORDER_QUERY = ('(order OR shipped OR dispatched OR "out for delivery" OR delivered OR tracking OR "on its way" OR courier) '
               'newer_than:45d')
INVOICE_QUERY = '(invoice OR "amount due" OR "payment due" OR bill) newer_than:90d'
STATUS = [("delivered", r"\bdelivered\b"), ("out for delivery", r"out for delivery"), ("shipped", r"\b(?:shipped|dispatched|on its way|"
          r"in transit|handed (?:over )?to (?:the )?courier)\b"), ("confirmed", r"\b(?:confirmed|received your order|order placed|thank you for your order)\b"),
          ("cancelled", r"\b(?:cancel+ed|refund)")]


def match(text):
    """Any op that claims the request wins. 'send' may decline ("draft an email to my professor" is writing help),
    but must not hide a request a later op owns ('Email Faisal about the deployment timeline', checklist 143)."""
    if re.search(r"\b(?:outlook|instagram|facebook|linkedin|whatsapp|twitter|x\.com)\b", text, I):
        return False
    if CONNECT.match(text.strip()) or DISCONNECT.match(text.strip()):
        return True
    for name, rx in OPS:
        if not rx.search(text):
            continue
        if name == "invoices" and LOCAL_FILE.search(text):
            continue
        if name == "send" and not (EMAIL.search(text) or re.search(r"\bsend\b", text, I)):
            continue
        return True
    return False


def _connection(text, session):
    """connect / disconnect commands -> a reply dict; None for everything else."""
    from ishti import oauth
    t = text.strip()
    if m := CONNECT.match(t):
        if m["cid"] and m["sec"]:
            reply = oauth.set_credentials("gmail", m["cid"], m["sec"])
            return {"reply": reply + " Say 'connect gmail' to authorise it in the browser.", "sources": []}
        if oauth.config("gmail")[0] and not oauth.connected("gmail"):
            from ishti import connectors
            return {"reply": connectors.start("gmail")["reply"], "sources": []}
        return {"reply": oauth.status("gmail"), "sources": []}
    if DISCONNECT.match(t):
        return {"reply": oauth.disconnect("gmail"), "sources": []}
    return None


def run(text, session):
    out = _connection(text, session)
    if out is not None:
        return out
    from ishti import oauth
    for name, rx in OPS:
        if m := rx.search(text):
            if name == "send" and not EMAIL.search(text) and not re.search(r"\bsend\b", text, I):
                continue  # not an outright send - another op (like 'write') may own it
            if not oauth.connected("gmail"):
                return {"reply": NOT_CONNECTED, "sources": [], "no_escalate": True}
            trace(session, "gmail", name)
            try:
                out = globals()["op_" + name](m, text, session)
            except Exception as exc:  # the API failed: say so, never invent a result
                trace(session, "gmail", f"{type(exc).__name__}", status="error")
                return {"reply": f"Gmail didn't answer ({type(exc).__name__}). Nothing was read or sent - try again shortly.",
                        "sources": [], "no_escalate": True}
            return out if isinstance(out, dict) else {"reply": out, "sources": []}
    return None


def _api():
    import ishti.gmail_api as api
    return api


def _sender(row):
    return row["from"].split("<")[0].strip().strip('"') or row["from"]


def op_inbox(m, text, session):
    rows = _api().unread(25)
    trace(session, "gmail", f"{len(rows)} unread", status="done")
    if not rows:
        return "No unread email in the inbox."
    if re.search(r"\bsummari[sz]e\b", text, I):
        listing = "\n".join(f"- {_sender(r)}: {r['subject']} - {r['snippet'][:160]}" for r in rows[:15])
        return llm.ask("Summarise the owner's unread email in a few bullets: what needs attention first, what can wait.\n"
                       + guard(listing, "the owner's unread email list", session), max_tokens=400)
    return (f"{len(rows)} unread email(s):\n" + "\n".join(f"- **{_sender(r)}**: {r['subject'][:80]}" for r in rows[:15])
            + ("\n..." if len(rows) > 15 else "") + "\nSay \"read the email from <name>\" to open one.")


def op_open(m, text, session):
    who = m.group("who").strip().strip("?.!")
    api = _api()
    hits = api.search(f"from:{who}", 5) or api.search(who, 5)
    if not hits:
        return f"No email from '{who}'. Try \"search my email for {who}\"."
    full = api.read(hits[0]["id"])
    body = guard(full["body"][:6000], f"an email from {full['from']}", session)
    summary = llm.ask(f"Email from {full['from']}, subject \"{full['subject']}\":\n{body}\n\nTell the owner what this email says "
                      "in 2-4 sentences, and whether it needs a reply or action.", max_tokens=300)
    return f"**{full['subject']}** (from {full['from']}, {full['date']})\n\n{summary}"


def op_search(m, text, session):
    q = (m.group("q") or f"from:{m.group('who')}").strip()
    rows = _api().search(q, 15)
    if not rows:
        return f"No email matches '{q}'."
    return f"Email matching '{q}':\n" + "\n".join(f"- {_sender(r)}: {r['subject'][:80]} ({r['date'][:16]})" for r in rows)


def order_status(subject):
    return next((st for st, rx in STATUS if re.search(rx, subject, I)), "update")


def op_orders(m, text, session):
    """The latest order/shipping email per store with its status (read-only)."""
    from ishti.skills import md_table
    rows = _api().search(ORDER_QUERY, 25)
    if not rows:
        return "No order or delivery emails in the last 45 days."
    latest = {}
    for r in rows:  # newest first: the first one per store is its latest news
        latest.setdefault(_sender(r), r)
    table = md_table(("Store", "Status", "Latest email", "Date"),
                     [(s, order_status(r["subject"]), r["subject"][:70], r["date"][:16]) for s, r in list(latest.items())[:12]])
    open_ones = sum(order_status(r["subject"]) in ("shipped", "out for delivery", "confirmed") for r in latest.values())
    return f"{len(latest)} store(s) with order emails in the last 45 days - {open_ones} still on the way:\n\n{table}"


def op_invoices(m, text, session):
    """Invoice-like emails from the last 90 days, as found - amounts are only quoted when the email itself says them."""
    rows = _api().search(INVOICE_QUERY, 15)
    if not rows:
        return "No invoice or payment-due emails in the last 90 days."
    return (f"{len(rows)} invoice/payment email(s) in the last 90 days:\n"
            + "\n".join(f"- **{_sender(r)}**: {r['subject'][:80]} ({r['date'][:16]})" for r in rows)
            + "\nSay \"read the email from <name>\" for the details of one.")


def _draft(text, to):
    raw = llm.ask(f"Write the email the owner asked for (to {to}): {text}\nReturn exactly:\nSubject: <subject>\n\n<body, polite, "
                  "concise, signed with the owner's name>", max_tokens=500)
    subj = (re.search(r"^Subject:\s*(.+)$", raw, re.M | I) or [None, "Hello"])[1].strip()
    body = raw.split("\n", 1)[1].strip() if raw.lower().startswith("subject:") else raw.strip()
    return subj, body


def _send_gate(session, to, subj, body, verb="Send"):
    return ask_first(session, "email", f"{verb} this email to {to}?\n\n**Subject:** {subj}\n\n{body}\n\n{verb} it",
                     lambda: _api().send(to, subj, body))


def op_send(m, text, session):
    to = EMAIL.search(text)
    if not to:
        return "Who should I send it to? Give me their email address (I only send to an address you give me)."
    subj, body = _draft(text, to.group(0))
    return _send_gate(session, to.group(0), subj, body)


def op_email_self(m, text, session):
    from ishti import oauth
    me = oauth.account("gmail")
    if not me or "@" not in me:
        return "I don't know your own Gmail address yet - reconnect Gmail (\"connect gmail\") and try again."
    subj, body = _draft(text, me)
    return _send_gate(session, me, subj, body)


def op_forward(m, text, session):
    """Forwarding is a send (T2), and the recipient must be a real address: "my accountant" is a role, not an address,
    so ISHTI asks instead of guessing one (checklist 121). The forwarded email is found by search, never assumed."""
    to = EMAIL.search(text)
    if not to:
        return {"reply": "Who should I forward it to? I don't have an email address for them on file - give me their "
                         "email address, never a guess.", "sources": []}
    what = re.search(r"forward\s+(?:the\s+|this\s+|my\s+)?(?P<what>.+?)(?:\s+(?:e-?mail\s+)?to\b|$)", text, I)
    label = re.sub(r"\b(?:e-?mails?|mail)\b", "", (what["what"] if what else "")).strip(" .?!,")[:120]
    api = _api()
    hits = api.search(label, 3) if label else []
    if not hits:
        return f"I couldn't find an email matching '{label or 'that'}' to forward. Say which one (sender or subject)."
    msg = api.read(hits[0]["id"])
    subj = f"Fwd: {msg['subject']}"
    body = (f"---------- Forwarded message ----------\nFrom: {msg['from']}\nDate: {msg['date']}\nSubject: {msg['subject']}\n\n"
            f"{msg['body'][:20000]}")
    preview = body if len(body) < 1500 else body[:1500] + "\n..."
    return ask_first(session, "email", f"Forward this to {to.group(0)}?\n\n**Subject:** {subj}\n\n{preview}\n\nSend it",
                     lambda: api.send(to.group(0), subj, body))


def _address_for(who):
    """An address ISHTI already has for this person - never invented."""
    from ishti.facts import get_facts
    rec = get_facts().get(who)
    for attr in ("email", "e-mail", "mail", "address", "contact"):
        if rec.get(attr) and "@" in str(rec[attr]):
            return str(rec[attr])
    return EMAIL.search(who).group(0) if EMAIL.search(who) else ""


def op_write(m, text, session):
    """'Email Faisal about the deployment timeline' / 'Reply saying I'll have it done by Friday'. A person -> the address
    ISHTI already has; none on file -> ask. Nothing is sent here: the draft goes through the same yes/no gate."""
    who = (m["who"] or "").strip() if "who" in (m.groupdict() or {}) else ""
    who = re.sub(r"\s+about\b.*$", "", who, flags=re.I).strip()
    to = EMAIL.search(text)
    to_addr = to.group(0) if to else (_address_for(who) if who else "")
    if not to_addr:
        return {"reply": f"I don't have an email address for {who or 'them'}. Tell me the address and I'll draft the email "
                         "- I won't guess one.", "sources": []}
    subj, body = _draft(text, to_addr)
    return _send_gate(session, to_addr, subj, body)


if __name__ == "__main__":
    cases = {"check my email": "inbox", "do I have any new emails": "inbox", "summarize my unread email": "inbox",
             "read the email from Ali": "open", "search my email for invoice": "search", "emails from github": "search",
             "send an email to ali@example.com about the meeting tomorrow": "send"}
    for t, want in cases.items():
        got = next((n for n, rx in OPS if rx.search(t)), None)
        assert got == want, (t, got, want)
    assert not match("what's the weather") and not match("remind me to email ali")
    assert next(n for n, rx in OPS if rx.search("where is my order")) == "orders" and next(n for n, rx in OPS if rx.search("track my packages")) == "orders"
    assert order_status("Your Daraz order has been shipped") == "shipped" and order_status("Delivered: your parcel") == "delivered"
    assert order_status("Thank you for your order #123") == "confirmed" and order_status("Weekly newsletter") == "update"
    assert _sender({"from": '"GitHub" <noreply@github.com>'}) == "GitHub"
    import ishti.oauth as _o
    _o.connected = lambda service: False  # offline: an unconnected account gets the setup sentence, never fake mail
    assert run("check my email", {})["reply"] == NOT_CONNECTED
    assert run("find my invoices", {})["reply"] == NOT_CONNECTED
    print("[self-check] email OK - routing, order status, unconnected Gmail answers honestly (no invented mail)")
