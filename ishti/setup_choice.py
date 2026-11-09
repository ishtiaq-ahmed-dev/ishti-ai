"""The owner's first-run choice of model provider: this PC (Local) or a cloud API. Asked once, after install, then saved.

Stored in data/setup.json. The API key is never stored here: it is read from the environment variable named in
config.json (llm.api.api_key_env), and this module only reports whether that variable is set.
"""
import json
import os
import time

from ishti import ROOT

from ishti.test_mode import scratch_path

PATH = scratch_path(os.path.join(ROOT, "data", "setup.json"))
PROVIDERS = ("openai", "anthropic", "gemini", "openrouter", "agentrouter")
DEFAULT_MODELS = {"openai": "gpt-4.1-mini", "anthropic": "claude-haiku-5-5",
                  "gemini": "gemini-2.5-flash", "openrouter": "openai/gpt-4.1-mini",
                  "agentrouter": "gpt-6.1-sol"}


_cache = {"key": None, "data": None}


def load() -> dict | None:
    """The saved choice, or None when the owner has not chosen yet (or the file is unreadable). Cached by path+mtime:
    llm.provider() asks several times per model call, and re-reading the file each time was pure disk churn."""
    try:
        key = (PATH, os.stat(PATH).st_mtime_ns)
    except OSError:
        return None
    if _cache["key"] == key:
        return dict(_cache["data"]) if _cache["data"] else None
    try:
        with open(PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    data = data if isinstance(data, dict) and data.get("provider") in ("local", "api") else None
    _cache.update(key=key, data=data)
    return dict(data) if data else None


def save(provider: str, api_provider: str = "", model: str = "", *, credential_source=None) -> dict:
    """Persist the owner's choice. Raises ValueError for an unknown provider or an API choice without a usable name."""
    if provider not in ("local", "api"):
        raise ValueError("provider must be 'local' or 'api'")
    data = {"provider": provider, "saved_at": time.time()}
    if provider == "api":
        if api_provider not in PROVIDERS:
            raise ValueError(f"api provider must be one of: {', '.join(PROVIDERS)}")
        data["api_provider"] = api_provider
        data["model"] = (model or "").strip() or DEFAULT_MODELS[api_provider]
        previous = load() or {}
        same = previous.get("provider") == "api" and previous.get("api_provider") == api_provider
        source = credential_source or (previous.get("credential_source") if same else None) or "environment"
        if source not in ("environment", "keyring"):
            raise ValueError("Unknown credential source")
        data["credential_source"] = source
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, PATH)
    return data


def save_key(provider, key):
    """OS credential store only. Never a file/config/log fallback."""
    if provider not in PROVIDERS or not isinstance(key, str) or not key.strip():
        raise ValueError("Choose a supported provider and supply a nonempty API key.")
    import keyring
    try:
        keyring.set_password("ishti-model-provider", provider, key.strip())
    except Exception:
        raise RuntimeError("The OS credential store could not save the key.") from None


def state(api_key_env: str = "ISHTI_API_KEY") -> dict:
    """What the first-run card shows: whether a choice exists, what it is, and whether the API key variable is set."""
    chosen = load()
    from ishti import llm
    return {"chosen": chosen is not None, "choice": chosen or {}, "api_key_env": api_key_env,
            "api_key_set": bool(llm.api_key()), "defaults": DEFAULT_MODELS}


if __name__ == "__main__":
    import tempfile
    globals()["PATH"] = os.path.join(tempfile.mkdtemp(), "setup.json")
    assert load() is None
    assert save("local")["provider"] == "local" and load()["provider"] == "local"
    d = save("api", "anthropic", "")
    assert d["model"] == DEFAULT_MODELS["anthropic"] and "api_key" not in d
    refused = False
    try:
        save("api", "nobody")
    except ValueError:
        refused = True
    assert refused, "unknown API provider was accepted"
    print("[self-check] setup_choice OK - local/API choice saved, default model, no key stored")
