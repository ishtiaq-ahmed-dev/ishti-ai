"""Use the owner's MCP servers as tools - the shared tool ecosystem Hermes Agent and OpenClaw plug into (GitHub, Notion,
Slack, databases, calendars, ...). The reverse direction (ISHTI as a server for other agents) is ishti/mcp_server.py.

Servers come from config.json "mcp_servers" (hand-edited) and data/mcp_servers.json (added in chat with "add mcp server
..."), in the format Claude Desktop, Hermes and OpenClaw use:
  {"github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
              "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "keyring:github"}, "allow": ["search_repositories"]},
   "docs":   {"url": "https://example.com/mcp", "headers": {"Authorization": "keyring:docs"}}}
Every tool of a server becomes a skill "mcp_<server>_<tool>" that the one model call (ishti/brain.py) can choose.

Safety:
- launchers are allow-listed (MCP_LAUNCHERS); a remote server must use https (plain http only on this PC).
- the server process gets a minimal environment (PATH and profile folders - none of ISHTI's own secrets) plus only the
  variables it declares. A "keyring:<name>" value is read from Windows Credential Manager (service "ishti-mcp") at
  launch and never written to a file.
- a tool asks the owner first (T2) unless the owner allow-listed it ("allow") or the server marks it read-only.
  ponytail: readOnlyHint is the server's own claim; a server you installed already runs with your permissions anyway.
- tool output is data, never instructions: it is capped, and instruction-like text in it is flagged to the owner."""
import asyncio
import hashlib
import json
import os
import re
import threading
from urllib.parse import urlsplit

from ishti import CONFIG, ROOT
from ishti.test_mode import scratch_path

MCP_LAUNCHERS = {"node", "npx", "python", "python3", "uvx", "uv", "cmd", "powershell", "docker", "ishti-mcp-login"}
SERVERS_PATH = scratch_path(os.path.join(ROOT, "data", "mcp_servers.json"))
CACHE_PATH = scratch_path(os.path.join(ROOT, "data", "mcp_tools.json"))
KEYRING_SERVICE = "ishti-mcp"
TIMEOUT_S = 60      # first launch of an npx/uvx server downloads it
OUTPUT_MAX = 6000   # characters of tool output shown (the reply is the owner's to read, not a data dump)
NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}$")
ARG_SECRET = re.compile(r"(?i)\b(token|secret|password|api[_-]?key)\b\s*=\s*\S+")


def _read(path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def servers() -> dict:
    """{name: spec}: config.json "mcp_servers", then the chat-added ones (which win on a name clash)."""
    out = dict(CONFIG.get("mcp_servers") or {})
    out.update(_read(SERVERS_PATH).get("mcp_servers") or {})
    return {n: s for n, s in out.items() if isinstance(s, dict)}


def problem(name: str, spec: dict) -> str:
    """Why this server may not run ('' when it may)."""
    if not NAME.match(str(name)):
        return "a server name is 1-32 lowercase letters, digits, - or _"
    if spec.get("url"):
        u = urlsplit(str(spec["url"]))
        local = u.hostname in ("localhost", "127.0.0.1", "::1")
        return "" if u.scheme == "https" or (u.scheme == "http" and local) else "a remote server must use https"
    cmd = str(spec.get("command") or "")
    if os.path.splitext(os.path.basename(cmd))[0].lower() not in MCP_LAUNCHERS:
        return f"'{cmd or '(none)'}' is not an allowed launcher ({', '.join(sorted(MCP_LAUNCHERS))})"
    if ARG_SECRET.search(" ".join(str(a) for a in spec.get("args") or [])):
        return "a secret is written into its arguments - put it in env as keyring:<name> instead"
    return ""


def save_server(name: str, spec: dict):
    data = _read(SERVERS_PATH)
    data.setdefault("mcp_servers", {})[name] = spec
    _write(SERVERS_PATH, data)


def remove_server(name: str) -> bool:
    """Remove a chat-added server (config.json ones are the owner's file to edit). True if it was there."""
    data = _read(SERVERS_PATH)
    if name not in (data.get("mcp_servers") or {}):
        return False
    del data["mcp_servers"][name]
    _write(SERVERS_PATH, data)
    cache = _read(CACHE_PATH)
    if cache.pop(name, None) is not None:
        _write(CACHE_PATH, cache)
    return True


def _resolve(mapping) -> dict:
    out = {}
    for key, value in (mapping or {}).items():
        value = str(value)
        if value.startswith("keyring:"):
            import keyring
            value = keyring.get_password(KEYRING_SERVICE, value[8:]) or ""
            if not value:
                raise LookupError(f"no secret is saved for {key} - say: set mcp secret {str(mapping[key])[8:]} <value>")
        out[str(key)] = value
    return out


async def _exchange(spec: dict, fn):
    from mcp import ClientSession
    if spec.get("url"):
        from mcp.client.streamable_http import streamablehttp_client
        transport = streamablehttp_client(str(spec["url"]), headers=_resolve(spec.get("headers")) or None)
    else:
        from mcp.client.stdio import StdioServerParameters, get_default_environment, stdio_client
        env = {**get_default_environment(), **_resolve(spec.get("env"))}
        transport = stdio_client(StdioServerParameters(command=str(spec["command"]), env=env, cwd=spec.get("cwd"),
                                                       args=[str(a) for a in spec.get("args") or []]))
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            return await fn(session)


def _run(spec: dict, fn):
    """One MCP exchange on a fresh event loop in its own thread (the caller may already be inside a loop).
    ponytail: one server process per call (1-3 s to start an npx server); a pooled session is the upgrade if tools
    get called often. Nothing is left running between calls."""
    box = {}

    def worker():
        try:
            box["value"] = asyncio.run(asyncio.wait_for(_exchange(spec, fn), TIMEOUT_S))
        except BaseException as exc:  # handed to the caller below
            box["error"] = exc

    t = threading.Thread(target=worker, daemon=True, name="mcp-call")
    t.start()
    t.join(TIMEOUT_S + 10)
    if t.is_alive():
        raise TimeoutError("the MCP server did not answer in time")
    err = box.get("error")
    while isinstance(err, BaseExceptionGroup) and err.exceptions:  # anyio task groups wrap the real cause
        err = err.exceptions[0]
    if err is not None:
        raise err
    return box.get("value")


def list_tools(spec: dict) -> list:
    async def fn(session):
        result = await session.list_tools()  # ponytail: first page only; servers rarely page their tool list
        return [{"name": t.name, "description": (t.description or "").strip(), "schema": t.inputSchema or {},
                 "read_only": bool(t.annotations and t.annotations.readOnlyHint)} for t in result.tools]
    return _run(spec, fn)


def call_tool(spec: dict, tool: str, args: dict) -> tuple[bool, str]:
    """(is_error, text) from one tool call."""
    async def fn(session):
        result = await session.call_tool(tool, args)
        text = "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
        if not text and result.structuredContent:
            text = json.dumps(result.structuredContent, ensure_ascii=False)
        return bool(result.isError), text
    return _run(spec, fn)


def _sig(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:16]


def tools(name: str, spec: dict, *, refresh: bool = False, cached_only: bool = False):
    """The server's tools, from the cache unless its config changed or refresh is asked. None when cached_only and
    there is no fresh cache entry."""
    cache = _read(CACHE_PATH)
    hit = cache.get(name)
    if hit and hit.get("sig") == _sig(spec) and not refresh:
        return hit.get("tools") or []
    if cached_only:
        return None
    found = list_tools(spec)
    cache[name] = {"sig": _sig(spec), "tools": found}
    _write(CACHE_PATH, cache)
    return found


def arguments(text: str, schema: dict):
    """The tool arguments for the model's input: a JSON object, or - for a tool with one text parameter - the plain
    request itself. None when a required argument is missing."""
    from ishti.memstore.jsonio import parse_json
    props = schema.get("properties") or {}
    required = [k for k in schema.get("required") or [] if isinstance(k, str)]
    data = parse_json(text) if str(text).strip().startswith("{") else None
    if isinstance(data, dict):
        args = {k: v for k, v in data.items() if not props or k in props}
    elif not props:
        args = {}
    elif len(required) == 1 or len(props) == 1:
        key = required[0] if len(required) == 1 else next(iter(props))
        if (props.get(key) or {}).get("type", "string") != "string":
            return None
        args = {key: str(text).strip()}
    else:
        return None
    return args if all(k in args for k in required) else None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_")[:64]


def why(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:200]}" if str(exc) else type(exc).__name__


class MCPTool:
    """One server tool, shaped like a skill (manifest / matches / run / info)."""
    status = "working"
    problem = ""
    module = None
    category = "MCP Tools"
    missing_optional: list = []

    def __init__(self, server: str, spec: dict, tool: dict):
        self.server, self.spec, self.tool = server, spec, tool
        self.name = self.folder = _slug(f"mcp_{server}_{tool['name']}")
        self.path = f"mcp:{server}"
        schema = tool.get("schema") or {}
        required = set(schema.get("required") or [])
        params = ", ".join(f"{p}{'*' if p in required else ''}" for p in schema.get("properties") or {})
        trusted = tool["name"] in (spec.get("allow") or []) or bool(tool.get("read_only"))
        self.manifest = {"name": self.name, "description": f"{server} (MCP): {tool.get('description') or tool['name']}"[:300],
                         "input_hint": f"input: JSON {{{params}}}, * = required" if params else "input: {}",
                         "tier": "T1" if trusted else "T2", "status": "working", "category": self.category,
                         "priority": 40, "route": "", "format": "MCP",
                         "scope": {"write": [], "net": [], "apps": False}}

    def matches(self, text):
        return False

    def info(self):
        return {**self.manifest, "path": self.path, "folder": self.folder, "status": self.status, "problem": "",
                "missing_optional": []}

    def run(self, text, session):
        from ishti import untrusted
        args = arguments(text, self.tool.get("schema") or {})
        if args is None:
            need = ", ".join((self.tool.get("schema") or {}).get("required") or []) or "its arguments"
            return {"reply": f"The {self.tool['name']} tool on {self.server} needs {need}. What should I use?",
                    "sources": [], "status": "needs_input", "verified": False}
        try:
            is_error, out = call_tool(self.spec, self.tool["name"], args)
        except Exception as exc:
            return {"reply": f"The {self.server} MCP server failed ({why(exc)}). Nothing was changed by ISHTI.",
                    "sources": [], "verified": False}
        out = (out or "").strip()
        reply = out[:OUTPUT_MAX] + ("\n... (cut)" if len(out) > OUTPUT_MAX else "") if out else "(the tool returned nothing)"
        if is_error:
            reply = f"The {self.server} tool {self.tool['name']} reported an error: {reply}"
        if untrusted.scan(out):
            reply += "\n\n(Warning: this output contains instructions aimed at an AI. ISHTI did not follow them.)"
        return {"reply": reply, "sources": [{"title": f"{self.server} / {self.tool['name']} (MCP)", "url": ""}],
                "verified": not is_error, "untrusted": True}


def register(skills: dict, name: str, spec: dict, *, refresh: bool = False, cached_only: bool = False) -> list:
    """Add (or replace) this server's tools in the live skill registry. Returns the new tool skills."""
    found = tools(name, spec, refresh=refresh, cached_only=cached_only)
    if found is None:
        return []
    unregister(skills, name)
    added = []
    for tool in found:
        skill = MCPTool(name, spec, tool)
        if skill.name in skills:  # never shadow a native skill
            continue
        skills[skill.name] = skill
        added.append(skill)
    return added


def unregister(skills: dict, name: str):
    for key in [k for k, s in skills.items() if isinstance(s, MCPTool) and s.server == name]:
        del skills[key]


def load(skills: dict, log=None):
    """At startup: register every allowed server from its cached tool list (no process launched, so boot stays fast);
    a server not yet cached is listed once in the background and joins the live registry when it answers."""
    pending = []
    for name, spec in servers().items():
        if why_not := problem(name, spec):
            if log:
                log(f"MCP server '{name}' skipped: {why_not}")
            continue
        if tools(name, spec, cached_only=True) is None:
            pending.append((name, spec))
        else:
            register(skills, name, spec, cached_only=True)
    if not pending:
        return

    def warm():
        for name, spec in pending:
            try:
                register(skills, name, spec)
            except Exception as exc:
                if log:
                    log(f"MCP server '{name}' did not list its tools: {why(exc)}")
    threading.Thread(target=warm, daemon=True, name="mcp-warm").start()
