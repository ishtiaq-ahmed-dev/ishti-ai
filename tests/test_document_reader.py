import io
import subprocess
import zipfile

import pytest

from ishti.document_reader import DocumentReadError, read_document


def test_text_snapshot_and_character_bound(tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text("αβγ\nInvoice 12", encoding="utf-8")
    assert read_document(source, limit=5) == "αβγ\nI"
    assert read_document(source, limit=0) == ""


def test_docx_paragraphs_and_tables(tmp_path):
    from docx import Document
    document = Document()
    document.add_paragraph("Invoice 314")
    cells = document.add_table(rows=1, cols=2).rows[0].cells
    cells[0].text, cells[1].text = "PKR", "9000"
    source = tmp_path / "invoice.docx"
    document.save(source)
    assert read_document(source) == "Invoice 314\nPKR | 9000"


def test_pdf_native_text_and_closed_handles(tmp_path):
    from reportlab.pdfgen import canvas
    source = tmp_path / "invoice.pdf"
    pdf = canvas.Canvas(str(source))
    pdf.drawString(50, 700, "Synthetic invoice 314. Amount PKR 9000. Due 12 October 2026.")
    pdf.save()
    assert "Amount PKR 9000" in read_document(source)
    source.unlink()  # Windows cannot unlink a source still held by a parser.


@pytest.mark.parametrize("extension", [".csv", ".xlsx", ".pptx"])
def test_other_office_formats(tmp_path, extension):
    source = tmp_path / ("synthetic" + extension)
    if extension == ".pptx":
        from pptx import Presentation
        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[1])
        slide.shapes.title.text = "Synthetic report 314"
        presentation.save(source)
    else:
        import pandas as pd
        frame = pd.DataFrame({"invoice": [314], "amount": [9000]})
        if extension == ".csv":
            frame.to_csv(source, index=False)
        else:
            frame.to_excel(source, index=False)
    assert "314" in read_document(source)


def test_damaged_document_fails_clearly(tmp_path):
    source = tmp_path / "broken.docx"
    source.write_bytes(b"not an Office archive")
    with pytest.raises(DocumentReadError, match="extraction failed"):
        read_document(source)


def test_timeout_terminates_reader(tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text("synthetic")
    with pytest.raises(DocumentReadError, match="timed out"):
        read_document(source, timeout=0.01)


def test_oversized_file_rejected_before_worker(tmp_path, monkeypatch):
    from ishti import document_reader
    source = tmp_path / "notes.txt"
    source.write_bytes(b"0123456789")
    monkeypatch.setattr(document_reader, "MAX_FILE_BYTES", 5)
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: pytest.fail("worker must not start"))
    with pytest.raises(DocumentReadError, match="reader limit"):
        read_document(source)


def test_archive_expansion_bound(monkeypatch):
    from ishti import document_reader_worker as worker
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("document.xml", "x" * 100)
    monkeypatch.setattr(worker, "MAX_ARCHIVE_BYTES", 50)
    with pytest.raises(ValueError, match="archive exceeds"):
        worker.extract(".docx", data.getvalue(), 100)


def test_network_blocked():
    from ishti.document_reader_worker import _deny_network
    with pytest.raises(RuntimeError, match="cannot use the network"):
        _deny_network(("example.invalid", 443))


def test_invalid_or_oversized_worker_output_rejected(tmp_path, monkeypatch):
    from ishti import document_reader
    source = tmp_path / "notes.txt"
    source.write_text("synthetic")
    for output in (b"not json", b'{"text":123}', b'{"text":"too long"}', b"x" * 20):
        monkeypatch.setattr(document_reader, "MAX_OUTPUT_BYTES", 15)
        monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: subprocess.CompletedProcess([], 0, output))
        with pytest.raises(DocumentReadError):
            read_document(source, limit=3)
