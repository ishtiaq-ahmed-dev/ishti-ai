"""Re-verification smoke: every skill is routed AND executed live with real inputs.

Gates already run by tools/verify_all.py (self-checks). This pass adds what a self-check
cannot show: the exact phrasings an owner would say, end-to-end through dispatcher.route(),
with the computed answer checked against hand-calculated ground truth.

- DB writes (checklist) are pointed at a temp dir before import — owner data untouched.
- LLM-backed skills (citation, mnemonic) run against the local pinned planner model only.
- The full 61-skill roster is printed with tier/scope/status so nothing is skipped silently.

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
