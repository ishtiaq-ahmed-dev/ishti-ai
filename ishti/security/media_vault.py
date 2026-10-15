"""Encrypted-at-rest Media Vault with Per-Chat Retention and Purge Policy.

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
from ishti import ROOT, test_mode

VAULT_DIR = test_mode.scratch_path(os.path.join(ROOT, "data", "media_vault"))
KEY_FILE = test_mode.scratch_path(os.path.join(ROOT, "data", ".vault_key"))
INDEX_FILE = os.path.join(VAULT_DIR, "vault_index.json")

DEFAULT_RETENTION_DAYS = 30
_LOCK = threading.RLock()
_AESGCM_KEY: Optional[bytes] = None
_AESGCM_KEY_FILE: Optional[str] = None


class VaultError(RuntimeError):
    pass


def _get_or_create_key() -> bytes:
    """Retrieves or initializes the 256-bit AES-GCM master vault key."""
    global _AESGCM_KEY, _AESGCM_KEY_FILE
    with _LOCK:
        if _AESGCM_KEY is not None and _AESGCM_KEY_FILE == KEY_FILE:
            return _AESGCM_KEY

        os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
        if os.path.exists(KEY_FILE):
            with open(KEY_FILE, "rb") as f:
                key = f.read()
                if len(key) == 32:
                    _AESGCM_KEY = key
                    _AESGCM_KEY_FILE = KEY_FILE
                    return _AESGCM_KEY
                raise VaultError("Vault key is invalid; existing key and ciphertext were preserved.")

        if _load_index():
            raise VaultError("Vault key is missing; restore the original key before writing media.")

        # Generate new random 256-bit key
        new_key = AESGCM.generate_key(bit_length=256)
        with open(KEY_FILE, "xb") as f:
            f.write(new_key)
        try:
            # Set read/write only for owner on platforms supporting chmod
            os.chmod(KEY_FILE, 0o600)
        except Exception:
            swallowed('ishti/security/media_vault.py:_get_or_create_key:50')

        _AESGCM_KEY = new_key
        _AESGCM_KEY_FILE = KEY_FILE
        return _AESGCM_KEY


def _load_index() -> Dict[str, Any]:
    if not os.path.exists(INDEX_FILE):
        return {}
    try:
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or any(not isinstance(rec, dict) for rec in data.values()):
            raise ValueError("invalid index shape")
        return data
    except (OSError, ValueError) as exc:
        raise VaultError("Vault index is unreadable; existing records were preserved.") from exc


def _media_path(media_id, record):
    name = record.get("enc_file", f"{media_id}.enc")
    if not isinstance(name, str) or name != os.path.basename(name) or "\\" in name:
        raise VaultError("Vault record contains an unsafe ciphertext path.")
    path = os.path.realpath(os.path.join(VAULT_DIR, name))
    if os.path.commonpath([os.path.realpath(VAULT_DIR), path]) != os.path.realpath(VAULT_DIR):
        raise VaultError("Vault ciphertext path escapes its storage directory.")
    return path


def _remove_record(index, media_id):
    path = _media_path(media_id, index[media_id])
    try:
        os.remove(path)
    except FileNotFoundError as exc:
        # Ciphertext already gone (crash between index write and unlink, or manual cleanup):
        # dropping the index row below is right, but the index/file mismatch must be visible.
        swallowed("media_vault:remove_record:missing_ciphertext", exc)
    except OSError as exc:
        swallowed("media_vault:remove_record", exc)
        return False
    del index[media_id]
    return True


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
    chat_id = str(chat_id).strip()
    if not chat_id:
        raise ValueError("A nonempty chat ID is required.")
    with _LOCK:
        _load_index()
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

    enc_path = _media_path(media_id, record)
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
    except Exception as exc:
        # A media row that can no longer be decrypted is reported as missing, never raised -
        # but the loss must be visible in the runtime log, not silent.
        swallowed("ishti/security/media_vault.py:get_media:decrypt", exc)
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
            deleted_count += int(_remove_record(idx, mid))
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
            deleted_count += int(_remove_record(idx, mid))
        if to_delete:
            _save_index(idx)

    return deleted_count


def get_vault_stats() -> Dict[str, Any]:
    """Returns storage metrics and policy metadata for the encrypted vault."""
    with _LOCK:
        idx = _load_index()
        total_items = len(idx)
        total_bytes = sum(rec.get("enc_size", rec.get("size_bytes", 0)) for rec in idx.values())
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
        for mid in list(idx):
            deleted_count += int(_remove_record(idx, mid))
        _save_index(idx)
    return deleted_count
