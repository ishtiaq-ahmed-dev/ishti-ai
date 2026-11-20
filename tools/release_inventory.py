"""Generate local release inventory/SBOM inputs without reading credentials or user stores."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def inventory():
    pins = []
    for name in ("requirements.txt", "requirements-api.txt"):
        for line in (ROOT/name).read_text(encoding="utf-8").splitlines():
            if match := re.match(r"([\w.-]+)(?:\[[^]]*\])?==([^;\s#]+)", line):
                pins.append({"manifest": name, "name": match[1], "version": match[2]})
    installed = [{"name": d.metadata.get("Name"), "version": d.version,
                  "license_declared": (d.metadata.get("License-Expression") or d.metadata.get("License") or "unrecorded")[:1000]}
                 for d in importlib.metadata.distributions()]
    files = []
    for folder in ("ishti", "interface/app/src", "desktop", "tools", "voice"):
        for p in (ROOT/folder).rglob("*"):
            if not p.is_file() or p.suffix not in (".py", ".ts", ".tsx", ".css", ".svg") or any(x in p.parts for x in ("__pycache__", "node_modules", "_drafts")):
                continue
            files.append({"path": p.relative_to(ROOT).as_posix(), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=10).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True, timeout=10).strip())
    except (OSError, subprocess.SubprocessError):
        revision, dirty = "unavailable", None
    return {"schema": 1, "revision": revision, "working_tree_dirty": dirty, "dependency_pins": pins,
            "installed_packages_not_all_shipped": sorted(installed, key=lambda d: d["name"] or ""),
            "source_hashes": files, "npm_lock_sha256": hashlib.sha256((ROOT/'interface/app/package-lock.json').read_bytes()).hexdigest(),
            "commercial_review": {"main_license": "MIT",
                                  "third_party_review": "unresolved: verify shipped Rare UI redistribution permissions and all model/asset terms",
                                  "approval": False},
            "limits": "Inventory inputs, not a license clearance, vulnerability scan, signed release or complete packaged SBOM."}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    result = inventory()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Inventoried {len(result['source_hashes'])} source files and {len(result['dependency_pins'])} pins; commercial review remains open.")
