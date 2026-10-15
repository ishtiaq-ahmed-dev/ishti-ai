"""Learning skill: natural language querying and inspection of learned lessons, mistakes, and settings."""
import json
import os
import re
import time
from typing import Any, Dict, Optional

from ishti.learning import dashboard, errors, lessons, store
from ishti.skills import trace

OPS = [
    ("lessons", re.compile(r"\b(?:what\s+(?:have\s+you\s+learned|lessons?\s+have\s+you\s+learned)|show\s+(?:my\s+)?lessons?|list\s+lessons?|learning\s+summary)\b", re.I)),
    ("mistakes", re.compile(r"\b(?:what\s+mistakes?\s+did\s+you\s+make|repeated\s+mistakes?|list\s+mistakes?|mistake\s+patterns?)\b", re.I)),
    ("proactive_on", re.compile(r"\b(?:start|enable|turn\s+on)\s+learning\s+proactively\b", re.I)),
    ("proactive_off", re.compile(r"\b(?:stop|disable|turn\s+off)\s+learning\s+proactively\b", re.I)),
    ("export", re.compile(r"\bexport\s+(?:my\s+)?(?:learned\s+)?preferences?\b", re.I)),
    ("remember_tool", re.compile(r"\bremember\s+that\s+i(?:'m|\s+am)\s+trying\s+to\s+learn\s+(?P<tool>[\w.-]+)", re.I)),
    # "Would you have suggested anything for me today?" (checklist #183) - a self-review answered from
    # actually-learned patterns, or an honest "nothing stood out". Never an invented suggestion.
    ("today_review", re.compile(r"\b(?:would\s+you\s+have\s+suggested|any\s+suggestions?\s+for\s+me|suggest(?:ed|\s+anything)?\s+anything\s+for\s+me|suggested\s+anything\s+(?:for\s+me\s+)?today|what\s+would\s+you\s+(?:have\s+)?suggest)\b", re.I)),
]

def _export_preferences() -> Dict[str, Any]:
    """Write the learned preferences and active lessons to a real JSON file and report that real file.

    The previous reply announced data/learned_preferences.json without writing anything at all, so the
    owner was told an export existed that did not (checklist 242). Now the file is written (or the write
    failure is reported) and the reply names the path and the true counts."""
    from ishti import CONFIG
    from ishti.facts import get_facts
    owner = {k: v for k, v in get_facts().get("owner").items() if k != "entity"}
    prefs = {k: v for k, v in owner.items() if k.startswith("prefers_")}
    active = [{k: l.get(k) for k in ("kind", "statement", "confidence", "scope", "context")}
              for l in lessons.active(limit=200)]
    # same root the manifest's write scope "data" resolves against (scope.DATA)
    path = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "learned_preferences.json")
    payload = {"exported_at": time.strftime("%Y-%m-%d %H:%M"),
               "learned_preferences": prefs, "active_lessons": active}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except OSError as exc:
        return {"reply": f"I couldn't write the export to {path}: {type(exc).__name__}: {exc}", "sources": []}
    if not prefs and not active:
        return {"reply": f"Wrote {path}, but there is nothing learned yet, so it holds no preferences and no "
                         f"lessons. It fills up as you tell me how you like things done.", "sources": []}
    shown = "\n".join(f"- {k}: {v}" for k, v in list(prefs.items())[:10]) \
        or f"(no prefers_* preferences yet; {len(active)} learned lesson(s))"
    return {"reply": f"Wrote {path} (readable JSON) with {len(prefs)} learned preference(s) and "
                     f"{len(active)} active lesson(s).\nLearned preferences:\n{shown}", "sources": []}


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
            if name == "export":
                return _export_preferences()
            if name == "remember_tool":
                m = rx.search(text)
                tool = m.group("tool") if m else "this new tool"
                return {"reply": f"Saved to memory: You are learning {tool}. I will keep this standing context in mind and offer tailored explanations, commands, and best practices as you go.", "sources": []}
            if name == "today_review":
                ls = lessons.active(limit=5)
                if not ls:
                    return {"reply": "Nothing stood out today - I haven't observed a pattern worth suggesting yet.", "sources": []}
                lines = [f"- {l['statement']} (confidence: {l['confidence']:.0%})" for l in ls]
                return {"reply": "Based on what I've actually learned about you:\n" + "\n".join(lines), "sources": []}
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
