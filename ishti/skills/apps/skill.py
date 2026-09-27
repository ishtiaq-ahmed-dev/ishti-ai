"""Apps skill: launch or focus desktop applications."""
import re

from orchestrator.intent_detector import IntentDetector

VERB = re.compile(r"^.*?\b(open|launch|start|run|switch\s+to|focus)\s+(?:the\s+|my\s+)?(?P<app>.+?)(?:\s+app)?[.!]?$", re.I)


def match(text):
    if re.search(r"\b(?:memory|facts?|profile|reminders?|notes?)\b", text, re.I):
        return False
    return (IntentDetector.classify(re.sub(r"\b(?:the|a|an|my)\s+", "", text, flags=re.I))["mode"] == "APP_CONTROL"
            and bool(VERB.match(text)))


def run(text, session):
    if re.search(r"\b(?:memory|facts?|profile|reminders?|notes?)\b", text, re.I):
        return None
    from desktop.computer_use import get_computer_use_agent
    m = VERB.match(text)
    if not m:
        return None  # no "open X" in it: not an app request, route it on
    agent = get_computer_use_agent()
    app = (m.group("app") or "").strip()
    if not app:
        return None
    if not re.search(r"switch\s+to|focus", text, re.I):
        res = agent.launch_app(app)
        if isinstance(res, dict) and res.get("status") == "success":
            return {"reply": f"Opened {app}.", "sources": []}
    if agent.focus_window(app):
        return {"reply": f"Switched to {app}.", "sources": []}
    return {"reply": f"Couldn't open {app}: not a known app or on PATH.", "sources": []}
