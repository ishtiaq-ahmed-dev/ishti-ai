"""
ISHTI Resilient Web Fetcher
Retrieves online page content using a cascade of methods:
1. Fast direct HTTP fetch + ContentExtractor
2. Reader Mode fallback (r.jina.ai open reader)
3. Isolated Chrome Playwright session (D:\\ISHTI\\data\\chrome_profile)
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
            print(f"[WebFetcher] Direct HTTP failed for {url}: {e}. Trying reader mode...")

        # Strategy 2: Reader Mode (Jina Reader fallback)
        try:
            reader_url = f"https://r.jina.ai/{url}"
            res = requests.get(reader_url, timeout=self.timeout + 2)
            if res.status_code == 200 and len(res.text) > 100:
                text = res.text[:max_chars]
                # Extract title from first line if present
                title = url
                lines = text.split("\n")
                if lines and lines[0].startswith("Title:"):
                    title = lines[0].replace("Title:", "").strip()
                return {
                    "url": url,
                    "title": title,
                    "content": text,
                    "date": "",
                    "status": "success",
                    "method": "reader_mode",
                }
        except Exception as e:
            print(f"[WebFetcher] Reader mode failed for {url}: {e}. Trying Playwright browser...")

        # Strategy 3: Playwright Isolated Chrome Browser
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
