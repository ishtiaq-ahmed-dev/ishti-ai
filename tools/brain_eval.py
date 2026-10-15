"""Live check of the single architecture: does the one model call pick the right skill, or answer directly?

Runs each prompt through ishti/brain.py with the REAL local model and the REAL skill shortlist, but with the skill
executor stubbed, so no tool touches the PC, the web or any account. Prints one line per prompt and a summary:
tool-choice accuracy, model calls per turn, and latency.

Needs a running Ollama with the planner model (config llm.models.planner). Run:
    venv\\Scripts\\python tools\\brain_eval.py
"""
import json
import sys
import time

sys.path.insert(0, ".")

from ishti import brain, llm  # noqa: E402
from ishti import skill_execution  # noqa: E402
from ishti.memstore import retrieval  # noqa: E402
from ishti.skills import load_skills  # noqa: E402

# expected: None (a direct answer, no tool); a skill name or a tuple of acceptable skills (one tool);
# or a list (every part must be called, each part a name or a tuple of acceptable names).
CASES = [
    ("what's the weather in Lahore tomorrow", "weather"),
    ("set a reminder to call Ali at 5 pm", "reminders"),
    ("what is 17 times 23", ("calc", "quickcalc")),
    ("what is the latest news about OpenAI", ("web_search", "deep_research")),
    ("what is the bitcoin price right now", "markets"),
    ("send a WhatsApp message to Ali saying I'm running late", "whatsapp"),
    ("find my invoice pdf in downloads", ("files", "folders", "fileops")),
    ("add a meeting with the team on Friday at 3pm to my calendar", "calendar"),
    ("make a note: buy milk and eggs", "notes"),
    ("I spent 500 rupees on lunch, log it", ("budget", "ledger")),
    ("quiz me on photosynthesis", "study"),
    ("make a bar chart of these numbers 3, 5, 7", "chart"),
    ("what is the capital of Kazakhstan", None),
    ("hi, how are you", None),
    ("explain in two sentences what a neural network is", None),
    ("what did I just say", None),
    ("what's the weather and set a reminder to take an umbrella", ["weather", "reminders"]),
]


def main():
    skills = load_skills()
    chosen = []

    def fake_execute(_skills, name, text, session):
        chosen.append((name, text))
        return {"reply": f"[{name} ran]", "sources": []}

    skill_execution.execute = fake_execute
    retrieval.assemble_context = lambda *a, **k: ""
    model_calls = {"n": 0}
    real_chat = llm.chat

    def counting_chat(*a, **kw):
        model_calls["n"] += 1
        return real_chat(*a, **kw)

    llm.chat = counting_chat

    rows, correct, total_calls, total_ms = [], 0, 0, 0.0
    for prompt, expect in CASES:
        session = {"id": f"eval-{len(rows)}", "history": [], "trace": lambda **kw: None}
        chosen.clear()
        model_calls["n"] = 0
        t0 = time.monotonic()
        out = brain.run(prompt, session, skills)
        ms = (time.monotonic() - t0) * 1000
        names = [c[0] for c in chosen]
        got = ",".join(names) or None
        if expect is None:
            ok = not names and out is not None
        elif isinstance(expect, list):
            ok = all(any(n in (e if isinstance(e, tuple) else (e,)) for n in names) for e in expect)
        else:
            ok = bool(names) and names[0] in (expect if isinstance(expect, tuple) else (expect,))
        correct += ok
        total_calls += model_calls["n"]
        total_ms += ms
        reply = (out or {}).get("reply") or "(no answer: the route would decide)"
        rows.append({"prompt": prompt, "expected": expect, "got": got, "ok": ok, "calls": model_calls["n"], "ms": round(ms)})
        print(f"{'PASS' if ok else 'FAIL'}  {ms:6.0f} ms  calls={model_calls['n']}  got={got!s:12}  {prompt}")
        if not ok:
            print(f"        reply: {reply[:160]!r}")

    n = len(CASES)
    print(f"\nTool/direct choice: {correct}/{n} ({100 * correct / n:.0f}%)")
    print(f"Model calls per turn: {total_calls / n:.2f}")
    print(f"Mean latency: {total_ms / n:.0f} ms (model: {llm.model_for('planner')})")
    print(json.dumps({"correct": correct, "total": n, "calls_per_turn": total_calls / n}))


if __name__ == "__main__":
    main()
