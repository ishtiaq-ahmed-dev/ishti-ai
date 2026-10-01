"""Files skill: PC file search (user folders + the ISHTI checkout), short answer with paths."""
import re

from ishti.skills import trace
from orchestrator.intent_detector import IntentDetector


# the legacy classifier calls every "search for X" a PC search; only the owner's own files are this skill's
LOCAL = re.compile(r"\b(?:on\s+(?:my\s+|the\s+)?(?:pc|computer|laptop|drive|disk)|files?|folders?|pdfs?|docs?|documents?|"
                   r"downloads|desktop|pictures|photos|videos|music)\b|\.[a-z0-9]{2,4}\b|[A-Za-z]:\\", re.I)


def match(text):
    return (IntentDetector.classify(re.sub(r"\b(?:the|a|an|my)\s+", "", text, flags=re.I))["mode"] == "PC_SEARCH"
            and bool(LOCAL.search(text)))


def query_of(text):
    q = re.sub(r"^(?:please\s+)?(?:search|find|locate|look)\b.*?\b(?:for|file|files|pc)\s+(?:for\s+|named\s+|called\s+)?",
               "", text, flags=re.I)
    q = re.sub(r"\b(?:on|in)\s+(?:my\s+)?(?:pc|computer|downloads|documents|desktop)\b|\bcontaining\b.*", "", q, flags=re.I)
    return q.strip(" .?!'\"") or text


def run(text, session):
    from desktop.computer_use import get_computer_use_agent
    exts = [f".{e.lower()}" for e in re.findall(r"\.([a-z0-9]{2,5})\b", text, flags=re.I)] or None
    m = re.search(r"\bcontaining\s+['\"]?(.+?)['\"]?\s*$", text, flags=re.I)
    q = m.group(1) if m else query_of(text)
    trace(session, "search", f"search your folders for '{q}'", status="start")
    hits = get_computer_use_agent().search_pc(q, extensions=exts, max_results=8, search_content=bool(m))
    trace(session, "search", f"{len(hits)} file(s) for '{q}'", results=[h["path"] for h in hits])
    if not hits:
        return {"reply": f"No files matching '{q}' in your user folders.", "sources": []}
    lines = [f"{i}. {h['path']} ({h['size']}, {h['modified']})" for i, h in enumerate(hits, 1)]
    return {"reply": f"Found {len(hits)} for '{q}':\n" + "\n".join(lines), "sources": [], "files": hits}
