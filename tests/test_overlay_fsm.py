"""WP-COUCOU acceptance: overlay island FSM (matrix row COU-01).

T-C01, against a fresh OverlayFSM with an injected clock:
  - hover-peek raises the strip, leaving hides it; no hidden -> home teleport
  - Esc closes exactly one level per press (home -> petit -> hidden)
  - idle-hide after quiet, but NEVER while a session is busy at home
  - notifications raise a peek, unread survives hiding, cleared by opening home
  - compact mode is a render flag; unknown events are ignored, never raise
  - the module's own __main__ self-check stays green

Run: venv\\Scripts\\python -m pytest tests/test_overlay_fsm.py -q
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from desktop.overlay import fsm  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _fsm(idle_s=4.0):
    c = Clock()
    return fsm.OverlayFSM(idle_s=idle_s, now=c), c


def test_hover_peek_and_leave_hides():
    f, _ = _fsm()
    assert f.state == fsm.HIDDEN
    assert f.handle("hover") is True
    assert f.state == fsm.PETIT
    f.handle("unhover")
    assert f.state == fsm.HIDDEN


def test_no_hidden_to_home_teleport():
    f, _ = _fsm()
    f.handle("click")
    assert f.state == fsm.HIDDEN, "clicking an invisible island must not open the panel"
    f.handle("hover")
    f.handle("click")
    assert f.state == fsm.HOME, "from petit, click opens home"


def test_esc_closes_one_level_per_press():
    f, _ = _fsm()
    f.handle("hover")
    f.handle("click")
    assert f.state == fsm.HOME
    f.handle("esc")
    assert f.state == fsm.PETIT, "first Esc: home -> petit"
    f.handle("esc")
    assert f.state == fsm.HIDDEN, "second Esc: petit -> hidden"
    assert f.handle("esc") is False, "Esc while hidden is a no-op"


def test_idle_hides_petit_after_quiet():
    f, c = _fsm(idle_s=4.0)
    f.handle("hover")
    assert f.tick() is False               # quiet period not elapsed yet
    c.t += 3.9
    assert f.tick() is False
    c.t += 0.2
    assert f.tick() is True
    assert f.state == fsm.HIDDEN


def test_home_never_idle_hides_while_busy_then_hides_after():
    f, c = _fsm(idle_s=4.0)
    f.handle("hover")
    f.handle("click")
    f.handle("busy_on")
    c.t += 99999
    for _ in range(50):
        assert f.tick() is False, "busy session at home must never be idle-hidden"
    assert f.state == fsm.HOME
    f.handle("busy_off")
    c.t += 4.1                              # busy kept the timer fresh; now let it lapse
    assert f.tick() is True, "after busy ends, quiet time still applies"
    assert f.state == fsm.HIDDEN


def test_activity_resets_the_idle_timer():
    f, c = _fsm(idle_s=4.0)
    f.handle("hover")
    c.t += 3.5
    f.handle("activity")                   # resets
    c.t += 3.5                             # 3.5 since reset, under 4
    assert f.tick() is False
    c.t += 0.6
    assert f.tick() is True


def test_notify_peeks_unread_survives_hide_and_clears_on_open():
    f, _ = _fsm()
    f.handle("notify")
    assert f.state == fsm.PETIT and f.unread == 1, "notify raises a peek when hidden"
    f.handle("hide")
    assert f.state == fsm.HIDDEN and f.unread == 1, "unread survives hiding"
    f.handle("hover")
    f.handle("click")
    assert f.state == fsm.HOME and f.unread == 0, "opening the panel clears unread"
    before = f.unread
    f.handle("notify")
    assert f.unread == before, "while home is open, live events do not pile up unread"


def test_compact_is_a_render_flag_and_unknown_events_do_not_raise():
    f, _ = _fsm()
    assert f.handle("compact_on") is True
    assert f.compact is True and f.state == fsm.HIDDEN
    f.handle("hover")
    assert f.state == fsm.PETIT, "compact does not change the state machine"
    assert f.handle("something-new") is False
    assert f.handle("") is False


def test_selfcheck_still_passes():
    mod = os.path.join(ROOT, "desktop", "overlay", "fsm.py")
    r = subprocess.run([sys.executable, mod], capture_output=True, text=True,
                       timeout=60, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[self-check] overlay fsm OK" in r.stdout
