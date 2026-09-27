"""Single entry point to the local LLM (Ollama's OpenAI-compatible /v1 endpoint).
Model names come from config.json, so models swap without code changes."""
import re
from types import SimpleNamespace

from openai import OpenAI

from ishti import killswitch
from ishti import CONFIG

STYLE = (
    "You are ISHTI, a local personal assistant for your owner. "
    "Follow these strict formatting and style rules for every response:\n"
    "1. Proper formatting & structure: Write clean, readable answers with proper line breaks between paragraphs. "
    "Never produce messy, unformatted, or random text.\n"
    "2. Use bullet points: When listing features, steps, details, reasons, or items, always format them as clean, "
    "well-spaced bullet points (- item).\n"
    "3. Use Markdown tables: When presenting comparisons, data, specifications, numbers, prices, or structured info, "
    "always organize it into a proper Markdown table with headers (| Header 1 | Header 2 |) and alignment dividers (|---|---|).\n"
    "4. NO UNREQUESTED CODE: Never answer in code, code blocks (```python, ```json, etc.), or scripts unless the owner "
    "explicitly asks you to write code or a program. Answer normal questions, math, explanations, and advice in natural language.\n"
    "5. Direct & concise: Answer directly in plain language. No conversational filler, no repeating the question back.\n"
    "6. Language: Reply in English, Urdu or Pashto - whichever the owner used (Urdu script in Urdu script, Roman Urdu in Roman Urdu)."
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


def _create(**args):
    """Degraded mode: if Ollama isn't running (after a reboot, or stopped), start it and retry once; if it still
    can't be reached, say so plainly instead of a raw connection error."""
    import openai
    from ishti import models
    models.used(args["model"])  # D3: usage decides how long the model stays in VRAM
    try:
        return _client.chat.completions.create(**args)
    except openai.APIConnectionError:
        import subprocess
        import time
        try:
            subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            pass
        for _ in range(15):
            time.sleep(1)
            try:
                return _client.chat.completions.create(**args)
            except openai.APIConnectionError:
                continue
        raise RuntimeError("the local AI model (Ollama) isn't running and I couldn't start it - start Ollama "
                           "and ask again") from None


class Cancelled(Exception):
    """Raised by an on_token callback to abort a streaming generation (voice barge-in)."""


def style() -> str:
    """What goes with every styled answer: the base style, the owner's own custom instructions (edited in
    About you) and the standing preferences learned from their chats. Shown verbatim in About you."""
    from ishti.facts import get_facts
    owner = get_facts().get("owner")
    prefs = [f"- {a.removeprefix('prefers_').replace('_', ' ')}: {v}" for a, v in owner.items() if a.startswith("prefers_")]
    out = STYLE
    if owner.get("instructions"):
        out += "\n\nThe owner's instructions (always follow them):\n" + owner["instructions"]
    if prefs:
        out += "\n\nThe owner's standing preferences:\n" + "\n".join(prefs)
    return out


def chat(messages, role="planner", system=STYLE, tools=None, temperature=0.3, max_tokens=1024, on_token=None, **kw):
    """Returns the OpenAI message object (has .content and .tool_calls).
    max_tokens is always set: with tools the model can keep generating after its tool call.
    on_token(str) streams the reply as it is generated (plain replies only, not tool calls)."""
    if system is STYLE:
        system = style()
    if any("<<EXTERNAL" in str(m.get("content", "")) for m in messages):  # outside text is data, never instructions
        from ishti.untrusted import RULE
        system = f"{system}\n\n{RULE}" if system else RULE
    msgs = ([{"role": "system", "content": system}] if system else []) + list(messages)
    args = dict(model=model_for(role), messages=msgs, temperature=temperature, max_tokens=max_tokens, **kw)
    context = " ".join(str(m.get("content", "")) for m in messages if m.get("role") == "user")
    if tools:
        args["tools"] = tools
    elif on_token:
        text = ""
        killswitch.check()
        for chunk in _create(**args, stream=True):
            killswitch.check()  # the kill switch stops generation mid-sentence
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                text += delta
                on_token(delta)
        return SimpleNamespace(content=clean(text, context), tool_calls=None)
    killswitch.check()
    if not tools:  # stream even when nobody listens: the kill switch can stop it at the next token
        text = ""
        for chunk in _create(**args, stream=True):
            killswitch.check()
            if chunk.choices and chunk.choices[0].delta.content:
                text += chunk.choices[0].delta.content
        return SimpleNamespace(content=clean(text, context), tool_calls=None)
    msg = _create(**args).choices[0].message
    killswitch.check()
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
