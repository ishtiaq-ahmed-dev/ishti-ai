"""
ISHTI Multi-Provider Search Registry
Integrates Bing Lite, GitHub API, Wikipedia API, arXiv API,
DuckDuckGo fallback, and isolated Playwright Chrome search with self-healing failover.
"""

import os
import sys
import time
import json
import base64
import re
import urllib.parse
from typing import List, Dict, Any, Optional
import requests
from bs4 import BeautifulSoup

ROOT_DIR = r"D:\ISHTI"
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from tools.web.provider import WebSearchProvider, WebSearchResult

REFLECTIONS_PATH = os.path.join(ROOT_DIR, "orchestrator", "memory", "reflections.md")

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


SEARXNG_CONTAINER = "ishti-searxng"
DOCKER_DESKTOP = r"C:\Program Files\Docker\Docker\Docker Desktop.exe"
_heal = {"last": 0.0}


def ensure_searxng(wait_s: float = 90) -> bool:
    """Degraded-mode self-heal: start the SearXNG container, launching Docker Desktop first if its engine is
    down (after a reboot neither is running and search silently fell back to poor providers)."""
    import subprocess
    import time
    url = SearxngSearchProvider.URL + "/healthz"
    ok = lambda: requests.get(url, timeout=2).status_code == 200
    try:
        if ok():
            return True
    except Exception:
        pass
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    run = lambda *a: subprocess.run(["docker", *a], capture_output=True, text=True, timeout=30, creationflags=flags)
    try:
        if run("info").returncode != 0 and os.path.isfile(DOCKER_DESKTOP):
            subprocess.Popen([DOCKER_DESKTOP], close_fds=True)
        deadline = time.time() + wait_s
        while time.time() < deadline:
            if run("start", SEARXNG_CONTAINER).returncode == 0:
                break
            time.sleep(3)
        while time.time() < deadline:
            try:
                if ok():
                    return True
            except Exception:
                pass
            time.sleep(1.5)
    except (OSError, subprocess.SubprocessError):
        pass
    return False


def heal_in_background():
    """At most one self-heal attempt every 10 minutes, never blocking the search that noticed the outage."""
    import threading
    import time
    if time.time() - _heal["last"] > 600:
        _heal["last"] = time.time()
        threading.Thread(target=ensure_searxng, daemon=True).start()


class SearxngSearchProvider(WebSearchProvider):
    """Local SearXNG metasearch (Docker, ishti/searxng/settings.yml). Aggregates Google, Brave,
    DuckDuckGo, Wikipedia... Scraping Bing directly now returns unrelated pages (it matched only
    the first word of multi-word queries), so this is the primary general-web provider."""

    URL = os.environ.get("ISHTI_SEARXNG_URL", "http://127.0.0.1:8890")

    @property
    def name(self) -> str:
        return "searxng"

    def is_available(self) -> bool:
        try:
            return requests.get(f"{self.URL}/healthz", timeout=2).status_code == 200
        except Exception:
            return False

    def search(self, query: str, max_results: int = 5) -> List[WebSearchResult]:
        try:
            r = requests.get(f"{self.URL}/search", params={"q": query, "format": "json", "language": "en"},
                             timeout=10)
            r.raise_for_status()
            hits = r.json().get("results", [])
        except Exception as exc:
            print(f"[SearXNG] unavailable ({type(exc).__name__}); falling back and starting it in the background")
            heal_in_background()
            return []
        return [WebSearchResult(title=h.get("title", ""), url=h["url"], snippet=h.get("content", "")[:400],
                                publication_date=h.get("publishedDate") or "",
                                metadata={"engines": h.get("engines", [])})
                for h in hits[:max_results] if h.get("url", "").startswith("http")]


class BingLiteSearchProvider(WebSearchProvider):
    """
    Scrapes Bing HTML results and extracts clean URLs by decoding u=a1 base64 links.
    """
    @property
    def name(self) -> str:
        return "bing_lite"

    def is_available(self) -> bool:
        try:
            r = requests.get("https://www.bing.com", headers=DEFAULT_HEADERS, timeout=4)
            return r.status_code == 200
        except Exception:
            return False

    def search(self, query: str, max_results: int = 5) -> List[WebSearchResult]:
        results: List[WebSearchResult] = []
        encoded = urllib.parse.quote(query)
        url = f"https://www.bing.com/search?q={encoded}&setlang=en&cc=US"

        try:
            res = requests.get(url, headers=DEFAULT_HEADERS, timeout=8)
            if res.status_code != 200:
                return results

            soup = BeautifulSoup(res.text, "html.parser")
            for item in soup.select("li.b_algo"):
                a = item.select_one("h2 a") or item.find("a")
                if not a:
                    continue

                title = a.get_text().strip()
                raw_url = a.get("href", "")
                actual_url = self._decode_bing_url(raw_url)
                p = item.select_one(".b_caption p") or item.find("p")
                snippet = p.get_text().strip() if p else ""

                # An undecodable bing.com/ck redirect is not a real source - drop it rather
                # than cite a URL whose destination we never resolved.
                if "bing.com/ck/" in actual_url:
                    continue
                if title and actual_url and actual_url.startswith("http"):
                    results.append(WebSearchResult(
                        title=title,
                        url=actual_url,
                        snippet=snippet,
                        verified=True
                    ))
                if len(results) >= max_results:
                    break
        except Exception as e:
            print(f"[BingLiteSearch] Query error for '{query}': {e}")

        return results

    def _decode_bing_url(self, raw_url: str) -> str:
        """Decodes Bing redirect links with u=a1... base64 encoding."""
        if "u=a1" in raw_url:
            try:
                part = raw_url.split("u=a1")[1].split("&")[0]
                part += "=" * (-len(part) % 4)
                decoded = base64.b64decode(part).decode("utf-8", errors="ignore")
                if decoded.startswith("http"):
                    return decoded
            except Exception:
                pass
        return raw_url


class GitHubSearchProvider(WebSearchProvider):
    """
    Direct GitHub Search API for open-source repositories, projects, frameworks, and tools.
    Zero-key required for public search.
    """
    @property
    def name(self) -> str:
        return "github_api"

    def is_available(self) -> bool:
        return True

    def search(self, query: str, max_results: int = 5) -> List[WebSearchResult]:
        results: List[WebSearchResult] = []
        clean_q = query.replace('"', '').replace("'", '').replace("github", "").replace("project", "").replace("projects", "").strip()
        url = f"https://api.github.com/search/repositories?q={urllib.parse.quote(clean_q)}&sort=stars&order=desc"
        headers = {"User-Agent": "ISHTI-Agent/2.0", "Accept": "application/vnd.github.v3+json"}

        try:
            r = requests.get(url, headers=headers, timeout=6)
            if r.status_code == 200:
                data = r.json()
                for item in data.get("items", [])[:max_results]:
                    full_name = item.get("full_name", "")
                    stars = item.get("stargazers_count", 0)
                    desc = item.get("description") or "Open-source GitHub repository"
                    repo_url = item.get("html_url", "")
                    results.append(WebSearchResult(
                        title=f"{full_name} ({stars:,} stars)",
                        url=repo_url,
                        snippet=f"{desc} | Stars: {stars:,} | Topics: {', '.join(item.get('topics', [])[:4])}",
                        source="GitHub",
                        verified=True
                    ))
        except Exception as e:
            print(f"[GitHubSearchProvider] Error on '{query}': {e}")
        return results


class GitHubReleaseProvider(WebSearchProvider):
    """
    Answers "latest version of X" exactly, from the project's own release feed.
    A scraped search page rarely states a version number; the release API always does.
    """
    # Two shapes, tried in order. "X latest version" is checked only after
    # "latest version of X", otherwise the alternation matches the filler word before it.
    VERSION_PATTERNS = [
        re.compile(r"(?:latest|current|newest|last)\s+(?:stable\s+)?(?:version|release)\s+(?:of\s+|for\s+)?([\w\-.+ ]{2,40})", re.IGNORECASE),
        re.compile(r"([\w\-.+]{2,30})\s+(?:latest|current|newest)\s+(?:version|release)", re.IGNORECASE),
    ]
    NOISE = {"the", "a", "an", "for", "of", "on", "software", "app", "tool", "internet", "please", "me"}

    @property
    def name(self) -> str:
        return "github_release"

    def is_available(self) -> bool:
        return True

    @classmethod
    def extract_subject(cls, query: str) -> Optional[str]:
        for pattern in cls.VERSION_PATTERNS:
            m = pattern.search(query or "")
            if not m:
                continue
            raw = (m.group(1) or "").strip(" ?.")
            words = [w for w in raw.split() if w.lower() not in cls.NOISE]
            if words:
                return " ".join(words[-2:])
        return None

    def search(self, query: str, max_results: int = 5) -> List[WebSearchResult]:
        subject = self.extract_subject(query)
        if not subject:
            return []
        headers = {"User-Agent": "ISHTI-Agent/2.0", "Accept": "application/vnd.github.v3+json"}
        try:
            repo_res = requests.get(
                "https://api.github.com/search/repositories",
                params={"q": subject, "sort": "stars", "order": "desc"},
                headers=headers, timeout=8,
            )
            items = repo_res.json().get("items", []) if repo_res.status_code == 200 else []
            if not items:
                return []
            repo = items[0]["full_name"]

            rel = requests.get(f"https://api.github.com/repos/{repo}/releases/latest",
                               headers=headers, timeout=8)
            if rel.status_code != 200:
                return []
            data = rel.json()
            tag = data.get("tag_name")
            if not tag:
                return []
            published = (data.get("published_at") or "")[:10]
            body = (data.get("body") or "").strip()
            return [WebSearchResult(
                title=f"{repo} {tag} - latest release",
                url=data.get("html_url", f"https://github.com/{repo}/releases"),
                snippet=f"Version {tag}, published {published}. {body[:220]}",
                source="github.com",
                publication_date=published,
                content=f"The latest release of {repo} is {tag}, published {published}.",
                verified=True,
            )]
        except Exception as e:
            print(f"[GitHubReleaseProvider] Error on '{query}': {e}")
        return []


class WikipediaSearchProvider(WebSearchProvider):
    """
    Direct Wikipedia Search API for definitions, facts, technologies, and history.
    """
    @property
    def name(self) -> str:
        return "wikipedia_api"

    def is_available(self) -> bool:
        return True

    def search(self, query: str, max_results: int = 5) -> List[WebSearchResult]:
        results: List[WebSearchResult] = []
        clean_q = query.replace("what is", "").replace("who is", "").strip(" ?.")
        url = (
            f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch="
            f"{urllib.parse.quote(clean_q)}&format=json"
        )
        headers = {"User-Agent": "ISHTI-Agent/2.0"}

        try:
            r = requests.get(url, headers=headers, timeout=6)
            if r.status_code == 200:
                data = r.json()
                items = data.get("query", {}).get("search", [])
                for item in items[:max_results]:
                    title = item.get("title", "")
                    raw_snippet = item.get("snippet", "")
                    clean_snippet = BeautifulSoup(raw_snippet, "html.parser").get_text()
                    page_url = f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
                    results.append(WebSearchResult(
                        title=title,
                        url=page_url,
                        snippet=clean_snippet,
                        source="Wikipedia",
                        verified=True
                    ))
        except Exception as e:
            print(f"[WikipediaSearchProvider] Error on '{query}': {e}")
        return results


class ArxivSearchProvider(WebSearchProvider):
    """
    Direct arXiv API for scientific papers, AI research, and preprints.
    """
    @property
    def name(self) -> str:
        return "arxiv_api"

    def is_available(self) -> bool:
        return True

    def search(self, query: str, max_results: int = 4) -> List[WebSearchResult]:
        results: List[WebSearchResult] = []
        clean_q = query.replace("paper", "").replace("research", "").strip()
        url = f"http://export.arxiv.org/api/query?search_query=all:{urllib.parse.quote(clean_q)}&max_results={max_results}"

        try:
            r = requests.get(url, timeout=6)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "xml")
                for entry in soup.find_all("entry"):
                    title = entry.find("title").get_text().strip().replace("\n", " ") if entry.find("title") else "Paper"
                    summary = entry.find("summary").get_text().strip().replace("\n", " ") if entry.find("summary") else ""
                    link = entry.find("id").get_text().strip() if entry.find("id") else ""
                    results.append(WebSearchResult(
                        title=title,
                        url=link,
                        snippet=summary[:250],
                        source="arXiv",
                        verified=True
                    ))
        except Exception as e:
            print(f"[ArxivSearchProvider] Error: {e}")
        return results


class NewsSearchProvider(WebSearchProvider):
    """
    Dated tech/AI news from the Hacker News index (Algolia): keyless, and every hit carries
    a real URL and a real publication timestamp, which a scraped search page does not.
    Used for time-scoped questions ("news this week", "what happened", "latest ... news").
    """
    NEWS_CUES = ("news", "happened", "announced", "release", "launch", "headlines", "stories")
    TIME_CUES = ("this week", "today", "latest", "recent", "current", "now", "this month")
    # Words that describe the request rather than the subject. Dropped before querying the
    # news index, so "research the latest AI news this week" searches for "AI".
    STRIP_WORDS = {
        "research", "latest", "recent", "current", "top", "news", "stories", "story", "this",
        "week", "month", "today", "about", "the", "and", "for", "of", "in", "on", "a", "an",
        "developments", "breakthroughs", "update", "updates", "what", "is", "are", "was",
        "happening", "tell", "me", "please", "search", "internet", "find", "out", "give",
    }

    @property
    def name(self) -> str:
        return "hn_news"

    def is_available(self) -> bool:
        try:
            return requests.get("https://hn.algolia.com/api/v1/search", timeout=5).status_code == 200
        except Exception:
            return False

    @classmethod
    def handles(cls, query: str) -> bool:
        q = (query or "").lower()
        return any(c in q for c in cls.NEWS_CUES) and any(c in q for c in cls.TIME_CUES)

    def search(self, query: str, max_results: int = 5) -> List[WebSearchResult]:
        words = [w for w in (t.strip(".,!?;:") for t in re.findall(r"[A-Za-z0-9+.#-]+", query or ""))
                 if w and w.lower() not in self.STRIP_WORDS]
        subject = " ".join(words)[:60].strip()
        since = int(time.time()) - 7 * 86400
        try:
            r = requests.get(
                # /search ranks by relevance to the subject; /search_by_date returns whatever
                # was posted recently, which for a broad term is mostly unrelated.
                "https://hn.algolia.com/api/v1/search",
                params={
                    "query": subject or "AI",
                    "tags": "story",
                    "numericFilters": f"created_at_i>{since},points>20",
                    "hitsPerPage": max_results,
                },
                headers={"User-Agent": "ISHTI-Agent/2.0"}, timeout=10,
            )
            if r.status_code != 200:
                return []
            out = []
            for hit in r.json().get("hits", []):
                url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
                title = hit.get("title") or ""
                if not title:
                    continue
                published = (hit.get("created_at") or "")[:10]
                out.append(WebSearchResult(
                    title=title,
                    url=url,
                    snippet=f"Published {published}, {hit.get('points', 0)} points, "
                            f"{hit.get('num_comments', 0)} comments. {(hit.get('story_text') or '')[:150]}",
                    publication_date=published,
                    verified=True,
                ))
            return out[:max_results]
        except Exception as e:
            print(f"[NewsSearchProvider] Error on '{query}': {e}")
        return []


class PlaywrightBrowserSearchProvider(WebSearchProvider):
    """
    High-fidelity fallback using Playwright inside the isolated profile D:\\ISHTI\\data\\chrome_profile.
    """
    @property
    def name(self) -> str:
        return "playwright_browser"

    def is_available(self) -> bool:
        chrome_exe = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        return os.path.exists(chrome_exe)

    def search(self, query: str, max_results: int = 5) -> List[WebSearchResult]:
        results: List[WebSearchResult] = []
        profile_dir = r"D:\ISHTI\data\chrome_profile"
        chrome_exe = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                ctx = p.chromium.launch_persistent_context(
                    user_data_dir=profile_dir,
                    executable_path=chrome_exe,
                    headless=True,
                    args=["--no-first-run", "--no-default-browser-check"]
                )
                page = ctx.new_page()
                encoded = urllib.parse.quote(query)
                page.goto(f"https://www.bing.com/search?q={encoded}&setlang=en&cc=US", wait_until="domcontentloaded", timeout=12000)

                algos = page.locator("li.b_algo").all()
                for a in algos[:max_results]:
                    h2 = a.locator("h2 a").first
                    p_elem = a.locator(".b_caption p").first
                    title = h2.inner_text().strip() if h2.count() > 0 else ""
                    raw_href = h2.get_attribute("href") if h2.count() > 0 else ""
                    snippet = p_elem.inner_text().strip() if p_elem.count() > 0 else ""

                    actual_url = raw_href
                    if "u=a1" in raw_href:
                        try:
                            part = raw_href.split("u=a1")[1].split("&")[0]
                            part += "=" * (-len(part) % 4)
                            actual_url = base64.b64decode(part).decode("utf-8", errors="ignore")
                        except Exception:
                            pass

                    if title and actual_url and actual_url.startswith("http"):
                        results.append(WebSearchResult(
                            title=title,
                            url=actual_url,
                            snippet=snippet,
                            verified=True
                        ))
                ctx.close()
        except Exception as e:
            print(f"[PlaywrightSearch] Search failed: {e}")
        return results


class MultiProviderSearch:
    """
    Intelligent routing search engine with multi-provider failover.
    Automatically picks the optimal primary provider based on intent:
    - Code / Repositories / AI Agents -> GitHub API
    - Papers / Scientific Preprints -> arXiv API
    - Definitions / Facts -> Wikipedia API
    - Live Web & Current Information -> Bing Lite + Targeted Domain search
    - Complex Interactive Pages -> Playwright Chrome Sandbox
    """
    def __init__(self):
        self.searxng = SearxngSearchProvider()
        self.bing = BingLiteSearchProvider()
        self.gh_release = GitHubReleaseProvider()
        self.news = NewsSearchProvider()
        self.github = GitHubSearchProvider()
        self.wikipedia = WikipediaSearchProvider()
        self.arxiv = ArxivSearchProvider()
        self.playwright = PlaywrightBrowserSearchProvider()

    def search(self, query: str, max_results: int = 5) -> List[WebSearchResult]:
        q_lower = query.lower()

        # Route 0: "latest version of X" - answer from the project's release feed, which
        # states an exact version, instead of a search page that usually does not.
        rel = self.gh_release.search(query, max_results=1)
        if rel:
            print(f"[MultiProviderSearch] GitHub release feed answered '{query}': {rel[0].title}")
            rest = max(0, max_results - 1)
            return rel + (self.searxng.search(query, rest) or self.bing.search(query, rest))

        # Route 0b: dated news questions - a real news index beats a scraped search page,
        # which returns front pages and (when rate-limited) unrelated junk.
        if NewsSearchProvider.handles(query):
            news = self.news.search(query, max_results=max_results)
            if news:
                print(f"[MultiProviderSearch] News index returned {len(news)} dated stories for '{query}'")
                return news

        # Route A: Developer / Repository / Agent framework queries
        # Narrow: only hand the query to GitHub when it is really about code hosting.
        # "agent"/"project"/"framework" alone used to hijack ordinary questions.
        if any(w in q_lower for w in ["github", "repo", "repository", "open-source library", "pull request"])                 or ("open-source" in q_lower and any(w in q_lower for w in ["project", "library", "tool", "framework"])):
            gh_res = self.github.search(query, max_results=max_results)
            if gh_res:
                print(f"[MultiProviderSearch] GitHub API yielded {len(gh_res)} results for '{query}'")
                return gh_res

        # Route B: Scientific research / paper queries
        if any(w in q_lower for w in ["paper", "arxiv", "preprint", "benchmark evaluation"]):
            arxiv_res = self.arxiv.search(query, max_results=max_results)
            if arxiv_res:
                print(f"[MultiProviderSearch] arXiv API yielded {len(arxiv_res)} results for '{query}'")
                return arxiv_res

        # Route C: no site: rewriting. Bing answers those queries with unrelated pages,
        # and version questions are handled authoritatively by Route 0 above.
        targeted_query = query

        # Step 0: local SearXNG metasearch
        t0 = time.time()
        results = self.searxng.search(targeted_query, max_results=max_results)
        if results:
            print(f"[MultiProviderSearch] SearXNG returned {len(results)} results in {round(time.time()-t0, 2)}s")
            return results

        # Step 1: Try Bing Lite
        t0 = time.time()
        results = self.bing.search(targeted_query, max_results=max_results)
        if results:
            print(f"[MultiProviderSearch] BingLite returned {len(results)} results in {round(time.time()-t0, 2)}s")
            return results

        # Step 2: Try Wikipedia for conceptual queries
        wiki_res = self.wikipedia.search(query, max_results=max_results)
        if wiki_res:
            self._log_failover(query, "bing_lite", "wikipedia_api")
            print(f"[MultiProviderSearch] Wikipedia returned {len(wiki_res)} results for '{query}'")
            return wiki_res

        # Step 3: Try Playwright Chrome Sandbox
        print(f"[MultiProviderSearch] Cascading to Playwright Chrome search for '{query}'...")
        pw_res = self.playwright.search(targeted_query, max_results=max_results)
        if pw_res:
            self._log_failover(query, "bing_lite", "playwright_browser")
            return pw_res

        # Step 4: Final fallback to GitHub if technical
        gh_fallback = self.github.search(query, max_results=max_results)
        if gh_fallback:
            self._log_failover(query, "bing_lite", "github_api")
            return gh_fallback

        return []

    def run_diagnostics(self) -> Dict[str, Dict[str, Any]]:
        """Probes all configured web search providers and returns availability and latency."""
        diag = {}
        for prov in [self.bing, self.gh_release, self.news, self.github, self.wikipedia, self.arxiv, self.playwright]:
            t0 = time.time()
            avail = prov.is_available()
            elapsed = round(time.time() - t0, 3)
            diag[prov.name] = {"available": avail, "latency_s": elapsed}
        return diag

    def _log_failover(self, query: str, failed_provider: str, active_provider: str) -> None:
        try:
            entry = (
                f"\n### Self-Healing Event: Web Search Provider Failover\n"
                f"- **Timestamp**: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n"
                f"- **Query**: `{query}`\n"
                f"- **Category**: `SearchProviderFailover`\n"
                f"- **Attempted Fix**: Automatically cascaded from '{failed_provider}' to '{active_provider}'\n"
                f"- **Outcome**: `RESOLVED`\n"
            )
            with open(REFLECTIONS_PATH, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception:
            pass


_search_manager_instance: Optional[MultiProviderSearch] = None

def get_search_manager() -> MultiProviderSearch:
    global _search_manager_instance
    if _search_manager_instance is None:
        _search_manager_instance = MultiProviderSearch()
    return _search_manager_instance


def web_search(query: str, max_results: int = 5) -> List[WebSearchResult]:
    """Convenience search helper."""
    return get_search_manager().search(query, max_results=max_results)

