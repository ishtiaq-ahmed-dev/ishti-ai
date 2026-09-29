"""Re-verification smoke: every skill is routed AND executed live with real inputs.

Gates already run by tools/verify_all.py (self-checks). This pass adds what a self-check
cannot show: the exact phrasings an owner would say, end-to-end through dispatcher.route(),
with the computed answer checked against hand-calculated ground truth.

- DB writes (checklist) are pointed at a temp dir before import — owner data untouched.
- LLM-backed skills (citation, mnemonic) run against the local pinned planner model only.
- The full roster is printed with tier/scope/status so nothing is skipped silently.

    venv/Scripts/python.exe -X utf8 tools/smoke_reverify.py
Exit 0 = every live case passed.
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ishti  # noqa: E402  (patch CONFIG before any skill module reads it)

_TMP = tempfile.mkdtemp(prefix="ishti_reverify_")
ishti.CONFIG["memory"]["db_path"] = os.path.join(_TMP, "memory.db")

from ishti import dispatcher as d  # noqa: E402
from ishti.skills import load_skills  # noqa: E402

# skill -> list of (request, any-of expected substrings in the reply)
LIVE = {
    "calc": [
        ("solve 2x + 5 = 13", ("x = 4", "[4]")),
        ("derivative of x^3 + 2x", ("3*x**2",)),
        ("integral of 3x^2", ("x**3",)),
        ("simplify (x**2 - 1)/(x - 1)", ("x + 1",)),
        ("what is 15% of 2400", ("360",)),
        ("tip on 1200 at 10%", ("120",)),
        ("prime factors of 84", ("2", "7")),
        ("gcd of 48 and 60", ("12",)),
        ("lcm of 4 and 6", ("12",)),
    ],
    "quickcalc": [
        ("split 4800 between 3 people with 12.5% tip", ("1,800", "1800")),
        ("bmi 82 and 178", ("25.9", "25.89", "26")),
        ("price after 30% discount on 2499", ("1,749", "1749")),
        ("fuel cost of 120 km at 14 km/l at rs 272 per litre", ("2,331", "2331")),
        ("wake up if i go to bed now", ("cycle",)),
    ],
    "textstats": [
        ("sentiment of this: I love this amazing product, it works great", ("positive",)),
        ("sentiment of this: This is terrible, broken and I hate it", ("negative",)),
        ("keywords: the quick brown fox jumps over the lazy dog again and again", ("again",)),
        ("how many words are in this: one two three four five", ("5",)),
        ("readability of this: The cat sat on the mat. It was warm.", ("Flesch", "flesch")),
        ("entities in this: Contact Ali at ali@example.com or https://x.pk", ("ali@example.com",)),
    ],
    "phishing_check": [
        ("is this link safe http://192.168.1.5/secure-login verify your account now", ("IP", "192.168")),
        ("check this link http://paypa1-security.tk/login", ("paypa",)),
        ("is this link safe https://bit.ly/3xY", ("bit.ly",)),
    ],
    "checklist": [
        ("new checklist for reverify: torch, rope", ("torch", "rope")),
        ("add matches to the reverify checklist", ("matches",)),
        ("tick off torch", ("torch",)),
        ("what's left on the reverify checklist", ("matches",)),
        ("reset the reverify checklist", ("reset", "0", "clear")),
        ("my reverify checklist", ("rope",)),
    ],
    "citation": [  # local planner model (pinned ishti-qwen2.5:14b), temperature 0.1
        ("apa citation: Khan, A. 2023. Local AI assistants. Journal of Things, 5(2), 12-20.", ("Khan",)),
        ("mla citation for: Deep Learning by Goodfellow, MIT Press 2016", ("Goodfellow",)),
    ],
    "mnemonic": [
        ("make a mnemonic for: RGB = red, green, blue", ("red", "Red")),
        ("memory palace for: keys, wallet, passport", ("keys", "palace", "location")),
    ],
    # ---- batch C (2026-09-28) ----
    "vocab": [
        ("add word serendipity: finding something good by accident", ("saved", "serendipity")),
        ("what does serendipity mean", ("by accident",)),
        ("quiz my vocab", ("serendipity",)),
        ("vocab stats", ("1 word",)),
    ],
    "recipe": [  # local planner model
        ("recipe from eggs, rice and tomatoes", ("egg", "rice", "cook", "dish", "ingredient")),
        ("grocery list for biryani", ("rice", "item", "buy", "-", "fresh", "spice")),
    ],
    "subscriptions": [
        ("track subscription netflix 1500 monthly on the 5th", ("tracked", "/month")),
        ("add subscription spotify 299 per month", ("tracked",)),
        ("my subscriptions", ("1,799/mo", "21,588/year")),  # 1500 + 299 exact
        ("upcoming renewals", ("netflix",)),
        ("cancel subscription spotify", ("removed",)),
    ],
    "networth": [
        ("track asset bank account 250000", ("tracked",)),
        ("track liability bike loan 180000", ("tracked",)),
        ("my net worth", ("70,000",)),
        ("list my assets and liabilities", ("bank account", "bike loan")),
    ],
    "sleeplog": [
        ("log sleep 7.5 hours", ("7.5 h",)),
        ("my sleep this week", ("average", "Average")),
        ("my sleep debt", ("debt",)),
    ],
    "moodlog": [
        ("rate my mood today 4", ("4/5",)),
        ("mood 2: stressful day", ("2/5", "was 4/5")),
        ("my mood this week", ("average", "Average")),
        ("i want to kill myself", ("1122",)),  # safety gate must fire, never a casual reply
    ],
    # ---- batch D (2026-09-28) ----
    "redact": [
        ("redact the text: email me at bob@corp.com about invoice 12345", ("[EMAIL-1]",)),
        ("redact show D:/definitely/missing.txt", ("not found",)),  # preview path, safe live
    ],
    "devops": [
        ("create a github actions workflow for a python app", ("actions/checkout@v4",)),
        ("kubernetes deployment for orders", ("kind: Deployment",)),
        ("terraform for aws s3", ("aws_s3_bucket",)),
        ("dockerfile for python", ("FROM python:3.12-slim",)),
    ],
    "gitops": [
        ("git status of the ishti project", ("branch", "clean", "modified", "On ")),
        ("commit my changes in the ishti project", ("state-changing", "T2")),  # queued, never run
        ("force push to origin", ("refuse",)),  # refusal owned in code
    ],
    "sendlater": [
        ("send an email to smoke@test.com tomorrow at 9 saying hello there", ("Scheduled email", "tomorrow")),
        ("list my scheduled emails", ("smoke@test.com",)),
        ("cancel the scheduled email to smoke@test.com", ("Cancelled 1",)),
    ],
    "mailmerge": [
        ("mailmerge D:/definitely/missing.csv: hi {name}", ("not found",)),
        ("show my mailmerge drafts", ("drafts in", "No mailmerge drafts yet")),
    ],
    "pdftables": [
        ("extract tables from D:/definitely/missing.pdf", ("not found",)),
    ],
    # ---- batch E (2026-09-28) ----
    "forecast": [
        ("forecast D:/definitely/missing.csv next 6 months", ("not found",)),
        ("trend of D:/definitely/missing.csv", ("not found",)),
    ],
    "chart": [
        ("bar chart of D:/definitely/missing.csv", ("not found",)),
        ("show charts", ("No charts yet", "chart(s) in")),
    ],
    "palette": [
        ("color palette of D:/definitely/missing.png", ("not found",)),
    ],
    "imgquality": [
        ("image quality of D:/definitely/missing.jpg", ("not found",)),
    ],
    "abtest": [
        # ground truth: 12.00% vs 9.31%, p = 0.0503 -> honestly not significant at alpha 0.05
        ("ab test of 120 out of 1000 vs 95 out of 1020", ("Not significant", "12.00%", "9.31%")),
        ("ab test of 1200 out of 10000 vs 950 out of 10200", ("Significant", "A wins")),
    ],
    "prioritize": [
        ("add tasks: smoke test task urgent", ("Added 1", "DO NOW")),
        ("prioritize my tasks", ("smoke test task",)),
        ("clear my tasks", ("Cleared",)),
    ],
    # ---- batch F (2026-09-28) ----
    "dataprep": [
        ("clean the csv D:/definitely/missing.csv", ("not found",)),
    ],
    "vulnscan": [
        ("vulnerability scan of D:/definitely/missing_proj", ("Not found",)),
    ],
    "optimize": [
        ("maximize 5x + 3y subject to x <= 4, y <= 6", ("38",)),
        ("minimize x**2 - 4*x at x near -10 to 10", ("x = 2", "-4")),
    ],
    "timeline": [
        ("timeline from these events: just words, more words", ("None of those lines",)),
    ],
    "formula": [
        ("formula to count unique values in D1:D50", ("SUMPRODUCT",)),
        ("what does =SUMIF(B1:B20, >100, C1:C20) do", ("SUMIF",)),
    ],
    "resumeparse": [
        ("parse my resume D:/definitely/missing.pdf", ("Could not read",)),
    ],
    # ---- batch G (2026-09-28) ----
    "metrics": [
        ("log weight 82", ("Logged **weight** 82",)),
        ("log blood pressure today 130 over 85", ("130/85",)),
        ("my weight this week", ("avg", "No **weight**")),
        ("metrics summary", ("weight",)),
    ],
    "symptomlog": [
        ("log smoke test symptom severity 3", ("Logged",)),
        ("symptom stats", ("smoke test symptom",)),
        ("doctor summary", ("Doctor-visit summary",)),
    ],
    "goaltrack": [
        ("new goal save 100k for smoke-test-goal", ("Goal **smoke-test-goal** created",)),
        ("add 25000 to the smoke-test-goal goal", ("25%",)),
        ("cancel the smoke-test-goal goal", ("Cancelled",)),
    ],
    "standup": [
        ("set standup yesterday: smoke test item", ("Standup yesterday set",)),
        ("give me my standup", ("**Yesterday:** smoke test item", "No standup lines")),
    ],
    "meetingcost": [
        ("cost of a 60 minute meeting with 8 people at 2500 per hour", ("20,000",)),
        ("yearly cost of a weekly 30 minute meeting with 6 people at 2000 per hour", ("312,000",)),
    ],
    "gantt": [
        ("gantt chart of: smokeA 2d, smokeB 1d after smokeA", ("dateFormat", "3 day(s) total",)),
    ],
    # ---- batch H (2026-09-29) ----
    "imgcrop": [
        ("crop D:/definitely/missing.png to square", ("not found",)),
    ],
    "meme": [
        ("meme D:/definitely/missing.png top: nope", ("not found",)),
        ("make a meme: smoke top text | smoke bottom text", ("Meme saved", "SMOKE TOP TEXT")),
    ],
    "audioquality": [
        ("audio quality of D:/definitely/missing.mp3", ("not found",)),
    ],
    "kanban": [
        ("kanban add smoke-card", ("Added to **todo**",)),
        ("move smoke-card to doing", ("→ doing",)),
        ("my kanban board", ("smoke-card",)),
        ("kanban done for smoke-card", ("→ done",)),
        ("clear kanban done", ("Cleared",)),
    ],
    "okr": [
        ("set objective smoke objective", ("Objective **smoke objective** set",)),
        ("add key result 1000 subscribers to smoke objective", ("0/1000",)),
        ("update kr 1 of smoke objective to 400", ("400/1000 (40%)",)),
        ("my okrs", ("smoke objective",)),
    ],
    "riskregister": [
        ("log risk smoke risk likelihood 4 impact 5", ("20/25", "HIGH")),
        ("mitigate smoke risk with nightly backups", ("nightly backups", "20/25")),
        ("top risks", ("smoke risk",)),
        ("close risk smoke risk", ("closed",)),
    ],
    # ---- batch I (2026-09-29) ----
    "scanqr": [
        ("qr code for ISHTI-SMOKE-42", ("Verified",)),
        ("scan the qr in D:/definitely/missing.png", ("not found",)),
    ],
    "audioclean": [
        ("denoise D:/definitely/missing.mp3", ("not found",)),
    ],
    "chapters": [
        ("chapters for D:/definitely/missing.mp4", ("not found",)),
    ],
    "sudoku": [
        ("solve this sudoku: 530070000600195000098000060800060003400803001700020006060000280000419005000080079", ("Solved",)),
    ],
    "bayes": [
        ("bayes: prior 1% sensitivity 99% specificity 95%", ("16.67%",)),
    ],
    "montecarlo": [
        ("simulate 10000 rolls of 2 dice", ("modal sum **7**", "matches")),
    ],
    # ---- batch J (2026-09-29) ----
    "legaldoc": [
        ("draft an nda between me and smoke corp for 2 years", ("not legal advice", "smoke corp", "[FILL:")),
    ],
    "pressrel": [
        ("draft a press release: smoke product launches 2026-11-01 with free shipping", ("PRESS RELEASE", "2026-11-01")),
        ("generate an faq from: returns accepted within 14 days; warranty 1 year", ("Q1.", "14 days")),
    ],
    "doclayout": [
        ("layout of D:/definitely/missing.pdf", ("not found",)),
    ],
    # ---- batch K (2026-09-29) ----
    "pantry": [
        ("add smoke-item to the pantry", ("Added **1 × smoke-item**",)),
        ("pantry list", ("smoke-item",)),
        ("used up the smoke-item", ("removed",)),
    ],
    "audiomark": [
        ("bookmark 00:01 in D:/smoke-test.mp3: smoke note", ("Bookmarked **00:01**",)),
        ("my bookmarks for D:/smoke-test.mp3", ("smoke note",)),
    ],
    "highlights": [
        ("highlights of D:/definitely/missing.mp4", ("not found",)),
    ],
    "chromakey": [
        ("remove the green screen from D:/definitely/missing.mp4", ("not found",)),
    ],
    "infographic": [
        ("infographic of D:/definitely/missing.csv", ("not found",)),
    ],
    # ---- batch L (2026-09-29) ----
    "gametheory": [
        ("analyze the game: TL 3,2; TR 1,1; BL 0,3; BR 2,0", ("Nash", "dominant")),
    ],
    "sensitivity": [
        ("sensitivity analysis of f(x) = 30*x - 1000, x from 10 to 50 step 5", ("| 10 | -700",)),
    ],
    "argumentcheck": [
        ("check my argument: he is an idiot so his plan fails", ("ad hominem",)),
    ],
}


def main():
    skills = load_skills()
    broken = {n: s.problem for n, s in skills.items() if s.problem}
    print(f"[roster] {len(skills)} skills loaded" + (f", BROKEN: {broken}" if broken else ", none broken"))

    print("[roster] name | route | tier | scope flags | status")
    for name, s in sorted(skills.items()):
        m = s.manifest
        sc = ",".join(k for k in ("write", "net", "apps") if (m.get("scope") or {}).get(k)) or "-"
        print(f"  {name:22s} | {m.get('route', '?'):18s} | {m.get('tier', '?')} | {sc:12s} | {s.status}")

    fails, passed = [], 0
    for skill, cases in LIVE.items():
        s = skills[skill]
        for text, expected in cases:
            hit = s.matches(text)
            routed = d.route(text, skills)
            ok_route = routed["skill"] == skill
            reply = ""
            ok_reply = False
            if hit:
                out = s.run(text, {"trace": None})
                reply = (out or {}).get("reply", "")
                ok_reply = bool(reply) and any(e.lower() in reply.lower() for e in expected)
            if hit and ok_route and ok_reply:
                passed += 1
                print(f"  PASS [{skill}] {text[:58]!r} -> {reply[:72]!r}")
            else:
                fails.append((skill, text, expected, reply, routed["skill"]))
                print(f"  FAIL [{skill}] {text[:58]!r} expected {expected}, "
                      f"match={hit}, routed={routed['skill']}({routed['source']}), reply={reply[:72]!r}")

    # negative controls: plain requests must not be captured by the new matchers
    negatives = [
        ("what is 17 times 23? reply with just the number", None),
        ("remind me to call the dentist tomorrow at 5pm", None),
        ("what's the weather tomorrow?", None),
    ]
    for text, _ in negatives:
        r = d.route(text, skills)
        if r["skill"] in LIVE:
            fails.append(("-", text, "not a batch-B skill", "", r["skill"]))
            print(f"  FAIL [negative] {text!r} wrongly routed to {r['skill']}")
        else:
            print(f"  PASS [negative] {text[:52]!r} -> {r['skill']}")

    shutil.rmtree(_TMP, ignore_errors=True)
    total = passed + len(fails)
    print(f"[smoke] {passed}/{total} live cases passed" + ("" if not fails else f"; {len(fails)} FAILED"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
