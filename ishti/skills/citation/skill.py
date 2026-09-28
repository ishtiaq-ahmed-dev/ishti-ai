"""Reference formatting (local model): messy reference -> clean APA / MLA / Harvard / IEEE.

The style rules live in the prompt; routing is deterministic so no classifier call.
"""
import re

from ishti import llm
from ishti.untrusted import fence
from ishti.skills import trace

I = re.I

STYLE = re.compile(r"\b(apa|mla|harvard|ieee|chicago)\b", I)
TRIGGER = re.compile(r"^\s*(?:please\s+)?(?:can\s+you\s+)?(?:cite\s+(?:this|it|the\s+following)|"
                     r"(?:apa|mla|harvard|ieee|chicago)\s+(?:citation|reference)\s*(?:for|of)?|format\s+(?:this|the)\s+(?:reference|citation)|"
                     r"bibliography\s+(?:entry|format)\s+(?:for|of)|reference\s+(?:for|of)\s*[:\-])\s*[:\-]?\s*(?P<body>.+)$", I | re.S)


def match(text):
    return bool(TRIGGER.match(text.strip()))


def run(text, session):
    m = TRIGGER.match(text.strip())
    if not m:
        return None
    body = (m.group("body") or "").strip()
    if not body:
        return None
    st = (STYLE.search(text) or [None, "APA"])[1].upper()
    trace(session, "citation", f"formatting in {st}")
    reply = llm.ask(
        f"Format this reference in {st} style (7th edition APA, 9th MLA, or the standard IEEE/Harvard forms). "
        "If a piece is missing, keep its place without inventing data. Output only the formatted citation, "
        "then one short line noting anything you could not verify.\n\nREFERENCE:\n" + fence(body),
        role="planner", system=None, temperature=0.1, max_tokens=350)
    return {"reply": reply, "sources": []}


if __name__ == "__main__":
    assert match("cite this in apa: Khan, A. 2023. Local AI assistants. Journal of Things, 5(2), 12-20.")
    assert match("mla citation for: Deep Learning by Goodfellow, MIT Press 2016")
    assert match("format this reference: https://example.com/article title here")
    assert match("ieee citation: some paper")
    assert not match("what's the weather")
    assert not match("summarize this: text")  # writing skill's
    print("[self-check] citation OK - routing for apa/mla/harvard/ieee formatting")
