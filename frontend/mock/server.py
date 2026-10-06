"""CONTRACT v2 mock server (frontend/mock/server.py) - implements docs/API_CONTRACT.md §14
exactly so the UI never waits for the backend. Run: python frontend/mock/server.py [port]

Offline-only: stdlib http.server + threads. Synthetic data. No real sends, no network.
WS replay is emulated by long-poll GET /api/events?since_seq=N (same envelope shapes).
"""
import json
import os
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

SEQ = 0
LOCK = threading.RLock()
EVENTS = []           # durable event log: {seq,type,data,ts}
CHATS = {}            # chat_id -> {chat_id,title,profile,created_at,updated_at}
MESSAGES = {}         # chat_id -> [ {seq,message_id,role,text,run_id,ts,channel} ]
RUNS = {}             # run_id -> {run_id,chat_id,state,text,steps,seq,profile,started_at,ended_at,error}
CLIENT_DEDUP = {}     # (chat_id, client_msg_id) -> {message_id, run_id}
FEEDBACK = []         # stored feedback rows
STATE = {"active_profile": "personal"}
CONNECTORS = {
    "whatsapp": {"status": "disconnected", "control_numbers": [], "paired": False, "code": None},
    "telegram": {"status": "disconnected", "paired": False},
    "mcp": {"status": "disconnected"},
    "a2a": {"status": "disconnected"},
}
AGENTS = {
    "researcher": {"skills": ["deep_research", "web_search"], "goal": "gather sources",
                   "state": "idle", "messages": []},
    "coder": {"skills": ["dev"], "goal": "write and run code", "state": "idle", "messages": []},
}
LEARNING = {"mode": "propose", "proposals": [
    {"id": "p1", "title": "Auto CSV summary skill", "skill": "csv_summary", "tier": "T0",
     "risk": "read-only", "status": "open", "origin": "trajectory"},
]}

PROMPT_LAYERS = {
    "layers": [
        {"name": "core", "chars": 1180, "preview": "You are ISHTI, a local-first personal assistant..."},
        {"name": "profile:personal", "chars": 128, "preview": "PROFILE: personal - warm, casual..."},
        {"name": "skills", "chars": 340, "count": 3},
        {"name": "dynamic", "chars": 40, "preview": "CONTEXT (untrusted data): (none)"},
    ],
    "total_chars": 1688, "core_digest": "a1b2c3d4e5f6",
}


def emit(type_, data):
    global SEQ
    with LOCK:
        SEQ += 1
        env = {"seq": SEQ, "type": type_, "data": data, "ts": time.time()}
        EVENTS.append(env)
        return env


def research_worker(run_id, chat_id):
    """Emit run_step research events, deltas, then run_finished (synthetic)."""
    steps = [("research", "Searching the internet", ["https://a.example", "https://b.example"]),
             ("research", "Reading the top 3 sources", ["https://a.example"]),
             ("verify", "Cross-checking claims", [])]
    for kind, label, sources in steps:
        time.sleep(0.4)
        with LOCK:
            run = RUNS.get(run_id)
            if not run or run["state"] != "running":
                return
        emit("run_step", {"run_id": run_id, "kind": kind, "label": label,
                          "n": steps.index((kind, label, sources)) + 1, "sources": sources})
    time.sleep(0.3)
    for chunk in ("The Downloads folder contains ", "4 files: ", "report.pdf, notes.txt, photo.png, data.csv."):
        with LOCK:
            run = RUNS.get(run_id)
            if not run or run["state"] != "running":
                return
        emit("run_delta", {"run_id": run_id, "text": chunk})
        time.sleep(0.2)
    with LOCK:
        run = RUNS.get(run_id)
        if not run or run["state"] != "running":
            return
        run["state"] = "done"
        run["text"] = ("The Downloads folder contains 4 files: report.pdf, notes.txt, "
                       "photo.png, data.csv.")
        msg = {"seq": len(MESSAGES.get(chat_id, [])) + 1, "message_id": uuid.uuid4().hex[:12],
               "role": "assistant", "text": run["text"], "run_id": run_id, "ts": time.time(),
               "channel": "text"}
        MESSAGES.setdefault(chat_id, []).append(msg)
    # contract parity with ishti/runs.py: the assistant row is announced BEFORE run_finished
    emit("message_created", {"chat_id": chat_id, "message": msg})
    emit("run_finished", {"run_id": run_id, "chat_id": chat_id, "text": run["text"],
                          "message_id": msg["message_id"]})


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            return {}

    # ---------------- health/ready/shutdown ----------------
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/api/health":
            return self._json({"ok": True, "state": "ready"})
        if u.path == "/api/ready":
            return self._json({"ready": True, "stage": "ready", "stages": [], "progress": 100,
                               "error": None, "state": "ready", "log_path": "", "elapsed_s": 1.0})
        if u.path == "/api/chats":
            chats = sorted(CHATS.values(), key=lambda c: -c["updated_at"])
            return self._json({"chats": chats})
        if u.path.startswith("/api/chats/") and u.path.endswith("/messages"):
            chat_id = u.path.split("/")[3]
            after = int((q.get("after_seq") or ["0"])[0])
            msgs = [m for m in MESSAGES.get(chat_id, []) if m["seq"] > after]
            return self._json({"chat_id": chat_id, "messages": msgs})
        if u.path.startswith("/api/runs/"):
            run_id = u.path.split("/")[3]
            run = RUNS.get(run_id)
            if not run:
                return self._json({"error": "unknown run_id"}, 404)
            return self._json(run)
        if u.path == "/api/events":  # replay endpoint
            since = int((q.get("since_seq") or ["0"])[0])
            if (q.get("wait") or ["0"])[0] == "1":
                deadline = time.time() + 10
                while time.time() < deadline and not any(e["seq"] > since for e in EVENTS):
                    time.sleep(0.15)
            return self._json({"events": [e for e in EVENTS if e["seq"] > since],
                               "last_seq": SEQ})
        if u.path == "/api/feedback/stats":
            pos = sum(1 for f in FEEDBACK if f["rating"] == 1)
            neg = sum(1 for f in FEEDBACK if f["rating"] == -1)
            return self._json({"total": len(FEEDBACK), "pos": pos, "neg": neg,
                               "neutral": len(FEEDBACK) - pos - neg, "by_reason": {}})
        if u.path == "/api/profile":
            return self._json({"active": STATE["active_profile"]})
        if u.path == "/api/prompt/layers":
            return self._json(PROMPT_LAYERS)
        if u.path == "/api/connectors":
            rows = [{"id": cid, "kind": "channel" if cid in ("whatsapp", "telegram") else "tool",
                     "status": c["status"], "configured": c["status"] != "disconnected"}
                    for cid, c in CONNECTORS.items()]
            return self._json({"connectors": rows})
        if u.path.startswith("/api/connectors/") and u.path.endswith("/status"):
            cid = u.path.split("/")[3]
            c = CONNECTORS.get(cid)
            if not c:
                return self._json({"error": "unknown connector"}, 404)
            return self._json({"id": cid, "status": c["status"], "configured": c["status"] != "disconnected",
                               "paired": c.get("paired", False), "detail": ""})
        if u.path == "/api/agents":
            rows = [{"id": aid, **{k: v for k, v in a.items() if k != "messages"}}
                    for aid, a in AGENTS.items()]
            return self._json({"agents": rows})
        if u.path.startswith("/api/agents/") and u.path.endswith("/messages"):
            aid = u.path.split("/")[3]
            if aid not in AGENTS:
                return self._json({"error": "unknown agent"}, 404)
            return self._json({"agent_id": aid, "messages": AGENTS[aid]["messages"]})
        if u.path == "/api/learning/status":
            open_p = sum(1 for p in LEARNING["proposals"] if p["status"] == "open")
            return self._json({"mode": LEARNING["mode"], "lessons": 12, "proposals_open": open_p,
                               "dataset_rows": 348})
        if u.path == "/api/skills/proposals":
            return self._json({"proposals": LEARNING["proposals"]})
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        body = self._body()
        if u.path == "/api/chats":
            chat_id = uuid.uuid4().hex[:12]
            profile = body.get("profile") if body.get("profile") in ("personal", "business") \
                else STATE["active_profile"]
            CHATS[chat_id] = {"chat_id": chat_id, "title": "New chat", "profile": profile,
                              "created_at": time.time(), "updated_at": time.time()}
            MESSAGES.setdefault(chat_id, [])
            return self._json({"chat_id": chat_id, "profile": profile})
        if u.path.startswith("/api/chats/") and u.path.endswith("/messages"):
            chat_id = u.path.split("/")[3]
            chat = CHATS.get(chat_id)
            if not chat:
                return self._json({"error": "unknown chat_id"}, 404)
            text = str(body.get("text") or "").strip()
            cmid = str(body.get("client_msg_id") or "")
            if not text:
                return self._json({"error": "text is required"}, 400)
            key = (chat_id, cmid)
            if cmid and key in CLIENT_DEDUP:
                prev = CLIENT_DEDUP[key]
                run = RUNS.get(prev["run_id"], {})
                return self._json({"message_id": prev["message_id"], "run_id": prev["run_id"],
                                   "duplicate": True, "state": run.get("state", "unknown")})
            msg = {"seq": len(MESSAGES.get(chat_id, [])) + 1, "message_id": uuid.uuid4().hex[:12],
                   "role": "user", "text": text, "run_id": "", "ts": time.time(),
                   "channel": body.get("channel") or "text"}
            MESSAGES.setdefault(chat_id, []).append(msg)
            chat["updated_at"] = time.time()
            run_id = uuid.uuid4().hex[:12]
            RUNS[run_id] = {"run_id": run_id, "chat_id": chat_id, "state": "running", "text": "",
                            "steps": [], "seq": 0, "profile": chat["profile"], "started_at": time.time(),
                            "ended_at": None, "error": None}
            msg["run_id"] = run_id
            if cmid:
                CLIENT_DEDUP[key] = {"message_id": msg["message_id"], "run_id": run_id}
            emit("message_created", {"chat_id": chat_id, "message": msg})
            emit("run_started", {"run_id": run_id, "chat_id": chat_id,
                                 "message_id": msg["message_id"], "profile": chat["profile"]})
            threading.Thread(target=research_worker, args=(run_id, chat_id), daemon=True).start()
            return self._json({"message_id": msg["message_id"], "run_id": run_id,
                               "duplicate": False})
        if u.path.startswith("/api/runs/") and u.path.endswith("/cancel"):
            run_id = u.path.split("/")[3]
            run = RUNS.get(run_id)
            if not run:
                return self._json({"error": "unknown run_id"}, 404)
            if run["state"] in ("queued", "running"):
                run["state"] = "cancelled"
                run["ended_at"] = time.time()
                emit("run_finished", {"run_id": run_id, "chat_id": run["chat_id"],
                                      "text": "(cancelled)", "cancelled": True})
            return self._json({"run_id": run_id, "cancelled": True})
        if u.path == "/api/feedback":
            fid = uuid.uuid4().hex[:12]
            rating = body.get("rating")
            if rating not in (-1, 0, 1):
                return self._json({"error": "rating must be -1|0|1"}, 400)
            FEEDBACK.append({"feedback_id": fid, **body, "ts": time.time()})
            emit("learning_update", {"kind": "feedback", "detail": {
                "message_id": body.get("message_id"), "rating": rating}})
            return self._json({"feedback_id": fid, "state": "stored"})
        if u.path == "/api/profile":
            if body.get("active") not in ("personal", "business"):
                return self._json({"error": "active must be personal|business"}, 400)
            STATE["active_profile"] = body["active"]
            emit("profile_changed", {"active": body["active"]})
            return self._json({"active": body["active"]})
        if u.path.startswith("/api/connectors/") and u.path.endswith("/connect"):
            cid = u.path.split("/")[3]
            c = CONNECTORS.get(cid)
            if not c:
                return self._json({"error": "unknown connector"}, 404)
            c["status"] = "connecting"
            if cid == "whatsapp" and not c.get("paired"):
                c["code"] = "4271"
                emit("connector_qr", {"connector_id": cid,
                                      "qr": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg'/>",
                                      "expires_in": 20, "pairing_code": c["code"]})
            else:
                c["status"] = "connected"
                c["paired"] = True
                emit("connector_status", {"connector_id": cid, "status": "connected"})
            return self._json({"ok": True, "detail": "connect requested"})
        if u.path.startswith("/api/connectors/") and u.path.endswith("/disconnect"):
            cid = u.path.split("/")[3]
            c = CONNECTORS.get(cid)
            if c:
                c["status"] = "disconnected"
                c["paired"] = False
                emit("connector_status", {"connector_id": cid, "status": "disconnected"})
            return self._json({"ok": True})
        if u.path.startswith("/api/connectors/") and u.path.endswith("/test"):
            cid = u.path.split("/")[3]
            return self._json({"ok": CONNECTORS.get(cid, {}).get("paired", False),
                               "detail": "dry-run test (mock)"})
        if u.path.startswith("/api/skills/proposals/"):
            pid = u.path.split("/")[4]
            action = u.path.split("/")[5]
            for p in LEARNING["proposals"]:
                if p["id"] == pid:
                    p["status"] = "approved" if action == "approve" else "rejected"
                    emit("learning_update", {"kind": "proposal", "detail": p})
                    return self._json({"id": pid, "status": p["status"]})
            return self._json({"error": "unknown proposal"}, 404)
        if u.path == "/api/shutdown":
            return self._json({"state": "shutting_down"})
        return self._json({"error": "not found"}, 404)

    def do_PUT(self):
        u = urlparse(self.path)
        body = self._body()
        if u.path.startswith("/api/connectors/") and u.path.endswith("/config"):
            cid = u.path.split("/")[3]
            c = CONNECTORS.get(cid)
            if not c:
                return self._json({"error": "unknown connector"}, 404)
            nums = body.get("control_numbers")
            if isinstance(nums, list):
                seen = set()
                for n in nums:
                    if not str(n).startswith("+") or len(str(n)) < 8:
                        return self._json({"error": "E.164 required (+countrynumber)"}, 400)
                    if n in seen:
                        return self._json({"error": "duplicate number"}, 400)
                    seen.add(n)
                c["control_numbers"] = [str(n) for n in nums]
            if isinstance(body.get("policy"), dict):
                c["policy"] = body["policy"]
            return self._json({"saved": True, "connector": cid})
        if u.path == "/api/learning/mode":
            if body.get("mode") not in ("off", "propose", "auto_t0"):
                return self._json({"error": "mode must be off|propose|auto_t0"}, 400)
            LEARNING["mode"] = body["mode"]
            emit("learning_update", {"kind": "mode", "detail": {"mode": body["mode"]}})
            return self._json({"mode": body["mode"]})
        return self._json({"error": "not found"}, 404)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8901
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    os.makedirs(os.path.join("frontend", "mock"), exist_ok=True)
    print(f"[mock] CONTRACT v2 mock on http://127.0.0.1:{port} (synthetic data only)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
