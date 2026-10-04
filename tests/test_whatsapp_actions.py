"""Offline edit preview/ownership/outcome checks using sanitized live UI structure."""
import pytest
from ishti.skills.whatsapp import actions, skill as wa
from ishti.skills.browser import skill as browser


ORIGINAL = "ISHTI SELF TEST fixture: original"
REPLACEMENT = "ISHTI SELF TEST fixture: edited"


def page(text=ORIGINAL, edited=False, own=True):
    marker = '      - generic [ref=edited]: Edited\n' if edited else ''
    sender = "You" if own else "Other"
    return ('- banner [ref=h]:\n  - button "Test Self (You) Message yourself" [ref=header]\n'
            '- generic [ref=messages]:\n'
            f'  - row "{sender}: {text} 4:13 PM Read" [ref=row]:\n'
            f'    - generic "{sender}:"\n    - generic [ref=body]: "{text}"\n' + marker +
            '- contentinfo [ref=f]:\n  - textbox "Type a message to Test Self" [ref=composer]')


def editor(text):
    return ('- dialog [ref=d]:\n  - heading "Edit message" [ref=heading]\n'
            f'  - generic: {ORIGINAL}\n  - textbox "Type a message" [ref=input]:\n'
            f'    - paragraph [ref=p]: "{text}"\n  - button "Send edited message" [ref=save]')


@pytest.mark.parametrize("prompt, contact, replacement", [
    ("edit the last message in Ahmed chat saying hello", "Ahmed", "hello"),
    ("edit my message to hello", "", "hello"),
    ("change the last message with احمد to سلام", "احمد", "سلام"),
])
def test_edit_request_preserves_exact_contact_and_replacement(prompt, contact, replacement):
    parsed = actions.parse_operation(prompt)
    assert parsed == {"kind": "edit", "contact": contact, "replacement": replacement}


def test_nodes_exclude_sidebar_and_preserve_ownership():
    snap = '- row "You: sidebar secret 4:12 PM Read" [ref=sidebar]\n' + page()
    nodes = actions.message_nodes(snap)
    assert len(nodes) == 1 and nodes[0]["body"] == ORIGINAL and nodes[0]["from_me"]
    assert nodes[0]["body_ref"] == "body"
    assert actions.message_nodes(page(own=False))[0]["from_me"] is False
    assert actions.message_nodes(page(REPLACEMENT, edited=True))[0]["edited"]


def test_enabled_control_must_be_unique_and_enabled():
    assert actions._enabled_ref('- menuitem "Edit" [ref=edit]', "menuitem", "Edit") == "edit"
    assert not actions._enabled_ref('- menuitem "Edit" [disabled] [ref=edit]', "menuitem", "Edit")
    assert not actions._enabled_ref('- menuitem "Edit" [ref=a]\n- menuitem "Edit" [ref=b]', "menuitem", "Edit")


@pytest.mark.parametrize("wrong_draft, final_marker", [(False, True), (True, True), (False, False)])
def test_edit_waits_for_confirmation_and_verifies_draft_and_result(monkeypatch, wrong_draft, final_marker):
    monkeypatch.delenv("ISHTI_TEST_MODE", raising=False)
    monkeypatch.setattr(browser, "use_server", lambda *a: None)
    monkeypatch.setattr(actions.time, "sleep", lambda *a: None)
    calls = []
    state = {"menu": False, "editor": False, "typed": False, "saved": False}
    def call(name, args):
        calls.append((name, args))
        if name == "browser_evaluate":
            return '### Result\n{"messageId":"SYNTHETIC001","fromMe":true}'
        if name == "browser_click":
            if args.get("button") == "right":
                state["menu"] = True
            elif args["target"] == "edit":
                state["menu"], state["editor"] = False, True
            elif args["target"] == "save":
                state["editor"], state["saved"] = False, True
        elif name == "browser_press_key":
            state["menu"] = False
        elif name == "browser_type":
            state["typed"] = True
        if state["editor"]:
            return editor("wrong" if wrong_draft else REPLACEMENT) if state["typed"] else editor(ORIGINAL)
        if state["menu"]:
            return page() + '\n- menuitem "Edit" [ref=edit]'
        if state["saved"]:
            return page(REPLACEMENT, edited=final_marker)
        return page()
    monkeypatch.setattr(browser, "_call", call)
    session = {"whatsapp_last": "Test Self", "live_self_test": True}
    preview = actions.prepare({"kind": "edit", "replacement": REPLACEMENT}, session)
    assert ORIGINAL in preview["reply"] and REPLACEMENT in preview["reply"]
    assert not any(name == "browser_type" for name, _ in calls)
    result = session["pending"]["action"]()
    assert bool(result["verified"]) == (not wrong_draft and final_marker)
    assert any(args.get("target") == "save" for _, args in calls) == (not wrong_draft)


def test_duplicate_original_is_not_selected_arbitrarily():
    with pytest.raises(ValueError, match="unique"):
        actions._exact_node(page() + page(), ORIGINAL)


def test_live_test_cannot_edit_existing_owner_data(monkeypatch):
    monkeypatch.delenv("ISHTI_TEST_MODE", raising=False)
    monkeypatch.setattr(actions, "_pane", lambda *a: page("existing personal message"))
    monkeypatch.setattr(actions, "_open_options", lambda *a: pytest.fail("selected real owner data"))
    result = actions.prepare({"kind": "edit", "replacement": REPLACEMENT}, {"whatsapp_last": "Test Self", "live_self_test": True})
    assert not result["verified"] and "only change the synthetic" in result["reply"]


@pytest.mark.parametrize("raw", ['null', '{}', '{"messageId":"SYNTHETIC001","fromMe":"true"}',
                                    '{"messageId":"","fromMe":true}', 'not-json'])
def test_identity_refuses_missing_or_fabricated_metadata(monkeypatch, raw):
    monkeypatch.setattr(browser, "_call", lambda *a: '### Result\n' + raw)
    with pytest.raises(ValueError, match="ownership"):
        actions.message_identity({"body_ref": "body"})


def test_consecutive_bubble_does_not_need_accessibility_sender(monkeypatch):
    monkeypatch.setattr(browser, "_call", lambda *a: '### Result\n{"messageId":"SYNTHETIC002","fromMe":true}')
    snap = page().replace('You: ' + ORIGINAL, ORIGINAL).replace('    - generic "You:"\n', '')
    node = actions.message_nodes(snap)[0]
    assert node["from_me"] is False
    assert actions.message_identity(node)["fromMe"] is True


@pytest.mark.parametrize("prompt", ["edit the last message saying hello", "delete the last message"])
def test_public_message_changes_are_deferred_without_browser_calls(monkeypatch, prompt):
    monkeypatch.setattr(browser, "_call", lambda *a: pytest.fail("deferred mutation opened browser"))
    session = {"pending": {"skill": "whatsapp", "action": lambda: pytest.fail("old approval reused")}}
    assert "deferred in NEEDS" in wa.op_message_edit(prompt, session)["reply"]
    assert session["pending"] is None
