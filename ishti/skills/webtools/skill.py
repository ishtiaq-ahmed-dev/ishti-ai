"""Web extras: download a file from a link, watch a page and announce when it changes, open several links at once,
pull the tables off a page (also saved as CSV), search the owner's Chrome bookmarks (Profile 10).
Programs and huge downloads ask first; page watching runs in the background every 15 minutes."""
import datetime as dt
import difflib
import hashlib
import json
import os
import re
import threading
import time
import webbrowser
from urllib.parse import unquote, urlparse

import requests

from ishti import ROOT
from ishti.skills import ask_first, trace

I = re.I
URL = re.compile(r"https?://[^\s<>\"')\]]+", I)
WATCHES = os.path.join(ROOT, "data", "watches.json")
EVERY_S = 15 * 60
RISKY_EXT = (".exe", ".msi", ".bat", ".cmd", ".ps1", ".scr", ".vbs", ".jar", ".apk", ".dll")
from ishti.skills.webtools import browser_data  # noqa: E402
BOOKMARKS = os.path.join(browser_data.profile_dir(), "Bookmarks")  # the main profile's (Profile 9) - was hard-coded Profile 10
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36"}
OPS = [
    ("watch_list", re.compile(r"\b(?:which|what|list)\s+(?:pages?|sites?)\s+(?:are\s+you\s+|am\s+i\s+)?watching\b|\bmy\s+watch(?:es|list)\b(?!.*\b(?:stock|share|crypto))", I)),
    ("unwatch", re.compile(r"\b(?:stop|don'?t)\s+watching\b|\bunwatch\b", I)),
    ("watch", re.compile(r"\b(?:watch|monitor|track|keep\s+an\s+eye\s+on)\b.*https?://|\b(?:tell|notify|alert)\s+me\s+when\s+.*https?://.*\bchanges?\b", I)),
    ("tables", re.compile(r"\b(?:get|extract|scrape|pull|grab)\s+(?:the\s+)?(?:tables?|data)\s+(?:from|on|off)\b.*https?://", I)),
    ("download", re.compile(r"\bdownload\b.*https?://", I)),
    ("open_many", re.compile(r"\bopen\b.*https?://\S+.*https?://", I)),
    ("bookmarks", re.compile(r"\bbookmarks?\b", I)),
]


def match(text):
    # "bookmark this page for me: <url>" is a browser task (the browser skill opens/reuses the tab);
    # webtools owns bookmark SEARCH (find my github bookmarks) and the bookmark file itself.
    # "this page" / "this site" with a URL is the browser's to open, not webtools' to search (checklist #44).
    if re.search(r"\b(?:bookmark|bookmarks?)\b", text, I) and re.search(r"https?://", text):
        return False  # browser opens the page; webtools only searches existing bookmarks
    return browser_data.match(text) or any(rx.search(text) for _, rx in OPS)


def run(text, session):
    if browser_data.match(text):  # cache / cookies / bookmark edits first: "delete the X bookmark" isn't a search
        from ishti.skills import ask_first
        trace(session, "web", "browser data")
        out = browser_data.run(text, session, ask_first)
        return out if isinstance(out, dict) or out is None else {"reply": out, "sources": []}
    for name, rx in OPS:
        if rx.search(text):
            trace(session, "web", name.replace("_", " "))
            out = globals()["op_" + name](text, session)
            return out if isinstance(out, dict) or out is None else {"reply": out, "sources": []}
    return None


# ---------------------------------------------------------------- download
def _filename(url, resp):
    cd = resp.headers.get("content-disposition", "")
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd, I)
    name = unquote(m.group(1)) if m else unquote(os.path.basename(urlparse(url).path)) or "download"
    return re.sub(r"[<>:\"/\\|?*]", "_", name)[:120]


def op_download(text, session):
    url = URL.search(text).group(0)
    from ishti.skills.folders.skill import known_folders
    folder = known_folders()["downloads"]
    try:
        head = requests.get(url, stream=True, timeout=20, headers=UA, allow_redirects=True)
        head.raise_for_status()
    except requests.RequestException as exc:
        return f"Couldn't reach that link: {type(exc).__name__}."
    name = _filename(url, head)
    size = int(head.headers.get("content-length") or 0)

    def fetch():
        from ishti.skills.folders.skill import free_name
        path = free_name(os.path.join(folder, name))
        got = 0
        with open(path, "wb") as f:
            for chunk in head.iter_content(1 << 20):
                f.write(chunk)
                got += len(chunk)
        return f"Downloaded {name} ({got / 2**20:.1f} MB) to {path}"
    if name.lower().endswith(RISKY_EXT) or size > 500 << 20:
        why = "it's a program that can run on your PC" if name.lower().endswith(RISKY_EXT) else f"it's {size / 2**30:.1f} GB"
        return ask_first(session, "webtools", f"Download {name} from {urlparse(url).netloc} ({why})", fetch)
    return fetch()


# ---------------------------------------------------------------- page watching
def _page_text(url):
    from tools.web.fetch import WebFetcher
    got = WebFetcher().fetch(url, max_chars=20000) or {}
    text = got.get("content") or ""
    if not text:
        text = re.sub(r"<[^>]+>", " ", requests.get(url, timeout=20, headers=UA).text)
    return re.sub(r"\s+", " ", text).strip()


def _load():
    try:
        with open(WATCHES, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _save(items):
    os.makedirs(os.path.dirname(WATCHES), exist_ok=True)
    with open(WATCHES, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=1)


def op_watch(text, session):
    url = URL.search(text).group(0)
    trace(session, "fetch", f"reading {urlparse(url).netloc} for a baseline", status="start")
    try:
        body = _page_text(url)
    except Exception as exc:
        return f"Couldn't read that page ({type(exc).__name__}), so I can't watch it."
    items = [w for w in _load() if w["url"] != url]
    items.append({"url": url, "hash": hashlib.sha256(body.encode()).hexdigest(), "text": body[:20000],
                  "added": dt.datetime.now().isoformat(timespec="minutes"), "checked": time.time()})
    _save(items)
    _start()
    return f"Watching {url}. I'll tell you when it changes (checked every {EVERY_S // 60} minutes)."


def op_watch_list(text, session):
    items = _load()
    return ("Watching:\n" + "\n".join(f"- {w['url']} (since {w['added']})" for w in items)) if items else "I'm not watching any pages."


def op_unwatch(text, session):
    m = URL.search(text)
    items = _load()
    keep = [w for w in items if m and w["url"] != m.group(0)] if m else []
    _save(keep)
    return f"Stopped watching {len(items) - len(keep)} page(s)."


def check_watches(announce):
    """One pass: re-read each watched page; on a change, announce a short diff and move the baseline."""
    items, changed = _load(), False
    for w in items:
        try:
            body = _page_text(w["url"])
        except Exception:
            continue
        h = hashlib.sha256(body.encode()).hexdigest()
        w["checked"] = time.time()
        if h != w["hash"]:
            old, new = w["text"].split(". "), body[:20000].split(". ")
            added = [s for s in difflib.ndiff(old, new) if s.startswith("+ ")][:3]
            announce(f"🔔 {urlparse(w['url']).netloc} changed: " + (" / ".join(a[2:120] for a in added) or "the page content is different")
                     + f" ({w['url']})")
            w["hash"], w["text"], changed = h, body[:20000], True
    _save(items) if items else None
    return changed


_started = {"on": False}


def _start():
    """Background watcher (only inside the running server, where announcements have somewhere to go)."""
    from ishti.dispatcher import HOOKS
    if _started["on"] or not HOOKS.get("emit"):
        return
    _started["on"] = True

    def loop():
        while True:
            time.sleep(EVERY_S)
            if _load():
                check_watches(lambda msg: HOOKS["emit"]("assistant_message", {"reply": msg}))
    threading.Thread(target=loop, daemon=True, name="page-watch").start()


# ---------------------------------------------------------------- tables, links, bookmarks
def _markdown(df, rows=15, cols=8):
    df = df.iloc[:rows, :cols].fillna("")
    # two-level headers ("City proper", "Population") read as one label, footnote marks like [a] dropped
    flat = lambda c: " / ".join(dict.fromkeys(str(p) for p in (c if isinstance(c, tuple) else (c,)) if not str(p).startswith("Unnamed")))
    head = [re.sub(r"\[\w+\]", "", flat(c))[:30] for c in df.columns]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(str(v).replace("|", "/")[:40] for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join(lines)


def op_tables(text, session):
    import io
    import pandas as pd
    url = URL.search(text).group(0)
    trace(session, "fetch", f"reading tables on {urlparse(url).netloc}", status="start")
    try:
        html = requests.get(url, timeout=25, headers=UA).text
        tables = [t for t in pd.read_html(io.StringIO(html)) if t.shape[0] >= 2]
    except (ValueError, requests.RequestException) as exc:
        return f"No readable tables on that page ({type(exc).__name__}). If it builds tables with JavaScript, ask me to open it in the browser."
    trace(session, "fetch", f"{len(tables)} tables", status="done")
    if not tables:
        return "That page has no data tables."
    tables.sort(key=lambda t: -t.size)
    folder = os.path.join(ROOT, "data", "tables")
    os.makedirs(folder, exist_ok=True)
    stem = re.sub(r"\W+", "_", urlparse(url).netloc + urlparse(url).path)[:60]
    paths = []
    for i, t in enumerate(tables[:3], 1):
        p = os.path.join(folder, f"{stem}_{i}.csv")
        t.to_csv(p, index=False)
        paths.append(p)
    return (f"{len(tables)} tables on the page; the biggest ({tables[0].shape[0]} rows × {tables[0].shape[1]} columns):\n\n"
            + _markdown(tables[0]) + "\n\nSaved as CSV:\n" + "\n".join(f"- {p}" for p in paths))


def op_open_many(text, session):
    urls = list(dict.fromkeys(URL.findall(text)))[:15]
    for u in urls:
        webbrowser.open_new_tab(u)
    return f"Opened {len(urls)} links in your browser."


def op_bookmarks(text, session):
    try:
        with open(BOOKMARKS, encoding="utf-8") as f:
            roots = json.load(f)["roots"]
    except (OSError, ValueError, KeyError):
        return f"I can't read the Chrome bookmarks ({BOOKMARKS})."
    marks = []

    def walk(node, path):
        if node.get("type") == "url":
            marks.append((node.get("name", ""), node.get("url", ""), path))
        for child in node.get("children", []):
            walk(child, path + ([node["name"]] if node.get("name") and node.get("type") == "folder" else []))
    for r in roots.values():
        if isinstance(r, dict):
            walk(r, [])
    q = re.sub(r"\b(?:find|search|show|list|my|the|bookmarks?|for|in|chrome|all|about|with)\b", " ", text, flags=I)
    words = [w for w in re.findall(r"\w+", q.lower()) if len(w) > 1]
    hits = [m for m in marks if all(w in (m[0] + " " + m[1]).lower() for w in words)] if words else marks
    if not hits:
        return f"No bookmark matches '{' '.join(words)}' ({len(marks)} bookmarks in Profile 10)."
    return (f"{len(hits)} bookmark(s)" + (f" matching '{' '.join(words)}'" if words else "") + ":\n"
            + "\n".join(f"- [{n or u}]({u})" + (f" ({'/'.join(p)})" if p else "") for n, u, p in hits[:20]))


if __name__ == "__main__":
    cases = {"download https://x.org/a.pdf": "download", "watch https://x.org/jobs for changes": "watch",
             "tell me when https://x.org/news changes": "watch", "which pages are you watching": "watch_list",
             "stop watching https://x.org/jobs": "unwatch", "get the table from https://x.org/list": "tables",
             "open https://a.com and https://b.com": "open_many", "find my github bookmarks": "bookmarks"}
    for t, want in cases.items():
        got = next((n for n, rx in OPS if rx.search(t)), None)
        assert got == want, (t, got, want)
    assert not match("open youtube") and not match("what's the weather")
    import pandas as pd
    md = _markdown(pd.DataFrame({"A": [1, 2], "B": ["x", None]}))
    assert md.splitlines()[0] == "| A | B |" and "| 2 |  |" in md, md
    print("[self-check] webtools OK - routing, table markdown")
