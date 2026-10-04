"""Unit tests for encrypted-at-rest media vault, retention, and per-chat purge."""
import os
import time
import pytest
from ishti.security.media_vault import (
    store_media,
    get_media,
    list_chat_media,
    purge_chat_media,
    cleanup_expired_media,
)


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
