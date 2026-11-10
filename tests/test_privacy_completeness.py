"""Privacy completeness: one export of everything ISHTI keeps, and retention for what is temporary by design (uploads,
channel voice leftovers, deleted-chat trash) - never real history. Runs in the suite's scratch data folder."""
import os
import time
import zipfile

from ishti import CONFIG


def test_export_everything_has_every_store_and_the_chats():
    import sqlite3
    from ishti.core import vault
    data = os.path.dirname(CONFIG["memory"]["db_path"])
    os.makedirs(data, exist_ok=True)
    with sqlite3.connect(os.path.join(data, "probe.db")) as c:  # a store in the (scratch) data folder
        c.execute("CREATE TABLE IF NOT EXISTS t(x)")
    os.makedirs(os.path.join(data, "sessions"), exist_ok=True)
    with open(os.path.join(data, "sessions", "sess_test.json"), "w", encoding="utf-8") as f:
        f.write('{"messages": []}')
    path = vault.export_everything()
    assert path.startswith(data) and os.path.basename(path).startswith("ishti-full-export-")
    names = zipfile.ZipFile(path).namelist()
    assert "manifest.json" in names and "chats/sess_test.json" in names
    assert any(n.startswith("stores/") and n.endswith(".db") for n in names)


def test_retention_prunes_only_temporary_files():
    from ishti import files_api
    from ishti.channels import media
    old = time.time() - 40 * 86400
    os.makedirs(files_api.UPLOAD_DIR, exist_ok=True)
    stale, fresh = os.path.join(files_api.UPLOAD_DIR, "old.pdf"), os.path.join(files_api.UPLOAD_DIR, "new.pdf")
    for p in (stale, fresh):
        open(p, "w").close()
    os.utime(stale, (old, old))
    os.makedirs(media.UPLOAD_DIR, exist_ok=True)
    voice = os.path.join(media.UPLOAD_DIR, "voice_old.ogg")
    open(voice, "w").close()
    os.utime(voice, (old, old))
    files_api._RETENTION["last"] = 0.0
    out = files_api.maybe_retention()
    assert not os.path.exists(stale) and os.path.exists(fresh) and not os.path.exists(voice)
    assert out["uploads"] >= 1 and files_api.maybe_retention() == {}  # once a day
