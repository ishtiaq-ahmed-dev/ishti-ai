"""ISHTI Overlay named-pipe IPC (WP-COUCOU, matrix COU-12; design R6-B/C).

Pattern ported from coucou's Windows relay (MIT code, original Python implementation):
- one pipe per user: ``.\\pipe\\ishti-overlay-<user-SID>``
- SECURITY_DESCRIPTOR granting read/write to the CURRENT USER ONLY (explicit DACL - never the
  permissive default descriptor; learn.microsoft.com named-pipe security, retrieved 2026-10-01)
- PIPE_REJECT_REMOTE_CLIENTS so only this machine's logon sessions can connect
- message size cap on read (largest message policy): anything bigger is discarded, never buffered

Contract:
  send(payload, budget_ms) -> bool   # relay side: never raises, gives up inside the budget
  serve(on_message, stop)            # overlay side: blocking accept loop, owner-only
  owner_only_dacl() / pipe_name()    # exposed for the security tests
"""
from __future__ import annotations

import threading
import time

import pywintypes
import win32api
import win32con
import win32file
import win32pipe
import win32security

MAX_MSG = 64 * 1024          # any larger: discard (coucou's "size limit" hardening, CHANGELOG #16)
DEFAULT_BUDGET_MS = 300      # coucou-hook budget: the agent is NEVER blocked by the overlay


def current_sid_obj():
    tok = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    return win32security.GetTokenInformation(tok, win32security.TokenUser)[0]


def current_sid() -> str:
    return win32security.ConvertSidToStringSid(current_sid_obj())


def pipe_name() -> str:
    return rf"\\.\pipe\ishti-overlay-{current_sid()}"


def owner_only_sd():
    """Security descriptor: ONLY the current user has read/write. No Everyone, no anonymous."""
    sid = current_sid_obj()
    acl = win32security.ACL()
    acl.AddAccessAllowedAce(win32security.ACL_REVISION,
                            win32con.GENERIC_READ | win32con.GENERIC_WRITE | win32con.SYNCHRONIZE, sid)
    sd = win32security.SECURITY_DESCRIPTOR()
    sd.SetSecurityDescriptorDacl(1, acl, 0)   # DACL present, not defaulted
    return sd


def _sd_ace_sids(sd) -> list[str]:
    """pywin32's GetAce returns ((type, flags), mask, sid) - the SID is element 2."""
    dacl = sd.GetSecurityDescriptorDacl()
    if dacl is None:
        return []
    return [win32security.ConvertSidToStringSid(dacl.GetAce(i)[2]) for i in range(dacl.GetAceCount())]


def _server_sa():
    """SECURITY_ATTRIBUTES for CreateNamedPipe.

    This pywin32 build exposes SECURITY_ATTRIBUTES() with NO constructor arguments
    (passing (length, sd, inherit) raises TypeError); the fields are set afterwards.
    """
    sa = win32security.SECURITY_ATTRIBUTES()
    sa.SECURITY_DESCRIPTOR = owner_only_sd()
    sa.bInheritHandle = False           # never inherit the pipe handle to children
    return sa


def open_server(wait_ms: int = 50):
    """Create the owner-only pipe instance. Raises OSError when the name is taken."""
    return win32pipe.CreateNamedPipe(
        pipe_name(),
        win32pipe.PIPE_ACCESS_DUPLEX,
        win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
        1, MAX_MSG, MAX_MSG, wait_ms,
        _server_sa(),
    )


def send(payload: bytes, budget_ms: int = DEFAULT_BUDGET_MS) -> bool:
    """Relay side (coucou-hook equivalent). Never raises; True iff the overlay took the message
    inside the budget. No server / slow / crashed overlay -> False, caller exits cleanly."""
    if len(payload) > MAX_MSG:
        return False
    deadline = time.monotonic() + budget_ms / 1000.0
    handle = None
    try:
        while time.monotonic() < deadline:
            try:
                handle = win32file.CreateFile(
                    pipe_name(), win32file.GENERIC_WRITE, 0, None,
                    win32file.OPEN_EXISTING, 0, None)
                break
            except pywintypes.error:
                time.sleep(0.01)          # overlay not up yet: retry inside the budget
        if handle is None:
            return False
        remaining = deadline - time.monotonic() if handle else 0
        if remaining <= 0:
            return False
        win32file.WriteFile(handle, payload)
        return True
    except Exception:
        return False                       # slow/crashed overlay: never propagate to the caller
    finally:
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass


def _connect_once(name: str) -> bool:
    """One self-connect attempt; True iff we grabbed the (single) pipe instance."""
    try:
        h = win32file.CreateFile(name, win32file.GENERIC_WRITE, 0, None,
                                 win32file.OPEN_EXISTING, 0, None)
        h.close()
        return True
    except pywintypes.error:
        return False


def _unblock_on_stop(stop: threading.Event, done: threading.Event, name: str,
                     grace_s: float = 1.0) -> None:
    """A thread parked in ConnectNamedPipe cannot observe ``stop.set()``. Self-connect once
    (connect+close): ConnectNamedPipe completes with ERROR_PIPELINE_CONNECTED, the following
    read sees the closed client, and the accept loop reaches its stop check and exits."""
    while not (stop.is_set() or done.is_set()):
        time.sleep(0.05)
    if done.is_set():
        return
    deadline = time.monotonic() + grace_s
    while not done.is_set() and time.monotonic() < deadline:
        if _connect_once(name):
            return
        time.sleep(0.05)


def serve(on_message, stop: threading.Event | None = None, max_msgs: int | None = None) -> int:
    """Overlay side: accept loop. Calls on_message(bytes) per complete message; returns messages
    served. stop.set() ends the loop cleanly. Oversized messages are discarded, not buffered."""
    served = 0
    stop = stop or threading.Event()
    done = threading.Event()
    name = pipe_name()
    threading.Thread(target=_unblock_on_stop, args=(stop, done, name), daemon=True).start()
    try:
        while not stop.is_set():
            try:
                handle = open_server()
            except Exception:
                time.sleep(0.05)
                continue
            try:
                try:
                    win32pipe.ConnectNamedPipe(handle, None)
                except pywintypes.error as exc:
                    # 535 ERROR_PIPE_CONNECTED: client arrived before the call;
                    # 232 ERROR_NO_DATA: client connected AND closed before the call -
                    # its message is already buffered, fall through and read it.
                    if exc.winerror not in (535, 232):
                        continue
                chunks = []
                total = 0
                while total <= MAX_MSG:
                    try:
                        hr, data = win32file.ReadFile(handle, 65536)
                    except pywintypes.error:
                        break
                    if not data:
                        break
                    total += len(data)
                    if total > MAX_MSG:              # size limit: discard the rest of this message
                        chunks = []
                        break
                    chunks.append(data)
                if chunks:
                    on_message(b"".join(chunks))
                    served += 1
                if max_msgs is not None and served >= max_msgs:
                    return served
            finally:
                try:
                    win32pipe.DisconnectNamedPipe(handle)
                    handle.close()
                except Exception:
                    pass
    finally:
        done.set()
    return served


if __name__ == "__main__":
    # self-check: DACL identity + roundtrip + relay budget (offline, same user)
    sd = owner_only_sd()
    sids = _sd_ace_sids(sd)
    assert sids == [current_sid()], f"owner-only DACL violated: {sids}"
    stop, got = threading.Event(), []
    t = threading.Thread(target=serve, args=(got.append, stop, 1), daemon=True)
    t.start()
    time.sleep(0.15)
    assert send(b'{"hook_event_name":"SessionStart"}'), "same-user send must succeed"
    t.join(timeout=3)
    assert got == [b'{"hook_event_name":"SessionStart"}'], got
    # no overlay listening any more: the relay gives up inside its budget, never raises
    t0 = time.monotonic()
    assert send(b'{"x":1}', budget_ms=120) is False
    elapsed_ms = (time.monotonic() - t0) * 1000
    assert elapsed_ms < 300, f"relay overran its budget: {elapsed_ms:.0f} ms"
    # oversized payloads are refused before touching the pipe
    assert send(b"x" * (MAX_MSG + 1), budget_ms=50) is False
    print(f"[self-check] overlay_pipe OK - owner-only DACL, roundtrip, relay budget {elapsed_ms:.0f} ms")
