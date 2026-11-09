"""Unit tests for encrypted-at-rest media vault, retention, and per-chat purge."""
import os
import time
import json
import subprocess
import sys
from pathlib import Path
import pytest
from ishti.security import media_vault
from ishti.security.media_vault import (
    store_media,
    get_media,
    list_chat_media,
    purge_chat_media,
    cleanup_expired_media,
)


@pytest.fixture(autouse=True)
def isolated_vault(tmp_path, monkeypatch):
    folder = tmp_path / "vault"
    monkeypatch.setattr(media_vault, "VAULT_DIR", str(folder))
    monkeypatch.setattr(media_vault, "INDEX_FILE", str(folder / "vault_index.json"))
    monkeypatch.setattr(media_vault, "KEY_FILE", str(tmp_path / "vault.key"))
    monkeypatch.setattr(media_vault, "_AESGCM_KEY", None)
    monkeypatch.setattr(media_vault, "_AESGCM_KEY_FILE", None)


def test_store_and_retrieve_encrypted_media(tmp_path):
    chat_id = "test_chat_123"
    payload = b"Top secret voice note recording bytes"
    filename = "voice_note.ogg"

    record = store_media(chat_id, payload, filename, "audio/ogg", ttl_days=7)
    assert record["chat_id"] == chat_id
    assert record["filename"] == filename
    assert record["raw_size"] == len(payload)
    media_id = record["media_id"]

    # Verify decryption
    decrypted = get_media(chat_id, media_id)
    assert decrypted == payload

    # Verify cross-chat unauthorized access is blocked
    unauthorized = get_media("other_chat_999", media_id)
    assert unauthorized is None


def test_purge_chat_media(tmp_path):
    chat_id = "purge_test_chat"
    payload1 = b"Attachment 1"
    payload2 = b"Attachment 2"

    rec1 = store_media(chat_id, payload1, "doc1.pdf", ttl_days=30)
    rec2 = store_media(chat_id, payload2, "doc2.pdf", ttl_days=30)

    items = list_chat_media(chat_id)
    assert len(items) >= 2

    # Purge all media for this chat
    deleted = purge_chat_media(chat_id)
    assert deleted >= 2

    # Verify both are deleted
    assert get_media(chat_id, rec1["media_id"]) is None
    assert get_media(chat_id, rec2["media_id"]) is None
    assert len(list_chat_media(chat_id)) == 0


def test_cleanup_expired_media(tmp_path):
    chat_id = "expiry_test_chat"
    payload = b"Expiring media content"

    # Store with negative/past TTL
    rec = store_media(chat_id, payload, "temp.txt", ttl_days=-1)
    media_id = rec["media_id"]

    cleaned = cleanup_expired_media()
    assert cleaned >= 1
    assert get_media(chat_id, media_id) is None


@pytest.mark.parametrize("content", [b"{broken", b"[]", b'{"bad": 5}'])
def test_corrupt_index_is_preserved(content):
    Path(media_vault.VAULT_DIR).mkdir()
    index = Path(media_vault.INDEX_FILE)
    index.write_bytes(content)
    with pytest.raises(media_vault.VaultError, match="index"):
        store_media("chat", b"payload", "note.txt")
    assert index.read_bytes() == content
    assert not Path(media_vault.KEY_FILE).exists()
    assert list(Path(media_vault.VAULT_DIR).glob("*.enc")) == []


def test_invalid_key_is_preserved():
    key = Path(media_vault.KEY_FILE)
    key.write_bytes(b"damaged-key")
    with pytest.raises(media_vault.VaultError, match="key is invalid"):
        store_media("chat", b"payload", "note.txt")
    assert key.read_bytes() == b"damaged-key"


def test_missing_key_never_replaces_existing_ciphertext(monkeypatch):
    record = store_media("chat", b"payload", "note.txt")
    index_before = Path(media_vault.INDEX_FILE).read_bytes()
    ciphertext = Path(media_vault.VAULT_DIR) / record["enc_file"]
    before = ciphertext.read_bytes()
    Path(media_vault.KEY_FILE).unlink()
    monkeypatch.setattr(media_vault, "_AESGCM_KEY", None)
    with pytest.raises(media_vault.VaultError, match="key is missing"):
        store_media("chat", b"new", "other.txt")
    assert not Path(media_vault.KEY_FILE).exists()
    assert ciphertext.read_bytes() == before
    assert Path(media_vault.INDEX_FILE).read_bytes() == index_before


@pytest.mark.parametrize("operation", ["chat", "all", "expired"])
def test_failed_delete_retains_index_record(monkeypatch, operation):
    record = store_media("chat", b"payload", "note.txt", ttl_days=-1)
    remove = media_vault.os.remove
    target = Path(media_vault.VAULT_DIR) / record["enc_file"]

    def denied(path, *args, **kwargs):
        if str(path) == str(target):
            raise PermissionError("synthetic denied delete")
        return remove(path, *args, **kwargs)

    monkeypatch.setattr(media_vault.os, "remove", denied)
    operation_fn = {"chat": lambda: purge_chat_media("chat"),
                    "all": media_vault.purge_all_media, "expired": cleanup_expired_media}[operation]
    assert operation_fn() == 0
    assert target.exists()
    assert list_chat_media("chat")[0]["media_id"] == record["media_id"]


def test_chat_id_normalization_and_measured_size():
    rec = store_media(" chat ", b"payload", "note.txt")
    assert get_media("chat", rec["media_id"]) == b"payload"
    assert media_vault.get_vault_stats()["total_bytes"] == rec["enc_size"]


def test_unsafe_index_path_never_deletes_outside(tmp_path):
    outside = tmp_path / "outside.enc"
    outside.write_bytes(b"sentinel")
    Path(media_vault.VAULT_DIR).mkdir()
    Path(media_vault.INDEX_FILE).write_text(json.dumps({"bad": {
        "chat_id": "chat", "enc_file": str(outside)
    }}))
    with pytest.raises(media_vault.VaultError, match="unsafe"):
        media_vault.purge_all_media()
    assert outside.read_bytes() == b"sentinel"


def test_scratch_defaults_leave_simulated_owner_vault_untouched(tmp_path):
    code = '''
import json, sys
from pathlib import Path
import ishti
ishti.ROOT = sys.argv[1]
owner = Path(ishti.ROOT) / "data"
vault = owner / "media_vault"
vault.mkdir(parents=True)
key = owner / ".vault_key"
key.write_bytes(b"owner-key-sentinel")
index = vault / "vault_index.json"
index.write_text(json.dumps({"old": {"chat_id":"owner", "expires_at":1, "enc_file":"old.enc"}}))
(vault / "old.enc").write_bytes(b"owner-ciphertext-sentinel")
before = index.read_bytes()
from ishti.security import media_vault
assert "_work" in media_vault.VAULT_DIR
assert "_work" in media_vault.KEY_FILE
assert media_vault.cleanup_expired_media() == 0
assert index.read_bytes() == before
assert key.read_bytes() == b"owner-key-sentinel"
assert (vault / "old.enc").read_bytes() == b"owner-ciphertext-sentinel"
'''
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path / "owner")],
                            # the per-pid default ("_work/scratch"), not the suite's pinned scratch folder
                            env={**{k: v for k, v in os.environ.items() if k != "ISHTI_SCRATCH_DIR"},
                                 "ISHTI_TEST_MODE": "1", "ISHTI_SCRATCH": "1"},
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
