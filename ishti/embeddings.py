"""Explicit local index strategy for API-only installs, with no model download.

The lexical index matches words/subwords; it is not a semantic neural embedder.
It uses a versioned separate collection so existing Ollama vectors are untouched.
"""
import hashlib
import math
import re

DIMENSIONS = 384


def backend():
    from ishti import CONFIG, llm
    configured = CONFIG.get("memory", {}).get("embedding_backend")
    if configured in ("ollama", "lexical"):
        return configured
    return "lexical" if llm.provider() == "api" else "ollama"


def lexical(texts):
    from ishti import calltrace
    output = []
    for text in texts:
        text = re.sub(r"^search_(?:query|document):\s*", "", text)
        tokens = re.findall(r"[^\W_]+", text.casefold(), re.UNICODE)
        vector = [0.0] * DIMENSIONS
        for token in tokens:
            features = [token] + [token[i:i+3] for i in range(len(token)-2)]
            for feature in features:
                digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
                vector[int.from_bytes(digest[:4], "big") % DIMENSIONS] += 1 if digest[4] & 1 else -1
        norm = math.sqrt(sum(v*v for v in vector)) or 1
        output.append([v/norm for v in vector])
    calltrace.note("embed", "ishti-local-lexical-v1", 0, backend="lexical", n=len(texts))
    return output
