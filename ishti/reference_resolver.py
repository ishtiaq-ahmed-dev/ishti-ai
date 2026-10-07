"""Shared reference resolution for pronouns, pasted blocks, tables, and recent files.

Resolves references in the owner's requests:
  "this / these / them / it / above / the above provided names / that file / those files"
in strict priority order:
  1. Payload pasted in the same message after the command (inline payload).
  2. Last owner paste in the session history.
  3. Last assistant table / list / artifact in the session history (e.g. file lists with names + sizes).
  4. Last created or opened file on this PC.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from ishti.normalizer import split_command_and_payload

_PRONOUN_REF = re.compile(
    r"\b(?:this|these|them|it|above|the\s+above(?:\s+provided)?(?:\s+(?:names|files|data|items|records|text))?|"
    r"that\s+file|those\s+files|all\s+these|all\s+of\s+them)\b",
    re.I,
)

_FILE_PATH_RE = re.compile(r"""(?:[A-Za-z]:[\\/]|/)[^\n"'<>|?*`:]+?\.[A-Za-z0-9_]{1,10}\b""", re.I)


def extract_files_from_text(content: str) -> List[Dict[str, str]]:
    """Extract list of file items (name, path, size) from assistant text/tables/lists."""
    if not content:
        return []

    results = []
    seen = set()

    # 1. Parse markdown tables
    lines = content.strip().splitlines()
    for line in lines:
        line_s = line.strip()
        # Table row: | ... | ... |
        if line_s.startswith("|") and line_s.endswith("|") and "---" not in line_s:
            cols = [c.strip() for c in line_s.split("|")[1:-1]]
            # Look for path or filename in columns
            path = None
            size = ""
            name = ""
            for c in cols:
                m_path = _FILE_PATH_RE.search(c)
                if m_path:
                    path = m_path.group(0).strip()
                    name = os.path.basename(path)
                elif re.search(r"\b\d+(?:\.\d+)?\s*(?:[KMG]B|bytes)\b", c, re.I):
                    size = c
                elif not name and re.search(r"\.[a-z0-9]{2,5}$", c, re.I):
                    name = c

            if path or name:
                key = path or name
                if key not in seen:
                    seen.add(key)
                    results.append({"name": name or (os.path.basename(path) if path else ""), "path": path or "", "size": size})

        # 2. Bullet lists: - path (size) or - name (size)
        elif line_s.startswith(("-", "*", "•")) or re.match(r"^\d+\.", line_s):
            m_path = _FILE_PATH_RE.search(line_s)
            size_m = re.search(r"\b\d+(?:\.\d+)?\s*(?:[KMG]B|bytes)\b", line_s, re.I)
            size = size_m.group(0) if size_m else ""
            if m_path:
                path = m_path.group(0).strip()
                name = os.path.basename(path)
                if path not in seen:
                    seen.add(path)
                    results.append({"name": name, "path": path, "size": size})
            else:
                fn_m = re.search(r"\b([A-Za-z0-9_.\-]+\.[a-z0-9]{2,5})\b", line_s, re.I)
                if fn_m:
                    name = fn_m.group(1).strip()
                    if name not in seen:
                        seen.add(name)
                        results.append({"name": name, "path": "", "size": size})

    # If no structured table/list matched, check for bare file paths
    if not results:
        for p in _FILE_PATH_RE.findall(content):
            p = p.strip()
            if p not in seen:
                seen.add(p)
                results.append({"name": os.path.basename(p), "path": p, "size": ""})

    return results


def resolve_reference(text: str, session: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Resolve pronouns and relative references in order:
    1. Payload pasted in same message after command.
    2. Last owner paste in session history.
    3. Last assistant table/list/artifact in session history.
    4. Last created or opened file.
    
    Returns:
      {
         "type": "inline_payload" | "last_owner_paste" | "last_assistant_table" | "last_file",
         "payload": str (for text/message payloads),
         "files": list[dict] (for file operations),
         "description": str,
      }
      or None if no reference could be resolved.
    """
    text = (text or "").strip()
    session = session or {}
    history = session.get("history", [])

    # Order 1: Payload pasted in the same message after the command
    cmd, inline_payload = split_command_and_payload(text)
    if inline_payload and inline_payload.strip():
        # Remember this as last_paste in session for subsequent turns
        session["last_paste"] = inline_payload.strip()
        return {
            "type": "inline_payload",
            "payload": inline_payload.strip(),
            "files": extract_files_from_text(inline_payload),
            "description": "payload pasted in current message",
        }

    # If the text has no pronoun or relative reference and no ambiguous action, return None
    has_ref = bool(_PRONOUN_REF.search(text))
    if not has_ref:
        return None

    # Order 2: Last owner paste in session
    if session.get("last_paste"):
        return {
            "type": "last_owner_paste",
            "payload": session["last_paste"],
            "files": extract_files_from_text(session["last_paste"]),
            "description": "last pasted payload from user",
        }

    for turn in reversed(history):
        if turn.get("role") == "user":
            content = str(turn.get("content") or "").strip()
            if not content:
                continue
            _, past_payload = split_command_and_payload(content)
            payload_to_use = past_payload.strip() if (past_payload and past_payload.strip()) else content
            session["last_paste"] = payload_to_use
            return {
                "type": "last_owner_paste",
                "payload": payload_to_use,
                "files": extract_files_from_text(payload_to_use),
                "description": "last pasted payload from user history",
            }

    # Order 3: Last assistant table / list / artifact
    for turn in reversed(history):
        if turn.get("role") == "assistant":
            content = str(turn.get("content") or "").strip()
            files = extract_files_from_text(content)
            if files:
                return {
                    "type": "last_assistant_table",
                    "payload": content,
                    "files": files,
                    "description": f"last assistant file list ({len(files)} files)",
                }
            # Markdown table of general rows
            if "|" in content and "---" in content:
                return {
                    "type": "last_assistant_table",
                    "payload": content,
                    "files": [],
                    "description": "last assistant table",
                }

    # Order 4: Last created / opened file
    last_file = session.get("last_file") or session.get("created_file")
    if last_file and (isinstance(last_file, str) or isinstance(last_file, dict)):
        path = last_file if isinstance(last_file, str) else last_file.get("path", "")
        if path:
            return {
                "type": "last_file",
                "payload": path,
                "files": [{"name": os.path.basename(path), "path": path, "size": ""}],
                "description": f"last accessed file: {path}",
            }

    # Check history for any assistant mentioned file
    for turn in reversed(history):
        content = str(turn.get("content") or "")
        paths = _FILE_PATH_RE.findall(content)
        if paths:
            path = paths[-1].strip()
            return {
                "type": "last_file",
                "payload": path,
                "files": [{"name": os.path.basename(path), "path": path, "size": ""}],
                "description": f"last mentioned file: {path}",
            }

    return None
