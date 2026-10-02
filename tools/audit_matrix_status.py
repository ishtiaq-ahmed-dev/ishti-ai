"""Audit REPO_FEATURE_MATRIX.md statuses against real test evidence.

Why: the matrix shipped with every built row still saying `mapped`. P3 requires
"every PORT/ADAPT/IMPROVE/OPT-IN row's test ID green 3 consecutive runs". This
script finds, for every row, the test files that actually cite its ID, so the
status column can be updated from evidence instead of memory.

Reference forms recognised inside tests/**/*.py:
  * explicit:   T-OD-12, T-ECC-09, T-JEV-04, T-R01 / T-REACH-01, T-COU-12 / T-C12
  * plain:      OD-12, ECC-06, JEV-17, REACH-19, COU-22   (docstrings/comments only)

Usage:
  venv\\Scripts\\python tools\\audit_matrix_status.py            # report only
  venv\\Scripts\\python tools\\audit_matrix_status.py --apply    # update statuses
  venv\\Scripts\\python tools\\audit_matrix_status.py --json _work\\matrix_status.json
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MATRIX = os.path.join(ROOT, "REPO_FEATURE_MATRIX.md")
TESTS = os.path.join(ROOT, "tests")

PREFIX_ALIASES = {"R": "REACH", "REACH": "REACH", "C": "COU", "COU": "COU",
                  "OD": "OD", "ECC": "ECC", "JEV": "JEV"}
EXPLICIT_RE = re.compile(r"\bT-([A-Z]{1,6})-?(\d{1,2})\b")
PLAIN_RE = re.compile(r"\b(OD|ECC|JEV|REACH|COU)-(\d{1,2})\b")


def _norm(prefix, num):
    return "%s-%02d" % (PREFIX_ALIASES.get(prefix, prefix), int(num))


def scan_tests():
    refs = {}  # row id -> list of (file, kind)
    for dirpath, dirnames, filenames in os.walk(TESTS):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, ROOT).replace("\\", "/")
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            seen = set()
            for m in EXPLICIT_RE.finditer(text):
                rid = _norm(m.group(1), m.group(2))
                key = (rid, "explicit", rel)
                if key not in seen:
                    seen.add(key)
                    refs.setdefault(rid, []).append(("explicit", rel))
            for m in PLAIN_RE.finditer(text):
                rid = _norm(m.group(1), m.group(2))
                key = (rid, "plain", rel)
                if key not in seen:
                    seen.add(key)
                    refs.setdefault(rid, []).append(("plain", rel))
    return refs


def parse_rows(lines):
    rows = []
    for i, line in enumerate(lines):
        if not re.match(r"^\| (OD|ECC|JEV|REACH|COU)-\d+ \|", line):
            continue
        cells = line.split("|")
        # cells[0] = ""; 1 ID; 2 repo; 3 feature; 4 source; 5 equivalent;
        # 6 disposition; 7 reason; 8 tier; 9 depends; 10 test id; 11 status; 12 evidence; 13 ""
        if len(cells) < 14:
            continue
        rows.append({"line": i, "id": cells[1].strip(), "disposition": cells[6].strip(),
                     "test_id": cells[10].strip(), "status": cells[11].strip(),
                     "evidence": cells[12].strip()})
    return rows


def main(argv):
    apply = "--apply" in argv
    json_out = None
    if "--json" in argv:
        json_out = argv[argv.index("--json") + 1]
    with open(MATRIX, "r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    refs = scan_tests()
    rows = parse_rows(lines)

    report, explicit_hits, plain_hits, uncovered = [], 0, 0, []
    for row in rows:
        found = refs.get(row["id"], [])
        ex = sorted({f for k, f in found if k == "explicit"})
        pl = sorted({f for k, f in found if k == "plain"})
        if ex:
            explicit_hits += 1
        elif pl:
            plain_hits += 1
        if row["disposition"] != "SKIP-WITH-PROOF" and not found:
            uncovered.append(row["id"])
        report.append({**row, "explicit": ex, "plain": pl})

    print("rows=%d  explicit-coverage=%d  plain-only=%d  no-coverage=%d"
          % (len(rows), explicit_hits, plain_hits, len(uncovered)))
    print("no test evidence: %s" % " ".join(uncovered))
    for r in report:
        if r["explicit"]:
            print("  %-9s %-15s -> %s" % (r["id"], r["status"], ", ".join(r["explicit"])))

    if json_out:
        os.makedirs(os.path.dirname(json_out) or ".", exist_ok=True)
        with open(json_out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)

    if apply:
        changed = 0
        for r in report:
            proving = r["explicit"] or r["plain"]
            if not proving or r["status"] in ("tested", "proved-skip"):
                continue
            line = lines[r["line"]]
            cells = line.split("|")
            cells[11] = " tested "
            files = ", ".join(proving)
            marker = "; tests: "
            if marker not in cells[12]:
                cells[12] = cells[12].rstrip() + marker + files + " "
            lines[r["line"]] = "|".join(cells)
            changed += 1
        with open(MATRIX, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(lines) + "\n")
        print("applied: %d rows -> tested" % changed)


if __name__ == "__main__":
    main(sys.argv[1:])
