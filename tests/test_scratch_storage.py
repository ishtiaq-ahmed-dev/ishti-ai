"""Scratch verification never opens the default owner stores."""
import os


def test_scratch_path_requires_both_flags(tmp_path, monkeypatch):
    import ishti
    from ishti.test_mode import scratch_path
    monkeypatch.setattr(ishti, "ROOT", str(tmp_path))
    owner_path = str(tmp_path / "owner" / "facts.db")
    monkeypatch.setenv("ISHTI_TEST_MODE", "1")
    monkeypatch.setenv("ISHTI_SCRATCH", "1")
    target = scratch_path(owner_path)
    assert target != owner_path
    assert target == str(tmp_path / "_work" / "scratch" / str(os.getpid()) / "data" / "facts.db")
    monkeypatch.delenv("ISHTI_SCRATCH")
    assert scratch_path(owner_path) == owner_path
    monkeypatch.setenv("ISHTI_SCRATCH", "1")
    monkeypatch.setenv("ISHTI_TEST_MODE", "0")
    assert scratch_path(owner_path) == owner_path


def test_explicit_fact_store_and_conversation_paths_are_preserved(tmp_path):
    from ishti.facts import Facts
    from orchestrator.memory.conversation import ConversationManager
    path = tmp_path / "explicit.db"
    facts = Facts(str(path))
    try:
        facts.set("fixture", "value", "synthetic")
        assert path.is_file()
    finally:
        facts.db.close()
    manager = ConversationManager(str(tmp_path / "explicit_sessions"))
    manager.add_message(manager.active_session_id, "user", "synthetic")
    assert list((tmp_path / "explicit_sessions").glob("*.json"))


def test_default_conversation_constructor_uses_scratch(tmp_path, monkeypatch):
    import ishti
    from orchestrator.memory.conversation import ConversationManager
    monkeypatch.setattr(ishti, "ROOT", str(tmp_path))
    monkeypatch.setenv("ISHTI_TEST_MODE", "1")
    monkeypatch.setenv("ISHTI_SCRATCH", "1")
    manager = ConversationManager()
    expected = tmp_path / "_work" / "scratch" / str(os.getpid()) / "data" / "sessions"
    assert manager.sessions_dir == str(expected)
    assert list(expected.glob("*.json"))
