"""Encrypted-at-rest Media Vault with Per-Chat Retention and Purge Policy (NEEDS.md Item 7).

Guarantees:
1. AES-256-GCM authenticated encryption for all media stored on disk.
2. Isolated per-chat storage and index tracking.
3. Configurable retention (TTL in days) per chat or globally.
4. Immediate delete-all / purge behavior for any chat.
5. Automated background cleanup of expired media items.
"""
from ishti.core.logutil import swallowed
import os
import json
import time
import uuid
import hashlib
import threading
from typing import Dict, Any, List, Optional
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VAULT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "media_vault")
KEY_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", ".vault_key")
INDEX_FILE = os.path.join(VAULT_DIR, "vault_index.json")

DEFAULT_RETENTION_DAYS = 30
_LOCK = threading.RLock()
_AESGCM_KEY: Optional[bytes] = None


def _get_or_create_key() -> bytes:
    """Retrieves or initializes the 256-bit AES-GCM master vault key."""
    global _AESGCM_KEY
    with _LOCK:
        if _AESGCM_KEY is not None:
            return _AESGCM_KEY

        os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
        if os.path.exists(KEY_FILE):
            with open(KEY_FILE, "rb") as f:
                key = f.read()
                if len(key) == 32:
                    _AESGCM_KEY = key
                    return _AESGCM_KEY

        # Generate new random 256-bit key
        new_key = AESGCM.generate_key(bit_length=256)
        with open(KEY_FILE, "wb") as f:
            f.write(new_key)
        try:
            # Set read/write only for owner on platforms supporting chmod
            os.chmod(KEY_FILE, 0o600)
        except Exception:
            swallowed('ishti/security/media_vault.py:_get_or_create_key:50')

        _AESGCM_KEY = new_key
        return _AESGCM_KEY


def _load_index() -> Dict[str, Any]:
    if not os.path.exists(INDEX_FILE):
        return {}
    try:
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_index(index_data: Dict[str, Any]) -> None:
    os.makedirs(VAULT_DIR, exist_ok=True)
    temp_path = INDEX_FILE + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(index_data, f, indent=2)
    os.replace(temp_path, INDEX_FILE)


def store_media(
    chat_id: str,
    data: bytes,
    filename: str,
    content_type: str = "application/octet-stream",
    ttl_days: Optional[int] = None
) -> Dict[str, Any]:
    """Encrypts and stores a media payload with defined chat association and TTL."""
    key = _get_or_create_key()
    aesgcm = AESGCM(key)

    nonce = os.urandom(12)  # 96-bit nonce for GCM
    aad = f"{chat_id}:{filename}".encode("utf-8")
    ciphertext = aesgcm.encrypt(nonce, data, aad)

    media_id = uuid.uuid4().hex[:16]
    sha256 = hashlib.sha256(data).hexdigest()
    now = time.time()
    retention_days = ttl_days if ttl_days is not None else DEFAULT_RETENTION_DAYS
    expires_at = now + (retention_days * 86400) if retention_days != 0 else 0

    os.makedirs(VAULT_DIR, exist_ok=True)
    enc_filename = f"{media_id}.enc"
    enc_path = os.path.join(VAULT_DIR, enc_filename)

    with open(enc_path, "wb") as f:
        f.write(nonce + ciphertext)

    record = {
        "media_id": media_id,
        "chat_id": str(chat_id).strip(),
        "filename": filename,
        "content_type": content_type,
        "raw_size": len(data),
        "enc_size": len(nonce + ciphertext),
        "sha256": sha256,
        "created_at": now,
        "expires_at": expires_at,
        "enc_file": enc_filename,
    }

    with _LOCK:
        idx = _load_index()
        idx[media_id] = record
        _save_index(idx)

    return record


def get_media(chat_id: str, media_id: str) -> Optional[bytes]:
    """Retrieves and decrypts a media payload for the authorized chat."""
    with _LOCK:
        idx = _load_index()
        record = idx.get(media_id)
        if not record:
            return None
        if record.get("chat_id") != str(chat_id).strip():
            return None  # Cross-chat access prohibited

    enc_path = os.path.join(VAULT_DIR, record.get("enc_file", f"{media_id}.enc"))
    if not os.path.exists(enc_path):
        return None

    try:
        with open(enc_path, "rb") as f:
            blob = f.read()
        nonce = blob[:12]
        ciphertext = blob[12:]

        key = _get_or_create_key()
        aesgcm = AESGCM(key)
        aad = f"{record['chat_id']}:{record['filename']}".encode("utf-8")
        return aesgcm.decrypt(nonce, ciphertext, aad)
    except Exception:
        return None


def list_chat_media(chat_id: str) -> List[Dict[str, Any]]:
    """Lists metadata for all active media items in a specific chat."""
    cid = str(chat_id).strip()
    with _LOCK:
        idx = _load_index()
        return [
            rec for rec in idx.values()
            if rec.get("chat_id") == cid
        ]


def purge_chat_media(chat_id: str) -> int:
    """Immediate delete-all behavior: permanently deletes all media belonging to a chat."""
    cid = str(chat_id).strip()
    deleted_count = 0

    with _LOCK:
        idx = _load_index()
        to_delete = [mid for mid, rec in idx.items() if rec.get("chat_id") == cid]
        for mid in to_delete:
            rec = idx.pop(mid)
            enc_path = os.path.join(VAULT_DIR, rec.get("enc_file", f"{mid}.enc"))
            if os.path.exists(enc_path):
                try:
                    os.remove(enc_path)
                    deleted_count += 1
                except Exception:
                    swallowed('ishti/security/media_vault.py:purge_chat_media:178')
        if to_delete:
            _save_index(idx)

    return deleted_count


def cleanup_expired_media() -> int:
    """Purges all media whose expiration date has passed."""
    now = time.time()
    deleted_count = 0

    with _LOCK:
        idx = _load_index()
        to_delete = [
            mid for mid, rec in idx.items()
            if rec.get("expires_at", 0) > 0 and rec["expires_at"] <= now
        ]
        for mid in to_delete:
            rec = idx.pop(mid)
            enc_path = os.path.join(VAULT_DIR, rec.get("enc_file", f"{mid}.enc"))
            if os.path.exists(enc_path):
                try:
                    os.remove(enc_path)
                    deleted_count += 1
                except Exception:
                    swallowed('ishti/security/media_vault.py:cleanup_expired_media:204')
        if to_delete:
            _save_index(idx)

    return deleted_count


def get_vault_stats() -> Dict[str, Any]:
    """Returns storage metrics and policy metadata for the encrypted vault."""
    with _LOCK:
        idx = _load_index()
        total_items = len(idx)
        total_bytes = sum(rec.get("size_bytes", 0) for rec in idx.values())
        chats = list(set(rec.get("chat_id", "") for rec in idx.values() if rec.get("chat_id")))
        return {
            "total_items": total_items,
            "file_count": total_items,
            "total_bytes": total_bytes,
            "retention_days": DEFAULT_RETENTION_DAYS,
            "encrypted": True,
            "algorithm": "AES-256-GCM",
            "active_chats": len(chats),
            "vault_dir": VAULT_DIR,
        }


def purge_all_media() -> int:
    """Permanently deletes all media files across all chats from the vault."""
    deleted_count = 0
    with _LOCK:
        idx = _load_index()
        for mid, rec in list(idx.items()):
            enc_path = os.path.join(VAULT_DIR, rec.get("enc_file", f"{mid}.enc"))
            if os.path.exists(enc_path):
                try:
                    os.remove(enc_path)
                    deleted_count += 1
                except Exception:
                    swallowed('ishti/security/media_vault.py:purge_all_media:242')
        _save_index({})
    return deleted_count

