"""Browser skill: local LLM + Playwright MCP tools, programmatic (accessibility refs) only.
Screenshot/coordinate clicking needs a local vision model - logged in NEEDS.md."""
import json
import os
import re
import threading
import time
import uuid

from ishti import CONFIG, llm
from ishti.mcp_client import get_hub
from ishti.skills import answer_of, trace
from ishti.untrusted import guard, scan
from orchestrator.intent_detector import IntentDetector

ALLOWED = {"browser_navigate", "browser_navigate_back", "browser_snapshot", "browser_click", "browser_type",
           "browser_fill_form", "browser_press_key", "browser_select_option", "browser_hover",
           "browser_wait_for", "browser_tabs", "browser_find", "browser_handle_dialog", "browser_close",
           "browser_file_upload"}
TAINTED = {"on": False}  # ponytail: one flag for the one browser task at a time (LOCK)
# files the owner said yes to sending to a website (online_fallback's T2 question); any other upload asks first
APPROVED_UPLOADS = set()
DOWNLOADS = os.path.join(os.path.expanduser("~"), "Downloads")  # where the owner's Chrome saves files
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
# ponytail: one module-level "current browser server", set per request (config "browser_profiles": main = the
# owner's Chrome Profile 9, login = Profile 10, or the isolated fallback). Fine for one request at a time (LOCK);
# the upgrade path is passing the server through _loop/_call if requests ever run concurrently.
SERVER = "main"
CAPTCHA = re.compile(r"recaptcha|hcaptcha|captcha|verify you are (?:a )?human|are you a robot|cf-challenge|"
                     r"press and hold|i'm not a robot", re.I)
# One page per Chrome profile: parallel tasks take turns in the browser (re-entrant for WhatsApp -> browser).
LOCK = threading.RLock()


def wait_download(since, timeout=60, folder=DOWNLOADS):
    """The newest file Chrome finished saving to Downloads after `since` (time.time()), or None."""
    end = time.time() + timeout
    while time.time() < end:
        names = os.listdir(folder) if os.path.isdir(folder) else []
        done = [os.path.join(folder, n) for n in names if not n.endswith((".crdownload", ".tmp", ".part"))
                and os.path.getmtime(os.path.join(folder, n)) >= since]
        if done and not any(n.endswith(".crdownload") for n in names):
            return max(done, key=os.path.getmtime)
        time.sleep(1)
    return None


def profile_for(skill):
    """The Chrome profile (mcp_servers entry) a skill browses with: config "browser_profiles", else its default."""
    profiles = CONFIG.get("browser_profiles", {})
    return profiles.get(skill) or profiles.get("default", "main")


def use_server(name, session):
    """Pick the MCP server for this request, falling back when the owner's profile isn't connected."""
    global SERVER
    hub = get_hub()
    chosen = hub.resolve(name)
    if chosen is None:
        raise RuntimeError(f"Chrome profile for '{name}' isn't connected yet: install the Playwright Extension in "
                           "that profile and paste its token in Settings -> Chrome profiles.")
    if chosen != name:
        trace(session, "profile", f"your Chrome profile isn't connected yet - using ISHTI's own browser")
    try:
        hub.tools(chosen)
    except Exception as exc:
        fallback = hub.cfg.get(name, {}).get("fallback")
        if not fallback or chosen == fallback:
            raise
        trace(session, "profile", f"couldn't reach your Chrome ({type(exc).__name__}) - using ISHTI's own browser", status="error")
        chosen = fallback
    SERVER = chosen
    return chosen


# the legacy classifier also calls plain "look up X" a browser task; a real browser job names one of these
BROWSER_CUE = re.compile(r"\b(?:browser|chrome|website|site|web\s*page|page|tab|click|log\s*in|login|sign\s*in|form|fill|"
                         r"submit|url|link|portal|account|download\s+from)\b|https?://|www\.", re.I)


def match(text):
    return bool(TRIGGER.search(text)) or (IntentDetector.classify(text)["mode"] in ("BROWSER_FORM", "BROWSER_VISIBLE")
                                          and bool(BROWSER_CUE.search(text)))


def _tools():
    return [{"type": "function", "function": {"name": t.name, "description": (t.description or "")[:600],
                                              "parameters": t.inputSchema}}
            for t in get_hub().tools(SERVER) if t.name in ALLOWED]


def _risky(name, args):
    """Description of a consequential action, or None. Enter counts when the page has a
    submit/order/pay-type control, since it submits the focused form."""
    if TAINTED.get("on") and name not in ("browser_snapshot", "browser_navigate_back", "browser_wait_for", "browser_tabs", "browser_find"):
        return f"{name.removeprefix('browser_').replace('_', ' ')} (a page in this task tried to give me instructions, so every action needs your yes)"
    if name == "browser_file_upload":
        paths = set(args.get("paths") or [])
        return None if paths and paths <= APPROVED_UPLOADS else f"upload {', '.join(sorted(paths)) or 'a file'} to this website"
    if name == "browser_press_key" and str(args.get("key", "")).lower() == "enter":
        hit = RISKY.search(get_hub().call(SERVER, "browser_snapshot"))  # the live page, not the last reply
        return hit and f"press Enter (submits the form: page has '{hit.group(0)}')"
    if name not in ("browser_click", "browser_type", "browser_fill_form", "browser_select_option"):
        return None
    # The model may send only a ref ("target": "e30") with no description, so read what that element
    # actually is on the live page ('button "Submit order" [ref=e30]') before deciding.
    refs = re.findall(r"\b(?:f\d+)?e\d+\b", json.dumps(args))
    labels = []
    if refs:
        page = get_hub().call(SERVER, "browser_snapshot")
        for ref in refs:
            line = next((l.strip() for l in page.splitlines() if f"[ref={ref}]" in l), "")
            labels.append(re.sub(r"\s*\[ref=[^\]]+\]", "", line).strip("- :"))
    what = json.dumps(args, ensure_ascii=False) + " " + " ".join(labels)
    hit = RISKY.search(what)
    return hit and (args.get("element") or next((l for l in labels if RISKY.search(l)), None) or what[:120])


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
    from ishti import killswitch
    killswitch.check()  # the kill switch ends a browser task between steps
    out = get_hub().call(SERVER, name, args)
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
                                  "steps": steps_left - step, "server": SERVER}
            trace(session, name, f"waiting for your yes: {label}", status="waiting")
            return {"reply": f"About to {name.removeprefix('browser_').replace('_', ' ')}: {what}. "
                             "Go ahead? (yes/no)", "sources": []}
        else:
            trace(session, name, label, status="start")
            out = _call(name, args)
            url = re.search(r"Page URL: (\S+)", out)
            trace(session, name, label, status="error" if out.startswith("ERROR") else "done",
                  url=url.group(1) if url else None)
            if CAPTCHA.search(out[:8000]):
                # never solved automatically: hand it to the owner and resume from this page afterwards
                msgs.append({"role": "tool", "tool_call_id": cid, "content": out[:8000]})
                session["pending"] = {"skill": "browser", "msgs": msgs, "log": log, "call": None,
                                      "steps": steps_left - step - 1, "server": SERVER}
                trace(session, "captcha", "a CAPTCHA appeared - handed to you", status="waiting")
                return {"reply": "This page is showing a CAPTCHA. I don't solve those - please solve it in the browser "
                                 "window, then say \"done\" and I'll carry on from there.", "sources": []}
        log.append(f"{name.removeprefix('browser_')} {raw}" + (" -> ERROR" if out.startswith("ERROR") else ""))
        if scan(out[:8000]):
            TAINTED["on"] = True  # flag, don't comply: from here on the owner confirms each action
        msgs.append({"role": "tool", "tool_call_id": cid, "content": guard(out[:8000], "the web page", session)})
    return {"reply": "Stopped after too many browser steps. " + _summary(msgs, log), "sources": []}


def run(text, session, server=None, start_url=None, hint="", start_snapshot=None):
    if not (session.get("pending") or {}).get("skill") == "browser":
        TAINTED["on"] = False  # a new task starts clean; a resumed one keeps its state
    with LOCK:
        return _run_locked(text, session, server or profile_for("browser"), start_url, hint, start_snapshot)


def _run_locked(text, session, server, start_url, hint, start_snapshot):
    p = session.get("pending")
    use_server(p["server"] if p and p.get("skill") == "browser" and p.get("server") else server, session)
    if p and p.get("skill") == "browser":
        session["pending"] = None
        answer = answer_of(text)
        if answer != "yes":
            if answer is None:
                return None  # moved on to something else: route the new message normally
            return {"reply": "Cancelled. Nothing was clicked.", "sources": []}
        if p.get("call") is None:  # back from a CAPTCHA handoff: look at the page again and carry on
            out = _call("browser_snapshot", {})
            p["msgs"].append({"role": "user", "content": "The owner solved the CAPTCHA. The page now:\n" + out[:8000]})
            return _loop(session, p["msgs"], p["log"], max(3, p["steps"]))
        cid, name, args = p["call"]
        out = _call(name, args)
        p["log"].append(f"{name.removeprefix('browser_')} {json.dumps(args)} (owner confirmed)")
        p["msgs"].append({"role": "tool", "tool_call_id": cid, "content": out[:8000]})
        return _loop(session, p["msgs"], p["log"], p["steps"] - 1)
    msgs, log = [{"role": "user", "content": f"{hint}\n\n{text}" if hint else text}], []
    if start_snapshot:  # the caller already has the page open and loaded (WhatsApp Web takes seconds to load)
        msgs += [{"role": "assistant", "content": "", "tool_calls": [{"id": "snap0", "type": "function",
                  "function": {"name": "browser_snapshot", "arguments": "{}"}}]},
                 {"role": "tool", "tool_call_id": "snap0", "content": start_snapshot[:8000]}]
    elif url := start_url or search_url(text):
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
    assert profile_for("email") == CONFIG["browser_profiles"]["email"] and profile_for("no_such_skill") == CONFIG["browser_profiles"]["default"]
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
    get_hub().call(SERVER, "browser_close")
    print("[self-check] browser skill OK - reads a live page, submit waits for confirmation")
