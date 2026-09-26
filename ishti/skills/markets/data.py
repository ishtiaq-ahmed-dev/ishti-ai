"""Live market data from free sources (no keys) and a technical analyzer.

Stocks: yfinance (the free provider OpenBB uses). PSX: the exchange's own company page (live quote + stats;
it blocks history). Crypto: CoinGecko. Currencies: open.er-api.com. Gold/silver: gold-api.com."""
import re
import time

import numpy as np
import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36"}
TOLA_G, OUNCE_G = 11.6638, 31.1035
_cache = {}


def cached(key, ttl, fn):
    """Market data changes by the minute; a short cache keeps repeated questions instant."""
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    value = fn()
    _cache[key] = (time.time(), value)
    return value


def fx_rates(base="USD"):
    return cached(f"fx:{base}", 600, lambda: requests.get(f"https://open.er-api.com/v6/latest/{base}", timeout=10).json()["rates"])


def fx(base, target):
    return fx_rates(base.upper())[target.upper()]


def metal(symbol="XAU"):
    """Spot price in USD per troy ounce."""
    return cached(f"metal:{symbol}", 300, lambda: float(
        requests.get(f"https://api.gold-api.com/price/{symbol}", timeout=10).json()["price"]))


def crypto_id(name):
    def find():
        coins = requests.get("https://api.coingecko.com/api/v3/search", params={"query": name}, timeout=10).json()["coins"]
        return coins[0]["id"] if coins else None
    return cached(f"cgid:{name.lower()}", 86400, find)


def crypto(coin_id, vs="usd"):
    """{'price', 'change_24h'} in vs currency."""
    d = cached(f"cg:{coin_id}:{vs}", 60, lambda: requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": coin_id, "vs_currencies": vs, "include_24hr_change": "true"}, timeout=10).json()[coin_id])
    return {"price": d[vs], "change_24h": d.get(f"{vs}_24h_change")}


def crypto_history(coin_id, days=365):
    d = cached(f"cgh:{coin_id}:{days}", 900, lambda: requests.get(
        f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart",
        params={"vs_currency": "usd", "days": days, "interval": "daily"}, timeout=15).json())
    return [p[1] for p in d.get("prices", [])]


def psx(symbol):
    """Live PSX quote from dps.psx.com.pk (first stats block = the regular market)."""
    def fetch():
        t = requests.get(f"https://dps.psx.com.pk/company/{symbol.upper()}", headers=UA, timeout=10).text
        close = re.search(r'quote__close">Rs\.([\d,.]+)<', t)
        if not close:
            return None
        stats = {}
        for k, v in re.findall(r'stats_label">([^<]+)</div>\s*<div class="stats_value">([^<]+)<', t):
            stats.setdefault(k.strip().rstrip(" ^*").strip(), v.strip())
        change = re.findall(r'class="change__(?:value|percent)"[^>]*>([^<]+)<', t)
        name = re.search(r'quote__name">([^<]+)<', t)
        neg = "change__text--neg" in t[t.find("quote__close"):t.find("quote__close") + 400]
        rng = re.findall(r"[\d,]+\.?\d*", stats.get("52-WEEK RANGE", ""))  # "238.20 — 352.00"
        return {"symbol": symbol.upper(), "name": (name.group(1) if name else symbol).replace("&amp;", "&"),
                "price": float(close.group(1).replace(",", "")),
                "change": (-1 if neg else 1) * float(change[0]) if change else None,
                "change_pct": (change[1].strip(" ()%") if len(change) > 1 else None),
                "pe": stats.get("P/E Ratio (TTM)"), "volume": stats.get("Volume"),
                "low_52w": float(rng[0].replace(",", "")) if len(rng) == 2 else None,
                "high_52w": float(rng[1].replace(",", "")) if len(rng) == 2 else None, "currency": "PKR"}
    return cached(f"psx:{symbol.upper()}", 60, fetch)


def psx_symbols():
    """{symbol: company name} of the KSE-100, to recognise PSX companies by name."""
    def fetch():
        t = requests.get("https://dps.psx.com.pk/indices/KSE100", headers=UA, timeout=10).text
        return {s: n.replace("&amp;", "&") for s, n in re.findall(r'href="/company/([A-Z0-9]+)" data-title="([^"]+)"', t)}
    try:
        return cached("psx:list", 86400, fetch)
    except requests.RequestException:
        return {}


def stock(symbol):
    """yfinance: {'symbol','name','currency','closes','info'}; None if unknown."""
    import yfinance as yf

    def fetch():
        t = yf.Ticker(symbol)
        h = t.history(period="1y", auto_adjust=True)
        if h.empty:
            return None
        try:
            info = t.info
        except Exception:
            info = {}
        return {"symbol": symbol.upper(), "name": info.get("shortName") or info.get("longName") or symbol.upper(),
                "currency": info.get("currency", ""), "closes": h["Close"].tolist(),
                "dates": [d.strftime("%Y-%m-%d") for d in h.index],
                "pe": info.get("trailingPE"), "market_cap": info.get("marketCap"), "sector": info.get("sector")}
    return cached(f"yf:{symbol.upper()}", 300, fetch)


def find_ticker(name):
    """Company name -> ticker via Yahoo's search ('nvidia' -> NVDA)."""
    import yfinance as yf

    def search():
        quotes = [q for q in yf.Search(name, max_results=5).quotes if q.get("quoteType") in ("EQUITY", "ETF", "INDEX")]
        return quotes[0]["symbol"] if quotes else None
    return cached(f"yfsearch:{name.lower()}", 86400, search)


def _ema(x, n):
    k, out = 2 / (n + 1), [x[0]]
    for v in x[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return np.array(out)


def analyze(closes):
    """Technical read of a daily close series (oldest first). Needs ~30+ points; more gives more indicators."""
    c = np.asarray(closes, dtype=float)
    last = c[-1]
    chg = lambda n: (last / c[-n - 1] - 1) * 100 if len(c) > n else None
    sma = lambda n: c[-n:].mean() if len(c) >= n else None
    d = np.diff(c[-15:])
    gain, loss = d[d > 0].sum() / 14, -d[d < 0].sum() / 14
    rsi = 100 - 100 / (1 + gain / loss) if loss else 100.0
    macd_line = _ema(c, 12) - _ema(c, 26)
    signal = _ema(macd_line, 9)
    rets = np.diff(np.log(c))
    peak = np.maximum.accumulate(c)
    a = {"last": last, "chg_1d": chg(1), "chg_1w": chg(5), "chg_1m": chg(21), "chg_3m": chg(63), "chg_1y": chg(len(c) - 1),
         "sma20": sma(20), "sma50": sma(50), "sma200": sma(200), "rsi14": rsi,
         "macd": macd_line[-1], "macd_signal": signal[-1], "volatility": rets.std() * np.sqrt(252) * 100,
         "max_drawdown": ((c - peak) / peak).min() * 100, "high": c.max(), "low": c.min()}
    a["range_pos"] = (last - a["low"]) / (a["high"] - a["low"]) * 100 if a["high"] > a["low"] else 50
    signals = []
    if a["sma200"]:
        signals.append(("above" if last > a["sma200"] else "below") + " its 200-day average (long-term trend "
                       + ("up)" if last > a["sma200"] else "down)"))
    if a["sma50"] and a["sma200"]:
        signals.append("50-day above 200-day (golden-cross setup)" if a["sma50"] > a["sma200"] else "50-day below 200-day (death-cross setup)")
    signals.append(f"RSI {rsi:.0f}: " + ("overbought, pullback risk" if rsi > 70 else "oversold, bounce possible" if rsi < 30 else "neutral"))
    signals.append("MACD " + ("above signal: momentum rising" if a["macd"] > a["macd_signal"] else "below signal: momentum fading"))
    # symmetric vote: long trend, cross, momentum and 3-month move each say up (+1) or down (-1)
    vote = lambda cond, known=True: 0 if not known else (1 if cond else -1)
    m3 = a["chg_3m"]
    score = (vote(last > (a["sma200"] or 0), bool(a["sma200"])) + vote((a["sma50"] or 0) > (a["sma200"] or 0), bool(a["sma50"] and a["sma200"]))
             + vote(a["macd"] > a["macd_signal"]) + (0 if m3 is None or abs(m3) < 5 else vote(m3 > 0)))
    a["verdict"] = "bullish" if score >= 2 else "bearish" if score <= -2 else "mixed"
    a["signals"] = signals
    return a


if __name__ == "__main__":
    up = analyze(list(np.linspace(100, 200, 260) + np.sin(np.arange(260)) * 2))
    assert up["verdict"] == "bullish" and up["chg_1y"] > 90 and up["range_pos"] > 90, up
    down = analyze(list(np.linspace(200, 100, 260) + np.sin(np.arange(260)) * 2))
    assert down["verdict"] == "bearish" and down["max_drawdown"] < -40, down
    flat = analyze([100.0] * 40)
    assert flat["volatility"] == 0 and flat["rsi14"] == 100.0
    print("[self-check] markets analyzer OK - trend, RSI, MACD, drawdown, verdict")
