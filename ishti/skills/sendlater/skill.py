"""Send-later (inventory domain 12 #7): schedule an email to go out at a future time.

"send an email to ali@corp.com tomorrow at 9 saying report is ready",
"email boss@work.com on monday at 10 saying running late",
"schedule an email for the 5th saying rent receipt",
"list my scheduled emails", "cancel the scheduled email to ali".

Design (safety first): the queue reuses the reminders store (same table the reminders skill
fires from), marked task=1, with the task text prefixed "email:" so only this skill consumes
it. When the time comes, fire_due() hands the text to run_task; here that means: build the
email request and hand it to the email skill's normal flow - which is T2-gated. So a
scheduled email is NEVER sent while the owner is away, asleep or angry at us: the send
happens only after a live "yes". That is a deliberate limitation, not a missing feature.

Time parsing delegates to reminders.when() so "tomorrow at 9", "on monday at 10:30" and
"in 2 hours" behave exactly like every other reminder on this machine.
"""
import re
import time

from ishti.facts import get_facts
from ishti.skills import trace
from ishti.skills.reminders import skill as reminders

I = re.I

EMAIL = r"[\w.+-]+@[\w-]+\.[\w.-]+"
SAYING = r"(?:,?\s+(?:and\s+)?(?:saying|that\s+says|with\s+(?:the\s+)?message)\s+)"
# "email to X <when chunk> saying Y" - the when chunk is free text that reminders.when() reads
# ("tomorrow at 9", "on monday at 10:30", "in 2 hours", "on the 5th at noon"), so the parser
# stays in step with every other reminder on this machine.
QUEUE = re.compile(
    r"^\s*(?:send\s+(?:an?\s+)?|write\s+(?:an?\s+)?)?e-?mails?\s+(?:to\s+)?(?P<to>" + EMAIL + r")"
    r"(?P<when>[^,]*?)" + SAYING + r"(?P<body>.+)?$", I)
# the other word order: "send email tomorrow at 9 to X saying Y"
QUEUE2 = re.compile(r"^\s*(?:send|write)\s+(?:an?\s+)?e-?mails?\s+(?P<when2>tomorrow|today|tonight|"
                    r"(?:on\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
                    r"(?:\s+(?:morning|evening|afternoon))?|in\s+\d+\s*(?:minute|min|hour|hr|day)s?)"
                    r"(?:\s+at\s+(?P<at>\d{1,2}(?::\d{2})?\s*(?:am|pm)?))?"
                    r"\s+to\s+(?P<to>" + EMAIL + r")"
                    r"(?P<rest2>[^,]*?)" + SAYING + r"?(?P<body2>.+)?$", I)
LIST = re.compile(r"^\s*(?:list|show|what)\s+(?:my\s+)?scheduled\s+(?:e-?mails?|messages?|sends?)\b", I)
CANCEL = re.compile(r"^\s*cancel\s+(?:the\s+)?scheduled\s+e-?mails?\s+to\s+(?P<who>" + EMAIL + r")\s*$", I)
PREFIX = "email:"


def _db():
    f = get_facts()
    with f.lock, f.db:
        f.db.execute("CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY, due REAL, text TEXT, "
                     "created REAL, done INTEGER DEFAULT 0)")
        cols = {r[1] for r in f.db.execute("PRAGMA table_info(reminders)")}
        if "repeat" not in cols:
            f.db.execute("ALTER TABLE reminders ADD COLUMN repeat TEXT")
            f.db.execute("ALTER TABLE reminders ADD COLUMN task INTEGER DEFAULT 0")
    return f


def _when_text(m):
    """Build a phrasing reminders.when() understands from the regex groups."""
    if m.groupdict().get("when"):
        return f"{m.group('when')}{' at ' + m.group('at') if m.group('at') else ''}"
    day, at = m.groupdict().get("day"), m.groupdict().get("at")
    if day:
        return f"{day}{' at ' + at if at else ''}"
    return f"at {at}" if at else ""


def _parts(m, second=False):
    """(to, when-text, body) from either queue regex's groups."""
    if second:
        when = m.group("when2") + (f" at {m.group('at')}" if m.group("at") else "")
        rest = (m.group("rest2") or "").strip()
        if rest:
            when = (when + " " + rest).strip()
        return m.group("to"), when.strip(), (m.group("body2") or "").strip() or None
    return m.group("to"), (m.group("when") or "").strip(), (m.group("body") or "").strip() or None


def _queue(to, body, due_ts, source=""):
    f = _db()
    text = f"{PREFIX} to {to} saying {body}"
    with f.lock, f.db:
        f.db.execute("INSERT INTO reminders(due, text, created, repeat, task) VALUES(?,?,?,NULL,1)",
                     (due_ts, text, time.time()))
    return text


def pending():
    rows = _db().db.execute(
        "SELECT id, due, text FROM reminders WHERE done=0 AND task=1 AND text LIKE ? ORDER BY due",
        (PREFIX + "%",)).fetchall()
    return rows


def match(text):
    t = text.strip()
    if LIST.match(t) or CANCEL.match(t):
        return True
    m = QUEUE.match(t)
    if m:
        to, when, body = _parts(m)
        return bool(when)              # no time given = the email skill's send-now
    m = QUEUE2.match(t)
    if m:
        to, when, body = _parts(m, second=True)
        return bool(when)
    return False


def run(text, session, now=None):
    import datetime as dt
    now = now or dt.datetime.now()
    t = text.strip()
    if LIST.match(t):
        rows = pending()
        if not rows:
            return {"reply": "No scheduled emails.", "sources": []}
        lines = [f"{i}. {reminders._fmt(d, now)}: {x}" for i, (_id, d, x) in enumerate(rows, 1)]
        return {"reply": "Scheduled emails (each asks for your yes at send time):\n" + "\n".join(lines),
                "sources": []}
    if (m := CANCEL.match(t)):
        who = m.group("who").lower()
        f = _db()
        rows = [r for r in pending() if who in r[2].lower()]
        with f.lock, f.db:
            f.db.executemany("UPDATE reminders SET done=1 WHERE id=?", [(r[0],) for r in rows])
        return {"reply": (f"Cancelled {len(rows)} scheduled email(s) to {who}."
                          if rows else f"No scheduled email to {who}."), "sources": []}
    m = QUEUE.match(t)
    second = False
    if not m:
        m, second = QUEUE2.match(t), True
    if not m:
        return None
    trace(session, "sendlater", "queue")
    to, when_txt, body = _parts(m, second)
    if not body:
        return {"reply": f"What should the email to {to} say? Add 'saying ...' and I'll schedule it.",
                "sources": []}
    due = reminders.when(f"remind me {when_txt} to x", now) if when_txt else None
    if due is None:
        return {"reply": "When should it go out? Say e.g. 'email ali@corp.com tomorrow at 9 saying ...'.",
                "sources": []}
    if due <= now:
        return {"reply": f"That time ({due:%H:%M}) has already passed.", "sources": []}
    text = _queue(to, body, due.timestamp())
    return {"reply": (f"Scheduled email to **{to}** at {reminders._fmt(due.timestamp(), now)}: "
                      f"\u201c{body}\u201d.\nAt that time I'll prepare it and ask for your yes "
                      f"(nothing sends without you)."),
            "sources": []}


def build_task_request(stored_text):
    """stored 'email: to X saying Y' -> the email skill's phrasing, for fire-time."""
    body = stored_text[len(PREFIX):].strip()
    return body


if __name__ == "__main__":
    import datetime as dt
    import os
    import tempfile
    import ishti.facts as F
    F._facts = F.Facts(os.path.join(tempfile.mkdtemp(), "t.db"))
    now = dt.datetime(2026, 9, 28, 14, 0)

    assert match("send an email to ali@corp.com tomorrow at 9 saying report is ready")
    assert match("send email to boss@work.com on monday at 10:30 saying running late")
    assert match("email team@x.org tomorrow saying the build broke")
    assert match("send an email tomorrow at 9 to me@x.com saying hi")
    assert match("list my scheduled emails")
    assert match("cancel the scheduled email to ali@corp.com")
    assert not match("send an email to ali@corp.com saying hi")      # no time: the email skill's send-now
    assert not match("email team@x.org saying hi")                   # same
    assert not match("check my email")                                # email skill's
    assert not match("what does an email look like")                  # chat's

    r = run("send an email to ali@corp.com tomorrow at 9 saying report is ready", {}, now)
    assert "ali@corp.com" in r["reply"] and "tomorrow" in r["reply"] and "report is ready" in r["reply"], r
    rows = pending()
    assert len(rows) == 1 and rows[0][2].startswith("email: to ali@corp.com"), rows
    # the stored task is what reminders.fire_due will hand back at due time
    req = build_task_request(rows[0][2])
    assert "to ali@corp.com" in req and "report is ready" in req, req
    # list and cancel
    r2 = run("list my scheduled emails", {}, now)
    assert "ali@corp.com" in r2["reply"] and "your yes" in r2["reply"], r2
    r3 = run("cancel the scheduled email to ali@corp.com", {}, now)
    assert "Cancelled 1" in r3["reply"], r3
    assert pending() == []
    r4 = run("cancel the scheduled email to nobody@x.com", {}, now)
    assert "No scheduled email" in r4["reply"], r4
    # a schedule without a message body is not parseable here: the email skill's
    # conversational flow takes it and asks what to say
    assert not match("email team@x.org tomorrow saying")
    print("[self-check] sendlater OK - queue/list/cancel, reuses the reminders store (task=1), "
          "fire-time T2, nothing sends without the owner's yes")
