"""Verify docs/build/AUDIT_LOG.jsonl (hash chain). Exit 0 = intact. Same check as `python tools/audit_log.py verify`.

Note: `docs/build/` is retired in this tree, so with no log present this reports an intact empty chain
rather than a missing store - see the caution in tools/audit_log.py."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit_log

ok, msg = audit_log.verify()
print(("OK: " if ok else "FAIL: ") + msg)
sys.exit(0 if ok else 1)
