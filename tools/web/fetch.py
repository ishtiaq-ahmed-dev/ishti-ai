"""
ISHTI Resilient Web Fetcher
Retrieves online page content using a cascade of methods:
1. Fast direct HTTP fetch + ContentExtractor
2. Isolated Chrome Playwright session (local; the old r.jina.ai cloud reader step was removed)
"""

import os
import sys
import time
import requests
from typing import Dict, Any, Optional

ROOT_DIR = r"D:\ISHTI"
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from tools.web.extract import ContentExtractor

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class WebFetcher:
    def __init__(self, timeout: int = 10):
        self.timeout = timeout

    def fetch(self, url: str, max_chars: int = 4000) -> Dict[str, Any]:
        """
        Fetches web page content with automatic fallback cascade.
        """
        # Strategy 1: Direct HTTP fetch
        try:
            res = requests.get(url, headers=DEFAULT_HEADERS, timeout=self.timeout)
            if res.status_code == 200 and len(res.text) > 150:
                parsed = ContentExtractor.extract_from_html(res.text, max_chars=max_chars)
                if parsed.get("content") and len(parsed["content"]) > 100:
                    return {
                        "url": url,
                        "title": parsed.get("title") or url,
                        "content": parsed.get("content"),
                        "date": parsed.get("date", ""),
                        "status": "success",
                        "method": "direct_http",
                    }
        except Exception as e:
            print(f"[WebFetcher] Direct HTTP failed for {url}: {e}. Trying the local browser...")

        # (A Jina r.jina.ai "reader mode" step used to sit here: a third-party cloud service that saw
        # every URL ISHTI read, and took 10-12 s. Removed - local-first; the local browser covers JS pages.)

        # Strategy 2: Playwright Isolated Chrome Browser
        try:
            from browser.browser_agent import BrowserAgent
            agent = BrowserAgent(headless=True)
            res = agent.fetch_page_text(url, max_chars=max_chars)
            if res.get("text"):
                return {
                    "url": url,
                    "title": res.get("title") or url,
                    "content": res.get("text"),
                    "date": "",
                    "status": "success",
                    "method": "playwright_browser",
                }
        except Exception as e:
            print(f"[WebFetcher] Playwright browser failed for {url}: {e}")

        return {
            "url": url,
            "title": url,
            "content": "",
            "date": "",
            "status": "failed",
            "error": "All retrieval strategies exhausted.",
        }


if __name__ == "__main__":
    fetcher = WebFetcher()
    data = fetcher.fetch("https://www.python.org/downloads/")
    print("Fetch status:", data["status"], "via", data.get("method"))
    print("Title:", data.get("title"))
    print("Excerpt:", data.get("content")[:200])
