"""WhatsApp skill: WhatsApp Web in the owner's Chrome Profile 9.

Reading unread chats is done directly from the page (fast, no model steps); anything else (opening a chat,
writing a message) goes to the browser engine, which still asks before sending."""
import difflib
import os
import re
import time

from ishti.skills import trace
from ishti.skills.browser import skill as browser

TRIGGER = re.compile(r"\b(?:w+h?a+t*s+\s?a+p+|w(?:h)?at(?:s\s?)?app?|wh?as+t?a+p+)\b", re.I)  # whatsap, watsapp, whastap; not "what app"
UNREAD = re.compile(r"\b(?:unread|unseen|new\s+messages?|who\s+(?:messaged|texted))\b", re.I)
SEND = re.compile(r"\b(?:send|reply|message\s+to|write\s+to|tell\s+\w+\s+that)\b", re.I)
URL = "https://web.whatsapp.com"
HINT = ("You are in WhatsApp Web, already logged in. To message someone: click the search box, type their "
        "name, open their chat from the results, click the message box, type the message and press Enter. "
        "To read messages: open the chat and read the latest messages from the page. Never send anything the "
        "owner did not ask for.")


def match(text):
    return bool(TRIGGER.search(text) or SEND_TO.search(text))


def open_whatsapp(session, wait_s=40):
    """Open WhatsApp Web and wait for the chat list (a fresh tab shows a progress bar for a few seconds)."""
    browser.use_server(browser.profile_for("whatsapp"), session)
    trace(session, "browser_navigate", "open WhatsApp Web (Chrome Profile 9)", status="start")
    snap, t0 = browser._call("browser_navigate", {"url": URL}), time.time()
    while "Chat list" not in snap and time.time() - t0 < wait_s:
        if re.search(r"Scan (?:the )?QR|Log in with phone|Link with phone", snap, re.I):
            break
        time.sleep(2)
        snap = browser._call("browser_snapshot", {})
    ok = "Chat list" in snap
    trace(session, "browser_navigate", "WhatsApp loaded" if ok else "WhatsApp didn't show the chat list",
          status="done" if ok else "error")
    return snap, ok


def unread(snap):
    """[(chat name, unread count or None)] from the visible chat list, plus WhatsApp's own unread-chats total."""
    rows = []
    for block in re.split(r"\n\s*- row \[", snap[snap.find("Chat list"):])[1:]:
        top = re.search(r'gridcell "(?:(\d+) unread messages?|(Unread)) ', block)
        if not top:
            continue
        name = re.search(r'- generic "([^"]+)" \[ref', block)
        rows.append((name.group(1) if name else "?", int(top.group(1)) if top.group(1) else None))
    total = re.search(r'"Unread (\d+)"', snap)
    return rows, int(total.group(1)) if total else len(rows)


# "send this video to qasim ims" names no app: WhatsApp is ISHTI's messenger.
SEND_TO = re.compile(r"^(?!.*(?:\be-?mails?\b|\bgmail\b|@))\s*(?:please\s+)?(?:now\s+)?send\s+.{1,300}?\bto\s+\S+", re.I)  # email is the email skill's
MEDIA_PATH = re.compile(r"[A-Za-z]:\\[^\n\"'<>|?*`]+?\.(?:mp4|webm|mov|mkv|png|jpe?g|gif|webp|3gp|mp3|wav|ogg|opus|m4a|aac|pdf|docx?|xlsx?|pptx?|txt|csv|zip|rar|7z)\b", re.I)


def norm(name):
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def search_results(snap):
    """[(name, row ref)] of chats/contacts in WhatsApp's search results (message hits are skipped)."""
    out, section = [], ""
    for block in re.split(r"\n\s*- row \[", snap[snap.find("Search results"):])[1:]:
        head = re.search(r'heading "([^"]+)"', block)
        if head and "gridcell" not in block:
            section = head.group(1)
            continue
        if section.lower().startswith("messages"):
            continue
        name, cell = re.search(r'- generic "([^"]+)" \[ref', block), re.search(r'gridcell "[^"]*" \[ref=([^\]]+)\]', block)
        if name and cell and name.group(1) not in [n for n, _ in out]:
            out.append((name.group(1), cell.group(1)))
    return out


def rank(wanted, names):
    """Closest contact names first: half whole-name similarity, half how well each word the owner said is
    matched ('qasim ims' ~ 'Qassim IMS' beats 'Qasim Ali', which only matches one of the two words)."""
    w = norm(wanted)
    said = [norm(t) for t in wanted.split() if norm(t)] or [w]
    def score(n):
        toks = [norm(t) for t in re.split(r"\W+", n) if t] or [norm(n)]
        per_word = sum(max(difflib.SequenceMatcher(None, s, t).ratio() for t in toks) for s in said) / len(said)
        return 0.5 * difflib.SequenceMatcher(None, w, norm(n)).ratio() + 0.5 * per_word
    return sorted(((n, round(score(n), 2)) for n in names), key=lambda x: -x[1])


def search(name, session):
    """Type the first letters (spelling and voice errors live in the rest) and read the result names."""
    snap = browser._call("browser_snapshot", {})
    box = browser.locate(snap[:snap.find("Chat list")] if "Chat list" in snap else snap,
                         "the search box above the chat list", "textbox", r"Search[^\"]*", session)
    if not box:
        return []
    probe = re.split(r"\s+", name.strip())[0][:3]
    browser._call("browser_type", {"element": "WhatsApp search box", "target": box, "text": probe})
    time.sleep(2)
    found = search_results(browser._call("browser_snapshot", {}))
    trace(session, "contacts", f"searched '{probe}': {len(found)} chats/contacts")
    return found


def parse_send(text, session):
    """-> (contact, file path or None, [messages], words for a voice note or None)."""
    from ishti import llm
    import json
    raw = llm.ask('Extract from this request to send something on WhatsApp. JSON: {"contact": "<who to send to, every word exactly as written, e.g. qasim ims>", '
                  '"refers_to_file": true/false (sending a file, video, image, audio, "this"/"it"), '
                  '"messages": ["<each separate text message, in order; split only if the owner asks for separate '
                  'messages; write emoji the owner names as the emoji characters, e.g. heart emoji -> ❤️>"], '
                  '"voice_note": "<the words to say if the owner wants a voice note / voice message / audio of him '
                  f'saying something, else null>"}}. Output only JSON.\n\n{text}', system=None, temperature=0, max_tokens=200,
                  response_format={"type": "json_object"})
    try:
        d = json.loads(raw)
    except ValueError:
        d = {}
    msgs = d.get("messages") or ([d["message"]] if d.get("message") else [])
    msgs = [str(m).strip() for m in (msgs if isinstance(msgs, list) else [msgs]) if str(m or "").strip()]
    voice = str(d.get("voice_note") or "").strip() or None
    if voice:
        msgs = [m for m in msgs if m != voice]  # the spoken words aren't also typed
    path = MEDIA_PATH.search(text)
    if not path and d.get("refers_to_file"):  # "this video": the last file ISHTI showed in this chat
        for m in reversed(session.get("history", [])):
            if m["role"] == "assistant" and (hit := MEDIA_PATH.findall(m["content"])):
                path = hit[-1]
                break
    else:
        path = path and path.group(0)
    return str(d.get("contact") or "").strip(), path if path and os.path.isfile(path) else None, msgs, voice


VISUAL = (".mp4", ".webm", ".mov", ".mkv", ".3gp", ".png", ".jpg", ".jpeg", ".gif", ".webp")
AUDIO = (".mp3", ".wav", ".ogg", ".opus", ".m4a", ".aac", ".amr")


def voice_note(words):
    """The words spoken in the owner's voice (his cloned voice when he has recorded a sample, else ISHTI's voice),
    as an Opus .ogg - WhatsApp's own voice-note format."""
    import subprocess
    import tempfile
    from ishti import ROOT
    from ishti.skills.media.skill import ff
    from ishti.voice import synthesize_as_owner
    wav, whose = synthesize_as_owner(words)
    src = os.path.join(tempfile.mkdtemp(), "note.wav")
    with open(src, "wb") as f:
        f.write(wav)
    out = os.path.join(ROOT, "data", "media_output", f"voice_note_{time.strftime('%Y%m%d_%H%M%S')}.ogg")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    try:
        ff("-i", src, "-c:a", "libopus", "-b:a", "32k", "-ac", "1", "-ar", "48000", out)
    except subprocess.CalledProcessError:
        return src, whose  # no Opus encoder: WhatsApp takes the WAV too
    return out, whose


def _stuck(want, name, task, session):
    """A step's element isn't on the page (WhatsApp changed, a popup is in the way): look at a screenshot, look up
    how WhatsApp does it, and let the browser agent finish the task from there (it still asks before sending)."""
    trace(session, "stuck", f"couldn't find {want} - looking at the screen and searching how it's done", status="error")
    notes = browser.troubleshoot(want, "WhatsApp Web", session)
    out = browser.run(f"In WhatsApp Web, in the open chat with {name}: {task}", session,
                      server=browser.profile_for("whatsapp"), hint=HINT + "\n" + notes,
                      start_snapshot=browser._call("browser_snapshot", {}))
    return (out or {}).get("reply") or f"I couldn't find {want} in {name}'s chat."


def _sent(snap, text):
    """The last sent text is on the page (WhatsApp shows it in the chat once it has gone)."""
    return text[:40].lower() in snap.lower()


def deliver(ref, name, path, messages, session):
    """Open the chat, send the file (the first message as its caption), then each text message in order. The owner
    named the contact and what to send, so an exact contact match sends without another question."""
    browser._call("browser_click", {"element": f"chat with {name}", "target": ref})
    time.sleep(1.5)
    snap = browser._call("browser_snapshot", {})
    messages, done = list(messages), []
    if path:
        ext = os.path.splitext(path)[1].lower()
        kind = "Photos & videos" if ext in VISUAL else "Audio" if ext in AUDIO else "Document"
        task = f"attach the file {path} with the '{kind}' option and send it" + (f" with the caption {messages[0]!r}" if messages else "")
        attach = browser.locate(snap, "the Attach button (paperclip or plus) of the chat", "button", "Attach", session)
        if not attach:
            return _stuck("the attach button", name, task, session)
        snap = browser._call("browser_click", {"element": "Attach", "target": attach})
        item = browser.locate(snap, f"the '{kind}' option of the attach menu", "menuitem",
                              re.escape(kind).replace("&", "(?:&|&amp;|and)"), session)
        if not item:
            browser._call("browser_press_key", {"key": "Escape"})
            return _stuck(f"the '{kind}' option", name, task, session)
        browser._call("browser_click", {"element": kind, "target": item})
        browser._call("browser_file_upload", {"paths": [path]})
        time.sleep(3)  # WhatsApp builds the preview
        snap = browser._call("browser_snapshot", {})
        if messages and kind != "Audio" and (cap := browser.locate(snap, "the caption box of the file preview", "textbox",
                                                                     r"(?:Add a caption|Type a message)[^\"]*", session)):
            browser._call("browser_type", {"element": "caption", "target": cap, "text": messages.pop(0)})
            snap = browser._call("browser_snapshot", {})
        send = browser.refs(snap, "button", "Send") or [browser.locate(snap, "the Send button of the file preview", session=session)]
        if not send[-1]:
            return _stuck("the Send button of the file preview", name, "send the file that is in the preview", session)
        browser._call("browser_click", {"element": "Send", "target": send[-1]})
        time.sleep(2)
        done.append(os.path.basename(path))
    for i, msg in enumerate(messages):
        snap = browser._call("browser_snapshot", {})
        box = browser.locate(snap, f"the message box of the chat with {name}", "textbox", r"Type a message[^\"]*", session)
        if not box:
            rest = " then ".join(repr(m) for m in messages[i:])
            return ("Sent " + ", ".join(done) + ". " if done else "") + _stuck("the message box", name, f"send the messages {rest}", session)
        browser._call("browser_type", {"element": "message box", "target": box, "text": msg, "submit": True})
        time.sleep(1.2)
        done.append(repr(msg) if len(msg) < 60 else "your message")
    snap = browser._call("browser_snapshot", {})
    if messages and not _sent(snap, messages[-1]):
        trace(session, "verify", "the last message isn't visible in the chat yet", status="error")
        return f"I typed it to {name}, but I can't see it in the chat - please check WhatsApp."
    trace(session, "send", f"sent {len(done)} item(s) to {name}")
    return f"Sent {', '.join(done)} to {name} on WhatsApp."


def send_flow(text, session, pend=None):
    if pend:  # answer to "which contact?"
        pick = re.search(r"\b(\d)\b", text)
        names = pend["candidates"]
        chosen = names[int(pick.group(1)) - 1] if pick and 0 < int(pick.group(1)) <= len(names) else \
            next((n for n in names if norm(text) and norm(text) in norm(n)), None)
        if not chosen:
            return None  # not an answer: route normally
        contact, path, messages, exact_name = chosen, pend["file"], pend["messages"], chosen
    else:
        contact, path, messages, voice = parse_send(text, session)
        exact_name = None
        if not contact:
            return {"reply": "Who should I send it to?", "sources": []}
        if voice and not path:
            path, whose = voice_note(voice)
            trace(session, "voice_note", f"recorded the voice note in {whose}")
        if not path and not messages:
            return {"reply": "What should I send? Name the file or open it in the chat first, or give me the message.", "sources": []}
    found = search(contact, session)
    browser._call("browser_press_key", {"key": "Escape"}) if not found else None
    ranked = [(n, s) for n, s in rank(contact, [n for n, _ in found]) if s >= 0.66]
    exact = [n for n, _ in ranked if norm(n) == norm(exact_name or contact)]
    if exact:
        ref = dict(found)[exact[0]]
        return {"reply": deliver(ref, exact[0], path, messages, session), "sources": []}
    browser._call("browser_press_key", {"key": "Escape"})
    if not ranked:
        return {"reply": f"I couldn't find anyone like '{contact}' on WhatsApp. Check the name?", "sources": []}
    names = [n for n, _ in ranked[:5]]
    session["pending"] = {"skill": "whatsapp", "candidates": names, "file": path, "messages": messages}
    return {"reply": f"I didn't find '{contact}' exactly. Did you mean:\n" + "\n".join(f"{i}. {n}" for i, n in enumerate(names, 1))
                     + "\nReply with the number or name.", "sources": []}


def run(text, session):
    with browser.LOCK:
        return _run(text, session)


def _run(text, session):
    p = session.get("pending")
    if p and p.get("skill") == "browser":  # yes/no to a send the browser engine asked about
        return browser.run(text, session)
    if p and p.get("skill") == "whatsapp":
        session["pending"] = None
        open_whatsapp(session)
        return send_flow(text, session, p)
    try:
        snap, ok = open_whatsapp(session)
    except RuntimeError as exc:
        return {"reply": str(exc), "sources": []}
    if not ok:
        return {"reply": "WhatsApp Web isn't logged in in Chrome Profile 9. Open it there, scan the QR code once, "
                         "and ask again.", "sources": []}
    if SEND.search(text) or SEND_TO.search(text):
        return send_flow(text, session)
    if UNREAD.search(text) and not SEND.search(text):
        rows, total = unread(snap)
        trace(session, "read", f"{total} unread chats")
        if not total:
            return {"reply": "No unread chats on WhatsApp.", "sources": []}
        lines = [f"- {n}" + (f" ({c})" if c else " (marked unread)") for n, c in rows]
        more = f"\n{total - len(rows)} more further down the list." if total > len(rows) else ""
        return {"reply": f"{total} unread chat{'s' if total != 1 else ''} on WhatsApp:\n" + "\n".join(lines) + more,
                "sources": []}
    return browser.run(text, session, server=browser.profile_for("whatsapp"), hint=HINT, start_snapshot=snap)


if __name__ == "__main__":
    assert match("check my whatsap") and match("watsapp ali") and not match("what app should I use")
    sample = ('- grid "Chat list" [ref=e191]:\n  - row [ref=e192]:\n    - gridcell "Ali 12:54 hi Pinned chat" [ref=e193]:\n'
              '      - generic "Ali" [ref=e206]\n  - row [ref=e280]:\n    - gridcell "2 unread messages Sara 2:01 pm ok" '
              '[ref=e281]:\n      - generic "Sara" [ref=e290]\n  - row [ref=e300]:\n    - gridcell "Unread Class group 1:27 pm" '
              '[ref=e301]:\n      - generic "Class group" [ref=e310]\n- button "Unread 2" [ref=e135]')
    assert unread(sample) == ([("Sara", 2), ("Class group", None)], 2), unread(sample)
    assert UNREAD.search("which chats are unread") and SEND.search("send hi to ali on whatsapp")
    res = "\n".join([
        '- grid "Search results." [ref=e1]:',
        '  - row [ref=e2]:',
        '    - heading "Chats" [level=2] [ref=e3]',
        '  - row [ref=e4]:',
        '    - gridcell "Qassim IMS 1:27 pm hi" [ref=e5]:',
        '      - generic "Qassim IMS" [ref=e6]',
        '  - row [ref=e7]:',
        '    - gridcell "Qasim Ali" [ref=e8]:',
        '      - generic "Qasim Ali" [ref=e9]',
        '  - row [ref=e10]:',
        '    - heading "Messages" [level=2] [ref=e11]',
        '  - row [ref=e12]:',
        '    - gridcell "Zara hi qasim" [ref=e13]:',
        '      - generic "Zara" [ref=e14]',
    ])
    assert search_results(res) == [("Qassim IMS", "e5"), ("Qasim Ali", "e8")], search_results(res)
    assert rank("qasim ims", ["Qasim Ali", "Qassim IMS", "Zara"])[0][0] == "Qassim IMS"
    assert norm("Qasim  IMS") == norm("qasim ims") and match("send this video to qasim ims")
    assert match("open whastap and send hi to bhai") and match("wasap ali") and not match("whatever happens")
    # the bug that stopped sends: Playwright's "[active]" flag between the box's name and its ref
    chat = ('- button "Attach" [ref=f1e1725] [cursor=pointer]\n- textbox "Type a message to Bhai" [active] [ref=f1e1740]:\n'
            '- button "Send" [disabled] [ref=f9]\n- button "Send" [ref=f10]')
    assert browser.ref(chat, "textbox", r"Type a message[^\"]*") == "f1e1740" and browser.refs(chat, "button", "Send")[-1] == "f10"
    assert browser.locate(chat, "attach", "button", "Attach") == "f1e1725"
    print("[self-check] whatsapp OK")
