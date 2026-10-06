"""Online fallback: a job this PC can't do locally (remove a background, upscale, convert a rare format...) done with
a free website in the owner's main Chrome.

  1. the tool: one that worked for this kind of job before (facts entity "online_fallbacks_used"), else a web search
     for "best free online <job> tool" -> the first result that is a tool, not a list article or a video
  2. T2: nothing is uploaded until the owner says yes - the question names the file, its size and the website
  3. the browser engine uploads that file (only that one: browser.APPROVED_UPLOADS), does the job, downloads
  4. the result is moved to data/online, the tool is recorded for next time, and the owner is asked to check it"""
import difflib
import json
import os
import re
import shutil
import time
import urllib.parse
import urllib.request

from ishti import ROOT
from ishti.test_mode import scratch_path
from ishti.facts import get_facts
from ishti.skills import ask_first, trace
from ishti.skills.browser import skill as browser

OUT = scratch_path(os.path.join(ROOT, "data", "online"))
REGISTRY = "online_fallbacks_used"
TRIGGER = re.compile(r"\b(?:online|web(?:site)?|free)\s+(?:tool|service|converter|app|site|website)s?\b|"
                     r"\b(?:use|with|using|try|find)\s+(?:an?\s+|some\s+)?(?:free\s+)?(?:online|website)\b|"
                     r"\b(?:do|convert|process)\s+(?:it|this|that)\s+online\b", re.I)
FILE = re.compile(r'"([^"]+\.\w{2,5})"|([a-zA-Z]:[\\/][^\n"<>|?*]+?\.\w{2,5})\b')
NOT_A_TOOL = re.compile(r"reddit\.|youtube\.|quora\.|medium\.com|wikipedia\.|facebook\.|/(?:blog|best|top|list)[-/]|"
                        r"\b(?:best|top\s*\d*|alternatives?|vs)\b", re.I)


def match(text):
    return bool(TRIGGER.search(text))


def parse(text):
    """(file path or None, the job in a few words)."""
    m = FILE.search(text)
    path = (m.group(1) or m.group(2)).strip() if m else None
    job = FILE.sub(" ", text)
    job = TRIGGER.sub(" ", job)
    job = re.sub(r"\b(?:please|can\s+you|could\s+you|for\s+me|to|on|from|of|in|this|the|file|image|an?|and|then|tools?|"
                  r"download\s+(?:it|the\s+result))\b", " ", job, flags=re.I)
    return path, " ".join(job.split()).strip(" .,:")[:60].lower()


def remembered(job):
    """A tool that did this kind of job before: {"url", "site", "uses"} or None."""
    known = {k: v for k, v in get_facts().get(REGISTRY).items() if k != "entity"}
    best = difflib.get_close_matches(job, list(known), n=1, cutoff=0.6)
    return json.loads(known[best[0]]) if best else None


def search_tool(job):
    """First search result that is a tool page for the job: {"url", "site", "title"} or None."""
    q = urllib.parse.quote_plus(f"free online {job} tool no sign up")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:8890/search?q={q}&format=json", timeout=20) as r:
            results = json.load(r).get("results", [])
    except Exception:
        return None
    return pick(results)


def pick(results):
    for r in results:
        url, title = r.get("url", ""), r.get("title", "")
        if url.startswith("https://") and not NOT_A_TOOL.search(url) and not NOT_A_TOOL.search(title):
            return {"url": url, "site": urllib.parse.urlparse(url).netloc.removeprefix("www."), "title": title}
    return None


def record(job, tool):
    prev = remembered(job) or {}
    get_facts().set(REGISTRY, job, json.dumps({"url": tool["url"], "site": tool["site"], "uses": prev.get("uses", 0) + 1,
                                               "last": time.strftime("%Y-%m-%d")}))


def run(text, session):
    path, job = parse(text)
    if not path:
        return {"reply": "Which file should I use? Give me its full path (e.g. D:\\photos\\cat.png).", "sources": []}
    if not os.path.isfile(path):
        return {"reply": f"I can't find {path}.", "sources": []}
    if not job:
        return {"reply": f"What should the online tool do with {os.path.basename(path)}?", "sources": []}
    tool = remembered(job)
    if tool:
        trace(session, "registry", f"{tool['site']} did this before ({tool['uses']}x)")
    else:
        trace(session, "search", f"looking for a free online tool to {job}", status="start")
        tool = search_tool(job)
        if not tool:
            return {"reply": f"I couldn't find a free online tool to {job}.", "sources": []}
        trace(session, "search", f"best match: {tool['site']}", status="done", url=tool["url"])
    size = os.path.getsize(path) / 1e6
    question = (f"I can't {job} on this PC. {tool['site']} can do it for free - but that means uploading "
                f"{os.path.basename(path)} ({size:.1f} MB) to {tool['site']}, so the file leaves this PC. Upload it")
    return ask_first(session, "online_fallback", question, lambda: _do(path, job, tool, session))


def _do(path, job, tool, session):
    browser.APPROVED_UPLOADS.add(path)
    try:
        since = time.time()
        out = browser.run(f"On this page: click the upload / choose file button, then upload the file with "
                          f"browser_file_upload, paths [\"{path}\"]. Then {job}. When the result is ready, click its "
                          f"download button.", session, server=browser.profile_for("online_fallback"), start_url=tool["url"])
        if session.get("pending"):  # the site wants a click that needs the owner's yes: the browser engine asks
            return out["reply"]
        got = browser.wait_download(since, timeout=60)
    finally:
        browser.APPROVED_UPLOADS.discard(path)
    if not got:
        return f"{out['reply']} No result reached {browser.DOWNLOADS}."
    os.makedirs(OUT, exist_ok=True)
    dest = os.path.join(OUT, os.path.basename(got))
    shutil.move(got, dest)
    record(job, tool)
    return f"Done with {tool['site']}: saved {dest}. Please check it looks right - I'll reuse {tool['site']} next time."


if __name__ == "__main__":
    import tempfile
    import ishti.facts
    ishti.facts._facts = ishti.facts.Facts(os.path.join(tempfile.mkdtemp(), "facts.db"))  # never the owner's memory
    assert match("use an online tool to remove the background from D:/x.png") and match("do it online")
    assert not match("remove the background from D:/x.png") and not match("what's online today")
    assert parse('use an online tool to remove the background from "D:/pics/cat.png"') == ("D:/pics/cat.png", "remove background")
    assert parse("upscale D:\\pics\\cat.jpg with a free online tool")[0] == "D:\\pics\\cat.jpg"
    results = [{"url": "https://www.reddit.com/r/x", "title": "What tool?"},
               {"url": "https://zapier.com/blog/best-bg-removers/", "title": "The 7 best background removers"},
               {"url": "https://www.remove.bg/upload", "title": "Remove Background from Image for Free"}]
    assert pick(results) == {"url": "https://www.remove.bg/upload", "site": "remove.bg",
                             "title": "Remove Background from Image for Free"}
    assert remembered("remove background") is None
    record("remove background", pick(results))
    record("remove background", pick(results))
    again = remembered("remove the background")
    assert again["site"] == "remove.bg" and again["uses"] == 2, again
    f = os.path.join(tempfile.mkdtemp(), "cat.png")
    open(f, "wb").write(b"x" * 2000)
    s = {"pending": None}
    out = run(f"remove the background from {f} with an online tool", s)
    assert out["reply"].endswith("(yes/no)") and "remove.bg" in out["reply"] and s["pending"]["skill"] == "online_fallback"
    assert f not in browser.APPROVED_UPLOADS  # nothing may be uploaded before the yes
    assert browser._risky("browser_file_upload", {"paths": ["C:/secret.txt"]})  # never approved: asks
    browser.APPROVED_UPLOADS.add("C:/ok.png")
    assert browser._risky("browser_file_upload", {"paths": ["C:/ok.png"]}) is None
    print("[self-check] online_fallback OK - routing, file/job parsing, tool pick, registry reuse, asks before upload")
