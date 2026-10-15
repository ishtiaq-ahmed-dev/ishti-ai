"""The model provider switch: local Ollama or one cloud model for every role. Offline: no network call is made."""
import pytest

from ishti import llm


@pytest.fixture
def api_mode(monkeypatch):
    monkeypatch.setenv("ISHTI_LLM_PROVIDER", "api")
    monkeypatch.setenv("ISHTI_API_KEY", "synthetic-key")
    monkeypatch.setattr(llm, "_api_client", None)
    yield
    monkeypatch.setattr(llm, "_api_client", None)


def test_api_mode_uses_one_model_for_every_role(api_mode):
    model = llm.api_settings()["model"]
    assert llm.provider() == "api"
    assert {llm.model_for(r) for r in ("planner", "main", "validation", "coder", "translator", "fast")} == {model}


def test_api_mode_drops_ollama_only_parameter_and_sends_one_call(api_mode, monkeypatch):
    seen = {}

    def fake_call(args, openai):
        seen.update(args)
        return "stub-reply"

    monkeypatch.setattr(llm, "_create_call", fake_call)
    out = llm._create(model="x", messages=[], reasoning_effort="none", _role="planner")
    assert out == "stub-reply"
    assert "reasoning_effort" not in seen


def test_api_mode_without_key_says_what_to_set(monkeypatch):
    monkeypatch.setenv("ISHTI_LLM_PROVIDER", "api")
    monkeypatch.delenv("ISHTI_API_KEY", raising=False)
    monkeypatch.setattr(llm, "_api_client", None)
    with pytest.raises(RuntimeError, match="ISHTI_API_KEY"):
        llm._get_client()


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
