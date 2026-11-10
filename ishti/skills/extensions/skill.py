"""Extensions: MCP tool servers and SKILL.md guides - the two ways Hermes Agent and OpenClaw grow, managed from chat.

  "add mcp server github npx -y @modelcontextprotocol/server-github"   (asks first: it runs a program on this PC)
  "add mcp server docs https://example.com/mcp"
  "set mcp secret github <token>"            -> Windows Credential Manager; use it as "keyring:github" in env
  "list mcp servers" / "show extensions"     "refresh mcp"     "remove mcp server github"   (asks first)
  "install skill from D:\\Downloads\\pdf-tips"  (asks first; copies only SKILL.md - nothing in the folder runs)
  "remove skill guide pdf-tips"              (asks first)

Unlike a public skill hub (OpenClaw's ClawHub had hundreds of poisoned skills), nothing is fetched from a marketplace:
the owner points at a server or a folder, a guide with instructions aimed at an AI is refused, and each addition is
confirmed once."""
import os
import re
import shlex
import shutil
from pathlib import Path

from ishti import mcp_client
from ishti.skills import ask_first

I = re.I
LIST = re.compile(r"^\s*(?:list|show|what\s+are|which\s+are|what)\s+(?:my\s+|the\s+|all\s+)?(?:installed\s+|connected\s+)?"
                  r"(?:mcp(?:\s+(?:servers?|tools?))?|extensions?|skill\s+guides?|guides?)\b(?:\s+(?:do\s+i\s+have|are\s+there|connected|installed))?\s*[?.!]?\s*$", I)
ADD = re.compile(r"^\s*(?:please\s+)?(?:add|connect|install)\s+(?:an?\s+|the\s+)?mcp\s+server\s+(?P<name>[A-Za-z0-9_-]{1,32})"
                 r"\s*[:=]?\s+(?P<target>\S.*?)\s*$", I)
REMOVE = re.compile(r"^\s*(?:please\s+)?(?:remove|delete|disconnect|uninstall)\s+(?:the\s+)?mcp\s+server\s+(?P<name>[A-Za-z0-9_-]{1,32})\s*[.!]?\s*$", I)
REFRESH = re.compile(r"^\s*(?:refresh|reload|update|rescan)\s+(?:the\s+|my\s+)?mcp(?:\s+(?:servers?|tools?))?\s*[.!]?\s*$", I)
SECRET = re.compile(r"^\s*(?:set|save|store)\s+(?:the\s+|an?\s+)?mcp\s+secret\s+(?P<name>[A-Za-z0-9_.-]{1,64})\s*[:=]?\s+(?P<value>\S+)\s*$", I)
INSTALL = re.compile(r"^\s*(?:please\s+)?(?:install|add|import)\s+(?:the\s+|a\s+)?skill\s+(?:guide\s+)?from\s+(?P<path>.+?)\s*$", I)
UNINSTALL = re.compile(r"^\s*(?:please\s+)?(?:remove|delete|uninstall)\s+(?:the\s+)?skill\s+guide\s+(?P<name>[a-z0-9-]{1,64})\s*[.!]?\s*$", I)
CHANNEL_ON = re.compile(r"^\s*(?:please\s+)?(?:connect|set\s+up|add|turn\s+on)\s+(?P<svc>discord|slack)\s+(?:bot\s+)?(?:token\s+)?"
                        r"(?P<token>\S{20,})\s+owner\s+(?:id\s+)?(?P<owner>[A-Za-z0-9]{5,32})\s*[.!]?\s*$", I)
HOOK_ADD = re.compile(r"^\s*(?:please\s+)?(?:create|make|add|set\s+up)\s+(?:a\s+|an\s+)?(?:new\s+)?webhook\s+(?:called|named)\s+"
                      r"(?P<name>[\w-]{1,32})\s+(?:that|to|which)\s+(?P<instr>\S.*?)\s*$", I)
HOOK_LIST = re.compile(r"^\s*(?:list|show)\s+(?:my\s+|the\s+|all\s+)?webhooks?\s*[?.!]?\s*$", I)
HOOK_DEL = re.compile(r"^\s*(?:please\s+)?(?:delete|remove)\s+(?:the\s+)?webhook\s+(?P<name>[\w-]{1,32})\s*[.!]?\s*$", I)
CHANNEL_OFF = re.compile(r"^\s*(?:please\s+)?(?:disconnect|turn\s+off|disable|stop)\s+(?:the\s+)?(?P<svc>discord|slack)"
                         r"(?:\s+(?:channel|bot))?\s*[.!]?\s*$", I)


def _live():
    from ishti.dispatcher import SKILLS
    return SKILLS


def _spec(target: str) -> dict:
    if re.match(r"https?://", target, I):
        return {"url": target}
    parts = [p.strip("\"'") for p in shlex.split(target, posix=False)]
    return {"command": parts[0], "args": parts[1:]} if parts else {}


def _summary(added) -> str:
    asks = [s for s in added if s.manifest["tier"] == "T2"]
    names = ", ".join(s.tool["name"] for s in added[:12]) + (" ..." if len(added) > 12 else "")
    return (f"{len(added)} tool(s): {names}. {len(added) - len(asks)} read-only tool(s) run directly; "
            f"{len(asks)} ask you first.") if added else "no tools."


def _add(m, session):
    name, target = m["name"].lower(), m["target"].strip()
    spec = _spec(target)
    if why := mcp_client.problem(name, spec):
        return f"I can't add that server: {why}."
    replacing = name in mcp_client.servers()

    def act():
        mcp_client.save_server(name, spec)
        try:
            added = mcp_client.register(_live(), name, spec, refresh=True)
        except Exception as exc:
            return (f"Saved the MCP server '{name}', but it did not start ({mcp_client.why(exc)}). Check the command, "
                    f"then say: refresh mcp.")
        return f"Added the MCP server '{name}' with {_summary(added)}"

    what = f"the address {spec['url']}" if "url" in spec else f"the command: {target}"
    verb = "Replace" if replacing else "Add"
    return ask_first(session, "extensions", f"{verb} the MCP server '{name}' using {what}? It runs with your "
                                            f"permissions on this PC", act)


def _remove(m, session):
    name = m["name"].lower()
    if name not in mcp_client.servers():
        return f"There's no MCP server called '{name}'."

    def act():
        if not mcp_client.remove_server(name):
            return f"'{name}' is set in config.json - remove it there (I only remove servers added in chat)."
        mcp_client.unregister(_live(), name)
        return f"Removed the MCP server '{name}' and its tools."
    return ask_first(session, "extensions", f"Remove the MCP server '{name}' and its tools", act)


def _refresh():
    out = []
    for name, spec in mcp_client.servers().items():
        if why := mcp_client.problem(name, spec):
            out.append(f"- {name}: skipped ({why})")
            continue
        try:
            out.append(f"- {name}: {_summary(mcp_client.register(_live(), name, spec, refresh=True))}")
        except Exception as exc:
            out.append(f"- {name}: did not answer ({mcp_client.why(exc)})")
    return "Refreshed:\n" + "\n".join(out) if out else "No MCP servers are set up."


def _list():
    from ishti.instruction_skills import InstructionSkill
    live = _live()
    lines = []
    for name, spec in mcp_client.servers().items():
        tools = [s for s in live.values() if isinstance(s, mcp_client.MCPTool) and s.server == name]
        why = mcp_client.problem(name, spec)
        lines.append(f"- {name}: " + (f"not allowed ({why})" if why else _summary(tools) if tools else "not started yet (say: refresh mcp)"))
    guides = [s for s in live.values() if isinstance(s, InstructionSkill)]
    out = (["MCP servers:", *lines] if lines else ["No MCP servers yet."])
    out += (["", "Skill guides:", *[f"- {g.record['name']}: {g.record['description'][:100]}" for g in guides]]
            if guides else ["No skill guides installed."])
    if not lines and not guides:
        out.append("Add one with: add mcp server <name> <command or https address>, or: install skill from <folder with SKILL.md>.")
    return "\n".join(out)


def _secret(m, session):
    import keyring
    name, value = m["name"], m["value"]
    session["redact"] = (session.get("redact") or []) + [value]  # masked wherever this exchange is kept
    keyring.set_password(mcp_client.KEYRING_SERVICE, name, value)
    return f"Saved the secret '{name}' in Windows Credential Manager. Use it as \"keyring:{name}\" in a server's env."


def _raw_url(url: str) -> str:
    """A GitHub folder or file page -> its raw SKILL.md (skill hubs link folders); any other URL is used as given."""
    if m := re.match(r"https://github\.com/([^/]+)/([^/]+)/(?:tree|blob)/(.+?)/?$", url):
        path = m[3] if m[3].endswith("SKILL.md") else m[3] + "/SKILL.md"
        return f"https://raw.githubusercontent.com/{m[1]}/{m[2]}/{path}"
    return url


def _download(url: str) -> Path:
    """Fetch one SKILL.md (SSRF-guarded, 64 KB cap) into a temp folder named after the skill, so read_skill can vet it."""
    import tempfile
    import yaml
    from tools.web.fetch import safe_get
    r = safe_get(_raw_url(url), max_bytes=65_536)
    r.raise_for_status()
    text = r.content.decode("utf-8", "replace").replace("\r\n", "\n")
    head = yaml.safe_load(text.split("\n---\n", 1)[0][4:]) if text.startswith("---\n") else None
    name = str((head or {}).get("name") or "") if isinstance(head, dict) else ""
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        raise ValueError("the file has no valid 'name' in its frontmatter")
    folder = Path(tempfile.mkdtemp(prefix="ishti-skill-")) / name
    folder.mkdir()
    (folder / "SKILL.md").write_text(text, encoding="utf-8")
    return folder / "SKILL.md"


def _install(m, session):
    import yaml
    from ishti import untrusted
    from ishti.instruction_skills import DEFAULT_ROOT, InstructionSkill, read_skill
    raw = m["path"].strip().strip("\"'")
    try:
        if re.match(r"https?://", raw, I):
            path = _download(raw)
        else:
            src = Path(raw)
            path = src if src.name == "SKILL.md" else src / "SKILL.md"
        rec = read_skill(path)
    except Exception as exc:  # unreadable, blocked address, bad frontmatter: nothing is installed
        return f"That isn't a usable skill guide ({mcp_client.why(exc)})."
    if hits := untrusted.scan(rec["description"] + "\n" + rec["body"]):
        return f"I won't install '{rec['name']}': it contains instructions aimed at an AI (\"{hits[0][:120]}\")."
    dest = Path(DEFAULT_ROOT) / rec["name"] / "SKILL.md"
    replacing = dest.exists()

    def act():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)  # only SKILL.md: scripts and other files beside it are never copied or run
        guide = InstructionSkill(read_skill(dest))
        live = _live()
        if guide.name in live and not isinstance(live[guide.name], InstructionSkill):
            return f"Copied, but '{guide.name}' clashes with a built-in skill, so it stays off."
        live[guide.name] = guide
        return f"Installed the '{rec['name']}' guide. I'll follow it when a request is about: {rec['description'][:160]}"
    verb = "Replace" if replacing else "Install"
    return ask_first(session, "extensions", f"{verb} the skill guide '{rec['name']}' ({rec['description'][:120]})? "
                                            f"Only its SKILL.md text is copied; nothing in it runs", act)


def _uninstall(m, session):
    from ishti.instruction_skills import DEFAULT_ROOT
    name = m["name"]
    folder = Path(DEFAULT_ROOT) / name
    if not (folder / "SKILL.md").is_file():
        return f"There's no installed skill guide called '{name}'."

    def act():
        (folder / "SKILL.md").unlink()
        if not any(folder.iterdir()):
            folder.rmdir()
        _live().pop("guide_" + name.replace("-", "_"), None)
        return f"Removed the skill guide '{name}'."
    return ask_first(session, "extensions", f"Remove the skill guide '{name}'", act)


def _channel_on(m, session):
    svc, tok, owner = m["svc"].lower(), m["token"], m["owner"]
    session["redact"] = (session.get("redact") or []) + [tok]  # the token never stays in the chat history

    def act():
        import keyring
        from ishti.channels import dm_channels, policy, service
        try:
            bot = dm_channels.APIS[svc](tok).me()
        except Exception as exc:
            return f"{svc.title()} didn't accept that token ({mcp_client.why(exc)}). Nothing was saved."
        keyring.set_password(dm_channels.KEYRING_SERVICE.format(svc), "bot", tok)
        policy.save({svc: {"enabled": True, "owner_id": owner}})
        service.get().start()
        return (f"{svc.title()} is on. Message your bot ({bot}) from your own account and I'll answer there. Only "
                f"account {owner} is obeyed; /kill stops everything, /lock locks me.")
    return ask_first(session, "extensions", f"Turn on {svc.title()} as a remote channel, obeying only account {owner}? "
                                            f"Its messages pass through {svc.title()}'s servers", act)


def _channel_off(m, session):
    from ishti.channels import policy, service
    svc = m["svc"].lower()
    cfg = policy.load().get(svc) or {}
    policy.save({svc: {**(cfg if isinstance(cfg, dict) else {}), "enabled": False}})
    service.get().start()  # follows the policy: the poller stops
    return f"{svc.title()} is off. I no longer read or answer messages there."


def _hook_add(m, session):
    from ishti import webhooks
    name, instruction = m["name"], m["instr"].strip()

    def act():
        hook_id, token = webhooks.create(name, instruction)
        session["redact"] = (session.get("redact") or []) + [token]  # shown now, masked in the saved chat
        return (f"Webhook '{name}' is ready. Call it with POST {webhooks.url(hook_id)} and the header "
                f"X-ISHTI-Token: {token}\nThe token is shown only now. Each call runs: {instruction}\n"
                f"It answers only on this PC unless remote access is on; anything that needs a yes waits in the bell.")
    return ask_first(session, "extensions", f"Create the webhook '{name}' that runs \"{instruction[:160]}\" whenever "
                                            f"it is called", act)


def _hook_list():
    from ishti import webhooks
    rows = [f"- {h['name']} ({hid}): {h['instruction'][:100]} - called {h.get('calls', 0)} time(s)"
            for hid, h in webhooks.hooks().items()]
    return "Webhooks:\n" + "\n".join(rows) if rows else "No webhooks yet. Say: create a webhook called <name> that <instruction>."


def _hook_del(m, session):
    from ishti import webhooks
    key = m["name"]
    if key not in webhooks.hooks() and not any(h.get("name") == key for h in webhooks.hooks().values()):
        return f"There's no webhook called '{key}'."
    return ask_first(session, "extensions", f"Delete the webhook '{key}' (its URL stops working)",
                     lambda: f"Deleted the webhook '{webhooks.delete(key)}'.")


def match(text):
    t = text.strip()
    return any(rx.match(t) for rx in (LIST, ADD, REMOVE, REFRESH, SECRET, INSTALL, UNINSTALL, CHANNEL_ON, CHANNEL_OFF,
                                      HOOK_ADD, HOOK_LIST, HOOK_DEL))


def run(text, session):
    t = text.strip()
    session = session if session is not None else {}
    for rx, fn in ((SECRET, _secret), (ADD, _add), (REMOVE, _remove), (INSTALL, _install), (UNINSTALL, _uninstall),
                   (CHANNEL_ON, _channel_on), (CHANNEL_OFF, _channel_off), (HOOK_ADD, _hook_add), (HOOK_DEL, _hook_del)):
        if m := rx.match(t):
            out = fn(m, session)
            return out if isinstance(out, dict) else {"reply": out, "sources": []}
    if HOOK_LIST.match(t):
        return {"reply": _hook_list(), "sources": []}
    if REFRESH.match(t):
        return {"reply": _refresh(), "sources": []}
    if LIST.match(t):
        return {"reply": _list(), "sources": []}
    return None


if __name__ == "__main__":
    assert _spec("npx -y @modelcontextprotocol/server-github") == {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"]}
    assert _spec("https://x.example/mcp") == {"url": "https://x.example/mcp"}
    assert match("add mcp server github npx -y pkg") and match("list mcp servers") and match("install skill from D:\\x")
    assert not match("add a server to the list")
    print("extensions self-check ok")
