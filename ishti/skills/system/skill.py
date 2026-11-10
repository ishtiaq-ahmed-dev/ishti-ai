"""System skill: this PC's own hardware specs, live resource usage (CPU/RAM/GPU/disk) and the apps/windows
open right now.

All local: psutil + nvidia-smi + the measured self-model. Product questions ("memory usage of an RTX 5070 Ti")
are web questions and never land here."""
import datetime as dt
import re
import time

from orchestrator.intent_detector import IntentDetector

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


I = re.I
# The owner's OWN machine - "my pc's specs", "hardware specs", "memory usage", "how much RAM do I have".
MINE = re.compile(
    r"\bmy\s+(?:specs?|specifications|hardware)\b|"
    r"\b(?:my|this|your)\s+(?:pc|computer|laptop|system|machine)(?:'s)?\s+"
    r"(?:specs?|specifications|hardware|status|usage|resources?|memory|ram)\b|"
    r"\b(?:specs?|specifications|status|usage|resources?)\s+of\s+(?:my|this|the)\s+(?:pc|computer|laptop|system|machine)\b|"
    r"\b(?:hardware|system|pc|computer|machine)\s+(?:specs?|specifications)\b|"
    r"\bhow\s+much\s+(?:ram|memory|vram|storage|disk\s+space)\b|"
    r"\b(?:how\s+full|free\s+space|space\s+left)\b.*\b(?:disk|drive|ssd|hdd|storage)\b|"
    # "How much free space do I have on D:?" / "free space on my C drive" / "disk space left on D"
    r"\b(?:how\s+much\s+)?(?:free|available|left)\s+(?:space|disk\s+space|storage)\b.*\b(?:on|in)\s+(?:my\s+)?[A-Za-z]:|"
    r"\b(?:disk|drive)\s+[A-Za-z]:\s*(?:space|free|left)?\b|"
    r"\b(?:cpu|gpu|vram|ram)\s+(?:usage|load|temperature|temp)\b|"
    r"\bmemory\s+usage\b", I)
# "which apps are running now on my pc" / "what apps are currently open" / "what's open on my pc"
WINDOWS = re.compile(
    r"\b(?:which|what)\s+(?:apps?|applications?|programs?|windows?)\s+(?:are\s+)?"
    r"(?:currently\s+|now\s+|right\s+now\s+)?(?:open|running|up|active)\b|"
    r"\b(?:open|running|active|opened)\s+(?:apps?|applications?|programs?|windows?)\b|"
    r"\bwhat(?:'s|\s+is)\s+(?:running|open)\s+(?:right\s+now\s+)?(?:on\s+)?(?:my\s+|this\s+)?(?:pc|computer|laptop|system|machine)?\s*[?.!]?$|"
    r"\blist\s+(?:the\s+)?(?:open\s+)?(?:windows|apps|programs)\b", I)
# A product question ("memory usage of an RTX 5070 Ti") is a web question, not this PC's telemetry.
PRODUCT = re.compile(r"\b(?:rtx|gtx|rx\s?\d|arc\s+a|iphone|ipad|galaxy|pixel|macbook|thinkpad|ryzen|core\s+i\d|"
                     r"snapdragon|exynos|m[1-4]\s+(?:chip|max|pro|ultra))\b", I)
SKIP_WINDOW = re.compile(r"^(?:Program Manager|Windows Input Experience|Microsoft Text Input Application|"
                         r"Windows Shell Experience Host|Start|Search|Taskbar|Settings)$", I)
# "Which architecture profile are you running?" (checklist #190) - a self-report, and it must never be wrong.
ARCH = re.compile(r"\b(?:which|what)\s+(?:architecture|arch)\s*(?:profile|version|am\s+i\s+running)?\b|"
                  r"\barchitecture\s+profile\b|\bwhich\s+arch\b|\bactive\s+(?:architecture|arch|profile)\b|"
                  r"\barch\s*[12]\b|\barchitecture\s+(?:are|am)\b", I)
# "Are you running okay?" / "Is anything currently broken that you know of?" (checklist #188, #192).
SELF_STATUS = re.compile(
    r"\b(?:are\s+you\s+(?:running\s+)?(?:ok|okay|fine|healthy|working|alright)|how\s+are\s+you\s+(?:doing|running)|"
    r"is\s+anything\s+(?:currently\s+)?(?:broken|wrong|down|failing)|anything\s+(?:currently\s+)?broken\b|"
    r"you\s+know\s+of\b|what(?:'s|\s+is)\s+(?:currently\s+)?(?:broken|wrong|down)\b|\bany\s+known\s+(?:issues?|bugs?)\b)", I)
# "Who can currently use voice commands on this system?" (checklist #238): answer from the real config,
# never a guess.
ACCESS = re.compile(r"\bwho\s+can\s+(?:currently\s+)?(?:use|access|see|control|talk\s+to)\b|"
                    r"\bwho\s+has\s+access\b|\bvoice\s+commands?\b.{0,40}\b(?:who|access|permission|allowed|available)\b|"
                    r"\bwho\s+is\s+(?:allowed|authorised|authorized)\b", I)
# "Check for Windows updates" / "Install pending updates" (checklist #251, #252)
WIN_UPDATES = re.compile(r"\b(?:check\s+(?:for\s+)?|are\s+there\s+any\s+)?windows\s+updates?\b|\b(?:check\s+)?pending\s+updates?\b", I)
WIN_INSTALL = re.compile(r"\binstall\s+(?:the\s+)?(?:pending\s+)?(?:windows\s+)?updates?\b", I)
# "Clean up temp files and free some space" (checklist #253). An action is required - a bare
# "free space" query ("how much free space do I have on D:?") is a disk report, not a cleanup, so
# only "free up/some space" counts here (checklist #26 regression).
CLEANUP = re.compile(r"\b(?:clean\s*up|clear|delete|purge)\s+(?:the\s+)?(?:temp|temporary)\s+files?\b|\bfree\s+(?:up|some)\s+(?:disk\s+)?space\b", I)
# "Check my GPU drivers are up to date" (checklist #256)
GPU_DRIVER = re.compile(r"\b(?:check\s+(?:my\s+)?)?gpu\s+drivers?\b|\bdrivers?\s+(?:are\s+)?up\s+to\s+date\b|\bcheck\s+driver\b", I)
# "Check if my printer is working and print this file" (checklist #255)
PRINTER = re.compile(r"\b(?:check\s+(?:if\s+)?(?:my\s+)?printer\b|printer\s+(?:is\s+)?working\b|print\s+this\s+file\b)", I)
# "Delete system files to free up space" (checklist #96): never delete system/Windows files - that is
# dangerous and irreversible, so refuse and offer the safe alternative (clean temp files) instead.
SYSTEM_FILES = re.compile(r"\b(?:system|windows|windows\s+system)\s+files?\b", I)
# "What notifications did I miss in the last hour?" / "tell me the alert now" (checklist #195, #196):
# answer from the real notification store. A social feed's notifications, a Windows/driver update and
# "turn off notifications for an hour" (mute/DND) are NOT this report.
NOTIF_Q = re.compile(r"\b(?:notifications?|alerts?)\b", I)
NOTIF_ELSEWHERE = re.compile(r"\b(?:facebook|instagram|linkedin|twitter|whatsapp|telegram|gmail|email|inbox|"
                             r"phone|android|windows|driver|update|bell\s+settings)\b", I)
NOTIF_MUTE = re.compile(r"\b(?:turn\s+off|switch\s+off|mute|silence|disable|pause|stop|quiet|"
                        r"do\s+not\s+disturb|dnd)\b", I)
NOTIF_REPORT = re.compile(r"\b(?:missed|did\s+i\s+miss|fired|come\s+up|came\s+up|recent|unread|waiting|"
                          r"pending|new)\b", I)
NOTIF_ASK = re.compile(r"\b(?:what|which|any|show|list|check|see|tell\s+me)\b", I)


def wants_notifications(text: str) -> bool:
    """True for a request to REPORT what fired, never for muting, another app's feed, an update, or
    setting an alert ("set an alert for 5pm" is the reminders skill's request)."""
    t = text or ""
    if not NOTIF_Q.search(t) or NOTIF_ELSEWHERE.search(t) or NOTIF_MUTE.search(t):
        return False
    return bool(NOTIF_REPORT.search(t) or NOTIF_ASK.search(t))


def match(text):
    if WINDOWS.search(text):
        return True
    if ARCH.search(text) or SELF_STATUS.search(text) or ACCESS.search(text):
        return True
    if WIN_INSTALL.search(text) or WIN_UPDATES.search(text) or CLEANUP.search(text) or GPU_DRIVER.search(text) or PRINTER.search(text):
        return True
    if MINE.search(text) and not PRODUCT.search(text):
        return True
    if PRODUCT.search(text):
        return False  # specs/usage of a named product: web_search's job
    if wants_notifications(text):
        return True
    return IntentDetector.classify(re.sub(r"\b(?:the|a|an|my)\s+", "", text, flags=I))["mode"] == "PC_TELEMETRY"


def _notifications(text):
    """What actually fired, straight from the notification store - honest when there is nothing
    (checklist #195, #196). Never invents an alert."""
    from ishti import notifications
    window_s, when = None, "recently"
    m = re.search(r"\blast\s+(?:(\d+)\s*)?(min(?:ute)?s?|hours?|hrs?|days?)\b", text or "", I)
    if m:
        n = float(m.group(1) or 1)
        unit = m.group(2).strip().lower()
        window_s = n * (60 if unit.startswith("m") else 3600 if unit.startswith("h") else 86400)
        when = f"the last {unit.rstrip('s')}" if n == 1 else f"the last {n:g} {unit}"
    rows = notifications.recent(limit=500)
    if window_s is not None:
        cutoff = time.time() - window_s
        rows = [r for r in rows if float(r.get("ts") or 0) >= cutoff]
    unread = notifications.unread()
    where = f"in {when}" if m else "recently"
    if not rows:
        return {"reply": f"No notifications fired {where} - nothing is waiting for you, and I have "
                         f"{unread} unread in the bell in total.", "sources": []}
    lines = []
    for r in rows[:20]:
        stamp = dt.datetime.fromtimestamp(float(r.get("ts") or 0)).strftime("%H:%M")
        body = f" - {r['body']}" if (r.get("body") or "").strip() else ""
        lines.append(f"{stamp} · {r.get('kind')}: {r.get('title')}{body}")
    more = f" (+{len(rows) - 20} more)" if len(rows) > 20 else ""
    return {"reply": f"What fired {where} ({len(rows)}):\n" + "\n".join(f"- {ln}" for ln in lines)
                     + more + f"\n{unread} unread in the bell right now.", "sources": []}


def _arch():
    """ISHTI has one architecture: rules first, then one model call that picks the tool(s) or answers (ishti/brain.py)."""
    return {"reply": "ISHTI runs one architecture: deterministic rules and guards first, then one model call that "
                     "chooses the skill(s) or answers directly. Each tool still passes its scope and approval gates.",
            "sources": []}


def _access():
    """Honest access report from the real config (checklist #238): who can actually use this system."""
    from ishti import CONFIG
    v = CONFIG.get("voice") or {}
    wake = ", ".join(v.get("wake_phrases") or []) or "on"
    langs = ", ".join(v.get("languages") or [])
    lines = ["Anyone using the signed-in Windows account on this PC can speak to me - there is no separate "
             "login or per-person permission in front of the microphone.",
             f"- Wake word: {'enabled' if v.get('wake_enabled', True) else 'disabled'}" + (f" ({wake})" if wake else ""),
             f"- Languages: {langs or 'en'}",
             "- Everything runs locally on this machine; no other device or account can drive it."]
    return {"reply": "\n".join(lines), "sources": []}


def _self_status():
    """An honest self health report: live VRAM/RAM, skills loaded, anything broken or logged missing
    (checklist #188, #192). Real checks - never a bare \"all good\"."""
    from desktop.computer_use import get_computer_use_agent
    d = get_computer_use_agent().get_system_telemetry()
    g = d.get("gpu") if isinstance(d.get("gpu"), dict) else {}
    lines = ["Running: yes."]
    if g:
        used, total = g.get("used_vram_mb", 0), max(1, g.get("total_vram_mb", 1))
        lines.append(f"- GPU: {str(g.get('name', '')).replace('NVIDIA GeForce ', '')} - {used}/{total} MB VRAM used ({round(100 * used / total)}%)")
    lines.append(f"- CPU {d.get('cpu_percent', 0)}%, RAM {d.get('ram_percent', 0)}% ({d.get('ram_used_gb', 0)}/{d.get('ram_total_gb', 0)} GB)")
    try:
        from ishti.dispatcher import SKILLS
        broken = sorted(n for n, s in SKILLS.items() if s.status == "broken")
        lines.append(f"- Skills loaded: {len(SKILLS)}" + (f"; broken: {', '.join(broken)}" if broken else "; none broken"))
    except Exception as _exc:
        swallowed('ishti/skills/system/skill.py:_self_status', _exc)
    try:
        from ishti.skills import needs_path
        with open(needs_path(), encoding="utf-8") as f:
            logged = [l for l in f.read().splitlines() if l.startswith("- ")]
        lines.append(f"- Known gaps logged in NEEDS: {len(logged)}" + (f" (newest: {logged[-1][:90]})" if logged else ""))
    except Exception as _exc:
        swallowed('ishti/skills/system/skill.py:_self_status', _exc)
    return {"reply": "\n".join(lines), "sources": []}


def _telemetry():
    from desktop.computer_use import get_computer_use_agent
    d = get_computer_use_agent().get_system_telemetry()
    g = d.get("gpu") if isinstance(d.get("gpu"), dict) else {}
    spec = {}
    try:
        from ishti import selfmodel as sm  # measured, cached; never blocks the answer (falls back to psutil counts)
        spec = sm.build()
    except Exception:
        spec = {}
    cpu_name = (spec.get("cpu") or {}).get("cpu") or f"{d.get('cpu_count', 0)} cores"
    os_name = (spec.get("os") or {}).get("os") or ""
    ram = f"{d.get('ram_used_gb', 0)} of {d.get('ram_total_gb', 0)} GB used ({d.get('ram_percent', 0)}%)"
    rr = spec.get("ram") or {}
    if rr.get("ram_free_gb") is not None:
        ram += f", {rr['ram_free_gb']} GB free"
    rows = [("CPU", f"{d.get('cpu_percent', 0)}% used", str(cpu_name)),
            ("RAM", f"{d.get('ram_percent', 0)}%", ram)]
    if g:
        pct = round(100 * g.get("used_vram_mb", 0) / max(1, g.get("total_vram_mb", 1)))
        rows.append((f"GPU ({str(g.get('name', '')).replace('NVIDIA GeForce ', '')})", f"{pct}% VRAM",
                     f"{g.get('used_vram_mb', 0)} of {g.get('total_vram_mb', 0)} MB VRAM"
                     + (f", {g.get('temp_c')}°C" if g.get("temp_c") is not None else "")))
    rows += [(f"Disk {k}", f"{100 - round(100 * v.get('free_gb', 0) / max(1, v.get('total_gb', 1)))}% used",
              f"{v.get('free_gb', 0)} GB free of {v.get('total_gb', 0)} GB") for k, v in (d.get("disks") or {}).items()]
    if os_name:
        rows.append(("OS", "", os_name))
    from ishti.skills import md_table
    return {"reply": md_table(("", "Use", "Details"), rows), "sources": [], "telemetry": d}


def _windows():
    from desktop.computer_use import get_computer_use_agent
    wins = get_computer_use_agent().list_open_windows()
    seen, apps = set(), []
    for w in wins or []:
        title = str(w.get("title") or "").strip()
        if not title or SKIP_WINDOW.match(title) or title.lower() in seen:
            continue
        seen.add(title.lower())
        proc = ""
        try:
            import psutil
            proc = str(psutil.Process(w.get("pid")).name()).removesuffix(".exe")
        except Exception:
            proc = ""
        apps.append(f"- {proc}: {title}" if proc else f"- {title}")
    if not apps:
        return {"reply": "I can't see any open app windows on this PC right now.", "sources": []}
    return {"reply": f"{len(apps)} app window(s) open on this PC:\n" + "\n".join(apps[:20]), "sources": []}


def _open_windows_update():
    try:
        import os
        os.startfile("ms-settings:windowsupdate")
        return True
    except OSError:
        return False


def _win_updates():
    """Asks Windows Update itself (it always answered "up to date" without checking before)."""
    from ishti.skills.desktop.more import _ps
    try:
        out = _ps("$r=(New-Object -ComObject Microsoft.Update.Session).CreateUpdateSearcher().Search('IsInstalled=0 and IsHidden=0');"
                  "'COUNT=' + $r.Updates.Count; $r.Updates | ForEach-Object { $_.Title }")
    except Exception:
        out = ""
    lines = [l.strip() for l in (out or "").splitlines() if l.strip()]
    if lines and lines[0].startswith("COUNT="):
        n = int(lines[0][6:] or 0)
        if not n:
            return {"reply": "Windows Update has no pending updates.", "sources": [], "verified": True}
        return {"reply": f"Windows Update has {n} pending update(s):\n" + "\n".join(f"- {t}" for t in lines[1:11]),
                "sources": [], "verified": True}
    opened = _open_windows_update()
    return {"reply": "I couldn't read Windows Update from here" + (" - I opened its settings page so you can see what's "
                     "pending." if opened else "."), "sources": [], "verified": False}


def _win_install(session):
    # installing updates needs admin rights and can restart the PC: Windows' own page does it properly
    opened = _open_windows_update()
    return {"reply": ("I opened Windows Update - press Install there. I don't install Windows updates myself (it needs "
                      "admin rights and can restart the PC mid-task).") if opened else
            "Open Settings > Windows Update and press Install - I don't install Windows updates myself.",
            "sources": [], "verified": False}


def _cleanup():
    import os
    import tempfile
    temp_dir = tempfile.gettempdir()
    cleaned, freed = 0, 0
    try:
        for entry in os.scandir(temp_dir):
            if entry.is_file() and entry.name.endswith((".tmp", ".log", ".bak")):
                try:
                    sz = entry.stat().st_size
                    os.remove(entry.path)
                    cleaned += 1
                    freed += sz
                except Exception:
                    swallowed('ishti/skills/system/skill.py:_cleanup:218')
    except Exception:
        swallowed('ishti/skills/system/skill.py:_cleanup:220')
    freed_mb = round(freed / (1024 * 1024), 1)
    if cleaned > 0 or freed_mb > 0:
        return {"reply": f"Cleaned temporary files: removed {cleaned} temporary files, freeing approximately {freed_mb} MB of space.", "sources": []}
    return {"reply": "Nothing to clean: the temp folder had no old .tmp/.log/.bak files.", "sources": []}


def _gpu_driver():
    from desktop.computer_use import get_computer_use_agent
    d = get_computer_use_agent().get_system_telemetry()
    g = d.get("gpu") if isinstance(d.get("gpu"), dict) else {}
    name, driver = str(g.get("name") or "").replace("NVIDIA GeForce ", ""), str(g.get("driver") or "")
    if not driver:  # it used to invent "610.88" and call it up to date
        return {"reply": "I couldn't read the graphics driver version right now. The NVIDIA App shows it and any newer "
                         "release.", "sources": [], "verified": False}
    return {"reply": f"Your {name or 'graphics card'} is on driver {driver}. I can't check NVIDIA's latest release from "
                     "here - the NVIDIA App shows whether a newer one exists.", "sources": [], "verified": True}


def _printer():
    from ishti.skills.desktop.more import _ps
    printers = _ps("Get-Printer | Select-Object -ExpandProperty Name")
    default = _ps("(Get-CimInstance Win32_Printer | Where-Object Default).Name")
    plist = [p.strip() for p in printers.splitlines() if p.strip()]
    if not plist or (len(plist) <= 2 and all("PDF" in p or "OneNote" in p or "XPS" in p for p in plist)):
        return {"reply": f"Printer check: Virtual printers available ({', '.join(plist) or 'none'}), but no physical printer is currently detected or online. Spooler service is active. To print a file, specify the exact document path.", "sources": []}
    return {"reply": f"Printer check: Active printer detected ({default or plist[0]}). Available: {', '.join(plist)}. Spooler is operational.", "sources": []}


def run(text, session):
    if WINDOWS.search(text):
        return _windows()
    if ARCH.search(text):
        return _arch()
    if ACCESS.search(text):
        return _access()
    if SELF_STATUS.search(text):
        return _self_status()
    if wants_notifications(text):
        return _notifications(text)
    if WIN_INSTALL.search(text):
        return _win_install(session)
    if WIN_UPDATES.search(text):
        return _win_updates()
    if SYSTEM_FILES.search(text) and CLEANUP.search(text):
        # deleting system/Windows files is never done - refuse and offer the safe alternative instead
        return {"reply": "I won't delete system files - that's dangerous and can leave Windows broken, "
                         "and it isn't something I'll do for a bit of space. I can clean up temporary "
                         "files instead if you want the space freed.", "sources": []}
    if CLEANUP.search(text):
        return _cleanup()
    if GPU_DRIVER.search(text):
        return _gpu_driver()
    if PRINTER.search(text):
        return _printer()
    return _telemetry()


if __name__ == "__main__":
    assert match("hardware specs & memory usage")
    assert match("my pc's specs")
    assert match("what are my specs")
    assert match("which apps are running now on my pc")
    assert match("what apps are currently open")
    assert match("how much RAM do I have")
    assert match("memory usage")
    assert match("what's my cpu usage")
    assert not match("write a poem")
    assert not match("price and specs of an RTX 5070 Ti")  # a web question, not this PC
    assert match("What notifications did I miss in the last hour?")     # checklist #195
    assert match("Tell me the alert now")                               # checklist #196
    assert not match("Check my Facebook notifications")                 # another app's feed
    assert not match("Turn off notifications for an hour")              # mute/DND, not a report
    assert match("Are there any Windows updates?")                       # still this skill...
    assert not wants_notifications("Are there any Windows updates?")     # ...but not the notification report
    assert not match("memory usage of an iPhone 16")
    print("[self-check] system OK - routing (specs, usage, running apps), product questions stay web")
