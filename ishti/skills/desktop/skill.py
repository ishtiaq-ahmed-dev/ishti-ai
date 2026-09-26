"""Desktop & OS control: windows, clipboard, screenshots and screen reading, volume, brightness, processes,
software (winget), power. Thin wrappers over pygetwindow/pywin32, pyperclip, mss, pycaw,
screen-brightness-control, psutil and winget. Reading is free; anything that can lose work or change the system
(close a window, kill a process, install/uninstall, lock/sleep/shut down) goes through the yes/no gate."""
import datetime as dt
import difflib
import os
import re
import subprocess

from ishti import ROOT
from ishti.skills import ask_first, trace
from ishti.skills.desktop import more

I = re.I
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
OPS = [  # (name, pattern) - first match wins
    ("screen_read", re.compile(r"\b(?:read|what(?:'s| is)\s+on|describe|look\s+at)\s+(?:my\s+|the\s+)?screen\b", I)),
    ("screenshot", re.compile(r"\b(?:take|grab|capture)\s+(?:a\s+)?(?:screenshot|screen\s*shot|screen\s+capture)|\bscreenshot\b", I)),
    ("clipboard_write", re.compile(r"\bcopy\s+(?P<text>.+?)\s+to\s+(?:the\s+|my\s+)?clipboard\b", I)),
    ("clipboard_read", re.compile(r"\b(?:what(?:'s| is)\s+(?:in|on)\s+(?:my\s+|the\s+)?clipboard|read\s+(?:my\s+|the\s+)?clipboard|clipboard\s+content)", I)),
    ("volume", re.compile(r"\b(?:un)?mute\b|\bvolume\b|\bsound\s+(?:up|down)\b|\b(?:louder|quieter)\b", I)),
    ("brightness", re.compile(r"\bbrightness\b|\b(?:brighter|dimmer|dim\s+the\s+screen)\b", I)),
    ("window", re.compile(r"\b(?P<act>minimi[sz]e|maximi[sz]e|restore|close)\s+(?:the\s+|my\s+)?(?P<app>.+?)(?:\s+window)?\s*$", I)),
    ("windows_list", re.compile(r"\b(?:which|what|list)\s+(?:windows|apps)\s+(?:are\s+)?open\b|\bopen\s+windows\b", I)),
    ("process_kill", re.compile(r"\b(?:kill|end|force\s+(?:close|quit)|terminate)\s+(?:the\s+)?(?:process\s+|task\s+)?(?P<name>[\w .-]+?)(?:\s+process)?\s*$", I)),
    ("process_top", re.compile(r"\b(?:what(?:'s| is)|which\s+(?:apps?|process(?:es)?))\s+(?:is\s+)?using\s+(?:the\s+)?most|\b(?:list|show)\s+(?:running\s+)?process(?:es)?\b|\brunning\s+(?:apps|programs|processes)\b", I)),
    ("updates", re.compile(r"\b(?:check\s+for|any|list|show)\s+(?:software\s+|app\s+)?updates\b|\bwhat\s+(?:needs|can\s+be)\s+updated\b", I)),
    # "update my semester to 8th" is a memory update, not an app: no "my ..." and no "... to ..."
    ("update_app", re.compile(r"^\s*(?:please\s+)?(?:update|upgrade)\s+(?P<name>(?!all\b|windows\b|my\b|the\s+memory\b)(?:(?!\bto\b)[\w .+-])+?)\s*$", I)),
    ("install", re.compile(r"^\s*(?:please\s+)?(?:install|download\s+and\s+install)\s+(?P<name>[\w .+-]+?)(?:\s+(?:app|software|on\s+my\s+pc))?\s*$", I)),
    ("uninstall", re.compile(r"^\s*(?:please\s+)?(?:uninstall|remove\s+the\s+app)\s+(?P<name>[\w .+-]+?)\s*$", I)),
    ("power", re.compile(r"\b(?P<act>lock\s+(?:the\s+|my\s+)?(?:screen|pc|computer)|sleep\s+(?:the\s+|my\s+)?(?:pc|computer)|put\s+(?:the\s+|my\s+)?(?:pc|computer)\s+to\s+sleep|shut\s*down\s+(?:the\s+|my\s+)?(?:pc|computer)|restart\s+(?:the\s+|my\s+)?(?:pc|computer))\b", I)),
]


def match(text):
    return more.match(text) or any(rx.search(text) for _, rx in OPS)


def run(text, session):
    for name, rx in more.OPS:  # first: "close this desktop" is a virtual desktop, not a window called "this desktop"
        if m := rx.search(text):
            trace(session, "desktop", name.replace("_", " "))
            out = getattr(more, "op_" + name)(m, text, session)
            return out if isinstance(out, dict) or out is None else {"reply": out, "sources": []}
    for name, rx in OPS:
        if m := rx.search(text):
            trace(session, "desktop", name.replace("_", " "))
            out = globals()["op_" + name](m, text, session)
            return out if isinstance(out, dict) or out is None else {"reply": out, "sources": []}
    return None


# ---------------------------------------------------------------- screen
def _shot(session=None):
    import mss
    import mss.tools
    folder = os.path.join(ROOT, "data", "screenshots")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, dt.datetime.now().strftime("screen_%Y-%m-%d_%H-%M-%S.png"))
    with getattr(mss, "MSS", mss.mss)() as s:
        img = s.grab(s.monitors[0])  # all monitors
        mss.tools.to_png(img.rgb, img.size, output=path)
    return path


def op_screenshot(m, text, session):
    return f"Screenshot saved: {_shot()}"


def op_screen_read(m, text, session):
    """Screen reading with the local vision model (Gemma 3): the text on screen and what's going on."""
    from PIL import Image
    path = _shot()
    small = path.replace(".png", "_read.jpg")
    im = Image.open(path).convert("RGB")
    im.thumbnail((1600, 1600))
    im.save(small, quality=88)
    from ishti.skills.video.pro import look
    trace(session, "vision", "reading the screen with the local vision model", status="start")
    seen = look(small, "This is the owner's computer screen. Read the important text on it and say in one or two "
                       "sentences what is open and happening.")
    os.remove(small)
    trace(session, "vision", "done", status="done")
    return f"{seen.get('text') or 'I could not read it.'}\n\n(Screenshot: {path})"


# ---------------------------------------------------------------- clipboard
def op_clipboard_read(m, text, session):
    import pyperclip
    content = pyperclip.paste()
    return f"Clipboard:\n```\n{content[:1500]}\n```" if content.strip() else "The clipboard is empty."


def op_clipboard_write(m, text, session):
    import pyperclip
    value = m.group("text").strip(" '\"")
    pyperclip.copy(value)
    return f"Copied to the clipboard: {value[:200]}"


# ---------------------------------------------------------------- sound & display
def _endpoint():
    from ctypes import POINTER, cast
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    dev = AudioUtilities.GetSpeakers()
    if hasattr(dev, "EndpointVolume"):  # pycaw 2024+: the wrapper exposes the volume interface directly
        return dev.EndpointVolume
    iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(iface, POINTER(IAudioEndpointVolume))


def op_volume(m, text, session):
    vol = _endpoint()
    now = round(vol.GetMasterVolumeLevelScalar() * 100)
    if re.search(r"\bunmute\b", text, I):
        vol.SetMute(0, None)
        return f"Unmuted (volume {now}%)."
    if re.search(r"\bmute\b", text, I):
        vol.SetMute(1, None)
        return "Muted."
    target = re.search(r"(\d{1,3})\s*%?", text)
    if target:
        level = max(0, min(100, int(target.group(1))))
    elif re.search(r"\b(?:up|louder|increase|raise)\b", text, I):
        level = min(100, now + 10)
    elif re.search(r"\b(?:down|quieter|lower|decrease|reduce)\b", text, I):
        level = max(0, now - 10)
    else:
        return f"Volume is {now}%" + (" (muted)." if vol.GetMute() else ".")
    vol.SetMasterVolumeLevelScalar(level / 100, None)
    return f"Volume {now}% -> {level}%."


def op_brightness(m, text, session):
    import screen_brightness_control as sbc
    try:
        now = sbc.get_brightness()
    except Exception as exc:
        return f"This display doesn't let Windows change its brightness ({type(exc).__name__}); use the monitor's buttons."
    cur = now[0] if isinstance(now, list) else now
    target = re.search(r"(\d{1,3})\s*%?", text)
    if target:
        level = max(0, min(100, int(target.group(1))))
    elif re.search(r"\b(?:up|brighter|increase|raise)\b", text, I):
        level = min(100, cur + 15)
    elif re.search(r"\b(?:down|dimmer|dim|lower|decrease|reduce)\b", text, I):
        level = max(0, cur - 15)
    else:
        return f"Brightness is {cur}%."
    sbc.set_brightness(level)
    return f"Brightness {cur}% -> {level}%."


# ---------------------------------------------------------------- windows
def _find_window(name):
    import pygetwindow as gw
    wins = [w for w in gw.getAllWindows() if w.title.strip() and w.visible]
    low = name.lower()
    hits = [w for w in wins if low in w.title.lower()]
    if not hits:
        close = difflib.get_close_matches(low, [w.title.lower() for w in wins], n=1, cutoff=0.5)
        hits = [w for w in wins if close and w.title.lower() == close[0]]
    return hits[0] if hits else None


def op_windows_list(m, text, session):
    import pygetwindow as gw
    titles = sorted({w.title.strip() for w in gw.getAllWindows() if w.title.strip() and w.visible})
    return "Open windows:\n" + "\n".join(f"- {t[:90]}" for t in titles[:40])


def op_window(m, text, session):
    act, app = m.group("act").lower(), m.group("app").strip()
    w = _find_window(app)
    if not w:
        return f"No open window matches '{app}'."
    if act.startswith("close"):
        # closing can lose unsaved work: ask first
        return ask_first(session, "desktop", f"Close the window '{w.title[:60]}' (unsaved work in it would be lost)",
                         lambda: (w.close(), f"Closed '{w.title[:60]}'.")[1])
    {"minimi": w.minimize, "maximi": w.maximize, "restor": w.restore}[act[:6]]()
    return f"{act.capitalize()}d '{w.title[:60]}'." if not act.endswith("e") else f"{act.capitalize()[:-1]}ed '{w.title[:60]}'."


# ---------------------------------------------------------------- processes
def op_process_top(m, text, session):
    import psutil
    procs = []
    for p in psutil.process_iter(["name", "memory_info", "cpu_percent"]):
        try:
            procs.append((p.info["memory_info"].rss / 2**20, p.info["name"], p.pid))
        except (psutil.Error, AttributeError):
            continue
    by_name = {}
    for mb, name, pid in procs:
        by_name.setdefault(name, [0.0, 0])
        by_name[name][0] += mb
        by_name[name][1] += 1
    top = sorted(by_name.items(), key=lambda kv: -kv[1][0])[:10]
    return "Using the most memory:\n" + "\n".join(f"- {n}: {mb / 1024:.1f} GB" + (f" ({c} processes)" if c > 1 else "")
                                                  for n, (mb, c) in top)


def op_process_kill(m, text, session):
    import psutil
    name = m.group("name").strip().lower()
    procs = [p for p in psutil.process_iter(["name"]) if p.info["name"] and name in p.info["name"].lower()]
    if not procs:
        return f"No running process matches '{name}'."
    names = sorted({p.info["name"] for p in procs})

    def kill():
        n = 0
        for p in procs:
            try:
                p.kill()
                n += 1
            except psutil.Error:
                pass
        return f"Ended {n} process(es): {', '.join(names)}."
    return ask_first(session, "desktop", f"End {len(procs)} process(es) named {', '.join(names)} "
                                         "(unsaved work in them is lost)", kill)


# ---------------------------------------------------------------- software (winget)
def _winget(*args, timeout=180):
    p = subprocess.run(["winget", *args, "--disable-interactivity"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout, creationflags=NO_WINDOW)
    return p.returncode, p.stdout


def _table(out):
    """winget's text table -> [(name, id, version, available)]."""
    lines = [l for l in out.splitlines() if l.strip() and not set(l.strip()) <= {"-"}]
    head = next((i for i, l in enumerate(lines) if l.startswith("Name") and " Id " in l), None)
    if head is None:
        return []
    h = lines[head]
    cols = [h.index(c) for c in ("Id", "Version")] + ([h.index("Available")] if "Available" in h else [])
    rows = []
    for l in lines[head + 1:]:
        if len(l) < cols[1]:
            continue
        name, id_ = l[:cols[0]].strip(), l[cols[0]:cols[1]].strip()
        ver = l[cols[1]:cols[2]].strip() if len(cols) > 2 else l[cols[1]:].split()[0] if l[cols[1]:].split() else ""
        avail = l[cols[2]:].split()[0] if len(cols) > 2 and l[cols[2]:].split() else ""
        if id_ and " " not in id_:
            rows.append((name, id_, ver, avail))
    return rows


def op_updates(m, text, session):
    trace(session, "winget", "checking for updates", status="start")
    _, out = _winget("upgrade", "--include-unknown")
    rows = [r for r in _table(out) if r[3]]
    trace(session, "winget", f"{len(rows)} updates", status="done")
    if not rows:
        return "Everything winget knows about is up to date."
    return (f"{len(rows)} updates available:\n" + "\n".join(f"- {n}: {v} -> {a}" for n, _, v, a in rows[:25])
            + "\nSay \"update <name>\" for one.")


def _package(name, installed=False):
    """Best winget package for a name: exact id/name first, else the first search hit."""
    rows = []
    # the winget community catalog first (real ids like GIMP.GIMP, known versions), the Microsoft Store as fallback
    for source in ((None,) if installed else ("winget", "msstore")):
        code, out = _winget("list" if installed else "search", name, "--accept-source-agreements",
                            *(("--source", source) if source else ()), timeout=90)
        if rows := _table(out):
            break
    exact = [r for r in rows if r[0].lower() == name.lower() or r[1].lower().endswith("." + name.lower().replace(" ", ""))]
    return (exact or rows or [None])[0]


def op_install(m, text, session):
    name = m.group("name").strip()
    trace(session, "winget", f"looking up '{name}' in the winget catalog", status="start")
    pkg = _package(name)
    trace(session, "winget", pkg[1] if pkg else "not found", status="done" if pkg else "error")
    if not pkg:
        return f"winget has no package called '{name}'. Tell me the exact app name or its website."
    if _package(pkg[1], installed=True):
        return f"{pkg[0]} is already installed."

    def install():
        code, out = _winget("install", "--id", pkg[1], "-e", "--silent", "--accept-package-agreements",
                            "--accept-source-agreements", timeout=1800)
        return f"Installed {pkg[0]} ({pkg[1]})." if code == 0 else f"winget couldn't install {pkg[0]}: {out.strip()[-300:]}"
    return ask_first(session, "desktop", f"Install {pkg[0]} ({pkg[1]}, version {pkg[2]}) with winget - saying yes also "
                                         "accepts its license terms", install)


def op_uninstall(m, text, session):
    name = m.group("name").strip()
    pkg = _package(name, installed=True)
    if not pkg:
        return f"No installed app matches '{name}'."

    def remove():
        code, out = _winget("uninstall", "--id", pkg[1], "-e", "--silent", timeout=900)
        return f"Uninstalled {pkg[0]}." if code == 0 else f"winget couldn't uninstall {pkg[0]}: {out.strip()[-300:]}"
    return ask_first(session, "desktop", f"Uninstall {pkg[0]} ({pkg[1]})", remove)


def op_update_app(m, text, session):
    name = m.group("name").strip()
    pkg = _package(name, installed=True)
    if not pkg:
        return None  # "update my notes" etc. - not an app: route on

    def upgrade():
        code, out = _winget("upgrade", "--id", pkg[1], "-e", "--silent", "--accept-package-agreements",
                            "--accept-source-agreements", timeout=1800)
        return f"Updated {pkg[0]}." if code == 0 else f"winget couldn't update {pkg[0]}: {out.strip()[-300:]}"
    return ask_first(session, "desktop", f"Update {pkg[0]} ({pkg[1]}, now {pkg[2]})", upgrade)


# ---------------------------------------------------------------- power
def op_power(m, text, session):
    act = m.group("act").lower()
    if act.startswith("lock"):
        cmd, what = ["rundll32.exe", "user32.dll,LockWorkStation"], "Lock the PC"
    elif "sleep" in act:
        cmd, what = ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], "Put the PC to sleep"
    elif act.startswith("restart"):
        cmd, what = ["shutdown", "/r", "/t", "10"], "Restart the PC in 10 seconds (save your work)"
    else:
        cmd, what = ["shutdown", "/s", "/t", "10"], "Shut down the PC in 10 seconds (save your work)"
    return ask_first(session, "desktop", what, lambda: (subprocess.Popen(cmd, creationflags=NO_WINDOW), "Done.")[1])


if __name__ == "__main__":
    cases = {"take a screenshot": "screenshot", "read my screen": "screen_read", "set volume to 30": "volume",
             "mute": "volume", "what's in my clipboard": "clipboard_read", "copy hello world to clipboard": "clipboard_write",
             "set brightness to 60": "brightness", "minimize chrome": "window", "close the notepad window": "window",
             "what is using the most memory": "process_top", "kill notepad": "process_kill", "check for updates": "updates",
             "install vlc": "install", "uninstall vlc": "uninstall", "update vlc": "update_app", "lock my pc": "power",
             "shut down the computer": "power", "which windows are open": "windows_list"}
    for t, want in cases.items():
        got = next((n for n, rx in OPS if rx.search(t)), None)
        assert got == want, (t, got, want)
    for t in ("what is the weather", "open youtube", "remind me to call ali", "update my resume in memory"):
        assert not match(t) or t.startswith("update"), t
    rows = _table("Name   Id               Version    Available  Source\n----------------------------------------------------\n"
                  "CapCut ByteDance.CapCut 8.9.1.3802 9.5.0.4050 msstore\n")
    assert rows == [("CapCut", "ByteDance.CapCut", "8.9.1.3802", "9.5.0.4050")], rows
    more_cases = {"new virtual desktop": "vdesk", "switch to the next desktop": "vdesk", "go to desktop 2": "vdesk",
                  "close this desktop": "vdesk", "clipboard history": "clip_history", "copy item 3": "clip_history",
                  "list my monitors": "displays", "extend my displays": "displays", "second screen only": "displays",
                  "print queue": "print_queue", "cancel all print jobs": "print_queue",
                  "print D:/docs/cv.pdf": "print_file", "print D:/docs/cv.pdf 2 copies": "print_file",
                  "record a macro called login": "macro", "stop recording macro": "macro", "play macro login": "macro",
                  "list my macros": "macro"}
    for t, want in more_cases.items():
        got = next((n for n, rx in more.OPS if rx.search(t)), None)
        assert got == want, (t, got, want)
    assert not more.match("print hello world in python") and not more.match("close the notepad window")
    import sqlite3
    import tempfile
    db = sqlite3.connect(os.path.join(tempfile.mkdtemp(), "c.db"))
    db.execute("CREATE TABLE clips(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, text TEXT)")
    assert more.remember_clip("first note", db) and not more.remember_clip("first note", db)  # no repeats
    assert not more.remember_clip("sk-abc123def456ghi789", db) and not more.remember_clip("Xy7kP2mQ9vL4nR8tW3", db)  # secrets
    for i in range(more.CLIP_KEEP + 5):
        more.remember_clip(f"item {i}", db)
    assert len(more.recent_clips(500, db)) == more.CLIP_KEEP and more.recent_clips(1, db)[0][0] == f"item {more.CLIP_KEEP + 4}"
    assert "display(s)" in more.op_displays(more.OPS[2][1].search("list my monitors"), "", {})
    print("[self-check] desktop OK - op routing, winget table parsing, virtual desktops/clipboard history/displays/"
          "print/macro routing, clipboard ring (no repeats, no secrets, capped)")
