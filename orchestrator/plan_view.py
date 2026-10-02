"""Local plan review page (WP-ECC, matrix ECC-08; ECC's plan-canvas, browser-only).

Renders the plan documents (every .md in data/plans/, or the names passed in) as one local page with
Approve / Request-changes buttons; decisions append to data/plan_decisions.json.

Security posture (matrix ECC-08: "local only (127.0.0.1)"):
  * serve() binds LOOPBACK ONLY - any other host is refused before a socket opens
  * markdown is HTML-escaped first, then minimally decorated: a plan file can never inject
    markup into the review page
  * POST bodies are capped (64 KB) and strictly validated; anything else is a 400
  * no external assets, no CDN, no network calls from the page

CLI: python -m orchestrator.plan_view [port]
Self-check: python orchestrator/plan_view.py
"""
from __future__ import annotations

import html
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOOPBACK = ("127.0.0.1", "localhost", "::1")
PLANS_DIR = os.path.join("data", "plans")  # plan documents to review live here, not among the repo's own docs
DECISION_KINDS = ("approve", "request-changes")
MAX_BODY = 64 * 1024
MAX_DECISIONS = 500


def default_docs(root: str = ROOT) -> tuple:
    """Every .md file in <root>/data/plans (sorted) - the plans waiting for the owner's decision."""
    try:
        names = sorted(n for n in os.listdir(os.path.join(root, PLANS_DIR)) if n.lower().endswith(".md"))
    except OSError:
        names = []
    return tuple(os.path.join(PLANS_DIR, n).replace("\\", "/") for n in names)


def decisions_path(root: str = ROOT) -> str:
    return os.path.join(root, "data", "plan_decisions.json")


# --------------------------------------------------------------------- rendering

def _md_to_html(text: str) -> str:
    """Escape FIRST, then decorate: headings, bullets, bold, tables collapse to <pre>."""
    safe = html.escape(text or "")
    out = []
    for line in safe.splitlines():
        if line.startswith("### "):
            out.append(f"<h4>{line[4:]}</h4>")
        elif line.startswith("## "):
            out.append(f"<h3>{line[3:]}</h3>")
        elif line.startswith("# "):
            out.append(f"<h2>{line[2:]}</h2>")
        elif re.match(r"^\s*[-*]\s+", line):
            item = re.sub(r"^\s*[-*]\s+", "", line)
            out.append("<div class='li'>" + item + "</div>")
        elif line.startswith("|"):
            out.append(f"<div class='row'>{line}</div>")
        elif line.strip():
            out.append(f"<p>{line}</p>")
    return "\n".join(out)


def render(root: str = ROOT, plans=None) -> str:
    """The review page. Total: a missing plan doc is shown as a note, never an error."""
    docs = tuple(plans) if plans else default_docs(root)
    sections, nav = [], []
    if not docs:
        sections.append("<section class='doc'><div class='body'><p>No plan documents yet - put them in "
                        "data/plans/ and reload.</p></div></section>")
    for name in docs:
        path = os.path.join(root, name)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                body = f.read()
            nav.append(f"<a href='#{html.escape(name)}'>{html.escape(name)}</a>")
        except OSError:
            body = f"({name} not found)"
        sections.append(f"""<section id="{html.escape(name)}" class="doc">
  <div class="bar">
    <h1>{html.escape(name)}</h1>
    <button data-doc="{html.escape(name)}" data-decision="approve">Approve</button>
    <button class="warn" data-doc="{html.escape(name)}" data-decision="request-changes">Request changes</button>
  </div>
  <div class="body">{_md_to_html(body)}</div>
</section>""")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>ISHTI plan review</title>
<style>
 body{{font:14px/1.5 system-ui,Segoe UI,sans-serif;margin:0;background:#101418;color:#d8e0e8}}
 header{{position:sticky;top:0;background:#161c22;padding:10px 16px;border-bottom:1px solid #2a333c}}
 header a{{color:#6cc7bd;margin-right:14px;text-decoration:none}}
 .doc{{margin:18px;border:1px solid #2a333c;border-radius:8px;background:#141a20}}
 .bar{{display:flex;align-items:center;gap:10px;padding:8px 14px;border-bottom:1px solid #2a333c}}
 .bar h1{{font-size:16px;margin:0;flex:1}}
 button{{background:#1f6f68;color:#fff;border:0;border-radius:6px;padding:6px 12px;cursor:pointer}}
 button.warn{{background:#8a5a1f}}
 .body{{padding:10px 16px;max-height:60vh;overflow:auto}}
 .row,.li{{font-size:12px;color:#9fb0bf;white-space:pre-wrap}}
 h2,h3,h4{{color:#e8eef4}}
 #status{{position:fixed;right:16px;bottom:16px;background:#1f6f68;padding:8px 12px;border-radius:6px;opacity:0;transition:.2s}}
 #status.show{{opacity:1}}
</style></head><body>
<header>{' '.join(nav)}<span style="color:#7b8b99">local only - decisions go to data/plan_decisions.json</span></header>
{''.join(sections)}
<div id="status"></div>
<script>
async function decide(doc, decision) {{
  const note = decision === "request-changes" ? (prompt("What should change?") || "") : "";
  const r = await fetch("/decision", {{method:"POST", headers:{{"Content-Type":"application/json"}},
    body: JSON.stringify({{doc, decision, note}})}});
  const box = document.getElementById("status");
  box.textContent = r.ok ? decision + " recorded for " + doc : "not recorded (" + r.status + ")";
  box.className = "show"; setTimeout(() => box.className = "", 2500);
}}
document.querySelectorAll("button[data-decision]").forEach(b =>
  b.addEventListener("click", () => decide(b.dataset.doc, b.dataset.decision)));
</script></body></html>"""


# --------------------------------------------------------------------- decisions

def load_decisions(path: str | None = None) -> list:
    try:
        with open(path or decisions_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def record_decision(payload, path: str | None = None) -> dict | None:
    """Validate + append one decision (bounded history). None when invalid; never raises."""
    try:
        if not isinstance(payload, dict):
            return None
        kind = str(payload.get("decision") or "").strip().lower()
        doc = str(payload.get("doc") or "").strip()[:200]
        note = str(payload.get("note") or "")[:500]
        if kind not in DECISION_KINDS or not doc:
            return None
        path = path or decisions_path()
        rows = load_decisions(path)
        row = {"ts": time.time(), "doc": doc, "decision": kind, "note": note}
        rows.append(row)
        rows = rows[-MAX_DECISIONS:]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2)
        return row
    except Exception:
        return None


# --------------------------------------------------------------------- server

class _Handler(BaseHTTPRequestHandler):
    server_version = "ISHTIPlanView/1.0"

    def log_message(self, *args):        # quiet: no request logging
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/decisions"):
            body = json.dumps(load_decisions(self.server.decisions_file)).encode()
            self._send(200, body, "application/json")
        elif self.path in ("/", "/index.html"):
            self._send(200, self.server.page_html.encode("utf-8"), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path != "/decision":
            self._send(404, b"not found", "text/plain")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            self._send(400, b"bad length", "text/plain")
            return
        if length > MAX_BODY:
            try:                                  # drain, so the client sees the 413
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
            except Exception as _exc:
                swallowed('orchestrator/plan_view.py:_Handler.do_POST', _exc)
            self._send(413, b"body too large", "text/plain")
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8", "replace"))
        except Exception:
            self._send(400, b"body must be JSON", "text/plain")
            return
        row = record_decision(payload, self.server.decisions_file)
        if row is None:
            self._send(400, b"decision must be approve|request-changes with a doc", "text/plain")
            return
        self._send(200, json.dumps(row).encode(), "application/json")


def serve(root: str = ROOT, host: str = "127.0.0.1", port: int = 0, plans=None,
          decisions_file: str | None = None) -> dict:
    """Start the loopback-only review server in a daemon thread.
    Returns {"ok", "url", "server", "thread"} or {"ok": False, "error"} - never raises."""
    try:
        if str(host) not in LOOPBACK:
            return {"ok": False, "error": f"refused: {host} is not loopback (the plan view is local only)"}
        httpd = ThreadingHTTPServer((host, int(port)), _Handler)
        httpd.page_html = render(root, plans)
        httpd.decisions_file = decisions_file or decisions_path(root)
        th = threading.Thread(target=httpd.serve_forever, daemon=True)
        th.start()
        shown = "127.0.0.1" if str(host) in ("localhost", "::1") else str(host)
        return {"ok": True, "server": httpd, "thread": th,
                "url": f"http://{shown}:{httpd.server_address[1]}/"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def stop(handle) -> bool:
    """Shut a serve() result down cleanly."""
    try:
        handle["server"].shutdown()
        handle["server"].server_close()
        return True
    except Exception:
        return False


def _self_check() -> None:
    import tempfile
    plan = "# Plan\n\n- step one\n- step two\n\n<script>alert(1)</script>\n"
    work = tempfile.mkdtemp()
    with open(os.path.join(work, "demo_plan.md"), "w", encoding="utf-8") as f:
        f.write(plan)
    page = render(work, plans=("demo_plan.md",))
    assert "Approve" in page and "Request changes" in page and "step one" in page
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page, "markup is escaped"
    dec = os.path.join(work, "data", "plan_decisions.json")
    assert record_decision({"doc": "demo_plan.md", "decision": "approve"}, dec)
    assert record_decision({"doc": "x", "decision": "request-changes", "note": "fix scope"}, dec)
    assert record_decision({"doc": "x", "decision": "maybe"}, dec) is None
    assert record_decision("junk", dec) is None and record_decision(None, dec) is None
    assert len(load_decisions(dec)) == 2 and load_decisions(dec)[1]["note"] == "fix scope"
    assert record_decision({"doc": "x" * 999, "decision": "approve"}, dec)
    assert len(load_decisions(dec)[-1]["doc"]) == 200, "doc bounded"
    assert len(load_decisions(os.path.join(work, "missing.json"))) == 0, "missing history is []"
    refused = serve(work, host="0.0.0.0")
    assert refused["ok"] is False and "loopback" in refused["error"]
    h = serve(work, plans=("demo_plan.md",), decisions_file=dec)
    assert h["ok"] and h["url"].startswith("http://127.0.0.1:")
    import urllib.request
    base = h["url"].rstrip("/")
    with urllib.request.urlopen(base + "/", timeout=5) as r:
        body = r.read().decode("utf-8")
    assert r.status == 200 and "Approve" in body
    req = urllib.request.Request(base + "/decision",
                                 data=json.dumps({"doc": "demo_plan.md",
                                                  "decision": "approve"}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200 and json.loads(r.read())["decision"] == "approve"
    assert len(load_decisions(dec)) == 4
    assert stop(h)
    print("[self-check] plan_view OK - escaped local page, approve/request-changes decisions "
          "(bounded), loopback-only server refused on 0.0.0.0")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        handle = serve(port=int(sys.argv[2]) if len(sys.argv) > 2 else 8765)
        print(handle.get("url") or handle.get("error"))
        if handle.get("ok"):
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                stop(handle)
    else:
        _self_check()
