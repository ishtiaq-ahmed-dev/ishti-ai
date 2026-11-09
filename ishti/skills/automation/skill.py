"""Automation: "when X, do Y" rules, checked every 20 s by one background thread the server starts.

Conditions (plain words): a new file appears in a folder (optionally of a type), battery below N% (on battery),
free disk space on a drive below N GB, CPU or memory above N%, an app starts or closes. The action is any request
ISHTI understands - it runs like a scheduled task and its answer is announced; "it" / "the file" in the action
becomes the new file's path. A rule fires once when its condition becomes true and re-arms after it's false again
(file rules fire once per new file), so a low battery doesn't announce every 20 seconds.
Rules live in the facts DB (table automations)."""
import glob
import json
import os
import re
import threading
import time

from ishti.facts import get_facts
from ishti.skills import trace

I = re.I
TICK = 20
RULE = re.compile(r"^\s*(?:please\s+)?(?:when(?:ever)?|if|each\s+time|every\s+time)\s+(?P<cond>.+?)\s*(?:,\s*(?:then\s+)?|\s+then\s+)(?P<action>.+)$", I)
LIST = re.compile(r"\b(?:list|show|what\s+are)\s+(?:my\s+|the\s+)?(?:automations?|rules|triggers)\b", I)
DELETE = re.compile(r"\b(?:delete|remove|stop|cancel)\s+(?:the\s+)?(?:automation|rule|trigger)\s+(?:#|number\s+)?(?P<n>\d+)\b", I)
PAUSE = re.compile(r"\b(?P<act>pause|resume|enable|disable)\s+(?:the\s+)?(?:automation|rule|trigger)\s+(?:#|number\s+)?(?P<n>\d+)\b", I)
EXT = {"pdf": ".pdf", "pdfs": ".pdf", "image": (".png", ".jpg", ".jpeg", ".webp"), "images": (".png", ".jpg", ".jpeg", ".webp"),
       "photo": (".png", ".jpg", ".jpeg", ".heic"), "photos": (".png", ".jpg", ".jpeg", ".heic"), "video": (".mp4", ".mov", ".mkv"),
       "videos": (".mp4", ".mov", ".mkv"), "zip": ".zip", "zips": ".zip", "document": (".pdf", ".docx", ".doc", ".txt"),
       "documents": (".pdf", ".docx", ".doc", ".txt"), "excel": (".xlsx", ".xls", ".csv"), "song": (".mp3", ".m4a"), "music": (".mp3", ".m4a")}


def parse_condition(cond):
    """{"kind", ...} or None."""
    c = cond.strip()  # original case: folder paths keep theirs
    if m := re.search(r"^(?:the\s+)?(?:log\s+(?:file\s+)?)?[\"']?(?P<path>[a-z]:[\\/][^\"']+?\.\w{2,5})[\"']?\s+(?:contains|shows|has|logs|gets)\s+"
                      r"(?:an?\s+)?[\"']?(?P<text>[^\"']+?)[\"']?$", c, I):
        return {"kind": "log", "path": m["path"], "text": m["text"].strip()}  # log monitor: new matching lines
    if m := re.search(r"\b(?:a|any|new)?\s*(?:new\s+)?(?P<type>\w+)?\s*(?:file|download)?s?\s+(?:appears?|is\s+(?:added|saved|downloaded)|"
                      r"arrives?|lands?|comes?)\s+(?:in|to|into)\s+(?:my\s+|the\s+)?(?P<where>.+)$", c, I):
        from ishti.skills.folders.skill import folder_in
        folder = m["where"].strip() if os.path.isdir(m["where"].strip()) else folder_in(m["where"])[1]
        if not folder:
            return None
        ext = EXT.get((m["type"] or "").lower())
        return {"kind": "file", "folder": folder, "ext": [ext] if isinstance(ext, str) else list(ext or [])}
    if m := re.search(r"\bbattery\s+(?:is\s+|goes\s+|drops\s+|falls\s+|gets\s+)?(?:below|under|less\s+than|lower\s+than)\s+(?P<n>\d{1,2})\s*%?", c, I):
        return {"kind": "battery", "below": int(m["n"])}
    if m := re.search(r"\b(?:disk|drive|space)\b.*?(?:(?P<d>[a-z])\s*(?::|drive)).*?(?:below|under|less\s+than)\s+(?P<n>\d+)\s*gb|"
                      r"\b(?P<d2>[a-z])\s*(?::|drive)\s+(?:has\s+)?(?:less\s+than|below|under)\s+(?P<n2>\d+)\s*gb", c, I):
        return {"kind": "disk", "drive": (m["d"] or m["d2"]).upper() + ":\\", "below": int(m["n"] or m["n2"])}
    if m := re.search(r"\b(?P<what>cpu|memory|ram)\b.*?(?:above|over|more\s+than|higher\s+than)\s+(?P<n>\d{1,3})\s*%?", c, I):
        return {"kind": "cpu" if m["what"].lower() == "cpu" else "memory", "above": int(m["n"])}
    if m := re.search(r"^(?:i\s+)?(?:open|start|launch|run)s?\s+(?P<app>[\w .-]+?)$|^(?P<app2>[\w .-]+?)\s+(?:opens|starts|is\s+opened|launches|runs)$", c, I):
        return {"kind": "app_start", "app": (m["app"] or m["app2"]).strip().removesuffix(".exe")}
    if m := re.search(r"^(?:i\s+)?(?:close|quit|exit)s?\s+(?P<app>[\w .-]+?)$|^(?P<app2>[\w .-]+?)\s+(?:closes|is\s+closed|exits|stops)$", c, I):
        return {"kind": "app_stop", "app": (m["app"] or m["app2"]).strip().removesuffix(".exe")}
    return None


def describe(cond):
    k = cond["kind"]
    return {"log": lambda: f"{cond['path']} shows \"{cond['text']}\"",
            "file": lambda: f"a new {'/'.join(cond['ext']) or ''} file appears in {cond.get('folder')}".replace("new  file", "new file"),
            "battery": lambda: f"battery is below {cond['below']}% (unplugged)",
            "disk": lambda: f"{cond['drive']} has less than {cond['below']} GB free",
            "cpu": lambda: f"CPU is above {cond['above']}%", "memory": lambda: f"memory is above {cond['above']}%",
            "app_start": lambda: f"{cond['app']} starts", "app_stop": lambda: f"{cond['app']} closes"}[k]()


def _running(app):
    import psutil
    want = app.lower().replace(" ", "")
    return any(want in (p.info["name"] or "").lower().replace(" ", "") for p in psutil.process_iter(["name"]))


def check(cond, state):
    """Condition true right now? state carries per-rule memory (seen files). Returns (true, detail or None)."""
    import psutil
    k = cond["kind"]
    if k == "file":
        now = {p for p in glob.glob(os.path.join(cond["folder"], "*"))
               if os.path.isfile(p) and (not cond["ext"] or p.lower().endswith(tuple(cond["ext"])))
               and not p.endswith((".crdownload", ".tmp", ".part"))}
        seen = set(state.get("seen", now))  # first look: what's already there doesn't count
        state["seen"] = sorted(now)
        new = sorted(now - seen)
        return bool(new), new
    if k == "log":  # read only what was appended since the last look; the first look just remembers the end
        path, size = cond["path"], os.path.getsize(cond["path"]) if os.path.isfile(cond["path"]) else 0
        start = state.get("offset", size)
        if size < start:  # rotated / truncated: start over
            start = 0
        new = ""
        if size > start:
            with open(path, encoding="utf-8", errors="replace") as f:
                f.seek(start)
                new = f.read(1_000_000)
        state["offset"] = size
        hits = [l.strip() for l in new.splitlines() if cond["text"].lower() in l.lower()]
        return bool(hits), (f"{len(hits)} new line(s): " + " | ".join(hits[:3])[:300]) if hits else None
    if k == "battery":
        b = psutil.sensors_battery()
        return bool(b and not b.power_plugged and b.percent < cond["below"]), f"{b.percent:.0f}%" if b else None
    if k == "disk":
        free = psutil.disk_usage(cond["drive"]).free / 1e9
        return free < cond["below"], f"{free:.1f} GB free"
    if k == "cpu":
        v = psutil.cpu_percent(interval=1)
        return v > cond["above"], f"{v:.0f}%"
    if k == "memory":
        v = psutil.virtual_memory().percent
        return v > cond["above"], f"{v:.0f}%"
    if k in ("app_start", "app_stop"):
        up = _running(cond["app"])
        return (up if k == "app_start" else (state.get("was_up") and not up)), state.update(was_up=up)
    return False, None


# ---------------------------------------------------------------- storage
def _db():
    f = get_facts()
    with f.lock, f.db:
        f.db.execute("CREATE TABLE IF NOT EXISTS automations(id INTEGER PRIMARY KEY, cond TEXT, action TEXT, created REAL, "
                     "active INTEGER DEFAULT 1, state TEXT DEFAULT '{}', armed INTEGER DEFAULT 1, fired INTEGER DEFAULT 0)")
    return f


def rules(active_only=False):
    f = _db()
    with f.lock:
        rows = f.db.execute("SELECT id, cond, action, active, state, armed, fired FROM automations ORDER BY id").fetchall()
    return [{"id": r[0], "cond": json.loads(r[1]), "action": r[2], "active": r[3], "state": json.loads(r[4] or "{}"),
             "armed": r[5], "fired": r[6]} for r in rows if r[3] or not active_only]


def _save(rule):
    f = _db()
    with f.lock, f.db:
        f.db.execute("UPDATE automations SET state=?, armed=?, fired=? WHERE id=?",
                     (json.dumps(rule["state"]), rule["armed"], rule["fired"], rule["id"]))


# ---------------------------------------------------------------- the loop
MAX_FIRES_PER_HOUR = 20      # a rule that triggers itself (its action creates the file it watches) cannot run away
_RUNNING = threading.BoundedSemaphore(3)  # at most 3 automation actions run at once; the rest wait their turn


def _allow_fire(rule) -> bool:
    now = time.time()
    fires = [t for t in rule["state"].get("_fires", []) if now - t < 3600]
    if len(fires) >= MAX_FIRES_PER_HOUR:
        rule["state"]["_fires"] = fires
        return False
    rule["state"]["_fires"] = fires + [now]
    return True


def _run_bounded(announce, run_task, action):
    with _RUNNING:
        announce(run_task(action))


def tick(announce, run_task):
    """One pass over the active rules; returns the actions started (for tests)."""
    started = []
    for rule in rules(active_only=True):
        try:
            true, detail = check(rule["cond"], rule["state"])
        except Exception as exc:
            print(f"[automation] rule {rule['id']}: {exc}")
            continue
        if rule["cond"]["kind"] == "file":
            for path in (detail or [])[:10]:
                if not _allow_fire(rule):
                    break
                action = re.sub(r"\b(?:it|the\s+file|that\s+file|this\s+file)\b", lambda _, p=path: f'"{p}"', rule["action"], flags=I)
                started.append(action)
            rule["fired"] += len(detail or [])
        elif rule["cond"]["kind"] == "log":
            if true and _allow_fire(rule):  # every new batch of matching lines is news (a log doesn't "stay" true)
                started.append(rule["action"])
                rule["fired"] += 1
                announce(f"Automation #{rule['id']}: {describe(rule['cond'])} - {detail}")
        elif true and rule["armed"] and _allow_fire(rule):
            started.append(rule["action"])
            rule["armed"], rule["fired"] = 0, rule["fired"] + 1
            detail and announce(f"Automation #{rule['id']}: {describe(rule['cond'])} ({detail}).")
        elif not true:
            rule["armed"] = 1  # re-arm once the condition clears
        _save(rule)
    for action in started:
        threading.Thread(target=_run_bounded, args=(announce, run_task, action), daemon=True).start()
    return started


def start(announce, run_task):
    def loop():
        while True:
            try:
                tick(announce, run_task)
            except Exception as exc:
                print(f"[automation] {exc}")
            time.sleep(TICK)
    threading.Thread(target=loop, daemon=True, name="automation").start()


# ---------------------------------------------------------------- the skill
def match(text):
    m = RULE.match(text)
    return bool((m and parse_condition(m["cond"])) or LIST.search(text) or DELETE.search(text) or PAUSE.search(text))


def run(text, session):
    f = _db()
    if m := DELETE.search(text):
        with f.lock, f.db:
            n = f.db.execute("DELETE FROM automations WHERE id=?", (int(m["n"]),)).rowcount
        return {"reply": f"Deleted automation #{m['n']}." if n else f"There's no automation #{m['n']}.", "sources": []}
    if m := PAUSE.search(text):
        on = m["act"].lower() in ("resume", "enable")
        with f.lock, f.db:
            n = f.db.execute("UPDATE automations SET active=? WHERE id=?", (int(on), int(m["n"]))).rowcount
        return {"reply": (f"Automation #{m['n']} {'resumed' if on else 'paused'}." if n else f"There's no automation #{m['n']}."),
                "sources": []}
    if LIST.search(text):
        rs = rules()
        if not rs:
            return {"reply": "No automations yet - e.g. \"when a pdf appears in downloads, move it to D:\\Docs\".", "sources": []}
        return {"reply": "Your automations:\n" + "\n".join(
            f"{r['id']}. When {describe(r['cond'])} -> {r['action']}" + ("" if r["active"] else " (paused)")
            + (f" - ran {r['fired']}x" if r["fired"] else "") for r in rs), "sources": []}
    m = RULE.match(text)
    if not m:
        return None
    cond = parse_condition(m["cond"])
    action = m["action"].strip(" .")
    state = {}
    if cond["kind"] == "file":
        check(cond, state)  # remember what's already in the folder: only new files count

    def create():
        with f.lock, f.db:
            cur = f.db.execute("INSERT INTO automations(cond, action, created, state) VALUES(?,?,?,?)",
                               (json.dumps(cond), action, time.time(), json.dumps(state)))
        trace(session, "automation", f"when {describe(cond)} -> {action}")
        return (f"Automation #{cur.lastrowid}: when {describe(cond)}, I'll {action[0].lower()}{action[1:]}. "
                "(\"list my automations\" to see them all.)")
    # A standing rule acts later with nobody watching (OWASP ASI08/ASI10): the owner approves the exact trigger and
    # action once, at creation.
    from ishti.skills import ask_first
    return ask_first(session, "automation", f"Create this standing rule? When {describe(cond)}, run: \"{action}\" "
                     f"(at most {MAX_FIRES_PER_HOUR} times an hour). Create it", create)


if __name__ == "__main__":
    import tempfile
    import ishti.facts as F
    F._facts = F.Facts(os.path.join(tempfile.mkdtemp(), "t.db"))
    folder = tempfile.mkdtemp()
    open(os.path.join(folder, "old.pdf"), "w").write("x")
    assert parse_condition(f"a pdf appears in {folder}") == {"kind": "file", "folder": folder, "ext": [".pdf"]}
    assert parse_condition("battery drops below 20%") == {"kind": "battery", "below": 20}
    assert parse_condition("the D drive has less than 50 GB") == {"kind": "disk", "drive": "D:\\", "below": 50}
    assert parse_condition("memory goes above 90%") == {"kind": "memory", "above": 90}
    assert parse_condition("I open spotify") == {"kind": "app_start", "app": "spotify"}
    assert parse_condition("chrome closes") == {"kind": "app_stop", "app": "chrome"}
    logf = os.path.join(folder, "server.log")
    open(logf, "w").write("start ok\n")
    assert parse_condition(f"{logf} contains ERROR") == {"kind": "log", "path": logf, "text": "ERROR"}
    st = {}
    assert check({"kind": "log", "path": logf, "text": "error"}, st) == (False, None)  # first look: remembers the end
    open(logf, "a").write("request ok\nERROR database timeout\n")
    got = check({"kind": "log", "path": logf, "text": "error"}, st)
    assert got[0] and "database timeout" in got[1], got
    assert check({"kind": "log", "path": logf, "text": "error"}, st)[0] is False  # old lines don't fire again
    assert match(f"when a pdf appears in {folder}, summarize it") and match("list my automations")
    assert not match("when is my exam") and not match("if it rains tomorrow")
    s = {}
    q = run(f"whenever a new pdf appears in {folder}, summarize it", s)["reply"]
    assert q.startswith("Create this standing rule?") and not rules(), q  # nothing is stored before the owner's yes
    r = s.pop("pending")["action"]()
    assert r.startswith("Automation #1") and "summarize" in r, r
    run("when memory goes above 1%, remind me to close some tabs", s)  # true right now: fires once
    s.pop("pending")["action"]()
    heard, ran = [], []
    fake = lambda t: ran.append(t) or f"done: {t}"
    assert tick(heard.append, fake) == ["remind me to close some tabs"]  # old.pdf doesn't count
    open(os.path.join(folder, "new.pdf"), "w").write("y")
    open(os.path.join(folder, "note.txt"), "w").write("z")  # not a pdf
    got = tick(heard.append, fake)
    assert got == [f'summarize "{os.path.join(folder, "new.pdf")}"'], got  # memory rule already fired: not again
    time.sleep(0.3)
    assert f'done: summarize "{os.path.join(folder, "new.pdf")}"' in heard and any(h.startswith("Automation #2") for h in heard)
    assert "ran 1x" in run("list my automations", s)["reply"]
    assert "paused" in run("pause automation 2", s)["reply"] and tick(heard.append, fake) == []
    assert "Deleted" in run("delete automation 1", s)["reply"] and len(rules()) == 1
    print("[self-check] automation OK - conditions (file/battery/disk/cpu/memory/app), fire once + re-arm, new files "
          "only, 'it' = the file, list/pause/delete")
