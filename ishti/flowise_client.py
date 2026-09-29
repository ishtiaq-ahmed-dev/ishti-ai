"""Flowise adapter (Part E1): call local Flowise flows over their prediction API.

Only the resolved request + minimum context ever leaves for a flow (E1 contract). Every
call goes through the shared circuit breaker — a Flowise outage degrades to ISHTI's own
skills instead of hanging turns. Credentials: the optional API key lives in the settings
secrets store (keyring), never in a prompt.
"""
from __future__ import annotations

import re
import urllib.request
import urllib.error

from ishti.core.settings import get_settings_service


def _cfg() -> dict:
    try:
        s = get_settings_service().get().get("integrations", {})
        return {"url": (s.get("flowise_url") or "").rstrip("/"),
                "key": s.get("flowise_api_key") or "",
                "flows": s.get("flowise_flows") or {}}
    except Exception:
        return {"url": "", "key": "", "flows": {}}


def configured() -> bool:
    return bool(_cfg()["url"])


def _headers(key: str) -> dict:
    h = {"Content-Type": "application/json"}
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def _req(url: str, payload: dict | None = None, timeout: float = 8.0) -> dict:
    data = None
    if payload is not None:
        data = __import__("json").dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=_headers(_cfg()["key"]),
                                 method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        import json
        return json.loads(r.read().decode("utf-8", "replace"))


def health() -> tuple[bool, str]:
    """(ok, detail) for the architecture health check and Settings."""
    cfg = _cfg()
    if not cfg["url"]:
        return False, "no Flowise flow URL configured (Settings -> Integrations)"
    try:
        out = _req(cfg["url"] + "/api/v1/ping", timeout=4)
        return True, f"Flowise reachable ({out.get('data', 'pong')})"
    except Exception as exc:
        return False, f"Flowise not reachable ({type(exc).__name__})"


def list_flows() -> list[dict]:
    """The chatflows the owner can bind to intents in Settings (E1)."""
    cfg = _cfg()
    if not cfg["url"]:
        return []
    try:
        rows = _req(cfg["url"] + "/api/v1/chatflows", timeout=6)
        return [{"id": r.get("id"), "name": r.get("name")} for r in (rows.get("data") or rows)
                if isinstance(r, dict) and r.get("id")][:50]
    except Exception:
        return []


def pick_flow(text: str) -> str:
    """Which flow handles this request: the intent->flow mapping from Settings (longest
    matching intent wins); when no mapping matches but exactly one flow is bound, it answers."""
    flows = _cfg()["flows"] or {}
    low = (text or "").lower()
    best, best_len = "", 0
    for intent, fid in flows.items():
        if intent and intent.lower() in low and len(intent) > best_len:
            best, best_len = fid, len(intent)
    if not best and len(flows) == 1:
        best = next(iter(flows.values()))
    return best


def run_flow(flow_id: str, question: str, context: str = "") -> dict:
    """One prediction call. Returns {reply, sources, raw} — never raises (breaker + fallback)."""
    cfg = _cfg()
    if not cfg["url"] or not flow_id:
        return {"reply": "", "error": "flowise not configured"}
    from ishti import resilience

    def _call():
        payload = {"question": question[:4000]}
        if context:
            payload["overrideConfig"] = {"systemMessagePrompt": context[:2000]}
        raw = _req(f"{cfg['url']}/api/v1/prediction/{flow_id}", payload=payload, timeout=120)
        reply = raw.get("text") or raw.get("data", {}).get("text") if isinstance(raw, dict) else str(raw)
        if not reply and isinstance(raw, dict):
            reply = raw.get("agentReasoning") or __import__("json").dumps(raw)[:800]
        srcs = []
        for key in ("sourceDocuments", "sources"):
            for d in (raw.get(key) or []) if isinstance(raw, dict) else []:
                if isinstance(d, dict) and (d.get("pageContent") or d.get("url")):
                    srcs.append({"url": d.get("url") or "", "title": (d.get("pageContent") or "")[:80]})
        return {"reply": str(reply or ""), "sources": srcs[:8], "raw_mode": "flowise"}

    out = resilience.breaker("flowise", fail_threshold=2, reset_s=120).run(
        _call, fallback=lambda e: {"reply": "", "error": f"flowise unavailable: {type(e).__name__}"})
    return out


def strip_markers(text: str) -> str:
    """Flowise replies sometimes carry agent chatter markers; keep the readable text."""
    return re.sub(r"\n{3,}", "\n\n", (text or "")).strip()


if __name__ == "__main__":
    import tempfile
    import os
    os.environ["ISHTI_SETTINGS_DIR"] = tempfile.mkdtemp(prefix="ishti_flowise_")
    from ishti.core import settings as S
    S._service = None  # fresh service on the scratch dir
    assert not configured() and health()[0] is False
    assert list_flows() == [] and pick_flow("anything") == ""
    get_settings_service().update({"integrations": {"flowise_url": "http://127.0.0.1:3010",
                                                    "flowise_flows": {"research": "flow-1",
                                                                      "translate to urdu": "flow-2"}}})
    assert configured()
    assert pick_flow("please research GPUs") == "flow-1"
    assert pick_flow("translate to urdu good morning") == "flow-2"
    assert pick_flow("nothing matches") == ""          # ambiguous: no auto-pick with 2 flows
    # run_flow against a dead endpoint degrades (breaker + fallback), never raises
    out = run_flow("flow-1", "hi")
    assert "error" in out and out["reply"] == "", out
    print("[self-check] ishti.flowise_client OK — config, intent->flow pick, breaker fallback, health")
