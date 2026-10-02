"""WP-COUCOU acceptance: OPT-IN integration pills (matrix row COU-08).

T-C08, everything injected (no network, no real credential store):
  - OFF BY DEFAULT is a contract: with no explicit opt-in, status() returns "off" for every
    provider and NEVER reads a credential or makes a request
  - opt-in is the only switch; the island being idle pauses polling without any call
  - cadence gate: refresh at most every REFRESH_S
  - no credential -> "missing-cred" and fetch is never called
  - live path calls cred then fetch exactly once and reports ok/error; fetch errors and
    hostile inputs become pill states, never exceptions
  - the module's own __main__ self-check stays green

Run: venv\\Scripts\\python -m pytest tests/test_overlay_integrations.py -q
"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from desktop.overlay import integrations  # noqa: E402


@pytest.fixture(autouse=True)
def clean_registry():
    """No provider stays opted in across tests."""
    saved = set(integrations._opted_in)
    integrations._opted_in.clear()
    yield
    integrations._opted_in.clear()
    integrations._opted_in.update(saved)


class Spies:
    def __init__(self, token="tok", result=True):
        self.calls = []
        self.token = token
        self.result = result

    def cred(self, provider):
        self.calls.append(("cred", provider))
        return self.token

    def fetch(self, provider, token):
        self.calls.append(("fetch", provider, token))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_off_by_default_is_zero_cred_zero_network():
    s = Spies()
    for provider in integrations.PROVIDERS:
        assert integrations.enabled(provider) is False
        r = integrations.status(provider, fetch=s.fetch, cred=s.cred)
        assert r["state"] == "off" and r["reason"] == "opt-in required", (provider, r)
    assert s.calls == [], "off by default must never read credentials or hit the network"
    # idle or not, off stays off and silent
    assert integrations.status("stripe", idle=True, fetch=s.fetch, cred=s.cred)["state"] == "off"
    assert s.calls == []


def test_opt_in_is_the_only_switch():
    assert integrations.opt_in("github") is True and integrations.enabled("github")
    assert integrations.opt_in("github", on=False) is False and not integrations.enabled("github")
    assert integrations.opt_in("not-a-provider") is False
    assert integrations.opt_in(None) is False
    assert integrations.enabled("GITHUB") is False, "case-insensitive lookup, still off"


def test_idle_pauses_without_any_call():
    integrations.opt_in("n8n")
    s = Spies()
    r = integrations.status("n8n", idle=True, fetch=s.fetch, cred=s.cred)
    assert r["state"] == "paused" and r["reason"] == "island idle"
    assert s.calls == [], "an idle island does not poll"
    # once active, the same provider is live
    assert integrations.status("n8n", idle=False, fetch=s.fetch, cred=s.cred)["state"] == "ok"
    assert s.calls == [("cred", "n8n"), ("fetch", "n8n", "tok")]


def test_cadence_gate_is_refresh_s():
    assert integrations.REFRESH_S >= 60
    assert integrations.due(10_000, now=10_000) is False
    assert integrations.due(10_000, now=10_000 + integrations.REFRESH_S - 1) is False
    assert integrations.due(10_000, now=10_000 + integrations.REFRESH_S) is True
    assert integrations.due(0, now=10 ** 9) is True


def test_missing_credential_never_fetches():
    integrations.opt_in("vercel")
    s = Spies(token=None)
    r = integrations.status("vercel", fetch=s.fetch, cred=s.cred)
    assert r["state"] == "missing-cred" and "Credential Manager" in r["reason"]
    assert [c[0] for c in s.calls] == ["cred"], "no fetch without a credential"
    s2 = Spies(token="")
    assert integrations.status("vercel", fetch=s2.fetch, cred=s2.cred)["state"] == "missing-cred"


def test_live_path_reports_ok_and_errors_never_raise():
    integrations.opt_in("stripe")
    s = Spies(result="42 objects")
    r = integrations.status("stripe", fetch=s.fetch, cred=s.cred)
    assert r["state"] == "ok" and r["detail"] == "42 objects"
    assert s.calls == [("cred", "stripe"), ("fetch", "stripe", "tok")]
    # fetch says unreachable -> error pill
    for bad in (False, None):
        r = integrations.status("stripe", fetch=Spies(result=bad).fetch, cred=Spies().cred)
        assert r["state"] == "error" and r["detail"] == "unreachable", bad
    # fetch explodes -> error pill, never an exception
    r = integrations.status("stripe", fetch=Spies(result=RuntimeError("net down")).fetch,
                            cred=Spies().cred)
    assert r["state"] == "error" and "RuntimeError" in r["detail"]
    # credential read explodes -> error pill, never an exception
    def bad_cred(_p):
        raise OSError("cred store locked")
    r = integrations.status("stripe", fetch=Spies().fetch, cred=bad_cred)
    assert r["state"] == "error" and "OSError" in r["detail"]
    # oversized detail is truncated to a pill-sized string
    r = integrations.status("stripe", fetch=Spies(result="x" * 500).fetch, cred=Spies().cred)
    assert r["state"] == "ok" and len(r["detail"]) == 200


def test_hostile_provider_names_never_raise():
    for bad in (None, 42, ["stripe"], {"p": 1}, ""):
        r = integrations.status(bad)
        assert r["state"] in ("unknown", "off"), (bad, r)
    assert integrations.status("unknown-provider") == {"state": "unknown", "provider": "unknown-provider"}
    assert integrations.status(None)["state"] == "unknown"


def test_corrupt_credential_lookup_is_none_not_raise():
    """win_credential reads read-only; a missing entry (certain here) must return None."""
    assert integrations.win_credential("__ishti_probe_nonexistent__") is None


def test_selfcheck_still_passes():
    mod = os.path.join(ROOT, "desktop", "overlay", "integrations.py")
    env = {**os.environ,
           "PYTHONPATH": ROOT + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")}
    r = subprocess.run([sys.executable, mod], capture_output=True, text=True,
                       timeout=120, cwd=ROOT, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[self-check] overlay integrations OK" in r.stdout
