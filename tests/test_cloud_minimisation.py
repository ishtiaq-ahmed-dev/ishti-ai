"""Data minimisation for cloud prompts: the chat context carries only the people a question is about (every family and
friend line used to ride along with every prompt sent to the cloud provider). Scratch stores (conftest)."""
from ishti import rgraph
from ishti.skills.chat.skill import build_context


def test_relationships_leave_the_pc_only_when_asked_about():
    rgraph.relate("me", "sister", "Sara", detail="birthday 3 March")
    rgraph.relate("me", "friend", "Bilal")
    assert "Sara" not in build_context("what's the capital of France?")
    sister = build_context("when is my sister's birthday?")
    assert "Sara" in sister and "Bilal" not in sister
    assert "Bilal" in build_context("is Bilal coming today?")
    family = build_context("tell me about my family")
    assert "Sara" in family and "Bilal" in family
