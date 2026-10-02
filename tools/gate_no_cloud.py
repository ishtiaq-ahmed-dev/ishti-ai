"""Safety gate 1 - no cloud calls: python tools/gate_no_cloud.py

Starts the real backend (own process tree, private port), samples every TCP connection of that tree 10x a second from the
moment of launch (so the boot - model loading, router, voice - is covered), and runs local tasks through it: chat, reflex,
system, notes, a code question and a multi-skill request that avoids the web. Passes when
  - no connection ever left the loopback interface, and
  - no request's call ledger (ishti/calltrace.py) recorded an outbound web call.
It also samples the Ollama processes (ours: fail; one that was already running, e.g. the tray app's server: reported, fail only
with --strict-shared) and every msedgewebview2 process that belongs to ISHTI's window, and reads the egress guard's ledger
(data/outbound.log): an allowed external row fails the gate, a blocked row is listed (a library tried to phone home and was
stopped). --window opens a small WebView2 window on the gate's port with the production flags so the browser is sampled too.
Skills whose job IS the web (search, browse, research) are not exercised here: they reach the internet by design and
declare their domains in their manifests (docs/SAFETY.md)."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

import psutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import profile_requests as P  # noqa: E402

STRICT_SHARED = "--strict-shared" in sys.argv
PORT = 8894
P.BASE = f"http://127.0.0.1:{PORT}"
TASKS = ["what is 7 plus 5?", "tell me a short fun fact about Peshawar", "what time is it", "how much RAM is free right now",
         "list my notes", "explain what a mutex is in two sentences"]
LOOPBACK = ("127.", "::1", "::ffff:127.")
LEDGER = os.path.join(ROOT, "data", "outbound.log")


def main():
    proc = subprocess.Popen([os.path.join(ROOT, "venv", "Scripts", "python.exe"), "-m", "ishti", str(PORT)], cwd=ROOT,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
    seen, shared, stop = {}, {}, threading.Event()
    ledger0 = os.path.getsize(LEDGER) if os.path.exists(LEDGER) else 0
    window = None
    if "--window" in sys.argv:  # a WebView2 window with the production flags, pointed at this backend
        code = ("import os,sys;sys.path.insert(0,%r);from ishti import privacy;os.environ.update(privacy.webview2_env());import webview;"
                "w=webview.create_window('gate',%r,width=400,height=300);webview.start(storage_path=%r)"
                % (ROOT, P.BASE, tempfile.mkdtemp(prefix="gate_wv2_")))
        window = subprocess.Popen([os.path.join(ROOT, "venv", "Scripts", "python.exe"), "-c", code], cwd=ROOT)

    def ours(p, tree_pids):
        """Is this process part of ISHTI: in the backend tree, started by ISHTI's Ollama registry entry, or a WebView2 whose
        ancestors include a process from this project (the window host)."""
        if p.pid in tree_pids:
            return "tree"
        try:
            name = (p.name() or "").lower()
            if name.startswith(("ollama", "llama-server")):
                reg = json.load(open(os.path.join(os.environ.get("LOCALAPPDATA", ""), "ISHTI", "run", "ishti.json"), encoding="utf-8"))
                return "ollama-ours" if reg.get("ollama_pid") in [p.pid] + [a.pid for a in p.parents()] else "ollama-shared"
            if name == "msedgewebview2.exe":
                anc = p.parents()
                if any(ROOT.lower() in " ".join(a.cmdline()).lower() or (a.exe() or "").lower().startswith(ROOT.lower()) for a in anc):
                    return "webview"
        except (psutil.Error, OSError, ValueError):
            pass
        return None

    def sample():
        while not stop.is_set():
            try:
                tree = {p.pid for p in [psutil.Process(proc.pid)] + psutil.Process(proc.pid).children(recursive=True)}
            except psutil.NoSuchProcess:
                tree = set()
            for p in psutil.process_iter(["name"]):
                n = (p.info["name"] or "").lower()
                if p.pid not in tree and not n.startswith(("ollama", "llama-server", "msedgewebview2")):
                    continue
                group = ours(p, tree)
                if not group:
                    continue
                try:
                    for c in p.net_connections(kind="inet"):
                        if c.raddr and not c.raddr.ip.startswith(LOOPBACK):
                            into = shared if group == "ollama-shared" else seen
                            key = (c.raddr.ip, c.raddr.port, f"{p.name()} [{group}]")
                            into[key] = into.get(key, 0) + 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            time.sleep(0.1)
    threading.Thread(target=sample, daemon=True).start()
    ledger_net, answers = 0, []
    try:
        t0 = time.time()
        while time.time() - t0 < 240:
            try:
                if json.load(urllib.request.urlopen(P.BASE + "/api/ready", timeout=1)).get("ready"):
                    break
            except Exception:
                time.sleep(0.2)
        for q in TASKS:
            res, wall, _ = P.post_stream(q, "gate-nocloud")
            c = (res or {}).get("calls") or {}
            ledger_net += c.get("net_calls") or 0
            answers.append((q, round(wall, 1), c.get("net_calls"), ((res or {}).get("final_answer") or "")[:50]))
        time.sleep(3)
    finally:
        try:
            urllib.request.urlopen(urllib.request.Request(P.BASE + "/api/shutdown", method="POST", data=b""), timeout=3)
            proc.wait(45)
        except Exception:
            proc.kill()
        stop.set()
        if window:
            window.kill()
    rows = []
    if os.path.exists(LEDGER):
        with open(LEDGER, "rb") as f:
            f.seek(ledger0)
            rows = [json.loads(x) for x in f.read().decode("utf-8", "replace").splitlines() if x.strip().startswith("{")]
    allowed_rows = [r for r in rows if r["allowed"]]
    for r in rows:
        if not r["allowed"]:
            print("  guard BLOCKED:", r["host"], r["port"], r["who"], "-", r["reason"])
    for a in answers:
        print(a)
    print("remote connections seen (backend tree, our Ollama, our WebView2):", {f"{k[0]}:{k[1]} ({k[2]})": v for k, v in seen.items()} or "none")
    print("remote connections of an Ollama ISHTI did not start (informational):", {f"{k[0]}:{k[1]} ({k[2]})": v for k, v in shared.items()} or "none")
    print("egress ledger: allowed external rows", len(allowed_rows), "| blocked rows", len(rows) - len(allowed_rows))
    print("outbound calls in request ledgers:", ledger_net)
    ok = not seen and ledger_net == 0 and not allowed_rows and not (STRICT_SHARED and shared)
    print("GATE no-cloud:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
