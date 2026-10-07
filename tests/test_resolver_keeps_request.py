"""Owner bug 2026-10-07: 'now send this to rayyan ims on whatsapp' was rewritten to 'continue on: <last paste>',
dropping the verb and recipient. The resolver may add context but must never replace an action request."""
from ishti.core import state as cstate
from ishti.core.resolver import QueryResolver


def _state(topic):
    mgr = cstate.ConversationStateManager(cstate.MemoryStore())
    with mgr.turn("s") as st:
        mgr.observe_user_turn(st, topic)
    return mgr.get("s")


def test_action_request_keeps_the_owners_words():
    st = _state("A1: Number of rows, columns, data types A2: Missing values per column")
    for text in ("send this to rayyan ims on whatsapp", "delete them", "forward that to ali on whatsapp"):
        r = QueryResolver().resolve(text, st)
        assert text in r.request, (text, r.request)
        assert not r.request.startswith("continue on"), r.request


def test_bare_followup_still_uses_the_anchor():
    st = _state("search Sony WH-1000XM5")
    assert "WH-1000XM5" in QueryResolver().resolve("go deeper", st).request
