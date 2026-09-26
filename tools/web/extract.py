"""
ISHTI Content Extractor
Parses raw HTML into clean, structured readable text while stripping boilerplate,
scripts, advertisements, and navigation elements.
"""

import re
from typing import Dict, Any, Optional
from bs4 import BeautifulSoup


class ContentExtractor:
    @staticmethod
    def extract_from_html(html: str, max_chars: int = 5000) -> Dict[str, Any]:
        """
        Parses HTML and extracts title, publication date heuristics, and main body text.
        """
        if not html:
            return {"title": "", "content": "", "date": "", "length": 0}

        soup = BeautifulSoup(html, "html.parser")

        # 1. Extract title
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        elif soup.select_one("h1"):
            title = soup.select_one("h1").get_text().strip()

        # 2. Extract publication date if available
        date = ""
        meta_date = soup.find("meta", {"property": ["article:published_time", "og:pubdate", "og:publish_date"]})
        if meta_date and meta_date.get("content"):
            date = meta_date["content"].strip()
        else:
            time_el = soup.find("time")
            if time_el:
                date = time_el.get("datetime") or time_el.get_text().strip()

        # 3. Strip clutter
        for tag in [
            "script", "style", "nav", "footer", "header", "aside", "form",
            "noscript", "svg", "iframe", "button", "dialog"
        ]:
            for el in soup.find_all(tag):
                el.decompose()

        # Target main or article if present
        container = soup.find("article") or soup.find("main") or soup.find("body") or soup
        text = container.get_text(separator="\n", strip=True)

        # Normalize redundant newlines
        cleaned = re.sub(r"\n{3,}", "\n\n", text)
        cleaned = re.sub(r"[ \t]+", " ", cleaned).strip()

        return {
            "title": title,
            "content": cleaned[:max_chars],
            "date": date,
            "length": len(cleaned),
        }
