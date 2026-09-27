"""Flights and hotels via Google Flights / Google Hotels in the owner's main Chrome profile. Bot-protected travel
sites refuse scrapers, but Google's own travel pages open fine in a real browser and take plain-language queries
("flights from Peshawar to Dubai on 5 October"). ISHTI opens the query, waits for results, reads the page and
lists the best options. Read-only: it never books or pays."""
import re
import time
import urllib.parse

from ishti import llm
from ishti.skills import ground_dates, trace
from ishti.skills.browser import skill as browser

I = re.I
FLIGHTS = re.compile(r"\b(?:flights?|fly|airfare|plane\s+tickets?|air\s+tickets?)\b.*\b(?:to|from)\b", I)
HOTELS = re.compile(r"\b(?:hotels?|places?\s+to\s+stay|accommodation|stay|rooms?|guest\s*house|airbnb)\b.*\b(?:in|at|near)\s+\w", I)
NOT = re.compile(r"\b(?:book|pay|buy|reserve)\b", I)  # read-only: bookings are for the owner (or the browser skill, with a yes)


def match(text):
    return (bool(FLIGHTS.search(text)) or bool(HOTELS.search(text)) and re.search(r"\b(?:find|search|show|cheap|best|price|"
                                                                                   r"look\s+for|hotels?\s+in)\b", text, I)) \
        and not NOT.search(text)


def query_url(text):
    """(kind, Google travel URL) for the request, dates grounded to real ones ("next friday" -> 2026-10-02)."""
    q = ground_dates(re.sub(r"^(?:please\s+)?(?:can\s+you\s+)?(?:find|search(?:\s+for)?|show(?:\s+me)?|look\s+for|check)\s+(?:me\s+)?",
                            "", text.strip(), flags=I)).strip(" ?.")
    if FLIGHTS.search(text):
        return "flights", "https://www.google.com/travel/flights?q=" + urllib.parse.quote(q if re.search(r"\bflights?\b", q, I) else "flights " + q)
    return "hotels", "https://www.google.com/travel/search?q=" + urllib.parse.quote(q)


READY = {"flights": re.compile(r"(?:Rs|PKR|\$|USD|AED|€|£)\s?[\d,]{3,}|\b\d{1,2}\s*hr\b", I),
         "hotels": re.compile(r"(?:Rs|PKR|\$|USD|AED|€|£)\s?[\d,]{2,}.*night|\bper\s+night\b|\b\d\.\d\s*\(\d", I)}


def run(text, session):
    kind, url = query_url(text)
    with browser.LOCK:
        try:
            browser.use_server(browser.profile_for("travel"), session)
        except RuntimeError as exc:
            return {"reply": str(exc), "sources": []}
        trace(session, "google", f"searching Google {kind.title()}", status="start", url=url)
        page, t0 = browser._call("browser_navigate", {"url": url}), time.time()
        while not READY[kind].search(page) and time.time() - t0 < 25:  # results load after the page itself
            time.sleep(2.5)
            page = browser._call("browser_snapshot", {})
        trace(session, "google", "results loaded" if READY[kind].search(page) else "no prices appeared", status="done")
    text_only = re.sub(r"\s*\[(?:ref|cursor)=[^\]]+\]", "", page)
    lines = [l.strip(" -") for l in text_only.splitlines() if re.search(r"\d", l) and len(l.strip()) > 8][:260]
    if not READY[kind].search(page):
        return {"reply": f"Google {kind.title()} didn't show prices for that - check the dates and places. It's open in your "
                         f"Chrome: {url}", "sources": [{"id": 1, "title": f"Google {kind.title()}", "url": url}]}
    answer = llm.ask(f"The owner asked: {text}\nThis is the Google {kind.title()} results page, as text:\n"
                     + "\n".join(lines)[:9000]
                     + f"\n\nList the best 5 {kind} options (cheapest and best-rated first) with {'airline, times, stops, duration' if kind == 'flights' else 'name, rating, area'}"
                       " and price exactly as shown. Only what the page says. Then one line on the cheapest.",
                     temperature=0, max_tokens=500)
    return {"reply": answer + f"\n\n(Live from Google {kind.title()}, open in your Chrome.)",
            "sources": [{"id": 1, "title": f"Google {kind.title()}", "url": url, "domain": "google.com"}]}


if __name__ == "__main__":
    for t in ["find flights from Peshawar to Dubai on 5 October", "cheap flights to Istanbul from Lahore next month",
              "search hotels in Dubai from 5 to 8 October", "best hotels in Istanbul near Taksim"]:
        assert match(t), t
    for t in ["book a flight to Dubai", "what's the weather in Dubai", "i stayed in a hotel yesterday", "remind me about my flight"]:
        assert not match(t), t
    kind, url = query_url("find flights from Peshawar to Dubai on 5 October")
    assert kind == "flights" and url.startswith("https://www.google.com/travel/flights?q=flights%20from%20Peshawar%20to%20Dubai"), url
    assert query_url("search hotels in Dubai from 5 to 8 October")[1].startswith("https://www.google.com/travel/search?q=hotels%20in%20Dubai")
    assert READY["flights"].search("PIA 10:05 AM – 1:15 PM 4 hr 10 min Nonstop PKR 64,820")
    print("[self-check] travel OK - routing (read-only), Google Flights/Hotels query URLs, results detection")
