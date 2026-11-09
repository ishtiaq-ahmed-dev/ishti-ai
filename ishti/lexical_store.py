"""Small encrypted SQLite recall index for API-only installations.

No Chroma, neural runtime, model download or external embedding service. Text,
vectors and metadata are sealed together; timestamps and random IDs are visible.
"""
from contextlib import closing
import heapq
import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from ishti import embeddings, secure_store

MAX_ROWS = 5000  # ponytail: every recall decrypts every row, so the index keeps the newest 5,000 turns


class LexicalStore:
    def __init__(self, path):
        folder = Path(path)
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder/'lexical.sqlite'
        self._lock = threading.RLock()
        self._cipher = None
        self.col = self  # lifecycle/diagnostics compatibility
        with closing(self._db()) as db:
            db.execute('CREATE TABLE IF NOT EXISTS recall(id TEXT PRIMARY KEY, ts REAL, payload TEXT NOT NULL)')
            db.commit()

    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('PRAGMA secure_delete=ON')
        return db

    def _box(self):
        if self._cipher is None:
            self._cipher = secure_store._cipher()
        return self._cipher

    def _seal(self, value):
        return secure_store.PREFIX+self._box().encrypt(json.dumps(value,ensure_ascii=False).encode()).decode('ascii')

    def _open(self, payload):
        if not payload.startswith(secure_store.PREFIX):
            raise RuntimeError('Unsealed recall record rejected')
        return json.loads(self._box().decrypt(payload[len(secure_store.PREFIX):].encode('ascii')))

    def count(self):
        with self._lock, closing(self._db()) as db:
            return db.execute('SELECT COUNT(*) FROM recall').fetchone()[0]

    def add(self, text, **meta):
        with self._lock:
            vector = embeddings.lexical(['search_document: '+text])[0]
            payload = self._seal({'text':text,'vector':vector,'meta':meta})
            with closing(self._db()) as db, db:
                db.execute('INSERT INTO recall VALUES(?,?,?)',(uuid.uuid4().hex,time.time(),payload))
                db.execute('DELETE FROM recall WHERE id NOT IN (SELECT id FROM recall ORDER BY ts DESC LIMIT ?)',(MAX_ROWS,))

    def recall(self, query, k=4, max_distance=0.45):
        vector = embeddings.lexical(['search_query: '+query])[0]
        def candidates(db):
            for ts, payload in db.execute('SELECT ts, payload FROM recall'):
                record = self._open(payload)
                score = sum(a*b for a,b in zip(vector,record['vector']))
                if score >= 1-max_distance:
                    yield score, ts, record['text']
        with self._lock, closing(self._db()) as db:
            hits=heapq.nlargest(k,candidates(db),key=lambda item:item[0])
            # dated, so "I live in Peshawar" from March can't outvote "I moved to Lahore" from May
            first = {}
            for _,ts,text in hits:
                first.setdefault(text.split('\nISHTI:')[0], ts)
            return [time.strftime('[%Y-%m-%d] ',time.localtime(ts))+text for text,ts in first.items()]

    def delete_where_text(self, text):
        with self._lock, closing(self._db()) as db, db:
            ids=[(row[0],) for row in db.execute('SELECT id,payload FROM recall')
                 if text.casefold() in self._open(row[1])['text'].casefold()]
            db.executemany('DELETE FROM recall WHERE id=?',ids)
            return len(ids)
