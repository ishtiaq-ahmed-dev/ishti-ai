"""Single entry point to the local LLM (Ollama's OpenAI-compatible /v1 endpoint).
Model names come from config.json, so models swap without code changes."""
from openai import OpenAI

from ishti import CONFIG

STYLE = (
    "You are ISHTI, a local personal assistant for your owner. "
    "Reply short, direct and in plain language. No filler, no restating the question, "
    "no long paragraphs. If detail is truly needed, use a few short bullet points. "
    "Facts marked KNOWN FACTS are exact and override anything else you recall."
)

_cfg = CONFIG["llm"]
_client = OpenAI(base_url=_cfg["base_url"], api_key="ollama", timeout=_cfg["timeout_s"])
_installed = None


def model_for(role: str) -> str:
    """Configured model for a role, falling back when it isn't pulled into Ollama."""
    global _installed
    name = _cfg["models"].get(role, _cfg["fallback"])
    if _installed is None:
        try:
            _installed = {m.id for m in _client.models.list().data}
        except Exception:
            _installed = set()
    if _installed and name not in _installed and f"{name}:latest" not in _installed:
        return _cfg["fallback"]
    return name


def chat(messages, role="planner", system=STYLE, tools=None, temperature=0.3, **kw):
    """Returns the OpenAI message object (has .content and .tool_calls)."""
    msgs = ([{"role": "system", "content": system}] if system else []) + list(messages)
    args = dict(model=model_for(role), messages=msgs, temperature=temperature, **kw)
    if tools:
        args["tools"] = tools
    return _client.chat.completions.create(**args).choices[0].message


def ask(prompt: str, role="planner", system=STYLE, **kw) -> str:
    return (chat([{"role": "user", "content": prompt}], role=role, system=system, **kw).content or "").strip()


if __name__ == "__main__":
    out = ask("Reply with exactly the word: pong", temperature=0)
    assert "pong" in out.lower(), out
    print(f"[self-check] llm OK via {model_for('planner')}: {out}")
