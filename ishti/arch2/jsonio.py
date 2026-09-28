"""Robust JSON from the local model (Ollama structured outputs, hardened).

Every arch2 call that MUST return JSON goes through ask_json():
  1. response_format={"type": "json_object"} - Ollama constrains the decoder itself
     (logit shaping), so a well-behaved model cannot wrap the object in prose.
  2. If the backend rejects response_format, retry plain and parse defensively.
  3. Defensive parse: strip markdown fences, json.loads, then a balanced-brace scan
     for the first complete object (handles leading chatter and trailing text).
  4. One retry with an explicit "JSON only" nudge when parsing still fails.

Callers pass `schema_hint` (a compact shape description) instead of a JSON Schema -
Ollama's `format` parameter takes a full schema, but qwen3:14b follows a prompt-level
hint reliably once the decoder is constrained, and the hint keeps prompts small.
`on_json_fail` lets callers degrade explicitly (e.g. validation defaults to ok=True).
"""
import json

from ishti import llm


def parse_json(text: str):
    """Best-effort JSON object extraction from model output. None when nothing parses."""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):  # markdown fence
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
        t = t.strip()
    try:
        d = json.loads(t)
        return d if isinstance(d, dict) else None
    except ValueError:
        pass
    # balanced-brace scan: first complete top-level object anywhere in the text
    depth, start, in_str, esc = 0, None, False, False
    for i, ch in enumerate(t):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    d = json.loads(t[start:i + 1])
                    if isinstance(d, dict):
                        return d
                except ValueError:
                    start = None
    return None


def ask_json(prompt: str, role="main", schema_hint: str = "", max_tokens=600, temperature=0,
             on_json_fail=None, **kw):
    """ask() constrained to JSON. Returns the parsed dict, or on_json_fail() (default {})."""
    from ishti import llm as L
    hint = f"\nReply with ONLY a JSON object. Shape: {schema_hint}" if schema_hint else \
        "\nReply with ONLY a JSON object."
    try:
        out = L.chat([{"role": "user", "content": prompt + hint}], role=role, system=None,
                     temperature=temperature, max_tokens=max_tokens,
                     response_format={"type": "json_object"}).content or ""
    except Exception:
        out = L.ask(prompt + hint, role=role, system=None, temperature=temperature,
                    max_tokens=max_tokens)
    d = parse_json(out)
    if d is not None:
        return d
    # one retry, decoder unconstrained, prompt louder
    retry = L.ask(prompt + "\nIMPORTANT: reply with ONLY the JSON object, nothing else. " +
                  (f"Shape: {schema_hint}" if schema_hint else ""),
                  role=role, system=None, temperature=0, max_tokens=max_tokens)
    d = parse_json(retry)
    if d is not None:
        return d
    return on_json_fail() if on_json_fail else {}


if __name__ == "__main__":
    assert parse_json('{"a": 1}') == {"a": 1}
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure! Here it is: {"a": {"b": 2}} hope that helps') == {"a": {"b": 2}}
    assert parse_json('no json here at all') is None
    assert parse_json('{"a": "has } brace inside"}') == {"a": "has } brace inside"}
    assert parse_json('{"tool": "web_search", "input": "x"}')["tool"] == "web_search"
    assert parse_json("") is None
    print("[self-check] arch2.jsonio OK - fences, chatter, nested braces, escapes")
