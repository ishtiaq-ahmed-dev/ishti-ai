"""WP-COUCOU acceptance: overlay named-pipe security + contract (matrix rows COU-12, COU-13).

Proves, against freshly created pipes (never the owner's running overlay):
  T-COU-12 owner-only DACL on the descriptor AND on the actual pipe creation call
           (explicit ACE for the current user only; no Everyone, no anonymous),
           plus an ENFORCEMENT proof: a token restricted to World only is DENIED
           on our pipe while an Everyone-allow control pipe GRANTS it (so the
           check discriminates - a permissive default would not deny);
  T-COU-13 same-user roundtrip; stop.set() ends the accept loop promptly;
           oversized messages are discarded (never delivered, server survives);
           a message of exactly MAX_MSG is delivered (the cap is >, not >=).

Run: venv\\Scripts\\python -m pytest tests/test_overlay_pipe.py -q
"""
import os
import subprocess
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

pytest.importorskip("win32pipe", reason="pywin32 not installed - overlay pipe is Windows-only")

import win32api  # noqa: E402
import win32con  # noqa: E402
import win32file  # noqa: E402
import win32pipe  # noqa: E402
import win32security  # noqa: E402

from ishti import overlay_pipe as op  # noqa: E402


def _wait_for_instance(name: str, timeout_s: float = 3.0) -> None:
    """Poll until a server instance exists (WaitNamedPipe does not connect)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            win32pipe.WaitNamedPipe(name, 100)
            return
        except Exception:
            time.sleep(0.02)
    raise AssertionError(f"pipe {name} never appeared")


# ---------------------------------------------------------------- T-COU-12: owner-only DACL

def test_dacl_is_single_owner_allow_ace():
    sd = op.owner_only_sd()
    sids = op._sd_ace_sids(sd)
    assert sids == [op.current_sid()], f"owner-only DACL violated: {sids}"
    dacl = sd.GetSecurityDescriptorDacl()
    assert dacl.GetAceCount() == 1, "exactly one ACE - no Everyone/Anonymous/SYSTEM widening"
    (ace_type, _flags), mask, _sid = dacl.GetAce(0)
    assert ace_type == 0, "ACCESS_ALLOWED_ACE_TYPE expected, no audit/deny surprises"
    assert mask & win32con.GENERIC_READ and mask & win32con.GENERIC_WRITE


def test_open_server_passes_owner_only_security_attributes(monkeypatch):
    """The REAL open_server() call must hand Windows our SD with inheritance off -
    not the permissive default descriptor."""
    real = win32pipe.CreateNamedPipe
    captured = {}

    def spy(*args, **kwargs):
        captured["args"] = args
        return real(*args, **kwargs)

    monkeypatch.setattr(op.win32pipe, "CreateNamedPipe", spy)
    handle = op.open_server()
    try:
        sa = captured["args"][7]
        assert not sa.bInheritHandle, "pipe handle must never be inheritable"
        assert op._sd_ace_sids(sa.SECURITY_DESCRIPTOR) == [op.current_sid()]
    finally:
        monkeypatch.undo()
        handle.close()


def test_world_only_token_is_denied_while_permissive_control_grants():
    """Enforcement: restrict our own token to World only (S-1-1-0). Opening the real
    owner-only pipe MUST fail; opening an Everyone-allow control pipe MUST succeed -
    proving the deny comes from OUR DACL, not from a broken impersonation."""
    world = win32security.ConvertStringSidToSid("S-1-1-0")
    tok = win32security.OpenProcessToken(win32api.GetCurrentProcess(),
                                          win32security.TOKEN_ALL_ACCESS)
    rt = win32security.CreateRestrictedToken(tok, 0, [], [], [(world, 0)])

    def try_open(name):
        try:
            win32security.ImpersonateLoggedOnUser(rt)
            try:
                h = win32file.CreateFile(name, win32file.GENERIC_WRITE, 0, None,
                                         win32file.OPEN_EXISTING, 0, None)
                h.close()
                return True
            except Exception:               # pywintypes.error is not an OSError subclass
                return False
        finally:
            win32security.RevertToSelf()

    # control pipe with an EXPLICIT Everyone-allow DACL (same mechanics as the probe)
    control_name = rf"\\.\pipe\ishti-overlay-control-{op.current_sid()}"
    acl = win32security.ACL()
    acl.AddAccessAllowedAce(win32security.ACL_REVISION,
                            win32con.GENERIC_READ | win32con.GENERIC_WRITE, world)
    sd = win32security.SECURITY_DESCRIPTOR()
    sd.SetSecurityDescriptorDacl(1, acl, 0)
    sa = win32security.SECURITY_ATTRIBUTES()
    sa.SECURITY_DESCRIPTOR = sd
    sa.bInheritHandle = False
    control = win32pipe.CreateNamedPipe(
        control_name, win32pipe.PIPE_ACCESS_DUPLEX,
        win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
        1, op.MAX_MSG, op.MAX_MSG, 50, sa)
    server = op.open_server()
    try:
        denied_on_owner_only = not try_open(op.pipe_name())
        granted_on_control = try_open(control_name)
    finally:
        server.close()
        control.close()
    assert denied_on_owner_only, "World-only token must NOT open the owner-only overlay pipe"
    assert granted_on_control, "control: Everyone-allow pipe must open - else the check is blind"


# ---------------------------------------------------------------- T-COU-13: contract

def test_roundtrip_same_user_delivers_exact_payload():
    stop, got = threading.Event(), []
    t = threading.Thread(target=op.serve, args=(got.append, stop, 1), daemon=True)
    t.start()
    try:
        payload = b'{"hook_event_name":"Notification","title":"x"}'
        assert op.send(payload, budget_ms=2000), "same-user send must succeed"
        t.join(timeout=5)
        assert got == [payload], got
    finally:
        stop.set()
        t.join(timeout=3)


def test_stop_ends_accept_loop_promptly_even_while_waiting_for_a_client():
    """No client ever connects; stop.set() must still end serve() (self-connect unblock)."""
    stop = threading.Event()
    result = []
    t = threading.Thread(target=lambda: result.append(op.serve(lambda m: None, stop)),
                         daemon=True)
    t.start()
    try:
        _wait_for_instance(op.pipe_name())
        t0 = time.monotonic()
        stop.set()
        t.join(timeout=3)
        elapsed = time.monotonic() - t0
        assert not t.is_alive(), "serve() must honour stop.set() while parked in ConnectNamedPipe"
        assert elapsed < 2.5, f"stop took {elapsed:.2f}s - must be prompt"
        assert result == [0]
    finally:
        stop.set()
        t.join(timeout=3)


def test_send_refuses_oversized_payload_before_touching_the_pipe():
    t0 = time.monotonic()
    assert op.send(b"x" * (op.MAX_MSG + 1), budget_ms=50) is False
    assert (time.monotonic() - t0) < 0.2, "size refusal must be instant, not a budget wait"


def test_oversized_message_is_discarded_and_server_survives():
    """A raw client bypassing send() writes > MAX_MSG: the overlay must NOT deliver it
    (never buffered), and must keep serving the next, well-formed message."""
    stop, got = threading.Event(), []
    t = threading.Thread(target=op.serve, args=(got.append, stop, 2), daemon=True)
    t.start()
    try:
        _wait_for_instance(op.pipe_name())
        raw = b"Z" * (op.MAX_MSG + 4096)
        done = threading.Event()

        def raw_write():
            try:
                h = win32file.CreateFile(op.pipe_name(), win32file.GENERIC_WRITE, 0, None,
                                         win32file.OPEN_EXISTING, 0, None)
                win32file.WriteFile(h, raw)
                h.close()
            except Exception:
                pass
            done.set()

        threading.Thread(target=raw_write, daemon=True).start()
        assert done.wait(5), "raw write must not hang"
        time.sleep(0.1)
        assert got == [], f"oversized message must never reach the overlay: {len(got)} delivered"
        # server still healthy: a normal message goes through
        assert op.send(b"after-oversized", budget_ms=2000)
        deadline = time.monotonic() + 3
        while got != [b"after-oversized"] and time.monotonic() < deadline:
            time.sleep(0.05)
        assert got == [b"after-oversized"], got
    finally:
        # NOTE: the discarded message does NOT increment `served`, so max_msgs=2 is never
        # reached - stop the accept loop explicitly or this instance leaks into later tests.
        stop.set()
        t.join(timeout=3)
        assert not t.is_alive(), "serve thread leaked past stop.set()"


def test_message_of_exactly_max_bytes_is_delivered():
    """The cap is > MAX_MSG (discard) not >= : exactly MAX_MSG is legitimate."""
    stop, got = threading.Event(), []
    t = threading.Thread(target=op.serve, args=(got.append, stop, 1), daemon=True)
    t.start()
    try:
        boundary = b"B" * op.MAX_MSG
        assert op.send(boundary, budget_ms=3000), "boundary payload must be accepted by send()"
        deadline = time.monotonic() + 3
        while not got and time.monotonic() < deadline:
            time.sleep(0.05)
        assert got == [boundary], f"expected the exact {op.MAX_MSG}-byte payload, got {[len(g) for g in got]}"
        t.join(timeout=5)
        assert not t.is_alive()
    finally:
        stop.set()
        t.join(timeout=3)


def test_selfcheck_still_passes():
    """The module's own __main__ invariant block must stay green."""
    mod = os.path.join(ROOT, "ishti", "overlay_pipe.py")
    r = subprocess.run([sys.executable, mod], capture_output=True, text=True,
                       timeout=60, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[self-check] overlay_pipe OK" in r.stdout
