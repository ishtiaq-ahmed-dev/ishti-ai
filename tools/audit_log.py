"""Append-only, hash-chained build log: docs/build/AUDIT_LOG.jsonl.

  python tools/audit_log.py add "<what happened>" [--actor NAME] [--refs ID,ID]   append one entry
  python tools/audit_log.py verify                                                  recompute the chain (exit 1 on any break)

Each line is {"i","ts","actor","event","refs","prev","hash"}; hash = sha256(prev + canonical JSON of the other fields),
so editing, deleting or reordering any earlier line breaks every hash after it. tools/verify_audit.py is the same check.

CAUTION: the store path below points into `docs/build/`, which this tree has retired. `add()` calls
`makedirs`, so the directory is recreated on first write and the log reappears as untracked files
rather than erroring. This tool and its store belong to the Prompt 1-7 build workflow, which is over:
repoint the path or retire the tool deliberately.
"""
import hashlib
import json
import os
import sys
import time

LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "build", "AUDIT_LOG.jsonl")
GENESIS = "0" * 64


def _hash(prev, rec):
    body = json.dumps({k: rec[k] for k in ("i", "ts", "actor", "event", "refs")}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256((prev + body).encode("utf-8")).hexdigest()


def read(path=LOG):
    try:
        with open(path, encoding="utf-8") as f:
            return [json.loads(ln) for ln in f if ln.strip()]
    except OSError:
        return []


def add(event, actor="claude", refs="", path=LOG):
    rows = read(path)
    prev = rows[-1]["hash"] if rows else GENESIS
    rec = {"i": len(rows) + 1, "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "actor": actor, "event": event,
           "refs": [r for r in refs.split(",") if r]}
    rec.update(prev=prev, hash=_hash(prev, rec))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def verify(path=LOG):
    """-> (ok, message)"""
    prev = GENESIS
    rows = read(path)
    for n, rec in enumerate(rows, 1):
        if rec.get("i") != n or rec.get("prev") != prev or rec.get("hash") != _hash(prev, rec):
            return False, f"chain broken at entry {n}"
        prev = rec["hash"]
    return True, f"{len(rows)} entries, chain intact"


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["add"] and len(a) >= 2:
        opt = {a[i]: a[i + 1] for i in range(2, len(a) - 1, 2)}
        print(add(a[1], opt.get("--actor", "claude"), opt.get("--refs", ""))["hash"][:12])
    elif a[:1] == ["verify"]:
        ok, msg = verify()
        print(("OK: " if ok else "FAIL: ") + msg)
        sys.exit(0 if ok else 1)
    else:
        sys.exit(__doc__)
