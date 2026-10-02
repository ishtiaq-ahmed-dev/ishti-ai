"""Overlay window shell: the top-edge island (WP-COUCOU COU-01; JEV-04 waveform bubble).

The island is a small always-on-top, frameless window at the top edge. Rendering tech:
pywebview (already ISHTI's desktop tech, desktop/app_window.py). Everything GUI-facing is
INJECTED (`factory`, `js`) so the whole shell runs headless in tests:

  * window options are enforced: on_top, frameless, non-resizable, no easy-drag
  * never steals focus: after creation a best-effort WS_EX_NOACTIVATE is applied to the
    native hwnd (Windows); the page's shell is pointer-events:none except interactive
    controls, so the island is click-through (JEV-04's bubble requirement)
  * state: the FSM (hidden/petit/home) drives the window size; pills/mascot levels are
    pushed into the page as one JSON payload via window.ishti.setState(...)
  * handle_event() is the single entry point for both manual actions ("hover", "click",
    "esc", "hide") and hook events ("PreToolUse", "Stop", "Notification", ...)
  * the waveform bubble (JEV-04): set_levels() feeds bounded, clamped mic levels drawn in
    the page; it lives in the click-through strip and never takes keyboard focus

Never raises: a missing GUI backend makes open() False with the page still generated.
Self-check: python desktop/overlay/window.py
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from desktop.overlay import fsm as fsm_mod  # noqa: E402
from desktop.overlay import mascot as mascot_mod  # noqa: E402
from desktop.overlay import pills as pills_mod  # noqa: E402

SIZES = {"hidden": (420, 3), "petit": (420, 46), "home": (460, 380)}  # hidden = a thin hover strip
MAX_LEVELS = 48
BUSY_ON = {"PreToolUse", "UserPromptSubmit", "SubagentStart"}
BUSY_OFF = {"Stop", "SessionEnd", "PostToolUseFailure"}
NOTIFY = {"Notification", "PostToolUseFailure", "RateLimit"}
PASSTHROUGH = {"hover", "unhover", "click", "esc", "hide", "notify", "activity"}


def _default_factory(**opts):
    import webview
    title = opts.pop("title", "ISHTI")
    html = opts.pop("html", "<html></html>")
    return webview.create_window(title, html=html, **opts)


class OverlayWindow:
    def __init__(self, *, factory=None, js=None, fsm=None, pills=None, mascot=None,
                 title="ISHTI overlay", click_through=True, now=None):
        self._factory = factory or _default_factory
        self._js = js
        self.fsm = fsm or fsm_mod.OverlayFSM(now=now)
        self.pills = pills if pills is not None else pills_mod.Pills()
        self.mascot = mascot or mascot_mod.Mascot(now=now)
        self.title = title
        self.click_through = bool(click_through)
        self._handle = None
        self._levels = []
        self._opened_no_activate = False

    # ------------------------------------------------------------------ page
    def page(self) -> str:
        """The island page. Original art only; no external assets; shell is click-through."""
        try:
            sprite = mascot_mod.sprite_svg(self.mascot, size=36)
        except Exception:
            sprite = ""
        shell_pointer = "none" if self.click_through else "auto"
        return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{self.title}</title>
<style>
 html,body{{margin:0;background:transparent;overflow:hidden;font:12px system-ui,Segoe UI,sans-serif}}
 #island{{position:fixed;inset:0;display:flex;align-items:center;gap:8px;padding:4px 10px;
   border-radius:10px;background:rgba(16,20,24,.92);color:#d8e0e8;pointer-events:{shell_pointer};
   border:1px solid #2a333c}}
 #island .interactive{{pointer-events:auto}}
 #wave{{width:72px;height:18px}}
 #pills{{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
 #mascot{{width:36px;height:36px}}
 .pill{{display:inline-block;background:#1f6f68;border-radius:8px;padding:1px 7px;margin-right:6px}}
 .pill.error{{background:#8a3a1f}} .pill.question{{background:#8a5a1f}}
 .pill.finished,.pill.idle{{background:#3a4650}}
</style></head><body>
<div id="island" data-state="hidden" data-click-through="{str(self.click_through).lower()}">
  <span id="mascot" class="interactive">{sprite}</span>
  <canvas id="wave" width="72" height="18"></canvas>
  <span id="pills"></span>
</div>
<script>
window.ishti = window.ishti || {{}};
window.ishti.setState = function(p) {{
  const island = document.getElementById("island");
  island.dataset.state = p.state || "hidden";
  document.getElementById("pills").innerHTML = (p.pills || []).map(x =>
    '<span class="pill ' + (x.state || "") + '" title="' + (x.ticker || "") + '">' +
    x.agent + ' ' + x.state + '</span>').join("");
  island.dataset.mood = p.mood || "neutral";
  const c = document.getElementById("wave");
  if (c && c.getContext) {{
    const g = c.getContext("2d"); g.clearRect(0, 0, c.width, c.height);
    g.fillStyle = "#6cc7bd";
    (p.levels || []).forEach((v, i) => {{
      const h = Math.max(1, Math.round(v * c.height));
      g.fillRect(i * 3, (c.height - h) / 2, 2, h);
    }});
  }}
  return true;
}};
</script></body></html>"""

    # ------------------------------------------------------------------ window
    def open(self) -> bool:
        """Create the island window. False (honestly) when the GUI backend is unavailable."""
        try:
            w, h = SIZES.get(self.fsm.state, SIZES["petit"])
            self._handle = self._factory(
                title=self.title, html=self.page(), width=w, height=h, x=None, y=0,
                on_top=True, frameless=True, resizable=False, easy_drag=False,
                background_color="#00000000", transparent=True)
            self._opened_no_activate = self._apply_no_activate()
            self.push_state()
            return self._handle is not None
        except Exception:
            self._handle = None
            return False

    def _apply_no_activate(self) -> bool:
        """Best effort WS_EX_NOACTIVATE on the native hwnd - the island must never steal focus."""
        try:
            native = getattr(self._handle, "native", None)
            hwnd = getattr(native, "Handle", None)
            if not hwnd:
                return False
            import ctypes
            GWL_EXSTYLE, WS_EX_NOACTIVATE = -20, 0x08000000
            user32 = ctypes.windll.user32
            cur = user32.GetWindowLongW(int(hwnd), GWL_EXSTYLE)
            user32.SetWindowLongW(int(hwnd), GWL_EXSTYLE, cur | WS_EX_NOACTIVATE)
            return True
        except Exception:
            return False

    def close(self) -> bool:
        try:
            if self._handle is not None:
                destroy = getattr(self._handle, "destroy", None)
                if callable(destroy):
                    destroy()
            self._handle = None
            return True
        except Exception:
            self._handle = None
            return False

    def is_open(self) -> bool:
        return self._handle is not None

    # ------------------------------------------------------------------ state
    def levels(self) -> list:
        return list(self._levels)

    def set_levels(self, values) -> list:
        """Feed mic levels (JEV-04): clamped to [0, 1], bounded, no exceptions."""
        try:
            vals = values if isinstance(values, (list, tuple)) else [values]
            for v in vals:
                try:
                    x = abs(float(v))
                except (TypeError, ValueError):
                    continue
                self._levels.append(max(0.0, min(1.0, x)))
            if len(self._levels) > MAX_LEVELS:
                del self._levels[:-MAX_LEVELS]
        except Exception:
            pass
        self.push_state()
        return self.levels()

    def state(self) -> dict:
        try:
            snap = self.pills.snapshot() if self.pills is not None else []
        except Exception:
            snap = []
        return {"v": 1, "state": self.fsm.state, "compact": bool(getattr(self.fsm, "compact", False)),
                "unread": int(getattr(self.fsm, "unread", 0)), "busy": bool(getattr(self.fsm, "busy", False)),
                "pills": list(snap), "mood": self.mascot.tick() if self.mascot else "neutral",
                "levels": self.levels(), "click_through": self.click_through,
                "no_activate": self._opened_no_activate,
                "size": list(SIZES.get(self.fsm.state, SIZES["petit"]))}

    def push_state(self) -> bool:
        """Push one JSON payload into the page (window.ishti.setState). False when no handle."""
        try:
            payload = json.dumps(self.state(), ensure_ascii=False)
            if self._js is not None:
                return bool(self._js(f"window.ishti.setState({payload})"))
            if self._handle is None:
                return False
            evaluate = getattr(self._handle, "evaluate_js", None)
            if not callable(evaluate):
                return False
            return bool(evaluate(f"window.ishti.setState({payload})"))
        except Exception:
            return False

    def handle_event(self, event, *, agent=None, label="") -> bool:
        """One entry point: FSM + pills + mascot, then a render push. Never raises."""
        try:
            ev = str(event)
            if ev in PASSTHROUGH:
                self.fsm.handle(ev)
            if self.pills is not None:
                try:
                    self.pills.apply(ev, agent=agent, label=label)
                except Exception:
                    pass
            if ev in BUSY_ON:
                self.fsm.handle("busy_on")
            elif ev in BUSY_OFF:
                self.fsm.handle("busy_off")
            if ev in NOTIFY:
                self.fsm.handle("notify")
                if self.mascot is not None:
                    (self.mascot.annoyed if ev == "PostToolUseFailure" else self.mascot.dizzy)()
            elif ev == "SessionStart" and self.mascot is not None:
                self.mascot.greet()
            self.push_state()
            return True
        except Exception:
            return False


if __name__ == "__main__":
    opened = {}

    class FakeHandle:
        def __init__(self):
            self.calls = []

        def evaluate_js(self, code):
            self.calls.append(code)
            return True

        def destroy(self):
            self.calls.append("destroy")

    def factory(**opts):
        opened.update(opts)
        return FakeHandle()

    win = OverlayWindow(factory=factory, js=None)
    assert win.open() and win.is_open()
    assert opened["on_top"] is True and opened["frameless"] is True
    assert opened["resizable"] is False and opened["easy_drag"] is False
    assert 'data-ishti-mascot="pebble-v1"' in opened["html"] and 'id="wave"' in opened["html"]
    assert "pointer-events:none" in opened["html"], "click-through shell"
    handle = win._handle
    win.handle_event("hover")
    assert win.fsm.state == "petit" and handle.calls, "state is pushed"
    assert "petit" in handle.calls[-1], handle.calls[-1][:120]
    win.handle_event("PreToolUse", agent="claude-cc", label="Bash")
    assert win.fsm.busy and "claude-cc" in handle.calls[-1]
    win.handle_event("Stop", agent="claude-cc")
    assert not win.fsm.busy
    win.handle_event("Notification", agent="ishti", label="Proceed?")
    assert win.fsm.unread >= 1 and win.mascot.mood == "dizzy"
    assert win.set_levels([0.2, -0.5, 2, "junk"]) == [0.2, 0.5, 1.0], win.levels()
    win.set_levels([0.1] * 100)
    assert len(win.levels()) == MAX_LEVELS, "levels are bounded"
    st = win.state()
    assert st["click_through"] is True and st["mood"] in mascot_mod.MOODS and st["pills"]
    assert win.close() and not win.is_open() and win.push_state() is False
    # a broken backend is honest, never an exception
    win2 = OverlayWindow(factory=lambda **k: (_ for _ in ()).throw(ImportError("no webview")))
    assert win2.open() is False and win2.is_open() is False and "pebble-v1" in win2.page()
    assert OverlayWindow(js=lambda c: False).push_state() is False
    print("[self-check] overlay window OK - on-top frameless island options, click-through page, "
          "FSM/pills/mascot state push, bounded waveform levels, honest backend failure")
