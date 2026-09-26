"""Web fetch skill: read the URL in the request and answer from it. When the owner points at a site to find something
("find the spring 2026 timetable on https://imsciences.edu.pk/"), a homepage rarely has it: then the site's own
links that match the goal (timetable, schedule, date sheet ...) and a site-restricted search are read too - PDFs
included - and only pages that really answer are used."""
import io
import re
from urllib.parse import urljoin, urlparse

import requests

from ishti import llm
from ishti.skills import trace
from ishti.skills.browser.skill import TRIGGER as BROWSER_TRIGGER

URL = re.compile(r"https?://[^\s<>\"')]+")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36"}
FILLER = re.compile(r"\b(?:please|can|could|you|search|find|look|check|try|here|on|in|at|the|this|that|site|website|page|link|"
                    r"for|me|and|give|get|show|official|from|it|there|now|go|to|a|of)\b", re.I)


def match(text):
    # "open/go to <url>" and "in the browser" mean interact -> browser skill; a bare URL means read it
    return bool(URL.search(text)) and not BROWSER_TRIGGER.search(text)


def goal_of(text, url):
    g = FILLER.sub(" ", text.replace(url, " "))
    words = [w for w in re.findall(r"[\w-]+", g) if len(w) > 1]
    return " ".join(words)


def read(url):
    """(title, text) of a page or PDF."""
    r = requests.get(url, headers=UA, timeout=15)
    if url.lower().endswith(".pdf") or "pdf" in r.headers.get("content-type", ""):
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(io.BytesIO(r.content))
        return url.rsplit("/", 1)[-1], "\n".join(doc[i].get_textpage().get_text_range() for i in range(min(len(doc), 15)))
    from tools.web.fetch import WebFetcher
    page = WebFetcher(timeout=10).fetch(url)
    return page.get("title") or url, page.get("content") or re.sub(r"<[^>]+>", " ", r.text)


def links(url, goal):
    """Same-site links whose text or address matches the goal words, best first."""
    g = re.sub(r"\btime\s+table\b", "timetable", re.sub(r"\bdate\s+sheet\b", "datesheet", goal.lower()))
    words = {w for w in g.split() if len(w) > 2 and not w.isdigit()}  # a bare year matched every 2026 news post
    strong = {"timetable", "schedule", "calendar", "datesheet", "academic", "exam", "exams"}
    if words & (strong | {"semester", "term"}):
        words |= strong
    html = requests.get(url, headers=UA, timeout=15).text
    host = urlparse(url).netloc.removeprefix("www.")
    scored = {}
    for href, label in re.findall(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', html, re.S | re.I):
        full = urljoin(url, href)
        if urlparse(full).netloc.removeprefix("www.") != host:
            continue
        hay = (re.sub(r"<[^>]+>", " ", label) + " " + full).lower().replace("time table", "timetable").replace("-", " ")
        score = sum(2 if w in strong else 1 for w in words if w in hay)
        if score:
            scored[full] = max(score, scored.get(full, 0))
    return sorted(scored, key=lambda u: -scored[u])[:6]


def answers(goal, title, body):
    v = llm.ask(f"Does this page contain {goal}? Page: {title}\n{body[:1500]}\nReply yes or no.", system=None,
                temperature=0, max_tokens=3).lower()
    return v.startswith("yes")


def run(text, session):
    url = URL.search(text).group(0).rstrip(".,;")
    goal = goal_of(text, url)
    try:
        title, body = read(url)
    except Exception as exc:
        return {"reply": f"Couldn't read {url}: {type(exc).__name__}", "sources": []}
    pages = [(url, title, body)]
    wants_something = len(goal.split()) >= 2 and not re.fullmatch(r"(?:summari[sz]e|summary|what\s+is\s+this|read)\b.*", goal, re.I)
    if wants_something and not answers(goal, title, body):
        from tools.web.search import SearxngSearchProvider
        host = urlparse(url).netloc.removeprefix("www.")
        trace(session, "site", f"the page doesn't have it - looking through {host} for: {goal}", status="start")
        cands = links(url, goal) + [h.url for h in SearxngSearchProvider().search(f"site:{host} {goal}", 5)]
        if cands:  # one level deeper: the best page's own matching links (a calendar page usually links the PDF)
            try:
                cands[1:1] = [u for u in links(cands[0], goal) if u.lower().endswith(".pdf")][:3]
            except requests.RequestException:
                pass
        pages, checked = [], []
        for u in list(dict.fromkeys(cands))[:8]:
            try:
                t, b = read(u)
            except Exception:
                continue
            checked.append((u, t))
            if len(b.strip()) > 80 and answers(goal, t, b):
                pages.append((u, t, b))
            if len(pages) >= 2:
                break
        trace(session, "site", f"{len(pages)} page(s) on the site answer it", status="done" if pages else "error")
        if not pages:
            near = "\n".join(f"- [{t[:70] or u}]({u})" for u, t in checked[:4])
            return {"reply": f"I looked through {host} (its pages, their PDFs and a site search) and couldn't find {goal} - "
                             "it doesn't seem to be published there (or it's behind the student login). Closest pages I checked:\n"
                             + near, "sources": [], "no_escalate": True}
    context = "\n\n".join(f"[{i}] {t} ({u})\n{b[:4000]}" for i, (u, t, b) in enumerate(pages, 1))
    answer = llm.ask(f"Owner's request: {text}\n\n{context}\n\nAnswer from these pages only, citing [n].", temperature=0.2)
    return {"reply": answer, "sources": [{"id": i, "title": t or u, "url": u, "domain": urlparse(u).netloc, "snippet": b[:200],
                                          "verified": True} for i, (u, t, b) in enumerate(pages, 1)]}


if __name__ == "__main__":
    assert goal_of("find the spring 2026 timetable on https://x.edu.pk/", "https://x.edu.pk/") == "spring 2026 timetable"
    assert goal_of("https://x.edu.pk/ try here", "https://x.edu.pk/") == ""
    assert match("summarize https://example.com") and not match("open https://example.com in the browser")
    print("[self-check] web_fetch OK - goal extraction, routing")
