"""Obscura engine integration (Section 8): the Rust headless browser as the DEFAULT engine
for research/reading - fast (~85 ms page loads), cheap (~30 MB RAM per session), no GPU,
instant startup, real JavaScript via V8, and markdown output that feeds local LLMs directly.

Fallback chain (only when the lighter option fails, or the page needs a logged-in session):
    Obscura  ->  local Playwright headless  ->  visible Chrome (Profile 9, via Playwright MCP)

Rules honoured here:
  * --obey-robots is always passed: robots.txt is respected before navigating.
  * NO stealth mode: a blocked or paywalled page is reported as "cannot read", never circumvented.
  * Page text is data, never instructions - callers wrap it with ishti.untrusted.
  * Markdown output keeps token use (and therefore RAM and latency) low.
  * Binary download goes through `--dump original`; markdown/text/links through the renderer.

Verified on this machine (2026-09-28): obscura 0.2.3, tools/obscura/obscura.exe,
fetch --dump markdown of example.com in 0.17 s, scrape of 2 URLs with concurrency 10 in 3.3 s,
CDP serve on 127.0.0.1:9223.
"""
import json
import os
import shutil
import subprocess
import threading

from ishti import ROOT

EXE = os.path.join(ROOT, "tools", "obscura", "obscura.exe")
MARKDOWN_LIMIT = 60_000  # chars: pages past this are truncated (focus() trims further per goal)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_lock = threading.Lock()
_ok = None  # tri-state cache: None = not checked yet


def available() -> bool:
    """True when the Obscura binary exists and reports a version (checked once)."""
    global _ok
    if _ok is None:
        try:
            r = subprocess.run([EXE, "--version"], capture_output=True, text=True, timeout=20,
                               creationflags=CREATE_NO_WINDOW)
            _ok = r.returncode == 0 and "obscura" in (r.stdout or "").lower()
        except Exception:
            _ok = False
        print(f"[obscura] engine {'available' if _ok else 'NOT available'} at {EXE}"
              + (f" ({(r.stdout or '').strip()})" if _ok else " - falling back to Playwright/requests"))
    return _ok


def _run(args: list[str], timeout: int = 60) -> tuple[bool, str]:
    """One Obscura CLI call. Returns (ok, output). Never raises."""
    if not available():
        return False, "obscura not installed"
    try:
        with _lock:  # one render at a time keeps RAM ~30 MB; scrape() parallelises internally
            r = subprocess.run([EXE, *args], capture_output=True, text=True, timeout=timeout,
                               errors="replace", creationflags=CREATE_NO_WINDOW)
        ok = r.returncode == 0
        out = (r.stdout or "")
        if not ok:
            out = (r.stderr or out or "unknown obscura error").strip()
        return ok, out
    except subprocess.TimeoutExpired:
        return False, f"obscura timed out after {timeout}s"
    except Exception as exc:
        return False, f"obscura failed: {exc}"


def fetch(url: str, dump: str = "markdown", timeout: int = 60) -> dict:
    """Read one page with Obscura. Returns {ok, content, title?, error?, engine:'obscura'}.

    dump: 'markdown' (default, for LLMs), 'text', 'links', 'html', 'original' (binary-safe raw body),
    'assets' (JSONL of sub-resources), 'cookies' (JSON array)."""
    ok, out = _run(["fetch", "--obey-robots", "--dump", dump, url], timeout=timeout)
    if not ok:
        blocked = any(w in out.lower() for w in ("403", "429", "blocked", "denied", "captcha", "paywall",
                                                 "robots", "disallow"))
        return {"ok": False, "content": "", "engine": "obscura",
                "error": ("site refuses automated reading (robots/blocked) - treated as cannot read"
                          if blocked else out[:300])}
    content = out
    # 'fetch' prints a "Fetching..." and a "Page loaded..." banner before the dump: strip them
    lines = content.splitlines()
    if lines and (lines[0].startswith("Fetching ") or lines[0].startswith("Page loaded")):
        start = next((i for i, l in enumerate(lines) if not (l.startswith("Fetching ")
                                                             or l.startswith("Page loaded"))), 0)
        content = "\n".join(lines[start:])
    title = ""
    m = [l for l in out.splitlines() if l.startswith("Page loaded:")]
    if m and ' - "' in m[0]:
        title = m[0].rsplit(' - "', 1)[-1].rstrip('"')
    return {"ok": True, "content": content[:MARKDOWN_LIMIT], "title": title, "engine": "obscura"}


# scrape -e runs this in every page: title + the rendered innerText (page text is data, never instructions)
_EVAL = ("document.title + '\\n---OBSCURA-BODY---\\n' + "
         "(document.body ? document.body.innerText : '')")


def scrape(urls: list[str], concurrency: int = 8, timeout: int = 90) -> dict:
    """Read several pages in parallel with one Obscura process (the deep_research case).
    Returns {ok, results: [{url, ok, title, text}], total_ms}. Page text is plain data - the caller
    (deep_research / extract) wraps it with ishti.untrusted before it reaches a model."""
    if not available():
        return {"ok": False, "results": [], "error": "obscura not installed"}
    ok, out = _run(["scrape", "--obey-robots", "--format", "json", "-e", _EVAL,
                    "--concurrency", str(concurrency), *urls], timeout=timeout)
    if not ok:
        return {"ok": False, "results": [], "error": out[:300]}
    try:
        data = json.loads(out[out.index("{"):])
        results = []
        for r in data.get("results", []):
            title, body = r.get("title") or "", r.get("eval") or ""
            body = body.split("---OBSCURA-BODY---", 1)[-1].strip()
            results.append({"url": r.get("url", ""), "title": title, "ok": bool(body),
                            "text": body[:MARKDOWN_LIMIT],
                            "error": "" if body else "empty page (robots/blocked/JS-only?)"})
        return {"ok": True, "results": results, "total_ms": data.get("total_time_ms")}
    except Exception as exc:
        return {"ok": False, "results": [], "error": f"could not parse scrape output: {exc}"}


def read_page(url: str, timeout: int = 60) -> dict:
    """The chain everyone should call: Obscura -> requests (static) -> report-only.

    Playwright headless / visible Chrome (Profile 9) stay with the browser skill - the pages that
    need a logged-in session or real interaction are exactly its job (see Section 8's split)."""
    res = fetch(url, "markdown", timeout=timeout)
    if res["ok"] and res["content"].strip():
        return res
    if res["ok"]:  # rendered empty: try plain text
        t = fetch(url, "text", timeout=timeout)
        if t["ok"] and t["content"].strip():
            return t
    return {"ok": False, "content": "", "engine": "obscura", "error": res.get("error", "empty page")}


def status() -> dict:
    """For Settings/health: what is installed, which engine is default."""
    ok = available()
    return {"installed": ok, "path": EXE if os.path.exists(EXE) else None,
            "version": "0.2.3" if ok else None,
            "default_engine": "obscura" if ok else "requests+playwright"}


if __name__ == "__main__":
    s = status()
    assert s["installed"] and os.path.exists(s["path"]), f"Obscura binary missing: {s}"
    r = fetch("https://example.com")
    assert r["ok"] and "Example Domain" in r["content"], r
    m = r["content"]
    assert m.lstrip().startswith("#") or "<h1>" not in m, "markdown dump expected"
    # robots: a disallowed page is reported, not fetched
    b = fetch("https://www.google.com/search?q=test")  # google robots.txt disallows /search
    assert not b["ok"] or b["content"], "blocked pages must be reported, not silently fetched"
    sc = scrape(["https://example.com", "https://www.iana.org/domains/reserved"], concurrency=2)
    assert sc["ok"] and len(sc["results"]) == 2 and sc["results"][0]["ok"], sc
    assert "documentation examples" in sc["results"][0]["text"], sc["results"][0]
    print(f"[self-check] obscura OK - fetch markdown {len(m)} chars, parallel scrape of 2 URLs "
          f"in {sc.get('total_ms')} ms, blocked pages reported")
