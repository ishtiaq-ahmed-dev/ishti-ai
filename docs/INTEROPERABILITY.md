# ISHTI interoperability

The MCP catalogue contains all native manifests (124 on October 13). It starts only when the owner
launches `tools/ishti_mcp.py`; the desktop does not expose a new network listener. The transport is
stdio, using the project's pinned MCP Python SDK 1.28.1. Ordinary prints go to stderr, including
import-time and interpreter-teardown output.

## Local MCP host configuration

Replace the example paths with the actual checkout. Use the console `python.exe`, not `pythonw.exe`.

```json
{
  "mcpServers": {
    "ishti": {
      "command": "C:/your-checkout/venv/Scripts/python.exe",
      "args": ["-B", "-u", "C:/your-checkout/tools/ishti_mcp.py", "--allow", "textstats"]
    }
  }
}
```

Without `--allow`, every entry is catalogue-only. Each `--allow` must name an exact working T0 skill;
T1/T2, disabled/broken skills and unknown names are rejected at startup. Enabled T0 execution still
passes the existing settings, kill switch, scope and audit boundary. The manifest is rechecked before
import/execution. Calls accept one `input` string of 1-8000 characters. One call runs at a time, with
a 30-second response deadline; a timed-out worker keeps its slot until it finishes. A Python thread
cannot forcibly interrupt native code, so this is not an OS sandbox.

T1/T2 entries return an error directing the owner to ISHTI's normal approval flow. A host approval,
`approved: true`, or a model saying “yes” cannot authorize those actions. This initial bridge does not
transfer pending approvals to a host, and does not claim that all 124 skills can execute remotely.
Only connect a local host the owner trusts: it can read the output of explicitly enabled tools.

## Portable instruction guides

An Agent Skills directory has `<name>/SKILL.md` with YAML frontmatter `name` and `description`, followed
by Markdown instructions. Add explicitly selected roots with `ISHTI_INSTRUCTION_SKILLS` (absolute
directories separated by `;` on Windows) before starting the app or MCP server.

The app lists these as `guide_<name>` in Instruction Guides; “show guide example” returns the guide.
The MCP server exposes them as prompts. They are documents: adjacent Python/shell scripts are never
imported, links are not fetched, and `tier`/`allowed-tools` metadata grants no permissions. A changed
document is refused by a loaded native guide until restart; the MCP prompt is freshly read on request.
The parser uses safe YAML, validates the directory/name, rejects reparse links, caps each file at 32 KiB
and caps roots collectively at 128 guides. Treat document text as untrusted context.

```powershell
venv\Scripts\python -B tools\ishti_mcp.py --export C:\your-new-empty-export
```

Export creates portable descriptions of the native tools with the current licence notice. It uses
exclusive creation and never overwrites an existing guide. These documents explain how to call ISHTI;
they do not repackage executable skills or waive dependencies or licensing terms.

## Verification and rollback

`tests/test_interoperability.py` covers malformed YAML, Unicode, permission injection, changed content,
input bounds and denied execution. A real stdio-client smoke test is recorded in the audit-fix evidence.
Interoperability with a named commercial host is a separate manual integration check.

Stop the MCP subprocess and remove the host configuration to disable the bridge. Unset
`ISHTI_INSTRUCTION_SKILLS` to disable guide discovery. The desktop's existing skill format remains the
native executable format; this does not install third-party tool code.

References: [MCP Python SDK 1.x](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x) and
[Agent Skills specification](https://agentskills.io/specification).
