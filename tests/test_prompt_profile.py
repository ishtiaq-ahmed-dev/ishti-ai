from orchestrator import persona


def test_live_prompt_has_protocol_and_profile_layer():
    persona.CURRENT_PROFILE.set("business")
    p = persona.build_system_prompt("SIMPLE")
    assert "Operating protocol" in p and "PROFILE: business" in p and "PROFILE: personal" not in p
    persona.CURRENT_PROFILE.set("personal")
    assert "PROFILE: personal" in persona.build_system_prompt("SIMPLE")
