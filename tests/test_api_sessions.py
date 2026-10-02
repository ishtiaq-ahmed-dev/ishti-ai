"""WP-OD acceptance: browser session + origin hardening (matrix rows OD-12, OD-23).

T-OD-12 / T-OD-23, no real server and no real credential store:
  - session tokens are stored ONLY as SHA-256 hashes; a fresh SessionStore (a restart) knows
    nothing about an old token; refresh() rotates and kills the old token; ttl/idle expire
  - origin policy: cross-site / same-site Sec-Fetch-Site refused, mismatched or unparseable
    Origin refused, absent Origin (curl/tests) allowed, matching Origin+Host allowed
  - every state-changing /api/remote route refuses a cross-site browser request with 403
    BEFORE auth or any side effect; same-origin and no-Origin callers still pass
  - the cookie posture stays HttpOnly + Secure + SameSite=Strict
  - both modules' __main__ self-checks stay green

Run: venv\\Scripts\\python -m pytest tests/test_api_sessions.py -q
"""
import asyncio
import json
import os
import re
import subprocess
import sys

import pytest
from starlette.requests import Request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ishti import api_sessions  # noqa: E402
from ishti.channels import api, policy as policy_mod  # noqa: E402


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


# ------------------------------------------------------------------ sessions

def test_only_hashes_are_stored_and_restart_invalidates():
    store = api_sessions.SessionStore()
    tok = store.create("owner", {"ua": "test"})
    assert tok and store.get(tok)["subject"] == "owner"
    assert tok not in store._rows and api_sessions.hash_token(tok) in store._rows
    assert tok not in repr(store._rows), "the raw token never appears in stored state"
    fresh = api_sessions.SessionStore()
    assert fresh.get(tok) is None, "a restart (new process) invalidates every session"


def test_rotation_kills_the_old_token():
    store = api_sessions.SessionStore()
    old = store.create()
    new = store.refresh(old)
    assert new and new != old
    assert store.get(old) is None and store.get(new) is not None
    assert store.refresh("unknown-token") is None
    assert store.count() == 1


def test_ttl_and_idle_expiry_with_injected_clock():
    clock = Clock()
    store = api_sessions.SessionStore(ttl_s=100, idle_s=40, now=clock)
    a = store.create()
    clock.t += 39
    assert store.get(a) is not None, "inside the idle window"
    clock.t += 39                                        # seen 39s ago again -> still alive
    assert store.get(a) is not None
    clock.t += 41
    assert store.get(a) is None, "idle expiry"
    b = store.create()
    for _ in range(3):                       # keep it active, so idle never trips
        clock.t += 30
        assert store.get(b) is not None
    clock.t += 20                            # 110s since creation > ttl 100
    assert store.get(b) is None, "ttl expiry even while active"
    c = store.create()
    clock.t += 41
    assert store.expire() == 1 and store.count() == 0


def test_revoke_and_revoke_all_count():
    store = api_sessions.SessionStore()
    a, b = store.create(), store.create()
    assert store.revoke(a) and not store.revoke(a)
    assert store.count() == 1 and store.revoke_all() == 1 and store.count() == 0
    assert store.revoke_all() == 0


def test_sessions_are_total_on_junk():
    store = api_sessions.SessionStore()
    for junk in (None, 42, [], {}, b"x", object()):
        assert store.get(junk) is None
        assert store.refresh(junk) is None
        assert store.revoke(junk) is False
        assert api_sessions.hash_token(junk)  # hashing junk never raises


# ------------------------------------------------------------------ origin policy

def test_origin_policy_matrix():
    ok = lambda o, h, s=None: api_sessions.origin_ok(o, h, s)
    assert ok(None, "127.0.0.1:8888") is True, "non-browser clients still work"
    assert ok("http://127.0.0.1:8888", "127.0.0.1:8888") is True
    assert ok("https://localhost", "localhost") is True
    assert ok("http://[::1]:8888", "[::1]:8888") is True
    assert ok("http://127.0.0.1", "127.0.0.1:80") is True, "default port equivalence"
    assert ok("http://127.0.0.1:8888", "127.0.0.1:8888", "same-origin") is True
    for site in ("cross-site", "same-site", "CROSS-SITE"):
        assert ok("http://127.0.0.1:8888", "127.0.0.1:8888", site) is False, site
    assert ok("http://evil.example", "127.0.0.1:8888") is False
    assert ok("http://127.0.0.1:9999", "127.0.0.1:8888") is False, "port matters"
    assert ok("http://127.0.0.1.evil.com", "127.0.0.1:8888") is False
    for bad in ("file:///etc/passwd", "javascript:alert(1)", "null", "junk", 42, b"x", {"a": 1}):
        assert ok(bad, "127.0.0.1:8888") is False, bad
    assert ok(None, "127.0.0.1:8888") is True and ok("", "127.0.0.1:8888") is True, "absent means absent"
    assert ok("http://127.0.0.1:8888", None) is False and ok("http://127.0.0.1:8888", "") is False


def test_cookie_posture_is_strict():
    assert api_sessions.session_cookie_flags() == {"httponly": True, "secure": True,
                                                   "samesite": "strict"}


# ------------------------------------------------------------------ route wiring

def _request(method="POST", path="/api/remote/policy", body=None, headers=None):
    raw = json.dumps(body if body is not None else {}).encode()
    scope = {"type": "http", "http_version": "1.1", "method": method, "scheme": "http",
             "path": path, "raw_path": path.encode(), "query_string": b"",
             "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
             "client": ("127.0.0.1", 12345), "server": ("127.0.0.1", 8888)}

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}
    return Request(scope, receive=receive)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(policy_mod, "PATH", str(tmp_path / "policy.yaml"))
    policy_mod.reset_cache()
    monkeypatch.setattr(api, "_auth", lambda request: True)   # origin gate is this suite's subject
    from ishti.channels import service
    monkeypatch.setattr(service, "get", lambda: (_ for _ in ()).throw(AssertionError("service called")))
    yield tmp_path
    policy_mod.reset_cache()


HOST = {"host": "127.0.0.1:8888"}
CROSS = {**HOST, "origin": "http://evil.example"}
SAME = {**HOST, "origin": "http://127.0.0.1:8888"}


def test_cross_site_state_change_is_refused_before_any_work(env):
    r = asyncio.run(api.put_policy(_request("PUT", headers=CROSS, body={"rate_limit_per_min": 5})))
    assert getattr(r, "status_code", 200) == 403
    assert json.loads(bytes(r.body))["error"] == "cross-site request refused"
    assert policy_mod.load().get("rate_limit_per_min") != 5, "nothing was written"
    site_only = asyncio.run(api.put_policy(_request("PUT", headers={**SAME, "sec-fetch-site": "cross-site"},
                                                    body={"rate_limit_per_min": 5})))
    assert site_only.status_code == 403, "Sec-Fetch-Site alone is enough to refuse"


def test_every_mutating_route_is_gated(env):
    cases = [
        (api.pair_start, "POST", "/api/remote/pair/start"),
        (api.pair_cancel, "POST", "/api/remote/pair/cancel"),
        (api.revoke, "POST", "/api/remote/revoke"),
        (api.set_token, "POST", "/api/remote/telegram/token"),
        (api.del_token, "DELETE", "/api/remote/telegram/token"),
        (api.set_pin, "POST", "/api/remote/pin"),
        (api.del_pin, "DELETE", "/api/remote/pin"),
        (api.enable_channel, "POST", "/api/remote/enable"),
        (api.test_message, "POST", "/api/remote/test"),
    ]
    for fn, method, path in cases:
        r = asyncio.run(fn(_request(method, path, headers=CROSS, body={"channel": "telegram"})))
        assert getattr(r, "status_code", 200) == 403, (path, r)
    # structural guard: every non-GET route in the router has the gate in the source
    src = open(os.path.join(ROOT, "ishti", "channels", "api.py"), encoding="utf-8").read()
    non_get = [r for r in api.router.routes if "GET" not in getattr(r, "methods", set())]
    assert src.count("if not _origin_ok(request):") == len(non_get), "a mutating route lost its gate"


def test_same_origin_and_no_origin_callers_still_pass(env):
    r = asyncio.run(api.put_policy(_request("PUT", headers=SAME, body={"stale_command_max_age_s": 90})))
    assert getattr(r, "status_code", 200) == 200
    assert r["stale_command_max_age_s"] == 90
    r = asyncio.run(api.put_policy(_request("PUT", headers=HOST, body={"stale_command_max_age_s": 91})))
    assert r["stale_command_max_age_s"] == 91, "curl/tests without browser headers keep working"


def test_same_origin_service_calls_still_reach_the_service(env, monkeypatch):
    from ishti.channels import service
    monkeypatch.setattr(service, "get", lambda: type("S", (), {"revoke_sessions": lambda self: {"revoked_ids": 0},
                                                              "push": lambda self, *a, **k: True})())
    r = asyncio.run(api.revoke(_request("POST", "/api/remote/revoke", headers=SAME)))
    assert getattr(r, "status_code", 200) == 200 and r["revoked_ids"] == 0
    r = asyncio.run(api.test_message(_request("POST", "/api/remote/test", headers=SAME)))
    assert r["sent"] is True


# ------------------------------------------------------------------ self-checks

@pytest.mark.parametrize("mod", ["ishti/api_sessions.py"])
def test_selfchecks_still_pass(mod):
    env2 = {**os.environ,
            "PYTHONPATH": ROOT + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")}
    r = subprocess.run([sys.executable, os.path.join(ROOT, mod)], capture_output=True, text=True,
                       timeout=120, cwd=ROOT, env=env2)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[self-check] api_sessions OK" in r.stdout
