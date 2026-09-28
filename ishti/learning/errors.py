"""Error analysis: turn a complaint/correction into a classified error and a stable signature.

Spec sections 7 (negative feedback taxonomy), 10 (repeated mistake detection) and 14 (error
analysis after a failed task). Classification is deterministic and pattern-first, and only runs
when the message actually carries a correction cue - ordinary chat must never be read as a
correction. Nothing here assumes *what* went wrong from a bare "no": a "no" with no target is
recorded as an unclassified correction for the owner to confirm, never guessed into a lesson.

The taxonomy is the one the spec lists:
wrong_skill, wrong_tool, wrong_application, wrong_person, wrong_file, wrong_parameter,
wrong_sequence, wrong_assumption, wrong_interpretation, wrong_permission, plus
entity_resolution_failure (section 14), language_mismatch and tool_failure (observed causes).
"""
import re

from ishti.learning import store

TAXONOMY = {
    "wrong_skill": "the wrong capability was chosen for the request",
    "wrong_tool": "the right capability used the wrong tool/route inside it",
    "wrong_application": "the wrong application was opened or used",
    "wrong_person": "the wrong person/contact was selected",
    "wrong_file": "the wrong file, folder or project was used",
    "wrong_parameter": "an argument (number, time, quantity) was wrong",
    "wrong_sequence": "the steps happened in the wrong order",
    "wrong_assumption": "something was assumed that the owner never said",
    "wrong_interpretation": "the request itself was understood incorrectly",
    "wrong_permission": "an action was taken without being asked / without a confirmation",
    "entity_resolution_failure": "several entities share a name and the wrong one was picked",
    "language_mismatch": "the answer language did not match the owner's preference",
    "tool_failure": "the tool/skill itself errored or returned nothing usable",
    "unknown": "a correction without a clear target",
}
# only these phrases start an analysis: "no, use chrome" - not "no thanks"
GATE = re.compile(
    r"\b(?:no[,.\s]|nope|wrong|incorrect|not\s+(?:what|right|that|the)|that'?s\s+(?:not|wrong)|"
    r"should\s+(?:have|be)|shouldn'?t|instead|i\s+(?:meant|said|asked|wanted)|did\s?n'?t|did\s+not|"
    r"do\s?n'?t\s+do|stop|why\s+did\s+you|you\s+(?:opened|used|sent|picked|chose|missed|skipped)|"
    r"redo|undo|mistake|misunderstood|assumed|wrong\s+(?:one|person|file|app|folder)|"
    r"(?:answer|reply|speak|talk|write)\s+(?:to\s+me\s+)?in\s+(?:english|urdu|pashto|roman\s+urdu)|"
    r"why\s+(?:are|did)\s+you\s+(?:answering|replying|writing|speaking))", re.I)
APPS = ("chrome", "edge", "firefox", "brave", "opera", "safari", "vivaldi", "notepad", "word", "excel",
        "powerpoint", "outlook", "whatsapp", "telegram", "vs code", "vscode", "terminal", "explorer")
SKILLS = ("web_search", "browser", "web_fetch", "deep_research", "chat", "email", "whatsapp", "memory",
          "files", "folders", "apps", "reminders", "contacts", "docs", "media", "video", "markets")
STOP = {"the", "a", "an", "my", "that", "this", "it", "one", "other", "not", "no", "to", "for", "from",
        "was", "is", "are", "and", "but", "please", "now", "again", "instead", "wrong", "right",
        "folder", "file", "project", "drive", "account", "app", "application", "browser", "version",
        "chat", "message", "email", "report", "thing"}
RELATIONS = ("brother", "sister", "father", "mother", "uncle", "aunt", "cousin", "friend", "boss",
             "manager", "colleague", "wife", "husband", "son", "daughter", "teacher", "professor")


def _app_hit(t):
    return next((a for a in APPS if re.search(rf"\b{re.escape(a)}\b", t, re.I)), "")


def _clean(value):
    return re.sub(r"\s+", " ", (value or "").strip().strip(".,!?\"'"))


def _person(t, decision, history):
    if not re.search(r"\b(?:meant|wrong|other|not)\b", t, re.I):
        return None
    m = (re.search(r"\bi\s+(?:meant|said)\s+(?:it\s+for\s+)?(?P<c>[A-Za-z][\w.'-]*(?:\s+(?:from|at)\s+[\w'-]+)?)", t, re.I)
         or re.search(r"\bthe\s+other\s+(?P<c>[A-Za-z][\w'-]+)", t, re.I)
         or re.search(r"\bwrong\s+(?P<w>[A-Za-z][\w'-]+)\b[^.;]*?(?:i\s+meant|use|it'?s)\s+(?P<c>[A-Za-z][\w'-]+)", t, re.I))
    if not m:
        return None
    correct = _clean(m.groupdict().get("c"))
    wrong = _clean(m.groupdict().get("w") or "")
    if not correct or correct.lower() in STOP:
        return None
    # "I meant Edge" after "you opened the wrong application" is an application correction, not a person
    if correct.lower() in APPS or re.search(r"\b(?:app|application|browser|program)\b", t, re.I):
        return None
    decided = _clean(str((decision or {}).get("entity") or (decision or {}).get("args", {}).get("person") or ""))
    return {"error_type": "wrong_person", "incorrect": wrong or decided, "correct": correct,
            "confidence": 0.9, "explicit": True, "note": "owner named the person they meant"}


def _application(t, decision, history):
    correct = ""
    m = re.search(r"\b(?:use|open|launch|switch\s+to|prefer)\s+(?:the\s+)?(?P<c>[\w ]+?)(?:\s+instead|\s+for\s+(?:this|that|it))?\s*[.!]?$", t, re.I)
    if m:
        candidate = _clean(m.group("c"))
        correct = candidate if candidate.lower() in APPS else ""
    if not correct:
        correct = _app_hit(t) if re.search(r"\b(?:meant|should|use|want)\b", t, re.I) else ""
    wrong = ""
    m2 = re.search(r"\b(?:not|instead\s+of|rather\s+than|you\s+(?:opened|used|launched))\s+(?:the\s+)?(?P<w>[\w ]+?)\b", t, re.I)
    if m2:
        cand = _clean(m2.group("w"))
        wrong = cand if cand.lower() in APPS else _app_hit(cand)
    if not correct and not wrong:
        return None
    correct, wrong = correct.lower(), wrong.lower()
    decided_app = _clean(str((decision or {}).get("args", {}).get("app") or (decision or {}).get("app") or "")).lower()
    return {"error_type": "wrong_application", "incorrect": wrong or decided_app,
            "correct": correct, "confidence": 0.95 if (correct and wrong) else 0.8, "explicit": True,
            "note": "owner named the application they wanted"}


PATH = re.compile(r"(?P<p>[A-Za-z]:\\[^\s\"']+|(?:[\w.-]+[\\/])+[\w.-]+|\b[\w-]+\.(?:pdf|docx?|xlsx?|pptx?|png|jpe?g|txt|csv|md|mp4|mp3)\b)")
OTHER = re.compile(r"\b(?:the\s+)?other\s+(?P<what>folder|file|project|drive|account|one)\b", re.I)


def _file(t, decision, history):
    if not re.search(r"\b(?:wrong|not|other|instead|meant|should)\b", t, re.I):
        return None
    other = OTHER.search(t)
    m = PATH.search(t)
    correct = _clean(m.group("p")) if m else (_clean(other.group("what")) if other else "")
    if not correct:
        return None
    if other:
        correct = f"the other {correct}"  # the actual path is context; the lesson keeps the instruction
    wrong = ""
    m2 = re.search(r"\b(?:not|instead\s+of)\s+(?P<w>[A-Za-z]:\\[^\s\"']+|\b[\w-]+\.(?:pdf|docx?|xlsx?|pptx?|png|jpe?g|txt|csv|md)\b)", t)
    if m2:
        wrong = _clean(m2.group("w"))
    return {"error_type": "wrong_file", "incorrect": wrong, "correct": correct, "confidence": 0.85,
            "explicit": True, "note": "owner pointed at a different file/folder"}


def _parameter(t, decision, history):
    m = (re.search(r"\bi\s+said\s+(?P<c>[\w:.]+\s*(?:am|pm)?)\s*,?\s*(?:not|instead\s+of)\s*(?P<w>[\w:.]+)", t, re.I)
         or re.search(r"\bnot\s+(?P<w>\d[\w:.]*)\s*[,;]?\s*(?P<c>\d[\w:.]*)", t)
         or re.search(r"\b(?P<c>\d[\w:.]*)\s*(?:not|instead\s+of)\s*(?P<w>\d[\w:.]*)", t))
    if not m:
        return None
    return {"error_type": "wrong_parameter", "incorrect": _clean(m.group("w")), "correct": _clean(m.group("c")),
            "confidence": 0.85, "explicit": True, "note": "owner corrected a value"}


def _skill(t, decision, history):
    m = (re.search(r"\b(?:that|this|it)\s+should\s+(?:have\s+been|be|go\s+to|use)\s+(?:the\s+)?(?P<c>[\w ]+?)(?:\s+skill)?\s*[.!]?$", t, re.I)
         or re.search(r"\buse\s+(?:the\s+)?(?P<c>[\w_]+)(?:\s+skill)?\s+for\s+(?:that|this|it)\b", t, re.I)
         or re.search(r"\byou\s+should\s+have\s+(?P<c>search|searched|looked\s+it\s+up|browsed)\w*\b", t, re.I))
    if not m:
        return None
    correct = _clean(m.group("c")).lower().replace(" ", "_")
    correct = {"search": "web_search", "searched": "web_search", "looked_it_up": "web_search",
               "browsed": "browser"}.get(correct, correct)
    if correct not in SKILLS and not correct.endswith("_search"):
        return None
    return {"error_type": "wrong_skill", "incorrect": _clean(str((decision or {}).get("skill") or "")),
            "correct": correct, "confidence": 0.95, "explicit": True, "note": "owner named the capability"}



def _sequence(t, decision, history):
    if re.search(r"\b(?:you\s+skipped|wrong\s+order|first\b[^.]*\bthen\b|before\s+that|not\s+yet|too\s+early|too\s+late)\b", t, re.I) \
            and re.search(r"\b(?:should|not|wrong|skipped)\b", t, re.I):
        return {"error_type": "wrong_sequence", "incorrect": "", "correct": "", "confidence": 0.6,
                "explicit": True, "note": "steps ran in the wrong order"}
    return None


def _permission(t, decision, history):
    if re.search(r"\b(?:i\s+did\s?n'?t\s+ask|who\s+(?:told|asked)\s+you|you\s+should\s?n'?t\s+have|"
                 r"without\s+(?:asking|my\s+permission|telling\s+me)|do\s?n'?t\s+do\s+(?:that|it)\s+without|"
                 r"you\s+were\s+not\s+supposed\s+to)\b", t, re.I):
        return {"error_type": "wrong_permission", "incorrect": "", "correct": "", "confidence": 0.9,
                "explicit": True, "note": "an action was taken that should have been confirmed first"}
    return None


def _assumption(t, decision, history):
    if re.search(r"\b(?:you\s+assumed|stop\s+assuming|i\s+never\s+said|i\s+did\s?n'?t\s+say|do\s?n'?t\s+assume)\b", t, re.I):
        return {"error_type": "wrong_assumption", "incorrect": "", "correct": "", "confidence": 0.8,
                "explicit": True, "note": "the owner never said what was assumed"}
    return None


def _interpretation(t, decision, history):
    if re.search(r"\b(?:that'?s\s+not\s+what\s+i\s+(?:meant|asked|wanted|said)|not\s+what\s+i\s+(?:meant|asked|wanted)|"
                 r"you\s+misunderstood|misunderstood\s+me|that'?s\s+(?:wrong|incorrect)|wrong\s+answer|"
                 r"you\s+got\s+it\s+wrong|i\s+(?:did\s?n'?t|did\s+not)\s+ask\s+for\s+that)\b", t, re.I):
        return {"error_type": "wrong_interpretation", "incorrect": "", "correct": "", "confidence": 0.7,
                "explicit": True, "note": "the request was understood incorrectly"}
    return None


def _language(t, decision, history):
    m = (re.search(r"\b(?:answer|reply|speak|talk|write)\s+(?:to\s+me\s+)?in\s+(?P<c>english|urdu|pashto|roman\s+urdu)\b", t, re.I)
         or re.search(r"\bwhy\s+(?:are\s+you|did\s+you)\s+(?:answering|replying|writing|speaking)\s+in\s+(?P<w>\w+)", t, re.I))
    if not m:
        return None
    return {"error_type": "language_mismatch", "incorrect": _clean(m.groupdict().get("w")),
            "correct": _clean(m.groupdict().get("c")), "confidence": 0.9, "explicit": True,
            "note": "answer language"}


CHECKS = (_person, _application, _file, _parameter, _skill, _sequence, _permission, _assumption,
          _interpretation, _language)


def classify(text, decision=None, result=None, history=None):
    """A correction cue + a clear target -> classified error; a bare "no" -> unknown; chat -> None."""
    t = (text or "").strip()
    if not t or not GATE.search(t):
        if result and str((result or {}).get("status", "")).lower() in ("error", "failed"):
            return {"error_type": "tool_failure", "incorrect": str(result.get("detail") or "")[:200],
                    "correct": "", "confidence": 0.6, "explicit": False, "note": "the tool failed"}
        return None
    tt = t.rstrip(" .!?\"'")
    for check in CHECKS:
        try:
            got = check(tt, decision, history)
        except Exception:
            got = None
        if got:
            got["message"] = t[:500]
            return got
    return {"error_type": "unknown", "incorrect": "", "correct": "", "confidence": 0.4, "explicit": True,
            "message": t[:500], "note": "a correction without a clear target - ask before learning"}



def signature(error_type, wrong, right, domain=""):
    """Stable grouping key: same mistake about the same thing counts together (spec 10)."""
    norm = lambda s: " ".join(re.findall(r"[a-z0-9]+", (s or "").lower()))[:60]
    parts = [norm(error_type), norm(domain), norm(wrong), norm(right)]
    return "|".join(p for p in parts if p) or "unknown"


def record(error, interaction_id="", note="", domain=""):
    """Write one mistake row (used by repeated-mistake detection)."""
    if not error or not error.get("error_type"):
        return 0
    return store.add("mistakes", ts=store.now(), interaction_id=interaction_id or "",
                     error_type=error.get("error_type"), wrong=error.get("incorrect") or "",
                     right=error.get("correct") or "", domain=domain or "",
                     signature=signature(error.get("error_type"), error.get("incorrect"),
                                         error.get("correct"), domain),
                     note=note or error.get("note") or "")


def repeated(min_count=None):
    """Mistake groups that happened often enough to deserve a rule (spec 10)."""
    floor = int(min_count or store.cfg()["evidence"].get("candidate_rule", 3))
    got = store.rows("SELECT error_type, wrong, right, domain, signature, COUNT(*) n, MAX(ts) last_ts "
                     "FROM mistakes GROUP BY signature HAVING n >= ? ORDER BY n DESC, last_ts DESC", (floor,))
    out = []
    for r in got:
        n = r.pop("n")
        out.append({**r, "count": n})
    return out



def analyze_failure(text, decision=None, result=None, history=None, task_id=""):
    """Spec 14: after a failure, say WHY it failed and what lesson it should produce - without
    memorising one specific answer when the real problem is entity resolution."""
    error = classify(text, decision=decision, result=result, history=history)
    if not error:
        return None
    kind, cause = error["error_type"], TAXONOMY.get(error["error_type"], TAXONOMY["unknown"])
    lesson = ""
    if kind == "wrong_person":
        try:
            from ishti.learning import entities
            name = error.get("correct") or error.get("incorrect") or ""
            if len(entities.candidates(name)) >= 2:
                kind, cause = "entity_resolution_failure", "several entities share this name"
        except Exception:
            pass
        lesson = (f"When several people share the name \"{error.get('correct') or 'this name'}\", use "
                  "context and relationship information to pick the right one - and ask when it is ambiguous.")
    elif kind == "wrong_application":
        wrong = error.get("incorrect") or ""
        lesson = (f"Use {error.get('correct') or 'the app the owner named'} for this workflow"
                  + (f" (not {wrong})." if wrong else "."))
    elif kind == "wrong_skill":
        lesson = f"For this request the right capability is {error.get('correct')}."
    elif kind == "wrong_permission":
        lesson = "Confirm before this action - the owner wants a yes/no gate on it."
    elif kind == "language_mismatch":
        lesson = f"Answer in {error.get('correct')}."
    elif kind == "wrong_parameter":
        lesson = f"The correct value is {error.get('correct')}, not {error.get('incorrect')}."
    elif kind == "wrong_file":
        lesson = f"Use {error.get('correct')} for this task."
    elif kind == "wrong_sequence":
        lesson = "Keep the step order the owner expects for this workflow."
    return {"error_type": kind, "cause": cause, "correction": error.get("correct") or "",
            "incorrect": error.get("incorrect") or "", "lesson": lesson,
            "confidence": error.get("confidence", 0.5),
            "signature": signature(kind, error.get("incorrect"), error.get("correct")),
            "raw": error, "task_id": task_id}


if __name__ == "__main__":
    import os
    import tempfile
    store.set_db_path(os.path.join(tempfile.mkdtemp(), "e.db"))
    assert classify("what's the weather tomorrow") is None
    assert classify("how are you") is None
    # the spec's examples
    got = classify("No, use Chrome.")
    assert got["error_type"] == "wrong_application" and got["correct"] == "chrome", got
    got = classify("You opened the wrong application, I meant Edge")
    assert got["error_type"] == "wrong_application", got
    got = classify("I meant Ahmed from work")
    assert got["error_type"] == "wrong_person" and "Ahmed" in got["correct"], got
    got = classify("that should have been web search")
    assert got["error_type"] == "wrong_skill" and got["correct"] == "web_search", got
    got = classify("I said 5 not 10")
    assert got["error_type"] == "wrong_parameter" and got["correct"] == "5", got
    got = classify("no, use the other folder")
    assert got["error_type"] == "wrong_file" and "other folder" in got["correct"], got
    assert classify("you shouldn't have sent that without asking")["error_type"] == "wrong_permission"
    assert classify("answer in urdu please")["error_type"] == "language_mismatch"
    assert classify("that's not what I meant")["error_type"] == "wrong_interpretation"
    assert classify("you assumed I wanted the new one")["error_type"] == "wrong_assumption"
    assert classify("no") is None  # a bare "no" answered a pending question, not a correction claim
    assert classify("no, wrong")["error_type"] == "unknown"
    # repeated-mistake detection (spec 10)
    for i in range(3):
        record(classify("you opened edge, I meant chrome"), interaction_id=f"i{i}", domain="browser")
    sig = signature("wrong_application", "edge", "chrome", "browser")
    assert repeated(min_count=3)[0]["signature"] == sig, repeated(min_count=3)
    assert repeated(min_count=4) == []
    # failure analysis (spec 14)
    analysis = analyze_failure("wrong Ahmed, I meant Ahmed from work")
    assert analysis["error_type"] == "wrong_person" and analysis["lesson"], analysis
    assert analyze_failure("what is 2+2") is None
    print("[self-check] learning.errors OK - taxonomy, correction targets, signatures, "
          "repeated detection, failure analysis")
