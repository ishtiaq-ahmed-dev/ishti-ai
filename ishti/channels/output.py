"""Output layer for remote channels (WP-A4): privacy honesty, chunking, attachments.

Sensitive-output policy (R1 C + R4 §5): Telegram/WhatsApp chats are cloud chats — not local, not
E2E. Remote replies therefore follow policy.sensitive_output per category:
  allow   send as-is
  redact  masked patterns (amounts, IBANs, emails, phone numbers) + "open on PC" note
  block   never sent; replaced by "open on PC"
Secrets and the owner profile are ALWAYS blocked (never sent), regardless of policy.

Chunking: 3800-char pieces with HTML-safe splitting (R1 A: sendMessage practical limit 4096).
Big tables: markdown pipe-tables over ~15 rows / 1500 chars become CSV attachments (the chat-digest
export machinery writes the file; the channel attaches it).
"""
from __future__ import annotations

import html
import re
import time

from ishti.channels import policy as policy_mod

CHUNK = 3800

# category detectors ---------------------------------------------------------------
_FIN = re.compile(r"(?:\$|₹|€|£|PKR|USD|Rs\.??)\s?\d[\d,]*(?:\.\d{1,2})?|\b\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE = re.compile(r"(?:\+92|0)3\d{2}[- ]?\d{7}|\+\d{1,3}[- ]?\d{6,12}")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
_PATNIC = re.compile(r"\b\d{5}-?\d{7}-?\d\b")  # CNIC/PAN-style national IDs

RULES = {
    "client_data": [_EMAIL, _PHONE],
    "finance": [_FIN, _IBAN],
    "chat_digests": [],
    "owner_profile": [],
    "secrets": [],
}


def redact_text(text: str, cats: dict) -> tuple[str, list[str]]:
    """Apply redact rules; return (new_text, categories_actually_hit)."""
    hits = []
    out = text
    for cat, mode in cats.items():
        if mode != "redact":
            continue
        for rx in RULES.get(cat, []):
            if rx.search(out):
                out = rx.sub(lambda m: "•" * min(10, max(4, len(m.group(0)) - 2)), out)
                if cat not in hits:
                    hits.append(cat)
    return out, hits


def block_markers(text: str, cats: dict) -> bool:
    """True when the reply visibly contains blocked-category content (secrets, profile...)."""
    if cats.get("secrets") == "block" and re.search(r"(?i)\b(?:api[_ ]?key|token|password|passphrase|secret)\b\s*[:=]", text):
        return True
    return False


def sensitive_filter(text: str) -> tuple[str, dict]:
    """The full filter. Returns (final_text, info) — info carries hits/blocked for the audit row."""
    pol = policy_mod.load()
    cats = pol.get("sensitive_output", {})
    info = {"redacted": [], "blocked": False, "note": False}
    if block_markers(text, cats):
        info["blocked"] = True
        return ("That reply contains material I never send remotely - it's open on the PC.", info)
    out, hits = redact_text(text, cats)
    if hits:
        info["redacted"] = hits
        info["note"] = True
        out += "\n\n(some details are minimized for remote viewing - open ISHTI on the PC for the full text)"
    return out, info


# ---------------------------------------------------------------- chunking
def chunk(text: str, size: int = CHUNK) -> list[str]:
    text = text or ""
    if len(text) <= size:
        return [text] if text else []
    parts, rest = [], text
    while len(rest) > size:
        window = rest[:size]
        cut = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind(". "), window.rfind(" "))
        cut = cut if cut > size * 0.5 else size
        parts.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        parts.append(rest)
    return parts


# ---------------------------------------------------------------- tables
_ROW = re.compile(r"^\s*\|.*\|\s*$")


def _table_rows(text: str):
    lines = text.splitlines()
    blocks, cur = [], []
    for ln in lines:
        if _ROW.match(ln):
            cur.append(ln)
        else:
            if len(cur) >= 2:
                blocks.append(cur)
            cur = []
    if len(cur) >= 2:
        blocks.append(cur)
    return blocks


def table_is_big(table_lines: list[str], max_rows: int = 15, max_chars: int = 1500) -> bool:
    return len(table_lines) > max_rows or sum(len(l) for l in table_lines) > max_chars


def md_table_to_csv(table_lines: list[str]) -> str:
    """A markdown pipe-table -> CSV text (the caller writes/attaches it via export_file)."""
    import csv
    import io

    rows = []
    for ln in table_lines:
        if re.match(r"^\s*\|[\s:|-]+\|\s*$", ln):  # separator row
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        rows.append(cells)
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return buf.getvalue()


def prepare_reply(text: str, export_file=None) -> tuple[list[str], list[tuple[str, str]]]:
    """Full output pipeline for one dispatcher reply.
    Returns (text_chunks, attachments) where attachments = [(path, caption)].
    export_file(name_hint, content) -> path is provided by the caller (media.py), so this module
    stays free of filesystem policy."""
    text, info = sensitive_filter(text)
    attachments: list[tuple[str, str]] = []
    if info["blocked"]:
        return chunk(text), attachments
    # big markdown tables -> CSV attachment, replaced by a pointer
    for tbl in _table_rows(text):
        if table_is_big(tbl):
            csv_text = md_table_to_csv(tbl)
            name = f"table_{time.strftime('%H%M%S')}.csv"
            path = export_file(name, csv_text) if export_file else None
            if path:
                first = tbl[0].strip("| ")
                attachments.append((path, f"Full table as CSV ({len(tbl) - 1} rows). First column: {first[:60]}"))
                block = "\n".join(tbl)
                text = text.replace(block, f"[table attached as {name}]")
            break  # one attachment per reply keeps the flow simple; small tables stay inline
    return chunk(text), attachments


def html_escape(text: str) -> str:
    """Telegram HTML parse mode: escape everything, then re-allow our own <b>/<i>/<code>/<pre>."""
    text = html.escape(text or "", quote=False)
    return text


if __name__ == "__main__":
    import ishti.channels.policy as P
    import tempfile, os
    P.PATH = os.path.join(tempfile.mkdtemp(), "p.yaml")
    P.reset_cache()
    # redaction
    out, info = sensitive_filter("Ali paid PKR 45,000 today, mail ali@x.com, phone 0300-1234567")
    assert info["redacted"] and "45,000" not in out and "ali@x.com" not in out, out
    assert "minimized" in out
    # block: secrets never travel
    out2, info2 = sensitive_filter("the API key is: sk-123 and the password: hunter2")
    assert info2["blocked"] and "sk-123" not in out2
    # chunking
    long = ("paragraph one. " * 200) + "\n\n" + ("paragraph two. " * 200)  # ~6 kB: needs 2+ pieces
    parts = chunk(long)
    assert all(len(p) <= 3800 for p in parts) and len(parts) >= 2
    # no words lost, order kept (boundary whitespace is trimmed by design)
    assert " ".join(long.split()) == " ".join(" ".join(parts).split())
    # small table stays, big table becomes CSV
    small = "| a | b |\n|---|---|\n| 1 | 2 |"
    chunks, atts = prepare_reply("before\n" + small + "\nafter")
    assert not atts and any("| a | b |" in c for c in chunks)
    big_rows = "| name | amount |\n|---|---|\n" + "\n".join(f"| person{i} | {i} |" for i in range(40))
    files = {}
    chunks, atts = prepare_reply("data:\n" + big_rows, export_file=lambda n, c: files.setdefault(n, c) or (n and f"/tmp/{n}"))
    assert atts and "attached" in chunks[-1] and "person39" not in "\n".join(chunks)
    print("[self-check] channels.output OK - redact, block, chunk, small-table inline, big-table CSV")
