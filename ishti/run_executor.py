"""Run executor (WP1): turns a queued run into a server-side dispatcher execution.

Runs execute on worker threads, independent of any UI connection. The executor maps the
dispatcher's result and trace events onto the durable run (run_step / run_delta / run_finished /
run_failed) so replay from the event log rebuilds everything the UI missed.
"""
from __future__ import annotations

import threading
import time

try:
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None

_MAX_WORKERS = 4
_sem = threading.BoundedSemaphore(_MAX_WORKERS)


def launch(run_id: str) -> threading.Thread:
    t = threading.Thread(target=_execute, args=(run_id,), name=f"ishti-run-{run_id}", daemon=True)
    t.start()
    return t


def _execute(run_id: str) -> None:
    from ishti import runs
    run = runs.get_run(run_id)
    if not run or run["state"] in ("done", "failed", "cancelled"):
        return
    with _sem:  # bounded concurrency; excess runs stay queued in the DB, never lost
        run = runs.get_run(run_id)
        if not run or run["state"] in ("done", "failed", "cancelled"):
            return
        t0 = time.perf_counter()
        cancelled = threading.Event()
        try:
            _run_one(run_id, run, cancelled, t0)
        except BaseException as exc:  # never leave a run stuck in queued/running
            if type(exc).__name__ == "Cancelled":
                runs.cancel_run(run_id)
            else:
                runs.fail_run(run_id, f"{type(exc).__name__}: {exc}")
                swallowed("ishti/run_executor.py", exc)


def _run_one(run_id: str, run: dict, cancelled: threading.Event, t0: float) -> None:
    from ishti import runs

    used: list[str] = []  # skills used this run, in order (trajectory mining -> skill proposals)

    def on_event(ev: dict) -> None:
        kind = ev.get("type")
        if kind == "tool":
            runs.run_step(run_id, "tool", ev.get("label") or ev.get("name") or "tool",
                          sources=ev.get("sources"))
        elif kind == "research":
            runs.run_step(run_id, "research", ev.get("label") or "research",
                          sources=ev.get("sources"))
        elif kind == "verify":
            runs.run_step(run_id, "verify", ev.get("label") or "verify")
        elif kind == "route":  # restore the "what I decided" line the old Thinking panel showed
            runs.run_step(run_id, "tool", "Routing: " + str(ev.get("why") or ev.get("skill") or "choosing a skill")[:200])
        elif kind == "step" and ev.get("status") == "start":
            used.append(str(ev.get("skill") or ""))
            runs.run_step(run_id, "tool", f"Using {ev.get('skill') or 'skill'}")
        elif kind == "escalate":
            runs.run_step(run_id, "research", str(ev.get("why") or "Trying another way"))
        elif kind == "research_state":
            # WP2 never-early gate: the orchestrator's deep_research state machine publishes
            # state changes; a final answer may only go out when the state is COMPLET else the
            # executor holds the result and reports what is still missing.
            states.add(str(ev.get("state") or ev.get("detail") or ""))

    def on_token(chunk: str) -> None:
        if not chunk:
            return
        if runs.run_state(run_id) == "cancelled":
            cancelled.set()
        runs.run_delta(run_id, chunk)

    states: set[str] = set()
    try:
        from ishti import dispatcher
        from orchestrator import persona
        persona.CURRENT_PROFILE.set(runs.chat_profile(run["chat_id"]))  # this chat's profile layer, not the global one
        res = dispatcher.handle(_text_of(run), run["chat_id"],
                                channel="text", on_token=on_token, on_event=on_event)
        if cancelled.is_set() or runs.run_state(run_id) == "cancelled":
            return  # already terminal; the dispatcher result is dropped
        answer = str(res.get("final_answer") or "")
        # WP2 gate: RESEARCHING/SEARCHING/VERIFYING states seen and never COMPLET -> the answer
        # would be early. Replace it with an honest exhaustion report instead of a guess.
        research_ran = any(s for s in states if s)
        complete = any("COMPLET" in s.upper() for s in states)
        if research_ran and not complete:
            answer = ("I could not finish verifying this one. " + answer[:600] +
                      "\n\nWhat I tried: " + "; ".join(sorted(s for s in states if s))[:400] +
                      ". What is missing: independent confirmation of the remaining claims.")
            runs.run_step(run_id, "verify", "research did not reach COMPLET; answer marked unverified")
        runs.finish_run(run_id, answer)
        from ishti import feedback_bridge
        feedback_bridge.trajectory(_text_of(run), used)
        feedback_bridge.auto_title_async(run["chat_id"])
    except BaseException as exc:  # dispatcher reports its own Exceptions; this is the belt
        if type(exc).__name__ == "Cancelled":
            runs.cancel_run(run_id)
        else:
            runs.fail_run(run_id, f"{type(exc).__name__}: {exc}")
            swallowed("ishti/run_executor.py", exc)


def _text_of(run: dict) -> str:
    """The run stores only ids; the prompt text lives on the user message."""
    from ishti import runs
    for m in runs.messages_after(run["chat_id"], 0, limit=200):
        if m["message_id"] == run.get("message_id"):
            return m["text"]
    return ""


if __name__ == "__main__":
    import os
    import tempfile
    os.environ.setdefault("ISHTI_TEST_MODE", "1")
    from ishti import runs
    runs.set_db_path(os.path.join(tempfile.mkdtemp(), "runs.db"))
    ch = runs.new_chat()
    msg = runs.add_user_message(ch["chat_id"], "ping", client_msg_id="x1")
    run = runs.start_run(ch["chat_id"], msg["message"]["message_id"])
    th = launch(run["run_id"])
    th.join(timeout=30)
    final = runs.get_run(run["run_id"])
    assert final["state"] in ("done", "failed"), final  # either is honest; no hang, no silent drop
    ev_types = [e["type"] for e in runs.events_since(0)]
    assert ev_types[-1] in ("run_finished", "run_failed"), ev_types[-3:]
    print("[self-check] run_executor OK - queued run executed server-side and terminalized, "
          f"state={final['state']}")
