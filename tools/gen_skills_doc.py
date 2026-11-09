"""Regenerate the counted sections of docs/SKILLS.md from ishti/skills/*/manifest.json (the hand-written counts drifted:
the doc listed removed skills). Rewrites the summary paragraph, the category table, the T2 list and the broad-network
list between their headings; the prose around them is left alone.

    venv\\Scripts\\python tools\\gen_skills_doc.py
"""
import collections
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "SKILLS.md"
TABLE_HEAD = "| Category | Count | Skills |\n|---|---|---|\n"


def manifests():
    for path in sorted((ROOT / "ishti" / "skills").glob("*/manifest.json")):
        yield json.loads(path.read_text(encoding="utf-8"))


def render():
    skills = list(manifests())
    status = collections.Counter(m.get("status", "working") for m in skills)
    tiers = collections.Counter(m.get("tier", "T0") for m in skills)
    disabled = sorted(m["name"] for m in skills if m.get("status") == "disabled")
    head = (f"Counted from `ishti/skills/*/manifest.json`: **{len(skills)} skills** have a manifest, "
            f"**{status.get('working', 0)} `working`**"
            + (f", **{len(disabled)} `disabled`** ({', '.join(f'`{d}`' for d in disabled)})" if disabled else "")
            + ". Tiers: " + ", ".join(f"**{t}: {tiers.get(t, 0)}**" for t in ("T0", "T1", "T2", "T3")) + ".")
    by_cat = collections.defaultdict(list)
    for m in skills:
        tag = "" if m.get("tier", "T0") == "T0" else f" ({m['tier']})"
        flag = " [disabled]" if m.get("status") == "disabled" else ""
        by_cat[m.get("category", "Other")].append(f"`{m['name']}`{tag}{flag}")
    rows = [f"| {cat} | {len(names)} | {', '.join(names)} |" for cat, names in sorted(by_cat.items())]
    table = TABLE_HEAD + "\n".join(rows)
    t2 = ", ".join(f"`{m['name']}`" for m in skills if m.get("tier") == "T2")
    net = ", ".join(f"`{m['name']}`" for m in skills if "*" in ((m.get("scope") or {}).get("net") or []))
    return head, table, t2, net


def main():
    text = DOC.read_text(encoding="utf-8")
    head, table, t2, net = render()
    text = re.sub(r"Counted from `ishti/skills/\*/manifest\.json`:.*?(?=\nDirectories in)", lambda _m: head, text, flags=re.S)
    text = re.sub(re.escape(TABLE_HEAD) + r"(?:\|.*\|\n?)+", lambda _m: table + "\n", text)
    text = re.sub(r"(### T2 skills \(always ask first\)\n\n).*?(?=\n\n###)", lambda m: m.group(1) + t2 + ".", text, flags=re.S)
    text = re.sub(r"(- Unrestricted network \(`net: \[\"\*\"\]`\): ).*?(?=\n- May launch)", lambda m: m.group(1) + net + ".",
                  text, flags=re.S)
    DOC.write_text(text, encoding="utf-8", newline="")
    print(head)


if __name__ == "__main__":
    main()
