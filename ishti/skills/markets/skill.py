"""Markets: live stocks (US/global via yfinance, PSX via the exchange), crypto, gold/silver and currency rates,
plus a technical analyzer ("analyze nvidia"). Several assets in one message are answered together."""
import concurrent.futures as cf
import difflib
import json
import re

from ishti.skills import trace
from ishti.skills.markets import data

I = re.I
CUR = {"dollar": "USD", "dollars": "USD", "usd": "USD", "euro": "EUR", "euros": "EUR", "eur": "EUR", "pound": "GBP",
       "pounds": "GBP", "gbp": "GBP", "riyal": "SAR", "riyals": "SAR", "sar": "SAR", "dirham": "AED", "dirhams": "AED",
       "aed": "AED", "yen": "JPY", "jpy": "JPY", "yuan": "CNY", "cny": "CNY", "pkr": "PKR", "inr": "INR",
       "rupee": "PKR", "rupees": "PKR", "cad": "CAD", "aud": "AUD", "chf": "CHF", "try": "TRY", "lira": "TRY"}
_C = "|".join(sorted(CUR, key=len, reverse=True))
FX_PAIR = re.compile(rf"\b({_C})\s*(?:to|/|in|vs\.?|into|against)\s*({_C})\b", I)
FX_RATE = re.compile(rf"\b({_C})\s+(?:rate|price|exchange\s+rate)\b|\b(?:rate|price)\s+of\s+(?:the\s+)?({_C})\b", I)
METALS = {"gold": "XAU", "silver": "XAG"}
COINS = re.compile(r"\b(bitcoin|btc|ethereum|eth|solana|sol|dogecoin|doge|xrp|ripple|bnb|cardano|ada|tether|usdt|"
                   r"litecoin|ltc|toncoin|shiba\s+inu|pepe|tron|trx|polkadot|dot|avalanche|avax|chainlink)\b", I)
STOCKY = re.compile(r"\b(?:stocks?|shares?|ticker|equit(?:y|ies)|psx|kse|nasdaq|nyse|s&p|dow\s+jones|market\s+cap|"
                    r"stock\s+market|share\s+price|index|etf)\b", I)
ANALYZE = re.compile(r"\b(?:analy[sz]e|analysis|analyzer|research|technical|trend|outlook|forecast|predict(?:ion)?|rsi|"
                     r"moving\s+average|macd|should\s+i\s+(?:buy|sell|invest|hold)|worth\s+(?:buying|investing)|bullish|bearish|"
                     r"how\s+much\s+(?:\w+\s+){0,3}(?:should|to|can)\s+(?:i\s+)?(?:buy|invest)|buy\s+or\s+sell|when\s+(?:to|should\s+i)\s+sell|"
                     r"stop[-\s]?loss|take[-\s]?profit|target\s+price|price\s+target|entry\s+(?:price|point)|recommend)\b", I)
TRACK = re.compile(r"\b(?:how\s+(?:did|are)\s+(?:your|the|my)\s+(?:market\s+|stock\s+|trading\s+)?(?:calls|picks|recommendations|predictions)|"
                   r"(?:market|stock)\s+calls?\s+(?:track\s+record|results)|track\s+record)\b", I)
TRIGGER = re.compile(rf"{FX_PAIR.pattern}|{FX_RATE.pattern}|\b(?:gold|silver)\s+(?:price|rate)|\b(?:price|rate)\s+of\s+"
                     rf"(?:the\s+)?(?:gold|silver)\b|\b(?:gold|silver)\b.*\btoday\b|{COINS.pattern}|{STOCKY.pattern}|"
                     r"\b(?:crypto|cryptocurrenc(?:y|ies)|forex|exchange\s+rate|stop[-\s]?loss|take[-\s]?profit|price\s+(?:target|prediction)|"
                     r"target\s+price|buy\s+or\s+sell)\b", I)
# "Buy 10 shares of NVDA" / "purchase 5 shares of Apple" - paper trading only, with confirmation
BUY_SHARES = re.compile(r"\b(?:buy|purchase|acquire|trade|invest\s+in)\s+(?:\d+\s+)?(?:shares?|stock|stocks?|units?)\b", I)
# an investment decision ("how much should I buy", "when to sell") - general deep research leaves these to markets/Laya
INVEST = re.compile(r"\b(?:how\s+much\s+(?:\w+\s+){0,3}(?:should|to|can)\s+(?:i\s+)?(?:buy|invest)|when\s+(?:to|should\s+i)\s+sell|"
                    r"should\s+i\s+(?:buy|sell|invest|hold)|buy\s+or\s+sell|stop[-\s]?loss|take[-\s]?profit|price\s+target)\b", I)


def match(text):
    return bool(TRACK.search(text)) or (bool(TRIGGER.search(text) or (INVEST.search(text) and named_asset(text))
                                        or (BUY_SHARES.search(text) and named_asset(text)))
                                        and not re.search(r"\b(?:jewell?ery|ring|necklace|buy\s+(?:a|an|the))\b", text, I))


def named_asset(text):
    """A coin, gold/silver, an ALL-CAPS ticker, or a KSE-100 company - without a network call (routing runs on every
    message; the PSX list is only used once it's cached)."""
    if COINS.search(text) or re.search(r"\b(?:gold|silver)\b", text, I) or re.search(r"\b[A-Z]{2,5}\b(?<!\bI)", text):
        return True
    hit = data._cache.get("psx:list")
    names = {w for n in (hit[1].values() if hit else []) for w in re.findall(r"[a-z]{4,}", n.lower())} - {
        "company", "limited", "pakistan", "bank", "development", "corporation", "industries", "power", "cement", "holdings"}
    return bool(names & set(re.findall(r"[a-z]{4,}", text.lower())))


def owner_currency():
    from ishti.facts import get_facts
    return {"pakistan": "PKR", "india": "INR", "united kingdom": "GBP", "uae": "AED", "saudi arabia": "SAR"}.get(
        get_facts().get("owner").get("country", "").lower(), "USD")


def money(v, cur="USD"):
    sym = {"USD": "$", "EUR": "€", "GBP": "£"}.get(cur)
    fmt = f"{v:,.2f}" if abs(v) < 1000 else f"{v:,.0f}"
    return f"{sym}{fmt}" if sym else f"{cur} {fmt}"


def pct(v):
    return "n/a" if v is None else f"{v:+.2f}%"


def stock_names(text):
    """Companies/indices named in the text (the local model reads them: 'nvidia and OGDC shares' -> 2)."""
    from ishti import llm
    raw = llm.ask('List the companies, stock tickers or indices this message asks about, as JSON {"stocks": [...]} '
                  f"(names or tickers exactly as written; [] if none).\n\n{text}", system=None, temperature=0,
                  max_tokens=80, response_format={"type": "json_object"})
    try:
        names = json.loads(raw).get("stocks") or []
    except (ValueError, AttributeError):
        names = []
    return [str(n).strip() for n in names if str(n).strip()][:5]


def psx_symbol(name, text):
    """PSX symbol for a name/ticker if it is a Pakistani listed company, else None."""
    syms = data.psx_symbols()
    up = name.upper().replace(" ", "")
    if up in syms:
        return up
    by_name = {n.lower(): s for s, n in syms.items()}
    hit = difflib.get_close_matches(name.lower(), by_name, n=1, cutoff=0.8) or \
        [n for n in by_name if name.lower() in n and len(name) > 3][:1]
    if hit:
        return by_name[hit[0]]
    if re.search(r"\b(?:psx|kse|pakistan)\b", text, I) and re.fullmatch(r"[A-Za-z]{2,6}", name):
        return name.upper()  # a PSX ticker outside the KSE-100
    return None


def stock_line(name, text, deep):
    sym = psx_symbol(name, text)
    if sym:
        q = data.psx(sym)
        if q:
            pos = (q["price"] - q["low_52w"]) / (q["high_52w"] - q["low_52w"]) * 100 if q.get("high_52w") else None
            line = (f"**{q['name']} ({sym}, PSX)**: Rs {q['price']:,.2f}, {q['change']:+.2f} ({q['change_pct']}%) today"
                    + (f", 52-week {q['low_52w']:,.2f}–{q['high_52w']:,.2f} ({pos:.0f}% of range)" if pos is not None else "")
                    + (f", P/E {q['pe']}" if q.get("pe") else ""))
            if deep:
                line += "\n- PSX blocks price history, so trend indicators aren't available for it; the range position and P/E are."
            return line, f"https://dps.psx.com.pk/company/{sym}", None
    ticker = name if name.isupper() and re.fullmatch(r"\^?[A-Z.\-]{1,6}", name) and data.stock(name) else data.find_ticker(name)
    s = ticker and data.stock(ticker)
    if not s:
        return f"**{name}**: no market data found (not a listed ticker?).", None, None
    a = data.analyze(s["closes"])
    cur = s["currency"] or "USD"
    line = (f"**{s['name']} ({s['symbol']})**: {money(a['last'], cur)}, {pct(a['chg_1d'])} today, {pct(a['chg_1m'])} 1 month, "
            f"{pct(a['chg_1y'])} 1 year; 52-week {money(a['low'], cur)}–{money(a['high'], cur)}")
    if deep:
        rows = [("Trend verdict", a["verdict"].upper()), ("RSI (14)", f"{a['rsi14']:.0f}"),
                ("20 / 50 / 200-day avg", " / ".join(money(a[k], cur) if a[k] else "n/a" for k in ("sma20", "sma50", "sma200"))),
                ("MACD vs signal", f"{a['macd']:.2f} vs {a['macd_signal']:.2f}"), ("3-month change", pct(a["chg_3m"])),
                ("Volatility (yearly)", f"{a['volatility']:.0f}%"), ("Max drawdown (1y)", f"{a['max_drawdown']:.0f}%"),
                ("Position in 52-week range", f"{a['range_pos']:.0f}%")]
        if s.get("pe"):
            rows.append(("P/E (trailing)", f"{s['pe']:.1f}"))
        line += "\n\n| Indicator | Value |\n|---|---|\n" + "\n".join(f"| {k} | {v} |" for k, v in rows)
        line += "\n\n" + "\n".join(f"- {sig}" for sig in a["signals"])
    chart = {"symbol": s["symbol"], "dates": s["dates"], "closes": [round(c, 4) for c in s["closes"]], "currency": cur}
    return line, f"https://finance.yahoo.com/quote/{s['symbol']}", chart


def research_desk(text, session, local):
    """Full research + decision for up to 3 named assets (research.py): what to do, at what price, how much, when to sell."""
    from ishti.skills.markets import research
    names = [w for w in METALS if re.search(rf"\b{w}\b", text, I)] + list(dict.fromkeys(c.lower() for c in COINS.findall(text)))
    taken = set(names)
    names += [n for n in stock_names(text) if n.lower() not in taken and n.lower() not in METALS]
    if not names:
        return None
    parts, sources = [], []
    for n in names[:3]:
        try:
            body, src = research.research(n, text, session, local)
        except Exception as exc:
            body, src = f"**{n}**: research failed ({type(exc).__name__}: {str(exc)[:120]}).", None
        parts.append(body)
        if src:
            sources.append(src)
    note = ("\n\n---\n_How this was decided: 5 years of prices, fundamentals, analyst targets and news, a backtest on the "
            "asset's own history and 3,000 simulated price paths. It is a rules-based estimate of odds, not a guarantee and "
            "not licensed financial advice - markets can move against any plan, so never invest money you can't afford to "
            "lose, and keep to the stop-loss._")
    return {"reply": "\n\n".join(parts) + note, "sources": sources, "no_escalate": True}


WATCHLIST = __import__("os").path.join(__import__("ishti").ROOT, "data", "watchlist.json")
DEFAULT_WATCH = [{"kind": "metal", "id": "XAU"}, {"kind": "crypto", "id": "bitcoin"}, {"kind": "fx", "id": "USD/PKR"},
                 {"kind": "psx", "id": "OGDC"}, {"kind": "stock", "id": "NVDA"}, {"kind": "stock", "id": "AAPL"}]


def watchlist(items=None):
    """Read (items=None) or replace the owner's watchlist (data/watchlist.json)."""
    if items is not None:
        with open(WATCHLIST, "w", encoding="utf-8") as f:
            json.dump(items, f)
        return items
    try:
        with open(WATCHLIST, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return DEFAULT_WATCH


def resolve(q):
    """Free text ('nvidia', 'OGDC', 'btc', 'gold', 'usd/pkr') -> watchlist item, or None."""
    q = q.strip()
    if m := FX_PAIR.search(q.replace("/", " to ")):
        return {"kind": "fx", "id": f"{CUR[m.group(1).lower()]}/{CUR[m.group(2).lower()]}"}
    if q.lower() in METALS:
        return {"kind": "metal", "id": METALS[q.lower()]}
    if COINS.fullmatch(q) or re.search(r"\bcoin\b|\bcrypto\b", q, I):
        return {"kind": "crypto", "id": data.crypto_id(COINS.sub(lambda m: m.group(0), q))}
    if sym := psx_symbol(q, q):
        return {"kind": "psx", "id": sym}
    ticker = q if q.isupper() and data.stock(q) else data.find_ticker(q)
    return {"kind": "stock", "id": ticker} if ticker else None


def quote(item):
    """One watchlist card: label, price, change %, currency and a sparkline (recent closes) where available."""
    kind, id_ = item["kind"], item["id"]
    if kind == "fx":
        a, b = id_.split("/")
        return {**item, "label": f"{a}/{b}", "price": data.fx(a, b), "currency": b, "change_pct": None, "spark": []}
    if kind == "metal":
        return {**item, "label": {"XAU": "Gold", "XAG": "Silver"}.get(id_, id_), "price": data.metal(id_),
                "currency": "USD", "unit": "oz", "change_pct": None, "spark": []}
    if kind == "crypto":
        q, hist = data.crypto(id_), data.crypto_history(id_, 90)
        return {**item, "label": id_.replace("-", " ").title(), "price": q["price"], "currency": "USD",
                "change_pct": q["change_24h"], "spark": hist[-60:]}
    if kind == "psx":
        q = data.psx(id_) or {}
        return {**item, "label": id_, "name": q.get("name"), "price": q.get("price"), "currency": "PKR",
                "change_pct": float(q["change_pct"]) * (1 if (q.get("change") or 0) >= 0 else -1) if q.get("change_pct") else None,
                "spark": []}
    s = data.stock(id_)
    a = data.analyze(s["closes"])
    return {**item, "label": s["symbol"], "name": s["name"], "price": float(a["last"]), "currency": s["currency"],
            "change_pct": None if a["chg_1d"] is None else float(a["chg_1d"]), "spark": s["closes"][-60:]}


def op_buy_shares(text, session, local):
    """"Buy 10 shares of NVDA" / "purchase 5 shares of Apple" - paper trading simulation with confirmation.
    Real broker execution is out of scope; this shows the cost and asks before simulating."""
    from ishti.skills import ask_first
    # extract quantity and asset name
    qm = re.search(r"\b(\d+)\s+(?:shares?|stocks?|units?)\b", text, I)
    qty = int(qm.group(1)) if qm else 1
    # find the asset name: everything after "of" or the named asset
    am = re.search(r"\b(?:of\s+)(.+?)(?:\s*$|\s+and\s+)", text, I)
    asset = am.group(1).strip() if am else ""
    if not asset:
        names = stock_names(text)
        asset = names[0] if names else ""
    if not asset:
        return "Which stock or asset do you want to buy? Tell me the name or ticker (for example 'buy 10 shares of NVDA')."
    # get the price
    ticker = asset if asset.isupper() and re.fullmatch(r"\^?[A-Z.\-]{1,6}", asset) and data.stock(asset) else data.find_ticker(asset)
    if not ticker:
        return f"I couldn't find market data for {asset}. Is it a listed stock?"
    s = data.stock(ticker)
    if not s:
        return f"I couldn't get a live price for {asset}."
    price = float(s["closes"][-1])
    cur = s.get("currency", "USD") or "USD"
    cost = price * qty
    low = s.get("low_52w")
    high = s.get("high_52w")
    range_str = ""
    if low and high:
        try:
            range_str = f"- 52-week range: {money(float(low), cur)}–{money(float(high), cur)}"
        except (TypeError, ValueError):
            range_str = "- 52-week range: n/a"
    else:
        range_str = "- 52-week range: n/a"
    reply = (f"**Paper trading simulation** (not real money - ISHTI doesn't execute broker orders):\n"
             f"- Asset: **{s['name']} ({s['symbol']})**\n"
             f"- Quantity: {qty} share{'s' if qty > 1 else ''}\n"
             f"- Price: {money(price, cur)} per share\n"
             f"- Total cost: **{money(cost, cur)}**\n"
             f"{range_str}\n\n"
             f"This is a paper-trading estimate only. Say 'yes' to run the simulation, or 'no' to cancel.")
    def simulate():
        return (f"**Paper trade executed** (simulation only - no real money moved):\n"
                f"Bought {qty} share{'s' if qty > 1 else ''} of {s['name']} ({s['symbol']}) at {money(price, cur)} each.\n"
                f"Total: {money(cost, cur)}.\n\n"
                f"This was a simulation. To trade for real you'd need a broker integration.")
    return ask_first(session, "markets", f"Paper-trade {qty} share{'s' if qty > 1 else ''} of {s['name']} ({s['symbol']}) for {money(cost, s['currency'])} (simulation only)", simulate)


def run(text, session):
    local = owner_currency()
    if TRACK.search(text):
        from ishti.skills.markets import research
        return {"reply": research.track_record(), "sources": []}
    if BUY_SHARES.search(text):
        return op_buy_shares(text, session, local)
    deep = bool(ANALYZE.search(text))
    if deep and not FX_PAIR.search(text) and (out := research_desk(text, session, local)):
        return out
    jobs, lines, sources, charts = [], [], [], []

    for a, b in FX_PAIR.findall(text):
        jobs.append(("fx", CUR[a.lower()], CUR[b.lower()]))
    for a, b in FX_RATE.findall(text):
        base = CUR[(a or b).lower()]
        if not any(j[0] == "fx" and j[1] == base for j in jobs) and base != local:
            jobs.append(("fx", base, local))
    for word, sym in METALS.items():
        if re.search(rf"\b{word}\b", text, I):
            jobs.append(("metal", word, sym))
    for coin in dict.fromkeys(c.lower() for c in COINS.findall(text)):
        jobs.append(("crypto", coin, None))
    if STOCKY.search(text) or deep or not jobs:
        # coin words the model also lists as "stocks" are already covered
        taken = {j[1].lower() for j in jobs if j[0] == "crypto"}
        jobs += [("stock", n, None) for n in stock_names(text) if n.lower() not in taken and n.lower() not in METALS]
    if not jobs:
        return None
    trace(session, "markets", "live data: " + ", ".join(j[1] if j[0] != "fx" else f"{j[1]}→{j[2]}" for j in jobs), status="start")

    def one(job):
        kind, a, b = job
        if kind == "fx":
            return f"**1 {a} = {data.fx(a, b):,.2f} {b}**", "https://open.er-api.com", None
        if kind == "metal":
            oz = data.metal(b)
            usd_pkr = data.fx("USD", local)
            per_tola = oz / data.OUNCE_G * data.TOLA_G * usd_pkr
            return (f"**{a.title()}** (spot): {money(oz)}/oz" + (f" · {money(per_tola, local)} per tola (24K, before local premium)"
                                                            if local != "USD" else ""), "https://gold-api.com", None)
        coin = data.crypto_id(a)
        q = data.crypto(coin)
        extra = f" · {money(q['price'] * data.fx('USD', local), local)}" if local != "USD" else ""
        line = f"**{coin.replace('-', ' ').title()}**: {money(q['price'])} ({pct(q['change_24h'])} 24h){extra}"
        chart = None
        if deep:
            closes = data.crypto_history(coin)
            if len(closes) > 30:
                an = data.analyze(closes)
                line += (f"\n- Trend: **{an['verdict']}** · RSI {an['rsi14']:.0f} · 1 month {pct(an['chg_1m'])} · 1 year "
                         f"{pct(an['chg_1y'])} · volatility {an['volatility']:.0f}%\n" + "\n".join(f"- {s}" for s in an["signals"]))
        return line, f"https://www.coingecko.com/en/coins/{coin}", chart

    def safe(job):
        try:
            return stock_line(job[1], text, deep) if job[0] == "stock" else one(job)
        except Exception as exc:
            return f"**{job[1]}**: couldn't fetch live data ({type(exc).__name__}).", None, None

    with cf.ThreadPoolExecutor(6) as pool:
        for line, src, chart in pool.map(safe, jobs):
            lines.append(line)
            if src:
                sources.append(src)
            if chart:
                charts.append(chart)
    trace(session, "markets", f"{len(lines)} live quotes", status="done")
    if not deep and len(lines) >= 2 and not any("\n" in l for l in lines):  # D1: several quotes -> a table
        from ishti.skills import md_table
        rows = [(m.group(1), m.group(2)) if (m := re.match(r"\*\*(.+?)\*\*:?\s*(.*)", l)) else ("", l) for l in lines]
        reply = md_table(("Asset", "Price"), rows)
    else:
        reply = "\n\n".join(lines) if deep or any("\n" in l for l in lines) else "\n".join(f"- {l}" for l in lines)
    if deep:
        reply += "\n\n_Signals from price history, not financial advice._"
    return {"reply": reply, "sources": sources, "charts": charts}


if __name__ == "__main__":
    assert match("whats the today price of gold and bitcoin and usd to pkr")
    assert match("analyze nvidia stock") and match("OGDC share price") and match("dollar rate today")
    assert not match("find me the cheapest samsung s25") and not match("buy a gold ring")
    assert FX_PAIR.findall("usd to pkr and usd to inr") == [("usd", "pkr"), ("usd", "inr")]
    assert FX_RATE.findall("what is the dollar rate today")[0][0] == "dollar"
    assert [c.lower() for c in COINS.findall("bitcoin and eth price")] == ["bitcoin", "eth"]
    assert match("should I buy BTC now") and match("what is the stop loss for HBL") and match("how did your market calls do")
    assert not match("when to sell my car") and not match("how much rice should I buy for a month")
    assert INVEST.search("research nvidia, how much should I buy with $3000") and ANALYZE.search("predict tesla stock price")
    print("[self-check] markets OK")
