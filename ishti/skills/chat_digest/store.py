"""Chat-digest message store (data/chat_digest.db) - what ISHTI already SAW, nothing more.

TWO PATHS, and the split is the whole point:

  ingest_*   (write)  called by the read path when ISHTI already has messages in front of it - the WhatsApp
                      skill's DOM read, an official WhatsApp export the owner hands over, or a JSON dump.
                      A message is stored once (chat_id + msg_id), with where it came from.
  read_*     (SELECT only) everything the digest uses. The digest run performs NO write of any kind:
                      tests/test_chat_digest.py asserts that from the audit log and from the DB file.

Nothing here opens a network socket, and nothing here is ever written into a client's isolated memory:
the digest is its own local report. Messages are DATA; see ishti.untrusted.

Window semantics live in window.py; this module only stores and returns rows.
"""
import datetime as dt
import hashlib
import os
import re
import sqlite3
import threading
import zipfile

from ishti import CONFIG

DB_PATH = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "chat_digest.db")
LOCK = threading.RLock()
_conn = None

CHAT_TYPES = ("dm", "group", "community", "channel", "broadcast", "unknown")
MEDIA = ("", "image", "voice", "video", "document", "sticker", "location", "contact")


def conn():
    global _conn
    with LOCK:
        if _conn is None:
            os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript("""
                CREATE TABLE IF NOT EXISTS chats(
                    chat_id TEXT PRIMARY KEY, name TEXT, type TEXT DEFAULT 'unknown',
                    archived INTEGER DEFAULT 0, muted INTEGER DEFAULT 0, unread INTEGER DEFAULT 0,
                    first_seen REAL, last_seen REAL, source TEXT DEFAULT '');
                CREATE TABLE IF NOT EXISTS messages(
                    chat_id TEXT, msg_id TEXT, ts REAL, sender TEXT, text TEXT DEFAULT '',
                    media TEXT DEFAULT '', media_text TEXT DEFAULT '', source TEXT DEFAULT '',
                    PRIMARY KEY (chat_id, msg_id));
                CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts);
            """)
        return _conn


def close():
    """Drop the cached connection (tests, shutdown)."""
    global _conn
    with LOCK:
        if _conn is not None:
            try:
                _conn.close()
            except sqlite3.Error:
                pass
            _conn = None

# ------------------------------------------------------------------ ingest (write path)

def upsert_chat(chat_id, name=None, type="unknown", archived=False, muted=False, unread=False, source="", when=None):
    """One row per chat. A known name/type is never overwritten with an empty one."""
    chat_id = str(chat_id)
    when = when or dt.datetime.now().timestamp()
    kind = type if type in CHAT_TYPES else "unknown"
    c = conn()
    with LOCK, c:
        row = c.execute("SELECT name, type, first_seen FROM chats WHERE chat_id=?", (chat_id,)).fetchone()
        if row is None:
            c.execute("INSERT INTO chats(chat_id,name,type,archived,muted,unread,first_seen,last_seen,source)"
                      " VALUES(?,?,?,?,?,?,?,?,?)",
                      (chat_id, name or chat_id, kind, int(bool(archived)), int(bool(muted)), int(bool(unread)),
                       when, when, source))
        else:
            keep_kind = row["type"] if (kind == "unknown" and row["type"] != "unknown") else kind
            c.execute("UPDATE chats SET name=?, type=?, archived=?, muted=?, unread=?, last_seen=?,"
                      " source=CASE WHEN ?='' THEN source ELSE ? END WHERE chat_id=?",
                      (name or row["name"], keep_kind, int(bool(archived)), int(bool(muted)), int(bool(unread)),
                       when, source, source, chat_id))
    return chat_id


def add_messages(chat_id, rows, source="dom", default_ts=None):
    """rows: [{"msg_id","ts","sender","text","media","media_text"}]. Returns how many were new."""
    chat_id = str(chat_id)
    c = conn()
    new = 0
    with LOCK, c:
        for i, m in enumerate(rows):
            text = (m.get("text") or "").strip()
            media = (m.get("media") or "").strip().lower()
            if media not in MEDIA:
                media = "document"
            ts = m.get("ts")
            ts = float(ts) if ts else float(default_ts or dt.datetime.now().timestamp())
            msg_id = str(m.get("msg_id") or auto_id(chat_id, ts, m.get("sender"), text, i))
            cur = c.execute("INSERT OR IGNORE INTO messages(chat_id,msg_id,ts,sender,text,media,media_text,source)"
                            " VALUES(?,?,?,?,?,?,?,?)",
                            (chat_id, msg_id, ts, (m.get("sender") or "").strip() or "unknown", text, media,
                             (m.get("media_text") or "").strip(), source))
            new += cur.rowcount
    return new


def auto_id(chat_id, ts, sender, text, i=0):
    h = hashlib.sha1(f"{chat_id}|{ts:.0f}|{sender}|{text}|{i}".encode("utf-8")).hexdigest()[:12]
    return f"{chat_id}-{int(ts)}-{h}"


# ------------------------------------------------------------------ read path (SELECT only)

def chats():
    return [dict(r) for r in conn().execute("SELECT * FROM chats ORDER BY name COLLATE NOCASE")]


def chat(chat_id):
    row = conn().execute("SELECT * FROM chats WHERE chat_id=?", (str(chat_id),)).fetchone()
    return dict(row) if row else None


def messages_between(start, end, chat_id=None):
    """Every stored message with start <= ts <= end (inclusive at both ends - the 24h boundary rule)."""
    sql = ("SELECT chat_id,msg_id,ts,sender,text,media,media_text,source FROM messages"
           " WHERE ts>=? AND ts<=?")
    args = [float(start), float(end)]
    if chat_id is not None:
        sql += " AND chat_id=?"
        args.append(str(chat_id))
    sql += " ORDER BY ts, rowid"
    return [dict(r) for r in conn().execute(sql, args)]


def provenance(start=None, end=None):
    """{chat_id: sorted distinct sources} - which data source each chat's coverage came from (4.3)."""
    sql = "SELECT chat_id, source, COUNT(*) n FROM messages WHERE 1=1"
    args = []
    if start is not None:
        sql += " AND ts>=?"
        args.append(float(start))
    if end is not None:
        sql += " AND ts<=?"
        args.append(float(end))
    out = {}
    for r in conn().execute(sql + " GROUP BY chat_id, source", args):
        out.setdefault(r["chat_id"], set()).add(r["source"] or "store")
    return {k: sorted(v) for k, v in out.items()}


def delete_chat(chat_id):
    c = conn()
    with LOCK, c:
        c.execute("DELETE FROM messages WHERE chat_id=?", (str(chat_id),))
        c.execute("DELETE FROM chats WHERE chat_id=?", (str(chat_id),))


def stats():
    c = conn()
    return {"db": DB_PATH, "chats": c.execute("SELECT COUNT(*) FROM chats").fetchone()[0],
            "messages": c.execute("SELECT COUNT(*) FROM messages").fetchone()[0]}

# ------------------------------------------------------------------ importers

def ingest_json(payload, source="json"):
    """payload: {"chats": [{"chat_id","name","type","archived","muted","unread","messages":[{...}]}]}.
    The synthetic test dataset and any script that already has messages in hand use this.

    `last_seen` is the chat's real last message time (max message ts) when the payload carries
    timestamps - NOT the ingest wall-clock. Stamping `now()` here made the digest's
    "most recent first" ordering follow SQLite write timing (nondeterministic run to run); found
    by the F1 digest-flake recheck 2026-10-02."""
    n = 0
    for ch in payload.get("chats", []):
        msgs = ch.get("messages", [])
        when = ch.get("last_seen")
        if not when and msgs:
            stamps = []
            for mm in msgs:
                try:
                    stamps.append(float(mm.get("ts")))
                except (TypeError, ValueError):
                    continue
            when = max(stamps) if stamps else None
        cid = upsert_chat(ch.get("chat_id") or ch.get("name"), ch.get("name"), ch.get("type", "unknown"),
                          ch.get("archived", False), ch.get("muted", False), ch.get("unread", False),
                          source=source, when=when)
        n += add_messages(cid, msgs, source=source)
    return n


def from_dom(parsed, chat_id, chat_name=None, when=None, source="dom"):
    """Turn the WhatsApp skill's DOM read ([{sender,text}]) into stored rows. A DOM snapshot carries no
    timestamps, so each row is stamped with the moment ISHTI actually saw it (never a guess about the past)."""
    when = when or dt.datetime.now().timestamp()
    rows = [{"sender": p.get("sender"), "text": p.get("text"), "ts": when} for p in parsed
            if (p.get("text") or "").strip()]
    upsert_chat(chat_id, chat_name or chat_id, "unknown", source=source, when=when)
    return add_messages(chat_id, rows, source=source, default_ts=when)


LINE = re.compile(r"^\[?(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}),?\s+"
                  r"(\d{1,2}:\d{2}(?::\d{2})?\s*(?:[APap]\.?[Mm]\.?)?)\]?\s*[-\u2013]?\s*(.*)$")
SENDER = re.compile(r"^(?P<sender>[^:]{1,60}?):\s(?P<text>.*)$", re.S)
SYSTEM = re.compile(r"^\s*(?:Messages and calls are end-to-end encrypted|You (?:created|added|changed|joined)\b|.*"
                    r"\b(?:joined using this group's invite link|left the group|changed the subject|"
                    r"deleted this message)\b)", re.I)
OMITTED = ("<media omitted>", "image omitted", "video omitted", "audio omitted", "sticker omitted",
           "gif omitted", "document omitted")


def parse_export(blob):
    """WhatsApp export text -> message rows. Unparseable dates are stamped now (never invented into a window)."""
    rows, cur = [], None
    for raw in (blob or "").splitlines():
        line = raw.rstrip("\r")
        m = LINE.match(line)
        if m:
            ts = _ts(m.group(1), m.group(2).replace("\u202f", " ").strip())
            rest = m.group(3)
            sender, text = "system", rest
            sm = SENDER.match(rest)
            if sm and not SYSTEM.match(rest):
                sender, text = sm.group("sender").strip(), sm.group("text")
            cur = {"ts": ts, "sender": sender, "text": text, "media": "", "media_text": ""}
            rows.append(cur)
            continue
        if cur is not None and line.strip():  # a multiline message: continuation of the previous row
            cur["text"] += "\n" + line
    out = []
    for r in rows:
        if r["sender"] == "system":
            continue
        if r["text"].strip().lower() in OMITTED or r["text"].strip().startswith("\u200e"):
            r["media"], r["text"] = "document", ""      # media IS present, contents are not (4.4: mark it)
        if r["ts"] is None:
            r["ts"] = dt.datetime.now().timestamp()
        out.append(r)
    return out


def _ts(date_s, time_s):
    date_s = date_s.replace(".", "/").replace("-", "/")
    parts = date_s.split("/")
    fmt_date = "%d/%m/%Y" if len(parts[-1]) == 4 else "%d/%m/%y"
    for f in ("%I:%M:%S %p", "%I:%M %p", "%H:%M:%S", "%H:%M"):
        try:
            t = dt.datetime.strptime(time_s.upper().replace(".", ""), f).time()
            return dt.datetime.strptime(date_s, fmt_date).replace(
                hour=t.hour, minute=t.minute, second=t.second, microsecond=0).timestamp()
        except ValueError:
            continue
    return None


def ingest_export(path, chat_name=None, source="export"):
    """Official WhatsApp chat export (.txt, or .zip containing it). Local file only, read-only."""
    texts = []
    path = str(path)
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                if info.filename.lower().endswith(".txt") and not info.is_dir():
                    with z.open(info) as f:
                        texts.append(f.read().decode("utf-8", "replace"))
    else:
        with open(path, encoding="utf-8", errors="replace") as f:
            texts.append(f.read())
    name = chat_name or os.path.splitext(os.path.basename(path))[0]
    cid = upsert_chat(name, name, "unknown", source=source)
    total = sum(add_messages(cid, parse_export(blob), source=source) for blob in texts)
    return {"chat_id": cid, "new_messages": total}

if __name__ == "__main__":
    import tempfile
    DB_PATH = os.path.join(tempfile.mkdtemp(prefix="cd_store_"), "s.db")
    close()
    assert stats()["chats"] == 0 and stats()["messages"] == 0
    ingest_json({"chats": [{"chat_id": "c1", "name": "Ali", "type": "dm", "messages": [
        {"msg_id": "m1", "ts": 1000, "sender": "Ali", "text": "salam"}]}]})
    assert stats()["chats"] == 1 and stats()["messages"] == 1, stats()
    assert messages_between(1000, 1000)[0]["msg_id"] == "m1"       # inclusive at both ends
    assert messages_between(1001, 2000) == []
    assert add_messages("c1", [{"msg_id": "m1", "ts": 1000, "sender": "Ali", "text": "salam"}]) == 0  # dedupe
    assert upsert_chat("c1", None, "unknown") == "c1" and chat("c1")["name"] == "Ali"  # name not blanked
    exp = os.path.join(os.path.dirname(DB_PATH), "chat.txt")
    with open(exp, "w", encoding="utf-8") as f:
        f.write("[12/09/2026, 09:14:22] Ali: Assalam o alaikum\n[12/09/2026, 09:15:00] You: walaikum salam\n"
                "12/09/26, 21:40 - Sara: dinner at 8?\n12/09/26, 21:41 - Sara: <Media omitted>\n"
                "[12/09/2026, 21:42:00] Messages and calls are end-to-end encrypted\n"
                "12/09/26, 21:43 - Sara: line one\nline two of the same message\n")
    got = ingest_export(exp)
    assert got["new_messages"] == 5, got
    rows = messages_between(0, 10 ** 10, chat_id=got["chat_id"])
    assert [r["sender"] for r in rows] == ["Ali", "You", "Sara", "Sara", "Sara"], rows
    assert rows[2]["text"] == "dinner at 8?" and rows[2]["ts"] > 0
    assert rows[3]["media"] == "document" and rows[3]["text"] == ""
    assert rows[4]["text"] == "line one\nline two of the same message"
    assert provenance(0, 10 ** 10)[got["chat_id"]] == ["export"]
    assert conn().execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    print("[self-check] chat_digest.store OK - dedupe, inclusive window, export parse (both formats, "
          "12h+24h clock, multiline, media placeholder, system lines dropped)")



