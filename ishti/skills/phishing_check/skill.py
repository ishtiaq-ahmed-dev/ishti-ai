"""Phishing & suspicious-link analysis (deterministic heuristics; never opens the link).

Signals: IP-literal hosts, punycode/homograph lookalikes, shortener domains, raw IPs,
excessive subdomains, credential-bait + urgency phrasing, mismatched display-vs-actual URLs.
Explains every signal and tells the owner how to verify out-of-band. Informational only.
"""
import ipaddress
import re
from urllib.parse import urlparse

I = re.I

URL = re.compile(r"(?P<url>https?://[^\s\"'<>]+|(?:[\w-]+\.)+[a-z]{2,}/[^\s\"'<>]*)", I)
CHECK = re.compile(r"^\s*(?:is\s+(?:this|the)\s+(?:link|url|email|site)\s+(?:safe|legit(imate)?|phishing|scam(?:my)?)|check\s+(?:this|the)\s+(?:link|url|email)|suspicious\s+link|scam\s+check|is\s+this\s+phishing)\b.*$", I | re.S)

SHORTENERS = {"bit.ly", "tinyurl.com", "t.co", "goo.gl", "is.gd", "cutt.ly", "rb.gy", "shorturl.at", "ow.ly"}
BRANDS = ["google", "facebook", "whatsapp", "instagram", "microsoft", "outlook", "apple", "icloud",
          "amazon", "paypal", "netflix", "ebay", "hbl", "meezan", "UBL", "jazzcash", "easypaisa",
          "daraz", "gmail", "yahoo", "binance", "coinbase"]
BAIT = re.compile(r"\b(?:verify|confirm|unlock|suspend\w*|disabled|limited|urgently|immediately|within\s+\d+\s+(?:hours?|minutes?)|"
                  r"account\s+(?:holder|has\s+been|will\s+be)|click\s+(?:here|the\s+link\s+below)|login|sign\s+in|"
                  r"password|otp|one[\s-]time\s+(?:code|password)|winner|prize|lottery|refund|kyc|update\s+your\s+billing)\b", I)
URGENT = re.compile(r"\b(?:urgent|asap|final\s+(?:warning|notice)|last\s+chance|act\s+now|expire[ds]?|24\s+hours)\b", I)


def _host(url):
    try:
        host = urlparse(url if "//" in url else "https://" + url).netloc.split("@")[-1]
        return host.split(":")[0].lower()
    except ValueError:
        return ""


def _signals(text):
    out = []
    m = URL.search(text)
    url = m.group("url") if m else ""
    host = _host(url) if url else ""
    if host:
        out.append(f"Link found: `{url}` (host: `{host}`)")
        try:
            ipaddress.ip_address(host)
            out.append("🚨 Host is a raw IP address — legitimate services use domain names.")
        except ValueError:
            pass
        if host.startswith("xn--") or "xn--" in host:
            out.append("🚨 Punycode (xn--) in the host — can imitate real letters (homograph attack).")
        for brand in BRANDS:
            if brand.lower() in host and not host.endswith((brand.lower() + ".com", brand.lower() + ".pk",
                                                           brand.lower() + ".net", brand.lower() + ".org")):
                out.append(f"🚨 Lookalike domain: contains '{brand}' but is not the real {brand} domain.")
        if host in SHORTENERS:
            out.append("⚠️ URL shortener — the real destination is hidden. Expand it first.")
        if host.count(".") >= 4:
            out.append(f"⚠️ Many subdomains ({host.count('.')} dots) — common in phishing.")
        if url.startswith("http://") and any(b in host for b in ("login", "pay", "bank", "verify")):
            out.append("🚨 Plain HTTP on a credential-looking page — no encryption.")
    hits = BAIT.findall(text)
    if hits:
        out.append("🚨 Credential/urgency bait phrases: " + ", ".join(sorted(set(h.lower() for h in hits))[:8]))
    if URGENT.search(text):
        out.append("⚠️ Pressure language (" + URGENT.search(text).group(0) + ") — urgency is a classic lever.")
    if re.search(r"\[(?:https?://|www\.)[^\]]+\]\(\s*https?://[^\s)]+\)", text):
        out.append("⚠️ Displayed link text differs from the underlying URL (markdown) — hover before trusting.")
    if not out:
        out.append("No phishing signals found by heuristics — heuristics are not a guarantee.")
    return out


def match(text):
    return bool(CHECK.match(text.strip()) and (URL.search(text) or len(text.strip()) > 60))


def run(text, session):
    if not match(text):
        return None
    signals = _signals(text)
    n_risk = sum(1 for s in signals if s.startswith("🚨"))
    verdict = ("LIKELY PHISHING — do not click, do not enter credentials."
               if n_risk >= 2 else
               "SUSPICIOUS — treat as untrusted; verify before clicking." if n_risk == 1 or
               any(s.startswith("⚠️") for s in signals) else
               "No obvious phishing signals — still verify anything asking for credentials.")
    body = "\n".join(f"- {s}" for s in signals)
    advice = ("\n\nAlways verify by the official app or by typing the address yourself — never by the "
              "link in the message. If it claims to be your bank/service, call the number on their site.")
    return {"reply": f"**{verdict}**\n{body}{advice}", "sources": []}


if __name__ == "__main__":
    assert match("is this link safe: http://192.168.1.4/hbl-login/verify.php")
    assert match("check this email: Urgent! Your account will be suspended, verify your password at http://hbl-secure.tk/login")
    assert not match("what's the weather")
    r = run("is this link safe: http://hbl-login-verify.tk/update/password.php", {})
    assert "PHISHING" in r["reply"], r
    r2 = run("check this link: https://github.com/h4ckf0r0day/obscura", {})
    assert "No obvious" in r2["reply"], r2
    r3 = run("is this link safe: https://bit.ly/abc123", {})
    assert "shortener" in r3["reply"].lower(), r3
    print("[self-check] phishing_check OK - lookalike/IP/bait/shortener signals, verdicts, advice")
