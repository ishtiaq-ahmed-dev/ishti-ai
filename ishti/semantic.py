"""Semantic memory (ChromaDB + local Ollama embeddings) for general context and past
conversation. Fuzzy by nature, so it always ranks below the exact fact store."""
import time
import uuid

import chromadb
import requests

from ishti import CONFIG

_cfg = CONFIG["memory"]


def embed(texts):
    r = requests.post(f"{_cfg['embed_url']}/api/embed",
                      json={"model": _cfg["embed_model"], "input": texts}, timeout=60)
    r.raise_for_status()
    return r.json()["embeddings"]


# nomic-embed-text is trained with these task prefixes; recall is noticeably sharper with them.
class Semantic:
    def __init__(self, path: str = None):
        client = chromadb.PersistentClient(path=path or _cfg["chroma_dir"])
        # Embeddings are always passed in explicitly, so Chroma never loads its own model.
        self.col = client.get_or_create_collection("memory", metadata={"hnsw:space": "cosine"})

    def add(self, text: str, **meta):
        meta = {"ts": time.time(), **meta}
        self.col.add(ids=[uuid.uuid4().hex], documents=[text], embeddings=embed([f"search_document: {text}"]), metadatas=[meta])

    def recall(self, query: str, k: int = 4, max_distance: float = 0.45) -> list:
        if self.col.count() == 0:
            return []
        res = self.col.query(query_embeddings=embed([f"search_query: {query}"]), n_results=min(k, self.col.count()))
        return [d for d, dist in zip(res["documents"][0], res["distances"][0]) if dist <= max_distance]


_sem = None


def get_semantic() -> Semantic:
    global _sem
    if _sem is None:
        _sem = Semantic()
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
