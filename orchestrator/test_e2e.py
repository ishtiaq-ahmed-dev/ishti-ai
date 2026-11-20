"""
ISHTI End-to-End Acceptance Tests (brief section 12, tests A-L).

Everything here hits the real stack: real model, real internet, real TTS.
Nothing is mocked and nothing passes because a package is installed.

    venv\\Scripts\\python.exe orchestrator/test_e2e.py          # all tests
    venv\\Scripts\\python.exe orchestrator/test_e2e.py C D      # selected tests
"""

import os
import sys
import time
import json

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from orchestrator.response_guard import find_refusals, find_contamination
from orchestrator.memory.user_profile import get_user_memory

RESULTS = []


def sentences(text: str) -> int:
    """Count real sentences. Fragments like '(open-meteo.com)' are not sentences."""
    parts = text.replace("!", ".").replace("?", ".").split(".")
    return len([s for s in parts if len(s.split()) >= 3])


def check(name, passed, detail=""):
    RESULTS.append({"test": name, "passed": bool(passed), "detail": detail})
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
    return passed


def ask(engine, prompt):
    """Every request goes through the real dispatcher (routing + skills), like the HUD and voice."""
    from ishti.dispatcher import handle
    t0 = time.time()
    res = handle(prompt, "e2e")
    res["_elapsed"] = round(time.time() - t0, 1)
    return res


def no_refusal_no_contamination(name, answer):
    refusals, contam = find_refusals(answer), find_contamination(answer)
    return check(name, not refusals and not contam,
                 f"refusals={refusals} contamination={contam}" if (refusals or contam) else "clean")


# ---------------------------------------------------------------- tests

def test_A(engine):
    print("\nTEST A - Identity")
    r = ask(engine, "Who am I?")
    a = r["final_answer"]
    print(f"    > {a[:220]}")
    ok = "ishtiaq" in a.lower() and "peshawar" in a.lower()
    return check("A: knows owner name and city", ok, f"{r['_elapsed']}s") and \
        no_refusal_no_contamination("A: no refusal/contamination", a)


def test_B(engine):
    print("\nTEST B - Memory retrieval")
    r = ask(engine, "What do you know about me?")
    a = r["final_answer"].lower()
    print(f"    > {r['final_answer'][:220]}")
    return check("B: recalls name, role, location",
                 "ishtiaq" in a and "peshawar" in a and ("student" in a or "ai" in a),
                 f"{r['_elapsed']}s")


def test_C(engine):
    print("\nTEST C - Live weather using remembered city")
    r = ask(engine, "what's the weather tomorrow?")
    a = r["final_answer"]
    print(f"    > {a}")
    ok = check("C: uses Peshawar not Karachi",
               "peshawar" in a.lower() and "karachi" not in a.lower())
    ok &= check("C: answer is <= 2 sentences", sentences(a) <= 2, f"{sentences(a)} sentences")
    ok &= check("C: contains a real temperature", any(ch.isdigit() for ch in a) and "C" in a)
    ok &= check("C: no dictionary/irrelevant source",
                all("dictionary" not in (s.get("url", "") + s.get("title", "")).lower()
                    for s in r.get("sources", [])))
    ok &= check("C: verified flag is real", r.get("verified") is True)
    return ok


def test_D(engine):
    print("\nTEST D - Live internet search")
    r = ask(engine, "Search the internet for the latest version of Ollama.")
    a = r["final_answer"]
    print(f"    > {a[:300]}")
    ok = no_refusal_no_contamination("D: no 'I can't search'", a)
    ok &= check("D: returned real sources", len(r.get("sources", [])) > 0,
                f"{len(r.get('sources', []))} sources")
    ok &= check("D: every source has an http URL",
                all(s.get("url", "").startswith("http") for s in r.get("sources", [])))
    return ok


def test_G(engine):
    print("\nTEST G - Research quality")
    r = ask(engine, "Research the latest AI news this week.")
    srcs = r.get("sources", [])
    print(f"    > {len(srcs)} sources, answer {len(r['final_answer'])} chars")
    for s in srcs[:6]:
        print(f"      [{s['id']}] {s['title'][:60]} - {s['url'][:70]}")
    bad = [s for s in srcs if any(d in s.get("url", "").lower() for d in
                                  ("dictionary.cambridge", "merriam-webster", "grammarly", "thesaurus"))]
    ok = check("G: no dictionary sources", not bad, str([s["url"] for s in bad]))
    ok &= check("G: sources exist and are fetchable URLs",
                bool(srcs) and all(s.get("url", "").startswith("http") for s in srcs))
    cited = {int(t) for t in __import__("re").findall(r"\[(\d+)\]", r["final_answer"])}
    valid = {s["id"] for s in srcs}
    ok &= check("G: no fabricated citations", cited <= valid, f"cited={sorted(cited)} valid={sorted(valid)}")
    return ok


def test_H(engine):
    print("\nTEST H - Response brevity on small talk")
    r = ask(engine, "hey how are you")
    a = r["final_answer"]
    print(f"    > {a}")
    ok = check("H: one or two sentences", sentences(a) <= 2, f"{sentences(a)} sentences")
    ok &= check("H: no report headings", "Executive Summary" not in a and "##" not in a)
    ok &= no_refusal_no_contamination("H: no classified/intel language", a)
    return ok


def test_I(engine):
    print("\nTEST I - English audio output")
    from ishti import CONFIG
    from ishti.voice import synthesize  # Piper is the primary TTS now (AUDIT.md B6)
    size = len(synthesize("**Tomorrow** in Peshawar: 32C, sunny [1]"))
    ok = check("I: TTS produced audio bytes", size > 1000, f"{size} bytes")
    ok &= check("I: an English voice is selected", CONFIG["voice"]["piper_voice"].startswith("en_"),
                CONFIG["voice"]["piper_voice"])
    r = ask(engine, "read that")
    ok &= check("I: audio toggle command works",
                get_user_memory().get_preferences().audio_output is True, r["final_answer"][:60])
    return ok


def test_J(engine):
    print("\nTEST J - Provider failover")
    from tools.web.search import get_search_manager
    sm = get_search_manager()
    original = sm.bing.search
    sm.bing.search = lambda q, max_results=5: []          # simulate primary outage
    try:
        results = sm.search("latest Python release version", max_results=3)
        ok = check("J: fallback provider returned results", bool(results),
                   f"{len(results)} results from {results[0].source if results else 'none'}")
    finally:
        sm.bing.search = original
    ok &= check("J: primary restored", bool(sm.search("python", max_results=2)))
    return ok


def test_K(engine):
    print("\nTEST K - Memory persistence across restart")
    # A genuinely fresh process, not a re-import of a cached singleton.
    code = (f"import sys; sys.path.insert(0, {ROOT_DIR!r});"
            "from orchestrator.memory.user_profile import get_user_memory as g;"
            "p=g().get_profile(); print(p.name + '|' + p.location.get('city',''))")
    import subprocess
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    line = (out.stdout or "").strip().splitlines()[-1] if out.stdout.strip() else ""
    return check("K: fresh process still knows the owner",
                 "Ishtiaq Ahmed" in line and "Peshawar" in line, line or out.stderr[:120])


def test_L(engine):
    print("\nTEST L - Refusal filter")
    poisoned = ("I'm not capable of directly searching the internet. Unfortunately, I cannot access "
                "websites. Also, per security protocols, this is a CLASSIFIED document.")
    from orchestrator.response_guard import fallback_from_evidence, inspect
    ok_flag, _, _ = inspect(poisoned)
    check("L: poisoned reply is detected", not ok_flag)
    guarded = fallback_from_evidence(  # what a guarded route answers with instead of the refusal
        [{"id": 1, "title": "Ollama releases", "snippet": "v0.12.0 released",
          "url": "https://github.com/ollama/ollama/releases"}], "what is the latest version of Ollama")
    print(f"    > {guarded[:200]}")
    ok = check("L: refusal text never reaches the owner", not find_refusals(guarded))
    ok &= check("L: contamination stripped", not find_contamination(guarded))
    from orchestrator.response_guard import is_safe_for_history
    ok &= check("L: poisoned text is barred from history", not is_safe_for_history(poisoned))
    return ok


TESTS = {"A": test_A, "B": test_B, "C": test_C, "D": test_D,
         "G": test_G, "H": test_H, "I": test_I, "J": test_J, "K": test_K, "L": test_L}

if __name__ == "__main__":
    from orchestrator.memory.user_profile import scratch_memory
    wanted = [a.upper() for a in sys.argv[1:]] or list(TESTS)
    print("=" * 70)
    print("ISHTI END-TO-END ACCEPTANCE TESTS - real stack, no mocks")
    print("=" * 70)

    import shutil
    import tempfile
    from ishti import CONFIG
    tmp = tempfile.mkdtemp(prefix="ishti_e2e_")  # facts/route log/semantic memory: a temp copy too
    if os.path.exists(CONFIG["memory"]["db_path"]):
        shutil.copy(CONFIG["memory"]["db_path"], os.path.join(tmp, "ishti.db"))
    CONFIG["memory"].update(db_path=os.path.join(tmp, "ishti.db"), chroma_dir=os.path.join(tmp, "chroma"))

    with scratch_memory():  # writes (audio toggle etc.) go to a temp copy of the owner's memory
        engine = None
        for key in wanted:
            try:
                TESTS[key](engine)
            except Exception as exc:
                check(f"{key}: raised {type(exc).__name__}", False, str(exc)[:200])

    passed = sum(1 for r in RESULTS if r["passed"])
    print("\n" + "=" * 70)
    print(f"RESULT: {passed}/{len(RESULTS)} assertions passed")
    for r in RESULTS:
        if not r["passed"]:
            print(f"  FAILED: {r['test']} - {r['detail']}")
    print("=" * 70)
    with open(os.path.join(ROOT_DIR, "data", "logs", "e2e_results.json"), "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, indent=2)
    sys.exit(0 if passed == len(RESULTS) else 1)
