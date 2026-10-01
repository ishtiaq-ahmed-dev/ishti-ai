"""Single entry point to the local LLM (Ollama's OpenAI-compatible /v1 endpoint).
Model names come from config.json, so models swap without code changes."""
import os
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
    "6. Language: Reply only in English, Urdu or Pashto - whichever the owner used (Urdu script in Urdu script, Roman "
    "Urdu in Roman Urdu). Never answer in any other language."
)

_cfg = CONFIG["llm"]
_client = OpenAI(base_url=(os.environ["ISHTI_OLLAMA_URL"].rstrip("/") + "/v1") if os.environ.get("ISHTI_OLLAMA_URL") else _cfg["base_url"],
                 api_key="ollama", timeout=_cfg["timeout_s"])  # ISHTI_OLLAMA_URL: a second Ollama (tests, another port)
_installed = None

FALLBACK_MAP = {
    "qwen3:30b-a3b": "ishti-qwen2.5:14b",
    "qwen3-coder:30b-a3b": "ishti-qwen2.5-coder:14b",
    "llama3.2:3b": "laya",
}
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
    """The model to use for a role right now: the configured one (with fallbacks below), unless the VRAM governor has
    moved the role to a lighter rung (planner downshifted while a heavy job runs, vision tier 2/3)."""
    from ishti import vram
    if role in ("vision2", "vision3"):  # no config entry: the ladder's best installed rung
        rs = vram.rungs(role)
        return vram.effective(role, rs[0]["name"] if rs else _cfg["fallback"])
    name = _configured_for(role)
    return name if name == "laya" else vram.effective("planner" if role in ("main", "validation") else role, name)


def _configured_for(role: str) -> str:
    """Configured model for a role, falling back when it isn't pulled into Ollama.
    arch2 (ishti/arch2/*) asks for its own roles ("main", "validation") which live in
    CONFIG["arch"]["arch2"]["models"], falling back to the planner model."""
    forced = os.environ.get(f"ISHTI_MODEL_{role.upper()}")  # e.g. ISHTI_MODEL_PLANNER=qwen2.5:3b: pins a role to one model (tests, low-VRAM days)
    if forced:
        return forced
    global _installed
    if role in ("main", "validation"):
        a2 = (CONFIG.get("arch") or {}).get("arch2", {}).get("models", {})
        name = a2.get(role) or _cfg["models"].get("planner", _cfg["fallback"])
        if _installed is None:
            try:
                _installed = {m.id for m in _client.models.list().data}
            except Exception:
                _installed = set()
        if _installed and name not in _installed and f"{name}:latest" not in _installed:
            return a2.get("fallback") or _cfg["fallback"]
        return name
    name = _cfg["models"].get(role, _cfg["fallback"])
    if role == "fast" or name.lower() in ("laya", "typed-decisions"):
        return "laya"
    if _installed is None:
        try:
            _installed = {m.id for m in _client.models.list().data}
        except Exception:
            _installed = set()
    if _installed and name not in _installed and f"{name}:latest" not in _installed:
        if role == "coder":
            for cand in ("qwen3-coder:30b-a3b", "ishti-qwen2.5-coder:14b", "qwen2.5-coder:14b"):
                if cand in _installed or f"{cand}:latest" in _installed:
                    return cand
        return _cfg.get("fallback", "ishti-qwen2.5:14b")
    return name


def _create(**args):
    """Degraded mode: if Ollama isn't running (after a reboot, or stopped), start it and retry once; if it still
    can't be reached, say so plainly instead of a raw connection error."""
    import openai
    from ishti import models, vram
    import requests
    role = args.pop("_role", None)
    if role in ("main", "validation"):  # arch2's roles are the planner: same rung, same pinning
        role = "planner"
    lease = None
    guard = vram.busy(args["model"])  # marked BEFORE the governor looks: a just-admitted model must never be picked as idle
    guard.__enter__()
    try:  # the governor makes room BEFORE the call, so Ollama never has to split a model between GPU and CPU
        res = vram.admit(args["model"], role=role, job=f"answer with {args['model']}")
        if not res["ok"]:
            raise vram.GpuBusy("The graphics card is busy: " + res["queued"] + ". Try again in a moment.")
        if res["model"] != args["model"]:  # the governor chose another rung: that is the model that is busy now
            guard.__exit__(None, None, None)
            args["model"] = res["model"]
            guard = vram.busy(args["model"])
            guard.__enter__()
        lease = res.get("lease")
    except vram.GpuBusy:
        guard.__exit__(None, None, None)
        raise
    except requests.HTTPError as exc:  # Ollama refused the load (out of memory...): sending the chat would just retry it ungoverned
        guard.__exit__(None, None, None)
        raise vram.GpuBusy(f"The model could not be loaded onto the graphics card ({exc.response.status_code if exc.response is not None else 'error'}).") from exc
    except Exception as exc:  # Ollama unreachable / governor bug: never block a chat on it - log, and the degraded path below handles Ollama being down
        print(f"[llm] VRAM governor skipped ({type(exc).__name__}: {exc})", flush=True)
    models.used(args["model"])  # D3: usage decides how long the model stays in VRAM

    def done():
        guard.__exit__(None, None, None)
        if lease:
            vram.release(lease)
    try:
        out = _create_call(args, openai)
    except BaseException:
        done()
        raise
    if not args.get("stream"):
        done()
        return out

    def stream():  # the busy mark and the job lease last until the last token has been read
        try:
            yield from out
        finally:
            done()
    return stream()


def _create_call(args, openai):
    try:
        return _client.chat.completions.create(**args)
    except openai.APIConnectionError:
        import time
        from ishti import lifecycle
        try:  # the one place that starts Ollama (hidden, in our Job Object, remembered as ours)
            lifecycle.start_ollama()
        except Exception as exc:
            print(f"[llm] could not start Ollama: {type(exc).__name__}: {exc}", flush=True)
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
    # The self-model (ishti/selfmodel.py): measured facts about THIS machine, skills and models, so the
    # model never has to recall them. Additive and wrapped: if any probe fails the block is simply absent
    # and a reply still goes out - a self-model must never be able to break answering. Measured cost:
    # ~91 ms cold, ~1 ms from cache (2026-09-30).
    try:
        from ishti.selfmodel import render as _self_model
        out += ("\n\n" + _self_model() +
                "\n\nHow to use the block above: it is MEASURED, not remembered. For any question about "
                "my hardware, models, skills, limits or abilities, answer only from it. If the fact is not "
                "in there, reply exactly \"I don't know - shall I check?\" and check it - never estimate, "
                "never recall, never invent a spec. Never say which model you are: the router picks one per "
                "request, and the block lists what is installed, not what is speaking.")
    except Exception as exc:
        print(f"[llm] self-model unavailable ({type(exc).__name__}: {exc}) - answering without it")
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
    args = dict(model=model_for(role), messages=msgs, temperature=temperature, max_tokens=max_tokens, _role=role, **kw)
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
