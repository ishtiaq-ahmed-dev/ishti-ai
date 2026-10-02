"""Settings -> Language: English only by default; Urdu and Pashto are switches (ishti/languages.py), and a model the owner picks
in Settings becomes the top rung of its role's ladder (ishti/vram.py _owner_first). Offline: facts and Ollama are stubbed."""
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("ISHTI_STATE_DB", os.path.join(tempfile.mkdtemp(prefix="ishti_lang_"), "state.db"))

from ishti import languages  # noqa: E402


class FakeFacts:
    def __init__(self):
        self.store = {}

    def get(self, entity):
        return dict(self.store)

    def set(self, entity, attr, value):
        self.store[attr] = value

    def forget(self, entity, attr):
        self.store.pop(attr, None)


@pytest.fixture
def facts(monkeypatch):
    f = FakeFacts()
    monkeypatch.setattr(languages, "_facts", lambda: f)
    return f


def test_default_is_english_only(facts):
    assert languages.enabled() == ["english"]
    assert languages.allowed("English") and not languages.allowed("Urdu") and not languages.allowed("Pashto")
    assert not languages.allowed("Roman Urdu (Urdu written in English letters)")
    assert languages.style_rule() == "Language: Reply only in English."
    assert [(x["id"], x["enabled"]) for x in languages.state()["languages"]] == [("english", True), ("urdu", False), ("pashto", False)]


def test_switching_on_and_off_and_the_lock_follows(facts):
    facts.store["prefers_language"] = "Urdu"
    assert languages.set_enabled(urdu=True) == ["english", "urdu"]
    assert languages.allowed("Roman Urdu") and "English or Urdu" in languages.style_rule() and facts.store["prefers_language"] == "Urdu"
    languages.set_enabled(urdu=False)
    assert "prefers_language" not in facts.store and languages.enabled() == ["english"]
    assert languages.set_enabled(pashto=True, urdu=True) == ["english", "urdu", "pashto"]
    assert languages.set_enabled() == ["english", "urdu", "pashto"]            # None = leave as is


def test_system_prompt_follows_the_switches(facts, monkeypatch):
    from ishti import llm
    import ishti.facts as facts_mod
    monkeypatch.setattr(facts_mod, "get_facts", lambda: facts)
    assert "6. Language: Reply only in English." in llm.style()
    languages.set_enabled(pashto=True)
    assert "English or Pashto" in llm.style() and "Reply only in English." not in llm.style()


def test_speech_recognition_is_limited_to_the_enabled_languages(facts):
    from ishti import voice
    voice._forced_language, voice._forced_by_languages = None, False
    voice.apply_languages()
    assert voice._cfg["languages"] == ["en"] and voice.get_forced_language() == "en"        # English only: simply told "en"
    languages.set_enabled(urdu=True)
    voice.apply_languages()
    assert voice._cfg["languages"] == ["en", "ur"] and voice.get_forced_language() is None   # released: detection among the enabled ones
    languages.set_enabled(urdu=False)
    voice.apply_languages()
    assert voice.get_forced_language() == "en"


def test_urdu_text_gets_an_english_answer_and_a_one_time_hint_when_urdu_is_off(facts, monkeypatch):
    import ishti.dispatcher as D
    monkeypatch.setattr(D, "get_facts", lambda: facts)
    assert D.preferred_language() is None
    facts.store["prefers_language"] = "Urdu"
    assert D.preferred_language() is None                 # a lock on a language that is off does nothing
    languages.set_enabled(urdu=True)
    facts.store["prefers_language"] = "Urdu"
    assert D.preferred_language() == "Urdu"


def test_the_owner_first_ladder(monkeypatch):
    from ishti import vram
    sizes = {"ishti-spark2.5:4b": 8.2e9, "ishti-spark2.5-q8:4b": 4.4e9, "qwen2.5:7b": 4.7e9, "qwen2.5:3b": 1.9e9, "ishti-qwen2.5:14b": 9.0e9}
    monkeypatch.setattr(vram, "tags", lambda: sizes)
    items = [{"model": m, "name": m} for m in sizes]
    spec = {"min_ctx": 8192}
    monkeypatch.setitem(vram.CONFIG["llm"]["models"], "planner", "ishti-spark2.5-q8:4b")
    assert [r["name"] for r in vram._owner_first("planner", spec, items)] == ["ishti-spark2.5-q8:4b", "qwen2.5:3b"]      # only lighter ones follow
    monkeypatch.setitem(vram.CONFIG["llm"]["models"], "planner", "ishti-spark2.5:4b")
    assert [r["name"] for r in vram._owner_first("planner", spec, items)] == ["ishti-spark2.5:4b", "qwen2.5:7b", "ishti-spark2.5-q8:4b", "qwen2.5:3b"]
    monkeypatch.setenv("ISHTI_MODEL_PLANNER", "qwen2.5:3b")
    assert [r["name"] for r in vram._owner_first("planner", spec, items)] == ["qwen2.5:3b"]                           # a pinned model has no fallback


def test_urdu_message_with_urdu_off_is_not_translated_and_gets_one_hint(facts, monkeypatch):
    import ishti.dispatcher as D
    import ishti.facts as facts_mod
    from orchestrator.memory.user_profile import scratch_memory
    for name in ("_remember", "_learn", "_learn_turn", "_arch2_consolidate", "_ftlog"):
        monkeypatch.setattr(D, name, lambda *a, **k: None)
    monkeypatch.setattr(D.planner, "candidate", lambda *a, **k: False)
    monkeypatch.setattr(D, "translate", lambda *a, **k: pytest.fail("Urdu is off: nothing may be translated"))
    monkeypatch.setattr(D, "route", lambda text, skills, exclude=None: {"skill": "chat", "confidence": 1.0, "source": "rule", "scores": []})
    monkeypatch.setattr(D.SKILLS["chat"], "run", lambda text, s: {"reply": "The weather is fine today, with clear skies and a light breeze.", "sources": [], "no_escalate": True})
    with scratch_memory():
        saved, facts_mod._facts = facts_mod._facts, facts_mod.Facts(os.path.join(tempfile.mkdtemp(), "facts.db"))
        try:
            first = D.handle("آج موسم کیسا ہے", session_id="langoff")["final_answer"]
            second = D.handle("اور کل کا موسم", session_id="langoff")["final_answer"]
        finally:
            facts_mod._facts = saved
    assert "is switched off" in first and "Settings > Language" in first and first.startswith("The weather is fine")
    assert "is switched off" not in second          # said once per chat, not on every message
