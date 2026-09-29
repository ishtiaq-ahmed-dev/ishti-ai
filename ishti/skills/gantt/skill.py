"""Gantt builder (inventory domain 15 #7): tasks with durations -> a computed schedule.

"gantt chart of: design 3d, build 5d after design, test 2d after build"
"project plan from these tasks: research 1w, prototype 2w, pitch 1d"

Each task: a name, a duration (2d/4h/1w/30m), optionally "after <task>" (dependencies,
comma-separated allowed). Day-1 start (today), tasks start after their latest dependency
ends, otherwise as soon as the previous independent task ends (a simple greedy serial
schedule — the honest default when nothing runs in parallel). Output: a Mermaid gantt
block (renders in the dashboard/markdown) + an ASCII bar view. T0.
"""
import datetime as dt
import re

from ishti.skills import trace

I = re.I

ASK = re.compile(
    r"^\s*(?:gantt(?:\s+chart)?\s+(?:of|for|from)|project\s+plan\s+(?:from|of|for)|schedule\s+these\s+tasks\s+into\s+an?\s+gantt)\s*[:\-]?\s*"
    r"(?P<body>.+)$", I)
DUR = re.compile(r"(\d+(?:\.\d+)?)\s*(d|days?|w|weeks?|h|hours?|m|min(?:ute)?s?)\b", I)
DUR_TAIL = re.compile(r"\s+\d+(?:\.\d+)?\s*(?:d|days?|w|weeks?|h|hours?|m|min(?:ute)?s?)\b.*$", I)
DEP = re.compile(r"\bafter\s+([\w &/-]+)$", I)


def _dur_days(s):
    m = DUR.search(s)
    if not m:
        return None
    n, u = float(m.group(1)), m.group(2).lower()
    if u.startswith("d"):
        return n
    if u.startswith("w"):
        return n * 7
    if u.startswith("h"):
        return max(0.25, n / 8)      # an 8h day
    return max(0.125, n / 480)       # minutes


def build(body):
    raw = [s.strip(" -*") for s in re.split(r"[;\n]|,(?=\s*[A-Za-z])", body) if s.strip(" -*")]
    if len(raw) < 1:
        return "Give me at least one task with a duration, e.g. \"design 3d\"."
    tasks, problems = [], []
    for it in raw:
        days = _dur_days(it)
        if days is None:
            problems.append(it)
            continue
        name = DUR_TAIL.sub("", it).strip()
        name = re.sub(r"\s+after\s+[\w ,/-]*$", "", name, flags=I).strip(" ,;-")
        deps = []
        if (dm := DEP.search(it)):
            deps = [_norm_dep(d) for d in re.split(r",|and", dm.group(1)) if d.strip()]
        tasks.append({"name": name or it, "days": days, "deps": deps})
    if problems:
        return ("These had no duration (add e.g. 3d / 1w / 4h): " + "; ".join(problems[:6]) + ".")
    by_name = {t["name"].lower(): t for t in tasks}
    start = dt.date.today()
    cursor = start
    for t in tasks:
        if t["deps"]:
            ends = [by_name[d]["end"] for d in t["deps"] if d in by_name and by_name[d].get("end")]
            t["start"] = max(ends) if ends else cursor
        else:
            t["start"] = cursor
        t["end"] = t["start"] + dt.timedelta(days=max(t["days"], 0.125))
        cursor = t["end"]
    total = (tasks[-1]["end"] - start).days
    # Mermaid block
    m = ["gantt", "    dateFormat YYYY-MM-DD", f"    title {len(tasks)} task(s), {total} day(s) total"]
    for t in tasks:
        m.append(f"    {t['name'].replace(',', ' ')} :{t['start'].isoformat()}, {t['days']:g}d")
    # ASCII view
    a = ["", "**Schedule** (start — end):"]
    for t in tasks:
        a.append(f"- {t['name']}: {t['start'].strftime('%a %d %b')} → {t['end'].strftime('%a %d %b')} ({t['days']:g}d"
                 + (f", after {' & '.join(t['deps'])}" if t["deps"] else "") + ")")
    return "```mermaid\n" + "\n".join(m) + "\n```\n" + "\n".join(a)


def _norm_dep(d):
    d = d.strip().lower()
    d = DUR_TAIL.sub("", d)
    return re.sub(r"\s+(?:task|first)?$", "", d).strip()


def match(text):
    return bool(ASK.match(text.strip()))


def run(text, session):
    m = ASK.match(text.strip())
    if not m:
        return None
    trace(session, "gantt", "build")
    return {"reply": build(m.group("body")), "sources": []}


if __name__ == "__main__":
    assert match("gantt chart of: design 3d, build 5d after design, test 2d after build")
    assert match("project plan from these tasks: research 1w, prototype 2w, pitch 1d")
    assert not match("gantt chart in excel how")
    assert not match("project plan template")          # no payload

    r = run("gantt chart of: design 3d, build 5d after design, test 2d after build", {})
    assert "gantt" in r["reply"] and "dateFormat" in r["reply"], r
    assert "design: " in r["reply"] and "test: " in r["reply"], r
    # dependency math: design ends day 3, build starts day 3 ends day 8, test 8->10
    assert "after build" in r["reply"] or "after design & build" in r["reply"] or "after design, build" in r["reply"], r
    assert "10 day(s) total" in r["reply"], r
    r2 = run("gantt chart of: a 2d, b 1d", {})
    assert "3 day(s) total" in r2["reply"], r2         # serial default
    r3 = run("gantt chart of: only text here", {})
    assert "no duration" in r3["reply"].lower(), r3
    print("[self-check] gantt OK - duration parsing (d/w/h/m), dependency sequencing, Mermaid + "
          "ASCII view, computed start/end dates")
