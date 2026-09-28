"""Self-evaluation and confirmation gate before risky actions (spec 15, 29).

Evaluates whether an action is irreversible, destructive, sensitive, or ambiguous,
and prompts confirmation when needed.
"""
import os
import re
from typing import Any, Dict, List, Optional
from ishti.learning import store

# Irreversible/high-risk actions
IRREVERSIBLE_PATTERNS = [
    re.compile(r"(?:\b|_)(?:delete|remove|rm|drop|erase|overwrite|format|purge)(?:\b|_)", re.I),
    re.compile(r"(?:\b|_)(?:send|email|post|tweet|transfer|pay|purchase|buy)(?:\b|_)", re.I),
    re.compile(r"(?:\b|_)(?:deploy|publish|release|terminate|kill)(?:\b|_)", re.I),
]

# Sensitive operations
SENSITIVE_PATTERNS = [
    re.compile(r"(?:\b|_)(?:password|credential|token|api[_\s]?key|secret|ssh)(?:\b|_)", re.I),
]

def evaluate(action: str, args: Optional[Dict[str, Any]] = None,
             text: str = "", session: Optional[Any] = None) -> Dict[str, Any]:
    """Evaluate whether an action requires explicit human confirmation."""
    act_str = f"{action} {text} {store.dump(args or {})}"
    checks = []
    requires_confirmation = False
    reversible = True
    question = None
    score = 0.9

    # Check irreversibility
    for pat in IRREVERSIBLE_PATTERNS:
        if pat.search(act_str):
            reversible = False
            requires_confirmation = True
            checks.append("action_is_irreversible")
            score -= 0.3
            break

    # Check sensitive data
    for pat in SENSITIVE_PATTERNS:
        if pat.search(act_str):
            requires_confirmation = True
            checks.append("action_involves_sensitive_data")
            score -= 0.4
            break

    if requires_confirmation:
        question = f"This action ({action}) may be irreversible or sensitive. Would you like me to proceed?"

    return {
        "confident": score >= 0.7 and not requires_confirmation,
        "score": max(0.0, min(1.0, score)),
        "checks": checks,
        "reversible": reversible,
        "requires_confirmation": requires_confirmation,
        "question": question
    }

if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp()
    store.set_db_path(os.path.join(tmp, "se.db"))
    
    safe = evaluate("read_file", {"path": "notes.txt"})
    assert safe["confident"] is True and safe["requires_confirmation"] is False
    
    risky = evaluate("delete_folder", {"path": "D:/data"})
    assert risky["reversible"] is False and risky["requires_confirmation"] is True
    assert "irreversible" in risky["question"]
    
    print("[self-check] learning.selfeval OK - risk scoring, reversibility checks, confirmation questions")
