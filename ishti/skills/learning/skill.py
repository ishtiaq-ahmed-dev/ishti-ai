"""Learning skill: natural language querying and inspection of learned lessons, mistakes, and settings."""
import re
from typing import Any, Dict, Optional

from ishti.learning import dashboard, errors, lessons, store
from ishti.skills import trace

OPS = [
    ("lessons", re.compile(r"\b(?:what\s+(?:have\s+you\s+learned|lessons?\s+have\s+you\s+learned)|show\s+(?:my\s+)?lessons?|list\s+lessons?|learning\s+summary)\b", re.I)),
    ("mistakes", re.compile(r"\b(?:what\s+mistakes?\s+did\s+you\s+make|repeated\s+mistakes?|list\s+mistakes?|mistake\s+patterns?)\b", re.I)),
    ("proactive_on", re.compile(r"\b(?:start|enable|turn\s+on)\s+learning\s+proactively\b", re.I)),
    ("proactive_off", re.compile(r"\b(?:stop|disable|turn\s+off)\s+learning\s+proactively\b", re.I)),
]

def match(text: str) -> bool:
    if re.search(r"\b(?:machine\s+learning|studied|course|progress)\b", text, re.I):
        return False
    return any(rx.search(text) for _, rx in OPS)

def run(text: str, session: Optional[Any] = None) -> Dict[str, Any]:
    for name, rx in OPS:
        if rx.search(text):
            trace(session, "learning", name)
            if name == "lessons":
                ls = lessons.active(limit=10)
                if not ls:
                    return {"reply": "I haven't learned any rules or lessons yet.", "sources": []}
                lines = [f"- [{l['kind']}] {l['statement']} (confidence: {l['confidence']:.0%})" for l in ls]
                return {"reply": f"Here are my active lessons:\n" + "\n".join(lines), "sources": []}
            if name == "mistakes":
                mks = errors.repeated(min_count=1)
                if not mks:
                    return {"reply": "No repeated mistakes recorded.", "sources": []}
                lines = [f"- [{m.get('error_type')}] {m.get('wrong')} -> {m.get('right')} (count: {m.get('count')})" for m in mks[:10]]
                return {"reply": f"Repeated mistakes:\n" + "\n".join(lines), "sources": []}
            if name == "proactive_on":
                store.set_setting("proactive", True)
                return {"reply": "Proactive learning enabled. I will ask confirmation questions when I notice recurring patterns.", "sources": []}
            if name == "proactive_off":
                store.set_setting("proactive", False)
                return {"reply": "Proactive learning disabled. I will only learn silently without asking unprompted questions.", "sources": []}
    return {"reply": "Learning skill ready.", "sources": []}

if __name__ == "__main__":
    import tempfile, os
    store.set_db_path(os.path.join(tempfile.mkdtemp(), "skl.db"))
    assert match("what have you learned")
    assert match("start learning proactively")
    assert not match("I studied machine learning for 2 hours")
    res = run("what have you learned")
    assert "learned" in res["reply"]
    res_on = run("start learning proactively")
    assert store.proactive() is True
    print("[self-check] learning skill OK - manifest, matcher, inspection & setting toggles")
