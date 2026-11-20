"""OpenAI's current models: max_completion_tokens instead of max_tokens, a reasoning effort, and sampling settings they
refuse are dropped (and remembered) instead of failing the request. No network: the API call is faked."""
import contextlib
from types import SimpleNamespace

from ishti import llm


class APIError(Exception):
    pass


class BadRequestError(APIError):
    def __init__(self, code, param):
        super().__init__(f"{code}: {param}")
        self.code, self.param = code, param


FAKE = SimpleNamespace(APIError=APIError, BadRequestError=BadRequestError)


def _setup(monkeypatch, refuses):
    sent = []

    def call(args, openai):
        sent.append(dict(args))
        for param in refuses:
            if param in args:
                raise BadRequestError("unsupported_value", param)
        return SimpleNamespace(choices=[])
    monkeypatch.setattr(llm, "api_settings", lambda: {"provider": "openai", "model": "gpt-x"})
    monkeypatch.setattr(llm, "_create_call", call)
    monkeypatch.setattr(llm.privacy if hasattr(llm, "privacy") else __import__("ishti.privacy").privacy,
                        "inference", lambda base: contextlib.nullcontext())
    monkeypatch.setattr(llm, "_UNSUPPORTED", {})
    return sent


def test_new_model_gets_max_completion_tokens_and_low_effort(monkeypatch):
    sent = _setup(monkeypatch, refuses=("temperature",))
    llm._create_api({"model": "gpt-x", "max_tokens": 50, "temperature": 0.2, "messages": []}, FAKE)
    assert "max_tokens" not in sent[0] and sent[0]["max_completion_tokens"] == 50 and sent[0]["reasoning_effort"] == "low"
    assert "temperature" not in sent[-1] and llm._UNSUPPORTED == {"gpt-x": {"temperature"}}
    llm._create_api({"model": "gpt-x", "max_tokens": 50, "temperature": 0.2, "messages": []}, FAKE)
    assert "temperature" not in sent[-1] and len(sent) == 3          # remembered: no second refusal


def test_old_model_gets_max_tokens_back(monkeypatch):
    sent = _setup(monkeypatch, refuses=("reasoning_effort", "max_completion_tokens"))
    llm._create_api({"model": "gpt-x", "max_tokens": 30, "messages": []}, FAKE)
    assert sent[-1].get("max_tokens") == 30 and "reasoning_effort" not in sent[-1]
