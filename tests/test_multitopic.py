"""Multi-tasking: many topics on the go in ONE conversation, at the same time.

The checklist asks for this explicitly ("tested with 3-4 simultaneous topics in one conversation, target ~10 without
cross-contamination") and for the pause rule ("a background task (a price lookup) keeps running when the topic
changes; its result is reported at a natural pause, not mid-thought"). Nothing exercised the dispatcher's concurrency
in one session before this: ishti/check.py only asserts that two Views share history while keeping their own trace.

The fake skills are offline and deterministic (no model, no network): each topic sleeps, so the requests really do
overlap, and each answers with its own token. Cross-contamination then shows up as another topic's token in an answer,
in the wrong history entry, or in the wrong task record. Also covered: ten topics at once, a follow-up waiting for the
request it is about (and NOT cancelling it), one failing skill not taking the conversation down (crash isolation), and
a sensitive (T2) confirmation staying with its own topic.

Run:  D:\\ISHTI\\venv\\Scripts\\python.exe D:\\ISHTI\\tests\\test_multitopic.py
"""
import os
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ishti import CONFIG  # noqa: E402

_tmp = tempfile.mkdtemp()
CONFIG["memory"].update(db_path=os.path.join(_tmp, "t.db"), chroma_dir=os.path.join(_tmp, "chroma"))

from ishti import dispatcher as d  # noqa: E402  (config patched first)
from ishti.skills import ask_first  # noqa: E402

TOPICS = ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet")
SLEEP = 0.4
sent = []  # a T2 action that must fire exactly once, for its own topic


def fake(name, matches, answer, sleep=SLEEP, tier="T1"):
    """A registry entry as the loader builds it: the key and manifest["name"] are the same (see skills/__init__.py)."""
    def run(text, session):
        time.sleep(sleep)
        return {"reply": f"{answer}: {text}", "sources": [{"id": 1, "url": "https://example.com", "title": name}]}
    return SimpleNamespace(name=name, manifest={"name": name, "tier": tier, "priority": 0, "route": f"x {name}"},
                           status="working", problem="", matches=lambda t, n=matches: t.startswith(n), run=run)


def boom(text, session):
    time.sleep(0.2)
    raise RuntimeError("skill exploded")


for topic in TOPICS:
    d.SKILLS[f"probe_{topic}"] = fake(f"probe_{topic}", topic, f"{topic.upper()} price 100")
d.SKILLS["probe_boom"] = SimpleNamespace(name="probe_boom", manifest={"name": "probe_boom", "tier": "T1", "priority": 0,
                                                                       "route": "x"},
                                         status="working", problem="", matches=lambda t: t.startswith("boom"),
                                         run=boom)
# the follow-up below is about a running request, so it must wait for it - and still be its own topic
d.SKILLS["probe_followup"] = fake("probe_followup", "what about", "FOLLOWUP answer", sleep=0.05)
d.SKILLS["probe_send"] = SimpleNamespace(
    name="probe_send", manifest={"name": "probe_send", "tier": "T2", "priority": 0, "route": "x"}, status="working",
    problem="", matches=lambda t: t.startswith("send to ali"),
    run=lambda t, s: ask_first(s, "probe_send", f"Send '{t}' to Ali", lambda: sent.append(t) or "Sent it."))

sid = "multi_" + os.urandom(3).hex()
from orchestrator.memory.user_profile import scratch_memory  # noqa: E402

# This test is about the dispatcher's own concurrency: the per-session in-flight count, the wait a follow-up owes to
# the request it is about, the tracked tasks and the T2 gate. Two layers sit in front of routing and are switched off
# so the run stays offline and deterministic (both have their own coverage: tests/test_planner_messenger.py and
# ishti.check exercise the planner; a live run showed `contextualize` rewriting "what about the price" into a full
# request about the earlier topics, which is correct behaviour but pulls in the network):
#   contextualize()  - rewrites a follow-up using the conversation so far
#   planner.candidate() - decides a rewritten request is several tasks and plans them
real_contextualize, real_candidate = d.contextualize, d.planner.candidate
d.contextualize = lambda text, s: text
d.planner.candidate = lambda *a, **k: False

with scratch_memory():  # chats, profile and archives in a temp dir
    try:
        # ---- 1. ten topics at once: every answer comes back to its own topic ---------------------------------
        results, t0 = {}, time.time()

        def ask(topic):
            results[topic] = d.handle(f"{topic} stock price", sid)

        threads = [threading.Thread(target=ask, args=(t,)) for t in TOPICS]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        elapsed = time.time() - t0
        assert len(results) == len(TOPICS), results
        assert elapsed < len(TOPICS) * SLEEP / 2, \
            f"the {len(TOPICS)} requests did not overlap ({elapsed:.2f} s; serial would be {len(TOPICS) * SLEEP:.1f} s)"
        for topic, res in results.items():
            assert res["skill"] == f"probe_{topic}", (topic, res["skill"])
            assert topic in res["final_answer"], (topic, res["final_answer"])
            for other in TOPICS:
                if other != topic:
                    assert other not in res["final_answer"], (topic, other, res["final_answer"])
                    assert other not in str(res.get("route", {})), (topic, other, res["route"])

        # ---- 2. every turn landed in the shared history, paired with its own reply ---------------------------
        hist = d.SESSIONS[sid]["history"]
        pairs = {hist[i]["content"].split()[0]: hist[i + 1]["content"] for i in range(0, len(hist) - 1, 2)}
        assert len(hist) == 2 * len(TOPICS) and sorted(pairs) == sorted(TOPICS), (len(hist), sorted(pairs))
        for topic, reply in pairs.items():
            assert topic in reply and all(o not in reply for o in TOPICS if o != topic), (topic, reply)

        # ---- 3. the task tracker holds them all, each finished, each its own topic ---------------------------
        tasks = [t for t in d.SESSIONS[sid]["tasks"] if t["status"] == "done"]
        assert len(tasks) == len(TOPICS) and {t["topic"].split()[0] for t in tasks} == set(TOPICS), tasks
        assert all(t["result"] for t in tasks), tasks  # each carries its own result, so "what's my status" can tell them apart

        # ---- 4. a follow-up waits for the request it is about, then runs as its own topic --------------------
        slow = threading.Thread(target=lambda: d.handle("bravo slow lookup", sid))
        slow.start()
        time.sleep(0.15)  # the slow request is definitely running now
        events = []
        follow = d.handle("what about the price", sid, on_event=events.append)
        waited = [e for e in events if e.get("type") == "tool" and e.get("name") == "wait"]
        assert waited, "a follow-up must wait for the request it is about"
        assert waited[-1].get("label", "").endswith("earlier request finished"), waited[-1]
        assert follow["skill"] == "probe_followup" and "FOLLOWUP" in follow["final_answer"], follow
        slow.join()
        assert d.SESSIONS[sid]["running"] == 0, d.SESSIONS[sid].get("running")

        # ---- 5. a failing skill is contained: the conversation carries on -----------------------------------
        bad = d.handle("boom now", sid)
        assert "RuntimeError" in bad["final_answer"] and "exploded" in bad["final_answer"], bad["final_answer"]
        after = d.handle("alpha again", sid)
        assert after["skill"] == "probe_alpha" and "again" in after["final_answer"], after  # or the session is stuck
        assert d.SESSIONS[sid]["running"] == 0, d.SESSIONS[sid].get("running")

        # ---- 6. a T2 confirmation belongs to its own topic and fires once -----------------------------------
        first = d.handle("send to ali the report", sid)
        assert first["final_answer"].endswith("(yes/no)") and not sent, first["final_answer"]
        d.handle("send to ali the report", sid)  # the double trigger: asked again, still nothing sent
        assert not sent, sent
        yes = d.handle("yes", sid)
        assert sent == ["send to ali the report"] and yes["final_answer"] == "Sent it.", (sent, yes["final_answer"])
        assert d.SESSIONS[sid]["pending"] is None, d.SESSIONS[sid]["pending"]
        print(f"[self-check] multitopic OK - {len(TOPICS)} topics at once ({elapsed:.2f} s for {len(TOPICS)} x {SLEEP} s "
              f"of work), no cross-contamination in answers/history/tasks, a follow-up waited for the request it was "
              f"about, a failing skill contained, T2 gate kept to its topic")
    finally:
        d.contextualize, d.planner.candidate = real_contextualize, real_candidate
        for n in list(d.SKILLS):
            if n.startswith("probe_"):
                d.SKILLS.pop(n, None)
        d.SESSIONS.pop(sid, None)
        try:
            d.get_conversation_manager().delete_session(sid)
        except Exception:
            pass
