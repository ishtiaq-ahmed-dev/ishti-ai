"""Web fetch skill: read the URL in the request and answer from it. When the owner points at a site to find something
("find the spring 2026 timetable on https://imsciences.edu.pk/"), a homepage rarely has it: then the site's own
links that match the goal (timetable, schedule, date sheet ...) and a site-restricted search are read too - PDFs
included - and only pages that really answer are used."""
import io
import re
from urllib.parse import urljoin, urlparse

import requests

from ishti import llm
from ishti.untrusted import fence, guard
from ishti.skills import trace
from ishti.skills.browser.skill import TRIGGER as BROWSER_TRIGGER

URL = re.compile(r"https?://[^\s<>\"')]+|(?<![@\w.])(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|org|net|edu|gov|pk|io|ai|"
                 r"co|info|uk|us|dev|app)(?:\.[a-z]{2})?(?:/[^\s<>\"')]*)?(?![\w.])", re.I)
# the owner's shorthand, and what documents actually say: "7th sem AI" -> "Semester VII", "Artificial Intelligence"
ALIASES = {"ai": "artificial intelligence", "cs": "computer science", "se": "software engineering", "ds": "data science",
           "bba": "business administration", "sem": "semester"}
ORDINALS = {"1st": "i", "2nd": "ii", "3rd": "iii", "4th": "iv", "5th": "v", "6th": "vi", "7th": "vii", "8th": "viii"}
HUBS = re.compile(r"notice\s*board|noticeboard|notices?|announcements?|news|downloads?|updates", re.I)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36"}
FILLER = re.compile(r"\b(?:please|can|could|you|search|find|look|check|try|here|on|in|at|the|this|that|site|website|page|link|"
                    r"for|me|and|give|get|show|official|from|it|there|now|go|to|a|of)\b", re.I)


def match(text):
    # "open/go to <url>" and "in the browser" mean interact -> browser skill; a bare URL means read it
    return bool(URL.search(text)) and not BROWSER_TRIGGER.search(text)


def full_url(found):
    return found if re.match(r"https?://", found, re.I) else "https://" + found


def terms(goal):
    """Words to look for: the goal's own, its abbreviations spelled out, "7th" also as the "VII" documents use."""
    out = set()
    for w in re.findall(r"[\w-]+", goal.lower()):
        out |= {w} | set(ALIASES.get(w, "").split()) | ({ORDINALS[w]} if w in ORDINALS else set())
    return {w for w in out if len(w) > 2 or w in ORDINALS.values()} - {"the", "and", "for"}


def focus(body, goal, limit=6000):
    """The parts of a long page or PDF about the goal (pages scored by goal words, kept in order), so a timetable's
    7th-semester page isn't lost behind 4000 characters of other semesters."""
    if len(body) <= limit:
        return body
    paged = "\f" in body
    parts = body.split("\f") if paged else [body[i:i + 2000] for i in range(0, len(body), 2000)]
    want = terms(goal)
    count = lambda text: sum(len(re.findall(rf"\b{re.escape(w)}\b", text, re.I)) * (3 if w in ORDINALS.values() else 1)
                             for w in want)
    # a PDF page's heading says what the page is ("BS Artificial Intelligence, Semester V, Group A"); words in its
    # body can be anything ("Artificial Intelligence Lab" is a room on the Software Engineering pages)
    score = (lambda part: 10 * count(heading(part)) + count(part)) if paged else count
    best = sorted(range(len(parts)), key=lambda i: -score(parts[i]))
    keep, size = [], 0
    for i in best:
        if size + len(parts[i]) > limit:
            continue
        keep.append(i)
        size += len(parts[i])
    return "\n...\n".join(parts[i] for i in sorted(keep))


def hits(body, goal):
    """How often the goal's words appear in a page (a 5th-semester AI timetable scores high for "7th semester AI")."""
    return sum(len(re.findall(rf"\b{re.escape(w)}\b", body, re.I)) for w in terms(goal) if not w.isdigit())


def heading(page):
    """First meaningful line of a PDF page (timetables, date sheets and reports title each page)."""
    return next((l.strip() for l in page.splitlines() if len(l.strip()) > 12), "")[:160]


def outline(body):
    """'Sections in this document: ...' for a multi-page PDF, so the answer can say what exists."""
    heads = list(dict.fromkeys(h for h in map(heading, body.split("\f")) if h)) if "\f" in body else []
    return ("Sections in this document:\n" + "\n".join(f"- {h}" for h in heads[:40]) + "\n\n") if len(heads) > 1 else ""


def goal_of(text, url):
    g = FILLER.sub(" ", text.replace(url, " "))
    words = [w for w in re.findall(r"[\w-]+", g) if len(w) > 1]
    return " ".join(words)


from ishti import PDFIUM_LOCK as PDF_LOCK  # noqa: E402


def read(url):
    """(title, text) of a page or PDF."""
    r = requests.get(url, headers=UA, timeout=15)
    if url.lower().endswith(".pdf") or "pdf" in r.headers.get("content-type", ""):
        import pypdfium2 as pdfium
        with PDF_LOCK:  # pdfium is not thread-safe: downloads overlap, parsing takes turns (it crashed otherwise)
            doc = pdfium.PdfDocument(io.BytesIO(r.content))
            try:
                return url.rsplit("/", 1)[-1], "\f".join(doc[i].get_textpage().get_text_range() for i in range(min(len(doc), 60)))
            finally:
                doc.close()
    from tools.web.fetch import WebFetcher
    page = WebFetcher(timeout=10).fetch(url)
    return page.get("title") or url, page.get("content") or re.sub(r"<[^>]+>", " ", r.text)


def links(url, goal):
    """Same-site links whose text or address matches the goal words, best first."""
    g = re.sub(r"\btime\s+table\b", "timetable", re.sub(r"\bdate\s+sheet\b", "datesheet", goal.lower()))
    own = {w for w in terms(g) if not w.isdigit() and w not in ORDINALS.values()}  # a bare year matched every 2026 news post
    strong = {"timetable", "schedule", "calendar", "datesheet", "academic", "exam", "exams"}
    doc = own & strong  # the kind of document the owner named weighs most; related kinds are only hints
    extra = strong - own if own & (strong | {"semester", "term"}) else set()
    import datetime
    year = datetime.date.today().year
    html = requests.get(url, headers=UA, timeout=15).text
    host = urlparse(url).netloc.removeprefix("www.")
    scored = {}
    for href, label in re.findall(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', html, re.S | re.I):
        full = urljoin(url, href)
        if urlparse(full).netloc.removeprefix("www.") != host:
            continue
        text = re.sub(r"<[^>]+>", " ", label)
        hay = (text + " " + full).lower().replace("time table", "timetable").replace("-", " ").replace("_", " ")
        score = sum(3 if w in doc else 1 for w in own if w in hay) + sum(1 for w in extra if w in hay)
        if (doc or extra) and HUBS.search(text):
            score += 3  # the noticeboard / news / downloads page is where timetables get posted
        years = [int(y) for y in re.findall(r"\b20\d\d\b", hay)]
        if score and years:
            score += 1 if max(years) >= year else -2  # this year's timetable, not 2024's
        if score > 0:
            scored[full] = max(score, scored.get(full, 0))
    return sorted(scored, key=lambda u: -scored[u])[:6]


def answers(goal, title, body):
    v = llm.ask(f"Does this page contain {goal}? Page: {title}\n{fence(focus(body, goal, 2500), title)}\nReply yes or no.", system=None,
                temperature=0, max_tokens=3).lower()
    return v.startswith("yes")


def run(text, session):
    m = URL.search(text)
    if not m:
        return None
    found = m.group(0).rstrip(".,;")
    goal, url = goal_of(text, found), full_url(found)
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
        import concurrent.futures as cf
        with cf.ThreadPoolExecutor(6) as pool:  # D2: network waits overlap; the checks below stay in order
            site_links = pool.submit(links, url, goal)
            searched = pool.submit(lambda: [h.url for h in SearxngSearchProvider().search(f"site:{host} {goal}", 5)])
            cands = site_links.result() + searched.result()
            # one level deeper: the best pages' own matching links (a noticeboard / calendar page links the PDF)
            hubs = cands[:2]
            deeper = {h: pool.submit(links, h, goal) for h in hubs}
            for hub in hubs:
                try:
                    more = [u for u in deeper[hub].result() if u not in cands][:3]
                except requests.RequestException:
                    continue
                cands[cands.index(hub) + 1:cands.index(hub) + 1] = more
            order = list(dict.fromkeys(cands))[:8]
            fetched = {u: pool.submit(read, u) for u in order}  # all candidate pages download at once
            pages, checked = [], []
            for u in order:  # ...but are judged in the same order as before, so the result is the same
                try:
                    t, b = fetched[u].result()
                except Exception:
                    continue
                checked.append((u, t, b))
                if len(b.strip()) > 80 and answers(goal, t, b):
                    pages.append((u, t, b))
                if len(pages) >= 2:
                    break
        if not pages and checked:  # nothing is exactly it: answer from the closest document, saying what is there
            best = max(checked, key=lambda c: hits(c[2], goal))
            if hits(best[2], goal) >= 6:
                pages = [best]
        trace(session, "site", f"{len(pages)} page(s) on the site answer it", status="done" if pages else "error")
        if not pages:
            near = "\n".join(f"- [{t[:70] or u}]({u})" for u, t, _ in checked[:4])
            return {"reply": f"I looked through {host} (its pages, their PDFs and a site search) and couldn't find {goal} - "
                             "it doesn't seem to be published there (or it's behind the student login). Closest pages I checked:\n"
                             + near, "sources": [], "no_escalate": True}
    context = "\n\n".join(f"[{i}] {t} ({u})\n" + guard(outline(b) + focus(b, goal or text), u, session)
                        for i, (u, t, b) in enumerate(pages, 1))
    answer = llm.ask(f"Owner's request: {text}\n\n{context}\n\nAnswer from these pages only, citing [n]. Use only the section "
                     "whose heading matches what was asked (programme, semester, group). If no heading matches, don't "
                     "answer from a different one: say plainly it isn't published and list the closest sections that are.",
                     temperature=0.2)
    return {"reply": answer, "sources": [{"id": i, "title": t or u, "url": u, "domain": urlparse(u).netloc, "snippet": b[:200],
                                          "verified": True} for i, (u, t, b) in enumerate(pages, 1)]}


if __name__ == "__main__":
    assert goal_of("find the spring 2026 timetable on https://x.edu.pk/", "https://x.edu.pk/") == "spring 2026 timetable"
    assert goal_of("https://x.edu.pk/ try here", "https://x.edu.pk/") == ""
    assert match("summarize https://example.com") and not match("open https://example.com in the browser")
    assert match("find the 7th semester AI timetable on imsciences.edu.pk") and not match("email me at a@b.com")
    assert not match("open notes.pdf") and URL.search("see www.x.edu.pk/a/b now").group(0) == "www.x.edu.pk/a/b"
    assert {"artificial", "intelligence", "vii", "semester", "timetable"} <= terms("7th semester AI timetable")
    pages = "\f".join([f"BS Software Engineering Semester VII Group {g}" + " x" * 900 for g in "AB"]
                       + ["BS Artificial Intelligence Semester V Group A" + " y" * 900])
    assert focus(pages, "7th semester AI timetable", 2000).startswith("BS Artificial Intelligence") is False
    assert "Artificial Intelligence" in focus(pages, "AI timetable", 2000)
    room = " Artificial Intelligence Lab" * 20  # a room name on other programmes' pages must not pull them in
    pages = "\f".join([f"BS Software Engineering Semester V Group A\n" + room + " x" * 800,
                       f"BS Artificial Intelligence Semester V Group A\n" + " y" * 900])
    assert focus(pages, "5th semester AI group A timetable", 2000).startswith("BS Artificial Intelligence Semester V")
    assert "- BS Software Engineering Semester V Group A" in outline(pages)
    print("[self-check] web_fetch OK - goal extraction, routing")
