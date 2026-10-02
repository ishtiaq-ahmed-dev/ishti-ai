"""Observability core (Part H): per-stage latency metrics, token/tool/agent counters,
an on-demand eval harness with stored results, and OpenTelemetry-shaped span records.

H1 tracing: the dispatcher/skills already emit trace events; `span()` records them into a
local rolling store shaped like OTel spans (name, start, end, attributes, status) — export
to a real collector can be added later without touching call sites.
H2 metrics: p50/p95/p99 per stage, token usage, tool success/error rates, agent success
rates, queue depth.
H4 redaction: span attributes go through ishti.logutil's redaction when available.
H5 eval harness: a fixed regression set runs on demand; results stored for comparison.
"""
from __future__ import annotations

import json
import os
import statistics
import threading
import time
from collections import defaultdict, deque

from ishti import ROOT

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


DB_PATH = os.path.join(ROOT, "data", "telemetry.jsonl")
MAX_LINES = 4000                     # rolling retention (H4: policy is configurable)
_lock = threading.Lock()
_spans: deque = deque(maxlen=MAX_LINES)
_stages: dict = defaultdict(lambda: deque(maxlen=500))
_counters: dict = defaultdict(int)
_tokens: dict = defaultdict(int)


def _redact(text: str) -> str:
    try:
        from ishti.core.settings import get_settings_service
        pats = get_settings_service().get().get("privacy", {}).get("redact_patterns") or []
        import re
        for p in pats:
            try:
                text = re.sub(p, "[redacted]", text or "")
            except re.error:
                continue
        return text
    except Exception:
        return text


def span(name: str, ms: float | None = None, status: str = "ok", session: str = "",
         **attrs) -> dict:
    """Record one span (a stage, a tool call, an agent run). ms=None -> auto-detect via
    the returned closer: `with span('x') as s: ...` then s['end']() or use the context form."""
    rec = {"name": name[:60], "status": status, "session": session[:40],
           "start": time.time(), "end": time.time(), "attrs": {}}
    if ms is not None:
        rec["end"] = rec["start"] + ms / 1000.0
        rec["attrs"]["ms"] = round(ms, 1)
    for k, v in attrs.items():
        rec["attrs"][k] = _redact(str(v)[:120]) if isinstance(v, str) else v
    with _lock:
        _spans.append(rec)
    if ms is not None:
        _stage(name, ms, status)
    return rec


class span_timer:
    """Context manager: with span_timer('llm.main', session=sid): do_work()"""

    def __init__(self, name: str, session: str = "", **attrs):
        self.name, self.session, self.attrs = name, session, attrs
        self.t0 = 0.0

    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        ms = (time.perf_counter() - self.t0) * 1000
        span(self.name, ms=ms, status="error" if exc_type else "ok",
             session=self.session, **self.attrs)
        return False


def _stage(name: str, ms: float, status: str):
    with _lock:
        _stages[name].append(ms if status == "ok" else ms)
        _counters[f"{name}.total"] += 1
        _counters[f"{name}.{'ok' if status == 'ok' else 'error'}"] += 1


def count(key: str, n: int = 1):
    with _lock:
        _counters[key] += n


def add_tokens(role: str, n: int):
    with _lock:
        _tokens[role] += max(0, int(n))


def percentile(vals: list, p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = min(len(s) - 1, max(0, round((p / 100.0) * (len(s) - 1))))
    return round(s[idx], 1)


def metrics() -> dict:
    """H2 snapshot: per-stage latency percentiles, counters, tokens, queue depth."""
    with _lock:
        stages = {name: {"p50": percentile(list(v), 50), "p95": percentile(list(v), 95),
                         "p99": percentile(list(v), 99), "n": len(v)}
                  for name, v in _stages.items()}
        counters = dict(_counters)
        tokens = dict(_tokens)
    agents = {}
    try:
        from ishti.agents import perf
        agents = perf()
    except Exception as _exc:
        swallowed('ishti/telemetry.py:metrics', _exc)
    queue = {}
    try:
        from ishti import taskmgr
        queue = taskmgr.stats()
    except Exception as _exc:
        swallowed('ishti/telemetry.py:metrics', _exc)
    try:
        import psutil
        sys = {"cpu_pct": psutil.cpu_percent(interval=None),
               "ram_pct": psutil.virtual_memory().percent}
    except Exception:
        sys = {}
    return {"stages": stages, "counters": counters, "tokens": tokens,
            "agents": agents, "queue": queue, "system": sys}


def recent_spans(limit: int = 100, name: str = "", status: str = "") -> list[dict]:
    with _lock:
        rows = list(_spans)
    if name:
        rows = [r for r in rows if name in r["name"]]
    if status:
        rows = [r for r in rows if r["status"] == status]
    return rows[-limit:][::-1]


def errors(limit: int = 50) -> list[dict]:
    return [r for r in recent_spans(limit * 3) if r["status"] == "error"][:limit]


# ------------------------------------------------------------------ H5: eval harness
EVAL_SET_PATH = os.path.join(ROOT, "data", "eval", "prompt3_regression.json")
DEFAULT_EVAL = [
    {"name": "memory_roundtrip", "type": "store", "do": "remember", "ask": "where is the ishti project?", "expect": ["D:\\Projects\\ISHTI"]},
    {"name": "math_reflex", "type": "reflex", "text": "what is 17 times 23? reply with just the number", "expect": ["391"]},
    {"name": "router_weather", "type": "route", "text": "what's the weather tomorrow?", "expect_skill": "weather"},
    {"name": "router_research", "type": "route", "text": "deep research the internet about GPU prices", "expect_skill": "deep_research"},
    {"name": "router_desktop", "type": "route", "text": "take a screenshot", "expect_skill": "desktop"},
    {"name": "multiagent_decompose", "type": "agents", "text": "research the latest GPU prices and take a screenshot of the dashboard", "expect_agents": 2},
    {"name": "factcheck_supported", "type": "factcheck", "claim": "The battery lasts 30 hours.",
     "hits": [{"url": "https://a.example", "snippet": "battery life is 30 hours"},
              {"url": "https://b.example", "snippet": "rated 30 hours"}], "expect_verdict": "supported"},
    {"name": "factcheck_unclear_no_sources", "type": "factcheck", "claim": "The battery lasts 30 hours.",
     "hits": [], "expect_verdict": "unclear"},
    {"name": "taskmgr_submit", "type": "task", "goal": "eval probe", "expect_status": "done"},
    {"name": "terminal_parse", "type": "terminal_parse", "cmd": "git status", "expect_ok": True},
    {"name": "terminal_deny", "type": "terminal_parse", "cmd": "format c:", "expect_ok": False},
]


def _ensure_eval_set() -> list[dict]:
    os.makedirs(os.path.dirname(EVAL_SET_PATH), exist_ok=True)
    if not os.path.isfile(EVAL_SET_PATH):
        with open(EVAL_SET_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_EVAL, f, indent=1)
    with open(EVAL_SET_PATH, encoding="utf-8") as f:
        return json.load(f)


def run_eval(tests: list | None = None) -> dict:
    """H5: run the regression set on demand. LLM-dependent paths stay out (this is the
    offline harness); live runs add `python -m ishti.check` on top."""
    tests = tests if tests is not None else _ensure_eval_set()
    results = []
    for t in tests:
        ok, detail = False, ""
        try:
            kind = t.get("type")
            if kind == "route":
                from ishti.skills import load_skills
                from ishti.router import route
                skills = load_skills()
                dec = route(t["text"], skills)
                ok = dec.get("skill") == t.get("expect_skill")
                detail = f"routed to {dec.get('skill')}"
            elif kind == "agents":
                import ishti.agents as A
                subs = A.decompose(t["text"], {"web_search": 1, "desktop": 1, "deep_research": 1,
                                               "apps": 1, "chat": 1, "dev": 1, "browser": 1})
                ok = len(subs) >= int(t.get("expect_agents", 1))
                detail = f"{len(subs)} subtask(s)"
            elif kind == "factcheck":
                import orchestrator.factcheck as F
                v = F.check_claim(t["claim"], search_fn=lambda q, k=6: t.get("hits") or [])
                ok = v["verdict"] == t.get("expect_verdict")
                detail = v["verdict"]
            elif kind == "task":
                from ishti import taskmgr
                rec = taskmgr.submit(lambda tt: "ok", goal=t.get("goal", "eval"), timeout_s=30)
                taskmgr.wait_all([rec["id"]], 20)
                ok = rec["status"] == t.get("expect_status", "done")
                detail = rec["status"]
            elif kind == "terminal_parse":
                from ishti.skills.terminal import skill as T
                p = T.parse(t["cmd"])
                ok = p["ok"] == bool(t.get("expect_ok"))
                detail = p.get("reason", p.get("exe", ""))
            elif kind == "reflex":
                from ishti import reflex
                ans = reflex.answer(t["text"])
                ok = bool(ans) and any(e.lower() in ans[0].lower() for e in t.get("expect", []))
                detail = (ans[0][:60] if ans else "no reflex")
            elif kind == "store":
                from ishti.facts import Facts
                import tempfile
                f = Facts(os.path.join(tempfile.mkdtemp(), "eval.db"))
                f.set("project", "ISHTI", "D:\\Projects\\ISHTI")
                ok = "D:\\Projects\\ISHTI" in f.get("project", "ISHTI")
                detail = "facts round-trip"
        except Exception as exc:
            ok, detail = False, f"{type(exc).__name__}: {str(exc)[:80]}"
        results.append({"name": t.get("name"), "ok": ok, "detail": detail})
    score = sum(1 for r in results if r["ok"]) / max(1, len(results))
    out = {"score": round(score, 3), "passed": sum(1 for r in results if r["ok"]),
           "total": len(results), "results": results, "at": time.time()}
    os.makedirs(os.path.dirname(EVAL_SET_PATH), exist_ok=True)
    with open(os.path.join(ROOT, "data", "eval",
                           f"eval-{time.strftime('%Y%m%d_%H%M')}.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    return out


def persist():
    """Append current spans to the rolling JSONL store (called by the health loop)."""
    with _lock:
        rows = list(_spans)[-500:]
    try:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        with open(DB_PATH, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        # retention: trim the file when it grows past MAX_LINES
        with open(DB_PATH, encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > MAX_LINES:
            with open(DB_PATH, "w", encoding="utf-8") as f:
                f.writelines(lines[-MAX_LINES:])
    except OSError as _exc:
        swallowed('ishti/telemetry.py:persist', _exc)


if __name__ == "__main__":
    # spans + metrics
    span("stt", ms=120.5, session="s1")
    span("router", ms=0.4, session="s1")
    with span_timer("llm.main", session="s1"):
        _ = sum(range(1000))
    span("tool.call", ms=30, status="error", session="s1", text="email me at a@b.com")
    m = metrics()
    assert "stt" in m["stages"] and m["stages"]["stt"]["n"] >= 1, m["stages"].keys()
    assert m["stages"]["stt"]["p50"] >= 120, m["stages"]["stt"]
    assert m["counters"].get("tool.call.error", 0) >= 1, m["counters"]
    assert m["counters"].get("router.ok", 0) >= 1
    # redaction applied to span attrs
    sp = recent_spans(name="tool.call")[0]
    assert "a@b.com" not in json.dumps(sp), sp
    # errors view
    assert errors() and errors()[0]["name"] == "tool.call"
    # eval harness (offline paths only)
    res = run_eval([t for t in DEFAULT_EVAL if t["type"] in ("factcheck", "task", "terminal_parse")])
    assert res["passed"] == res["total"], res
    assert res["score"] == 1.0
    # persistence round-trip
    persist()
    assert os.path.isfile(DB_PATH)
    print(f"[self-check] ishti.telemetry OK — spans, redaction, percentiles, errors, "
          f"eval {res['passed']}/{res['total']}, metrics stages {len(m['stages'])}")
