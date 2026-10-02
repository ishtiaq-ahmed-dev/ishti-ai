"""Backend event bus for /ws/events (docs/API_CONTRACT.md): any thread publishes, the server's subscriber fans out.

publish(type, data) is cheap and never blocks or raises. The latest boot_stage / vram_status / error are kept so a
client that connects late gets them replayed. Coalescing (vram_status at most once a second) is the producer's job
(ishti.vram.sampler); everything else is sent at once."""
import threading
import time

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


REPLAY = ("boot_stage", "vram_status", "error")
_subs, _latest, _lock = [], {}, threading.Lock()


def publish(type_, data):
    msg = {"type": type_, "data": data, "ts": time.time()}
    with _lock:
        if type_ in REPLAY:
            _latest[type_] = msg
        subs = list(_subs)
    for fn in subs:
        try:
            fn(msg)
        except Exception as _exc:  # one broken listener must not stop the others
            swallowed('ishti/events.py:publish', _exc)
    return msg


def subscribe(fn):
    with _lock:
        _subs.append(fn)


def unsubscribe(fn):
    with _lock:
        if fn in _subs:
            _subs.remove(fn)


def replay():
    """The latest event of each replayable type, oldest first."""
    with _lock:
        return sorted(_latest.values(), key=lambda m: m["ts"])


if __name__ == "__main__":
    got = []
    subscribe(got.append)
    publish("boot_stage", {"id": "a"})
    publish("model_switch", {"role": "planner"})
    publish("boot_stage", {"id": "b"})
    assert [m["type"] for m in got] == ["boot_stage", "model_switch", "boot_stage"]
    assert [m["data"]["id"] for m in replay()] == ["b"]  # only replayable types, latest of each
    subscribe(lambda m: 1 / 0)  # a failing listener is isolated
    publish("error", {"message": "x"})
    assert len(got) == 4 and {m["type"] for m in replay()} == {"boot_stage", "error"}
    print("[self-check] events OK - fan-out, replay of the latest boot_stage/vram_status/error, listener isolation")
