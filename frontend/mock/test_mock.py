"""Mock server contract checks (CONTRACT v2 §14): idempotency, event log, runs, feedback."""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8901"


def req(path, body=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(r, timeout=5))
    except urllib.error.HTTPError as e:
        return {"__status": e.code}


def wait_run(run_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = req(f"/api/runs/{run_id}")
        if run.get("state") in ("done", "failed", "cancelled"):
            return run
        time.sleep(0.2)
    raise AssertionError("run did not settle")


def test_health_and_chats():
    assert req("/api/health")["ok"] is True
    chat = req("/api/chats", {}, "POST")
    assert chat["profile"] in ("personal", "business") and chat["chat_id"]


def test_send_idempotent_and_run_completes():
    chat = req("/api/chats", {}, "POST")
    cid = chat["chat_id"]
    a = req(f"/api/chats/{cid}/messages", {"client_msg_id": "m-1", "text": "list downloads"}, "POST")
    b = req(f"/api/chats/{cid}/messages", {"client_msg_id": "m-1", "text": "list downloads"}, "POST")
    assert a["run_id"] and b["message_id"] == a["message_id"] and b.get("duplicate") is True
    run = wait_run(a["run_id"])
    assert run["state"] == "done" and "Downloads" in run["text"]
    msgs = req(f"/api/chats/{cid}/messages")["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]


def test_event_log_replay():
    req("/api/chats", {}, "POST")
    time.sleep(0.2)
    full = req("/api/events?since_seq=0")
    types = [e["type"] for e in full["events"]]
    assert types.count("message_created") >= 1 and "run_step" in types
    cut = full["events"][0]["seq"]
    tail = req(f"/api/events?since_seq={cut}")["events"]
    assert [e["seq"] for e in tail] == list(range(cut + 1, full["last_seq"] + 1))


def test_cancel_and_connectors_and_learning():
    chat = req("/api/chats", {}, "POST")
    sent = req(f"/api/chats/{chat['chat_id']}/messages", {"client_msg_id": "c-x", "text": "long task"}, "POST")
    out = req(f"/api/runs/{sent['run_id']}/cancel", {}, "POST")
    assert out["cancelled"] is True
    assert req("/api/connectors")["connectors"]
    assert req("/api/connectors/ghost/status")["__status"] == 404
    assert req("/api/skills/proposals")["proposals"]
    assert req("/api/learning/mode", {"mode": "auto_t0"}, "PUT")["mode"] == "auto_t0"
    assert req("/api/learning/mode", {"mode": "chaos"}, "PUT")["__status"] == 400
    assert req("/api/profile")["active"] in ("personal", "business")
    assert req("/api/prompt/layers")["layers"][0]["name"] == "core"
    fb = req("/api/feedback", {"message_id": "m", "run_id": "r", "rating": 1, "reasons": []}, "POST")
    assert fb["state"] == "stored"
    assert req("/api/feedback/stats")["total"] >= 1
    # E.164 validation + duplicate block
    assert req("/api/connectors/whatsapp/config", {"control_numbers": ["+923001234567"]}, "PUT")["saved"]
    assert req("/api/connectors/whatsapp/config", {"control_numbers": ["12345"]}, "PUT")["__status"] == 400
    assert req("/api/connectors/whatsapp/config",
               {"control_numbers": ["+923001234567", "+923001234567"]}, "PUT")["__status"] == 400


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
    print("mock contract checks: all pass")
