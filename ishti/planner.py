"""Multi-skill plans: "check the latest Ollama version and remember it" -> web_search, then memory.
Only requests with a connector followed by an action verb pay for the LLM planning call; the
planner may still answer with one step, in which case normal routing takes over."""
import json
import re

from ishti import llm

# ponytail: connector+verb regex as the gate (Laya zero-shot scored 0/4 on multi-task detection).
# Upgrade path: a Laya noul "several tasks?" once the fine-tuned checkpoint exists (NEEDS.md #1).
GATE = re.compile(r"\b(?:and|then|also|after\s+that|afterwards)\s+(?:then\s+|also\s+)?(?:please\s+)?"
                  r"(?:tell|open|find|search|check|remember|save|note|show|get|launch|start|summari[sz]e|read|"
                  r"write|look|give|set|forget|research|play|compare|translate|what|what's|how|where|when|who)\b",
                  re.I)
MAX_STEPS = 4
PREV = "{prev}"


def candidate(text):
    return len(text.split()) >= 6 and bool(GATE.search(text))


def plan(text, skills):
    """[{"skill", "request"}, ...] with >= 2 steps, or None (a single skill can do it)."""
    live = {n: s.manifest["route"] for n, s in skills.items() if s.status != "broken"}
    out = llm.ask(
        "Split the owner's request into steps, one skill per step. Skills:\n"
        + "\n".join(f"- {n}: {r}" for n, r in live.items())
        + '\n\nReturn JSON {"steps": [{"skill": "<name>", "request": "<self-contained request for that skill>"}]}. '
        f"Use a single step if one skill can do the whole request; a browser step includes reading and acting on the "
        f"page it opens. At most {MAX_STEPS} steps. Keep the owner's wording. "
        f"Where a step needs the previous step's answer, write {PREV} in its request.\n\nRequest: {text}",
        system=None, temperature=0, max_tokens=300, response_format={"type": "json_object"})
    try:
        steps = json.loads(out).get("steps") or []
    except (ValueError, AttributeError):
        return None
    steps = [s for s in steps if isinstance(s, dict) and s.get("skill") in live and str(s.get("request", "")).strip()]
    return steps[:MAX_STEPS] if len(steps) >= 2 else None


def run(steps, session, run_step):
    """run_step(text, decision) -> skill output. Stops early if a step waits for the owner (a pending
    confirmation), so nothing after an unconfirmed action runs."""
    replies, sources, prev = [], [], ""
    for i, step in enumerate(steps, 1):
        text = step["request"].replace(PREV, prev[:400])
        out = run_step(text, {"skill": step["skill"], "confidence": 1.0, "source": "plan", "step": i})
        replies.append(out["reply"])
        sources += out.get("sources", [])
        prev = out["reply"]
        if session.get("pending"):
            if i < len(steps):
                replies.append(f"(Waiting for your answer before the remaining {len(steps) - i} step(s).)")
            break
    return {"reply": "\n\n".join(r for r in replies if r), "sources": sources, "mode": "PLAN", "steps": steps}


if __name__ == "__main__":
    assert candidate("check the latest version of ollama and remember it")
    assert candidate("find the file ishti_logo and then open task manager")
    assert not candidate("tell me the pros and cons of rust")
    assert not candidate("what is the difference between TCP and UDP")
    calls = []
    fake = lambda text, dec: calls.append((dec["skill"], text)) or {"reply": f"R{len(calls)}", "sources": []}
    out = run([{"skill": "web_search", "request": "latest ollama version"},
               {"skill": "memory", "request": f"remember ollama latest is {PREV}"}], {}, fake)
    assert calls[1] == ("memory", "remember ollama latest is R1") and out["reply"] == "R1\n\nR2", (calls, out)
    s = {"pending": None}
    stop = lambda text, dec: (s.update(pending={"skill": "browser"}) or {"reply": "Go ahead?", "sources": []})
    assert "Waiting" in run([{"skill": "browser", "request": "a"}, {"skill": "chat", "request": "b"}], s, stop)["reply"]
    print("[self-check] planner OK - gate, {prev} chaining, stops at a pending confirmation")
