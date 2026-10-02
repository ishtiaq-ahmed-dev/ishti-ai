"""Research desk: "should I buy OGDC?", "research nvidia", "how much bitcoin should I buy with 200k?"

Laid out like the open-source multi-agent trading desks (TauricResearch/TradingAgents: analysts -> bull/bear debate ->
trader -> risk manager), but every number comes from code and data, never from the language model:
  1. data       5 years of daily prices (Yahoo: stocks, PSX as SYMBOL.KA, crypto as BTC-USD, gold GC=F), fundamentals
                and analyst targets, recent news headlines; PSX's live price from the exchange
  2. analysts   technical (trend, RSI, MACD, ATR, support/resistance), fundamental (valuation, growth, analysts),
                news sentiment (local model rates each headline), history (yearly returns, CAGR, worst drawdown)
  3. backtest   did "trend + momentum up" actually pay on THIS asset? 20-day returns after such days vs all days
  4. forecast   3,000 simulated paths from the asset's own last 2 years of daily moves (bootstrap): price range in 1 and
                3 months, and the odds of reaching the target before the stop
  5. decision   score -> BUY / BUY ON A DIP / WAIT / AVOID; entry, stop (2 ATR), targets (1.5R, 3R), position size from
                the owner's capital (risk 1.5% of it per trade, never more than 20% in one asset)
  6. write-up   the local model writes the bull and bear case from those facts (no new numbers)
Each call is logged, so "how did your market calls do?" checks them against today's prices.
ponytail: rules-based scoring + bootstrap Monte Carlo, not a trained price model. Ceiling: history-driven odds
can't see surprises (earnings shocks, policy); the upgrade path is a walk-forward-validated model per asset class."""
import json
import math
import re
import time

import numpy as np

from ishti.skills import md_table, trace
from ishti.skills.markets import data

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


RISK_PER_TRADE, MAX_ALLOC = 0.015, 0.20
RNG = np.random.default_rng()


# ---------------------------------------------------------------- 1. data
def history(symbol, years=5):
    """Daily OHLCV DataFrame (oldest first), or None."""
    import yfinance as yf
    return data.cached(f"hist:{symbol}:{years}", 900, lambda: _hist(yf, symbol, years))


def _hist(yf, symbol, years):
    h = yf.Ticker(symbol).history(period=f"{years}y", auto_adjust=True)
    return None if h.empty or len(h) < 60 else h[["Open", "High", "Low", "Close", "Volume"]].dropna()


def fundamentals(symbol):
    import yfinance as yf

    def fetch():
        try:
            i = yf.Ticker(symbol).info or {}
        except Exception:
            i = {}
        keys = {"pe": "trailingPE", "forward_pe": "forwardPE", "peg": "trailingPegRatio", "margin": "profitMargins",
                "revenue_growth": "revenueGrowth", "earnings_growth": "earningsGrowth", "debt_equity": "debtToEquity",
                "roe": "returnOnEquity", "target": "targetMeanPrice", "target_high": "targetHighPrice",
                "target_low": "targetLowPrice", "rating": "recommendationKey", "rating_mean": "recommendationMean",
                "analysts": "numberOfAnalystOpinions", "dividend_yield": "dividendYield", "beta": "beta",
                "sector": "sector", "name": "shortName", "currency": "currency"}
        return {k: i.get(v) for k, v in keys.items()}
    return data.cached(f"fund:{symbol}", 3600, fetch)


def news(symbol, name):
    """Recent headlines: Yahoo's feed for the ticker; a web search when Yahoo has none (most PSX companies)."""
    import yfinance as yf
    words = {w for w in re.findall(r"[a-z]{4,}", name.lower()) if w not in GENERIC} | {symbol.split(".")[0].split("-")[0].lower()}
    about = lambda title, text="": bool(words & set(re.findall(r"[a-z0-9]+", f"{title} {text}".lower())))
    items = []
    try:  # Yahoo's feed for a ticker mixes in related stories (Broadcom, Taiwan...): keep the ones about this company
        for n in (yf.Ticker(symbol).news or [])[:20]:
            c = n.get("content") or n
            if about(c.get("title", ""), c.get("summary") or ""):
                items.append({"title": c.get("title", ""), "summary": (c.get("summary") or "")[:240],
                              "date": (c.get("pubDate") or "")[:10], "source": (c.get("provider") or {}).get("displayName", ""),
                              "url": (c.get("canonicalUrl") or {}).get("url", "")})
    except Exception as _exc:
        swallowed('ishti/skills/markets/research.py:news', _exc)
    if len(items) < 3:
        try:
            from tools.web.search import web_search
            items += [{"title": h.title, "summary": h.snippet[:240], "date": "", "source": "web", "url": h.url}
                      for h in web_search(f"{name} news", max_results=8)
                      if not JUNK.search(h.title + " " + h.url) and not QUOTE_PAGE.search(h.title) and about(h.title)]
        except Exception as _exc:
            swallowed('ishti/skills/markets/research.py:news', _exc)
    return [n for n in items if n["title"]][:10]


# web-search fallback: keep articles about the company, not quote pages or spam
GENERIC = {"company", "limited", "development", "corporation", "holdings", "group", "bank", "international", "pakistan"}
JUNK = re.compile(r"porn|\bsex|xxx|casino|betting|onlyfans|hamster", re.I)
QUOTE_PAGE = re.compile(r"stock\s+(?:quote|price)|share\s+price|\bquote\b|\((?:PSX|NASDAQ|NYSE|KSE):|\b[A-Z]{2,6}\.PSX\b|"
                        r"historical\s+(?:data|prices)|market\s+cap|price\s+chart|live\s+price", re.I)


# ---------------------------------------------------------------- 2. analysts
def atr(df, n=14):
    h, l, c = df["High"].values, df["Low"].values, df["Close"].values
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    return float(tr[-n:].mean())


def technical(df):
    c = df["Close"].values
    a = data.analyze(c[-260:])  # the one-year indicator read the quick analyzer uses
    a["atr"] = atr(df)
    a["support"], a["resistance"] = float(df["Low"].values[-20:].min()), float(df["High"].values[-20:].max())
    vol = df["Volume"].values
    a["volume_trend"] = float(vol[-20:].mean() / vol[-120:].mean()) if vol[-120:].mean() else None
    a["chg_6m"] = (c[-1] / c[-127] - 1) * 100 if len(c) > 127 else None
    return a


def past(df):
    """The asset's own history: yearly returns, CAGR, worst fall, and how often it rose over a year."""
    c = df["Close"]
    yearly = c.groupby(c.index.year).agg(["first", "last"])
    years = [(int(y), (r["last"] / r["first"] - 1) * 100) for y, r in yearly.iterrows()]
    span = (c.index[-1] - c.index[0]).days / 365.25
    peak = np.maximum.accumulate(c.values)
    one_y = c.values[252:] / c.values[:-252] - 1 if len(c) > 300 else np.array([])
    return {"years": years, "cagr": ((c.iloc[-1] / c.iloc[0]) ** (1 / span) - 1) * 100 if span > 0.9 else None,
            "max_drawdown": float(((c.values - peak) / peak).min() * 100), "span_years": span,
            "up_year_share": float((one_y > 0).mean() * 100) if len(one_y) else None}


def fundamental_view(f, price):
    """(+/-points, notes) from valuation, growth and the analysts' consensus, clipped to [-2, 2]."""
    pts, notes = 0, []
    if f.get("target") and price:
        up = (f["target"] / price - 1) * 100
        notes.append(f"analysts' average target {f['target']:,.2f} ({up:+.0f}% from here, {f.get('analysts') or '?'} analysts)")
        pts += 1 if up > 15 else -1 if up < -5 else 0
    if f.get("rating_mean"):
        pts += 1 if f["rating_mean"] <= 2 else -1 if f["rating_mean"] >= 3.5 else 0
        notes.append(f"consensus rating {str(f.get('rating') or '').replace('_', ' ')} ({f['rating_mean']:.1f} on 1=strong buy..5=sell)")
    if f.get("earnings_growth") is not None:
        pts += 1 if f["earnings_growth"] > 0.10 else -1 if f["earnings_growth"] < 0 else 0
        notes.append(f"earnings growth {f['earnings_growth'] * 100:+.0f}% y/y")
    if f.get("pe") and f.get("forward_pe"):  # the direction is spelled out: the write-up once read a rising P/E as "cheap"
        way = ("earnings expected to grow" if f["forward_pe"] < f["pe"] * 0.95 else
               "earnings expected to fall" if f["forward_pe"] > f["pe"] * 1.05 else "earnings expected flat")
        notes.append(f"P/E {f['pe']:.1f} now vs {f['forward_pe']:.1f} on next year's estimate ({way})")
    if f.get("debt_equity") is not None and f["debt_equity"] > 200:
        pts -= 1
        notes.append(f"heavy debt (debt/equity {f['debt_equity']:.0f}%)")
    return max(-2, min(2, pts)), notes


def sentiment(headlines):
    """-1..1 from the local model rating each headline (fenced: headlines are untrusted text)."""
    if not headlines:
        return None, []
    from ishti import llm
    from ishti.untrusted import guard
    listing = "\n".join(f"{i}. {h['title']} - {h['summary'][:160]}" for i, h in enumerate(headlines, 1))
    raw = llm.ask("Rate each headline for the company's share price: positive, negative or neutral. JSON "
                  '{"ratings": ["positive"|"negative"|"neutral", ...]} in the same order.\n\n' + guard(listing, "news headlines"),
                  system=None, temperature=0, max_tokens=200, response_format={"type": "json_object"})
    try:
        ratings = [str(r).lower() for r in json.loads(raw).get("ratings", [])][:len(headlines)]
    except (ValueError, AttributeError):
        return None, []
    val = {"positive": 1, "negative": -1}
    scored = [val.get(r, 0) for r in ratings]
    return (sum(scored) / len(scored) if scored else None), ratings


# ---------------------------------------------------------------- 3. backtest
def backtest(df, horizon=20):
    """20-day returns after days the trend+momentum rule was on (close > 200-day, 50-day > 200-day, MACD > signal)
    vs after all days. Edge > 0 means the rule has paid on this asset."""
    c = df["Close"]
    sma50, sma200 = c.rolling(50).mean(), c.rolling(200).mean()
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    on = (c > sma200) & (sma50 > sma200) & (macd > macd.ewm(span=9, adjust=False).mean())
    fwd = (c.shift(-horizon) / c - 1) * 100
    valid = sma200.notna() & fwd.notna()
    if valid.sum() < 100:
        return None
    sig, base = fwd[valid & on], fwd[valid]
    if len(sig) < 20:
        return {"days": int(len(sig)), "note": "the rule was rarely on - too few cases to judge"}
    b = {"days": int(len(sig)), "hit": float((sig > 0).mean() * 100), "avg": float(sig.mean()),
         "base_avg": float(base.mean()), "base_hit": float((base > 0).mean() * 100),
         "edge": float(sig.mean() - base.mean()), "on_now": bool(on.iloc[-1])}
    b["verdict"] = ("has worked here" if b["edge"] > 0.5 and b["hit"] >= b["base_hit"] else
                    "has NOT worked here" if b["edge"] < -0.5 else "has made little difference here")
    return b


# ---------------------------------------------------------------- 4. forecast
def simulate(df, entry, stop, target, days=63, n=3000):
    """Bootstrap the last 2 years of daily log returns into n paths: P(target before stop), median days to target,
    and the 10th/50th/90th percentile price after 21 and 63 trading days."""
    c = df["Close"].values[-505:]
    rets = np.diff(np.log(c))
    paths = entry * np.exp(np.cumsum(RNG.choice(rets, size=(n, days)), axis=1))
    hit_t = np.where((paths >= target).any(axis=1), (paths >= target).argmax(axis=1), days + 1)
    hit_s = np.where((paths <= stop).any(axis=1), (paths <= stop).argmax(axis=1), days + 1)
    wins = hit_t < hit_s
    return {"p_target": float(wins.mean()), "p_stop": float((hit_s < hit_t).mean()),
            "days_to_target": int(np.median(hit_t[wins])) + 1 if wins.any() else None,
            "m1": [float(np.percentile(paths[:, 20], q)) for q in (10, 50, 90)],
            "m3": [float(np.percentile(paths[:, -1], q)) for q in (10, 50, 90)]}


# ---------------------------------------------------------------- 5. decision
def score(t, fund_pts, sent, bt):
    parts = {"trend": (1 if t["last"] > (t["sma200"] or 0) else -1) * bool(t["sma200"])
                      + (1 if (t["sma50"] or 0) > (t["sma200"] or 0) else -1) * bool(t["sma50"] and t["sma200"]),
             "momentum": (1 if t["macd"] > t["macd_signal"] else -1)
                         + (0 if t["chg_3m"] is None or abs(t["chg_3m"]) < 5 else 1 if t["chg_3m"] > 0 else -1),
             "RSI": -1 if t["rsi14"] > 75 else (1 if t["rsi14"] < 30 and t["last"] > (t["sma200"] or 1e18) else 0),
             "fundamentals": fund_pts,
             "news": 0 if sent is None else 1 if sent > 0.25 else -1 if sent < -0.25 else 0,
             "backtest": 0 if not bt or "edge" not in bt else 1 if bt["edge"] > 1 and bt["hit"] > 55 else -1 if bt["edge"] < -1 else 0}
    return sum(parts.values()), parts


def plan(price, t, total):
    """Entry, stop and targets. A strong score buys now; a moderate one waits for a dip toward the 20-day average."""
    a = t["atr"]
    if total >= 4:
        action, entry = "BUY", price
    elif total >= 2:
        action, entry = "BUY ON A DIP", min(price, max(price - a, t["sma20"] or price - a))
    elif total >= -1:
        action, entry = "WAIT", None
    else:
        action, entry = "AVOID", None
    if entry is None:
        return {"action": action}
    stop = entry - 2 * a
    r = entry - stop
    return {"action": action, "entry": entry, "stop": stop, "t1": entry + 1.5 * r, "t2": entry + 3 * r, "risk_per_unit": r}


CAPITAL = re.compile(r"(?:(?P<cur>rs\.?|pkr|usd|\$|aed|sar|£|€)\s*)?(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<mult>k|lakh|lac|lakhs|million|m|crore)?"
                     r"\s*(?P<cur2>rs|rupees|pkr|usd|dollars|aed|sar)?", re.I)


def capital_of(text, default_cur):
    """'with 2 lakh', 'I have Rs 500,000', '$3000' -> (amount, currency) or None."""
    for m in CAPITAL.finditer(text):
        if not (m["mult"] or m["cur"] or m["cur2"] or re.search(r"\b(?:have|with|invest|capital|budget)\b[^.]{0,20}$", text[:m.start()], re.I)):
            continue
        n = float(m["num"].replace(",", ""))
        n *= {"k": 1e3, "lakh": 1e5, "lac": 1e5, "lakhs": 1e5, "million": 1e6, "m": 1e6, "crore": 1e7}.get((m["mult"] or "").lower(), 1)
        cur = (m["cur"] or m["cur2"] or "").lower().strip(".")
        cur = {"rs": "PKR", "rupees": "PKR", "pkr": "PKR", "$": "USD", "usd": "USD", "dollars": "USD", "aed": "AED", "sar": "SAR",
               "£": "GBP", "€": "EUR"}.get(cur, default_cur)
        if n >= 10:
            return n, cur
    return None


def position(capital, entry, stop, fractional=False):
    """Units to buy: risk at most 1.5% of capital if the stop is hit, and never put more than 20% of it in one asset."""
    units = min(capital * RISK_PER_TRADE / (entry - stop), capital * MAX_ALLOC / entry)
    units = round(units, 4) if fractional else math.floor(units)
    return units, units * entry


# ---------------------------------------------------------------- the desk
def resolve(name, text):
    """-> (history symbol, display name, currency, live price or None, kind)."""
    from ishti.skills.markets.skill import COINS, METALS, psx_symbol
    low = name.lower().strip()
    if low in METALS:
        return {"XAU": "GC=F", "XAG": "SI=F"}[METALS[low]], low.title(), "USD", data.metal(METALS[low]), "metal"
    if COINS.fullmatch(low):
        sym = data.COIN_SYMS.get(low) or data.COIN_SYMS.get(data.crypto_id(low), low.upper())
        coin = data.crypto_id(low)
        return f"{sym}-USD", coin.replace("-", " ").title(), "USD", data.crypto(coin)["price"], "crypto"
    if sym := psx_symbol(name, text):
        q = data.psx(sym) or {}
        return f"{sym}.KA", q.get("name") or sym, "PKR", q.get("price"), "psx"
    ticker = name if name.isupper() and re.fullmatch(r"\^?[A-Z.\-]{1,6}", name) else data.find_ticker(name)
    return ticker, None, None, None, "stock"


def research(name, text, session, local_cur):
    sym, label, cur, live, kind = resolve(name, text)
    if not sym:
        return f"**{name}**: I couldn't find it as a listed stock, coin or metal.", None
    trace(session, "research", f"{name}: 5 years of prices, fundamentals, news", status="start")
    df = history(sym)
    if df is None:
        return f"**{name}** ({sym}): no price history available to research.", None
    f = fundamentals(sym) if kind in ("stock", "psx") else {}
    label = label or f.get("name") or sym
    cur = cur or f.get("currency") or "USD"
    price = float(live or df["Close"].iloc[-1])
    t, hist = technical(df), past(df)
    heads = news(sym, label) if kind != "metal" else []
    trace(session, "research", f"{len(df)} days of prices, {len(heads)} headlines - analysing", status="done")
    sent, ratings = sentiment(heads)
    bt = backtest(df)
    fund_pts, fund_notes = fundamental_view(f, price)
    total, parts = score(t, fund_pts, sent, bt)
    p = plan(price, t, total)
    run_sim = lambda q: simulate(df, q.get("entry") or price, q.get("stop") or price - 2 * t["atr"], q.get("t1") or price + 3 * t["atr"])
    sim = run_sim(p)
    # the risk manager: a good score isn't enough if the simulated odds don't back it - step down and re-plan
    for weaker in (3, 0):
        if "entry" not in p or sim["p_target"] >= 0.45:
            break
        why = f"only {sim['p_target'] * 100:.0f}% of simulated paths reach the first target before the stop at {p['action']}"
        p = {**plan(price, t, weaker), "why_downgraded": why}
        sim = run_sim(p)
    money = lambda v: data_money(v, cur)
    coin = sym.split("-")[0] if kind == "crypto" else "units"
    trace(session, "research", f"decision: {p['action']} (score {total:+d})", status="done")

    # ---- the plan the owner acts on
    lines = [f"## {label} ({sym.removesuffix('.KA')}{', PSX' if kind == 'psx' else ''}) - **{p['action']}**",
             f"Price {money(price)} · score **{total:+d}** (BUY needs +4, buy on a dip +2; "
             f"{', '.join(f'{k} {v:+d}' for k, v in parts.items())})"]
    if p.get("why_downgraded"):
        lines.append(f"_Stepped down by the risk check: {p['why_downgraded']}._")
    if "entry" in p:
        lines.append(md_table(("Plan", ""), plan_rows({**p, "unit": coin}, t, sim, text, local_cur, cur, kind, money)))
    elif p["action"] == "WAIT":
        # "how much should I buy?" still gets an exact plan: the one that would apply once it breaks out
        e = t["resistance"] * 1.005
        brk = {"action": "BREAKOUT", "entry": e, "stop": e - 2 * t["atr"], "t1": e + 3 * t["atr"], "t2": e + 6 * t["atr"], "risk_per_unit": 2 * t["atr"]}
        bsim = simulate(df, brk["entry"], brk["stop"], brk["t1"])
        why = ("The trend is up, but its swings are too wide for a good trade right now: a sensible stop is hit before the "
               "first target more often than not" if p.get("why_downgraded") else "No clear edge right now - signals are mixed")
        if bsim["p_target"] >= 0.45:
            lines.append(f"{why}, so don't buy yet. **Buy only if it closes above {money(t['resistance'])}** (its recent high); "
                         "then:\n\n" + md_table(("If it breaks out", ""), plan_rows({**brk, "unit": coin}, t, bsim, text, local_cur, cur, kind, money)))
        else:
            lines.append(f"{why}, so don't buy yet - a breakout above {money(t['resistance'])} wouldn't have good odds either "
                         f"({bsim['p_target'] * 100:.0f}%). I'd look again after a calmer week or a pullback toward {money(t['support'])}.")
    else:
        lines.append("The trend, momentum and odds are against buying now. If you already hold it, protect it with a stop at "
                     f"{money(price - 2 * t['atr'])} (2 × its daily range below today's price).")

    # ---- forecast, analysts, history
    lines.append("### Where it could be (simulated from its own last 2 years)\n" + md_table(
        ("In", "Low (10%)", "Middle", "High (90%)"),
        [("1 month", *map(money, sim["m1"])), ("3 months", *map(money, sim["m3"]))]))
    tech_rows = [("Trend", f"{'above' if t['last'] > (t['sma200'] or 0) else 'below'} its 200-day average; 50-day "
                           f"{'above' if (t['sma50'] or 0) > (t['sma200'] or 0) else 'below'} 200-day"),
                 ("Momentum", f"MACD {'above' if t['macd'] > t['macd_signal'] else 'below'} signal; 3 months {data_pct(t['chg_3m'])}, 6 months {data_pct(t['chg_6m'])}"),
                 ("RSI (14)", f"{t['rsi14']:.0f}" + (" - stretched" if t["rsi14"] > 70 else " - oversold" if t["rsi14"] < 30 else "")),
                 ("Daily range (ATR)", money(t["atr"])), ("Support / resistance (20 days)", f"{money(t['support'])} / {money(t['resistance'])}")]
    if t["volume_trend"]:
        tech_rows.append(("Volume", f"{t['volume_trend']:.1f}× its 6-month average"))
    lines.append("### Analysts' desk\n" + md_table(("Technical", ""), tech_rows))
    if fund_notes:
        lines.append("**Fundamentals:** " + "; ".join(fund_notes) + ".")
    if heads:
        tone = "positive" if (sent or 0) > 0.25 else "negative" if (sent or 0) < -0.25 else "mixed"
        lines.append(f"**News ({len(heads)} recent headlines, {tone}):**\n" + "\n".join(
            f"- {h['title']}" + (f" ({h['source']}, {h['date']})" if h['date'] else "") + (f" - _{r}_" if r else "")
            for h, r in zip(heads[:5], ratings[:5] + [""] * 5)))
    if bt and "edge" in bt:
        lines.append(f"**Backtest on its own history:** after the {bt['days']} days its trend and momentum were both up, it rose over "
                     f"the next 20 days {bt['hit']:.0f}% of the time (average {bt['avg']:+.1f}%) vs {bt['base_hit']:.0f}% "
                     f"({bt['base_avg']:+.1f}%) on any day - this signal {bt['verdict']}."
                     + (" The signal is on now." if bt["on_now"] else " The signal is off now."))
    yrs = " · ".join(f"{y}: {r:+.0f}%" for y, r in hist["years"])
    lines.append(f"**Past {hist['span_years']:.0f} years:** {yrs}. "
                 + (f"Average {hist['cagr']:+.1f}% a year; " if hist["cagr"] is not None else "")
                 + f"worst fall {hist['max_drawdown']:.0f}%"
                 + (f"; up over a year {hist['up_year_share']:.0f}% of the time." if hist["up_year_share"] is not None else "."))

    # ---- bull vs bear (words only)
    facts = {"asset": label, "action": p["action"], "score_parts": parts, "technical": {k: t[k] for k in ("rsi14", "chg_3m", "chg_6m", "volatility")},
             "fundamentals": fund_notes, "news": [h["title"] for h in heads[:6]], "backtest": bt,
             "odds_target_first": sim["p_target"], "history": hist["years"]}
    from ishti import llm
    debate = llm.ask("You are the research desk's bull and bear analysts. From ONLY these facts, write:\n**Bull case:** 2-3 "
                     "short bullets\n**Bear case:** 2-3 short bullets\n**Bottom line:** one sentence agreeing with the desk's "
                     f"action ({p['action']}). Do not add any number that is not in the facts.\n\n{json.dumps(facts, default=str)[:3500]}",
                     temperature=0.2, max_tokens=320)
    lines.append(debate.strip())
    reply = "\n\n".join(lines)
    data_used = {"symbol": sym, "kind": kind, "days": int(len(df)), "headlines": len(heads),
                 "sources": ["Yahoo Finance"] + (["PSX"] if kind == "psx" else []) + (["CoinGecko"] if kind == "crypto" else [])}
    log_call(sym, label, cur, price, p, question=text, analysis=reply, data_used=data_used)
    return reply, f"https://finance.yahoo.com/quote/{sym}"


def plan_rows(p, t, sim, text, local_cur, cur, kind, money):
    """How much, buy at, stop, the two sells and the simulated odds - the table the owner acts on."""
    how = {"BUY ON A DIP": " (limit order - wait for this dip)", "BREAKOUT": " (a buy-stop order just above the recent high)"}
    rows = [("Buy at", money(p["entry"]) + how.get(p["action"], " (now)")),
            ("Stop-loss", f"{money(p['stop'])} ({(p['stop'] / p['entry'] - 1) * 100:.1f}%) - sell everything if it closes below"),
            ("Sell half at (target 1)", f"{money(p['t1'])} ({(p['t1'] / p['entry'] - 1) * 100:+.1f}%) - then move the stop up to your buy price"),
            ("Sell the rest at (target 2)", f"{money(p['t2'])} ({(p['t2'] / p['entry'] - 1) * 100:+.1f}%) - or earlier if it closes "
                                            f"below the 50-day average ({money(t['sma50'] or 0)})"),
            ("Odds (simulated)", f"{sim['p_target'] * 100:.0f}% reach target 1 before the stop, {sim['p_stop'] * 100:.0f}% hit the stop first"
                                 + (f"; typically in ~{sim['days_to_target']} trading days" if sim["days_to_target"] else ""))]
    unit = {"psx": "shares", "stock": "shares", "metal": "oz"}.get(kind) or p.get("unit", "units")
    if cap := capital_of(text, local_cur):
        units, cost = position(cap[0] * (data.fx(cap[1], cur) if cap[1] != cur else 1.0), p["entry"], p["stop"], fractional=kind not in ("psx", "stock"))
        yours = f" ({data_money(cost * data.fx(cur, cap[1]), cap[1])})" if cap[1] != cur else ""
        rows.insert(0, ("How much to buy", f"**{units:g} {unit}** ≈ {money(cost)}{yours} of your {data_money(cap[0], cap[1])} - if the stop "
                                           f"is hit you lose about {money(units * p['risk_per_unit'])} ({RISK_PER_TRADE * 100:.1f}% of it)"))
    else:
        per = 100_000 if local_cur == "PKR" else 1_000
        units, cost = position(per * (data.fx(local_cur, cur) if local_cur != cur else 1), p["entry"], p["stop"], fractional=kind not in ("psx", "stock"))
        rows.insert(0, ("How much to buy", f"per {data_money(per, local_cur)} of your capital: **{units:g} {unit}** ≈ {money(cost)} "
                                           "(tell me your amount for an exact figure)"))
    return rows


def data_money(v, cur):
    from ishti.skills.markets.skill import money
    return money(v, cur)


def data_pct(v):
    return "n/a" if v is None else f"{v:+.1f}%"


# ---------------------------------------------------------------- track record
def _db():
    from ishti.facts import get_facts
    f = get_facts()
    with f.lock, f.db:
        f.db.execute("CREATE TABLE IF NOT EXISTS market_calls(ts REAL, symbol TEXT, name TEXT, currency TEXT, price REAL, "
                     "action TEXT, entry REAL, stop REAL, t1 REAL, t2 REAL)")
        cols = {r[1] for r in f.db.execute("PRAGMA table_info(market_calls)")}
        for col in ("question", "analysis", "data"):   # H: history keeps the question, the full write-up and the data used
            if col not in cols:
                f.db.execute(f"ALTER TABLE market_calls ADD COLUMN {col} TEXT")
    return f


def log_call(sym, name, cur, price, p, question="", analysis="", data_used=None):
    f = _db()
    with f.lock, f.db:
        f.db.execute("INSERT INTO market_calls(ts, symbol, name, currency, price, action, entry, stop, t1, t2, question, analysis, data) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (time.time(), sym, name, cur, price, p["action"], p.get("entry"), p.get("stop"), p.get("t1"),
                      p.get("t2"), question or "", analysis or "", json.dumps(data_used or {})))


_CALL_COLS = ("rowid, ts, symbol, name, currency, price, action, entry, stop, t1, t2, question, analysis, data")


def _call_json(r):
    try:
        data = json.loads(r[13] or "{}")
    except (TypeError, ValueError):
        data = {}
    return {"id": r[0], "ts": r[1], "symbol": r[2], "name": r[3], "currency": r[4], "price": r[5],
            "action": r[6], "entry": r[7], "stop": r[8], "t1": r[9], "t2": r[10],
            "question": r[11] or "", "analysis": r[12] or "", "data": data}


def history_rows(q="", limit=200):
    """Saved analyses, newest first (lesson: a LIMIT must cut the oldest rows, never the newest)."""
    f = _db()
    limit = max(1, min(int(limit or 200), 500))
    with f.lock:
        if (q or "").strip():
            like = f"%{q.strip()}%"
            rows = f.db.execute(f"SELECT {_CALL_COLS} FROM market_calls WHERE symbol LIKE ? OR name LIKE ? OR question LIKE ? "
                                "OR action LIKE ? OR analysis LIKE ? ORDER BY ts DESC LIMIT ?",
                                (like, like, like, like, like, limit)).fetchall()
        else:
            rows = f.db.execute(f"SELECT {_CALL_COLS} FROM market_calls ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    return [_call_json(r) for r in rows]


def get_call(cid):
    f = _db()
    with f.lock:
        r = f.db.execute(f"SELECT {_CALL_COLS} FROM market_calls WHERE rowid=?", (int(cid),)).fetchone()
    return _call_json(r) if r else None


def delete_call(cid):
    f = _db()
    with f.lock, f.db:
        return f.db.execute("DELETE FROM market_calls WHERE rowid=?", (int(cid),)).rowcount


def track_record():
    """Every past call checked against the prices since: did a BUY reach target 1 or the stop first?"""
    f = _db()
    with f.lock:
        calls = f.db.execute("SELECT * FROM market_calls ORDER BY ts DESC LIMIT 20").fetchall()
    if not calls:
        return "No market calls yet - ask me to research something first."
    rows = []
    for ts, sym, name, cur, price, action, entry, stop, t1, t2 in calls:
        df = history(sym, 1)
        since = df[df.index.tz_localize(None) >= np.datetime64(int(ts), "s")] if df is not None else None
        now = float(df["Close"].iloc[-1]) if df is not None else None
        outcome = "-"
        if entry and since is not None and len(since):
            filled = since[since["Low"] <= entry]
            if not len(filled):
                outcome = "not filled yet"
            else:
                after = since.loc[filled.index[0]:]
                hit_t = after.index[after["High"] >= t1]
                hit_s = after.index[after["Low"] <= stop]
                outcome = ("target 1 reached" if len(hit_t) and (not len(hit_s) or hit_t[0] <= hit_s[0]) else
                           "stopped out" if len(hit_s) else "open")
        rows.append((time.strftime("%d %b", time.localtime(ts)), name, action, data_money(price, cur),
                     data_money(now, cur) if now else "?", f"{(now / price - 1) * 100:+.1f}%" if now else "?", outcome))
    return md_table(("Date", "Asset", "Call", "Price then", "Now", "Change", "Outcome"), rows)


if __name__ == "__main__":
    import os
    import tempfile

    import pandas as pd
    import ishti.facts as F
    F._facts = F.Facts(os.path.join(tempfile.mkdtemp(), "t.db"))
    days = pd.bdate_range("2021-01-01", periods=1300)
    noise = np.random.default_rng(1).normal(0, 0.012, 1300)
    up = pd.DataFrame({"Close": 100 * np.exp(np.cumsum(0.0012 + noise))}, index=days)
    up["Open"], up["High"], up["Low"], up["Volume"] = up["Close"], up["Close"] * 1.01, up["Close"] * 0.99, 1e6
    t = technical(up)
    total, parts = score(t, 1, 0.5, backtest(up))
    p = plan(float(up["Close"].iloc[-1]), t, total)
    assert total >= 2 and p["action"] in ("BUY", "BUY ON A DIP") and p["stop"] < p["entry"] < p["t1"] < p["t2"], (total, parts, p)
    sim = simulate(up, p["entry"], p["stop"], p["t1"])
    assert 0 <= sim["p_target"] <= 1 and sim["m3"][0] < sim["m3"][1] < sim["m3"][2]
    down = up.copy()
    down["Close"] = 100 * np.exp(np.cumsum(-0.0012 + noise))
    down["High"], down["Low"] = down["Close"] * 1.01, down["Close"] * 0.99
    assert plan(float(down["Close"].iloc[-1]), technical(down), score(technical(down), -1, -0.5, backtest(down))[0])["action"] == "AVOID"
    units, cost = position(100_000, 100, 95)  # risk 1.5k / 5 per share = 300, but the 20% cap allows only 200
    assert units == 200 and cost == 20_000
    assert position(10_000, 100, 80) == (7, 700)  # 150 risk / 20 per share = 7.5 -> 7
    assert capital_of("how much OGDC should I buy with 2 lakh", "PKR") == (200_000, "PKR")
    assert capital_of("I have Rs 500,000 to invest", "PKR") == (500_000, "PKR") and capital_of("research nvidia with $3000", "PKR") == (3000, "USD")
    assert capital_of("should I buy OGDC", "PKR") is None and capital_of("research nvidia 2026 outlook", "USD") is None
    h = past(up)
    assert h["cagr"] > 0 and h["max_drawdown"] < 0 and len(h["years"]) >= 5
    print("[self-check] markets research OK - scoring, plan (entry/stop/targets), dip vs buy vs avoid, sizing (1.5% risk, 20% cap), "
          "capital parsing, backtest, simulation, history")
