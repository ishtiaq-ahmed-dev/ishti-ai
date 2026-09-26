"""WhatsApp skill: WhatsApp Web in the owner's Chrome Profile 9.

Reading unread chats is done directly from the page (fast, no model steps); anything else (opening a chat,
writing a message) goes to the browser engine, which still asks before sending."""
import re
import time

from ishti.skills import trace
from ishti.skills.browser import skill as browser

TRIGGER = re.compile(r"\bw(?:h)?at(?:s\s?)?app?\b", re.I)  # typos: whatsap, watsapp, whats app; not "what app"
UNREAD = re.compile(r"\b(?:unread|unseen|new\s+messages?|who\s+(?:messaged|texted))\b", re.I)
SEND = re.compile(r"\b(?:send|reply|message\s+to|write\s+to|tell\s+\w+\s+that)\b", re.I)
URL = "https://web.whatsapp.com"
HINT = ("You are in WhatsApp Web, already logged in. To message someone: click the search box, type their "
        "name, open their chat from the results, click the message box, type the message and press Enter. "
        "To read messages: open the chat and read the latest messages from the page. Never send anything the "
        "owner did not ask for.")


def match(text):
    return bool(TRIGGER.search(text))


def open_whatsapp(session, wait_s=40):
    """Open WhatsApp Web and wait for the chat list (a fresh tab shows a progress bar for a few seconds)."""
    browser.use_server("whatsapp", session)
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


def run(text, session):
    p = session.get("pending")
    if p and p.get("skill") == "browser":  # yes/no to a send the browser engine asked about
        return browser.run(text, session)
    try:
        snap, ok = open_whatsapp(session)
    except RuntimeError as exc:
        return {"reply": str(exc), "sources": []}
    if not ok:
        return {"reply": "WhatsApp Web isn't logged in in Chrome Profile 9. Open it there, scan the QR code once, "
                         "and ask again.", "sources": []}
    if UNREAD.search(text) and not SEND.search(text):
        rows, total = unread(snap)
        trace(session, "read", f"{total} unread chats")
        if not total:
            return {"reply": "No unread chats on WhatsApp.", "sources": []}
        lines = [f"- {n}" + (f" ({c})" if c else " (marked unread)") for n, c in rows]
        more = f"\n{total - len(rows)} more further down the list." if total > len(rows) else ""
        return {"reply": f"{total} unread chat{'s' if total != 1 else ''} on WhatsApp:\n" + "\n".join(lines) + more,
                "sources": []}
    return browser.run(text, session, server="whatsapp", hint=HINT, start_snapshot=snap)


if __name__ == "__main__":
    assert match("check my whatsap") and match("watsapp ali") and not match("what app should I use")
    sample = ('- grid "Chat list" [ref=e191]:\n  - row [ref=e192]:\n    - gridcell "Ali 12:54 hi Pinned chat" [ref=e193]:\n'
              '      - generic "Ali" [ref=e206]\n  - row [ref=e280]:\n    - gridcell "2 unread messages Sara 2:01 pm ok" '
              '[ref=e281]:\n      - generic "Sara" [ref=e290]\n  - row [ref=e300]:\n    - gridcell "Unread Class group 1:27 pm" '
              '[ref=e301]:\n      - generic "Class group" [ref=e310]\n- button "Unread 2" [ref=e135]')
    assert unread(sample) == ([("Sara", 2), ("Class group", None)], 2), unread(sample)
    assert UNREAD.search("which chats are unread") and SEND.search("send hi to ali on whatsapp")
    print("[self-check] whatsapp OK")
