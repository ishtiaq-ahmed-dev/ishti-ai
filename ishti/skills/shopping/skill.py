"""Shopping: the cheapest price for a product at official and verified stores, as a comparison table with links.

Prices come from the store pages themselves: schema.org Product data (official stores publish it) first,
else the most repeated price in the page text. Price-guide sites are shown only as a reference."""
import concurrent.futures as cf
import json
import os
import re
import threading
import time
from collections import Counter
from urllib.parse import urlparse

import requests

from ishti import ROOT
from ishti.skills import trace

TRIGGER = re.compile(r"\b(?:cheapest|lowest\s+price|best\s+(?:price|deal|reviewed)|where\s+(?:can\s+i|to|should\s+i)\s+buy|"
                     r"(?:want|going|planning|looking)\s+to\s+buy|buy\s+(?:a|an|the|me|this|that|it)\b|shop(?:ping)?\s+for|"
                     r"compare\s+(?:the\s+)?prices?|price\s+comparison|official\s+stores?|verified\s+stores?|"
                     r"best\s+reviewed|under\s+\d|\bprice\b.{0,20}\b(?:under|below|max|at\s+most|limit)\b)", re.I)
NOT_SHOPPING = re.compile(r"\b(?:file|folder|pdf|documents?|downloads|desktop|my\s+(?:pc|computer|laptop))\b", re.I)

# ponytail: hand-kept allow-lists. Anything not listed (and not the brand's own site) is left out of the table,
# which drops shady sellers but also unknown legit ones; add stores here as the owner trusts them.
STORES = {"daraz.pk": "Daraz", "priceoye.pk": "PriceOye", "telemart.pk": "Telemart", "ishopping.pk": "iShopping",
          "homeshopping.pk": "HomeShopping", "mega.pk": "Mega.pk", "shophive.com": "Shophive", "czone.com.pk": "Czone",
          "galaxy.pk": "Galaxy.pk", "paklap.pk": "Paklap", "naheed.pk": "Naheed", "hummart.com": "HumMart",
          "amazon.com": "Amazon", "amazon.co.uk": "Amazon UK", "amazon.ae": "Amazon UAE", "ebay.com": "eBay",
          "bestbuy.com": "Best Buy", "walmart.com": "Walmart", "newegg.com": "Newegg", "bhphotovideo.com": "B&H",
          "noon.com": "Noon", "flipkart.com": "Flipkart", "aliexpress.com": "AliExpress"}
GUIDES = {"whatmobile.com.pk": "WhatMobile", "pricedekho.com.pk": "PriceDekho", "hamariweb.com": "HamariWeb",
          "propakistani.pk": "ProPakistani", "qeemat.com.pk": "Qeemat", "mobilemall.pk": "MobileMall",
          "gsmarena.com": "GSMArena", "pricebaba.com": "PriceBaba", "91mobiles.com": "91mobiles"}
CURRENCY = {"pakistan": ("PKR", r"(?:Rs\.?|PKR)\s?"), "india": ("INR", r"(?:₹|Rs\.?|INR)\s?"),
            "united kingdom": ("GBP", r"£\s?"), "uae": ("AED", r"AED\s?")}
BRAND_OF = {"iphone": "apple", "ipad": "apple", "macbook": "apple", "airpods": "apple", "galaxy": "samsung",
            "pixel": "google", "redmi": "mi", "poco": "mi", "xiaomi": "mi", "playstation": "sony", "xbox": "microsoft",
            "surface": "microsoft", "geforce": "nvidia", "rtx": "nvidia", "thinkpad": "lenovo"}
VARIANTS = {"ultra", "plus", "pro", "max", "lite", "fe", "mini", "edge", "+"}
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/140.0 Safari/537.36", "Accept-Language": "en-US,en;q=0.9"}


# Price-drop tracking: "tell me when the S25 gets cheaper", "watch the price of airpods pro 2".
WATCH = re.compile(r"\b(?:tell|notify|alert)\s+me\s+when\s+.+\s+(?:gets?\s+cheaper|drops?|price\s+(?:drops?|falls?|goes\s+down))\b|"
                   r"\b(?:watch|track|monitor)\s+(?:the\s+)?price\s+(?:of|for)\b|\bprice\s+(?:drop\s+)?alert\b", re.I)
from ishti.test_mode import scratch_path  # noqa: E402
WATCHES = scratch_path(os.path.join(ROOT, "data", "price_watch.json"))
CHECK_EVERY_S = 6 * 3600


def match(text):
    return (bool(TRIGGER.search(text)) or bool(WATCH.search(text))) and not NOT_SHOPPING.search(text)


def _watches():
    try:
        with open(WATCHES, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def watch(text, session):
    from ishti.facts import get_facts
    product = product_of(re.sub(r"\b(?:tell|notify|alert)\s+me\s+when|gets?\s+cheaper|price\s+(?:drops?|falls?|alert)|watch|track|monitor|the\s+price\s+(?:of|for)", " ", text, flags=re.I))
    country = get_facts().get("owner").get("country", "")
    rows = [r for r in compare(product, country, lambda n, l: trace(session, n, l)) if r["kind"] != "guide"]
    if not rows:
        return f"I couldn't find {product} in verified stores to start tracking."
    best = rows[0]
    items = [w for w in _watches() if w["product"].lower() != product.lower()]
    items.append({"product": product, "country": country, "price": best["price"], "currency": best["currency"],
                  "store": best["store"], "url": best["url"], "since": time.strftime("%Y-%m-%d")})
    with open(WATCHES, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=1)
    start_price_watch()
    return (f"Tracking **{product}**: cheapest now {best['currency']} {best['price']:,.0f} at {best['store']}. "
            f"I'll tell you when a verified store goes lower (checked every {CHECK_EVERY_S // 3600} h).")


def check_price_watches(announce):
    items, changed = _watches(), False
    for w in items:
        rows = [r for r in compare(w["product"], w["country"]) if r["kind"] != "guide" and r["currency"] == w["currency"]]
        if rows and rows[0]["price"] < w["price"]:
            new = rows[0]
            announce(f"💸 {w['product']} dropped to {new['currency']} {new['price']:,.0f} at {new['store']} "
                     f"(was {w['price']:,.0f}): {new['url']}")
            w.update(price=new["price"], store=new["store"], url=new["url"])
            changed = True
    if changed:
        with open(WATCHES, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=1)


_price_thread = {"on": False}


def start_price_watch():
    """Background re-check (only inside the running server, where announcements go somewhere)."""
    from ishti.dispatcher import HOOKS
    if _price_thread["on"] or not HOOKS.get("emit"):
        return
    _price_thread["on"] = True

    def loop():
        while True:
            time.sleep(CHECK_EVERY_S)
            try:  # one bad product page must not end the price watch for the rest of the session
                if _watches():  # the bell keeps it when the window is closed (the socket message alone was lost)
                    from ishti import notifications
                    check_price_watches(lambda msg: (HOOKS["emit"]("assistant_message", {"reply": msg}),
                                                     notifications.add("alert", msg)))
            except Exception as exc:
                print(f"[price-watch] pass skipped: {type(exc).__name__}")
    threading.Thread(target=loop, daemon=True, name="price-watch").start()


def product_of(text):
    """The product (or product category) the owner wants, in their own words. A small model copied the old prompt's
    example ("PlayStation 5 Slim") for "best laptop under 1000 dollars", so an answer sharing no word with the request
    is rejected, and no style prompt rides along on a 24-token extraction."""
    from ishti import llm
    name = llm.ask("From the owner's message, write only the product or product category they want to buy - brand, "
                   "model, variant and budget words exactly as they wrote them, nothing else.\n\nMessage: " + text,
                   system=None, max_tokens=24, temperature=0).strip(" .\"'")
    said = {w for w in re.findall(r"[a-z0-9]{3,}", text.lower())}
    if not (0 < len(name) < 80) or not said & set(re.findall(r"[a-z0-9]{3,}", name.lower())):
        return re.sub(r"^\s*(?:what(?:'s|\s+is)\s+the\s+|find\s+me\s+(?:the\s+)?|i\s+want\s+to\s+buy\s+(?:an?\s+)?)"
                      r"|\s*(?:right\s+now|today|please)?\s*[?.!]*\s*$", "", text, flags=re.I).strip() or text
    return name


def site_of(url):
    return urlparse(url).netloc.lower().removeprefix("www.")


def kind_of(domain, brand):
    """('official' | 'store' | 'guide' | None, display name)."""
    if brand and domain.split(".")[0] == brand:  # exact: a look-alike such as samsung-deals.pk is not official
        return "official", f"{brand.title()} (official)"
    for table, kind in ((STORES, "store"), (GUIDES, "guide")):
        for d, name in table.items():
            if domain == d or domain.endswith("." + d):
                return kind, name
    return None, domain


def relevant(product, title, url):
    """The page is about this exact product: every model number is there, and no bigger variant
    (an 'S25' search must not price an 'S25 Ultra' page)."""
    words = set(re.findall(r"[a-z0-9+]+", product.lower()))
    hay = (title + " " + url).lower()
    squashed = re.sub(r"[^a-z0-9+]", "", hay)
    if not all(w in squashed for w in words if re.search(r"\d", w)):
        return False
    return not ((set(re.findall(r"[a-z+]+", title.lower())) & VARIANTS) - words)


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def price_of(html, cur_rx):
    """(price, currency) from schema.org offers, else the most repeated local-currency price in the text."""
    for block in re.findall(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", html, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        for d in _walk(data):
            p = d.get("price") or d.get("lowPrice")
            if p and d.get("priceCurrency"):
                try:
                    value = float(str(p).replace(",", ""))
                except ValueError:
                    continue
                if value > 0:
                    return value, d["priceCurrency"]
    if cur_rx:
        text = re.sub(r"<[^>]+>", " ", html)
        found = [float(v.replace(",", "")) for v in re.findall(cur_rx[1] + r"([\d,]{4,}(?:\.\d+)?)", text)[:12]]
        if found:
            return Counter(found).most_common(1)[0][0], cur_rx[0]
    return None


def fetch(hit, product, brand, cur_rx):
    kind, name = kind_of(site_of(hit.url), brand)
    if not kind:
        return None
    try:
        html = requests.get(hit.url, headers=HEADERS, timeout=6).text
    except requests.RequestException:
        return None
    title = (re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I) or [None, hit.title])[1]
    if not relevant(product, title or hit.title, hit.url):
        return None
    found = price_of(html, cur_rx)
    return found and {"store": name, "kind": kind, "price": found[0], "currency": found[1], "url": hit.url}


def compare(product, country, trace_fn=lambda *a: None):
    # FIX-11: the direct SearxngSearchProvider call returned 0 results whenever the local
    # SearXNG container is down, so shopping silently found "0 store pages" and declined
    # (measured: searxng direct 0, full manager 5). The manager's cascade (searxng -> bing
    # -> wikipedia -> ...) is exactly what every other consumer uses.
    from tools.web.search import get_search_manager
    first = product.split()[0].lower() if product.split() else ""
    brand = BRAND_OF.get(first, first)
    cur_rx = CURRENCY.get(country.lower()) or (("USD", r"(?:US)?\$\s?") if not country else None)
    where = f" in {country}" if country else ""
    queries = [f"{product} price{where}", f"buy {product}{where} online", f"{product} {brand} official store{where}"]
    trace_fn("search", f"search stores: {' · '.join(queries)}")
    with cf.ThreadPoolExecutor(8) as pool:
        _mgr = get_search_manager()
        hits = [h for batch in pool.map(lambda q: _mgr.search(q, 12), queries) for h in batch]
        seen, unique = set(), []
        for h in hits:
            if h.url not in seen:
                seen.add(h.url)
                unique.append(h)
        trace_fn("fetch", f"read {len(unique)} store pages")
        rows = [r for r in pool.map(lambda h: fetch(h, product, brand, cur_rx), unique[:24]) if r]
    if rows:  # ponytail: drops accessories/instalments priced far below the product; a real price under 40% of the median is lost
        mid = sorted(r["price"] for r in rows)[len(rows) // 2]
        rows = [r for r in rows if r["price"] >= 0.4 * mid]
    best = {}
    for r in sorted(rows, key=lambda r: r["price"]):
        best.setdefault(r["store"], r)  # the lowest price per store
    return list(best.values())


def table(product, rows):
    stores = [r for r in rows if r["kind"] != "guide"]
    guides = [r for r in rows if r["kind"] == "guide"]
    if not stores and not guides:
        return None
    lines = [f"**{product}**, cheapest first:", "", "| Store | Price | Type | Link |", "|---|---|---|---|"]
    for i, r in enumerate(stores):
        price = f"{r['currency']} {r['price']:,.0f}"
        lines.append(f"| {'**' + r['store'] + '** (cheapest)' if i == 0 else r['store']} | "
                     f"{'**' + price + '**' if i == 0 else price} | {r['kind'].title()} | [open]({r['url']}) |")
    if not stores:
        lines = [f"No official or verified store shows **{product}** with a price right now."]
    if guides:
        lines += ["", "Price guides: " + ", ".join(f"[{g['store']}]({g['url']}) {g['currency']} {g['price']:,.0f}" for g in guides[:4])]
    return "\n".join(lines)


def run(text, session):
    from ishti.facts import get_facts
    if WATCH.search(text):
        return {"reply": watch(text, session), "sources": []}
    product = product_of(text)
    country = get_facts().get("owner").get("country", "")
    trace(session, "product", f"looking for: {product}" + (f" ({country})" if country else ""))
    rows = compare(product, country, lambda name, label: trace(session, name, label))
    reply = table(product, rows)
    if not reply:
        return None  # nothing priced: the dispatcher escalates to web search
    trace(session, "compare", f"{sum(r['kind'] != 'guide' for r in rows)} stores priced")
    return {"reply": reply, "sources": [r["url"] for r in rows]}


if __name__ == "__main__":
    assert match("find me the cheapest samsung galaxy s25") and match("i want to buy an iphone 16")
    assert not match("find the cheapest pdf in my downloads") and not match("what is a transformer")
    assert relevant("Samsung Galaxy S25", "Samsung Galaxy S25 Price in Pakistan", "https://priceoye.pk/x")
    assert not relevant("Samsung Galaxy S25", "Samsung Galaxy S25 Ultra Price", "https://priceoye.pk/x")
    assert not relevant("Samsung Galaxy S25", "Samsung Galaxy S24 Price", "https://priceoye.pk/x")
    assert kind_of("samsung.com", "samsung")[0] == "official" and kind_of("priceoye.pk", "samsung")[0] == "store"
    assert kind_of("shadyphones.biz", "samsung")[0] is None and kind_of("samsung-deals.pk", "samsung")[0] is None
    assert price_of('<script type="application/ld+json">{"offers":{"price":"249,999","priceCurrency":"PKR"}}</script>', None) == (249999.0, "PKR")
    assert price_of("<b>Rs 269,999</b> Rs. 269,999 EMI Rs 10,000", CURRENCY["pakistan"]) == (269999.0, "PKR")
    rows = [{"store": "B", "kind": "store", "price": 2.0, "currency": "PKR", "url": "https://b"},
            {"store": "A", "kind": "official", "price": 1.0, "currency": "PKR", "url": "https://a"}]
    assert "**A** (cheapest)" in table("X", sorted(rows, key=lambda r: r["price"]))
    assert match("tell me when the samsung s25 gets cheaper") and match("watch the price of airpods pro 2")
    assert WATCH.search("price alert for ps5 slim")
    print("[self-check] shopping OK")
