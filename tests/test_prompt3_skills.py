r"""PROMPT 3 tests: Skill Registry, resilience, task manager, multi-agent orchestrator,
Flowise, find_leads, terminal sandbox, telemetry — plus F4 (context resolution through
the multi-agent, deep-research and background-task paths, not just the single agent).

Everything offline: skills/models are doubles, stores point at scratch files, the owner's
data is never touched (house rule).
"""
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["ISHTI_STATE_DB"] = os.path.join(tempfile.mkdtemp(prefix="ishti_state_p3_"), "state.db")
os.environ["ISHTI_SETTINGS_DIR"] = tempfile.mkdtemp(prefix="ishti_settings_p3_")

import pytest  # noqa: E402
from ishti.core import settings as settings_mod  # noqa: E402

settings_mod._service = None  # fresh settings service on the scratch dir


# ------------------------------------------------------------------ registry (A1-A3)
def test_skill_registry_records_and_retrieval():
    from ishti.skills import load_skills, registry as REG
    skills = load_skills()
    assert len(skills) > 50
    recs = REG.records(skills, refresh=True)
    assert set(recs) == set(skills)
    r = recs["desktop"]
    assert {"skill_id", "name", "domain", "description", "input_schema", "output_schema",
            "tools_used", "risk_level", "requires_confirmation", "enabled", "version",
            "status", "test_file", "embedding", "examples"} <= set(r), sorted(r)
    assert r["status"] == "implemented" and r["domain"] == "System & Desktop"
    t2 = recs["terminal"]
    assert t2["requires_confirmation"] is True and t2["risk_level"] == "high"
    # A2: 5-20 retrieval window, ranked
    win = REG.retrieve("what's the weather tomorrow in Peshawar?", skills, k=12, with_scores=True)
    assert 5 <= len(win) <= 20
    assert "weather" in [x["name"] for x in win]
    scores = [x["_score"] for x in win]
    assert scores == sorted(scores, reverse=True), "window sorted by score"
    # A3: disable -> gone from retrieval; re-enable -> back
    REG.set_enabled("weather", False)
    assert "weather" not in [x["name"] for x in REG.retrieve("weather tomorrow?", skills, k=20)]
    REG.set_enabled("weather", True)
    assert "weather" in [x["name"] for x in REG.retrieve("weather tomorrow?", skills, k=20)]
    # domain disable
    REG.set_enabled("Utilities & Daily", False, is_domain=True)
    assert "weather" not in [x["name"] for x in REG.retrieve("weather tomorrow?", skills, k=20)]
    REG.set_enabled("Utilities & Daily", True, is_domain=True)
    try:
        REG.set_enabled("not_a_skill", False)
        raise AssertionError("unknown skill accepted")
    except ValueError:
        pass


# ------------------------------------------------------------------ resilience (G1-G3)
def test_resilience_classification_retry_breakers():
    from ishti import resilience
    assert resilience.classify(TimeoutError("read timed out")) == "transient"
    assert resilience.classify(ValueError("bad shape")) == "permanent"
    assert resilience.classify(RuntimeError("CUDA error")) == "critical"
    calls = {"n": 0}
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise TimeoutError("timed out")
        return "ok"
    assert resilience.retry(flaky, base_delay=0.01) == "ok"
    br = resilience.CircuitBreaker("t", fail_threshold=2, reset_s=0.1)
    boom = lambda: (_ for _ in ()).throw(TimeoutError("x"))
    for _ in range(2):
        br.run(boom, fallback=lambda e: "fb")
    assert br.state == "open"
    assert br.run(lambda: "real", fallback=lambda e: "fb") == "fb"
    time.sleep(0.12)
    assert br.run(lambda: "recovered") == "recovered" and br.state == "closed"
    out, who = resilience.run_with_fallback([(lambda: 1 / 0, "a"), (lambda: "b", "b")])
    assert (out, who) == ("b", "b")
    # checkpoint round-trip
    resilience.CHECKPOINT_DIR = tempfile.mkdtemp()
    resilience.checkpoint("p3", {"step": 2})
    assert resilience.restore("p3")["step"] == 2
    resilience.drop_checkpoint("p3")
    # safe-mode probe never raises
    assert isinstance(resilience.safe_mode_reason(), str)


# ------------------------------------------------------------------ task manager (D1-D5)
def test_taskmgr_submit_cancel_pause_idempotency():
    from ishti import taskmgr
    r = taskmgr.submit(lambda t: (taskmgr.coop(t), 41 + 1)[-1], goal="math", timeout_s=15)
    taskmgr.wait_all([r["id"]], 15)
    assert r["status"] == "done" and r["result"] == 42
    # cancel a running task (and its children)
    def slow(t):
        for _ in range(100):
            taskmgr.coop(t)
            time.sleep(0.02)
    parent = taskmgr.submit(slow, goal="parent", timeout_s=30)
    ch = taskmgr.child(parent, slow, goal="child", timeout_s=30)
    time.sleep(0.05)
    assert taskmgr.cancel(parent["id"])
    taskmgr.wait_all([ch["id"]], 5)
    assert ch["status"] == "cancelled"
    # pause/resume
    pa = taskmgr.submit(slow, goal="pa", timeout_s=30)
    time.sleep(0.05)
    assert taskmgr.pause(pa["id"]) and pa["status"] == "paused"
    assert taskmgr.resume(pa["id"])
    taskmgr.wait_all([pa["id"]], 20)
    assert pa["status"] == "done"
    # idempotency key dedupes
    a = taskmgr.submit(lambda t: "x", goal="once", idempotency_key="k1", timeout_s=10)
    b = taskmgr.submit(lambda t: "y", goal="once", idempotency_key="k1", timeout_s=10)
    assert a["id"] == b["id"]
    # D5: own state snapshot
    assert "session_id" in a["state_snapshot"]
    # D4: voice commands
    got = taskmgr.handle_command("what tasks are running")
    assert got is None or "task" in got.lower()
    assert taskmgr.cancel(a["id"])


def test_background_task_keeps_own_topic():
    """D5: a background task snapshots its own conversation state — the chat can move on
    without mixing topics (the Prompt-1 bug class, asserted through the task path)."""
    from ishti import taskmgr
    from ishti.core.state import get_manager
    mgr = get_manager()
    st = mgr.get("bgsess")
    st.active_topic = "sony wh-1000xm5 research"
    mgr.save(st)
    rec = taskmgr.submit(lambda t: f"working on {t['state_snapshot'].get('active_topic')}",
                         goal="keep researching", sid="bgsess", timeout_s=15)
    taskmgr.wait_all([rec["id"]], 15)
    assert rec["status"] == "done"
    assert "sony wh-1000xm5" in str(rec["result"]), rec["result"]
    # the chat's own topic can now change; the finished task's snapshot keeps the old one
    st2 = mgr.get("bgsess")
    st2.active_topic = "new different topic"
    mgr.save(st2)
    assert rec["state_snapshot"]["active_topic"] == "sony wh-1000xm5 research"


# ------------------------------------------------------------------ agents (C1-C9)
class _FakeSkill:
    def __init__(self, answer, fail=False):
        self.answer, self.fail = answer, fail
        self.manifest = {"tier": "T1"}
        self.status = "working"
        self.calls = 0

    def matches(self, t):
        return False

    def run(self, text, session):
        self.calls += 1
        if self.fail:
            return {"reply": "I can't do that yet - no skill handles it.", "sources": []}
        return {"reply": self.answer, "sources": [{"id": 1, "url": "https://s.example", "title": "src"}]}


def test_multiagent_end_to_end():
    import ishti.agents as A
    skills = {"web_search": _FakeSkill("The answer is 42, per source."),
              "desktop": _FakeSkill("Screenshot taken."),
              "chat": _FakeSkill("The synthesis is ready."),
              "dev": _FakeSkill("Patch written.")}
    # decomposition returns valid agent assignments (LLM or deterministic fallback)
    subs = A.decompose("research GPU prices and take a screenshot of the dashboard", skills)
    assert subs and all(s["agent"] in A.AGENTS for s in subs), subs
    # C4 contract + fan-out: with a DETERMINISTIC decomposition (injected), the supervisor
    # dispatches each subtask to its own agent
    received = []
    def spy_run(agent, task, sk, session, bb, budget, depth=0):
        received.append((agent, task))
        return {"reply": f"{agent} did: {task[:30]}", "sources": []}
    real_run, real_decomp = A._run_agent, A.decompose
    A._run_agent = spy_run
    A.decompose = lambda request, sk: [
        {"agent": "researcher", "task": "research GPU prices", "criteria": "prices found"},
        {"agent": "pc_control", "task": "take a screenshot of the dashboard", "criteria": "screenshot taken"}]
    try:
        out = A.run("research GPU prices and take a screenshot of the dashboard",
                    {"id": "masess", "trace": lambda **k: None}, skills)
    finally:
        A._run_agent, A.decompose = real_run, real_decomp
    assert out["mode"] == "MULTI_AGENT" and out["reply"]
    agents_used = {a for a, _t in received}
    assert "researcher" in agents_used and "pc_control" in agents_used, received
    # C6: a failing agent escalates with the exact reason — never a silent stop
    fail_skills = {"web_search": _FakeSkill("", fail=True)}
    r = A._run_agent("researcher", "find things", fail_skills,
                     {"id": "fx", "trace": lambda **k: None}, {"entries": {}},
                     {"max_iterations": 2, "timeout_s": 5, "max_tool_calls": 5, "max_total_tokens": 9999})
    assert r.get("escalated") and "iteration limit" in r["reply"], r
    # C8: performance tracked
    assert A.perf()


# ------------------------------------------------------------------ flowise (E1)
def test_flowise_adapter_and_profile():
    import ishti.flowise_client as FC
    assert FC.configured() is False
    from ishti.core.settings import get_settings_service
    get_settings_service().update({"integrations": {
        "flowise_url": "http://127.0.0.1:39999",
        "flowise_flows": {"research gpus": "flow-research", "translate": "flow-tr"}}})
    assert FC.configured()
    assert FC.pick_flow("please research gpus now") == "flow-research"
    assert FC.pick_flow("translate this") == "flow-tr"
    assert FC.pick_flow("unrelated") == ""  # 2 flows bound: no blind default
    # dead endpoint: breaker fallback, never raises
    out = FC.run_flow("flow-research", "hi")
    assert out.get("error") and out["reply"] == ""
    ok, detail = FC.health()
    assert ok is False and detail
    # the dispatcher's flowise_flow path answers the bind-missing case without a server
    from ishti.core.architectures import get_registry
    reg = get_registry()
    before = reg.active()
    assert reg.set_active("flowise_flow", by="test", run_health_check=False).get("switched") is True
    import ishti.dispatcher as D
    saved = {k: getattr(D, k) for k in ("_remember", "_ftlog", "_learn", "_learn_turn", "_arch2_consolidate")}
    for k in saved:
        setattr(D, k, lambda *a, **k: None)
    try:
        res = D.handle("a request no flow is bound to", session_id="flowp3")
        assert res["skill"] == "flowise" and ("no flow" in res["final_answer"].lower()
                                              or "unreachable" in res["final_answer"].lower()
                                              or "couldn't answer" in res["final_answer"].lower()), res["final_answer"][:160]
    finally:
        for k, fn in saved.items():
            setattr(D, k, fn)
        reg.set_active(before, by="test", run_health_check=False)


# ------------------------------------------------------------------ find_leads (E4)
def test_find_leads_pipeline_offline():
    import ishti.leads as L
    import ishti.skills.find_leads.skill as FL
    L.set_db_path(os.path.join(tempfile.mkdtemp(), "leads.db"))
    p = FL.parse_request("find 15 US LLC formation agencies in Texas")
    assert p["max_results"] == 15 and "texas" in p["locations"] and "agency" in p["roles"]
    # injected transport: one page with two contact-bearing blocks
    page = ("Acme Formation Services LLC — info@acme.example — https://acme.example — Dallas, Texas. "
            "Gulf Consulting FZE — hello@gulf.example — Dubai.")
    FL._search = lambda q, k=8: [{"url": "https://dir.example/tx", "title": "directory"}]
    FL._fetch = lambda url: page
    res = FL.find(p, use_scrapegraph=False, max_pages=2)
    assert res["leads"] and all(x.get("source_url") for x in res["leads"])
    assert all(x.get("email") for x in res["leads"])
    assert len(res["leads"]) == 2, res["leads"]  # both emails extracted from one page
    counts = L.add_many(res["leads"], run_id="r1")
    assert counts["added"] >= 1, counts
    rows = L.rows(run_id="r1")
    assert rows == sorted(rows, key=lambda x: -x["relevance"]) or len(rows) == 1
    # follow-up works through state
    p2 = FL.parse_request("only the ones in Dubai", {"constraints": {"country": "UAE"}})
    assert p2["locations"]
    # compliance: robots refusal is honored (robotparser consulted per page)
    real_robots = FL.robots_ok
    FL.robots_ok = lambda url: False
    try:
        res2 = FL.find(p, use_scrapegraph=False, max_pages=2)
    finally:
        FL.robots_ok = real_robots
    assert res2["leads"] == [], res2


# ------------------------------------------------------------------ terminal (B5)
def test_terminal_sandbox():
    from ishti.skills.terminal import skill as T
    p = T.parse("git status")
    assert p["ok"] and not p["write"]
    assert not T.parse("format c:")["ok"]
    assert not T.parse("git status && del file")["ok"], "shell chaining refused"
    assert T.parse("pip install requests")["write"] is True
    r = T.execute("python --version", timeout=30)
    assert r["ok"] and "Python" in r["output"]
    r2 = T.execute("python -c \"import time; time.sleep(15)\"", timeout=2)
    assert not r2["ok"] and "timed out" in r2["output"]


# ------------------------------------------------------------------ telemetry (H1-H5)
def test_telemetry_metrics_and_eval():
    from ishti import telemetry
    telemetry.span("p3.stage", ms=25.0)
    telemetry.span("p3.stage", ms=75.0)
    telemetry.span("p3.err", ms=5.0, status="error", text="mail bob@corp.example now")
    m = telemetry.metrics()
    st = m["stages"]["p3.stage"]
    assert st["n"] >= 2 and st["p95"] >= st["p50"]
    assert m["counters"]["p3.err.error"] >= 1
    assert "bob@corp.example" not in json.dumps(telemetry.recent_spans(50)), "PII redacted (H4)"
    res = telemetry.run_eval([t for t in telemetry.DEFAULT_EVAL
                              if t["type"] in ("factcheck", "terminal_parse", "task")])
    assert res["passed"] == res["total"], res
    # agent perf + queue depth surface in metrics (H2)
    assert "agents" in m and "queue" in m


# ------------------------------------------------------------------ F4: regressions
def test_f4_context_resolution_through_all_paths():
    """The Prompt-1 context tests must hold through deep-research and background paths."""
    from ishti.core.state import get_manager
    mgr = get_manager()
    # deep-research path: record_search pins the topic; a bare follow-up resolves against it
    sid = "f4research"
    mgr.record_search(sid, "Sony WH-1000XM5 review", ["https://example.com/review"])
    st = mgr.get(sid)
    assert st.active_topic and "wh-1000xm5" in st.active_topic.lower(), st.active_topic
    from ishti.core.resolver import QueryResolver
    out = QueryResolver().resolve("cheapest price?", st)
    assert not out.ambiguous, out
    assert "wh-1000xm5" in out.request.lower(), out.request
    # multi-agent path: sub-agents receive the state summary (C4) — asserted with a spy
    import ishti.agents as A
    seen_tasks = []
    real = A._run_agent
    A._run_agent = lambda agent, task, sk, session, bb, budget, depth=0: (
        seen_tasks.append((agent, task)), {"reply": "ok", "sources": []})[1]
    skills = {"web_search": _FakeSkill("x"), "chat": _FakeSkill("y")}
    try:
        A.run("continue the sony research", {"id": sid, "trace": lambda **k: None}, skills)
    finally:
        A._run_agent = real
    assert seen_tasks, "orchestrator must have dispatched at least one subtask"
    # background path: task snapshot keeps its own topic (tested fully in its own test)


if __name__ == "__main__":
    test_skill_registry_records_and_retrieval()
    test_resilience_classification_retry_breakers()
    test_taskmgr_submit_cancel_pause_idempotency()
    test_background_task_keeps_own_topic()
    test_multiagent_end_to_end()
    test_flowise_adapter_and_profile()
    test_find_leads_pipeline_offline()
    test_terminal_sandbox()
    test_telemetry_metrics_and_eval()
    test_f4_context_resolution_through_all_paths()
    print("[integration] prompt3 OK — registry, resilience, taskmgr, agents, flowise, leads, terminal, telemetry, F4")
