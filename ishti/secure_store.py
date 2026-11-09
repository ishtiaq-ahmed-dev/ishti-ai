"""Authenticated storage for new sensitive records; no production plaintext-key fallback.

Tests use an isolated scratch key and never create/read the owner's OS keychain.
Legacy plaintext payloads can be read for controlled migration; new writes seal.
"""
import os
import threading

PREFIX = "sealed1:"
_lock = threading.RLock()


def _cipher():
    from cryptography.fernet import Fernet
    from ishti import test_mode
    with _lock:
        key = os.environ.get("ISHTI_STORAGE_KEY")
        if not key and test_mode.enabled():
            folder = test_mode.scratch_dir()
            if not folder:
                raise RuntimeError("Sensitive-storage tests require isolated scratch storage.")
            path = os.path.join(folder, ".test-storage.key")
            try:
                with open(path, "rb") as f:
                    key = f.read()
            except FileNotFoundError:
                key = Fernet.generate_key()
                with open(path, "xb") as f:
                    f.write(key)
        if not key:
            try:
                import keyring
                key = keyring.get_password("ishti-sensitive-storage", "data-key")
                if not key:
                    key = Fernet.generate_key().decode("ascii")
                    keyring.set_password("ishti-sensitive-storage", "data-key", key)
            except Exception:
                raise RuntimeError("The OS credential store is unavailable; sensitive data was not written in plaintext.") from None
        try:
            return Fernet(key.encode("ascii") if isinstance(key, str) else key)
        except Exception:
            raise RuntimeError("The configured sensitive-storage key is invalid.") from None


def restrict_to_owner(path) -> bool:
    """Make a secret file readable only by the current Windows user and SYSTEM (protected DACL, no inherited
    'Users'/'Authenticated Users' entries). -> True when applied. Best effort: never raises, and a no-op off Windows."""
    if os.name != "nt":
        try:
            os.chmod(path, 0o600)
            return True
        except OSError:
            return False
    try:
        import ntsecuritycon as con
        import win32api
        import win32security
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
        user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        system = win32security.CreateWellKnownSid(win32security.WinLocalSystemSid)
        acl = win32security.ACL()
        for sid in (user, system):
            acl.AddAccessAllowedAce(win32security.ACL_REVISION, con.FILE_ALL_ACCESS, sid)
        win32security.SetNamedSecurityInfo(
            str(path), win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
            None, None, acl, None)
        return True
    except Exception:
        return False


def seal_text(text):
    return PREFIX + _cipher().encrypt(text.encode("utf-8")).decode("ascii")


def open_text(text):
    if not text.startswith(PREFIX):
        if os.environ.get("ISHTI_MIGRATE_PLAINTEXT_WORKFLOWS") == "1":
            return text  # explicit, temporary migration; next successful save seals it
        raise RuntimeError("Unsealed workflow record rejected; explicit migration is required.")
    try:
        return _cipher().decrypt(text[len(PREFIX):].encode("ascii")).decode("utf-8")
    except Exception:
        raise RuntimeError("This record could not be authenticated/decrypted; no stored action was replayed.") from None
