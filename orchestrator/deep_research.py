"""
ISHTI Deep Research Engine
Executes multi-source online investigation inspired by Odysseus:
1. Research planner (decomposing topic into sub-queries)
2. Parallel web searches via MultiProviderSearch
3. Parallel content fetching & text extraction
4. Evidence extraction & cross-checking
5. Synthesized final report with verifiable citations [1], [2]
6. Persistent Research Memory
"""

import os
import sys
import time
import json
import re
import concurrent.futures
from typing import Dict, Any, List, Optional, Callable
import requests

ROOT_DIR = r"D:\ISHTI"
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from tools.web.search import get_search_manager, WebSearchResult
from tools.web.fetch import WebFetcher
from orchestrator.model_router import ModelRouter
from tools.web.relevance import filter_sources
from orchestrator.memory.user_profile import get_user_memory

RESEARCH_MEM_DIR = os.path.join(ROOT_DIR, "orchestrator", "memory", "research")
os.makedirs(RESEARCH_MEM_DIR, exist_ok=True)


READ_BUDGET_S = 4.0
MAX_REWORDS = 2  # reformulations after the first search before "no confident match"
BLOCKED = re.compile(r"enable javascript|access denied|are you a robot|captcha|verify you are human|subscribe to (?:continue|read)|"
                     r"sign in to (?:continue|read)|403 forbidden|404 not found|page not found|just a moment\.\.\.")


class DeepResearchEngine:
    def __init__(self, event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None):
        self.search_manager = get_search_manager()
        self.fetcher = WebFetcher(timeout=10)
        self.model_router = ModelRouter()
        self.event_callback = event_callback

    def emit(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.event_callback:
            try:
                self.event_callback(event_type, data)
            except Exception as e:
                print(f"[DeepResearch] Event callback error: {e}")
        print(f"[RESEARCH] [{event_type.upper()}] {json.dumps(data, ensure_ascii=False)[:180]}")

    def quick_search(self, user_prompt: str, depth: str = "SIMPLE") -> Dict[str, Any]:
        """
        Quick Search: For single factual current queries.
        1 search query -> read top sources -> synthesize answer with citations.
        """
        # Clean query, then localise it if it is a "near me / local" question.
        search_query = re.sub(r"^(what\s+is\s+the|what's\s+the|who\s+is|tell\s+me\s+about|search\s+(?:for|on|the))\s+", "", user_prompt, flags=re.IGNORECASE).strip(" ?.")
        search_query = self._localize(search_query)

        # Self-verifying loop: search -> read -> keep only pages that really answer the question; if none do,
        # reword the query and try again (at most MAX_REWORDS times), then say so instead of guessing.
        tried, skipped, good = [], [], []
        query = search_query
        for attempt in range(MAX_REWORDS + 1):
            tried.append(query)
            self.emit("status_change", {"state": "SEARCHING", "detail": ("Searching the internet for: " if not attempt
                                                                         else "Trying a different search: ") + query})
            results = self.search_manager.search(query, max_results=6)
            if not results and not attempt:
                results = self.search_manager.search(user_prompt, max_results=6)
            if results:
                # Drop sources that do not address the query (dictionary pages, unrelated products).
                relevant = filter_sources(query, [r.to_dict() | {"_obj": r} for r in results])
                results = [r["_obj"] for r in relevant] or results[:3]
                top = [r for r in results if r.url not in {g["url"] for g in good}][:4]
                self.emit("status_change", {"state": "RESEARCHING", "detail": f"Found {len(results)} sources, opening the top {len(top)}..."})
                pages = self._read(top)
                self.emit("status_change", {"state": "RESEARCHING", "detail": "Reading through them now..."})
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(pages) or 1) as pool:
                    verdicts = list(pool.map(lambda pg: self._page_verdict(user_prompt, pg), pages))
                for pg, (ok, why) in zip(pages, verdicts):
                    if ok:
                        good.append(pg)
                    else:
                        skipped.append(f"{pg['domain']} ({why})")
                        self.emit("status_change", {"state": "VERIFYING", "detail": f"Skipped {pg['domain']}: {why}"})
                if good:
                    break
            if attempt < MAX_REWORDS:
                query = self._reword(user_prompt, tried, skipped)

        if not good:
            return {
                "answer": ("I couldn't find a source that confidently answers that. Searched: "
                           + "; ".join(f"\"{q}\"" for q in tried)
                           + (". Skipped: " + ", ".join(skipped[:5]) if skipped else ". The search providers returned nothing.")
                           + " Tell me more (a name, place or date) and I'll look again."),
                "sources": [], "verified": False, "mode": "QUICK_SEARCH", "confident": False,
            }

        self.emit("status_change", {"state": "VERIFYING", "detail": f"{len(good)} source{'s' if len(good) != 1 else ''} answer it; writing the answer..."})
        sources_context, sources_list = [], []
        for idx, pg in enumerate(good[:4], 1):
            sources_context.append(f"[{idx}] Title: {pg['title']}\nURL: {pg['url']}\nExcerpt: {pg['content'][:1000]}")
            sources_list.append({"id": idx, "title": pg["title"], "domain": pg["domain"], "url": pg["url"],
                                 "snippet": pg["snippet"], "verified": pg["read"]})

        # Synthesize with local model
        self.emit("status_change", {"state": "VERIFYING", "detail": "Cross-checking facts and synthesizing response..."})
        answer = self._synthesize_quick_answer(user_prompt, sources_context, sources_list, depth=depth)

        return {
            "answer": answer,
            "sources": sources_list,
            # verified only if at least one source was really fetched and every source has a URL
            "verified": any(s["verified"] for s in sources_list) and all(
                str(s.get("url", "")).startswith("http") for s in sources_list),
            "mode": "QUICK_SEARCH",
        }

    def _read(self, top) -> List[Dict[str, Any]]:
        """Read pages in parallel under one time budget. A slow page (the reader-mode fallback can take 10-12 s)
        must not hold the answer hostage: past the budget its search snippet is used instead."""
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=len(top) or 1)
        futures = [pool.submit(self.fetcher.fetch, r.url, max_chars=2500) for r in top]
        concurrent.futures.wait(futures, timeout=READ_BUDGET_S)
        pool.shutdown(wait=False, cancel_futures=True)
        pages = []
        for r, fut in zip(top, futures):
            fetched = fut.result() if fut.done() and not fut.exception() else {}
            content = fetched.get("content") or ""
            pages.append({"title": r.title, "url": r.url, "domain": r.source, "snippet": r.snippet,
                          "content": content or r.snippet, "read": len(content) > 80})
        return pages

    def _page_verdict(self, question: str, page: Dict[str, Any]):
        """(keep?, reason). Blocked/paywalled/empty pages are dropped without asking the model; otherwise the
        local model judges whether the page answers the question - leniently, because its own knowledge ends
        before today (the wording that rejected correct 2026 answers was tuned out in the dispatcher's verifier)."""
        head = (page["content"] or "")[:700].lower()
        if page["read"] and BLOCKED.search(head):
            return False, "blocked or paywalled"
        if len((page["content"] or "").strip()) < 60:
            return False, "no readable content"
        from ishti import llm
        verdict = llm.ask(
            f"Today is {time.strftime('%d %B %Y')}. Question: {question}\nPage title: {page['title']}\nURL: {page['url']}\n"
            f"Page text: {page['content'][:1400]}\n\nDoes this page contain information that answers the question, about "
            "the right thing (same product, person, place, event)? Partly is enough. Reply 'yes' or 'no: <reason in "
            "3-6 words, e.g. wrong product, different person, outdated, only a homepage>'.",
            system=None, temperature=0, max_tokens=16).strip().lower()
        if verdict.startswith("no"):
            return False, verdict.split(":", 1)[-1].strip(" .") or "doesn't answer it"
        return True, ""

    def _reword(self, question: str, tried: List[str], skipped: List[str]) -> str:
        from ishti import llm
        q = llm.ask(
            f"Today is {time.strftime('%d %B %Y')}. Question: {question}\nSearches already tried: {tried}\n"
            f"Pages rejected: {skipped[:6]}\nWrite ONE different web search query that is more likely to find a page "
            "that answers the question (other keywords, add the likely year/place/official site, or make it more "
            "specific). Output only the query.", system=None, temperature=0.3, max_tokens=30).strip().strip('"')
        return q if 2 <= len(q.split()) <= 20 and q not in tried else f"{tried[0]} {time.strftime('%Y')}"

    def _localize(self, query: str) -> str:
        """Local questions get the owner's city, so the search engine cannot guess by IP."""
        q = query.lower()
        if not any(cue in q for cue in ("near me", " local ", "nearby", "around here", "in my city", "here")):
            return query
        prefs = get_user_memory().get_preferences()
        profile = get_user_memory().get_profile()
        city = prefs.default_weather_city or (profile.location or {}).get("city", "")
        return f"{query} {city}".strip() if city and city.lower() not in q else query

    def run_deep_research(self, topic: str, max_queries: int = 4) -> Dict[str, Any]:
        """
        Deep Research: Multi-query decomposition, parallel source reading,
        cross-checking, structured report, and Research Memory storage.
        """
        self.emit("research_started", {"topic": topic, "status": "planning"})
        self.emit("status_change", {"state": "PLANNING", "detail": f"Formulating deep research strategy for: '{topic}'"})

        # Step 1: Query Formulation. The owner's own question is always searched first -
        # the model's paraphrases lose the cues that route to the right provider (a news
        # index, a release feed), so searching only them returns encyclopedia pages.
        queries = self._generate_research_queries(topic, max_queries)
        if topic not in queries:
            queries = [topic] + queries[: max_queries - 1]
        self.emit("research_queries_generated", {"queries": queries})

        all_results: Dict[str, WebSearchResult] = {}

        # Step 2: Multi-Query Search
        for q_idx, q in enumerate(queries):
            self.emit("status_change", {
                "state": "SEARCHING",
                "detail": f"Query {q_idx+1}/{len(queries)}: '{q}'",
                "progress_pct": int(((q_idx + 1) / (len(queries) * 2)) * 50),
            })
            found = self.search_manager.search(q, max_results=3)
            for item in found:
                if item.url not in all_results:
                    all_results[item.url] = item

        # Relevance gate: keep only sources that actually address the topic. This is what
        # stops a weather question from citing a dictionary entry (docs/AUDIT.md F4).
        pooled = list(all_results.values())
        # Score against each query that was actually searched, separately: a source has to
        # answer ONE of them. Concatenating them into a single string would demand that one
        # page cover every angle at once, which drops everything.
        kept = filter_sources([topic] + queries, [s.to_dict() | {"_obj": s} for s in pooled])
        dropped = len(pooled) - len(kept)
        sources_pool = [k["_obj"] for k in kept][:8]
        if dropped:
            print(f"[DeepResearch] Dropped {dropped} irrelevant source(s) of {len(pooled)}.")
        self.emit("research_sources_found", {
            "count": len(sources_pool),
            "dropped_irrelevant": dropped,
            "sources": [s.to_dict() for s in sources_pool],
        })

        # Step 3: Parallel Page Reading & Evidence Extraction
        self.emit("status_change", {
            "state": "RESEARCHING",
            "detail": f"Reading and extracting evidence from {len(sources_pool)} sources...",
            "progress_pct": 55,
        })

        extracted_evidence = []
        structured_sources = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            future_to_src = {
                executor.submit(self.fetcher.fetch, src.url, max_chars=3000): (i + 1, src)
                for i, src in enumerate(sources_pool)
            }
            for future in concurrent.futures.as_completed(future_to_src):
                cid, src = future_to_src[future]
                fetch_ok = False
                try:
                    fetched = future.result()
                    fetch_ok = fetched.get("status") == "success" and bool(fetched.get("content"))
                    text = fetched.get("content") or src.snippet
                except Exception:
                    text = src.snippet

                self.emit("research_reading_source", {
                    "source_num": cid,
                    "total": len(sources_pool),
                    "title": src.title,
                    "url": src.url,
                })

                extracted_evidence.append({
                    "citation_id": cid,
                    "title": src.title,
                    "url": src.url,
                    "domain": src.source,
                    "excerpt": text[:1200],
                })
                structured_sources.append({
                    "id": cid,
                    "title": src.title,
                    "domain": src.source,
                    "url": src.url,
                    "snippet": src.snippet,
                    # verified = this page was really fetched, not assumed
                    "verified": fetch_ok,
                })

        # Sort sources by citation ID
        structured_sources.sort(key=lambda s: s["id"])
        extracted_evidence.sort(key=lambda e: e["citation_id"])

        # Step 4: Cross-check & Verification
        self.emit("status_change", {
            "state": "VERIFYING",
            "detail": "Cross-checking claims across independent sources...",
            "progress_pct": 85,
        })

        # Step 5: Synthesize Comprehensive Research Report
        self.emit("status_change", {
            "state": "RESPONDING",
            "detail": "Compiling final verified research report with citations...",
            "progress_pct": 95,
        })
        report = self._synthesize_research_report(topic, extracted_evidence, structured_sources)

        # Step 6: Store in Research Memory
        self._store_research_memory(topic, queries, report, structured_sources)

        self.emit("research_completed", {
            "topic": topic,
            "sources_count": len(structured_sources),
            "report_preview": report[:250],
        })
        self.emit("status_change", {"state": "COMPLETED", "detail": "Research report generated successfully.", "progress_pct": 100})

        return {
            "topic": topic,
            "report": report,
            "sources": structured_sources,
            "queries": queries,
            "evidence_count": len(extracted_evidence),
            "sources_dropped_irrelevant": dropped,
            "verified": any(s["verified"] for s in structured_sources),
            "mode": "DEEP_RESEARCH",
        }

    def _generate_research_queries(self, topic: str, count: int = 3) -> List[str]:
        """Uses local model to generate 3-4 diverse search angles."""
        host = self.model_router.host
        # The 3B fast model invents vague sub-queries ("AI"), which return front pages.
        model = self.model_router.get_model("primary")

        # Every query must stay on the owner's actual subject. Unconstrained, the model produced tangents
        # like "meaning of the phrase ..." and "user confusion when searching online".
        prompt = (
            f"Today is {time.strftime('%d %B %Y')}; if a query needs a year, use {time.strftime('%Y')}.\n"
            f"Write {count} web search queries that find the facts needed to answer this request.\n"
            f"Request: '{topic}'\n"
            f"Rules: each query names the exact subject (product, event, person) from the request; cover "
            f"different facts the answer needs (e.g. price, specs, date), not different topics; no queries about "
            f"words, phrases, meanings or the request itself; short keyword-style queries.\n"
            f"Return ONLY {count} numbered queries (1. ... 2. ...), no markdown."
        )
        queries = [topic]
        try:
            res = requests.post(
                f"{host}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0.2}},
                timeout=12,
            )
            if res.status_code == 200:
                raw = res.json().get("response", "")
                lines = [re.sub(r"^\d+[\.\)]\s*", "", l).strip() for l in raw.split("\n") if l.strip()]
                clean_queries = [l for l in lines if len(l) > 5][:count]
                if clean_queries:
                    queries = clean_queries
        except Exception:
            pass

        # Fallback queries if model failed
        if len(queries) < 2:
            queries = [
                topic,
                f"latest developments in {topic}",
                f"{topic} official documentation github",
            ]
        return queries

    def _synthesize_quick_answer(self, prompt: str, context: List[str], sources: List[Dict[str, Any]],
                                 depth: str = "SIMPLE") -> str:
        today = time.strftime("%A %d %B %Y")
        answer = self.model_router.generate(
            f"Today is {today}. Live sources fetched seconds ago:\n" + "\n\n".join(context)
            + f"\n\nOwner's question: {prompt}\n\nUse the most recent information in the sources; if a source is "
              "about an earlier year than the question asks for, say so instead of presenting it as current.",
            depth=depth, tools_used=["web_search", "web_fetch"], evidence_is_live=True,
            memory_query=prompt, timeout=45,
        )
        if answer:
            return answer
        bullets = "\n".join([f"- {s['title']}: {s['snippet']} ({s['url']})" for s in sources[:3]])
        return f"From live search:\n\n{bullets}"

    def _synthesize_research_report(self, topic: str, evidence: List[Dict[str, Any]], sources: List[Dict[str, Any]]) -> str:
        if not evidence:
            return (f"I could not find sources that actually address '{topic}'. "
                    f"The searches returned results, but none were relevant, so there is nothing to report.")

        evidence_str = "\n\n".join([
            f"Source [{e['citation_id']}] ({e['title']} - {e['domain']}):\n{e['excerpt']}"
            for e in evidence
        ])

        report = self.model_router.generate(
            f"Research topic: {topic}\n\n"
            f"Evidence actually fetched from the web (these are the only sources that exist):\n{evidence_str}\n\n"
            f"Write the report. Include only the sections the evidence supports - if the evidence "
            f"does not let you compare options, do not write a comparison section; if the sources do "
            f"not agree or disagree about anything, do not write a cross-verification section. "
            f"Cite as [1], [2] using exactly the numbers above. Never cite a number that is not listed.",
            depth="DEEP", tools_used=["web_search", "web_fetch"], evidence_is_live=True,
            memory_query=topic, timeout=90,
        )
        if report:
            return report

        cards = "\n".join([f"- [{s['id']}] {s['title']} ({s['domain']}): {s['snippet']}" for s in sources])
        return f"Research on {topic} - sources fetched:\n{cards}"

    def _store_research_memory(self, topic: str, queries: List[str], report: str, sources: List[Dict[str, Any]]) -> None:
        """Stores structured research knowledge into Research Memory."""
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", topic[:40]).strip("_").lower()
        filepath = os.path.join(RESEARCH_MEM_DIR, f"{slug}_{int(time.time())}.json")

        record = {
            "topic": topic,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "queries": queries,
            "sources_count": len(sources),
            "sources": sources,
            "summary": report[:1000],
            "full_report": report,
        }

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(record, f, indent=2, ensure_ascii=False)
            print(f"[DeepResearch] Stored research memory to: {filepath}")
        except Exception as e:
            print(f"[DeepResearch] Failed to store research memory: {e}")


if __name__ == "__main__":
    engine = DeepResearchEngine()
    print("Testing Quick Search for latest Python version...")
    res = engine.quick_search("What is the latest version of Python?")
    print("\nAnswer:\n", res["answer"][:300])
    print("\nSources count:", len(res["sources"]))
    for s in res["sources"][:2]:
        print(f"[{s['id']}] {s['title']} - {s['url']}")
