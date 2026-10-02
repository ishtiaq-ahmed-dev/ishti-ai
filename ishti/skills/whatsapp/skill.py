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


PRONOUN_SEND = re.compile(r"^\s*(?:(?:and|then|now|also|please|yes|yeah|ok|okay)[,]?\s+)*(?:send|text|message|tell|reply)\s+(?:it\s+)?(?:to\s+)?(?:him|her|them)\b", re.I)  # "and send him hi": the last chat


def match(text):
    return bool(TRIGGER.search(text) or SEND_TO.search(text) or PRONOUN_SEND.search(text)
                or (SAID.search(text) and discussed_in(text)))  # "search for Ishaq Ahmed and let me know what he said"


def open_whatsapp(session, wait_s=4):
    """Open WhatsApp Web and wait for the chat list (a fresh tab shows a progress bar for a few seconds).
    Reuses the already-open WhatsApp tab (issue 6) instead of navigating - which reloads the page and loses the
    open chat every single time."""
    browser.use_server(browser.profile_for("whatsapp"), session)
    trace(session, "browser_navigate", "open WhatsApp Web (Chrome Profile 9)", status="start")
    snap, t0 = browser.open_url(URL, session), time.time()
    while "Chat list" not in snap and time.time() - t0 < wait_s:
        if re.search(r"Scan (?:the )?QR|Log in with phone|Link with phone", snap, re.I):
            break
        time.sleep(1)
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

# ---------------------------------------------------------------- chat context (issue 4/5)
# A message row in the chat pane: row "Bhai: Good 5:38 PM" [ref=...] / 'row "You: hi 1:39 PM Read" [ref=...]'
MSG_ROW = re.compile(r"\brow \"([^\"]+)\" \[ref=[^\]]+\]")
#   sender (WhatsApp drops it on consecutive messages from one person) + body + time + optional tick label
MSG_TIME = re.compile(r"^(?:(?P<who>[^:]{1,40}):\s+)?(?P<body>.*?)\s+(?P<t>\d{1,2}:\d{2}\s?[AP]M)"
                      r"(?:\s+(?:Sent|Delivered|Read))?$", re.I)


def parse_messages(snap, n=10):
    """The last n messages of the open chat as [(who, text)], oldest first - the context a reply needs.
    Supports row labels and generic/gridcell nodes in Playwright accessibility snapshots.
    Stops reading further back once the recent request/question is clear (early stopping)."""
    out, last = [], None
    candidates = []
    for m in MSG_ROW.finditer(snap):
        candidates.append(m.group(1))

    if not candidates:
        chat_start = snap.find("Message list")
        if chat_start == -1:
            chat_start = max(snap.rfind("Chat list"), snap.rfind("Search results"))
            chat_start = snap.find("\n-", chat_start + 10) if chat_start != -1 else 0
        chat_end = snap.find('textbox "Type a message', chat_start)
        if chat_end == -1:
            chat_end = len(snap)
        for line in snap[chat_start:chat_end].split("\n"):
            m = re.search(r'-\s*(?:generic|gridcell|text|statictext|row)\s*"([^"]+)"', line)
            if m:
                candidates.append(m.group(1))

    for raw in candidates:
        g = MSG_TIME.match(raw)
        who, body, t = None, None, None
        if g:
            who, body, t = (g.group("who") or "").strip(), (g.group("body") or "").strip(), g.group("t")
        else:
            time_match = re.search(r'\b(\d{1,2}:\d{2}\s?[AP]M)\b', raw, re.I)
            if time_match:
                t = time_match.group(1)
                body = raw.replace(t, "").strip().strip(",- ")
                if ":" in body:
                    parts = body.split(":", 1)
                    if len(parts[0].split()) <= 3:
                        who, body = parts[0].strip(), parts[1].strip()

        if not body:
            continue

        low = body.lower()
        if "voice message" in low or "play voice" in low:
            body = "[voice message]"
        elif "sticker" in low:
            body = "[sticker]"
        elif "photo" in low and "download" not in low:
            body = "[photo]"

        who = who or last or "Them"
        last = who
        out.append((who, body))

    # Early stopping: scan backwards from latest
    recent = []
    for who, body in reversed(out):
        recent.append((who, body))
        if ("?" in body or re.search(r'\b(?:can you|could you|please|let me know|are you|when|where|what|why|how|reschedule|confirm)\b', body, re.I)) and who.lower() not in ("you", "me", "owner"):
            if len(recent) >= 2:
                break
        if len(recent) >= n:
            break

    recent.reverse()
    return recent


read_chat_messages_raw = parse_messages


def read_chat_messages(snap, max_messages=10):
    """The last messages of the open chat as [{'sender': who, 'text': text}], oldest first."""
    msgs = parse_messages(snap, n=max_messages)
    return [{"sender": who, "text": text} for who, text in msgs]


def format_chat_context(contact, messages):
    """Formats messages into a clean transcript for the LLM prompt."""
    if not messages:
        return f"No recent messages visible in the chat with {contact}."
    lines = [f"Recent WhatsApp conversation with {contact}:"]
    for item in messages:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            who, text = item[0], item[1]
        elif isinstance(item, dict):
            who, text = item.get("sender", "Them"), item.get("text", "")
        else:
            who, text = "Message", str(item)
        lines.append(f"{who}: {text}")
    return "\n".join(lines)


def formulate_reply(contact, context_str, owner_text, session=None):
    """Passes the chat context directly to the LLM prompt to formulate a natural reply."""
    from ishti import llm
    prompt = (
        f"You are ISHTI, replying to a WhatsApp conversation on behalf of the owner.\n\n"
        f"{context_str}\n\n"
        f"Owner's reply intent / message: \"{owner_text}\"\n\n"
        f"Based on the conversation context, generate the final concise, natural WhatsApp reply message to send to {contact}. "
        f"If the owner gave the exact message to say, preserve it; if the owner gave instructions like 'say yes' or 'tell him I agree', formulate the appropriate natural reply. "
        f"Output ONLY the message text to send, with no quotes or explanations."
    )
    reply = llm.ask(prompt, system=None, temperature=0.2, max_tokens=150)
    return reply.strip().strip('"\'')


def open_chat(contact, session, exact_name=None):
    """Search for `contact` and open its chat -> (name, fresh chat snapshot); when the match is not exact,
    -> (None, ranked candidate names) so the owner picks - same rule the send flow uses."""
    found = search(contact, session)
    if not found:
        browser._call("browser_press_key", {"key": "Escape"})
        return None, []
    ranked = [(n, s) for n, s in rank(contact, [n for n, _ in found]) if s >= 0.66]
    exact = [n for n, _ in ranked if norm(n) == norm(exact_name or contact)]
    name = exact[0] if exact else (ranked[0][0] if len(ranked) == 1 else None)
    if not name:
        browser._call("browser_press_key", {"key": "Escape"})
        return None, [n for n, _ in ranked[:5]]
    browser._call("browser_click", {"element": f"chat with {name}", "target": dict(found)[name]})
    time.sleep(1.5)
    trace(session, "read", f"opened the chat with {name}")
    return name, browser._call("browser_snapshot", {})


REPLY = re.compile(r"\b(?:reply|respond|write\s+back|get\s+back\s+to)\b", re.I)
READ_CHAT = re.compile(r"\b(?:read|check|open|what(?:'s| is)?\s+(?:the\s+)?)\s*(?:out\s+)?"
                       r"(?:my\s+|the\s+|his\s+|her\s+)?(?:last\s+|latest\s+|recent\s+)?\d*\s*"
                       r"(?:messages?|msgs|chats?)\b|\bwhat\s+(?:did|has)\s+\w+\s+(?:said|sent|texted|written)\b"
                       r"|\bmessages?\s+(?:from|with|in)\s+\w+", re.I)
VOICE_NOTE = re.compile(r"\b(?:voice\s*(?:note|message)|audio\s+message|say\s+it|as\s+(?:a\s+)?voice)\b", re.I)


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


def parse_send(text, session, context=None):
    """-> (contact, file path or None, [messages], words for a voice note or None).

    `context` - the last messages of the chat being replied to - goes into the prompt, so a reply the owner
    only described ("reply that I'm coming") is written against what was actually said."""
    from ishti import llm
    import json
    ctx = ""
    if context:
        ctx = ("\n\nRecent messages in that chat (for context, never to quote back):\n"
               + "\n".join(f"{who}: {body}" for who, body in context))
    raw = llm.ask('Extract from this request to send something on WhatsApp. JSON: {"contact": "<who to send to, every word exactly as written, e.g. qasim ims>", '
                  '"refers_to_file": true/false (sending a file, video, image, audio, "this"/"it"), '
                  '"messages": ["<each separate text message, in order; split only if the owner asks for separate '
                  'messages; write emoji the owner names as the emoji characters, e.g. heart emoji -> ❤️>"], '
                  '"voice_note": "<the words to say if the owner wants a voice note / voice message / audio of him '
                  f'saying something, else null>"}}. Output only JSON.\n\n{text}{ctx}', system=None, temperature=0, max_tokens=200,
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
    contact = resolve_contact(str(d.get("contact") or "").strip(), session)
    return contact, path if path and os.path.isfile(path) else None, msgs, voice


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


def _out(reply, verified):
    """A skill result that carries the truth about verification (the dispatcher warns on verified=False claims)."""
    return {"reply": reply, "sources": [], "verified": bool(verified)}


def _enabled_send(snap):
    """True when a Send button that is NOT disabled is on the page - i.e. a file preview is still waiting to go."""
    return any("disabled" not in flags for flags in re.findall(r'button "Send"((?: \[[^\]]*\])*)', snap))


def deliver(ref, name, path, messages, session):
    """Open the chat, send the file (the first message as its caption), then each text message in order. The owner
    named the contact and what to send, so an exact contact match sends without another question.

    Returns {"reply", "sources", "verified"}: verified=True only when the page itself shows the outcome (the sent
    text in the chat, or the file preview closed after Send) - an unverified claim must never sound like a done
    deal (the dispatcher appends an honest warning to verified=False claims)."""
    browser._call("browser_click", {"element": f"chat with {name}", "target": ref})
    time.sleep(1.5)
    snap = browser._call("browser_snapshot", {})

    # Issue 4: Read conversation context from the opened chat
    chat_messages = parse_messages(snap, n=10)
    context_str = format_chat_context(name, chat_messages)
    session["whatsapp_context"] = {"contact": name, "messages": chat_messages, "context_str": context_str}
    trace(session, "context", f"Read {len(chat_messages)} recent message(s) from chat with {name}")

    # If this is a reply, formulate using the conversation context in LLM prompt
    if session.get("is_reply") and messages and context_str:
        refined = formulate_reply(name, context_str, messages[0], session)
        if refined:
            messages = [refined] + messages[1:]

    # Issue 5: If a voice note reply is requested, generate voice message via TTS
    if (session.get("is_voice_reply") or session.get("voice")) and not path:
        voice_words = messages.pop(0) if messages else (session.get("voice") or "Yes")
        if session.get("is_reply") and context_str:
            voice_words = formulate_reply(name, context_str, voice_words, session)
        path, whose = voice_note(voice_words)
        trace(session, "voice_note", f"Recorded context-aware voice reply in {whose}")

    messages, done = list(messages), []
    file_ok = True
    if path:
        ext = os.path.splitext(path)[1].lower()
        # WhatsApp Web attach menu has 'Photos & videos' and 'Document'; audio files are uploaded as Document
        kind = "Photos & videos" if ext in VISUAL else "Document"
        task = f"attach the file {path} with the '{kind}' option and send it" + (f" with the caption {messages[0]!r}" if messages else "")
        attach = browser.locate(snap, "the Attach button (paperclip or plus) of the chat", "button", "Attach", session)
        if not attach:
            return _out(_stuck("the attach button", name, task, session), False)
        snap = browser._call("browser_click", {"element": "Attach", "target": attach})
        item = browser.locate(snap, f"the '{kind}' option of the attach menu", "menuitem",
                              re.escape(kind).replace("&", "(?:&|&amp;|and)"), session)
        if not item:
            browser._call("browser_press_key", {"key": "Escape"})
            return _out(_stuck(f"the '{kind}' option", name, task, session), False)
        browser._call("browser_click", {"element": kind, "target": item})
        browser._call("browser_file_upload", {"paths": [path]})
        time.sleep(3)  # WhatsApp builds the preview
        snap = browser._call("browser_snapshot", {})
        if messages and kind == "Photos & videos" and (cap := browser.locate(snap, "the caption box of the file preview", "textbox",
                                                                             r"(?:Add a caption|Type a message)[^\"]*", session)):
            browser._call("browser_type", {"element": "caption", "target": cap, "text": messages.pop(0)})
            snap = browser._call("browser_snapshot", {})
        send = browser.refs(snap, "button", "Send") or [browser.locate(snap, "the Send button of the file preview", session=session)]
        if not send[-1]:
            return _out(_stuck("the Send button of the file preview", name, "send the file that is in the preview", session), False)
        browser._call("browser_click", {"element": "Send", "target": send[-1]})
        time.sleep(2)
        done.append(os.path.basename(path))
        # the preview is gone (only the disabled chat-box Send remains) = WhatsApp took the file
        snap = browser._call("browser_snapshot", {})
        file_ok = not _enabled_send(snap)
        if not file_ok:
            trace(session, "verify", f"the file preview for {os.path.basename(path)} is still open", status="error")
    for i, msg in enumerate(messages):
        snap = browser._call("browser_snapshot", {})
        box = browser.locate(snap, f"the message box of the chat with {name}", "textbox", r"Type a message[^\"]*", session)
        if not box:
            rest = " then ".join(repr(m) for m in messages[i:])
            return _out(("Sent " + ", ".join(done) + ". " if done else "") + _stuck("the message box", name, f"send the messages {rest}", session), False)
        browser._call("browser_type", {"element": "message box", "target": box, "text": msg, "submit": True})
        time.sleep(1.2)
        done.append(repr(msg) if len(msg) < 60 else "your message")
    snap = browser._call("browser_snapshot", {})
    text_ok = not messages or _sent(snap, messages[-1])
    if not text_ok:
        trace(session, "verify", "the last message isn't visible in the chat yet", status="error")
        return _out(f"I typed it to {name}, but I can't see it in the chat - please check WhatsApp.", False)
    if not file_ok:
        return _out(f"I attached {done[0] if done else 'the file'} to {name}'s chat, but the preview didn't close - "
                    f"it may not have gone out. Please check WhatsApp.", False)
    trace(session, "send", f"sent {len(done)} item(s) to {name}", status="done")
    return _out(f"Sent {', '.join(done)} to {name} on WhatsApp.", True)


def send_flow(text, session, pend=None):
    exact_name = None
    voice = None
    owner_named = False  # the owner just answered "who should I send it to?" - a clear match is the answer
    if pend:
        if pend.get("ask") == "contact":
            c, _, msgs, v = parse_send(f"send to {text}", session)
            # "The name is Bai." answers the question - the phrase must not become part of the contact name
            contact = re.sub(r"^(?:the\s+name\s+is|name\s+is|it'?s|it\s+is|contact\s+is)\s+", "",
                             (c or text).strip(), flags=re.I).strip(" .,!?")
            path = pend.get("file")
            messages = pend.get("messages") or msgs
            voice = pend.get("voice") or v
            owner_named = True
            exact_name = contact
        elif pend.get("ask") == "content":
            contact = pend.get("contact")
            exact_name = pend.get("exact_name")
            path = pend.get("file")
            if pend.get("voice"):
                path, whose = voice_note(text.strip())
                trace(session, "voice_note", f"recorded the voice note in {whose}")
                messages = []
            else:
                messages = [text.strip()]
        elif pend.get("ask") == "reply":
            contact = pend.get("contact")
            exact_name = pend.get("exact_name") or contact
            is_voice = bool(VOICE_NOTE.search(text))
            session["is_reply"] = True
            session["is_voice_reply"] = is_voice
            context_str = pend.get("context", "")
            refined = formulate_reply(contact, context_str, text, session)
            if is_voice:
                path, whose = voice_note(refined)
                messages = []
            else:
                path = None
                messages = [refined]
        elif pend.get("ask") == "retry":
            contact = pend.get("contact")
            exact_name = pend.get("contact")
            path = pend.get("path") or pend.get("file")
            messages = pend.get("messages") or []
        elif "candidates" in pend:
            pick = re.search(r"\b(\d)\b", text)
            names = pend["candidates"]
            chosen = names[int(pick.group(1)) - 1] if pick and 0 < int(pick.group(1)) <= len(names) else \
                next((n for n in names if norm(text) and norm(text) in norm(n)), None)
            if not chosen:
                return None  # not an answer: route normally
            contact, path, messages, exact_name = chosen, pend.get("file"), pend.get("messages", []), chosen
        else:
            contact = pend.get("contact")
            path = pend.get("file")
            messages = pend.get("messages", [])
            exact_name = pend.get("exact_name")
    else:
        is_reply = bool(REPLY.search(text))
        is_voice = bool(VOICE_NOTE.search(text))
        session["is_reply"] = is_reply
        session["is_voice_reply"] = is_voice
        contact, path, messages, voice = parse_send(text, session)
        if is_voice:
            session["voice"] = voice or (messages[0] if messages else True)
        if not contact:
            session["pending"] = {"skill": "whatsapp", "sticky": True, "ask": "contact", "file": path, "messages": messages, "voice": voice}
            return {"reply": "Who should I send it to?", "sources": []}
        if voice and not path:
            path, whose = voice_note(voice)
            trace(session, "voice_note", f"recorded the voice note in {whose}")
        if not path and not messages and not is_reply:
            session["pending"] = {"skill": "whatsapp", "sticky": True, "ask": "content", "contact": contact, "exact_name": exact_name, "voice": bool(voice)}
            return {"reply": "What should I send? Name the file or open it in the chat first, or give me the message.", "sources": []}
    if not contact:
        session["pending"] = {"skill": "whatsapp", "sticky": True, "ask": "contact", "file": path, "messages": messages, "voice": voice}
        return {"reply": "Who should I send it to?", "sources": []}
    found = search(contact, session)
    browser._call("browser_press_key", {"key": "Escape"}) if not found else None
    ranked = [(n, s) for n, s in rank(contact, [n for n, _ in found]) if s >= 0.66]
    exact = [n for n, _ in ranked if norm(n) == norm(exact_name or contact)]
    if owner_named and not exact and ranked:
        # the owner just named the person: the one clear match above the threshold IS their answer - asking
        # "did you mean 1. Bhai?" after "the name is Bai" is the loop that lost the message
        top = ranked[0]
        if len(ranked) == 1 or top[1] - ranked[1][1] >= 0.1:
            exact = [top[0]]
    if exact:
        ref = dict(found)[exact[0]]
        res = deliver(ref, exact[0], path, messages, session)
        if res.get("verified"):
            session["pending"] = None
            session["last_whatsapp_send"] = {"contact": exact[0], "path": path, "messages": messages}
            # the flags describe this one send; a later, ordinary message must not inherit them
            session["is_reply"] = session["is_voice_reply"] = False
            session["voice"] = None
            return res
        session["pending"] = {"skill": "whatsapp", "sticky": True, "ask": "retry", "contact": exact[0], "path": path, "messages": messages}
        return res
    browser._call("browser_press_key", {"key": "Escape"})
    if not ranked:
        session["pending"] = {"skill": "whatsapp", "sticky": True, "ask": "contact", "file": path, "messages": messages, "voice": voice}
        return {"reply": f"I couldn't find anyone like '{contact}' on WhatsApp. Check the name?", "sources": []}
    names = [n for n, _ in ranked[:5]]
    session["pending"] = {"skill": "whatsapp", "sticky": True, "candidates": names, "file": path, "messages": messages, "voice": voice}
    return {"reply": f"I didn't find '{contact}' exactly. Did you mean:\n" + "\n".join(f"{i}. {n}" for i, n in enumerate(names, 1))
                     + "\nReply with the number or name.", "sources": []}


def _copy_file_clipboard(path):
    """Put a file on the Windows clipboard as CF_HDROP so Ctrl+V pastes it (WhatsApp Desktop accepts drops this
    way). True on success; False when pywin32/the clipboard is unavailable (the send then must not pretend)."""
    try:
        import win32clipboard
        import win32con
        from ctypes import sizeof, c_uint, c_bool, c_long, Structure

        class _POINT(Structure):
            _fields_ = [("x", c_long), ("y", c_long)]

        class _DROPFILES(Structure):
            _fields_ = [("pFiles", c_uint), ("pt", _POINT), ("fNC", c_bool), ("fWide", c_bool)]

        hdr = _DROPFILES()
        hdr.pFiles = sizeof(_DROPFILES)
        hdr.fWide = True
        data = bytes(hdr) + (os.path.abspath(path) + "\0\0").encode("utf-16le")
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_HDROP, data)
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception as exc:
        print(f"[whatsapp] clipboard file copy failed: {type(exc).__name__}: {exc}")
        return False


def send_desktop(contact, messages, path, session):
    """Sends via Windows WhatsApp Desktop app using desktop automation."""
    from desktop.computer_use import get_computer_use_agent
    import pyperclip

    agent = get_computer_use_agent()
    trace(session, "app", "Opening WhatsApp Desktop", status="start")

    # 1. Launch/focus WhatsApp
    try:
        os.startfile("whatsapp:")
    except Exception:
        agent.launch_app("whatsapp")
    time.sleep(1.2)
    agent.focus_window("whatsapp")
    time.sleep(0.8)

    # 2. Focus search and locate contact
    trace(session, "contacts", f"Searching for '{contact}' in WhatsApp")
    agent.keyboard_hotkey("ctrl", "f")
    time.sleep(0.4)
    agent.keyboard_hotkey("ctrl", "a")
    agent.press_key("backspace")
    time.sleep(0.2)

    agent.keyboard_type(contact)
    time.sleep(1.2)

    agent.press_key("enter")
    time.sleep(1.0)

    done = []
    if path and os.path.isfile(path):
        trace(session, "upload", f"Attaching {os.path.basename(path)}")
        if _copy_file_clipboard(path):  # CF_HDROP paste: how the desktop app accepts a file drop
            agent.keyboard_hotkey("ctrl", "v")
            time.sleep(1.5)
            agent.press_key("enter")
            time.sleep(1.0)
            done.append(os.path.basename(path))
        else:
            return _out(f"I couldn't put {os.path.basename(path)} on the clipboard, so nothing was sent to "
                        f"{contact} - please attach it in WhatsApp yourself.", False)

    for msg in messages:
        trace(session, "type", f"Typing: {msg}")
        pyperclip.copy(msg)
        agent.keyboard_hotkey("ctrl", "v")
        time.sleep(0.3)
        agent.press_key("enter")
        time.sleep(0.8)
        done.append(repr(msg) if len(msg) < 60 else "your message")

    trace(session, "send", f"Sent to {contact} on WhatsApp", status="done")
    items = ", ".join(done) if done else "message"
    # the desktop app is driven blind (keyboard automation): what went out cannot be confirmed from here
    return _out(f"Sent {items} to {contact} on WhatsApp.", False)


def run(text, session):
    with browser.LOCK:
        return _run(text, session)


# a message that starts a NEW request is not the answer to "which contact?" (owner's chat: "Open WhatsApp Web." was searched as a contact name)
NEW_COMMAND = re.compile(r"^\s*(?:please\s+|now\s+|and\s+)?(?:open|launch|start|go\s+to|switch\s+to|check|read|show|send|message|text|tell|search|find|call|remind|what|who|how|why|when)\b", re.I)
PRONOUN = {"him", "her", "them", "he", "she", "it", "and", "again", "same", "the same person", "that person", "this person", "same person"}
OPEN_ONLY = re.compile(r"^\s*(?:please\s+)?(?:open|launch|start|switch\s+to|focus|go\s+to)\s+(?:the\s+|my\s+)?(?:whatsapp|whatsap|watsapp|whastap)"
                       r"(?:\s+(?P<kind>web|desktop|app|application))?(?:\s+(?:in|on)\s+(?:chrome|browser|my\s+(?:pc|computer|desktop)))?\s*[.!]?$", re.I)


DISCUSS = re.compile(
    r"\b(?:search|check|look|open|go)\s+(?:for\s+|in\s+|into\s+|to\s+)?(?:the\s+|my\s+)?(?P<a>[\w][\w&'-]*(?:\s+[\w&'-]+){0,4}?)\s*(?:group|chat)?"
    r"\s*(?:,\s*|\s+and\s+)(?:then\s+)?(?:let\s+me\s+know|tell\s+me|give\s+me|summari[sz]e|show\s+me|read)"
    r"|\bwhat\s+(?:we|they|he|she|people)\s+(?:have\s+|had\s+|has\s+)?(?:discussed|talked\s+about|said|wrote|decided)\s+(?:in|with|there\s+in)\s+"
    r"(?:the\s+|my\s+)?(?P<b>[\w][\w&'-]*(?:\s+[\w&'-]+){0,4}?)(?:\s+group|\s+chat)?\s*[.?!]?$", re.I)
SAID = re.compile(r"\bwhat\s+(?:he|she|they|we)\s+(?:have\s+|had\s+|has\s+)?(?:said|say|says|discussed|talked\s+about|wrote|sent)\b", re.I)


def discussed_in(text):
    """The chat/group the owner wants read ("automation workflow"), or None."""
    m = DISCUSS.search(text)
    if not m:
        return None
    name = re.sub(r"^(?:whatsapp\s+(?:and\s+)?(?:search|check|look|open)?\s*(?:for\s+)?)", "", (m["a"] or m["b"] or "").strip(), flags=re.I)
    name = re.sub(r"\s+(?:group|chat)$", "", name, flags=re.I).strip()
    return name if name and name.lower() not in ("whatsapp", "it", "there", "that", "this") else None


def resolve_contact(name, session):
    """Who "him", "my brother" or "bhai" is: the person ISHTI just wrote to / read, or the owner's relative of that kind."""
    key = (name or "").strip().lower()
    if not key or key in PRONOUN:
        return session.get("whatsapp_last") or name
    kin = re.sub(r"^my\s+", "", key)
    from ishti import rgraph
    if kin in rgraph.KIN:
        edges = rgraph.edges_of("me", rel=kin, direction="out") or rgraph.edges_of("me", rel=kin, direction="in")
        if edges:
            e = edges[0]
            return (e["d_name"] if e["s_name"] == "me" else e["s_name"]).title()
    session["whatsapp_last"] = name
    return name


def open_preferred(session, kind=None):
    """Open WhatsApp ONCE, the way the owner prefers (Settings: Learning > About you): Web in Chrome Profile 9 by default, or the desktop
    app - never both (the owner's complaint: 'it was opening WhatsApp Web and the Desktop app both, which was a mess')."""
    from ishti import prefs
    mode = "desktop" if (kind or "").lower() in ("desktop", "app", "application") else "web" if kind else prefs.whatsapp_mode()
    if mode == "desktop":
        from desktop.computer_use import get_computer_use_agent
        agent = get_computer_use_agent()
        agent.launch_app("whatsapp")
        time.sleep(0.6)
        agent.focus_window("whatsapp")
        return {"reply": "Opened WhatsApp Desktop.", "sources": []}
    snap, ok = open_whatsapp(session)
    if ok:
        return {"reply": "Opened WhatsApp Web (Chrome Profile 9).", "sources": []}
    if re.search(r"Scan (?:the )?QR|Log in with phone|Link with phone", snap or "", re.I):
        return {"reply": "WhatsApp Web is open but it asks you to scan the QR code with your phone first (WhatsApp > Linked devices).", "sources": []}
    return {"reply": "I opened WhatsApp Web in Chrome Profile 9 but the chat list did not load yet - give it a few seconds.", "sources": []}


def _run(text, session):
    p = session.get("pending")
    if p and p.get("skill") == "whatsapp" and NEW_COMMAND.match(text):
        session["pending"] = p = None
    if p and p.get("skill") == "browser":  # yes/no to a send the browser engine asked about
        return browser.run(text, session)
    if p and p.get("skill") == "whatsapp":
        try:
            snap, ok = open_whatsapp(session, wait_s=3)
        except Exception:
            ok = False
        if ok:
            return send_flow(text, session, p)
        chosen_contact = p.get("contact") or (p.get("candidates", [text])[0] if p.get("candidates") else text)
        return send_desktop(chosen_contact, p.get("messages", []), p.get("file"), session)

    # 1. Plain "open WhatsApp" / "open WhatsApp Web": one window, the preferred kind
    m_open = OPEN_ONLY.match(text)
    if m_open and not (SEND.search(text) or SEND_TO.search(text)):
        return open_preferred(session, m_open.group("kind"))

    is_send = bool(SEND.search(text) or SEND_TO.search(text) or re.search(r"\b(?:send|message|text|write)\b", text, re.I))

    # Fast check if WhatsApp Web is open & logged in
    snap, ok = None, False
    try:
        snap, ok = open_whatsapp(session, wait_s=3)
    except Exception:
        ok = False

    # 2a. "open WhatsApp and check the automation workflow group and let me know what we discussed there": read that chat and answer
    if (target := discussed_in(text)) and not (SEND.search(text) or SEND_TO.search(text)):
        if not ok:
            return {"reply": "WhatsApp Web is not ready in Chrome Profile 9 yet (it may need the QR scan). Open it once and ask again.",
                    "sources": []}
        target = resolve_contact(target, session)
        name, chat_snap = open_chat(target, session)
        if isinstance(chat_snap, list) and not name:
            if not chat_snap:
                return {"reply": f"I couldn't find a chat or group called \"{target}\" on WhatsApp.", "sources": []}
            session["pending"] = {"skill": "whatsapp", "sticky": True, "candidates": chat_snap, "ask": "contact"}
            return {"reply": "Which one?\n" + "\n".join(f"{i}. {n}" for i, n in enumerate(chat_snap, 1)), "sources": []}
        session["whatsapp_last"] = name
        messages = parse_messages(chat_snap, n=40)
        if not messages:
            return {"reply": f"I opened \"{name}\" but no messages were visible to read.", "sources": []}
        from ishti import llm
        transcript = format_chat_context(name, messages)
        trace(session, "read", f"read {len(messages)} messages in {name}")
        answer = llm.ask(f"{transcript}\n\nThe owner asks: {text}\nAnswer from these messages only: who said what, decisions, "
                         "open questions. Short bullet points. If the messages do not cover it, say so.", role="planner", temperature=0.2,
                         max_tokens=600)
        session["whatsapp_context"] = {"contact": name, "messages": messages, "context_str": transcript}
        return {"reply": f"**{name}** (last {len(messages)} messages):\n{answer}", "sources": []}

    # 2. Direct Read Chat Messages intent: "what did Ali say", "read messages from Ali", "check chat with Bhai"
    if READ_CHAT.search(text) and not (SEND.search(text) or SEND_TO.search(text)):
        m = re.search(r"\b(?:from|with|in)\s+([A-Za-z0-9_\s]{1,30})", text, re.I) or re.search(r"\bwhat\s+(?:did|has)\s+([A-Za-z0-9_\s]{1,30})\s+(?:say|send|text)", text, re.I)
        target_name = m.group(1).strip().rstrip(".,!?") if m else None
        if not target_name:
            c, _, _, _ = parse_send(f"send to {text}", session)
            target_name = c
        if target_name and ok:
            name, chat_snap = open_chat(target_name, session)
            if isinstance(chat_snap, list) and not name:  # candidates
                session["pending"] = {"skill": "whatsapp", "sticky": True, "candidates": chat_snap, "ask": "contact"}
                return {"reply": "Did you mean:\n" + "\n".join(f"{i}. {n}" for i, n in enumerate(chat_snap, 1)) + "\nReply with the number or name.", "sources": []}
            if name:
                messages = parse_messages(chat_snap, n=10)
                context_str = format_chat_context(name, messages)
                session["whatsapp_last"] = name
                session["whatsapp_context"] = {"contact": name, "messages": messages, "context_str": context_str}
                session["pending"] = {"skill": "whatsapp", "sticky": True, "ask": "reply", "contact": name, "exact_name": name, "context": context_str}
                return {"reply": f"{context_str}\n\nWould you like me to send a text or voice reply to {name}?", "sources": []}

    if is_send:
        if ok:
            return send_flow(text, session)
        # Desktop fallback
        contact, path, messages, voice = parse_send(text, session)
        if not contact:
            return {"reply": "Who should I send it to?", "sources": []}
        if voice and not path:
            path, whose = voice_note(voice)
            trace(session, "voice_note", f"recorded the voice note in {whose}")
        if not path and not messages:
            return {"reply": "What should I send? Give me the message or file.", "sources": []}
        return send_desktop(contact, messages, path, session)

    if not ok:
        from desktop.computer_use import get_computer_use_agent
        agent = get_computer_use_agent()
        agent.launch_app("whatsapp")
        time.sleep(0.6)
        agent.focus_window("whatsapp")
        return {"reply": "Opened WhatsApp on your desktop.", "sources": []}

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
    assert match("open whastap and send hi to bhai") and match("wasap ali") and not match("whatever happens")
    assert match("open WhatsApp and send the message hi to bhai")
    assert match("send or open WhatsApp and send the message hi to bhai")
    assert match("open whatsapp")
    # the bug that stopped sends: Playwright's "[active]" flag between the box's name and its ref
    chat = ('- button "Attach" [ref=f1e1725] [cursor=pointer]\n- textbox "Type a message to Bhai" [active] [ref=f1e1740]:\n'
            '- button "Send" [disabled] [ref=f9]\n- button "Send" [ref=f10]')
    assert browser.ref(chat, "textbox", r"Type a message[^\"]*") == "f1e1740" and browser.refs(chat, "button", "Send")[-1] == "f10"
    assert browser.locate(chat, "attach", "button", "Attach") == "f1e1725"
    print("[self-check] whatsapp OK")
