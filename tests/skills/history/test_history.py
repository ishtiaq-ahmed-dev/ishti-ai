"""Search past conversations (Hermes-style session search) over the scratch chat store."""
import pytest

from ishti import runs
from ishti.skills.history import skill as history


@pytest.fixture
def chats():
    visa = runs.new_chat(title="Visa plans")["chat_id"]
    runs.add_user_message(visa, "my visa appointment at the embassy is on the 14th")
    runs.add_assistant_message(visa, "Noted - the embassy appointment is on the 14th.")
    other = runs.new_chat(title="Dinner")["chat_id"]
    runs.add_user_message(other, "what should I cook for dinner tonight")
    yield visa, other
    for cid in (visa, other):
        try:
            runs.trash_chat(cid)
        except Exception:
            pass


def test_finds_the_chat_by_its_words(chats):
    out = history.run("what did we talk about the embassy visa", {})["reply"]
    assert "Visa plans" in out and "Dinner" not in out and "14th" in out


def test_says_so_when_nothing_matches(chats):
    assert "couldn't find" in history.run("search my chats for submarine blueprints", {})["reply"]


def test_trashed_chats_are_not_searched(chats):
    runs.trash_chat(chats[0])
    assert "couldn't find" in history.run("have we discussed the embassy before?", {})["reply"]


def test_the_question_itself_is_not_a_result(chats):
    q = "search my chats for cook dinner"
    cid = runs.new_chat(title="q")["chat_id"]
    runs.add_user_message(cid, q)
    out = history.run(q, {})["reply"]
    assert "Dinner" in out and '"q"' not in out
    runs.trash_chat(cid)


@pytest.mark.parametrize("text", ["what did we talk about the weather last week", "when did I ask you about Karachi flights",
                                  "search my chats for the downloads folder cleanup"])
def test_history_wins_over_noun_cues(text):
    from ishti import dispatcher, tool_routing
    assert tool_routing.route(text, dispatcher.SKILLS).get("skill") == "history"
