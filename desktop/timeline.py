"""Launch timeline: one `name epoch_seconds` line per stage in %LOCALAPPDATA%/ISHTI/logs/launch_timeline.log.

Stages (a)-(c) of startup are timed here, (d) - the backend's own stages - by ishti.lifecycle (data/boot_times.json).
tools/launch_probe.py reads this file; the owner can too when a start feels slow."""
import os
import time

PATH = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "ISHTI", "logs", "launch_timeline.log")


def reset():
    try:
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        open(PATH, "w").close()
    except OSError:
        pass


def mark(name):
    try:
        with open(PATH, "a", encoding="utf-8") as f:
            f.write(f"{name} {time.time():.3f}\n")
    except OSError:
        pass


def read():
    try:
        with open(PATH, encoding="utf-8") as f:
            out = {}
            for ln in f:
                if ln.strip():
                    k, v = ln.split()
                    out.setdefault(k, float(v))      # first occurrence: "loaded" fires again on every navigation
            return out
    except (OSError, ValueError):
        return {}
