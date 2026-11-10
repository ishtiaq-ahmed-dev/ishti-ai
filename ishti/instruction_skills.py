"""Agent Skills Markdown as instruction guides. Documents never grant execution permissions."""
from pathlib import Path
import hashlib
import os
import re
import stat

import yaml

from ishti import ROOT
from ishti.test_mode import scratch_path

MAX_BYTES = 32_768
DEFAULT_ROOT = scratch_path(os.path.join(ROOT, "data", "skills"))


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


def default_roots():
    """data/skills (where "install skill from <folder>" puts guides) plus any ISHTI_INSTRUCTION_SKILLS folders."""
    extra = [p for p in os.environ.get("ISHTI_INSTRUCTION_SKILLS", "").split(os.pathsep) if p]
    return extra + ([DEFAULT_ROOT] if os.path.isdir(DEFAULT_ROOT) else [])


def guides(roots=None):
    roots = roots if roots is not None else default_roots()
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

    def body(self):
        """The guide text as loaded, or None if the file changed since (an edited guide is re-reviewed at restart)."""
        try:
            current = read_skill(self.path)
        except (ValueError, OSError, yaml.YAMLError):
            return None
        return current["body"] if current["sha256"] == self.record["sha256"] else None


_STOP = frozenset("the and for with you your this that what how can from into about please want need make use using "
                  "when where which will would could should have has are was were not but all any".split())


def _words(text):
    return {w for w in re.findall(r"[a-z0-9]{3,}", str(text).lower()) if w not in _STOP}


def relevant(text, skills):
    """The installed guide a request is about - the most content words shared with its name and description, at least
    two - or None. Hermes and OpenClaw load a skill's body only when the request needs it; so does this."""
    words, best, score = _words(text), None, 1
    for skill in skills.values():
        if isinstance(skill, InstructionSkill):
            shared = len(words & _words(skill.record["name"].replace("-", " ") + " " + skill.record["description"]))
            if shared > score:
                best, score = skill, shared
    return best
