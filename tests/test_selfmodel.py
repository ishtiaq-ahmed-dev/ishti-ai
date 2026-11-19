"""Acceptance suite for the self-model (WP-D2, master prompt acceptance tests 10-12).

    venv\\Scripts\\python -m pytest tests/test_selfmodel.py -q

Split deliberately:
  * OFFLINE (default) - the facts are measured and correct, the triggers recognise their paraphrases in
    four languages, privacy holds, and the block stays inside its budget. No model, no GPU.
  * E2E (deselected by `pytest.ini`, run explicitly) - asks the LIVE model and checks the answers contain
    the measured numbers and the honesty hedge. Needs Ollama and the GPU:
        venv\\Scripts\\python -m pytest tests/test_selfmodel.py -m e2e -s
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from ishti import selfmodel as sm  # noqa: E402
from ishti.skills.meta import skill as meta  # noqa: E402

SELF_CHECK_RX = dict(meta.OPS)["self_check"]


@pytest.fixture(autouse=True)
def offline_probe_inputs(request, monkeypatch):
    """Rendering/refresh contracts use explicit probe inputs; e2e keeps actual hardware/models."""
    if request.node.get_closest_marker('e2e'):
        yield
        return
    probes = {
        'os': {'os':'Windows fixture','system':'Windows','tag':'fixture'},
        'cpu': {'cpu':'fixture CPU','cores':8,'threads':16,'tag':'fixture'},
        'ram': {'ram_total_gb':64.0,'ram_free_gb':32.0,'ram_percent':50.0,'tag':'fixture'},
        'disk': {'disk':'fixture','disk_free_gb':100.0,'disk_total_gb':200.0,'tag':'fixture'},
        'gpu': {'gpu':'RTX fixture','vram_total_mb':16384,'vram_used_mb':1024,'driver':'fixture','tag':'fixture'},
    }
    monkeypatch.setattr(sm, 'CHEAP', tuple((key, lambda row=row: dict(row)) for key,row in probes.items()))
    monkeypatch.setattr(sm, '_cache', {'cheap_ts':0.0,'full_ts':0.0,'data':{},'fp':''})
    def get(url, **kwargs):
        from types import SimpleNamespace
        assert url.endswith(('/api/tags', '/api/ps')), 'Unexpected self-model network probe'
        models = [{'name':'fixture-planner','size':1024}] if url.endswith('/api/tags') else []
        return SimpleNamespace(json=lambda: {'models':models})
    monkeypatch.setattr('requests.get', get)
    yield

PARAPHRASES = {
    "EN": ["who are you", "whose assistant are you", "what pc are you running on", "your pc specs",
           "what are your specs", "what gpu do i have", "how much ram do i have",
           "which models are you using", "what skills do you have", "refresh your self-knowledge"],
    "UR": ["تم کون ہو", "میرے کمپیوٹر کی سپیفیکیشن کیا ہیں", "میری گرافکس کارڈ کون سی ہے",
           "تم کون کون سی ماڈل استعمال کرتے ہو", "تمہارے پاس کون کون سی اسکل ہیں",
           "تم کیا نہیں کر سکتے"],
    "PS": ["ته ول څوک یې", "زما کمپیوټر څه ډول دی", "زما ګرافیکس کارډ څه دی",
           "ته کوم ډول ماډلونه کارول", "ته څه مهارتونه لرې", "ته څه نه شې کولی"],
    "RU": ["tum kaun ho", "mere computer ki specs kya hain", "meri graphics card konsi hai",
           "kaun kaun si models use karte ho", "tumhare paas kaun kaun si skills hain",
           "tum kya nahi kar sakte"],
}
MUST_NOT_MATCH = ["what is the weather tomorrow in Peshawar", "merge this folder of images into one pdf",
                  "play some music", "send this file to Ali"]


# ==================================================== acceptance 10: the trigger reaches the skill
@pytest.mark.parametrize("text", [t for lang in PARAPHRASES for t in PARAPHRASES[lang]])
def test_paraphrase_is_recognised_in_every_language(text):
    """20+ paraphrases across English, Urdu, Pashto and Roman-Urdu must all reach self-knowledge."""
    assert SELF_CHECK_RX.search(text), f"not recognised: {text!r}"


@pytest.mark.parametrize("text", MUST_NOT_MATCH)
def test_ordinary_requests_are_not_stolen(text):
    assert not SELF_CHECK_RX.search(text), f"wrongly captured: {text!r}"


def test_four_languages_are_covered():
    assert set(PARAPHRASES) == {"EN", "UR", "PS", "RU"}
    assert sum(len(v) for v in PARAPHRASES.values()) >= 20


# ==================================================== acceptance 10: the answers match live data
def test_rendered_facts_match_the_probe_inputs():
    data = sm.build(force=True)
    gpu, ram, os_d = data["gpu"], data["ram"], data["os"]
    assert "RTX" in gpu["gpu"] and gpu["vram_total_mb"] > 8000, gpu
    assert gpu["vram_used_mb"] <= gpu["vram_total_mb"], gpu
    assert 30 < ram["ram_total_gb"] < 200, ram
    assert os_d["os"].startswith("Windows"), os_d
    block = sm.render(data)
    for needle in (gpu["gpu"], f"{ram['ram_total_gb']} GB"):
        assert needle in block, f"{needle!r} missing from the block:\n{block}"


def test_no_placeholder_or_stale_numbers():
    """Every number in the block must come from this run - never a hardcoded spec."""
    data = sm.build(force=True)
    block = sm.render(data)
    assert str(data["gpu"]["vram_total_mb"]) in block
    assert str(data["ram"]["ram_total_gb"]) in block
    assert data["measured_at"] in block


def test_inventory_is_real_not_zero():
    """Regression: build() once reported 0 skills (ISSUES.md SC-18)."""
    data = sm.build(force=True)
    assert data["skills"]["skills_total"] > 50, data["skills"]
    assert data["models"]["models_installed"] > 0, data["models"]
    assert "Skills (0 registered)" not in sm.detail()


# ==================================================== acceptance 11: change a fact -> answers update
def test_refresh_reports_a_fingerprint_and_rebuilds():
    first, second = sm.refresh(), sm.refresh()
    assert first["fingerprint"] and second["fingerprint"]
    assert "changed" in second and "measured_at" in second
    assert first["chars"] <= sm.BUDGET_CHARS and second["chars"] <= sm.BUDGET_CHARS
    assert first["render"].startswith("SELF-MODEL")


def test_a_removed_skill_disappears_from_the_inventory():
    """Acceptance 11: change a fact (here: a skill), refresh, and the answer follows."""
    data = sm.build(force=True)
    rows = data["skills"]["skills"]
    victim = sorted(rows)[0]
    rows.pop(victim)
    assert f"\n- {victim} [" not in sm.detail(), f"{victim} should be gone once removed"


def test_unknown_facts_are_not_invented():
    """Acceptance 11: a fact that is not measured must not appear with a confident number."""
    block = sm.render(sm.build(force=True))
    for invented in ("context window", "temperature 0.7", "trained on"):
        assert invented not in block.lower(), invented
    assert "I don't know" in block


# ==================================================== acceptance 12: size + privacy
def test_block_is_inside_its_char_budget():
    block = sm.render()
    assert len(block) <= sm.BUDGET_CHARS, f"{len(block)} chars > {sm.BUDGET_CHARS}"
    assert sm.BUDGET_CHARS <= 1500, "the budget must stay token-frugal for every request"


def test_owner_identity_is_absent_from_the_resident_block():
    owner = (sm.profile().get("owner") or {})
    block = sm.render()
    for value in (owner.get("name"), owner.get("preferred_address")):
        if value:
            assert value not in block, f"owner identity in the resident block: {value}"
    host = os.environ.get("COMPUTERNAME", "")
    if len(host) > 2:
        assert host not in block, "hostname in the resident block"


def test_outbound_queries_are_stripped_of_owner_identity(monkeypatch):
    # hermetic: a stand-in profile, not the owner's real (git-ignored) owner_profile.yaml, which may not exist
    monkeypatch.setattr(sm, "profile", lambda: {"owner": {"name": "Ishtiaq Ahmed", "preferred_address": "Ishtiaq"}})
    name = "Ishtiaq"
    probe = f"who is {name} and what is my PC"
    assert sm.assert_local_only(probe), "the chokepoint must catch the owner's name"
    assert name not in sm.sanitize_outbound(probe)
    assert sm.assert_local_only("what is the weather in Peshawar") == []


def test_self_model_never_rides_along_in_a_search_query():
    """The block must not become a query; sanitize_outbound is the chokepoint (RESEARCH.md R5 S5)."""
    owner = (sm.profile().get("owner") or {})
    terms = [v for v in (owner.get("name"), owner.get("preferred_address")) if v]
    query = sm.sanitize_outbound(" ".join([sm.render().splitlines()[1], "weather in Peshawar"]))
    for t in terms:
        assert t not in query, t


# ==================================================== e2e: the live model really answers correctly
@pytest.mark.e2e
def test_live_model_answers_from_the_block_and_hedges_on_unknowns():
    """Needs Ollama + the GPU. Checks the acceptance behaviours on real model answers."""
    from ishti import llm
    gpu_name = sm.build()["gpu"]["gpu"]

    ans = llm.ask("In one short sentence: who are you, and are you a cloud service?", role="planner")
    assert any(w in ans.lower() for w in ("ishti", "local", "pc", "offline")), ans

    ans2 = llm.ask("What graphics card do I have? If you do not know, say so.", role="planner")
    assert gpu_name.split(",")[0].split("(")[0].strip()[:12] in ans2 or "don't know" in ans2.lower(), ans2

    ans3 = llm.ask("What is your context window size in tokens?", role="planner")
    assert "don't know" in ans3.lower() or "shall i check" in ans3.lower(), \
        f"a fact that is NOT in the self-model must be hedged, not invented: {ans3}"

