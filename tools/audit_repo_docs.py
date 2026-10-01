"""Pass B (independent docs-level) enumerator for the 5 vendored repos.

Reads ONLY human-facing docs (README*, CHANGELOG*, docs/*.md top-level guides,
release notes) without consulting REPO_FEATURE_MATRIX.md, and writes
audit/<repo>_passB.csv: section headings + feature bullets + env-var table rows.

Usage: python tools/audit_repo_docs.py   (from the ISHTI repo root)
"""
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor"
OUT = ROOT / "audit"

DOC_SETS = {
    "open-dots": ["README.md"],
    "ecc": ["README.md", "CHANGELOG.md", "TROUBLESHOOTING.md", "COMMANDS-QUICK-REF.md",
            "docs/releases/2.0.0/release-notes.md"],
    "hey-jev": ["README.md"],
    "agent-reach": ["README.md", "CHANGELOG.md", "docs/README_en.md", "docs/install.md",
                    "docs/update.md", "docs/troubleshooting.md"],
    "coucou": ["README.md", "CHANGELOG.md", "windows/README.md", "docs/INTEGRATIONS.md"],
}

rows = []


def add(repo, kind, item, source):
    item = " ".join(str(item).split())
    if item:
        rows.append((repo, kind, item, source))


def main():
    OUT.mkdir(exist_ok=True)
    for repo, names in DOC_SETS.items():
        before = len(rows)
        for name in names:
            p = VENDOR / repo / name
            if not p.exists():
                continue
            in_table = False
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                if re.match(r"^#{1,4} \S", line):
                    add(repo, "doc_section", line.lstrip("#").strip(), name)
                elif re.match(r"^\s*[-*] \S", line):
                    add(repo, "doc_bullet", line.strip("-* ").strip()[:160], name)
                elif re.match(r"^\|\s*\S", line):
                    if re.search(r"^\|\s*[-:| ]+\|\s*$", line):
                        continue
                    if not in_table:
                        in_table = True
                        add(repo, "doc_table_row", line.strip("| ")[:160], name)
                    else:
                        add(repo, "doc_table_row", line.strip("| ")[:160], name)
                elif line.strip() == "":
                    in_table = False
        with open(OUT / f"{repo}_passB.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["repo", "kind", "item", "source"])
            for row in rows[before:]:
                w.writerow(row)
        print(f"{repo}: {len(rows) - before} doc items -> audit/{repo}_passB.csv")
    print(f"TOTAL: {len(rows)}")


if __name__ == "__main__":
    main()
