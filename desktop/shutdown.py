"""Stop ISHTI. The ONE stop path is the backend's graceful shutdown (POST /api/shutdown: stop intake, finish tasks,
flush SQLite/Chroma, stop voice/mic, unload models, close browser tools, release the port, exit). This module only asks
for it and waits. If the backend cannot answer (crashed/hung) it kills by the pids ISHTI recorded in
%LOCALAPPDATA%\ISHTI\run\ishti.json - never by matching process names or command lines (that used to be able to kill
unrelated Python processes)."""
import json
import os
import sys
import time
import urllib.request

ROOT = r"D:\ISHTI"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

STOP_FLAG = os.path.join(ROOT, "data", "stop.flag")


def _get(url, timeout=1.5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status
    except Exception:
        return None


def _post(url, body=b"{}", timeout=3):
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except Exception:
        return None


def _self_and_ancestors() -> set[int]:
    """PIDs that must never be killed: this process and everything it was launched from (a recorded pid can be reused,
    and the caller of a stop - the tray, the launcher, a test - may sit above the process being stopped)."""
    import psutil
    me = psutil.Process()
    return {me.pid, *(p.pid for p in me.parents())}


def shutdown(port=8888, wait_s=45, quiet=False) -> int:
    """0 when everything ISHTI started is gone and the port is free, 1 otherwise."""
    import psutil
    from ishti import lifecycle
    say = (lambda *_: None) if quiet else print
    base = f"http://127.0.0.1:{port}"
    say("Stopping ISHTI (graceful shutdown)...")
    lifecycle._st["port"] = port  # the registry is per port
    reg = lifecycle.registry()
    if _get(base + "/api/health"):
        say("  asking the backend to shut down" if _post(base + "/api/shutdown") == 202 else "  backend did not accept the request")
    else:
        say("  the backend is not answering - stopping what ISHTI recorded")
        os.makedirs(os.path.dirname(STOP_FLAG), exist_ok=True)
        open(STOP_FLAG, "w").write("stop\n")  # so a watchdog that is still alive does not restart it
    end = time.time() + wait_s
    while time.time() < end:
        alive = [reg[k] for k in ("server_pid", "watchdog_pid") if reg.get(k) and lifecycle._alive(reg[k], "python", reg.get(k.replace("pid", "ct")))]
        if not alive and not _get(base + "/api/health", 0.5):
            break
        time.sleep(0.5)
    # whatever is still there: kill by recorded pid, tree included
    left, keep = [], _self_and_ancestors()
    for key, name in (("server_pid", "python"), ("watchdog_pid", "python"), ("ollama_pid", "ollama")):
        pid = reg.get(key)
        if pid and pid not in keep and lifecycle._alive(pid, name, reg.get(key.replace("pid", "ct"))):  # same creation time: still OUR process
            if key == "ollama_pid" and not lifecycle.ollama_ours():
                continue
            say(f"  force-stopping {key} {pid}")
            lifecycle.kill_tree(pid)
            left.append(key)
    time.sleep(0.5)
    clean = not _get(base + "/api/health", 0.5) and not any(
        c.laddr.port == port and c.status == "LISTEN" for c in psutil.net_connections(kind="inet"))
    try:
        os.remove(STOP_FLAG)
    except OSError:
        pass
    say("ISHTI is stopped." if clean else "ISHTI may still be running - check the log in %LOCALAPPDATA%\ISHTI\logs.")
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(shutdown(int(sys.argv[1]) if len(sys.argv) > 1 else 8888))
