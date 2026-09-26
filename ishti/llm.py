"""Single entry point to the local LLM (Ollama's OpenAI-compatible /v1 endpoint).
Model names come from config.json, so models swap without code changes."""
import re
from types import SimpleNamespace

from openai import OpenAI

from ishti import CONFIG

STYLE = (
    "You are ISHTI, a local personal assistant for your owner. "
    "Reply short, direct and in plain language. No filler, no restating the question, "
    "no long paragraphs. If detail is truly needed, use a few short bullet points."
)

_cfg = CONFIG["llm"]
_client = OpenAI(base_url=_cfg["base_url"], api_key="ollama", timeout=_cfg["timeout_s"])
_installed = None
# qwen2.5:14b (Q4_K_M) sometimes opens a tool-calling reply with a Thai/Chinese junk line
# ("คณะกรรม... (委员会)"); the tool calls themselves still parse. Drop those lines unless the
# conversation itself uses that script.
_JUNK = {"thai": re.compile(r"[฀-๿]"), "cjk": re.compile(r"[一-鿿]")}


def clean(text: str, context: str = "") -> str:
    for rx in _JUNK.values():
        if rx.search(text or "") and not rx.search(context):
            text = "\n".join(line for line in text.splitlines() if not rx.search(line))
    return (text or "").strip()


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


def chat(messages, role="planner", system=STYLE, tools=None, temperature=0.3, max_tokens=1024, on_token=None, **kw):
    """Returns the OpenAI message object (has .content and .tool_calls).
    max_tokens is always set: with tools the model can keep generating after its tool call.
    on_token(str) streams the reply as it is generated (plain replies only, not tool calls)."""
    msgs = ([{"role": "system", "content": system}] if system else []) + list(messages)
    args = dict(model=model_for(role), messages=msgs, temperature=temperature, max_tokens=max_tokens, **kw)
    context = " ".join(str(m.get("content", "")) for m in messages if m.get("role") == "user")
    if tools:
        args["tools"] = tools
    elif on_token:
        text = ""
        for chunk in _client.chat.completions.create(**args, stream=True):
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                text += delta
                on_token(delta)
        return SimpleNamespace(content=clean(text, context), tool_calls=None)
    msg = _client.chat.completions.create(**args).choices[0].message
    msg.content = clean(msg.content, context)
    return msg


def ask(prompt: str, role="planner", system=STYLE, **kw) -> str:
    return (chat([{"role": "user", "content": prompt}], role=role, system=system, **kw).content or "").strip()


if __name__ == "__main__":
    assert clean("คณะ (委员会)\nThe heading is Example Domain.") == "The heading is Example Domain."
    assert clean("你好", context="说中文") == "你好"  # kept when the owner writes that script
    out = ask("Reply with exactly the word: pong", temperature=0)
    assert "pong" in out.lower(), out
    print(f"[self-check] llm OK via {model_for('planner')}: {out}")
