"""Shared test mocks: a deterministic LLM and deterministic tools so the whole suite
runs fast, offline and identically on any machine (E4)."""
from __future__ import annotations


class MockLLM:
    """Scripted LLM: returns the next scripted reply per call, or the last one forever.
    Records every prompt for assertions (what the resolver / validators actually asked)."""

    def __init__(self, replies: list[str] | None = None):
        self.replies = list(replies or [])
        self.i = 0
        self.prompts: list[str] = []

    def __call__(self, prompt: str, **kw) -> str:
        self.prompts.append(prompt)
        if self.i < len(self.replies):
            r = self.replies[self.i]
            self.i += 1
            return r
        return self.replies[-1] if self.replies else ""


class MockSearchTool:
    """Records the queries it was asked; returns a canned result. Used to prove that
    search calls carry the resolved topic (T1) and that sub-agents get it too (T6)."""

    def __init__(self, reply: str = "FOUND: the answer with current prices"):
        self.queries: list[str] = []
        self.reply = reply

    def __call__(self, args: str, session=None) -> dict:
        self.queries.append(args)
        return {"reply": self.reply, "sources": [{"id": 1, "url": "https://example.com/price", "title": "Example"}]}


class FakeSkill:
    """Minimal stand-in for ishti.skills.Skill."""

    def __init__(self, name: str, fn=None, tier: str = "T1"):
        self.name = name
        self.manifest = {"name": name, "description": f"{name} fake skill", "tier": tier}
        self.status = "working"
        self._fn = fn

    def run(self, text, session):
        if self._fn:
            return self._fn(text, session)
        return {"reply": f"{self.name} ran: {text[:40]}", "sources": []}


class TracingSession(dict):
    """A minimal session (the dispatcher's View shape) that captures trace events."""

    def __init__(self, sid: str = "t"):
        super().__init__({"id": sid, "trace": self._trace, "on_token": None,
                          "history": [], "pending": None, "state_summary": ""})
        self.events: list[dict] = []

    def _trace(self, **ev):
        self.events.append(ev)
