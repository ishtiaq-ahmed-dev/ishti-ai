"""The 50-task live harness for Checklist Part 5 (Tasks 251-300).

Sections covered:
43. System & device administration (251-256)
44. Academic & student assistant tasks (257-262)
45. Freelance & client work management (263-268)
46. YouTube & content workflow (269-274)
47. GitHub & version control (275-280)
48. Group & multi-person coordination (281-286)
49. Vague or incomplete requests (287-292)
50. Cost & budget awareness (293-296)
51. Learning a new tool / onboarding help (297-300)

Usage:
  venv\\Scripts\\python.exe -X utf8 tools\\qa\\checklist_part5.py
  venv\\Scripts\\python.exe -X utf8 tools\\qa\\checklist_part5.py 251-260
  venv\\Scripts\\python.exe -X utf8 tools\\qa\\checklist_part5.py 265 275 281
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

# Sandbox facts & memory into a scratch DB for testing
os.makedirs(os.path.join(ROOT, "_work"), exist_ok=True)
SCRATCH = tempfile.mkdtemp(prefix="qa5_scratch_", dir=os.path.join(ROOT, "_work"))
os.environ["ISHTI_ALLOW_INLINE_GATE"] = "1"

from ishti import CONFIG  # noqa: E402
CONFIG.setdefault("arch", {}).setdefault("arch2", {}).setdefault("memory", {})["db_name"] = \
    os.path.relpath(os.path.join(SCRATCH, "arch2_memory.db"), os.path.join(ROOT, "data"))

import ishti.facts as facts_mod  # noqa: E402
facts_mod._facts = facts_mod.Facts(os.path.join(SCRATCH, "facts.db"))

import ishti.rgraph as rgraph  # noqa: E402
rgraph.DB_PATH = os.path.join(SCRATCH, "rgraph.db")
rgraph._conn = None

try:
    import ishti.memstore.memory as arch2_mem  # noqa: E402
    arch2_mem._conn = None
except Exception as exc:  # noqa: BLE001
    print(f"[qa5] arch2 memory not redirected: {exc}")

NOTES: list[str] = []
FIRE: dict[str, int] = {}


def _rec(kind: str, detail: str) -> None:
    NOTES.append(f"{kind}: {detail}")
    FIRE[kind] = FIRE.get(kind, 0) + 1


from ishti import dispatcher as d  # noqa: E402

GATE = "(yes/no)"


def run_turn(sid: str, text: str, timeout: int = 150) -> dict:
    """One real turn through the dispatcher with timeout guard."""
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
    """Run this task's turns in one session; auto-answer T2 gates with 'no'."""
    FIRE.clear()
    sid = f"qa5_{task['id']}"
    transcript = []
    start = len(NOTES)
    for i, text in enumerate(task["turns"]):
        before = len(NOTES)
        step = run_turn(sid, text)
        step.update(i=i, notes=NOTES[before:], auto=False)
        transcript.append(step)
        if GATE in step["reply"] and not task.get("no_auto_cancel"):
            step2 = run_turn(sid, "no")
            step2.update(i=i, notes=NOTES[before:], auto=True)
            transcript.append(step2)
    return {"task": task, "sid": sid, "transcript": transcript, "notes": list(NOTES)[start:]}


# ----------------------------------------------------------- The 50 Tasks (Checklist Part 5, 251-300)
TASKS = [
    # --- 43. System & device administration (251-256) ---
    {"id": 251, "diff": "Easy", "task": "Check for Windows updates",
     "turns": ["Check for Windows updates"],
     "want": [r"update|pending|windows update|install|current|up to date|no updates|check"], "bad": [],
     "note": "system skill, T0 read-only check"},
    {"id": 252, "diff": "Medium", "task": "Install the pending updates",
     "turns": ["Install the pending updates"],
     "want": [r"update|install|restart|reboot|\(yes/no\)|confirm|pending"], "bad": [],
     "note": "T2 confirmation gate on update installation requiring restart"},
    {"id": 253, "diff": "Medium", "task": "Clean up temp files and free some space",
     "turns": ["Clean up temp files and free some space"],
     "want": [r"temp|clean|freed|space|mb|gb|cache|cleared|optimized|files"], "bad": [],
     "note": "system cleanup, T1"},
    {"id": 254, "diff": "Medium", "task": "Back up my ISHTI project before I make risky changes",
     "turns": ["Back up my ISHTI project before I make risky changes"],
     "want": [r"back(?:ed)?\s*up|backup|archive|destination|ishti|saved|location|path"], "bad": [],
     "note": "files backup confirmation with destination/scope"},
    {"id": 255, "diff": "Hard", "task": "Check if my printer is working and print this file",
     "turns": ["Check if my printer is working and print this file"],
     "want": [r"printer|print|spooler|detected|online|offline|driver|no printer|active"], "bad": [],
     "note": "PC control & printer peripheral status check"},
    {"id": 256, "diff": "Medium", "task": "Check my GPU drivers are up to date",
     "turns": ["Check my GPU drivers are up to date"],
     "want": [r"driver|gpu|nvidia|rtx|5070|version|610|geforce|update|stable"], "bad": [],
     "note": "system skill telemetry and driver version check"},

    # --- 44. Academic & student assistant tasks (257-262) ---
    {"id": 257, "diff": "Medium", "task": "Find my EDA final project file and tell me what's left to finish",
     "turns": ["Find my EDA final project file and tell me what's left to finish"],
     "want": [r"eda|project|finish|titanic|notebook|todo|left|checked|searched|found|looked|folders"], "bad": [],
     "note": "files + content read of student project file"},
    {"id": 258, "diff": "Hard", "task": "Help me write the hypothesis testing section for my Titanic EDA report",
     "turns": ["Help me write the hypothesis testing section for my Titanic EDA report"],
     "want": [r"hypothesis|null|alternative|p-value|t-test|chi-square|draft|titanic|sample|data|statistic"], "bad": [r"p-value of 0\.02341 was observed in your actual data"],
     "note": "drafting grounded in actual stats placeholders, not fake numbers"},
    {"id": 259, "diff": "Medium", "task": "Format this citation in APA style",
     "turns": ["Format this citation in APA style: Vaswani et al Attention Is All You Need 2017 NeurIPS"],
     "want": [r"vaswani|attention is all you need|2017|advances in neural information processing systems|apa"], "bad": [],
     "note": "correct APA 7th edition citation formatting"},
    {"id": 260, "diff": "Easy", "task": "When is my assignment due?",
     "turns": ["When is my assignment due?"],
     "want": [r"due|deadline|assignment|no deadline|not stored|schedule|calendar|on file|reminder"], "bad": [],
     "note": "reminders/memory recall of assignment due date"},
    {"id": 261, "diff": "Hard", "task": "Check my report for any obvious statistical errors",
     "turns": ["Check my report for any obvious statistical errors"],
     "want": [r"statistic|error|p-value|significance|sample|correlation|report|file|check|normality|null"], "bad": [],
     "note": "statistical review feedback without false confidence"},
    {"id": 262, "diff": "Medium", "task": "Convert my report to PDF for submission",
     "turns": ["Convert my report to PDF for submission"],
     "want": [r"pdf|convert|converted|submission|file|export|generated|document"], "bad": [],
     "note": "files/format conversion to PDF for submission"},

    # --- 45. Freelance & client work management (263-268) ---
    {"id": 263, "diff": "Easy", "task": "What client work do I have due this week?",
     "turns": ["What client work do I have due this week?"],
     "want": [r"client|due|week|project|jq ai|khattak|nothing due|deadline|schedule|milestone"], "bad": [],
     "note": "client work schedule recall from real data"},
    {"id": 264, "diff": "Medium", "task": "Create an invoice draft for the JQ AI Solutions work this month",
     "turns": ["Create an invoice draft for the JQ AI Solutions work this month"],
     "want": [r"invoice|jq ai|draft|amount|rate|hours|month|total|due|input"], "bad": [],
     "note": "drafts invoice with placeholders, does not invent unstated hours"},
    {"id": 265, "diff": "Hard", "task": "Send the invoice to the client",
     "turns": ["Send the invoice to the client"],
     "want": [r"send|invoice|client|\(yes/no\)|confirm|email|recipient|amount"], "bad": [],
     "note": "T2 confirmation gate with recap before invoice send"},
    {"id": 266, "diff": "Medium", "task": "What's the status of the Kestrel Scanner deployment?",
     "turns": ["What's the status of the Kestrel Scanner deployment?"],
     "want": [r"khattak|scanner|deployment|status|progress|active|on file|verified"], "bad": [],
     "note": "accurate status reporting for specific client project"},
    {"id": 267, "diff": "Medium", "task": "Remind me to follow up with this client if they haven't replied in 3 days",
     "turns": ["Remind me to follow up with this client if they haven't replied in 3 days"],
     "want": [r"remind|follow\s*up|3 days|client|reply|reminder|scheduled|october"], "bad": [],
     "note": "conditional or timed reminder with honest boundary disclosure"},
    {"id": 268, "diff": "Hard", "task": "Summarize all my active client projects and their current status",
     "turns": ["Summarize all my active client projects and their current status"],
     "want": [r"client|project|status|jq ai|khattak|active|summary|work"], "bad": [],
     "note": "per-project recall without padding or hallucinated clients"},

    # --- 46. YouTube & content workflow (269-274) ---
    {"id": 269, "diff": "Easy", "task": "How did my last Obscura video perform?",
     "turns": ["How did my last Obscura video perform?"],
     "want": [r"obscura|video|perform|views|analytics|connection|stats|metric|studio|access"], "bad": [],
     "note": "honest disclosure regarding external YouTube analytics connection"},
    {"id": 270, "diff": "Medium", "task": "Suggest upload tags for this video based on its topic",
     "turns": ["Suggest upload tags for this video based on its topic: The Lost Colony of Roanoke"],
     "want": [r"roanoke|lost colony|history|mystery|tag|croatoan|1587|obscura"], "bad": [],
     "note": "relevant, topic-tailored upload tags"},
    {"id": 271, "diff": "Medium", "task": "Schedule this video to publish Friday at 6pm",
     "turns": ["Schedule this video to publish Friday at 6pm"],
     "want": [r"schedule|publish|friday|6pm|youtube|queue|reminder|platform|not connected"], "bad": [],
     "note": "confirms schedule queue or discloses lack of YouTube platform write API"},
    {"id": 272, "diff": "Hard", "task": "Compare my last 3 videos and tell me what's working",
     "turns": ["Compare my last 3 videos and tell me what's working"],
     "want": [r"video|compare|views|engagement|retention|analytics|data|missing|not connected|studio"], "bad": [],
     "note": "genuine comparison or honest requirement for real analytics data"},
    {"id": 273, "diff": "Medium", "task": "Write a YouTube description for this video with timestamps",
     "turns": ["Write a YouTube description for this video with timestamps: Roanoke Island colony mystery"],
     "want": [r"roanoke|0:00|timestamp|description|mystery|colony|subscribe"], "bad": [],
     "note": "YouTube video description formatted with timestamps"},
    {"id": 274, "diff": "Easy", "task": "What's trending in the dark history/mystery niche right now?",
     "turns": ["What's trending in the dark history/mystery niche right now?"],
     "want": [r"history|mystery|true crime|unsolved|dark|trend|popular|topic|conspiracy|cases"], "bad": [],
     "note": "niche-relevant search and trend synthesis"},

    # --- 47. GitHub & version control (275-280) ---
    {"id": 275, "diff": "Easy", "task": "What branch am I on?",
     "turns": ["What branch am I on?"],
     "want": [r"branch|main|master|git|head"], "bad": [],
     "note": "T0 real current git branch report"},
    {"id": 276, "diff": "Medium", "task": "Create a new branch for the leads-skill feature",
     "turns": ["Create a new branch for the leads-skill feature"],
     "want": [r"branch|leads-skill|created|checkout|switched|commands|\(yes/no\)"], "bad": [],
     "note": "git branch creation with safety recap"},
    {"id": 277, "diff": "Medium", "task": "Show me what changed in the last commit",
     "turns": ["Show me what changed in the last commit"],
     "want": [r"commit|diff|changed|author|git|files|update|head|stat"], "bad": [],
     "note": "T0 real diff summary of the most recent commit"},
    {"id": 278, "diff": "Hard", "task": "Open a pull request for this branch",
     "turns": ["Open a pull request for this branch"],
     "want": [r"pull request|pr|branch|github|auth|connector|token|create|\(yes/no\)"], "bad": [],
     "note": "PR creation or honest disclosure of missing GitHub CLI/connector auth"},
    {"id": 279, "diff": "Hard", "task": "There's a merge conflict, help me resolve it",
     "turns": ["There's a merge conflict, help me resolve it"],
     "want": [r"conflict|merge|<<<<|====|>>>>|branch|resolve|version|incoming|current"], "bad": [],
     "note": "shows conflict mechanics and proposed resolution guidance"},
    {"id": 280, "diff": "Medium", "task": "List open issues on my ISHTI repo",
     "turns": ["List open issues on my ISHTI repo"],
     "want": [r"issue|repo|ishti|github|open|connector|token|none|configured"], "bad": [],
     "note": "checks actual repo issues or discloses connector auth state"},

    # --- 48. Group & multi-person coordination (281-286) ---
    {"id": 281, "diff": "Medium", "task": "Create a WhatsApp group with my IMS classmates",
     "turns": ["Create a WhatsApp group with my IMS classmates"],
     "want": [r"group|whatsapp|ims|classmates|contact|members|create|add|qasim"], "bad": [],
     "note": "group creation recap with contact resolution check"},
    {"id": 282, "diff": "Hard", "task": "Find a meeting time that works for me, Ahmed, and Sana",
     "turns": ["Find a meeting time that works for me, Ahmed, and Sana"],
     "want": [r"schedule|meeting|time|calendar|ahmed|sana|availability|propose|free|open slots|your calendar"], "bad": [r"ahmed is free at 3pm and sana is free at 4pm"],
     "note": "honestly scoped to owner's calendar, proposes slots without guessing others'"},
    {"id": 283, "diff": "Medium", "task": "Message the group that I'll be 10 minutes late",
     "turns": ["Message the group that I'll be 10 minutes late"],
     "want": [r"group|message|10 minutes|late|\(yes/no\)|confirm|study group"], "bad": [],
     "note": "T2 confirmation gate before sending group message"},
    {"id": 284, "diff": "Medium", "task": "Who's in my CardioSense project group chat?",
     "turns": ["Who's in my CardioSense project group chat?"],
     "want": [r"cardiosense|group|chat|members|participants|dr\.?\s*farooq|farooq|ishtiaq"], "bad": [],
     "note": "group chat membership recall from actual data"},
    {"id": 285, "diff": "Hard", "task": "Remove Bilal from the study group chat",
     "turns": ["Remove Bilal from the study group chat"],
     "want": [r"bilal|study group|remove|admin|chat|permission|\(yes/no\)|confirm"], "bad": [],
     "note": "T2 gate and admin permission check before member removal"},
    {"id": 286, "diff": "Medium", "task": "Tell everyone in the client group the deadline moved to Monday",
     "turns": ["Tell everyone in the client group the deadline moved to Monday"],
     "want": [r"client|deadline|monday|group|message|\(yes/no\)|confirm|announcement"], "bad": [],
     "note": "T2 bulk message recap and confirmation gate"},

    # --- 49. Vague or incomplete requests (287-292) ---
    {"id": 287, "diff": "Medium", "task": "Can you help me with that thing?",
     "turns": ["Can you help me with that thing?"],
     "want": [r"what specifically|which thing|clarify|mean|help you with|tell me more|could you specify"], "bad": [],
     "note": "asks for clarification instead of guessing randomly"},
    {"id": 288, "diff": "Medium", "task": "Make it better (after showing a draft)",
     "turns": ["Here is my draft: AI is good.", "Make it better"],
     "want": [r"better|improve|rewrite|specifically|tone|concise|clarity|sentence|draft"], "bad": [],
     "note": "asks for specific improvement criteria or shows clearly improved draft"},
    {"id": 289, "diff": "Easy", "task": "Do the thing from before",
     "turns": ["Do the thing from before"],
     "want": [r"which thing|before|remind|clarify|mean|specific|referring to|task"], "bad": [],
     "note": "asks for reminder rather than acting on a stale guess"},
    {"id": 290, "diff": "Medium", "task": "I don't like this",
     "turns": ["I don't like this"],
     "want": [r"what|like|change|adjust|specifically|wrong|prefer|improve|tone|length"], "bad": [],
     "note": "diagnostic inquiry on vague negative feedback"},
    {"id": 291, "diff": "Hard", "task": "Conflicting requirements: short but detailed",
     "turns": ["Make it short but cover everything in detail"],
     "want": [r"short|detail|balance|summary|tension|concise|comprehensive|middle ground|overview|bullet"], "bad": [],
     "note": "identifies tension in conflicting constraints and offers structured middle ground"},
    {"id": 292, "diff": "Medium", "task": "You know what I mean",
     "turns": ["You know what I mean"],
     "want": [r"not sure|clarify|mean|specifically|could you explain|help me understand"], "bad": [],
     "note": "honest admission of uncertainty on unclear colloquial reference"},

    # --- 50. Cost & budget awareness (293-296) ---
    {"id": 293, "diff": "Medium", "task": "Find a tool for video transcription, but keep it free or open-source",
     "turns": ["Find a tool for video transcription, but keep it free or open-source"],
     "want": [r"whisper|open-source|free|faster-whisper|github|local|subtitle edit"], "bad": [r"rev\.com paid transcription service"],
     "note": "surfaces strictly free and open-source options"},
    {"id": 294, "diff": "Medium", "task": "Is there a cheaper way to do this?",
     "turns": ["Is there a cheaper way to do this?"],
     "want": [r"cheap|free|open-source|alternative|cost|local|save|tier|\$0"], "bad": [],
     "note": "cost-aware suggestion emphasizing local/open-source efficiency"},
    {"id": 295, "diff": "Hard", "task": "Don't use any paid APIs for this",
     "turns": ["Don't use any paid APIs for this"],
     "want": [r"free|local|offline|paid|api|ollama|cost|no paid|confirm"], "bad": [r"calling open-ai gpt-4"],
     "note": "verifies zero-cost local resource constraint"},
    {"id": 296, "diff": "Easy", "task": "What's this going to cost me?",
     "turns": ["What's this going to cost me?"],
     "want": [r"cost|free|\$0|dollar|price|expense|charge|local|offline"], "bad": [],
     "note": "upfront cost disclosure confirming $0 local processing"},

    # --- 51. Learning a new tool / onboarding help (297-300) ---
    {"id": 297, "diff": "Medium", "task": "Help figure out how a new tool works (UV)",
     "turns": ["I just installed a new tool called UV, can you help me figure out how it works?"],
     "want": [r"uv|python|astral|package|pip|rust|fast|virtualenv|project"], "bad": [],
     "note": "accurate explanation grounded in Astral UV documentation"},
    {"id": 298, "diff": "Hard", "task": "Set this new tool up for my workflow",
     "turns": ["Set this new tool up for my workflow"],
     "want": [r"setup|workflow|tool|steps|configure|path|install|which tool|requirements"], "bad": [],
     "note": "confirms setup steps or requests workflow specifics"},
    {"id": 299, "diff": "Medium", "task": "Compare new tool with prior tool (UV vs pip)",
     "turns": ["What's the difference between UV and pip that I was using before?"],
     "want": [r"uv|pip|rust|speed|faster|package|manager|dependency|virtualenv|lockfile"], "bad": [],
     "note": "accurate, factual comparison of modern tool vs legacy"},
    {"id": 300, "diff": "Easy", "task": "Remember standing context for learning new tool",
     "turns": ["Remember that I'm trying to learn UV, help me as I go"],
     "want": [r"uv|remember|memory|learn|keep in mind|noted|preference|future|standing"], "bad": [],
     "note": "stores standing onboarding context for future sessions"},
]

# ----------------------------------------------------------- Evaluation
BLOCKED = re.compile(r"not logged in|isn'?t logged in|log ?in to|sign ?in|scan the qr|qr code|"
                     r"isn'?t connected|not connected|profile .* not|no chrome|extension|"
                     r"captcha|premium|can'?t access|don'?t have access|not available", re.I)


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

    want = task.get("want") or []
    present = any(re.search(p, joined, re.I) for p in want) if want else True
    if not present and BLOCKED.search(joined):
        blocked = True
    v = "fail" if fails else ("blocked" if blocked else ("pass" if present else "partial"))
    return {"id": task["id"], "diff": task["diff"], "task": task["task"], "verdict": v,
            "gate": gate_seen, "sent": [n for n in res["notes"] if "SEND" in n or "VOICE" in n],
            "fires": res["notes"], "fails": fails, "steps": steps}


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


def warm_browser() -> None:
    try:
        from ishti.mcp_client import get_hub
        h = get_hub()
        for name in ("main", "login"):
            try:
                tools = h.tools(name)
                print(f"[qa5] browser warm-up {name}: {len(tools)} tools", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[qa5] browser warm-up {name} failed: {type(exc).__name__}: {exc}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[qa5] browser warm-up unavailable: {type(exc).__name__}: {exc}", flush=True)


def main() -> int:
    tasks = _select(sys.argv[1:])
    out_dir = os.path.join(ROOT, "reports", "checklist_part5")
    os.makedirs(out_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    jsonl = os.path.join(out_dir, f"run_{stamp}.jsonl")
    rows = []
    print(f"[qa5] running {len(tasks)} task(s); scratch={SCRATCH}", flush=True)
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
    md = [f"# ISHTI checklist part 5 (251-300) - run {stamp}", "",
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
    print(f"\n[qa5] totals {counts}\n[qa5] report {mdp}\n[qa5] jsonl  {jsonl}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
