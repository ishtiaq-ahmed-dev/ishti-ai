"""E: ISHTI never SPEAKS the process. Step labels, tool names and progress lines stay on screen; the final answer
is the only thing voiced. Offline: no audio device, no synthesis - the talker's queue is inspected directly."""
import re

from ishti import dispatcher, talk

CHATTY = [
    "Searching the internet.",
    "Searching the web for that",
    "Opening the top 3 searches",
    "Opening WhatsApp",
    "Trying a different search",
    "Found 4 sources",
    "3 sources found",
    "Reading through the results",
    "Reading the page",
    "Transcribing the voice note",
    "Rendering the PDF",
    "Cutting the video",
    "Step 2: calling the markets skill",
    "Tool: browser",
    "Waiting for your earlier request to finish first",
    "Let me check that for you",
    "One moment",
    "   ",
]

ANSWERS = [
    "The weather tomorrow in Peshawar is sunny and 34 degrees.",
    "You have three reminders: call Ali at 5 pm, pay the rent, and take your medicine.",
    "Opening hours at the bank are 9 to 5.",
    "I found three flights from Islamabad to Dubai.",
    "Searching the web is what I do when a question needs current facts.",
]


def test_step_labels_and_tool_names_are_classified_as_chatter():
    assert all(talk.is_process_chatter(t) for t in CHATTY), [t for t in CHATTY if not talk.is_process_chatter(t)]
    assert not any(talk.is_process_chatter(a) for a in ANSWERS), [a for a in ANSWERS if talk.is_process_chatter(a)]


class _Queue:
    def __init__(self, out):
        self.out = out

    def put(self, item):
        self.out.append(item)


class _Worker:
    def is_alive(self):
        return True                 # say() must not start a real synthesis thread in a test


def _bare_talker():
    t = talk.Talker.__new__(talk.Talker)
    talk.Talker.__init__(t)
    t.duck = False
    t.worker = _Worker()
    spoken = []
    t.generation_texts = _Queue(spoken)
    return t, spoken


def test_the_talker_drops_chatter_and_keeps_the_answer():
    t, spoken = _bare_talker()
    for line in CHATTY:
        t.say(line)
    t.say("Tomorrow in Peshawar it is sunny and 34 degrees.")
    assert [text for _gen, text in spoken] == ["Tomorrow in Peshawar it is sunny and 34 degrees."]


def test_the_remote_talker_uses_the_same_rule():
    """Live mode speaks through RemoteTalker, which inherits Talker.say - so the filter covers it too."""
    assert talk.RemoteTalker.say is talk.Talker.say


def test_the_dispatcher_no_longer_has_a_step_narration_path():
    assert not hasattr(dispatcher, "narrate") and not hasattr(dispatcher, "NARRATE")
    source = open(dispatcher.__file__, encoding="utf-8").read()
    assert not re.search(r"^\s*narrate\(", source, re.M), "the dispatcher must never speak a step aloud"
