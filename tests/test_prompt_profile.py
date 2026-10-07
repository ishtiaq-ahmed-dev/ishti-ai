from orchestrator import persona


def test_live_prompt_follows_chat_profile():
    persona.CURRENT_PROFILE.set("business")
    p = persona.build_system_prompt("SIMPLE")
    assert "ACTIVE MODE: BUSINESS" in p and "ACTIVE MODE: PERSONAL" not in p
    persona.CURRENT_PROFILE.set("personal")
    assert "ACTIVE MODE: PERSONAL" in persona.build_system_prompt("SIMPLE")
