"""Start the opt-in local stdio MCP bridge, or export portable instruction documents."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow", action="append", default=[], help="Enable one exact working T0 skill; default allows no execution")
    parser.add_argument("--export", help="Export manifest descriptions as SKILL.md instruction guides into this directory")
    args = parser.parse_args()
    # Keep ALL import-time prints away from the JSON-RPC wire, including buffered teardown output.
    wire = sys.stdout
    sys.stdout = sys.stderr
    from ishti import privacy
    privacy.install()
    from ishti.mcp_server import catalogue, serve
    if args.export:
        import yaml
        destination = Path(args.export)
        count = 0
        for name, record in catalogue().items():
            portable = name.replace("_", "-")
            directory = destination / portable
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / "SKILL.md"
            text = "---\n" + yaml.safe_dump({"name": portable, "description": record["description"] or name,
                                               "license": "PolyForm Noncommercial 1.0.0; see ISHTI LICENSE"}, sort_keys=False) + "---\n\n"
            text += f"Use the ISHTI MCP tool `{name}` with one `input` string. Tier: {record['tier']}.\n\n"
            text += "The owner must authorize execution locally. This document grants no permissions and contains no executable scripts.\n"
            with path.open("x", encoding="utf-8") as target:
                target.write(text)
            count += 1
        print(json.dumps({"exported": count, "directory": str(destination)}))
    else:
        import asyncio
        # stdio_server binds the saved wire descriptor; Python stdout stays on stderr permanently.
        import os
        from contextlib import ExitStack
        import anyio
        from mcp.server import stdio as mcp_stdio
        with ExitStack() as stack:
            stream = stack.enter_context(os.fdopen(os.dup(wire.fileno()), "w", encoding="utf-8"))
            original = mcp_stdio.stdio_server
            from contextlib import asynccontextmanager
            @asynccontextmanager
            async def transport():
                async with original(stdout=anyio.wrap_file(stream)) as streams:
                    yield streams
            import ishti.mcp_server as server_module
            server_module.stdio_server = transport
            asyncio.run(serve(args.allow))


if __name__ == "__main__":
    main()
