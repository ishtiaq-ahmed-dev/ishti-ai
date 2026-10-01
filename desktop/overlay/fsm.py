"""ISHTI overlay top-edge island FSM (WP-COUCOU, matrix COU-01; coucou windows/README).

States (coucou's Windows island, behaviour re-implemented originally):
  hidden  - invisible when idle; the strip is gone, notifications still queue
  petit   - the top-edge strip / hover-peek; click opens home
  home    - the full panel; Esc closes one level at a time

Rules:
  - hover-peek: pointing at the top edge raises petit; leaving hides it again
  - click-open: click petit -> home; click while hidden does nothing (no teleport)
  - Esc-close: home -> petit -> hidden, one level per press (never a jump to hidden)
  - hide-when-idle: idle_s without activity -> hidden; NEVER while a session is busy
  - notifications raise a peek so the owner notices; unread survives hiding,
    cleared when the panel is actually opened
  - compact mode: rendering flag for notch-less screens (state stays petit)

Pure and clock-injectable: handle(event) / tick() -> True when a render is needed.
Self-check: python desktop/overlay/fsm.py
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

HIDDEN, PETIT, HOME = "hidden", "petit", "home"
STATES = (HIDDEN, PETIT, HOME)

# events that reset the idle timer (they ARE activity)
ACTIVE = {"hover", "click", "notify", "activity"}


class OverlayFSM:
    def __init__(self, idle_s: float = 4.0, now=None):
        self.idle_s = float(idle_s)
        self._now = now or time.monotonic
        self.state = HIDDEN
        self.busy = False        # a session is running: home must not auto-hide
        self.compact = False
        self.unread = 0
        self._last = self._now()

    # ---- transitions ------------------------------------------------------
    def handle(self, event: str) -> bool:
        """Apply one event; True when something the renderer must see changed."""
        before = (self.state, self.compact, self.unread, self.busy)
        ev = str(event)
        if ev in ACTIVE:
            self._last = self._now()

        if ev == "hover":
            if self.state == HIDDEN:
                self.state = PETIT
        elif ev == "unhover":
            if self.state == PETIT:
                self.state = HIDDEN
        elif ev == "click":
            if self.state == PETIT:                 # click-open
                self.state = HOME
                self.unread = 0
            # click while hidden: nothing is visible, nothing happens (no hidden->home)
        elif ev == "esc":
            if self.state == HOME:                  # staged close, one level per press
                self.state = PETIT
            elif self.state == PETIT:
                self.state = HIDDEN
        elif ev == "hide":
            self.state = HIDDEN
        elif ev == "notify":
            if self.state == HOME:                  # owner is already looking
                pass
            else:
                self.unread += 1
                if self.state == HIDDEN:
                    self.state = PETIT              # peek so the owner notices
        elif ev == "busy_on":
            self.busy = True
        elif ev == "busy_off":
            self.busy = False
        elif ev == "compact_on":
            self.compact = True
        elif ev == "compact_off":
            self.compact = False
        elif ev == "idle":
            return self._idle()
        elif ev == "activity":
            pass
        else:
            return False                            # unknown event: ignore, never raise
        return (self.state, self.compact, self.unread, self.busy) != before

    def tick(self) -> bool:
        """Periodic check: hide after idle_s of quiet (never while busy at home)."""
        if self.state == HIDDEN:
            self._last = self._now()                # nothing to hide; keep clock fresh
            return False
        if self._now() - self._last >= self.idle_s:
            return self._idle()
        return False

    def _idle(self) -> bool:
        if self.state == HOME and self.busy:
            self._last = self._now()                # running session counts as activity
            return False
        if self.state == HIDDEN:
            return False
        self.state = HIDDEN
        return True


if __name__ == "__main__":
    # self-check: peek, staged close, no hidden->home teleport, idle-hide with busy-protect,
    # unread persistence, compact flag
    clock = [0.0]
    f = OverlayFSM(idle_s=4.0, now=lambda: clock[0])
    assert f.state == HIDDEN
    f.handle("hover")
    assert f.state == PETIT, "hover-peek raises petit"
    f.handle("unhover")
    assert f.state == HIDDEN, "leaving hides the peek"
    f.handle("click")
    assert f.state == HIDDEN, "no hidden->home teleport"
    f.handle("hover")
    f.handle("click")
    assert f.state == HOME, "click petit opens home"
    f.handle("esc")
    assert f.state == PETIT, "Esc closes one level"
    f.handle("esc")
    assert f.state == HIDDEN, "Esc again hides"
    f.handle("notify")
    assert f.state == PETIT and f.unread == 1, "notify raises a peek"
    f.handle("hide")
    assert f.state == HIDDEN and f.unread == 1, "unread survives hiding"
    f.handle("hover")
    f.handle("click")
    assert f.state == HOME and f.unread == 0, "opening the panel clears unread"
    f.handle("busy_on")
    clock[0] += 9999
    f.tick()
    assert f.state == HOME and f.busy, "busy session: never idle-hidden"
    f.handle("busy_off")
    clock[0] += 9999                         # busy kept the timer fresh; now let it lapse
    f.tick()
    assert f.state == HIDDEN, "quiet after busy: idle-hide applies"
    f.handle("hover")
    f.handle("compact_on")
    assert f.compact and f.state == PETIT, "compact is a render flag, state unchanged"
    assert f.handle("no-such-event") is False, "unknown events are ignored, not errors"
    print("[self-check] overlay fsm OK - peek, staged close, busy-protected idle-hide, unread, compact")
