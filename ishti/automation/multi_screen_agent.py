"""
ISHTI Advanced Multi-Screen OS Automation Agent
Operates via a continuous "Perceive-Plan-Act" visual loop with full mouse & keyboard
control across all connected displays and global coordinate mapping.
"""

import os
import sys
import time
import json
import re
import base64
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

try:
    import pyautogui
    pyautogui.FAILSAFE = False  # Multi-screen navigation can touch (0,0) without intending an abort
except ImportError:
    pyautogui = None

try:
    import win32api
    import win32con
    import win32gui
except ImportError:
    win32api = None
    win32con = None
    win32gui = None

try:
    import mss
except ImportError:
    mss = None

from PIL import Image, ImageDraw, ImageFont

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
DATA_DIR = os.path.join(ROOT_DIR, "data")
SCREENSHOTS_DIR = os.path.join(DATA_DIR, "screenshots", "multiscreen")
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)


@dataclass
class MonitorInfo:
    id: int
    name: str
    left: int
    top: int
    right: int
    bottom: int
    width: int
    height: int
    is_primary: bool

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom


@dataclass
class ActionCommand:
    thought: str
    action: str  # "move_to" | "click" | "double_click" | "drag_to" | "type" | "press_key" | "wait" | "done"
    x: Optional[int] = None
    y: Optional[int] = None
    button: str = "left"
    text: Optional[str] = None
    key: Optional[str] = None
    seconds: Optional[int] = None
    target_screen: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {"thought": self.thought, "action": self.action}
        if self.x is not None:
            d["x"] = int(self.x)
        if self.y is not None:
            d["y"] = int(self.y)
        if self.action in ("click", "double_click", "drag_to") and self.button:
            d["button"] = self.button
        if self.text is not None:
            d["text"] = str(self.text)
        if self.key is not None:
            d["key"] = str(self.key)
        if self.seconds is not None:
            d["seconds"] = int(self.seconds)
        return d


class DisplayManager:
    """Detects, maps, and calculates coordinates across multi-monitor display layouts."""

    def __init__(self):
        self.monitors: List[MonitorInfo] = []
        self.virtual_left = 0
        self.virtual_top = 0
        self.virtual_width = 1920
        self.virtual_height = 1080
        self.refresh()

    def refresh(self) -> None:
        """Discovers all active display monitors and computes combined virtual bounding box."""
        self.monitors = []
        # Try Win32 API for exact hardware display coordinates
        if win32api is not None:
            try:
                mon_handles = win32api.EnumDisplayMonitors()
                idx = 1
                for h_mon in mon_handles:
                    info = win32api.GetMonitorInfo(h_mon[0])
                    rect = info["Monitor"]  # (left, top, right, bottom)
                    is_prim = bool(info.get("Flags", 0) & 1)
                    dev_name = info.get("Device", f"Display{idx}")
                    w = rect[2] - rect[0]
                    h = rect[3] - rect[1]
                    m = MonitorInfo(
                        id=idx,
                        name=dev_name,
                        left=rect[0],
                        top=rect[1],
                        right=rect[2],
                        bottom=rect[3],
                        width=w,
                        height=h,
                        is_primary=is_prim,
                    )
                    self.monitors.append(m)
                    idx += 1
            except Exception as e:
                print(f"[DisplayManager] Win32 display enum error: {e}")

        # Fallback to mss if Win32 gave nothing
        if not self.monitors and mss is not None:
            try:
                with mss.MSS() as s:
                    for i, m in enumerate(s.monitors[1:], 1):
                        is_prim = (m.get("left") == 0 and m.get("top") == 0)
                        self.monitors.append(
                            MonitorInfo(
                                id=i,
                                name=f"Monitor {i}",
                                left=m["left"],
                                top=m["top"],
                                right=m["left"] + m["width"],
                                bottom=m["top"] + m["height"],
                                width=m["width"],
                                height=m["height"],
                                is_primary=is_prim,
                            )
                        )
            except Exception:
                pass

        # Ultimate fallback to single monitor via pyautogui or default 1920x1080
        if not self.monitors:
            w, h = 1920, 1080
            if pyautogui is not None:
                try:
                    sz = pyautogui.size()
                    w, h = sz.width, sz.height
                except Exception:
                    pass
            self.monitors.append(
                MonitorInfo(
                    id=1,
                    name="Primary Monitor",
                    left=0,
                    top=0,
                    right=w,
                    bottom=h,
                    width=w,
                    height=h,
                    is_primary=True,
                )
            )

        # Sort monitors so Primary is Screen 1
        self.monitors.sort(key=lambda m: (not m.is_primary, m.left, m.top))
        for idx, m in enumerate(self.monitors, 1):
            m.id = idx

        # Compute virtual combined bounds
        min_x = min(m.left for m in self.monitors)
        min_y = min(m.top for m in self.monitors)
        max_x = max(m.right for m in self.monitors)
        max_y = max(m.bottom for m in self.monitors)

        self.virtual_left = min_x
        self.virtual_top = min_y
        self.virtual_width = max_x - min_x
        self.virtual_height = max_y - min_y

    def which_screen(self, x: int, y: int) -> int:
        """Returns monitor ID (1-indexed) containing the given global coordinates."""
        for m in self.monitors:
            if m.contains(x, y):
                return m.id
        # Fallback to closest monitor
        return 1

    def clamp(self, x: int, y: int) -> Tuple[int, int]:
        """Clamps coordinate within the total valid virtual screen boundaries."""
        cx = max(self.virtual_left, min(x, self.virtual_left + self.virtual_width - 1))
        cy = max(self.virtual_top, min(y, self.virtual_top + self.virtual_height - 1))
        return cx, cy

    def get_environment_config_text(self) -> str:
        """Formats the exact prompt configuration section with live detected resolutions."""
        lines = [
            "### ENVIRONMENT CONFIGURATION",
            f"- You are operating on a {len(self.monitors)}-monitor setup ({len(self.monitors)} screens).",
            f"- Total Combined Resolution: {self.virtual_width}x{self.virtual_height}",
            "- Display Layout:",
        ]
        for m in self.monitors:
            prim_tag = " (Primary)" if m.is_primary else " (Secondary)" if m.id == 2 else ""
            lines.append(
                f"  * Screen {m.id}{prim_tag}: {m.width}x{m.height}, "
                f"Coordinates: ({m.left},{m.top}) to ({m.right - 1}, {m.bottom - 1})"
            )
        lines.append("- The global coordinate system maps seamlessly across all screens. "
                     "Moving past screen boundaries seamlessly places the cursor on adjacent displays.")
        return "\n".join(lines)


class VisualCaptureEngine:
    """Stitches multi-monitor displays into a unified visual frame and superimposes the cursor position."""

    def __init__(self, display_mgr: DisplayManager):
        self.display_mgr = display_mgr

    def get_cursor_position(self) -> Tuple[int, int]:
        """Returns the current hardware mouse cursor position."""
        if pyautogui is not None:
            try:
                pos = pyautogui.position()
                return int(pos.x), int(pos.y)
            except Exception:
                pass
        if win32api is not None:
            try:
                x, y = win32api.GetCursorPos()
                return int(x), int(y)
            except Exception:
                pass
        return (0, 0)

    def capture_stitched(self, highlight_cursor: bool = True, allow_synthetic: bool = False) -> Tuple[Image.Image, Tuple[int, int], str]:
        """
        Captures all connected monitors and stitches them side-by-side / in their layout canvas.
        Draws the visual cursor reticle and coordinate text directly onto the stitched image.
        Returns: (PIL.Image, (cursor_x, cursor_y), saved_file_path)
        """
        self.display_mgr.refresh()
        vw = self.display_mgr.virtual_width
        vh = self.display_mgr.virtual_height
        v_left = self.display_mgr.virtual_left
        v_top = self.display_mgr.virtual_top

        stitched = Image.new("RGB", (vw, vh), color=(18, 24, 38))
        grab_success = False

        # Attempt 1: Multi-screen grab via mss
        if mss is not None:
            try:
                with mss.MSS() as s:
                    for mon_info in self.display_mgr.monitors:
                        # Find corresponding mss monitor dict
                        match_m = None
                        for sm in s.monitors[1:]:
                            if sm["left"] == mon_info.left and sm["top"] == mon_info.top:
                                match_m = sm
                                break
                        if not match_m:
                            match_m = {
                                "left": mon_info.left,
                                "top": mon_info.top,
                                "width": mon_info.width,
                                "height": mon_info.height,
                            }
                        shot = s.grab(match_m)
                        im_part = Image.frombytes("RGB", shot.size, shot.rgb)
                        offset_x = mon_info.left - v_left
                        offset_y = mon_info.top - v_top
                        stitched.paste(im_part, (offset_x, offset_y))
                        grab_success = True
            except Exception as e:
                # GDI BitBlt might fail if screen is locked
                grab_success = False

        # Attempt 2: ImageGrab.grab(all_screens=True)
        if not grab_success:
            try:
                from PIL import ImageGrab
                full = ImageGrab.grab(all_screens=True)
                if full.size[0] >= vw * 0.8:
                    stitched.paste(full.resize((vw, vh)), (0, 0))
                    grab_success = True
            except Exception:
                pass

        # A fabricated frame must never authorize a live mouse/keyboard action.
        if not grab_success:
            if not allow_synthetic:
                raise RuntimeError("no_interactive_desktop: a real screenshot could not be captured")
            self._render_synthetic_desktop(stitched)

        cursor_x, cursor_y = self.get_cursor_position()

        # Superimpose visual mouse cursor reticle
        if highlight_cursor:
            self._draw_cursor_reticle(stitched, cursor_x - v_left, cursor_y - v_top, cursor_x, cursor_y)

        # Save to disk
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:19]
        out_path = os.path.join(SCREENSHOTS_DIR, f"stitched_{timestamp}.png")
        try:
            stitched.save(out_path)
        except Exception:
            pass

        return stitched, (cursor_x, cursor_y), out_path

    def _draw_cursor_reticle(self, img: Image.Image, rx: int, ry: int, abs_x: int, abs_y: int):
        """Draws high-visibility red/white crosshair and coordinate tag at mouse cursor."""
        draw = ImageDraw.Draw(img)
        r = 14
        # Red outer ring
        draw.ellipse((rx - r, ry - r, rx + r, ry + r), outline=(239, 68, 68), width=3)
        # White inner ring
        draw.ellipse((rx - r + 3, ry - r + 3, rx + r - 3, ry + r - 3), outline=(255, 255, 255), width=1)
        # Crosshair lines
        draw.line((rx - r - 8, ry, rx + r + 8, ry), fill=(239, 68, 68), width=2)
        draw.line((rx, ry - r - 8, rx, ry + r + 8), fill=(239, 68, 68), width=2)
        # Center dot
        draw.ellipse((rx - 2, ry - 2, rx + 2, ry + 2), fill=(255, 255, 255))
        # Coordinate badge
        screen_id = self.display_mgr.which_screen(abs_x, abs_y)
        tag = f"Cursor: ({abs_x}, {abs_y}) [Screen {screen_id}]"
        # Badge background
        bx, by = rx + 16, ry + 8
        draw.rectangle((bx - 4, by - 2, bx + 190, by + 18), fill=(15, 23, 42), outline=(56, 189, 248), width=1)
        draw.text((bx, by), tag, fill=(248, 250, 252))

    def _render_synthetic_desktop(self, img: Image.Image):
        """Draws a clean schematic of the multi-monitor setup and active windows if GDI grab is unavailable."""
        draw = ImageDraw.Draw(img)
        v_left = self.display_mgr.virtual_left
        v_top = self.display_mgr.virtual_top

        # Draw monitor boundaries
        for m in self.display_mgr.monitors:
            ox = m.left - v_left
            oy = m.top - v_top
            draw.rectangle((ox, oy, ox + m.width - 1, oy + m.height - 1), fill=(30, 41, 59), outline=(100, 116, 139), width=3)
            # Header bar
            draw.rectangle((ox, oy, ox + m.width - 1, oy + 40), fill=(15, 23, 42))
            tag = f"Screen {m.id} ({'Primary' if m.is_primary else 'Secondary'}) - {m.width}x{m.height} [{m.name}]"
            draw.text((ox + 20, oy + 12), tag, fill=(148, 163, 184))

        # Query and draw visible application windows
        if win32gui is not None:
            def enum_cb(hwnd, _):
                if win32gui.IsWindowVisible(hwnd) and not win32gui.IsIconic(hwnd):
                    title = win32gui.GetWindowText(hwnd).strip()
                    if title:
                        rect = win32gui.GetWindowRect(hwnd)
                        wx, wy = rect[0] - v_left, rect[1] - v_top
                        ww = rect[2] - rect[0]
                        wh = rect[3] - rect[1]
                        if ww > 100 and wh > 80:
                            draw.rectangle((wx, wy, wx + ww, wy + wh), fill=(51, 65, 85), outline=(56, 189, 248), width=2)
                            draw.rectangle((wx, wy, wx + ww, wy + 26), fill=(15, 23, 42))
                            draw.text((wx + 8, wy + 6), title[:50], fill=(226, 232, 240))
                return True
            try:
                win32gui.EnumWindows(enum_cb, None)
            except Exception:
                pass


class ActionExecutor:
    """Executes validated multi-screen OS automation actions according to the action schema."""

    def __init__(self, display_mgr: DisplayManager, dry_run: bool = False):
        self.display_mgr = display_mgr
        self.dry_run = dry_run
        self.history: List[Dict[str, Any]] = []

    def execute(self, cmd: ActionCommand, *, approved: bool = False) -> Dict[str, Any]:
        """Validates and executes a single ActionCommand."""
        from ishti import test_mode
        is_test = self.dry_run or test_mode.enabled() or os.environ.get("ISHTI_TEST_MODE") == "1"

        result = {
            "action": cmd.action,
            "success": True,
            "error": None,
            "timestamp": datetime.now().isoformat(),
            "target_screen": None,
            "executed_coords": None,
        }

        # Resolve screen & clamp coordinates
        if cmd.x is not None and cmd.y is not None:
            cx, cy = self.display_mgr.clamp(cmd.x, cmd.y)
            cmd.target_screen = self.display_mgr.which_screen(cx, cy)
            result["target_screen"] = cmd.target_screen
            result["executed_coords"] = (cx, cy)
        else:
            cx, cy = 0, 0

        if is_test:
            # Under test mode, log and record without moving real mouse
            details = f"[TEST_MODE / DRY_RUN] Action {cmd.action} recorded cleanly."
            if cmd.action == "done":
                details += " Goal marked complete."
            result["details"] = details
            self.history.append({"cmd": cmd.to_dict(), "result": result})
            return result

        if cmd.action not in {"done", "wait"} and not approved:
            result["success"] = False
            result["error"] = "A specific approved screen action is required"
            return result

        if cmd.action in {"move_to", "click", "double_click", "drag_to"} and (
            not isinstance(cmd.x, int) or isinstance(cmd.x, bool) or not isinstance(cmd.y, int) or isinstance(cmd.y, bool)
            or not any(mon.contains(cmd.x, cmd.y) for mon in self.display_mgr.monitors)
        ):
            result["success"] = False
            result["error"] = "Coordinates must be inside a real monitor"
            return result

        if pyautogui is None:
            result["success"] = False
            result["error"] = "pyautogui is not installed or available on this host."
            return result

        try:
            if cmd.action == "move_to":
                pyautogui.moveTo(cx, cy, duration=0.25)

            elif cmd.action == "click":
                pyautogui.click(x=cx, y=cy, button=cmd.button or "left")

            elif cmd.action == "double_click":
                pyautogui.doubleClick(x=cx, y=cy, button=cmd.button or "left")

            elif cmd.action == "drag_to":
                # Crucial for dragging applications or windows across screen boundaries!
                # Uses duration to give Windows window manager time to track drag event
                pyautogui.dragTo(cx, cy, button=cmd.button or "left", duration=0.6)

            elif cmd.action == "type":
                if cmd.text:
                    pyautogui.write(cmd.text, interval=0.015)

            elif cmd.action == "press_key":
                if cmd.key:
                    k = cmd.key.strip().lower()
                    if "+" in k:
                        parts = [p.strip() for p in k.split("+")]
                        pyautogui.hotkey(*parts)
                    else:
                        pyautogui.press(k)

            elif cmd.action == "wait":
                secs = max(1, min(cmd.seconds or 1, 30))
                time.sleep(secs)

            elif cmd.action == "done":
                result["details"] = "Goal marked complete."

            else:
                result["success"] = False
                result["error"] = f"Unknown action: {cmd.action}"

        except Exception as exc:
            result["success"] = False
            result["error"] = str(exc)

        self.history.append({"cmd": cmd.to_dict(), "result": result})
        return result


class MultiScreenOSAgent:
    """
    Advanced Multi-Screen OS Automation Agent running the continuous Perceive-Plan-Act visual loop.
    Enforces dual-screen coordinate awareness, cursor tracking, and structured JSON output.
    """

    SYSTEM_PROMPT_TEMPLATE = """You are an advanced Multi-Screen OS Automation Agent. You operate via a continuous "Perceive-Plan-Act" visual loop. You have full control over the user's mouse and keyboard across all connected displays.

{env_config}

### CURRENT INPUT
You will be provided with:
1. A continuous stitched screenshot showing the visual state of BOTH screens side-by-side.
2. The current (X, Y) pixel position of the mouse cursor.

### OPERATIONAL INSTRUCTIONS
For every step, follow this mental process strictly:
1. PERCEIVE: Analyze the provided multi-screen screenshot. Locate the current position of the mouse cursor. Identify target UI elements (buttons, text boxes, windows) mentioned in the user's objective, regardless of which screen they are on.
2. PLAN: Determine what micro-action is required next to move closer to the user's goal. If an app needs to be dragged from Screen 1 to Screen 2, plan the sequence of actions.
3. ACT: Output exactly ONE structured JSON command. Do not add conversational text or code block formatting outside the JSON.

### AVAILABLE ACTIONS
Output your response using ONLY this schema:
{{
  "thought": "Brief explanation of what you see on the screens, where the cursor is, and why you are choosing this action.",
  "action": "move_to" | "click" | "double_click" | "drag_to" | "type" | "press_key" | "wait" | "done",
  "x": int,          // Required for mouse actions. Must map accurately to the target screen's canvas.
  "y": int,          // Required for mouse actions.
  "button": "left" | "right", // Optional for click, defaults to left
  "text": "string",  // Required for type action
  "key": "string",   // Required for press_key action (e.g., "enter", "ctrl+v")
  "seconds": int     // Required for wait action
}}

### CRITICAL RULES
- MONITOR AWARENESS: Always verify which screen your target window is currently residing on before calculating coordinates. 
- CURSOR TRACKING: Use the provided cursor position to gauge relative distances. If you need to click an element, output its precise (X,Y) coordinates from the global visual layout.
- STATE VERIFICATION: If you click a button and the screen doesn't change in the next loop, your click may have missed the bounding box. Re-adjust your coordinates slightly and try again.
- NEVER assume or hallucinate screen state. Rely strictly on the visual information in the latest screenshot."""

    def __init__(self, dry_run: bool = False, max_steps: int = 15):
        self.display_mgr = DisplayManager()
        self.capture_engine = VisualCaptureEngine(self.display_mgr)
        self.executor = ActionExecutor(self.display_mgr, dry_run=dry_run)
        self.max_steps = max_steps
        self.step_history: List[Dict[str, Any]] = []

    def get_system_prompt(self) -> str:
        env_text = self.display_mgr.get_environment_config_text()
        return self.SYSTEM_PROMPT_TEMPLATE.format(env_config=env_text)

    def parse_action(self, response_text: str) -> Optional[ActionCommand]:
        """Parses model response into a validated ActionCommand."""
        clean_text = response_text.strip()
        # Find JSON object inside markdown code block if present
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", clean_text, re.DOTALL)
        if m:
            clean_text = m.group(1)
        else:
            m2 = re.search(r"(\{.*\})", clean_text, re.DOTALL)
            if m2:
                clean_text = m2.group(1)

        try:
            data = json.loads(clean_text)
            action = str(data.get("action", "")).strip().lower()
            if action not in {"move_to", "click", "double_click", "drag_to", "type", "press_key", "wait", "done"}:
                return None
            if action in {"move_to", "click", "double_click", "drag_to"} and (
                not isinstance(data.get("x"), int) or isinstance(data.get("x"), bool)
                or not isinstance(data.get("y"), int) or isinstance(data.get("y"), bool)
                or not any(mon.contains(data["x"], data["y"]) for mon in self.display_mgr.monitors)
            ):
                return None
            if action == "type" and (not isinstance(data.get("text"), str) or not data["text"] or len(data["text"]) > 1000):
                return None
            if action == "press_key" and (
                not isinstance(data.get("key"), str)
                or not re.fullmatch(r"[a-z0-9+]{1,40}", data["key"], re.I)
                or data["key"].casefold() in {"win+l", "ctrl+alt+del"}
            ):
                return None
            if action in {"click", "double_click", "drag_to"} and data.get("button", "left") not in {"left", "right"}:
                return None
            if action == "wait" and (not isinstance(data.get("seconds"), int) or not 1 <= data["seconds"] <= 10):
                return None
            return ActionCommand(
                thought=str(data.get("thought", ""))[:300],
                action=action,
                x=data.get("x"),
                y=data.get("y"),
                button=data.get("button", "left"),
                text=data.get("text"),
                key=data.get("key"),
                seconds=data.get("seconds"),
            )
        except Exception as e:
            print(f"[MultiScreenOSAgent] JSON parse error: {e} in {clean_text[:100]}")
            return None

    def step(self, objective: str, prior_steps: List[Dict[str, Any]] = None) -> Tuple[ActionCommand, Dict[str, Any]]:
        """
        Executes one step of the Perceive-Plan-Act loop:
        1. Perceive: Captures multi-screen stitched frame and current cursor (X, Y).
        2. Plan: Consults reasoning engine (vision/llm) with multi-monitor geometry.
        3. Act: Dispatches action and verifies outcome.
        """
        prior_steps = prior_steps or []
        # 1. PERCEIVE
        stitched_img, (cur_x, cur_y), img_path = self.capture_engine.capture_stitched(allow_synthetic=self.executor.dry_run)
        screen_id = self.display_mgr.which_screen(cur_x, cur_y)

        # 2. PLAN
        prompt_user = (
            f"User Objective: {objective}\n\n"
            f"Current Mouse Position: X={cur_x}, Y={cur_y} (located on Screen {screen_id}).\n"
            f"Previous actions taken in this session:\n"
        )
        if not prior_steps:
            prompt_user += "  (None - this is the first action)\n"
        else:
            for i, st in enumerate(prior_steps[-4:], 1):
                prompt_user += f"  Step {i}: {st.get('action')} -> {st.get('thought', '')}\n"

        prompt_user += "\nOutput your next action using ONLY the JSON schema."

        # Call visual reasoning engine
        raw_reply = self._call_reasoning_engine(stitched_img, prompt_user)
        cmd = self.parse_action(raw_reply)

        if not cmd:
            raise RuntimeError("Vision model did not return a valid action; no screen input was sent")

        # 3. ACT
        exec_res = self.executor.execute(cmd) if self.executor.dry_run else {
            "success": False, "details": "Awaiting explicit approval of this one action"}

        step_record = {
            "step": len(self.step_history) + 1,
            "objective": objective,
            "cursor_before": (cur_x, cur_y),
            "command": cmd.to_dict(),
            "execution": exec_res,
            "screenshot": img_path,
        }
        self.step_history.append(step_record)
        return cmd, step_record

    def run_loop(self, objective: str, max_steps: Optional[int] = None) -> Dict[str, Any]:
        """
        Runs the continuous Perceive-Plan-Act loop until 'done' or max_steps is reached.
        Returns full execution trace and summary.
        """
        if not self.executor.dry_run:
            raise RuntimeError("Live visual automation requires a separate approval for each planned action")
        steps = max_steps or self.max_steps
        self.step_history = []
        start_time = time.time()

        print(f"\n[MultiScreenOSAgent] Starting OS Automation Loop for: '{objective}'")
        print(f"[MultiScreenOSAgent] Displays: {len(self.display_mgr.monitors)} monitors | "
              f"Combined: {self.display_mgr.virtual_width}x{self.display_mgr.virtual_height}")

        for i in range(1, steps + 1):
            cmd, record = self.step(objective, self.step_history)
            print(f"[Step {i}/{steps}] Action: {cmd.action} | Thought: {cmd.thought[:80]}")

            if cmd.action == "done":
                print(f"[MultiScreenOSAgent] Objective achieved in {i} steps.")
                break

            time.sleep(0.5)

        elapsed = round(time.time() - start_time, 2)
        return {
            "objective": objective,
            "completed": any(s["command"]["action"] == "done" for s in self.step_history),
            "total_steps": len(self.step_history),
            "elapsed_seconds": elapsed,
            "monitors": [asdict(m) for m in self.display_mgr.monitors],
            "history": self.step_history,
        }

    def _call_reasoning_engine(self, img: Image.Image, user_prompt: str) -> str:
        """Invokes ISHTI's local multimodal vision/LLM engine with the stitched frame."""
        sys_prompt = self.get_system_prompt()

        # Try local Ollama vision model via ishti.llm
        try:
            from ishti import llm
            # Convert stitched image to base64 JPEG
            import io
            buf = io.BytesIO()
            # Downsample slightly if ultra-wide to respect vision token budget while keeping text sharp
            max_dim = 2048
            w, h = img.size
            if max(w, h) > max_dim:
                scale = max_dim / max(w, h)
                preview = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
            else:
                preview = img
            preview.save(buf, format="JPEG", quality=85)
            b64_img = base64.b64encode(buf.getvalue()).decode("utf-8")

            # Try vision model role
            role = "vision2"
            messages = [
                {"role": "system", "content": sys_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}},
                    ],
                },
            ]
            resp = llm.chat(messages, role=role, temperature=0.0, max_tokens=250)
            if resp and resp.content:
                return resp.content
        except Exception:
            if self.executor.dry_run:
                return self._heuristic_planner(user_prompt)
            raise RuntimeError("Local vision planning failed; no screen action was taken")

        if self.executor.dry_run:
            return self._heuristic_planner(user_prompt)
        raise RuntimeError("Local vision returned no action; no screen action was taken")

    def _heuristic_planner(self, prompt: str) -> str:
        """Determines coordinate micro-actions for common desktop and multi-monitor operations."""
        low = prompt.lower()
        mon1 = self.display_mgr.monitors[0]
        mon2 = self.display_mgr.monitors[1] if len(self.display_mgr.monitors) > 1 else mon1

        if "done" in low or "finish" in low:
            return json.dumps({
                "thought": "All required visual actions for the objective have been completed.",
                "action": "done",
            })

        # Drag window from Screen 1 to Screen 2
        if "drag" in low or "move" in low and ("screen 2" in low or "monitor 2" in low or "secondary" in low):
            # Pick center of screen 1 title bar to center of screen 2
            src_x = mon1.left + int(mon1.width * 0.4)
            src_y = mon1.top + 30
            dst_x = mon2.left + int(mon2.width * 0.5)
            dst_y = mon2.top + int(mon2.height * 0.4)
            return json.dumps({
                "thought": f"Dragging target window from Screen 1 ({src_x},{src_y}) to Screen 2 ({dst_x},{dst_y}).",
                "action": "drag_to",
                "x": dst_x,
                "y": dst_y,
                "button": "left",
            })

        # Default click center of target monitor
        target_mon = mon2 if ("screen 2" in low or "second" in low) else mon1
        target_x = target_mon.left + int(target_mon.width * 0.5)
        target_y = target_mon.top + int(target_mon.height * 0.5)

        return json.dumps({
            "thought": f"Locating target element on Screen {target_mon.id} at ({target_x}, {target_y}) to execute interaction.",
            "action": "click",
            "x": target_x,
            "y": target_y,
            "button": "left",
        })


_AGENT_INSTANCE: Optional[MultiScreenOSAgent] = None


def get_multi_screen_agent(dry_run: bool = False) -> MultiScreenOSAgent:
    """Returns singleton MultiScreenOSAgent."""
    global _AGENT_INSTANCE
    if _AGENT_INSTANCE is None:
        _AGENT_INSTANCE = MultiScreenOSAgent(dry_run=dry_run)
    return _AGENT_INSTANCE


if __name__ == "__main__":
    agent = get_multi_screen_agent(dry_run=True)
    print("--- Detected Multi-Screen Environment ---")
    print(agent.get_system_prompt())
    print("\n--- Running Dry-Run Step ---")
    cmd, rec = agent.step("Move the window from Screen 1 to Screen 2 and click center")
    print("Command Output:")
    print(json.dumps(cmd.to_dict(), indent=2))
    print("\nRecorded Step:")
    print(json.dumps(rec, indent=2))
