"""Regression test: one WhatsApp messenger request must stay ONE task, even when it asks for several messages.

The owner's log (data/sessions/sess_20260927_110742_0bc6b6.json) shows the request
"open whastap and send mesage to bhai hi how are you in two diffrent messages" being split into two plan
steps, each re-opening the chat and reporting "couldn't find the message box".

Run:  $env:PYTHONIOENCODING='utf-8'; D:\ISHTI\venv\Scripts\python.exe D:\ISHTI\tests\test_planner_messenger.py
"""
import sys

sys.path.insert(0, r"D:\ISHTI")

import ishti.router as router_mod  # noqa: E402
import ishti.planner as planner  # noqa: E402


class _Skill:  # what candidate() needs from a skill object
    def matches(self, text):
        return False


SKILLS = {n: _Skill() for n in ("whatsapp", "apps", "chat", "web_search", "browser", "weather", "video")}
SENT = {}

KEYWORDS = (("whastap", "whatsapp"), ("whatsapp", "whatsapp"), ("send", "whatsapp"), ("weather", "weather"),
            ("video", "video"), ("search the web", "web_search"), ("flights", "web_search"))


def fake_route(text, skills, exclude=None):
    """A stand-in for Laya: says which skill each clause is about, without loading any model."""
    key = text.lower()
    skill = next((s for k, s in KEYWORDS if k in key), "chat")
    SENT[text] = skill
    return {"skill": skill, "confidence": 1.0, "source": "test"}


saved = router_mod.route
router_mod.route = fake_route
try:
    text = "open whastap and send mesage to bhai hi how are you in two diffrent messages"
    assert not planner.candidate(text, SKILLS), "a messenger request must not be split into several tasks"
    # ... but two genuinely different tasks still are
    assert planner.candidate("send a message to ali on whatsapp and search the web for flights", SKILLS)
    assert planner.candidate("check the weather and edit the video in downloads", SKILLS)
finally:
    router_mod.route = saved

print("[self-check] planner messenger OK - one WhatsApp request stays one task; mixed tasks still split")
