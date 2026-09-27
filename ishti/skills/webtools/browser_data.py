"""Chrome data housekeeping for the owner's own profiles: cache, cookies, bookmarks.

Chrome keeps these files open and rewrites them while it runs, and automation can't open chrome:// settings
(checked: 'Detached while handling command'). So changes are made on the files with Chrome closed - always after
the owner's yes, and the question says Chrome will close:
  clear the cache          -> Cache, Code Cache, GPUCache folders of the profile (regenerated; you stay signed in)
  clear all cookies        -> the profile's Cookies database (signs you out of every site - said in the question)
  clear cookies for a site -> that site's storage + script-visible cookies, live in the page (no restart; Chrome
                              doesn't let pages touch HttpOnly cookies - said in the reply)
  add / delete a bookmark  -> the profile's Bookmarks file (checksum dropped; Chrome accepts that)"""
import json
import os
import re
import shutil
import subprocess
import time

from ishti import CONFIG

I = re.I
CHROME = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser(r"~\AppData\Local")), "Google", "Chrome", "User Data")
CACHE_DIRS = ("Cache", "Code Cache", "GPUCache", os.path.join("Service Worker", "CacheStorage"))
OPS = [
    ("cache", re.compile(r"\b(?:clear|delete|empty|wipe)\s+(?:the\s+|my\s+)?(?:browser\s+|chrome\s+)?cache\b", I)),
    ("cookies_site", re.compile(r"\b(?:clear|delete|remove)\s+(?:the\s+)?cookies\s+(?:for|of|on|from)\s+(?P<site>[\w.-]+\.[a-z]{2,})", I)),
    ("cookies_all", re.compile(r"\b(?:clear|delete|remove)\s+(?:all\s+(?:my\s+)?|my\s+|the\s+)?(?:browser\s+|chrome\s+)?cookies\b(?!\s+(?:for|of|on|from))", I)),
    ("bookmark_add", re.compile(r"\b(?:add|save|create)\s+(?:a\s+)?bookmark\s+(?:for\s+|to\s+)?(?P<url>https?://\S+|[\w-]+\.[a-z]{2,}\S*)"
                                r"(?:\s+(?:called|named|as)\s+[\"']?(?P<name>[^\"']+?)[\"']?)?\s*$", I)),
    ("bookmark_delete", re.compile(r"\b(?:delete|remove)\s+(?:the\s+)?(?P<name>.+?)\s+bookmark\b|\b(?:delete|remove)\s+(?:the\s+)?bookmark\s+"
                                   r"(?:for\s+|called\s+|named\s+)?[\"']?(?P<name2>[^\"']+?)[\"']?\s*$", I)),
]


def match(text):
    return any(rx.search(text) for _, rx in OPS)


def profile_dir(server=None):
    """The folder of the Chrome profile a skill uses (config mcp_servers args --profile-dir-name)."""
    from ishti.skills.browser.skill import profile_for
    args = CONFIG["mcp_servers"].get(server or profile_for("browser"), {}).get("args", [])
    name = next((args[i + 1] for i, a in enumerate(args[:-1]) if a == "--profile-dir-name"), "Default")
    return os.path.join(CHROME, name)


def close_chrome(wait=8):
    """Ask Chrome to close (tabs come back on its next start if the owner has 'continue where you left off')."""
    subprocess.run(["taskkill", "/IM", "chrome.exe"], capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for _ in range(wait * 2):
        if "chrome.exe" not in subprocess.run(["tasklist", "/FI", "IMAGENAME eq chrome.exe"], capture_output=True, text=True,
                                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout:
            return True
        time.sleep(0.5)
    subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    time.sleep(1)
    return True


def _size(path):
    return sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(path) for f in fs if os.path.isfile(os.path.join(r, f)))


def clear_cache(pdir):
    freed = 0
    for d in CACHE_DIRS:
        p = os.path.join(pdir, d)
        if os.path.isdir(p):
            freed += _size(p)
            shutil.rmtree(p, ignore_errors=True)
    return freed


def clear_cookies(pdir):
    gone = []
    for rel in (os.path.join("Network", "Cookies"), os.path.join("Network", "Cookies-journal"), "Cookies", "Cookies-journal"):
        p = os.path.join(pdir, rel)
        if os.path.isfile(p):
            os.remove(p)
            gone.append(rel)
    return gone


def _bookmarks(pdir):
    with open(os.path.join(pdir, "Bookmarks"), encoding="utf-8") as f:
        return json.load(f)


def _save_bookmarks(pdir, data):
    data.pop("checksum", None)  # Chrome recomputes it; a stale one would be worse than none
    with open(os.path.join(pdir, "Bookmarks"), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=3, ensure_ascii=False)


def add_bookmark(pdir, url, name):
    data = _bookmarks(pdir)
    bar = data["roots"]["bookmark_bar"]
    ids = [int(n.get("id", 0)) for n in _walk(data["roots"])]
    stamp = str(int((time.time() + 11644473600) * 1_000_000))  # Chrome's microseconds since 1601
    bar.setdefault("children", []).append({"date_added": stamp, "id": str(max(ids + [0]) + 1), "name": name, "type": "url", "url": url})
    _save_bookmarks(pdir, data)


def delete_bookmark(pdir, name):
    data, removed = _bookmarks(pdir), []

    def prune(node):
        kids = node.get("children", [])
        keep = []
        for k in kids:
            if k.get("type") == "url" and name.lower() in (k.get("name", "") + " " + k.get("url", "")).lower():
                removed.append(k.get("name") or k.get("url"))
            else:
                prune(k)
                keep.append(k)
        if "children" in node:
            node["children"] = keep
    for root in data["roots"].values():
        if isinstance(root, dict):
            prune(root)
    if removed:
        _save_bookmarks(pdir, data)
    return removed


def _walk(roots):
    for r in roots.values():
        if isinstance(r, dict):
            stack = [r]
            while stack:
                n = stack.pop()
                yield n
                stack += n.get("children", [])


def run(text, session, ask_first):
    for name, rx in OPS:
        if m := rx.search(text):
            return globals()["op_" + name](m, text, session, ask_first)
    return None


def op_cache(m, text, session, ask_first):
    pdir = profile_dir()
    size = sum(_size(os.path.join(pdir, d)) for d in CACHE_DIRS if os.path.isdir(os.path.join(pdir, d))) / 1e6
    return ask_first(session, "webtools", f"Close Chrome and delete {os.path.basename(pdir)}'s cache ({size:.0f} MB) - "
                                          "you stay signed in",
                     lambda: close_chrome() and f"Cleared {clear_cache(pdir) / 1e6:.0f} MB of cache from {os.path.basename(pdir)}. "
                                                "Chrome is closed - open it again when you like.")


def op_cookies_all(m, text, session, ask_first):
    pdir = profile_dir()
    return ask_first(session, "webtools", f"Close Chrome and delete ALL cookies of {os.path.basename(pdir)} - this signs you "
                                          "out of every website in that profile (WhatsApp Web, Gmail...)",
                     lambda: close_chrome() and (f"Deleted the cookies of {os.path.basename(pdir)} ({', '.join(clear_cookies(pdir)) or 'none found'}). "
                                                 "You'll need to sign in to sites again."))


def op_cookies_site(m, text, session, ask_first):
    from ishti.skills.browser import skill as browser
    site = m["site"].lower().removeprefix("www.")
    js = ("() => { const n = document.cookie.split(';').map(c => c.split('=')[0].trim()).filter(Boolean);"
          " for (const c of n) for (const d of [location.hostname, '.' + location.hostname.split('.').slice(-2).join('.')])"
          " document.cookie = c + '=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/; domain=' + d;"
          " localStorage.clear(); sessionStorage.clear(); return n.length; }")

    def go():
        with browser.LOCK:
            browser.use_server(browser.profile_for("browser"), session)
            browser._call("browser_navigate", {"url": f"https://{site}"})
            out = browser._call("browser_evaluate", {"function": js})
        n = re.search(r"### Result\s*\n(\d+)", out)
        return (f"Cleared {site}'s site storage and {n.group(1) if n else 'its'} script-visible cookie(s). Chrome doesn't let "
                "pages remove HttpOnly cookies (usually the sign-in ones) - say \"clear all cookies\" for those.")
    return ask_first(session, "webtools", f"Clear the cookies and site data of {site}", go)


def op_bookmark_add(m, text, session, ask_first):
    url = m["url"] if m["url"].startswith("http") else "https://" + m["url"]
    name = (m["name"] or re.sub(r"^https?://(www\.)?", "", url).split("/")[0]).strip()
    pdir = profile_dir()
    return ask_first(session, "webtools", f"Close Chrome and add the bookmark \"{name}\" ({url}) to {os.path.basename(pdir)}'s bookmarks bar",
                     lambda: close_chrome() and (add_bookmark(pdir, url, name) or f"Bookmarked {name} on the bookmarks bar."))


def op_bookmark_delete(m, text, session, ask_first):
    name = (m["name"] or m["name2"]).strip(" \"'")
    pdir = profile_dir()
    hits = [n.get("name") for n in _walk(_bookmarks(pdir)["roots"]) if n.get("type") == "url"
            and name.lower() in (n.get("name", "") + " " + n.get("url", "")).lower()]
    if not hits:
        return f"No bookmark matches \"{name}\"."
    return ask_first(session, "webtools", f"Close Chrome and delete {len(hits)} bookmark(s): {', '.join(hits[:5])}",
                     lambda: close_chrome() and f"Deleted: {', '.join(delete_bookmark(pdir, name))}.")


if __name__ == "__main__":
    import tempfile
    for t, want in {"clear the browser cache": "cache", "clear all my cookies": "cookies_all", "clear cookies for daraz.pk": "cookies_site",
                    "add a bookmark for https://imsciences.edu.pk called IMSciences": "bookmark_add",
                    "delete the IMSciences bookmark": "bookmark_delete"}.items():
        assert next((n for n, rx in OPS if rx.search(t)), None) == want, (t, want)
    assert not match("clear my calendar") and not match("show my bookmarks")
    pdir = tempfile.mkdtemp()
    json.dump({"checksum": "x", "roots": {"bookmark_bar": {"children": [{"id": "5", "name": "Old", "type": "url", "url": "https://old.com"}],
                                                           "id": "1", "type": "folder", "name": "Bookmarks bar"},
                                          "other": {"children": [], "id": "2", "type": "folder"}}}, open(os.path.join(pdir, "Bookmarks"), "w"))
    add_bookmark(pdir, "https://imsciences.edu.pk", "IMSciences")
    data = _bookmarks(pdir)
    assert "checksum" not in data and data["roots"]["bookmark_bar"]["children"][-1]["name"] == "IMSciences"
    assert data["roots"]["bookmark_bar"]["children"][-1]["id"] == "6"
    assert delete_bookmark(pdir, "old.com") == ["Old"] and len(_bookmarks(pdir)["roots"]["bookmark_bar"]["children"]) == 1
    os.makedirs(os.path.join(pdir, "Cache", "Cache_Data"))
    open(os.path.join(pdir, "Cache", "Cache_Data", "f_0001"), "wb").write(b"x" * 5000)
    assert clear_cache(pdir) == 5000 and not os.path.exists(os.path.join(pdir, "Cache"))
    os.makedirs(os.path.join(pdir, "Network"))
    open(os.path.join(pdir, "Network", "Cookies"), "w").write("db")
    assert clear_cookies(pdir) == [os.path.join("Network", "Cookies")]
    print("[self-check] browser_data OK - routing, bookmarks add/delete (ids, checksum), cache and cookie files")
