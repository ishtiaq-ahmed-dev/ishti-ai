"""Semantic memory (ChromaDB + local Ollama embeddings) for general context and past
conversation. Fuzzy by nature, so it always ranks below the exact fact store."""
import time
import uuid

import chromadb
import requests

from ishti import CONFIG
from ishti import keepalive

import os
import threading

_cfg = CONFIG["memory"]


_EMBED_CACHE = {}


def embed(texts):
    cfg = CONFIG["memory"]
    from ishti import calltrace, models
    key = (cfg["embed_model"], tuple(texts))
    hit = _EMBED_CACHE.get(key)
    if hit is not None:     # the same text embedded again this session (a repeated or retried question): no model call
        calltrace.note("embed", cfg["embed_model"], 0, cached=True)
        return hit
    models.used(cfg["embed_model"])
    with calltrace.timed("embed", cfg["embed_model"], n=len(texts)):
        r = keepalive.post(f"{cfg['embed_url']}/api/embed",
                           json={"model": cfg["embed_model"], "input": texts}, timeout=60)
        r.raise_for_status()
    out = r.json()["embeddings"]
    if len(_EMBED_CACHE) >= 256:   # ponytail: drop-oldest, not true LRU; a 256-entry cache of short strings never matters more
        _EMBED_CACHE.pop(next(iter(_EMBED_CACHE)))
    _EMBED_CACHE[key] = out
    return out


# nomic-embed-text is trained with these task prefixes; recall is noticeably sharper with them.
class Semantic:
    def __init__(self, path: str = None):
        self.path = path or CONFIG["memory"]["chroma_dir"]
        self._lock = threading.RLock()
        self._col = None

    @property
    def col(self):
        with self._lock:
            if self._col is not None:
                return self._col
            os.makedirs(self.path, exist_ok=True)
            for attempt in range(3):
                try:
                    client = chromadb.PersistentClient(path=self.path, settings=chromadb.Settings(anonymized_telemetry=False, is_persistent=True))
                    self._col = client.get_or_create_collection("memory", metadata={"hnsw:space": "cosine"})
                    return self._col
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(0.15)

    def add(self, text: str, **meta):
        meta = {"ts": time.time(), **meta}
        with self._lock:
            self.col.add(ids=[uuid.uuid4().hex], documents=[text], embeddings=embed([f"search_document: {text}"]), metadatas=[meta])

    def recall(self, query: str, k: int = 4, max_distance: float = 0.45) -> list:
        col = self.col
        if col.count() == 0:
            return []
        res = col.query(query_embeddings=embed([f"search_query: {query}"]), n_results=min(k, col.count()))
        return [d for d, dist in zip(res["documents"][0], res["distances"][0]) if dist <= max_distance]


_sem = None
_sem_path = None


def get_semantic(path: str = None) -> Semantic:
    global _sem, _sem_path
    cur_path = path or CONFIG["memory"]["chroma_dir"]
    if _sem is None or _sem_path != cur_path:
        _sem = Semantic(cur_path)
        _sem_path = cur_path
    return _sem


if __name__ == "__main__":
    import tempfile
    s = Semantic(tempfile.mkdtemp())
    s.add("User: my sister's birthday is on March 3rd")
    s.add("User: I prefer dark roast coffee")
    hits = s.recall("when is my sister's birthday?")
    assert hits and "birthday" in hits[0], hits
    assert not s.recall("quantum chromodynamics lattice gauge theory"), "unrelated query must return nothing"
    print("[self-check] semantic OK - relevant recall, irrelevant filtered")
