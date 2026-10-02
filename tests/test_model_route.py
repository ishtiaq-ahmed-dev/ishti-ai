"""WP-ECC acceptance: model-route decision with fallback chain (matrix row ECC-13).

T-ECC-13, offline (config in a scratch file, installed list injected, no Ollama):
  - route() serves the configured model when it is installed and reports an ordered,
    de-duplicated fallback chain (served -> configured -> fallback -> fast -> installed)
  - a stale configured name is resolved to the configured fallback (or the first usable
    installed model) - the decision never returns a model that is not installed
  - roles (primary/fast/fallback/coding) each get a non-empty chain: fallback coverage 1.0
    where `get_model()` alone (BEFORE) had no fallback field at all
  - decision latency is measured per call; a local decision stays well under 50 ms
  - the decision performs no model load: _installed_alternative and get_model_sizes are
    never called when the installed list is injected (VRAM invariant untouched)
  - route() never raises, even on junk arguments

Run: venv\\Scripts\\python -m pytest tests/test_model_route.py -q
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from orchestrator.model_router import ModelRouter  # noqa: E402

INSTALLED = ["qwen2.5:14b", "deepseek-r1:8b", "llama3.2:3b", "qwen2.5-coder:7b"]


def _router(tmp_path, cfg=None):
    path = tmp_path / "router_config.json"
    if cfg is not None:
        path.write_text(json.dumps(cfg), encoding="utf-8")
    return ModelRouter(config_path=str(path))


def _config(primary="qwen2.5:14b", fallback="deepseek-r1:8b", fast="llama3.2:3b"):
    return {"ollama": {"host": "http://127.0.0.1:11434", "primary_model": primary,
                       "fallback_model": fallback, "fast_model": fast}}


def test_decision_serves_configured_model_with_a_full_chain(tmp_path):
    r = _router(tmp_path, _config()).route("primary", installed=INSTALLED)
    assert r["v"] == 1 and r["role"] == "primary" and r["model"] == "qwen2.5:14b"
    assert r["chain"][0] == "qwen2.5:14b"
    assert r["chain"] == ["qwen2.5:14b", "deepseek-r1:8b", "llama3.2:3b", "qwen2.5-coder:7b"], r["chain"]
    assert r["fallback"] == "deepseek-r1:8b" and "installed" in r["reason"]
    assert r["latency_ms"] < 50


def test_stale_configured_name_is_resolved_to_an_installed_model(tmp_path):
    cfg = _config(primary="qwen3:30b-a3b")
    r = _router(tmp_path, cfg).route("primary", installed=["deepseek-r1:8b", "llama3.2:3b"])
    assert r["model"] == "deepseek-r1:8b", "configured fallback wins over a stale primary"
    assert "unavailable" in r["reason"] and r["chain"][0] == "deepseek-r1:8b"
    # fallback also missing -> the first usable installed model, never the missing name
    r = _router(tmp_path, _config(primary="qwen3:30b", fallback="nope:70b")).route(
        "primary", installed=["llama3.2:3b"])
    assert r["model"] == "llama3.2:3b"


def test_empty_install_list_never_invents_a_model(tmp_path):
    r = _router(tmp_path, _config()).route("primary", installed=[])
    assert r["model"] == "qwen2.5:14b", "no installed info: serve the configured name, never invent"
    assert r["chain"][0] == r["model"] and r["fallback"] == "deepseek-r1:8b"


def test_every_role_has_a_nonempty_chain(tmp_path):
    router = _router(tmp_path, _config())
    for role in ("primary", "fast", "fallback", "coding"):
        r = router.route(role, installed=INSTALLED)
        assert r["model"] in INSTALLED and r["chain"] and r["fallback"], (role, r)
        # BEFORE (get_model alone) returned a bare string with no fallback information
        assert isinstance(router.get_model(role), str)


def test_decision_is_fast_and_never_loads(tmp_path, monkeypatch):
    router = _router(tmp_path, _config())

    def forbidden(*a, **k):
        raise AssertionError("the route decision must not fetch/load anything")

    monkeypatch.setattr(router, "_installed_alternative", forbidden)
    monkeypatch.setattr(router, "get_model_sizes", forbidden)
    import time
    n = 200
    t0 = time.perf_counter()
    for _ in range(n):
        r = router.route("primary", installed=INSTALLED)
    avg_ms = (time.perf_counter() - t0) * 1000 / n
    assert r["model"] == "qwen2.5:14b" and avg_ms < 5.0, f"decision averaged {avg_ms:.3f} ms"


def test_route_never_raises_on_junk(tmp_path):
    router = _router(tmp_path, _config())
    for role in (None, 42, "", "unknown-role"):
        r = router.route(role, installed=INSTALLED)
        assert isinstance(r, dict) and "chain" in r and isinstance(r["latency_ms"], (int, float))
    for junk_installed in ("junk", 42, [None, 7, "llama3.2:3b"], object()):
        r = router.route("primary", installed=junk_installed)
        assert isinstance(r, dict) and isinstance(r["chain"], list), repr(junk_installed)
    assert isinstance(router.route("primary", risk=None, budget=[1]).get("risk"), str)


def test_fallback_labels_are_carried_through(tmp_path):
    r = _router(tmp_path, _config()).route("primary", installed=INSTALLED, risk="high", budget="tight")
    assert r["risk"] == "high" and r["budget"] == "tight"
