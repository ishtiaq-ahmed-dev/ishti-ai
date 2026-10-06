"""The 24-hour chat digest (Section 4) - T0, READ-ONLY.

"open agent and check the last 24 hours of all chats, groups and communities and give me a report"

What this skill does: reads the messages ISHTI already saw (local store), falls back to the read-only DOM
read of the owner's open WhatsApp tab or an official export, analyses them locally and prints Tables 1-4 of
the spec. What it never does: send, reply, react, mark as read, delete, upload - those stay T2 actions that
need the owner's explicit yes. Draft replies are drafts in a table, nothing more.

Data sources, in the spec's order (log which one was used - it is printed in the report):
  1. the local message store (data/chat_digest.db)  -> what ISHTI already saw
  2. the read-only DOM read of the open WhatsApp tab (ishti/skills/whatsapp.read_chat_messages)
  3. an official WhatsApp export (.txt/.zip) the owner hands over -> ingest_export
  4. an unofficial Baileys reader - NOT IMPLEMENTED, default off (ban risk + npm supply chain)
"""
import os
import re

from ishti import ROOT, audit
from ishti.skills.chat_digest import analyze, export, report
from ishti.skills.chat_digest import store
from ishti.skills.chat_digest import window as win_mod
from ishti.test_mode import scratch_path

DIGEST = re.compile(
    r"\b(?:chat|whatsapp)\s+digest\b"
    r"|\bdigest\b[^.?!]{0,30}\b(?:chat|whatsapp)s?\b"
    r"|\b(?:24|twenty[\s-]?four)\s*[\s-]?(?:hour|hr)s?\b[^.?!]{0,40}\b(?:chat|whatsapp|group|communit)"
    r"|\b(?:chat|whatsapp|group|communit)s?\b[^.?!]{0,30}\blast\s+(?:24|twenty[\s-]?four)\s*[\s-]?(?:hour|hr)s?"
    r"|\b(?:read|check|scan|go through|review)\b[^.?!]{0,30}\ball\b[^.?!]{0,30}\b(?:my\s+)?(?:chats?|groups?|communities)\b"
    r"|\bwhat did i miss\b[^.?!]{0,20}\b(?:chat|whatsapp|group)?"
    r"|\b(?:make|give|save|export)\b[^.?!]{0,20}\b(?:pdf|docx|word|markdown|md)\b[^.?!]{0,20}\b(?:of|for|from)?\s*(?:the\s+)?(?:chat\s+)?digest\b",
    re.I)
HOURS = re.compile(r"\b(?:last|past)\s+(\d{1,2})\s*(?:hours?|hrs?)\b", re.I)
WANT_PDF = re.compile(r"\b(?:pdf|print)\b", re.I)
WANT_DOCX = re.compile(r"\b(?:docx|word|doc\b)", re.I)
WANT_MD = re.compile(r"\b(?:markdown|md file|\.md)\b", re.I)
DRAFTS = re.compile(r"\b(?:draft|suggest)\b[^.?!]{0,24}\b(?:repl(?:y|ies)?|answer|response|message)s?\b"
                    r"|\bwhat should i (?:reply|say)\b", re.I)
INGEST = re.compile(r"\b(?:import|read from|use)\b[^.?!]{0,24}\bexport\b|\b(?:whatsapp|wa)\s+export\b", re.I)
LAST_MD = scratch_path(os.path.join(ROOT, "data", "chat_digest_last.md"))
NOTIF_Q = re.compile(r"\b(?:notifications?|alerts?)\b", re.I)
CHAT_TOPIC = re.compile(r"\b(?:chats?|whatsapp|groups?|communit\w*|messages?|dms?)\b", re.I)


def match(text):
    """Deliberately specific: the plain 'check my whatsapp' request still belongs to the whatsapp skill
    (priority 16 runs first), and nothing here fires on the labelled requests of other skills -
    tools/check_skills.py replays those and fails on a matcher that steals one."""
    t = text or ""
    # "What notifications did I miss?" / "tell me the alert" are the notifications bell, not the chats
    # (the digest's "what did i miss" branch used to steal them - checklist #195).
    if NOTIF_Q.search(t) and not CHAT_TOPIC.search(t):
        return False
    return bool(DIGEST.search(t))


def _hours(text, cfg):
    m = HOURS.search(text or "")
    return float(m.group(1)) if m else float(cfg.get("hours", 24))


def ingest_sources(text, session=None):
    """Fill the store from whatever the owner pointed at. Returns a note for the report. Read-only towards
    WhatsApp: an export file is read, the already-open tab is only snapshotted."""
    notes = []
    for path in re.findall(r"[\w\\/. \-]{2,}?\.(?:txt|zip)", text or ""):
        p = path.strip()
        if os.path.isfile(p):
            try:
                got = store.ingest_export(p)
                notes.append(f"imported {got['new_messages']} message(s) from {os.path.basename(p)}")
            except Exception as exc:
                notes.append(f"could not read {os.path.basename(p)} ({type(exc).__name__})")
    if INGEST.search(text or ""):
        notes.append("to add WhatsApp history without touching the phone: WhatsApp > chat > Export chat > "
                     "without media, then tell me the file path (source 3 of the spec).")
    return notes


def drafts_for(result):
    """Neutral draft templates for the action items someone else sent (4.5's optional last table).
    They invent no facts and are never sent: sending is T2."""
    out = []
    for a in result["actions"][:5]:
        if a.get("mine"):
            continue
        due = f" I'll get it to you by {a['deadline'].split(' ')[0]}." if a.get("deadline") else \
              " I'll get back to you shortly."
        out.append({"chat": a["chat"], "why": f"action item from {a['from']} [{', '.join(a['src'])}]",
                    "text": f"Hi {a['from']}, noted - regarding \"{a['text'][:70]}\"..{due}",
                    "status": "template only - edit it; sending needs your yes (T2)"})
    return out

def run(text, session):
    """The digest. Read-only: no send/reply/react/read/delete calls exist on this path at all."""
    cfg = win_mod.config()
    store.conn()                                       # create the schema if this is a fresh install
    notes = ingest_sources(text, session)
    win = win_mod.window(hours=_hours(text, cfg))
    result = analyze.analyze(win, cfg=cfg, use_llm=bool(cfg.get("use_llm")), session=session)
    md = report.render(result, cfg=cfg, drafts=drafts_for(result) if DRAFTS.search(text or "") else None)
    problems = report.table_problems(md)
    with open(LAST_MD, "w", encoding="utf-8") as f:    # so "make a pdf" can follow in a later turn
        f.write(md)
    audit.record("chat digest run (read-only, analysis only)", f"{result['totals']}", tier="T0",
                 request=(text or "")[:200])
    if session is not None and hasattr(session, "__setitem__"):
        session["chat_digest"] = {"window": win["label"], "totals": result["totals"], "md": md}
    made = []
    if text and (WANT_PDF.search(text) or WANT_DOCX.search(text) or WANT_MD.search(text)) \
            and re.search(r"\b(?:make|give|save|export|print|send me)\b", text, re.I):
        if WANT_PDF.search(text):
            try:
                path, method = export.save_pdf(md)
                made.append(f"PDF: {path} ({method})")
            except Exception as exc:
                made.append(f"PDF not produced: {exc}")
        if WANT_DOCX.search(text):
            try:
                made.append(f"DOCX: {export.save_docx(md)}")
            except Exception as exc:
                made.append(f"DOCX not produced ({type(exc).__name__}: {exc})")
        if WANT_MD.search(text):
            made.append(f"Markdown: {export.save_md(md)}")
    reply = md
    if problems:
        reply += ("\n\n(Table check found issues: " + "; ".join(problems[:3])
                  + " - the report above is still readable, but tell me and I will fix the renderer.)")
    if notes:
        reply += "\n\n" + "\n".join(f"- {n}" for n in notes)
    if made:
        reply += "\n\n**Saved:** " + " · ".join(made)
    else:
        reply += ("\n\nSay **make a PDF of the digest** (or DOCX / markdown) and I will save it to `reports/` "
                  "and tell you the exact path.")
    return {"reply": reply, "sources": []}


if __name__ == "__main__":
    import datetime as dt
    import tempfile
    store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="cd_skill_"), "s.db")
    store.close()
    now = dt.datetime.now().timestamp()
    store.ingest_json({"chats": [
        {"chat_id": "c1", "name": "Ali Work", "type": "dm", "messages": [
            {"msg_id": "m1", "ts": now - 3600, "sender": "Ali", "text": "please send the report by tomorrow"},
            {"msg_id": "m2", "ts": now - 60, "sender": "Ali", "text": "سب ٹھیک ہے؟ 🎯"}]}]})
    assert match("open agent and check the last 24 hours of all chats, groups and communities and give me a report")
    assert match("chat digest please") and match("what did I miss")
    assert not match("What notifications did I miss in the last hour?")   # the bell, not the chats
    assert match("what did I miss in my group chats")                     # a chat question stays here
    assert not match("check my whatsap and let me know which chat is unread")     # whatsapp's request
    assert not match("give me my brief") and not match("what's the weather")
    assert not match("summarize https://example.com for me")
    out = run("check the last 24 hours of all chats and give me a report", {})
    assert out["reply"].startswith("# ISHTI 24-hour chat digest")
    assert "Table 4 — Coverage" in out["reply"] and "Ali Work" in out["reply"]
    assert "Unknown (not in my relationship data)" in out["reply"] and report.table_problems(out["reply"]) == []
    out2 = run("make a pdf of the digest", {})
    assert "**Saved:**" in out2["reply"] and ".pdf" in out2["reply"], out2["reply"][-400:]
    out3 = run("chat digest with draft replies", {})
    assert "Draft replies (draft only" in out3["reply"] and "T2" in out3["reply"]
    print("[self-check] chat_digest.skill OK - routing (specific, no overreach), report renders, PDF export "
          "saved, drafts are drafts only")

