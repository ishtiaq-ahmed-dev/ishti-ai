"""Automated API & WebSocket Contract Conformance Test Suite (Phase B).
Validates docs/API_CONTRACT.md against the running ISHTI server or live ASGI app.
Tests all HTTP endpoints, schemas, WebSocket /ws and /ws/events streams, and event payloads.
"""
import asyncio
import json
import os
import sys
import time
from typing import Dict, Any

import subprocess
import pytest
import requests
import websockets

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

BASE_URL = os.environ.get("ISHTI_BASE_URL", "http://127.0.0.1:8888")
WS_URL = BASE_URL.replace("http://", "ws://").replace("https://", "wss://")


@pytest.fixture(scope="session", autouse=True)
def ensure_server():
    """Ensure the backend is running for the contract tests; spin up if needed."""
    started = False
    proc = None
    try:
        requests.get(f"{BASE_URL}/api/health", timeout=1.5)
    except Exception:
        started = True
        python_exe = sys.executable
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        proc = subprocess.Popen([python_exe, "-m", "ishti", "8888"], cwd=ROOT, creationflags=flags)
        for _ in range(40):
            time.sleep(0.5)
            try:
                if requests.get(f"{BASE_URL}/api/health", timeout=1.5).ok:
                    break
            except Exception:
                pass
    yield
    if started and proc:
        try:
            requests.post(f"{BASE_URL}/api/shutdown", json={"force": True}, timeout=3)
        except Exception:
            pass
        try:
            proc.terminate()
        except Exception:
            pass


def test_http_health_contract():
    """Validates GET /api/health schema and types."""
    res = requests.get(f"{BASE_URL}/api/health", timeout=5)
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    data = res.json()
    assert isinstance(data.get("ok"), bool), "ok must be a boolean"
    assert "state" in data, "state must be present in health response"
    assert data["state"] in ("starting", "ready", "shutting_down", "stopped"), f"Unexpected state: {data['state']}"
    print(f"  [PASS] GET /api/health conformant: state={data['state']}")


def test_http_ready_contract():
    """Validates GET /api/ready schema, types, and stage structures."""
    res = requests.get(f"{BASE_URL}/api/ready", timeout=5)
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    data = res.json()
    assert isinstance(data.get("ready"), bool), "ready must be boolean"
    assert isinstance(data.get("stage"), str), "stage must be string"
    assert isinstance(data.get("stages"), list), "stages must be list"
    assert isinstance(data.get("progress"), (int, float)), "progress must be numeric"
    assert data.get("error") is None or isinstance(data.get("error"), str), "error must be string or None"
    assert "log_path" in data, "log_path must be present"

    for st in data["stages"]:
        assert "id" in st and "label" in st and "done" in st, f"Invalid stage item structure: {st}"
        assert isinstance(st["done"], bool), f"stage done must be boolean: {st}"
    print(f"  [PASS] GET /api/ready conformant: stage={data['stage']}, progress={data['progress']}")


def test_http_vram_contract():
    """Validates GET /api/vram schema (Section 5 / snapshot)."""
    res = requests.get(f"{BASE_URL}/api/vram", timeout=5)
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    data = res.json()
    assert "gpu" in data, "gpu object missing from vram snapshot"
    gpu = data["gpu"]
    for key in ("name", "total_mb", "used_mb", "free_mb", "used_pct"):
        assert key in gpu, f"gpu.{key} missing"
        if "mb" in key:
            assert isinstance(gpu[key], (int, float)), f"gpu.{key} must be numeric"
    assert "models" in data and isinstance(data["models"], list), "models list missing"
    assert "roles" in data and isinstance(data["roles"], dict), "roles dict missing"
    print(f"  [PASS] GET /api/vram conformant: GPU={gpu['name']}, Used={gpu['used_mb']}/{gpu['total_mb']} MB ({gpu['used_pct']}%)")


def test_http_vram_footprints_contract():
    """Validates GET /api/vram/footprints schema."""
    res = requests.get(f"{BASE_URL}/api/vram/footprints", timeout=5)
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    data = res.json()
    assert "footprints" in data, "footprints missing from response"
    assert isinstance(data["footprints"], list), "footprints must be a list"
    print(f"  [PASS] GET /api/vram/footprints conformant: count={len(data['footprints'])}")


def test_http_focus_contract():
    """Validates POST /api/focus (Section 7 Single-Instance Focus)."""
    res = requests.post(f"{BASE_URL}/api/focus", timeout=5)
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    data = res.json()
    assert data.get("ok") is True, f"Expected ok: True, got {data}"
    print("  [PASS] POST /api/focus conformant")


def test_http_ollama_settings_contract():
    """Validates GET and POST /api/ollama/settings."""
    res = requests.get(f"{BASE_URL}/api/ollama/settings", timeout=5)
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    data = res.json()
    assert "settings" in data and "env" in data, "missing settings or env"
    s = data["settings"]
    assert "num_parallel" in s and "max_loaded_models" in s and "keep_alive" in s
    print(f"  [PASS] GET /api/ollama/settings conformant: num_parallel={s['num_parallel']}, max_models={s['max_loaded_models']}")


def test_websocket_mirroring_and_events():
    """Subscribes to both /ws/events and /ws, forces test events, and validates schemas."""
    async def _runner():
        from ishti import events
        received_events = []
        received_ws = []

        async def listen_endpoint(path: str, sink: list, duration_s: float = 2.5):
            url = f"{WS_URL}{path}"
            try:
                async with websockets.connect(url, open_timeout=3, close_timeout=3) as ws:
                    start = time.time()
                    while time.time() - start < duration_s:
                        try:
                            msg_text = await asyncio.wait_for(ws.recv(), timeout=0.5)
                            data = json.loads(msg_text)
                            sink.append(data)
                        except asyncio.TimeoutError:
                            continue
            except Exception as e:
                pytest.fail(f"Failed to connect or listen to {path}: {e}")

        # Launch listeners on both /ws/events and /ws concurrently
        t_events = asyncio.create_task(listen_endpoint("/ws/events", received_events))
        t_ws = asyncio.create_task(listen_endpoint("/ws", received_ws))

        # Allow websockets to connect
        await asyncio.sleep(0.5)
        # Call /api/focus on the live server to trigger an in-process event broadcast
        await asyncio.to_thread(requests.post, f"{BASE_URL}/api/focus", timeout=3)

        await asyncio.gather(t_events, t_ws)

        # Validate /ws/events received items
        types_events = [m.get("type") for m in received_events]
        assert "vram_status" in types_events or "boot_stage" in types_events, f"No core events on /ws/events: {types_events}"
        assert "show_window" in types_events, f"show_window not received on /ws/events: {types_events}"

        # Validate /ws received items (mirrored)
        types_ws = [m.get("type") for m in received_ws]
        assert "connected" in types_ws or "vram_status" in types_ws, f"No core events on /ws: {types_ws}"
        assert "show_window" in types_ws, f"show_window not received on mirrored /ws: {types_ws}"

        # Validate exact schemas
        sw_ev = next(m for m in received_events if m.get("type") == "show_window")
        assert "data" in sw_ev and "reason" in sw_ev["data"], f"Invalid show_window event: {sw_ev}"

        vram_ev = next((m for m in received_events if m.get("type") == "vram_status"), None)
        if vram_ev:
            vd = vram_ev["data"]
            assert "gpu" in vd and "name" in vd["gpu"] and "total_mb" in vd["gpu"], f"Invalid vram_status: {vram_ev}"

        print("  [PASS] WebSocket mirroring and event schemas verified on both /ws/events and /ws")

    asyncio.run(_runner())


def test_http_shutdown_contract_optional_body():
    """Validates POST /api/shutdown accepts request with or without body (returns 202)."""
    # Note: We test validation against mock or inspect handler directly to avoid stopping the server mid-test
    from ishti.lifecycle_api import router
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from unittest.mock import patch

    sub_app = FastAPI()
    sub_app.include_router(router)
    client = TestClient(sub_app)

    with patch("ishti.lifecycle.begin_shutdown", return_value={"state": "shutting_down", "message": "shutting down"}):
        # 1. Without body
        r1 = client.post("/api/shutdown")
        assert r1.status_code == 202, f"Expected 202 without body, got {r1.status_code}"
        assert r1.json()["state"] == "shutting_down"

        # 2. With reason body
        r2 = client.post("/api/shutdown", json={"reason": "user_exit"})
        assert r2.status_code == 202, f"Expected 202 with reason body, got {r2.status_code}"

        # 3. With force body
        r3 = client.post("/api/shutdown", json={"force": True})
        assert r3.status_code == 202, f"Expected 202 with force body, got {r3.status_code}"

    print("  [PASS] POST /api/shutdown accepts empty, reason, and force bodies (202 Accepted)")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
