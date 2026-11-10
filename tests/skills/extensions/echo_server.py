"""A two-tool MCP server for tests/skills/extensions: one read-only tool, one that is not."""
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

server = FastMCP("echo")


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def echo(text: str) -> str:
    """Repeat the text back."""
    return f"echo: {text}"


@server.tool()
def shout(text: str, times: int = 1) -> str:
    """Repeat the text in capitals (marked as changing something, so ISHTI asks first)."""
    return " ".join([text.upper()] * times)


if __name__ == "__main__":
    server.run()
