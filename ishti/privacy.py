"""Privacy enforcement: nothing leaves this PC unless the owner asked for it.

Owner requirement: no model, library or program may share the owner's data with a third party. The only internet traffic
allowed is what the owner explicitly asks for through a skill whose job is the web (scope.net in its manifest), a download
the owner starts in Settings, or a remote channel the owner turned on (Telegram). Everything else is refused AND recorded.

Four layers, all here:
  harden()   environment variables set before any heavy import: no telemetry/analytics, Hugging Face offline, no update
             pings from the tools we spawn (npm, pip, Ollama cloud). Called from ishti/__init__.py.
  install()  the EGRESS GUARD. Patches socket.socket.connect/connect_ex/sendto, socket.getaddrinfo/gethostbyname/create_connection
             and asyncio's Windows connect: a connection to a non-loopback, non-LAN address is allowed only when
               - the master switch (`internet`, data/privacy.json) is ON, and
               - a skill that declares the host in manifest scope.net is running (ishti.scope's skill context, reused:
                 audit.current()["skill"], inherited by worker threads), or allow("reason") is active (owner-started
                 downloads), or the owner enabled that channel (Telegram).
             Telemetry hosts (TELEMETRY_DENY) are refused even inside a skill whose scope.net is "*".
             A library thread with no skill context (a telemetry or update-check thread) is therefore refused.
  ledger     data/outbound.log: one JSON line per external connection attempt - ts, host, port, who, allowed, reason.
             Never a request body, query, URL path or header. Rotates at 1 MB, keeps 2 files.
  snapshot() what GET /api/privacy returns (ishti/privacy_api.py).

Limits (stated in docs/SAFETY.md): this is Python-level. Native extensions that open their own sockets (curl_cffi, Rust
wheels, grpc), child processes (Ollama, Node/Playwright, FFmpeg, Docker/SearXNG, the browser) cannot be hooked; they are
covered by environment settings, by the Ollama/WebView2 flags and by tools/gate_no_cloud.py, which samples their sockets."""
import contextlib
import importlib.abc
import importlib.util
import ipaddress
import json
import os
import socket
import sys
import threading
import time

from ishti import ROOT
from ishti.test_mode import scratch_path

DATA = os.path.join(ROOT, "data")
SETTINGS_PATH = scratch_path(os.path.join(DATA, "privacy.json"))
LEDGER_PATH = scratch_path(os.path.join(DATA, "outbound.log"))
LEDGER_MAX = 1_000_000

# ---------------------------------------------------------------- hardening (environment)
# Forced on purpose (not setdefault): a stray `ANONYMIZED_TELEMETRY=True` in the owner's shell must not re-enable anything.
HARDEN_ENV = {
    "HF_HUB_DISABLE_TELEMETRY": "1", "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",  # the stored HF token is never sent unasked
    "ANONYMIZED_TELEMETRY": "False",                                           # chromadb -> posthog
    "DO_NOT_TRACK": "1", "SCARF_NO_ANALYTICS": "true", "GRADIO_ANALYTICS_ENABLED": "False",
    "SCRAPEGRAPHAI_TELEMETRY_ENABLED": "false",                                # (the library ignores its own flag; the guard blocks the host)
    "OTEL_SDK_DISABLED": "true", "CREWAI_DISABLE_TELEMETRY": "true", "CREWAI_DISABLE_TRACKING": "true",
    "LANGCHAIN_TRACING_V2": "false", "LANGCHAIN_TRACING": "false", "LANGSMITH_TRACING": "false",
    "WANDB_DISABLED": "true", "WANDB_MODE": "disabled", "NEXT_TELEMETRY_DISABLED": "1", "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
    "NO_ALBUMENTATIONS_UPDATE": "1",
    "PIP_DISABLE_PIP_VERSION_CHECK": "1",                                      # pip would ask pypi.org for its own version
    "npm_config_update_notifier": "false", "npm_config_audit": "false", "npm_config_fund": "false",
    "npm_config_prefer_offline": "true",                                       # npm: use the cache, no registry round-trip
    "OLLAMA_NO_CLOUD": "1",                                                    # an Ollama that WE start: no ollama.com cloud models/web search
}
# Chromium/WebView2 switches for the dashboard window. The dashboard is 127.0.0.1 only, so the resolver rule makes every
# non-loopback hostname fail to resolve inside the browser: nothing in WebView2 (sync, component updates, Safe Browsing,
# pings, telemetry endpoints) can reach a server even if a flag below is ignored.
WEBVIEW2_ARGS = ("--disable-features=ElasticOverscroll,OptimizationHints,Translate,AutofillServerCommunication,"
                 "CertificateTransparencyComponentUpdater,MediaRouter,msSmartScreenProtection "
                 "--disable-background-networking --disable-sync --disable-component-update --no-pings "
                 "--disable-domain-reliability --disable-breakpad --disable-client-side-phishing-detection "
                 "--disable-default-apps --no-first-run --safebrowsing-disable-auto-update "
                 '--host-resolver-rules="MAP * ~NOTFOUND , EXCLUDE 127.0.0.1 , EXCLUDE localhost"')
_applied = {}


def _hf_cache_exists():
    hub = os.path.join(os.environ.get("HF_HOME") or os.path.join(os.path.expanduser("~"), ".cache", "huggingface"), "hub")
    try:
        return any(n.startswith("models--") for n in os.listdir(hub))
    except OSError:
        return False


def harden():
    """Set the privacy environment. Idempotent, stdlib only, safe to call before any other import."""
    env = dict(HARDEN_ENV)
    if _hf_cache_exists():  # the weights are on disk: never ask the hub anything (downloads use allow(), below)
        env["HF_HUB_OFFLINE"] = env["TRANSFORMERS_OFFLINE"] = "1"
    for k, v in env.items():
        os.environ[k] = v
    _applied.update(env)
    return dict(_applied)


def webview2_env():
    """Environment for the desktop window process (desktop/launcher.py applies it before the WebView2 control starts)."""
    args = WEBVIEW2_ARGS
    if os.environ.get("ISHTI_DEBUG_PORT"):   # test hook (tests/test_launcher_window.py): the env var replaces pywebview's own flags, so keep this one
        args += f" --remote-debugging-port={int(os.environ['ISHTI_DEBUG_PORT'])}"
    return {"WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": args}


# ---------------------------------------------------------------- owner setting: the master switch
_sw = {"mtime": None, "checked": 0.0, "internet": True}
_sw_lock = threading.Lock()


def internet():
    """True when the owner lets owner-requested web skills reach the internet (default). Re-read at most every 2 s so a
    change made in another process (CLI, tests) applies without a restart."""
    now = time.monotonic()
    if now - _sw["checked"] < 2.0:
        return _sw["internet"]
    with _sw_lock:
        _sw["checked"] = now
        try:
            mt = os.stat(SETTINGS_PATH).st_mtime
            if mt != _sw["mtime"]:
                with open(SETTINGS_PATH, encoding="utf-8") as f:
                    _sw["internet"], _sw["mtime"] = json.load(f).get("internet", True) is not False, mt
        except (OSError, ValueError):
            _sw["internet"], _sw["mtime"] = True, None  # no/invalid file = the default
    return _sw["internet"]


def set_internet(on):
    if not isinstance(on, bool):
        raise ValueError("internet must be true or false")
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    tmp = SETTINGS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"internet": on}, f)
    os.replace(tmp, SETTINGS_PATH)
    _sw.update(internet=on, checked=time.monotonic(), mtime=os.stat(SETTINGS_PATH).st_mtime)
    return on


# ---------------------------------------------------------------- the ledger (host/port/who only)
_led_lock = threading.Lock()
_last = {}
_totals = {"allowed": None, "blocked": None}


def _ctx():
    from ishti import audit
    return audit.current()


def _count_existing():
    a = b = 0
    for p in (LEDGER_PATH + ".1", LEDGER_PATH):
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    a, b = (a + 1, b) if '"allowed": true' in line else (a, b + 1)
        except OSError:
            pass
    return a, b


def _log(host, port, who, allowed, reason):
    """Append one row; identical rows within 1 s are counted but not rewritten (a telemetry retry loop must not fill the disk)."""
    with _led_lock:
        if _totals["allowed"] is None:
            _totals["allowed"], _totals["blocked"] = _count_existing()
        _totals["allowed" if allowed else "blocked"] += 1
        key, now = (host, who, allowed), time.time()
        if now - _last.get(key, 0) < 1.0:
            return
        _last[key] = now
        if len(_last) > 500:
            _last.clear()
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)), "host": host, "port": port,
               "who": who, "allowed": allowed, "reason": reason}
        try:
            from ishti import audit
            # This metadata-only first-party ledger is not a skill output. Drop
            # only the skill identity for this exact write, never for the action.
            with audit.context(skill=None):
                os.makedirs(os.path.dirname(LEDGER_PATH), exist_ok=True)
                if os.path.exists(LEDGER_PATH) and os.path.getsize(LEDGER_PATH) > LEDGER_MAX:
                    os.replace(LEDGER_PATH, LEDGER_PATH + ".1")
                with open(LEDGER_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            pass  # the ledger must never break the action itself


def recent(n=100):
    rows = []
    for p in (LEDGER_PATH + ".1", LEDGER_PATH):
        try:
            with open(p, encoding="utf-8") as f:
                rows += f.readlines()[-n:]
        except OSError:
            pass
    out = []
    for line in rows[-n:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


# ---------------------------------------------------------------- who is asking, and may they?
# Telemetry / analytics / crash-report endpoints: refused everywhere, even where scope.net is "*" or allow() is active.
TELEMETRY_DENY = (
    "i.posthog.com", "app.posthog.com", "ingest.sentry.io", "api.segment.io", "cdn.segment.com", "api.mixpanel.com",
    "api-js.mixpanel.com", "api.amplitude.com", "api2.amplitude.com", "browser-intake-datadoghq.com",
    "http-intake.logs.datadoghq.com", "scarf.sh", "telemetry.crewai.com", "sgai-oss-tracing.onrender.com",
    "api.smith.langchain.com", "eu.api.smith.langchain.com", "google-analytics.com", "analytics.google.com",
    "googletagmanager.com", "stats.g.doubleclick.net", "app-measurement.com", "bugsnag.com", "clarity.ms",
    "vortex.data.microsoft.com", "events.data.microsoft.com", "telemetry.ollama.com")
LOCAL_SUFFIXES = (".local", ".lan", ".home", ".internal", ".localdomain", ".home.arpa", ".localhost")
_STACK_SKIP = {"socket", "ssl", "http", "urllib", "urllib3", "requests", "httpx", "httpcore", "anyio", "asyncio", "threading",
               "concurrent", "selectors", "ishti", "contextlib", "functools", "queue", "email", "__main__", "sniffio"}


class EgressBlocked(PermissionError):
    """An outbound connection ISHTI refuses to make (nothing was sent)."""


def _in(host, domains):
    return any(host == d or host.endswith("." + d) for d in domains)


def _kind(host):
    """'local' (loopback, LAN, multicast, unspecified), 'external' (a public IP or a DNS name), per the host string."""
    h = host.split("%")[0].strip("[]").lower().rstrip(".")
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        local = h == "localhost" or "." not in h or h.endswith(LOCAL_SUFFIXES)
        return "local" if local else "external"
    ip = getattr(ip, "ipv4_mapped", None) or ip
    return "local" if (ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast or ip.is_unspecified
                       or ip == ipaddress.ip_address("255.255.255.255")) else "external"


def _is_ip(h):
    try:
        ipaddress.ip_address(h.split("%")[0].strip("[]"))
        return True
    except ValueError:
        return False


_manifest_net = {}


def skill_net():
    """{skill: [hosts]} for every skill whose manifest scope.net is not empty (ports stripped, like scope.check_url)."""
    if not _manifest_net:
        base = os.path.join(ROOT, "ishti", "skills")
        for d in sorted(os.listdir(base)) if os.path.isdir(base) else []:
            try:
                with open(os.path.join(base, d, "manifest.json"), encoding="utf-8") as f:
                    m = json.load(f)
            except (OSError, ValueError):
                continue
            net = [str(h).split(":")[0].lower() for h in (m.get("scope") or {}).get("net", [])]
            _manifest_net[m.get("name", d)] = net
    return {k: v for k, v in _manifest_net.items() if v}


def _scope_net(skill):
    try:  # reuse scope.py's manifest reader (the source of truth for the call-level check)
        from ishti import scope
        return [str(h).split(":")[0].lower() for h in scope.scope_of(skill).get("net", [])]
    except Exception:
        return []


def _channel(host):
    """Remote channels the owner switched on: Telegram's API host only, only while policy.telegram_enabled."""
    if _in(host, ("telegram.org",)):
        try:
            from ishti.channels import policy
            return "channel:telegram" if policy.load().get("telegram_enabled") else ""
        except Exception:
            return ""
    return ""


def _caller():
    """The first frame outside the plumbing: which library is dialling (e.g. 'posthog'). A package name, nothing else."""
    f = sys._getframe(3)
    while f:
        top = (f.f_globals.get("__name__") or "?").split(".")[0]
        if top not in _STACK_SKIP and top not in sys.stdlib_module_names:
            return top
        f = f.f_back
    return "?"


@contextlib.contextmanager
def inference(endpoint):
    """Narrow grant for the selected model endpoint, never a general download grant.

    Keep this context alive until a streaming response is closed. Redirects must be
    disabled by the client. The Internet switch and telemetry denylist still win.
    """
    from urllib.parse import urlsplit
    from ishti import audit
    url = urlsplit(endpoint)
    host = (url.hostname or "").lower().rstrip(".")
    local = host in ("localhost", "127.0.0.1", "::1")
    if not host or url.username or url.password or url.query or url.fragment or url.scheme not in ("http", "https"):
        raise ValueError("The model endpoint must be a URL without credentials, query or fragment.")
    if url.scheme != "https" and not local:
        raise ValueError("Remote model endpoints require HTTPS.")
    port = url.port or (443 if url.scheme == "https" else 80)
    if not internet():
        raise EgressBlocked("Cloud inference is disabled while the Internet switch is OFF.")
    with audit.context(inference_endpoint=(host, port)):
        yield


def decide(host, port=None):
    """-> (allowed, who, reason) for an external host in the current thread's context."""
    h = host.lower().rstrip(".")
    ctx = _ctx()
    skill, grant = ctx.get("skill"), ctx.get("privacy_allow")
    who = skill or (f"owner:{grant}" if grant else "")
    if _in(h, TELEMETRY_DENY):
        return False, who or f"library:{_caller()}", "known telemetry/analytics endpoint"
    if not internet():
        return False, who or f"library:{_caller()}", "internet switch is OFF"
    endpoint = ctx.get("inference_endpoint")
    if endpoint:
        if h == endpoint[0] and (port is None or port == endpoint[1]):
            return True, "model-provider", "explicitly selected inference endpoint"
        return False, "model-provider", "outside selected inference endpoint"
    if grant:
        return True, f"owner:{grant}", "owner-started download"
    if skill:
        net = _scope_net(skill)
        if "*" in net or _in(h, net):
            return True, skill, "declared in the skill's scope.net"
        return False, skill, f"outside {skill}'s scope.net {net}"
    channel = _channel(h)
    if channel:
        return True, channel, "channel the owner enabled"
    return False, f"library:{_caller()}", "no skill or owner action is running"


_orig = {}
_tl = threading.local()
_resolved = {}  # ip -> hostname it was resolved from (so a connect to an IP can be matched to scope.net)


def _remember(host, results):
    try:
        names = _tl.__dict__.setdefault("names", {})
        for r in results:
            names[r[4][0]] = _resolved[r[4][0]] = host.lower()
        if len(_resolved) > 2000:
            _resolved.clear()
    except Exception:
        pass


def _enforce(host, port, via):
    """Raise EgressBlocked / ScopeError when this connection is not allowed; log external attempts either way."""
    if not isinstance(host, str) or host in ("127.0.0.1", "localhost", "::1") or _kind(host) == "local":
        return
    if getattr(_tl, "busy", False):  # decide() must never recurse into itself (it opens files)
        return
    _tl.busy = True
    try:
        name = getattr(_tl, "names", {}).get(host) or _resolved.get(host) or host
        allowed, who, reason = decide(name, port)
        _log(name, port, who, allowed, reason)
        if allowed:
            return
        skill = _ctx().get("skill")
        if skill and reason.startswith("outside"):
            from ishti import scope
            scope._deny(skill, f"connect to {name}:{port}", reason)  # same wording + audit row as the call-level check
        raise EgressBlocked(f"ISHTI blocked an outbound connection to {name}:{port} ({reason}); nothing was sent. "
                            f"See Privacy in the dashboard or data/outbound.log.")
    finally:
        _tl.busy = False


def _addr(address):
    return (address[0], address[1]) if isinstance(address, tuple) and len(address) >= 2 else (None, None)


def install():
    """Install the egress guard once. Safe to call from every entry point."""
    if _orig:
        return
    S = socket.socket
    for name in ("connect", "connect_ex"):
        orig = getattr(S, name)
        _orig[name] = orig

        def make(orig):
            def guarded(self, address, *a):
                if self.family in (socket.AF_INET, socket.AF_INET6):
                    _enforce(*_addr(address), "connect")
                return orig(self, address, *a)
            return guarded
        setattr(S, name, make(orig))
    _orig["sendto"] = S.sendto

    def sendto(self, data, *args):  # UDP without connect(): sendto(data, addr) or sendto(data, flags, addr)
        if self.family in (socket.AF_INET, socket.AF_INET6) and args and isinstance(args[-1], tuple):
            _enforce(*_addr(args[-1]), "sendto")
        return _orig["sendto"](self, data, *args)
    S.sendto = sendto

    _orig["getaddrinfo"], _orig["gethostbyname"] = socket.getaddrinfo, socket.gethostbyname

    def getaddrinfo(host, *a, **k):  # a DNS lookup of a non-local name is itself a disclosure: judge it before resolving
        if isinstance(host, str) and _kind(host) == "external" and not _is_ip(host):  # an IP literal needs no lookup: connect judges it
            _enforce(host, a[0] if a else None, "dns")
        res = _orig["getaddrinfo"](host, *a, **k)
        if isinstance(host, str):
            _remember(host, res)
        return res
    socket.getaddrinfo = getaddrinfo

    def gethostbyname(host):
        if _kind(host) == "external":
            _enforce(host, None, "dns")
        return _orig["gethostbyname"](host)
    socket.gethostbyname = gethostbyname

    if sys.platform == "win32":  # asyncio's default Windows loop connects through ConnectEx, not socket.connect
        try:
            from asyncio import windows_events
            _orig["iocp_connect"] = windows_events.IocpProactor.connect

            def iocp_connect(self, conn, address):
                if conn.family in (socket.AF_INET, socket.AF_INET6):
                    _enforce(*_addr(address), "connect")
                return _orig["iocp_connect"](self, conn, address)
            windows_events.IocpProactor.connect = iocp_connect
        except (ImportError, AttributeError):
            pass
    if "ishti.manage" in sys.modules:
        _wire_owner_downloads()
    else:  # manage.py is imported lazily by the Settings endpoints: wire it the moment it loads (no boot cost here)
        sys.meta_path.insert(0, _manage_hook)


def uninstall():
    """Tests only: put every patched function back."""
    if not _orig:
        return
    S = socket.socket
    for name in ("connect", "connect_ex", "sendto"):
        setattr(S, name, _orig[name])
    socket.getaddrinfo, socket.gethostbyname = _orig["getaddrinfo"], _orig["gethostbyname"]
    if "iocp_connect" in _orig:
        from asyncio import windows_events
        windows_events.IocpProactor.connect = _orig["iocp_connect"]
    _unwire_owner_downloads()
    if _manage_hook in sys.meta_path:
        sys.meta_path.remove(_manage_hook)
    _orig.clear()


# ---------------------------------------------------------------- owner-started downloads
_hf = {"holds": 0, "env": {}}
_hf_lock = threading.Lock()
_HF_VARS = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")


def _hf_hold(on):
    """Hugging Face's offline flag is read once, when huggingface_hub is first imported. For an owner-started download:
    clear it in the environment (an import that happens now sees it) and in the module if already imported; put both back
    when the last download finishes."""
    with _hf_lock:
        hub = sys.modules.get("huggingface_hub.constants")
        if on:
            if _hf["holds"] == 0:
                _hf["env"] = {k: os.environ.get(k) for k in _HF_VARS}
                for k in _HF_VARS:
                    os.environ[k] = "0"
                if hub is not None:
                    hub.HF_HUB_OFFLINE = False
            _hf["holds"] += 1
            return
        _hf["holds"] -= 1
        if _hf["holds"] == 0:
            for k, v in _hf["env"].items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            if hub is not None and _hf["env"].get("HF_HUB_OFFLINE") == "1":  # restore the offline default
                hub.HF_HUB_OFFLINE = True


@contextlib.contextmanager
def allow(reason, hold_until=None):
    """The owner started this (a model/voice download in Settings): external connections made inside - and by the threads it
    starts, which inherit the audit context - are allowed (the master switch and the telemetry list still win).
    `hold_until`: callable; keeps the Hugging Face online flag up until it returns True (for a download that continues in a
    background thread after the `with` block ends)."""
    from ishti import audit, killswitch
    killswitch.propagate()  # idempotent: make sure worker threads inherit the context
    _hf_hold(True)
    release = [True]

    def drop():
        if release[0]:
            release[0] = False
            _hf_hold(False)
    if hold_until:
        def waiter():
            deadline = time.time() + 7200
            while time.time() < deadline and not hold_until():
                time.sleep(1.0)
            drop()
        threading.Thread(target=waiter, daemon=True, name="privacy-hold").start()
    try:
        with audit.context(privacy_allow=str(reason)[:60]):
            yield
    finally:
        if not hold_until:
            drop()


def require_internet(what="this"):
    if not internet():
        raise EgressBlocked(f"{what} needs the internet, and the Internet switch in Privacy is OFF")


_wrapped = {}


def _wire_owner_downloads():
    """ishti.manage's downloads are what the owner starts with a Settings button. Wrap them (no edit to manage.py) so their
    threads run under allow(). The Ollama pull itself is Ollama's own connection (loopback from here); the switch is checked."""
    manage = sys.modules.get("ishti.manage")
    for name in ("pull_model", "download_whisper", "download_files"):
        orig = getattr(manage, name, None)
        if orig is None or name in _wrapped:
            continue
        _wrapped[name] = orig

        def make(orig, name):
            def wrapper(*a, **k):
                require_internet("a download")
                key = {"download_whisper": lambda: f"whisper:{a[0] if a else ''}", "download_files": lambda: a[0] if a else ""
                       }.get(name, lambda: None)()
                hold = (lambda: manage.JOBS.get(key, {}).get("status") in ("done", "error")) if key else None
                with allow(f"{name} (Settings)", hold_until=hold):
                    return orig(*a, **k)
            wrapper.__name__ = name
            return wrapper
        setattr(manage, name, make(orig, name))


class _ManageHook(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name != "ishti.manage":
            return None
        sys.meta_path.remove(self)
        spec = importlib.util.find_spec(name)
        if spec and spec.loader:
            exec_module = spec.loader.exec_module

            def wired(module):
                exec_module(module)
                _wire_owner_downloads()
            spec.loader.exec_module = wired
        return spec


_manage_hook = _ManageHook()


def _unwire_owner_downloads():
    manage = sys.modules.get("ishti.manage")
    for name, orig in _wrapped.items():
        setattr(manage, name, orig)
    _wrapped.clear()


# ---------------------------------------------------------------- what the dashboard shows
# Every third-party path found in the 2026-10-02 audit (evidence is in the audit report / docs/SAFETY.md).
def silence_ollama_tray():
    """Ollama's tray app (`ollama app.exe`) asks ollama.com for updates every hour (OS, CPU, version, a nonce) and Ollama offers no
    setting that turns that off. ISHTI starts and owns its own `ollama serve`, so the tray app is not needed: stop it, and move its
    Startup shortcut to the Recycle Bin so it does not come back at the next login. The `ollama serve` it started is left running.
    -> what was done (a list of plain-language strings; empty when there was nothing to do). Windows only; never raises."""
    done = []
    if os.name != "nt":
        return done
    try:
        import psutil
        for p in psutil.process_iter(["name"]):
            if (p.info["name"] or "").lower() == "ollama app.exe":
                p.terminate()
                done.append("stopped Ollama's tray app (it checks ollama.com for updates every hour)")
        startup = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "Startup", "Ollama.lnk")
        if os.path.exists(startup):
            from win32com.shell import shell, shellcon
            shell.SHFileOperation((0, shellcon.FO_DELETE, startup, None, shellcon.FOF_ALLOWUNDO | shellcon.FOF_NOCONFIRMATION | shellcon.FOF_SILENT, None, None))
            done.append("moved Ollama's Startup shortcut to the Recycle Bin")
    except Exception as exc:
        done.append(f"could not fully stop Ollama's tray app: {type(exc).__name__}")
    for what in done:
        _log("ollama.com", 443, "ishti", False, what)   # shows up in Settings > Privacy as a blocked attempt
    return done


THIRD_PARTIES = [
    {"name": "Ollama tray app update check", "to": "ollama.com/api/update", "data": "OS, CPU arch, Ollama version, timestamp, random nonce",
     "status": "stopped at every start: ISHTI closes Ollama's tray app and removes its Startup shortcut (silence_ollama_tray); ISHTI runs its own Ollama"},
    {"name": "Ollama cloud models / web search", "to": "ollama.com", "data": "prompts sent to cloud models, only if you pick one",
     "status": "disabled: OLLAMA_NO_CLOUD=1 and ~/.ollama/server.json disable_ollama_cloud"},
    {"name": "Ollama model downloads", "to": "registry.ollama.ai", "data": "model name", "status": "owner-initiated (Settings > Models)"},
    {"name": "WebView2 (dashboard window)", "to": "Microsoft endpoints (sync, updates, telemetry)", "data": "browser diagnostics",
     "status": "blocked: loopback-only resolver rule plus --disable-* flags"},
    {"name": "scrapegraphai (find_leads)", "to": "sgai-oss-tracing.onrender.com",
     "data": "the extraction prompt, page text and the model's answer (when enabled)", "status": "blocked by name at the socket guard"},
    {"name": "chromadb / posthog", "to": "posthog", "data": "anonymous usage events", "status": "off: anonymized_telemetry=False and ANONYMIZED_TELEMETRY=False"},
    {"name": "huggingface_hub", "to": "huggingface.co", "data": "model name, the saved HF token if any, telemetry ping",
     "status": "offline when the cache exists; telemetry and implicit token off; downloads only via Settings"},
    {"name": "crewai / langsmith / opentelemetry (installed, not imported by ISHTI)", "to": "telemetry.crewai.com, api.smith.langchain.com",
     "data": "n/a unless imported", "status": "env switches set; hosts on the deny list"},
    {"name": "Telegram remote control", "to": "api.telegram.org", "data": "your messages and replies, through Telegram's servers",
     "status": "opt-in: allowed only while telegram_enabled is on"},
    {"name": "Web skills (web_search, web_fetch, markets, ...)", "to": "whatever you ask them to open", "data": "the query or URL you gave",
     "status": "allowed by design, per manifest scope.net; blocked when the Internet switch is OFF"},
    {"name": "SearXNG (Docker)", "to": "startpage, mojeek, yahoo, bing, qwant, wikipedia", "data": "your search terms",
     "status": "owner-initiated through web_search; the container is outside Python's reach"},
]
_dyn = {"t": 0.0, "v": []}


def _dynamic_findings():
    if time.time() - _dyn["t"] < 30:
        return _dyn["v"]
    out = []
    try:
        import psutil
        names = {(p.info["name"] or "").lower() for p in psutil.process_iter(["name"])}
        if "ollama app.exe" in names:
            out.append({"name": "Ollama tray app is running", "to": "ollama.com/api/update (hourly)",
                        "data": "OS, arch, Ollama version, timestamp", "status": "ACTIVE - quit it (tray icon > Quit) and remove Startup\\Ollama.lnk to stop"})
    except Exception:
        pass
    _dyn.update(t=time.time(), v=out)
    return out


def snapshot():
    if _totals["allowed"] is None:
        with _led_lock:
            if _totals["allowed"] is None:
                _totals["allowed"], _totals["blocked"] = _count_existing()
    return {"internet": internet(), "blocked_total": _totals["blocked"], "allowed_total": _totals["allowed"], "recent": recent(100),
            "skills": [{"name": k, "hosts": v} for k, v in skill_net().items()],
            "hardening": {"env": dict(_applied), "egress_guard": bool(_orig), "webview2_args": WEBVIEW2_ARGS,
                          "telemetry_deny": list(TELEMETRY_DENY)},
            "third_parties_found": THIRD_PARTIES + _dynamic_findings()}


if __name__ == "__main__":
    import tempfile
    d = tempfile.mkdtemp()
    SETTINGS_PATH, LEDGER_PATH = os.path.join(d, "privacy.json"), os.path.join(d, "outbound.log")
    assert harden()["DO_NOT_TRACK"] == "1" and internet() is True
    install()
    try:
        socket.create_connection(("93.184.216.34", 80), timeout=1)
        raise AssertionError("an unattributed external connect was not blocked")
    except EgressBlocked:
        pass
    uninstall()
    row = recent()[-1]
    assert row["allowed"] is False and row["host"] == "93.184.216.34" and set(row) == {"ts", "host", "port", "who", "allowed", "reason"}, row
    print("[self-check] privacy OK - external connect blocked and logged (host/port/who only)")
