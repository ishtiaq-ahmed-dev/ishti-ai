"""Legacy skill: hands the request to orchestrator/crew.ExecutionEngine unchanged."""
import re

from orchestrator.intent_detector import IntentDetector

MODES = {"VIDEO_EDIT", "PC_SEARCH", "PC_TELEMETRY", "APP_CONTROL", "DEEP_RESEARCH",
         "WEB_FETCH"}
_engine = None


def match(text):
    # the regex cues are article-free ("open task manager"); strip articles so "open the task manager" hits
    return IntentDetector.classify(re.sub(r"\b(?:the|a|an|my)\s+", "", text, flags=re.I))["mode"] in MODES


def engine():
    global _engine
    from ishti.dispatcher import HOOKS
    if HOOKS["engine"]:
        return HOOKS["engine"]  # the web server's engine, wired to the UI confirmation gate
    if _engine is None:
        from orchestrator.crew import ExecutionEngine
        _engine = ExecutionEngine(confirmation_callback=HOOKS["confirm"], event_callback=HOOKS["emit"])
    return _engine


def run(text, session):
    res = engine().process_request(text, session_id=session["id"])
    return {**res, "reply": res.get("final_answer", ""), "persisted": True}
