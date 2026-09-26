"""Skill loader. Each skill is a folder: manifest.json + skill.py exposing run(text, session).

run() returns {"reply": str, "sources": [...]} or None ("not mine" - the dispatcher falls
through to the next choice). Optional match(text) -> bool is a deterministic trigger checked
before Laya. A skill that fails to load is marked broken and skipped; nothing else breaks."""
import importlib
import importlib.util
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


class Skill:
    def __init__(self, folder):
        with open(os.path.join(HERE, folder, "manifest.json"), encoding="utf-8") as f:
            self.manifest = json.load(f)
        self.name = self.manifest["name"]
        self.module = None
        self.problem = ""
        missing = [m for m in self.manifest.get("needs", {}).get("python", [])
                   if importlib.util.find_spec(m) is None]
        if missing:
            self.problem = f"missing python packages: {', '.join(missing)}"
        else:
            try:
                self.module = importlib.import_module(f"ishti.skills.{folder}.skill")
            except Exception as exc:
                self.problem = f"{type(exc).__name__}: {exc}"

    @property
    def status(self):
        return "broken" if self.problem else self.manifest.get("status", "working")

    def matches(self, text: str) -> bool:
        fn = getattr(self.module, "match", None)
        return bool(fn and fn(text))

    def run(self, text, session):
        return self.module.run(text, session)

    def info(self):
        return {**self.manifest, "status": self.status, "problem": self.problem}


def load_skills() -> dict:
    return {s.name: s for s in (Skill(d) for d in sorted(os.listdir(HERE))
                                if os.path.isfile(os.path.join(HERE, d, "manifest.json")))}


def trace(session, name, label, status="done", **detail):
    """Report a tool-level action to the live Thinking panel / graph. No-op outside a traced request."""
    fn = session.get("trace") if isinstance(session, dict) else None
    if fn:
        fn(type="tool", name=name, label=label, status=status, **detail)


YES = {"yes", "y", "yeah", "yep", "go ahead", "do it", "ok", "okay", "confirm", "sure", "proceed"}
NO = {"no", "n", "nope", "cancel", "stop", "don't", "dont", "abort", "never mind", "nevermind"}


def answer_of(text):
    """'yes' / 'no' / None for a reply to a skill's yes/no question. None = the owner moved on to
    something else: the skill drops its question and the message is routed normally."""
    t = text.strip().lower().strip(" .!")
    return "yes" if t in YES else "no" if t in NO else None


def ask_first(session, skill, question, action):
    """The one yes/no gate for sensitive (T2) actions: nothing runs until the owner says yes. action() -> reply
    text. The dispatcher runs it on "yes", cancels on "no", and drops it if the owner moves on to something else."""
    session["pending"] = {"skill": skill, "gate": question, "action": action}
    return {"reply": question.rstrip(" ?") + "? (yes/no)", "sources": []}


def log_need(kind, skill, request, detail):
    """Errors and capability gaps go to NEEDS.md (what was asked, what failed, why) instead of being swallowed.
    The same kind+skill+detail is logged once."""
    import time
    path = os.path.join(os.path.dirname(os.path.dirname(HERE)), "md files", "NEEDS.md")
    key = f"[{kind}] {skill}: {detail}"[:200]
    try:
        with open(path, encoding="utf-8") as f:
            if key in f.read():
                return
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n- {time.strftime('%Y-%m-%d %H:%M')} {key} - asked: \"{request[:120]}\"")
    except OSError:
        pass


def ground_dates(text, today=None):
    """Relative dates -> real ones before searching ("this year" -> 2026). Search engines match the
    literal words, so "final this year" otherwise returns whichever year's final ranks highest."""
    import datetime as dt
    import re
    d = today or dt.date.today()
    rules = [(r"\bthis year\b", str(d.year)), (r"\blast year\b", str(d.year - 1)), (r"\bnext year\b", str(d.year + 1)),
             (r"\bthis month\b", d.strftime("%B %Y")), (r"\blast month\b", (d.replace(day=1) - dt.timedelta(days=1)).strftime("%B %Y")),
             (r"\byesterday\b", (d - dt.timedelta(days=1)).strftime("%d %B %Y")), (r"\btoday\b", d.strftime("%d %B %Y"))]
    for rx, value in rules:
        text = re.sub(rx, value, text, flags=re.I)
    return text
