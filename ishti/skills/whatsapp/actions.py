"""Verified message changes using observed WhatsApp controls and frozen T2 previews."""
from __future__ import annotations
from ishti.core.logutil import swallowed

import json
import re
import time

from ishti.skills import ask_first, trace
from ishti.skills.browser import skill as browser


def parse_operation(text):
    match = re.fullmatch(
        r"\s*(?:please\s+)?(?:edit|change)\s+(?:(?:the|my|last)\s+)*(?:message|msg|text)"
        r"(?:\s+(?:in|with|to)\s+(.+?)(?:\s+chat)?)?\s+(?:saying|to|with)\s+(.+?)\s*", text, re.I)
    if match:
        return {"kind": "edit", "contact": (match[1] or "").strip(), "replacement": match[2].strip()}
    return None


def _result(reply, verified=False):
    return {"reply": reply, "sources": [], "verified": verified}


def _unquote(value):
    if value.startswith('"') and value.endswith('"'):
        try:
            return json.loads(value)
        except ValueError:
            swallowed('ishti/skills/whatsapp/actions.py:_unquote:29')
    return value.strip("'")


def message_nodes(snap):
    """Exact text, ownership and live refs from message rows, excluding the sidebar."""
    from ishti.skills.whatsapp import skill as wa
    lines = (snap or "").splitlines()
    composers = list(re.finditer(r'textbox "Type a message', snap or ""))
    if composers:
        banners = list(re.finditer(r'^[ \t]*- banner \[ref=', snap[:composers[-1].start()], re.M))
        if banners:
            lines = snap[banners[-1].start():composers[-1].start()].splitlines()
    nodes = []
    for i, line in enumerate(lines):
        row = re.search(r'\brow "((?:\\.|[^"\\])*)" \[ref=([^\]]+)\]', line)
        if not row:
            continue
        label = _unquote('"' + row[1] + '"')
        parsed = wa.MSG_TIME.match(label)
        if not parsed:
            continue
        indentation = len(line) - len(line.lstrip())
        end = next((j for j in range(i + 1, len(lines)) if lines[j].strip() and
                    len(lines[j]) - len(lines[j].lstrip()) <= indentation), len(lines))
        block = "\n".join(lines[i:end])
        content = parsed["body"].strip()
        edited = bool(re.search(r'\bgeneric(?: "Edited"(?:\s|\[)|(?: \[ref=[^\]]+\])?: Edited\b)', block, re.I))
        if content.endswith(" Edited") and "Edited" in block:
            content = content[:-7].rstrip()
            edited = True
        body_ref = None
        for child in re.finditer(r'\bgeneric \[ref=([^\]]+)\]: ([^\n]+)', block):
            if _unquote(child[2]).strip() == content:
                body_ref = child[1]
                break
        nodes.append({"body": content, "ref": row[2], "body_ref": body_ref,
                      "from_me": parsed["who"] in ("You", "Me", "Owner") or 'generic "You:"' in block,
                      "edited": edited, "block": block})
    return nodes


def _enabled_ref(snap, role, label):
    found = re.findall(rf'\b{role} "{re.escape(label)}"((?: \[[^\]]*\])*)', snap or "")
    refs = []
    for flags in found:
        match = re.search(r'\[ref=([^\]]+)\]', flags)
        if match and "[disabled]" not in flags:
            refs.append(match[1])
    return refs[0] if len(refs) == 1 else None


def _editor_text(snap):
    lines = snap.splitlines()
    starts = [i for i, line in enumerate(lines) if 'textbox "Type a message"' in line]
    if len(starts) != 1:
        return None
    start = starts[0]
    indentation = len(lines[start]) - len(lines[start].lstrip())
    end = next((j for j in range(start + 1, len(lines)) if lines[j].strip() and
                len(lines[j]) - len(lines[j].lstrip()) <= indentation), len(lines))
    paragraphs = []
    for line in lines[start + 1:end]:
        match = re.search(r'\bparagraph(?: \[ref=[^\]]+\])?: (.+)$', line)
        if match:
            paragraphs.append(_unquote(match[1]))
    return "\n".join(paragraphs) if paragraphs else None


def _dialog_content(snap):
    lines = snap.splitlines()
    starts = [i for i, line in enumerate(lines) if re.match(r'^[ \t]*- dialog\b', line)]
    if len(starts) != 1:
        return ""
    start = starts[0]
    indentation = len(lines[start]) - len(lines[start].lstrip())
    end = next((j for j in range(start + 1, len(lines)) if lines[j].strip() and
                len(lines[j]) - len(lines[j].lstrip()) <= indentation), len(lines))
    return "\n".join(lines[start:end])


def _pane(session, contact):
    from ishti.skills.whatsapp import skill as wa
    browser.use_server(browser.profile_for("whatsapp"), session)
    snap = browser._call("browser_snapshot", {})
    if re.search(r'^[ \t]*- dialog\b', snap, re.M):
        raise ValueError("A dialog is already open in WhatsApp. Close it before changing a message.")
    actual, own = wa.current_chat_identity(snap)
    if not actual or wa.norm(actual) != wa.norm(contact):
        selected, snap = wa.open_chat(contact, session, exact_name=contact)
        if not selected or not isinstance(snap, str):
            raise ValueError("I could not locate the exact intended chat. Nothing was changed.")
        actual, own = wa.current_chat_identity(snap)
    if not actual or wa.norm(actual) != wa.norm(contact):
        raise ValueError("The active chat differs from the intended recipient. Nothing was changed.")
    if session.get("live_self_test") and not own:
        raise ValueError("Live testing can only change the verified Message yourself conversation.")
    return snap


def _exact_node(snap, body):
    nodes = [node for node in message_nodes(snap) if node["body"] == body]
    if len(nodes) != 1 or not nodes[0]["body_ref"]:
        raise ValueError("The original message is no longer a unique, identifiable message from you. Nothing was changed.")
    return nodes[0]


def message_identity(node):
    """Read only identity/status metadata from the exact observed message element.

    Consecutive outgoing bubbles omit the accessibility sender. Plain message text
    cannot supply these DOM attributes, so a quoted 'You:' is not ownership proof.
    """
    output = browser._call("browser_evaluate", {
        "element": "the exact message identity and delivery status", "target": node["body_ref"],
        "function": "element => { const row = element.closest('[data-id]'); "
                    "if (!row) return null; "
                    "const labels = Array.from(row.querySelectorAll('[aria-label]'))"
                    ".map(e => (e.getAttribute('aria-label') || '').trim()); "
                    "return {messageId: row.getAttribute('data-id'), "
                    "fromMe: labels.some(label => /^(Read|Delivered|Sent|Sending|Pending)$/.test(label))}; }"})
    try:
        raw = output.split("### Result", 1)[-1].lstrip()
        identity, _ = json.JSONDecoder().raw_decode(raw)
        if (not isinstance(identity, dict) or not isinstance(identity.get("messageId"), str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{6,200}", identity["messageId"])
                or not isinstance(identity.get("fromMe"), bool)):
            raise ValueError
        return identity
    except (TypeError, ValueError):
        raise ValueError("The message identity and ownership could not be verified. Nothing was changed.") from None


def _open_options(node):
    # A right click on the row itself opened the CHAT menu during live testing.
    # The exact message text element opens the observed message action menu.
    browser._call("browser_click", {"element": "the exact message text options",
                                   "target": node["body_ref"], "button": "right"})
    return browser._call("browser_snapshot", {})


def prepare(operation, session):
    from ishti import test_mode
    if test_mode.enabled():
        return _result("I did not change anything. Message changes are blocked in automated dry-run mode.")
    contact = operation.get("contact") or session.get("whatsapp_last")
    if not contact:
        return _result("Which WhatsApp chat contains the message you want to edit?")
    replacement = str(operation.get("replacement") or "").strip()
    if not replacement:
        return _result("What exact replacement text should I use?")
    with browser.LOCK:
        try:
            snap = _pane(session, contact)
            candidates = [node for node in message_nodes(snap) if node["body_ref"]]
            if session.get("live_self_test"):
                candidates = [node for node in candidates if node["body"].startswith("ISHTI SELF TEST ")]
                if not candidates:
                    return _result("Live testing may only change the synthetic messages created for this test.")
            node = identity = None
            for candidate in reversed(candidates[-20:]):
                evidence = message_identity(candidate)
                if evidence["fromMe"]:
                    node, identity = candidate, evidence
                    break
            if not node:
                return _result("No identifiable message from you is visible in that chat. Nothing was changed.")
            original = node["body"]
            node = _exact_node(snap, original)
            approved_id = identity["messageId"]
            if original == replacement:
                return _result("That message already contains the requested text.", True)
            menu = _open_options(node)
            eligible = bool(_enabled_ref(menu, "menuitem", "Edit"))
            browser._call("browser_press_key", {"key": "Escape"})
            if not eligible:
                return _result("WhatsApp does not offer Edit for that message. It may be outside the 15-minute window; nothing was changed.")
        except (ValueError, RuntimeError) as exc:
            return _result(str(exc))

    def commit():
        if test_mode.enabled():
            return test_mode.blocked("whatsapp", "edit approved message")
        with browser.LOCK:
            try:
                snap = _pane(session, contact)
                node = _exact_node(snap, original)
                identity = message_identity(node)
                if not identity["fromMe"] or identity["messageId"] != approved_id:
                    return _result("The message no longer matches the approved outgoing message. Nothing was changed.")
                menu = _open_options(node)
                edit = _enabled_ref(menu, "menuitem", "Edit")
                if not edit:
                    browser._call("browser_press_key", {"key": "Escape"})
                    return _result("Edit is no longer available. Nothing was changed.")
                browser._call("browser_click", {"element": "Edit the approved message", "target": edit})
                editor = browser._call("browser_snapshot", {})
                dialog = _dialog_content(editor)
                box = _enabled_ref(dialog, "textbox", "Type a message")
                if not box or original not in dialog or not re.search(r'heading "Edit message"', dialog):
                    return _result("The edit dialog could not be verified. I stopped before saving.")
                browser._call("browser_type", {"element": "approved replacement text", "target": box, "text": replacement})
                ready = browser._call("browser_snapshot", {})
                save = _enabled_ref(ready, "button", "Send edited message")
                typed = _editor_text(ready)
                if not save or typed is None or typed.strip() != replacement:
                    return _result("The edited draft did not match the approved replacement or has no enabled save control. I stopped before saving.")
                browser._call("browser_click", {"element": "Save the approved message edit", "target": save})
                for _ in range(4):
                    after = browser._call("browser_snapshot", {})
                    nodes = message_nodes(after)
                    matches = [n for n in nodes if n["body"] == replacement and n["edited"] and n["body_ref"]]
                    verified = any((identity := message_identity(n))["fromMe"]
                                   and identity["messageId"] == approved_id for n in matches)
                    if verified:
                        session["pending"] = None
                        last_send = session.get("last_whatsapp_send")
                        if isinstance(last_send, dict):
                            last_send["messages"] = tuple(replacement if message == original else message
                                                          for message in last_send.get("messages", ()))
                        trace(session, "verify", "edited message and Edited marker verified", status="done")
                        return _result(f"Edited your message in {contact}'s WhatsApp chat.", True)
                    time.sleep(0.5)
                return _result("The edit was submitted, but its final text and Edited marker were not verified. Check WhatsApp before retrying.")
            except (ValueError, RuntimeError) as exc:
                return _result(str(exc))

    return ask_first(session, "whatsapp", f"Edit your message in {contact}'s chat?\n\n"
                     f"Original: {original}\nReplacement: {replacement}", commit)
