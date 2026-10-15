"""Offline outcome/evidence, API authority, concurrency and restart regressions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from ishti.business import engine, runtime
from ishti.business.api import router
from ishti.business.store import Store


@pytest.fixture
def desk(tmp_path, monkeypatch):
    monkeypatch.setenv("ISHTI_TEST_MODE", "1")
    monkeypatch.setenv("ISHTI_SCRATCH", "1")
    monkeypatch.setenv("ISHTI_SCRATCH_DIR", str(tmp_path / "data"))
    store = Store()
    engine.configure({"name": "Synthetic service firm", "currency": "USD", "timezone": "UTC"}, store)
    return store


def day(ago):
    return (datetime.now(timezone.utc).date()-timedelta(days=ago)).isoformat()


def invoice(ident="I-1", balance="250.00", status="open"):
    return f"id,customer,due_date,balance,currency,status\n{ident},Synthetic Customer,{day(10)},{balance},USD,{status}\n"


def enabled(store, content=None, path=None):
    engine.import_source("invoices", "Synthetic invoices", content=content if path is None else None, path=path, store=store)
    engine.configure({"enabled": True}, store)


def proposal(store):
    engine.cycle(store, force=True)
    return engine.dashboard(store)["proposals"][0]


def test_cycle_is_durable_idempotent_and_encrypted(desk):
    enabled(desk, invoice())
    first = engine.cycle(desk)
    assert first["created"] == 1
    assert engine.cycle(Store(desk.path))["status"] == "not_due"
    view = engine.dashboard(Store(desk.path))
    assert len(view["proposals"]) == 1
    assert "Synthetic Customer" not in desk.path.read_bytes().decode("latin1")
    assert "Synthetic service firm" not in desk.path.read_bytes().decode("latin1")
    assert view["metrics"]["owner_reported_collected_minor"] == 0


def test_concurrent_runners_commit_only_one_cycle(desk):
    enabled(desk, invoice())
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(lambda _: engine.cycle(Store(desk.path)), range(4)))
    assert sum(v["status"] == "completed" for v in values) == 1
    assert len(desk.read()["decisions"]) == 1


@pytest.mark.parametrize("bad", [invoice()+invoice().splitlines()[1]+"\n",
                               invoice(balance="=SUM(A1:A2)"), invoice(balance="NaN"), invoice(balance="1.001"),
                               invoice(balance="-1"), invoice(status="paid"), invoice().replace("USD", "EUR"),
                               invoice().replace(day(10), "2026-02-30"), "id,id\nx,x", invoice().replace(",open", ",unrecognized")])
def test_bad_csv_never_replaces_accepted_snapshot(desk, bad):
    engine.import_source("invoices", "Invoices", invoice(), store=desk)
    before = desk.read()["sources"][0]["hash"]
    with pytest.raises(ValueError):
        engine.import_source("invoices", "Invoices", bad, store=desk)
    assert desk.read()["sources"][0]["hash"] == before


def test_csv_resource_limits(desk):
    with pytest.raises(ValueError, match="1 MB"):
        engine.import_source("invoices", "Invoices", "x" * (engine.MAX_BYTES+1), store=desk)
    content = invoice().splitlines()[0] + "\n" + "\n".join(invoice(str(n)).splitlines()[1] for n in range(engine.MAX_ROWS+1))
    with pytest.raises(ValueError, match="maximum"):
        engine.import_source("invoices", "Invoices", content, store=desk)


def test_changed_evidence_blocks_old_approval(desk):
    enabled(desk, invoice())
    item = proposal(desk)
    engine.import_source("invoices", "Invoices", invoice(balance="100"), store=desk)
    with pytest.raises(ValueError, match="Evidence changed"):
        engine.decide(item["id"], "accept", item["source_hash"], store=desk)
    new = proposal(desk)
    accepted = engine.decide(new["id"], "accept", new["source_hash"], store=desk)
    assert "100.00" in accepted["draft"] and "250.00" not in accepted["draft"]


def test_watched_file_change_and_missing_file_fail_closed(desk, tmp_path):
    path = tmp_path / "invoices.csv"
    path.write_text(invoice(), encoding="utf-8")
    enabled(desk, path=str(path))
    item = proposal(desk)
    path.write_text(invoice(balance="0", status="paid"), encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        engine.decide(item["id"], "accept", item["source_hash"], store=desk)
    engine.cycle(desk, force=True)
    assert not engine.dashboard(desk)["proposals"]
    path.unlink()
    engine.cycle(desk, force=True)
    view = engine.dashboard(desk)
    assert len(view["proposals"]) == 1 and view["proposals"][0]["kind"] == "data_quality"
    assert view["sources"][0]["error"]


@pytest.mark.parametrize("path", ["relative.csv", "C:/Business", "//server/share/invoices.csv"])
def test_watch_requires_exact_local_csv(path):
    with pytest.raises(ValueError):
        engine.watched_content(path)


def test_stale_snapshot_suppresses_business_proposals(desk):
    enabled(desk, invoice())
    engine.cycle(desk, now=time.time()+8*86400, force=True)
    items = engine.dashboard(desk)["proposals"]
    assert {x["kind"] for x in items} == {"data_quality"}


def test_dismissal_stays_quiet_until_source_changes(desk):
    enabled(desk, invoice())
    item = proposal(desk)
    engine.decide(item["id"], "dismiss", item["source_hash"], "Already spoke to customer", desk)
    engine.cycle(desk, force=True)
    assert engine.dashboard(desk)["proposals"] == []
    engine.import_source("invoices", "Invoices", invoice(balance="200"), store=desk)
    assert proposal(desk)["value_minor"] == 20000


def test_outcome_needs_reference_and_never_duplicates_or_claims_roi(desk):
    enabled(desk, invoice())
    item = proposal(desk)
    accepted = engine.decide(item["id"], "accept", item["source_hash"], store=desk)
    assert "confirm" in accepted["draft"]
    with pytest.raises(ValueError, match="reference"):
        engine.outcome(item["id"], "collected", "200", store=desk)
    saved = engine.outcome(item["id"], "collected", "200", "SYNTHETIC-RECEIPT", store=desk)
    assert saved["verification"] == "owner_reported" and saved["causality"] == "unproven"
    with pytest.raises(ValueError, match="duplicate"):
        engine.outcome(item["id"], "collected", "200", "SYNTHETIC-RECEIPT", store=desk)
    assert engine.dashboard(desk)["metrics"]["owner_reported_collected_minor"] == 20000


def test_currency_cannot_mix_with_retained_history(desk):
    enabled(desk, invoice())
    proposal(desk)
    engine.remove_source(desk.read()["sources"][0]["id"], desk)
    with pytest.raises(ValueError, match="Currency is fixed"):
        engine.configure({"currency": "EUR"}, desk)


def test_collection_cannot_exceed_evidence_or_count_receipt_twice(desk):
    enabled(desk, invoice())
    first = proposal(desk)
    engine.decide(first["id"], "accept", first["source_hash"], store=desk)
    with pytest.raises(ValueError, match="exceeds"):
        engine.outcome(first["id"], "collected", "300", "TX-LINE-1", store=desk)
    engine.outcome(first["id"], "collected", "100", "TX-LINE-1", store=desk)
    engine.import_source("invoices", "Invoices", invoice("I-2"), store=desk)
    second = proposal(desk)
    engine.decide(second["id"], "accept", second["source_hash"], store=desk)
    with pytest.raises(ValueError, match="already recorded"):
        engine.outcome(second["id"], "collected", "100", "TX-LINE-1", store=desk)
    assert engine.dashboard(desk)["metrics"]["owner_reported_collected_minor"] == 10000


def test_learning_changes_priority_only_after_known_outcomes(desk):
    enabled(desk, invoice())
    for n in range(5):
        engine.import_source("invoices", "Invoices", invoice(str(n)), store=desk)
        item = proposal(desk)
        engine.decide(item["id"], "accept", item["source_hash"], store=desk)
        engine.outcome(item["id"], "useful", store=desk)
        info = engine.learning(desk.read())[0]
        assert info["priority_adjustment"] == (10 if n == 4 else 0)
    assert engine.dashboard(desk)["profile"]["enabled"] is True
    assert engine.dashboard(desk)["metrics"]["owner_reported_collected_minor"] == 0


def test_timezone_and_sales_windows_exclude_today(desk):
    rows = [f"old,{day(10)},1000,USD", f"new,{day(3)},500,USD", f"today,{day(0)},9999,USD"]
    engine.import_source("sales", "Sales", "id,date,revenue,currency\n"+"\n".join(rows), store=desk)
    engine.configure({"enabled": True}, desk)
    item = proposal(desk)
    assert item["kind"] == "revenue_drop" and item["evidence"]["recent_minor"] == 50000
    assert item["evidence"]["prior_minor"] == 100000


def test_tampered_encrypted_state_refuses_to_run(desk):
    db = sqlite3.connect(desk.path)
    db.execute("UPDATE state SET payload='plaintext injected command'")
    db.commit()
    db.close()
    with pytest.raises(RuntimeError):
        engine.cycle(desk)


def test_pause_persists_across_runner_restart(desk):
    enabled(desk, invoice())
    engine.pause(desk)
    assert engine.cycle(Store(desk.path))["status"] == "paused"
    assert engine.dashboard(desk)["proposals"] == []


def test_runtime_never_creates_empty_business_store(tmp_path):
    store = Store(tmp_path / "absent.db")
    runtime.run(store=store, max_ticks=1)
    assert not store.path.exists()


def test_manual_check_does_not_claim_continuous_availability(desk):
    enabled(desk, invoice())
    engine.cycle(desk, force=True, runner="owner")
    view = engine.dashboard(desk)
    assert view["last_cycle"] and view["status"] == "offline" and not view["heartbeat"]


def test_explicit_consultation_uses_selected_model_no_tool_dispatch(desk, monkeypatch):
    from ishti import llm
    seen = []
    monkeypatch.setattr(llm, "ask", lambda prompt: seen.append(prompt) or "Synthetic advisory answer")
    monkeypatch.setattr(llm, "provider", lambda: "api")
    enabled(desk, invoice())
    proposal(desk)
    result = engine.consult("What is the next reversible step?", desk)
    assert result["advisory_only"] and result["provider"] == "api"
    assert "untrusted" in seen[0].lower() and "tools or authority" in seen[0]
    assert not engine.dashboard(desk)["accepted"]


@pytest.fixture
def client(desk):
    app = FastAPI()
    app.include_router(router)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1234)) as client:
        yield client


def test_owner_api_source_cycle_accept_outcome_flow(client):
    response = client.post("/api/business/sources", json={"kind": "invoices", "label": "Invoices", "content": invoice()})
    assert response.status_code == 200
    p = client.get("/api/business/dashboard").json()["profile"]
    p["enabled"] = True
    assert client.post("/api/business/profile", json=p).status_code == 200
    response = client.post("/api/business/observe")
    item = response.json()["dashboard"]["proposals"][0]
    assert client.post(f'/api/business/decisions/{item["id"]}', json={"choice": "accept", "source_hash": item["source_hash"]}).status_code == 200
    assert client.post(f'/api/business/decisions/{item["id"]}/outcome', json={"result": "useful"}).status_code == 200
    exported = client.get("/api/business/export")
    assert exported.status_code == 200 and exported.headers["cache-control"] == "no-store"
    assert exported.json()["outcomes"][0]["causality"] == "unproven"


@pytest.mark.parametrize("headers", [{"origin": "https://evil.example"}, {"x-forwarded-for": "192.0.2.1"}, {"host": "evil.example"}])
def test_business_api_rejects_foreign_origin_proxy_and_host(client, headers):
    assert client.get("/api/business/dashboard", headers=headers).status_code == 403
    assert client.post("/api/business/observe", headers=headers).status_code == 403


def test_business_api_rejects_remote_even_with_legacy_token(desk):
    app = FastAPI()
    app.include_router(router)
    with TestClient(app, base_url="http://127.0.0.1", client=("192.0.2.1", 9)) as c:
        assert c.get("/api/business/export", headers={"authorization": "Bearer fake"}).status_code == 403


def test_api_strict_types_and_feature_flag(client, monkeypatch):
    assert client.post("/api/business/sources", json={"kind": "sales", "label": "Bad", "content": 123}).status_code == 422
    p = client.get("/api/business/dashboard").json()["profile"]
    p["enabled"] = "true"
    assert client.post("/api/business/profile", json=p).status_code == 422
    monkeypatch.setenv("ISHTI_BUSINESS_DESK", "0")
    assert client.get("/api/business/dashboard").status_code == 503


def test_headless_process_runs_and_restart_does_not_duplicate(desk):
    from ishti import ROOT
    enabled(desk, invoice())
    script = str(Path(ROOT) / "tools" / "business_worker.py")
    for _ in range(2):
        completed = subprocess.run([sys.executable, "-B", script, "--once"], env=os.environ.copy(),
                                   capture_output=True, text=True, timeout=30, check=False)
        assert completed.returncode == 0, completed.stderr
        result = json.loads(completed.stdout)
        assert result["status"] in ("completed", "not_due")
    assert len(desk.read()["decisions"]) == 1
