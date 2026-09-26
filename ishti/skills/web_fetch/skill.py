"""Web fetch skill: read the URL in the request, answer from the page text only."""
import re

from ishti import llm
from ishti.skills.browser.skill import TRIGGER as BROWSER_TRIGGER

URL = re.compile(r"https?://[^\s<>\"')]+")


def match(text):
    # "open/go to <url>" and "in the browser" mean interact -> browser skill; a bare URL means read it
    return bool(URL.search(text)) and not BROWSER_TRIGGER.search(text)


def run(text, session):
    from tools.web.fetch import WebFetcher
    url = URL.search(text).group(0).rstrip(".,;")
    page = WebFetcher(timeout=10).fetch(url)
    if page.get("status") != "success" or not page.get("content"):
        return {"reply": f"Couldn't read {url}: {page.get('error') or 'empty page'}", "sources": []}
    answer = llm.ask(f"Owner's request: {text}\n\nPage [1]: {page.get('title', '')} ({url})\n"
                     f"{page['content'][:6000]}\n\nAnswer from this page only.", temperature=0.2)
    domain = url.split("//")[-1].split("/")[0]
    return {"reply": answer, "sources": [{"id": 1, "title": page.get("title") or url, "url": url, "domain": domain,
                                          "snippet": page["content"][:200], "verified": True}]}
