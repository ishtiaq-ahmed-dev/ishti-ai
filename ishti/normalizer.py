"""Typo and slang normaliser for user inputs before routing.

Normalises common typos, speech-to-text slips, and owner abbreviations
(e.g. 'wahstap' -> 'whatsapp', 'calssrom' -> 'classroom', 'assigemnt' -> 'assignment')
against known skill, application, site, and entity vocabulary while strictly
preserving quoted text, code blocks, and pasted payloads.
"""
from __future__ import annotations

import difflib
import re
from typing import Tuple

# Canonical vocabulary mapping for exact and near-exact known slang/typos
EXACT_REPLACEMENTS = {
    # WhatsApp typos
    "wahstap": "whatsapp",
    "whstap": "whatsapp",
    "watsapp": "whatsapp",
    "whatsap": "whatsapp",
    "whastap": "whatsapp",
    "watsap": "whatsapp",
    "whatssap": "whatsapp",
    "whtsapp": "whatsapp",
    "onwhatsap": "on whatsapp",
    "onw hstap": "on whatsapp",
    "onwhstap": "on whatsapp",
    # Classroom typos
    "calssrom": "classroom",
    "clasrom": "classroom",
    "classrom": "classroom",
    "calssroom": "classroom",
    "clssroom": "classroom",
    # Assignment typos
    "asssigmnet": "assignment",
    "assigemnt": "assignment",
    "asignment": "assignment",
    "assignmnt": "assignment",
    "asigments": "assignments",
    "assigments": "assignments",
    "assigemnts": "assignments",
    "ssigemnt": "assignment",
    "ssigment": "assignment",
    # Common run-ons & speech artifacts
    "thetop": "the top",
    "todo": "to-do",
    "todos": "to-dos",
    "to dos": "to-dos",
}

# Canonical site and app names for fuzzy matching
CANONICAL_TERMS = [
    "whatsapp",
    "classroom",
    "assignment",
    "assignments",
    "google classroom",
    "youtube",
    "gmail",
    "github",
    "browser",
    "calendar",
    "reminders",
    "weather",
    "calculator",
    "contacts",
    "relationships",
    "fileops",
    "files",
    "download",
    "downloads",
    "settings",
    "profile",
    "notebook",
    "ipynb",
]

# Patterns for payload separation
_CODE_BLOCK = re.compile(r"```[\s\S]*?```", re.M)
_QUOTES = re.compile(r'"[^"\n]*"|\'[^\'\n]*\'', re.M)

# Split inline command and payload: e.g. "send this data to rayyan ims on whatsapp: A1: 92 B2: 88" or with 2+ spaces
_INLINE_CMD_PAYLOAD = re.compile(
    r"^(?P<cmd>(?:please\s+)?(?:now\s+)?(?:send|message|text|forward|write)\s+.+?\bto\s+[a-z0-9_ -]+?(?:\s+(?:on|via)\s+[a-z0-9_ -]+?)?)(?:\s*[:\n]\s*|\s{2,})(?P<payload>[A-Z]\d+:[\s\S]+|[A-Za-z0-9_]+:\s+[\s\S]+|[\w\s,.-]+)$",
    re.I,
)


def split_command_and_payload(text: str) -> Tuple[str, str | None]:
    """Split an owner message into the command directive and any trailing payload block.
    
    Example:
      'send this data to rayyan ims on whatsapp\n\nA1: 92\nB2: 88' ->
      ('send this data to rayyan ims on whatsapp', 'A1: 92\nB2: 88')
    """
    raw = (text or "").strip()
    if not raw:
        return "", None

    # Check for multiline paste where first or last line is a command
    if "\n" in raw:
        lines = [line for line in raw.split("\n")]
        first_line = lines[0].strip()
        if re.search(r"\b(?:send|message|text|forward|post|write)\b", first_line, re.I):
            payload = "\n".join(lines[1:]).strip()
            if payload:
                return first_line, payload
        last_line = lines[-1].strip()
        if re.search(r"\b(?:delete|remove|send|trash|recycle|forward)\b", last_line, re.I):
            payload = "\n".join(lines[:-1]).strip()
            if payload:
                return last_line, payload

    # Check regex for inline payload separation with ':' or double space
    m = _INLINE_CMD_PAYLOAD.match(raw)
    if m:
        cmd = m.group("cmd").strip()
        payload = m.group("payload").strip()
        if payload and payload != cmd:
            return cmd, payload

    return raw, None


def normalize_token(token: str) -> str:
    """Normalize a single lowercase word token using exact mapping or difflib."""
    clean = token.strip(".,!?:;()[]{}'\"")
    if not clean:
        return token

    # Check exact replacement
    low = clean.lower()
    if low in EXACT_REPLACEMENTS:
        repl = EXACT_REPLACEMENTS[low]
        return token.replace(clean, repl)

    # If it's short or contains digits, skip fuzzy
    if len(clean) < 5 or any(ch.isdigit() for ch in clean):
        return token

    # Fuzzy match against canonical terms
    matches = difflib.get_close_matches(low, CANONICAL_TERMS, n=1, cutoff=0.82)
    if matches and matches[0] != low:
        # Don't accidentally replace common short English words
        if low in {"there", "where", "which", "these", "those", "other", "about", "their"}:
            return token
        return token.replace(clean, matches[0])

    return token


def normalize_phrase(text: str) -> str:
    """Normalize multi-word phrases and common phrase typos."""
    t = text
    # Phrase-level exact replacements
    t = re.sub(r"\bonw\s*hstap\b", "on whatsapp", t, flags=re.I)
    t = re.sub(r"\bon\s+whatsap\b", "on whatsapp", t, flags=re.I)
    t = re.sub(r"\bon\s+wahstap\b", "on whatsapp", t, flags=re.I)
    t = re.sub(r"\bon\s+watsapp\b", "on whatsapp", t, flags=re.I)
    t = re.sub(r"\bgoogle\s+calssrom\b", "google classroom", t, flags=re.I)
    t = re.sub(r"\bgoogle\s+clasrom\b", "google classroom", t, flags=re.I)
    t = re.sub(r"\bgoogle\s+classrom\b", "google classroom", t, flags=re.I)
    t = re.sub(r"\bwahstap\s+web\b", "whatsapp web", t, flags=re.I)
    t = re.sub(r"\bwatsapp\s+web\b", "whatsapp web", t, flags=re.I)
    t = re.sub(r"\bwhstap\s+web\b", "whatsapp web", t, flags=re.I)
    t = re.sub(r"\bthetop\b", "the top", t, flags=re.I)
    t = re.sub(r"\bto\s+dos\b", "to-dos", t, flags=re.I)
    return t


def normalize_message(text: str) -> str:
    """Normalise typos and slang in a user message while preserving quoted/pasted payload."""
    if not text or not text.strip():
        return text or ""

    # 1. Separate command and payload if present
    cmd, payload = split_command_and_payload(text)
    if payload:
        norm_cmd = normalize_message(cmd)
        return f"{norm_cmd}\n\n{payload}"

    # 2. Preserve code blocks and quotes by placeholder substitution
    placeholders = []

    def save_span(match):
        placeholders.append(match.group(0))
        return f"__SP_{len(placeholders) - 1}__"

    protected = _CODE_BLOCK.sub(save_span, text)
    protected = _QUOTES.sub(save_span, protected)

    # 3. Apply phrase-level normalization
    protected = normalize_phrase(protected)

    # 4. Token-by-token normalization
    words = protected.split(" ")
    norm_words = []
    for w in words:
        if w.startswith("__SP_"):
            norm_words.append(w)
        else:
            norm_words.append(normalize_token(w))
    result = " ".join(norm_words)

    # 5. Restore protected spans
    for i, span in enumerate(placeholders):
        result = result.replace(f"__SP_{i}__", span)

    return result
