"""Context-window management (Part D4).

History assembled for every LLM call follows three rules:
  1. recent turns verbatim (last N messages, config);
  2. older turns summarized ONCE per session and cached (turn-stamped) — re-summarized
     only when the verbatim window moves past the cache stamp;
  3. the Conversation-State summary (topic / task / constraints) is ALWAYS pinned at the
     top — context anchoring — so no volume of tool output can push the topic out.

Also (D4): thinking blocks are stripped from stored history (Qwen3 <think>...</think>),
and large tool outputs are replaced by short summaries + URL references before they are
kept in history.
"""
from __future__ import annotations

import re

from ishti.core import state as cstate

THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
THINK_OPEN = re.compile(r"<think>.*$", re.S | re.I)          # unterminated (stream cut)
URL = re.compile(r"https?://\S+")


def strip_thinking(text: str) -> str:
    """Qwen3 thinking blocks never enter stored history (only final answers do)."""
    t = THINK.sub("", text or "")
    t = THINK_OPEN.sub("", t)
    return t.strip()


def summarize_tool_output(output: str, max_chars: int = 300) -> str:
    """A large tool output becomes a short summary: the first meaningful line plus any
    links it carried, so history keeps the reference without the flood."""
    t = re.sub(r"\s+", " ", strip_thinking(output or "")).strip()
    urls = URL.findall(t)[:3]
    head = t[:max_chars]
    if len(t) > max_chars:
        cut = head.rfind(" ", max(0, max_chars - 40))
        head = head[:cut if cut > 0 else max_chars].rstrip(" ,;") + " …"
    if urls and len(head) + len(" ".join(urls)) < max_chars + 120:
        head = f"{head} Links: {' '.join(urls)}" if "Links:" not in head else head
    return head


class ContextAssembler:
    """Builds the message list for an LLM call from stored history + state.

    `summarize_fn(list[str]) -> str` is the injectable summarizer (LLM in production,
    deterministic mock in tests). None = older turns are dropped with a marker.
    """

    def __init__(self, summarize_fn=None, verbatim_turns: int = 8,
                 max_messages: int = 40, max_msg_chars: int = 2000):
        self.summarize_fn = summarize_fn
        self.verbatim_turns = verbatim_turns
        self.max_messages = max_messages
        self.max_msg_chars = max_msg_chars

    # ------------------------------------------------------------------ API
    def build(self, history: list[dict], st: cstate.ConversationState,
              state_summary: str | None = None) -> list[dict]:
        """history: [{role, content}]; returns the messages for the LLM with the
        pinned state block first. Does NOT mutate the input."""
        history = history or []
        # normalise + clean on the way IN (thinking blocks already excluded at store time,
        # but clean again here so raw/stored paths behave identically)
        msgs = [{"role": m.get("role"), "content": strip_thinking(str(m.get("content", "")))}
                for m in history if m.get("content")]
        msgs = msgs[-self.max_messages:]

        out: list[dict] = []
        pin = state_summary if state_summary is not None else cstate.ConversationStateManager.summary(st)
        if pin:
            out.append({"role": "system", "content": "CONVERSATION STATE (always applies):\n" + pin})

        verbatim_n = self.verbatim_turns * 2  # a turn = user+assistant
        old, recent = msgs[:-verbatim_n], msgs[-verbatim_n:]
        if old:
            older_summary = self._summarize_old(old)
            if older_summary:
                out.append({"role": "system",
                            "content": "EARLIER CONVERSATION (summarized; details may be dropped):\n" + older_summary})
        out.extend(self._clip(m) for m in recent)
        return out

    # ---------------------------------------------------------------- internals
    def _clip(self, m: dict) -> dict:
        content = m["content"]
        if len(content) > self.max_msg_chars:
            content = summarize_tool_output(content, self.max_msg_chars - 40)
        return {"role": m["role"], "content": content}

    def _summarize_old(self, old: list[dict]) -> str:
        if not old:
            return ""
        if self.summarize_fn is None:
            lines = [f"{m['role']}: {summarize_tool_output(m['content'], 120)}" for m in old[-4:]]
            return "(earlier turns omitted) " + " | ".join(lines)
        try:
            transcript = "\n".join(f"{m['role']}: {m['content'][:400]}" for m in old)
            return (self.summarize_fn(transcript) or "").strip()
        except Exception:
            return ""
