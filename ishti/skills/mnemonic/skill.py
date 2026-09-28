"""Memory aids (local model): mnemonics for lists, memory-palace walks, peg systems.

Deterministic routing; the creative part belongs to the planner model.
"""
import re

from ishti import llm
from ishti.untrusted import fence
from ishti.skills import trace

I = re.I

PALACE = re.compile(r"\bmemory\s+palace\b|\bloci\b", I)
TRIGGER = re.compile(r"^\s*(?:please\s+)?(?:can\s+you\s+)?(?:make|create|give\s+me)\s+(?:a\s+|an\s+)?(?:mnemonic|acronym|memory\s+(?:aid|trick)|acrostic)\s+(?:for|to\s+remember|of)\s*[:\-]?\s*(?P<body>.+)$|"
                     r"^\s*(?:help\s+me\s+)?(?:remember|memori[sz]e)\s+(?:this\s+)?list\s*[:\-]?\s*(?P<body2>.+)$|"
                     r"^\s*how\s+(?:can\s+i|do\s+i)\s+remember\s*[:\-]?\s*(?P<body3>.+)$|"
                     r"^\s*memory\s+palace\s+(?:for|of)\s*[:\-]?\s*(?P<body4>.+)$", I | re.S)


def match(text):
    return bool(TRIGGER.match(text.strip()))


def run(text, session):
    m = TRIGGER.match(text.strip())
    if not m:
        return None
    gd = m.groupdict()
    body = (gd.get("body") or gd.get("body2") or gd.get("body3") or gd.get("body4") or "").strip()
    if not body:
        return None
    kind = ("a memory-palace walk (name 5-10 familiar locations in order and attach one item to each, "
            "with a vivid image per stop)" if PALACE.search(text) else
            "the best mnemonic for it: try an acronym or acrostic first (first letters), a rhyme or a "
            "mini-story linking the items second — pick whichever is most memorable and give both the "
            "device and how to use it")
    trace(session, "mnemonic", kind.split("(")[0].strip())
    reply = llm.ask(f"Items to remember:\n{fence(body)}\n\nCreate {kind}.",
                    role="planner", system=None, temperature=0.7, max_tokens=400)
    return {"reply": reply, "sources": []}


if __name__ == "__main__":
    assert match("make a mnemonic for: red orange yellow green blue indigo violet")
    assert match("create an acronym for this list: plan, execute, review, ship")
    assert match("help me memorize this list: insulin, glucose, pancreas")
    assert match("how can I remember the phases of mitosis")
    assert match("memory palace for: keys, wallet, passport, charger")
    assert not match("remember that my name is Ishtiaq")  # memory skill's
    assert not match("remind me at 5")  # reminders'
    print("[self-check] mnemonic OK - routing for mnemonics/acrostics/palaces")
