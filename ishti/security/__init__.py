"""Security subsystem for ISHTI.
Provides upload scanning, malware protection, and encrypted-at-rest media vault.
"""
from ishti.security.upload_scanner import scan_upload, is_safe_extension, validate_magic_bytes
from ishti.security.media_vault import store_media, get_media, purge_chat_media, cleanup_expired_media

__all__ = [
    "scan_upload",
    "is_safe_extension",
    "validate_magic_bytes",
    "store_media",
    "get_media",
    "purge_chat_media",
    "cleanup_expired_media",
]
