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
ANALYZE = re.compile(r"\b(?:analy[sz]e|analysis|analyzer|technical|trend|outlook|forecast|rsi|moving\s+average|macd|"
                     r"should\s+i\s+(?:buy|sell|invest)|worth\s+(?:buying|investing)|bullish|bearish)\b", I)
TRIGGER = re.compile(rf"{FX_PAIR.pattern}|{FX_RATE.pattern}|\b(?:gold|silver)\s+(?:price|rate)|\b(?:price|rate)\s+of\s+"
                     rf"(?:the\s+)?(?:gold|silver)\b|\b(?:gold|silver)\b.*\btoday\b|{COINS.pattern}|{STOCKY.pattern}|"
                     r"\b(?:crypto|cryptocurrenc(?:y|ies)|forex|exchange\s+rate)\b", I)


def match(text):
    return bool(TRIGGER.search(text)) and not re.search(r"\b(?:jewell?ery|ring|necklace|buy\s+(?:a|an|the))\b", text, I)


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


def run(text, session):
    local = owner_currency()
    deep = bool(ANALYZE.search(text))
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
    print("[self-check] markets OK")
