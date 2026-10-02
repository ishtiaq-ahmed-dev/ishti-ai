"""Runtime-log bug "browser: UnsupportedOperation: fileno" (the 'internet reach' request, 2026-10-02 13:40).

mcp.client.stdio.stdio_client(errlog=sys.stderr) binds the stream object that was current when the mcp module was
imported. When that stream is later replaced by one without a file descriptor (pytest capture, a service wrapper), the
child launch dies with `UnsupportedOperation: fileno`. ishti.mcp_client now gives every server its own real log file.
Offline: the 'server' is a 10-line local FastMCP echo script; no browser, no network."""
import io
import os
import sys
import textwrap

from ishti import mcp_client

SERVER = textwrap.dedent('''
    import sys
    from mcp.server.fastmcp import FastMCP
    m = FastMCP("echo")

    @m.tool()
    def echo(text: str) -> str:
        print("server-side stderr noise", file=sys.stderr)
        return "echo:" + text

    m.run()
''')


def test_server_launches_and_answers_even_when_sys_stderr_has_no_fileno(tmp_path, monkeypatch):
    script = tmp_path / "echo_server.py"
    script.write_text(SERVER, encoding="utf-8")
    monkeypatch.setattr(mcp_client, "TOKENS_PATH", str(tmp_path / "mcp_tokens.json"))  # logs go under tmp_path/logs
    monkeypatch.setattr(sys, "stderr", io.StringIO())      # a stream with no fileno(), exactly the failing condition
    hub = mcp_client.MCPHub({"echo": {"command": sys.executable, "args": [str(script)]}})
    try:
        assert [t.name for t in hub.tools("echo")] == ["echo"]
        assert hub.call("echo", "echo", {"text": "hi"}, timeout=60) == "echo:hi"
    finally:
        hub.close("echo")
    log = tmp_path / "logs" / "mcp_echo.err.log"
    assert log.exists()                                    # the server's stderr went to its own file, not the console


def test_stdio_client_is_always_given_a_real_file_for_the_server_stderr(tmp_path, monkeypatch):
    """The old call relied on stdio_client's import-time default; this fails if anyone drops the explicit errlog."""
    script = tmp_path / "echo_server.py"
    script.write_text(SERVER, encoding="utf-8")
    monkeypatch.setattr(mcp_client, "TOKENS_PATH", str(tmp_path / "mcp_tokens.json"))
    seen = {}
    real = mcp_client.stdio_client

    def spy(params, errlog=None):
        seen["errlog"] = errlog
        seen["fileno"] = errlog.fileno()                   # raises UnsupportedOperation for a StringIO-like stream
        return real(params, errlog=errlog)
    monkeypatch.setattr(mcp_client, "stdio_client", spy)
    hub = mcp_client.MCPHub({"echo": {"command": sys.executable, "args": [str(script)]}})
    try:
        hub.tools("echo")
    finally:
        hub.close("echo")
    assert isinstance(seen["fileno"], int) and seen["errlog"].name.endswith(os.path.join("logs", "mcp_echo.err.log"))
