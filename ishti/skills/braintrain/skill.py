"""Braintrain skill: the conversational front of orchestrator/finetune.py (Part 3).

"brain training status" -> examples logged, readiness, missing deps
"export my training data" -> curated JSONL (redaction + dedup + balance applied)
"fine-tune your brain" / "train on my data" -> the QLoRA job (idle-hours job; asks
before a long GPU run, since ISHTI's own models would be evicted while it trains)
"evaluate the fine-tune" / "promote the adapter" / "roll back the adapter" -> the gate.
Everything heavy lives in orchestrator/finetune.py; this skill only talks.
"""
import json
import os
import re
import sys

if __package__ in (None, ""):  # run directly: python ishti/skills/braintrain/skill.py
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from ishti.skills import ask_first, trace

I = re.I
STATUS = re.compile(r"\b(?:brain|fine[-\s]?tun\w*|training)\s+(?:training\s+)?(?:status|progress|stats?|readiness)\b"
                    r"|how\s+(?:many|much)\s+(?:training\s+)?(?:examples?|data)\b", I)
EXPORT = re.compile(r"\bexport\b.*\b(?:training|dataset|fine[-\s]?tun)\w*|\b(?:training|brain)\s+data\s+export\b", I)
TRAIN = re.compile(r"\b(?:fine[-\s]?tune|train)\b.*\b(?:brain|yourself|my\s+data|on\s+my\s+data|adapter)\b|"
                   r"\bbrain\s+training\s+(?:job|run|start)\b", I)
# The bare-word form ("...gate") matched any sentence containing the word - "jot down the gate code",
# "open the gate" - and routed it to fine-tune evaluation. Require a verb or "regression" before eval/gate.
EVAL = re.compile(r"\bevaluat\w*\b.*\b(?:fine[-\s]?tun\w*|adapter|candidate|brain)\b|"
                  r"\b(?:run|check|show|start|evaluate|eval)\s+(?:the\s+|a\s+)?(?:regression\s+)?(?:eval|gate)\b|"
                  r"\bregression\s+(?:eval|gate)\b", I)
PROMOTE = re.compile(r"\bpromote\b.*\b(?:adapter|fine[-\s]?tun\w*|candidate)\b", I)
ROLLBACK = re.compile(r"\broll\s?back\b.*\b(?:adapter|fine[-\s]?tun\w*|brain)\b|\brestore\s+the\s+previous\s+adapter\b", I)


def match(text):
    return bool(STATUS.search(text) or EXPORT.search(text) or TRAIN.search(text)
                or EVAL.search(text) or PROMOTE.search(text) or ROLLBACK.search(text))


def run(text, session):
    from orchestrator import finetune
    from ishti import ftlog
    if STATUS.search(text):
        s = finetune.status()
        by_skill = ", ".join(f"{k}: {v}" for k, v in list(s.get("top_skills", {}).items())[:4])
        lines = [f"Training examples logged: {s['examples_logged']} of {s['min_needed']} needed"
                 + (" - enough to train." if s["ready"] else " - keep using me, collection is automatic.")]
        lines.append("Training deps: " + ("all present" if not s["training_deps_missing"]
                                          else "missing " + ", ".join(s["training_deps_missing"])))
        lines.append("Adapter in production: " + ("yes" if s["current_adapter"] else
                                                  "none (the stock model is serving)"))
        if by_skill:
            lines.append("Most common domains: " + by_skill)
        return {"reply": "\n".join(lines), "sources": []}
    if EXPORT.search(text):
        path, n = ftlog.export()
        return {"reply": f"Exported {n} curated examples to {path} (secrets redacted, deduped, "
                         "balanced across skills).", "sources": []}
    if TRAIN.search(text):
        ok, note = finetune.train()  # refuses politely when data/deps aren't there
        if ok:
            def _do():
                return f"Training finished: {note}. Say \"evaluate the fine-tune\" next."
            return ask_first(session, "braintrain",
                             "Training evicts ISHTI's models from the GPU for a while - start the "
                             "fine-tune now? (best in idle hours)", _do)
        return {"reply": f"Can't train yet: {note}", "sources": []}
    if EVAL.search(text):
        from orchestrator.finetune import CURRENT
        adapter = CURRENT if os.path.isdir(CURRENT) else None
        score, detail = finetune.run_eval(adapter)
        misses = [d["request"] for d in detail if not d["ok"]]
        lines = [f"Eval score: {score:.0%} ({'current adapter' if adapter else 'stock model'})."]
        if misses:
            lines.append("Missed: " + "; ".join(m[:60] for m in misses[:3]))
        return {"reply": "\n".join(lines), "sources": []}
    if PROMOTE.search(text):
        stamp_dirs = sorted((d for d in os.listdir(finetune.ADAPTERS)
                             if not d.startswith(("archive_", "rollback")) and d != "current"),
                            reverse=True) if os.path.isdir(finetune.ADAPTERS) else []
        if not stamp_dirs:
            return {"reply": "No trained candidate found - \"fine-tune your brain\" first.", "sources": []}
        cand = os.path.join(finetune.ADAPTERS, stamp_dirs[0])
        trace(session, "finetune", f"evaluating candidate {stamp_dirs[0]} before promoting", status="start")
        good, note = finetune.promote(cand)
        trace(session, "finetune", note[:120], status="done" if good else "error")
        return {"reply": ("Promoted. " if good else "Not promoted. ") + note, "sources": []}
    if ROLLBACK.search(text):
        good, note = finetune.rollback()
        return {"reply": ("Rolled back. " if good else "Can't roll back: ") + note, "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    import ishti.facts as F
    F._facts = F.Facts(os.path.join(tempfile.mkdtemp(), "t.db"))
    assert match("brain training status")
    assert match("how many training examples do you have")
    assert match("export my training data")
    assert match("fine-tune your brain on my data")
    assert match("evaluate the fine-tune")
    assert match("promote the adapter")
    assert match("roll back the adapter")
    assert not match("what's the weather tomorrow") and not match("open chrome")
    r = run("brain training status", {})
    assert "Training examples logged" in r["reply"], r
    r = run("fine-tune your brain on my data", {})
    assert "Can't train yet" in r["reply"], r  # no data in the temp DB: the polite refusal path
    r = run("export my training data", {})
    assert "Exported" in r["reply"], r
    r = run("evaluate the fine-tune", {})
    assert "Eval score" in r["reply"], r
    print("[self-check] braintrain OK - status, export, guarded train, eval, promote/rollback routing")
