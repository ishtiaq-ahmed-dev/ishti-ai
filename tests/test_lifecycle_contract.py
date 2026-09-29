"""Contract test: docs/API_CONTRACT.md vs a REAL running backend (needs Ollama + the GPU; run explicitly):

    venv\\Scripts\\python -m pytest tests/test_lifecycle_contract.py -m e2e -s

It starts `python -m ishti <port>` on a spare port, waits for /api/ready, then checks every response, the /ws/events
stream and the graceful shutdown against the `shape` blocks (and the stage / shutdown-step lists) IN THE DOCUMENT, so a
change to the doc or to the code that breaks the other fails here."""
import json
import os
import re
import subprocess
import sys
import time

import psutil
import pytest
import requests
from websockets.sync.client import connect

ROOT = r"D:\ISHTI"
DOC = open(os.path.join(ROOT, "docs", "API_CONTRACT.md"), encoding="utf-8").read()
PORT = 8897
BASE = f"http://127.0.0.1:{PORT}"
pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------- reading the contract
def shapes():
    return {name: json.loads(body) for name, body in re.findall(r"<!-- shape:([\w.]+) -->\s*```json\s*(.*?)```", DOC, re.S)}


SHAPES = shapes()
TYPES = {"string": lambda v: isinstance(v, str), "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
         "bool": lambda v: isinstance(v, bool), "null": lambda v: v is None, "array": lambda v: isinstance(v, list), "object": lambda v: isinstance(v, dict)}


def check(name, value):
    """Errors of `value` against the named shape block ([] when it conforms)."""
    errs = []
    for key, spec in SHAPES[name].items():
        optional, k = key.endswith("?"), key.rstrip("?")
        if k not in value:
            if not optional:
                errs.append(f"{name}: missing '{k}'")
            continue
        if not any(TYPES[t](value[k]) for t in spec.split("|")):
            errs.append(f"{name}.{k}: expected {spec}, got {type(value[k]).__name__} ({str(value[k])[:40]!r})")
    return errs


def doc_stage_ids():
    return re.findall(r"^\| `(\w+)` \| ([^|]+) \| \d+s|^\| `ready` \| ([^|]+) \|", DOC, re.M)


def doc_shutdown_steps():
    return re.findall(r"^\d+\. `(\w+)`:", DOC.split("### 4.3")[1], re.M)


# ---------------------------------------------------------------- the running backend
@pytest.fixture(scope="module")
def backend():
    if os.path.exists(os.path.join(os.environ.get("LOCALAPPDATA", ""), "ISHTI", "run", "ishti.json")):
        pass
    proc = subprocess.Popen([sys.executable, "-m", "ishti", str(PORT)], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        end = time.time() + 300
        while time.time() < end:
            try:
                if requests.get(BASE + "/api/ready", timeout=2).json()["ready"]:
                    break
            except (requests.RequestException, ValueError):
                pass
            assert proc.poll() is None, "the backend exited during boot"
            time.sleep(1)
        else:
            pytest.fail("backend not ready within 300 s")
        yield proc
    finally:
        if proc.poll() is None:  # a test that did not shut it down: stop it properly, then by force
            try:
                requests.post(BASE + "/api/shutdown", json={}, timeout=3)
                proc.wait(40)
            except Exception:
                proc.kill()


def get(path):
    r = requests.get(BASE + path, timeout=15)
    assert r.status_code == 200, (path, r.status_code, r.text[:200])
    return r.json()


# ---------------------------------------------------------------- tests
def test_health(backend):
    d = get("/api/health")
    assert check("health", d) == [] and d["ok"] is True and d["state"] == "ready"


def test_ready_matches_contract(backend):
    d = get("/api/ready")
    assert check("ready", d) == []
    assert d["ready"] is True and d["error"] is None and d["stage"] == "ready" and d["progress"] == 100
    for s in d["stages"]:
        assert check("stage", s) == [] and s["done"] is True
    live = [(s["id"], s["label"]) for s in d["stages"]]
    documented = [(m[0] or "ready", (m[1] or m[2]).strip()) for m in doc_stage_ids()]
    assert live == documented, f"stages in the document {documented} != live {live}"
    assert os.path.isfile(d["log_path"]), "log_path must exist"


def test_vram_matches_contract(backend):
    d = get("/api/vram")
    assert check("vram", d) == []
    assert check("vram.gpu", d["gpu"]) == [] and d["gpu"]["total_mb"] > 0
    assert d["models"], "the planner must be loaded after boot"
    for m in d["models"]:
        assert check("vram.model", m) == []
        assert m["fully_on_gpu"] is True and m["size_vram_mb"] >= m["size_mb"] - 1, f"PARTIAL OFFLOAD: {m}"
    assert d["violations"] == []
    for role, r in d["roles"].items():
        assert check("vram.role", r) == [], role
    assert set(d["roles"]) >= {"planner", "coder", "fast", "translator", "vision2", "vision3"}
    assert check("vram.ollama", d["ollama"]) == []
    assert d["roles"]["planner"]["pinned"] is True and d["roles"]["planner"]["model"] in {m["name"] for m in d["models"]}
    assert 0 <= d["other_mb"] <= d["gpu"]["used_mb"]


def test_footprints(backend):
    rows = get("/api/vram/footprints")["footprints"]
    assert rows, "the planner's footprint is recorded when it is warmed up"
    for r in rows:
        assert check("footprint", r) == []


def test_admit_fast_path_for_the_resident_planner(backend):
    planner = get("/api/vram")["roles"]["planner"]["model"]
    d = requests.post(BASE + "/api/vram/admit", json={"model": planner, "role": "planner", "job": "contract test"}, timeout=60).json()
    assert check("admit", d) == [] and d["ok"] is True and d["actions"] == [], d
    assert requests.post(BASE + "/api/vram/admit", json={}, timeout=5).status_code == 400


def test_ollama_settings(backend):
    d = get("/api/ollama/settings")
    assert check("ollama_settings", d) == []
    assert "OLLAMA_NUM_PARALLEL" in d["env"] and "OLLAMA_NUM_GPU" not in d["env"]  # partial offload must be impossible by config
    bad = requests.post(BASE + "/api/ollama/settings", json={"kv_cache_type": "bogus"}, timeout=10)
    assert bad.status_code == 400 and "error" in bad.json()


def test_events_stream_and_focus(backend):
    with connect(f"ws://127.0.0.1:{PORT}/ws/events") as ws:
        seen, seqs = [], []
        deadline = time.time() + 8
        while time.time() < deadline and {"boot_stage", "vram_status"} - {m["type"] for m in seen}:
            m = json.loads(ws.recv(timeout=6))
            assert check("event", m) == [], m
            seen.append(m)
            seqs.append(m["seq"])
        kinds = {m["type"] for m in seen}
        assert {"boot_stage", "vram_status"} <= kinds, f"late replay missing: got {kinds}"
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
        vs = next(m for m in seen if m["type"] == "vram_status")["data"]
        assert check("vram", vs) == []
        # a second launch asks the first to show its window
        r = requests.post(BASE + "/api/focus", timeout=5).json()
        assert r["ok"] is True and r["listeners"] >= 1
        end = time.time() + 5
        while time.time() < end:
            m = json.loads(ws.recv(timeout=5))
            if m["type"] == "show_window":
                assert m["data"]["reason"]
                break
        else:
            pytest.fail("no show_window event after /api/focus")


def test_other_websites_cannot_drive_the_dangerous_endpoints(backend):
    """A page from another site open in the owner's browser must not be able to shut ISHTI down, load models or restart Ollama
    (a cross-site POST from a browser carries an Origin; a DNS-rebinding page carries the attacker's Host)."""
    for path in ("/api/shutdown", "/api/ollama/apply", "/api/vram/admit", "/api/focus"):
        r = requests.post(BASE + path, json={"model": "x"}, headers={"Origin": "https://evil.example"}, timeout=10)
        assert r.status_code == 403 and r.json()["error"] == "foreign origin", (path, r.status_code)
        r = requests.post(BASE + path, json={"model": "x"}, headers={"Host": "evil.example"}, timeout=10)
        assert r.status_code == 403, (path, "rebinding", r.status_code)
    # the app's own origin, and a caller with no Origin at all (the desktop shell, the launcher), are fine
    assert requests.post(BASE + "/api/focus", headers={"Origin": f"http://127.0.0.1:{PORT}"}, timeout=5).status_code == 200
    assert requests.post(BASE + "/api/focus", timeout=5).status_code == 200
    assert requests.get(BASE + "/api/health", timeout=5).json()["state"] == "ready"  # and it is still up
    with pytest.raises(Exception):  # the WebSocket refuses a foreign Origin too
        connect(f"ws://127.0.0.1:{PORT}/ws/events", origin="https://evil.example").recv(timeout=3)


def test_second_launch_starts_no_second_backend(backend):
    r = subprocess.run([sys.executable, "-m", "ishti", str(PORT)], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "already running" in r.stdout
    listeners = [c.pid for c in psutil.net_connections(kind="inet") if c.laddr.port == PORT and c.status == "LISTEN"]
    assert len(set(listeners)) == 1, listeners


def test_zz_shutdown_is_graceful_and_complete(backend):
    reg = json.load(open(os.path.join(os.environ["LOCALAPPDATA"], "ISHTI", "run", f"ishti-{PORT}.json"), encoding="utf-8"))
    server_pid = reg["server_pid"]
    got = []
    with connect(f"ws://127.0.0.1:{PORT}/ws/events") as ws:
        r = requests.post(BASE + "/api/shutdown", json={}, timeout=5)
        assert r.status_code == 202 and check("shutdown_ack", r.json()) == [] and r.json()["state"] == "shutting_down"
        assert requests.post(BASE + "/api/shutdown", json={}, timeout=5).status_code == 202  # idempotent
        # new work is refused while shutting down
        assert requests.get(BASE + "/api/skills", timeout=5).status_code == 503
        # the governor is closed too: an admit during shutdown would load a model AFTER the unload step
        assert requests.post(BASE + "/api/vram/admit", json={"model": "gemma3:4b"}, timeout=5).status_code == 503
        assert requests.get(BASE + "/api/vram", timeout=10).status_code == 200  # read-only status keeps answering
        end = time.time() + 40
        try:
            while time.time() < end:
                m = json.loads(ws.recv(timeout=40))
                if m["type"] == "shutdown_progress":
                    assert check("shutdown_progress", m["data"]) == [], m
                    got.append(m["data"])
                    if m["data"]["step"] == "exit":
                        break
        except Exception:
            pass
    steps = [g["step"] for g in got]
    assert steps == doc_shutdown_steps(), f"steps {steps} != documented {doc_shutdown_steps()}"
    assert all(g["total"] == len(steps) and g["index"] == i + 1 for i, g in enumerate(got))
    end = time.time() + 30
    while time.time() < end and psutil.pid_exists(server_pid):
        time.sleep(0.3)
    assert not psutil.pid_exists(server_pid), "the server process is still alive after the shutdown"
    assert not [c for c in psutil.net_connections(kind="inet") if c.laddr.port == PORT and c.status == "LISTEN"], "port still open"
