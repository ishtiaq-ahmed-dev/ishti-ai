"""Verified glossary: short, sourced definitions for the local-AI terms the owner actually asks about.

Why this exists: the resident 4B model, asked "Define 'imatrix' for me", invented a signal-processing
meaning ("a type of matrix used in signal processing and image analysis"). That is a wrong answer to a
timeless definition question - the exact failure mode the owner's checklist forbids ("pure accuracy").

Every entry here is checked against a primary source (linked in `source`), is one or two sentences, and
is injected into the model's context ONLY when the owner's request mentions the term. The model is told
these definitions are exact and to use them verbatim rather than recall. Terms that could not be
verified from a primary source are NOT here - a missing definition falls through to the model, which
may still answer correctly, and to the "I don't know" honesty rule when it cannot.

Maintenance: add a term only with a primary source (upstream repo/docs), never from a secondary blog.
"""

TERMS = {
    "imatrix": {
        "aliases": ("importance matrix", "i-matrix"),
        "definition": (
            "An **imatrix** (importance matrix) is a calibration artifact for LLM quantization: it scores "
            "how important each weight is, measured from the model's activations on a sample of text. "
            "llama.cpp computes one with `llama-imatrix`, and using it during quantization makes a "
            "quantized model noticeably better at the same size."
        ),
        "source": "https://github.com/ggml-org/llama.cpp/blob/master/tools/imatrix/README.md",
    },
    "gguf": {
        "aliases": ("gguf file", "gguf format"),
        "definition": (
            "**GGUF** is llama.cpp's single-file format for models: it holds the quantized weights plus "
            "all the metadata (architecture, tokenizer, chat template) needed to load and run the model, "
            "designed for fast memory-mapped loading."
        ),
        "source": "https://github.com/ggml-org/ggml/blob/master/docs/gguf.md",
    },
    "quantization": {
        "aliases": ("quantize", "quantized", "quantisation"),
        "definition": (
            "**Quantization** stores a model's weights at lower precision (e.g. 4-bit instead of 16-bit) "
            "so it needs much less memory and runs faster, at a small cost in quality. Common GGUF levels "
            "are Q4_K_M, Q5_K_M and Q8_0; higher numbers keep more quality and use more memory."
        ),
        "source": "https://github.com/ggml-org/llama.cpp/blob/master/tools/quantize/README.md",
    },
    "lora": {
        "aliases": ("qlora", "lora adapter"),
        "definition": (
            "**LoRA** (Low-Rank Adaptation) fine-tunes a model by training small low-rank matrices added "
            "to its existing weights instead of changing the whole model, so it needs far less memory and "
            "produces a small adapter file. **QLoRA** is the same idea with the base model quantized to "
            "4-bit, which is how a large model can be fine-tuned on one consumer GPU."
        ),
        "source": "https://arxiv.org/abs/2106.09685",
    },
    "embeddings": {
        "aliases": ("embedding", "vector embeddings"),
        "definition": (
            "**Embeddings** turn text into vectors of numbers so that similar meanings land close together. "
            "They power semantic search and memory recall: a question is embedded and the nearest stored "
            "vectors are returned even when the wording is different."
        ),
        "source": "https://platform.openai.com/docs/guides/embeddings",
    },
    "vram": {
        "aliases": ("video memory", "vram usage"),
        "definition": (
            "**VRAM** is the memory on the graphics card. A local model must fit in it (weights plus the "
            "KV cache for the context) or inference falls back to the CPU and slows down sharply."
        ),
        "source": "https://github.com/ggml-org/llama.cpp/discussions/4167",
    },
    "kv cache": {
        "aliases": ("kv-cache", "key-value cache"),
        "definition": (
            "The **KV cache** stores the attention keys and values for the tokens already processed, so "
            "each new token does not re-read the whole prompt. Its size grows with the context length and "
            "is a large part of a long-context model's VRAM use."
        ),
        "source": "https://github.com/ggml-org/llama.cpp/discussions/4167",
    },
    "ollama": {
        "aliases": ("ollama server",),
        "definition": (
            "**Ollama** is a local runtime for running open models on your own machine: it manages model "
            "downloads, serves an OpenAI-compatible API on localhost, and handles GPU offload. ISHTI runs "
            "its models through it."
        ),
        "source": "https://github.com/ollama/ollama",
    },
    "llama.cpp": {
        "aliases": ("llamacpp", "llama cpp"),
        "definition": (
            "**llama.cpp** is the C/C++ inference engine that runs GGUF models efficiently on CPUs and "
            "GPUs. It defines the GGUF format, the K-quant and I-quant schemes, and the `llama-imatrix` "
            "tool used for importance-matrix quantization."
        ),
        "source": "https://github.com/ggml-org/llama.cpp",
    },
    "context window": {
        "aliases": ("context length", "num_ctx", "ctx size"),
        "definition": (
            "The **context window** is how many tokens a model can consider at once (prompt plus reply). "
            "Everything older is dropped, so a bigger window holds more conversation but needs more VRAM "
            "for the KV cache."
        ),
        "source": "https://github.com/ggml-org/llama.cpp/discussions/4167",
    },
}


def lookup(text: str):
    """The glossary entry the request asks about, or None. Matches on the term or an alias as a whole
    phrase; the request must also look like a definition question (define/what is/meaning/explain),
    so a passing mention ("my imatrix folder is on D:") never injects a definition."""
    import re

    t = (text or "").lower()
    if not re.search(r"\b(?:define|definition|what(?:'s|s|\s+is|\s+does)|meaning\s+of|explain|tell\s+me\s+about)\b", t):
        return None
    for term, entry in TERMS.items():
        for name in (term,) + tuple(entry.get("aliases") or ()):
            if re.search(rf"\b{re.escape(name)}\b", t):
                return {"term": term, "definition": entry["definition"], "source": entry["source"]}
    return None


def context_line(text: str) -> str:
    """A one-line block for the model's context, or '' when no glossary term is asked about."""
    hit = lookup(text)
    if not hit:
        return ""
    return (f"VERIFIED DEFINITION (exact - use this, do not recall from memory): {hit['definition']} "
            f"(source: {hit['source']})")


if __name__ == "__main__":
    assert lookup("Define 'imatrix' for me")["term"] == "imatrix"
    assert lookup("what is gguf?")["term"] == "gguf"
    assert lookup("explain quantization")["term"] == "quantization"
    assert lookup("what does QLoRA mean")["term"] == "lora"
    assert lookup("what is the capital of Kazakhstan") is None
    assert lookup("my imatrix folder is on D:") is None, "a passing mention must not inject a definition"
    assert "importance matrix" in context_line("define imatrix")
    print("[self-check] glossary OK - verified definitions, definition-questions only")
