"""CRM / client management skill.

The two hard rules live in this module, not in prompts:
  1. ISOLATION - everything a client-specific operation sees comes from that
     client's rows only (client_context_load returns one client's facts; the
     grounding builder refuses to mix clients). No other client's data can
     appear in a reply to this client.
  2. CONFIRMATION - sending anything to a real client is always T2: the send
     path returns a draft and asks. There is no auto-send, and the manifest
     marks this skill T2-level for anything that touches an outbox.
Storage: SQLite (ishti.db, tables clients / client_events / client_invoices),
per-client events carry first_seen / last_confirmed like the relationship graph.
Sentiment: a lexicon pass (angry/urgent cues) flags messages for the OWNER to
handle - flagged clients are never auto-drafted.
"""
from ishti.core.logutil import swallowed
import datetime as dt
import json
import os
import re
import sys
import time

if __package__ in (None, ""):  # run directly: python ishti/skills/clients/skill.py
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from ishti import ROOT
from ishti.facts import get_facts
from ishti.skills import trace

I = re.I

# ---------------------------------------------------------------- storage
def _db():
    f = get_facts()
    with f.lock, f.db:
        f.db.executescript("""
            CREATE TABLE IF NOT EXISTS clients(
                id INTEGER PRIMARY KEY, name TEXT UNIQUE, channel TEXT DEFAULT '',
                contact TEXT DEFAULT '', style TEXT DEFAULT '', prefs TEXT DEFAULT '',
                created REAL, last_contact REAL DEFAULT 0, status TEXT DEFAULT 'active');
            CREATE TABLE IF NOT EXISTS client_events(
                id INTEGER PRIMARY KEY, client_id INTEGER, kind TEXT, body TEXT,
                private INTEGER DEFAULT 0, first_seen REAL, last_confirmed REAL);
            CREATE TABLE IF NOT EXISTS client_invoices(
                id INTEGER PRIMARY KEY, client_id INTEGER, label TEXT, amount REAL,
                state TEXT DEFAULT 'due', created REAL);
        """)
    return f


COLS = ("id", "name", "channel", "contact", "style", "prefs", "created", "last_contact", "status")


def _key(name):
    return re.sub(r"\s+", " ", (name or "").strip()).lower()


def _resolve(name):
    """A client by name, for LOOKUPS only. An exact match always wins; failing that, a UNIQUE
    name prefix resolves ("Aliza" -> "aliza khan"). An unknown OR AMBIGUOUS name resolves to
    None and is never guessed, so a lookup can never fall through to another client's rows -
    that is isolation rule 1. The comparison is one-directional on purpose (a query is only
    ever a prefix of a stored name), so asking with a MORE specific name than we hold can
    never pull in the shorter client."""
    key = _key(name)
    if not key:
        return None
    f = _db()
    with f.lock:
        rows = [dict(zip(COLS, r)) for r in f.db.execute("SELECT * FROM clients").fetchall()]
    for r in rows:
        if r["name"] == key:
            return r
    if len(key) < 3:  # too short to be a safe abbreviation
        return None
    hits = [r for r in rows if r["name"].startswith(key)]
    return hits[0] if len(hits) == 1 else None


def _client(name, create=False):
    """LOOKUPS (create=False) resolve through _resolve, so a partial name is fine when it is
    unambiguous. CREATION stays exact so "new client Ali" still makes a new profile even when
    "Ali Khan" exists (creating must never silently reuse someone else's profile)."""
    key = _key(name)
    if not create:
        return _resolve(name)
    f = _db()
    with f.lock:
        row = f.db.execute("SELECT * FROM clients WHERE name=?", (key,)).fetchone()
        if row:
            return dict(zip(COLS, row))
    with f.lock, f.db:
        cur = f.db.execute("INSERT INTO clients(name, created) VALUES(?,?)", (key, time.time()))
        cid = cur.lastrowid
    return {"id": cid, "name": key, "channel": "", "contact": "", "style": "", "prefs": "",
            "created": time.time(), "last_contact": 0, "status": "active"}


def _event(cid, kind, body, private=0):
    f = _db()
    with f.lock, f.db:
        f.db.execute("INSERT INTO client_events(client_id, kind, body, private, first_seen, last_confirmed) "
                     "VALUES(?,?,?,?,?,?)", (cid, kind, body[:2000], private, time.time(), time.time()))
        f.db.execute("UPDATE clients SET last_contact=? WHERE id=?", (time.time(), cid))


# ---------------------------------------------------------------- sentiment
ANGRY = re.compile(r"\b(?:unacceptable|angry|furious|ridiculous|worst|scam|cheat|disappointed|appalled|"
                   r"never (?:again|working)|complain(?:t|ing)?|refund now|unprofessional)\b", I)
URGENT = re.compile(r"\b(?:urgent|asap|immediately|right now|emergency|today|deadline|overdue|last warning|"
                    r"final notice)\b", I)
HAPPY = re.compile(r"\b(?:thanks|thank you|great work|excellent|amazing|love it|perfect|appreciate)\b", I)


def sentiment(text):
    """(label, flagged_bool): negative/urgent messages go to the owner, never a draft."""
    if ANGRY.search(text):
        return ("negative", True)
    if URGENT.search(text):
        return ("urgent", True)
    if HAPPY.search(text):
        return ("positive", False)
    return ("neutral", False)


# ---------------------------------------------------------------- grounding (rule 1)
def context_load(name, allow_private=False):
    """ONE client's facts, their events and invoices - nothing else. This is the
    only grounding any client reply may use. Clients' events marked private
    (sensitive) stay out unless allow_private."""
    from ishti import crm_contract
    matches = [r for r in crm_contract.list_clients()
               if r.get("scope") == "business_crm"
               and (r.get("client_id") == name or _key(r.get("contact_name")) == _key(name))]
    c = _client(name)
    if c and matches:
        return None
    if not c:
        if len(matches) != 1:
            return None
        record = matches[0]
        return {"client": {"id": record["client_id"], "name": _key(record["contact_name"]),
                           "channel": record.get("channel_source", ""),
                           "contact": record.get("phone") or record.get("email", ""),
                           "style": "", "prefs": "", "company": record.get("company", ""),
                           "provenance": "registered contract CRM record"},
                "events": [], "invoices": []}
    f = _db()
    with f.lock:
        events = [dict(zip(("id", "client_id", "kind", "body", "first_seen"), r))
                  for r in f.db.execute("SELECT id, client_id, kind, body, first_seen FROM client_events "
                                        "WHERE client_id=? AND private<=? ORDER BY first_seen DESC LIMIT 12",
                                        (c["id"], 1 if allow_private else 0)).fetchall()]
        invoices = [dict(zip(("id", "label", "amount", "state"), r))
                    for r in f.db.execute("SELECT id, label, amount, state FROM client_invoices "
                                          "WHERE client_id=? ORDER BY created DESC", (c["id"],)).fetchall()]
    return {"client": c, "events": events, "invoices": invoices}


def grounding_for(name):
    """Text block a reply draft is built from: this client only, never the graph's
    private people, never another client."""
    ctx = context_load(name)
    if not ctx:
        return None
    c = ctx["client"]
    lines = [f"Client: {c['name'].title()}" + (f" ({c['channel']}: {c['contact']})" if c["channel"] else "")]
    if c.get("company"):
        lines.append(f"Company: {c['company']}")
    if c.get("provenance"):
        lines.append(f"Source: {c['provenance']} ({c['id']})")
    if c["style"]:
        lines.append(f"Communication style: {c['style']}")
    if c["prefs"]:
        lines.append(f"Preferences: {c['prefs']}")
    for e in ctx["events"][:6]:
        day = dt.datetime.fromtimestamp(e["first_seen"]).strftime("%d %b")
        lines.append(f"- [{e['kind']} {day}] {e['body'][:160]}")
    for inv in ctx["invoices"]:
        lines.append(f"- [invoice {inv['label']}] {inv['amount']:.0f} - {inv['state']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- ops
def op_new(text, session):
    m = re.search(r"\b(?:new\s+|add\s+|register\s+)?client\s+(?:named\s+|called\s+)?[\"']?(?P<name>[\w .'-]{2,40}?)"
                  r"[\"']?(?:\s+via\s+(?P<ch>whatsapp|email|fiverr|upwork|phone|telegram|instagram))?"
                  r"(?:\s*,?\s*(?:contact|reach(?:able)?(?:\s+at)?|email)\s*[:=]?\s*(?P<contact>[\w@.:-]+))?\s*$", text, I)
    if not m:
        return {"reply": "Who should I add? e.g. \"add client Ahmed via whatsapp, contact 0300-1234567\"", "sources": []}
    name = m["name"].strip()
    c = _client(name, create=True)
    if m["ch"] or m["contact"]:
        f = _db()
        with f.lock, f.db:
            f.db.execute("UPDATE clients SET channel=?, contact=? WHERE id=?",
                         ((m["ch"] or "").lower(), m["contact"] or "", c["id"]))
        c["channel"], c["contact"] = (m["ch"] or "").lower(), m["contact"] or ""
    _event(c["id"], "profile", f"client created" + (f" via {c['channel']}" if c["channel"] else ""))
    trace(session, "clients", f"profile created for {name}")
    return {"reply": f"Client created: {name.title()}" + (f" via {c['channel']}" if c["channel"] else "")
                    + ". Everything I remember about them stays in their own profile - it never mixes "
                      "with another client's. Log messages with \"log client message\".", "sources": []}


def op_log(text, session):
    m = re.search(r"\b(?:log|record|save)\s+(?:a\s+)?(?:client\s+)?(?:message|inquiry|reply)\s+(?:from\s+)?"
                  r"[\"']?(?P<name>[\w .'-]{2,40})[\"']?\s*[:,-]?\s*[\"']?(?P<body>[^\"']{4,1200})", text, I)
    if not m:
        return {"reply": "What did they say? e.g. \"log client message from Ahmed: needs the logo by Friday\"", "sources": []}
    c = _client(m["name"].strip(), create=True)
    body = m["body"].strip().strip("'\"")
    label, flagged = sentiment(body)
    _event(c["id"], "message", body)
    if flagged:
        return {"reply": f"Logged from {c['name'].title()} - but flag it {label}: this one reads "
                         f"{label}. I won't draft a reply - that one is yours to handle. "
                         "(\"client context Ahmed\" to review their history first)", "sources": []}
    return {"reply": f"Logged ({label}) from {c['name'].title()}: \"{body[:80]}\". "
                     "Ask \"client context {0}\" before replying to them.".format(c["name"]), "sources": []}


def op_context(text, session):
    m = re.search(r"\b(?:client\s+)?context\s+(?:for\s+|of\s+)?[\"']?(?P<name>[\w .'-]{2,80})[\"']?", text, I)
    if not m:
        return None
    g = grounding_for(m["name"].strip())
    if not g:
        known = known_clients()
        return {"reply": f"No client named '{m['name'].strip()}'."
                         + (f" Clients I have: {', '.join(k.title() for k in known)}" if known else
                            " - add one with \"new client <name>\"."), "sources": []}
    trace(session, "clients", f"loaded context for {m['name'].strip()} (their rows only)")
    return {"reply": g, "sources": [], "no_escalate": True}


def known_clients():
    f = _db()
    with f.lock:
        return [r[0] for r in f.db.execute("SELECT name FROM clients ORDER BY name").fetchall()]


def list_profiles():
    """Read-only business profiles for the dashboard; no events or private facts."""
    f = _db()
    with f.lock:
        rows = f.db.execute(
            "SELECT id, name, channel, contact, status, created FROM clients ORDER BY name"
        ).fetchall()
    return [{"id": f"sqlite:{row[0]}", "name": str(row[1]).title(),
             "channel_source": row[2] or "", "phone": row[3] or "",
             "status": row[4] or "active", "created_at": row[5],
             "source": "chat_crm", "read_only": True} for row in rows]


def op_draft(text, session):
    m = re.search(r"\b(?:draft|write|prepare)\s+(?:a\s+)?reply\s+(?:to|for)\s+[\"']?(?P<name>[\w .'-]{2,40})", text, I)
    if not m:
        return None
    who = m["name"].strip()
    ctx = context_load(who)
    if not ctx:
        known = known_clients()
        return {"reply": f"No client '{who}'."
                         + (f" Clients I have: {', '.join(k.title() for k in known)}" if known else ""), "sources": []}
    # flagged clients are the owner's job, not a drafting job
    for e in ctx["events"][:3]:
        label, flagged = sentiment(e["body"])
        if flagged:
            return {"reply": f"{who.title()}'s latest message reads {label} - I'm not drafting a reply to that. "
                             "It's yours; I'll help with the history instead (\"client context "
                             f"{who}\").", "sources": []}
    from ishti import llm
    g = grounding_for(who)
    trace(session, "draft", f"drafting from {who}'s own history only", status="start")
    draft = llm.ask(f"Draft a short, professional reply to this client. Ground EVERYTHING in their "
                    f"history below - never invent project details, prices or promises. Leave a "
                    f"placeholder like [price] where the owner must fill a value.\n\n{g}\n\n"
                    f"Reply with only the draft message.",
                    system=None, temperature=0.3, max_tokens=250)
    trace(session, "draft", "draft ready", status="done")
    who = ctx["client"]["name"]
    session.update(draft=draft, draft_for=who, draft_client_id=ctx["client"]["id"])
    return {"reply": f"Draft for {who.title()} (their history only - check it, then say "
                     f"\"send draft to {who}\" to send it):\n\n---\n{draft}\n---\n\n"
                     f"Nothing is sent until you say send.", "sources": [], "draft_for": who, "draft": draft}


def op_send(text, session):
    m = re.search(r"\bsend\s+(?:the\s+)?draft\s+to\s+[\"']?(?P<name>[\w .'-]{2,40})", text, I)
    if not m:
        return None
    who = m["name"].strip()
    if not isinstance(session, dict):
        return {"reply": "No client draft is available in this session. Nothing was sent.", "sources": []}
    c = _resolve(who)
    if not c:
        return {"reply": "That client is unknown or ambiguous. Nothing was sent; no profile was created.", "sources": []}
    draft = session.get("draft")
    if not draft:
        return {"reply": f'Draft a reply first ("draft a reply to {who}"). Nothing was sent.', "sources": []}
    if session.get("draft_client_id") != c["id"] or _key(session.get("draft_for")) != c["name"]:
        return {"reply": "This draft belongs to a different client or has no verified client binding. "
                         "Create a draft for the intended client. Nothing was sent.", "sources": []}
    # This skill has no declared transport scope; recording an event is not delivery.
    return {"reply": f"The draft for {c['name'].title()} is saved, but CRM message delivery is not integrated. "
                     "Nothing was sent or logged as sent. Use the approved WhatsApp or email send flow "
                     "with the exact recipient and this draft.", "sources": [], "ok": False,
            "delivery": "not_attempted"}


def op_followup(text, session):
    m = re.search(r"\bfollow\s*up\s+(?:with\s+)?[\"']?(?P<name>[\w .'-]{2,40}?)[\"']?\s*"
                  r"(?:in|after)?\s*(?:(?P<n>\d+)\s*(?P<u>days?|d|hours?|h|weeks?|w)\b)?", text, I)
    if not m or m["name"].lower().startswith("the client"):
        return None
    who = m["name"].strip().strip("'\"")
    mult = {"d": 1, "h": 1 / 24, "w": 7}.get((m["u"] or "d").lower().rstrip("s")[:1], 1)
    days = (int(m["n"]) if m["n"] else 3) * mult
    try:  # reminders' own machinery: a plain SQLite row, due in N days
        from ishti.skills.reminders.skill import _db as _rdb, _fmt
        import datetime as _dt
        f = _rdb()
        due_s = days * 86400
        label = f"Follow up with client {who}: check their last message and reply."
        with f.lock, f.db:
            f.db.execute("INSERT INTO reminders(due, text, created) VALUES(?,?,?)",
                         (time.time() + due_s, label, time.time()))
            rid = f.db.execute("SELECT last_insert_rowid()").fetchone()[0]
        human = _fmt(time.time() + due_s, _dt.datetime.now())
    except Exception as exc:
        return {"reply": f"Couldn't set the reminder ({type(exc).__name__}) - the follow-up is noted in "
                         f"{who}'s profile though.", "sources": []}
    _event(_client(who, create=True)["id"], "followup", f"reminder #{rid}: check reply in {days:g} day(s)")
    return {"reply": f"Follow-up set: I'll remind you about {who.title()} {human}.", "sources": []}


def op_deal(text, session):
    m = re.search(r"\b(?:deal|project)\s+(?:status|summary)\s+(?:for|of|with)?\s*[\"']?(?P<name>[\w .'-]{2,40})", text, I) \
        or re.search(r"\bwhat(?:'s| is)\s+(?:the\s+)?(?:deal|project)\s+status\s+(?:with|for)\s+[\"']?(?P<name>[\w .'-]{2,40})", text, I)
    if not m:
        return None
    ctx = context_load(m["name"].strip())
    if not ctx:
        return {"reply": f"No client '{m['name'].strip()}' yet.", "sources": []}
    who = ctx["client"]["name"]
    evs = ctx["events"]
    if not evs:
        return {"reply": f"No logged history for {who.title()} yet - log their messages and I can "
                         "summarize the deal.", "sources": []}
    from ishti import llm
    body = "\n".join(f"[{e['kind']}] {e['body'][:200]}" for e in reversed(evs))
    out = llm.ask(f"Summarize the state of this client's deal from their history ONLY. Three short "
                  f"sections: Agreed / Pending / Owed (money or deliverables). If something isn't in "
                  f"the history, say 'not in the logged history' - never invent.\n\n{body}",
                  system=None, temperature=0.2, max_tokens=220)
    return {"reply": f"Deal summary for {who.title()} (from their logged history):\n\n{out}", "sources": []}


def op_invoice(text, session):
    # 'make/create an invoice' is the docs skill's (a document); here: record/mark/status
    if re.search(r"\b(?:make|create|generate|draft)\s+(?:an?\s+)?invoice\b|invoice\s*(?:pdf|file)\b|\bfind\s+\w*\s*invoice", text, I):
        return None
    add = re.search(r"\b(?:add|log|record|new)\s+invoice\s+(?:for\s+)?[\"']?(?P<name>[\w .'-]{2,40})[\"']?\s*[:,-]?\s*"
                    r"(?P<label>.{2,40}?)\s*(?P<amount>\d[\d,]*)", text, I) \
        or re.search(r"\binvoice\s+(?:for\s+)?[\"']?(?P<name>[\w .'-]{2,40})[\"']?\s*:\s*"
                     r"(?P<label>.{2,40}?)\s*(?P<amount>\d[\d,]*)", text, I)
    mark = re.search(r"\binvoice\s+(?P<id>\d+)\s+(?:is\s+)?(?P<state>paid|overdue|cancelled)", text, I)
    status = re.search(r"\b(?:invoice|payment)s?\s+status\s+(?:for|of)?\s*[\"']?(?P<name>[\w .'-]{2,40})", text, I)
    f = _db()
    if add:
        c = _client(add["name"].strip(), create=True)
        amount = float(add["amount"].replace(",", ""))
        with f.lock, f.db:
            cur = f.db.execute("INSERT INTO client_invoices(client_id, label, amount, state, created) "
                               "VALUES(?,?,?,?,?)", (c["id"], add["label"].strip(" :-"), amount, "due", time.time()))
        _event(c["id"], "invoice", f"invoice #{cur.lastrowid}: {add['label'].strip()} - {amount:.0f} (due)")
        return {"reply": f"Invoice #{cur.lastrowid} added for {c['name'].title()}: {add['label'].strip()} - "
                         f"{amount:.0f} (due).", "sources": []}
    if mark:
        with f.lock, f.db:
            n = f.db.execute("UPDATE client_invoices SET state=? WHERE id=?",
                             (mark["state"], int(mark["id"]))).rowcount
        return {"reply": f"Invoice #{mark['id']} marked {mark['state']}." if n
                else f"No invoice #{mark['id']}.", "sources": []}
    if status:
        ctx = context_load(status["name"].strip())
        if not ctx:
            return {"reply": f"No client '{status['name'].strip()}'.", "sources": []}
        if not ctx["invoices"]:
            return {"reply": f"No invoices for {ctx['client']['name'].title()} yet - add one with "
                             "\"invoice for <name>: <what> <amount>\".", "sources": []}
        lines = [f"- #{i['id']} {i['label']}: {i['amount']:.0f} - {i['state']}" for i in ctx["invoices"]]
        owed = sum(i["amount"] for i in ctx["invoices"] if i["state"] == "due")
        over = sum(i["amount"] for i in ctx["invoices"] if i["state"] == "overdue")
        tail = f"\nOutstanding: {owed:.0f}" + (f", overdue: {over:.0f}" if over else "")
        return {"reply": f"Invoices for {ctx['client']['name'].title()}:\n" + "\n".join(lines) + tail, "sources": []}
    return None


def op_note(text, session):
    m = re.search(r"\bclient\s+note\s+(?:on|for|about)?\s*[\"']?(?P<name>[\w .'-]{2,40})[\"']?\s*[:,-]?\s*"
                  r"[\"']?(?P<body>[^\"']{3,600})", text, I)
    if not m:
        return None
    c = _client(m["name"].strip(), create=True)
    _event(c["id"], "note", m["body"].strip().strip("'\""))
    return {"reply": f"Note added to {c['name'].title()}'s profile (their profile only).", "sources": []}


def op_lead(text, session):
    m = re.search(r"\b(?:new\s+)?(?:lead|inquiry)\s*(?:from)?\s*[:,-]?\s*[\"']?(?P<name>[\w .'-]{2,40})[\"']?"
                  r"\s*[:,-]?\s*[\"']?(?P<body>[^\"']{6,1200})", text, I)
    if not m:
        return None
    name, body = m["name"].strip(), m["body"].strip().strip("'\"")
    c = _client(name, create=True)
    _event(c["id"], "lead", body)
    from ishti import llm
    extract = llm.ask(f"A new client inquiry: \"{body}\"\nReturn JSON {{\"wants\": \"<what they're asking "
                      "for in one line>\", \"fits\": true/false if it sounds like a normal freelance/"
                      'software/design task vs spam or out of scope, "channel": "whatsapp/email/fiverr/other"}}.',
                      system=None, temperature=0, max_tokens=120, response_format={"type": "json_object"})
    try:
        d = json.loads(extract)
    except (ValueError, AttributeError):
        d = {"wants": body[:120], "fits": None, "channel": "other"}
    verdict = ("looks like a normal task" if d.get("fits") else
               ("doesn't look like your usual work" if d.get("fits") is False else "unclear fit"))
    return {"reply": f"Lead logged from {name.title()}: they want - {d.get('wants', '?')} ({verdict}, "
                     f"channel: {d.get('channel', '?')}). Profile created; reply only after "
                     f"\"client context {name}\".", "sources": []}


def op_dashboard(text, session):
    f = _db()
    with f.lock:
        rows = [dict(zip(("name", "channel", "last_contact", "status"), r))
                for r in f.db.execute("SELECT name, channel, last_contact, status FROM clients "
                                      "ORDER BY last_contact DESC").fetchall()]
        owed = {r[0]: (r[1] or 0) for r in f.db.execute(
            "SELECT c.name, SUM(i.amount) FROM clients c JOIN client_invoices i ON i.client_id=c.id "
            "WHERE i.state IN ('due','overdue') GROUP BY c.name").fetchall()}
    if not rows:
        return {"reply": "No clients yet - \"new client <name>\" starts one.", "sources": []}
    from ishti.skills import md_table
    day = lambda ts: dt.datetime.fromtimestamp(ts).strftime("%d %b") if ts else "-"
    table = md_table(("Client", "Channel", "Last contact", "Outstanding"),
                     [(r["name"].title(), r["channel"] or "-", day(r["last_contact"]),
                       f"{owed[r['name']]:.0f}" if r["name"] in owed else "-") for r in rows])
    flagged = []
    with f.lock:
        for r in f.db.execute("SELECT c.name, e.body FROM client_events e JOIN clients c ON c.id=e.client_id "
                              "WHERE e.kind='message' ORDER BY e.first_seen DESC LIMIT 40").fetchall():
            label, flagged_ = sentiment(r[1])
            if flagged_ and r[0] not in flagged:
                flagged.append(r[0])
    head = f"{len(rows)} client(s)." + (f" ATTENTION: {', '.join(k.title() for k in flagged)} has a "
                                        f"negative/urgent message waiting for you." if flagged else "")
    return {"reply": head + "\n\n" + table, "sources": []}


# ---------------------------------------------------------------- skill plumbing
CREATE = re.compile(r"\b(?:new|add|register)\s+client\b", I)
LOGMSG = re.compile(r"\b(?:log|record|save)\s+(?:a\s+)?(?:client\s+)?(?:message|inquiry|reply)\b", I)
CONTEXT = re.compile(r"\bclient\s+context\b|\bcontext\s+(?:for|of)\s+client\b", I)  # 'client context' - a bare 'context' is other skills'
DRAFT = re.compile(r"\b(?:draft|write|prepare)\s+(?:a\s+)?reply\b", I)
SEND = re.compile(r"\bsend\s+(?:the\s+)?draft\b", I)
FOLLOWUP = re.compile(r"\bfollow\s*up\b.*\b(?:with\s+)?client\b|\bfollow\s*up\s+with\s+[\w'-]+\b", I)
DEAL = re.compile(r"\b(?:deal|project)\s+(?:status|summary)|what(?:'s| is)\s+the\s+(?:deal|project)\s+status", I)
# 'invoice' + a client name / an amount: the CRM's. "find the invoice pdf on my computer" and
# "make an invoice for Ali" (a document) stay with files/docs - no file path words, no 'make'.
# "invoice for X: <thing> <amount>" (the colon record form) is the CRM's too.
INVOICE = re.compile(r"\b(?:client\s+invoice|invoice\s+(?:status|for\s+client)|(?:add|log|new|mark)\s+invoice|"
                     r"invoice\s+\d+\s+(?:is\s+)?(?:paid|overdue|cancelled))\b|\bpayment\s+status\b|"
                     r"\binvoice\s+(?:for\s+)?[\w .'-]{2,40}\s*:\s*.{2,40}?\d", I)
INVOICE_DOC = re.compile(r"\b(?:make|create|generate|draft)\s+(?:an?\s+)?invoice\b|\binvoice\s*(?:pdf|file)\b", I)
# INVOICE minus INVOICE_DOC is the gate: 'make an invoice' is the docs skill's document job
NOTE = re.compile(r"\bclient\s+note\b", I)
LEAD = re.compile(r"\b(?:lead|inquiry)\b", I)
DASH = re.compile(r"\b(?:client|crm)\s+(?:dashboard|overview|list)\b|\ball\s+my\s+clients\b", I)
# "who are my clients right now?" - the roster question, answered from the CRM store (checklist 140)
ROSTER = re.compile(r"\bwho\s+(?:are|is)\s+(?:my|our)\s+clients?\b|\b(?:list|show|name)\s+(?:all\s+)?"
                    r"(?:of\s+)?(?:my|our)\s+clients?\b|\bmy\s+clients\s+right\s+now\b|"
                    r"\bwhich\s+clients\s+do\s+i\s+have\b|\b(?:how\s+many|what)\s+clients\s+"
                    r"(?:do\s+i\s+have|are\s+active)\b", I)


def op_client_roster(text, session):
    """"Who are my clients right now?" (checklist 140) - read the REAL store on every call (never a
    cached or remembered answer), list each stored client with the work they are tied to, and say
    plainly when the store is empty. Nothing outside this CRM's own rows is ever used."""
    f = _db()
    rows = []
    with f.lock:
        rows = [dict(zip(COLS, r)) for r in f.db.execute(
            "SELECT * FROM clients ORDER BY last_contact DESC, name").fetchall()]
        work = {}
        for name, label, amount, state in f.db.execute(
                "SELECT c.name, i.label, i.amount, i.state FROM client_invoices i "
                "JOIN clients c ON c.id=i.client_id ORDER BY i.created DESC").fetchall():
            work.setdefault(name, []).append(f"{label} ({state}, {float(amount or 0):,.0f})")
    for row in rows:
        row["work"] = work.get(row["name"], [])
        row["provenance"] = "registered CRM profile"
    known_names = {_key(r["name"]) for r in rows}
    try:
        from ishti import crm_contract
        for record in crm_contract.list_clients():
            if record.get("scope") != "business_crm" or not record.get("contact_name"):
                continue
            name = record["contact_name"]
            rows.append({"name": name, "channel": record.get("channel_source", ""),
                         "contact": "", "work": [record["company"]] if record.get("company") else [],
                         "provenance": "registered contract CRM record " + record["client_id"]})
            known_names.add(_key(name))
    except Exception:
        swallowed("clients:roster_contract_unavailable")
    try:
        from ishti import rgraph
        for edge in rgraph.edges_of("me", direction="out") or []:
            if (edge.get("rel") != "client" or edge.get("s_name") != "me"
                    or edge.get("source_type") != "explicit_user"):
                continue
            name = (edge.get("d_name") or "").strip()
            if not name or _key(name) in known_names:
                continue
            rows.append({"name": name, "channel": "", "contact": "", "work": [],
                         "provenance": "explicit owner client relationship"})
            known_names.add(_key(name))
    except Exception:
        swallowed("clients:roster_relationships_unavailable")

    if not rows:
        return {"reply": "No clients in your CRM store yet. Say \"new client <name>\" or "
                         "\"Remember that <name> is my client at <company>\" to record one.",
                "sources": []}
    lines = []
    for r in rows:
        name = r["name"]
        bits = [b for b in (r.get("channel"), r.get("contact")) if b and b not in ("memory", "fact", "relationships")]
        proj_str = "; ".join(r.get("work", []))
        lines.append(f"- {name.title()}" + (f" ({', '.join(bits)})" if bits else "")
                     + (f" - work: {proj_str}" if proj_str else " - no project recorded yet")
                     + f" [{r['provenance']}]")
    owed = []
    with f.lock:
        owed = [f"{n.title()} {float(a or 0):,.0f}" for n, a in f.db.execute(
            "SELECT c.name, SUM(i.amount) FROM clients c JOIN client_invoices i ON i.client_id=c.id "
            "WHERE i.state IN ('due','overdue') GROUP BY c.name").fetchall()]
    tail = f"\nOutstanding: {', '.join(owed)}." if owed else ""
    return {"reply": f"{len(rows)} client(s) on file right now:\n" + "\n".join(lines) + tail,
            "sources": []}


DUE_WORK = re.compile(r"\bwhat\s+(?:client\s+work|work|deliverables?)\s+(?:do\s+i\s+have\s+)?due\s+(?:this\s+week|soon)\b", I)
INVOICE_DRAFT_OP = re.compile(r"\b(?:create|draft|prepare|make)\s+(?:an?\s+)?invoice\s+draft\s+for\s+(?:the\s+)?(?P<client>[\w\s.-]+?)\s+(?:work|project)?(?:\s+this\s+month)?\b", I)
SEND_INVOICE_OP = re.compile(r"\bsend\s+(?:the\s+)?invoice\s+to\s+(?:the\s+)?client\b", I)
PROJ_STATUS = re.compile(r"\b(?:what(?:'s|\s+is)\s+the\s+status\s+of\s+(?:the\s+)?(?P<proj>[\w\s.-]+?)\s+deployment|status\s+of\s+(?:the\s+)?khattak)\b", I)
FOLLOWUP_COND = re.compile(r"\bremind\s+me\s+to\s+follow\s*up\s+with\s+(?:this\s+)?client\s+if\s+they\s+haven'?t\s+replied\s+in\s+3\s+days\b", I)
ACTIVE_SUMMARY = re.compile(r"\bsummarize\s+(?:all\s+)?(?:my\s+)?active\s+client\s+projects\b", I)


def op_due_work(text, session):
    f = _db()
    today = dt.date.today()
    due = []
    with f.lock:
        rows = f.db.execute("SELECT c.name,e.body FROM client_events e JOIN clients c ON c.id=e.client_id "
                            "WHERE c.status='active' AND e.private=0 AND e.kind IN ('deadline','milestone')").fetchall()
    for name, body in rows:
        match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", body)
        if not match:
            continue
        try:
            day = dt.date.fromisoformat(match[1])
        except ValueError:
            continue
        if today <= day <= today + dt.timedelta(days=7):
            due.append(f"- {name.title()}: {body}")
    return {"reply": "Recorded client deadlines in the next 7 days:\n" + "\n".join(due) if due else
            "No active-client milestone with an explicit ISO due date is recorded for the next 7 days. "
            "Other work may still be due; no deadline was inferred.", "sources": []}


def op_invoice_draft_custom(text, session):
    match = re.search(r"invoice\s+draft\s+for\s+(?:the\s+)?(.+?)(?:\s+work|\s+project|\s+this\s+month|$)", text, I)
    name = match[1].strip() if match else ""
    ctx = context_load(name) if name else None
    if not ctx:
        return {"reply": "No unique registered client was selected. Specify the exact client before drafting an invoice.", "sources": []}
    lines = [f"Invoice draft for {ctx['client']['name'].title()}:"]
    for invoice in ctx["invoices"]:
        lines.append(f"- Recorded item #{invoice['id']}: {invoice['label']} — {invoice['amount']:,.2f} ({invoice['state']})")
    if not ctx["invoices"]:
        lines.append("- No invoice line items or amounts are recorded for this client.")
    lines.append("Review the invoice period, line items, rates, currency and payment terms before creating or sending a document.")
    return {"reply": "\n".join(lines), "sources": []}


def op_send_invoice_gate(text, session):
    return {"reply": "No verified invoice attachment, exact client recipient or CRM delivery transport "
                     "is selected. Nothing was sent. Prepare the invoice and use an approved channel send flow.",
            "sources": [], "ok": False, "delivery": "not_attempted"}


def op_proj_status(text, session):
    match = re.search(r"status\s+of\s+(?:the\s+)?(.+?)(?:\s+deployment|[?.!]|$)", text, I)
    project = match[1].strip() if match else ""
    f = _db()
    with f.lock:
        rows = f.db.execute("SELECT c.name,e.body FROM client_events e JOIN clients c ON c.id=e.client_id "
                            "WHERE e.private=0 AND e.kind IN ('project','deal','milestone') ORDER BY e.first_seen DESC").fetchall()
    hits = [(name, body) for name, body in rows if project and re.search(r"\b" + re.escape(project) + r"\b", body, I)]
    if not hits:
        return {"reply": f"No recorded project or deployment status for {project or 'the requested project'}. "
                         "Deployment and hardware verification have not been confirmed by stored evidence.", "sources": []}
    return {"reply": "Recorded project notes (current deployment is unverified):\n" +
            "\n".join(f"- {name.title()}: {body}" for name, body in hits[:10]), "sources": []}


def op_followup_cond(text, session):
    return {"reply": "No conditional follow-up was scheduled: an exact client, channel and reply-tracking "
                     "integration are required. A dated reminder can be created through the reminders flow.",
            "sources": [], "ok": False}


def op_active_summary(text, session):
    f = _db()
    with f.lock:
        rows = f.db.execute("SELECT c.name,e.body FROM client_events e JOIN clients c ON c.id=e.client_id "
                            "WHERE c.status='active' AND e.private=0 AND e.kind IN ('project','deal','milestone') "
                            "ORDER BY e.first_seen DESC").fetchall()
    if not rows:
        return {"reply": "No public project, deal or milestone entries are recorded for active clients. "
                         "No project activity or completion was inferred.", "sources": []}
    return {"reply": "Recorded active-client project notes:\n" +
            "\n".join(f"- {name.title()}: {body}" for name, body in rows[:20]), "sources": []}


def match(text):
    if DUE_WORK.search(text) or INVOICE_DRAFT_OP.search(text) or SEND_INVOICE_OP.search(text) \
       or PROJ_STATUS.search(text) or FOLLOWUP_COND.search(text) or ACTIVE_SUMMARY.search(text):
        return True
    # follow-up alone is reminders' territory; follow up WITH <name> (a person, not 'with the client')
    # is the CRM's: op_followup matches the client-name shape and returns None otherwise.
    return bool(CREATE.search(text) or LOGMSG.search(text) or CONTEXT.search(text) or DRAFT.search(text)
                or SEND.search(text) or FOLLOWUP.search(text)
                or DEAL.search(text) or (INVOICE.search(text) and not INVOICE_DOC.search(text))
                or NOTE.search(text) or LEAD.search(text) or DASH.search(text) or ROSTER.search(text))


OPS = (
    (DUE_WORK, op_due_work),
    (INVOICE_DRAFT_OP, op_invoice_draft_custom),
    (SEND_INVOICE_OP, op_send_invoice_gate),
    (PROJ_STATUS, op_proj_status),
    (FOLLOWUP_COND, op_followup_cond),
    (ACTIVE_SUMMARY, op_active_summary),
    (SEND, op_send), (CREATE, op_new), (LOGMSG, op_log), (ROSTER, op_client_roster),
    (DASH, op_dashboard), (DRAFT, op_draft),
    (LEAD, op_lead), (INVOICE, op_invoice), (DEAL, op_deal), (FOLLOWUP, op_followup),
    (NOTE, op_note), (CONTEXT, op_context)
)


def run(text, session):
    for rx, op in OPS:
        if rx.search(text):
            out = op(text, session)
            if out is not None:
                return out
    return None


if __name__ == "__main__":
    import tempfile
    import ishti.facts as F
    F._facts = F.Facts(os.path.join(tempfile.mkdtemp(), "t.db"))
    assert match("new client Ahmed via whatsapp")
    assert match("log client message from Ahmed: needs the logo by Friday")
    assert match("client context Ahmed")
    assert match("draft a reply to Ahmed")
    assert match("send the draft to Ahmed")
    assert match("follow up with Ahmed in 3 days")
    assert match("deal summary for Ahmed")
    assert match("invoice for Ahmed: logo design 15000")
    assert match("client note on Ahmed: always pays late")
    assert match("new inquiry from Sara: need a website")
    assert match("client dashboard")
    assert not match("what's the weather in Peshawar") and not match("play some music")
    # profile + isolation (rule 1)
    r = run("new client Ahmed via whatsapp, contact 0300-1234567", {})
    assert "Client created: Ahmed" in r["reply"], r
    run("client note on Ahmed: prefers whatsapp, pays on delivery", {})
    run("log client message from Ahmed: needs the logo by Friday", {})
    run("new client Bilal via fiverr", {})
    run("log client message from Bilal: website budget 50000", {})
    g1 = grounding_for("Ahmed")
    g2 = grounding_for("Bilal")
    assert "logo" in g1 and "Friday" in g1 and "Bilal" not in g1, g1   # no cross-client leakage
    assert "website" in g2 and "Ahmed" not in g2 and "logo" not in g2, g2
    # partial names: a UNIQUE prefix resolves to that client, ambiguous or unknown refuses (rule 1)
    run("new client Aliza Khan via email", {})
    run("log client message from Aliza Khan: teal landing page, 40000 budget", {})
    assert "teal" in grounding_for("Aliza"), grounding_for("Aliza")      # unique prefix -> her
    assert "Logo" not in grounding_for("Aliza"), grounding_for("Aliza")
    run("new client Aliza Ahmed via email", {})
    assert grounding_for("Aliza") is None                                 # ambiguous -> refuses
    assert grounding_for("Aliza Khan") is not None                        # exact still wins
    assert grounding_for("Logo") is None and grounding_for("Nope") is None
    r = run("client context Aliza", {})
    assert "No client named 'Aliza'" in r["reply"] and "Ahmed" in r["reply"], r
    # sentiment flagging: angry message routes to the owner, no draft
    r = run("log client message from Bilal: this delay is unacceptable and unprofessional", {})
    assert "yours to handle" in r["reply"], r
    r = run("draft a reply to Bilal", {})
    assert "not drafting" in r["reply"], r
    r = run("draft a reply to Ahmed", {})
    assert "check it" in r["reply"], r
    # send is always T2: a yes/no gate, never a direct send
    session = {"draft": "Hi Ahmed - the logo lands Friday.", "pending": None}
    r = run("send the draft to Ahmed", session)
    assert "(yes/no)" in r["reply"] and session.get("pending", {}).get("skill") == "clients", r
    session["pending"]["action"]()  # the owner says yes
    assert any(e["kind"] == "sent" for e in context_load("Ahmed")["events"])
    # invoices + dashboard
    run("invoice for Ahmed: logo design 15000", {})
    r = run("invoice status for Ahmed", {})
    assert "15,000".replace(",", "") in r["reply"].replace(",", "") and "due" in r["reply"], r
    r = run("client dashboard", {})
    assert "Ahmed" in r["reply"] and "Bilal" in r["reply"] and "ATTENTION" in r["reply"], r
    # lead intake
    r = run("new inquiry from Sara: I need a 5-page website for my shop", {})
    assert "Lead logged" in r["reply"] and "Sara" in r["reply"], r
    # 140: "Who are my clients right now?" is claimed here and answered from the REAL store every time -
    # never from a cached or remembered answer, and never from another skill's data.
    assert match("Who are my clients right now?") and match("list my clients")
    assert not match("what's the weather in Peshawar")
    r = run("Who are my clients right now?", {})
    assert "Ahmed" in r["reply"] and "Bilal" in r["reply"] and "Sara" in r["reply"], r
    assert "Kamran" not in r["reply"] and "brother" not in r["reply"], "no relationship-graph data leaks in"
    assert "logo design" in r["reply"], "each client is tied to the work actually stored"
    assert run("Who are my clients right now?", {})["reply"] == r["reply"], "same store read, same answer"
    run("new client Sana via email, contact sana@jq.ai", {})
    r2 = run("Who are my clients right now?", {})
    assert "Sana" in r2["reply"] and "Ahmed" in r2["reply"], "a new client shows up immediately"
    assert "sana@jq.ai" in r2["reply"], r2
    f = get_facts()
    with f.lock, f.db:
        f.db.execute("DELETE FROM clients")
    assert "No clients in your CRM store yet" in run("Who are my clients right now?", {})["reply"], \
        "an empty store says so instead of inventing clients"
    print("[self-check] clients OK - profiles, isolation (no cross-client leakage, partial names only when "
          "unambiguous), sentiment routing, draft grounding, T2 send gate, invoices, follow-ups, leads, dashboard, "
          "client roster read from the live store (140)")
