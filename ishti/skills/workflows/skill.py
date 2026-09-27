"""Named webhooks: "run my backup workflow" POSTs to a URL the owner saved - an n8n workflow, a Home Assistant
automation, Make/Zapier, anything that starts on a webhook. One generic caller instead of a client per service.
Every run asks first (T2): a workflow can do anything. Local hosts (n8n on :5678, Home Assistant on :8123) pass the
scope check; a remote host has to be added to this skill's manifest "net" on purpose."""
import re

import requests

from ishti.facts import get_facts
from ishti.skills import ask_first, md_table, trace

I = re.I
ADD = re.compile(r"\b(?:add|save|create|register)\s+(?:a\s+)?(?:webhook|workflow)\s+(?:called\s+|named\s+)?[\"']?(?P<name>[\w .-]+?)[\"']?"
                 r"\s*(?:=|:|->|at|to|with\s+url|url)?\s+(?P<url>https?://\S+)\s*$", I)
LIST = re.compile(r"\b(?:list|show|what\s+are)\s+(?:my\s+|the\s+)?(?:webhooks|workflows|n8n\s+workflows)\b", I)
DELETE = re.compile(r"\b(?:delete|remove|forget)\s+(?:the\s+)?(?:webhook|workflow)\s+[\"']?(?P<name>[\w .-]+?)[\"']?\s*$|"
                    r"\b(?:delete|remove|forget)\s+(?:the\s+)?[\"']?(?P<name2>[\w .-]+?)[\"']?\s+(?:webhook|workflow)\s*$", I)
RUN = re.compile(r"\b(?:run|trigger|start|fire|call)\s+(?:the\s+|my\s+)?(?:n8n\s+)?(?:(?:webhook|workflow)\s+)?[\"']?(?P<name>[\w .-]+?)[\"']?"
                 r"(?:\s+(?:n8n\s+)?(?:webhook|workflow))?\s*$", I)


def _db():
    f = get_facts()
    with f.lock, f.db:
        f.db.execute("CREATE TABLE IF NOT EXISTS webhooks(name TEXT PRIMARY KEY, url TEXT, created REAL)")
    return f


def hooks():
    f = _db()
    with f.lock:
        return dict(f.db.execute("SELECT name, url FROM webhooks ORDER BY name").fetchall())


def _named(text):
    """The saved webhook a "run ..." request names ("run my backup workflow" -> backup)."""
    m = RUN.search(text)
    if not m:
        return None
    said = m["name"].strip().lower()
    return next((n for n in hooks() if n.lower() == said), None)


def match(text):
    return bool(ADD.search(text) or LIST.search(text) or DELETE.search(text) or
                (RUN.search(text) and (re.search(r"\b(?:webhook|workflow|n8n)\b", text, I) or _named(text))))


def fire(name, url, text):
    r = requests.post(url, json={"from": "ISHTI", "workflow": name, "request": text}, timeout=30)
    body = r.text.strip()[:300]
    if not r.ok:
        return f"The {name} workflow answered HTTP {r.status_code}" + (f": {body}" if body else ".")
    return f"Ran the {name} workflow (HTTP {r.status_code})." + (f"\n\nIt replied: {body}" if body else "")


def run(text, session):
    f = _db()
    if m := ADD.search(text):
        name, url = m["name"].strip().lower(), m["url"].rstrip(".,)")
        with f.lock, f.db:
            f.db.execute("INSERT OR REPLACE INTO webhooks VALUES (?, ?, strftime('%s','now'))", (name, url))
        return {"reply": f"Saved the {name} workflow ({url}). Say \"run {name} workflow\" - I'll ask before each run.", "sources": []}
    if LIST.search(text):
        h = hooks()
        if not h:
            return {"reply": "No workflows saved yet. Add one: \"add webhook backup http://localhost:5678/webhook/backup\" "
                             "(an n8n Webhook node, a Home Assistant webhook trigger, Make, Zapier...).", "sources": []}
        return {"reply": md_table(["Workflow", "URL"], [[n, u] for n, u in h.items()]), "sources": []}
    if m := DELETE.search(text):
        name = (m["name"] or m["name2"]).strip().lower()
        with f.lock, f.db:
            gone = f.db.execute("DELETE FROM webhooks WHERE lower(name) = ?", (name,)).rowcount
        return {"reply": f"Removed the {name} workflow." if gone else f"There's no workflow called {name}.", "sources": []}
    name = _named(text)
    if not name:
        said = RUN.search(text)["name"].strip() if RUN.search(text) else "that"
        known = ", ".join(hooks()) or "none yet"
        return {"reply": f"I don't have a workflow called {said}. Saved: {known}.", "sources": []}
    url = hooks()[name]
    trace(session, "webhook", f"{name} -> {url}", status="start")
    return ask_first(session, "workflows", f"Run the {name} workflow ({url})", lambda: fire(name, url, text))


if __name__ == "__main__":
    import http.server
    import tempfile
    import threading

    import os
    import ishti.facts as F
    F._facts = F.Facts(os.path.join(tempfile.mkdtemp(), "t.db"))
    got = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            got.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/webhook/test"
    s = {}
    assert "Saved the selfcheck demo" in run(f"add webhook selfcheck demo {url}", s)["reply"]
    for t in ["run selfcheck demo", "run the selfcheck demo workflow", "trigger my n8n workflow selfcheck demo", "list my workflows"]:
        assert match(t), t
    for t in ["run the tests", "run my script backup.py", "start chrome", "what's a workflow?"]:
        assert not match(t), t
    out = run("run the selfcheck demo workflow", s)
    assert out["reply"].endswith("(yes/no)") and not got, out  # nothing sent before the yes
    assert "HTTP 200" in s["pending"]["action"]() and b'"workflow": "selfcheck demo"' in got[0]
    assert "don't have a workflow called nope" in run("run nope workflow", {})["reply"]
    assert "Removed" in run("delete webhook selfcheck demo", {})["reply"] and "selfcheck demo" not in hooks()
    srv.shutdown()
    print("[self-check] workflows OK - add/list/delete, asks before running, posts JSON to the webhook")
