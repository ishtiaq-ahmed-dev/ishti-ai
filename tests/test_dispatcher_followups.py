r"""Isolated replay tests for the dispatcher's Issue-1 fixes (no network, no owner memory touched):

  * the language lock ("Talk to me in English.") is saved as a fact, not routed to research
  * a skill's "sticky" pending question survives the owner's answer, complaint or correction
    ("The name is Bai.", "No send it.", "not sent yet send her now")
  * a normal follow-up still drops a non-sticky question (no regression)
  * the follow-up patterns do not swallow ordinary requests ("send hi to bhai")

Run:  $env:PYTHONIOENCODING='utf-8'; D:\ISHTI\venv\Scripts\python.exe D:\ISHTI\tests\test_dispatcher_followups.py
"""
import os
import sys
import tempfile

ROOT = r"D:\ISHTI"
sys.path.insert(0, ROOT)

import ishti.facts as facts_mod  # noqa: E402
import ishti.test_mode  # noqa: E402
from orchestrator.memory.user_profile import scratch_memory  # noqa: E402
import ishti.dispatcher as D  # noqa: E402

# ---- pure patterns -------------------------------------------------------------------------------------
assert D.FOLLOW_UP.search("not sent yet send her now")
assert D.FOLLOW_UP.search("No send it.")
assert D.FOLLOW_UP.search("resend that")
assert D.FOLLOW_UP.search("send it now")
assert D.FOLLOW_UP.search("didn't send it")
assert D.FOLLOW_UP.search("why didn't you send it")
# ordinary requests must NOT be treated as follow-ups (they would be rewritten / wait for another request)
assert not D.FOLLOW_UP.search("send a message to Ali")
assert not D.FOLLOW_UP.search("send hi to bhai")
assert not D.FOLLOW_UP.search("message my brother that I am late")
assert not D.FOLLOW_UP.search("open WhatsApp and send the message hi to bhai")

assert D.LANGUAGE_CMD.search("Talk to me in English.")
assert D.LANGUAGE_CMD.search("please answer in urdu")
assert D.LANGUAGE_CMD.search("switch your language to Pashto")
assert D.LANGUAGE_CMD.search("talk to me in my own language")
assert not D.LANGUAGE_CMD.search("write a poem in English about rain")  # a request, not a preference

assert D.CLAIM.search("I have sent a voice message to Bhai")
assert D.CLAIM.search("Sent 'hi' to Bhai on WhatsApp.")

# ---- state tests ---------------------------------------------------------------------------------------
with scratch_memory():
    db = os.path.join(tempfile.mkdtemp(prefix="ishti_facts_"), "facts.db")
    saved = facts_mod._facts
    facts_mod._facts = facts_mod.Facts(db)
    saved_remember, saved_learn = D._remember, D._learn
    D._remember = lambda *a, **k: None      # never write test chatter into the real Chroma memory
    D._learn = lambda *a, **k: None         # never learn facts from test text
    saved_route = D.route
    saved_candidate = D.planner.candidate
    D.planner.candidate = lambda *a, **k: False   # the multi-step planner (added later) must not
    # intercept these rows - this file tests the follow-up patterns + honesty layer through the
    # route() stub, the same way it already isolates memory; without this the planner once ran a
    # real deep_research pass for 'send hi to bhai' inside the test.
    saved_runs = {n: D.SKILLS[n].run for n in ("notes", "whatsapp", "browser", "chat")}
    D.SKILLS["chat"].run = lambda text, s: {"reply": "ROUTED NORMALLY", "sources": []}
    D.route = lambda text, skills, exclude=None: {"skill": "chat", "confidence": 1.0, "source": "test"}
    try:
        # 1. the language lock: saved, answered, never researched
        res = D.handle("Talk to me in English.", session_id="t1")
        assert res["skill"] == "owner", res
        assert "English" in res["final_answer"], res["final_answer"]
        assert D.get_facts().get("owner").get("prefers_language") == "English"
        assert D.preferred_language() == "English"
        assert D.LANG_TARGET[D.preferred_language()] == "English"

        # 2. clearing the lock
        D.handle("talk to me in my own language", session_id="t1")
        assert D.preferred_language() is None, D.preferred_language()

        # 3. a sticky pending: the owner's complaint is the same flow, not a new request.
        # T0 skill (notes) on purpose: under ISHTI_TEST_MODE=1 the dispatcher blocks every T2 run
        # (that is the safety contract, asserted in 3b) - the sticky-pending CONTRACT itself is
        # tier-independent, so it is exercised through a skill the safety layer never intercepts.
        seen = []
        D.SKILLS["notes"].run = lambda text, s: (seen.append(text), None)[1]  # declines on purpose
        D.SESSIONS["t2"] = {"id": "t2", "history": [], "last_entity": None,
                            "pending": {"skill": "notes", "sticky": True, "ask": "what should the note say"}}
        res = D.handle("not sent yet send her now", session_id="t2")
        assert seen == ["not sent yet send her now"], seen       # the skill saw the complaint
        assert D.SESSIONS["t2"]["pending"] is None               # declined -> the slot is released
        assert res["final_answer"] == "ROUTED NORMALLY", res     # ... and the message routes normally

        # 3b. the same complaint against a T2 pending IS blocked under test mode - and the block
        # itself is the asserted contract (it must reach the last step with the skill named)
        if ishti.test_mode.enabled():
            def _t2_stub(text, s):
                seen.append(text)
                return {"reply": "REAL SEND (must never happen)", "sources": []}
            D.SKILLS["whatsapp"].run = _t2_stub
            D.SESSIONS["t2b"] = {"id": "t2b", "history": [], "last_entity": None,
                                 "pending": {"skill": "whatsapp", "sticky": True, "ask": "who"}}
            res = D.handle("not sent yet send her now", session_id="t2b")
            assert "test-mode" in res["final_answer"] and "stopped at the last step" in res["final_answer"], res
            assert "REAL SEND" not in res["final_answer"], res   # the stub never ran

        # 4. a sticky pending whose question the owner answers stays the skill's
        D.SKILLS["notes"].run = lambda text, s: (seen.append(text), {"reply": "SAVED (test)", "sources": []})[1]
        D.SESSIONS["t3"] = {"id": "t3", "history": [], "last_entity": None,
                            "pending": {"skill": "notes", "sticky": True, "ask": "what should the note say"}}
        res = D.handle("Yes the name is Bai.", session_id="t3")
        assert seen[-1] == "Yes the name is Bai." and res["final_answer"] == "SAVED (test)", res

        # 5. no regression: a plain follow-up still drops a pending yes/no question (not sticky)
        seen.clear()
        D.SKILLS["browser"].run = lambda text, s: (seen.append(text), {"reply": "BROWSER", "sources": []})[1]
        D.SESSIONS["t4"] = {"id": "t4", "history": [], "last_entity": None,
                            "pending": {"skill": "browser", "gate": "open it?"}}
        res = D.handle("what about the other site instead", session_id="t4")
        assert seen == [], seen and "browser must not see an unrelated follow-up"
        assert D.SESSIONS["t4"]["pending"] is None
        assert res["final_answer"] == "ROUTED NORMALLY", res

        # 6. tool honesty: an unverified action claim is followed by an explicit warning
        # (routed through the T0 chat stub: the honesty suffix is claim-shaped, not tier-shaped;
        # a T2 route would be test-mode-blocked before its reply could carry the claim)
        D.route = lambda text, skills, exclude=None: {"skill": "chat", "confidence": 1.0, "source": "test"}
        # no_escalate: the dispatcher's own escape hatch - without it the (later-added) escalation
        # hop saw the claim stub 'fall short' and ran a real deep_research pass inside the test
        D.SKILLS["chat"].run = lambda text, s: {"reply": "Sent 'hi' to Bhai on WhatsApp.", "sources": [],
                                                "verified": False, "no_escalate": True}
        D.SESSIONS["t5"] = {"id": "t5", "history": [], "last_entity": None, "pending": None}
        res = D.handle("send hi to bhai", session_id="t5")
        assert "couldn't confirm that actually went through" in res["final_answer"], res["final_answer"]
    finally:
        D._remember, D._learn, D.route = saved_remember, saved_learn, saved_route
        D.planner.candidate = saved_candidate
        for n, run in saved_runs.items():
            D.SKILLS[n].run = run
        for sid in ("t1", "t2", "t2b", "t3", "t4", "t5"):
            D.SESSIONS.pop(sid, None)
        facts_mod._facts = saved

print("[self-check] dispatcher follow-ups OK - language lock saved and answered, sticky pending survives "
      "answers/complaints, plain follow-ups still drop questions, unverified sends are flagged")

assert not D.CLAIM.search("The message box wasn't found in Bhai's chat.")
