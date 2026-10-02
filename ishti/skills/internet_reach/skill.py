"""internet_reach - ISHTI's capability layer for the internet (WP-REACH).

Design ported from Agent-Reach (MIT, pin in VENDOR_PINS.md): a *capability layer*, not a wrapper -
it selects, probes and routes to backends; actual reads go through local tools or plain HTTP.
Original ISHTI code; no agent-reach source text copied.

Contract of this module:
  run(text, session) -> reply dict | None    (ISHTI skill convention)
  doctor(as_json=False) -> list[dict]        per-channel probe results (offline probes only)
  safe_url(url) -> (ok, reason)              SSRF guard (agent-reach transcribe.py behaviour, re-derived)
  run_cmd(argv) -> (rc, out)                 UTF-8 subprocess helper (Windows cp1252 fix, REACH-26)

Safety:
  - OPT-IN login-state channels are OFF until the owner runs `reach enable <channel>`; enabling is a
    purely local config write (data/reach/channels.json) and sends NOTHING.
  - Probes never touch the network except channels declared online; doctor on its own is local-only.
  - Fetched/external text is fenced as data (ishti.untrusted) before it enters a reply.
  - No auto-install, ever: repair output is advice; installing a tool is a T2 action for the owner.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
from urllib.parse import urlparse

from ishti.untrusted import fence  # transcripts of fetched audio are untrusted data

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


REPO_VERSION = "1.0.0"
STATE_DIR = os.path.join("data", "reach")
CHANNELS_FILE = os.path.join(STATE_DIR, "channels.json")

# --------------------------------------------------------------------------- registry
# Ordered primary -> fallback backends per channel (agent-reach's core idea: swapping the
# order is how a platform gets fixed, never rewriting code). probe: "bin:<name>" or "py:<module>".
CHANNELS: dict[str, dict] = {
    "web": {"label": "Web pages", "backends": [{"name": "requests+bs4", "probe": "py:bs4"}],
            "hint": "read pages directly; jina reader is the OPT-IN fallback (r.jina.ai)"},
    "search": {"label": "Web search (local SearXNG)", "backends": [{"name": "searxng-local", "probe": "py:requests"}],
               "hint": "governed loopback-only search via ishti.gateway (OD-14); cloud search backends are OPT-IN and OFF"},
    "youtube": {"label": "YouTube", "backends": [{"name": "yt-dlp", "probe": "bin:yt-dlp"},
                                                  {"name": "requests", "probe": "py:requests"}],
                "hint": "subtitles/search via yt-dlp (ISHTI planners skill uses the same)"},
    "rss": {"label": "RSS/Atom", "backends": [{"name": "stdlib-xml", "probe": "py:xml"},
                                               {"name": "feedparser", "probe": "py:feedparser"}],
            "hint": "local parse, no key"},
    "github": {"label": "GitHub public reads", "backends": [{"name": "gh CLI", "probe": "bin:gh"},
                                                            {"name": "https api", "probe": "py:requests"}],
               "hint": "public reads work unauthenticated; 'gh auth status' unlocks more (owner-run)"},
    "v2ex": {"label": "V2EX", "backends": [{"name": "requests", "probe": "py:requests"}], "hint": "anonymous"},
    "xueqiu": {"label": "Xueqiu stocks", "backends": [{"name": "requests", "probe": "py:requests"}],
               "hint": "fallback backend for the markets skill"},
    "xiaoyuzhou": {"label": "Xiaoyuzhou podcasts", "backends": [{"name": "requests", "probe": "py:requests"}],
                   "hint": "audio is transcribed LOCALLY (media skill, faster-whisper)"},
    "bilibili": {"label": "Bilibili (anonymous search)", "backends": [{"name": "bili-cli", "probe": "bin:bili"},
                                                                      {"name": "requests", "probe": "py:requests"}],
                 "hint": "login-free search only; subtitles need a login-state backend"},
    "transcribe": {"label": "Link/file transcription", "backends": [{"name": "media skill (local whisper)", "probe": "mod:ishti.skills.media.skill"}],
                   "hint": "SSRF-guarded download then LOCAL transcription - no cloud STT"},
    # ---- OPT-IN: login-state / third-party-cloud channels: OFF until enabled (N38) ----
    "twitter": {"label": "Twitter/X", "opt_in": True, "backends": [{"name": "twitter-cli", "probe": "bin:twitter"},
                                                                   {"name": "opencli", "probe": "bin:opencli"}],
                "risk": "cookie/login-state: platform may restrict the account (use a burner account)"},
    "reddit": {"label": "Reddit", "opt_in": True, "backends": [{"name": "opencli", "probe": "bin:opencli"},
                                                               {"name": "rdt-cli", "probe": "bin:rdt-cli"}],
               "risk": "login-state only (anonymous API blocked)"},
    "facebook": {"label": "Facebook", "opt_in": True, "backends": [{"name": "opencli", "probe": "bin:opencli"}],
                 "risk": "reuses your existing browser session - ToS/ban risk"},
    "instagram": {"label": "Instagram", "opt_in": True, "backends": [{"name": "opencli", "probe": "bin:opencli"}],
                  "risk": "reuses your existing browser session - ToS/ban risk"},
    "xiaohongshu": {"label": "XiaoHongShu", "opt_in": True, "backends": [{"name": "opencli", "probe": "bin:opencli"}],
                    "risk": "cookie/login-state - ToS/ban risk; ISHTI never extracts cookies itself"},
    "linkedin": {"label": "LinkedIn", "opt_in": True, "backends": [{"name": "linkedin-mcp", "probe": "bin:linkedin-mcp-server"},
                                                                   {"name": "jina reader", "probe": "py:requests"}],
                 "risk": "public pages read anonymously; profile/job reads need login (owner-configured MCP)"},
    "boss": {"label": "Boss zhipin jobs", "opt_in": True, "backends": [{"name": "cdp-chrome", "probe": "bin:chrome"}],
             "risk": "dedicated logged-in Chrome session - manual owner login only"},
    "exa": {"label": "Exa semantic search", "opt_in": True, "backends": [{"name": "mcporter", "probe": "bin:mcporter"}],
            "risk": "third-party cloud search (keyless MCP) - local SearXNG is the default instead"},
}

REPAIR = {
    "bin:yt-dlp": "python -m pip install -U yt-dlp   (T2: say yes to install)",
    "bin:gh": "install GitHub CLI from https://cli.github.com (owner action)",
    "bin:opencli": "OpenCLI is NOT installed - login-state channels stay OFF (N38)",
    "bin:bili": "optional: install bili-cli for richer Bilibili reads (T2)",
    "py:feedparser": "python -m pip install feedparser (optional; stdlib fallback works)",
}


# --------------------------------------------------------------------------- helpers
def _decode(data: bytes) -> str:
    """Windows consoles emit cp1252/mbcs while tools like gh emit UTF-8: try UTF-8 first, fall back to
    the ANSI codepage, never raise (REACH-26 - the UTF-8 subprocess fix, re-derived)."""
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        if os.name == "nt":
            try:
                return data.decode("mbcs")
            except Exception:
                pass
        return data.decode("utf-8", errors="replace")


def run_cmd(argv: list[str], timeout: int = 30) -> tuple[int, str]:
    """Bytes in, decoded text out; never a shell string (command-injection safety + REACH-26 fix)."""
    try:
        p = subprocess.run(argv, capture_output=True, timeout=timeout)
        out = _decode(p.stdout or b"") + _decode(p.stderr or b"")
        return p.returncode, out.strip()
    except FileNotFoundError:
        return 127, f"{argv[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, f"{' '.join(argv)} timed out after {timeout}s"
    except Exception as exc:  # never crash the doctor
        return 1, str(exc)


def _probe(spec: str) -> bool:
    if spec.startswith("bin:"):
        return shutil.which(spec[4:]) is not None
    if spec.startswith("py:"):
        mod = spec[3:]
        if mod == "xml":
            return True  # stdlib
        return importlib.util.find_spec(mod) is not None
    if spec.startswith("mod:"):
        return importlib.util.find_spec(spec[4:]) is not None
    return False


def enabled_channels() -> dict:
    try:
        with open(CHANNELS_FILE, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def set_enabled(channel: str, on: bool) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    data = enabled_channels()
    data[channel] = bool(on)
    with open(CHANNELS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def doctor(as_json: bool = False):
    """Probe every channel; local-only. Returns list of dicts:
    {channel, label, state, active, backends: [{name, ok}], hint/risk, repair}."""
    enabled = enabled_channels()
    rows = []
    for name, spec in CHANNELS.items():
        opt = bool(spec.get("opt_in"))
        row = {"channel": name, "label": spec["label"], "opt_in": opt,
               "hint": spec.get("hint", ""), "risk": spec.get("risk", ""),
               "state": "", "active": None, "backends": [], "repair": ""}
        if opt and not enabled.get(name):
            row["state"] = "opt-in-off"           # default: inert, nothing probed for use
            for b in spec["backends"]:
                row["backends"].append({"name": b["name"], "ok": False, "probe": b["probe"]})
            rows.append(row)
            continue
        active = None
        for b in spec["backends"]:
            ok = _probe(b["probe"])
            row["backends"].append({"name": b["name"], "ok": ok, "probe": b["probe"]})
            if ok and active is None:
                active = b["name"]
                if b["probe"] in REPAIR:
                    pass
        if active:
            row["state"] = "ok" if not opt else "ok (OPT-IN enabled)"
            row["active"] = active
        else:
            row["state"] = "missing"
            first = spec["backends"][0]["probe"]
            row["repair"] = REPAIR.get(first, "no backend found - install or configure one (T2)")
        rows.append(row)
    return rows


def safe_url(url: str) -> tuple[bool, str]:
    """SSRF guard for anything we download (re-derived from agent-reach transcribe.py rules):
    http(s) only, no credentials in the URL, no localhost/.local, no private/loopback/link-local/reserved IP."""
    try:
        u = urlparse(url)
    except Exception:
        return False, "unparseable URL"
    if u.scheme not in ("http", "https"):
        return False, f"scheme '{u.scheme or '(none)'}' not allowed (http/https only)"
    if "@" in (u.netloc or ""):
        return False, "credentials in URL are not allowed"
    host = (u.hostname or "").lower()
    if not host:
        return False, "no host"
    if host in ("localhost",) or host.endswith(".local") or host.endswith(".localhost"):
        return False, "local hosts are not allowed"
    try:
        import ipaddress
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False, f"{host} is a private/reserved address"
    except ValueError:
        pass  # hostname, not an IP - fine
    return True, "ok"


def download(url: str, dest_dir: str, max_bytes: int = 50 * 1024 * 1024) -> str:
    """Size-capped streaming download with a RE-VALIDATED final URL (redirects can dodge safe_url)."""
    import requests
    ok, why = safe_url(url)
    if not ok:
        raise ValueError(f"blocked: {why}")
    os.makedirs(dest_dir, exist_ok=True)
    with requests.get(url, stream=True, timeout=(5, 60),
                      headers={"User-Agent": "ISHTI-internet_reach/1.0"}) as r:
        r.raise_for_status()
        ok, why = safe_url(str(r.url))          # after redirects
        if not ok:
            raise ValueError(f"blocked after redirect: {why}")
        ctype = (r.headers.get("content-type") or "").split(";")[0].strip()
        ext = {"audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/mp4": ".m4a",
               "audio/ogg": ".ogg", "video/mp4": ".mp4"}.get(ctype, ".bin")
        name = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(urlparse(str(r.url)).path)) or "download"
        path = os.path.join(dest_dir, name if name.endswith(ext) else name + ext)
        total = 0
        with open(path, "wb") as f:
            for chunk in r.iter_content(64 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    f.close()
                    os.remove(path)
                    raise ValueError(f"file exceeds {max_bytes // (1024 * 1024)} MB cap")
                f.write(chunk)
        if total == 0:
            raise ValueError("empty response")
        return path


_search_impl = None      # tests inject a fake; production goes through the governed gateway


def web_search(query: str, transport=None) -> dict:
    """One web search through the GOVERNED gateway (matrix OD-14): loopback-only endpoint,
    audited risk=external, results fenced as untrusted data, cloud_calls == 0 by construction.
    Never raises."""
    impl = _search_impl
    if impl is not None:
        return impl(query, transport=transport)
    try:
        from ishti import gateway
    except Exception as exc:  # pragma: no cover - import guard only
        return {"ok": False, "error": f"gateway unavailable ({type(exc).__name__})"}
    return gateway.search(query, transport=transport)


def _search_reply(query: str) -> dict:
    """Render a governed-search result for chat: the local backend's name, the fenced results,
    and an honest refusal/outage message (nothing is ever silently sent to the cloud)."""
    res = web_search(query)
    if not isinstance(res, dict):
        res = {"ok": False, "error": "search returned no answer"}
    if not res.get("ok"):
        why = str(res.get("error") or "no results")
        if res.get("refused"):
            return {"reply": f"Refused, nothing was sent: {why}."}
        return {"reply": f"Local search did not answer ({why}). The SearXNG container may be down; "
                         f"nothing left this PC."}
    try:
        from ishti import gateway
        fenced = gateway.as_text(res)
    except Exception:
        fenced = ""
    n = len(res.get("results") or [])
    head = f"Local SearXNG search for {query!r}: {n} result(s)."
    tail = "(searched locally through the governed gateway - cloud calls: 0)"
    if fenced:
        return {"reply": f"{head}\n\n{fenced}\n\n{tail}"}
    rows = [f"- {r.get('title', '')} {r.get('url', '')}".rstrip() for r in (res.get("results") or [])[:8]]
    return {"reply": "\n".join([head, *rows, "", tail])}


def format_json(text: str) -> str:
    """Pull the first JSON object/array out of arbitrary text (fences, prose, log tails) and pretty-print it."""
    s = text.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.M).strip()
    start = min([i for i in (s.find("{"), s.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("no JSON found")
    dec = json.JSONDecoder()
    obj, _ = dec.raw_decode(s[start:])
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False)


_DOCTOR_HDR = "channel | state | active backend"


def _doctor_report(as_json: bool) -> str:
    rows = doctor(as_json=as_json)
    if as_json:
        return json.dumps(rows, indent=2, ensure_ascii=False)
    ok = sum(1 for r in rows if r["state"].startswith("ok"))
    off = sum(1 for r in rows if r["state"] == "opt-in-off")
    lines = [f"Reach doctor: {ok} ok, {off} OPT-IN off, {len(rows) - ok - off} need attention.",
             "| channel | state | active | note |", "|---|---|---|---|"]
    for r in rows:
        note = r["repair"] or r["hint"] or r["risk"]
        lines.append(f"| {r['channel']} | {r['state']} | {r['active'] or '-'} | {note or '-'} |")
    return "\n".join(lines)


def _transcribe(target: str, session) -> dict:
    """Link -> SSRF-guarded download -> LOCAL transcription through the media skill. File -> media skill."""
    if target.startswith(("http://", "https://")):
        ok, why = safe_url(target)
        if not ok:
            return {"reply": f"I did not fetch that link: {why} (safety guard)."}
        try:
            path = download(target, os.path.join(STATE_DIR, "downloads"))
        except Exception as exc:
            return {"reply": f"The download failed, nothing was saved: {exc}"}
    else:
        path = target.strip().strip("\"'")
        if not os.path.isfile(path):
            return {"reply": f"No such file: {path}"}
    try:
        from ishti.skills.media.skill import run as media_run
        out = media_run(f"transcribe {path}", session if isinstance(session, dict) else {})
        if isinstance(out, dict) and str(out.get("reply", "")).strip():
            # the transcript is untrusted audio-derived content: fence it as data (injection defence)
            reply = fence(str(out["reply"]), source="local transcription of " + os.path.basename(path))
            return {"reply": reply + "\n\n(transcribed locally - no audio left this PC)"}
        return {"reply": "The transcriber produced nothing - the audio may be silent or unsupported."}
    except Exception as exc:
        return {"reply": f"Local transcription failed: {exc}"}


def run(text: str, session):
    t = str(text or "").strip()
    low = t.lower()
    if not t:
        return None

    # --- doctor / status
    if re.search(r"\b(reach\b.*\bdoctor|doctor\b.*\breach|reach status|channels status|which channels|reach json)", low):
        as_json = bool(re.search(r"\bjson\b", low))
        from ishti.skills import trace
        try:
            trace(session, "internet_reach", "probing channels (local probes only)", status="done")
        except Exception as _exc:
            swallowed('ishti/skills/internet_reach/skill.py:run', _exc)
        return {"reply": _doctor_report(as_json)}

    # --- enable/disable an OPT-IN channel (purely local write, sends nothing)
    m = re.match(r"^reach\s+(enable|disable)\s+([a-z0-9_]+)\s*$", low)
    if m:
        verb, chan = m.group(1), m.group(2)
        if chan not in CHANNELS:
            return {"reply": f"Unknown channel '{chan}'. Known: {', '.join(sorted(CHANNELS))}."}
        if verb == "enable":
            set_enabled(chan, True)
            risk = CHANNELS[chan].get("risk", "")
            return {"reply": f"Enabled '{chan}' locally (nothing was sent). Risk you accepted: {risk or 'n/a'}. "
                             f"Run 'reach doctor' to see its backend."}
        set_enabled(chan, False)
        return {"reply": f"Disabled '{chan}'. It is inert again."}

    # --- local web search through the governed gateway (OD-14: cloud search is OPT-IN and off)
    m = re.search(r"\breach\b(?:\s*:\s*|\s+)search\s+(.+)$", low) or re.search(r"\bsearch\s+locally\s+for\s+(.+)$", low)
    if m:
        return _search_reply(t[t.lower().find(m.group(1)):].strip())

    # --- transcribe a link or file (local STT)
    m = re.search(r"transcribe\s+(\S+)", low)
    if m:
        return _transcribe(t[t.lower().find(m.group(1)):].strip(), session)

    # --- format json (the verb AND something json-shaped in the text)
    if re.search(r"\bformat\b", low) and ("{" in t or "[" in t or re.search(r"\bjson\b", low)):
        try:
            return {"reply": "```\n" + format_json(t) + "\n```"}
        except json.JSONDecodeError:
            return {"reply": "That does not parse as JSON - nothing was changed."}
        except ValueError:
            return {"reply": "I can't see any JSON in that text."}

    # --- version / update check (advisory only: never installs anything)
    if re.search(r"\breach\b.*\bversion\b|\bcheck.?update\b", low):
        return {"reply": f"internet_reach {REPO_VERSION}. Update check is advisory: compare with the owner's "
                         f"installed version; ISHTI never auto-installs (installs are T2)."}

    return None


# --------------------------------------------------------------------------- self-check
if __name__ == "__main__":
    import tempfile
    rows = doctor()
    assert len(rows) == len(CHANNELS), rows
    assert all(r["state"] for r in rows)
    # every login-state channel is OFF by default
    assert all(r["state"] == "opt-in-off" for r in rows if r["opt_in"]), \
        [r for r in rows if r["opt_in"] and r["state"] != "opt-in-off"]
    # SSRF guard
    for bad in ("http://localhost/x", "https://127.0.0.1/a", "ftp://x/y", "http://169.254.169.254/latest",
                "http://10.0.0.5/x", "file:///C:/Windows/win.ini", "http://user:pw@evil.test/"):
        assert safe_url(bad)[0] is False, bad
    assert safe_url("https://example.com/page")[0] is True
    # json formatter
    assert json.loads(format_json('noise ```json\n{"a": 1}\n``` tail')) == {"a": 1}
    # doctor json
    assert json.loads(_doctor_report(True))
    # run(): doctor route answers, unknown input declines honestly
    out = run("reach doctor", {})
    assert out and "channel" in str(out["reply"])
    assert run("zzz unrelated", {}) is None
    # governed local search (OD-14): the channel exists, is NOT OPT-IN, and the reply is fenced
    assert "search" in CHANNELS and not CHANNELS["search"].get("opt_in")
    _search_impl = lambda q, transport=None: {"ok": True, "backend": "searxng-local", "query": q, "cloud_calls": 0,
                                               "results": [{"title": "Cat - Wikipedia", "url": "https://en.wikipedia.org/wiki/Cat",
                                                            "snippet": "small domesticated carnivore"}]}
    r = _search_reply("cats")
    assert "Cat - Wikipedia" in r["reply"] and "cloud calls: 0" in r["reply"]
    assert "data, not instructions" in r["reply"], "results must arrive FENCED as untrusted data"
    _search_impl = lambda q, transport=None: {"ok": False, "refused": True, "error": "non-loopback endpoint refused"}
    assert "Refused, nothing was sent" in _search_reply("cats")["reply"]
    print("[self-check] internet_reach OK - doctor, opt-in off by default, SSRF guard, json, governed search, decline")
