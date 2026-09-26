"""Browser skill: local LLM + Playwright MCP tools, programmatic (accessibility refs) only.
Screenshot/coordinate clicking needs a local vision model - logged in NEEDS.md."""
import json
import os
import re
import uuid

from ishti import llm
from ishti.mcp_client import get_hub
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
TRIGGER = re.compile(rf"^(?:please\s+)?(?:go\s+to|navigate\s+to|visit|browse\s+to|open)\s+{URL}|"
                     r"\b(?:in|on|using|with)\s+(?:the\s+|a\s+)?browser\b", re.I)
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
         "action now. If everything is done, reply only: DONE")


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
    tools, checked = _tools(), False
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
            if checked:
                return {"reply": _summary(msgs, log), "sources": []}
            checked = True  # the model thinks it's finished: make it re-check the request once
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
        if name not in ALLOWED:
            out = f"ERROR: tool {name} is not allowed"
        elif what := _risky(name, args):
            # Park the plan; resume from exactly here only on an explicit yes.
            session["pending"] = {"skill": "browser", "msgs": msgs, "log": log, "call": (cid, name, args),
                                  "steps": steps_left - step}
            return {"reply": f"About to {name.removeprefix('browser_').replace('_', ' ')}: {what}. "
                             "Go ahead? (yes/no)", "sources": []}
        else:
            out = _call(name, args)
        log.append(f"{name.removeprefix('browser_')} {raw}" + (" -> ERROR" if out.startswith("ERROR") else ""))
        msgs.append({"role": "tool", "tool_call_id": cid, "content": out[:8000]})
    return {"reply": "Stopped after too many browser steps. " + _summary(msgs, log), "sources": []}


def run(text, session):
    p = session.get("pending")
    if p and p.get("skill") == "browser":
        session["pending"] = None
        if text.strip().lower().strip(" .!") not in ("yes", "y", "go ahead", "do it", "ok", "okay", "confirm"):
            return {"reply": "Cancelled. Nothing was clicked.", "sources": []}
        cid, name, args = p["call"]
        out = _call(name, args)
        p["log"].append(f"{name.removeprefix('browser_')} {json.dumps(args)} (owner confirmed)")
        p["msgs"].append({"role": "tool", "tool_call_id": cid, "content": out[:8000]})
        return _loop(session, p["msgs"], p["log"], p["steps"] - 1)
    return _loop(session, [{"role": "user", "content": text}], [], MAX_STEPS)


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
