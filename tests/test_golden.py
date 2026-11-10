"""Golden conversations: owner messages from the live scenario rounds, replayed through dispatcher.handle with the model
stubbed - the regression gate for every bug those rounds found. Offline and deterministic: every socket refused,
in-memory keyring, scratch stores only (conftest), a scripted model whose unscripted calls return a sentinel.

Scenario files: tests/golden/*.yaml. Each turn may assert skill, route source, llm_calls (max), reply_has / reply_any /
reply_not / reply_re, prompt_not (never inside a model prompt), asks_confirmation, asks_question and store
side effects (delta / has / lacks; "a&b" = all parts in one row). real_t2: true runs that scenario without test mode
(its T2 skill really runs, against the stubbed keyring). A new bug gets a scenario that fails before the fix."""
import errno
import glob
import os
import re
import socket
import sqlite3
from types import SimpleNamespace

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
SCENARIOS = [sc for path in sorted(glob.glob(os.path.join(HERE, "golden", "*.yaml")))
             for sc in yaml.safe_load(open(path, encoding="utf-8")) or []]
SENTINEL = "<<UNSCRIPTED-MODEL-CALL>>"


def _stores():
    from ishti import CONFIG
    from ishti.core import state
    from ishti.facts import get_facts
    d = os.path.dirname(CONFIG["memory"]["db_path"])
    real = os.path.normcase(os.path.join(os.path.dirname(HERE), "data"))
    assert not os.path.normcase(d).startswith(real), "golden scenarios must never touch the owner's data folder"
    facts_db = get_facts().db  # conftest swaps the facts store per test
    return {
        "reminders": (facts_db, "SELECT text, due FROM reminders WHERE done=0"),
        "facts": (facts_db, "SELECT key, attr, value FROM facts"),
        "checklist": (os.path.join(d, "checklists.db"), "SELECT list, item, done FROM items"),
        "notes": (os.path.join(d, "notes.db"), "SELECT body FROM notes"),
        "audit": (os.path.join(d, "audit.db"), "SELECT skill, action, detail FROM actions"),
        "memories": (os.path.join(d, "arch2_memory.db"), "SELECT content, status FROM memories"),
        "relationships": (os.path.join(d, "rgraph.db"), "SELECT a.name, e.rel, b.name FROM edges e "
                                                         "JOIN nodes a ON a.id=e.src JOIN nodes b ON b.id=e.dst"),
        "state": (state._db_path() if hasattr(state, "_db_path") else os.path.join(d, "convo_state.db"),
                  "SELECT * FROM convo_state"),
    }


def _rows(spec):
    where, query = spec
    try:
        if isinstance(where, sqlite3.Connection):
            return [" | ".join(str(v) for v in r).lower() for r in where.execute(query)]
        if not os.path.exists(where):
            return []
        with sqlite3.connect(where) as c:
            return [" | ".join(str(v) for v in r).lower() for r in c.execute(query)]
    except sqlite3.Error:
        return []


@pytest.fixture
def golden(monkeypatch):
    from ishti import CONFIG, llm
    import keyring
    import ishti.dispatcher as D
    script, seen, vault = [], [], {}

    def stub(messages, role="planner", system=None, **_kw):
        seen.append(" ".join([str(system or "")] + [str(m.get("content", "")) for m in messages]))
        return SimpleNamespace(content=script.pop(0) if script else SENTINEL, tool_calls=None)

    def refuse(_sock, _addr):
        raise ConnectionRefusedError(errno.ECONNREFUSED, "golden: network blocked")
    monkeypatch.setattr(llm, "_chat", stub)
    monkeypatch.setattr(D, "enable_skill", lambda _name: False)  # a "yes" must never rewrite a manifest in the repo
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setitem(CONFIG.setdefault("memory", {}), "embedding_backend", "lexical")
    monkeypatch.setattr(keyring, "get_password", lambda s, u: vault.get((s, u)))
    monkeypatch.setattr(keyring, "set_password", lambda s, u, p: vault.__setitem__((s, u), p))
    monkeypatch.setattr(keyring, "delete_password", lambda s, u: vault.pop((s, u), None))
    monkeypatch.setattr(D, "SESSIONS", {})
    for where, _q in _stores().values():  # each scenario starts from empty stores (scratch only - asserted above)
        if isinstance(where, str) and os.path.exists(where):
            with sqlite3.connect(where) as c:
                for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
                    c.execute(f'DELETE FROM "{t}"')
    return SimpleNamespace(script=script, seen=seen, vault=vault, D=D)


def _check(exp, res, before, stores, sid, g):
    fails, reply = [], res.get("final_answer") or ""
    low, calls = reply.lower(), (res.get("calls") or {}).get("llm_calls", 0)
    if "skill" in exp:
        want = exp["skill"] if isinstance(exp["skill"], list) else [exp["skill"]]
        if res.get("skill") not in want:
            fails.append(f"skill {res.get('skill')!r} not in {want}")
    if "source" in exp and (res.get("route") or {}).get("source") != exp["source"]:
        fails.append(f"source {(res.get('route') or {}).get('source')!r} != {exp['source']!r}")
    if "llm_calls" in exp and calls > exp["llm_calls"]:
        fails.append(f"llm_calls {calls} > {exp['llm_calls']}")
    fails += [f"reply lacks {s!r}" for s in exp.get("reply_has", []) if s.lower() not in low]
    if exp.get("reply_any") and not any(s.lower() in low for s in exp["reply_any"]):
        fails.append(f"reply has none of {exp['reply_any']}")
    fails += [f"reply contains {s!r}" for s in exp.get("reply_not", []) if s.lower() in low]
    if exp.get("reply_re") and not re.search(exp["reply_re"], reply, re.I):
        fails.append(f"reply !~ /{exp['reply_re']}/")
    fails += [f"model prompt contained {s!r}" for s in exp.get("prompt_not", [])
              if any(s.lower() in p.lower() for p in g.seen)]
    pend = (g.D.SESSIONS.get(sid) or {}).get("pending")
    gated = bool(pend and callable(pend.get("action")))
    if "asks_confirmation" in exp and gated != exp["asks_confirmation"]:
        fails.append(f"asks_confirmation={gated}, want {exp['asks_confirmation']}")
    if "asks_question" in exp and reply.rstrip().endswith(("?", "(yes/no)")) != exp["asks_question"]:
        fails.append(f"asks_question want {exp['asks_question']}")
    for name, rule in (exp.get("store") or {}).items():
        now = _rows(stores[name])
        if "delta" in rule and len(now) - len(before[name]) != rule["delta"]:
            fails.append(f"{name} delta {len(now) - len(before[name])} != {rule['delta']}")
        fails += [f"{name} has no row with {n!r}" for n in rule.get("has", [])
                  if not any(all(p.lower() in r for p in n.split("&")) for r in now)]
        fails += [f"{name} still has a row with {n!r}" for n in rule.get("lacks", []) if any(n.lower() in r for r in now)]
    return fails


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_golden(scenario, golden, monkeypatch):
    monkeypatch.setenv("ISHTI_TEST_MODE", "0" if scenario.get("real_t2") else "1")
    sid, problems = "golden_" + scenario["id"], []
    for i, turn in enumerate(scenario["turns"]):
        golden.script[:] = list(turn.get("model") or [])
        golden.seen.clear()
        stores = _stores()
        before = {n: _rows(spec) for n, spec in stores.items()}
        res = golden.D.handle(turn["say"], session_id=sid)
        for f in _check(turn.get("expect") or {}, res, before, stores, sid, golden):
            problems.append(f"turn {i} {turn['say']!r}: {f} | reply: {(res.get('final_answer') or '')[:160]!r}")
    assert not problems, "\n".join(problems)
