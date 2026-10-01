"""Browser skill: local LLM + Playwright MCP tools, programmatic (accessibility refs) only.
A site already open in a tab is reused (browser_tabs select), never reopened from scratch.
Screenshot/coordinate clicking needs a local vision model - logged in NEEDS.md."""
import json
import os
import re
import threading
import time
import uuid
from urllib.parse import urlsplit

from ishti import CONFIG, llm
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
    "the ref values shown in the latest page snapshot; never guess selectors or XPath. A site that is "
    "already open in a tab must be reused: check the open tabs (browser_tabs list) and select the tab "
    "that already has the site instead of navigating to it again - never open a duplicate tab for a site "
    "that is already open. Never invent "
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


def get_hub():
    """The MCP browser hub, imported on first use. (Measured 2026-10-01, -X importtime:
    importing ishti.mcp_client pulls the `mcp` package for ~362 ms cumulative, and skills_load
    imports every skill.py at boot - so the module-level import made every ISHTI start pay for
    the browser stack even when the browser is never used. Same singleton as before, deferred.)
"""
    from ishti.mcp_client import get_hub as _get_hub
    return _get_hub()


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


MY_DETAILS = re.compile(r"\b(?:my|the\s+owner'?s)\s+(?:details|info|information|data|profile)\b", re.I)


def owner_details():
    """The owner's saved facts, for filling forms: name, email, phone, city... (never secrets)."""
    from ishti.facts import get_facts
    o = get_facts().get("owner")
    keep = {k: v for k, v in o.items() if k in ("name", "email", "phone", "city", "country", "address", "university",
                                                 "role", "semester", "program", "company", "job") and v}
    return ("Use these details of the owner where the form asks for them (leave other fields empty and never "
            "submit without the owner's yes): " + "; ".join(f"{k}: {v}" for k, v in keep.items())) if keep else ""


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


# browser_tabs (playwright-mcp 0.0.82) renders one line per open tab:
#   - 2: (current) [Page title](https://example.com/page) [crashed]
# The number is the index select/close take; extension tabs and about:blank are listed too and are tolerated.
TAB = re.compile(r"^\s*-\s*(\d+)\s*:\s*(?:\(current\)\s*)?\[(.*)\]\((.*?)\)\s*(?:\[crashed\])?\s*$")
# two-part public suffixes seen in the owner's web: those hosts need three labels for the registrable domain
TWO_PART = {"co.uk", "org.uk", "ac.uk", "gov.uk", "com.pk", "co.pk", "com.au", "net.au", "org.au", "co.nz",
            "com.br", "co.in", "co.jp", "co.kr", "com.sg", "com.tr", "com.mx", "com.cn", "com.hk", "co.za"}


def _domain(host):
    """'gemini.google.com' and 'www.google.com' -> 'google.com': multi-host sites are still one site."""
    parts = [p for p in (host or "").lower().split(".") if p]
    if len(parts) >= 3 and ".".join(parts[-2:]) in TWO_PART:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else (parts[0] if parts else "")


def _where(url):
    """(host, path, query, fragment) to compare pages with: scheme, 'www.' and a trailing slash don't matter.
    None for a URL that isn't a site page (about:blank, data:...), which can never stand in for a site."""
    s = (url or "").strip()
    if not s:
        return None
    if "://" not in s and not re.match(r"^[a-z][a-z0-9+.-]*:", s, re.I):
        s = "https://" + s  # "go to example.com" means the same page as "https://example.com/"
    p = urlsplit(s)
    host = (p.hostname or "").lower().removeprefix("www.")
    if not host or p.scheme in ("about", "data", "blob"):
        return None
    return host, (p.path.rstrip("/") or "/"), p.query, p.fragment


def _best_tab(url, urls):
    """(index, url) of the best open tab for `url`, else (None, None). Tiers: the exact page first, then the
    same host+path, then same host (for web apps where path mutates like /app, /#inbox), then the same
    registrable domain+path (google.com/gemini.google.com) - so a site that is already open in some tab
    (WhatsApp Web, Gemini, Gmail...) is reused instead of opened again."""
    want = _where(url)
    if not want:
        return None, None
    exact = same_host_path = same_host = same_domain = None
    for i, tab in enumerate(urls or []):
        got = _where(tab)
        if not got:
            continue
        if got[:2] == want[:2]:  # same host and path
            if got[2:] == want[2:] and exact is None:
                exact = (i, tab)
            elif same_host_path is None:
                same_host_path = (i, tab)
        elif got[0] == want[0] and same_host is None:
            # Same host: if requested URL is opening the site (root path or no query), reuse active tab
            if want[1] in ("/", "") and not want[2]:
                same_host = (i, tab)
        elif same_domain is None and got[1] == want[1] and _domain(got[0]) == _domain(want[0]):
            same_domain = (i, tab)
    return exact or same_host_path or same_host or same_domain or (None, None)


def tabs(session=None):
    """URLs of the open browser tabs, in index order (position = the index browser_tabs select/close take)."""
    try:
        out = get_hub().call(SERVER, "browser_tabs", {"action": "list"})
    except Exception as exc:  # a tab call must never break a task: report it and behave as "no tabs open"
        trace(session, "browser_tabs", f"couldn't read the open tabs ({type(exc).__name__})", status="error")
        return []
    return [m.group(3) for line in out.splitlines() if (m := TAB.match(line))]


def find_tab(url, urls=None):
    """Index of the open tab that already serves `url` (same page, else same host, else same domain), or None.
    `urls` is for tests and callers that already have the list - the matching itself is pure."""
    i, _ = _best_tab(url, tabs() if urls is None else urls)
    return i


def open_url(url, session=None, new_tab=False):
    """Open `url`, reusing an already-open tab for that site. Returns a fresh page snapshot. Any trouble with
    the tab path falls back to browser_navigate, so this never breaks a task that used to work."""
    if not new_tab:
        try:
            i, tab = _best_tab(url, tabs())
        except Exception:
            i, tab = None, None
        if i is not None:
            host = (_where(tab) or ("",))[0]
            try:
                _call("browser_tabs", {"action": "select", "index": i})
                snap = _call("browser_snapshot", {})
                trace(session, "browser_tabs", f"reused the open {host} tab", status="done")
                return f"reused the open {host} tab\n{snap}"
            except Exception as exc:  # select/snapshot failed: open it the old way after all
                trace(session, "browser_tabs", f"couldn't reuse the open {host} tab ({type(exc).__name__})"
                                               " - opening it", status="error")
    return _call("browser_navigate", {"url": url})


def _same_page(a, b):
    """True when showing tab `b` already is what loading `a` shows: the same URL (scheme/'www.'/trailing slash
    aside), or the same host+path with no query/fragment on either side, or the same host when opening a site/app
    at root (e.g. web.whatsapp.com, gemini.google.com/app). A different ?query/#hash must load,
    otherwise a search would "reuse" a tab showing stale results."""
    wa, wb = _where(a), _where(b)
    if not wa or not wb:
        return False
    if wa[:2] == wb[:2] and (wa[2:] == wb[2:] or not any(wa[2:] + wb[2:])):
        return True
    if wa[0] == wb[0] and wa[1] in ("/", "") and not wa[2]:
        return True
    return False


NAV_HELPERS = ("new_tab", "tab")  # our own flags on browser_navigate args; the MCP schema takes only url


def _navigate_args(args):
    """(MCP args, wanted a fresh tab?) for a browser_navigate call: our helper keys never reach the MCP tool
    ('additionalProperties: false') and their presence means the caller asked for a new tab on purpose."""
    return {k: v for k, v in args.items() if k == "url"}, any(k in args for k in NAV_HELPERS)


def _call(name, args):
    """MCP call; action results only link the page snapshot file, so inline it for the model."""
    from ishti import killswitch
    killswitch.check()  # the kill switch ends a browser task between steps
    if name == "browser_navigate" and isinstance(args, dict):
        args, wanted_new = _navigate_args(args)
        if not wanted_new and args.get("url"):
            try:
                i, tab = _best_tab(args["url"], tabs())
            except Exception:
                i = None
            if i is not None and _same_page(args["url"], tab):
                host = (_where(tab) or ("",))[0]
                try:
                    _call("browser_tabs", {"action": "select", "index": i})
                    return f"reused the open {host} tab\n{_call('browser_snapshot', {})}"
                except Exception:
                    pass  # any tab trouble: navigate below, exactly like before
    return _plain_call(name, args)


def _plain_call(name, args):
    """The MCP call itself: C4 retry, the linked page snapshot inlined, a login page moved to the login profile."""
    try:
        out = get_hub().call(SERVER, name, args)
    except Exception:  # C4: one retry per step - a transient MCP/page error shouldn't sink a long flow
        time.sleep(1.0)
        out = get_hub().call(SERVER, name, args)
    m = re.search(r"\[Snapshot\]\(([^)]+\.yml)\)", out)
    if m and os.path.isfile(m.group(1)):
        with open(m.group(1), encoding="utf-8") as f:
            out = out.replace(m.group(0), "\n" + f.read())
    if name == "browser_navigate" and login_wall(out) and (moved := _to_login(args["url"])):
        return moved
    return out


# A sign-in page instead of the site: Google/Microsoft account pages, /login-style URLs, a password box, a
# "Sign in" heading. WhatsApp's QR page is its own kind of login (never in the login profile).
LOGIN_WALL = re.compile(r"Page URL: https?://(?:accounts\.google\.com|login\.(?:live|microsoftonline)\.com|"
                        r"[^\s/]+/(?:\S*/)?(?:log-?in|sign-?in|signin|auth/login)\b)|"
                        r'textbox "[^"]*password[^"]*"|heading "(?:sign in|log ?in|sign in to [^"]*)"', re.I)


def login_wall(out):
    return bool(LOGIN_WALL.search(out[:8000])) and "web.whatsapp.com" not in out[:600]


def _to_login(url):
    """Everything browses in the main profile (Chrome Profile 9); a site that asks to sign in there is opened at
    the same URL in the login profile (Profile 10, where the owner's logins are) and the task carries on there."""
    global SERVER
    if SERVER == "login" or "login" not in CONFIG["mcp_servers"] or get_hub().resolve("login") != "login":
        return None
    first, SERVER = SERVER, "login"
    print(f"[browser] {url} asks for a login in '{first}' - opening it in the login profile")
    out = get_hub().call(SERVER, "browser_navigate", {"url": url})
    m = re.search(r"\[Snapshot\]\(([^)]+\.yml)\)", out)
    if m and os.path.isfile(m.group(1)):
        with open(m.group(1), encoding="utf-8") as f:
            out = out.replace(m.group(0), "\n" + f.read())
    note = ("This page asked for a login in Chrome Profile 9, so it was opened in Profile 10 (the login profile). "
            + ("It asks for a login there too - the owner has to sign in once in Profile 10." if login_wall(out) else ""))
    return f"{note}\n{out}"


# Playwright puts state flags between an element's name and its ref ([active], [disabled], [cursor=pointer]...);
# WhatsApp's message box broke on "[active]". These patterns skip them.
FLAGS = r'(?: \[(?!ref=)[^\]]*\])*'


def ref(snap, role, label=None):
    """The ref of the first `role "label"` element; with no label, the first `role` element, named or not."""
    name = rf'(?: "[^"]*")?' if label is None else rf' "(?:{label})"'
    m = re.search(rf'\b{role}{name}{FLAGS} \[ref=([^\]]+)\]', snap, re.I)
    return m.group(1) if m else None


def refs(snap, role, label=r'[^"]*'):
    return re.findall(rf'\b{role} "(?:{label})"{FLAGS} \[ref=([^\]]+)\]', snap, re.I)


INTERACTIVE = re.compile(r"\b(?:button|textbox|link|combobox|menuitem|checkbox|radio|gridcell|option|tab|searchbox)\b"
                         r"[^\n]*\[ref=([^\]]+)\]")


def locate(snap, want, role=None, label=None, session=None):
    """The ref of the element a task needs, even after the site renames it: the exact role/label first, else the
    local model picks it from the page's interactive elements (a renamed label is still the same element)."""
    if role and (r := ref(snap, role, label)):
        return r
    items = [line.strip()[:160] for line in snap.splitlines() if INTERACTIVE.search(line)]
    if not items:
        return None
    raw = llm.ask("Page elements (one per line, each with its ref):\n" + "\n".join(items[:250])
                  + f"\n\nWhich element is: {want}? Reply with only its ref (for example e123), or none.",
                  system=None, temperature=0, max_tokens=16)
    known = {m.group(1) for line in items if (m := INTERACTIVE.search(line))}
    got = next((t for t in re.findall(r"\w+", raw or "") if t in known), None)
    if got:
        trace(session, "locate", f"found '{want}' by what it is (its label changed)")
    return got


def screenshot(name):
    """Screenshot of the current page -> its absolute path, or None. A bare filename is saved relative to the MCP
    server's working folder (the repo root), so it's given a full path under data/screenshots."""
    from ishti import ROOT
    path = os.path.join(ROOT, "data", "screenshots", name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _call("browser_take_screenshot", {"filename": path})
    return path if os.path.isfile(path) else None


def troubleshoot(want, site, session=None):
    """When an element can't be found at all: what the screen shows (screenshot + local vision model: a popup, a
    banner, a logged-out page) and how the site does it (web search). Returned as a hint for the browser agent."""
    notes = []
    try:
        if path := screenshot(f"stuck_{int(time.time())}.png"):
            from ishti.skills.video.pro import look
            seen = look(path, f"I need to {want} on this {site} page. What is on screen? Is a popup, dialog, "
                              "banner or login page in the way? Where is what I need?")
            notes.append("The screen shows: " + str(seen.get("text") or "")[:600]
                         + (f" Problem: {seen['problem']}" if seen.get("problem") else ""))
            trace(session, "vision", "looked at a screenshot of the page", status="done")
    except Exception as exc:
        notes.append(f"(screenshot failed: {type(exc).__name__})")
    try:
        from tools.web.search import web_search
        hits = web_search(f"how to {want} on {site}", max_results=3)
        if hits:
            notes.append("How it's done (web search): " + " | ".join(f"{h.title}: {h.snippet[:200]}" for h in hits))
            trace(session, "web_search", f"looked up how to {want} on {site}", status="done")
    except Exception as exc:
        notes.append(f"(web search failed: {type(exc).__name__})")
    return "\n".join(notes)


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
    pages = [m["content"] for m in msgs if m["role"] == "tool"]
    # the page before the last one too: the re-check sometimes clicks one link past the answer (example.com's
    # heading, then "Learn more" -> IANA), and the answer must come from whichever page shows it
    earlier = f"Previous page (excerpt):\n{pages[-2][:1500]}\n\n" if len(pages) > 1 else ""
    done = "\n".join(log) or "(no browser actions ran)"
    out = llm.ask(f"Owner's request: {request}\n\nBrowser actions that actually ran, in order:\n{done}\n\n{earlier}"
                  f"Current page (excerpt):\n{(pages or [''])[-1][-3000:]}\n\nAnswer the owner in 1-3 short sentences "
                  "from whichever of these pages answers the request. Report only what the actions and pages show; if "
                  "part of the request was not done, say which part.", temperature=0, max_tokens=200)
    # The model occasionally writes only a Thai/CJK junk line (llm.clean drops it) - an empty string
    # must never be the owner-facing answer (found by the per-skill suite's failure rows, 2026-10-01).
    if not out.strip():
        out = "I opened the browser but couldn't read anything useful from the page for that request."
    return out


def _loop(session, msgs, log, steps_left):
    tools, checks = _tools(), 0
    for step in range(steps_left):
        for m in msgs[:-3]:  # older page states shrink to their top (title, headings) - the latest stay whole
            if m["role"] == "tool" and len(m["content"]) > 1500:
                m["content"] = m["content"][:1500] + "\n[rest of this older page omitted]"
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
    if MY_DETAILS.search(text):  # C4: fill forms from what ISHTI already knows (submitting still needs a yes)
        hint = (hint + "\n" if hint else "") + owner_details()
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
    assert login_wall("- Page URL: https://accounts.google.com/v3/signin/identifier?x") and login_wall("- Page URL: https://github.com/login?r=1")
    assert login_wall('- Page URL: https://x.com/home\n- textbox "Password" [ref=e5]')
    assert not login_wall('- Page URL: https://example.com/\n- heading "Example Domain"') and not login_wall("- Page URL: https://blog.com/how-to-login-faster")
    assert not login_wall('- Page URL: https://web.whatsapp.com/\n- heading "Log in"')
    s = {}
    r = run("go to example.com and tell me the main heading", s)
    assert "Example Domain" in r["reply"] and not s.get("pending"), r
    s = {}
    r = run("open https://httpbin.org/forms/post, fill customer name with Test User and submit the order", s)
    assert s.get("pending"), f"submit must wait for a yes: {r}"
    assert "Cancelled" in run("no", s)["reply"]
    get_hub().call(SERVER, "browser_close")
    print("[self-check] browser skill OK - reads a live page, submit waits for confirmation")
