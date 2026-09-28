"""Internet speed test: latency, jitter, download and upload against Cloudflare's free
speed endpoints (speed.cloudflare.com, no key, no account). Download pulls ~8 MB in streams,
upload sends ~2 MB of random bytes, all discarded server-side. The last 20 runs are kept in
ishti.db (speedtest table) - "speed test history". Nothing else about the PC is sent."""
import json
import re
import statistics
import time

from ishti.skills import trace

I = re.I
BASE = "https://speed.cloudflare.com"
MB_DOWN = 8   # MB to pull for the download measure (a balance of accuracy and time)
MB_UP = 2     # MB to send for the upload measure

TEST = re.compile(r"\b(?:speed\s*test|internet\s+speed|connection\s+speed|how\s+(?:fast|quick)\s+is\s+my\s+"
                  r"(?:internet|connection|wifi|net)|test\s+my\s+(?:internet|connection|wifi|net))\b", I)
HISTORY = re.compile(r"\b(?:speed\s*test|internet)\s+(?:history|log|stats?)\b|\b(?:past|previous)\s+speed\s*tests?\b", I)


def match(text):
    return bool(TEST.search(text))


def _meta():
    """Your IP's ISP and city as Cloudflare sees it (shown once per test, nothing stored but this)."""
    import requests
    r = requests.get(f"{BASE}/meta", timeout=10)
    d = r.json()
    return d.get("asOrganization", ""), d.get("city", "")


def latency(session=None):
    """(median_ms, jitter_ms) over 10 tiny requests on one warm connection (the first one also
    pays DNS + TLS, which would inflate the median on a cold start)."""
    import requests
    s = requests.Session()
    s.get(f"{BASE}/__down?bytes=0", timeout=15)  # warm-up: DNS, TCP, TLS done before measuring
    times = []
    for _ in range(10):
        t0 = time.perf_counter()
        s.get(f"{BASE}/__down?bytes=0", timeout=10)
        times.append((time.perf_counter() - t0) * 1000)
    return statistics.median(times), (statistics.stdev(times) if len(times) > 1 else 0.0)


def download(session=None):
    """Mbps from streaming MB_DOWN MB."""
    import requests
    total, t0 = 0, time.perf_counter()
    with requests.get(f"{BASE}/__down?bytes={MB_DOWN * 1_000_000}", stream=True, timeout=60) as r:
        for chunk in r.iter_content(256 * 1024):
            total += len(chunk)
    return total * 8 / (time.perf_counter() - t0) / 1e6


def upload(session=None):
    """Mbps from sending MB_UP MB of random bytes."""
    import requests
    payload = bytes(bytearray(MB_UP * 1_000_000))  # zeros compress nothing: Cloudflare discards the body
    t0 = time.perf_counter()
    requests.post(f"{BASE}/__up", data=payload, timeout=120)
    return MB_UP * 8 / (time.perf_counter() - t0)


def _save(ping, jitter, down, up, isp, city):
    from ishti.facts import get_facts
    f = get_facts()
    with f.lock, f.db:
        f.db.execute("CREATE TABLE IF NOT EXISTS speedtest(ts REAL, ping_ms REAL, jitter_ms REAL, down_mbps REAL, "
                     "up_mbps REAL, isp TEXT, city TEXT)")
        f.db.execute("INSERT INTO speedtest VALUES(?,?,?,?,?,?,?)", (time.time(), ping, jitter, down, up, isp, city))
        f.db.execute("DELETE FROM speedtest WHERE ts NOT IN (SELECT ts FROM speedtest ORDER BY ts DESC LIMIT 20)")


def history(n=10):
    from ishti.facts import get_facts
    f = get_facts()
    try:
        with f.lock:
            rows = f.db.execute("SELECT ts, ping_ms, down_mbps, up_mbps FROM speedtest ORDER BY ts DESC LIMIT ?",
                                (n,)).fetchall()
    except Exception:
        return "No speed tests saved yet - \"run a speed test\" to do the first one."
    if not rows:
        return "No speed tests saved yet - \"run a speed test\" to do the first one."
    lines = ["Your last speed tests:", ""]
    for ts, ping, down, up in rows:
        lines.append(f"- {time.strftime('%a %d %b %H:%M', time.localtime(ts))}: {down:.0f} Mbps down, "
                     f"{up:.0f} up, {ping:.0f} ms ping")
    return "\n".join(lines)


def run(text, session):
    if HISTORY.search(text):
        return {"reply": history(), "sources": []}
    trace(session, "speedtest", "measuring latency", status="start")
    ping, jitter = latency(session)
    trace(session, "speedtest", f"ping {ping:.0f} ms - downloading {MB_DOWN} MB", status="start")
    down = download(session)
    trace(session, "speedtest", f"{down:.0f} Mbps down - uploading {MB_UP} MB", status="start")
    up = upload(session)
    try:
        isp, city = _meta()
    except Exception:
        isp, city = "", ""
    trace(session, "speedtest", f"{down:.0f} Mbps down / {up:.0f} up", status="done")
    _save(ping, jitter, down, up, isp, city)
    where = f" ({isp}, {city})" if isp else ""
    verdict = "fast enough for anything" if down >= 50 else ("decent" if down >= 20 else
              ("slow - video calls may struggle" if down >= 5 else "very slow"))
    return {"reply": f"Your internet{where}: **{down:.0f} Mbps down**, {up:.0f} Mbps up, "
                     f"ping {ping:.0f} ms (jitter {jitter:.0f} ms) - {verdict}.",
            "sources": [{"id": 1, "title": "Cloudflare speed test", "url": BASE, "domain": "speed.cloudflare.com"}]}


if __name__ == "__main__":
    import tempfile
    import ishti.facts as F
    F._facts = F.Facts(__import__("os").path.join(tempfile.mkdtemp(), "t.db"))
    assert match("run a speed test") and match("how fast is my internet?")
    assert match("test my connection speed") and match("internet speed test")
    assert not match("what is the speed of light") and not match("download this file")
    assert "No speed tests saved yet" in history()
    ping, jitter = latency()
    assert 0 < ping < 2000 and jitter >= 0, (ping, jitter)  # a real network: this PC's line can be slow, the check is that it measured
    down = download()
    assert down > 1, down  # anything over 1 Mbps means the measurement path works
    _save(ping, jitter, down, 0.0, "Test ISP", "Peshawar")
    assert "Mbps down" in history()
    r = run("speed test", {})
    assert "**" in r["reply"] and "Mbps down" in r["reply"], r
    print(f"[self-check] speedtest OK - latency {ping:.0f} ms, download measured {down:.0f} Mbps, history saved")
