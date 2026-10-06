"""A second @get/@post on the same path silently shadows the first (the later UI work hit this 4 times).
The four below were merged on purpose (legacy handler delegates to / matches the contract handler); any new one fails."""
import glob
import re
from collections import defaultdict

KNOWN = {("get", "/api/agents"), ("get", "/api/connectors"),
         ("post", "/api/connectors/{}/connect"), ("post", "/api/connectors/{}/disconnect")}


def test_no_new_duplicate_routes():
    seen = defaultdict(list)
    for f in glob.glob("ishti/**/*.py", recursive=True) + glob.glob("interface/web_ui/*.py"):
        for line in open(f, encoding="utf-8", errors="ignore"):
            m = re.match(r'\s*@\w+\.(get|post|put|delete|patch)\(\s*"([^"]+)"', line)
            if m:
                seen[(m.group(1), re.sub(r"\{[^}]+\}", "{}", m.group(2)))].append(f)
    dup = {k for k, v in seen.items() if len(v) > 1}
    assert dup <= KNOWN, f"new shadowed routes: {sorted(dup - KNOWN)}"
