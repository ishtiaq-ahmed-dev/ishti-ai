"""Routing accuracy: the in-sample LABELLED set from ishti/check.py plus a HELD_OUT paraphrase set never used to tune rules.
Run: set ISHTI_TEST_MODE=1 ISHTI_SCRATCH=1; venv\Scripts\python.exe tools\qa\route_eval.py   (needs the local planner model for LLM fallback)"""
import ast
import json
import os
import sys
import time

os.environ.setdefault("ISHTI_TEST_MODE", "1")
os.environ.setdefault("ISHTI_SCRATCH", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

HELD_OUT = [
    ("what's the temperature in Lahore right now", "weather"), ("do I need an umbrella today", "weather"),
    ("who is the president of France", "web_search"), ("what happened in the news about the budget", "web_search"),
    ("remind me to call mom at 6pm", "reminders"), ("wake me up in 45 minutes", "reminders"),
    ("how much free space is left on my C drive", "system"), ("what graphics card do I have", "system"),
    ("open notepad", "apps"), ("start spotify", "apps"),
    ("summarize the pdf in my downloads called lease", "docs"), ("write down that the plumber comes friday", "docs"),
    ("what's the dollar rate in pakistan", "utilities"), ("price of bitcoin", "markets"),
    ("send a whatsapp to Ali saying I'm late", "whatsapp"), ("what did Sara message me yesterday", "whatsapp"),
    ("find my passport scan", "files"), ("move all screenshots to a folder", "folders"),
    ("play the song Tum Hi Ho on youtube", "browser"), ("book a table: search restaurants near me", "browser"),
    ("tell me a bedtime story", "chat"), ("what is the capital of Japan", "chat"),
    ("explain recursion like I'm five", "chat"), ("translate good morning into Urdu", "chat"),
    ("compare the best 3 phones under 50000 rupees and where to buy", "shopping"),
    ("deep dive on how solid state batteries work, with sources", "deep_research"),
    ("I spent 1200 on petrol", "ledger"), ("remember that my wifi password is on the fridge", "memory"),
    ("my brother's name is Hamza", "relationships"), ("make a short video from these clips", "video"),
]

def main():
    src = open("ishti/check.py", encoding="utf-8").read()
    lab = next(n for n in ast.parse(src).body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "LABELLED")
    sets = {"in_sample": ast.literal_eval(lab.value), "held_out": HELD_OUT}
    from ishti import dispatcher as d, router
    report = {}
    for name, rows in sets.items():
        t0, bad = time.time(), []
        for text, exp in rows:
            r = router.route(text, d.SKILLS)
            if r["skill"] != exp:
                bad.append([text, exp, r["skill"], r["source"]])
        report[name] = {"n": len(rows), "correct": len(rows) - len(bad), "accuracy": round(100 * (len(rows) - len(bad)) / len(rows), 1),
                        "seconds": round(time.time() - t0, 1), "misses": bad}
        print(f"{name}: {report[name]['correct']}/{len(rows)} = {report[name]['accuracy']}%")
        for b in bad:
            print("   MISS", b)
    os.makedirs("docs/audit", exist_ok=True)
    json.dump(report, open("docs/audit/route_eval.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
