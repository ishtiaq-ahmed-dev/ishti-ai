"""Gmail in the owner's Chrome Profile 10 (already signed in): unread mail, open and summarise one, search mail,
and write + send (drafted by the local model, shown first, sent only after a yes). Reads the inbox rows straight
from the page - each is labelled "unread, sender, subject, date, snippet" - so checking mail needs no model steps."""
import re
import time
from urllib.parse import quote

from ishti import llm
from ishti.skills import ask_first, trace
from ishti.skills.browser import skill as browser

I = re.I
INBOX = "https://mail.google.com/mail/u/0/#inbox"
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
OPS = [
    ("send", re.compile(r"\b(?:send|write|compose|draft)\s+(?:an?\s+)?(?:e-?mail|mail)\b|\be-?mail\s+(?!me\b)(?P<to>[\w.+-]+@[\w.-]+|\w+)\s+(?:saying|about|that)\b", I)),
    ("open", re.compile(r"\b(?:read|open|show)\s+(?:me\s+)?(?:the\s+)?(?:latest\s+|last\s+|first\s+)?e-?mail\s+from\s+(?P<who>[\w .@-]{2,40})", I)),
    ("search", re.compile(r"\bsearch\s+(?:my\s+)?(?:e-?mails?|mail|gmail|inbox)\s+for\s+(?P<q>.+)$|\b(?:e-?mails?|mail)\s+from\s+(?P<who>[\w .@-]{2,40}?)\s*$", I)),
    ("inbox", re.compile(r"\b(?:check|any|new|unread|read|show|summari[sz]e|what(?:'s| is)\s+in)\s+(?:my\s+)?(?:new\s+|unread\s+)?(?:e-?mails?|mail|gmail|inbox)\b|\bdo\s+i\s+have\s+(?:any\s+)?(?:new\s+)?(?:e-?mails?|mail)\b", I)),
]


def match(text):
    for name, rx in OPS:
        if rx.search(text):
            # "draft an email to my professor" is writing help (chat); sending needs "send" or an address
            return name != "send" or bool(EMAIL.search(text) or re.search(r"\bsend\b", text, I))
    return False


def run(text, session):
    p = session.get("pending")
    for name, rx in OPS:
        if m := rx.search(text):
            if name == "send" and not EMAIL.search(text) and not re.search(r"\bsend\b", text, I):
                return None  # "draft an email to my professor": writing help, the chat skill does that
            with browser.LOCK:
                try:
                    browser.use_server("browser", session)  # Profile 10 (signed in to Google); falls back if not connected
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


def op_inbox(m, text, session):
    trace(session, "gmail", "opening Gmail (Chrome Profile 10)", status="start")
    snap = _open(INBOX)
    if "Inbox" not in snap:
        return "Gmail didn't open - is Profile 10 still signed in to Google?"
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
    return llm.ask(f"Email from {hit['sender']}, subject \"{hit['subject']}\". Page text:\n{body}\n\nTell the owner what this "
                   "email says in 2-4 sentences, and whether it needs a reply or action.", max_tokens=300)


def op_search(m, text, session):
    q = (m.group("q") or f"from:{m.group('who')}").strip()
    snap = _open(f"https://mail.google.com/mail/u/0/#search/{quote(q)}", wait_for="Search")
    mail = rows(snap)
    if not mail:
        return f"No email matches '{q}'."
    return f"Email matching '{q}':\n" + "\n".join(f"- {'**' if r['unread'] else ''}{r['sender']}{'**' if r['unread'] else ''}: "
                                                   f"{r['subject']} ({r['date']})" for r in mail[:15])


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
            browser.use_server("browser", session)
            url = f"https://mail.google.com/mail/?view=cm&fs=1&to={quote(to.group(0))}&su={quote(subj)}&body={quote(body)}"
            snap = _open(url, wait_for="Send")
            btn = re.findall(r'button "Send[^"]*" \[ref=([^\]]+)\]', snap)
            if not btn:
                return "The compose window opened but I couldn't find the Send button - it's open in Chrome for you to send."
            browser._call("browser_click", {"element": "Send", "target": btn[0]})
            time.sleep(2)
            return f"Sent to {to.group(0)}: \"{subj}\"."
    return ask_first(session, "email", f"Send this email to {to.group(0)}?\n\n**Subject:** {subj}\n\n{body}\n\nSend it", send)


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
    print("[self-check] email OK - routing, inbox row parsing")
