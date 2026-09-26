"""Thin MCP client. Each configured server (config.json "mcp_servers") runs as one long-lived
stdio session on a background event loop, so state such as an open browser page survives
between calls. Skills use the sync API: tools(server) and call(server, tool, args)."""
import asyncio
import os
import concurrent.futures
import threading

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from ishti import CONFIG


class MCPHub:
    def __init__(self, servers=None):
        self.cfg = servers or CONFIG.get("mcp_servers", {})
        self.live = {}  # name -> (queue, tools)
        self.lock = threading.Lock()
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True, name="mcp-loop").start()

    async def _serve(self, name, ready):
        # The anyio contexts must open and close in this one task, so the session lives here and
        # requests arrive through a queue.
        cfg = self.cfg[name]
        params = StdioServerParameters(command=cfg["command"], args=cfg.get("args", []), env=cfg.get("env"))
        try:
            async with stdio_client(params) as (r, w), ClientSession(r, w) as session:
                await session.initialize()
                q = asyncio.Queue()
                self.live[name] = (q, (await session.list_tools()).tools)
                ready.set_result(True)
                while (item := await q.get()) is not None:
                    tool, args, fut = item
                    try:
                        fut.set_result(await session.call_tool(tool, args))
                    except Exception as exc:
                        fut.set_exception(exc)
        except Exception as exc:
            if not ready.done():
                ready.set_exception(exc)
        finally:
            self.live.pop(name, None)

    def _reap_orphans(self, name):
        """A server killed without a clean shutdown leaves its MCP child (and that child's Chrome) running,
        still holding the persistent browser profile, so the next launch hangs. Kill only processes whose
        command line carries this server's own profile path - never anything else."""
        import psutil
        args = self.cfg[name].get("args", [])
        marker = next((args[i + 1] for i, a in enumerate(args[:-1]) if a == "--user-data-dir"), None)
        if not marker:
            return
        for p in psutil.process_iter(["pid", "cmdline"]):
            try:
                if p.pid != os.getpid() and marker in " ".join(p.info["cmdline"] or []):
                    for child in p.children(recursive=True):
                        child.kill()
                    p.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    def _ensure(self, name, timeout=120):
        with self.lock:
            if name not in self.live:
                if name not in self.cfg:
                    raise KeyError(f"no MCP server '{name}' in config.json")
                self._reap_orphans(name)
                ready = concurrent.futures.Future()
                asyncio.run_coroutine_threadsafe(self._serve(name, ready), self.loop)
                ready.result(timeout)
        return self.live[name]

    def tools(self, name):
        return self._ensure(name)[1]

    def call(self, name, tool, args=None, timeout=90) -> str:
        q, _ = self._ensure(name)
        fut = concurrent.futures.Future()
        self.loop.call_soon_threadsafe(q.put_nowait, (tool, args or {}, fut))
        res = fut.result(timeout)
        text = "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")
        return f"ERROR: {text}" if res.isError else text

    def close(self, name):
        if name in self.live:
            self.loop.call_soon_threadsafe(self.live[name][0].put_nowait, None)


_hub = None


def get_hub() -> MCPHub:
    global _hub
    if _hub is None:
        _hub = MCPHub()
    return _hub


if __name__ == "__main__":
    import time
    hub = get_hub()
    t0 = time.time()
    names = [t.name for t in hub.tools("browser")]
    print(f"browser MCP up in {time.time() - t0:.1f}s, {len(names)} tools: {', '.join(names)}")
    assert "browser_navigate" in names and "browser_snapshot" in names
    out = hub.call("browser", "browser_navigate", {"url": "https://example.com"})
    assert "Example Domain" in out, out[:500]
    snap = hub.call("browser", "browser_snapshot")
    assert "Example Domain" in snap and "ref=" in snap, snap[:500]  # state persisted between calls
    hub.call("browser", "browser_close")
    print("[self-check] mcp_client OK - persistent session, navigate, snapshot")
