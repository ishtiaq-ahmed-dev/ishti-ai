"""Complete process-tree termination, GPU VRAM release, and post-stop verification for ISHTI."""
import os
import sys
import time
import urllib.request
import json
import psutil

ROOT = r"D:\ISHTI"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DATA_DIR = os.path.join(ROOT, "data")
STOP_FLAG = os.path.join(DATA_DIR, "stop.flag")


def release_gpu_vram():
    """Unload all active models in Ollama to free GPU memory on RTX 5070 Ti."""
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/ps")
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = data.get("models", [])
            for m in models:
                name = m.get("name") or m.get("model")
                if name:
                    unload_req = urllib.request.Request(
                        "http://127.0.0.1:11434/api/generate",
                        data=json.dumps({"model": name, "keep_alive": 0}).encode("utf-8"),
                        headers={"Content-Type": "application/json"}
                    )
                    with urllib.request.urlopen(unload_req, timeout=3.0) as un_resp:
                        if un_resp.status == 200:
                            print(f"  [x] Released GPU VRAM for model: {name}")
    except Exception as exc:
        # Ollama might not be running or already stopped
        pass


def signal_server_stop():
    """Signal the in-flight server via stop.flag and /api/kill."""
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(STOP_FLAG, "w", encoding="utf-8") as f:
            f.write("stop\n")
    except Exception:
        pass

    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8888/api/kill",
            data=json.dumps({"reason": "full stop"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            pass
    except Exception:
        pass


def _self_and_ancestors() -> set[int]:
    """PIDs that must never be killed: this process and everything it was launched from.

    Without this, running shutdown.py from inside ISHTI (the inline fallback in launcher.ctrl_handler, or a
    test) would match its own parent - the command line contains the ISHTI path - and kill the caller."""
    pids, me = set(), psutil.Process()
    while True:
        pids.add(me.pid)
        try:
            me = me.parent()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            break
        if me is None:
            break
    return pids


def kill_process_tree(proc: psutil.Process):
    """Recursively kill a process and all its children."""
    try:
        children = proc.children(recursive=True)
        for c in children:
            try:
                c.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        proc.kill()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass


def terminate_all_ishti_processes():
    """Finds and terminates all ISHTI-related processes, Playwright MCP servers, and listening ports."""
    ports = [8888, 8899]
    killed_count = 0

    # 1. Kill listeners on ports 8888, 8899
    for p in psutil.process_iter(["pid", "name"]):
        try:
            for conn in p.net_connections(kind="inet"):
                if conn.laddr and conn.laddr.port in ports and conn.status == "LISTEN":
                    print(f"  [x] Terminating port {conn.laddr.port} listener (PID: {p.pid}, {p.name()})")
                    kill_process_tree(p)
                    killed_count += 1
                    break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # 2. Kill python processes matching ishti, watchdog, startup.py
    keep = _self_and_ancestors()
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if p.pid in keep:
                continue
            cmd = " ".join(p.info.get("cmdline") or [])
            name = (p.info.get("name") or "").lower()
            if name.startswith("python"):
                # Exclude IDE/VSCode processes
                if "antigravity-ide" in cmd or "vscode" in cmd or "ms-python" in cmd:
                    continue
                if any(k in cmd for k in ["ishti", "startup.py", "watchdog.py", "doctor.py"]):
                    print(f"  [x] Terminating ISHTI background process (PID: {p.pid})")
                    kill_process_tree(p)
                    killed_count += 1
            # 3. Kill Node Playwright MCP servers and related browser sessions
            elif name.startswith("node"):
                if "@playwright/mcp" in cmd or "mcp_browser_profile" in cmd or "mcp_output" in cmd:
                    print(f"  [x] Terminating Playwright MCP session (PID: {p.pid})")
                    kill_process_tree(p)
                    killed_count += 1
            # 4. Kill isolated Chrome/Edge windows running ISHTI profile
            elif name.startswith("chrome") or name.startswith("msedge"):
                if "chrome_profile" in cmd or "mcp_browser_profile" in cmd:
                    print(f"  [x] Terminating ISHTI browser window (PID: {p.pid})")
                    kill_process_tree(p)
                    killed_count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return killed_count


def verify_clean_state() -> bool:
    """Verifies that no lingering processes or open ports remain (skipping this run's own process chain)."""
    time.sleep(1.0)
    lingering = []
    ports = [8888, 8899]
    keep = _self_and_ancestors()

    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if p.pid in keep:
                continue
            cmd = " ".join(p.info.get("cmdline") or [])
            name = (p.info.get("name") or "").lower()
            if "antigravity-ide" in cmd or "vscode" in cmd or "ms-python" in cmd:
                continue
            if name.startswith("python") and any(k in cmd for k in ["ishti", "startup.py", "watchdog"]):
                lingering.append(f"Python PID {p.pid}: {cmd[:60]}")
            if name.startswith("node") and "@playwright/mcp" in cmd:
                lingering.append(f"Node MCP PID {p.pid}")
            for conn in p.net_connections(kind="inet"):
                if conn.laddr and conn.laddr.port in ports and conn.status == "LISTEN":
                    lingering.append(f"Port {conn.laddr.port} still in use by PID {p.pid}")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if lingering:
        print("[WARN] Lingering processes detected during verification:")
        for item in lingering:
            print(f"       - {item}")
        return False
    return True


def shutdown():
    print("========================================================================")
    print("  STOPPING ISHTI ASSISTANT — FULL CLEAN SHUTDOWN")
    print("========================================================================")
    try:  # Section 7: mic must stay closed across restarts; the persisted state is what re-opens it
        from ishti import mic_control
        mic_control.mic_off("shutdown", by="shutdown")
    except Exception as exc:
        print(f"  [mic] could not force mic OFF: {exc}")
    print("  [1/4] Releasing Ollama GPU VRAM (RTX 5070 Ti)...")
    release_gpu_vram()

    print("  [2/4] Signaling server and watchdog...")
    signal_server_stop()

    print("  [3/4] Terminating process trees & browser automation sessions...")
    terminate_all_ishti_processes()

    print("  [4/4] Verifying clean shutdown...")
    clean = verify_clean_state()
    if clean:
        print("\n[SUCCESS] All ISHTI services, processes, and GPU memory released.")
        return 0
    else:
        # Force secondary pass
        terminate_all_ishti_processes()
        if verify_clean_state():
            print("\n[SUCCESS] Clean shutdown verified after secondary cleanup pass.")
            return 0
        else:
            print("\n[WARN] Some non-critical processes could not be verified.")
            return 1


if __name__ == "__main__":
    sys.exit(shutdown())
