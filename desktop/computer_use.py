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

try:
    import pyautogui
    pyautogui.FAILSAFE = True
except ImportError:
    pyautogui = None

try:
    from PIL import ImageGrab
except ImportError:
    ImageGrab = None

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

ROOT_DIR = r"D:\ISHTI"
DATA_DIR = os.path.join(ROOT_DIR, "data")
USER_PROFILE_DIR = os.environ.get("USERPROFILE", r"C:\Users\PC")

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
    # 1. SCREEN AWARENESS & SCREENSHOTS
    # -------------------------------------------------------------
    def capture_screen(self, filename: str = "desktop_state.png") -> str:
        """Captures primary desktop screenshot and returns file path."""
        out_path = os.path.join(self.data_dir, filename)

        if ImageGrab is not None:
            try:
                screenshot = ImageGrab.grab()
                screenshot.save(out_path)
                print(f"[ComputerUse] Captured desktop screenshot: {out_path}")
                return out_path
            except Exception as e:
                print(f"[ComputerUse] ImageGrab failed ({e}), trying PyAutoGUI...")

        if pyautogui is not None:
            try:
                screenshot = pyautogui.screenshot()
                screenshot.save(out_path)
                return out_path
            except Exception as e:
                print(f"[ComputerUse] PyAutoGUI grab failed ({e}), trying PowerShell...")

        try:
            ps_script = f"""
            Add-Type -AssemblyName System.Windows.Forms
            Add-Type -AssemblyName System.Drawing
            $screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
            $bitmap = New-Object System.Drawing.Bitmap $screen.Width, $screen.Height
            $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
            $graphics.CopyFromScreen($screen.X, $screen.Y, 0, 0, $bitmap.Size)
            $bitmap.Save('{out_path}')
            $graphics.Dispose()
            $bitmap.Dispose()
            """
            subprocess.run(["powershell", "-Command", ps_script], check=True, timeout=5)
            return out_path
        except Exception as e:
            print(f"[ComputerUse] PowerShell screen capture failed: {e}")
            # If all else fails, create a placeholder status image
            try:
                from PIL import Image, ImageDraw
                img = Image.new("RGB", (1280, 720), color=(15, 23, 42))
                d = ImageDraw.Draw(img)
                d.text((50, 50), f"ISHTI Desktop Monitor - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", fill=(56, 189, 248))
                img.save(out_path)
                return out_path
            except Exception:
                return out_path

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
            found = shutil.which(key) or shutil.which(f"{key}.exe")
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
                        except Exception:
                            pass

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
    def mouse_click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> bool:
        """Clicks at coordinates on screen."""
        if pyautogui is None:
            return False
        try:
            pyautogui.click(x=x, y=y, button=button, clicks=clicks)
            return True
        except Exception as e:
            print(f"[ComputerUse] Mouse click failed: {e}")
            return False

    def mouse_move(self, x: int, y: int, duration: float = 0.2) -> bool:
        """Smoothly moves mouse to coordinate."""
        if pyautogui is None:
            return False
        try:
            pyautogui.moveTo(x, y, duration=duration)
            return True
        except Exception as e:
            print(f"[ComputerUse] Mouse move failed: {e}")
            return False

    def keyboard_type(self, text: str, interval: float = 0.01) -> bool:
        """Types string text into current focused application."""
        if pyautogui is None:
            return False
        try:
            pyautogui.write(text, interval=interval)
            return True
        except Exception as e:
            print(f"[ComputerUse] Keyboard typing failed: {e}")
            return False

    def keyboard_hotkey(self, *keys: str) -> bool:
        """
        Executes keyboard shortcut combination.
        e.g. keyboard_hotkey('ctrl', 's') or keyboard_hotkey('alt', 'tab')
        """
        if pyautogui is None:
            return False
        try:
            pyautogui.hotkey(*keys)
            return True
        except Exception as e:
            print(f"[ComputerUse] Keyboard hotkey failed: {e}")
            return False

    def press_key(self, key: str) -> bool:
        """Presses a single key (e.g. 'enter', 'esc', 'tab', 'backspace')."""
        if pyautogui is None:
            return False
        try:
            pyautogui.press(key)
            return True
        except Exception as e:
            print(f"[ComputerUse] Key press failed: {e}")
            return False

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
        if win32gui is None:
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
        """Gathers real-time host hardware metrics (CPU, RAM, Disks, GPU)."""
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
                telemetry["cpu_percent"] = psutil.cpu_percent(interval=0.1)
                vm = psutil.virtual_memory()
                telemetry["ram_total_gb"] = round(vm.total / (1024**3), 1)
                telemetry["ram_used_gb"] = round(vm.used / (1024**3), 1)
                telemetry["ram_percent"] = vm.percent

                for part in psutil.disk_partitions(all=False):
                    try:
                        usage = psutil.disk_usage(part.mountpoint)
                        telemetry["disks"][part.device] = {
                            "mount": part.mountpoint,
                            "total_gb": round(usage.total / (1024**3), 1),
                            "free_gb": round(usage.free / (1024**3), 1),
                            "percent_used": usage.percent,
                        }
                    except Exception:
                        pass
            except Exception as e:
                print(f"[ComputerUse] psutil metrics error: {e}")

        # GPU detection via nvidia-smi
        try:
            smi = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,temperature.gpu", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if smi.returncode == 0 and smi.stdout.strip():
                parts = [p.strip() for p in smi.stdout.strip().split(",")]
                if len(parts) >= 4:
                    telemetry["gpu"] = {
                        "name": parts[0],
                        "total_vram_mb": int(parts[1]),
                        "used_vram_mb": int(parts[2]),
                        "temp_c": int(parts[3]),
                    }
        except Exception:
            pass

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
    shot = agent.capture_screen("test_shot.png")
    print("Screenshot exists:", os.path.exists(shot))
    search = agent.search_pc("python", max_results=3)
    print(f"Found {len(search)} files matching 'python'")
