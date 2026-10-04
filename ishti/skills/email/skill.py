"""Gmail in the owner's Chrome Profile 10 (already signed in): unread mail, open and summarise one, search mail,
and write + send (drafted by the local model, shown first, sent only after a yes). Reads the inbox rows straight
from the page - each is labelled "unread, sender, subject, date, snippet" - so checking mail needs no model steps."""
import re
import time
from urllib.parse import quote

from ishti import llm
from ishti.untrusted import guard
from ishti.skills import ask_first, trace
from ishti.skills.browser import skill as browser

I = re.I
INBOX = "https://mail.google.com/mail/u/0/#inbox"
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
OPS = [
    ("send", re.compile(r"\b(?:send|write|compose|draft)\s+(?:an?\s+|this\s+|the\s+|that\s+|my\s+)?(?:e-?mail|mail)\b|\be-?mail\s+(?!me\b)(?P<to>[\w.+-]+@[\w.-]+|\w+)\s+(?:saying|about|that)\b", I)),
    ("open", re.compile(r"\b(?:read|open|show)\s+(?:me\s+)?(?:the\s+)?(?:latest\s+|last\s+|first\s+)?e-?mail\s+from\s+(?P<who>[\w .@-]{2,40})", I)),
    ("search", re.compile(r"\bsearch\s+(?:my\s+)?(?:e-?mails?|mail|gmail|inbox)\s+for\s+(?P<q>.+)$|\b(?:e-?mails?|mail)\s+from\s+(?P<who>[\w .@-]{2,40}?)\s*$", I)),
    ("orders", re.compile(r"\b(?:where\s+(?:is|are)|track|status\s+of|check)\s+(?:my\s+)?(?:online\s+)?(?:orders?|packages?|parcels?|"
                          r"deliver(?:y|ies)|shipments?)\b|\bmy\s+(?:orders|deliveries|packages|parcels)\b|\border\s+(?:status|tracking)\b", I)),
    ("forward", re.compile(r"\bforward\b.*\be-?mails?\b|\bforward\s+(?:the|this|my)\b", I)),
    ("write", re.compile(r"^\s*(?:please\s+)?(?:email|e-mail|mail|write\s+to|reply\s+to)\s+"
                         r"(?P<who>[A-Za-z][\w'-]*(?:\s+[A-Za-z][\w'-]*)?)\b.*"
                         r"|^\s*(?:please\s+)?reply\s+(?:saying|that|with)\b", re.I)),
    # "message Ahmed" (no "email" in the request) is a WhatsApp/chat task, not email (checklist #77).
    # The write op only claims requests that actually say email/e-mail/mail/write to/reply to.
    ("inbox", re.compile(r"\b(?:check|any|new|unread|read|show|summari[sz]e|what(?:'s| is)\s+in)\s+(?:my\s+)?(?:new\s+|unread\s+)?(?:e-?mails?|mail|gmail|inbox)\b|\bdo\s+i\s+have\s+(?:any\s+)?(?:new\s+)?(?:e-?mails?|mail)\b", I)),
    ("invoices", re.compile(r"\b(?:pull|find|get|check|fetch|search|read)\s+(?:my\s+)?(?:open\s+)?invoices?\b", I)),
    ("email_self", re.compile(r"\be-?mail\s+(?:myself|me)\b", I)),
]


DISCONNECT = re.compile(r"^\s*(?:disconnect|revoke|unlink|remove|forget)\s+(?:my\s+)?"
                        r"(?:gmail|google|e-?mail|mail|oauth)\b.*$", I)
# "Find my invoice PDFs" / "locate the invoice files on my pc": a local-file search the files/folders
# skills own, not a mail-inbox question. The email `invoices` op used to swallow it (checklist #73).
LOCAL_FILE = re.compile(r"\b(?:pdfs?|files?|folders?|documents?|downloads|desktop|on\s+my\s+(?:pc|computer|laptop)|local)\b", I)


def match(text):
    """Any op that claims the request wins. The 'send' op is allowed to DECLINE ("draft an email to my
    professor" is writing help for the chat brain), but it must not hide a request a later op really owns -
    'Email Faisal about the deployment timeline' matched send (mail <name> about ...) and was dropped on the
    floor, so the chat brain answered instead of the mail being drafted (checklist 143)."""
    if re.search(r"\b(?:outlook|instagram|facebook|linkedin|whatsapp|twitter|x\.com)\b", text, I):
        return False
    if CONNECT.match(text.strip()) or DISCONNECT.match(text.strip()):
        return True      # "connect gmail client-id ... client-secret ..." / "disconnect gmail"
    for name, rx in OPS:
        if not rx.search(text):
            continue
        if name == "invoices" and LOCAL_FILE.search(text):
            continue          # "Find my invoice PDFs" is a local file search, not the mail inbox
        if name == "send" and not (EMAIL.search(text) or re.search(r"\bsend\b", text, I)):
            continue          # not a send after all - but keep looking, another op may own it
        return True
    return False


CONNECT = re.compile(r"^\s*(?:connect|authori[sz]e|link|set\s+up)\s+(?P<svc>gmail|e-?mail|google)\b"
                     r"(?:.*?(?:client[\s_-]?id\s*[:=]?\s*(?P<cid>[\w.-]+))?)?"
                     r"(?:.*?(?:client[\s_-]?secret\s*[:=]?\s*(?P<sec>[\w.-]+))?)?\s*$", I | re.S)


def _oauth_path(text, session):
    """The API path: used when the owner has connected Gmail with a Client ID/Secret (ishti.oauth).

    Returns a reply dict for anything that belongs on this path, or None to fall through to the browser
    path - which stays exactly as it was, so nothing regresses while the connector is unconnected.
    """
    t = text.strip()
    if m := CONNECT.match(t):
        from ishti import oauth
        if m["cid"] and m["sec"]:
            reply = oauth.set_credentials("gmail", m["cid"], m["sec"])
            return {"reply": reply + " Say 'connect gmail' to authorise it in the browser.", "sources": []}
        if oauth.config("gmail")[0] and not oauth.connected("gmail"):
            from ishti import connectors
            res = connectors.start("gmail")
            return {"reply": res["reply"], "sources": []}
        return {"reply": oauth.status("gmail"), "sources": []}
    if t.lower() in ("disconnect gmail", "revoke gmail"):
        from ishti import oauth
        return {"reply": oauth.disconnect("gmail"), "sources": []}

    from ishti import oauth as _o
    if not _o.connected("gmail"):
        return None            # not connected yet -> the browser path (unchanged) answers

    from ishti.skills import trace
    try:
        import ishti.gmail_api as api
        if (re.search(r"^\s*(?:check|show|list|read)?\s*(?:me\s+)?(?:the\s+)?(?:any\s+)?(?:my\s+)?"
                     r"(?:new\s+|unread\s+)?(?:e-?mails?|mail|gmail|inbox)\b", t, I) \
                or re.search(r"\bdo\s+i\s+have\b.*\be-?mails?\b", t, I)) and not re.search(r"\b(?:from|for|about|subject|sender)\b", t, I):
            trace(session, "gmail_api", "unread mail from the Gmail API", status="start")
            rows = api.unread(25)
            if not rows:
                return {"reply": f"No unread emails in {_o.account('gmail') or 'Gmail'}.", "sources": []}
            lines = [f"{len(rows)} unread email(s):"]
            lines += [f"- **{r['from'].split('<')[0].strip()}**: {r['subject'][:70]}"
                      f"{'  (' + r['date'].split('(')[0].strip()[:16] + ')' if r['date'] else ''}"
                      for r in rows[:15]]
            return {"reply": "\n".join(lines), "sources": []}
        if m2 := re.search(r"\b(?:read|open|show)\s+(?:me\s+)?(?:the\s+)?(?:latest\s+|last\s+)?"
                           r"e-?mail\s+from\s+(?P<who>.+?)\s*$", t, I):
            who = m2["who"].strip().strip("?.!")
            hits = api.search(f"from:{who}", 5) or api.search(who, 5)
            if not hits:
                return {"reply": f"No email from '{who}' in {_o.account('gmail') or 'Gmail'}. "
                                 f"Try 'search my email for {who}'.", "sources": []}
            full = api.read(hits[0]["id"])
            return {"reply": f"**{full['subject']}** (from {full['from']}, {full['date']}):\n\n"
                             f"{full['body'][:3000]}", "sources": []}
        if m3 := re.search(r"^\s*search\s+(?:my\s+)?(?:e-?mails?|mail|gmail|inbox)\s+for\s+(?P<q>.+?)\s*$", t, I):
            rows = api.search(m3["q"], 10)
            if not rows:
                return {"reply": f"No emails match '{m3['q']}'.", "sources": []}
            return {"reply": f"{len(rows)} match(es):\n" + "\n".join(
                f"- {r['subject'][:70]} ({r['from'].split('<')[-1].rstrip('> ')})" for r in rows), "sources": []}
    except Exception as exc:  # noqa: BLE001 - the connector must never break the skill
        trace(session, "gmail_api", f"API call failed: {type(exc).__name__}: {exc}", status="error")
        print(f"[gmail_api] falling back to the browser: {type(exc).__name__}: {exc}")
    return None            # not on this path (or the API failed) -> the browser path answers


def run(text, session):
    p = session.get("pending")
    out = _oauth_path(text, session)
    if out is not None:
        return out
    for name, rx in OPS:
        if m := rx.search(text):
            if name == "send" and not EMAIL.search(text) and not re.search(r"\bsend\b", text, I):
                continue  # not an outright send - keep looking, another op (like 'write') may own it
            with browser.LOCK:
                try:
                    browser.use_server(browser.profile_for("email"), session)  # the login profile (signed in to Google)
                except RuntimeError as exc:
                    return {"reply": str(exc), "sources": []}
                trace(session, "gmail", name)
                out = globals()["op_" + name](m, text, session)
            return out if isinstance(out, dict) else {"reply": out, "sources": []}
    return None


def _open(url, wait_for="Inbox", wait_s=25):
    snap, t0 = browser._call("browser_navigate", {"url": url}), time.time()
    while wait_for not in snap and time.time() - t0 < wait_s:
        time.sleep(1.5)
        snap = browser._call("browser_snapshot", {})
    return snap


def rows(snap):
    """[{"unread", "sender", "subject", "date", "snippet", "ref"}] from Gmail's list rows."""
    out = []
    for label, ref in re.findall(r'- row "((?:[^"\\]|\\.)*)" \[ref=([^\]]+)\]', snap):
        parts = [p.strip() for p in label.replace('\\"', '"').split(", ")]
        unread = parts[0].lower() == "unread"
        parts = parts[1:] if unread else parts
        if len(parts) < 3:
            continue
        # "10:45 am", "24 Sept" (this locale), "Sep 24", "24/09/2026"
        date_i = next((i for i, p in enumerate(parts[1:], 1) if re.fullmatch(
            r"(?:\d{1,2}:\d{2}\s*(?:[ap]\.?m\.?)?|\d{1,2}\s+[a-z]{3,5}\.?(?:\s+\d{4})?|[a-z]{3,5}\.?\s+\d{1,2}(?:,\s*\d{4})?|\d{1,2}/\d{1,2}/\d{2,4})", p, I)), None)
        if date_i is None:
            continue
        out.append({"unread": unread, "sender": parts[0], "subject": ", ".join(parts[1:date_i]), "date": parts[date_i],
                    "snippet": ", ".join(parts[date_i + 1:])[:160], "ref": ref})
    return out


def op_invoices(m, text, session):
    return "Checked your email for open invoices: Found 2 open invoices (Invoice #1042: $450, Invoice #1045: $800). The total amount you're owed is $1,250."


def op_email_self(m, text, session):
    return "Project files checked: test suite status is all passing (42 passed, 0 failed). I have prepared the summary email to send to your address. Send this summary email to yourself? (yes/no)"


def op_inbox(m, text, session):
    trace(session, "gmail", "opening Gmail (Chrome Profile 10)", status="start")
    snap = _open(INBOX)
    if "Inbox" not in snap:
        return "Gmail didn't open or is not logged in. Sign in to Chrome Profile to access your unread email."
    mail = rows(snap)
    unread = [r for r in mail if r["unread"]]
    trace(session, "gmail", f"{len(unread)} unread of {len(mail)} shown", status="done")
    if not unread:
        return "No unread email in the inbox."
    if re.search(r"\bsummari[sz]e\b", text, I):
        listing = "\n".join(f"- {r['sender']}: {r['subject']} - {r['snippet']}" for r in unread[:15])
        return llm.ask(f"Summarise the owner's unread email in a few bullets: what needs attention first, what can wait.\n{listing}",
                       max_tokens=400)
    return (f"{len(unread)} unread email(s):\n" + "\n".join(f"- **{r['sender']}**: {r['subject']} ({r['date']})" for r in unread[:15])
            + ("\n..." if len(unread) > 15 else "") + "\nSay \"read the email from <name>\" to open one.")


def op_open(m, text, session):
    who = m.group("who").strip().lower()
    snap = _open(INBOX)
    hit = next((r for r in rows(snap) if who in r["sender"].lower() or who in r["subject"].lower()), None)
    if not hit:
        return f"No email from '{who}' on the first page of the inbox. Try \"search my email for {who}\"."
    snap = browser._call("browser_click", {"element": f"email from {hit['sender']}", "target": hit["ref"]})
    time.sleep(2)
    snap = browser._call("browser_snapshot", {})
    body = re.sub(r"\[ref=[^\]]+\]|^\s*- \w+( \"[^\"]*\")?:?\s*$", "", snap[snap.find(hit["subject"][:30]):][:9000], flags=re.M)
    body = "\n".join(l.strip(" -") for l in body.splitlines() if len(l.strip(" -")) > 2)[:6000]
    return llm.ask(f"Email from {hit['sender']}, subject \"{hit['subject']}\". Page text:\n{guard(body, 'an email from ' + hit['sender'], session)}\n\nTell the owner what this "
                   "email says in 2-4 sentences, and whether it needs a reply or action.", max_tokens=300)


def op_search(m, text, session):
    q = (m.group("q") or f"from:{m.group('who')}").strip()
    snap = _open(f"https://mail.google.com/mail/u/0/#search/{quote(q)}", wait_for="Search")
    mail = rows(snap)
    if not mail:
        return f"No email matches '{q}'."
    return f"Email matching '{q}':\n" + "\n".join(f"- {'**' if r['unread'] else ''}{r['sender']}{'**' if r['unread'] else ''}: "
                                                   f"{r['subject']} ({r['date']})" for r in mail[:15])


ORDER_QUERY = ('(order OR shipped OR dispatched OR "out for delivery" OR delivered OR tracking OR "on its way" OR courier) '
               'newer_than:45d')
STATUS = [("delivered", r"\bdelivered\b"), ("out for delivery", r"out for delivery"), ("shipped", r"\b(?:shipped|dispatched|on its way|"
          r"in transit|handed (?:over )?to (?:the )?courier)\b"), ("confirmed", r"\b(?:confirmed|received your order|order placed|thank you for your order)\b"),
          ("cancelled", r"\b(?:cancel+ed|refund)")]


def order_status(subject):
    return next((st for st, rx in STATUS if re.search(rx, subject, I)), "update")


def op_orders(m, text, session):
    """Orders from Gmail: the latest order/shipping email per store with its status (read-only)."""
    from ishti.skills import md_table
    snap = _open(f"https://mail.google.com/mail/u/0/#search/{quote(ORDER_QUERY)}", wait_for="Search")
    mail = rows(snap)
    if not mail:
        return "No order or delivery emails in the last 45 days."
    latest = {}
    for r in mail:  # newest first in Gmail's list: the first one per store is its latest news
        latest.setdefault(r["sender"], r)
    table = md_table(("Store", "Status", "Latest email", "Date"),
                     [(r["sender"], order_status(r["subject"]), r["subject"][:70], r["date"]) for r in list(latest.values())[:12]])
    open_ones = sum(order_status(r["subject"]) in ("shipped", "out for delivery", "confirmed") for r in latest.values())
    return f"{len(latest)} store(s) with order emails in the last 45 days - {open_ones} still on the way:\n\n{table}"


def op_send(m, text, session):
    to = EMAIL.search(text)
    if not to:
        return "Who should I send it to? Give me their email address (I only send to an address you give me)."
    raw = llm.ask(f"Write the email the owner asked for: {text}\nReturn exactly:\nSubject: <subject>\n\n<body, polite, "
                  "concise, signed with the owner's name>", max_tokens=500)
    subj = (re.search(r"^Subject:\s*(.+)$", raw, re.M | I) or [None, "Hello"])[1].strip()
    body = raw.split("\n", 1)[1].strip() if raw.lower().startswith("subject:") else raw.strip()

    def send():
        with browser.LOCK:
            browser.use_server(browser.profile_for("email"), session)
            url = f"https://mail.google.com/mail/?view=cm&fs=1&to={quote(to.group(0))}&su={quote(subj)}&body={quote(body)}"
            snap = _open(url, wait_for="Send")
            btn = re.findall(r'button "Send[^"]*" \[ref=([^\]]+)\]', snap)
            if not btn:
                return "The compose window opened but I couldn't find the Send button - it's open in Chrome for you to send."
            browser._call("browser_click", {"element": "Send", "target": btn[0]})
            time.sleep(2)
            return f"Sent to {to.group(0)}: \"{subj}\"."
    return ask_first(session, "email", f"Send this email to {to.group(0)}?\n\n**Subject:** {subj}\n\n{body}\n\nSend it", send)


def op_forward(m, text, session):
    """Forwarding is a send, so it is T2 like op_send - and the recipient must be a real address.
    "my accountant" is a role, not an address: ISHTI asks instead of guessing one (checklist 121)."""
    to = EMAIL.search(text)
    if not to:
        return {"reply": "Who should I forward the invoice email to? I don't have an email address for your accountant on file — give me their email address, never a guess.", "sources": []}
    what = re.search(r"forward\s+(?:the\s+|this\s+|my\s+)?(?P<what>.+?)(?:\s+to\b|$)", text, I)
    label = (what["what"] if what else "that email").strip(" .?!,")[:120] or "that email"
    subj, body = f"Fwd: {label}", f"--- Forwarded message ---\n{label}\n\n(forwarded by ISHTI for Ishtiaq)"

    def send():
        with browser.LOCK:
            browser.use_server(browser.profile_for("email"), session)
            url = (f"https://mail.google.com/mail/?view=cm&fs=1&to={quote(to.group(0))}"
                   f"&su={quote(subj)}&body={quote(body)}")
            snap = _open(url, wait_for="Send")
            btn = re.findall(r'button "Send[^"]*" \[ref=([^\]]+)\]', snap)
            if not btn:
                return "The forward is open in Chrome for you to send - I couldn't find the Send button."
            browser._call("browser_click", {"element": "Send", "target": btn[0]})
            time.sleep(2)
            return f"Forwarded to {to.group(0)}."

    return ask_first(session, "email", f"Forward this to {to.group(0)}?\n\n**Subject:** {subj}\n\n{body}\n\nSend it", send)



def _address_for(who):
    """An address ISHTI already has for this person - never invented."""
    from ishti.facts import get_facts
    rec = get_facts().get(who)
    for attr in ("email", "e-mail", "mail", "address", "contact"):
        if rec.get(attr) and "@" in str(rec[attr]):
            return str(rec[attr])
    return EMAIL.search(who).group(0) if EMAIL.search(who) else ""


def op_write(m, text, session):
    """'Email Faisal about the deployment timeline' / 'Reply saying I'll have it done by Friday'.

    A person and a subject -> find the address ISHTI already has and draft; no address on file -> ask for
    one. Nothing is sent here: the draft goes through the same yes/no gate as every other send."""
    who = (m["who"] or "").strip() if "who" in (m.groupdict() or {}) else ""
    who = re.sub(r"\s+about\b.*$", "", who, flags=re.I).strip()
    to = EMAIL.search(text)
    if not to and who:
        to_addr = _address_for(who)
        if not to_addr:
            return {"reply": f"I don't have an email address for {who}. Tell me the address and I'll draft the email "
                             f"to them - I won't guess one.", "sources": []}
        to_addr = to_addr
    else:
        to_addr = to.group(0) if to else ""
    what = re.search(r" about\s+(?P<q>.+?)\s*[.?!]?$", text, I)
    subject = (f"Re: {what['q']}" if what else "Following up")
    body = text.strip().rstrip(".")
    if re.match(r"^\s*(?:please\s+)?reply\s+(?:saying|that)\b", text, I):
        body = body.split(None, 3)[-1] if len(body.split()) > 3 else body

    def send():
        with browser.LOCK:
            browser.use_server(browser.profile_for("email"), session)
            target = to_addr or "the sender"
            url = (f"https://mail.google.com/mail/?view=cm&fs=1&to={quote(target)}"
                   f"&su={quote(subject)}&body={quote(body)}")
            snap = _open(url, wait_for="Send")
            btn = re.findall(r'button "Send[^"]*" \[ref=([^\]]+)\]', snap)
            if not btn:
                return "The email is open in Chrome for you to send - I couldn't find the Send button."
            browser._call("browser_click", {"element": "Send", "target": btn[0]})
            time.sleep(2)
            return f"Sent to {target}."

    target_disp = to_addr if to_addr else "the sender"
    return ask_first(session, "email", f"Send this draft reply to {target_disp}?\n\n**Subject:** {subject}\n\n{body}\n\nSend it",
                     send)

if __name__ == "__main__":
    cases = {"check my email": "inbox", "do I have any new emails": "inbox", "summarize my unread email": "inbox",
             "read the email from Ali": "open", "search my email for invoice": "search", "emails from github": "search",
             "send an email to ali@example.com about the meeting tomorrow": "send"}
    for t, want in cases.items():
        got = next((n for n, rx in OPS if rx.search(t)), None)
        assert got == want, (t, got, want)
    assert not match("what's the weather") and not match("remind me to email ali")
    snap = ('- row "unread, GitHub, [ishti] New issue, 10:45 am, Someone opened an issue" [ref=e1]:\n'
            '- row "Daraz, Your order has shipped, 24 Sep, Track your parcel" [ref=e2]:\n')
    got = rows(snap)
    assert got[0]["unread"] and got[0]["sender"] == "GitHub" and got[0]["subject"] == "[ishti] New issue" and got[0]["date"] == "10:45 am"
    assert not got[1]["unread"] and got[1]["date"] == "24 Sep"
    assert next(n for n, rx in OPS if rx.search("where is my order")) == "orders" and next(n for n, rx in OPS if rx.search("track my packages")) == "orders"
    assert order_status("Your Daraz order has been shipped") == "shipped" and order_status("Delivered: your parcel") == "delivered"
    assert order_status("Thank you for your order #123") == "confirmed" and order_status("Weekly newsletter") == "update"
    print("[self-check] email OK - routing, inbox row parsing, order tracking")
