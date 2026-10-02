"""Probe how Flowise 3.0.5's /apikey endpoint authenticates (status + body, no secrets printed)."""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flowise_wire import BASE, EMAIL, PASSWORD, login_cookie

jar = login_cookie()
print("cookie names:", [c.split("=")[0] for c in jar.split("; ")])


def probe(path, method="GET", payload=None, extra=None):
    data = json.dumps(payload).encode() if payload is not None else None
    h = {"Content-Type": "application/json", "Cookie": jar, "X-Requested-With": "XMLHttpRequest"}
    h.update(extra or {})
    req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode("utf-8", "replace")
            print(f"{method} {path} -> {r.status}: {body[:160]}")
            return body
    except urllib.error.HTTPError as e:
        print(f"{method} {path} -> {e.code}: {e.read().decode('utf-8', 'replace')[:160]}")
    return ""


probe("/api/v1/apikey")
probe("/api/v1/apikey", "POST", {"keyName": "ishti"})
probe("/api/v1/apikey", "POST", {"keyName": "ishti"}, {"Authorization": "Bearer ishti-local-key"})
