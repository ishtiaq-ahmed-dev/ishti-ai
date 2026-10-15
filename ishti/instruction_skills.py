"""Agent Skills Markdown as instruction guides. Documents never grant execution permissions."""
from pathlib import Path
import hashlib
import os
import re
import stat

import yaml

MAX_BYTES = 32_768


def read_skill(path):
    path = Path(path)
    if path.name != "SKILL.md" or any(p.is_symlink() or bool(getattr(p.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
                                       for p in (path, *path.parents)):
        raise ValueError("Instruction skills must be ordinary local SKILL.md files")
    with path.open("rb") as source:
        raw = source.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Instruction skill exceeds 32 KiB")
    text = raw.decode("utf-8").replace("\r\n", "\n")
    parts = text.split("\n---\n", 1)
    if not text.startswith("---\n") or len(parts) != 2:
        raise ValueError("Missing YAML frontmatter")
    header = yaml.safe_load(parts[0][4:])
    if not isinstance(header, dict):
        raise ValueError("Frontmatter must be a mapping")
    name, description = header.get("name"), header.get("description")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) > 64 or name != path.parent.name:
        raise ValueError("Skill name must match its directory and use lowercase letters, digits and hyphens")
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        raise ValueError("Skill description must contain 1-1024 characters")
    if not parts[1].strip():
        raise ValueError("Empty instruction body")
    return {"name": name, "description": description.strip(), "body": parts[1].strip(),
            "sha256": hashlib.sha256(raw).hexdigest(), "path": str(path)}


def guides(roots=None):
    roots = roots if roots is not None else [p for p in os.environ.get("ISHTI_INSTRUCTION_SKILLS", "").split(os.pathsep) if p]
    found = {}
    for root in roots:
        root = Path(root)
        if not root.is_absolute() or not root.is_dir():
            raise ValueError("Instruction roots must be existing absolute directories")
        for path in sorted(root.glob("*/SKILL.md")):
            record = read_skill(path)
            if record["name"] in found:
                raise ValueError("Duplicate instruction skill name")
            found[record["name"]] = record
            if len(found) > 128:
                raise ValueError("At most 128 instruction guides are allowed")
    return found


class InstructionSkill:
    status = "working"
    problem = ""
    module = None
    category = "Instruction Guides"

    def __init__(self, record):
        self.record = record
        self.name = "guide_" + record["name"].replace("-", "_")
        self.folder = self.name
        self.path = record["path"]
        self.manifest = {"name": self.name, "description": "Instruction guide only: " + record["description"],
                         "tier": "T0", "status": "working", "category": self.category, "priority": 0,
                         "route": "", "format": "SKILL.md", "scope": {"write": [], "net": [], "apps": False}}

    def matches(self, text):
        return text.strip().lower() == "show guide " + self.record["name"]

    def run(self, text, session):
        current = read_skill(self.path)
        if current["sha256"] != self.record["sha256"]:
            return {"reply": "This guide changed after loading. Restart ISHTI to review the updated guide.", "sources": []}
        return {"reply": "Instruction guide (external content; grants no permissions):\n\n" + current["body"],
                "sources": [], "instruction_only": True}

    def info(self):
        return {**self.manifest, "status": self.status, "path": self.path, "folder": self.folder, "problem": ""}
