"""Safety gate 1 - no cloud calls: python tools/gate_no_cloud.py

Starts the real backend (own process tree, private port), samples every TCP connection of that tree 10x a second from the
moment of launch (so the boot - model loading, router, voice - is covered), and runs local tasks through it: chat, reflex,
system, notes, a code question and a multi-skill request that avoids the web. Passes when
  - no connection ever left the loopback interface, and
  - no request's call ledger (ishti/calltrace.py) recorded an outbound web call.
Skills whose job IS the web (search, browse, research) are not exercised here: they reach the internet by design and
declare their domains in their manifests (docs/SAFETY.md)."""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

import psutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import profile_requests as P  # noqa: E402

PORT = 8894
P.BASE = f"http://127.0.0.1:{PORT}"
TASKS = ["what is 7 plus 5?", "tell me a short fun fact about Peshawar", "what time is it", "how much RAM is free right now",
         "list my notes", "explain what a mutex is in two sentences"]
LOOPBACK = ("127.", "::1", "::ffff:127.")


def main():
    proc = subprocess.Popen([os.path.join(ROOT, "venv", "Scripts", "python.exe"), "-m", "ishti", str(PORT)], cwd=ROOT,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
    seen, stop = {}, threading.Event()

    def sample():
        while not stop.is_set():
            try:
                root = psutil.Process(proc.pid)
                for p in [root] + root.children(recursive=True):
                    try:
                        for c in p.net_connections(kind="inet"):
                            if c.raddr and not c.raddr.ip.startswith(LOOPBACK):
                                seen[(c.raddr.ip, c.raddr.port, p.name())] = seen.get((c.raddr.ip, c.raddr.port, p.name()), 0) + 1
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except psutil.NoSuchProcess:
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
    for a in answers:
        print(a)
    print("remote connections seen:", {f"{k[0]}:{k[1]} ({k[2]})": v for k, v in seen.items()} or "none")
    print("outbound calls in request ledgers:", ledger_net)
    ok = not seen and ledger_net == 0
    print("GATE no-cloud:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
