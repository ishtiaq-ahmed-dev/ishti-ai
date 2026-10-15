"""Offline regressions for independently reproduced international readiness defects."""
from types import SimpleNamespace

import pytest

from ishti import audit, embeddings, llm, privacy
from ishti.memstore import consolidate, memory, retrieval
from ishti.skills.writing import skill as writing


@pytest.mark.parametrize("prompt", ["convert my report to pdf", "convert my document to pdf"])
def test_pdf_without_source_never_claims_completion(prompt):
    out = writing.run(prompt, {})
    assert out["status"] == "needs_input" and out["verified"] is False
    assert "no PDF has been created" in out["reply"]


@pytest.mark.parametrize("prompt,forbidden", [
    ("format this citation in apa style: Different Author, Different Work", "Vaswani"),
    ("help me write the hypothesis section for crop yields", "Titanic"),
    ("draft a reply declining this meeting prompt from Mary", "Ishtiaq Ahmed"),
])
def test_writing_uses_supplied_material_not_fixed_answers(monkeypatch, prompt, forbidden):
    seen = []
    monkeypatch.setattr(writing, "_ask", lambda job, body, extra="": seen.append((job, body)) or "Synthetic draft")
    out = writing.run(prompt, {})
    assert out["reply"] == "Synthetic draft" and forbidden not in out["reply"]
    assert prompt.split()[-1] in seen[0][1]


@pytest.mark.parametrize("text", ["میرا بھائی احمد لاہور میں رہتا ہے", "زما ورور احمد په کابل کې ژوند کوي"])
def test_native_script_memory_is_considered_and_tokenized(text):
    assert "احمد" in retrieval._kw(text)
    assert consolidate.worth_extracting(text)


@pytest.mark.parametrize("response,prompt", [("你好", "Translate hello into Chinese"),
                                               ("สวัสดี", "Translate hello into Thai")])
def test_translation_keeps_the_prompted_script(response, prompt):
    assert llm.clean(response, prompt) == response


def test_selected_provider_grant_cannot_become_general_egress(monkeypatch):
    monkeypatch.setattr(privacy, "internet", lambda: True)
    with audit.context(skill="writing"):
        assert not privacy.decide("api.anthropic.com")[0]
        with privacy.inference("https://api.anthropic.com/v1"):
            assert privacy.decide("api.anthropic.com", 443)[0]
            assert not privacy.decide("api.anthropic.com", 8443)[0]
            assert not privacy.decide("evil.example", 443)[0]
            assert not privacy.decide("api.anthropic.com.evil.example", 443)[0]
        assert not privacy.decide("api.anthropic.com")[0]


def test_internet_off_still_denies_inference(monkeypatch):
    monkeypatch.setattr(privacy, "internet", lambda: False)
    with pytest.raises(privacy.EgressBlocked):
        with privacy.inference("https://api.anthropic.com/v1"):
            pytest.fail("grant must not open")


@pytest.mark.parametrize("endpoint", ["http://remote.example/v1", "https://key@remote.example/v1",
                                      "https://remote.example/v1?key=x", "file:///secret"])
def test_inference_rejects_unsafe_endpoint_shapes(monkeypatch, endpoint):
    monkeypatch.setattr(privacy, "internet", lambda: True)
    with pytest.raises(ValueError):
        with privacy.inference(endpoint):
            pytest.fail("invalid endpoint")


def test_api_call_keeps_endpoint_grant_alive_for_stream(monkeypatch):
    monkeypatch.setenv("ISHTI_LLM_PROVIDER", "api")
    monkeypatch.setattr(privacy, "internet", lambda: True)
    monkeypatch.setattr(llm, "api_settings", lambda: {"provider": "anthropic", "model": "synthetic"})
    def stream():
        assert audit.current().get("inference_endpoint") == ("api.anthropic.com", 443)
        yield "token"
    monkeypatch.setattr(llm, "_create_call", lambda args, sdk: stream())
    assert list(llm._create(model="synthetic", messages=[], stream=True)) == ["token"]
    assert not audit.current().get("inference_endpoint")


def test_anthropic_compatibility_omits_rejected_sampling_controls(monkeypatch):
    monkeypatch.setenv("ISHTI_LLM_PROVIDER", "api")
    monkeypatch.setattr(privacy, "internet", lambda: True)
    monkeypatch.setattr(llm, "api_settings", lambda: {"provider": "anthropic", "model": "synthetic"})
    seen = {}
    monkeypatch.setattr(llm, "_create_call", lambda args, sdk: seen.update(args) or "synthetic reply")
    assert llm._create(model="synthetic", messages=[], temperature=0.2, top_p=0.9) == "synthetic reply"
    assert "temperature" not in seen and "top_p" not in seen


def test_api_local_index_needs_no_model_or_network(monkeypatch):
    monkeypatch.setattr(llm, "provider", lambda: "api")
    assert embeddings.backend() == "lexical"
    vectors = embeddings.lexical(["search_query: احمد لاہور", "search_document: احمد لاہور"])
    assert vectors[0] == vectors[1] and len(vectors[0]) == embeddings.DIMENSIONS
    assert abs(sum(x*x for x in vectors[0])-1) < 1e-6


def test_old_memory_beyond_previous_cap_remains_searchable(tmp_path, monkeypatch):
    old = memory._conn
    monkeypatch.setattr(memory, "_conn", None)
    monkeypatch.setattr(memory, "db_path", lambda: str(tmp_path / "memory.db"))
    try:
        for i in range(305):
            memory.semantic(f"unrelated record {i}", importance=1.0)
        memory.semantic("uniquecontractidentifier", importance=0.1)
        hits = retrieval.memory_hits("uniquecontractidentifier")
        assert any("uniquecontractidentifier" in h["row"]["content"] for h in hits)
    finally:
        if memory._conn:
            memory._conn.close()
        memory._conn = old


def test_background_queue_is_bounded_and_restores_context(monkeypatch):
    import queue
    from ishti import background
    monkeypatch.setattr(background, "_queue", queue.Queue(maxsize=1))
    monkeypatch.setattr(background, "_threads", [SimpleNamespace()])
    monkeypatch.setattr(background, "_closing", False)
    assert background.submit(lambda: None)
    assert not background.submit(lambda: None)
    assert background.snapshot()["queued"] == 1
    assert background.snapshot()["capacity"] == 1


def test_background_cost_updates_lifecycle_without_retaining_prompt():
    from ishti import calltrace
    calltrace.begin("private arbitrary prompt")
    ledger = calltrace.current()
    calltrace.pending(1)
    response = calltrace.end()
    assert "private arbitrary prompt" not in str(response)
    calltrace.bind(ledger, background=True)
    calltrace.note("llm", "extractor", 1)
    calltrace.pending(-1)
    calltrace.bind(None)
    recent = calltrace.RECENT[-1]
    assert recent["request_id"] == response["request_id"]
    assert recent["llm_calls"] == 1 and recent["background_calls"] == 1
    assert recent["lifecycle_complete"] and recent["input_tokens"] is None


def test_key_saved_only_to_os_store(monkeypatch, tmp_path):
    from ishti import setup_choice
    import keyring
    seen = []
    monkeypatch.setattr(keyring, "set_password", lambda *args: seen.append(args))
    monkeypatch.setattr(setup_choice, "PATH", str(tmp_path / "setup.json"))
    setup_choice.save_key("anthropic", "synthetic-secret")
    setup_choice.save("api", "anthropic", "synthetic-model")
    assert seen == [("ishti-model-provider", "anthropic", "synthetic-secret")]
    assert "synthetic-secret" not in (tmp_path / "setup.json").read_text()


def test_explicit_ui_key_is_not_overridden_by_another_environment_key(monkeypatch, tmp_path):
    from ishti import setup_choice
    import keyring
    monkeypatch.setattr(setup_choice, "PATH", str(tmp_path/"setup.json"))
    monkeypatch.setenv("ISHTI_API_KEY", "another-provider-environment-key")
    monkeypatch.setattr(keyring, "get_password", lambda service, provider: "explicit-selected-key")
    setup_choice.save("api", "anthropic", "synthetic", credential_source="keyring")
    assert llm.api_key() == "explicit-selected-key"
    setup_choice.save("api", "anthropic", "new-selected-model")
    assert llm.api_key() == "explicit-selected-key"


def test_pdf_export_quits_word_when_document_close_fails(tmp_path, monkeypatch):
    from ishti.skills.docs import skill as docs
    quit_calls = []
    def close(*args):
        raise RuntimeError("synthetic close failure")
    document = SimpleNamespace(SaveAs2=lambda *a, **k: None, Close=close)
    word = SimpleNamespace(Documents=SimpleNamespace(Open=lambda *a, **k: document), Quit=lambda: quit_calls.append(True))
    monkeypatch.setattr(docs, "_word", lambda: word)
    with pytest.raises(RuntimeError, match="synthetic close failure"):
        docs.docx_to_pdf(str(tmp_path/"source.docx"), str(tmp_path/"output.pdf"))
    assert quit_calls == [True] and not (tmp_path/"output.pdf").exists()


def test_word_automation_disables_document_macros(monkeypatch):
    from ishti.skills.docs import skill as docs
    import pythoncom
    import win32com.client
    word = SimpleNamespace(Quit=lambda: None)
    monkeypatch.setattr(pythoncom, "CoInitialize", lambda: None)
    monkeypatch.setattr(win32com.client, "DispatchEx", lambda *a: word)
    assert docs._word().AutomationSecurity == 3


def test_missing_optional_analysis_does_not_disable_basic_documents(monkeypatch):
    import importlib.util
    from ishti.skills import Skill
    from ishti.skills.docs import skill as docs
    original = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None if name in ("pandas", "matplotlib") else original(name))
    catalog = Skill("docs")
    assert catalog.status == "partial" and catalog.module is not None
    assert catalog.info()["missing_optional"] == ["pandas", "matplotlib"]
    out = docs.run("make a chart of sales in C:/synthetic.xlsx", {})
    assert out["execution_blocked"] and out["verified"] is False


def test_pdf_export_reopens_before_publish_and_preserves_existing(tmp_path, monkeypatch):
    from ishti.skills.docs import skill as docs
    from pypdf import PdfWriter
    src = tmp_path / "report.docx"
    src.write_bytes(b"synthetic input")
    dst = tmp_path / "report.pdf"
    dst.write_bytes(b"existing owner output")
    def save(path, **kw):
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        with open(path, "wb") as output:
            writer.write(output)
    document = SimpleNamespace(SaveAs2=save, Close=lambda *a: None)
    word = SimpleNamespace(Documents=SimpleNamespace(Open=lambda *a, **k: document), Quit=lambda: None)
    monkeypatch.setattr(docs, "_word", lambda: word)
    actual = docs.docx_to_pdf(str(src), str(dst))
    assert actual != str(dst) and dst.read_bytes() == b"existing owner output"
    assert __import__("pathlib").Path(actual).is_file()


def test_api_installer_saves_choice_before_packages_and_skips_local_downloads(tmp_path, monkeypatch):
    from tools import install
    monkeypatch.setattr(install, "ROOT", str(tmp_path))
    monkeypatch.setattr(install, "config", lambda: {"llm": {"api": {"provider": "openai", "model": "synthetic"}}})
    args = SimpleNamespace(provider="api", api_provider="openai", model="synthetic", voice=False, check=False, yes=True)
    install.configure_install(args)
    assert install.REQS.endswith("requirements-api.txt")
    import json
    assert json.loads((tmp_path/"data/setup.json").read_text())["provider"] == "api"
    for name in ("ollama_exe", "start_ollama", "models_step", "voice_step"):
        monkeypatch.setattr(install, name, lambda *a: pytest.fail("API mode attempted a local model/voice step"))
    monkeypatch.setattr(install, "run_logged", lambda *a, **k: (0, ""))
    monkeypatch.setattr(install, "bridge_step", lambda: None)
    assert install.phase2() == 0


def test_api_dependency_manifest_has_no_gpu_or_voice_stack():
    from pathlib import Path
    reqs = (Path(__file__).resolve().parents[1]/"requirements-api.txt").read_text()
    for name in ("torch==", "torchaudio==", "faster-whisper==", "transformers==", "piper-tts==", "chromadb=="):
        assert name not in reqs


def test_api_recall_store_is_encrypted_local_and_forgettable(tmp_path):
    from ishti.lexical_store import LexicalStore
    store = LexicalStore(str(tmp_path/'recall'))
    store.add('User: contractidentifier synthetic private document')
    assert store.count() == 1
    assert store.recall('contractidentifier synthetic private document')
    assert b'contractidentifier' not in store.path.read_bytes()
    restored = LexicalStore(str(tmp_path/'recall'))
    assert restored.recall('contractidentifier synthetic private document')
    assert restored.delete_where_text('contractidentifier') == 1
    assert restored.count() == 0
