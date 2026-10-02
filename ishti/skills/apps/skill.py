"""Apps skill: launch or focus desktop applications."""
import re

from orchestrator.intent_detector import IntentDetector

VERB = re.compile(r"^\s*(?:please\s+)?(?:open|launch|start|run|switch\s+to|focus)\s+(?:the\s+|my\s+)?(?P<app>[a-z0-9 _.-]+?)(?:\s+app)?\s*[.!]?$", re.I)
MULTI_STEP = re.compile(r"\b(?:and|then|after|turn|switch|set|enable|disable|change|click|type|press|search|go)\b", re.I)  # "open settings and turn on dark mode" is a task for screen control, not an app name
MESSAGING_VERBS = re.compile(r"\b(?:send|message\s+to|write\s+to|text\s+to|reply)\b", re.I)


WHATSAPP = re.compile(r"\bwh?a+t*s*\s?a+p+\b|\bwhastap\b|\bwatsapp\b", re.I)  # the whatsapp skill opens it: one window, the owner's preferred kind


def match(text):
    if WHATSAPP.search(text):
        return False
    if re.search(r"\b(?:memory|facts?|profile|reminders?|notes?)\b", text, re.I):
        return False
    if MESSAGING_VERBS.search(text):
        return False
    if (m := VERB.match(text)) and MULTI_STEP.search(m.group("app")):
        return False
    return (IntentDetector.classify(re.sub(r"\b(?:the|a|an|my)\s+", "", text, flags=re.I))["mode"] == "APP_CONTROL"
            and bool(VERB.match(text)))


def run(text, session):
    if re.search(r"\b(?:memory|facts?|profile|reminders?|notes?)\b", text, re.I) or MESSAGING_VERBS.search(text):
        return None
    from desktop.computer_use import get_computer_use_agent
    m = VERB.match(text)
    if not m or MULTI_STEP.search(m.group("app")):
        return None  # no "open X" in it: not an app request, route it on
    agent = get_computer_use_agent()
    app = (m.group("app") or "").strip()
    if not app:
        return None
    from ishti import prefs
    app = prefs.app_for(app) or app  # "open music" -> the app the owner said to always use for music (Learning > About you)
    prefs.note_use("apps", app)
    if not re.search(r"switch\s+to|focus", text, re.I):
        res = agent.launch_app(app)
        if isinstance(res, dict) and res.get("status") == "success":
            return {"reply": f"Opened {app}.", "sources": []}
    if agent.focus_window(app):
        return {"reply": f"Switched to {app}.", "sources": []}
    return {"reply": f"Couldn't open {app}: not a known app or on PATH.", "sources": []}


if __name__ == "__main__":
    assert match("open notepad") and match("open the calculator app")
    assert not match("what is a file")
    print("[self-check] apps OK - routing")
