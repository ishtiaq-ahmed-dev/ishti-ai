
"""Adversarial live-UI checks that the per-task sweep cannot cover.

Each check drives the real dashboard in real Chrome and independently probes the backend
(HTTP API or storage) to decide whether the UI told the truth:

  stale   - a reminder created through the backend while the Tasks page is open: does the page
            reflect it without a manual reload? (live-update staleness)
  arch    - Settings > Architecture switch: does the badge/state update live, and does the
            backend really change engine?
  multitab- second tab on the same session, first tab backgrounded: does the second tab see the
            same turns, and does the busy tab still finish its stream?
  pages   - every nav destination renders without a console error or blank body
  dead    - static audit: every /api route in server.py vs. its callers in the built bundle
            (a capability the UI never calls is a dead/unintegrated feature)

Usage:
    venv\\Scripts\\python.exe tools\\qa\\live_ui_checks.py --url http://127.0.0.1:8888/ --out reports/live_ui_checks.json --shots --headed
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import random
import re
import sys
import time
import urllib.request


def _swallow(where: str, exc: BaseException) -> None:
    """Log a handled-but-ignored failure through the project logger (repo hygiene rule)."""
    try:
        from ishti.core.logutil import swallowed
        swallowed(where, exc)
    except Exception:  # noqa: BLE001 - logging must never break the QA run
        print(f"[qa] swallowed {where}: {type(exc).__name__}: {exc}")


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

EVIDENCE = os.path.join(ROOT, "docs", "build", "evidence", "p9_live")
SERVER_PY = os.path.join(ROOT, "interface", "web_ui", "server.py")
DIST = os.path.join(ROOT, "interface", "app", "dist")
QA_DATA = os.path.join(ROOT, "_work", "qa_p9", "data")

ASSISTANT = "div.flex.gap-3.min-w-0.max-w-full"
INPUT = 'textarea[placeholder*="Ask or tell ISHTI"], textarea[placeholder*="Assign a task"]'

INIT_JS = r"""
window.__qaErrors = [];
window.addEventListener('error', e => window.__qaErrors.push(String(e.message || e)));
window.addEventListener('unhandledrejection', e => window.__qaErrors.push('rejection: ' + String(e.reason)));
window.__ws = {opened:0, closed:0, messages:0, lastAt:0, types:[]};
(function () {
  const OW = window.WebSocket;
  window.WebSocket = function (...a) {
    const ws = new OW(...a);
    window.__ws.opened++;
    ws.addEventListener('message', (e) => {
      window.__ws.messages++; window.__ws.lastAt = Date.now();
      if (window.__ws.types.length < 30) {
        const s = String(e.data || '');
        const t = (s.match(/"(?:type|event)"\s*:\s*"([^"]+)"/) || [null, s.slice(0, 60)])[1];
        window.__ws.types.push(t);
      }
    });
    ws.addEventListener('close', () => { window.__ws.closed++; });
    return ws;
  };
  window.WebSocket.prototype = OW.prototype;
})();
"""


def log(m: str) -> None:
    print(f"  {m}", flush=True)


def api(path: str, payload: dict | None = None, url: str = "http://127.0.0.1:8888") -> dict:
    req = urllib.request.Request(url.rstrip("/") + path, method="POST" if payload is not None else "GET")
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=body, timeout=30) as r:
            raw = r.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw.strip().startswith(("{", "[")) else {"raw": raw}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------------- checks
def check_stale(page, url: str, result: dict) -> None:
    """Backend creates a reminder while the Tasks page is open; the page must show it without reload."""
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(2000)
    try:
        page.get_by_role("button", name="Tasks", exact=True).first.click(timeout=6000)
        page.wait_for_timeout(2000)
    except Exception as exc:
        _swallow("tools/qa/live_ui_checks.py:106", exc)
    marker = "water the plants"
    before = page.evaluate("() => document.body.innerText")
    result["stale_view_before"] = marker in before
    # create the reminder through the backend, not through this page
    resp = api("/api/execute", {"prompt": "Remind me to water the plants in 45 minutes", "session_id": ""}, url)
    result["stale_api_reply"] = str(resp.get("final_answer") or resp.get("error") or "")[:200]
    page.wait_for_timeout(4000)
    after = page.evaluate("() => document.body.innerText")
    result["stale_view_after"] = marker in after
    result["stale_needs_reload"] = not result["stale_view_after"]


LABELS = {"arch1": "ARCH 1", "arch2": "ARCH 2"}
CLICK_BACKEND_JS = """(label) => {
  const btns = Array.from(document.querySelectorAll('button'));
  const hit = btns.find(b => (b.innerText || '').trim().startsWith(label));
  if (!hit) return '';
  hit.click();
  return (hit.innerText || '').trim().slice(0, 70);
}"""


def check_arch(page, url: str, result: dict) -> None:
    """Switch engine by clicking the Architecture card in Settings: backend + UI must both follow."""
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(2000)
    try:
        page.get_by_role("button", name="Settings", exact=True).first.click(timeout=6000)
        page.wait_for_timeout(2000)
    except Exception as exc:
        _swallow("tools/qa/live_ui_checks.py:137", exc)
    state_before = api("/api/arch", None, url)
    result["arch_before"] = state_before.get("active")
    target = "arch1" if state_before.get("active") == "arch2" else "arch2"
    label = LABELS[target]
    # expand the card, then click the backend tile that is NOT active
    try:
        page.get_by_role("button").filter(has_text="Architecture").first.click(timeout=6000)
        page.wait_for_timeout(900)
        result["arch_clicked_tile"] = page.evaluate(CLICK_BACKEND_JS, label)
    except Exception as exc:  # noqa: BLE001
        result["arch_click_error"] = f"{type(exc).__name__}: {exc}"[:200]
    # live: does the card header (and the rest of the page) follow without a reload?
    t0 = time.time()
    shown = False
    while time.time() - t0 < 20:
        try:
            txt = page.evaluate("() => document.body.innerText") or ""
            if f"Active: {label}" in txt:
                shown = True
                break
        except Exception as exc:  # noqa: BLE001
            _swallow("tools/qa/live_ui_checks.py:148", exc)
        time.sleep(0.5)
    result["arch_ui_active_after_s"] = round(time.time() - t0, 1)
    result["arch_shown_live"] = shown
    result["arch_after"] = api("/api/arch", None, url).get("active")
    # put the original engine back through the same card
    back = LABELS[state_before.get("active") or "arch2"]
    try:
        page.evaluate(CLICK_BACKEND_JS, back)
        page.wait_for_timeout(2500)
    except Exception as exc:  # noqa: BLE001
        result["arch_restore_error"] = f"{type(exc).__name__}: {exc}"[:160]
    result["arch_restored"] = api("/api/arch", None, url).get("active")


def _open_chat(tab) -> None:
    """The app restores its last view, so make sure the chat box exists before typing."""
    for _ in range(3):
        if tab.locator(INPUT).count():
            return
        try:
            tab.get_by_role("button", name="Chat", exact=True).first.click(timeout=6000)
        except Exception as exc:  # noqa: BLE001
            _swallow("tools/qa/live_ui_checks.py:171", exc)
        tab.wait_for_timeout(1200)


def check_multitab(ctx, url: str, result: dict) -> None:
    """Two tabs on the same session; tab 1 backgrounded while busy."""
    tab1 = ctx.new_page()
    tab1.goto(url, wait_until="domcontentloaded")
    tab1.wait_for_timeout(1500)
    _open_chat(tab1)
    tab2 = ctx.new_page()
    tab2.goto(url, wait_until="domcontentloaded")
    tab2.wait_for_timeout(1500)
    _open_chat(tab2)
    result["multitab_ws_tab2"] = tab2.evaluate("() => window.__ws")
    # a fresh product each run, so an earlier turn in the same session cannot fake the answer
    a, b = random.choice([13, 23, 29, 37]), random.choice([7, 11, 19])
    answer = str(a * b)
    question = f"What is {a} multiplied by {b}? Reply with the number only."
    result["multitab_question"] = question
    result["multitab_expected"] = answer
    # send from tab1, then immediately bring tab2 to front and wait there
    box = tab1.locator(INPUT).last
    box.click(timeout=10000)
    box.fill(question)
    box.press("Enter")
    tab2.bring_to_front()
    t0 = time.time()
    arrived = False
    while time.time() - t0 < 90:
        try:
            t1 = tab1.evaluate("() => document.body.innerText") or ""
            if answer in t1:
                arrived = True
                break
        except Exception as exc:  # noqa: BLE001
            _swallow("tools/qa/live_ui_checks.py:207", exc)
        time.sleep(0.5)
    result["multitab_backgrounded_reply_arrived"] = arrived
    result["multitab_wait_s"] = round(time.time() - t0, 1)
    try:
        result["multitab_tab2_ws_after"] = tab2.evaluate("() => window.__ws")
    except Exception as exc:  # noqa: BLE001
        _swallow("tools/qa/live_ui_checks.py:214", exc)
    # both tabs are the same conversation: how long until the front tab mirrors the turn?
    t1 = time.time()
    mirrored_q = mirrored_a = False
    while time.time() - t1 < 30:
        try:
            t2 = tab2.evaluate("() => document.body.innerText") or ""
            mirrored_q = f"{a} multiplied by {b}" in t2
            mirrored_a = answer in t2
            if mirrored_q and mirrored_a:
                break
        except Exception as exc:  # noqa: BLE001
            _swallow("tools/qa/live_ui_checks.py:226", exc)
        time.sleep(0.5)
    result["multitab_tab2_shows_question"] = mirrored_q
    result["multitab_tab2_shows_answer"] = mirrored_a
    result["multitab_tab2_mirror_s"] = round(time.time() - t1, 1)
    try:
        t1 = tab1.evaluate("() => document.body.innerText") or ""
        result["multitab_tab1_shows_answer"] = answer in t1
    except Exception as exc:  # noqa: BLE001
        _swallow("tools/qa/live_ui_checks.py:235", exc)
    # a reload is the only way the second tab can pick the turn up: record whether it is stale
    try:
        tab2.reload(wait_until="domcontentloaded")
        tab2.wait_for_timeout(3000)
        _open_chat(tab2)
        tab2.wait_for_timeout(1500)
        t2r = tab2.evaluate("() => document.body.innerText") or ""
        result["multitab_tab2_after_reload_shows_answer"] = answer in t2r
    except Exception as exc:  # noqa: BLE001
        result["multitab_tab2_reload_error"] = f"{type(exc).__name__}: {exc}"[:160]
    tab2.close()
    tab1.close()


def check_pages(ctx, url: str, shots: bool, result: dict) -> None:
    """Every nav destination must render real content and log no console error."""
    page = ctx.new_page()
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(2000)
    nav = ["Chat", "Overview", "Inputs", "Connectors", "Tasks", "Learning", "Skills", "Settings"]
    out = {}
    for name in nav:
        errs_before = page.evaluate("() => window.__qaErrors.length")
        try:
            page.get_by_role("button", name=name, exact=True).first.click(timeout=6000)
        except Exception:  # noqa: BLE001
            out[name] = {"clicked": False}
            continue
        page.wait_for_timeout(2200)
        text = page.evaluate("() => document.body.innerText") or ""
        errs = page.evaluate("() => window.__qaErrors.slice()")
        out[name] = {
            "clicked": True,
            "chars": len(text.strip()),
            "blank": len(text.strip()) < 120,
            "new_errors": [e for e in errs[int(errs_before):]] if isinstance(errs_before, int) else errs,
            "has_error_text": bool(re.search(r"(failed to fetch|uncaught|is not defined|cannot read|500)", text, re.I)),
        }
        if shots:
            try:
                page.screenshot(path=os.path.join(EVIDENCE, f"page_{name.lower()}.png"), timeout=8000)
            except Exception as exc:  # noqa: BLE001
                _swallow("tools/qa/live_ui_checks.py:278", exc)
    result["pages"] = out
    page.close()


def check_dead_features(result: dict) -> None:
    """Static: every API route vs. its callers in the built bundle. Uncalled + no UI = dead capability."""
    try:
        src = io.open(SERVER_PY, encoding="utf-8").read()
    except OSError as exc:
        result["dead_check_error"] = str(exc)
        return
    routes = re.findall(r'@app\.(?:get|post|put|delete|websocket)\("([^"]+)"', src)
    bundle = ""
    for dirpath, _dirs, files in os.walk(DIST):
        for fn in files:
            if fn.endswith((".js", ".mjs")):
                try:
                    bundle += io.open(os.path.join(dirpath, fn), encoding="utf-8", errors="replace").read()
                except OSError as exc:
                    _swallow("tools/qa/live_ui_checks.py:309", exc)
    uncalled = []
    for r in routes:
        base = r.split("{")[0].rstrip("/")
        if not base or r in ("/", "/hud"):
            continue
        if base not in bundle and r not in bundle:
            uncalled.append(r)
    result["api_routes_total"] = len(routes)
    result["api_routes_without_ui_caller"] = uncalled


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8888/")
    ap.add_argument("--out", default=os.path.join(ROOT, "reports", "live_ui_checks.json"))
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--shots", action="store_true")
    ap.add_argument("--only", default="stale,arch,multitab,pages,dead")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception as exc:  # noqa: BLE001
        _swallow("tools/qa/live_ui_checks.py:321", exc)
    os.makedirs(EVIDENCE, exist_ok=True)
    from playwright.sync_api import sync_playwright

    want = {x.strip() for x in args.only.split(",") if x.strip()}
    result: dict = {"generated": dt.datetime.now().isoformat(timespec="seconds"), "url": args.url, "checks": {}}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=not args.headed)
        ctx = browser.new_context(viewport={"width": 1500, "height": 950})
        ctx.add_init_script(INIT_JS)
        page = ctx.new_page()
        page.goto(args.url, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        if "pages" in want:
            log("pages: clicking every nav destination")
            try:
                check_pages(ctx, args.url, args.shots, result.setdefault("checks", {}).setdefault("pages", {}))
            except Exception as _exc:
                result["checks"].setdefault("pages", {})["error"] = f"{type(_exc).__name__}: {_exc}"[:300]
        if "arch" in want:
            log("arch: live engine switch")
            try:
                check_arch(page, args.url, result["checks"].setdefault("arch", {}))
            except Exception as _exc:
                result["checks"].setdefault("arch", {})["error"] = f"{type(_exc).__name__}: {_exc}"[:300]
        if "stale" in want:
            log("stale: backend change while a page is open")
            try:
                check_stale(page, args.url, result["checks"].setdefault("stale", {}))
            except Exception as _exc:
                result["checks"].setdefault("stale", {})["error"] = f"{type(_exc).__name__}: {_exc}"[:300]
        if "multitab" in want:
            log("multitab: two tabs, first backgrounded while busy")
            try:
                check_multitab(ctx, args.url, result["checks"].setdefault("multitab", {}))
            except Exception as _exc:
                result["checks"].setdefault("multitab", {})["error"] = f"{type(_exc).__name__}: {_exc}"[:300]
        if "dead" in want:
            log("dead: API routes with no UI caller")
            check_dead_features(result["checks"].setdefault("dead", {}))
        browser.close()
    with io.open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    log(f"wrote {args.out}")
    for name, data in result["checks"].items():
        log(f"{name}: {json.dumps(data)[:400]}")


if __name__ == "__main__":
    main()
