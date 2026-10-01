"""Classify candidate secret-looking lines WITHOUT printing any value.

Used for the issue hunt: the rule is "never print a secret value", so this only reports the SHAPE of the
right-hand side (env lookup / structural / a literal long enough to be worth a human look).
"""
import re
import sys

LOOKUP = re.compile(r"os\.environ|getenv|settings|\.get\(|vault|read_text|\bargs\b|\bvalue\b|\btext\b"
                    r"|\bself\b|\bcfg\b|\bitem\b|\bdata\b", re.I)
EMPTY = {"", "{}", "None", "''", '""', "()", "[]"}

for path, ln in [(a.split(":")[0], int(a.split(":")[1])) for a in sys.argv[1:]]:
    try:
        line = open(path, encoding="utf-8").read().splitlines()[ln - 1]
    except (OSError, IndexError) as exc:
        print(f"{path}:{ln}  unreadable ({type(exc).__name__})")
        continue
    rhs = line.split("=", 1)[1].strip() if "=" in line else ""
    if LOOKUP.search(rhs):
        kind = "env/lookup - not a literal"
    elif rhs in EMPTY:
        kind = "empty/structural - not a secret"
    elif len(rhs) >= 8 and re.fullmatch(r"[\"'].*[\"']", rhs):
        kind = f"STRING LITERAL of {len(rhs)} chars - human must review"
    else:
        kind = f"expression ({rhs[:24]}...) - not a literal"
    print(f"{path}:{ln}  {kind}")
