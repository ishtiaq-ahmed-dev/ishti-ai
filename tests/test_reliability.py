"""Reliability for unattended running (round C): a failing log write can't recurse forever, Live calls don't leak kill
hooks, token deltas are pruned, key stores are integrity-checked and backed up daily, a recovered cloud model makes
the next outage news again. Scratch stores only."""
import logging
import os
import sqlite3
import time


def test_failing_log_write_does_not_recurse(monkeypatch):
    from ishti import lifecycle

    class Broken(logging.Handler):
        def emit(self, record):
            raise OSError("disk full")
    log = logging.getLogger("stdout")
    h = Broken()
    log.addHandler(h)
    stream = lifecycle._Stream(logging.INFO, None)
    monkeypatch.setattr("sys.stderr", stream)  # logging's handleError writes here: it used to log again, forever
    try:
        assert stream.write("hello\n") == len("hello\n")
    finally:
        log.removeHandler(h)


def test_killswitch_hooks_can_be_unregistered():
    from ishti import killswitch
    hook = killswitch.on_kill(lambda: "x")
    assert hook in killswitch._hooks
    killswitch.off(hook)
    killswitch.off(hook)  # twice is harmless
    assert hook not in killswitch._hooks


def test_old_token_deltas_are_pruned():
    from ishti import runs
    runs.publish("run_delta", {"run_id": "r", "text": "tok"})
    with runs._lock:
        runs.conn().execute("UPDATE event_log SET ts=? WHERE type='run_delta'", (time.time() - 2 * 86400,))
        runs.conn().commit()
    runs.publish("run_delta", {"run_id": "r", "text": "fresh"})
    assert runs.prune_deltas() >= 1
    left = runs.conn().execute("SELECT data FROM event_log WHERE type='run_delta'").fetchall()
    assert left and all("fresh" in r[0] for r in left)


def test_daily_check_backs_up_good_stores_and_reports_a_bad_one():
    from ishti import CONFIG, notifications
    from ishti.core import vault
    data = os.path.dirname(CONFIG["memory"]["db_path"])
    os.makedirs(data, exist_ok=True)
    with sqlite3.connect(os.path.join(data, "good_store.db")) as c:
        c.execute("CREATE TABLE IF NOT EXISTS t(x)")
    with open(os.path.join(data, "bad_store.db"), "wb") as f:
        f.write(b"this is not a sqlite database" * 100)
    out = vault.daily_check_and_backup(stores=("good_store.db", "bad_store.db"))
    assert out.get("good_store.db") == "ok"
    assert os.path.exists(os.path.join(data, "backups", f"good_store-{time.strftime('%a')}.db"))
    assert out.get("bad_store.db") == "failed"
    assert any("bad_store.db failed its integrity check" in n["title"] for n in notifications.recent(10))


def test_recovered_cloud_clears_the_outage_text():
    from ishti import llm
    llm._cloud_state.update(until=time.monotonic() + 60, error="AgentRouter refused the key")
    src = open(llm.__file__, encoding="utf-8").read()
    assert src.count('_cloud_state.update(until=0.0, error="")') == 2
    llm._cloud_state.update(until=0.0, error="")


def test_a_promised_action_is_done_by_the_right_skill():
    from ishti import dispatcher as D
    from ishti.facts import get_facts
    from ishti.skills.reminders import skill as reminders
    reminders._db()
    s = {"id": "promise", "history": [], "trace": lambda **kw: None}
    out = {"reply": "Sure - I'll remind you at 5pm to call mom."}  # the chat model's promise, no tool behind it
    D._verify_reply("chat", "remind me at 5pm to call mom", out, s)
    assert out["reply"].startswith("Reminder set") and "call mom" in out["reply"]
    assert any("call mom" in r[0] for r in get_facts().db.execute("SELECT text FROM reminders WHERE done=0"))
    nothing = {"reply": "I'll keep an eye on that for you."}
    D._verify_reply("chat", "tell me something nice", nothing, s)
    assert "haven't actually done that" in nothing["reply"]
