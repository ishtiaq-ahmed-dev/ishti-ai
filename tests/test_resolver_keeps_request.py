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


def test_delete_them_refers_to_the_files_just_listed_not_the_last_owner_message():
    from ishti.reference_resolver import resolve_reference
    table = "| Name | Size | Modified |\n|---|---|---|\n| a.mkv | 8.2 GB | 07 Oct 2026 |\n| b.exe | 212.6 MB | 06 Oct 2026 |"
    session = {"history": [{"role": "user", "content": "show my 3 newest files in downloads"},
                           {"role": "assistant", "content": table}]}
    ref = resolve_reference("delete them", session)
    assert ref and [f["name"] for f in ref["files"]] == ["a.mkv", "b.exe"], ref
