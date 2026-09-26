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
