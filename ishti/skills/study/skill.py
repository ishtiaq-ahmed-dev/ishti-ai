"""Study tools (local models, ISHTI's own planner).

make flashcards from <text> (saved to data/flashcards.db for spaced repetition with reminders)
quiz me on <topic> / explain <concept> simply / study plan for <subject> / check my answer
"""
import json
import os
import re
import sqlite3
import threading

from ishti import CONFIG, llm
from ishti.untrusted import fence
from ishti.skills import trace

I = re.I
DB = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "flashcards.db")
_lock = threading.Lock()

_OPS = [
    # "make 10 flashcards on photosynthesis" is planners' labelled Anki-CSV case; study's flashcards
    # are the saved-for-review kind: "make flashcards from <text>"
    ("flashcards", re.compile(r"^\s*(?:please\s+)?(?:can\s+you\s+)?(?:make|create|generate)\s+(?:me\s+)?flashcards?\s+from\s*[:\-]?\s*(?P<body>.+)$", I | re.S)),
    ("quiz", re.compile(r"^\s*(?:please\s+)?(?:can\s+you\s+)?quiz\s+me\s+(?:on|about|for)\s*[:\-]?\s*(?P<body>.+)$", I | re.S)),
    # "explain <topic> simply" — study's simple-explainer takes explicit teach-me forms; the bare
    # "explain quantum entanglement simply" chat case stays with chat (labelled), so require
    # a teach/eli5 marker or the like-i'm-5 form.
    ("simple", re.compile(r"^\s*(?:please\s+)?(?:can\s+you\s+)?(?:teach\s+me|eli5)\s*[:\-]?\s*(?P<body>.+)$|"
                          r"^\s*(?:please\s+)?(?:can\s+you\s+)?(?:explain|what\s+is|what\s+are)\s+(?P<body2>.+?)\s+like\s+i'?m\s+(?:5|five|a\s+beginner|new)\s*\??$", I)),
    ("plan", re.compile(r"^\s*(?:please\s+)?(?:can\s+you\s+)?(?:make|create)\s+(?:me\s+)?a\s+study\s+plan\s+(?:for|about)\s*[:\-]?\s*(?P<body>.+)$", I | re.S)),
    ("grade", re.compile(r"^\s*(?:is\s+my\s+answer|check\s+my\s+answer|grade\s+my\s+answer)\s*[:\-]?\s*(?P<body>.+)$", I | re.S)),
]


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS cards(id INTEGER PRIMARY KEY, ts REAL, topic TEXT, front TEXT, back TEXT, box INTEGER DEFAULT 1, last_reviewed REAL DEFAULT 0)")
    return c


def match(text):
    return any(rx.match(text.strip()) for _n, rx in _OPS)


def run(text, session):
    t = text.strip()
    for name, rx in _OPS:
        m = rx.match(t)
        if not m:
            continue
        body = (m.group("body") or "").strip()
        if not body:
            return None
        trace(session, "study", name)
        if name == "flashcards":
            raw = llm.ask("Turn the TEXT into 5-10 flashcards for the owner. Reply ONLY a JSON object "
                          '{"cards": [{"front": "...", "back": "..."}]}. Fronts are short questions; backs are '
                          "precise answers.", role="planner", system=None, temperature=0.2, max_tokens=1500,
                          response_format={"type": "json_object"})
            try:
                cards = json.loads(raw)["cards"][:12]
            except Exception:
                return {"reply": "I couldn't structure flashcards from that - try pasting clearer text.", "sources": []}
            topic = body[:60]
            with _lock, _db() as c:
                c.executemany("INSERT INTO cards(ts, topic, front, back) VALUES(?,?,?,?)",
                              [(os.times()[0], topic, c2["front"][:300], c2["back"][:600]) for c2 in cards])
            lines = "\n".join(f"- {c2['front']}" for c2 in cards)
            return {"reply": f"Made {len(cards)} flashcards on \"{topic}\" (saved for review):\n{lines}", "sources": []}
        if name == "quiz":
            raw = llm.ask("Write 5 quiz questions on the TOPIC, mixed difficulty. Reply ONLY JSON "
                          '{"questions": ["q1", "q2", ...]}. No answers shown.', role="planner", system=None,
                          temperature=0.4, max_tokens=800, response_format={"type": "json_object"})
            try:
                qs = json.loads(raw)["questions"][:5]
            except Exception:
                return {"reply": "I couldn't write a quiz on that topic - be more specific.", "sources": []}
            return {"reply": "Quiz on " + body + ":\n" + "\n".join(f"{i+1}. {q}" for i, q in enumerate(qs)) +
                    "\n\nAnswer any and say 'check my answer: ...'", "sources": []}
        if name == "simple":
            return {"reply": llm.ask(f"Explain to a beginner: {body}. Use a real-world analogy, 3-5 sentences, "
                                     "no jargon.", role="planner", system=None, temperature=0.4, max_tokens=400),
                    "sources": []}
        if name == "plan":
            from ishti.skills.planners.skill import op_study_plan
            out = op_study_plan(f"make a study plan for {body}", session)
            return {"reply": out, "sources": []}
        if name == "grade":
            return {"reply": llm.ask("The owner gives their ANSWER to an exam-style question. Grade it: say "
                                     "correct / partially correct / incorrect, what's right, what's missing, and the "
                                     "ideal answer in 2 sentences.\n\nANSWER:\n" + fence(body),
                                     role="planner", system=None, temperature=0.2, max_tokens=400), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    DB = os.path.join(tempfile.mkdtemp(), "f.db")
    assert match("make flashcards from: the water cycle has evaporation, condensation, precipitation")
    assert not match("make 10 flashcards on photosynthesis")  # planners' labelled Anki-CSV case
    assert match("quiz me on world war 2")
    assert match("teach me: neural networks")
    assert match("explain backpropagation like I'm 5")
    assert not match("explain quantum entanglement simply")  # chat's labelled conversational case
    assert match("make a study plan for data structures exam")
    assert match("check my answer: the water cycle is rain")
    assert not match("remind me to study at 5")
    print("[self-check] study OK - routing for flashcards/quiz/simple/plan/grade")
