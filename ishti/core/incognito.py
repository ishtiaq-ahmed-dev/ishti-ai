"""Incognito mode (Part H3): a conversation that writes nothing to memory.

One process-wide flag, mirrored into the request session so every writer can check it.
When it is on, the dispatcher's background hooks (semantic remember, ftlog, arch2
consolidation, owner-learning, turn-learning) are skipped and the chat itself is not
persisted. Explicit commands still work — 'forget that' while incognito is fine, since
it REMOVES rather than remembers.

A session can pin its own mode with session['incognito']=True (an integration hook);
the per-session value wins over the global toggle.
"""
from __future__ import annotations

import os
import threading

_flag = threading.Event()
_lock = threading.Lock()


def enabled(session: dict | None = None) -> bool:
    """True when nothing should be written for this request (per-session wins)."""
    if isinstance(session, dict) and session.get("incognito") is not None:
        return bool(session["incognito"])
    return _flag.is_set() or os.environ.get("ISHTI_INCOGNITO") == "1"


def set_enabled(on: bool) -> None:
    with _lock:
        if on:
            _flag.set()
        else:
            _flag.clear()


def mark_session(session: dict, on: bool = True) -> None:
    """Pin incognito onto one request session (the API can pass it per chat tab)."""
    if isinstance(session, dict):
        session["incognito"] = bool(on)


if __name__ == "__main__":
    assert not enabled()  # default off
    set_enabled(True)
    assert enabled()
    assert not enabled({"incognito": False}), "per-session override wins over the global flag"
    mark_session({"incognito": None}, True)
    assert enabled({"incognito": True})
    set_enabled(False)
    assert not enabled()
    os.environ["ISHTI_INCOGNITO"] = "1"
    assert enabled()
    os.environ.pop("ISHTI_INCOGNITO")
    print("[self-check] core.incognito OK — global toggle, env override, per-session pin")
