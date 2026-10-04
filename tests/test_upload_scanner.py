"""Unit tests for upload scanner, malware prevention and file signature verification."""
import os
import zipfile
import pytest
from ishti.security.upload_scanner import (
    is_safe_extension,
    validate_magic_bytes,
    check_archive_safety,
    scan_upload,
)


def test_disallowed_extensions():
    assert not is_safe_extension("malware.exe")
    assert not is_safe_extension("script.bat")
    assert not is_safe_extension("payload.vbs")
    assert not is_safe_extension("shell.ps1")
    assert is_safe_extension("document.pdf")
    assert is_safe_extension("photo.png")
    assert is_safe_extension("archive.zip")


def test_magic_bytes_detection(tmp_path):
    # Valid PDF
    ok, _ = validate_magic_bytes(b"%PDF-1.7\n%...", "doc.pdf")
    assert ok

    # Valid PNG
    ok, _ = validate_magic_bytes(b"\x89PNG\r\n\x1a\n\x00...", "image.png")
    assert ok

    # Executable disguised as PDF
    ok, reason = validate_magic_bytes(b"MZ\x90\x00\x03\x00\x00\x00", "invoice.pdf")
    assert not ok
    assert "DOS/Windows Executable" in reason

    # Linux ELF disguised as PNG
    ok, reason = validate_magic_bytes(b"\x7fELF\x02\x01\x01\x00", "avatar.png")
    assert not ok
    assert "Linux ELF Executable" in reason


def test_zip_bomb_prevention(tmp_path):
    # Create safe zip
    safe_zip = tmp_path / "safe.zip"
    with zipfile.ZipFile(safe_zip, "w") as zf:
        zf.writestr("test.txt", "Hello World! " * 50)

    ok, msg = check_archive_safety(str(safe_zip))
    assert ok
    assert "safe" in msg.lower()


def test_archive_path_traversal_prevention(tmp_path):
    # Create malicious zip with path traversal
    bad_zip = tmp_path / "traversal.zip"
    with zipfile.ZipFile(bad_zip, "w") as zf:
        zf.writestr("../../../etc/passwd", "evil content")

    ok, reason = check_archive_safety(str(bad_zip))
    assert not ok
    assert "Path traversal attempt detected" in reason


def test_scan_upload_end_to_end(tmp_path):
    pdf_file = tmp_path / "report.pdf"
    pdf_file.write_bytes(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF")

    res = scan_upload(str(pdf_file))
    assert res["safe"] is True
    assert res["filename"] == "report.pdf"
    assert "Magic bytes verified" in res["signature_status"]
