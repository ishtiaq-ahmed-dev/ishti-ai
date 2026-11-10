"""Background work: the request runs in its own session off the chat, the answer (or a yes/no question) reaches the bell."""
import time

import pytest

from ishti.skills.background import skill as bg


@pytest.mark.parametrize("text,inner", [("research laptops under 200k in the background", "research laptops under 200k"),
                                        ("in the background: summarize my unread email", "summarize my unread email"),
                                        ("compare these phones and let me know when it's done", "compare these phones")])
def test_requests_are_recognised(text, inner):
    assert bg.request(text) == inner


@pytest.mark.parametrize("text", ["cancel everything you are doing in the background", "what is running in the background",
                                  "list background tasks", "tell me when"])
def test_control_and_questions_are_not_jobs(text):
    assert not bg.match(text)


def _wait(pred, limit=5.0):
    end = time.time() + limit
    while time.time() < end and not pred():
        time.sleep(0.05)
    return pred()


def test_answer_lands_in_the_bell_from_its_own_session():
    from ishti import notifications
    seen = []
    out = bg.run("check the weather in Swat in the background",
                 {}, handle=lambda text, session_id, channel: seen.append((text, session_id, channel)) or {"final_answer": "Sunny, 18C"})
    assert "Started in the background" in out["reply"]
    assert _wait(lambda: any("Sunny, 18C" in (n.get("body") or "") for n in notifications.recent(20)))
    text, sid, channel = seen[0]
    assert text == "check the weather in Swat" and sid.startswith("bg-") and channel == "scheduled"


def test_a_question_waits_in_the_bell_as_answerable():
    from ishti import notifications
    bg.run("send the weekly report to Ali in the background", {},
           handle=lambda text, session_id, channel: {"final_answer": "Send it to Ali? (yes/no)"})
    assert _wait(lambda: any((n.get("meta") or {}).get("answerable") and "Send it to Ali" in (n.get("body") or "")
                             for n in notifications.recent(20)))


def test_routing_beats_noun_cues():
    from ishti import dispatcher, tool_routing
    assert tool_routing.route("check the weather in Swat in the background", dispatcher.SKILLS).get("skill") == "background"
