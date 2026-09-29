"""Fact-checker (F2): verify claims against independent sources; flag conflicts; never
invent sources. Claim-level verdicts feed deep research (per-claim confidence) and the
orchestrator's fact_checker agent.

Verdict shape: {claim, verdict: supported|contradicted|unclear, confidence, evidence:
[{url, title, snippet, agrees}], checked_at}. A claim with NO evidence is 'unclear' —
never silently supported (F2: never invents sources).
"""
from __future__ import annotations

import re
import time

NUM = re.compile(r"\b\d[\d.,]*\b")
STOP = {"the", "a", "an", "of", "in", "on", "for", "and", "or", "is", "are", "was", "were",
        "to", "by", "as", "at", "it", "its", "that", "this", "with", "from"}


def extract_claims(text: str, max_claims: int = 6) -> list[str]:
    """Declarative sentences worth checking (numbers, superlatives, named facts)."""
    claims = []
    for sent in re.split(r"(?<=[.!?])\s+", (text or "").strip()):
        s = sent.strip()
        if len(s) < 15 or len(s) > 400 or not s[0:1].isupper() or s.endswith("?"):
            continue
        if NUM.search(s) or re.search(r"\b(?:first|largest|biggest|oldest|fastest|only|most)\b", s, re.I):
            claims.append(s.rstrip(".!"))
        if len(claims) >= max_claims:
            break
    return claims


def _tokens(claim: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]{3,}", claim.lower()) if w not in STOP]


def _agree(claim: str, snippet: str) -> bool:
    """Does the snippet carry the claim's key words AND its number (when it has one)?"""
    ct, st = set(_tokens(claim)), set(_tokens(snippet))
    overlap = len(ct & st) / max(1, len(ct))
    cnums, snums = set(NUM.findall(claim)), set(NUM.findall(snippet))
    num_ok = (not cnums) or any(n in snums or n.replace(",", "") in {x.replace(",", "") for x in snums}
                                for n in cnums)
    return overlap >= 0.45 and num_ok


def check_claim(claim: str, search_fn=None, max_sources: int = 5) -> dict:
    """One claim: search independent sources, agree/disagree across them, verdict + confidence."""
    search_fn = search_fn or _default_search
    evidence, tried = [], 0
    for hit in (search_fn(claim) or [])[:max_sources]:
        tried += 1
        url = hit.get("url") or hit.get("link") or ""
        if not url:
            continue
        snippet = (hit.get("snippet") or hit.get("content") or hit.get("text") or "")[:500]
        evidence.append({"url": url, "title": (hit.get("title") or "")[:120],
                         "snippet": snippet, "agrees": _agree(claim, snippet)})
    agreeing = sum(1 for e in evidence if e["agrees"])
    if not evidence:
        return {"claim": claim, "verdict": "unclear", "confidence": 0.0, "evidence": [],
                "note": "no independent source found - never assumed true (F2)", "checked_at": time.time()}
    if agreeing == 0:
        return {"claim": claim, "verdict": "contradicted", "confidence": min(0.9, 0.4 + 0.1 * tried),
                "evidence": evidence, "checked_at": time.time()}
    ratio = agreeing / len(evidence)
    if ratio >= 0.6 and agreeing >= 2:
        verdict, conf = "supported", min(0.95, 0.6 + 0.1 * agreeing)
    elif ratio >= 0.5:
        verdict, conf = "supported", 0.6
    else:
        verdict, conf = "unclear", 0.45
    note = "" if verdict == "supported" else "sources disagree on this claim"
    return {"claim": claim, "verdict": verdict, "confidence": round(conf, 2), "evidence": evidence,
            "note": note, "checked_at": time.time()}


def _default_search(query: str, k: int = 6):
    """The existing search stack (SearXNG/Bing); [] on failure — check_claim then reports unclear."""
    try:
        from orchestrator.deep_research import quick_search
        return quick_search(query, max_results=k)
    except Exception:
        return []


def check(text: str, search_fn=None, max_claims: int = 4) -> dict:
    """Fact-check a passage: extract claims, verify each, flag conflicts. Returns
    {claims: [verdicts], supported, contradicted, unclear, conflicts}."""
    verdicts = [check_claim(c, search_fn=search_fn) for c in extract_claims(text, max_claims)]
    conflicts = [v for v in verdicts if v["verdict"] == "contradicted"]
    return {"claims": verdicts,
            "supported": sum(1 for v in verdicts if v["verdict"] == "supported"),
            "contradicted": len(conflicts),
            "unclear": sum(1 for v in verdicts if v["verdict"] == "unclear"),
            "conflicts": [{"claim": v["claim"], "evidence": v["evidence"][:3]} for v in conflicts]}


def format_report(result: dict) -> str:
    """The owner-facing summary (claim -> verdict + one source each)."""
    if not result["claims"]:
        return "No checkable claims found."
    icon = {"supported": "[OK]", "contradicted": "[X]", "unclear": "[?]"}
    lines = []
    for v in result["claims"]:
        src = f" — {v['evidence'][0]['url'][:60]}" if v["evidence"] else " — no independent source found"
        lines.append(f"- {icon[v['verdict']]} {v['claim'][:140]}{src}")
    head = f"Fact check: {result['supported']} supported, {result['contradicted']} contradicted, {result['unclear']} unclear"
    return head + "\n" + "\n".join(lines)


if __name__ == "__main__":
    # claim extraction
    text = ("The Sony WH-1000XM5 weighs 250 grams. It was released in 2022. Is it waterproof? "
            "The battery lasts 30 hours. Hello!")
    claims = extract_claims(text)
    assert any("250 grams" in c for c in claims) and any("30 hours" in c for c in claims), claims
    assert not any("waterproof" in c for c in claims), "questions are not claims"
    # agreement logic
    assert _agree("The battery lasts 30 hours.", "SONY WH-1000XM5 battery life rated for 30 hours of playback")
    assert not _agree("The battery lasts 30 hours.", "the camera module is made of aluminium")
    # checking with a fake search fn: two agreeing sources -> supported
    def good_search(q, k=6):
        return [{"url": "https://a.example", "title": "review", "snippet": "battery life is 30 hours with ANC off"},
                {"url": "https://b.example", "title": "specs", "snippet": "rated 30 hours of playback"}]
    v = check_claim("The battery lasts 30 hours.", search_fn=good_search)
    assert v["verdict"] == "supported" and v["confidence"] >= 0.6, v
    # contradicting evidence -> contradicted
    def bad_search(q, k=6):
        return [{"url": "https://x.example", "snippet": "the wheelbase is 2900 mm long"},
                {"url": "https://y.example", "snippet": "unrelated horsepower figures here"}]
    v2 = check_claim("The battery lasts 30 hours.", search_fn=bad_search)
    assert v2["verdict"] == "contradicted", v2
    # no sources -> unclear, never supported (F2: never invents)
    v3 = check_claim("The battery lasts 30 hours.", search_fn=lambda q, k=6: [])
    assert v3["verdict"] == "unclear" and v3["evidence"] == [] and "never assumed" in v3["note"], v3
    # full passage check + report shape
    res = check(text, search_fn=good_search, max_claims=3)
    assert res["claims"] and set(res) >= {"supported", "contradicted", "unclear", "conflicts"}
    rep = format_report(res)
    assert "Fact check:" in rep and rep.count("[") >= len(res["claims"]), rep
    print(f"[self-check] orchestrator.factcheck OK — claims, agreement, verdicts, conflicts, "
          f"{res['supported']}S/{res['contradicted']}C/{res['unclear']}U")
