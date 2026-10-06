from orchestrator import finetune


def test_custom_ollama_tag_is_not_invented_as_hf_training_source(monkeypatch, tmp_path):
    monkeypatch.delenv("ISHTI_TRAINING_BASE_MODEL", raising=False)
    assert finetune.training_base_model() is None


def test_training_base_requires_existing_local_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("ISHTI_TRAINING_BASE_MODEL", str(tmp_path / "missing"))
    assert finetune.training_base_model() is None
    base = tmp_path / "base"
    base.mkdir()
    monkeypatch.setenv("ISHTI_TRAINING_BASE_MODEL", str(base))
    assert finetune.training_base_model() == str(base)


def test_adapter_eval_refuses_when_serving_path_cannot_load_adapter():
    try:
        finetune._answer_with("local", "/tmp/candidate", "test")
    except finetune.AdapterEvaluationUnavailable as exc:
        assert "refusing" in str(exc)
    else:
        raise AssertionError("adapter eval must never claim a bare-model score")


def test_promote_preserves_current_when_adapter_eval_is_unavailable(monkeypatch, tmp_path):
    adapters = tmp_path / "adapters"
    candidate = adapters / "candidate"
    current = adapters / "current"
    candidate.mkdir(parents=True)
    current.mkdir()
    (current / "sentinel").write_text("keep")
    monkeypatch.setattr(finetune, "ADAPTERS", str(adapters))
    monkeypatch.setattr(finetune, "CURRENT", str(current))
    ok, message = finetune.promote(str(candidate))
    assert not ok and "refused" in message
    assert (current / "sentinel").read_text() == "keep"
