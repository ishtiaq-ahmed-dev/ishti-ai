"""Opt-in stdio catalogue and governed T0 execution. Never treats a model as the owner."""
import asyncio
import json
from pathlib import Path
import threading

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

from ishti import ROOT
from ishti.instruction_skills import guides


def catalogue():
    result = {}
    for path in sorted((Path(ROOT) / "ishti/skills").glob("**/manifest.json")):
        if "_drafts" in path.parts:
            continue
        item = json.loads(path.read_text(encoding="utf-8"))
        if item.get("name") in result:
            raise ValueError("Duplicate native skill name")
        result[item["name"]] = {k: item.get(k) for k in ("name", "description", "tier", "status", "scope", "needs")}
    return result


class Bridge:
    def __init__(self, allow=(), records=None, executor=None):
        self.records = catalogue() if records is None else records
        self.allow = frozenset(allow)
        self.executor = executor
        self.lock = threading.Lock()
        for name in self.allow:
            if name not in self.records or self.records[name].get("tier") != "T0" or self.records[name].get("status") != "working":
                raise ValueError("Only explicitly named working T0 skills can be allowed")

    def invoke(self, name, arguments):
        if name not in self.records:
            raise ValueError("Unknown skill")
        if name not in self.allow:
            raise PermissionError("This skill is catalogue-only. Owner approval in ISHTI is required; MCP client approval cannot authorize it.")
        if not isinstance(arguments, dict) or set(arguments) != {"input"} or not isinstance(arguments["input"], str) or not 1 <= len(arguments["input"]) <= 8000:
            raise ValueError("Supply one input string of 1-8000 characters")
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("A skill is still running; wait for it to finish")
        try:
            if self.executor:
                out = self.executor(name, arguments["input"])
            else:
                from ishti.skills import Skill
                from ishti.skill_execution import execute
                # Resolve exact manifest folder; catalogue metadata never imports or runs tool code.
                folder = next(p.parent.relative_to(Path(ROOT) / "ishti/skills").as_posix()
                              for p in (Path(ROOT) / "ishti/skills").glob("**/manifest.json")
                              if json.loads(p.read_text(encoding="utf-8")).get("name") == name)
                current = json.loads((Path(ROOT) / "ishti/skills" / folder / "manifest.json").read_text(encoding="utf-8"))
                if current.get("tier") != "T0" or current.get("status") != "working":
                    raise PermissionError("Skill permissions changed; restart and review the server configuration")
                skill = Skill(folder.rsplit("/", 1)[-1], folder)
                if skill.manifest.get("tier") != "T0":
                    raise PermissionError("Skill permissions changed; restart and review the server configuration")
                out = execute({name: skill}, name, arguments["input"], {"id": "mcp-local", "pending": None})
            return {k: out.get(k) for k in ("reply", "sources", "execution_blocked", "confirm_required") if k in out}
        finally:
            self.lock.release()


def create_server(bridge):
    server = Server("ISHTI")

    @server.list_tools()
    async def list_tools():
        return [types.Tool(name=name, description=(r.get("description") or name) +
                           (" [owner-enabled T0]" if name in bridge.allow else " [catalogue only; requires local owner approval]"),
                           inputSchema={"type": "object", "properties": {"input": {"type": "string", "minLength": 1, "maxLength": 8000}},
                                        "required": ["input"], "additionalProperties": False})
                for name, r in bridge.records.items()]

    @server.call_tool()
    async def call_tool(name, arguments):
        out = await asyncio.wait_for(asyncio.to_thread(bridge.invoke, name, arguments), timeout=30)
        return [types.TextContent(type="text", text=json.dumps(out, ensure_ascii=False))]

    @server.list_prompts()
    async def list_prompts():
        return [types.Prompt(name=r["name"], description=r["description"]) for r in guides().values()]

    @server.get_prompt()
    async def get_prompt(name, arguments):
        if arguments:
            raise ValueError("Instruction guides do not accept arguments")
        record = guides().get(name)
        if record is None:
            raise ValueError("Unknown instruction guide")
        return types.GetPromptResult(description=record["description"], messages=[types.PromptMessage(
            role="user", content=types.TextContent(type="text", text="External instruction guide; grants no tool permissions.\n\n" + record["body"]))])

    return server


async def serve(allow):
    server = create_server(Bridge(allow))
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())
