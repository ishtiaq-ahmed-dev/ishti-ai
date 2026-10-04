"""Build tests/task_suite/MASTER_250.json from the four ISHTI checklist files.

One entry per task row. The four files use one table shape:
    | # | Diff | Task | Expected action | Expected reply - A | Expected reply - B |
where A/B are success/failed or correct/wrong depending on the section header (recorded in
`reply_labels`). Ids must be 1-250 with no gaps and no duplicates - the script refuses to write
a partial list.
"""
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FILES = [
    r"C:\Users\PC\Downloads\ISHTI_100_task_test_checklist.md",
    r"C:\Users\PC\Downloads\ISHTI_50_task_test_checklist_part2.md",
    r"C:\Users\PC\Downloads\ISHTI_50_task_test_checklist_part3.md",
    r"C:\Users\PC\Downloads\ISHTI_50_task_test_checklist_part4.md",
    r"C:\Users\PC\Downloads\ISHTI_50_task_test_checklist_part5.md",
]

SECTION = re.compile(r"^##\s+\d+\.\s*(.+?)\s*$")


def parse_file(path):
    rows, category = [], ""
    with io.open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if line.startswith("## "):
                m = SECTION.match(line)
                if m:
                    # "Information lookup & search (1-10)" -> keep the range too
                    category = m.group(1).strip()
                continue
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not cells or not cells[0].isdigit():
                continue  # header / separator / non-task row
            if len(cells) < 6:
                rows.append({"_bad": line, "_cells": cells})
                continue
            tid = int(cells[0])
            rows.append({
                "id": tid,
                "difficulty": cells[1],
                "task_text": cells[2],
                "expected_action": cells[3],
                "reply_a": cells[4],
                "reply_b": cells[5],
                "category": category,
                "source_file": os.path.basename(path),
            })
    return rows


def main():
    all_rows, bad, seen = [], [], {}
    for path in FILES:
        if not os.path.isfile(path):
            sys.exit(f"missing checklist file: {path}")
        for r in parse_file(path):
            if "_bad" in r:
                bad.append((os.path.basename(path), r["_cells"]))
                continue
            if r["id"] in seen:
                sys.exit(f"duplicate id {r['id']} in {path} (also in {seen[r['id']]})")
            seen[r["id"]] = os.path.basename(path)
            all_rows.append(r)
    if bad:
        sys.exit(f"{len(bad)} row(s) did not have 6 cells: {bad[:5]}")
    all_rows.sort(key=lambda r: r["id"])
    ids = [r["id"] for r in all_rows]
    total_count = len(all_rows)
    target_count = 300 if any("part5" in p for p in FILES) else 250
    missing = sorted(set(range(1, target_count + 1)) - set(ids))
    extra = sorted(set(ids) - set(range(1, target_count + 1)))
    if total_count != target_count or missing or extra:
        sys.exit(f"count check failed: got {total_count}, missing={missing[:10]}, extra={extra[:10]}")

    # label the two reply columns and derive requires_confirmation from the action/table
    for r in all_rows:
        a, b = r.pop("reply_a"), r.pop("reply_b")
        low = (r["expected_action"] + " " + r["task_text"]).lower()
        if re.search(r"\bt2\b|confirmation-required|exact recap|explicit yes", low):
            r["requires_confirmation"] = True
        else:
            r["requires_confirmation"] = False
        r["expected_reply_a"] = a
        r["expected_reply_b"] = b
    out300 = os.path.join(ROOT, "tests", "task_suite", "MASTER_300.json")
    with io.open(out300, "w", encoding="utf-8") as f:
        json.dump({"count": len(all_rows), "tasks": all_rows}, f, ensure_ascii=False, indent=1)
    out250 = os.path.join(ROOT, "tests", "task_suite", "MASTER_250.json")
    with io.open(out250, "w", encoding="utf-8") as f:
        json.dump({"count": min(len(all_rows), 250), "tasks": all_rows[:250]}, f, ensure_ascii=False, indent=1)
    cats = {}
    for r in all_rows:
        cats.setdefault(r["category"], []).append(r["id"])
    print(f"MASTER_250.json written: {len(all_rows)} tasks")
    print(f"categories: {len(cats)}")
    for c, v in cats.items():
        print(f"  {v[0]:>3}-{v[-1]:<3} {c}")
    print("confirmation-required:", sum(1 for r in all_rows if r["requires_confirmation"]))


if __name__ == "__main__":
    main()
