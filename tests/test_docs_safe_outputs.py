from __future__ import annotations

import datetime as dt
from pathlib import Path

import pikepdf
import pytest
from docx import Document
from pptx import Presentation
from reportlab.pdfgen import canvas

from ishti.skills.docs import skill


def _pdf(path: Path, text: str = "CERULEAN-42") -> None:
    page = canvas.Canvas(str(path))
    page.drawString(72, 720, text)
    page.save()


def test_flag_off_preserves_legacy_docx_overwrite(monkeypatch, tmp_path):
    monkeypatch.setenv(skill.SAFE_OUTPUTS_FLAG, "0")
    monkeypatch.setattr(skill, "HOME", str(tmp_path))
    monkeypatch.setattr(skill.llm, "ask", lambda *_a, **_k: "# Same topic\nnew body")
    target = tmp_path / f"Same topic {dt.date.today()}.docx"
    target.write_bytes(b"legacy sentinel")

    result = skill.op_make_doc(None, "create a document about Same topic", {})

    assert str(target) in result
    assert target.read_bytes() != b"legacy sentinel"


def test_flag_on_versions_and_reopens_docx(monkeypatch, tmp_path):
    monkeypatch.setenv(skill.SAFE_OUTPUTS_FLAG, "1")
    monkeypatch.setattr(skill, "HOME", str(tmp_path))
    monkeypatch.setattr(skill.llm, "ask", lambda *_a, **_k: "# Same topic\nCERULEAN-42 body")
    original = tmp_path / f"Same topic {dt.date.today()}.docx"
    original.write_bytes(b"owner sentinel")

    result = skill.op_make_doc(None, "create a document about Same topic", {})
    created = tmp_path / f"Same topic {dt.date.today()}_1.docx"

    assert str(created) in result
    assert original.read_bytes() == b"owner sentinel"
    assert "CERULEAN-42" in "\n".join(p.text for p in Document(created).paragraphs)
    assert not list(tmp_path.glob(".ishti-*"))


def test_flag_on_versions_and_reopens_pptx(monkeypatch, tmp_path):
    monkeypatch.setenv(skill.SAFE_OUTPUTS_FLAG, "true")
    monkeypatch.setattr(skill, "HOME", str(tmp_path))
    monkeypatch.setattr(
        skill.llm,
        "ask",
        lambda *_a, **_k: '{"title":"Same deck","subtitle":"CERULEAN-42","slides":[{"title":"One","bullets":["proof"]}]}',
    )
    original = tmp_path / f"Same deck {dt.date.today()}.pptx"
    original.write_bytes(b"owner sentinel")

    result = skill.op_make_deck(None, "create a presentation about Same deck", {})
    created = tmp_path / f"Same deck {dt.date.today()}_1.pptx"

    assert str(created) in result
    assert original.read_bytes() == b"owner sentinel"
    reopened = Presentation(created)
    assert reopened.slides[0].shapes.title.text == "Same deck"
    assert not list(tmp_path.glob(".ishti-*"))


def test_flag_on_pdf_split_uses_unique_folder_and_preserves_existing(monkeypatch, tmp_path):
    monkeypatch.setenv(skill.SAFE_OUTPUTS_FLAG, "yes")
    source = tmp_path / "source.pdf"
    _pdf(source)
    old = tmp_path / "source_pages"
    old.mkdir()
    sentinel = old / "page_001.pdf"
    sentinel.write_bytes(b"owner sentinel")

    result = skill.op_pdf_split(None, f"split {source}", {})
    created_dir = tmp_path / "source_pages_1"
    created = created_dir / "page_001.pdf"

    assert str(created_dir) in result
    assert sentinel.read_bytes() == b"owner sentinel"
    with pikepdf.open(created) as pdf:
        assert len(pdf.pages) == 1


def test_atomic_failure_removes_reservation_and_temp(monkeypatch, tmp_path):
    monkeypatch.setenv(skill.SAFE_OUTPUTS_FLAG, "on")
    target = str(tmp_path / "broken.docx")

    def fail(path):
        Path(path).write_bytes(b"partial")
        raise RuntimeError("synthetic failure")

    try:
        skill._atomic_save_new(target, fail)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected failure")

    assert not Path(target).exists()
    assert not list(tmp_path.glob(".ishti-*"))


def test_atomic_publish_never_replaces_a_racing_target(tmp_path):
    target = tmp_path / "race.docx"

    def race(path):
        Path(path).write_bytes(b"our complete file")
        target.write_bytes(b"foreign owner file")

    try:
        skill._atomic_save_new(str(target), race)
    except (FileExistsError, PermissionError):
        pass
    else:
        raise AssertionError("no-replace publication unexpectedly overwrote the target")

    assert target.read_bytes() == b"foreign owner file"
    assert not list(tmp_path.glob(".ishti-*"))


def test_directory_collision_versions_instead_of_failing(tmp_path):
    occupied = tmp_path / "report.docx"
    occupied.mkdir()
    assert skill._versioned_path(str(occupied)).endswith("report_1.docx")


def test_temp_cleanup_failure_does_not_publish_duplicate_versions(monkeypatch, tmp_path):
    target = tmp_path / "report.docx"
    real_remove = skill.os.remove

    def locked_temp(path):
        if Path(path).name.startswith(".ishti-"):
            raise PermissionError("synthetic sharing lock")
        return real_remove(path)

    monkeypatch.setattr(skill.os, "remove", locked_temp)
    made = skill._atomic_unique_save(str(target), lambda path: Path(path).write_bytes(b"complete"))

    assert Path(made).read_bytes() == b"complete"
    assert not (tmp_path / "report_1.docx").exists()


def test_no_clobber_publication_checks_destination_scope(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(skill.scope, "check_write", lambda path, what: seen.append((path, what)))
    target = tmp_path / "scoped.docx"

    skill._atomic_save_new(str(target), lambda path: Path(path).write_bytes(b"complete"))

    assert seen == [(str(target), "create"), (str(target), "create")]


def test_scope_denial_happens_before_temp_creation_or_save(monkeypatch, tmp_path):
    target = tmp_path / "denied.docx"
    called = False

    def deny(_path, _what):
        raise skill.scope.ScopeError("synthetic denial")

    def save(_path):
        nonlocal called
        called = True

    monkeypatch.setattr(skill.scope, "check_write", deny)

    with pytest.raises(skill.scope.ScopeError, match="synthetic denial"):
        skill._atomic_save_new(str(target), save)

    assert called is False
    assert not target.exists()
    assert not list(tmp_path.glob(".ishti-*"))
