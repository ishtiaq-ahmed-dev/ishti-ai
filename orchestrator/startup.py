"""
ISHTI Startup Sequence & Health-Check Orchestrator
Executes the startup sequence:
1. Environment & GPU verification
2. Ollama health-check & auto-start
3. Model responsiveness test (auto re-benchmark if unresponsive)
4. Animated terminal HUD display
5. Launch Web UI server & Voice Listener
6. TTS Audio Announcement
7. Open dedicated Chrome browser window to Web UI
"""

import os
import sys
import time
import subprocess
import requests
import webbrowser

ROOT_DIR = r"D:\ISHTI"
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from orchestrator.model_router import ModelRouter
from voice.tts import speak, speakable
from orchestrator.memory.user_profile import get_user_memory

CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE_DIR = r"D:\ISHTI\data\chrome_profile"


def print_banner():
    banner = r"""
  ========================================================================
    ___ ____  _   _ _____ ___ 
   |_ _/ ___|| | | |_   _|_ _|    ISHTI - INTELLIGENT PERSONAL ASSISTANT
    | |\___ \| |_| | | |  | |     100% Local Inference • Zero Ongoing Cost
    | | ___) |  _  | | |  | |     RTX 5070 Ti (16GB GDDR7) • Intel Ultra 9
   |___|____/|_| |_| |_| |___|    Filesystem & Profile Isolation Enforced
  ========================================================================
    """
    print(banner)


def check_ollama(router: ModelRouter) -> bool:
    print("  [1/6] Checking Ollama Service...", end="", flush=True)
    try:
        r = requests.get(f"{router.host}/api/tags", timeout=3)
        if r.status_code == 200:
            print(" [ONLINE]")
            return True
    except Exception:
        pass

    print(" [OFFLINE] -> Launching Ollama daemon...", end="", flush=True)
    flags = (
        subprocess.CREATE_NO_WINDOW
        | subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NEW_PROCESS_GROUP
        if os.name == "nt"
        else 0
    )
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        for _ in range(8):
            time.sleep(1.0)
            try:
                r = requests.get(f"{router.host}/api/tags", timeout=2)
                if r.status_code == 200:
                    print(" [READY]")
                    return True
            except Exception:
                continue
    except Exception as e:
        print(f" [FAILED: {e}]")
        return False
    return False


def check_model_responsiveness(router: ModelRouter) -> str:
    print("  [2/6] Checking Active Model Responsiveness...", end="", flush=True)
    model = router.get_model("primary")
    try:
        t0 = time.time()
        res = requests.post(
            f"{router.host}/api/generate",
            json={"model": model, "prompt": "ping", "stream": False},
            timeout=10,
        )
        if res.status_code == 200:
            elapsed = round(time.time() - t0, 2)
            print(f" [{model} OK ({elapsed}s)]")
            return model
    except Exception as e:
        print(f" [WARN: {model} failed ({e})]")

    print("        Triggering automatic model re-benchmark...", flush=True)
    router.run_full_benchmark(reason="startup_health_check_failed")
    return router.get_model("primary")


def check_web_ui() -> bool:
    print("  [3/6] Checking Web UI Server...", end="", flush=True)
    try:
        r = requests.get("http://127.0.0.1:8888/api/status", timeout=2)
        if r.status_code == 200:
            print(" [ACTIVE (http://127.0.0.1:8888)]")
            return True
    except Exception:
        pass

    print(" [STARTING]...", end="", flush=True)
    pythonw_exe = os.path.join(ROOT_DIR, "venv", "Scripts", "pythonw.exe")
    python_exe = pythonw_exe if os.path.exists(pythonw_exe) else os.path.join(ROOT_DIR, "venv", "Scripts", "python.exe")
    flags = (
        subprocess.CREATE_NO_WINDOW
        | subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NEW_PROCESS_GROUP
        if os.name == "nt"
        else 0
    )
    # the watchdog runs the server: restarts it after a crash or hang, and owns the kill-switch hotkey
    subprocess.Popen(
        [python_exe, "-m", "ishti.watchdog", "8888"],
        cwd=ROOT_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    for _ in range(60):  # models load first: allow up to a minute
        time.sleep(1.0)
        try:
            r = requests.get("http://127.0.0.1:8888/api/status", timeout=2)
            if r.status_code == 200:
                print(" [ONLINE]")
                return True
        except Exception:
            continue
    print(" [FAILED]")
    return False


def launch_browser():
    print("  [4/6] Launching ISHTI Application Window...", end="", flush=True)
    flags = (
        subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NEW_PROCESS_GROUP
        if os.name == "nt"
        else 0
    )
    pythonw_exe = os.path.join(ROOT_DIR, "venv", "Scripts", "pythonw.exe")
    python_exe = pythonw_exe if os.path.exists(pythonw_exe) else os.path.join(ROOT_DIR, "venv", "Scripts", "python.exe")
    app_window_py = os.path.join(ROOT_DIR, "desktop", "app_window.py")

    if os.path.exists(app_window_py):
        subprocess.Popen(
            [python_exe, app_window_py],
            cwd=ROOT_DIR,
            creationflags=flags,
        )
        print(" [DESKTOP WINDOW]")
        return

    if os.path.exists(CHROME_EXE):
        subprocess.Popen(
            [
                CHROME_EXE,
                f"--user-data-dir={PROFILE_DIR}",
                "--no-first-run",
                "--no-default-browser-check",
                "--app=http://127.0.0.1:8888",
            ],
            creationflags=flags,
        )
        print(" [LAUNCHED]")
    else:
        edge_exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
        if os.name == "nt" and os.path.exists(edge_exe):
            subprocess.Popen(
                [
                    edge_exe,
                    f"--user-data-dir={PROFILE_DIR}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--app=http://127.0.0.1:8888",
                ],
                creationflags=flags,
            )
            print(" [LAUNCHED (EDGE)]")
        else:
            webbrowser.open("http://127.0.0.1:8888")
            print(" [DEFAULT BROWSER]")


def announce_readiness(model: str):
    print("  [5/6] Voice Synthesis Announcement...", end="", flush=True)
    greeting = f"All systems online. I am ISHTI. Running locally on your RTX 5070 Ti with {model.split(':')[0]}. Standing by."
    print(" [SPOKEN]")
    speak(greeting, wait=False)


def main():
    print_banner()

    cli_mode = "--cli" in sys.argv or "-c" in sys.argv or "cli" in sys.argv
    if not cli_mode:
        try:
            from desktop.focus_ishti import is_running, focus_window
            if is_running():
                print("  [INFO] ISHTI is already active on port 8888.")
                if focus_window():
                    print("  [INFO] Brought existing window to focus. Exiting duplicate launcher.")
                    sys.exit(0)
                else:
                    print("  [INFO] Reopening app window...")
                    launch_browser()
                    sys.exit(0)
        except Exception:
            pass

    router = ModelRouter()

    # Step 1: Ollama
    if not check_ollama(router):
        print("\n[CRITICAL ERROR] Could not start Ollama. Please check installation.")
        sys.exit(1)

    # Step 1b: local web search (SearXNG in Docker) - without it search falls back to poor providers
    print("  [1b]  Checking local search (SearXNG)...", end="", flush=True)
    from tools.web.search import ensure_searxng
    print(" [ONLINE]" if ensure_searxng() else " [OFFLINE] -> web search will use fallback providers")

    # Step 2: Model
    active_model = check_model_responsiveness(router)

    # Step 3: Web UI
    if not check_web_ui():
        print("\n[WARNING] Web UI could not start. Falling back to terminal REPL.")

    # Step 4: Chrome
    launch_browser()

    # Step 5: Voice Announcement
    announce_readiness(active_model)

    # Step 6: Load the skills; any with a missing dependency is reported and skipped.
    print("  [6/6] Loading skills...", end="", flush=True)
    from ishti.dispatcher import SKILLS, handle
    broken = {n: s.problem for n, s in SKILLS.items() if s.status == "broken"}
    print(f" [{len(SKILLS) - len(broken)}/{len(SKILLS)} ready]")
    for name, problem in broken.items():
        print(f"        - {name}: {problem[:70]}")

    cli_mode = "--cli" in sys.argv or "-c" in sys.argv or "cli" in sys.argv

    if not cli_mode:
        print("\n" + "=" * 72)
        print("  ISHTI DESKTOP APPLICATION IS ACTIVE & RUNNING")
        print("  Web HUD: http://127.0.0.1:8888")
        print("  Desktop application opened successfully.")
        print("  All services running in background. Stop anytime with stop.bat.")
        print("  Closing console in 2 seconds...")
        print("=" * 72 + "\n")
        time.sleep(2.0)
        return

    print("\n" + "=" * 72)
    print("  ISHTI IS READY FOR INSTRUCTIONS (CLI MODE)")
    print("  Web HUD: http://127.0.0.1:8888")
    print("  Type commands below, or speak into your microphone.")
    print("  Press Ctrl+C to stop.")
    print("=" * 72 + "\n")

    # Interactive CLI REPL

    while True:
        try:
            prompt = input("ISHTI> ").strip()
            if not prompt:
                continue
            if prompt.lower() in ["exit", "quit", "shutdown"]:
                print("Shutting down ISHTI. Goodbye!")
                speak("Shutting down. Goodbye.", wait=True)
                break
            if prompt.lower() in ["rebenchmark", "benchmark"]:
                print("Running full model benchmark...")
                router.run_full_benchmark(reason="cli_user_request")
                continue

            print(f"\n[Processing]: {prompt}")
            # Same entry point as the Web UI. Calling execute_plan directly here used to
            # skip intent routing, memory injection and the response guard, so the terminal
            # and the HUD behaved differently for the same question.
            res = handle(prompt, channel="text")
            answer = res.get("final_answer", "")
            print("\n[ISHTI Response]:")
            print(answer)
            if answer and get_user_memory().get_preferences().audio_output:
                speak(speakable(answer), wait=False)
            print("-" * 50 + "\n")
        except KeyboardInterrupt:
            print("\nSession paused. Goodbye!")
            break
        except Exception as e:
            print(f"\n[Error]: {e}\n")


if __name__ == "__main__":
    main()
