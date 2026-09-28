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
    saved_runs = {n: D.SKILLS[n].run for n in ("whatsapp", "browser", "chat")}
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

        # 3. a sticky pending: the owner's complaint is the same send flow, not a new request
        seen = []
        D.SKILLS["whatsapp"].run = lambda text, s: (seen.append(text), None)[1]  # declines on purpose
        D.SESSIONS["t2"] = {"id": "t2", "history": [], "last_entity": None,
                            "pending": {"skill": "whatsapp", "sticky": True, "ask": "who"}}
        res = D.handle("not sent yet send her now", session_id="t2")
        assert seen == ["not sent yet send her now"], seen       # the send flow saw the complaint
        assert D.SESSIONS["t2"]["pending"] is None               # declined -> the slot is released
        assert res["final_answer"] == "ROUTED NORMALLY", res     # ... and the message routes normally

        # 4. a sticky pending whose question the owner answers stays the skill's
        D.SKILLS["whatsapp"].run = lambda text, s: (seen.append(text), {"reply": "SENT (test)", "sources": []})[1]
        D.SESSIONS["t3"] = {"id": "t3", "history": [], "last_entity": None,
                            "pending": {"skill": "whatsapp", "sticky": True, "ask": "who"}}
        res = D.handle("Yes the name is Bai.", session_id="t3")
        assert seen[-1] == "Yes the name is Bai." and res["final_answer"] == "SENT (test)", res

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
        D.route = lambda text, skills, exclude=None: {"skill": "whatsapp", "confidence": 1.0, "source": "test"}
        D.SKILLS["whatsapp"].run = lambda text, s: {"reply": "Sent 'hi' to Bhai on WhatsApp.", "sources": [],
                                                    "verified": False}
        D.SESSIONS["t5"] = {"id": "t5", "history": [], "last_entity": None, "pending": None}
        res = D.handle("send hi to bhai", session_id="t5")
        assert "couldn't confirm that actually went through" in res["final_answer"], res["final_answer"]
    finally:
        D._remember, D._learn, D.route = saved_remember, saved_learn, saved_route
        for n, run in saved_runs.items():
            D.SKILLS[n].run = run
        for sid in ("t1", "t2", "t3", "t4", "t5"):
            D.SESSIONS.pop(sid, None)
        facts_mod._facts = saved

print("[self-check] dispatcher follow-ups OK - language lock saved and answered, sticky pending survives "
      "answers/complaints, plain follow-ups still drop questions, unverified sends are flagged")

assert not D.CLAIM.search("The message box wasn't found in Bhai's chat.")
