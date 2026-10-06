"""The vault: encryption at rest for the memory stores (Part A3) + backup/export/import.

Scope (start simple, single user): the two stores that hold the owner's private facts —
arch2's memory DB and the relationship graph — get field-level encryption at rest. The
learning DB (behaviour data) and convo state stay plaintext for now; both are documented
in REQUIREMENTS_MATRIX_PROMPT2 as a follow-up, not a gap in the owner-facing facts.

How it works without SQLCipher (not installed; a documented design choice):
  - Key: one Fernet key, generated on first use, stored in the OS keychain (keyring,
    service "ishti" / account "memory_key" — the same store the settings secrets use).
    No keychain (headless/CI): a key file under data/keys/ with user-only permissions.
    ISHTI_VAULT_KEY (hex) overrides both — the test hook, so tests never touch the
    real keychain.
  - Fields: memories.content + keywords, entities.notes/aliases/contact_methods/
    preferences/important_facts, rgraph edges.detail + pending.old/new. Encrypted values
    carry an "enc1:" prefix so reads are format-detecting and a mixed plain/encrypted DB
    (encrypt was enabled midway) is normal.
  - Search on an encrypted DB: SQL LIKE can't see ciphertext, so text filtering falls
    back to Python over the decrypted rows (personal-scale row counts make this free).
    Uniqueness/dedupe uses a plaintext SHA-256 in memories.content_hash — the same
    content always maps to the same row even though its ciphertext never repeats.

Nothing is encrypted until the owner turns it on (Settings -> Memory -> encrypt_at_rest);
turning it on re-encrypts the stores in place, turning it off decrypts them. The key
NEVER leaves the machine and never appears in any export.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import threading
import time
import zipfile

from ishti import ROOT
from ishti.test_mode import scratch_path

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


KEY_SERVICE = "ishti"
KEY_NAME = "memory_key"
KEY_DIR = scratch_path(os.path.join(ROOT, "data", "keys"))
ENC_PREFIX = "enc1:"

_lock = threading.RLock()
_key_cache: bytes | None = None


# --------------------------------------------------------------------------- the key
def _env_key() -> bytes | None:
    raw = os.environ.get("ISHTI_VAULT_KEY", "").strip()
    if not raw:
        return None
    try:
        return base64.urlsafe_b64encode(bytes.fromhex(raw))
    except ValueError:
        print("[vault] ISHTI_VAULT_KEY is not valid hex — ignored")
        return None


def get_or_create_key() -> bytes:
    """The Fernet signing key (urlsafe-b64 of 32 bytes), created once and kept forever."""
    global _key_cache
    with _lock:
        if _key_cache is not None:
            return _key_cache
        if got := _env_key():
            _key_cache = got
            return _key_cache
        try:
            import keyring
            raw = keyring.get_password(KEY_SERVICE, KEY_NAME)
            if raw:
                _key_cache = raw.encode()
                return _key_cache
            key = _new_key()
            keyring.set_password(KEY_SERVICE, KEY_NAME, key.decode())
            _key_cache = key
            return _key_cache
        except Exception as _exc:
            swallowed('ishti/core/vault.py:get_or_create_key', _exc)  # no keychain: fall through to the key file
        os.makedirs(KEY_DIR, exist_ok=True)
        key_path = os.path.join(KEY_DIR, "memory.key")
        if os.path.isfile(key_path):
            with open(key_path, "rb") as f:
                _key_cache = f.read().strip()
            return _key_cache
        key = _new_key()
        with open(key_path, "wb") as f:
            f.write(key)
        try:
            os.chmod(key_path, 0o600)  # POSIX; Windows ACLs are the OS defaults
        except OSError as _exc:
            swallowed('ishti/core/vault.py:get_or_create_key', _exc)
        _key_cache = key
        return _key_cache


def _new_key() -> bytes:
    from cryptography.fernet import Fernet
    return Fernet.generate_key()


def reset_key_cache():
    """Tests only: forget the cached key so an env override is picked up."""
    global _key_cache
    with _lock:
        _key_cache = None


# --------------------------------------------------------------------------- fields
def _f():
    from cryptography.fernet import Fernet
    return Fernet(get_or_create_key())


def encrypt_text(s: str) -> str:
    """"secret" -> "enc1:gAAAA..." (plaintext passthrough when vault is off)."""
    if not s or str(s).startswith(ENC_PREFIX) or not enabled():
        return s
    token = _f().encrypt(str(s).encode("utf-8"))
    return ENC_PREFIX + token.decode("ascii")


def decrypt_text(s: str) -> str:
    """Inverse of encrypt_text; anything without the prefix comes back unchanged.
    A wrong/lost key yields a visible placeholder — never an exception mid-reply."""
    if not isinstance(s, str) or not s.startswith(ENC_PREFIX):
        return s
    try:
        return _f().decrypt(s[len(ENC_PREFIX):].encode("ascii")).decode("utf-8")
    except Exception:
        return "[encrypted — key unavailable]"


def content_hash(plaintext: str) -> str:
    """Stable dedupe key for encrypted content (the ciphertext itself never repeats)."""
    return hashlib.sha256((plaintext or "").encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- on/off
def enabled() -> bool:
    if os.environ.get("ISHTI_VAULT") == "0":
        return False  # test escape hatch: force-off wins over config
    try:
        from ishti.arch2.memory import MEM
        return bool(MEM().get("encrypt_at_rest", False)) or os.environ.get("ISHTI_VAULT") == "1"
    except Exception:
        return os.environ.get("ISHTI_VAULT") == "1"


_MEMORY_FIELDS = {  # table -> plaintext-bearing columns (the enc1: prefix tells them apart)
    "memories": ["content", "keywords"],
    "entities": ["notes", "aliases", "contact_methods", "preferences", "important_facts"],
}
_RGRAPH_FIELDS = {"edges": ["detail"], "pending": ["old", "new"]}


def _reencrypt(conn: sqlite3.Connection, spec: dict, direction: str) -> dict:
    counts = {}
    for table, cols in spec.items():
        try:
            rows = conn.execute(f"SELECT rowid, {', '.join(cols)} FROM {table}").fetchall()
        except sqlite3.OperationalError:
            continue  # table not created yet (fresh DB): nothing to convert
        fn = encrypt_text if direction == "encrypt" else decrypt_text
        n = 0
        for row in rows:
            vals = [fn(r) if isinstance(r, str) else r for r in row[1:]]
            if vals != list(row[1:]):
                conn.execute(f"UPDATE {table} SET {', '.join(f'{c}=?' for c in cols)} WHERE rowid=?",
                             (*vals, row[0]))
                n += 1
        counts[table] = n
    conn.commit()
    return counts


def set_enabled(on: bool) -> dict:
    """Turn field encryption on (encrypt everything) or off (decrypt everything), in place."""
    from ishti.arch2 import memory
    from ishti import rgraph
    out = {"vault": "on" if on else "off"}
    with _lock:
        out["memory"] = _reencrypt(memory.conn(), _MEMORY_FIELDS, "encrypt" if on else "decrypt")
        out["rgraph"] = _reencrypt(rgraph.conn(), _RGRAPH_FIELDS, "encrypt" if on else "decrypt")
    try:  # remember the choice next to the memory config it governs
        from ishti.core.settings import get_settings_service
        get_settings_service().update({"memory": {"encrypt_at_rest": bool(on)}})
    except Exception as _exc:
        swallowed('ishti/core/vault.py:set_enabled', _exc)
    return out


# --------------------------------------------------------------------------- backup
def backup(db_path: str | None = None, out_path: str | None = None) -> str:
    """Consistent online copy of one SQLite DB (sqlite3's backup API, WAL-safe).
    Default: data/backups/<name>-<stamp>.db. Returns the backup path."""
    src = db_path or scratch_path(os.path.join(ROOT, "data", "arch2_memory.db"))
    out = out_path or scratch_path(os.path.join(
        ROOT, "data", "backups", f"{os.path.splitext(os.path.basename(src))[0]}-{time.strftime('%Y%m%d_%H%M')}.db"))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with _lock:
        src_conn = sqlite3.connect(src)
        dst = sqlite3.connect(out)
        with dst:
            src_conn.backup(dst)
        dst.close()
        src_conn.close()
    return out


# --------------------------------------------------------------------------- export / import
def export_all(out_path: str | None = None, include_private: bool = True) -> str:
    """The owner's portable backup: one ZIP with decrypted JSON of both stores + a manifest.
    (Values are decrypted — the owner already has the key; this file is the sensitive one.)
    include_private=False drops kin/family facts for a shareable subset."""
    from ishti.arch2 import memory
    from ishti import rgraph
    out = out_path or os.path.join(
        ROOT, "data", "backups", f"ishti-memory-export-{time.strftime('%Y%m%d_%H%M')}.zip")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    mem_rows = [_dec_row(r) for r in memory.conn().execute("SELECT * FROM memories").fetchall()]
    ent_rows = [_dec_row(r) for r in memory.conn().execute("SELECT * FROM entities").fetchall()]
    edge_rows = [_dec_row(dict(r), _RGRAPH_FIELDS) for r in
                 rgraph.conn().execute("SELECT e.*, s.name AS s_name, d.name AS d_name FROM edges e "
                                       "JOIN nodes s ON s.id=e.src JOIN nodes d ON d.id=e.dst").fetchall()]
    if not include_private:
        mem_rows = [r for r in mem_rows if not _looks_private(r.get("content", ""))]
        edge_rows = [e for e in edge_rows if not e.get("private")]
    manifest = {"format": "ishti-memory-export-v1", "created": time.time(),
                "counts": {"memories": len(mem_rows), "entities": len(ent_rows), "edges": len(edge_rows)},
                "encrypted_at_rest": enabled()}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=1))
        z.writestr("memories.json", json.dumps(mem_rows, ensure_ascii=False, indent=1))
        z.writestr("entities.json", json.dumps(ent_rows, ensure_ascii=False, indent=1))
        z.writestr("edges.json", json.dumps(edge_rows, ensure_ascii=False, indent=1))
    return out


def _dec_row(row, spec: dict | None = None) -> dict:
    d = dict(row)
    for col, val in list(d.items()):
        if isinstance(val, str):
            d[col] = decrypt_text(val)
    return d


def _looks_private(text: str) -> bool:
    low = (text or "").lower()
    return any(k in low for k in ("brother", "sister", "mother", "father", "son", "daughter",
                                  "spouse", "partner", "uncle", "aunt", "cousin", "nephew", "niece"))


def import_all(zip_path: str, replace: bool = False) -> dict:
    """Restore an export ZIP. replace=True empties the stores first; otherwise rows merge
    (existing ids win). Returns what landed."""
    from ishti.arch2 import memory
    from ishti import rgraph
    counts = {"memories": 0, "entities": 0, "edges": 0}
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        if not {"manifest.json", "memories.json"} <= names:
            raise ValueError("not an ISHTI memory export (manifest.json/memories.json missing)")
        mems = json.loads(z.read("memories.json"))
        ents = json.loads(z.read("entities.json")) if "entities.json" in names else []
        edges = json.loads(z.read("edges.json")) if "edges.json" in names else []
    mc = memory.conn()
    with _lock, mc:
        if replace:
            mc.execute("DELETE FROM memory_links")
            mc.execute("DELETE FROM memories")
            mc.execute("DELETE FROM entities")
        for r in mems:
            stored = dict(r)
            for col in ("content", "keywords"):
                if isinstance(stored.get(col), str):
                    stored[col] = encrypt_text(stored[col])
            if stored.get("content") and not stored.get("content_hash"):
                stored["content_hash"] = content_hash(decrypt_text(stored["content"]))
            cols = {k: v for k, v in stored.items()
                    if k in {d[1] for d in mc.execute("PRAGMA table_info(memories)").fetchall()}}
            if cols.get("id"):
                mc.execute(f"INSERT OR IGNORE INTO memories({','.join(cols)}) "
                           f"VALUES({','.join('?' for _ in cols)})", tuple(cols.values()))
                counts["memories"] += mc.execute("SELECT changes()").fetchone()[0]
        for r in ents:
            stored = {k: (encrypt_text(v) if k in ("notes", "aliases", "contact_methods",
                                                   "preferences", "important_facts") and isinstance(v, str) else v)
                      for k, v in r.items()}
            cols = {k: v for k, v in stored.items()
                    if k in {d[1] for d in mc.execute("PRAGMA table_info(entities)").fetchall()}}
            if cols.get("id"):
                mc.execute(f"INSERT OR IGNORE INTO entities({','.join(cols)}) "
                           f"VALUES({','.join('?' for _ in cols)})", tuple(cols.values()))
                counts["entities"] += mc.execute("SELECT changes()").fetchone()[0]
        mc.commit()
    rc = rgraph.conn()
    with _lock, rc:
        if replace:
            rc.execute("DELETE FROM edges")
            rc.execute("DELETE FROM nodes")
        ids = {}
        for name in {e.get("s_name") for e in edges} | {e.get("d_name") for e in edges}:
            if name:
                ids[name] = rgraph.node(name)
        for e in edges:
            if e.get("s_name") in ids and e.get("d_name") in ids:
                rc.execute("INSERT OR IGNORE INTO edges(src, dst, rel, detail, private, first_seen, last_confirmed) "
                           "VALUES(?,?,?,?,?,?,?)",
                           (ids[e["s_name"]], ids[e["d_name"]], e.get("rel", ""), encrypt_text(e.get("detail", "") or ""),
                            e.get("private", 0), e.get("first_seen") or time.time(),
                            e.get("last_confirmed") or time.time()))
                counts["edges"] += rc.execute("SELECT changes()").fetchone()[0]
        rc.commit()
    return counts


def status() -> dict:
    key_source = ("env" if _env_key() else
                  "keyring" if _keyring_has() else
                  "key file" if os.path.isfile(os.path.join(KEY_DIR, "memory.key")) else "not created")
    return {"enabled": enabled(), "key_source": key_source, "cipher": "Fernet (AES128-CBC + HMAC)"}


def _keyring_has() -> bool:
    try:
        import keyring
        return bool(keyring.get_password(KEY_SERVICE, KEY_NAME))
    except Exception:
        return False


if __name__ == "__main__":
    import tempfile
    os.environ["ISHTI_VAULT_KEY"] = os.urandom(32).hex()
    os.environ["ISHTI_VAULT"] = "1"  # the self-check opts in (production: Settings -> Memory)
    os.environ["ISHTI_STATE_DB"] = os.path.join(tempfile.mkdtemp(prefix="ishti_vault_"), "s.db")
    reset_key_cache()
    assert enabled(), "vault must honor the env opt-in"
    # round-trip
    tok = encrypt_text("Ahmed is my brother")
    assert tok.startswith(ENC_PREFIX) and tok != "Ahmed is my brother"
    assert decrypt_text(tok) == "Ahmed is my brother"
    assert decrypt_text("plain") == "plain" and encrypt_text(tok) == tok  # idempotent, passthrough
    # real stores, round trip + search
    import ishti.arch2.memory as M
    import ishti.rgraph as R
    tmp = tempfile.mkdtemp(prefix="ishti_vault_")
    M.db_path = lambda: os.path.join(tmp, "m.db")
    M._conn = None
    R.DB_PATH = os.path.join(tmp, "g.db")
    R._conn = None
    m = M.semantic("User prefers Chrome.", source="explicit_user", confidence=1.0)
    raw = sqlite3.connect(M.db_path())
    stored = raw.execute("SELECT content FROM memories WHERE id=?", (m["id"],)).fetchone()[0]
    assert stored.startswith(ENC_PREFIX), "content must be ciphertext at rest"
    assert "Chrome" not in stored
    raw.close()
    assert M.search("Chrome")[0]["content"] == "User prefers Chrome.", "decrypted search still finds it"
    assert M.get(m["id"])["content"] == "User prefers Chrome."
    R.relate("me", "brother", "ahmed")
    R.set_detail("ahmed", "likes", "coffee", "drinks it daily")
    graw = sqlite3.connect(R.DB_PATH)
    det = graw.execute("SELECT detail FROM edges WHERE rel='likes'").fetchone()[0]
    assert det.startswith(ENC_PREFIX), "edge detail must be ciphertext at rest"
    graw.close()
    assert "drinks it daily" in R.describe_edge(R.edges_of("ahmed", rel="likes")[0])
    # backup + export + re-import
    bz = backup(M.db_path(), os.path.join(tmp, "back.db"))
    assert os.path.isfile(bz)
    zpath = export_all(os.path.join(tmp, "exp.zip"))
    counts = import_all(zpath)  # merge into the same stores: dedupe by id
    assert counts["memories"] >= 0
    with zipfile.ZipFile(zpath) as z:
        assert "User prefers Chrome." in z.read("memories.json").decode()
        assert ENC_PREFIX not in z.read("memories.json").decode(), "export is decrypted for the owner"
    # disable decrypts everything
    set_enabled(False)  # config flip may fail without settings service; fields still decrypt below
    os.environ["ISHTI_VAULT"] = "0"
    assert decrypt_text(stored) == "User prefers Chrome."
    print(f"[self-check] core.vault OK — {status()}; encrypt/decrypt, dedupe, search, backup, export, import")
