"""
ISHTI Desktop Automation & Computer-Use Engine
Provides real-world PC automation for a JARVIS-like assistant:
- Multi-app launcher (VS Code, Chrome, Explorer, Media Editors, Tools, Settings)
- Deep PC file search across drives and user folders (Desktop, Downloads, Documents, etc.)
- Open files and reveal items in Windows File Explorer
- Mouse, keyboard, and hotkey control via PyAutoGUI
- Window management (focus, minimize, maximize, list)
- Full-screen high-resolution display capture
- System telemetry (CPU, RAM, GPU, Disks, Batteries)
"""

import os
import sys
import time
import glob
import shutil
import subprocess
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


try:
    import pyautogui
    pyautogui.FAILSAFE = True
except ImportError:
    pyautogui = None

try:
    import psutil
except ImportError:
    psutil = None

try:
    import win32gui
    import win32con
    import win32process
except ImportError:
    win32gui = None
    win32con = None
    win32process = None

# ---- Telemetry caches (Cycle 4) ----------------------------------------------------------------
# get_system_telemetry() is on the HUD's 3-second /api/system poll and on every "how much ram am I
# using" request. The old body slept 100 ms (cpu_percent interval) and spawned nvidia-smi per call:
# measured 150 ms. CPU now needs no sleep (psutil returns the delta since the previous call), and the
# slow facts (nvidia-smi subprocess, per-partition disk usage) are cached with a short TTL - fresh
# enough for a status read, cheap enough to poll.
_GPU_CACHE: Dict[str, Any] = {"ts": 0.0, "value": "Unknown"}
_GPU_TTL_S = 2.0
_DISKS_CACHE: Dict[str, Any] = {"ts": 0.0, "value": {}}
_DISKS_TTL_S = 5.0
_CPU_PRIMED = False


def installed_apps() -> Dict[str, str]:
    """{lowercase app name: Start-menu shortcut} for every app installed for this user or all users - how Windows itself lists them."""
    roots = [os.path.join(os.environ.get(v, ""), r"Microsoft\Windows\Start Menu\Programs") for v in ("PROGRAMDATA", "APPDATA")]
    out = {}
    for root in roots:
        for lnk in glob.glob(os.path.join(root, "**", "*.lnk"), recursive=True):
            name = os.path.basename(lnk)[:-4].lower()
            if not any(w in name for w in ("uninstall", "readme", "help", "documentation", "website", "release notes")):
                out.setdefault(name, lnk)
    return out


def find_installed(name: str) -> Optional[str]:
    """The Start-menu shortcut for an app the owner names loosely ("brave", "brawe browser", "spotify"), or None."""
    import difflib
    import re as _re
    key = _re.sub(r"\s+(?:app|application|browser)$", "", name.lower().strip())
    apps = installed_apps()
    if not key or not apps:
        return None
    for n, lnk in apps.items():  # exact first, then word-separated containment: "brave" -> "Brave"
        if key == n or _re.search(rf"(?:^|\s){_re.escape(key)}(?:\s|$)", n):
            return lnk
    close = difflib.get_close_matches(key, list(apps), n=1, cutoff=0.75)  # a typo: "brawe" -> "brave"
    if not close:
        close = [n for n in apps if difflib.SequenceMatcher(None, key, n.split()[0]).ratio() >= 0.8][:1]
    if close:
        cand = close[0]
        # "notepad" is not "notepad++": a candidate that is only this key plus a product suffix is
        # a different program, and returning it would launch the wrong app.
        suffix = cand[len(key):]
        if cand.startswith(key) and suffix and not suffix[0].isspace():
            return None
        return apps[cand]
    return None


def _cpu_percent_fast() -> float:
    """Non-blocking CPU% (no 0.1 s sleep). The first call primes psutil's baseline and waits 50 ms
    once, so even the first answer is real instead of 0.0."""
    global _CPU_PRIMED
    if psutil is None:
        return 0.0
    if not _CPU_PRIMED:
        psutil.cpu_percent(None)  # prime the baseline
        _CPU_PRIMED = True
        time.sleep(0.05)
    return psutil.cpu_percent(None)


def _disks_cached() -> Dict[str, Any]:
    """Per-partition usage, cached ~5 s (disk_usage stats each mountpoint; unchanged second to second)."""
    if psutil is None:
        return {}
    now = time.time()
    if now - _DISKS_CACHE["ts"] < _DISKS_TTL_S:
        return _DISKS_CACHE["value"]
    disks = {}
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            disks[part.device] = {"mount": part.mountpoint, "total_gb": round(usage.total / (1024**3), 1),
                                  "free_gb": round(usage.free / (1024**3), 1), "percent_used": usage.percent}
        except Exception:
            pass
    _DISKS_CACHE.update(ts=now, value=disks)
    return disks


def _gpu_cached():
    """nvidia-smi facts, cached ~2 s. A subprocess spawn per status read cost ~40-80 ms; two seconds of
    staleness is invisible in a HUD gauge and keeps the poll path near-free."""
    now = time.time()
    if now - _GPU_CACHE["ts"] < _GPU_TTL_S:
        return _GPU_CACHE["value"]
    value = _GPU_CACHE["value"]
    try:
        smi = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,temperature.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2)
        if smi.returncode == 0 and smi.stdout.strip():
            parts = [p.strip() for p in smi.stdout.strip().split(",")]
            if len(parts) >= 4:
                value = {"name": parts[0], "total_vram_mb": int(parts[1]), "used_vram_mb": int(parts[2]),
                         "temp_c": int(parts[3])}
    except Exception:
        pass  # keep the last good value (or "Unknown") - never fail a status read on nvidia-smi
    _GPU_CACHE.update(ts=now, value=value)
    return value

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")
USER_PROFILE_DIR = os.environ.get("USERPROFILE") or os.path.expanduser("~")

# Common search directories on Windows
DEFAULT_SEARCH_PATHS = [
    os.path.join(USER_PROFILE_DIR, "Desktop"),
    os.path.join(USER_PROFILE_DIR, "Downloads"),
    os.path.join(USER_PROFILE_DIR, "Documents"),
    os.path.join(USER_PROFILE_DIR, "Videos"),
    os.path.join(USER_PROFILE_DIR, "Pictures"),
    os.path.join(USER_PROFILE_DIR, "Music"),
    ROOT_DIR,
]


class ComputerUseAgent:
    """Autonomous PC controller and desktop assistant engine."""

    def __init__(self, data_dir: str = DATA_DIR):
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)

    # -------------------------------------------------------------

    def get_screen_size(self) -> Tuple[int, int]:
        """Returns screen width and height."""
        if pyautogui is not None:
            sz = pyautogui.size()
            return sz.width, sz.height
        return (1920, 1080)

    # -------------------------------------------------------------
    # 2. APPLICATION LAUNCHING & CONTROL
    # -------------------------------------------------------------
    def launch_app(self, app_name: str, target_file: Optional[str] = None) -> Dict[str, Any]:
        """
        Launches desktop applications or opens files in default viewers.
        Supports common named aliases, executables in PATH, and shell links.
        """
        from ishti import test_mode
        if test_mode.enabled():  # NEEDS N21: under ISHTI_TEST_MODE=1 a direct call must never open a real app either
            from ishti import audit
            audit.record(f"TEST-MODE BLOCK launch_app: {app_name}", str(target_file or "")[:120], skill="desktop_control",
                         initiator="test-harness", tier="T2", confirmed=None, request="[test-mode]")
            return {"status": "blocked", "app": app_name, "details": "ISHTI_TEST_MODE=1: the app was not launched, only recorded"}
        app_aliases = {
            "notepad": "notepad.exe",
            "calculator": "calc.exe",
            "calc": "calc.exe",
            "explorer": "explorer.exe",
            "file explorer": "explorer.exe",
            "files": "explorer.exe",
            "chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            "google chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            "edge": "msedge.exe",
            "microsoft edge": "msedge.exe",
            "code": "code.cmd",
            "vscode": "code.cmd",
            "vs code": "code.cmd",
            "visual studio code": "code.cmd",
            "terminal": "wt.exe",
            "cmd": "cmd.exe",
            "powershell": "powershell.exe",
            "task manager": "taskmgr.exe",
            "taskmgr": "taskmgr.exe",
            "settings": "ms-settings:",
            "vlc": "vlc.exe",
            "paint": "mspaint.exe",
            "mspaint": "mspaint.exe",
            "snippingtool": "snippingtool.exe",
            "word": "winword.exe",
            "excel": "excel.exe",
            "premiere": "Adobe Premiere Pro.exe",
            "capcut": "CapCut.exe",
            "blender": "blender.exe",
            "obs": "obs64.exe",
            "whatsapp": "whatsapp:",
            "whatsapp desktop": "whatsapp:",
            "whatsap": "whatsapp:",
            "watsapp": "whatsapp:",
        }

        key = app_name.lower().strip()
        exe = app_aliases.get(key)

        # Direct executable search if not in aliases
        if not exe:
            found = shutil.which(key) or shutil.which(f"{key}.exe") or find_installed(key)
            if found:
                exe = found
            else:
                # Never hand request text to a shell (AUDIT.md B4).
                return {"status": "error", "app": app_name, "error": f"Unknown app '{app_name}'"}

        try:
            # Check for Windows URI scheme (e.g. ms-settings:)
            if exe.endswith(":"):
                os.startfile(exe)
                return {"status": "success", "app": app_name, "details": f"Launched URI scheme: {exe}"}

            if exe.lower().endswith(".lnk"):  # an installed app found through its Start-menu shortcut (Brave, Spotify, Discord...)
                os.startfile(exe, arguments=f'"{os.path.abspath(target_file)}"' if target_file else "")
                return {"status": "success", "app": app_name, "details": f"Launched {os.path.basename(exe)[:-4]}"}
            resolved = exe if os.path.isabs(exe) else shutil.which(exe)
            if not resolved:
                # Trusted alias not on PATH (e.g. CapCut.exe): ShellExecute resolves it via App Paths,
                # without a shell parsing the string.
                os.startfile(exe, arguments=f'"{os.path.abspath(target_file)}"' if target_file else "")
                return {"status": "success", "app": app_name, "details": f"Launched via App Paths: {exe}"}
            args = [resolved]
            if target_file:
                args.append(os.path.abspath(target_file))

            # Launch process detached
            creation_flags = 0
            if sys.platform == "win32":
                creation_flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                if resolved.lower().endswith((".cmd", ".bat")):
                    args = [os.environ.get("COMSPEC", "cmd.exe"), "/c", resolved] + (args[1:] if len(args) > 1 else [])

            proc = subprocess.Popen(args, creationflags=creation_flags)
            print(f"[ComputerUse] Successfully launched '{app_name}' (PID: {proc.pid})")
            return {
                "status": "success",
                "app": app_name,
                "pid": proc.pid,
                "command": " ".join(args),
            }
        except Exception as e:
            print(f"[ComputerUse] Failed to launch '{app_name}': {e}")
            return {"status": "error", "app": app_name, "error": str(e)}

    def open_file_or_folder(self, target_path: str) -> Dict[str, Any]:
        """Opens any file or directory with its default Windows application."""
        abs_path = os.path.abspath(target_path)
        if not os.path.exists(abs_path):
            return {"status": "error", "error": f"Path does not exist: {abs_path}"}

        try:
            os.startfile(abs_path)
            return {"status": "success", "path": abs_path, "action": "opened"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def reveal_in_explorer(self, target_path: str) -> Dict[str, Any]:
        """Opens Windows Explorer with the specific file or folder highlighted."""
        abs_path = os.path.abspath(target_path)
        if not os.path.exists(abs_path):
            return {"status": "error", "error": f"Path does not exist: {abs_path}"}

        try:
            if os.path.isfile(abs_path):
                subprocess.Popen(["explorer", f"/select,{abs_path}"])
            else:
                subprocess.Popen(["explorer", abs_path])
            return {"status": "success", "revealed": abs_path}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    # -------------------------------------------------------------
    # 3. PC FILE & FOLDER SEARCH
    # -------------------------------------------------------------
    def search_pc(
        self,
        query: str,
        search_root: Optional[str] = None,
        extensions: Optional[List[str]] = None,
        max_results: int = 15,
        search_content: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Deep search across PC files, user folders, or specific directory paths.
        - query: filename pattern or text content
        - search_root: custom root path (defaults to user Desktop, Downloads, Docs, Videos, etc.)
        - extensions: optional list of extensions to filter (e.g. ['.mp4', '.py', '.pdf'])
        - search_content: if True, greps inside readable text files
        """
        results = []
        roots_to_scan = []

        if search_root:
            if os.path.exists(search_root):
                roots_to_scan.append(os.path.abspath(search_root))
        else:
            for p in DEFAULT_SEARCH_PATHS:
                if os.path.exists(p):
                    roots_to_scan.append(p)

        norm_exts = [e.lower() if e.startswith(".") else f".{e.lower()}" for e in (extensions or [])]
        query_lower = query.lower().strip()

        # Folders to skip for speed and security
        skip_dirs = {
            "node_modules", ".git", ".venv", "venv", "__pycache__", "appdata",
            "$recycle.bin", "system volume information", "windows"
        }

        for root in roots_to_scan:
            if len(results) >= max_results:
                break

            for dirpath, dirnames, filenames in os.walk(root):
                # Prune skip dirs in-place
                dirnames[:] = [d for d in dirnames if d.lower() not in skip_dirs]

                for fname in filenames:
                    f_lower = fname.lower()
                    ext = os.path.splitext(f_lower)[1]

                    if norm_exts and ext not in norm_exts:
                        continue

                    full_path = os.path.join(dirpath, fname)
                    matched = False
                    match_snippet = ""

                    # 1. Match by filename
                    if query_lower in f_lower or glob.fnmatch.fnmatch(f_lower, f"*{query_lower}*"):
                        matched = True

                    # 2. Match by content if requested and not matched by name
                    elif search_content and ext in [".txt", ".py", ".md", ".json", ".csv", ".log", ".html", ".js", ".ts"]:
                        try:
                            # Read first 100KB only
                            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                                content = f.read(102400)
                                if query_lower in content.lower():
                                    matched = True
                                    idx = content.lower().find(query_lower)
                                    start = max(0, idx - 40)
                                    end = min(len(content), idx + 60)
                                    match_snippet = f"...{content[start:end].replace(chr(10), ' ')}..."
                        except Exception as _exc:
                            swallowed('desktop/computer_use.py:ComputerUseAgent.search_pc', _exc)

                    if matched:
                        try:
                            stat = os.stat(full_path)
                            size_kb = round(stat.st_size / 1024, 1)
                            size_str = f"{size_kb} KB" if size_kb < 1024 else f"{round(size_kb / 1024, 2)} MB"
                            mod_time = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
                        except Exception:
                            size_str = "Unknown"
                            mod_time = "Unknown"

                        results.append({
                            "name": fname,
                            "path": full_path,
                            "directory": dirpath,
                            "size": size_str,
                            "modified": mod_time,
                            "snippet": match_snippet,
                        })

                        if len(results) >= max_results:
                            break

                if len(results) >= max_results:
                    break

        return results

    # -------------------------------------------------------------
    # 4. MOUSE, KEYBOARD & HOTKEY AUTOMATION
    # -------------------------------------------------------------
    # -------------------------------------------------------------
    # 5. WINDOW MANAGEMENT
    # -------------------------------------------------------------
    def list_open_windows(self) -> List[Dict[str, Any]]:
        """Lists active visible application windows."""
        windows = []
        if win32gui is not None:
            def enum_cb(hwnd, _):
                if win32gui.IsWindowVisible(hwnd):
                    title = win32gui.GetWindowText(hwnd).strip()
                    if title:
                        _, pid = win32process.GetWindowThreadProcessId(hwnd)
                        rect = win32gui.GetWindowRect(hwnd)
                        windows.append({
                            "hwnd": hwnd,
                            "title": title,
                            "pid": pid,
                            "bounds": {"left": rect[0], "top": rect[1], "right": rect[2], "bottom": rect[3]},
                        })
                return True

            try:
                win32gui.EnumWindows(enum_cb, None)
            except Exception as e:
                print(f"[ComputerUse] EnumWindows error: {e}")

        return windows

    def focus_window(self, title_keyword: str) -> bool:
        """Finds and brings a window matching title_keyword to the foreground."""
        from ishti import test_mode
        if win32gui is None or test_mode.enabled():  # NEEDS N21: tests never move the owner's windows
            return False

        keyword_lower = title_keyword.lower()
        target_hwnd = None

        def enum_cb(hwnd, _):
            nonlocal target_hwnd
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if keyword_lower in title.lower():
                    target_hwnd = hwnd
            return True

        try:
            win32gui.EnumWindows(enum_cb, None)
            if target_hwnd:
                win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(target_hwnd)
                return True
        except Exception as e:
            print(f"[ComputerUse] Focus window error: {e}")

        return False

    # -------------------------------------------------------------
    # 6. SYSTEM TELEMETRY & HARDWARE AWARENESS
    # -------------------------------------------------------------
    def get_system_telemetry(self) -> Dict[str, Any]:
        """Gathers real-time host hardware metrics (CPU, RAM, Disks, GPU).

        Speed (Cycle 4): this runs on the /api/system poll (every 3 s in the HUD) and on every
        "how much ram am I using" request. Two costs dominated it: psutil.cpu_percent(interval=0.1)
        sleeps 100 ms by design, and nvidia-smi is a process spawn (~40-80 ms) - measured 150 ms per
        call. CPU is now non-blocking (primed once), and the GPU/disk facts are cached for a couple of
        seconds: a status read is allowed to be ~2 s old, a frozen UI is not.
        """
        telemetry = {
            "timestamp": datetime.now().isoformat(),
            "cpu_percent": 0.0,
            "cpu_count": os.cpu_count() or 0,
            "ram_total_gb": 0.0,
            "ram_used_gb": 0.0,
            "ram_percent": 0.0,
            "disks": {},
            "gpu": "Unknown",
        }

        if psutil is not None:
            try:
                telemetry["cpu_percent"] = _cpu_percent_fast()
                vm = psutil.virtual_memory()
                telemetry["ram_total_gb"] = round(vm.total / (1024**3), 1)
                telemetry["ram_used_gb"] = round(vm.used / (1024**3), 1)
                telemetry["ram_percent"] = vm.percent
                telemetry["disks"] = _disks_cached()
            except Exception as e:
                print(f"[ComputerUse] psutil metrics error: {e}")

        # GPU detection via nvidia-smi (cached ~2 s: it is a subprocess, not a syscall)
        telemetry["gpu"] = _gpu_cached()
        return telemetry


_computer_use_agent = None


def get_computer_use_agent() -> ComputerUseAgent:
    global _computer_use_agent
    if _computer_use_agent is None:
        _computer_use_agent = ComputerUseAgent()
    return _computer_use_agent


if __name__ == "__main__":
    agent = ComputerUseAgent()
    print("Screen size:", agent.get_screen_size())
    print("Telemetry:", agent.get_system_telemetry())
    search = agent.search_pc("python", max_results=3)
    print(f"Found {len(search)} files matching 'python'")
