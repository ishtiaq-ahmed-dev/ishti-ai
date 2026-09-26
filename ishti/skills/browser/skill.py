"""Browser skill: local LLM + Playwright MCP tools, programmatic (accessibility refs) only.
Screenshot/coordinate clicking needs a local vision model - logged in NEEDS.md."""
import json
import os
import re
import uuid

from ishti import llm
from ishti.mcp_client import get_hub
from ishti.skills import answer_of, trace
from orchestrator.intent_detector import IntentDetector

ALLOWED = {"browser_navigate", "browser_navigate_back", "browser_snapshot", "browser_click", "browser_type",
           "browser_fill_form", "browser_press_key", "browser_select_option", "browser_hover",
           "browser_wait_for", "browser_tabs", "browser_find", "browser_handle_dialog", "browser_close"}
# ponytail: keyword gate on the element the model is about to touch. Upgrade path: a Laya noul
# "is this action consequential?" over the element + page title.
RISKY = re.compile(r"\b(buy|purchase|pay|checkout|check out|place order|order now|send|submit|post|publish|"
                   r"tweet|delete|remove|confirm|transfer|subscribe|sign up|register|book|reserve|donate|"
                   r"apply|password|log ?in|sign ?in)\b", re.I)
URL = r"(?:https?://\S+|www\.\S+|[\w-]+\.(?:com|org|net|io|dev|ai|pk|co|edu|gov|app)\b\S*)"
SITES = r"(?:youtube|google|gmail|github|facebook|twitter|linkedin|reddit|wikipedia|amazon|netflix|instagram|daraz|chatgpt)"
TRIGGER = re.compile(rf"^(?:please\s+)?(?:go\s+to|navigate\s+to|visit|browse\s+to|open)\s+(?:{URL}|{SITES}\b)|"
                     r"\b(?:in|on|using|with)\s+(?:the\s+|a\s+|my\s+)?(?:web\s+)?browser\b|"
                     r"\b(?:open|launch|start|use)\s+(?:a\s+|the\s+|my\s+)?(?:web\s+)?browser\b|"
                     rf"^(?:please\s+)?(?:search|look\s+up|find)\s+(?:on\s+)?{SITES}\b|"
                     r"\b(?:open|launch|start)\s+(?:google\s+)?chrome\s+and\b", re.I)
SYSTEM = llm.STYLE + (
    " You control a real web browser through tools, one action at a time. Target elements only by "
    "the ref values shown in the latest page snapshot; never guess selectors or XPath. Never invent "
    "page content or claim an action worked unless the page shows it. When done, answer the owner "
    "in 1-3 short sentences with what you found or did.")
MAX_STEPS = 10


def match(text):
    return bool(TRIGGER.search(text)) or IntentDetector.classify(text)["mode"] in ("BROWSER_FORM", "BROWSER_VISIBLE")


def _tools():
    return [{"type": "function", "function": {"name": t.name, "description": (t.description or "")[:600],
                                              "parameters": t.inputSchema}}
            for t in get_hub().tools("browser") if t.name in ALLOWED]


def _risky(name, args):
    """Description of a consequential action, or None. Enter counts when the page has a
    submit/order/pay-type control, since it submits the focused form."""
    if name == "browser_press_key" and str(args.get("key", "")).lower() == "enter":
        hit = RISKY.search(get_hub().call("browser", "browser_snapshot"))  # the live page, not the last reply
        return hit and f"press Enter (submits the form: page has '{hit.group(0)}')"
    if name not in ("browser_click", "browser_type", "browser_fill_form", "browser_select_option"):
        return None
    what = json.dumps(args, ensure_ascii=False)
    return RISKY.search(what) and (args.get("element") or what[:120])


def human(name, args):
    """Short label for the Thinking panel: never raw JSON or page snapshots."""
    what = args.get("element") or args.get("url") or args.get("key") or args.get("text") or ""
    verb = {"browser_navigate": "open", "browser_snapshot": "read the page", "browser_click": "click",
            "browser_type": "type into", "browser_fill_form": f"fill {len(args.get('fields', []))} fields",
            "browser_press_key": "press", "browser_select_option": "choose", "browser_wait_for": "wait for",
            "browser_navigate_back": "go back", "browser_find": "find", "browser_tabs": "switch tab",
            "browser_hover": "hover", "browser_close": "close the browser"}.get(name, name.removeprefix("browser_"))
    return f"{verb} {what}".strip()[:90]


def _call(name, args):
    """MCP call; action results only link the page snapshot file, so inline it for the model."""
    out = get_hub().call("browser", name, args)
    m = re.search(r"\[Snapshot\]\(([^)]+\.yml)\)", out)
    if m and os.path.isfile(m.group(1)):
        with open(m.group(1), encoding="utf-8") as f:
            out = out.replace(m.group(0), "\n" + f.read())
    return out


def _text_calls(content):
    """qwen2.5 sometimes writes {"name": ..., "arguments": ...} as plain text instead of a tool call.
    Take only the first: later ones were planned without seeing the page."""
    for line in (content or "").splitlines():
        try:
            d = json.loads(line.strip())
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("name") in ALLOWED:
            return [(f"text_{uuid.uuid4().hex[:8]}", d["name"], json.dumps(d.get("arguments") or {}))]
    return []


CHECK = ("Before answering, compare with the owner's request. If any part is not done yet, do the next "
         "action now. If the information asked for is not on this page, open the most relevant link "
         "(click it by its ref) and read that page - never stop at a list of results or tell the owner to "
         "visit a site themselves. If everything is done, reply only: DONE")


def _summary(msgs, log):
    """Final answer grounded in what actually ran, never in the model's own claims."""
    request = msgs[0]["content"]
    page = next((m["content"] for m in reversed(msgs) if m["role"] == "tool"), "")[-3000:]
    done = "\n".join(log) or "(no browser actions ran)"
    return llm.ask(f"Owner's request: {request}\n\nBrowser actions that actually ran, in order:\n{done}\n\n"
                   f"Current page (excerpt):\n{page}\n\nAnswer the owner in 1-3 short sentences. Report only "
                   "what the actions and page show; if part of the request was not done, say which part.",
                   temperature=0, max_tokens=200)


def _loop(session, msgs, log, steps_left):
    tools, checks = _tools(), 0
    for step in range(steps_left):
        for m in msgs[:-3]:  # keep only the latest page states in context
            if m["role"] == "tool" and len(m["content"]) > 300:
                m["content"] = "[older page state omitted]"
        msg = llm.chat(msgs, system=SYSTEM, tools=tools, temperature=0, max_tokens=400)
        # One action per step: the model must see each result before planning the next action,
        # otherwise it batches navigate+fill+submit against element targets it guessed.
        calls = ([(tc.id, tc.function.name, tc.function.arguments) for tc in msg.tool_calls or []]
                 or _text_calls(msg.content))[:1]
        if not calls:
            if checks >= 2:
                return {"reply": _summary(msgs, log), "sources": []}
            checks += 1  # it thinks it's finished (or stuck): make it re-check the request, twice at most
            msgs += [{"role": "assistant", "content": msg.content or ""}, {"role": "user", "content": CHECK}]
            continue
        cid, name, raw = calls[0]
        try:
            args = json.loads(raw or "{}")
        except ValueError:
            args = {}
        msgs.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": cid, "type": "function", "function": {"name": name, "arguments": raw}}]})
        print(f"[browser] step {step}: {name} {raw}")
        label = human(name, args)
        if name not in ALLOWED:
            out = f"ERROR: tool {name} is not allowed"
        elif what := _risky(name, args):
            # Park the plan; resume from exactly here only on an explicit yes.
            session["pending"] = {"skill": "browser", "msgs": msgs, "log": log, "call": (cid, name, args),
                                  "steps": steps_left - step}
            trace(session, name, f"waiting for your yes: {label}", status="waiting")
            return {"reply": f"About to {name.removeprefix('browser_').replace('_', ' ')}: {what}. "
                             "Go ahead? (yes/no)", "sources": []}
        else:
            trace(session, name, label, status="start")
            out = _call(name, args)
            url = re.search(r"Page URL: (\S+)", out)
            trace(session, name, label, status="error" if out.startswith("ERROR") else "done",
                  url=url.group(1) if url else None)
        log.append(f"{name.removeprefix('browser_')} {raw}" + (" -> ERROR" if out.startswith("ERROR") else ""))
        msgs.append({"role": "tool", "tool_call_id": cid, "content": out[:8000]})
    return {"reply": "Stopped after too many browser steps. " + _summary(msgs, log), "sources": []}


def run(text, session):
    p = session.get("pending")
    if p and p.get("skill") == "browser":
        session["pending"] = None
        answer = answer_of(text)
        if answer != "yes":
            if answer is None:
                return None  # moved on to something else: route the new message normally
            return {"reply": "Cancelled. Nothing was clicked.", "sources": []}
        cid, name, args = p["call"]
        out = _call(name, args)
        p["log"].append(f"{name.removeprefix('browser_')} {json.dumps(args)} (owner confirmed)")
        p["msgs"].append({"role": "tool", "tool_call_id": cid, "content": out[:8000]})
        return _loop(session, p["msgs"], p["log"], p["steps"] - 1)
    msgs, log = [{"role": "user", "content": text}], []
    if url := search_url(text):
        # Programmatic first: open the results page directly instead of letting the model find and
        # fill a search box (Google's page alone cost 4-5 slow steps). The model starts from the results.
        trace(session, "browser_navigate", f"open results for your search", status="start")
        out = _call("browser_navigate", {"url": url})
        trace(session, "browser_navigate", f"results page: {url.split('?')[0]}", status="done", url=url)
        msgs += [{"role": "assistant", "content": "", "tool_calls": [{"id": "search0", "type": "function",
                  "function": {"name": "browser_navigate", "arguments": json.dumps({"url": url})}}]},
                 {"role": "tool", "tool_call_id": "search0", "content": out[:8000]}]
        log.append(f"navigate {url}")
    return _loop(session, msgs, log, MAX_STEPS)


SEARCH = re.compile(r"\bsearch\s+(?:(?:on|in)\s+)?(?:(?P<site>google|youtube|github|amazon|wikipedia|bing|reddit)\s+)?"
                    r"(?:for\s+)?(?P<q>.+?)(?:\s*(?:,|\band\b|\bthen\b).*)?$", re.I)
SEARCH_URLS = {"google": "https://www.google.com/search?q={}", "youtube": "https://www.youtube.com/results?search_query={}",
               "github": "https://github.com/search?q={}", "amazon": "https://www.amazon.com/s?k={}",
               "wikipedia": "https://en.wikipedia.org/w/index.php?search={}", "bing": "https://www.bing.com/search?q={}",
               "reddit": "https://www.reddit.com/search/?q={}"}


def search_url(text):
    """Results-page URL when the request is a search; local SearXNG unless the owner names a site."""
    m = SEARCH.search(text)
    if not m or not m["q"].strip():
        return None
    from urllib.parse import quote_plus
    named = re.search(r"\b(google|youtube|github|amazon|wikipedia|bing|reddit)\b", text, re.I)  # "go to youtube and search…"
    site = (m["site"] or (named.group(1) if named else "")).lower()
    template = SEARCH_URLS.get(site, "http://127.0.0.1:8890/search?q={}")
    from ishti.skills import ground_dates
    return template.format(quote_plus(ground_dates(m["q"].strip(" .?!"))))


if __name__ == "__main__":
    assert match("go to example.com and tell me the heading") and match("check my inbox in the browser")
    assert not match("what is a web browser?")
    assert _text_calls('junk\n{"name": "browser_navigate", "arguments": {"url": "x"}}\n{"name": "browser_click"}') \
        [0][1:] == ("browser_navigate", '{"url": "x"}')
    assert _risky("browser_click", {"element": "Place order", "target": "e9"})
    assert not _risky("browser_click", {"element": "Next page", "target": "e3"})
    s = {}
    r = run("go to example.com and tell me the main heading", s)
    assert "Example Domain" in r["reply"] and not s.get("pending"), r
    s = {}
    r = run("open https://httpbin.org/forms/post, fill customer name with Test User and submit the order", s)
    assert s.get("pending"), f"submit must wait for a yes: {r}"
    assert "Cancelled" in run("no", s)["reply"]
    get_hub().call("browser", "browser_close")
    print("[self-check] browser skill OK - reads a live page, submit waits for confirmation")
