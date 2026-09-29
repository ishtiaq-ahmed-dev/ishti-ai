"""Lead finder (Part E4): natural language -> search -> scrape -> structured leads ->
dedupe -> verify -> rank -> table + export. Stored in ishti/leads.py's own table (work
data, never personal memory); follow-ups ("only the ones in Dubai", "find more like
these") reuse the conversation state's active topic + constraints.

ScrapeGraphAI: used when installed AND enabled in Settings; the LLM-powered extraction
runs on the local backend (config.llm defaults). Without it (or when a scrape fails) the
skill falls back to built-in search + fetch + regex/bs4 extraction — the pipeline, the
compliance rules and the output shape are identical in both modes; only the extractor
differs. Every mode is injectable for offline tests (`_search`, `_fetch`, `_extract`).
"""
from __future__ import annotations

import json
import re
import time

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?:\+\d{1,3}[\s-]?)?(?:\(\d{2,4}\)[\s-]?)?\d{3,4}[\s-]?\d{3,4}[\s-]?\d{3,4}")
SITE = re.compile(r"https?://[^\s\"'<>)]+", re.I)


# ------------------------------------------------------------------ request parsing
def parse_request(text: str, state: dict | None = None) -> dict:
    """The lead request: role/industry, location(s), max results, source types.
    Follow-ups inherit location/industry from the conversation state (D5/E4)."""
    t = (text or "").lower()
    out = {"industry": "", "roles": [], "locations": [], "max_results": 12, "query": (text or "").strip()}
    m = re.search(r"\b(?:find|get)\s+(\d{1,3})\b", t)
    if m:
        out["max_results"] = min(100, int(m.group(1)))
    for rx, key in ((r"\b(?:agencies|agency)\b", "agency"),
                    (r"\bfounders?\b", "founder"),
                    (r"\b(?:consultanc\w+|consultants?)\b", "consultant"),
                    (r"\b(?:law firms?|attorneys?)\b", "law firm"),
                    (r"\b(?:e-?commerce)\b(?:\s+\w+)?", "ecommerce"),
                    (r"\b(?:companies|firms|businesses)\b", "company")):
        if re.search(rx, t):
            out["roles"].append(key)
    locs = []
    for m in re.finditer(r"\bin\s+((?:the\s+)?[a-z][a-z .'-]{2,28}?)(?:\s+(?:who|that|which|with|for|and|,)|$)", t):
        cand = m.group(1).strip(" ,.")
        cand = re.sub(r"^(?:the|us|uk|uae)\s+", "", cand)
        if 2 < len(cand) <= 30 and not any(w in cand for w in ("who", "which", "that", "find")):
            locs.append(cand)
    known = ("texas", "dubai", "uae", "london", "karachi", "lahore", "peshawar", "islamabad",
             "new york", "california", "delaware", "singapore", "toronto", "berlin")
    for k in known:
        if re.search(rf"\b{k}\b", t):
            locs.append(k)
    out["locations"] = sorted(set(locs))[:3]
    # follow-up context: constraints from the conversation state override/extend
    for k, v in ((state or {}).get("constraints") or {}).items():
        if k == "country" and v and not out["locations"]:
            out["locations"] = [v.lower()]
    out["industry"] = next((k for k in ("llc formation", "e-commerce", "ecommerce", "real estate",
                                        "logistics", "immigration") if k in t), "")
    if not out["roles"]:
        out["roles"] = ["company"]
    return out


def build_queries(p: dict) -> list[str]:
    """Search queries from the parsed request (site-hinted where it helps)."""
    role = " ".join(p["roles"][:2]) or "companies"
    loc = " in " + p["locations"][0] if p.get("locations") else ""
    ind = f" {p['industry']}" if p["industry"] else ""
    base = f"{role}{ind}{loc}"
    return [f"{base} directory", f"{base} contact email", base]


# ------------------------------------------------------------------ pluggable I/O
def _search(query: str, k: int = 8) -> list[dict]:
    """Web search via the existing stack (SearXNG/Bing wrappers); [] when offline."""
    try:
        from orchestrator.deep_research import quick_search
        out = quick_search(query, max_results=k)
        return out if isinstance(out, list) else []
    except Exception:
        return []


def _fetch(url: str) -> str:
    """One page's readable text (the browser stack's fetcher when present)."""
    try:
        from orchestrator.deep_research import fetch_text
        return fetch_text(url) or ""
    except Exception:
        try:
            import requests
            r = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0 (ISHTI lead finder)"},
                             allow_redirects=True)
            if r.status_code != 200:
                return ""
            import bs4
            return bs4.BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True)[:8000]
        except Exception:
            return ""


def robots_ok(url: str) -> bool:
    """E4 compliance: robots.txt disallow check for the page's path (public data only;
    no login-wall scraping, no CAPTCHA bypassing anywhere in this pipeline)."""
    try:
        from urllib.parse import urlparse
        from urllib.robotparser import RobotFileParser
        p = urlparse(url)
        rp = RobotFileParser()
        rp.set_url(f"{p.scheme}://{p.netloc}/robots.txt")
        rp.read()
        return rp.can_fetch("ISHTI-leadfinder", url) or rp.can_fetch("*", url)
    except Exception:
        return True  # unreachable robots.txt: default-allow, rate-limited fetch


def _scrapegraph_extract(page_text: str, what: dict) -> list[dict]:
    """ScrapeGraphAI's LLM extraction on the LOCAL backend; None when unavailable."""
    try:
        from scrapegraphai.graphs import SmartScraperGraph
    except Exception:
        return None
    cfg_graph = {
        # exact tags that exist on this box ("ollama/qwen2.5" alone would 404 in ollama):
        # 3b is the fast extraction model; the owner's bigger models stay free for chat
        "llm": {"model": "ollama/qwen2.5:3b", "base_url": "http://localhost:11434",
                "format": "json", "temperature": 0},
        "embeddings": {"model": "ollama/nomic-embed-text", "base_url": "http://localhost:11434"},
        "verbose": False, "headless": True,
    }
    prompt = ("Extract businesses/people matching this request from the page text. "
              f"Looking for: {json.dumps(what)}. Return JSON list of "
              "{name, company, role, website, email, phone, location}.")
    try:
        g = SmartScraperGraph(prompt=prompt, source=page_text, config=cfg_graph)
        out = g.run()
        return out if isinstance(out, list) else (out or {}).get("leads", [])
    except Exception:
        return None


def _regex_extract(page_text: str, source_url: str, what: dict) -> list[dict]:
    """Built-in extractor: contact-shaped finds from one page (always available)."""
    if not page_text:
        return []
    leads = []
    for m in EMAIL.finditer(page_text):
        email = m.group(0)
        if any(email.lower().endswith(b) for b in (".png", ".jpg", ".webp", ".gif")):
            continue
        ctx = page_text[max(0, m.start() - 120):m.end() + 120]
        site = SITE.search(ctx) or SITE.search(page_text[:400])
        name_m = re.search(r"([A-Z][A-Za-z&.\- ]{2,40}(?:LLC|Ltd|Inc|GmbH|FZE|Services|Agency|Consulting))",
                           ctx) or re.search(r"([A-Z][A-Za-z&.\- ]{2,40}(?:LLC|Ltd|Inc|GmbH|FZE|Services|Agency|Consulting))",
                                             page_text[:400])
        leads.append({
            "name": (name_m.group(1).strip() if name_m else email.split("@")[0])[:80],
            "company": (name_m.group(1).strip() if name_m else "")[:80],
            "role": what.get("roles", ["company"])[0] if what.get("roles") else "",
            "website": (site.group(0) if site and site.group(0) != source_url else "")[:120],
            "email": email[:80], "phone": "",
            "location": (what.get("locations") or [""])[0].title(),
            "industry": what.get("industry", ""),
            "source_url": source_url, "relevance": 0.5,
        })
        if len(leads) >= 5:
            break
    return leads


def find(p: dict, *, use_scrapegraph: bool = True, max_pages: int = 6) -> dict:
    """The pipeline: queries -> search -> fetch (robots-ok, rate-limited) -> extract
    (ScrapeGraphAI -> fallback regex) -> dedupe -> verify hints. Returns the lead rows."""
    queries = build_queries(p)
    seen_urls, pages, leads, mode = set(), 0, [], "regex"
    for q in queries:
        for hit in _search(q, k=6):
            url = (hit.get("url") or hit.get("link") or "").strip() if isinstance(hit, dict) else ""
            title = (hit.get("title") or "") if isinstance(hit, dict) else ""
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            if not robots_ok(url):
                continue
            pages += 1
            if pages > max_pages:
                break
            time.sleep(0.4)  # E4: rate limit — one polite fetch per page
            text = _fetch(url)
            found = None
            if use_scrapegraph:
                found = _scrapegraph_extract(text, p)
                if found is not None:
                    mode = "scrapegraphai"
            if not found:
                found = _regex_extract(text, url, p)
            for f in found:
                f.setdefault("source_url", url)
                if title and not f.get("company"):
                    f["company"] = title[:80]
                leads.append(f)
        if pages >= max_pages:
            break
    # dedupe within the run: same email or same name+domain wins once
    dedup, by_email = [], set()
    for f in leads:
        key = (f.get("email") or f"{(f.get('name') or '').lower()}|{(f.get('website') or '').lower()}")
        if not key or key in by_email:
            continue
        by_email.add(key)
        f["relevance"] = min(0.95, 0.4 + (0.2 if f.get("email") else 0) +
                             (0.15 if f.get("website") else 0) + (0.1 if f.get("location") else 0))
        dedup.append(f)
    return {"leads": dedup[:p.get("max_results", 12)], "pages": pages, "mode": mode,
            "queries": queries}


# ------------------------------------------------------------------ ISHTI skill entry
def match(text: str) -> bool:
    """Deterministic trigger (checked before Laya): explicit lead-finder intents —
    'find leads/prospects…', 'find agencies/founders/companies … in <place>'."""
    t = (text or "").lower()
    if "more like these" in t:  # follow-up to a previous find
        return True
    if not re.search(r"\b(?:leads?|prospects?|agenc(?:y|ies)|founders?|vendors?|suppliers?)\b", t):
        return False
    return bool(re.search(r"\b(?:find|get|build|scrape|collect|generate|make)\b", t))


def _table(leads: list) -> str:
    from ishti.skills import md_table
    return md_table(["Name", "Company", "Email", "Location", "Relevance", "Source"],
                    [[x.get("name"), x.get("company"), x.get("email"), x.get("location"),
                      f"{x.get('relevance', 0):.0%}", x.get("source_url", "")[:40]] for x in leads[:12]])


def run(text: str, session) -> dict:
    from ishti.skills import trace
    state = {}
    try:
        from ishti.core.state import get_manager
        state = get_manager().get(session.get("id", "default")).to_dict()
    except Exception:
        pass
    followup = any(w in text.lower() for w in ("more like these", "find more", "only the ones"))
    p = parse_request(text, state)
    if followup and not p["locations"]:
        p["locations"] = [(state.get("constraints") or {}).get("country", "").lower()] or []
    trace(session, "find_leads", f"searching: {p['query'][:60]}", status="start")
    try:
        from ishti.core.settings import get_settings_service
        sg_enabled = bool(get_settings_service().get().get("integrations", {}).get("scrapegraph_enabled", True))
    except Exception:
        sg_enabled = True
    res = find(p, use_scrapegraph=sg_enabled)
    if not res["leads"]:
        return {"reply": ("I searched but found no leads I can verify from public pages. "
                          "Try naming an industry or a city, or widen the request."),
                "sources": [], "no_escalate": True}
    import ishti.leads as leads_db
    run_id = f"run_{time.strftime('%Y%m%d_%H%M%S')}"
    counts = leads_db.add_many(res["leads"], run_id=run_id)
    rows = leads_db.rows(run_id=run_id, limit=20)
    csv_path = leads_db.export(fmt="csv")
    trace(session, "find_leads", f"{len(rows)} leads ({res['mode']}, {res['pages']} pages)", status="done")
    reply = (f"Found {len(rows)} leads ({res['mode']}; {res['pages']} public pages read; "
             f"{counts['added']} new, {counts['refreshed']} already known). Saved to the leads "
             f"store and exported:\n\n{_table(rows)}\n\nCSV: {csv_path}\n"
             "Every lead keeps its source URL; say 'find more like these' or 'only the ones in <city>' to refine.")
    return {"reply": reply, "sources": [{"url": r["source_url"], "title": r.get("company") or r.get("name")}
                                        for r in rows[:8]]}


if __name__ == "__main__":
    import tempfile
    import ishti.leads as leads_db
    leads_db.set_db_path(os_path := __import__("os").path.join(tempfile.mkdtemp(), "leads.db"))
    # parsing
    p = parse_request("find 20 US LLC formation agencies in Texas")
    assert p["max_results"] == 20 and "texas" in p["locations"] and "agency" in p["roles"], p
    p2 = parse_request("find e-commerce founders in UAE who need US company setup")
    assert "founder" in p2["roles"] and "uae" in p2["locations"], p2
    # follow-up constraints from state
    p3 = parse_request("only the ones in Dubai", {"constraints": {"country": "UAE"}})
    assert "uae" in p3["locations"] or "dubai" in p3["locations"], p3
    # queries
    qs = build_queries(p)
    assert any("texas" in q for q in qs) and len(qs) == 3, qs
    # regex extractor
    page = ("Best LLC formation services in Texas! Contact Acme Business Services LLC at "
            "info@acmesvc.com or visit https://acmesvc.example.com. Also Gulf Consultants FZE "
            "hello@gulf.example (Dubai).")
    got = _regex_extract(page, "https://dir.example/texas", {"roles": ["agency"], "locations": ["texas"]})
    emails = {x["email"] for x in got}
    assert "info@acmesvc.com" in emails and "hello@gulf.example" in emails, got
    assert any("Acme" in x["name"] for x in got), got
    # scrapegraph missing -> regex mode end-to-end with injected search/fetch
    import ishti.skills.find_leads.skill as FL
    FL._search = lambda q, k=8: [{"url": "https://dir.example/texas", "title": "Texas agencies"}]
    FL._fetch = lambda url: page
    res = FL.find(p, use_scrapegraph=False, max_pages=3)
    assert res["mode"] == "regex" and res["leads"], res
    assert all(x["source_url"] == "https://dir.example/texas" for x in res["leads"])
    # stored + exported
    counts = leads_db.add_many(res["leads"], run_id="r1")
    assert counts["added"] >= 2, counts
    rows = leads_db.rows(run_id="r1")
    assert rows and rows[0]["relevance"] >= rows[-1]["relevance"], "ranked by relevance"
    print(f"[self-check] find_leads OK — parsing, follow-ups, queries, extraction, pipeline "
          f"({res['mode']}, {res['pages']} page), dedupe+store {counts}")
