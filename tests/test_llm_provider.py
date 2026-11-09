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


def _status_error(cls, code, body):
    import httpx
    response = httpx.Response(code, request=httpx.Request("POST", "https://provider.invalid/v1/chat/completions"))
    return cls("refused", response=response, body=body)


@pytest.mark.parametrize("cls_name,code,body,expect", [
    ("AuthenticationError", 401, {"error": {"message": "unauthorized client detected"}}, "allow-listed"),
    ("AuthenticationError", 401, {"error": {"message": "bad key"}}, "rejected the API key"),
    ("RateLimitError", 429, {}, "rate-limiting"),
    ("NotFoundError", 404, {}, "does not offer"),
])
def test_api_errors_become_one_plain_sentence(api_mode, monkeypatch, cls_name, code, body, expect):
    import openai

    def refuse(args, _openai):
        raise _status_error(getattr(openai, cls_name), code, body)

    monkeypatch.setattr(llm, "_create_call", refuse)
    with pytest.raises(llm.ModelUnavailable, match=expect) as info:
        llm._create(model="x", messages=[], _role="planner")
    assert "synthetic-key" not in str(info.value)


def test_refused_cloud_fails_over_to_local_model_and_breaker_skips_cloud(api_mode, monkeypatch):
    import openai
    calls = []

    def fake_call(args, _openai, local=False):
        calls.append(("local" if local else "cloud", args["model"]))
        if not local:
            raise _status_error(openai.AuthenticationError, 401, {"error": {"message": "unauthorized client detected"}})
        return "local-reply"

    monkeypatch.setattr(llm, "_create_call", fake_call)
    monkeypatch.setattr(llm, "failover_model", lambda: "light:4b")
    monkeypatch.setitem(llm._cloud_state, "until", 0.0)

    class Guard:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    from ishti import vram, models
    monkeypatch.setattr(vram, "busy", lambda m: Guard())
    monkeypatch.setattr(vram, "admit", lambda m, **k: {"ok": True, "model": m, "lease": None})
    monkeypatch.setattr(models, "used", lambda m: None)
    assert llm._create(model="gpt-x", messages=[], _role="planner") == "local-reply"
    assert llm._create(model="gpt-x", messages=[], _role="planner") == "local-reply"
    assert calls == [("cloud", "gpt-x"), ("local", "light:4b"), ("local", "light:4b")]  # breaker: cloud tried once
    assert llm.model_for("planner") == "light:4b"  # the trace names the model that really answers
    monkeypatch.setitem(llm._cloud_state, "until", 0.0)


def test_no_failover_model_means_the_plain_error_reaches_the_owner(api_mode, monkeypatch):
    import openai
    monkeypatch.setattr(llm, "failover_model", lambda: "")
    monkeypatch.setattr(llm, "_create_call", lambda a, o, local=False: (_ for _ in ()).throw(
        _status_error(openai.RateLimitError, 429, {})))
    with pytest.raises(llm.ModelUnavailable, match="rate-limiting"):
        llm._create(model="x", messages=[], _role="planner")


def test_background_roles_use_the_local_model_only_when_enabled_and_available(api_mode, monkeypatch):
    cloud = llm.api_settings()["model"]
    monkeypatch.setattr(llm, "failover_model", lambda: "light:4b")
    monkeypatch.setattr(llm, "api_settings", lambda: {"model": cloud, "background_local": True, "models": {}})
    assert llm.model_for("main") == "light:4b" and llm.model_for("planner") == cloud
    monkeypatch.setattr(llm, "api_settings", lambda: {"model": cloud, "background_local": False,
                                                     "models": {"translator": "mini-x"}})
    assert llm.model_for("main") == cloud and llm.model_for("translator") == "mini-x"
    monkeypatch.setattr(llm, "failover_model", lambda: "")  # no local model running: background stays on the cloud
    monkeypatch.setattr(llm, "api_settings", lambda: {"model": cloud, "background_local": True, "models": {}})
    assert llm.model_for("main") == cloud


def test_agentrouter_is_a_known_provider():
    from ishti import setup_choice
    assert llm.PROVIDERS["agentrouter"] == "https://agentrouter.org/v1"
    assert setup_choice.DEFAULT_MODELS["agentrouter"] == "gpt-6.1-sol"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
