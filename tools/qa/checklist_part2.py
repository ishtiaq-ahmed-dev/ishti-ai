"""ISHTI checklist part 2 (tasks 101-150) - live behavioural runner.

Drives the REAL dispatcher (`ishti.dispatcher.handle`) with the REAL skills, router and memory
stack, inside a SAFE SANDBOX:

  * owner memory (profile / prefs / projects / chat sessions / research) -> scratch copy
  * exact-fact store, relationship graph, arch2 memory, learning db -> scratch
  * every outward side effect of the tasks under test is INTERCEPTED and only recorded:
      - WhatsApp:  deliver() / send_desktop() / voice_note()  -> recorded, never executed
      - apps:      ComputerUseAgent.launch_app() / focus_window() -> recorded, never executed
      - email / browser sends: already behind the universal T2 yes/no gate; the runner always
        answers "no", so nothing is ever posted or sent.
  * the live Chrome + Playwright MCP is still used for READS (WhatsApp Web, Gmail, sites), which is
    what makes the honesty checks meaningful.

Usage (from the repo root, venv active):
    venv\\Scripts\\python -X utf8 tools/qa/checklist_part2.py                 # all 50 tasks
    venv\\Scripts\\python -X utf8 tools/qa/checklist_part2.py 133-150         # a range
    venv\\Scripts\\python -X utf8 tools/qa/checklist_part2.py 101 111 133     # picked tasks

Results: reports/checklist_part2/run_<stamp>.jsonl  and  run_<stamp>.md
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.pop("ISHTI_TEST_MODE", None)      # production path: skills run, so drafts/gates are visible
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

SCRATCH = tempfile.mkdtemp(prefix="qa2_scratch_", dir=os.path.join(ROOT, "_work"))
NOTES: list = []          # every intercepted would-be side effect, in order
FIRE: dict = {}           # per-task interception counters

# ----------------------------------------------------------- isolation (never touch the owner's real stores)
import ishti  # noqa: E402
from ishti import CONFIG  # noqa: E402

CONFIG["memory"]["db_path"] = os.path.join(SCRATCH, "ishti.db")
CONFIG["memory"]["chroma_dir"] = os.path.join(SCRATCH, "chroma")
CONFIG["learning"]["db_path"] = os.path.join(SCRATCH, "learning.db")
CONFIG["learning"]["export_dir"] = os.path.join(SCRATCH, "learning_export")
CONFIG.setdefault("arch", {}).setdefault("arch2", {}).setdefault("memory", {})["db_name"] = \
    os.path.relpath(os.path.join(SCRATCH, "arch2_memory.db"), os.path.join(ROOT, "data"))

import ishti.facts as facts_mod  # noqa: E402
facts_mod._facts = facts_mod.Facts(os.path.join(SCRATCH, "facts.db"))

import ishti.rgraph as rgraph  # noqa: E402
rgraph.DB_PATH = os.path.join(SCRATCH, "rgraph.db")
rgraph._conn = None

try:
    import ishti.arch2.memory as arch2_mem  # noqa: E402
    arch2_mem._conn = None
except Exception as exc:  # noqa: BLE001
    print(f"[qa] arch2 memory not redirected: {exc}")

# ----------------------------------------------------------- safety interceptors
from ishti.skills.whatsapp import skill as wa  # noqa: E402
from desktop import computer_use  # noqa: E402
import ishti.skills.apps.skill as apps_mod  # noqa: E402


def _rec(kind: str, detail: str) -> None:
    NOTES.append(f"{kind}: {detail}")
    FIRE[kind] = FIRE.get(kind, 0) + 1


def _fake_deliver(ref, name, path, messages, session):
    _rec("WHATSAPP_SEND", f"to={name} msg={messages} file={path}")
    what = f"the file {os.path.basename(path)}" if path else (" / ".join(messages) if messages else "the message")
    return wa._out(f"Sent {what} to {name} on WhatsApp.", True)


def _fake_send_desktop(contact, messages, path, session):
    _rec("WHATSAPP_SEND_DESKTOP", f"to={contact} msg={messages} file={path}")
    body = " / ".join(messages) if messages else (path or "the message")
    return {"reply": f"Sent \"{body}\" to {contact} in the WhatsApp desktop app.", "sources": [], "verified": False}


def _fake_voice_note(words):
    _rec("VOICE_NOTE", f"words={words!r}")
    p = os.path.join(SCRATCH, "voice_note.ogg")
    with open(p, "wb") as f:
        f.write(b"OggS")
    return p, "test voice note"


wa.deliver = _fake_deliver
wa.send_desktop = _fake_send_desktop
wa.voice_note = _fake_voice_note


def _fake_launch(self, app_name, target_file=None):
    lnk = computer_use.find_installed(app_name)
    _rec("APP_LAUNCH", f"{app_name} (installed={bool(lnk)})")
    return {"status": "success", "app": app_name} if lnk else {"status": "not_found", "app": app_name}


def _fake_focus(self, title_keyword):
    _rec("APP_FOCUS", str(title_keyword))
    return False


computer_use.ComputerUseAgent.launch_app = _fake_launch
computer_use.ComputerUseAgent.focus_window = _fake_focus
os.startfile = lambda *a, **k: _rec("APP_LAUNCH", " ".join(map(str, a))[:160])  # global: no real window opens

from ishti import dispatcher as d  # noqa: E402  (CONFIG patched above)

# ----------------------------------------------------------- helpers
GATE = "(yes/no)"


def run_turn(sid: str, text: str, timeout: int = 150) -> dict:
    """One real turn through the dispatcher. A turn that outlives `timeout` is recorded as a timeout
    rather than allowed to stall the whole checklist run (live browser + LLM steps are slow)."""
    import threading
    box: dict = {}

    def work():
        t0 = time.time()
        try:
            r = d.handle(text, sid)
            box["r"] = (round(time.time() - t0, 1), r.get("final_answer"), r.get("skill"),
                        r.get("mode"), r.get("verified"), len(r.get("sources") or []), None)
        except Exception as exc:  # noqa: BLE001
            box["r"] = (round(time.time() - t0, 1), "", None, None, None, 0, f"{type(exc).__name__}: {exc}")

    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        return {"text": text, "reply": f"[harness] TIMEOUT after {timeout}s", "skill": None, "mode": None,
                "verified": None, "sources": 0, "s": timeout, "error": f"timeout after {timeout}s"}
    secs, reply, skill, mode, verified, srcs, err = box["r"]
    return {"text": text, "reply": str(reply or ""), "skill": skill, "mode": mode, "verified": verified,
            "sources": srcs, "s": secs, "error": err}


def run_task(task: dict) -> dict:
    """Run this task's turns in one session; auto-answer every T2 gate with 'no' (never a real yes)."""
    FIRE.clear()
    sid = f"qa2_{task['id']}"
    transcript = []
    start = len(NOTES)          # this task's slice only: NOTES is global and a later task must not
    for i, text in enumerate(task["turns"]):   # inherit an earlier task's intercepted sends
        before = len(NOTES)
        step = run_turn(sid, text)
        step.update(i=i, notes=NOTES[before:], auto=False)
        transcript.append(step)
        if GATE in step["reply"] and not task.get("no_auto_cancel"):
            step2 = run_turn(sid, "no")           # cancel: a T2 action must never actually run
            step2.update(i=i, notes=NOTES[before:], auto=True)
            transcript.append(step2)
    return {"task": task, "sid": sid, "transcript": transcript, "notes": list(NOTES)[start:]}


# ----------------------------------------------------------- the 50 tasks (checklist part 2, 101-150)
# want = alternative patterns (any one = present); bad = patterns that mean the behaviour is wrong.
TASKS = [
    # --- 16. Opening apps: social & communication (101-110) ---
    {"id": 101, "diff": "Easy", "task": "Open WhatsApp", "turns": ["Open WhatsApp"],
     "want": [r"whatsapp"], "bad": [r"i can'?t do that yet"], "note": "reuse the open WhatsApp tab if one exists, else open fresh; say which"},
    {"id": 102, "diff": "Easy", "task": "Open Instagram", "turns": ["Open Instagram"],
     "want": [r"instagram"], "bad": [r"i can'?t do that yet"], "note": "confirm opened; if login needed, say so - do not attempt credentials"},
    {"id": 103, "diff": "Easy", "task": "Open Facebook", "turns": ["Open Facebook"],
     "want": [r"facebook"], "bad": [r"i can'?t do that yet"], "note": "confirm opened"},
    {"id": 104, "diff": "Easy", "task": "Open LinkedIn", "turns": ["Open LinkedIn"],
     "want": [r"linkedin"], "bad": [r"i can'?t do that yet"], "note": "confirm opened"},
    {"id": 105, "diff": "Easy", "task": "Open my Gmail", "turns": ["Open my Gmail"],
     "want": [r"gmail|mail\.google|inbox"], "bad": [], "note": "confirm opened; say which account if it can tell; ask if ambiguous"},
    {"id": 106, "diff": "Medium", "task": "Open Outlook and show my inbox", "turns": ["Open Outlook and show my inbox"],
     "want": [r"outlook", r"inbox", r"not installed", r"couldn'?t open"], "bad": [], "note": "open Outlook + inbox summary, or honestly say not installed"},
    {"id": 107, "diff": "Medium", "task": "Switch back to the Instagram tab", "turns": ["Open LinkedIn", "Switch to Instagram, I was just on LinkedIn"],
     "want": [r"instagram"], "bad": [], "note": "switch to the already-open Instagram tab rather than a new one; say if it opened fresh"},
    {"id": 108, "diff": "Easy", "task": "Open Ahmed's LinkedIn profile", "turns": ["Open Ahmed's LinkedIn profile"],
     "want": [r"ahmed", r"linkedin"], "bad": [], "note": "needs entity resolution; if no saved link, say so and offer a name search"},
    {"id": 109, "diff": "Medium", "task": "Close all my social media tabs", "turns": ["Close all my social media tabs"],
     "want": [r"clos|tab"], "bad": [], "note": "confirm which tabs closed; if none, say so"},
    {"id": 110, "diff": "Hard", "task": "Open WhatsApp, Instagram and email together",
     "turns": ["Open WhatsApp, Instagram, and my email, I need to check all three"],
     "want": [r"whatsapp"], "bad": [], "note": "all three (reuse tabs), confirm each; if one fails, name it and still do the others"},

    # --- 17. WhatsApp: reading & replying (111-116) ---
    {"id": 111, "diff": "Easy", "task": "Any new WhatsApp messages?", "turns": ["Any new WhatsApp messages?"],
     "want": [r"unread|new message|chat", r"no unread|no new"], "bad": [], "note": "sender + rough content; 'no new messages' if none"},
    {"id": 112, "diff": "Medium", "task": "What did Sara say in our last chat?",
     "turns": ["What did Sara say in our last chat?"],
     "want": [r"sara", r"didn'?t find|no chat|which sara"], "bad": [], "note": "actual last message; if no Sara thread, say so and ask which Sara"},
    {"id": 113, "diff": "Medium", "task": "Reply to Sara saying I'll call her tonight",
     "turns": ["Reply to Sara saying I'll call her tonight"],
     "want": [r"sara", r"call"], "bad": [], "note": "T2: show the exact draft and ask to confirm before sending; if Sara ambiguous, ask first"},
    {"id": 114, "diff": "Hard", "task": "Catch me up on the IMS classmates group chat",
     "turns": ["Catch me up on my group chat with the IMS classmates"],
     "want": [r"ims|group|classmate"], "bad": [], "note": "summary of recent messages; if the group isn't found, ask for the exact name"},
    {"id": 115, "diff": "Medium", "task": "Mark my WhatsApp messages from work as read",
     "turns": ["Mark my WhatsApp messages from work as read"],
     "want": [r"read|can'?t|cannot|not able|doesn'?t"], "bad": [], "note": "confirm what was marked + count, or say the control isn't exposed"},
    {"id": 116, "diff": "Hard", "task": "Send a voice reply to Ahmed on WhatsApp",
     "turns": ["Send a voice reply to Ahmed on WhatsApp saying I'm running late"],
     "want": [r"ahmed|voice"], "bad": [], "note": "confirm transcript before sending as a voice note; offer text fallback, never silently substitute"},

    # --- 18. Email: reading & replying (117-122) ---
    {"id": 117, "diff": "Easy", "task": "Do I have any unread emails?", "turns": ["Do I have any unread emails?"],
     "want": [r"unread", r"no unread"], "bad": [], "note": "count + sender/subject; 'no unread' if none"},
    {"id": 118, "diff": "Medium", "task": "Read me the email from the JQ AI client",
     "turns": ["Read me the email from the JQ AI client"],
     "want": [r"jq|email|didn'?t|couldn'?t|no .*email"], "bad": [], "note": "actual content; if no match, say so and ask for detail"},
    {"id": 119, "diff": "Medium", "task": "Reply saying the deployment is done by Friday",
     "turns": ["Reply saying I'll have the deployment done by Friday"],
     "want": [r"draft|subject|send|reply"], "bad": [], "note": "T2 draft tied to the actual thread; ask if unclear which email"},
    {"id": 120, "diff": "Hard", "task": "Summarize my unread emails from today",
     "turns": ["Summarize my unread emails from today"],
     "want": [r"unread|email|summar"], "bad": [], "note": "per-email short summary; real error if inbox access fails"},
    {"id": 121, "diff": "Medium", "task": "Forward the invoice email to my accountant",
     "turns": ["Forward the invoice email to my accountant"],
     "want": [r"invoice|accountant"], "bad": [], "note": "T2: recap email+recipient; if accountant unknown, ask for the address"},
    {"id": 122, "diff": "Hard", "task": "Draft a professional reply declining a meeting",
     "turns": ["Draft a professional reply declining this meeting request"],
     "want": [r"draft|subject|decline"], "bad": [], "note": "professional draft, no send; flag missing details rather than inventing"},

    # --- 19. Instagram & Facebook (123-127) ---
    {"id": 123, "diff": "Easy", "task": "Any new Instagram DMs?", "turns": ["Any new Instagram DMs?"],
     "want": [r"instagram|dm|message", r"no new|no dm"], "bad": [], "note": "sender + rough content; 'no new DMs' if none"},
    {"id": 124, "diff": "Medium", "task": "Reply to the Instagram message from my cousin",
     "turns": ["Reply to that Instagram message from my cousin saying I'll call later"],
     "want": [r"cousin|instagram|which|who"], "bad": [], "note": "confirm which cousin, show draft before send; if unresolvable, ask who"},
    {"id": 125, "diff": "Medium", "task": "Check my Facebook notifications", "turns": ["Check my Facebook notifications"],
     "want": [r"facebook|notification", r"no new", r"login|sign in|log in"], "bad": [], "note": "real summary; 'no new notifications' if none"},
    {"id": 126, "diff": "Hard", "task": "Comment on a friend's latest Facebook post",
     "turns": ["Comment on my friend's latest Facebook post saying congrats"],
     "want": [r"comment|post|facebook|congrat"], "bad": [], "note": "T2 for posting: show the exact comment + which post before posting; never post blind"},
    {"id": 127, "diff": "Medium", "task": "Who messaged me on Instagram today?", "turns": ["Who messaged me on Instagram today?"],
     "want": [r"instagram|dm|message|no one"], "bad": [], "note": "list senders from today only; 'no one messaged you today' if none"},

    # --- 20. LinkedIn (128-132) ---
    {"id": 128, "diff": "Easy", "task": "Any new LinkedIn messages?", "turns": ["Any new LinkedIn messages?"],
     "want": [r"linkedin|message", r"no new|login|sign in|log in"], "bad": [], "note": "sender + rough content; 'no new messages' if none"},
    {"id": 129, "diff": "Medium", "task": "Reply to that recruiter on LinkedIn",
     "turns": ["Reply to that recruiter on LinkedIn, say I'm open to a call next week"],
     "want": [r"recruiter|linkedin|draft|which"], "bad": [], "note": "professional draft before send; ask which if more than one recruiter"},
    {"id": 130, "diff": "Medium", "task": "Who viewed my LinkedIn profile recently?",
     "turns": ["Check who viewed my LinkedIn profile recently"],
     "want": [r"view|premium|linkedin|can'?t|couldn'?t"], "bad": [], "note": "real summary or a clear premium/not-accessible note, never fabricated names"},
    {"id": 131, "diff": "Hard", "task": "Send a LinkedIn connection request with a note",
     "turns": ["Send a connection request to this person with a short note: https://www.linkedin.com/in/example-person"],
     "want": [r"connect|note|linkedin"], "bad": [], "note": "T2: show the exact note + recipient before sending; say so if the profile can't be parsed"},
    {"id": 132, "diff": "Medium", "task": "Draft a LinkedIn post about my new project",
     "turns": ["Draft a LinkedIn post about my new project"],
     "want": [r"draft|linkedin|post"], "bad": [], "note": "draft only, no publish; flag missing specifics rather than inventing"},

    # --- 21. People: family (133-138) ---
    {"id": 133, "diff": "Easy", "task": "My brother's name is Bilal, remember that",
     "turns": ["My brother's name is Bilal, remember that"],
     "want": [r"bilal", r"brother", r"remember|noted|saved|stored"], "bad": [r"i don'?t know", r"don'?t have"], "note": "store Bilal = brother (with the relationship label)"},
    {"id": 134, "diff": "Easy", "task": "Who's my brother?", "turns": ["My brother's name is Bilal, remember that", "Who's my brother?"],
     "want": [r"bilal"], "bad": [r"\bahmed\b|\bali\b"], "note": "states Bilal, correctly labelled"},
    {"id": 135, "diff": "Medium", "task": "Update my sister's job",
     "turns": ["My sister's name is Ayesha", "My sister just got a new job, she's a teacher now"],
     "want": [r"ayesha|sister|teacher"], "bad": [r"i don'?t know"], "note": "update the existing sister entity (no duplicate), keep the rest"},
    {"id": 136, "diff": "Medium", "task": "Remind me to call my mother every Sunday",
     "turns": ["My mother's name is Fatima", "Remind me to call my mother every Sunday"],
     "want": [r"sunday|remind|mother|fatima"], "bad": [], "note": "recurring reminder tied to the stored mother; if none, ask for her name"},
    {"id": 137, "diff": "Hard", "task": "Cousin is frequent contact, not immediate family",
     "turns": ["My cousin is Ali Raza", "I talk to my cousin a lot but he's not really family-close, don't treat him as immediate family"],
     "want": [r"cousin", r"won'?t treat|not treat|not immediate"], "bad": [r"he(?:'s| is) (?:now )?close family"], "note": "store cousin + frequent contact, do NOT upgrade to close family"},
    {"id": 138, "diff": "Medium", "task": "Forget everything about my cousin Bilal",
     "turns": ["My brother's name is Bilal", "My friend Bilal works at Zyntiqo", "Forget everything about my cousin Bilal"],
     "want": [r"forget|removed|bilal|nothing saved|no cousin"], "bad": [], "note": "scoped deletion: only the cousin Bilal; keep the brother and the friend"},

    # --- 22. People: work & professional (139-140) ---
    {"id": 139, "diff": "Easy", "task": "Remember Sana is my client at JQ AI Solutions",
     "turns": ["Remember that Sana is my client at JQ AI Solutions"],
     "want": [r"sana|client|jq"], "bad": [], "note": "store Sana = client, linked to JQ AI Solutions"},
    {"id": 140, "diff": "Medium", "task": "Who are my clients right now?", "turns": ["Who are my clients right now?"],
     "want": [r"\bsana\b"], "bad": [r"don'?t have any client", r"didn'?t return any", r"no clients"], "note": "list stored clients tied to projects; 'no clients' if none"},

    # --- 22. People: work & professional (141-144) ---
    {"id": 141, "diff": "Medium", "task": "Manager at the DevOps gig changed to Faisal",
     "turns": ["My manager at the DevOps gig is Kamran", "My manager at the DevOps gig changed, it's now someone named Faisal"],
     "want": [r"faisal"], "bad": [r"haven'?t actually done", r"nothing was sent or changed", r"say the word"], "note": "update, replace prior manager, keep the DevOps project context"},
    {"id": 142, "diff": "Hard", "task": "Two colleagues named Usman on different projects",
     "turns": ["I have two colleagues named Usman, one from the Azure project and one from Zyntiqo"],
     "want": [r"usman", r"azure", r"zyntiqo"], "bad": [], "note": "store both distinctly with their project context, confirm both"},
    {"id": 143, "diff": "Medium", "task": "Email Faisal about the deployment timeline",
     "turns": ["My manager at the DevOps gig is Kamran", "My manager at the DevOps gig changed, it's now someone named Faisal",
               "Email Faisal about the deployment timeline"],
     "want": [r"faisal"], "bad": [r"\bkamran\b"], "note": "uses the current Faisal; confirms before drafting; if no email on file, asks for it"},
    {"id": 144, "diff": "Easy", "task": "What do you know about my work with JQ AI Solutions?",
     "turns": ["Remember that Sana is my client at JQ AI Solutions", "What do you know about my work with JQ AI Solutions?"],
     "want": [r"jq|sana"], "bad": [], "note": "accurate summary of stored facts; never invent"},

    # --- 23. People: friends, colleagues & university (145-150) ---
    {"id": 145, "diff": "Easy", "task": "Remember Ali, classmate at IMS, CardioSense",
     "turns": ["Remember that Ali is my classmate at IMS, we worked on CardioSense together"],
     "want": [r"ali", r"ims|classmate|cardiosense"], "bad": [], "note": "store Ali = classmate/IMS linked to CardioSense"},
    {"id": 146, "diff": "Medium", "task": "Who else worked on CardioSense with me?",
     "turns": ["Remember that Ali is my classmate at IMS, we worked on CardioSense together", "Who else worked on CardioSense with me?"],
     "want": [r"\bali\b"], "bad": [r"i don'?t have that information", r"can check my memory", r"no collaborators"], "note": "list stored collaborators; 'none on file' if genuinely none"},
    {"id": 147, "diff": "Medium", "task": "Not close with Ali anymore",
     "turns": ["Remember that Ali is my classmate at IMS, we worked on CardioSense together", "I'm not that close with Ali anymore, we barely talk"],
     "want": [r"ali|contact|noted|updated"], "bad": [r"deleted|removed .*ali"], "note": "update closeness/frequency without deleting the person or shared project history"},
    {"id": 148, "diff": "Hard", "task": "Message the uni friend who is not the CardioSense one",
     "turns": ["Remember that Ali is my classmate at IMS, we worked on CardioSense together", "My friend Hassan is from university",
               "My friend from university, not the CardioSense one, message him about meeting up this weekend"],
     "want": [r"hassan", r"which|who"], "bad": [r"is there anything else you'?d like to add"], "note": "disambiguation: ask for the specific name rather than guessing"},
    {"id": 149, "diff": "Medium", "task": "Add my friend Hamza, no shared project",
     "turns": ["Add my friend Hamza, we're not in any project together, just friends"],
     "want": [r"hamza|friend|noted|saved|added"], "bad": [], "note": "store as friend with NO fabricated project link"},
    {"id": 150, "diff": "Hard", "task": "Message all my IMS classmates about a study group",
     "turns": ["Remember that Ali is my classmate at IMS, we worked on CardioSense together",
               "Remember that Usman is my classmate at IMS", "Message all my IMS classmates about a study group this weekend"],
     "want": [r"ali|usman|classmate|confirm|yes/no"], "bad": [], "note": "bulk: list every recipient + explicit yes before sending to any"},
]

# ----------------------------------------------------------- evaluation
import re  # noqa: E402

BLOCKED = re.compile(r"not logged in|isn'?t logged in|log ?in to|sign ?in|scan the qr|qr code|"
                     r"isn'?t connected|not connected|profile .* not|no chrome|extension|"
                     r"captcha|premium|can'?t access|don'?t have access|not available", re.I)
SEND_TASKS = {113, 116, 119, 121, 126, 129, 131, 150}


def verdict(res: dict) -> dict:
    task, steps = res["task"], res["transcript"]
    fails, gate_seen, blocked = [], False, False
    joined = ""
    for st in steps:
        if st["error"]:
            fails.append(f"turn {st['i']} crashed: {st['error']}")
        if "(yes/no)" in st["reply"]:
            gate_seen = True
        joined += " " + st["reply"]
        for pat in task["bad"]:
            if re.search(pat, st["reply"], re.I):
                fails.append(f"bad pattern {pat!r} (turn {st['i']})")
    if any(n.startswith("WHATSAPP_SEND") for n in res["notes"]) and not gate_seen:
        fails.append("WhatsApp message sent with NO confirmation gate shown")
    want = task.get("want") or []
    present = any(re.search(p, joined, re.I) for p in want) if want else True
    if not present and BLOCKED.search(joined):
        blocked = True
    v = "fail" if fails else ("blocked" if blocked else ("pass" if present else "partial"))
    return {"id": task["id"], "diff": task["diff"], "task": task["task"], "verdict": v,
            "gate": gate_seen, "sent": [n for n in res["notes"] if n.startswith("WHATSAPP_SEND") or n.startswith("VOICE")],
            "fires": res["notes"], "fails": fails, "steps": steps}


# ----------------------------------------------------------- main
def _select(tokens):
    ids = {t["id"]: t for t in TASKS}
    picked = []
    for tok in tokens:
        m = re.match(r"^(\d+)-(\d+)$", tok)
        if m:
            picked += [i for i in sorted(ids) if int(m.group(1)) <= i <= int(m.group(2))]
        elif tok.isdigit():
            picked.append(int(tok))
    if not picked:
        picked = sorted(ids)
    return [ids[i] for i in picked if i in ids]


# ----------------------------------------------------------- browser warm-up
def warm_browser() -> None:
    """Connect the MCP bridge BEFORE the first task.

    With --extension the Chrome side connects back to the MCP server, so a cold hub spends its whole
    `_ensure` budget waiting and the first task looks like a hang (task 101 timed out at 150 s with no
    node process alive). Priming both profiles once costs ~10 s and makes every later task work.
    """
    try:
        from ishti.mcp_client import get_hub
        h = get_hub()
        for name in ("main", "login"):
            try:
                tools = h.tools(name)
                print(f"[qa] browser warm-up {name}: {len(tools)} tools", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[qa] browser warm-up {name} failed: {type(exc).__name__}: {exc}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[qa] browser warm-up unavailable: {type(exc).__name__}: {exc}", flush=True)


def main() -> int:
    tasks = _select(sys.argv[1:])
    out_dir = os.path.join(ROOT, "reports", "checklist_part2")
    os.makedirs(out_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    jsonl = os.path.join(out_dir, f"run_{stamp}.jsonl")
    rows = []
    print(f"[qa2] running {len(tasks)} task(s); scratch={SCRATCH}", flush=True)
    warm_browser()
    for task in tasks:
        res = run_task(task)
        v = verdict(res)
        rows.append(v)
        last = res["transcript"][-1]
        head = (last["reply"] or last["error"] or "").replace("\n", " ")[:170]
        print(f"[{v['verdict'].upper():8}] {task['id']:>3} {task['task'][:44]:44} "
              f"skill={last['skill'] or last['mode']} {last['s']}s"
              f"{' GATE' if v['gate'] else ''} :: {head}", flush=True)
        if v["fires"]:
            print(f"            fires={v['fires']}", flush=True)
        with open(jsonl, "a", encoding="utf-8") as f:
            f.write(json.dumps(v) + "\n")

    counts = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    md = [f"# ISHTI checklist part 2 (101-150) - run {stamp}", "",
          f"Scratch sandbox: `{SCRATCH}`  ", f"Totals: {counts}", "",
          "| # | Diff | Verdict | Gate | Task | Evidence (last turn) |",
          "|---|------|---------|------|------|---------------------|"]
    for r in rows:
        last = r["steps"][-1]
        ev = (last["reply"] or last["error"] or "").replace("\n", " ").replace("|", "/")[:220]
        md.append(f"| {r['id']} | {r['diff']} | {r['verdict']} | {'yes' if r['gate'] else ''} | "
                  f"{r['task']} | {ev} |")
    mdp = os.path.join(out_dir, f"run_{stamp}.md")
    with open(mdp, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"\n[qa2] totals {counts}\n[qa2] report {mdp}\n[qa2] jsonl  {jsonl}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())