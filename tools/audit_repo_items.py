"""Pass C (mechanical) completeness extractor for the 5 vendored repos.

Extracts every CLI command, HTTP route, slash command, skill/agent name, hook,
config/env key, README/CHANGELOG bullet, and settings label per repo into
audit/<repo>_items.csv so COMPLETENESS_AUDIT.md can diff Pass A/B/C.

Usage: python tools/audit_repo_items.py   (from the ISHTI repo root)
Safe: read-only over vendor/; writes only under audit/.
"""
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor"
OUT = ROOT / "audit"

rows = []  # (repo, kind, item, source)


def add(repo, kind, item, source):
    item = " ".join(str(item).split())
    if item:
        rows.append((repo, kind, item, source))


def read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def each_file(repo, patterns):
    base = VENDOR / repo
    for pat in patterns:
        for p in sorted(base.rglob(pat)):
            if ".git/" in p.as_posix() or "/node_modules/" in p.as_posix():
                continue
            yield p


# ---------------------------------------------------------------- open-dots
def open_dots():
    r = "open-dots"
    for p in each_file(r, ["server/app/routers/*.py"]):
        text = read(p)
        for m in re.finditer(r'@router\.(get|post|put|delete|patch)\("([^"]*)"', text):
            add(r, "http_route", f"{m.group(1).upper()} {m.group(2)}", f"{p.relative_to(VENDOR)}")
    cfg = read(VENDOR / r / "server/app/config.py")
    for m in re.finditer(r'os\.getenv\("([A-Z0-9_]+)"', cfg):
        add(r, "env_var", m.group(1), "server/app/config.py")
    chat = read(VENDOR / r / "server/app/services/chat.py") + read(VENDOR / r / "server/app/routers/chat.py")
    for m in re.finditer(r'"/(workspace|connector|search)\b[^"]*"', chat):
        add(r, "slash_command", m.group(0).strip('"'), "routers/chat.py")
    ws = read(VENDOR / r / "server/app/services/workspace_service.py")
    for m in re.finditer(r'startswith\("/(\w+)"', ws):
        add(r, "slash_command", "/" + m.group(1), "workspace_service.py")
    conn = read(VENDOR / r / "server/app/services/connector_actions.py")
    for m in re.finditer(r'"/connector\s+([\w_]+)"', conn):
        add(r, "slash_command", "/connector " + m.group(1), "connector_actions.py")
    gw = read(VENDOR / r / "server/app/services/action_gateway.py")
    for m in re.finditer(r'"([\w]+\.[\w]+)": ActionDefinition', gw):
        add(r, "registered_action", m.group(1), "action_gateway.py")
    for p in each_file(r, ["client/components/*.jsx", "client/components/*.js"]):
        add(r, "ui_component", p.stem, str(p.relative_to(VENDOR)))
    readme = read(VENDOR / r / "README.md")
    for line in readme.splitlines():
        if line.startswith("- ") or line.startswith("| "):
            add(r, "readme_bullet", line.lstrip("-| ").split("`")[0][:120], "README.md")


# ---------------------------------------------------------------- ecc
def ecc():
    r = "ecc"
    base = VENDOR / r
    for p in sorted((base / "commands").glob("*.md")):
        add(r, "slash_command", "/" + p.stem, f"commands/{p.name}")
        first = read(p).splitlines()
        desc = next((l for l in first if l.startswith("description:")), "")
        if desc:
            add(r, "command_description", desc[len("description:"):].strip()[:160], f"commands/{p.name}")
    for p in sorted((base / "agents").glob("*.md")):
        add(r, "agent", p.stem, f"agents/{p.name}")
    for d in sorted((base / "skills").iterdir()):
        if d.is_dir():
            add(r, "skill", d.name, "skills/")
    for d in sorted((base / "rules").iterdir()):
        if d.is_dir() and d.name != "README.md":
            add(r, "rule_pack", d.name, "rules/")
    hooks = json.loads(read(base / "hooks/hooks.json") or '{"hooks":{}}')
    for event, entries in (hooks.get("hooks") or {}).items():
        names = []
        for e in entries if isinstance(entries, list) else []:
            cmd = e.get("command") or (e.get("hooks") or [{}])
            names.append(str(cmd)[:80])
        add(r, "hook", f"{event} ({'; '.join(names) or 'n/a'})", "hooks/hooks.json")
    for p in sorted((base / "schemas").glob("*.json")):
        add(r, "schema", p.stem, f"schemas/{p.name}")
    for p in each_file(r, ["scripts/*.js", "scripts/*.sh", "scripts/*/*.js"]):
        text = read(p)
        if "process.env." in text:
            for m in set(re.findall(r"process\.env\.([A-Z0-9_]+)", text)):
                add(r, "env_var", m, str(p.relative_to(VENDOR)))
        m = re.search(r"Usage: ([^\n]+)", text)
        if m:
            add(r, "cli_usage", m.group(1)[:140], str(p.relative_to(VENDOR)))
    for name in ("README.md", "CHANGELOG.md", "docs/releases/2.0.0/release-notes.md"):
        text = read(base / name)
        for line in text.splitlines():
            if re.match(r"^\s*[-*] \S", line) or re.match(r"^\s*\d+\. \S", line):
                add(r, "doc_bullet", line.strip()[:140], name)


# ---------------------------------------------------------------- hey-jev
def hey_jev():
    r = "hey-jev"
    siri = read(VENDOR / r / "siri.py")
    for block in ("ACTIONS", "REPLIES", "QUESTIONS"):
        m = re.search(rf"{block} = \{{(.*?)\n\}}", siri, re.S)
        if m:
            for key in re.findall(r'^\s{4}"([a-z_]+)":', m.group(1), re.M):
                add(r, f"action_def_{block.lower()}", key, "siri.py")
    apps = json.loads(read(VENDOR / r / "apps.json") or "{}")
    for k in apps:
        add(r, "controllable_app", k, "apps.json")
    for m in re.finditer(r'add_argument\("([^"]+)"', siri):
        add(r, "cli_flag", m.group(1), "siri.py")
    for m in re.finditer(r'getenv\("([A-Z0-9_]+)"', siri + read(VENDOR / r / "secrets_store.py")):
        add(r, "env_var", m.group(1), "siri.py/secrets_store.py")
    secrets = read(VENDOR / r / "secrets_store.py")
    for m in re.finditer(r'"([A-Z0-9_]+(?:_API_KEY)?)"', secrets):
        if "KEY" in m.group(1):
            add(r, "secret_name", m.group(1), "secrets_store.py")
    ui = read(VENDOR / r / "assistant_ui.py")
    for m in re.finditer(r'(?:add\w*\(|title=|label=|text=)[(]?\s*["\']([A-Z][A-Za-z ]{2,24})["\']', ui):
        add(r, "settings_label", m.group(1), "assistant_ui.py")
    for m in re.finditer(r'(?i)(tab\w*|"home"|"keys"|"privacy"|"dictionary"|"apps"|"dictation")', ui):
        add(r, "ui_tab", m.group(1), "assistant_ui.py")
    for name in ("README.md",):
        for line in read(VENDOR / r / name).splitlines():
            if line.startswith("- **") or line.startswith("### "):
                add(r, "readme_bullet", line.lstrip("#- ").split("`")[0][:120], name)


# ---------------------------------------------------------------- agent-reach
def agent_reach():
    r = "agent-reach"
    cli = read(VENDOR / r / "agent_reach/cli.py")
    for m in re.finditer(r'add_parser\("([a-z-]+)"', cli):
        add(r, "cli_command", m.group(1), "agent_reach/cli.py")
    for p in sorted((VENDOR / r / "agent_reach/channels").glob("*.py")):
        if p.name != "__init__.py":
            add(r, "channel", p.stem, str(p.relative_to(VENDOR)))
    env = read(VENDOR / r / ".env.example")
    for line in env.splitlines():
        m = re.match(r"#?\s*([A-Z][A-Z0-9_]{3,})=", line.strip())
        if m:
            add(r, "env_var", m.group(1), ".env.example")
    for p in sorted((VENDOR / r / "agent_reach/guides").glob("setup-*.md")):
        add(r, "setup_guide", p.stem, str(p.relative_to(VENDOR)))
    for p in sorted((VENDOR / r / "tests").glob("test_*.py")):
        add(r, "test_file", p.stem, str(p.relative_to(VENDOR)))
    for name in ("README.md", "CHANGELOG.md"):
        for line in read(VENDOR / r / name).splitlines():
            if re.match(r"^\s*[-*] \S", line):
                add(r, "doc_bullet", line.strip()[:140], name)


# ---------------------------------------------------------------- coucou
def coucou():
    r = "coucou"
    for p in each_file(r, ["windows/src-tauri/src/*.rs"]):
        text = read(p)
        for m in re.finditer(r'#\[tauri::command\]\s*\n\s*(?:pub )?fn (\w+)', text):
            add(r, "tauri_command", m.group(1), str(p.relative_to(VENDOR)))
        for m in re.finditer(r'fn (handle_\w+|on_\w+)\(', text):
            add(r, "rs_handler", m.group(1), str(p.relative_to(VENDOR)))
    ag = read(VENDOR / r / "docs/AGENTS.md")
    for m in re.finditer(r"^\| `?(\w+)`? \|", ag, re.M):
        add(r, "hook_event", m.group(1), "docs/AGENTS.md")
    pollers = [p.stem for p in each_file(r, ["NotchBuddy/Sources/App/*Poller.swift"])]
    for name in pollers:
        add(r, "integration_poller", name, "NotchBuddy/Sources/App/")
    src = VENDOR / r / "windows/src"
    for p in sorted(src.rglob("*")):
        if p.is_file() and p.suffix in {".ts", ".css", ".html"}:
            add(r, "ui_module", str(p.relative_to(src)).replace("\\", "/"), "windows/src/")
    tray = read(VENDOR / r / "windows/src-tauri/src/tray.rs")
    for m in re.finditer(r'"([A-Z][A-Za-z ]{3,20})"', tray):
        add(r, "tray_label", m.group(1), "tray.rs")
    settings = read(VENDOR / r / "windows/settings.html") + read(VENDOR / r / "windows/src/settings/main.ts")
    for m in re.finditer(r"<label[^>]*>\s*([^<]{3,40})\s*<", settings):
        add(r, "settings_label", m.group(1).strip(), "windows/settings.html")
    for name in ("README.md", "CHANGELOG.md", "windows/README.md"):
        for line in read(VENDOR / r / name).splitlines():
            if re.match(r"^\s*[-*] \S", line):
                add(r, "doc_bullet", line.strip()[:140], name)


def main():
    if not VENDOR.is_dir():
        sys.exit("vendor/ missing - clone the five integration repos into vendor/ first")
    OUT.mkdir(exist_ok=True)
    for fn in (open_dots, ecc, hey_jev, agent_reach, coucou):
        before = len(rows)
        fn()
        repo = {open_dots: "open-dots", ecc: "ecc", hey_jev: "hey-jev",
                agent_reach: "agent-reach", coucou: "coucou"}[fn]
        with open(OUT / f"{repo}_items.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["repo", "kind", "item", "source"])
            for row in rows[before:]:
                w.writerow(row)
        print(f"{repo}: {len(rows) - before} items -> audit/{repo}_items.csv")
    print(f"TOTAL: {len(rows)}")


if __name__ == "__main__":
    main()
