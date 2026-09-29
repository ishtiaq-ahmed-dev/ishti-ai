"""Tool execution stage (B4) + confirmation policy (E3/Rule 6) + validation stage (B5).

- ToolDispatcher: runs a tool callable with timeout, retries and a STRUCTURED result per
  call: {tool, args, status, output, error, duration_ms}. Async at the dispatcher level
  (executor threads); the UI is never blocked.
- ConfirmationPolicy (E3): config-driven — high-impact actions require confirmation
  BEFORE execution (Rule 6). Verbs come from settings.confirmation.verbs; skills carry
  manifest tiers; both are honoured.
- ValidationStage (B5): judges tool results (did it succeed? relevant?) and the final
  answer (is it about the resolved topic? — D5). On failure: retry / re-plan / say clearly
  what failed. `llm_fn` injectable; deterministic floors run without any model.
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, Callable

from ishti.core.pipeline import Tracer

# ------------------------------------------------------------------ E3: confirmation policy

DEFAULT_CONFIRM_VERBS = ["send", "delete", "remove", "pay", "buy", "order", "purchase", "post",
                         "publish", "submit", "install", "uninstall", "shut down", "restart",
                         "transfer", "forward", "empty", "log in", "sign in", "kill"]


class ConfirmationPolicy:
    """Config-driven gate: returns a decision for a candidate action —
    {required: bool, reason: str}. High-impact verb OR T2-tier skill => confirm."""

    def __init__(self, verbs: list[str] | None = None, tiers: dict | None = None,
                 always_confirm_tiers: tuple = ("T2",), require_all: bool = False):
        self.verbs = [v.lower() for v in (verbs or DEFAULT_CONFIRM_VERBS)]
        self.tiers = tiers or {}
        self.always_confirm_tiers = always_confirm_tiers
        self.require_all = require_all
        self._rx = re.compile(r"\b(?:%s)\b" % "|".join(re.escape(v) for v in self.verbs), re.I) if self.verbs else None

    def check(self, action_text: str, tool_name: str = "") -> dict:
        if self._rx and self._rx.search(action_text or ""):
            return {"required": True, "reason": f"high-impact action ({', '.join(w for w in self.verbs if re.search(w, action_text or '', re.I))[:60]})"}
        tier = (self.tiers or {}).get(tool_name, "")
        if tier in self.always_confirm_tiers:
            return {"required": True, "reason": f"{tool_name} is a {tier} (confirm-first) skill"}
        return {"required": False, "reason": "read-only or low-impact"}


# ------------------------------------------------------------------ B4: tool dispatcher

ToolFn = Callable[[str, Any], dict | None]


class ToolDispatcher:
    """Runs tools with timeout, retries and structured results. One shared ThreadPool so
    parallel tool calls don't spawn unbounded threads."""

    def __init__(self, timeout_s: float = 60.0, retries: int = 0, max_workers: int = 4,
                 policy: ConfirmationPolicy | None = None, tracer: Tracer | None = None):
        self.timeout_s = timeout_s
        self.retries = retries
        self.policy = policy or ConfirmationPolicy()
        self.tracer = tracer or Tracer()
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="tool")

    def run(self, name: str, args: str, fn: ToolFn, session: Any = None,
            confirm_action: Callable[[str, Callable[[], dict]], dict] | None = None) -> dict:
        """Run one tool. When the policy requires confirmation, `confirm_action(question,
        do_run)` is called instead (arch1 wires ask_first here; tests capture it).
        Returns {tool, args, status, output, error, duration_ms}."""
        t0 = time.perf_counter()
        decision = self.policy.check(f"{args or ''}", name)
        if decision["required"] and confirm_action is not None:
            self.tracer.emit("tool", "start", name, f"confirm: {(args or '')[:80]}")
            out = confirm_action(f"Run {name} for: {(args or '')[:120]}", lambda: self._run_once(name, args, fn, session))
            out.setdefault("tool", name)
            out.setdefault("args", args)
            out["duration_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            return out
        return self._run_traced(name, args, fn, session)

    def _run_traced(self, name, args, fn, session) -> dict:
        span = self.tracer.span("tool", f"{name}: {(args or '')[:80]}")
        attempts = self.retries + 1
        last: dict = {}
        for attempt in range(attempts):
            last = self._run_once(name, args, fn, session)
            if last["status"] == "ok":
                break
            if attempt < attempts - 1:
                self.tracer.emit("tool", "retry", name, f"attempt {attempt + 2}/{attempts}")
        span.done(f"{last['status']} in {last['duration_ms']}ms")
        return last

    def _run_once(self, name, args, fn, session) -> dict:
        t0 = time.perf_counter()
        fut = self._pool.submit(fn, args, session)
        try:
            result = fut.result(timeout=self.timeout_s)
        except FutureTimeout:
            fut.cancel()
            return {"tool": name, "args": args, "status": "timeout", "output": "",
                    "error": f"{name} timed out after {self.timeout_s:.0f}s", "duration_ms": round((time.perf_counter() - t0) * 1000, 1)}
        except Exception as exc:  # containment: the turn carries on
            return {"tool": name, "args": args, "status": "error", "output": "",
                    "error": f"{type(exc).__name__}: {str(exc)[:200]}", "duration_ms": round((time.perf_counter() - t0) * 1000, 1)}
        reply = ""
        if isinstance(result, dict):
            reply = str(result.get("reply", result.get("output", "")))
        elif result is not None:
            reply = str(result)
        return {"tool": name, "args": args, "status": "ok", "output": reply,
                "error": "", "duration_ms": round((time.perf_counter() - t0) * 1000, 1)}

    def run_parallel(self, calls: list[tuple[str, str, ToolFn, Any]],
                     confirm_action=None) -> list[dict]:
        """Several independent tools at once (each still policy-checked)."""
        with ThreadPoolExecutor(max_workers=min(len(calls), 4)) as pool:
            futs = [pool.submit(self.run, n, a, f, s, confirm_action) for n, a, f, s in calls]
            return [f.result() for f in futs]

    def shutdown(self):
        self._pool.shutdown(wait=False, cancel_futures=True)


# ------------------------------------------------------------------ B5: validation

FELL_SHORT_HINTS = re.compile(
    r"\b(?:couldn'?t|could not|can'?t|cannot|unable to|not able to|didn'?t (?:find|open|get|reach)|"
    r"no (?:\w+ )?(?:results?|matching|information)|not found|i don'?t (?:know|have))\b", re.I)


class ValidationStage:
    """Validates tool results and the final answer against the RESOLVED request (D5).

    Deterministic floors run with no model; `llm_fn(prompt) -> str` adds the model judge
    when provided. Tool verdict: {"ok": bool, "missing": str}. Answer verdict:
    {"ok": bool, "on_topic": bool, "reason": str}."""

    def __init__(self, llm_fn=None, tracer: Tracer | None = None):
        self.llm_fn = llm_fn
        self.tracer = tracer or Tracer()

    # ---- tool results ------------------------------------------------------
    def validate_tool_result(self, request: str, tool: str, result: dict) -> dict:
        output = str(result.get("output", ""))
        if result.get("status") != "ok":
            return {"ok": False, "missing": result.get("error") or f"{tool} did not run"}
        if not output.strip():
            return {"ok": False, "missing": f"{tool} returned nothing"}
        if FELL_SHORT_HINTS.search(output):
            return {"ok": False, "missing": "the tool said it could not find or do it"}
        if self.llm_fn:
            span = self.tracer.span("validate", f"tool {tool}")
            try:
                raw = self.llm_fn(
                    f"The user asked: {request[:400]}\nThe tool \"{tool}\" returned:\n{output[:1200]}\n\n"
                    "Did the tool's output actually accomplish the request? Reply ONLY JSON: "
                    '{"ok": true/false, "missing": "<what is missing, empty if none>"}')
                import json
                d = json.loads(re.search(r"\{.*\}", raw, re.S).group(0)) if raw else {}
                ok = bool(d.get("ok", True))
                span.done("verified" if ok else f"gaps: {str(d.get('missing'))[:60]}")
                return {"ok": ok, "missing": str(d.get("missing", ""))}
            except Exception:
                span.error("validator JSON failed — defaulting to ok")
                return {"ok": True, "missing": ""}
        return {"ok": True, "missing": ""}

    # ---- final answer (D5) ---------------------------------------------------
    def validate_answer(self, resolved_request: str, answer: str,
                        topic: str = "", keywords: list[str] | None = None) -> dict:
        """Is the answer about the resolved topic? Deterministic floor: the topic's
        distinctive tokens (or the explicit keywords) must appear unless the answer
        declines. The model judge (when wired) refines this."""
        answer_l = (answer or "").lower()
        declined = bool(FELL_SHORT_HINTS.search(answer_l))
        if declined:
            return {"ok": False, "on_topic": False, "reason": "the answer says it could not do it"}
        toks = [t for t in (keywords or _distinctive(topic)) if t]
        if topic and toks and not any(t.lower() in answer_l for t in toks):
            return {"ok": False, "on_topic": False,
                    "reason": f"the answer never mentions the topic ({topic})"}
        if self.llm_fn and resolved_request:
            try:
                verdict = (self.llm_fn(
                    f"Resolved request: {resolved_request[:400]}\nAnswer: {answer[:1200]}\n\n"
                    "Does the answer address the resolved request's topic? Reply yes or no.") or "").lower()
                if verdict.startswith("no"):
                    return {"ok": False, "on_topic": False, "reason": "judge: off-topic answer"}
            except Exception:
                pass
        return {"ok": True, "on_topic": True, "reason": ""}


def _distinctive(topic: str) -> list[str]:
    """Tokens of the topic that are distinctive enough to prove an answer is about it:
    product codes and brands, else the two longest words."""
    toks = re.findall(r"[A-Za-z0-9][A-Za-z0-9-]*", topic or "")
    strong = [t for t in toks if len(t) >= 3 and (re.search(r"\d", t) or t.lower() in
               ("sony", "apple", "iphone", "samsung", "galaxy", "pixel", "nvidia", "bose", "rtx", "macbook"))]
    if strong:
        return strong
    rest = sorted((t for t in toks if len(t) >= 4), key=len, reverse=True)
    return rest[:2]
